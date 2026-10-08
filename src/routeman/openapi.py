"""JSON Schema / OpenAPI 3 -> routeman model. Used for FastAPI (which builds its own spec) and pydantic."""
from __future__ import annotations

import re
from typing import Dict, List, Optional

from .examples import example
from .model import NO_DEFAULT, Api, Body, Field, Route

FORMATS = {
    'uuid': 'uuid', 'date': 'date', 'date-time': 'datetime', 'time': 'time', 'email': 'email',
    'uri': 'url', 'url': 'url', 'binary': 'file', 'uri-reference': 'url',
}


class SchemaReader:
    def __init__(self, root: dict):
        self.root = root or {}

    def deref(self, schema: dict, seen=()) -> dict:
        while isinstance(schema, dict) and '$ref' in schema:
            ref = schema['$ref']
            if ref in seen:
                return {}
            seen = seen + (ref,)
            node = self.root
            for part in ref.lstrip('#/').split('/'):
                node = node.get(part.replace('~1', '/').replace('~0', '~'), {}) if isinstance(node, dict) else {}
            extra = {k: v for k, v in schema.items() if k != '$ref'}
            schema = {**node, **extra}
        return schema if isinstance(schema, dict) else {}

    def field(self, name: str, schema: dict, required=False, depth=0, seen=()) -> Field:
        ref = schema.get('$ref') if isinstance(schema, dict) else None
        if ref and ref in seen:
            return Field(name=name, type='object', required=required, children=[])
        seen = seen + ((ref,) if ref else ())
        schema = self.deref(schema)
        # Optional[X] -> anyOf [X, null]; Union -> first non-null option
        for key in ('anyOf', 'oneOf'):
            if key in schema:
                options = [o for o in schema[key] if self.deref(o).get('type') != 'null']
                if options:
                    merged = {k: v for k, v in schema.items() if k != key}
                    inner = self.field(name, options[0], required, depth, seen)
                    if 'default' in merged:
                        inner.default = merged['default']
                    if merged.get('description') and not inner.description:
                        inner.description = merged['description']
                    if 'example' in merged or 'examples' in merged:
                        inner.example = merged.get('example', (merged.get('examples') or [NO_DEFAULT])[0])
                    return inner
        if 'allOf' in schema:
            merged = {k: v for k, v in schema.items() if k != 'allOf'}
            for part in schema['allOf']:
                part = self.deref(part)
                props = {**merged.get('properties', {}), **part.get('properties', {})}
                merged = {**part, **merged, 'properties': props,
                          'required': list(merged.get('required', [])) + list(part.get('required', []))}
            schema = merged
        kind = schema.get('type')
        if isinstance(kind, list):
            kind = next((k for k in kind if k != 'null'), 'string')
        fmt = schema.get('format')
        f = Field(name=name, required=required, description=schema.get('description') or schema.get('title') or '')
        if name and f.description == name.replace('_', ' ').title():
            f.description = ''
        if 'example' in schema:
            f.example = schema['example']
        elif schema.get('examples'):
            ex = schema['examples']
            f.example = ex[0] if isinstance(ex, list) else next(iter(ex.values()), NO_DEFAULT)
            if isinstance(f.example, dict) and 'value' in f.example and not isinstance(ex, list):
                f.example = f.example['value']
        if 'default' in schema:
            f.default = schema['default']
        if 'const' in schema:
            f.choices = [schema['const']]
        if 'enum' in schema:
            f.choices = [v for v in schema['enum'] if v is not None]
        if kind == 'object' or 'properties' in schema:
            f.type = 'object'
            req = set(schema.get('required') or ())
            f.children = [] if depth > 6 else [
                self.field(k, v, k in req, depth + 1, seen) for k, v in (schema.get('properties') or {}).items()
                if not self.deref(v).get('readOnly')
            ]
        elif kind == 'array':
            f.type = 'array'
            f.item = self.field(name, schema.get('items') or {}, depth=depth + 1, seen=seen)
        elif fmt in FORMATS:
            f.type = FORMATS[fmt]
        elif kind == 'string' and (schema.get('contentMediaType') or schema.get('contentEncoding') in ('binary', 'base64')
                                   and schema.get('contentMediaType')):
            f.type = 'file'  # OpenAPI 3.1 upload: {"type": "string", "contentMediaType": "application/octet-stream"}
        elif kind in ('integer', 'number', 'boolean', 'string'):
            f.type = kind
        else:
            f.type = 'any'
        self.fit(f, schema)
        return f

    @staticmethod
    def fit(f: Field, schema: dict):
        """Respect min/max constraints so the example validates."""
        if f.example is not NO_DEFAULT or f.default is not NO_DEFAULT or f.choices:
            return
        literal = re.fullmatch(r'\^?\(?([\w.-]+)(?:\|[\w.|-]+)?\)?\$?', schema.get('pattern') or '')
        if literal and f.type == 'string':
            f.example = literal.group(1)  # pattern '^password$' / '^(asc|desc)$'
            return
        value = example(f)
        if isinstance(value, str) and f.type == 'string':
            lo, hi = schema.get('minLength'), schema.get('maxLength')
            if hi is not None and len(value) > hi:
                f.example = value[:hi]
            elif lo is not None and len(value) < lo:
                f.example = (value * (lo // max(len(value), 1) + 1))[:max(lo, len(value))]
        elif f.type in ('integer', 'number') and isinstance(value, (int, float)):
            lo = schema.get('minimum', schema.get('exclusiveMinimum'))
            hi = schema.get('maximum', schema.get('exclusiveMaximum'))
            if lo is not None and value <= lo:
                f.example = lo + 1 if 'exclusiveMinimum' in schema else lo
            if hi is not None and value >= hi:
                f.example = hi - 1 if 'exclusiveMaximum' in schema else hi
        elif f.type == 'array':
            lo = schema.get('minItems')
            if lo and lo > 1 and f.item is not None:
                f.example = [example(f.item)] * lo

    def object_fields(self, schema: dict) -> List[Field]:
        f = self.field('', schema)
        if f.type == 'object':
            return f.children or []
        return []


def fields_from_json_schema(schema: dict) -> List[Field]:
    return SchemaReader(schema).object_fields(schema)


API_KEY_HEADERS = {'x-api-key', 'api-key', 'apikey', 'x-api-token', 'x-auth-token', 'x-access-token'}
SECURITY_TYPES = {('http', 'bearer'): 'bearer', ('http', 'basic'): 'basic', ('oauth2', None): 'bearer',
                  ('openIdConnect', None): 'bearer'}


def from_openapi(spec: dict, framework='openapi') -> Api:
    reader = SchemaReader(spec)
    api = Api(framework=framework, title=(spec.get('info') or {}).get('title', ''))
    schemes = (spec.get('components') or {}).get('securitySchemes') or {}
    global_security = spec.get('security')
    scheme_kind: Dict[str, str] = {}
    for name, s in schemes.items():
        t = s.get('type')
        if t == 'apiKey':
            scheme_kind[name] = 'apikey'
            if s.get('in') == 'header':
                api.auth_header = s.get('name', 'Authorization')
        else:
            scheme_kind[name] = SECURITY_TYPES.get((t, (s.get('scheme') or '').lower() or None),
                                                   SECURITY_TYPES.get((t, None), 'bearer'))
        if t == 'oauth2':
            flows = s.get('flows') or {}
            url = (flows.get('password') or {}).get('tokenUrl')
            if url:
                api.login_path = '/' + url.lstrip('/') if not url.startswith('http') else url
    used = []
    header_auth = {}  # routes that read an auth header as a plain parameter
    servers = spec.get('servers') or []
    base_path = ''
    if servers and isinstance(servers[0], dict):
        m = re.match(r'^(?:https?://[^/]+)?(/.*)$', servers[0].get('url', ''))
        if m and m.group(1) != '/':
            base_path = m.group(1).rstrip('/')

    for path, item in (spec.get('paths') or {}).items():
        common = item.get('parameters') or []
        for method in ('get', 'post', 'put', 'patch', 'delete', 'head', 'options'):
            op = item.get(method)
            if not isinstance(op, dict):
                continue
            route = Route(path=base_path + path, method=method.upper(),
                          name=op.get('summary') or op.get('operationId') or '',
                          description=op.get('description') or '',
                          folder=list((op.get('tags') or [])[:1]),
                          source=op.get('operationId', ''))
            for p in common + (op.get('parameters') or []):
                p = reader.deref(p)
                f = reader.field(p.get('name', ''), p.get('schema') or {}, bool(p.get('required')))
                if p.get('description'):
                    f.description = p['description']
                if 'example' in p:
                    f.example = p['example']
                where = p.get('in')
                if where == 'path':
                    route.path_params.append(f)
                elif where == 'query':
                    route.query.append(f)
                elif where == 'header':
                    lower = p.get('name', '').lower()
                    if lower == 'authorization':
                        header_auth.setdefault(id(route), ('bearer', 'Authorization'))
                    elif lower in API_KEY_HEADERS:
                        header_auth.setdefault(id(route), ('apikey', p.get('name')))
                    elif lower not in ('content-type', 'accept'):
                        route.headers.append(f)
            rb = reader.deref(op.get('requestBody') or {})
            content = rb.get('content') or {}
            for mime, mode in (('application/json', 'json'), ('multipart/form-data', 'form'),
                               ('application/x-www-form-urlencoded', 'urlencoded')):
                if mime in content:
                    media = content[mime] or {}
                    schema = media.get('schema') or {}
                    body = Body(mode=mode, fields=reader.object_fields(schema))
                    if 'example' in media:
                        body.example = media['example']
                    elif not body.fields and reader.deref(schema).get('type') not in (None, 'object'):
                        body.example = example(reader.field('body', schema))
                    route.body = body
                    break
            else:
                other = next(iter(content), None)
                if other:
                    route.body = Body(mode='raw')
            security = op.get('security', global_security)
            if security is not None:
                if not security or security == [{}]:
                    route.auth = 'none'
                else:
                    names = [n for req in security for n in (req or {})]
                    used.extend(scheme_kind.get(n, 'bearer') for n in names)
                    if {} in security:
                        route.auth = None
            elif schemes:
                route.auth = 'none'
            api.routes.append(route)
    if used:
        api.auth = max(set(used), key=used.count)
    elif header_auth:
        kinds = list(header_auth.values())
        api.auth, api.auth_header = max(set(kinds), key=kinds.count)
        for r in api.routes:
            if id(r) not in header_auth:
                r.auth = 'none'
    for r in api.routes:
        if api.login_path and r.method == 'POST' and r.path.rstrip('/') == api.login_path.rstrip('/'):
            r.is_login = True
            r.auth = 'none'
    return api
