# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Flujo de Git — leer primero

`main` está protegida: solo avanza mediante un Pull Request mergeado, nunca con push directo.

- **Nunca commitear directo en `main`.** Antes de empezar cualquier cambio — incluso uno pequeño — crear o cambiar a una rama (`feature/<slug>`, `fix/<slug>`, `chore/<slug>`).
- **Commitear a medida que se avanza.** No dejar una sesión con cambios sin stagear o sin commitear "para después" — un working tree sin commitear no es un punto de guardado. Si el trabajo quedó a medias, commitearlo igual como WIP en la rama.
- **Pushear y abrir un PR** en cuanto haya algo revisable, en vez de dejar trabajo terminado solo en local. Un PR en draft está bien si sigue en progreso.
- **Los pull requests se documentan SIEMPRE en español**: el título y toda la descripción (qué hace, qué revisar, cómo se verificó), en cada PR que abras o edites, seas persona o agente. Si encuentras un PR en inglés, reescríbelo.
- **La documentación no se sube al repo.** Vive en `Somos-R/docs-archivo/back/` (permisos, despliegue, modelo de datos); en el repo solo va código y lo que el código necesita para construirse y probarse.
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

URLs locales: API `http://localhost:8000` · Swagger `http://localhost:8000/docs` · ReDoc `http://localhost:8000/redoc` · pgAdmin `http://localhost:5050` (solo con `--profile tools`). Los puertos quedan publicados únicamente en `127.0.0.1`; las credenciales de desarrollo se pueden cambiar en un `.env` (ver `.env.example`). Producción: `docs-archivo/back/despliegue.md` (fuera del repo).

## Arquitectura

DDD ligero con tres dominios (`auth`, `users`, `catalogs`). El punto de entrada es `app/main.py`, que monta los tres routers. La infraestructura compartida vive en `app/core/` (config, database, security).

**Tabla `users` polimórfica** — un único modelo SQLAlchemy maneja 7 tipos de actor (citizen, building, recycler, eca, association, b2b_client y `platform`, que es Somos R: sin autoregistro y sin acceso a la API de los clientes; ver `docs-archivo/back/matriz-permisos.md` (fuera del repo)) con columnas nullable según el tipo. El tipo se discrimina vía FK `user_type_code` a la tabla lookup `user_types`.

**Organizaciones** — `app/domains/organizations/`: la entidad `Organization` (tipo `association` o `eca`, con su `status` de incorporación: `draft` → `submitted` → `in_review` → `changes_requested` / `approved` / `rejected`, `suspended` reservado). Todo el personal de una ECA o Asociación pertenece a una (`users.organization_id`); el cliente nunca la elige: el personal se da de alta por **invitación** (`POST /users/invitations`: la persona elige su contraseña por un enlace de un solo uso) y hereda la organización de quien invita. Es la base del aislamiento entre organizaciones: el personal de una organización solo se alcanza desde ella (`in_scope` en `app/core/permissions.py`; falla cerrado si la cuenta no tiene organización, y lo ajeno responde 404 como si no existiera). Aplica a usuarios, asignación de roles, auditoría y **datos operativos**: bodegas, inventario, pesajes y transacciones pertenecen a la ECA dueña de la bodega (`warehouses.organization_id`), y las reglas de quién alcanza qué viven en `app/domains/organizations/scope.py` (la ECA recibe el material de quien lo traiga y guarda su `affiliation_status`; a la asociación solo llegan los pesajes `linked` de sus recicladores y lee el inventario de sus ECA vinculadas). Toda consulta o cambio operativo nuevo debe pasar por `scope`. **Todo endpoint nuevo que devuelva o modifique datos de una organización debe respetar este alcance y tener una prueba cruzada entre dos organizaciones** (`tests/test_organization_isolation.py`). El diseño completo está fuera del repositorio.

**Recicladores** — cada reciclador pertenece a una asociación (`organization_id` de tipo `association`), que es la única que lo ve y lo verifica (`in_scope`); la elige al registrarse. El personal de ECA los alcanza en el directorio solo si su asociación está vinculada a su ECA; para pesar a cualquiera usa `GET /recyclers/lookup`.

**Solicitudes de incorporación** — `app/domains/applications/`: el flujo público por el que una Asociación o ECA pide unirse sin cuenta. La organización nace en `draft` (`organizations`) y `organization_applications` guarda a quien aplica, el hash del enlace mágico (`X-Application-Token`) y el consentimiento; nunca se devuelve el token en una respuesta ni se confirma si un correo ya aplicó. Los endpoints públicos llevan `applications_limit`. La unicidad de (tipo, NIT) solo cuenta entre organizaciones que operan. Detalle en `docs-archivo/back/matriz-permisos.md` (fuera del repo).

**Documentos de las solicitudes** — la lista de documentos que se piden es un catálogo editable (`organization_document_types`, `/admin/catalogs/organization-documents`); los archivos van a almacenamiento privado (`app/core/storage.py`, hoy solo `local`: producción necesita uno que sobreviva) con clave aleatoria y se validan por su contenido (`app/core/uploads.py`: PDF, PNG o JPG, 5 MB). Nunca se sirven directamente ni se devuelve su ubicación: el revisor los abre con un enlace firmado de pocos minutos (`app/core/signed_links.py`, atado al archivo exacto) cuyo pedido y descarga se auditan; aprobar exige los obligatorios en `ok`. En los tests se escriben en un directorio temporal (`conftest.py`).

