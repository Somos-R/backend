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


INVITE_STAFF_DOCS: dict[str, Any] = {
    "summary": "Invitar a una persona a tu organización",
    "description": """
Crea la cuenta de una persona del personal **sin contraseña** y le envía por correo un enlace de activación
de un solo uso; ella elige su propia contraseña con `POST /auth/activate`. Nadie escribe la contraseña de otra
persona. Es la forma de dar de alta personal: reemplaza a `POST /auth/register` para este fin.

**Quién puede:** administradores de organización (`eca_admin`, `association_admin`).

**Lo que hace:**
- La persona queda en **tu organización** (`organization_id`), con el **tipo de usuario de tu organización**
  (una ECA invita personal de ECA; una asociación, de asociación). No se envían ni se pueden elegir.
- `role_code` debe ser un rol activo de tu propia organización (`GET /catalogs/roles`).
- Hasta que active la cuenta no puede iniciar sesión. El enlace vence a las 48 horas y solo sirve una vez; si
  vence o se pierde, usa `POST /users/{id}/invitation/resend`.
- Queda auditado (`user.invited`, con el rol y sin datos personales).

**Errores:** `403 no_organization` (tu cuenta no pertenece a una organización), `403 organization_not_active`
(tu organización no está aprobada), `422 invalid_role`, `422 invalid_id_type`, `409 account_already_exists`
(correo o documento ya registrados).
""",
    "responses": {
        403: {"description": "No eres administrador de organización, o tu organización no puede invitar"},
        409: {"description": "Correo o número de documento ya registrado"},
        422: {"description": "Datos inválidos, `role_code` que no es de tu organización o `id_type` inexistente"},
    },
}

RESEND_INVITATION_DOCS: dict[str, Any] = {
    "summary": "Reenviar la invitación de una persona",
    "description": """
Envía un enlace de activación nuevo a alguien de **tu organización** que fue invitada y todavía no activó su
cuenta. El enlace anterior deja de funcionar.

**Quién puede:** administradores de organización (`eca_admin`, `association_admin`).

Responde `404 user_not_found` si la persona no es de tu organización, y `409 invitation_not_pending` si ya
activó su cuenta. Queda auditado (`user.invitation_resent`).
""",
    "responses": {
        404: {"description": "La persona no existe o no es de tu organización"},
        409: {"description": "La persona ya activó su cuenta"},
    },
}


RECYCLER_LOOKUP_DOCS: dict[str, Any] = {
    "summary": "Identificar a un reciclador por su documento",
    "description": """
Para el momento de pesar: busca a un reciclador **registrado** por su número de documento, de **cualquier
asociación** o de ninguna, porque una ECA recibe el material de quien lo traiga. Devuelve solo lo necesario
para pesar: nombre, documento, si su cuenta está activa, su estado de verificación, su asociación (nombre y
ciudad) y `affiliation` **respecto a tu ECA**:

- `linked`: reciclador verificado de una asociación con vínculo activo con tu ECA.
- `unlinked_association`: tiene asociación, pero no vinculada a tu ECA (o aún no está verificado).
- `independent`: no tiene asociación.

Ningún dato de contacto. `404 recycler_not_found` si no está registrado: en ese caso se puede pesar
identificando a la persona con `seller` en `POST /weighings`.

**Quién puede:** quienes registran pesajes (`eca_admin`, `eca_operator`). `id_type` es opcional.
""",
}
