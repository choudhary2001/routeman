# routeman: convert your Python API to a Postman collection

[![PyPI version](https://img.shields.io/pypi/v/routeman.svg)](https://pypi.org/project/routeman/)
[![Python versions](https://img.shields.io/pypi/pyversions/routeman.svg)](https://pypi.org/project/routeman/)
[![License: MIT](https://img.shields.io/pypi/l/routeman.svg)](https://github.com/choudhary2001/routeman/blob/main/LICENSE)
[![tests](https://github.com/choudhary2001/routeman/actions/workflows/ci.yml/badge.svg)](https://github.com/choudhary2001/routeman/actions/workflows/ci.yml)

**routeman converts your Django, Django REST framework (DRF), Flask or FastAPI API into a ready-to-use Postman collection and Postman environment with one command. It includes every endpoint, method, path variable, query parameter, request body, authentication method and login token. You don't need OpenAPI, Swagger, drf-spectacular, drf-yasg, flasgger or any other documentation library.**

```bash
pip install routeman
cd your-project
routeman generate
```

```
✓ django: 24 requests (11 GET, 9 POST, 2 PUT, 1 PATCH, 1 DELETE), 1 websocket(s)
✓ auth: bearer (login: POST /api/v1/auth/token/)
✓ wrote postman/your-project-api.postman_collection.json
✓ wrote postman/your-project-api.local.postman_environment.json
  done in 0.26s - import the files in Postman (File → Import)
```

routeman loads your application the same way your server does and reads its real routes. You get every endpoint with its methods, path variables, query parameters and request bodies filled with working example values, plus authentication and a login request that saves the token for you. Import the two files into Postman and start sending requests.

Use it to export a Python REST API to Postman, to share an API with your frontend or QA team, to onboard new developers, or to smoke-test every endpoint with the Postman Collection Runner or Newman.

Made by [Shwastik Tech Solutions Pvt Ltd](https://swastik.ai).

---

## What you get

| | |
|---|---|
| **Every route** | Every URL the framework would serve, including nested `include()`s, routers, blueprints and mounted routers. Admin and static files are left out. |
| **Folders** | One folder per Django app, Flask blueprint or FastAPI tag, with sub-folders per resource. |
| **Request bodies** | JSON, `x-www-form-urlencoded` or `multipart/form-data` (file uploads become Postman file pickers), with example values that pass validation: they respect choices, min/max, lengths, regex patterns and field names (`email` gets `user@example.com`). |
| **Path variables** | `{{product_id}}`, `{{user_id}}`… stored in the environment and named after the resource, typed from the model (UUID or integer). |
| **Query parameters** | Pagination, search, ordering, filters, plus anything your code reads from `request.GET`, `query_params` or `args`. Optional ones are added but switched off. |
| **Authentication** | Detected automatically: Bearer/JWT, `Token` (DRF authtoken, Knox), Basic, API key, or session. Public endpoints are set to *No Auth*. |
| **Login script** | The login or token request saves `access_token` / `refresh_token` from its response. Refresh endpoints send the refresh token. |
| **Environments** | One environment file per server (local, staging, production…). |
| **Smoke test** | Every request checks that the response is not a 5xx, so the Collection Runner or Newman can smoke-test the whole API. |
| **Docs** | Each request's description shows the view's docstring and a table of fields (type, required, allowed values). WebSocket routes (Django Channels, FastAPI) are listed in the collection description. |

## How it reads your project (no schema needed)

| Framework | Routes from | Bodies and parameters from |
|---|---|---|
| **Django REST framework** | the URLconf (routers, viewsets, `@action`, APIViews, `@api_view`) | serializers (nested, `many=True`, relations, choices, files, read-only fields skipped), the serializers a view builds itself (`Serializer(data=request.data)`), pagination, `SearchFilter`, `OrderingFilter`, django-filter `filterset_fields`/`filterset_class`, `permission_classes`, `authentication_classes` |
| **Django** | the URLconf (`path`, `re_path`, `include`, class-based and function views) | `form_class` and `ModelForm`s, `require_http_methods`, `request.method` checks, and the view code: `request.POST`, `request.GET`, `request.FILES`, `json.loads(request.body)` |
| **Flask** | `app.url_map` (blueprints, `MethodView`, Flask-RESTful resources) | the view code (`request.json`, `get_json()`, `form`, `args`, `files`), marshmallow schemas, flask-smorest `@arguments`, Pydantic models, `@jwt_required`, `@login_required`, Flask-HTTPAuth |
| **FastAPI** | FastAPI's built-in OpenAPI document, plus routes with `include_in_schema=False` | Pydantic models, `Query`/`Form`/`File`/`Header`, `Depends()` security (OAuth2, HTTP Bearer, API key) |

When a view declares no serializer, form or schema, routeman reads the view's source code to find the fields it uses, for example `request.data.get('email')`, `data['name']`, `int(request.data.get('age', 0))` or `request.args.get('page', type=int)`. Those requests are marked so you know the list was inferred.

## Commands

```bash
routeman generate                 # write postman/<name>.postman_collection.json + environments
routeman generate --stdout        # print the collection instead
routeman routes                   # list what routeman found (method, path, auth, body fields)
routeman init                     # save the project details in routeman.toml (asks a few questions)
routeman --version
```

Useful options (all optional; routeman detects everything it can):

| Option | Meaning |
|---|---|
| `-C, --project DIR` | project folder (default: current folder) |
| `-f, --framework` | `django`, `flask` or `fastapi` |
| `-a, --app` | Django settings module (`mysite.settings`) or Flask/FastAPI app: `main:app`, `myapp:create_app()` |
| `-n, --name` | collection name |
| `-o, --output DIR` | output folder (default `postman`) |
| `-b, --base-url URL` | base URL of the `local` environment |
| `-e, --env NAME=URL` | add an environment, e.g. `-e production=https://api.example.com` (repeatable) |
| `-x, --exclude REGEX` | leave out paths, e.g. `-x '^/internal/'` (repeatable) |
| `--auth TYPE` | force `none`, `bearer`, `token`, `basic`, `apikey` or `session` |
| `--login PATH` | the POST route whose response contains the token |
| `--env-file FILE` | environment variables your settings need (default: `.env` if present) |

## Configuration file

`routeman init` writes `routeman.toml`. You can also put the same table under `[tool.routeman]` in `pyproject.toml`:

```toml
[routeman]
name = "Shop API"
framework = "django"
app = "shop.settings"          # Flask/FastAPI: "main:app" or "factory:create_app()"
output = "postman"
exclude = ["^/internal/"]

[routeman.environments]
local = "http://localhost:8000"
production = "https://api.example.com"

[routeman.auth]
type = "auto"                  # auto | none | bearer | token | basic | apikey | session
login = "/api/token/"          # optional: detected automatically
# prefix = "JWT"               # Authorization: JWT <token>
# header = "X-API-Key"         # header for apikey auth
```

Command-line options override the file.

## Requirements

* Python 3.9 or newer, with **no dependencies** besides `tomli` on Python < 3.11.
* Run routeman from the virtualenv your project runs in, because it imports your app to read the routes. Importing is read-only: routeman never touches your database or sends requests.
* If your settings need environment variables, keep them in `.env` (loaded automatically) or pass `--env-file`.

Tested with Django 3.2 to 6, Django REST framework 3.12+, Flask 2.0 to 3.x, FastAPI 0.95+ with Pydantic v1 and v2.

## Tips

* Run **Login** first in Postman. The token is stored and sent with every other request.
* Set the `{{…_id}}` variables in the environment from list responses, or edit the request.
* Django form views (session/CSRF): send any GET first so Postman receives the `csrftoken` cookie. The collection copies it into the `X-CSRFToken` header for you.
* Regenerate whenever your API changes. Collection and request ids are stable, so importing again replaces the previous version.

## FAQ

**How do I convert a Django REST framework API to a Postman collection?**
Install routeman in the virtualenv of your project, then run `routeman generate` in the folder that contains `manage.py`. Import the generated `.postman_collection.json` and `.postman_environment.json` files in Postman with *File → Import*.

**How do I export a Flask or FastAPI API to Postman?**
Run `routeman generate --app main:app` (or `--app myapp:create_app()` for an app factory). routeman usually finds the app by itself, so a plain `routeman generate` often works.

**Do I need Swagger or an OpenAPI schema?**
No. routeman reads the routes, serializers, forms, schemas and view code directly. For Django and Flask you don't need drf-spectacular, drf-yasg, flasgger or apispec. For FastAPI, routeman uses the OpenAPI document FastAPI already builds and adds the routes FastAPI hides from it.

**Does it work with JWT or token authentication?**
Yes. routeman detects Bearer/JWT (Simple JWT, flask-jwt-extended, OAuth2), DRF `Token`, Knox, Basic, API key and session authentication. It also adds a script to the login request that saves the token, so every other request is authenticated.

**Can I run the collection in CI?**
Yes. Every request has a test that fails on a 5xx response, so `newman run postman/<name>.postman_collection.json -e postman/<name>.local.postman_environment.json` smoke-tests your whole API.

**Is it safe to run on my project?**
Yes. routeman imports your app to read its routes, the same way your server does, but it never writes to your database and never sends HTTP requests.

## License

MIT © [Shwastik Tech Solutions Pvt Ltd](https://swastik.ai). Issues and pull requests are welcome on [GitHub](https://github.com/choudhary2001/routeman).
