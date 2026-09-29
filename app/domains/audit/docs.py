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
