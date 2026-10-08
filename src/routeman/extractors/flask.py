"""Flask: read app.url_map of the configured application."""
from __future__ import annotations

import inspect
import re
import sys
from typing import List, Optional

from ..fields import fields_of, has_files
from ..model import Api, Body, Field, Route
from ..naming import humanize
from ..scan import Scan, scan
from .common import docstring, guess_login

ORDER = ['GET', 'POST', 'PUT', 'PATCH', 'DELETE']
CONVERTERS = {'int': 'integer', 'float': 'number', 'uuid': 'uuid', 'path': 'string', 'string': 'string',
              'any': 'string'}
PROTECTING = re.compile(
    r'(^|\.)(jwt_required|fresh_jwt_required|login_required|token_required|auth_required|requires_auth|'
    r'require_auth|authenticated|auth_token_required|roles_required|roles_accepted|permission_required|'
    r'verify_jwt_in_request|requires_login|admin_required)$')
SMOREST_LOCATIONS = {'json': 'json', 'form': 'urlencoded', 'files': 'form', 'query': 'query',
                     'querystring': 'query', 'headers': 'header'}


def rule_to_path(rule: str):
    types = {}

    def sub(m):
        conv, name = m.group(1), m.group(2)
        types[name] = CONVERTERS.get(conv or 'string', 'string')
        return '{%s}' % name
    return re.sub(r'<(?:(\w+)(?:\([^)]*\))?:)?(\w+)>', sub, rule), types


def decorator_names(handler, view_class, s: Scan) -> List[str]:
    names = [name for name, _, _ in s.decorators]
    for deco in getattr(view_class, 'decorators', None) or ():
        names.append(getattr(deco, '__qualname__', '') or getattr(deco, '__name__', ''))
    for deco in getattr(view_class, 'method_decorators', None) or ():
        if isinstance(deco, dict):
            deco = [d for ds in deco.values() for d in (ds if isinstance(ds, (list, tuple)) else [ds])]
        for d in deco if isinstance(deco, (list, tuple)) else [deco]:
            names.append(getattr(d, '__qualname__', '') or getattr(d, '__name__', ''))
    return names


def is_protected(names: List[str]) -> bool:
    return any(PROTECTING.search(n.split('.<locals>')[0]) for n in names)


def smorest_parts(handler):
    """flask-smorest @blp.arguments(Schema, location=...) stores the schemas on the function."""
    doc = getattr(handler, '_apidoc', None)
    out = []
    if isinstance(doc, dict):
        for param in (doc.get('arguments') or {}).get('parameters', []) or []:
            schema = param.get('schema')
            location = SMOREST_LOCATIONS.get(param.get('location', 'json'), 'json')
            if schema is not None:
                out.append((schema, location))
    return out


def annotated_parts(handler):
    """Flask-Pydantic style: def view(body: Model, query: Model)."""
    out = []
    try:
        hints = inspect.signature(inspect.unwrap(handler)).parameters
    except (TypeError, ValueError):
        return out
    for name, param in hints.items():
        ann = param.annotation
        if ann is inspect.Parameter.empty or isinstance(ann, str):
            continue
        location = {'body': 'json', 'form': 'urlencoded', 'query': 'query'}.get(name)
        if location and fields_of(ann) is not None:
            out.append((ann, location))
    return out


def body_and_query(handler, s: Scan):
    fields: List[Field] = []
    query: List[Field] = list(s.query.values())
    mode: Optional[str] = None
    declared = False
    for schema, location in smorest_parts(handler) + annotated_parts(handler):
        got = fields_of(schema) or []
        if location == 'query':
            query.extend(f for f in got if f.name not in {q.name for q in query})
            continue
        declared = True
        mode = location if location != 'header' else mode
        fields.extend(f for f in got if f.name not in {x.name for x in fields})
    for obj, kind in s.schemas:
        got = fields_of(obj)
        if got is not None:
            declared = True
            mode = mode or ('urlencoded' if kind == 'form' else 'json')
            fields.extend(f for f in got if f.name not in {x.name for x in fields})
    for f in list(s.body.values()) + list(s.files.values()):
        if f.name not in {x.name for x in fields}:
            fields.append(f)
    if not fields and not s.kinds - {'query'} and not s.raw_body:
        return None, query
    if mode is None:
        mode = 'json' if ('json' in s.kinds or not s.kinds) else 'urlencoded'
    if s.files or has_files(fields):
        mode = 'form'
    return Body(mode=mode, fields=fields, partial=not declared), query


