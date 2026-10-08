"""
End to end: generate collections for real Django / Flask / FastAPI projects with the installed
`routeman` command, validate them against the official Postman schema, then run every request
against the running application.
"""
import json
import os
import shutil
import socket
import subprocess
import sys
import time
from pathlib import Path

import jsonschema
import pytest

from runner import Runner, load

HERE = Path(__file__).parent
PROJECTS = HERE / 'projects'
SCHEMA = json.loads((HERE / 'data' / 'postman-collection-v2.1.json').read_text())
ROUTEMAN = [sys.executable, '-m', 'routeman']


def free_port():
    with socket.socket() as s:
        s.bind(('127.0.0.1', 0))
        return s.getsockname()[1]


def wait(port, proc, timeout=30):
    deadline = time.time() + timeout
    while time.time() < deadline:
        if proc.poll() is not None:
            raise RuntimeError(proc.stdout.read().decode(errors='replace'))
        with socket.socket() as s:
            if s.connect_ex(('127.0.0.1', port)) == 0:
                return
        time.sleep(0.1)
    raise RuntimeError('server did not start')


def copy_project(name, tmp_path):
    target = tmp_path / name
    shutil.copytree(PROJECTS / name, target, ignore=shutil.ignore_patterns('__pycache__', '*.sqlite3', 'postman'))
    return target


def generate(project, *extra):
    result = subprocess.run(ROUTEMAN + ['generate', *extra], cwd=project, capture_output=True, text=True,
                            timeout=120)
    assert result.returncode == 0, result.stderr + result.stdout
    return result


def all_requests(collection):
    out = []

    def walk(items):
        for item in items:
            if 'item' in item:
                walk(item['item'])
            else:
                out.append(item)
    walk(collection['item'])
    return out


@pytest.fixture
def server():
    procs = []

    def start(cmd, cwd, env=None):
        port = free_port()
        proc = subprocess.Popen([c.replace('{port}', str(port)) for c in cmd], cwd=cwd,
                                stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                env={**os.environ, **(env or {})})
        procs.append(proc)
        wait(port, proc)
        return f'http://127.0.0.1:{port}'
    yield start
    for proc in procs:
        proc.terminate()
        try:
            proc.wait(5)
        except subprocess.TimeoutExpired:
            proc.kill()


def check_schema(collection):
    jsonschema.validate(collection, SCHEMA)


# --- Django ---------------------------------------------------------------------------------

def test_django_end_to_end(tmp_path, server):
    project = copy_project('djshop', tmp_path)
    env = {'SHOP_SECRET': 'x' * 40, 'SHOP_DB': str(tmp_path / 'shop.sqlite3')}
    py = sys.executable
    subprocess.run([py, 'manage.py', 'migrate', '-v0'], cwd=project, env={**os.environ, **env}, check=True)
    subprocess.run([py, 'manage.py', 'shell', '-c',
                    "from django.contrib.auth.models import User; User.objects.create_user('alice', password='wonderland');"
                    "from shop.models import Category; Category.objects.create(id=1, name='Books')"],
                   cwd=project, env={**os.environ, **env}, check=True)
    base = server([py, 'manage.py', 'runserver', '127.0.0.1:{port}', '--noreload'], project, env)

    out = generate(project, '--base-url', base)  # SHOP_SECRET comes from the project's .env
    assert 'auth: bearer (login: POST /api/v1/auth/token/)' in out.stdout
    collection, environment = load(project / 'postman', 'djshop-api')
    check_schema(collection)
    names = {(i['request']['method'], i['request']['url']['raw'].split('?')[0]) for i in all_requests(collection)}
    assert not any('/admin/' in path for _, path in names)
    assert ('POST', '{{base_url}}/api/v1/products/{{product_id}}/discount/') in names
    assert 'ws/stock/{{product_id}}' in collection['info']['description']

    run = Runner(collection, environment, username='alice', password='wonderland')
    assert run.call('POST', '/api/v1/auth/token/').status_code == 200
    assert run.vars['access_token'] and run.vars['refresh_token']
    assert run.call('POST', '/api/v1/auth/token/refresh/').status_code == 200
    assert run.call('GET', '/api/v1/').status_code == 200
    assert run.call('GET', '/api/v1/categories/').status_code == 200
    assert run.call('GET', '/api/v1/categories/{{category_id}}/').status_code == 200

    created = run.call('POST', '/api/v1/products/')
    assert created.status_code == 201, created.text
    run.vars['product_id'] = created.json()['id']
    listed = run.call('GET', '/api/v1/products/')
    assert listed.status_code == 200 and listed.json()['count'] == 1
    for method in ('GET', 'PUT', 'PATCH'):
        response = run.call(method, '/api/v1/products/{{product_id}}/')
        assert response.status_code == 200, (method, response.text)
    assert run.call('POST', '/api/v1/products/{{product_id}}/discount/').status_code == 200

    order = run.call('POST', '/api/v1/orders/')  # the sample product id in the body does not exist
    assert order.status_code == 400 and set(order.json()) == {'lines'}, order.text
    assert run.call('GET', '/api/v1/orders/').status_code == 200
    assert run.call('POST', '/api/v1/contact/').status_code == 200
    assert run.call('GET', '/api/v1/reports/{{year}}/').status_code == 200
    run.vars['code'] = 'ABC'
    assert run.call('GET', '/api/v1/legacy/{{code}}/').status_code == 200
    assert run.call('GET', '/health/').status_code == 200
    assert run.call('POST', '/webhooks/payment/').status_code == 200
    assert run.call('GET', '/newsletter/').status_code == 200
    assert run.call('POST', '/newsletter/').status_code == 200
    assert run.call('GET', '/feedback/').status_code == 200   # sets the CSRF cookie
    feedback = run.call('POST', '/feedback/')
    assert feedback.status_code == 302, feedback.text          # valid form -> redirect
    assert run.call('DELETE', '/api/v1/products/{{product_id}}/').status_code == 204

    # Nothing in the collection makes the server fail
    for item, auth in run.items():
        assert run.send(item, auth).status_code < 500, item['name']


