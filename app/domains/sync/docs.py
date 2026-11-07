from typing import Any

SYNC_BATCH_DOCS: dict[str, Any] = {
    "summary": "Subir de una vez lo que se guardó sin conexión",
    "description": """
Recibe en una sola petición los registros que la app guardó mientras no tenía señal y los procesa **uno por uno**.
Cada operación pasa por la misma creación que su endpoint (`POST /weighings`, `POST /collections`), con sus mismos
permisos y reglas, y con idempotencia.

```json
{ "operations": [
  { "type": "weighing", "idempotency_key": "<uuid>", "client_created_at": "2026-11-03T09:15:00-05:00",
    "payload": { ...cuerpo de POST /weighings... } },
  { "type": "collection", "idempotency_key": "<uuid>", "client_created_at": "...", "payload": { ...cuerpo de POST /collections... } }
] }
```

- **Tipos:** `weighing` y `collection`. (Los estados de parada se agregan cuando existan las rutas.)
- **`idempotency_key`:** un UUID que el dispositivo genera al guardar el registro y repite en cada reintento. Es la
  misma clave que acepta la cabecera `Idempotency-Key` de los endpoints: una clave usada en uno se reconoce en el otro.
- **`client_created_at`:** cuándo lo guardó el dispositivo. Se procesa en ese orden (el más antiguo primero) y se
  devuelve tal cual. La fecha del registro la pone el servidor, que también calcula su hash.
- **Límites:** de 1 a 200 operaciones por lote y **a lo sumo 10 de tipo `collection`** (`422 validation_error` si se
  pasa): el lote no es un camino para saltarse el límite de `POST /collections`. Además la petición cuenta contra el
  mismo límite de peticiones por IP que ese endpoint (`429 rate_limited`).

**Respuesta (200):** `summary` con el conteo y `results` en el **orden de la petición**, uno por operación:

| `status` | Significado |
|---|---|
| `created` | Se creó; `record` es lo que responde el endpoint de creación. |
| `duplicate` | Ya se había creado con esa clave y el mismo contenido; `record` es el original y no se crea nada. |
| `error` | No se creó; `error.code` y `error.detail` dicen por qué (los mismos códigos del endpoint, `validation_error` con la lista de errores del `payload`, `forbidden`, `idempotency_key_reused`, `idempotency_request_in_progress`, `internal_error`). |

**Una operación que falla no revierte las demás.** Reenviar el mismo lote es seguro: lo ya creado vuelve como
`duplicate`, y solo se vuelve a intentar lo que falló.

Cada operación exige los mismos permisos que su endpoint: un `weighing` lo pueden crear `eca_admin` y `eca_operator`,
y una `collection` un ciudadano o un edificio. Una operación para la que la cuenta no tiene permiso se informa como
`error` sin afectar a las demás.
""",
}
