"""
Read a view function's source to find what it takes from the request.

Used when a view declares no serializer/schema: request.data.get('email'), request.json['name'],
request.args.get('page'), json.loads(request.body)['x'], Form(request.POST), Schema().load(...).
"""
from __future__ import annotations

import ast
import inspect
import textwrap
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

from .model import NO_DEFAULT, Field

# request.<attr> -> where the value comes from
SOURCES = {
    'data': 'data',            # DRF: JSON or form
    'POST': 'form',
    'json': 'json',            # Flask
    'form': 'form',
    'values': 'form',
    'FILES': 'files',
    'files': 'files',
    'GET': 'query',
    'query_params': 'query',
    'args': 'query',
    'headers': 'header',
    'META': 'meta',
}
CALL_SOURCES = {'get_json': 'json', 'json': 'json', 'form': 'form', 'body': 'json'}
CASTS = {'int': 'integer', 'float': 'number', 'bool': 'boolean', 'str': 'string', 'Decimal': 'number',
         'UUID': 'uuid', 'list': 'array', 'dict': 'object'}
HTTP = {'GET', 'POST', 'PUT', 'PATCH', 'DELETE'}


@dataclass
class Scan:
    body: Dict[str, Field] = field(default_factory=dict)
    query: Dict[str, Field] = field(default_factory=dict)
    headers: Dict[str, Field] = field(default_factory=dict)
    files: Dict[str, Field] = field(default_factory=dict)
    kinds: set = field(default_factory=set)         # json / form / data seen
    raw_body: bool = False                            # reads the body as a whole
    methods: set = field(default_factory=set)         # request.method == 'POST'
    only_methods: set = field(default_factory=set)    # request.method != 'POST' -> view serves POST only
    decorators: List[Tuple[str, list]] = field(default_factory=list)
    schemas: List[Tuple[Any, str]] = field(default_factory=list)  # (class or instance, source kind)
    ok: bool = False


def source_of(func) -> Optional[ast.AST]:
    try:
        func = inspect.unwrap(func)
        src = textwrap.dedent(inspect.getsource(func))
        tree = ast.parse(src)
    except (OSError, TypeError, SyntaxError, IndentationError):
        return None
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            return node
    return None


def dotted(node) -> str:
    parts = []
    while isinstance(node, ast.Attribute):
        parts.append(node.attr)
        node = node.value
    if isinstance(node, ast.Name):
        parts.append(node.id)
    elif isinstance(node, ast.Call):
        return dotted(node.func) + '()'
    return '.'.join(reversed(parts))


def literal(node, default=NO_DEFAULT):
    try:
        return ast.literal_eval(node)
    except Exception:  # noqa: BLE001 - anything that isn't a literal
        return default


def resolve(name: str, namespace: dict):
    """Find a dotted name (`forms.SignupForm`, `UserSchema()`) in a module namespace."""
    called = name.endswith('()')
    name = name[:-2] if called else name
    obj = namespace
    for i, part in enumerate(name.split('.')):
        if i == 0:
            if part not in namespace:
                import builtins
                return getattr(builtins, part, None)
            obj = namespace[part]
        else:
            obj = getattr(obj, part, None)
            if obj is None:
                return None
    return obj


