"""Command line: routeman init | generate | routes."""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
from pathlib import Path
from typing import List, Optional

from . import __version__
from .config import AUTH_TYPES, CONFIG_FILE, Config, dump, load
from .model import Api
from .project import ProjectError, detect, import_app, load_env_file

FRAMEWORKS = ('django', 'flask', 'fastapi')
DEFAULT_PORTS = {'django': 8000, 'flask': 5000, 'fastapi': 8000}
COLOR = sys.stdout.isatty() and os.environ.get('NO_COLOR') is None


def paint(text, code):
    return f'\033[{code}m{text}\033[0m' if COLOR else text


def ok(msg):
    print(paint('✓', '32'), msg)


def warn(msg):
    print(paint('!', '33'), msg, file=sys.stderr)


def slug(text: str) -> str:
    return re.sub(r'[^a-z0-9]+', '-', text.lower()).strip('-') or 'api'


# --- settings resolution -----------------------------------------------------------------------

def resolve(args, root: Path) -> Config:
    cfg = load(root)
    configured_envs = bool(cfg.environments)
    if args.framework:
        cfg.framework = args.framework
    if args.app:
        cfg.app = args.app
    if getattr(args, 'name', None):
        cfg.name = args.name
    if getattr(args, 'output', None):
        cfg.output = args.output
    if getattr(args, 'exclude', None):
        cfg.exclude = cfg.exclude + args.exclude
    if getattr(args, 'auth', None):
        cfg.auth = args.auth
    if getattr(args, 'login', None):
        cfg.login = args.login
    if getattr(args, 'env_file', None):
        cfg.env_file = args.env_file
    for item in getattr(args, 'env', None) or []:
        if '=' not in item:
            raise ProjectError(f"--env expects NAME=URL, got '{item}'")
        key, _, value = item.partition('=')
        cfg.environments[key.strip()] = value.strip()
    if getattr(args, 'base_url', None):
        cfg.environments = {'local': args.base_url, **{k: v for k, v in cfg.environments.items() if k != 'local'}}
    if not cfg.framework or not cfg.app:
        framework, app = detect(root)
        if not cfg.framework:
            cfg.framework = framework or ''
        if not cfg.app and framework == cfg.framework:
            cfg.app = app or ''
    if cfg.framework not in FRAMEWORKS:
        raise ProjectError('could not detect the framework; pass --framework django|flask|fastapi '
                           f'and --app, or run `routeman init` in the project folder ({root})')
    if not cfg.app:
        what = 'settings module (e.g. mysite.settings)' if cfg.framework == 'django' else 'application (module:app)'
        raise ProjectError(f'could not find the {what}; pass --app')
    if not configured_envs and 'local' not in cfg.environments:
        # -e only adds environments; the default local one stays first
        cfg.environments = {'local': f'http://localhost:{DEFAULT_PORTS[cfg.framework]}', **cfg.environments}
    if not cfg.name:
        cfg.name = humanize_name(root.name)
    return cfg


def humanize_name(text: str) -> str:
    words = re.split(r'[-_\s]+', text)
    return ' '.join(w[:1].upper() + w[1:] for w in words if w) + ' API'


# --- extraction ------------------------------------------------------------------------------

def extract(cfg: Config, root: Path) -> Api:
    os.chdir(root)
    env_file = Path(cfg.env_file) if cfg.env_file else root / '.env'
    if not env_file.is_absolute():
        env_file = root / env_file
    if env_file.is_file():
        load_env_file(env_file)
    elif cfg.env_file:
        raise ProjectError(f'env file not found: {env_file}')
    if cfg.framework == 'django':
        from .extractors import django as ex
        try:
            ex.setup(cfg.app, str(root))
        except Exception as exc:  # noqa: BLE001
            raise ProjectError(project_failure('Django settings ' + repr(cfg.app), exc)) from exc
        try:
            api = ex.extract(cfg.app, str(root), cfg.exclude)
        except ImportError as exc:  # a dependency of the project (e.g. an auth backend) is missing
            raise ProjectError(project_failure('the URLconf of ' + repr(cfg.app), exc)) from exc
    else:
        try:
            app = import_app(cfg.app, root)
        except ProjectError:
            raise
        except Exception as exc:  # noqa: BLE001
            raise ProjectError(project_failure(repr(cfg.app), exc)) from exc
        if cfg.framework == 'flask':
            if not hasattr(app, 'url_map'):
                raise ProjectError(f"'{cfg.app}' is not a Flask application")
            from .extractors import flask as ex
        else:
            if not hasattr(app, 'openapi'):
                raise ProjectError(f"'{cfg.app}' is not a FastAPI application")
            from .extractors import fastapi as ex
        api = ex.extract(app, cfg.exclude)
    apply_auth(api, cfg)
    return api