def auth_kind(app, protected_names: List[str]) -> str:
    if 'flask_jwt_extended' in sys.modules and any('jwt' in n for n in protected_names):
        prefix = app.config.get('JWT_HEADER_TYPE', 'Bearer')
        return 'bearer' if prefix in ('Bearer', None) else ('token', prefix)
    if 'flask_httpauth' in sys.modules:
        import flask_httpauth
        for n in protected_names:
            owner = n.split('.')[0]
            for module in list(sys.modules.values()):
                obj = getattr(module, owner, None)
                if isinstance(obj, getattr(flask_httpauth, 'HTTPBasicAuth', ())):
                    return 'basic'
                if isinstance(obj, getattr(flask_httpauth, 'HTTPTokenAuth', ())):
                    scheme = getattr(obj, 'scheme', 'Bearer')
                    return 'bearer' if scheme == 'Bearer' else ('token', scheme)
    if 'flask_login' in sys.modules and any(n.endswith('login_required') for n in protected_names):
        return 'session'
    return 'bearer'


def extract(app, exclude=()) -> Api:
    api = Api(framework='flask', title=app.name)
    patterns = [re.compile(p) for p in exclude or ()]
    protected_all: List[str] = []
    for rule in app.url_map.iter_rules():
        if rule.endpoint == 'static' or rule.endpoint.endswith('.static'):
            continue
        path, types = rule_to_path(rule.rule)
        if any(x.search(path) for x in patterns):
            continue
        view = app.view_functions.get(rule.endpoint)
        if view is None:
            continue
        view_class = getattr(view, 'view_class', None)
        blueprint = rule.endpoint.rsplit('.', 1)[0] if '.' in rule.endpoint else ''
        methods = [m for m in ORDER if m in (rule.methods or ())]
        for method in methods:
            try:
                handler = view
                if view_class is not None:
                    handler = getattr(view_class, method.lower(), None) or getattr(view_class, 'dispatch_request')
                s = scan(handler, request_names=('request',), request_arg=False)
                names = decorator_names(handler, view_class, s)
                func = inspect.unwrap(handler)
                base = view_class.__name__ if view_class is not None else func.__name__
                r = Route(path=path, method=method, folder=[blueprint] if blueprint else [],
                          name=humanize(re.sub(r'(View|Resource|API|Api)$', '', base)) or base,
                          description=docstring(handler) or docstring(view_class),
                          path_params=[Field(name=n, type=types.get(n, 'string'), required=True)
                                       for n in re.findall(r'\{(\w+)\}', path)],
                          source=f'{getattr(func, "__module__", "")}.{getattr(func, "__qualname__", "")}')
                body, query = body_and_query(handler, s)
                if method in ('POST', 'PUT', 'PATCH') or (method == 'DELETE' and body is not None and body.fields):
                    r.body = body
                r.query = query
                r.headers = list(s.headers.values())
                if any(n.split('.')[-1] == 'jwt_required' and kw.get('refresh') for n, _, kw in s.decorators):
                    r.auth = 'refresh'  # @jwt_required(refresh=True): send the refresh token
                    protected_all.extend(names)
                elif is_protected(names):
                    protected_all.extend(names)
                else:
                    r.auth = 'none'
                api.routes.append(r)
            except Exception as exc:  # noqa: BLE001
                api.warnings.append(f'{method} {path}: {type(exc).__name__}: {exc}')
    if protected_all:
        kind = auth_kind(app, protected_all)
        if isinstance(kind, tuple):
            api.auth, api.token_prefix = kind
        else:
            api.auth = kind
        if api.auth == 'session':
            for r in api.routes:
                r.auth = None
    guess_login(api, [])
    return api
