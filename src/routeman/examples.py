"""Sample values for request fields: by declared type first, then by field name."""
from __future__ import annotations

import datetime
import decimal
import enum
import re
import uuid
from typing import Any, List

from .model import NO_DEFAULT, Field

SAMPLE_UUID = '3fa85f64-5717-4562-b3fc-2c963f66afa6'

# Field-name hints, checked in order; first match wins. Only used for plain strings/numbers.
NAME_HINTS = [
    (r'(^|_)e?mail(_address)?$', 'email', 'user@example.com'),
    (r'password|passwd|secret', 'string', 'Str0ngPassw0rd!'),
    (r'(^|_)(phone|mobile|contact_number|phone_number)$', 'string', '+919876543210'),
    (r'(^|_)username$', 'string', 'johndoe'),
    (r'(^|_)first_?name$', 'string', 'John'),
    (r'(^|_)last_?name$', 'string', 'Doe'),
    (r'(^|_)(full_?name|name)$', 'string', 'John Doe'),
    (r'(^|_)(url|link|website|homepage)$', 'url', 'https://example.com'),
    (r'(^|_)(slug)$', 'string', 'sample-slug'),
    (r'(^|_)(title|subject)$', 'string', 'Sample title'),
    (r'(^|_)(description|bio|about|summary|content|body|text|message|comment|caption|note|notes)$',
     'string', 'Sample text'),
    (r'(^|_)(otp|code|pin)$', 'string', '123456'),
    (r'(^|_)refresh(_token)?$', 'string', '{{refresh_token}}'),
    (r'(^|_)(token|access|access_token)$', 'string', '{{access_token}}'),
    (r'(^|_)(city)$', 'string', 'Mumbai'),
    (r'(^|_)(country)$', 'string', 'India'),
    (r'(^|_)(zip|zipcode|postal_code|pincode)$', 'string', '400001'),
    (r'(^|_)(address|street)$', 'string', '221B Baker Street'),
    (r'(^|_)(gender)$', 'string', 'male'),
    (r'(^|_)(currency)$', 'string', 'INR'),
    (r'(^|_)(lang|language|locale)$', 'string', 'en'),
    (r'(^|_)(color|colour)$', 'string', '#3366ff'),
    (r'(^|_)(ip|ip_address)$', 'string', '127.0.0.1'),
    (r'(^|_)(dob|date_of_birth|birth_date|birthday)$', 'date', '2000-01-31'),
]
_HINTS = [(re.compile(p, re.I), t, v) for p, t, v in NAME_HINTS]


def plain(value: Any) -> Any:
    """Make a default/choice value JSON-serialisable."""
    if isinstance(value, enum.Enum):
        return plain(value.value)
    if isinstance(value, (datetime.datetime, datetime.date, datetime.time)):
        return value.isoformat()
    if isinstance(value, uuid.UUID):
        return str(value)
    if isinstance(value, decimal.Decimal):
        return float(value)
    if isinstance(value, (list, tuple, set, frozenset)):
        return [plain(v) for v in value]
    if isinstance(value, dict):
        return {str(k): plain(v) for k, v in value.items()}
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    return str(value)


def by_type(kind: str) -> Any:
    return {
        'string': 'string',
        'integer': 1,
        'number': 1.5,
        'boolean': True,
        'uuid': SAMPLE_UUID,
        'date': '2026-01-31',
        'datetime': '2026-01-31T10:00:00Z',
        'time': '10:00:00',
        'email': 'user@example.com',
        'url': 'https://example.com',
        'file': None,
        'any': 'string',
        'object': {},
        'array': [],
    }.get(kind, 'string')


def example(f: Field, depth: int = 0) -> Any:
    if f.example is not NO_DEFAULT:
        return plain(f.example)
    if f.default is not NO_DEFAULT and f.default is not None and not callable(f.default):
        return plain(f.default)
    if f.choices:
        return plain(f.choices[0])
    if f.type == 'object':
        if depth > 5:
            return {}
        return {c.name: example(c, depth + 1) for c in f.children or ()}
    if f.type == 'array':
        if f.item is None or depth > 5:
            return []
        return [example(f.item, depth + 1)]
    if f.type in ('string', 'any', 'integer', 'number'):
        for pattern, kind, value in _HINTS:
            if pattern.search(f.name or ''):
                if f.type in ('integer', 'number') and not isinstance(value, (int, float)):
                    break
                return value
    return by_type(f.type)


def body_example(fields: List[Field]) -> dict:
    return {f.name: example(f) for f in fields}


def path_example(f: Field) -> str:
    """Path values: by type only (a name hint like 'code' would suggest a wrong format)."""
    if f.example is not NO_DEFAULT:
        value = plain(f.example)
    elif f.type == 'string':
        value = 'value'
    else:
        value = by_type(f.type)
    return '' if value is None else str(value)