def project_failure(what: str, exc: Exception) -> str:
    hint = ''
    if isinstance(exc, (ImportError, ModuleNotFoundError)):
        hint = ' Run routeman from the virtualenv the project is installed in.'
    elif isinstance(exc, KeyError) or 'environ' in str(exc).lower() or 'improperly' in type(exc).__name__.lower():
        hint = ' The project probably needs environment variables: put them in .env or pass --env-file.'
    return f'loading {what} failed: {type(exc).__name__}: {exc}.{hint}'


def apply_auth(api: Api, cfg: Config):
    from .extractors.common import guess_login
    if cfg.auth and cfg.auth != 'auto':
        api.auth = cfg.auth
    if cfg.token_prefix:
        api.token_prefix = cfg.token_prefix
        if api.auth == 'bearer' and cfg.token_prefix != 'Bearer':
            api.auth = 'token'
    if cfg.auth_header:
        api.auth_header = cfg.auth_header
    if cfg.login:
        target = '/' + cfg.login.strip('/')
        for r in api.routes:
            r.is_login = False
        match = [r for r in api.routes if r.method == 'POST' and r.path.rstrip('/') == target]
        if not match:
            warn(f'login path {cfg.login} is not a POST route of this project')
        for r in match:
            r.is_login = True
            r.auth = 'none'
            api.login_path = r.path
    elif cfg.auth not in ('auto', '') and api.login_path is None:
        guess_login(api, [])


# --- commands --------------------------------------------------------------------------------

def cmd_generate(args) -> int:
    started = time.perf_counter()
    root = Path(args.project).resolve()
    cfg = resolve(args, root)
    api = extract(cfg, root)
    from .postman import Writer
    if not api.routes:
        raise ProjectError('no routes found; check --app / exclude patterns')
    first_url = next(iter(cfg.environments.values()))
    writer = Writer(api, cfg.name, first_url)
    collection = writer.collection()
    if args.stdout:
        json.dump(collection, sys.stdout, indent=2, ensure_ascii=False)
        sys.stdout.write('\n')
        return 0
    out = Path(cfg.output)
    if not out.is_absolute():
        out = root / out
    out.mkdir(parents=True, exist_ok=True)
    base = slug(cfg.name)
    files = [out / f'{base}.postman_collection.json']
    files[0].write_text(json.dumps(collection, indent=2, ensure_ascii=False) + '\n', encoding='utf-8')
    for env_name, url in cfg.environments.items():
        path = out / f'{base}.{slug(env_name)}.postman_environment.json'
        path.write_text(json.dumps(writer.environment(env_name, url), indent=2, ensure_ascii=False) + '\n',
                        encoding='utf-8')
        files.append(path)
    for w in api.warnings:
        warn(w)
    methods = {}
    for r in api.routes:
        methods[r.method] = methods.get(r.method, 0) + 1
    summary = ', '.join(f'{n} {m}' for m, n in sorted(methods.items(), key=lambda kv: -kv[1]))
    ok(f'{api.framework}: {len(api.routes)} requests ({summary})'
       + (f', {len(api.websockets)} websocket(s)' if api.websockets else ''))
    auth = api.auth + (f' (login: POST {api.login_path})' if api.login_path else '')
    ok(f'auth: {auth}')
    for f in files:
        ok(f'wrote {os.path.relpath(f, Path.cwd()) if str(f).startswith(str(Path.cwd())) else f}')
    print(paint(f'  done in {time.perf_counter() - started:.2f}s - import the files in Postman '
                '(File → Import)', '2'))
    return 0


def cmd_routes(args) -> int:
    root = Path(args.project).resolve()
    cfg = resolve(args, root)
    api = extract(cfg, root)
    if args.json:
        from dataclasses import asdict

        def default(o):
            return None
        print(json.dumps([asdict(r) for r in api.routes], indent=2, default=default))
        return 0
    width = max((len(r.path) for r in api.routes), default=10)
    for r in sorted(api.routes, key=lambda r: (r.path, r.method)):
        body = ''
        if r.body is not None:
            body = f'{r.body.mode}: ' + ', '.join(f.name for f in r.body.fields) if r.body.fields else r.body.mode
        lock = '' if r.auth == 'none' or api.auth in ('none',) else '🔒'
        print(f'{r.method:7} {r.path:{width}}  {lock:1} {paint(body, "2")}')
    print(paint(f'{len(api.routes)} routes, auth: {api.auth}', '2'))
    for w in api.warnings:
        warn(w)
    return 0


def ask(prompt: str, default: str = '', choices=None) -> str:
    hint = f' [{default}]' if default else ''
    while True:
        try:
            answer = input(f'{prompt}{hint}: ').strip()
        except EOFError:
            answer = ''
        answer = answer or default
        if not choices or answer in choices:
            return answer
        print(f'  choose one of: {", ".join(choices)}')


