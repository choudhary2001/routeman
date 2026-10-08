"""Django + Django REST framework: read the URLconf of the configured project."""
from __future__ import annotations

import inspect
import os
import re
import sys
from typing import Dict, List, Optional, Tuple

from ..fields import fields_of, has_files
from ..model import Api, Body, Field, Route, WebSocket
from ..naming import HUMAN, humanize, resource, singular
from ..scan import Scan, scan
from .common import LOGIN_CLASSES, docstring, guess_login, regex_to_path

METHODS = ('get', 'post', 'put', 'patch', 'delete')
ORDER = ['GET', 'POST', 'PUT', 'PATCH', 'DELETE']
CRUD = {'list', 'create', 'retrieve', 'update', 'partial_update', 'destroy'}
DEFAULT_EXCLUDE = (r'^/?admin/', r'^/?__debug__/', r'^/?__reload__/', r'^/?static/', r'^/?media/')
CONVERTER_TYPES = {'IntConverter': 'integer', 'UUIDConverter': 'uuid', 'SlugConverter': 'string',
                   'PathConverter': 'string', 'StringConverter': 'string'}


def setup(settings: str, root: str):
    from django.apps import apps
    if apps.ready:
        return
    if root not in sys.path:
        sys.path.insert(0, root)
    os.environ['DJANGO_SETTINGS_MODULE'] = settings
    import django
    django.setup()


# --- URL patterns ---------------------------------------------------------------------------

def pattern_text(pattern) -> Tuple[str, Dict[str, str], bool]:
    """(path piece in {name} form, {param: type}, is_format_suffix)."""
    kind = type(pattern).__name__
    if kind == 'LocalePrefixPattern':
        return pattern.language_prefix, {}, False
    if kind == 'RoutePattern':
        route = str(pattern._route if hasattr(pattern, '_route') else pattern)
        types = {}
        for name, conv in getattr(pattern, 'converters', {}).items():
            types[name] = CONVERTER_TYPES.get(type(conv).__name__, 'string')

        def sub(m):
            types.setdefault(m.group(2), CONVERTER_TYPES.get(
                {'int': 'IntConverter', 'uuid': 'UUIDConverter'}.get(m.group(1) or '', ''), 'string'))
            return '{%s}' % m.group(2)
        text = re.sub(r'<(?:(\w+):)?(\w+)>', sub, route)
        return text, types, 'format' in types
    return regex_to_path(str(pattern.regex.pattern if hasattr(pattern, 'regex') else pattern))


def walk(patterns, prefix='', types=None, namespace=''):
    from django.urls import URLPattern, URLResolver
    types = dict(types or {})
    for p in patterns:
        text, ptypes, fmt = pattern_text(p.pattern)
        if fmt:
            continue
        merged = {**types, **ptypes}
        if isinstance(p, URLResolver):
            try:
                children = p.url_patterns
            except Exception:  # noqa: BLE001 - a broken include must not stop the rest
                continue
            yield from walk(children, prefix + text, merged, p.namespace or namespace)
        elif isinstance(p, URLPattern):
            yield '/' + (prefix + text).lstrip('/'), merged, p


# --- helpers --------------------------------------------------------------------------------

PROJECT_ROOT = ''


def app_label(obj) -> str:
    """Folder for a view: its Django app, or '' for third-party views (grouped by path instead)."""
    from django.apps import apps
    module = getattr(obj, '__module__', '') or ''
    file = getattr(sys.modules.get(module), '__file__', '') or ''
    if PROJECT_ROOT and not os.path.abspath(file).startswith(PROJECT_ROOT + os.sep):
        return ''
    if os.sep + 'site-packages' + os.sep in file:
        return ''
    config = apps.get_containing_app_config(module)
    if config is not None:
        return config.label
    return module.split('.')[0]


def fake_request(method='get', path='/'):
    from django.contrib.auth.models import AnonymousUser
    from django.test import RequestFactory
    req = getattr(RequestFactory(), method.lower())(path)
    req.user = AnonymousUser()
    return req


