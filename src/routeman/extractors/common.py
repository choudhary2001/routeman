"""Helpers shared by the framework extractors."""
from __future__ import annotations

import inspect
import re
from typing import Dict, List, Tuple

from ..model import Api, Route

# Views whose POST returns a token
LOGIN_CLASSES = {
    'TokenObtainPairView', 'TokenObtainSlidingView', 'TokenObtainView', 'ObtainAuthToken', 'ObtainJSONWebToken',
    'ObtainJSONWebTokenView', 'KnoxLoginView', 'TokenCreateView', 'LoginView',
}
LOGIN_PATH = re.compile(
    r'(^|/)(login|log-in|log_in|signin|sign-in|sign_in|token|obtain[-_]?token|api[-_]token[-_]auth|'
    r'jwt(/create)?|auth/token|access[-_]token)/?$', re.I)
NOT_LOGIN = re.compile(r'refresh|verify|logout|log-out|blacklist|revoke|reset|register|signup|sign-up', re.I)


def guess_type(rx: str) -> str:
    if re.fullmatch(r'(\\d|\[0-9\])(\+|\{\d+(,\d*)?\})', rx):
        return 'integer'
    if '0-9a-f' in rx.lower() and '-' in rx:
        return 'uuid'
    return 'string'


def regex_to_path(rx: str) -> Tuple[str, Dict[str, str], bool]:
    """'^users/(?P<pk>[0-9]+)/$' -> ('users/{pk}/', {'pk': 'integer'}, False). Last item: format suffix."""
    s = rx[1:] if rx.startswith('^') else rx
    s = re.sub(r'(\\Z|\$)$', '', s)
    out, types, fmt, unnamed, i = '', {}, False, 0, 0
    while i < len(s):
        c = s[i]
        if c == '\\' and i + 1 < len(s):
            nxt = s[i + 1]
            out += nxt if not nxt.isalnum() else ''
            i += 2
            continue
        if c == '(':
            depth, j = 0, i
            while j < len(s):
                if s[j] == '\\':
                    j += 2
                    continue
                if s[j] == '(':
                    depth += 1
                elif s[j] == ')':
                    depth -= 1
                    if depth == 0:
                        break
                j += 1
            group = s[i:j + 1]
            named = re.match(r'\(\?P<(\w+)>(.*)\)$', group, re.S)
            if named:
                name, inner = named.groups()
                if name == 'format':
                    fmt = True
                types[name] = guess_type(inner)
                out += '{%s}' % name
            elif group.startswith('(?'):
                inner = re.sub(r'^\(\?[:=!]?', '', group[:-1])
                if re.fullmatch(r'[\w/.-]*', inner):
                    out += inner
            else:
                unnamed += 1
                name = f'arg{unnamed}'
                types[name] = guess_type(group[1:-1])
                out += '{%s}' % name
            i = j + 1
            if i < len(s) and s[i] in '?*+':
                i += 1
            continue
        if c in '?*+^$':
            i += 1
            continue
        if c == '[':  # a character class outside a group: keep nothing
            j = s.find(']', i)
            i = j + 1 if j > 0 else len(s)
            if i < len(s) and s[i] in '?*+':
                i += 1
            continue
        out += c
        i += 1
    return out, types, fmt


def docstring(obj, skip_modules=()) -> str:
    if obj is None:
        return ''
    try:
        obj = inspect.unwrap(obj)
    except Exception:  # noqa: BLE001
        pass
    module = getattr(obj, '__module__', '') or ''
    if skip_modules and module.split('.')[0] in skip_modules:
        return ''
    doc = obj.__doc__ if isinstance(getattr(obj, '__doc__', None), str) else ''
    return inspect.cleandoc(doc) if doc else ''


def guess_login(api: Api, candidates: List) -> None:
    """Mark the request that returns a token, so the collection can store it."""
    if api.auth not in ('bearer', 'token'):
        return
    chosen = None
    if candidates:
        chosen = sorted(candidates, key=lambda c: (c[0], len(c[1].path)))[0][1]
    else:
        posts = [r for r in api.routes if r.method == 'POST' and LOGIN_PATH.search(r.path.rstrip('/') + '/')
                 and not NOT_LOGIN.search(r.path)]
        if posts:
            # API views before Django form views (those need a CSRF cookie and return HTML)
            chosen = sorted(posts, key=lambda r: (r.csrf, 0 if re.search(r'login|token/?$', r.path, re.I) else 1,
                                                  len(r.path)))[0]
    if chosen is not None:
        chosen.is_login = True
        chosen.auth = 'none'
        api.login_path = chosen.path
