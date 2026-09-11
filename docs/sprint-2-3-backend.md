# Documentación Sprint 2 & 3 — Backend

> **Proyecto:** Somos R  
> **Responsable backend:** Oscar Mauricio Garcia Mesa  
> **Estado:** Completado

---

## [Sprint 2] 1.3 — Levantar entorno Docker + FastAPI + PostgreSQL

### Descripción

Se configuró el entorno local de desarrollo completamente dockerizado. Cualquier developer puede levantar el proyecto completo con un solo comando, sin necesidad de instalar Python, Poetry ni PostgreSQL en su máquina.

### Servicios levantados

| Servicio | Imagen | Puerto |
|----------|--------|--------|
| Base de datos | PostgreSQL 15 + PostGIS 3.4 | `5432` |
| API | FastAPI + Uvicorn (hot-reload) | `8000` |
| Administrador DB | pgAdmin 4 | `5050` |

### Archivos creados

- `Dockerfile` — imagen de la aplicación
- `docker-compose.yml` — orquestación de los tres servicios
- `.dockerignore` — excluye secretos y archivos innecesarios del build
- `.env.example` — plantilla de variables de entorno para nuevos developers

### Comando para levantar el entorno

```bash
docker compose up --build -d
docker compose exec app poetry run alembic upgrade head
```

### Evidencias

> **Qué agregar aquí:**
> - Captura de `docker compose ps` mostrando los 3 contenedores en estado `running`
> - Captura del navegador en `http://localhost:8000/health` con respuesta `{"status": "ok"}`
> - Captura del navegador en `http://localhost:8000/docs` con el Swagger de la API

<!-- EVIDENCIA 1: docker compose ps -->

<!-- EVIDENCIA 2: health check en el navegador -->

<!-- EVIDENCIA 3: Swagger UI -->

---

## [Sprint 2] 2.1 — Definir modelos ER de tablas

### Descripción

Se definió el modelo de datos principal del sistema. Se optó por una **tabla única `users`** que soporta los 6 actores mediante campos opcionales por tipo de usuario, más una **tabla de catálogo `document_types`** para los tipos de documento de identidad.

### Decisiones de diseño

| Decisión | Razón |
|----------|-------|
| Tabla única para todos los actores | Simplifica queries de autenticación y evita JOINs innecesarios |
| `user_type` y `verification_status` como enum nativo de PostgreSQL | Son valores de dominio fijos que no cambian sin un cambio de producto |
| `document_types` como tabla de lookup | Es un catálogo que puede crecer (nuevos tipos de documento) sin requerir migraciones de código |
| `id_type` + `id_number` como campos comunes obligatorios | Todo usuario en Colombia tiene un documento de identidad, independiente de su rol |

### Actores soportados

| Actor | `user_type` | Campos específicos |
|-------|------------|-------------------|
| Ciudadano | `citizen` | `address`, `latitude`, `longitude` |
| Admin Conjunto | `building_admin` | `building_name`, `num_units`, `representation_document` |
| Reciclador | `recycler` | `profile_picture`, `id_picture`, `verification_status` |
| Operador ECA | `eca_operator` | `employee_code`, `permissions` |
| Admin ASOBEUM | `asobeum_admin` | `association_nit`, `legal_representative`, `coverage_area` |
| Cliente B2B | `b2b_client` | `company_name`, `tax_id`, `commercial_contact` |

### Tipos de documento disponibles

| Código | Descripción |
|--------|-------------|
| `CC` | Cédula de Ciudadanía |
| `CE` | Cédula de Extranjería |
| `NIT` | Número de Identificación Tributaria |
| `PA` | Pasaporte |
| `PPT` | Permiso de Protección Temporal |

### Archivos creados

- `app/domains/users/models.py` — modelo SQLAlchemy de la tabla `users`
- `app/domains/users/enums.py` — enums `UserType` y `VerificationStatus`
- `app/domains/catalogs/models.py` — modelo SQLAlchemy de la tabla `document_types`
- `migrations/versions/ee29ea6fdc06_*.py` — migración con creación de tablas y seed de `document_types`

### Evidencias

> **Qué agregar aquí:**
> - Captura de pgAdmin (`http://localhost:5050`) mostrando las tablas `users` y `document_types` creadas
> - Captura del contenido de la tabla `document_types` con los 5 registros del seed
> - Captura de la estructura de columnas de la tabla `users`

<!-- EVIDENCIA 1: tablas en pgAdmin -->

<!-- EVIDENCIA 2: registros en document_types -->

<!-- EVIDENCIA 3: columnas de la tabla users -->

---

## [Sprint 2] 2.2 — Endpoint para registrar un usuario

### Descripción

Se implementó el endpoint `POST /auth/register` que permite registrar cualquier tipo de actor del sistema. Usa **discriminated unions de Pydantic** para validar automáticamente los campos requeridos según el `user_type` recibido.

### Endpoint

```
POST /auth/register
```

### Comportamiento