def test_django_project_needs_its_env_file(tmp_path):
    project = copy_project('djshop', tmp_path)
    (project / '.env').unlink()
    env = {k: v for k, v in os.environ.items() if k != 'SHOP_SECRET'}
    result = subprocess.run(ROUTEMAN + ['generate'], cwd=project, capture_output=True, text=True, env=env)
    assert result.returncode == 2
    assert 'SHOP_SECRET' in result.stderr and '--env-file' in result.stderr
    (tmp_path / 'secrets.env').write_text('SHOP_SECRET=abc\n')
    result = subprocess.run(ROUTEMAN + ['generate', '--env-file', str(tmp_path / 'secrets.env')], cwd=project,
                            capture_output=True, text=True, env=env)
    assert result.returncode == 0, result.stderr


# --- Flask ----------------------------------------------------------------------------------

def test_flask_end_to_end(tmp_path, server):
    project = copy_project('flaskshop', tmp_path)
    base = server([sys.executable, '-m', 'flask', 'run', '--port', '{port}'], project,
                  {'FLASK_APP': 'shopapp:create_app'})  # FLASK_APP works on Flask 2.0 too
    out = generate(project, '--base-url', base)
    assert 'auth: bearer (login: POST /auth/login)' in out.stdout
    collection, environment = load(project / 'postman', 'flaskshop-api')
    check_schema(collection)

    run = Runner(collection, environment, username='alice', password='wonderland')
    assert run.call('GET', '/api/books/{{book_id}}').status_code == 401   # before login
    assert run.call('POST', '/auth/login').status_code == 200
    assert run.call('POST', '/auth/refresh').status_code == 200          # sends the refresh token
    assert run.call('GET', '/auth/me').json() == {'user': 'alice'}
    assert run.call('GET', '/ping').status_code == 200
    assert run.call('GET', '/api/books/').status_code == 200
    created = run.call('POST', '/api/books/')
    assert created.status_code == 201, created.text
    assert run.call('GET', '/api/books/{{book_id}}').status_code == 200
    assert run.call('POST', '/api/books/{{book_id}}/cover').status_code == 200
    assert run.call('GET', '/api/books/{{book_id}}/reviews').status_code == 200
    assert run.call('POST', '/api/books/{{book_id}}/reviews').status_code == 201
    assert run.call('DELETE', '/api/books/{{book_id}}').status_code == 204
    for item, auth in run.items():
        assert run.send(item, auth).status_code < 500, item['name']


# --- FastAPI --------------------------------------------------------------------------------

