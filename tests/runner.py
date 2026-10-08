"""
A small Postman collection runner for the tests: it sends each request the way Postman would
(variables, inherited auth, raw/urlencoded/form-data bodies, cookies, the login script), so the
generated collection is checked against a real server.
"""
import json
import re

import httpx

VAR = re.compile(r'\{\{(\w+)\}\}')
TOKEN_KEYS = ['access_token', 'accessToken', 'access', 'token', 'jwt', 'id_token', 'idToken', 'key',
              'auth_token', 'authToken']
REFRESH_KEYS = ['refresh_token', 'refreshToken', 'refresh']


def find(obj, keys, depth=0):
    if not isinstance(obj, dict) or depth > 4:
        return None
    for k in keys:
        if isinstance(obj.get(k), str) and obj[k]:
            return obj[k]
    for v in obj.values():
        r = find(v, keys, depth + 1)
        if r:
            return r
    return None


class Runner:
    def __init__(self, collection, environment, **overrides):
        self.collection = collection
        self.vars = {v['key']: v.get('value', '') for v in collection.get('variable', [])}
        self.vars.update({v['key']: v['value'] for v in environment['values'] if v.get('enabled', True)})
        self.vars.update({k: str(v) for k, v in overrides.items()})
        self.client = httpx.Client(timeout=15, follow_redirects=False)
        self.csrf = any('csrftoken' in ''.join(e['script']['exec']) for e in collection.get('event', []))

    def sub(self, text):
        for _ in range(3):
            text = VAR.sub(lambda m: str(self.vars.get(m.group(1), m.group(0))), text)
        return text

    def items(self, items=None, auth=None):
        """(item, effective auth) for every request, depth first."""
        items = self.collection['item'] if items is None else items
        auth = self.collection.get('auth') if auth is None else auth
        for item in items:
            if 'item' in item:
                yield from self.items(item['item'], item.get('auth', auth))
            else:
                yield item, item['request'].get('auth', auth)

    def find_item(self, method, path):
        for item, auth in self.items():
            req = item['request']
            raw = req['url']['raw'].split('?')[0].replace('{{base_url}}', '')
            if req['method'] == method and raw == path:
                return item, auth
        raise KeyError(f'{method} {path} not in collection')

    def send(self, item, auth, files=None):
        req = item['request']
        url = self.sub(req['url']['raw'])
        headers = {h['key']: self.sub(h['value']) for h in req.get('header', []) if not h.get('disabled')}
        if auth and auth.get('type') != 'noauth':
            kind = auth['type']
            values = {e['key']: self.sub(e['value']) for e in auth[kind]}
            if kind == 'bearer' and values['token']:  # Postman sends nothing for an empty token
                headers['Authorization'] = f"Bearer {values['token']}"
            elif kind == 'apikey':
                headers[values['key']] = values['value']
            elif kind == 'basic':
                import base64
                headers['Authorization'] = 'Basic ' + base64.b64encode(
                    f"{values['username']}:{values['password']}".encode()).decode()
        kwargs = {}
        body = req.get('body')
        if body:
            mode = body['mode']
            if mode == 'raw':
                kwargs['content'] = self.sub(body['raw']).encode()
            elif mode == 'urlencoded':
                data = {}
                for e in body['urlencoded']:
                    if not e.get('disabled'):
                        data.setdefault(e['key'], []).append(self.sub(e['value']))
                kwargs['data'] = data
            elif mode == 'formdata':
                data, upload = [], []
                for e in body['formdata']:
                    if e.get('disabled'):
                        continue
                    if e.get('type') == 'file':
                        upload.append((e['key'], ('sample.png', b'\x89PNG\r\n\x1a\n' + b'0' * 32, 'image/png')))
                    else:
                        data.append((e['key'], self.sub(e['value'])))
                kwargs['data'] = dict(data) if data else None
                kwargs['files'] = upload or None
        response = self.client.request(req['method'], url, headers=headers, **kwargs)
        # login script
        if item.get('event'):
            try:
                payload = response.json()
            except ValueError:
                payload = None
            access, refresh = find(payload, TOKEN_KEYS), find(payload, REFRESH_KEYS)
            if access:
                self.vars['access_token'] = access
            if refresh:
                self.vars['refresh_token'] = refresh
        # collection script: Django CSRF cookie
        if self.csrf and self.client.cookies.get('csrftoken'):
            self.vars['csrftoken'] = self.client.cookies.get('csrftoken')
        return response

    def call(self, method, path):
        item, auth = self.find_item(method, path)
        return self.send(item, auth)


def load(directory, stem):
    collection = json.loads((directory / f'{stem}.postman_collection.json').read_text())
    environment = json.loads((directory / f'{stem}.local.postman_environment.json').read_text())
    return collection, environment
