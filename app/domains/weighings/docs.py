from typing import Any

CREATE_WEIGHING_DOCS: dict[str, Any] = {
    "summary": "Registrar un pesaje",
    "description": """
Registra un pesaje en una bodega de la ECA de quien pesa, de un reciclador (`recycler_id`) o de una persona no
registrada (`seller`). Nace en `pending`.

**Reintentos sin duplicar:** la cabecera opcional `Idempotency-Key` (un UUID que el cliente genera al crear el
pesaje y repite en cada reintento, por ejemplo al sincronizar lo que se registró sin conexión) hace que un
reintento devuelva la respuesta original sin registrar otro pesaje. La misma clave con otro cuerpo responde
`422 idempotency_key_reused`; mientras la primera petición sigue en curso, `409 idempotency_request_in_progress`.
La clave se recuerda 7 días y es propia de cada cuenta.
""",
}
