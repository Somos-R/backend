# Somos R — Backend

API REST del proyecto **Somos R**: plataforma de gestión de reciclaje que conecta ciudadanos, recicladores, ECAs y empresas B2B.

- **Framework:** Python 3.11+ · FastAPI
- **Base de datos:** PostgreSQL 15 + PostGIS (local vía Docker, producción en Supabase)
- **ORM / Migraciones:** SQLAlchemy 2.0 · Alembic
- **Autenticación:** JWT (python-jose) · bcrypt
- **Gestor de paquetes:** Poetry

---

## Requisitos previos

El entorno local corre **completamente en Docker**. Solo necesitas:

| Herramienta | Verificar |
|-------------|-----------|
| Docker Desktop | `docker --version` |
| Git | `git --version` |

> No necesitas instalar Python, Poetry ni PostgreSQL en tu máquina.

---

## 1. Clonar el repositorio

```bash
git clone https://github.com/Somos-R/backend.git
cd backend
```

---

## 2. Configurar variables de entorno

```bash
cp .env.example .env.local
```

El archivo `.env.local` **nunca se commitea**. Contenido mínimo para desarrollo local:

```env
DATABASE_URL=postgresql://postgres:postgres@localhost:5432/somos_r_dev
SECRET_KEY=dev-secret-key-change-in-production
ALGORITHM=HS256
ACCESS_TOKEN_EXPIRE_MINUTES=30
```

> El `DATABASE_URL` en `.env.local` usa `localhost` para herramientas externas.
> Dentro de los contenedores, docker-compose sobreescribe esta variable usando `postgres` como host (nombre del servicio).

---

## 3. Levantar el entorno completo

La primera vez construye la imagen y levanta todos los servicios:

```bash
docker compose up --build -d
```

Las siguientes veces (sin cambios en dependencias):

```bash
docker compose up -d
```

Esto levanta tres contenedores:

| Contenedor | Descripción | Puerto |
|------------|-------------|--------|
| `somos-r-db` | PostgreSQL 15 + PostGIS | `5432` |
| `somos-r-backend` | FastAPI con hot-reload | `8000` |
| `somos-r-pgadmin` | Interfaz web para la DB (opcional) | `5050` |

Para verificar que todo está corriendo:

```bash
docker compose ps
```

---

## 4. Ejecutar migraciones

Las migraciones actualizan el esquema de la base de datos según los modelos definidos en el código. **Deben ejecutarse dentro del contenedor de la app** (no en tu máquina local), porque es ahí donde están instaladas las dependencias.

Con los contenedores corriendo, ejecuta:

```bash
docker compose exec app poetry run alembic upgrade head
```

Para verificar que se aplicaron correctamente:

```bash
docker compose exec app poetry run alembic current
```

Cada vez que el equipo agregue nuevos modelos o campos a la base de datos, habrá migraciones nuevas en `migrations/versions/`. Deberás volver a correr `alembic upgrade head` para aplicarlas.

---

## 5. Verificar el servidor

El servidor arranca automáticamente con hot-reload. Accede en:

- **API:** http://localhost:8000
- **Documentación interactiva (Swagger):** http://localhost:8000/docs
- **Documentación alternativa (ReDoc):** http://localhost:8000/redoc
- **Health check:** http://localhost:8000/health

Cualquier cambio en el código se refleja automáticamente sin reiniciar el contenedor.

---

## pgAdmin (interfaz gráfica para la DB)

Accede en http://localhost:5050 con:
- **Email:** `admin@somosr.com`
- **Password:** `admin`

Para conectarte a la base de datos desde pgAdmin, crea un servidor con:
- **Host:** `postgres` (nombre del servicio Docker, no `localhost`)
- **Port:** `5432`
- **Database:** `somos_r_dev`
- **Username:** `postgres`
- **Password:** `postgres`

---

## Estructura del proyecto

```
backend/
├── app/
│   ├── main.py                      # Punto de entrada FastAPI — registra todos los routers
│   ├── core/
│   │   ├── config.py                # Variables de entorno (pydantic-settings)
│   │   ├── database.py              # Engine, SessionLocal, Base, get_db
│   │   └── security.py             # hash_password, verify_password, create_access_token, get_current_user
│   └── domains/
│       ├── auth/
│       │   ├── docs.py              # Metadata Swagger: REGISTER_DOCS, LOGIN_DOCS
│       │   ├── router.py            # POST /auth/register · POST /auth/login
│       │   └── schemas.py          # RegisterRequest (discriminated union) · LoginRequest · TokenResponse · UserResponse
│       ├── catalogs/
│       │   ├── docs.py              # Metadata Swagger: DOCUMENT_TYPES_DOCS
│       │   ├── models.py            # DocumentType · UserType · Role (tablas de lookup)
│       │   └── router.py            # GET /catalogs/document-types
│       └── users/
│           ├── docs.py              # Metadata Swagger: LIST_USERS_DOCS · GET_USER_DOCS · UPDATE_USER_DOCS
│           ├── enums.py             # VerificationStatus
│           ├── models.py            # User (tabla principal)
│           ├── router.py            # GET /users · GET /users/{id} · PATCH /users/{id}
│           └── schemas.py          # UpdateUserRequest · UserDetailResponse · UserListResponse
├── migrations/
│   ├── env.py                       # Configuración Alembic
│   ├── script.py.mako               # Template para nuevas migraciones
│   └── versions/
│       └── 0001_initial_schema.py   # Tablas + seeds: document_types, user_types, roles, users
├── scripts/
│   ├── impact_check.py              # Hook PostToolUse: detecta cambios en archivos clave
│   └── init-db.sql                  # Script de inicialización (extensiones PostGIS)
├── .claude/
│   └── settings.json                # Configuración de hooks para Claude Code
├── CLAUDE.md                        # Instrucciones de proyecto para Claude
├── Dockerfile                       # Imagen de la app (construida por docker compose)
├── docker-compose.yml               # Orquestación de todos los servicios locales
├── .dockerignore                    # Archivos excluidos del build de Docker
├── pyproject.toml
└── .env.local                       # Variables de entorno locales (NO commitear)
```