**Vínculos ECA ↔ Asociación** — `EcaAssociationLink` (mismo dominio): la ECA solicita, el administrador de la asociación decide y cualquiera de las dos retira; solo concierne a sus dos organizaciones (404 para el resto). El vínculo activo condiciona el acceso a datos operativos (`scope.py`).

**Registro con unión discriminada** — `RegisterRequest` en `auth/schemas.py` usa discriminadores de Pydantic; cada variante valida sólo los campos de su tipo de actor. **`eca` y `association` no se autoregistran** (`403 registration_closed`, `ensure_registration_is_open` en `auth/service.py`): entran por solicitud aprobada o por invitación; solo el administrador de ese mismo tipo puede añadir personal por `POST /auth/register` con contraseña (vía de contingencia que se mantiene a propósito; la normal es `POST /users/invitations`).

**Observabilidad** — `app/core/request_context.py` es el middleware más externo: asigna un `X-Request-ID`, escribe una línea de log (`app.access`) por petición y cuenta las métricas. El identificador y la IP del cliente viven en `app/core/context.py` y se leen desde cualquier parte sin pasar `request`. Nunca registrar cuerpos, query strings, cabeceras ni contraseñas en logs ni en Sentry.

**Auditoría** — `app/domains/audit/`: tabla `audit_log` de solo anexar (un trigger de PostgreSQL rechaza `UPDATE` y `DELETE`). Toda operación sensible (sesión, contraseñas, verificación de recicladores, roles, pesajes, transacciones, precios de inventario) llama a `audit.record(db, Action.X, actor=..., target_type=..., target_id=..., details=...)` **antes del `db.commit()`**, para que el evento y la acción se confirmen o deshagan juntos. Los eventos que deben sobrevivir a un error (un login fallido) se confirman explícitamente antes de lanzar la excepción. Nunca poner contraseñas, tokens ni valores personales en `details` (se enmascaran las claves sensibles, pero no confiar solo en eso); guardar nombres de campo, no valores. Al agregar un endpoint que cambie datos sensibles, agregar su acción en `audit/actions.py`, registrarla y cubrirla en `tests/test_audit.py`.

**Backoffice (`/admin`)** — `app/domains/admin/`. Las cuentas de Somos R (`platform`) entran en dos pasos (contraseña y luego TOTP o código de recuperación; `app/core/mfa.py`) y reciben tokens con `aud="backoffice"`; los de los clientes llevan `aud="portal"`. Cada API acepta solo su audiencia (`get_current_user` vs `get_platform_user`), también los tokens de refresco. Los endpoints del backoffice piden una capacidad (`require_capability`), nunca un rol, y deben auditar lo que hacen. Todo `/admin` pasa por `enforce_admin_network` y por límites más estrictos. La consulta de auditoría (`app/domains/audit/queries.py`) es común: el visor de clientes la restringe a su organización y el de Somos R (`GET /admin/audit-log`) ve todo y deja constancia de cada lectura. Detalle en `docs-archivo/back/matriz-permisos.md` (fuera del repo).

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
- **Avisa al usuario** (y en la descripción del PR) que se agregaron dependencias: cualquiera que haga `git pull` debe reiniciar o reconstruir.

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
- **Routers delgados:** el router solo traduce HTTP (parámetros, dependencias, `BackgroundTasks`, códigos de estado); la lógica de negocio, las consultas y el `commit` viven en `service.py` del dominio (`auth` y `users` ya lo cumplen; los demás dominios se irán moviendo). Un servicio que envía correos devuelve el token y el router lo encola.
- **Dominios que reaccionan a otros:** un dominio no importa el servicio de otro para reaccionar a lo que pasó; publica un evento (`app/core/events.py`, el evento vive en `<dominio>/events.py` con valores simples, no filas ORM) y quien reacciona lo escucha. Los suscriptores se conectan en `app/domains/handlers.py`, corren de forma síncrona en la misma transacción (un error deshace todo; nunca hacen `commit`) y un evento sin suscriptores falla en voz alta. Hoy: `WeighingValidated` → inventario suma el stock y transacciones crea la compra.
- **Errores:** todo error de la API se lanza con `ApiError("codigo_en_ingles", status_code=..., detail="mensaje en español")` (`app/core/errors.py`), nunca con `HTTPException` a secas: la respuesta lleva `code` (estable, lo usa el cliente para traducir) y `detail` (texto de respaldo). Los códigos son parte del contrato: renombrar uno exige avisar al web. `tests/test_error_codes.py` falla si se usa `HTTPException` con `detail`.
- **Idioma del código:** inglés, **sin excepciones**: variables, funciones, clases, columnas, valores de enums, **códigos de catálogo** (`materials.code`, roles, tipos), parámetros y campos de la API. El español es solo para texto que ve una persona: descripciones de Swagger (`docs.py`), etiquetas (`label`) de los catálogos y mensajes de error (`detail`, que pasarán a códigos estables en inglés). Un identificador nuevo en español es un defecto. Ejemplos: `status` (no `estado`), `price_per_kg` (no `precio_kg`), `occurred_at` (no `fecha`), `sale`/`purchase` (no `venta`/`compra`), `paper`/`plastic` (no `papel`/`plastico`).
