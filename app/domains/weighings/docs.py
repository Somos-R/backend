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

WEIGHING_STATS_DOCS: dict[str, Any] = {
    "summary": "Cifras de los pesajes de un periodo",
    "description": """
Cantidad y kilos de los pesajes del periodo, kilos por material y cuántos esperan validación. Por defecto es el mes en
curso hasta hoy; `date_from` y `date_to` (ambos incluidos, como en el listado) eligen otro periodo y `material_code`
lo reduce a un material. Los campos `*_month` conservan su nombre aunque el periodo sea otro. Los pendientes son
todos los que aún esperan validación, sin importar cuándo se pesaron. Si `date_to` es anterior a `date_from`
responde `422 invalid_period`.
""",
}

VERIFY_WEIGHING_DOCS: dict[str, Any] = {
    "summary": "Verificar la integridad de un pesaje",
    "description": """
Recalcula el hash SHA-256 del pesaje y lo compara con el que se guardó al registrarlo. `valid` es `true` si nada
cambió, `false` si algún dato fue alterado y `null` si el pesaje es anterior al hash. Lo puede consultar quien puede
leer el pesaje; a los demás les responde `404 weighing_not_found`.
""",
}

LIST_ALL_WEIGHINGS_DOCS: dict[str, Any] = {
    "summary": "Todos los pesajes que el usuario puede ver, sin paginar",
    "description": """
Devuelve de una vez los pesajes que el usuario puede leer, del más reciente al más antiguo, para los clientes que
filtran, buscan y paginan en el navegador. No recibe filtros ni paginación: son los mismos pesajes (y el mismo alcance)
que se obtienen recorriendo `GET /weighings` sin filtros.

Tiene un tope de seguridad (10.000 pesajes por defecto). Si hay más, `items` trae solo los más recientes,
`truncated` es `true` y `total` sigue siendo el conteo real; en ese caso el cliente debe avisar y volver a la
paginación del servidor.
""",
}
