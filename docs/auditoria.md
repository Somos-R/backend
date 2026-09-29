# Auditoría: quién hizo qué, cuándo y desde dónde

El registro de auditoría responde preguntas que los logs no pueden responder de forma fiable: *¿quién verificó a este reciclador?, ¿quién pagó este pesaje?, ¿quién cambió el rol de esta cuenta?, ¿hubo intentos de entrar a esta cuenta?* Está en la base de datos (no en archivos que rotan), es consultable por API y **no se puede modificar**.

## Qué se registra

| Grupo | Acciones (`action`) |
|---|---|
| Cuentas y sesión | `user.registered`, `auth.login`, `auth.login_failed`, `auth.logout`, `account.activated`, `email.verified` |
| Contraseñas | `password.reset_requested`, `password.reset`, `password.changed`, `password.change_failed` |
| Señales de ataque | `auth.refresh_reuse_detected` (un refresh token ya rotado volvió a presentarse: probablemente copiado), `auth.refresh_denied` |
| Usuarios | `recycler.verified`, `recycler.rejected`, `user.updated`, `user.role_changed` |
| Pesajes | `weighing.created`, `weighing.validated`, `weighing.rejected`, `weighing.paid` |
| Transacciones | `transaction.created`, `transaction.cancelled`, `transaction.delivered`, `transaction.paid` |
| Inventario | `inventory.updated` (con valor anterior y nuevo de precio y stock mínimo) |

**No se registran** las lecturas (`GET`): serían decenas de miles de filas sin valor probatorio; para eso están los logs con `request_id`. Tampoco los refresh normales, ni los intentos de recuperar contraseña para correos que no existen.

## Qué contiene cada evento

| Campo | Significado |
|---|---|
| `occurred_at` | Cuándo (UTC) |
| `action`, `outcome` | Qué pasó y si salió bien (`success`) o fue rechazado (`failure`) |
| `actor_id`, `actor_role` | Quién lo hizo (su rol, o su tipo si no tiene rol). Vacío en eventos anónimos, como un login fallido |
| `target_type`, `target_id` | Sobre qué (`user`, `weighing`, `transaction`, `inventory_item`) |
| `ip` | IP del cliente (la real detrás del proxy, gracias a `--proxy-headers`) |
| `request_id` | El mismo `X-Request-ID` de los logs: une el evento con el rastro completo de la petición |
| `details` | Datos específicos: rol anterior y nuevo, kilos, precio, motivo del fallo… |

## Lo que nunca entra al registro

- **Contraseñas, tokens, hashes y cabeceras de autorización**: `audit.sanitize` enmascara cualquier clave de `details` que contenga `password`, `token`, `secret`, `hash`, `authorization`, `cookie` o `api_key`, aunque alguien las incluya por error.
- **El correo o la contraseña con los que se intentó entrar** en un login fallido: es texto controlado por quien ataca y, en un error de tipeo, datos de otra persona. Se guarda el *motivo* (`unknown_account`, `bad_password`, `locked`, `inactive`, `pending_verification`, `no_password`), no el dato.
- **Valores personales**: `user.updated` guarda los *nombres* de los campos que cambiaron, no sus valores; el motivo de rechazo de un reciclador es texto libre y solo se guarda si existió (`has_reason`).
- `details` se limita a 200 caracteres por texto y 4 KB en total.

## Garantías

1. **Atómico con la acción.** El evento se escribe en la misma transacción que la operación: si esta falla y se deshace, no queda un evento fantasma; si tiene éxito, no puede faltar. Un intento rechazado (validar un pesaje ya validado, una venta sin stock) no genera evento de éxito.
2. **Los fallos que importan sobreviven al error.** Un login fallido se confirma explícitamente antes de responder 401; se probó con conexiones reales.
3. **Solo se anexa.** Un trigger de PostgreSQL rechaza `UPDATE` y `DELETE` (también los masivos y los del ORM). `actor_id` y `target_id` no son claves foráneas a propósito: el historial debe sobrevivir a los registros que describe y nunca bloquearlos.
4. **Límite conocido:** el dueño de la tabla todavía puede hacer `TRUNCATE` o `DROP`. En producción la aplicación debe conectarse con un rol sin esos permisos (ver `docs/despliegue.md`).

## Consulta: `GET /audit-log`

Solo `association_admin`. Filtros: `action`, `outcome`, `actor_id`, `target_type`, `target_id`, `request_id`, `since`, `until` (ISO 8601); `limit` (1–200, 50 por defecto) y `offset`. Orden: más reciente primero. No hay endpoints para escribir, modificar ni borrar.

Ejemplos:

```bash
# ¿Quién validó o pagó este pesaje?
GET /audit-log?target_type=weighing&target_id=<id>

# Todo lo que hizo un usuario la última semana
GET /audit-log?actor_id=<id>&since=2026-09-22T00:00:00Z

# Intentos de entrada fallidos contra una cuenta
GET /audit-log?action=auth.login_failed&target_id=<id>

# Posible robo de sesiones
GET /audit-log?action=auth.refresh_reuse_detected

# Reconstruir una petición concreta que reportó un usuario
GET /audit-log?request_id=<el X-Request-ID de la respuesta>
```

## Cómo agregar un evento nuevo

1. Añadir la constante a `app/domains/audit/actions.py`.
2. Llamar a `audit.record(db, Action.X, actor=..., target_type=..., target_id=..., details={...})` **antes** del `db.commit()` de la operación.
3. Si el evento debe persistir aunque la petición falle, hacer `db.commit()` justo después y antes de lanzar la excepción.
4. Escribir los tests en `tests/test_audit.py`: el evento existe, no contiene datos personales, y una operación fallida no lo genera.
5. Agregar la acción a `docs.py` del dominio `audit` y a la tabla de este documento.

## Pendientes de decisión

- **Retención.** Hoy no se borra nada (la tabla es de solo anexar). Definir cuánto tiempo se conserva y cómo se archiva es una decisión legal y de negocio: el registro contiene IP y actividad de personas, y la Ley 1581 de 2012 (Habeas Data) exige una finalidad y un plazo. Una vez definida, el borrado periódico debe hacerlo un rol de mantenimiento, no la aplicación.
- **Alertas.** Conviene avisar ante `auth.refresh_reuse_detected` y ante ráfagas de `auth.login_failed`; hoy se pueden vigilar con las métricas (`security_events_total`, `auth_logins_total`) y consultando este endpoint.
- **Alcance del `eca_admin`:** hoy no puede consultar el registro; se puede abrir con un filtro por organización si se necesita.
