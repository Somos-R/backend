# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Flujo de Git — leer primero

`main` está protegida: solo avanza mediante un Pull Request mergeado, nunca con push directo.

- **Nunca commitear directo en `main`.** Antes de empezar cualquier cambio — incluso uno pequeño — crear o cambiar a una rama (`feature/<slug>`, `fix/<slug>`, `chore/<slug>`).
- **Commitear a medida que se avanza.** No dejar una sesión con cambios sin stagear o sin commitear "para después" — un working tree sin commitear no es un punto de guardado. Si el trabajo quedó a medias, commitearlo igual como WIP en la rama.
- **Pushear y abrir un PR** en cuanto haya algo revisable, en vez de dejar trabajo terminado solo en local. Un PR en draft está bien si sigue en progreso.
- Esto aplica igual si la sesión es de una persona o de Claude Code/un agente — sin excepciones por "es un cambio chico".

Esto no es teórico: este repo tuvo ~4 meses de trabajo real (dominios de inventory/weighings/transactions) sin commitear en el working tree hasta que se consolidó en un PR. No repetirlo.

## Comandos de desarrollo

```bash
# Iniciar los servicios (app, postgres). pgAdmin es opcional: agregar `--profile tools`
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

# Lo mismo que corre el CI (lint, tipos, tests con cobertura mínima de 90%)
docker compose exec app poetry run ruff check app tests
docker compose exec app poetry run mypy app
docker compose exec app poetry run pytest --cov
```

**Tests:** `tests/conftest.py` recrea una base `<nombre>_test` (nunca toca la de desarrollo) y la migra con Alembic en cada sesión; cada test corre en una transacción con rollback. Necesita Postgres/PostGIS arriba (`docker compose up -d postgres`). `tests/test_known_issues.py` contiene brechas de seguridad conocidas como `xfail(strict=True)`: al corregir una, su test pasa, falla por ser estricto, y hay que quitar la marca. `tests/test_concurrency.py` es la excepción al aislamiento por rollback: confirma datos reales para que varios hilos compitan, y limpia las tablas al terminar.

**Seguridad en el CI** (`.github/workflows/security.yml`, en cada PR, en `main` y cada lunes): `pip-audit` (vulnerabilidades en dependencias), `gitleaks` (secretos en todo el historial) y Trivy (imagen de producción, HIGH/CRITICAL con arreglo disponible). Para correrlos en local:

```bash
docker compose exec app poetry run pip-audit
docker run --rm -v "$PWD:/repo" zricethezav/gitleaks:v8.21.2 detect --source /repo --redact --no-banner
```

Si `pip-audit` falla, actualizar la dependencia (`poetry update <paquete>`); si Trivy falla por el sistema base, subir la versión de la imagen en `Dockerfile.prod`. Un falso positivo de `gitleaks` se acepta por huella exacta en `.gitleaksignore` (o con `# gitleaks:allow` en la línea), nunca desactivando la regla. Dependabot abre los PR de actualización cada lunes.

URLs locales: API `http://localhost:8000` · Swagger `http://localhost:8000/docs` · ReDoc `http://localhost:8000/redoc` · pgAdmin `http://localhost:5050` (solo con `--profile tools`). Los puertos quedan publicados únicamente en `127.0.0.1`; las credenciales de desarrollo se pueden cambiar en un `.env` (ver `.env.example`). Producción: `docs/despliegue.md`.

## Arquitectura

DDD ligero con tres dominios (`auth`, `users`, `catalogs`). El punto de entrada es `app/main.py`, que monta los tres routers. La infraestructura compartida vive en `app/core/` (config, database, security).

**Tabla `users` polimórfica** — un único modelo SQLAlchemy maneja 6 tipos de actor (citizen, building, recycler, eca, association, b2b_client) con columnas nullable según el tipo. El tipo se discrimina vía FK `user_type_code` a la tabla lookup `user_types`.

**Registro con unión discriminada** — `RegisterRequest` en `auth/schemas.py` usa discriminadores de Pydantic; cada variante valida sólo los campos de su tipo de actor.

**Observabilidad** — `app/core/request_context.py` es el middleware más externo: asigna un `X-Request-ID`, escribe una línea de log (`app.access`) por petición y cuenta las métricas. El identificador y la IP del cliente viven en `app/core/context.py` y se leen desde cualquier parte sin pasar `request`. Nunca registrar cuerpos, query strings, cabeceras ni contraseñas en logs ni en Sentry. Detalle en `docs/observabilidad.md`.

**Auditoría** — `app/domains/audit/`: tabla `audit_log` de solo anexar (un trigger de PostgreSQL rechaza `UPDATE` y `DELETE`). Toda operación sensible (sesión, contraseñas, verificación de recicladores, roles, pesajes, transacciones, precios de inventario) llama a `audit.record(db, Action.X, actor=..., target_type=..., target_id=..., details=...)` **antes del `db.commit()`**, para que el evento y la acción se confirmen o deshagan juntos. Los eventos que deben sobrevivir a un error (un login fallido) se confirman explícitamente antes de lanzar la excepción. Nunca poner contraseñas, tokens ni valores personales en `details` (se enmascaran las claves sensibles, pero no confiar solo en eso); guardar nombres de campo, no valores. Al agregar un endpoint que cambie datos sensibles, agregar su acción en `audit/actions.py`, registrarla y cubrirla en `tests/test_audit.py`. Detalle en `docs/auditoria.md`.

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

### `pyproject.toml` / `poetry.lock` (dependencias) — también al usar `poetry add`, `remove`, `update` o `lock`
Toda dependencia nueva o actualizada deja **desactualizada la imagen de desarrollo**: el contenedor sigue "Up" pero la API deja de responder con `ModuleNotFoundError`. Ya pasó dos veces. Al tocar dependencias, en la misma sesión:
- **Reconstruye la imagen:** `docker compose up -d --build --no-deps app` (o, más rápido, `docker compose restart app`: el contenedor instala lo que falte antes de arrancar).
- **Comprueba que responde:** `curl localhost:8000/health/ready` debe dar 200 (si no, `docker compose logs --tail 30 app`).
- Corre `poetry run pip-audit` y la verificación completa (ruff, mypy, pytest).
- Una dependencia que la app usa en producción va en el grupo principal, no en `dev`; `Dockerfile.prod` instala desde `poetry.lock` y no necesita cambios.
- **Avisa al usuario** (y en la descripción del PR) que se agregaron dependencias: cualquiera que haga `git pull` debe reiniciar o reconstruir. El hook `scripts/impact_check.py` lo recuerda al editar estos archivos o ejecutar `poetry add/remove/update/lock`.

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
- **Listados y estadísticas:** los agregados (sumas, conteos, filtros por estado) se calculan en SQL, nunca trayendo las filas a Python; los listados con relaciones en la respuesta usan `selectinload` para no hacer una consulta por fila (N+1); todo `ORDER BY` paginado lleva `id` como desempate. `tests/test_performance.py` falla si se rompe alguna de estas reglas.
- **Idioma del código:** inglés (variables, campos DB, enums, clases) · **Idioma descripciones Swagger:** español
