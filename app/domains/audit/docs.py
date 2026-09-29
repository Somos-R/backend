from typing import Any

LIST_AUDIT_DOCS: dict[str, Any] = {
    "summary": "Consultar el registro de auditoría",
    "description": """
Lista los eventos de seguridad y de negocio registrados, del más reciente al más antiguo: quién
(`actor_id`, `actor_role`), qué (`action`), sobre qué (`target_type`, `target_id`), con qué resultado
(`outcome`: `success` o `failure`), cuándo (`occurred_at`), desde dónde (`ip`) y en qué petición
(`request_id`, el mismo `X-Request-ID` de los logs). `details` trae datos específicos del evento
(por ejemplo, el rol anterior y el nuevo); **nunca** contraseñas, tokens ni hashes.

**Quién puede:** solo `association_admin`.

El registro es de solo lectura y de solo anexar: la base de datos rechaza modificar o borrar filas.

**Acciones registradas** (`action`):
- Cuentas y sesión: `user.registered`, `auth.login`, `auth.login_failed`, `auth.logout`,
  `auth.refresh_reuse_detected`, `auth.refresh_denied`, `account.activated`, `email.verified`,
  `password.reset_requested`, `password.reset`, `password.changed`, `password.change_failed`.
- Usuarios: `recycler.verified`, `recycler.rejected`, `user.updated`, `user.role_changed`.
- Pesajes: `weighing.created`, `weighing.validated`, `weighing.rejected`, `weighing.paid`.
- Transacciones: `transaction.created`, `transaction.cancelled`, `transaction.delivered`, `transaction.paid`.
- Inventario: `inventory.updated`.
- Somos R (solo visibles desde el backoffice): `admin.*` y `platform_admin.created`.

**Filtros:** `action`, `outcome`, `actor_id`, `target_type`, `target_id`, `request_id`, `since` y `until`
(fechas ISO 8601). Paginación con `limit` (1–200, por defecto 50) y `offset`.

**Alcance por organización:** solo se listan los eventos de las personas de **tu organización** y lo intentado contra sus cuentas; nunca los de otras organizaciones. Sin organización, la lista está vacía.

Requiere autenticación con **Bearer token**.
""",
    "responses": {
        403: {
            "description": "El usuario no es `association_admin`",
            "content": {"application/json": {"example": {
                "detail": "No tienes permisos para realizar esta acción"}}},
        },
    },
}


ADMIN_LIST_AUDIT_DOCS: dict[str, Any] = {
    "summary": "Consultar todo el registro de auditoría (backoffice)",
    "description": """
El registro **completo**, de todas las organizaciones y de las cuentas de Somos R, del más reciente al más
antiguo. Es el mismo formato que `GET /audit-log`, sin el alcance por organización.

**Quién puede:** cuentas de Somos R con la capacidad `audit.read` (hoy `platform_admin`), con un token de
backoffice. Como todo `/admin`, respeta la restricción de red y los límites estrictos.

**Filtros:** los de `GET /audit-log` (`action`, `outcome`, `actor_id`, `target_type`, `target_id`,
`request_id`, `since`, `until`) más `actor_role` y **`organization_id`** (los eventos de las personas de esa
organización y lo intentado contra sus cuentas). Paginación con `limit` (1–200) y `offset`.

**Cada consulta queda registrada** (`admin.audit_viewed`): quién la hizo y qué filtros usó (solo sus
nombres, no sus valores), porque leer todo el registro es en sí mismo un acceso sensible.

Acciones propias de Somos R: `admin.login`, `admin.login_failed`, `admin.logout`, `admin.mfa_enrolled`,
`admin.mfa_failed`, `admin.recovery_code_used`, `admin.recovery_codes_regenerated`, `admin.mfa_reset`,
`admin.audit_viewed` y `platform_admin.created`.
""",
    "responses": {
        401: {"description": "Token ausente, inválido o que no es de backoffice"},
        403: {"description": "La cuenta no tiene la capacidad `audit.read` o la red no está permitida"},
    },
}