def body_from_scan(s: Scan, default_mode: str) -> Optional[Body]:
    fields: List[Field] = []
    found_schema = False
    for obj, _ in s.schemas:
        got = fields_of(obj)
        if got is not None:
            found_schema = True
            fields.extend(f for f in got if f.name not in {x.name for x in fields})
    for f in list(s.body.values()) + list(s.files.values()):
        if f.name not in {x.name for x in fields}:
            fields.append(f)
    if not fields and not s.raw_body and not s.kinds:
        return None
    mode = default_mode
    if 'json' in s.kinds or (s.raw_body and not s.kinds - {'json'}):
        mode = 'json'
    if 'form' in s.kinds and 'json' not in s.kinds:
        mode = 'urlencoded'
    if has_files(fields) or s.files:
        mode = 'form'
    return Body(mode=mode, fields=fields, partial=not found_schema)


def path_params(path: str, types: Dict[str, str]) -> List[Field]:
    return [Field(name=n, type=types.get(n, 'string'), required=True) for n in re.findall(r'\{(\w+)\}', path)]


# --- DRF --------------------------------------------------------------------------------

def drf_auth_kind(classes) -> Optional[str]:
    names = [c.__name__ if inspect.isclass(c) else type(c).__name__ for c in classes or ()]
    mods = [getattr(c, '__module__', '') for c in classes or ()]
    for n, m in zip(names, mods):
        if 'JWT' in n or 'Jwt' in n or 'jwt' in m:
            return 'bearer'
    for n, m in zip(names, mods):
        if 'Token' in n:
            return 'token'
    for n in names:
        if 'Basic' in n:
            return 'basic'
    for n in names:
        if 'Session' in n:
            return 'session'
    return None


def is_public(permission_classes, method: str) -> bool:
    names = []
    for p in permission_classes or ():
        # OperandHolder (IsAuthenticated | AllowAny) -> treat as not public
        names.append(getattr(p, '__name__', type(p).__name__))
    if not names:
        return True
    if all(n == 'AllowAny' for n in names):
        return True
    if method == 'GET' and all(n in ('AllowAny', 'IsAuthenticatedOrReadOnly', 'DjangoModelPermissionsOrAnonReadOnly')
                               for n in names):
        return True
    return False


def drf_view(cls, initkwargs, action, method):
    from rest_framework.request import Request
    try:
        view = cls(**{k: v for k, v in (initkwargs or {}).items() if hasattr(cls, k)})
    except Exception:  # noqa: BLE001
        view = cls()
    view.action = action
    view.args, view.kwargs, view.format_kwarg = (), {}, None
    view.headers = {}
    try:
        view.request = view.initialize_request(fake_request(method))
    except Exception:  # noqa: BLE001
        view.request = Request(fake_request(method))
    return view


def is_list_action(cls, action, method) -> bool:
    if action is not None:
        return action == 'list'
    return method == 'GET' and hasattr(cls, 'list') and not hasattr(cls, 'retrieve')


