# Observabilidad: logs, errores y métricas

Tres herramientas que responden tres preguntas distintas: los **logs** dicen *qué pasó* en una petición concreta, **Sentry** avisa *cuándo algo se rompió*, y las **métricas** muestran *cómo se comporta el servicio* a lo largo del tiempo.

## 1. Logs estructurados

En `staging` y `prod` cada línea es un objeto JSON (`LOG_FORMAT=auto`); en `dev` es texto legible. Se fuerza con `LOG_FORMAT=json|text`, y el nivel con `LOG_LEVEL` (`INFO` por defecto).

Cada petición produce **una** línea con el logger `app.access`:

```json
{"timestamp": "2026-09-29T06:28:15.225+00:00", "level": "INFO", "logger": "app.access",
 "message": "GET /catalogs/roles -> 200", "request_id": "41cf96c7bbc7…", "method": "GET",
 "path": "/catalogs/roles", "route": "/catalogs/roles", "status": 200, "duration_ms": 3.1,
 "client_ip": "203.0.113.7", "user_id": null}
```

- **Severidad:** `INFO` para respuestas normales, `WARNING` para 401/403/429 (intentos rechazados) y `ERROR` para 5xx.
- **`user_id`** aparece cuando la petición iba autenticada. Es solo el id, nunca el correo ni el nombre.
- **No se registran** el query string, las cabeceras ni el cuerpo de la petición: contienen contraseñas, tokens y datos personales.
- `/health/*` y `/metrics` no se registran (se consultan cada pocos segundos y taparían el tráfico real).

### El identificador de petición (`X-Request-ID`)

Toda respuesta lleva la cabecera `X-Request-ID`, y todas las líneas de log de esa petición llevan el mismo `request_id`. Es la forma de seguir un problema de punta a punta:

1. Un usuario reporta un error → pídele el `request_id` (los errores 500 lo incluyen en el cuerpo: `{"detail": "Error interno del servidor", "request_id": "…"}`).
2. Búscalo en los logs: verás la línea de acceso y el *traceback* completo.
3. Si el frontend envía su propio `X-Request-ID` (hasta 64 caracteres `A-Z a-z 0-9 . _ -`), se conserva, así se une el rastro del navegador con el del servidor. Un valor sospechoso se reemplaza por uno generado.

### Búsquedas útiles (Railway, Loki, CloudWatch…)

| Quiero ver | Filtro |
|---|---|
| Todo lo de una petición | `request_id = "…"` |
| Errores del servidor | `logger = "app.access" AND status >= 500` |
| Intentos rechazados | `logger = "app.access" AND status IN (401, 403, 429)` |
| Lo que hizo un usuario | `user_id = "…"` |
| Peticiones lentas | `duration_ms > 1000` |

## 2. Sentry (errores)

Se activa solo si `SENTRY_DSN` tiene valor; sin él no hace nada. Captura las excepciones no controladas con su contexto (ruta, versión, entorno).

| Variable | Notas |
|---|---|
| `SENTRY_DSN` | La URL que da Sentry al crear el proyecto (proyecto de tipo Python/FastAPI) |
| `SENTRY_RELEASE` | Identifica la versión, p. ej. el SHA del commit. Permite saber qué despliegue introdujo un error |
| `SENTRY_TRACES_SAMPLE_RATE` | `0` por defecto = solo errores. Subirlo (`0.05`) activa trazas de rendimiento y consume cuota |

**Privacidad, por diseño:** no se envían cuerpos de petición (llevan contraseñas y tokens), ni cookies, ni query strings; las cabeceras `Authorization`, `Cookie` y similares se enmascaran; y al usuario se le reporta **solo por id**. Con `send_default_pii=False` Sentry tampoco guarda IP ni correo.

En el arranque en `staging`/`prod` sin `SENTRY_DSN` aparece un aviso `Deployment check` en el log.

## 3. Métricas (Prometheus)

`GET /metrics` en formato Prometheus, **protegido por token**: no existe (404) hasta que se define `METRICS_TOKEN`, y con él exige `Authorization: Bearer <token>`. Usa un valor largo y aleatorio (16+ caracteres; se avisa si es más corto). No aparece en Swagger.

Configuración de Prometheus / Grafana Agent:

```yaml
scrape_configs:
  - job_name: somos-r-backend
    scheme: https
    metrics_path: /metrics
    authorization:
      type: Bearer
      credentials: <METRICS_TOKEN>
    static_configs:
      - targets: ["api.somosr.com"]
```

| Métrica | Etiquetas | Para qué |
|---|---|---|
| `http_requests_total` | `method`, `route`, `status` | Tasa de peticiones y de errores |
| `http_request_duration_seconds` | `method`, `route` | Latencia (histograma: p50/p95/p99) |
| `http_requests_in_progress` | — | Peticiones en curso (saturación) |
| `auth_logins_total` | `outcome` = `success`, `failed`, `blocked` | Detectar fuerza bruta |
| `security_events_total` | `event` = `unauthorized`, `forbidden`, `rate_limited` | Picos de accesos rechazados |

**La etiqueta `route` es la plantilla** (`/users/{user_id}`), nunca la ruta real, y todo lo que no existe cae en `unmatched`. Así nadie puede disparar el número de series pidiendo URLs al azar.

**Varios workers:** cada proceso tiene sus propios contadores. La imagen de producción define `PROMETHEUS_MULTIPROC_DIR`, con lo que `/metrics` suma los de todos los workers (verificado con 3 workers: 30 peticiones repartidas dieron un total de 30). Si ejecutas sin la imagen y con más de un worker, define esa variable.

### Alertas sugeridas

| Alerta | Expresión (PromQL) | Por qué |
|---|---|---|
| Errores del servidor | `sum(rate(http_requests_total{status=~"5.."}[5m])) / sum(rate(http_requests_total[5m])) > 0.02` | Más del 2 % de respuestas 5xx |
| Latencia alta | `histogram_quantile(0.95, sum by (le) (rate(http_request_duration_seconds_bucket[5m]))) > 1` | p95 por encima de 1 s |
| Posible fuerza bruta | `rate(auth_logins_total{outcome="failed"}[5m]) > 1` | Más de un login fallido por segundo sostenido |
| Rate limit disparándose | `rate(security_events_total{event="rate_limited"}[5m]) > 0.5` | Alguien está siendo limitado de forma continua |

## 4. Errores no controlados

Cualquier excepción que no se maneje devuelve `500 {"detail": "Error interno del servidor", "request_id": "…"}`: el cliente nunca ve la traza ni los detalles internos. El detalle completo queda en el log (con el mismo `request_id`) y en Sentry.