def test_fastapi_end_to_end(tmp_path, server):
    project = copy_project('fastshop', tmp_path)
    base = server([sys.executable, '-m', 'uvicorn', 'app.main:app', '--port', '{port}'], project)
    out = generate(project, '--base-url', base, '--env', 'production=https://api.example.com')
    assert 'auth: bearer (login: POST /auth/token)' in out.stdout
    collection, environment = load(project / 'postman', 'fastshop-api')
    check_schema(collection)
    production = json.loads((project / 'postman' / 'fastshop-api.production.postman_environment.json').read_text())
    assert {v['key']: v['value'] for v in production['values']}['base_url'] == 'https://api.example.com'

    run = Runner(collection, environment, username='alice', password='wonderland')
    assert run.call('GET', '/api/v1/users/me').status_code == 401
    assert run.call('POST', '/auth/token').status_code == 200
    assert run.call('GET', '/api/v1/users/me').json() == {'user': 'alice'}
    created = run.call('POST', '/api/v1/items/')
    assert created.status_code == 201, created.text
    run.vars['item_id'] = created.json()['id']
    for method, path, code in [
        ('GET', '/api/v1/items/', 200), ('GET', '/api/v1/items/{{item_id}}', 200),
        ('PATCH', '/api/v1/items/{{item_id}}', 200), ('POST', '/api/v1/items/{{item_id}}/image', 200),
        ('GET', '/api/v1/items/by-ref/{{ref}}', 200), ('POST', '/api/v1/users/signup', 201),
        ('GET', '/health', 200), ('GET', '/internal/stats', 200), ('DELETE', '/api/v1/items/{{item_id}}', 204),
    ]:
        response = run.call(method, path)
        assert response.status_code == code, (method, path, response.text)
    for item, auth in run.items():
        assert run.send(item, auth).status_code < 500, item['name']


# --- CLI ------------------------------------------------------------------------------------

@pytest.mark.parametrize('name,framework,app', [
    ('djshop', 'django', 'djshop.settings'),
    ('flaskshop', 'flask', 'shopapp:create_app()'),
    ('fastshop', 'fastapi', 'app.main:app'),
])
def test_init_detects_and_generate_uses_the_config(tmp_path, name, framework, app):
    project = copy_project(name, tmp_path)
    result = subprocess.run(ROUTEMAN + ['init', '--yes', '-n', 'Shop', '-e', 'staging=https://staging.example.com'],
                            cwd=project, capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    config = (project / 'routeman.toml').read_text()
    assert f'framework = "{framework}"' in config and f'app = "{app}"' in config
    generate(project)
    files = sorted(p.name for p in (project / 'postman').iterdir())
    assert files == ['shop.local.postman_environment.json', 'shop.postman_collection.json',
                     'shop.staging.postman_environment.json']
    again = subprocess.run(ROUTEMAN + ['init', '--yes'], cwd=project, capture_output=True, text=True)
    assert again.returncode == 2 and 'already exists' in again.stderr


def test_stdout_and_exclude(tmp_path):
    project = copy_project('fastshop', tmp_path)
    result = subprocess.run(ROUTEMAN + ['generate', '--stdout', '-x', '^/internal'], cwd=project,
                            capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    collection = json.loads(result.stdout)
    paths = [i['request']['url']['raw'] for i in all_requests(collection)]
    assert paths and not any('/internal/' in p for p in paths)
    assert not (project / 'postman').exists()


def test_extra_environment_keeps_the_local_one(tmp_path):
    project = copy_project('flaskshop', tmp_path)
    generate(project, '-e', 'production=https://api.example.com')
    files = sorted(p.name for p in (project / 'postman').iterdir())
    assert files == ['flaskshop-api.local.postman_environment.json', 'flaskshop-api.postman_collection.json',
                     'flaskshop-api.production.postman_environment.json']
    local = json.loads((project / 'postman' / files[0]).read_text())
    assert {v['key']: v['value'] for v in local['values']}['base_url'] == 'http://localhost:5000'


def test_routes_listing(tmp_path):
    project = copy_project('flaskshop', tmp_path)
    result = subprocess.run(ROUTEMAN + ['routes'], cwd=project, capture_output=True, text=True)
    assert result.returncode == 0
    assert 'POST    /api/books/' in result.stdout and 'json: title, price, genre, tags, published' in result.stdout


def test_unknown_project_is_a_clear_error(tmp_path):
    result = subprocess.run(ROUTEMAN + ['generate'], cwd=tmp_path, capture_output=True, text=True)
    assert result.returncode == 2
    assert 'could not detect the framework' in result.stderr


def test_version():
    result = subprocess.run(ROUTEMAN + ['--version'], capture_output=True, text=True)
    from routeman import __version__
    assert result.stdout.strip() == f'routeman {__version__}'
