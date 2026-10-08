# Changelog

## 0.1.0 - 2026-10-08

First release.

* `routeman generate`, `routeman routes`, `routeman init`.
* Django + Django REST framework, Flask and FastAPI support without any schema/documentation library.
* Postman collection v2.1 with folders, path variables, query parameters, example bodies (JSON, urlencoded,
  multipart with files), detected authentication, automatic token capture and a no-5xx test per request.
* One Postman environment per configured server.
* `routeman.toml` / `[tool.routeman]` configuration, `.env` loading.
