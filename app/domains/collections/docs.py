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

Una solicitud rechazada trae `rejection_reason` (`code`, `label` e `is_correctable`: si quien la pidió puede
corregirla y volver a enviarla, o si el rechazo es definitivo) y `rejected_at`; en otro estado ambos son `null`.
`association_id` es la asociación que atiende la solicitud: se asigna al crearla (hoy, la única asociación aprobada
del piloto) y es `null` si no hay una sola a quién asignarla.
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

RESUBMIT_COLLECTION_DOCS: dict[str, Any] = {
    "summary": "Corregir y reenviar una solicitud rechazada",
    "description": f"""
Quien pidió la recolección corrige una solicitud **rechazada por un motivo corregible**
(`rejection_reason.is_correctable: true`) y la vuelve a enviar. El cuerpo es la solicitud completa ya corregida,
con los mismos campos y reglas que `POST /collections` (`address`, `latitude`, `longitude`, `materials`, `notes`,
`scheduled_date`): reemplaza lo enviado antes.

La solicitud vuelve a `pending_assignment` **conservando su `created_at` original**, así que no pierde su lugar en
la bandeja de la asociación (ordenada de la más antigua a la más reciente). `rejection_reason` y `rejected_at`
vuelven a `null`, y vuelve a la misma asociación (`association_id` no cambia; solo si no tenía una se le asigna
como al crearla). Cada rechazo y cada reenvío quedan
en el historial (`collection_rejections`).

Errores:
- `409 collection_not_resubmittable`: la solicitud no está `rejected`.
- `409 collection_rejection_final`: el motivo del rechazo es definitivo; la solicitud queda cerrada.
- `409 collection_resubmission_limit_reached`: ya se reenvió el máximo de veces (hoy 3,
  `COLLECTION_MAX_RESUBMISSIONS`).
- `404 material_not_found`, `400 material_inactive`, `422 scheduled_date_in_past`, `422 validation_error`.

Queda auditado como `collection.resubmitted` (códigos de material, motivo anterior y número de reenvío; nunca la
dirección ni las notas).
{_WHO}
""",
}

REJECTION_REASONS_DOCS: dict[str, Any] = {
    "summary": "Motivos de rechazo de una solicitud de recolección",
    "description": """
Catálogo de los motivos con los que una asociación rechaza una solicitud, solo los activos; primero los que
quien la pidió puede corregir (`is_correctable: true`) y luego los definitivos. Cada uno trae `code`, `label`
e `is_correctable`.

Hoy: corregibles `incomplete_address`, `invalid_contact`, `insufficient_description`; definitivos
`duplicate_request`, `outside_coverage`, `non_recoverable_material`.

**Quién puede:** cualquier cuenta autenticada.
""",
}

REJECT_COLLECTION_DOCS: dict[str, Any] = {
    "summary": "Rechazar una solicitud de recolección",
    "description": """
La asociación rechaza una solicitud que le fue asignada y sigue en `pending_assignment`, con un motivo del
catálogo (`reason_code`, ver `GET /collections/rejection-reasons`), nunca texto libre. La solicitud pasa a
`rejected` y quien la pidió ve el motivo, y si puede corregirla, en su detalle; si el motivo es corregible, puede
reenviarla con `PUT /collections/{id}/resubmit`. Cada rechazo queda en el historial (`collection_rejections`).

Errores: `422 rejection_reason_not_found` (código desconocido o desactivado), `409 collection_not_rejectable`
(la solicitud ya no está en `pending_assignment`), `422 validation_error`.

Queda auditado como `collection.rejected` (solo el código del motivo).

**Quién puede:** el personal de la asociación con rol `association_admin` o `association_operator`
(capacidad `collections.reject`), y solo sobre las solicitudes asignadas a su propia asociación; cualquier
otra responde `404 collection_not_found`, igual que si no existiera. Otros roles reciben `403`.
""",
}

ASSOCIATION_INBOX_DOCS: dict[str, Any] = {
    "summary": "Bandeja de solicitudes de recolección de la asociación",
    "description": """
Lista paginada de las solicitudes asignadas a la asociación `association_id`, **de la más antigua a la más
reciente** (la que lleva más tiempo esperando va primero). Filtro opcional `status` (`pending_assignment`,
`assigned`, `rejected`, `cancelled`); para la bandeja de revisión, `status=pending_assignment`. Paginación con
`limit` (1–100, por defecto 20) y `offset`. Responde `{"total", "items"}` con la misma forma de cada solicitud que
`GET /collections` (dirección, coordenadas, materiales, notas, fecha programada y, si fue rechazada, su motivo).

Aún no hay filtros por zona ni por escalado: llegan con la zona de la asociación y con el SLA de REC-04.

Errores: `404 organization_not_found` (el `association_id` no es la asociación de quien consulta),
`422 validation_error` (estado desconocido, `limit` fuera de rango).

**Quién puede:** el personal de la asociación con rol `association_admin` o `association_operator`
(capacidad `collections.view`), y solo sobre su propia asociación: el `id` de otra responde
`404 organization_not_found`, igual que si no existiera. Otros roles reciben `403`.
""",
}

NEARBY_COLLECTIONS_DOCS: dict[str, Any] = {
    "summary": "Solicitudes de recolección cercanas a un punto",
    "description": """
Para el mapa del Operativo y del Encargado de rutas: las solicitudes de **la propia asociación** que están dentro
de un radio de un punto, **de la más cercana a la más lejana**.

- `lat` (-90 a 90) y `lng` (-180 a 180): el punto de referencia.
- `radius`: radio en **metros**, de 1 a 50.000 (por defecto 2.000).
- `status`: por defecto `pending_assignment`, las que esperan ruta.
- `limit` (1–100, por defecto 20) y `offset`.

Responde `{"total", "items"}`; cada solicitud trae la misma información que en la bandeja de la asociación y,
además, `distance_meters`: la distancia en línea recta, en metros, desde el punto pedido. `total` cuenta todas las
que caen dentro del radio, no solo las de la página.

El alcance va primero y el radio nunca lo amplía: solo aparecen las solicitudes asignadas a la asociación de quien
consulta. Las que aún no tienen asociación no aparecen.

**Quién puede:** el personal de la asociación con rol `association_admin`, `association_operator` o `route_manager`
(capacidad `collections.nearby`). Otros roles reciben `403`. Errores: `422 validation_error` (coordenadas, radio o
estado fuera de rango).
""",
}
