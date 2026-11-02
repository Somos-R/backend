from typing import Any

INVENTORY_STATS_DOCS: dict[str, Any] = {
    "summary": "Cifras del stock actual",
    "description": """
Kilos y valor del stock que el usuario puede ver, y cuántos ítems hay disponibles, con stock bajo o agotados. Es una
foto de hoy, así que no admite periodo; `material_code` la reduce a un material.
""",
}
