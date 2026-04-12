# Somos R — Backend

API REST del proyecto **Somos R**: plataforma de gestión de reciclaje que conecta ciudadanos, recicladores, ECAs y empresas B2B.

- **Framework:** Python 3.11+ · FastAPI
- **Base de datos:** PostgreSQL 15 + PostGIS (local vía Docker, producción en Supabase)
- **ORM / Migraciones:** SQLAlchemy 2.0 · Alembic
- **Autenticación:** JWT (python-jose) · bcrypt
- **Gestor de paquetes:** Poetry

---

## Requisitos previos

Asegúrate de tener instalado:

| Herramienta | Versión mínima | Verificar |
|-------------|---------------|-----------|
| Python | 3.11 | `python --version` |
| Poetry | 1.8+ | `poetry --version` |
| Docker Desktop | cualquiera | `docker --version` |
| Git | cualquiera | `git --version` |

> **Poetry no instalado?**
> ```bash
> pip install pipx && pipx install poetry && pipx ensurepath
> ```
> Luego abre una nueva terminal.

---

## 1. Clonar el repositorio

```bash
git clone https://github.com/Somos-R/backend.git
cd backend
```

---

## 2. Instalar dependencias

```bash
poetry install
```

Esto crea un virtualenv aislado e instala todas las dependencias (producción + dev) definidas en `pyproject.toml`.

---

## 3. Configurar variables de entorno

Crea el archivo `.env.local` en la raíz del proyecto. **Este archivo nunca se commitea.**

```bash
cp .env.example .env.local   # si existe el ejemplo, o créalo manualmente
```

Contenido mínimo para desarrollo local:

```env
DATABASE_URL=postgresql://postgres:postgres@localhost:5432/somos_r_dev
SECRET_KEY=dev-secret-key-change-in-production
ALGORITHM=HS256
ACCESS_TOKEN_EXPIRE_MINUTES=30
```

---

## 4. Levantar la base de datos

La base de datos corre en Docker. Usa el siguiente comando desde la raíz del proyecto:

```bash
docker compose up -d
```

Esto levanta el container `somos-r-db` con:
- **Imagen:** `postgis/postgis:15-3.4`
- **Base de datos:** `somos_r_dev`
- **Usuario:** `postgres` / **Contraseña:** `postgres`
- **Puerto:** `5432`
- **Extensiones habilitadas automáticamente:** `postgis`, `uuid-ossp`

Para verificar que está corriendo:

```bash
docker compose exec postgres pg_isready -U postgres -d somos_r_dev
# Resultado esperado: /var/run/postgresql:5432 - accepting connections
```

Para verificar PostGIS:

```bash
docker compose exec postgres psql -U postgres -d somos_r_dev -c "SELECT PostGIS_Version();"
```

---

## 5. Ejecutar migraciones

Con la DB corriendo, aplica las migraciones con Alembic:

```bash
poetry run alembic upgrade head
```

Para ver el estado actual de las migraciones:

```bash
poetry run alembic current
```

Para ver el historial:

```bash
poetry run alembic history
```

---

## 6. Levantar el servidor de desarrollo

```bash
poetry run uvicorn app.main:app --reload
```

El servidor queda disponible en:

- **API:** http://localhost:8000
- **Documentación interactiva (Swagger):** http://localhost:8000/docs
- **Documentación alternativa (ReDoc):** http://localhost:8000/redoc
- **Health check:** http://localhost:8000/health

El flag `--reload` reinicia el servidor automáticamente al detectar cambios en el código.

---

## Estructura del proyecto

```
backend/
├── app/
│   ├── main.py                  # Punto de entrada FastAPI
│   ├── core/
│   │   ├── config.py            # Variables de entorno (pydantic-settings)
│   │   └── database.py          # Engine, SessionLocal, Base, get_db
│   └── domains/
│       └── users/
│           ├── enums.py         # UserType, VerificationStatus
│           ├── models.py        # Modelo SQLAlchemy: tabla users
│           └── schemas.py       # Schemas Pydantic por actor
├── migrations/
│   ├── env.py                   # Configuración Alembic
│   ├── script.py.mako           # Template para nuevas migraciones
│   └── versions/                # Archivos de migración generados
├── scripts/
│   └── init-db.sql              # Script de inicialización (extensiones PostGIS)
├── docker-compose.yml
├── pyproject.toml
└── .env.local                   # Variables de entorno locales (NO commitear)
```

---

## Comandos frecuentes

### Base de datos

```bash
# Levantar DB
docker compose up -d

# Detener DB (conserva los datos)
docker compose stop

# Detener DB y eliminar volúmenes (resetea todo)
docker compose down -v

# Ver logs del container
docker compose logs postgres
```

### Migraciones

```bash
# Crear una nueva migración (autogenera desde los modelos)
poetry run alembic revision --autogenerate -m "descripción del cambio"

# Aplicar migraciones pendientes
poetry run alembic upgrade head

# Revertir la última migración
poetry run alembic downgrade -1

# Revertir todas las migraciones
poetry run alembic downgrade base
```

> **Nota:** Al crear una migración que incluya columnas de geometría (`GeoAlchemy2`), asegúrate de que el archivo generado tenga `import geoalchemy2` en los imports. El template ya está configurado para incluirlo automáticamente.

### Tests

```bash
# Ejecutar todos los tests
poetry run pytest

# Con output detallado
poetry run pytest -v

# Un archivo específico
poetry run pytest tests/test_users.py
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

## Actores del sistema

| Actor | Plataforma | `user_type` |
|-------|-----------|------------|
| Ciudadano | Mobile | `citizen` |
| Admin Conjunto | Mobile / Web | `building_admin` |
| Reciclador | Mobile | `recycler` |
| Operador ECA | Web | `eca_operator` |
| Admin ASOBEUM | Web | `asobeum_admin` |
| Cliente B2B | Web | `b2b_client` |

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
