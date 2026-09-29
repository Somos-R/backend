from typing import Any

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
    "summary": "Activar cuenta de reciclador",
    "description": f"""
El reciclador recibe por correo un enlace de un solo uso cuando la asociación verifica su perfil
(`PATCH /users/{{id}}/verification-status`). Este endpoint recibe el `token` del enlace y la
contraseña elegida, y deja la cuenta lista para iniciar sesión.
{_PASSWORD_POLICY}

El enlace vence a las 48 horas y solo puede usarse una vez. No requiere autenticación.
""",
    "responses": _BAD_LINK,
}

VERIFY_EMAIL_DOCS: dict[str, Any] = {
    "summary": "Confirmar correo electrónico",
    "description": """
Confirma el correo con el `token` del enlace enviado al registrarse. Marca `email_verified_at`.
El enlace vence a las 24 horas y solo puede usarse una vez. No requiere autenticación.
""",
    "responses": _BAD_LINK,
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
}

RESET_PASSWORD_DOCS: dict[str, Any] = {
    "summary": "Restablecer contraseña",
    "description": f"""
Establece una nueva contraseña con el `token` recibido por correo.
{_PASSWORD_POLICY}

El enlace solo puede usarse una vez. No requiere autenticación.
""",
    "responses": _BAD_LINK,
}

CHANGE_PASSWORD_DOCS: dict[str, Any] = {
    "summary": "Cambiar contraseña",
    "description": f"""
Cambia la contraseña del usuario autenticado. Requiere la contraseña actual.
{_PASSWORD_POLICY}

Requiere autenticación con **Bearer token**.
""",
    "responses": {
        400: {
            "description": "La contraseña actual es incorrecta",
            "content": {"application/json": {"example": {"detail": "La contraseña actual es incorrecta"}}},
        },
    },
}
