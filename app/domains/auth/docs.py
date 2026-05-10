REGISTER_DOCS = {
    "summary": "Registrar un nuevo usuario",
    "description": """
Crea una cuenta para cualquier tipo de actor del sistema.

El campo **`user_type`** determina qué schema se aplica y qué campos adicionales
son requeridos. Selecciona un ejemplo del dropdown para ver el formato de cada actor.

El campo **`user_type_code`** determina qué schema se aplica y qué campos adicionales son requeridos.

**Campos comunes a todos los actores (excepto recycler):** `email`, `password`, `full_name`, `id_type`, `id_number`.

**Campos requeridos por actor:**
- `building` → `building_name`, `num_units`
- `association` → `association_nit`, `legal_representative`
- `b2b_client` → `company_name`, `tax_id`

**Registro de reciclador (lo hace la asociación):** no requiere `password`. El reciclador queda
en estado `0` (pendiente) y no puede iniciar sesión hasta ser verificado con `PATCH /users/{id}/verification-status`.

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

LOGOUT_DOCS = {
    "summary": "Cerrar sesión",
    "description": """
Invalida el token JWT activo agregándolo a la lista negra del servidor.

Después de llamar este endpoint, cualquier petición con el mismo token
recibirá un `401 La sesión ha sido cerrada`, aunque el token no haya expirado.

Requiere autenticación con **Bearer token**.
""",
    "responses": {
        401: {
            "description": "Token ausente, inválido o ya revocado",
            "content": {
                "application/json": {
                    "example": {"detail": "Token inválido o expirado"}
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
