REGISTER_DOCS = {
    "summary": "Registrar un nuevo usuario",
    "description": """
Crea una cuenta para cualquier tipo de actor del sistema.

El campo **`user_type`** determina qué schema se aplica y qué campos adicionales
son requeridos. Selecciona un ejemplo del dropdown para ver el formato de cada actor.

**Campos comunes a todos los actores:** `email`, `password`, `full_name`, `id_type`, `id_number`.

**Campos requeridos por actor:**
- `building_admin` → `building_name`, `num_units`
- `asobeum_admin` → `association_nit`, `legal_representative`
- `b2b_client` → `company_name`, `tax_id`

Consulta los valores válidos de `id_type` en `GET /catalogs/document-types`.
""",
    "responses": {
        409: {
            "description": "Email o número de documento ya registrado",
            "content": {
                "application/json": {
                    "example": {"detail": "Email or ID number already registered"}
                }
            },
        },
        422: {
            "description": "Campos requeridos faltantes o `user_type` inválido",
            "content": {
                "application/json": {
                    "example": {
                        "detail": [
                            {
                                "type": "missing",
                                "loc": ["body", "building_name"],
                                "msg": "Field required",
                            }
                        ]
                    }
                }
            },
        },
    },
}

LOGIN_DOCS = {
    "summary": "Iniciar sesión",
    "description": """
Valida las credenciales del usuario y retorna un **JWT Bearer token**.

El token expira en **30 minutos** y debe enviarse en el header de cada
petición protegida:

```
Authorization: Bearer <access_token>
```

El payload del token contiene `sub` (UUID del usuario) y `user_type`.
Por seguridad, el error 401 no indica si el email existe o no.
""",
    "responses": {
        401: {
            "description": "Credenciales inválidas (email no existe o contraseña incorrecta)",
            "content": {
                "application/json": {
                    "example": {"detail": "Invalid credentials"}
                }
            },
        },
    },
}