def cmd_init(args) -> int:
    root = Path(args.project).resolve()
    target = root / CONFIG_FILE
    if target.exists() and not args.force:
        raise ProjectError(f'{CONFIG_FILE} already exists (use --force to overwrite)')
    detected, detected_app = detect(root)
    framework = args.framework or detected or ''
    app = args.app or (detected_app if framework == detected else '') or ''
    cfg = Config(name=args.name or humanize_name(root.name), framework=framework, app=app)
    port = DEFAULT_PORTS.get(framework, 8000)
    local = args.base_url or f'http://localhost:{port}'
    production = ''
    if not args.yes:
        print(paint('routeman init', '1') + ' — press Enter to accept the value in brackets\n')
        cfg.name = ask('Collection name', cfg.name)
        cfg.framework = ask('Framework (django/flask/fastapi)', cfg.framework, FRAMEWORKS)
        what = 'Django settings module' if cfg.framework == 'django' else 'Application (module:app)'
        cfg.app = ask(what, cfg.app if cfg.framework == framework else '')
        port = DEFAULT_PORTS.get(cfg.framework, port)
        local = args.base_url or f'http://localhost:{port}'
        local = ask('Local base URL', local)
        production = ask('Production base URL (optional)', '')
        cfg.auth = ask('Auth (auto/none/bearer/token/basic/apikey/session)', 'auto', AUTH_TYPES)
        cfg.login = ask('Login path that returns a token (optional, auto-detected)', '')
        cfg.output = ask('Output folder', cfg.output)
    if cfg.framework not in FRAMEWORKS:
        raise ProjectError('could not detect the framework; pass --framework')
    cfg.environments = {'local': local}
    if production:
        cfg.environments['production'] = production
    for item in args.env or []:
        key, _, value = item.partition('=')
        cfg.environments[key.strip()] = value.strip()
    target.write_text(dump(cfg), encoding='utf-8')
    ok(f'wrote {CONFIG_FILE} ({cfg.framework}, app: {cfg.app or "?"})')
    print('  next: ' + paint('routeman generate', '1'))
    return 0


# --- parser ----------------------------------------------------------------------------------

def common(p):
    p.add_argument('-C', '--project', default='.', help='project folder (default: current folder)')
    p.add_argument('-f', '--framework', choices=FRAMEWORKS, help='framework (default: detect)')
    p.add_argument('-a', '--app', help='Django settings module, or Flask/FastAPI "module:app" / "module:create_app()"')
    p.add_argument('--env-file', help='file of KEY=VALUE lines loaded before importing the project (default: .env)')
    p.add_argument('-x', '--exclude', action='append', metavar='REGEX', help='leave out matching paths (repeatable)')


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog='routeman',
        description='Generate Postman collections from Django, Flask and FastAPI projects - no schema or '
                    'documentation library needed.',
        epilog='Example: cd myproject && routeman generate')
    parser.add_argument('-V', '--version', action='version', version=f'routeman {__version__}')
    sub = parser.add_subparsers(dest='command', metavar='command')

    g = sub.add_parser('generate', aliases=['gen'], help='write the Postman collection and environments')
    common(g)
    g.add_argument('-n', '--name', help='collection name')
    g.add_argument('-o', '--output', help='output folder (default: postman)')
    g.add_argument('-b', '--base-url', help='base URL of the "local" environment')
    g.add_argument('-e', '--env', action='append', metavar='NAME=URL', help='add an environment (repeatable)')
    g.add_argument('--auth', choices=AUTH_TYPES, help='override the detected auth scheme')
    g.add_argument('--login', metavar='PATH', help='POST path whose response contains the token')
    g.add_argument('--stdout', action='store_true', help='print the collection instead of writing files')
    g.set_defaults(func=cmd_generate)

    r = sub.add_parser('routes', aliases=['ls'], help='list the routes routeman finds')
    common(r)
    r.add_argument('--json', action='store_true', help='machine-readable output')
    r.set_defaults(func=cmd_routes)

    i = sub.add_parser('init', help=f'create {CONFIG_FILE} with the project details')
    i.add_argument('-C', '--project', default='.', help='project folder (default: current folder)')
    i.add_argument('-f', '--framework', choices=FRAMEWORKS)
    i.add_argument('-a', '--app')
    i.add_argument('-n', '--name')
    i.add_argument('-b', '--base-url')
    i.add_argument('-e', '--env', action='append', metavar='NAME=URL')
    i.add_argument('-y', '--yes', action='store_true', help='accept detected values without asking')
    i.add_argument('--force', action='store_true', help=f'overwrite an existing {CONFIG_FILE}')
    i.set_defaults(func=cmd_init)
    return parser


def main(argv: Optional[List[str]] = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if not getattr(args, 'command', None):
        parser.print_help()
        return 0
    try:
        return args.func(args)
    except ProjectError as exc:
        print(paint('error:', '31'), exc, file=sys.stderr)
        return 2
    except KeyboardInterrupt:
        return 130
    except Exception as exc:  # noqa: BLE001
        if os.environ.get('ROUTEMAN_DEBUG'):
            raise
        print(paint('error:', '31'), f'routeman failed: {type(exc).__name__}: {exc}', file=sys.stderr)
        print('  rerun with ROUTEMAN_DEBUG=1 for the traceback, and please report it with that output.',
              file=sys.stderr)
        return 1


if __name__ == '__main__':  # pragma: no cover
    sys.exit(main())
