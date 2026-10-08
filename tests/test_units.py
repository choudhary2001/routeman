import json

import pytest

from routeman.examples import example, path_example
from routeman.extractors.common import regex_to_path
from routeman.model import Api, Body, Field, Route
from routeman.naming import resource, singular, variable_names
from routeman.openapi import fields_from_json_schema, from_openapi
from routeman.postman import Writer
from routeman.project import load_env_file
from routeman.scan import scan


@pytest.mark.parametrize('rx,path,types,fmt', [
    (r'^users/(?P<pk>[0-9]+)/$', 'users/{pk}/', {'pk': 'integer'}, False),
    (r'^users/(?P<pk>[^/.]+)\.(?P<format>[a-z0-9]+)/?$', 'users/{pk}.{format}/', None, True),
    (r'^api/(?P<id>[0-9a-f-]+)/x\.json$', 'api/{id}/x.json', {'id': 'uuid'}, False),
    (r'^a/(\d+)/b/$', 'a/{arg1}/b/', {'arg1': 'integer'}, False),
    (r'^docs/(?:v2/)?$', 'docs/v2/', {}, False),
    (r'^(?P<code>[A-Z]{3})/$', '{code}/', {'code': 'string'}, False),
])
def test_regex_to_path(rx, path, types, fmt):
    got_path, got_types, got_fmt = regex_to_path(rx)
    assert got_fmt is fmt
    if not fmt:
        assert got_path == path
        assert got_types == types


@pytest.mark.parametrize('word,expected', [
    ('users', 'user'), ('categories', 'category'), ('addresses', 'address'), ('boxes', 'box'),
    ('status', 'status'), ('news', 'new'), ('data', 'data'), ('class', 'class'),
])
def test_singular(word, expected):
    assert singular(word) == expected


def test_variable_names_and_resource():
    assert variable_names('/api/v1/users/{id}/posts/{pk}/')[1] == {'id': 'user_id', 'pk': 'post_id'}
    assert variable_names('/users/{user_id}/')[1] == {}
    assert resource('/api/v1/users/{id}/') == 'users'
    assert resource('/api/v2.1/shop/items/', 'shop') == 'items'


def view_with_everything(request, pk):
    data = json.loads(request.body)
    name = data['name']
    age = int(data.get('age', 0))
    tags = request.POST.getlist('tags')
    page = request.GET.get('page', 1)
    token = request.META.get('HTTP_X_CLIENT_TOKEN')
    upload = request.FILES['avatar']
    if 'nickname' in data:
        pass
    if request.method == 'POST':
        return name, age, tags, page, token, upload


def test_scan_reads_request_usage():
    s = scan(view_with_everything)
    assert s.ok
    assert s.body['name'].required and s.body['name'].type == 'string'
    assert s.body['age'].type == 'integer' and not s.body['age'].required
    assert s.body['tags'].type == 'array'
    assert 'nickname' in s.body
    assert s.query['page'].type == 'integer'
    assert s.headers['X-Client-Token'].name == 'X-Client-Token'
    assert s.files['avatar'].type == 'file'
    assert s.methods == {'POST'}
    assert 'json' in s.kinds and s.raw_body


class SignupSchema:  # resolved from the function's module by name
    pass


def view_with_schema(request):
    form = SignupSchema(request.POST)
    return form


def test_scan_resolves_schema_classes():
    s = scan(view_with_schema)
    assert s.schemas == [(SignupSchema, 'form')]


def test_examples_respect_types_names_and_constraints():
    assert example(Field('email')) == 'user@example.com'
    assert example(Field('price', type='number')) == 1.5
    assert example(Field('id', type='uuid')) == '3fa85f64-5717-4562-b3fc-2c963f66afa6'
    assert example(Field('status', choices=['draft', 'live'])) == 'draft'
    assert example(Field('when', type='datetime')) == '2026-01-31T10:00:00Z'
    assert path_example(Field('code')) == 'value'
    fields = fields_from_json_schema({'type': 'object', 'required': ['n'], 'properties': {
        'n': {'type': 'string', 'minLength': 12},
        'q': {'type': 'integer', 'minimum': 5},
        'g': {'type': 'string', 'pattern': '^password$'},
        'opt': {'anyOf': [{'type': 'integer'}, {'type': 'null'}], 'default': 3},
    }})
    by = {f.name: f for f in fields}
    assert len(example(by['n'])) >= 12 and by['n'].required
    assert example(by['q']) == 5
    assert example(by['g']) == 'password'
    assert example(by['opt']) == 3 and by['opt'].type == 'integer'


def test_openapi_security_and_refs():
    spec = {
        'openapi': '3.0.0', 'info': {'title': 'T'},
        'components': {
            'securitySchemes': {'key': {'type': 'apiKey', 'in': 'header', 'name': 'X-API-Key'}},
            'schemas': {'Pet': {'type': 'object', 'properties': {
                'name': {'type': 'string'}, 'owner': {'$ref': '#/components/schemas/Owner'}}},
                'Owner': {'type': 'object', 'properties': {'pet': {'$ref': '#/components/schemas/Pet'}}}}},
        'paths': {'/pets': {
            'post': {'security': [{'key': []}], 'requestBody': {'content': {'application/json': {
                'schema': {'$ref': '#/components/schemas/Pet'}}}}},
            'get': {'parameters': [{'name': 'limit', 'in': 'query', 'schema': {'type': 'integer'}}]}}},
    }
    api = from_openapi(spec)
    assert api.auth == 'apikey' and api.auth_header == 'X-API-Key'
    post = next(r for r in api.routes if r.method == 'POST')
    get = next(r for r in api.routes if r.method == 'GET')
    assert [f.name for f in post.body.fields] == ['name', 'owner']   # recursive ref does not loop
    assert get.auth == 'none' and get.query[0].name == 'limit'


def test_writer_patch_and_form_bodies():
    api = Api(framework='test', auth='bearer', routes=[
        Route(path='/things/{id}/', method='PATCH', body=Body(fields=[
            Field('name', required=True), Field('size', type='integer')])),
        Route(path='/things/', method='POST', body=Body(mode='form', fields=[
            Field('file', type='file', required=True), Field('note')])),
        Route(path='/open/', method='GET', auth='none'),
    ])
    writer = Writer(api, 'Things')
    collection = writer.collection()
    items = {i['request']['method']: i for f in collection['item'] for i in f['item']}
    assert json.loads(items['PATCH']['request']['body']['raw']) == {'name': 'John Doe'}
    assert items['PATCH']['request']['url']['raw'] == '{{base_url}}/things/{{thing_id}}/'
    form = items['POST']['request']['body']['formdata']
    assert form[0]['type'] == 'file' and form[1].get('disabled') is True
    assert items['GET']['request']['auth'] == {'type': 'noauth'}
    env = {v['key']: v['value'] for v in writer.environment('local', 'https://x.io/')['values']}
    assert env['base_url'] == 'https://x.io' and env['thing_id'] == '1'


def test_env_file(tmp_path, monkeypatch):
    monkeypatch.setenv('KEEP', 'original')
    f = tmp_path / '.env'
    f.write_text('# comment\nexport A=1\nB="two words"\nC=3 # trailing\nKEEP=changed\n')
    monkeypatch.delenv('A', raising=False)
    monkeypatch.delenv('B', raising=False)
    monkeypatch.delenv('C', raising=False)
    load_env_file(f)
    import os
    assert (os.environ['A'], os.environ['B'], os.environ['C'], os.environ['KEEP']) == ('1', 'two words', '3', 'original')
