from typing import Any

TRANSACTION_STATS_DOCS: dict[str, Any] = {
    "summary": "Cifras de las transacciones de un periodo",
    "description": """
Compras y ventas del periodo: cantidad, kilos y valor por tipo, y las pendientes. Por defecto es el mes en curso
hasta hoy; `date_from` y `date_to` (ambos incluidos, como en el listado) eligen otro periodo y `material_code` lo
reduce a un material. Los campos `*_month` conservan su nombre aunque el periodo sea otro. Si `date_to` es anterior a
`date_from` responde `422 invalid_period`.
""",
}