def drf_query(view, cls, action, method) -> List[Field]:
    out: List[Field] = []
    if not is_list_action(cls, action, method):
        return out
    try:
        paginator = view.paginator
    except Exception:  # noqa: BLE001
        paginator = None
    if paginator is not None:
        kind = type(paginator).__mro__
        names = {c.__name__ for c in kind}
        if 'CursorPagination' in names:
            out.append(Field(name=paginator.cursor_query_param, description='Pagination cursor'))
        elif 'LimitOffsetPagination' in names:
            out.append(Field(name=paginator.limit_query_param, type='integer', description='Page size',
                             example=paginator.default_limit or 10))
            out.append(Field(name=paginator.offset_query_param, type='integer', description='Offset', example=0))
        elif 'PageNumberPagination' in names:
            out.append(Field(name=paginator.page_query_param, type='integer', description='Page number', example=1))
            if paginator.page_size_query_param:
                out.append(Field(name=paginator.page_size_query_param, type='integer', description='Page size',
                                 example=paginator.page_size or 10))
    for backend in getattr(view, 'filter_backends', ()) or ():
        name = backend.__name__ if inspect.isclass(backend) else type(backend).__name__
        try:
            if name == 'SearchFilter' and getattr(view, 'search_fields', None):
                out.append(Field(name=backend.search_param, description='Search in: ' +
                                 ', '.join(str(f).lstrip('^=@$') for f in view.search_fields)))
            elif name == 'OrderingFilter' and (getattr(view, 'ordering_fields', None) or getattr(view, 'ordering', None)):
                fields = getattr(view, 'ordering_fields', None)
                desc = 'Order by: ' + ', '.join(fields) if isinstance(fields, (list, tuple)) else 'Field to order by'
                out.append(Field(name=backend.ordering_param, description=desc + ' (prefix - for descending)'))
            elif name == 'DjangoFilterBackend':
                out.extend(filterset_fields(backend, view))
        except Exception:  # noqa: BLE001
            continue
    return out


def filterset_fields(backend, view) -> List[Field]:
    filterset_class = None
    try:
        queryset = view.get_queryset()
        filterset_class = backend().get_filterset_class(view, queryset)
    except Exception:  # noqa: BLE001
        filterset_class = getattr(view, 'filterset_class', None)
    out = []
    if filterset_class is not None:
        for name, flt in filterset_class.base_filters.items():
            kind = type(flt).__name__
            f = Field(name=name, description=str(flt.label or '') if getattr(flt, 'label', None) else '')
            if 'Number' in kind:
                f.type = 'number'
            elif 'Boolean' in kind:
                f.type = 'boolean'
            elif 'DateTime' in kind:
                f.type = 'datetime'
            elif 'Date' in kind:
                f.type = 'date'
            elif 'UUID' in kind:
                f.type = 'uuid'
            elif 'ModelChoice' in kind or 'ModelMultipleChoice' in kind:
                f.type = 'integer'
            choices = (getattr(flt, 'extra', {}) or {}).get('choices')
            if choices and not callable(choices):
                f.choices = [c[0] for c in choices if c and c[0] not in ('', None)]
            out.append(f)
    else:
        fields = getattr(view, 'filterset_fields', None) or ()
        out = [Field(name=n) for n in (fields if not isinstance(fields, dict) else fields.keys())]
    return out


def is_api_view(cls) -> bool:
    """Class generated by @api_view (DRF renames it after the function, the qualname keeps it)."""
    return 'WrappedAPIView' in getattr(cls, '__qualname__', '')


def lookup_type(view, params: List[Field]):
    """Router detail routes match any text ({pk}); the model knows the real type."""
    lookup = getattr(view, 'lookup_url_kwarg', None) or getattr(view, 'lookup_field', 'pk')
    for p in params:
        if p.name != lookup or p.type != 'string':
            continue
        try:
            model = view.get_queryset().model
            field_name = getattr(view, 'lookup_field', 'pk')
            field = model._meta.pk if field_name == 'pk' else model._meta.get_field(field_name)
            internal = field.get_internal_type()
        except Exception:  # noqa: BLE001
            continue
        if internal == 'UUIDField':
            p.type = 'uuid'
        elif internal in ('AutoField', 'BigAutoField', 'SmallAutoField', 'IntegerField', 'BigIntegerField',
                          'PositiveIntegerField', 'PositiveBigIntegerField'):
            p.type = 'integer'


def unwrap_api_view(cls, method):
    """@api_view functions: the user's function lives in the generated handler's closure."""
    handler = getattr(cls, method, None)
    for cell in getattr(handler, '__closure__', None) or ():
        value = cell.cell_contents
        if inspect.isfunction(value):
            return value
    return handler


