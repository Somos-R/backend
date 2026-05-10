"""
PostToolUse hook — detects changes to key files and outputs an impact checklist.
Claude reads this output and acts on it automatically.
"""
import io
import json
import os
import sys

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

IMPACT_RULES = {
    "users/models.py": [
        ("migrations/versions/",         "¿Requiere nueva migración de Alembic?"),
        ("app/domains/auth/schemas.py",  "¿Los nombres de campo coinciden con el modelo?"),
        ("app/domains/auth/docs.py",     "¿Las descripciones del Swagger están actualizadas?"),
        ("README.md",                    "¿Cambió la estructura de tablas?"),
    ],
    "catalogs/models.py": [
        ("migrations/versions/",         "¿El seed refleja los nuevos modelos?"),
        ("app/domains/catalogs/docs.py", "¿Las descripciones del catálogo están actualizadas?"),
        ("app/domains/catalogs/router.py","¿Hay endpoint para el nuevo catálogo?"),
    ],
    "auth/schemas.py": [
        ("app/domains/auth/docs.py",     "¿Los nombres de campo en las descripciones coinciden?"),
        ("localhost:8000/redoc",         "Reinicia el servidor para ver los cambios"),
    ],
    "users/enums.py": [
        ("app/domains/users/models.py",  "¿Se actualizaron las referencias al enum?"),
        ("app/domains/auth/schemas.py",  "¿Los valores Literal están actualizados?"),
        ("app/domains/catalogs/models.py","¿El enum se convirtió en tabla de lookup?"),
    ],
    "auth/router.py": [
        ("app/domains/auth/docs.py",     "¿Las descripciones coinciden con la lógica actual?"),
    ],
    "catalogs/router.py": [
        ("app/domains/catalogs/docs.py", "¿Las descripciones coinciden con la lógica actual?"),
    ],
}

def main():
    try:
        data = json.load(sys.stdin)
    except Exception:
        sys.exit(0)

    tool_name = data.get("tool_name", "")
    if tool_name not in ("Edit", "Write"):
        sys.exit(0)

    file_path = data.get("tool_input", {}).get("file_path", "")
    if not file_path:
        sys.exit(0)

    normalized = file_path.replace("\\", "/")

    matched_impacts = []
    for pattern, impacts in IMPACT_RULES.items():
        if pattern in normalized:
            matched_impacts = impacts
            break

    if not matched_impacts:
        sys.exit(0)

    file_name = os.path.basename(normalized)
    print(f"\n[impact-check] Archivo clave modificado: {file_name}")
    print("Revisa y actualiza los siguientes archivos relacionados:\n")
    for path, reason in matched_impacts:
        print(f"  • {path}")
        print(f"    → {reason}")
    print()

if __name__ == "__main__":
    main()
