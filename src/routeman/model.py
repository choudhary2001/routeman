"""Framework-neutral description of an HTTP API, filled in by the extractors."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, List, Optional

# Field types understood by the example generator and the Postman writer.
TYPES = (
    'string', 'integer', 'number', 'boolean', 'array', 'object', 'file',
    'uuid', 'date', 'datetime', 'time', 'email', 'url', 'any',
)

NO_DEFAULT = object()


@dataclass
class Field:
    name: str
    type: str = 'string'
    required: bool = False
    description: str = ''
    default: Any = NO_DEFAULT
    example: Any = NO_DEFAULT
    choices: Optional[list] = None
    item: Optional['Field'] = None            # element of an array
    children: Optional[List['Field']] = None  # members of an object

    @property
    def has_file(self) -> bool:
        if self.type == 'file':
            return True
        if self.item is not None and self.item.has_file:
            return True
        return any(c.has_file for c in self.children or ())


@dataclass
class Body:
    mode: str = 'json'           # json | form (multipart) | urlencoded | raw
    fields: List[Field] = field(default_factory=list)
    example: Any = NO_DEFAULT    # a complete example, when the framework supplies one
    partial: bool = False        # fields were found by reading code; there may be more

    @property
    def fields_known(self) -> bool:
        """Required/optional comes from a declaration, not from reading code."""
        return not self.partial


@dataclass
class Route:
    path: str                                   # /users/{user_id}/
    method: str                                 # GET, POST, ...
    name: str = ''
    description: str = ''
    folder: List[str] = field(default_factory=list)
    path_params: List[Field] = field(default_factory=list)
    query: List[Field] = field(default_factory=list)
    headers: List[Field] = field(default_factory=list)
    body: Optional[Body] = None
    auth: Optional[str] = None                  # None = collection default; 'none' = public; 'refresh'
    csrf: bool = False                          # Django session view: send X-CSRFToken
    source: str = ''                            # module.function, for --verbose listings
    is_login: bool = False


@dataclass
class WebSocket:
    path: str
    description: str = ''
    source: str = ''


@dataclass
class Api:
    framework: str
    routes: List[Route] = field(default_factory=list)
    websockets: List[WebSocket] = field(default_factory=list)
    auth: str = 'none'                 # none | bearer | token | basic | apikey | session
    auth_header: str = 'Authorization'  # header for apikey auth
    token_prefix: str = 'Token'         # 'token' auth: Authorization: <prefix> <token>
    login_path: Optional[str] = None
    title: str = ''
    warnings: List[str] = field(default_factory=list)
