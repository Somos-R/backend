# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Comandos de desarrollo

```bash
# Iniciar todos los servicios (app, postgres, pgadmin)
docker compose up -d --build

# Ver logs del backend en tiempo real
docker compose logs -f somos-r-backend

# Aplicar migraciones pendientes
docker compose exec app poetry run alembic upgrade head

# Generar nueva migración vacía
docker compose exec app poetry run alembic revision -m "descripcion"

# Autogenerar migración desde cambios en modelos
docker compose exec app poetry run alembic revision --autogenerate -m "descripcion"

# Ejecutar todos los tests
docker compose exec app poetry run pytest

# Ejecutar un test específico
docker compose exec app poetry run pytest tests/ruta/test_archivo.py::nombre_test -v
```

URLs locales: API `http://localhost:8000` · Swagger `http://localhost:8000/docs` · ReDoc `http://localhost:8000/redoc` · pgAdmin `http://localhost:5050`

## Arquitectura

DDD ligero con tres dominios (`auth`, `users`, `catalogs`). El punto de entrada es `app/main.py`, que monta los tres routers. La infraestructura compartida vive en `app/core/` (config, database, security).

**Tabla `users` polimórfica** — un único modelo SQLAlchemy maneja 6 tipos de actor (citizen, building, recycler, eca, association, b2b_client) con columnas nullable según el tipo. El tipo se discrimina vía FK `user_type_code` a la tabla lookup `user_types`.

**Registro con unión discriminada** — `RegisterRequest` en `auth/schemas.py` usa discriminadores de Pydantic; cada variante valida sólo los campos de su tipo de actor.

**Blacklist JWT** — `POST /auth/logout` escribe el `jti` en `revoked_tokens`; `get_current_user` en `security.py` consulta esa tabla en cada request autenticado.

**PostGIS** — El campo `coverage_area` en `User` es un polígono GeoAlchemy2. La DB corre PostGIS 15-3.4 en Docker.

## Reglas de impacto al modificar archivos

Cuando modifiques cualquiera de los archivos clave listados abajo, **siempre** revisa y actualiza los archivos relacionados en la misma sesión, sin esperar a que el usuario lo pida.

### `app/domains/users/models.py`
- **`migrations/versions/`** — ¿requiere nueva migración de Alembic? Si la DB es local y está en desarrollo temprano, borra la migración anterior y regenera limpio.
- **`app/domains/auth/schemas.py`** — ¿los nombres de campo del schema coinciden con los del modelo?
- **`app/domains/auth/docs.py`** — ¿las descripciones del Swagger reflejan los campos actuales?

### `app/domains/catalogs/models.py`
- **`migrations/versions/`** — ¿el seed de datos refleja los nuevos modelos?
- **`app/domains/catalogs/docs.py`** — ¿las descripciones del catálogo están actualizadas?
- **`app/domains/catalogs/router.py`** — ¿hay endpoints para el nuevo catálogo?

### `app/domains/auth/schemas.py`
- **`app/domains/auth/docs.py`** — los nombres de campo en las descripciones deben coincidir exactamente con los del schema

### `app/domains/users/enums.py`
- **`app/domains/users/models.py`** — ¿referencias al enum actualizadas?
- **`app/domains/auth/schemas.py`** — ¿valores `Literal` actualizados?
- **`app/domains/catalogs/models.py`** — si el enum se convirtió en tabla lookup, ¿el modelo existe?

### `app/domains/auth/router.py` o cualquier `router.py`
- **`docs.py` del mismo dominio** — ¿las descripciones del endpoint coinciden con la lógica actual?

### `migrations/versions/`
Al crear o modificar una migración, recuerda al usuario aplicarla con:
```bash
docker compose exec app poetry run alembic upgrade head
```

## Convenciones del proyecto

- **FK columns:** `{tabla_singular}_code` (ej: `role_code`, `user_type_code`, `id_type`)
- **Tablas de lookup (catálogos):** siempre tienen `code` (PK), `label`, `is_active`
- **Estructura de dominio:** `models.py`, `schemas.py`, `router.py`, `docs.py`, `__init__.py`
- **Seeds de datos:** dentro de la migración con `op.bulk_insert`, nunca como script manual
- **Swagger metadata:** en `docs.py`, nunca inline en `router.py`
- **Idioma del código:** inglés (variables, campos DB, enums, clases) · **Idioma descripciones Swagger:** español
