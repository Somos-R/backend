from typing import Any

TOO_MANY_REQUESTS: dict[int | str, dict[str, Any]] = {
    429: {
        "description": "Demasiadas solicitudes desde esta IP; reintenta en unos minutos (header `Retry-After`)",
        "content": {"application/json": {"example": {
            "detail": "Demasiadas solicitudes. Intenta de nuevo en unos minutos"}}},
    },
}

REGISTER_DOCS: dict[str, Any] = {
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

**Asignación de rol al crear (`eca` y `association`):** ambos aceptan `role_code` opcional,
pero **solo si la petición viene autenticada como administrador** (`eca_admin` o `association_admin`)
y el rol es de su propia organización. Un registro anónimo con `role_code` responde 403; sin
`role_code` el usuario queda sin rol (y sin permisos) hasta que un administrador se lo asigne.
El valor debe existir y estar activo en `GET /catalogs/roles`; si no, retorna `422`.

**Organización del personal (`eca` y `association`):** el personal que crea un administrador pasa a formar parte de la organización de ese administrador (`organization_id`). No se envía en la petición: no se puede elegir. Un registro anónimo queda sin organización.

**Para dar de alta personal, usa `POST /users/invitations`** (la persona elige su propia contraseña por un enlace). Crear personal con contraseña por este endpoint queda como está por compatibilidad, pero es la vía que se retirará.

**Asociación del reciclador (`association_id`):** todo reciclador pertenece a una asociación, que es la que lo verifica. El personal de una asociación lo registra en la suya (no hace falta enviarlo); cualquier otro debe elegir una asociación aprobada de `GET /catalogs/associations` (`422 association_required` si falta, `422 invalid_association` si no existe o no está aprobada).

**Registro de reciclador (lo hace la asociación):** no requiere `password`. El reciclador queda
en estado `0` (pendiente) y no puede iniciar sesión hasta ser verificado con `PATCH /users/{id}/verification-status`.

Consulta los valores válidos de `id_type` en `GET /catalogs/document-types`.
""",
    "responses": {
        **TOO_MANY_REQUESTS,
        409: {
            "description": "Email o número de documento ya registrado",
            "content": {
                "application/json": {
                    "example": {"detail": "Email or ID number already registered"}
                }
            },
        },
        422: {
            "description": "Campos requeridos faltantes, `user_type` inválido, o `role_code` inexistente/inactivo o que no corresponde al tipo de usuario",
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

ME_DOCS: dict[str, Any] = {
    "summary": "Perfil y capacidades de la sesión actual",
    "description": """
Devuelve el perfil del usuario autenticado (mismos campos que `GET /users/{id}`) y **`capabilities`**:
la lista de lo que puede hacer, **evaluada por el servidor** con el par (tipo de usuario, rol).

Las capacidades usan notación de punto (`weighings.create`, `inventory.view`, `recyclers.verify`, ...)
y salen del mismo módulo de permisos que protege los endpoints, así que no pueden contradecirlos.
Una capacidad solo existe si hay un endpoint que la exige.

Sirve para mostrar u ocultar botones y pantallas sin duplicar la matriz de permisos en el cliente.
**No es una barrera de seguridad**: cada endpoint sigue respondiendo `403` a quien no tiene permiso.
Un usuario sin rol asignado recibe la lista vacía. El rol se lee de la base de datos, no del token,
así que el resultado está al día aunque el token sea anterior a un cambio de rol.

Requiere autenticación con **Bearer token**.
""",
    "responses": {
        401: {
            "description": "Token ausente, inválido o ya revocado",
            "content": {"application/json": {"example": {"detail": "Token inválido o expirado", "code": "invalid_token"}}},
        },
    },
}

LOGOUT_DOCS: dict[str, Any] = {
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

LOGIN_DOCS: dict[str, Any] = {
    "summary": "Iniciar sesión",
    "description": """
Valida las credenciales del usuario y retorna un **JWT Bearer token**.

Retorna un `access_token` de corta duración (`expires_in`, en segundos) y un `refresh_token`
de un solo uso. Cuando el access token expire, se renueva con `POST /auth/refresh` sin volver a pedir
la contraseña. Guarda el refresh token de forma segura (Keychain/Keystore en móvil).

El access token debe enviarse en el header de cada petición protegida:

```
Authorization: Bearer <access_token>
```

El payload del token contiene `sub` (UUID del usuario) y `user_type`.
Por seguridad, el error 401 no indica si el email existe o no.

**Protección contra abuso:**
- Máximo **10 intentos por minuto por IP** (429 al superarlo).
- Tras **5 fallos consecutivos** la cuenta se bloquea 1 minuto; cada fallo adicional duplica el bloqueo
  (2, 4, 8… hasta 60 minutos). Un login exitoso o un restablecimiento de contraseña reinician el contador.
- Mientras la cuenta está bloqueada la respuesta es el mismo 401 de credenciales inválidas, incluso con la
  contraseña correcta, para no revelar qué cuentas existen ni cuáles están bloqueadas.
""",
    "responses": {
        **TOO_MANY_REQUESTS,
        401: {
            "description": "Credenciales inválidas (email no existe, contraseña incorrecta o cuenta bloqueada temporalmente)",
            "content": {
                "application/json": {
                    "example": {"detail": "Invalid credentials"}
                }
            },
        },
    },
}


_BAD_LINK: dict[int | str, dict[str, Any]] = {
    400: {
        "description": "El enlace es inválido, ya fue usado o expiró",
        "content": {"application/json": {"example": {"detail": "El enlace es inválido, ya fue usado o expiró"}}},
    },
}

_PASSWORD_POLICY = """
**Política de contraseña:** mínimo 10 caracteres, máximo 72 bytes, no puede ser solo números,
demasiado repetitiva ni una contraseña común."""

ACTIVATE_DOCS: dict[str, Any] = {
    "summary": "Activar cuenta (reciclador o personal invitado)",
    "description": f"""
Sirve para dos casos: el **reciclador**, que recibe por correo un enlace de un solo uso cuando la asociación
verifica su perfil (`PATCH /users/{{id}}/verification-status`), y el **personal invitado** por su organización
(`POST /users/invitations`). Este endpoint recibe el `token` del enlace y la contraseña elegida, y deja la
cuenta lista para iniciar sesión. Solo funciona con cuentas que aún no tienen contraseña.
{_PASSWORD_POLICY}

El enlace vence a las 48 horas y solo puede usarse una vez. No requiere autenticación.
""",
    "responses": {**_BAD_LINK, **TOO_MANY_REQUESTS},
}

VERIFY_EMAIL_DOCS: dict[str, Any] = {
    "summary": "Confirmar correo electrónico",
    "description": """
Confirma el correo con el `token` del enlace enviado al registrarse. Marca `email_verified_at`.
El enlace vence a las 24 horas y solo puede usarse una vez. No requiere autenticación.
""",
    "responses": {**_BAD_LINK, **TOO_MANY_REQUESTS},
}

RESEND_VERIFICATION_DOCS: dict[str, Any] = {
    "summary": "Reenviar correo de confirmación",
    "description": """
Envía un nuevo enlace de confirmación al correo del usuario autenticado y anula el anterior.
Si el correo ya está confirmado no envía nada. Requiere autenticación con **Bearer token**.
""",
}

FORGOT_PASSWORD_DOCS: dict[str, Any] = {
    "summary": "Solicitar restablecimiento de contraseña",
    "description": """
Envía un enlace de restablecimiento al correo indicado, si corresponde a una cuenta activa con
contraseña. **Siempre responde 200 con el mismo mensaje**, exista o no la cuenta, para no revelar
qué correos están registrados. El enlace vence a los 60 minutos. No requiere autenticación.
""",
    "responses": TOO_MANY_REQUESTS,
}

RESET_PASSWORD_DOCS: dict[str, Any] = {
    "summary": "Restablecer contraseña",
    "description": f"""
Establece una nueva contraseña con el `token` recibido por correo.
{_PASSWORD_POLICY}

El enlace solo puede usarse una vez. No requiere autenticación.
""",
    "responses": {**_BAD_LINK, **TOO_MANY_REQUESTS},
}

CHANGE_PASSWORD_DOCS: dict[str, Any] = {
    "summary": "Cambiar contraseña",
    "description": f"""
Cambia la contraseña del usuario autenticado. Requiere la contraseña actual.
{_PASSWORD_POLICY}

Requiere autenticación con **Bearer token**.
""",
    "responses": {
        **TOO_MANY_REQUESTS,
        400: {
            "description": "La contraseña actual es incorrecta",
            "content": {"application/json": {"example": {"detail": "La contraseña actual es incorrecta"}}},
        },
    },
}

REFRESH_DOCS: dict[str, Any] = {
    "summary": "Renovar sesión",
    "description": """
Intercambia un `refresh_token` por un nuevo par `access_token` + `refresh_token`. **Cada refresh token
sirve una sola vez**: el anterior queda invalidado (rotación) y hay que guardar el nuevo.

Si se presenta un refresh token que ya fue usado, se asume que fue copiado y se **revocan todas las
sesiones de ese inicio de sesión**; el usuario debe volver a iniciar sesión. También se rechaza si la
cuenta fue desactivada, el reciclador dejó de estar verificado, la sesión se cerró (`POST /auth/logout`) o
la contraseña cambió. Los refresh tokens duran 30 días. No requiere `Authorization`.
""",
    "responses": {
        **TOO_MANY_REQUESTS,
        401: {
            "description": "Refresh token inválido, usado, revocado o expirado",
            "content": {"application/json": {"example": {
                "detail": "El token de renovación es inválido o expiró. Inicia sesión de nuevo"}}},
        },
    },
}
