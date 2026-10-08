"""Path normalisation, variable names, folders and request titles."""
from __future__ import annotations

import re
from typing import List, Tuple

# Placeholders in our normalised paths: /users/{user_id}/
PARAM = re.compile(r'\{(\w+)(?::[^}]*)?\}')
VERSION = re.compile(r'^v\d+(\.\d+)?$', re.I)
PREFIXES = {'api', 'apis', 'rest', 'json'}

HUMAN = {
    'list': 'List', 'create': 'Create', 'retrieve': 'Get', 'update': 'Update',
    'partial_update': 'Partially update', 'destroy': 'Delete',
}


def singular(word: str) -> str:
    w = word.lower()
    if w.endswith('ies') and len(w) > 3:
        return word[:-3] + 'y'
    if re.search(r'(ss|x|z|ch|sh)es$', w):
        return word[:-2]
    if w.endswith('s') and not w.endswith(('ss', 'us', 'is')):
        return word[:-1]
    return word


def snake(text: str) -> str:
    text = re.sub(r'[^0-9a-zA-Z]+', '_', text)
    text = re.sub(r'([a-z0-9])([A-Z])', r'\1_\2', text)
    return text.strip('_').lower()


def humanize(name: str) -> str:
    name = snake(name).replace('_', ' ').strip()
    return name[:1].upper() + name[1:] if name else name


def segments(path: str) -> List[str]:
    return [s for s in path.strip('/').split('/') if s]


def variable_names(path: str) -> Tuple[str, dict]:
    """Rename ambiguous {id}/{pk} after the collection they index: /users/{id} -> {user_id}."""
    renames = {}
    parts = segments(path)
    for i, part in enumerate(parts):
        m = PARAM.fullmatch(part)
        if not m:
            continue
        name = m.group(1)
        if name.lower() in ('id', 'pk', 'uuid', 'key', 'slug') and i > 0 and not PARAM.fullmatch(parts[i - 1]):
            base = snake(singular(parts[i - 1]))
            renames[name] = f'{base}_{name.lower() if name.lower() != "pk" else "id"}'
    return path, renames


def resource(path: str, folder: str = '') -> str:
    """First meaningful path segment: /api/v1/users/{id}/ -> users."""
    for part in segments(path):
        low = part.lower()
        if PARAM.fullmatch(part) or low in PREFIXES or VERSION.match(low) or low == folder.lower():
            continue
        return part
    return ''


def title(route) -> str:
    if route.name:
        return route.name
    return f'{route.method} {route.path}'
