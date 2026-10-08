"""FastAPI: FastAPI builds an OpenAPI document from type hints by itself; read it plus the routes it hides."""
from __future__ import annotations

import inspect
import re

from ..fields import fields_of
from ..model import NO_DEFAULT, Api, Body, Field, Route, WebSocket
from ..naming import humanize
from ..openapi import from_openapi
from ..scan import scan
from .common import docstring, guess_login

ORDER = ['GET', 'POST', 'PUT', 'PATCH', 'DELETE']
DOC_ENDPOINTS = {'openapi', 'swagger_ui_html', 'swagger_ui_redirect', 'redoc_html'}


ANNOTATIONS = {int: 'integer', float: 'number', bool: 'boolean', str: 'string', bytes: 'file'}


def _annotation_type(ann) -> str:
    import enum
    import typing
    import uuid
    import datetime
    args = [a for a in typing.get_args(ann) if a is not type(None)]
    if typing.get_origin(ann) is typing.Union or type(ann).__name__ == 'UnionType':
        return _annotation_type(args[0]) if args else 'string'
    if typing.get_origin(ann) in (list, tuple, set, frozenset):
        return 'array'
    if ann in ANNOTATIONS:
        return ANNOTATIONS[ann]
    if inspect.isclass(ann):
        if issubclass(ann, enum.Enum):
            return 'string'
        if issubclass(ann, uuid.UUID):
            return 'uuid'
        if issubclass(ann, datetime.datetime):
            return 'datetime'
        if issubclass(ann, datetime.date):
            return 'date'
        if ann.__name__ in ('UploadFile', 'StarletteUploadFile'):
            return 'file'
    return 'string'


def _param(model_field) -> Field:
    info = getattr(model_field, 'field_info', None)
    ann = getattr(info, 'annotation', None)
    if ann is None:
        ann = getattr(model_field, 'type_', None)
    name = getattr(model_field, 'alias', None) or model_field.name
    if hasattr(model_field, 'required'):
        required = bool(model_field.required)
    elif info is not None and hasattr(info, 'is_required'):
        required = bool(info.is_required())
    else:
        required = False
    f = Field(name=name, type=_annotation_type(ann), required=required)
    choices = [m.value for m in ann] if inspect.isclass(ann) and issubclass(ann, __import__('enum').Enum) else None
    if choices:
        f.choices = choices
    default = getattr(info, 'default', None)
    if not f.required and default is not None and type(default).__name__ not in ('PydanticUndefinedType', 'ellipsis'):
        f.default = default
    if getattr(info, 'description', None):
        f.description = info.description
    return f


class _Flat:
    def __init__(self):
        self.path_params, self.query_params, self.header_params, self.body_params = [], [], [], []
        self.secured = False


def _flatten(dependant, flat=None, depth=0) -> _Flat:
    """All parameters of a route and its sub-dependencies (works across FastAPI versions)."""
    flat = flat or _Flat()
    if depth > 30:
        return flat
    try:
        from fastapi.security.base import SecurityBase
        if isinstance(getattr(dependant, 'call', None), SecurityBase):
            flat.secured = True
    except ImportError:  # pragma: no cover
        pass
    if getattr(dependant, 'security_requirements', None):
        flat.secured = True
    for attr in ('path_params', 'query_params', 'header_params', 'body_params'):
        known = {p.name for p in getattr(flat, attr)}
        getattr(flat, attr).extend(p for p in getattr(dependant, attr, None) or () if p.name not in known)
    for sub in getattr(dependant, 'dependencies', None) or ():
        _flatten(sub, flat, depth + 1)
    return flat


