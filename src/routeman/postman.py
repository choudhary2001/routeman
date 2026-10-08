"""routeman model -> Postman collection v2.1 + environments."""
from __future__ import annotations

import json
import re
import uuid
from collections import OrderedDict
from typing import Dict, List

from . import __version__
from .examples import body_example, example, path_example, plain
from .model import NO_DEFAULT, Api, Body, Field, Route
from .naming import PARAM, humanize, resource, segments, title, variable_names

SCHEMA = 'https://schema.getpostman.com/json/collection/v2.1.0/collection.json'
NAMESPACE = uuid.UUID('6f2b8a52-1c3e-4c55-9a63-7d0f3e9b2a11')

TOKEN_SCRIPT = r"""// routeman: store the tokens returned by this login request
let body;
try { body = pm.response.json(); } catch (e) { body = null; }
function find(obj, keys, depth) {
    if (!obj || typeof obj !== 'object' || depth > 4) return undefined;
    for (const k of keys) if (typeof obj[k] === 'string' && obj[k]) return obj[k];
    for (const v of Object.values(obj)) { const r = find(v, keys, depth + 1); if (r) return r; }
    return undefined;
}
const access = find(body, ['access_token', 'accessToken', 'access', 'token', 'jwt', 'id_token', 'idToken', 'key', 'auth_token', 'authToken'], 0);
const refresh = find(body, ['refresh_token', 'refreshToken', 'refresh'], 0);
if (access) { pm.environment.set('access_token', access); pm.collectionVariables.set('access_token', access); }
if (refresh) { pm.environment.set('refresh_token', refresh); pm.collectionVariables.set('refresh_token', refresh); }
pm.test('login returned a token', function () { pm.expect(access, 'no token found in the response').to.be.a('string'); });"""

STATUS_SCRIPT = """pm.test('no server error', function () { pm.expect(pm.response.code).to.be.below(500); });"""
CSRF_SCRIPT = """const csrf = pm.cookies.get('csrftoken');
if (csrf) { pm.collectionVariables.set('csrftoken', csrf); }"""

TYPE_LABEL = {'datetime': 'date-time', 'any': 'any'}


def _script(lines: str, listen='test'):
    return {'listen': listen, 'script': {'type': 'text/javascript', 'exec': lines.split('\n')}}


def _uid(*parts) -> str:
    return str(uuid.uuid5(NAMESPACE, '/'.join(str(p) for p in parts)))


def _type(f: Field) -> str:
    if f.type == 'array' and f.item is not None:
        return f'array of {_type(f.item)}'
    return TYPE_LABEL.get(f.type, f.type)


def _field_table(fields: List[Field], heading: str) -> str:
    if not fields:
        return ''
    rows = [f'**{heading}**', '', '| Field | Type | Required | Notes |', '|---|---|---|---|']
    for f in fields:
        notes = f.description.replace('|', '\\|').replace('\n', ' ')
        if f.choices:
            notes = (notes + ' ' if notes else '') + 'One of: ' + ', '.join(f'`{plain(c)}`' for c in f.choices[:20])
        rows.append(f'| `{f.name}` | {_type(f)} | {"yes" if f.required else "no"} | {notes} |')
    return '\n'.join(rows)


def _form_value(value) -> str:
    if isinstance(value, bool):
        return 'true' if value else 'false'
    if isinstance(value, (dict, list)):
        return json.dumps(value)
    return '' if value is None else str(value)