def drf_routes(path, types, cb, cls, label) -> List[Route]:
    actions = getattr(cb, 'actions', None)
    initkwargs = getattr(cb, 'initkwargs', {}) or {}
    wrapped = is_api_view(cls)
    if actions:
        pairs = [(m.upper(), a) for m, a in actions.items() if m in METHODS]
    else:
        pairs = [(m.upper(), None) for m in METHODS if m in cls.http_method_names and hasattr(cls, m)]
    routes = []
    basename = initkwargs.get('basename') or resource(path, label)
    for method, action in pairs:
        view = drf_view(cls, initkwargs, action, method)
        handler = unwrap_api_view(cls, method.lower()) if wrapped else getattr(cls, action or method.lower(), None)
        r = Route(path=path, method=method, folder=[label] if label else [], path_params=path_params(path, types))
        lookup_type(view, r.path_params)
        # Title + description
        if action in HUMAN:
            noun = humanize(basename).lower() if action == 'list' else singular(humanize(basename).lower())
            if action == 'list' and not noun.endswith('s'):
                noun += 's'
            r.name = f'{HUMAN[action]} {noun}'
        elif action:
            r.name = humanize(action)
        else:
            base = handler.__name__ if wrapped and handler is not None else re.sub(r'(API)?View(Set)?$', '', cls.__name__)
            r.name = humanize(base)
        r.description = docstring(handler, skip_modules=('rest_framework',)) or docstring(
            cls, skip_modules=('rest_framework',))
        target = handler if handler is not None else cls
        r.source = f'{getattr(target, "__module__", "")}.{getattr(target, "__qualname__", "")}'

        # Code scan of the handler (and get_queryset, for query params)
        s = scan(handler) if handler is not None and inspect.isfunction(inspect.unwrap(handler)) else Scan()
        if is_list_action(cls, action, method):
            for extra in ('get_queryset', 'filter_queryset'):
                fn = getattr(cls, extra, None)
                if fn is not None and not (getattr(fn, '__module__', '') or '').startswith('rest_framework'):
                    for q in scan(fn).query.values():
                        s.query.setdefault(q.name, q)

        if method in ('POST', 'PUT', 'PATCH'):
            body = None
            if action is None or action not in CRUD:
                scanned = body_from_scan(s, 'json')
                if scanned is not None and (scanned.fields or not scanned.partial):
                    body = scanned
            if body is None and not wrapped:
                serializer = None
                try:
                    serializer = view.get_serializer_class()
                except Exception:  # noqa: BLE001
                    serializer = initkwargs.get('serializer_class') or getattr(cls, 'serializer_class', None)
                if serializer is not None:
                    try:
                        from ..fields import drf_fields
                        fields = drf_fields(serializer, context={'request': view.request, 'view': view})
                        body = Body(mode='json', fields=fields)
                    except Exception:  # noqa: BLE001
                        body = None
            if body is None:
                body = body_from_scan(s, 'json')
            if body is not None:
                parsers = [p.__name__ for p in getattr(cls, 'parser_classes', ()) or ()]
                if body.mode == 'json' and parsers and 'JSONParser' not in parsers:
                    body.mode = 'form'
                if has_files(body.fields):
                    body.mode = 'form'
                if method == 'PATCH':
                    for f in body.fields:
                        f.required = False
                r.body = body
        r.query = drf_query(view, cls, action, method)
        known = {q.name for q in r.query}
        r.query += [q for q in s.query.values() if q.name not in known]
        r.headers = list(s.headers.values())

        perms = getattr(view, 'permission_classes', None)
        authn = getattr(view, 'authentication_classes', None)
        if is_public(perms, method) or authn == [] or authn == ():
            r.auth = 'none'
        routes.append(r)
    return routes


# --- plain Django --------------------------------------------------------------------------

def form_body(view_class, initkwargs, method) -> Optional[Body]:
    if not hasattr(view_class, 'get_form_class'):
        return None
    try:
        view = view_class(**{k: v for k, v in (initkwargs or {}).items() if hasattr(view_class, k)})
        view.request = fake_request(method)
        view.args, view.kwargs = (), {}
        if not hasattr(view, 'object'):
            view.object = None
        form_class = view.get_form_class()
    except Exception:  # noqa: BLE001
        form_class = getattr(view_class, 'form_class', None)
    if form_class is None:
        return None
    fields = fields_of(form_class)
    if fields is None:
        return None
    return Body(mode='form' if has_files(fields) else 'urlencoded', fields=fields)