1. Recibe el body con `user_type` y los campos correspondientes al actor
2. Pydantic selecciona el schema correcto según `user_type` y valida los campos
3. La contraseña se hashea con bcrypt antes de guardarla (nunca se almacena en texto plano)
4. Se guarda el usuario en la base de datos
5. Retorna el usuario creado sin el campo `password_hash`

### Campos comunes (todos los actores)

| Campo | Tipo | Requerido |
|-------|------|-----------|
| `email` | string | ✅ |
| `password` | string | ✅ |
| `full_name` | string | ✅ |
| `id_type` | string (código) | ✅ |
| `id_number` | string | ✅ |
| `user_type` | string (enum) | ✅ |
| `phone` | string | ❌ |

### Campos requeridos por actor

| Actor | Campos requeridos |
|-------|------------------|
| `citizen` | — (solo campos comunes) |
| `building_admin` | `building_name`, `num_units` |
| `recycler` | — (solo campos comunes) |
| `eca_operator` | — (solo campos comunes) |
| `asobeum_admin` | `association_nit`, `legal_representative` |
| `b2b_client` | `company_name`, `tax_id` |

### Códigos de respuesta

| Código | Situación |
|--------|-----------|
| `201 Created` | Usuario registrado exitosamente |
| `409 Conflict` | Email o número de documento ya registrado |
| `422 Unprocessable Entity` | Campos requeridos faltantes o `user_type` inválido |

### Archivos creados

- `app/domains/auth/schemas.py` — schemas de registro por actor y schema de respuesta
- `app/domains/auth/router.py` — endpoint de registro
- `app/core/security.py` — función `hash_password` con bcrypt

### Evidencias

> **Qué agregar aquí:**
> - Captura del Swagger (`http://localhost:8000/docs`) mostrando el endpoint `POST /auth/register`
> - Captura de una petición exitosa (201) registrando un ciudadano desde Swagger o Postman
> - Captura de una petición exitosa (201) registrando un `building_admin` con sus campos específicos
> - Captura del error 409 al intentar registrar el mismo email dos veces
> - Captura del error 422 al registrar un `building_admin` sin `building_name`

<!-- EVIDENCIA 1: endpoint en Swagger -->

<!-- EVIDENCIA 2: registro exitoso de citizen -->

<!-- EVIDENCIA 3: registro exitoso de building_admin -->

<!-- EVIDENCIA 4: error 409 email duplicado -->

<!-- EVIDENCIA 5: error 422 campos faltantes -->

---

## [Sprint 3] 3.1 — Login Backend (API + JWT)

### Descripción

Se implementó el endpoint `POST /auth/login` que valida las credenciales del usuario y retorna un **JSON Web Token (JWT)** para autenticar las siguientes peticiones.

### Endpoint

```
POST /auth/login
```

### Comportamiento

1. Recibe `email` y `password`
2. Busca el usuario en la base de datos por email
3. Verifica la contraseña contra el hash almacenado con bcrypt
4. Si las credenciales son válidas, genera un JWT firmado con `SECRET_KEY`
5. Retorna el token con tipo `bearer`

### Contenido del JWT

| Campo | Valor |
|-------|-------|
| `sub` | UUID del usuario |
| `user_type` | Tipo de actor (`citizen`, `recycler`, etc.) |
| `exp` | Timestamp de expiración (configurable, default 30 min) |

### Códigos de respuesta

| Código | Situación |
|--------|-----------|
| `200 OK` | Credenciales válidas — retorna `access_token` |
| `401 Unauthorized` | Email no existe o contraseña incorrecta |

### Uso del token en el frontend

El token debe enviarse en el header de cada petición autenticada:

```
Authorization: Bearer <access_token>
```

### Endpoint adicional — Catálogo de tipos de documento

```
GET /catalogs/document-types
```

Retorna la lista de tipos de documento activos para poblar dropdowns en el frontend de registro. No requiere autenticación.

### Archivos creados / modificados

- `app/domains/auth/router.py` — endpoint de login
- `app/core/security.py` — funciones `verify_password` y `create_access_token`
- `app/domains/catalogs/router.py` — endpoint `GET /catalogs/document-types`
- `app/main.py` — registro de los routers `auth` y `catalogs`

### Evidencias

> **Qué agregar aquí:**
> - Captura del Swagger mostrando el endpoint `POST /auth/login`
> - Captura de un login exitoso (200) con el `access_token` en la respuesta
> - Captura del error 401 con credenciales incorrectas
> - Captura del endpoint `GET /catalogs/document-types` con los 5 tipos retornados
> - Captura del JWT decodificado en `https://jwt.io` mostrando el payload con `sub` y `user_type`

<!-- EVIDENCIA 1: endpoint en Swagger -->

<!-- EVIDENCIA 2: login exitoso con token -->

<!-- EVIDENCIA 3: error 401 credenciales inválidas -->

<!-- EVIDENCIA 4: GET /catalogs/document-types -->

<!-- EVIDENCIA 5: JWT decodificado en jwt.io -->
