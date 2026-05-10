LIST_USERS_DOCS = {
    "summary": "Listar usuarios",
    "description": """
Retorna el listado paginado de todos los usuarios registrados, con su perfil completo.

Requiere autenticación con **Bearer token**.

**Filtros disponibles (query params):**
- `user_type_code` — filtra por tipo de usuario (`citizen`, `building`, `recycler`, `eca`, `association`, `b2b_client`)
- `role_code` — filtra por rol (`eca_admin`, `eca_operator`, `association_admin`)
- `verification_status` — filtra recicladores por estado (`pending`, `verified`, `rejected`)

**Paginación:**
- `limit` — cantidad de registros por página (default: `20`, máx: `100`)
- `offset` — registros a saltar (default: `0`)

La respuesta incluye `total` con el conteo total antes de aplicar paginación.
""",
    "responses": {
        401: {
            "description": "Token ausente, inválido o expirado",
            "content": {
                "application/json": {
                    "example": {"detail": "Token inválido o expirado"}
                }
            },
        },
    },
}

GET_USER_DOCS = {
    "summary": "Obtener perfil de un usuario",
    "description": """
Retorna el perfil completo de un usuario por su UUID.

Requiere autenticación con **Bearer token**.

Los campos mostrados varían según el `user_type_code` del usuario:
- `citizen` / `building` → `address`, `latitude`, `longitude`
- `building` → `building_name`, `num_units`, `representation_document`
- `recycler` → `profile_picture`, `id_picture`, `verification_status`
- `eca` → `employee_code`, `permissions`
- `association` → `association_nit`, `legal_representative`
- `b2b_client` → `company_name`, `tax_id`, `commercial_contact`, `rep_goals`
""",
    "responses": {
        401: {
            "description": "Token ausente, inválido o expirado",
            "content": {
                "application/json": {
                    "example": {"detail": "Token inválido o expirado"}
                }
            },
        },
        404: {
            "description": "Usuario no encontrado",
            "content": {
                "application/json": {
                    "example": {"detail": "Usuario no encontrado"}
                }
            },
        },
    },
}

UPDATE_RECYCLER_STATUS_DOCS = {
    "summary": "Actualizar estado de verificación de un reciclador",
    "description": """
Actualiza el estado de verificación de un reciclador. Solo aplica para usuarios de tipo `recycler`.

Requiere autenticación con **Bearer token**.

**Estados disponibles:**
- `pending` → Pendiente (estado inicial al registrar)
- `verified` → Verificado — se asigna automáticamente la contraseña con el número de documento del reciclador
- `rejected` → Rechazado — se debe incluir `rejection_reason` explicando el motivo

Cuando el reciclador pasa a estado `verified`, su contraseña queda configurada como su `id_number`,
permitiéndole iniciar sesión desde ese momento en `POST /auth/login`.
""",
    "responses": {
        400: {
            "description": "El usuario no es de tipo reciclador",
            "content": {
                "application/json": {
                    "example": {"detail": "Este endpoint solo aplica para recicladores"}
                }
            },
        },
        401: {
            "description": "Token ausente, inválido o expirado",
            "content": {
                "application/json": {
                    "example": {"detail": "Token inválido o expirado"}
                }
            },
        },
        404: {
            "description": "Usuario no encontrado",
            "content": {
                "application/json": {
                    "example": {"detail": "Usuario no encontrado"}
                }
            },
        },
        422: {
            "description": "rejection_reason requerido al rechazar",
            "content": {
                "application/json": {
                    "example": {"detail": "rejection_reason es requerido cuando el estado es rechazado (2)"}
                }
            },
        },
    },
}

UPDATE_USER_DOCS = {
    "summary": "Actualizar perfil de un usuario",
    "description": """
Actualiza parcialmente el perfil de un usuario. Solo se modifican los campos
que se incluyan en el cuerpo de la petición (**PATCH semántico**).

Requiere autenticación con **Bearer token**.

**Campos no actualizables por este endpoint:** `email`, `password`, `id_type`,
`id_number`, `user_type_code`, `verification_status`.

**Ejemplos de uso:**
- Cambiar teléfono: `{"phone": "3001234567"}`
- Asignar rol: `{"role_code": "eca_admin"}`
- Actualizar varios campos a la vez: `{"full_name": "...", "phone": "...", "role_code": "..."}`
""",
    "responses": {
        401: {
            "description": "Token ausente, inválido o expirado",
            "content": {
                "application/json": {
                    "example": {"detail": "Token inválido o expirado"}
                }
            },
        },
        404: {
            "description": "Usuario no encontrado",
            "content": {
                "application/json": {
                    "example": {"detail": "Usuario no encontrado"}
                }
            },
        },
        409: {
            "description": "El `tax_id` ya está en uso por otro usuario",
            "content": {
                "application/json": {
                    "example": {"detail": "tax_id already registered"}
                }
            },
        },
    },
}