def from_dependant(r: Route, dependant, method: str):
    """Parameters, body and security of a route that is not in the OpenAPI document."""
    flat = _flatten(dependant)
    types = {p.name: _param(p).type for p in flat.path_params}
    for p in r.path_params:
        p.type = types.get(p.name, p.type)
    r.query = [_param(p) for p in flat.query_params]
    r.headers = [_param(p) for p in flat.header_params
                 if (getattr(p, 'alias', '') or p.name).lower() not in ('authorization', 'content-type', 'accept')]
    if flat.body_params and method in ('POST', 'PUT', 'PATCH', 'DELETE'):
        fields = []
        mode = 'json'
        for p in flat.body_params:
            info = getattr(p, 'field_info', None)
            kind = type(info).__name__
            if kind in ('Form', 'File'):
                mode = 'form' if kind == 'File' or mode == 'form' else 'urlencoded'
            ann = getattr(info, 'annotation', None)
            nested = fields_of(ann) if ann is not None else None
            if nested is not None and len(flat.body_params) == 1 and kind == 'Body':
                fields.extend(nested)
            elif nested is not None:
                fields.append(Field(name=p.alias or p.name, type='object', required=bool(p.required), children=nested))
            else:
                f = _param(p)
                if kind == 'File':
                    f.type = 'file'
                fields.append(f)
        r.body = Body(mode=mode, fields=fields)
    r.auth = None if flat.secured else 'none'


def _walk(routes, prefix=''):
    for route in routes:
        sub = getattr(route, 'routes', None)
        kind = type(route).__name__
        if kind == 'Mount' and sub is not None and not hasattr(route, 'endpoint'):
            yield from _walk(sub, prefix + route.path)
            continue
        yield prefix, route


def extract(app, exclude=()) -> Api:
    spec = app.openapi()
    api = from_openapi(spec, framework='fastapi')
    api.title = (spec.get('info') or {}).get('title', '')
    patterns = [re.compile(p) for p in exclude or ()]
    in_spec = {(r.method, r.path) for r in api.routes}
    api.routes = [r for r in api.routes if not any(x.search(r.path) for x in patterns)]

    for prefix, route in _walk(app.routes):
        kind = type(route).__name__
        path = prefix + getattr(route, 'path', '')
        if any(x.search(path) for x in patterns):
            continue
        endpoint = getattr(route, 'endpoint', None)
        if kind in ('APIWebSocketRoute', 'WebSocketRoute'):
            api.websockets.append(WebSocket(path=re.sub(r'\{(\w+):\w+\}', r'{\1}', path),
                                            description=docstring(endpoint), source=getattr(endpoint, '__qualname__', '')))
            continue
        if endpoint is None or getattr(endpoint, '__name__', '') in DOC_ENDPOINTS:
            continue
        methods = [m for m in ORDER if m in (getattr(route, 'methods', None) or ())]
        clean = re.sub(r'\{(\w+):\w+\}', r'{\1}', path)
        for method in methods:
            if (method, clean) in in_spec or (method, path) in in_spec:
                continue
            # include_in_schema=False routes and plain Starlette routes: rebuild what we can
            r = Route(path=clean, method=method, name=humanize(getattr(endpoint, '__name__', '')),
                      description=docstring(endpoint), folder=list((getattr(route, 'tags', None) or [])[:1]),
                      path_params=[Field(name=n, required=True) for n in re.findall(r'\{(\w+)\}', clean)],
                      source=f'{getattr(endpoint, "__module__", "")}.{getattr(endpoint, "__qualname__", "")}')
            dependant = getattr(route, 'dependant', None)
            if dependant is not None:
                try:
                    from_dependant(r, dependant, method)
                except Exception as exc:  # noqa: BLE001
                    api.warnings.append(f'{method} {clean}: {type(exc).__name__}: {exc}')
            elif inspect.isfunction(inspect.unwrap(endpoint)):
                s = scan(endpoint)
                r.query = list(s.query.values())
                if method in ('POST', 'PUT', 'PATCH') and (s.body or s.kinds - {'query'}):
                    r.body = Body(mode='json' if 'form' not in s.kinds else 'urlencoded',
                                  fields=list(s.body.values()), partial=True)
                r.auth = 'none'
            api.routes.append(r)
    if api.login_path is None:
        guess_login(api, [])
    return api