def decorator_methods(s: Scan) -> Optional[List[str]]:
    for name, args, kwargs in s.decorators:
        short = name.split('.')[-1]
        if short == 'require_http_methods':
            values = args[0] if args else kwargs.get('request_method_list')
            if isinstance(values, (list, tuple, set)):
                return [v.upper() for v in values if isinstance(v, str)]
        if short == 'require_POST':
            return ['POST']
        if short in ('require_GET', 'require_safe'):
            return ['GET']
        if short in ('api_view',):
            values = args[0] if args else kwargs.get('http_method_names')
            if isinstance(values, (list, tuple)):
                return [v.upper() for v in values]
    return None


def django_routes(path, types, cb, label) -> List[Route]:
    view_class = getattr(cb, 'view_class', None)
    initkwargs = getattr(cb, 'view_initkwargs', {}) or {}
    csrf = not getattr(cb, 'csrf_exempt', False)
    routes = []
    if view_class is not None:
        methods = [m.upper() for m in METHODS if m in view_class.http_method_names and hasattr(view_class, m)]
        if 'RedirectView' in {c.__name__ for c in view_class.__mro__}:
            methods = ['GET']
        for method in methods:
            handler = getattr(view_class, method.lower())
            r = Route(path=path, method=method, folder=[label] if label else [], path_params=path_params(path, types), csrf=csrf,
                      name=humanize(re.sub(r'View$', '', view_class.__name__)),
                      description=docstring(handler, skip_modules=('django',)) or docstring(
                          view_class, skip_modules=('django',)),
                      source=f'{view_class.__module__}.{view_class.__qualname__}')
            s = scan(handler) if (getattr(handler, '__module__', '') or '').split('.')[0] != 'django' else Scan()
            if method in ('POST', 'PUT', 'PATCH'):
                r.body = form_body(view_class, initkwargs, method) or body_from_scan(s, 'urlencoded')
            r.query = list(s.query.values())
            r.headers = list(s.headers.values())
            r.auth = 'none'  # session/cookie views: the API token is not used
            routes.append(r)
        return routes

    func = inspect.unwrap(cb)
    s = scan(cb)
    methods = decorator_methods(s)
    reads_body = bool(s.body or s.files or s.schemas or s.raw_body or s.kinds - {'query'})
    if methods is None:
        if s.only_methods:      # if request.method != 'POST': return 405
            methods = sorted(s.only_methods, key=ORDER.index)
        elif s.methods:         # if request.method == 'POST': ... (other methods fall through to GET)
            methods = sorted(s.methods | {'GET'}, key=ORDER.index)
        else:
            methods = ['POST'] if reads_body else ['GET']
    for method in methods:
        if method not in ('GET', 'POST', 'PUT', 'PATCH', 'DELETE'):
            continue
        r = Route(path=path, method=method, folder=[label] if label else [], path_params=path_params(path, types), csrf=csrf,
                  name=humanize(func.__name__), description=docstring(func),
                  source=f'{func.__module__}.{func.__qualname__}')
        if method in ('POST', 'PUT', 'PATCH'):
            r.body = body_from_scan(s, 'urlencoded')
        r.query = list(s.query.values())
        r.headers = list(s.headers.values())
        r.auth = 'none'
        routes.append(r)
    return routes


# --- Channels -------------------------------------------------------------------------------