class Scanner(ast.NodeVisitor):
    def __init__(self, request_names, namespace):
        self.req = set(request_names)
        self.ns = namespace or {}
        self.aliases: Dict[str, str] = {}      # variable -> source kind
        self.instances: Dict[str, str] = {}    # variable -> dotted class it was built from
        self.result = Scan()

    # --- what is a request source? -------------------------------------------------------
    def is_request(self, node) -> bool:
        if isinstance(node, ast.Name):
            return node.id in self.req
        # self.request
        return isinstance(node, ast.Attribute) and node.attr == 'request'

    def kind(self, node) -> Optional[str]:
        """Source kind of an expression, or None."""
        if isinstance(node, ast.Await):
            return self.kind(node.value)
        if isinstance(node, ast.Name):
            return self.aliases.get(node.id)
        if isinstance(node, ast.BoolOp):  # request.get_json() or {}
            for value in node.values:
                k = self.kind(value)
                if k:
                    return k
            return None
        if isinstance(node, ast.Attribute) and self.is_request(node.value):
            if node.attr == 'body':
                return 'rawbody'
            return SOURCES.get(node.attr)
        if isinstance(node, ast.Call):
            func = node.func
            # request.get_json(), await request.json(), await request.form()
            if isinstance(func, ast.Attribute) and self.is_request(func.value) and func.attr in CALL_SOURCES:
                return CALL_SOURCES[func.attr]
            # request.data.copy(), request.POST.dict()
            if isinstance(func, ast.Attribute) and func.attr in ('copy', 'dict', 'to_dict'):
                return self.kind(func.value)
            # json.loads(request.body) / json.loads(request.body.decode())
            if dotted(func) in ('json.loads', 'loads', 'orjson.loads', 'ujson.loads') and node.args:
                inner = node.args[0]
                if isinstance(inner, ast.Call) and isinstance(inner.func, ast.Attribute):
                    inner = inner.func.value
                k = self.kind(inner)
                if k in ('rawbody', 'json', 'data'):
                    return 'json'
        return None

    # --- recording -----------------------------------------------------------------------
    def add(self, kind, name, required, default=NO_DEFAULT, type_=None, many=False):
        if not isinstance(name, str) or not name:
            return
        if kind == 'meta':
            if not name.startswith('HTTP_'):
                return
            kind, name = 'header', name[5:].replace('_', '-').title()
        bucket = {'query': self.result.query, 'files': self.result.files,
                  'header': self.result.headers}.get(kind, self.result.body)
        if kind in ('json', 'form', 'data'):
            self.result.kinds.add(kind)
        if type_ is None:
            if kind == 'files':
                type_ = 'file'
            elif default is not NO_DEFAULT and default is not None:
                type_ = {bool: 'boolean', int: 'integer', float: 'number', list: 'array',
                         dict: 'object'}.get(type(default), 'string')
            else:
                type_ = 'string'
        f = bucket.get(name)
        if f is None:
            f = bucket[name] = Field(name=name, type=type_, required=required)
            if default is not NO_DEFAULT and default is not None and not isinstance(default, (list, dict)):
                f.default = default
        else:
            f.required = f.required or required
            if f.type == 'string' and type_ != 'string':
                f.type = type_
        if many and f.type != 'array':
            f.item = Field(name=name, type=f.type)
            f.type = 'array'

    # --- visitors ------------------------------------------------------------------------
    def visit_Assign(self, node):
        k = self.kind(node.value)
        for target in node.targets:
            if isinstance(target, ast.Name):
                if k and k not in ('files',):
                    self.aliases[target.id] = k
                if isinstance(node.value, ast.Call):
                    self.instances[target.id] = dotted(node.value.func)
        self.generic_visit(node)

    visit_AnnAssign = lambda self, node: self.visit_Assign(  # noqa: E731
        ast.Assign(targets=[node.target], value=node.value)) if node.value is not None else None

    def cast_of(self, node) -> Optional[str]:
        parent = getattr(node, '_parent', None)
        if isinstance(parent, ast.Call) and parent.args and parent.args[0] is node:
            return CASTS.get(dotted(parent.func).split('.')[-1])
        return None

    def visit_Call(self, node):
        func = node.func
        # X.get('name'[, default]) / X.getlist('name')
        if isinstance(func, ast.Attribute) and func.attr in ('get', 'getlist', 'pop', 'getone') and node.args:
            k = self.kind(func.value)
            if k and k != 'rawbody':
                name = literal(node.args[0], None)
                default = literal(node.args[1]) if len(node.args) > 1 else NO_DEFAULT
                for kw in node.keywords:
                    if kw.arg == 'default':
                        default = literal(kw.value)
                    if kw.arg == 'type':
                        cast = CASTS.get(dotted(kw.value).split('.')[-1])
                        if cast:
                            self.add(k, name, False, default, cast)
                self.add(k, name, False, default, self.cast_of(node), many=func.attr == 'getlist')
        # A schema/form/serializer fed with request data
        fed = [a for a in node.args if self.kind(a)] + [kw.value for kw in node.keywords if self.kind(kw.value)]
        if fed:
            target = func
            if isinstance(func, ast.Attribute) and func.attr in (
                    'load', 'loads', 'validate', 'model_validate', 'parse_obj', 'from_dict', 'is_valid'):
                target = func.value
            name = dotted(target)
            if isinstance(target, ast.Name) and target.id in self.instances:
                name = self.instances[target.id]
            obj = resolve(name, self.ns) if name else None
            if obj is not None and not inspect.isbuiltin(obj) and not inspect.isfunction(obj):
                kinds = {self.kind(a) for a in fed}
                kinds.discard('files')
                self.result.schemas.append((obj, next(iter(kinds), 'data')))
        self.generic_visit(node)

    def visit_Subscript(self, node):
        k = self.kind(node.value)
        if k and k != 'rawbody' and isinstance(node.ctx, ast.Load):
            key = node.slice
            if hasattr(ast, 'Index') and isinstance(key, getattr(ast, 'Index')):  # Python < 3.9
                key = key.value
            self.add(k, literal(key, None), True, type_=self.cast_of(node))
        self.generic_visit(node)

    def visit_Compare(self, node):
        # 'name' in request.data
        if len(node.ops) == 1 and isinstance(node.ops[0], (ast.In, ast.NotIn)):
            k = self.kind(node.comparators[0])
            if k and k != 'rawbody':
                self.add(k, literal(node.left, None), False)
        # request.method == 'POST' / request.method in ('PUT', 'PATCH')
        left = node.left
        if isinstance(left, ast.Attribute) and left.attr == 'method' and self.is_request(left.value):
            negated = isinstance(node.ops[0], (ast.NotEq, ast.NotIn))
            for comp in node.comparators:
                value = literal(comp, None)
                values = value if isinstance(value, (list, tuple, set)) else [value]
                found = {v.upper() for v in values if isinstance(v, str) and v.upper() in HTTP}
                (self.result.only_methods if negated else self.result.methods).update(found)
        self.generic_visit(node)

    def visit_Attribute(self, node):
        if self.is_request(node.value) and node.attr == 'body':
            self.result.raw_body = True
        self.generic_visit(node)


def scan(func, namespace=None, request_names=('request', 'req'), request_arg=True) -> Scan:
    """request_arg: the view receives the request as its first argument (Django, Starlette)."""
    node = source_of(func)
    if node is None:
        return Scan()
    args = [a.arg for a in node.args.args]
    names = set(request_names)
    if args and request_arg:
        first = args[1] if args[0] in ('self', 'cls') and len(args) > 1 else args[0]
        if first not in ('self', 'cls'):
            names.add(first)
    if namespace is None:
        namespace = getattr(inspect.unwrap(func), '__globals__', {})
    for parent in ast.walk(node):
        for child in ast.iter_child_nodes(parent):
            child._parent = parent
    scanner = Scanner(names, namespace)
    for deco in node.decorator_list:
        call = deco if isinstance(deco, ast.Call) else None
        name = dotted(call.func if call else deco)
        args = [literal(a, None) for a in call.args] if call else []
        kwargs = {kw.arg: literal(kw.value, None) for kw in call.keywords} if call else {}
        scanner.result.decorators.append((name, args, kwargs))
    for stmt in node.body:
        scanner.visit(stmt)
    scanner.result.ok = True
    return scanner.result
