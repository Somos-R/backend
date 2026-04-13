DOCUMENT_TYPES_DOCS = {
    "summary": "Listar tipos de documento",
    "description": """
Retorna los tipos de documento de identidad disponibles para el registro de usuarios.

Usa el campo **`code`** como valor de `id_type` al llamar `POST /auth/register`.

| code | Descripción |
|------|-------------|
| `CC` | Cédula de Ciudadanía |
| `CE` | Cédula de Extranjería |
| `NIT` | Número de Identificación Tributaria |
| `PA` | Pasaporte |
| `PPT` | Permiso de Protección Temporal |

No requiere autenticación.
""",
}
