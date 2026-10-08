"""routeman.toml (or [tool.routeman] in pyproject.toml)."""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional

try:
    import tomllib  # Python 3.11+
except ModuleNotFoundError:  # pragma: no cover - older Pythons
    import tomli as tomllib  # type: ignore

CONFIG_FILE = 'routeman.toml'
AUTH_TYPES = ('auto', 'none', 'bearer', 'token', 'basic', 'apikey', 'session')


@dataclass
class Config:
    name: str = ''
    framework: str = ''                  # django | flask | fastapi ('' = detect)
    app: str = ''                        # Django settings module, or module:attr
    output: str = 'postman'
    environments: Dict[str, str] = field(default_factory=dict)
    exclude: List[str] = field(default_factory=list)
    auth: str = 'auto'
    auth_header: str = ''
    token_prefix: str = ''
    login: str = ''
    env_file: str = ''
    source: Optional[Path] = None


def load(root: Path) -> Config:
    cfg = Config()
    data = None
    path = root / CONFIG_FILE
    if path.exists():
        data = tomllib.loads(path.read_text(encoding='utf-8')).get('routeman', {})
        cfg.source = path
    else:
        pyproject = root / 'pyproject.toml'
        if pyproject.exists():
            data = tomllib.loads(pyproject.read_text(encoding='utf-8')).get('tool', {}).get('routeman')
            if data is not None:
                cfg.source = pyproject
    if not data:
        return cfg
    auth = data.get('auth') or {}
    if isinstance(auth, str):
        auth = {'type': auth}
    cfg.name = str(data.get('name', ''))
    cfg.framework = str(data.get('framework', '')).lower()
    cfg.app = str(data.get('app', ''))
    cfg.output = str(data.get('output', cfg.output))
    cfg.environments = {str(k): str(v) for k, v in (data.get('environments') or {}).items()}
    cfg.exclude = [str(x) for x in data.get('exclude') or []]
    cfg.auth = str(auth.get('type', 'auto')).lower()
    cfg.auth_header = str(auth.get('header', ''))
    cfg.token_prefix = str(auth.get('prefix', ''))
    cfg.login = str(auth.get('login', ''))
    cfg.env_file = str(data.get('env_file', ''))
    return cfg


def _s(value: str) -> str:
    return json.dumps(value)  # a valid TOML basic string


def dump(cfg: Config) -> str:
    lines = [
        '# routeman configuration - https://pypi.org/project/routeman/',
        '# Generate the Postman collection with:  routeman generate',
        '',
        '[routeman]',
        f'name = {_s(cfg.name)}',
        f'framework = {_s(cfg.framework)}',
        f'# Django: settings module.  Flask / FastAPI: "module:app" or "module:create_app()"',
        f'app = {_s(cfg.app)}',
        f'output = {_s(cfg.output)}',
    ]
    if cfg.env_file:
        lines.append(f'env_file = {_s(cfg.env_file)}')
    lines.append('# Regular expressions of paths to leave out, e.g. "^/internal/"')
    lines.append('exclude = [' + ', '.join(_s(x) for x in cfg.exclude) + ']')
    lines += ['', '[routeman.environments]', '# one Postman environment file per entry']
    for key, value in (cfg.environments or {'local': 'http://localhost:8000'}).items():
        lines.append(f'{key} = {_s(value)}')
    lines += ['', '[routeman.auth]', '# auto | none | bearer | token | basic | apikey | session',
              f'type = {_s(cfg.auth or "auto")}']
    if cfg.login:
        lines.append(f'login = {_s(cfg.login)}')
    else:
        lines.append('# login = "/api/token/"      # request whose response holds the token')
    if cfg.token_prefix:
        lines.append(f'prefix = {_s(cfg.token_prefix)}')
    if cfg.auth_header:
        lines.append(f'header = {_s(cfg.auth_header)}')
    return '\n'.join(lines) + '\n'
