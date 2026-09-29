from typing import Any

LIST_USERS_DOCS: dict[str, Any] = {
    "summary": "Listar usuarios",
    "description": """
Retorna el listado paginado de los usuarios que el rol puede consultar, con su perfil completo.

**Alcance por organización:** el personal de ECA y Asociación solo aparece para quienes pertenecen a **su misma organización**; el de otras organizaciones nunca aparece (ni en el listado, ni en `total`, ni en la búsqueda `q`). Quien no tiene organización no ve a nadie del personal.

Requiere autenticación con **Bearer token**.

**Quién puede:** personal de asociación o ECA (`association_admin`, `association_operator`, `route_manager`, `eca_admin`, `eca_operator`, `eca_warehouse`). El alcance depende del rol:
- `association_admin`: todos los tipos de usuario.
- `eca_admin`: usuarios `eca` y recicladores.
- Resto del personal: solo recicladores (403 si pide otro tipo).

**Filtros disponibles (query params):**
- `user_type_code` — filtra por tipo de usuario (`citizen`, `building`, `recycler`, `eca`, `association`, `b2b_client`)
- `role_code` — filtra por rol (ver `GET /catalogs/roles`)
- `verification_status` — filtra recicladores por estado (`pending`, `verified`, `rejected`)
- `q` — búsqueda por texto: contiene, sin distinguir mayúsculas ni tildes (`perez` encuentra `Pérez`), sobre `full_name`, `id_number` y `email`. Mínimo 2 caracteres (si es menor se ignora), máx. 100. Se combina con el resto de filtros y `total` refleja el filtro completo.

**Paginación:**
- `limit` — cantidad de registros por página (default: `20`, máx: `500`)
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

GET_USER_DOCS: dict[str, Any] = {
    "summary": "Obtener perfil de un usuario",
    "description": """
Retorna el perfil completo de un usuario por su UUID.

Requiere autenticación con **Bearer token**.

**Quién puede:** el propio usuario, o personal con permiso de consulta sobre ese tipo de usuario (mismo alcance que `GET /users`). Cualquier otro caso responde 403.

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

UPDATE_RECYCLER_STATUS_DOCS: dict[str, Any] = {
    "summary": "Actualizar estado de verificación de un reciclador",
    "description": """
Actualiza el estado de verificación de un reciclador. Solo aplica para usuarios de tipo `recycler`.

Requiere autenticación con **Bearer token**.

**Quién puede:** `association_admin` y `association_operator`.

**Estados disponibles:**
- `pending` → Pendiente (estado inicial al registrar)
- `verified` → Verificado — se envía al correo del reciclador un enlace de un solo uso para crear su contraseña
- `rejected` → Rechazado — se debe incluir `rejection_reason` explicando el motivo

Cuando el reciclador pasa a estado `verified` recibe un correo con un enlace (vigente 48 horas, de un solo
uso). Con el `token` del enlace crea su contraseña en `POST /auth/activate` y desde ese momento puede iniciar
sesión en `POST /auth/login`. Nunca se deriva una contraseña de sus datos personales. Si ya tenía contraseña
(por ejemplo, fue rechazado y luego verificado de nuevo), no se envía un enlace nuevo.
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

UPDATE_USER_DOCS: dict[str, Any] = {
    "summary": "Actualizar perfil de un usuario",
    "description": """
Actualiza parcialmente el perfil de un usuario. Solo se modifican los campos
que se incluyan en el cuerpo de la petición (**PATCH semántico**).

Requiere autenticación con **Bearer token**.

**Quién puede:**
- El propio usuario, sobre sus datos personales.
- `association_admin` sobre usuarios `association` y recicladores; `eca_admin` sobre usuarios `eca`.

`organization_id` (la organización de la que forma parte el personal de ECA o Asociación) es de solo lectura: no se puede modificar por este endpoint.

Los campos `role_code`, `permissions`, `association_id` y `employee_code` solo los puede modificar un administrador de la organización sobre *otro* usuario, nunca sobre sí mismo. El `role_code` debe ser un rol válido para el tipo del usuario destino (`eca_*` para `eca`; `association_*` y `route_manager` para `association`).

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
