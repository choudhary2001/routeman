"""Find the framework and application of a project, load .env files, import the app."""
from __future__ import annotations

import importlib
import os
import re
import sys
from pathlib import Path
from typing import Optional, Tuple

SKIP_DIRS = {'.git', '.venv', 'venv', 'env', '.env', 'node_modules', '__pycache__', 'site-packages', 'build',
             'dist', '.tox', '.nox', '.mypy_cache', '.pytest_cache', 'migrations', 'static', 'media', 'tests',
             'test', 'docs'}
PREFERRED = ('main.py', 'app.py', 'application.py', 'wsgi.py', 'asgi.py', 'server.py', 'api.py', '__init__.py')


class ProjectError(Exception):
    """A problem the user can fix; printed without a traceback."""


def load_env_file(path: Path) -> int:
    """KEY=VALUE lines into os.environ, never overriding variables that are already set."""
    count = 0
    for line in path.read_text(encoding='utf-8', errors='replace').splitlines():
        line = line.strip()
        if not line or line.startswith('#') or '=' not in line:
            continue
        if line.startswith('export '):
            line = line[7:]
        key, _, value = line.partition('=')
        key, value = key.strip(), value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in '"\'':
            value = value[1:-1]
        elif ' #' in value:
            value = value.split(' #', 1)[0].rstrip()
        if key and key not in os.environ:
            os.environ[key] = value
            count += 1
    return count


def python_files(root: Path, depth=3):
    def walk(d: Path, level: int):
        try:
            entries = sorted(d.iterdir(), key=lambda p: (p.name not in PREFERRED, p.name))
        except OSError:
            return
        for p in entries:
            if p.is_dir():
                if level < depth and p.name not in SKIP_DIRS and not p.name.startswith('.'):
                    yield from walk(p, level + 1)
            elif p.suffix == '.py':
                yield p
    yield from walk(root, 0)


def module_name(root: Path, file: Path) -> str:
    rel = file.relative_to(root).with_suffix('')
    parts = list(rel.parts)
    if parts and parts[0] == 'src':
        parts = parts[1:]
    if parts and parts[-1] == '__init__':
        parts = parts[:-1]
    return '.'.join(parts)


def detect(root: Path) -> Tuple[Optional[str], Optional[str]]:
    """(framework, app) — app is a settings module for Django, 'module:attr' otherwise."""
    manage = root / 'manage.py'
    if manage.exists():
        m = re.search(r"DJANGO_SETTINGS_MODULE['\"]\s*,\s*['\"]([\w.]+)['\"]", manage.read_text(errors='replace'))
        if m:
            return 'django', m.group(1)
    found = {}
    for file in python_files(root):
        try:
            text = file.read_text(encoding='utf-8', errors='replace')
        except OSError:
            continue
        for framework, ctor in (('fastapi', 'FastAPI'), ('flask', 'Flask')):
            if framework in found:
                continue
            m = re.search(r'^(\w+)\s*(?::\s*\w+\s*)?=\s*(?:\w+\.)?%s\(' % ctor, text, re.M)
            if m:
                found[framework] = f'{module_name(root, file)}:{m.group(1)}'
                continue
            if framework == 'flask' and re.search(r'\bfrom flask import\b.*\bFlask\b|\bimport flask\b', text):
                f = re.search(r'^def (create_app|make_app|build_app|get_app)\(', text, re.M)
                if f:
                    found[framework] = f'{module_name(root, file)}:{f.group(1)}()'
        if 'fastapi' in found:
            break
    if 'fastapi' in found:
        return 'fastapi', found['fastapi']
    if 'flask' in found:
        return 'flask', found['flask']
    settings = next((p for p in python_files(root, 2) if p.name == 'settings.py'), None)
    if settings is not None:
        return 'django', module_name(root, settings)
    return None, None


def import_app(spec: str, root: Path):
    """'package.module:app', 'module:create_app()', or 'module' (first app found in it)."""
    if str(root) not in sys.path:
        sys.path.insert(0, str(root))
    if (root / 'src').is_dir() and str(root / 'src') not in sys.path:
        sys.path.insert(1, str(root / 'src'))
    module_path, _, attr = spec.partition(':')
    module = importlib.import_module(module_path)
    if not attr:
        for name in ('app', 'application', 'api'):
            if hasattr(module, name):
                attr = name
                break
        else:
            for name in ('create_app', 'make_app'):
                if hasattr(module, name):
                    attr = name + '()'
                    break
    if not attr:
        raise ProjectError(f"no application found in '{module_path}'; use --app {module_path}:<name>")
    try:
        obj = eval(attr, vars(module))  # noqa: S307 - the user's own module and expression
    except Exception as exc:  # noqa: BLE001
        raise ProjectError(f"could not get '{attr}' from '{module_path}': {type(exc).__name__}: {exc}") from exc
    if callable(obj) and not hasattr(obj, 'url_map') and not hasattr(obj, 'routes') and attr.isidentifier():
        try:
            obj = obj()
        except TypeError:
            pass
    return obj
