from typing import Any

_WHO = """
**Quién puede:** ciudadanos (`citizen`) y edificios (`building`), siempre sobre sus propias solicitudes. Cualquier
otra cuenta recibe `403 collections_requesters_only`; la solicitud de otra persona responde
`404 collection_not_found`, igual que si no existiera."""

CREATE_COLLECTION_DOCS: dict[str, Any] = {
    "summary": "Solicitar una recolección",
    "description": f"""
Crea una solicitud de recolección en estado `pending_assignment`. Quien la pide es siempre la cuenta autenticada:
el cuerpo no lleva `citizen_id` ni `requester_id`.

- `latitude` / `longitude`: grados decimales (hasta 6 decimales). Se guardan también como punto PostGIS para el
  motor de rutas.
- `materials`: de 1 a 10 códigos activos del catálogo de materiales (hoy `paper`, `plastic`, `glass`, `metal`,
  `cardboard`, `electronic`, `organic`); los repetidos se ignoran.
- `scheduled_date` (opcional): no puede ser anterior a hoy (hora de Colombia).

Errores: `404 material_not_found`, `400 material_inactive`, `422 scheduled_date_in_past`, `422 validation_error`.

**Reintentos sin duplicar:** la cabecera opcional `Idempotency-Key` (un UUID que el cliente genera al crear la
solicitud y repite en cada reintento) hace que un reintento devuelva la respuesta original, sin crear otra
solicitud. La misma clave con otro cuerpo responde `422 idempotency_key_reused`; mientras la primera petición
sigue en curso, `409 idempotency_request_in_progress`. La clave se recuerda 7 días.

Queda auditado como `collection.requested` (solo los códigos de material, nunca la dirección).
{_WHO}
""",
}

LIST_COLLECTIONS_DOCS: dict[str, Any] = {
    "summary": "Mis solicitudes de recolección",
    "description": f"""
Lista paginada de las solicitudes de la cuenta autenticada, de la más reciente a la más antigua. Filtro opcional
`status` (`pending_assignment`, `assigned`, `rejected`, `cancelled`). Responde `{{"total", "items"}}`.
{_WHO}
""",
}

GET_COLLECTION_DOCS: dict[str, Any] = {
    "summary": "Detalle de una solicitud de recolección",
    "description": f"""
Una solicitud propia por su `id`.
{_WHO}
""",
}

CANCEL_COLLECTION_DOCS: dict[str, Any] = {
    "summary": "Cancelar una solicitud de recolección",
    "description": f"""
Cancela una solicitud que sigue en `pending_assignment`, con un motivo opcional (`reason`, hasta 200
caracteres). En cualquier otro estado (ya asignada a una ruta, rechazada o cancelada) responde
`409 collection_not_cancellable`. Queda auditado como `collection.cancelled`.
{_WHO}
""",
}
