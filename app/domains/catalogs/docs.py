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

ROLES_DOCS = {
    "summary": "Listar roles disponibles",
    "description": """
Retorna los roles activos que pueden asignarse a usuarios de tipo `eca` o `association`.

Usa el campo **`code`** como valor de `role_code` al llamar `POST /auth/register`
(actores `eca`/`association`) o `PATCH /users/{id}`.

| code | Descripción |
|------|-------------|
| `eca_admin` | Administrador ECA |
| `eca_operator` | Operador ECA |
| `association_admin` | Administrador Asociación |

No requiere autenticación.
""",
}