class Writer:
    def __init__(self, api: Api, name: str, base_url: str = 'http://localhost:8000'):
        self.api = api
        self.name = name
        self.base_url = base_url.rstrip('/')
        self.variables: Dict[str, str] = OrderedDict()   # path variables -> example values

    # --- requests ----------------------------------------------------------------------------
    def url(self, route: Route) -> dict:
        _, renames = variable_names(route.path)
        params = {p.name: p for p in route.path_params}
        parts = []
        for part in segments(route.path):
            def sub(m):
                name = m.group(1)
                var = renames.get(name, name)
                if var not in self.variables:
                    p = params.get(name)
                    self.variables[var] = path_example(p) if p else '1'
                return '{{%s}}' % var
            parts.append(PARAM.sub(sub, part))
        trailing = route.path.endswith('/') and route.path != '/'
        path = '/'.join(parts) + ('/' if trailing else '')
        query = []
        for q in route.query:
            value = example(q)
            query.append({
                'key': q.name,
                'value': _form_value(value[0] if isinstance(value, list) and value else value),
                'description': (q.description + (' (required)' if q.required else '')).strip(),
                'disabled': not q.required,
            })
        enabled = [q for q in query if not q['disabled']]
        raw = '{{base_url}}/' + path
        if enabled:
            raw += '?' + '&'.join(f"{q['key']}={q['value']}" for q in enabled)
        url = {'raw': raw, 'host': ['{{base_url}}'], 'path': parts + ([''] if trailing else [])}
        if query:
            url['query'] = query
        return url

    def body(self, body: Body, method: str):
        if body.mode == 'json':
            value = plain(body.example) if body.example is not NO_DEFAULT else body_example(body.fields)
            if method == 'PATCH' and body.example is NO_DEFAULT and body.fields:
                required = {f.name for f in body.fields if f.required}
                trimmed = {k: v for k, v in value.items() if k in required} or value
                value = trimmed
            return {'mode': 'raw', 'raw': json.dumps(value, indent=2, ensure_ascii=False),
                    'options': {'raw': {'language': 'json'}}}
        if body.mode == 'raw':
            return {'mode': 'raw', 'raw': ''}
        entries = []
        for f in body.fields:
            notes = f.description + (' (required)' if f.required else '')
            if f.type == 'file' or (f.type == 'array' and f.item is not None and f.item.type == 'file'):
                entries.append({'key': f.name, 'type': 'file', 'src': [], 'description': notes.strip()})
                continue
            value = example(f)
            values = value if isinstance(value, list) and f.type == 'array' and value else [value]
            for v in values:
                entry = {'key': f.name, 'value': _form_value(v), 'description': notes.strip()}
                if not f.required and body.fields_known:
                    entry['disabled'] = True  # optional: switch it on in Postman when needed
                if body.mode == 'form':
                    entry['type'] = 'text'
                entries.append(entry)
        if body.mode == 'form':
            return {'mode': 'formdata', 'formdata': entries}
        return {'mode': 'urlencoded', 'urlencoded': entries}

    def request(self, route: Route) -> dict:
        headers = [{'key': 'Accept', 'value': 'application/json'}]
        req = {'method': route.method, 'header': headers, 'url': self.url(route)}
        notes = [route.description.strip()] if route.description else []
        if route.body is not None:
            if route.body.mode == 'json':
                headers.append({'key': 'Content-Type', 'value': 'application/json'})
            elif route.body.mode == 'urlencoded':
                headers.append({'key': 'Content-Type', 'value': 'application/x-www-form-urlencoded'})
            if route.is_login:
                for f in route.body.fields:
                    if re.search(r'pass', f.name, re.I):
                        f.example = '{{password}}'
                    elif re.search(r'^(username|user|email|login|phone|mobile|identifier)$', f.name, re.I):
                        f.example = '{{username}}'
            req['body'] = self.body(route.body, route.method)
            if route.body.partial:
                notes.append('_Body fields were found by reading the view code, so this list may be incomplete._')
            notes.append(_field_table(route.body.fields, 'Body'))
        for h in route.headers:
            value = example(h)
            headers.append({'key': h.name, 'value': _form_value(value), 'description': h.description})
        if route.csrf and route.method not in ('GET', 'HEAD', 'OPTIONS'):
            headers.append({'key': 'X-CSRFToken', 'value': '{{csrftoken}}',
                            'description': 'Django CSRF token; send any GET first so the cookie is set.'})
        notes.append(_field_table(route.query, 'Query parameters'))
        if route.source:
            notes.append(f'Source: `{route.source}`')
        req['description'] = '\n\n'.join(n for n in notes if n)
        if route.auth == 'none' and self.auth() is not None:
            req['auth'] = {'type': 'noauth'}
        elif route.auth == 'refresh':
            req['auth'] = {'type': 'bearer', 'bearer': [
                {'key': 'token', 'value': '{{refresh_token}}', 'type': 'string'}]}
        item = {'id': _uid(self.name, route.method, route.path), 'name': title(route), 'request': req,
                'response': []}
        if route.is_login:
            item['event'] = [_script(TOKEN_SCRIPT)]
        return item

    # --- tree --------------------------------------------------------------------------------
    def items(self) -> list:
        tree: 'OrderedDict[str, OrderedDict[str, list]]' = OrderedDict()
        routes = sorted(self.api.routes, key=lambda r: (
            humanize(r.folder[0]) if r.folder else humanize(resource(r.path)) or '~',
            r.path, ['GET', 'POST', 'PUT', 'PATCH', 'DELETE'].index(r.method)
            if r.method in ('GET', 'POST', 'PUT', 'PATCH', 'DELETE') else 9))
        for r in routes:
            top = r.folder[0] if r.folder else ''
            res = resource(r.path, top)
            if not top:
                top, res = res or 'root', ''
            tree.setdefault(humanize(top) or 'Root', OrderedDict()).setdefault(humanize(res), []).append(
                self.request(r))
        out = []
        for folder, groups in tree.items():
            children = []
            singles = []
            for group, requests in groups.items():
                if group and len(requests) > 1 and len(groups) > 1:
                    children.append({'name': group, 'item': requests})
                else:
                    singles.extend(requests)
            out.append({'name': folder, 'item': children + singles})
        return out

    def auth(self):
        kind = self.api.auth
        if kind == 'bearer':
            return {'type': 'bearer', 'bearer': [{'key': 'token', 'value': '{{access_token}}', 'type': 'string'}]}
        if kind == 'token':
            return {'type': 'apikey', 'apikey': [
                {'key': 'key', 'value': 'Authorization', 'type': 'string'},
                {'key': 'value', 'value': self.api.token_prefix + ' {{access_token}}', 'type': 'string'},
                {'key': 'in', 'value': 'header', 'type': 'string'}]}
        if kind == 'apikey':
            return {'type': 'apikey', 'apikey': [
                {'key': 'key', 'value': self.api.auth_header, 'type': 'string'},
                {'key': 'value', 'value': '{{api_key}}', 'type': 'string'},
                {'key': 'in', 'value': 'header', 'type': 'string'}]}
        if kind == 'basic':
            return {'type': 'basic', 'basic': [
                {'key': 'username', 'value': '{{username}}', 'type': 'string'},
                {'key': 'password', 'value': '{{password}}', 'type': 'string'}]}
        return None

    def description(self) -> str:
        api = self.api
        lines = [f'Generated by [routeman](https://pypi.org/project/routeman/) {__version__} '
                 f'from the {api.framework} application\'s own routes.', '', '**Getting started**',
                 '1. Import the environment file and select it (top-right in Postman).']
        auth_help = {
            'bearer': 'Requests send `Authorization: Bearer {{access_token}}`.',
            'token': f'Requests send `Authorization: {api.token_prefix} {{{{access_token}}}}`.',
            'apikey': f'Requests send `{api.auth_header}: {{{{api_key}}}}`; set `api_key` in the environment.',
            'basic': 'Requests use HTTP Basic auth with `username` / `password` from the environment.',
            'session': 'The API uses session cookies: run the login request first; Postman keeps the cookie.',
        }.get(api.auth)
        if api.login_path and api.auth in ('bearer', 'token'):
            lines.append(f'2. Run the login request (`{api.login_path}`); the returned token is saved to '
                         '`access_token` automatically.')
        elif api.auth in ('bearer', 'token'):
            lines.append('2. Paste a token into the `access_token` environment variable.')
        if auth_help:
            lines.append(auth_help)
        lines.append('3. Path ids such as `{{user_id}}` are environment variables; set them from list responses.')
        lines.append('')
        lines.append('Every request checks that the server did not answer with a 5xx, so the collection can be '
                     'run as a smoke test. Requests that change data run against the selected environment.')
        if api.websockets:
            lines += ['', '**WebSocket endpoints** (open with *New → WebSocket* in Postman):', '']
            lines += [f'- `{{{{ws_url}}}}{PARAM.sub(lambda m: "{{" + m.group(1) + "}}", w.path)}`'
                      + (f' — {w.description}' if w.description else '') for w in api.websockets]
        return '\n'.join(lines)

    def collection(self) -> dict:
        items = self.items()
        events = [_script(STATUS_SCRIPT)]
        if any(r.csrf for r in self.api.routes):
            events.append(_script(CSRF_SCRIPT))
        variables = [{'key': 'base_url', 'value': self.base_url}]
        if self.api.auth in ('bearer', 'token'):
            variables += [{'key': 'access_token', 'value': ''}, {'key': 'refresh_token', 'value': ''}]
        if any(r.csrf for r in self.api.routes):
            variables.append({'key': 'csrftoken', 'value': ''})
        out = {
            'info': {
                '_postman_id': _uid('collection', self.name),
                'name': self.name,
                'description': self.description(),
                'schema': SCHEMA,
            },
            'item': items,
            'event': events,
            'variable': variables,
        }
        auth = self.auth()
        if auth:
            out['auth'] = auth
        return out

    def environment(self, env_name: str, base_url: str) -> dict:
        base_url = base_url.rstrip('/')
        values = [{'key': 'base_url', 'value': base_url, 'type': 'default', 'enabled': True}]
        if self.api.websockets:
            ws = 'wss' + base_url[5:] if base_url.startswith('https') else 'ws' + base_url[4:] \
                if base_url.startswith('http') else base_url
            values.append({'key': 'ws_url', 'value': ws, 'type': 'default', 'enabled': True})
        if self.api.auth in ('bearer', 'token'):
            values += [{'key': 'access_token', 'value': '', 'type': 'secret', 'enabled': True},
                       {'key': 'refresh_token', 'value': '', 'type': 'secret', 'enabled': True}]
        if self.api.auth == 'apikey':
            values.append({'key': 'api_key', 'value': '', 'type': 'secret', 'enabled': True})
        if self.api.auth == 'basic' or self.api.login_path:
            values += [{'key': 'username', 'value': '', 'type': 'default', 'enabled': True},
                       {'key': 'password', 'value': '', 'type': 'secret', 'enabled': True}]
        for key, value in self.variables.items():
            values.append({'key': key, 'value': value, 'type': 'default', 'enabled': True})
        return {
            'id': _uid('environment', self.name, env_name),
            'name': f'{self.name} - {env_name}',
            'values': values,
            '_postman_variable_scope': 'environment',
        }
