# Despliegue: checklist de producción

Guía de lo que hay que configurar para pasar de desarrollo a un servidor real (Railway u otro). El código no cambia entre entornos: todo se controla con variables de entorno. La imagen es `Dockerfile.prod`.

## 1. Variables de entorno

**Obligatorias**

| Variable | Valor | Notas |
|---|---|---|
| `APP_ENV` | `prod` (o `staging`) | Sin esto la app arranca como `dev`: docs públicos, sin HSTS y sin validar el secreto |
| `DATABASE_URL` | `postgresql://usuario:clave@host:5432/base` | Supabase: usar la cadena del pooler |
| `SECRET_KEY` | 32+ caracteres aleatorios | `python -c "import secrets; print(secrets.token_urlsafe(48))"`. La app **se niega a arrancar** en prod con una clave corta o de ejemplo. Cambiarla cierra todas las sesiones |

**Recomendadas (la app arranca sin ellas pero escribe un aviso `Deployment check` en el log)**

| Variable | Ejemplo | Por qué |
|---|---|---|
| `CORS_ORIGINS` | `https://app.somosr.com` | Lista separada por comas de los orígenes del frontend. El valor por defecto incluye `localhost` |
| `ALLOWED_HOSTS` | `api.somosr.com` | Hosts que la API acepta (protege contra cabeceras `Host` falsas). Con Railway agregar también el dominio `*.up.railway.app` que asigne |
| `FRONTEND_URL` | `https://app.somosr.com` | Base de los enlaces de los correos (activación, verificación, reset) |
| `EMAIL_BACKEND` | `resend` | Con `console` los enlaces solo salen en el log y nadie los recibe |
| `EMAIL_API_KEY`, `EMAIL_FROM` | `re_…`, `Somos R <no-reply@somosr.com>` | El dominio del remitente debe estar verificado en Resend |
| `RATE_LIMIT_STORAGE_URI` | `redis://…` | Con varios workers/instancias los contadores en memoria son por proceso |

**Ajuste**

| Variable | Defecto | Notas |
|---|---|---|
| `WEB_CONCURRENCY` | `2` | Procesos de uvicorn. Cada uno abre su propio pool de conexiones |
| `DB_POOL_SIZE` / `DB_MAX_OVERFLOW` | `5` / `10` | Conexiones totales = workers × (size + overflow). Mantener por debajo del límite de la base/pooler |
| `ACCESS_TOKEN_EXPIRE_MINUTES` | `30` | Bajar a 15 cuando el frontend renueve con `/auth/refresh` |
| `ENABLE_DOCS` | (según entorno) | `true` para exponer Swagger fuera de `dev`. Por defecto solo en `dev` |
| `FORWARDED_ALLOW_IPS` | `*` | Proxies de confianza para `X-Forwarded-For`. `*` solo es correcto si la app **solo** es accesible a través del proxy de la plataforma; de lo contrario, listar sus IP (si no, un cliente podría falsear su IP y esquivar el rate limit) |
| `PORT` | `8000` | Las plataformas suelen inyectarlo |

## 2. Contenedor

- Corre como usuario `app` (uid 10001), no como root.
- Arranca con `uvicorn --proxy-headers`, así el rate limit ve la IP real del cliente detrás del proxy.
- `HEALTHCHECK` de la imagen usa `/health/live`.

## 3. Health checks de la plataforma

| Endpoint | Úsalo para | Toca la base |
|---|---|---|
| `/health/live` (y el alias `/health`) | Decidir si **reiniciar** el contenedor | No |
| `/health/ready` | Decidir si **enviar tráfico** (responde 503 `{"status":"unavailable"}` si la base no contesta) | Sí (`SELECT 1`) |

No usar `/health/ready` como criterio de reinicio: una caída de la base reiniciaría el servicio en bucle sin arreglar nada.

## 4. Base de datos

Aplicar las migraciones antes de arrancar la nueva versión: `alembic upgrade head` (automatizarlo en el deploy es la tarea 5.8). Las migraciones 0011 y 0012 fallan sin cambiar nada si ya hay datos que violen las restricciones nuevas.

## 5. Tareas programadas

`python scripts/purge_expired_tokens.py` una vez al día (borra tokens vencidos).

## 6. Después de desplegar, comprobar

- `curl https://<api>/health/ready` → `{"status":"ok"}`.
- `https://<api>/docs` → 404.
- Cabeceras: `curl -I https://<api>/health/live` debe incluir `strict-transport-security`, `x-content-type-options` y `content-security-policy`.
- Desde el dominio del frontend, una petición con `Origin` distinto al configurado no recibe `access-control-allow-origin`.
- Registrar un usuario y confirmar que el correo llega.
- En el log de arranque no debe aparecer ningún `Deployment check`.