---

## Comandos frecuentes

### Contenedores

```bash
# Levantar todo (primera vez, con build)
docker compose up --build -d

# Levantar todo (sin rebuild)
docker compose up -d

# Detener todo (conserva los datos)
docker compose stop

# Detener y eliminar contenedores y volúmenes (resetea la DB)
docker compose down -v

# Ver logs de la app en tiempo real
docker compose logs -f app

# Ver logs de la DB
docker compose logs postgres

# Abrir una shell dentro del contenedor de la app
docker compose exec app bash
```

### Migraciones (dentro del contenedor)

```bash
# Aplicar migraciones pendientes
docker compose exec app poetry run alembic upgrade head

# Crear una nueva migración (autogenera desde los modelos)
docker compose exec app poetry run alembic revision --autogenerate -m "descripción del cambio"

# Ver estado actual
docker compose exec app poetry run alembic current

# Ver historial
docker compose exec app poetry run alembic history

# Revertir la última migración
docker compose exec app poetry run alembic downgrade -1

# Revertir todas las migraciones
docker compose exec app poetry run alembic downgrade base
```

> **Nota:** Al crear una migración que incluya columnas de geometría (`GeoAlchemy2`), verifica que el archivo generado tenga `import geoalchemy2` en los imports. El template ya está configurado para incluirlo automáticamente.

### Tests (dentro del contenedor)

```bash
# Ejecutar todos los tests
docker compose exec app poetry run pytest

# Con output detallado
docker compose exec app poetry run pytest -v

# Un archivo específico
docker compose exec app poetry run pytest tests/test_users.py
```

### Rebuild de la imagen

Solo es necesario cuando cambias dependencias en `pyproject.toml`:

```bash
docker compose up --build -d
```

---

## Convenciones de código

- **Variables y funciones:** `snake_case`
- **Clases:** `PascalCase`
- **Constantes:** `UPPER_SNAKE_CASE`
- **Todo el código en inglés** (variables, campos DB, enums, nombres de clases)
- **Commits:** Conventional Commits — `tipo(scope): descripción`
  - Tipos: `feat`, `fix`, `docs`, `refactor`, `chore`
  - Ejemplo: `feat(auth): add citizen registration endpoint`
- **Branches:** `main` (producción), `develop` (integración), `feature/*`, `fix/*`

---

## Endpoints disponibles

Todos los endpoints protegidos requieren header `Authorization: Bearer <token>`.

### Auth — `/auth`

| Método | Ruta | Descripción | Auth |
|--------|------|-------------|------|
| `POST` | `/auth/register` | Registrar nuevo usuario (discriminado por `user_type_code`) | No |
| `POST` | `/auth/login` | Iniciar sesión · retorna JWT Bearer token | No |

### Usuarios — `/users`

| Método | Ruta | Descripción | Auth |
|--------|------|-------------|------|
| `GET` | `/users` | Listar usuarios con paginación y filtros | Sí |
| `GET` | `/users/{user_id}` | Obtener perfil completo de un usuario | Sí |
| `PATCH` | `/users/{user_id}` | Actualizar campos del perfil (PATCH semántico) | Sí |

Filtros disponibles en `GET /users`: `user_type_code`, `role_code`, `verification_status`, `limit`, `offset`.

### Catálogos — `/catalogs`

| Método | Ruta | Descripción | Auth |
|--------|------|-------------|------|
| `GET` | `/catalogs/document-types` | Listar tipos de documento válidos para `id_type` | No |

---

## Actores del sistema

| Actor | Plataforma | `user_type_code` |
|-------|-----------|-----------------|
| Ciudadano | Mobile | `citizen` |
| Administrador de Conjunto | Mobile / Web | `building` |
| Reciclador | Mobile | `recycler` |
| Operador ECA | Web | `eca` |
| Administrador ASOBEUM | Web | `association` |
| Cliente B2B | Web | `b2b_client` |

### Roles disponibles

| `role_code` | Descripción |
|-------------|-------------|
| `eca_admin` | Administrador ECA |
| `eca_operator` | Operador ECA |
| `association_admin` | Administrador de Asociación |

---

## Stack completo

| Capa | Tecnología |
|------|-----------|
| API | FastAPI 0.135 |
| ORM | SQLAlchemy 2.0 + GeoAlchemy2 |
| Migraciones | Alembic 1.18 |
| Auth | python-jose 3.5 + bcrypt 5.0 |
| DB local | PostgreSQL 15 + PostGIS 3.4 (Docker) |
| DB producción | Supabase |
| Eventos | Upstash Redis Pub/Sub |
| Hosting | Railway o Render |