def websockets() -> List[WebSocket]:
    from django.conf import settings
    target = getattr(settings, 'ASGI_APPLICATION', None)
    if not target:
        return []
    try:
        module, _, attr = target.rpartition('.')
        app = getattr(__import__(module, fromlist=[attr]), attr)
    except Exception:  # noqa: BLE001
        return []
    found: List[WebSocket] = []

    def visit(obj, prefix='', depth=0, seen=None):
        seen = seen if seen is not None else set()
        if obj is None or depth > 25 or id(obj) in seen:
            return
        seen.add(id(obj))
        mapping = getattr(obj, 'application_mapping', None)
        if isinstance(mapping, dict):
            visit(mapping.get('websocket'), prefix, depth + 1, seen)
            return
        routes = getattr(obj, 'routes', None)
        if isinstance(routes, (list, tuple)) and type(obj).__name__ == 'URLRouter':
            for route in routes:
                text, _, _ = pattern_text(route.pattern)
                callback = getattr(route, 'callback', None)
                if type(callback).__name__ == 'URLRouter' or hasattr(callback, 'routes'):
                    visit(callback, prefix + text, depth + 1, seen)
                    continue
                consumer = getattr(callback, 'consumer_class', None) or callback
                found.append(WebSocket(path='/' + (prefix + text).lstrip('/'),
                                       description=docstring(consumer), source=getattr(consumer, '__qualname__', '')))
            return
        for attr in ('inner', 'application', 'app'):
            visit(getattr(obj, attr, None), prefix, depth + 1, seen)

    visit(app)
    return found


# --- entry point ------------------------------------------------------------------------------

def extract(settings: str, root: str, exclude=()) -> Api:
    global PROJECT_ROOT
    PROJECT_ROOT = os.path.abspath(root)
    setup(settings, root)
    from django.conf import settings as dj_settings
    from django.urls import get_resolver

    api = Api(framework='django')
    patterns = [re.compile(p) for p in list(DEFAULT_EXCLUDE) + list(exclude or ())]
    login_candidates = []
    auth_votes: List[str] = []
    drf = 'rest_framework' in dj_settings.INSTALLED_APPS

    for path, types, p in walk(get_resolver().url_patterns):
        if any(x.search(path) or x.search(path.lstrip('/')) for x in patterns):
            continue
        cb = p.callback
        module = getattr(cb, '__module__', '') or ''
        if module.startswith(('django.contrib.admin', 'django.views.static', 'django.contrib.staticfiles',
                              'debug_toolbar')):
            continue
        cls = getattr(cb, 'cls', None)
        try:
            if cls is not None and drf:
                target = cls
                if is_api_view(cls):
                    target = next((unwrap_api_view(cls, m) for m in METHODS if hasattr(cls, m)), cls)
                label = app_label(target)
                routes = drf_routes(path, types, cb, cls, label)
                kind = drf_auth_kind(getattr(cls, 'authentication_classes', ()))
                if kind:
                    auth_votes.append(kind)
                if cls.__name__ in LOGIN_CLASSES or any(c.__name__ in LOGIN_CLASSES for c in cls.__mro__[1:4]):
                    login_candidates.extend((0, r) for r in routes if r.method == 'POST')
            else:
                view = getattr(cb, 'view_class', None) or inspect.unwrap(cb)
                routes = django_routes(path, types, cb, app_label(view))
        except Exception as exc:  # noqa: BLE001 - report and keep going
            api.warnings.append(f'{path}: {type(exc).__name__}: {exc}')
            continue
        api.routes.extend(routes)

    api.websockets = websockets()
    if drf:
        from rest_framework.settings import api_settings
        default = drf_auth_kind(api_settings.DEFAULT_AUTHENTICATION_CLASSES)
        api.auth = default or (max(set(auth_votes), key=auth_votes.count) if auth_votes else 'none')
        if api.auth == 'bearer':
            try:
                from rest_framework_simplejwt.settings import api_settings as jwt_settings
                prefix = jwt_settings.AUTH_HEADER_TYPES[0]
                if prefix != 'Bearer':
                    api.auth, api.token_prefix = 'token', prefix
            except Exception:  # noqa: BLE001
                pass
    else:
        api.auth = 'session'
    if api.auth == 'session':
        for r in api.routes:
            if r.auth == 'none':
                r.auth = None
    guess_login(api, login_candidates)
    return api
