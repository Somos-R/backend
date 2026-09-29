# Matriz de permisos

Fuente de verdad de **quién puede hacer qué**. Se implementa en `app/core/permissions.py` y se verifica de forma independiente en `tests/test_authorization.py` (la matriz de ese archivo debe coincidir con esta).

Origen: *Documento Técnico de Plataforma* (roles de Asociación y ECA) y los endpoints que existen hoy. Los dominios de logística (solicitudes, rutas) y reportes aún no existen en el backend; sus permisos se definirán cuando se construyan.

## Actores y roles

| Actor (`user_type_code`) | Rol (`role_code`) | Nombre en el documento técnico |
|---|---|---|
| `association` | `association_admin` | Asociación · Administrativo |
| `association` | `association_operator` | Asociación · Operativo |
| `association` | `route_manager` | Asociación · Encargado de rutas |
| `eca` | `eca_admin` | ECA · Administrativo |
| `eca` | `eca_operator` | ECA · Operador de báscula |
| `eca` | `eca_warehouse` | ECA · Encargado de bodega |
| `platform` | `platform_admin` | Administrador de Somos R (ver «Somos R» más abajo) |
| `recycler` | — | Reciclador |
| `citizen`, `building` | — | Ciudadano / Conjunto-JAC |
| `b2b_client` | — | Empresa obligada REP |

Reglas generales:

- Un rol solo es válido para su tipo de actor (`eca_*` ↔ `eca`; `association_*` y `route_manager` ↔ `association`). Un usuario con un rol de otro tipo **no obtiene ningún permiso**.
- Un `eca` o `association` sin rol no tiene permisos hasta que un administrador se lo asigne.
- Los roles los asigna un **administrador de la misma organización**; nadie se asigna roles a sí mismo (ni un administrador).
- Sin token → 401. Con token pero sin permiso → 403.

## Endpoints

✅ permitido · — denegado (403) · 👤 solo sobre el propio recurso

| Endpoint | assoc admin | assoc operativo | route mgr | eca admin | eca báscula | eca bodega | reciclador | ciudadano / conjunto / B2B |
|---|:-:|:-:|:-:|:-:|:-:|:-:|:-:|:-:|
| `POST /auth/register` (sin rol) | ✅ público | | | | | | | |
| `POST /auth/register` (con `role_code`) | ✅ su org. | — | — | ✅ su org. | — | — | — | — |
| `POST /auth/login`, `/logout` | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ |
| `GET /auth/me` | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ |
| `GET /catalogs/*` | ✅ público | | | | | | | |
| `GET /users` | ✅ todos | ✅ recicladores | ✅ recicladores | ✅ eca + recicladores | ✅ recicladores | ✅ recicladores | — | — |
| `GET /users/{id}` | ✅ todos | ✅ recicladores | ✅ recicladores | ✅ eca + recicladores | ✅ recicladores | ✅ recicladores | 👤 | 👤 |
| `PATCH /users/{id}` (datos personales) | ✅ assoc + recicladores | — | — | ✅ eca | — | — | 👤 | 👤 |
| `PATCH /users/{id}` (`role_code`, `permissions`, `association_id`, `employee_code`) | ✅ otro usuario | — | — | ✅ otro usuario | — | — | — | — |
| `PATCH /users/{id}/verification-status` | ✅ | ✅ | — | — | — | — | — | — |
| `GET /weighings`, `/weighings/{id}` | ✅ | ✅ | — | ✅ | ✅ | ✅ | 👤 | — |
| `GET /weighings/stats` | ✅ | ✅ | — | ✅ | ✅ | ✅ | — | — |
| `POST /weighings` | — | — | — | ✅ | ✅ | — | — | — |
| `PATCH /weighings/{id}/status` → `validated` / `rejected` | ✅ | ✅ | — | ✅ | ✅ | — | — | — |
| `PATCH /weighings/{id}/status` → `paid` | ✅ | — | — | ✅ | — | — | — | — |
| `GET /inventory` (+ `/stats`, `/materials`, `/warehouses`, `/{id}`) | ✅ | ✅ | — | ✅ | ✅ | ✅ | — | — |
| `PATCH /inventory/{id}` | — | — | — | ✅ | — | ✅ | — | — |
| `GET /transactions` (+ `/stats`, `/{id}`) | ✅ | — | — | ✅ | ✅ | ✅ | — | — |
| `POST /transactions` (venta) | — | — | — | ✅ | — | ✅ | — | — |
| `PATCH /transactions/{id}/status` → `cancelled` / `delivered` | — | — | — | ✅ | — | ✅ | — | — |
| `PATCH /transactions/{id}/status` → `paid` | ✅ | — | — | ✅ | — | — | — | — |
| `GET /audit-log` | ✅ | — | — | — | — | — | — | — |

## Alcance por organización

Los permisos de arriba dicen *qué* puede hacer cada rol; el alcance dice *sobre quién*. El personal de una ECA o Asociación pertenece a **una** organización (`users.organization_id`) y solo se alcanza desde ella:

- **`GET /users`, `GET /users/{id}`, `PATCH /users/{id}`, asignación de roles:** un administrador ve y edita únicamente al personal de **su** organización. El personal de otra organización responde como un usuario inexistente (**404 `user_not_found`**, idéntico a un id que no existe) y en los listados y en la búsqueda `q` no aparece. Una asociación tampoco ve al personal de ninguna ECA.
- **`GET /audit-log`:** el administrador de asociación lee solo lo que hizo el personal de su organización y lo intentado contra sus cuentas (un login fallido no tiene actor, pero sí la cuenta a la que apuntó).
- **Sin organización, sin alcance (falla cerrado):** una cuenta de personal sin organización no alcanza a nadie del personal (ni siquiera a otras cuentas sin organización), no puede crear personal (403 `no_organization`) y no lee la auditoría. Nadie más puede ver a esas cuentas.
- **Lo que hace cada persona sobre sí misma no cambia.**

**Todavía NO aislado** (pendiente, tarea 6.17):
- **Recicladores** (y ciudadanos, conjuntos, empresas B2B): no pertenecen a una organización, así que todo el personal con permiso los sigue alcanzando. Cambiará cuando el reciclador elija su asociación (6.16).
- **Datos operativos** (bodegas, pesajes, inventario, transacciones): no tienen dueño; hoy todo el personal con el rol adecuado los ve, de cualquier organización. Con una sola ECA y una sola asociación no hay fuga; antes de operar con varias hay que darles dueño y hacer que la Asociación los lea por sus vínculos con las ECA (6.12).

Notas de comportamiento:

- Un reciclador que consulta el pesaje de otro recibe **404** (no 403), para no revelar que existe. Al listar solo ve los suyos; pedir `recycler_id` ajeno da 403.
- `GET /users/{id}` sobre alguien fuera del alcance del rol da 403.

## Somos R (`platform`)

Somos R es un actor propio, separado de los clientes: tipo `platform`, rol `platform_admin` (las personas de Somos R, mismo acceso).

- **Sin autoregistro.** `POST /auth/register` no admite el tipo `platform`, un administrador de ECA o asociación no puede asignar el rol (422) y `GET /catalogs/roles` no lo lista. La primera cuenta la crea un operador con `scripts/create_platform_admin.py`; las siguientes, otro `platform_admin` desde el backoffice.
- **No entra por el login público.** `POST /auth/login` responde igual que ante una contraseña errónea, aunque las credenciales sean correctas (queda auditado con el motivo `platform_account`); las cuentas de Somos R entran por el backoffice (ver «Backoffice»), con segundo factor y audiencia de token propia. Tampoco se cuentan sus intentos fallidos (nadie puede bloquear a un administrador adivinando) ni se le ofrece recuperar la contraseña por un enlace público.
- **Cero acceso a la API de los clientes:** todos los endpoints de esta matriz responden 403 a un `platform_admin`.
- **Permisos por capacidad, no por rol** (`PLATFORM_CAPABILITIES` en `app/core/permissions.py`, dependencia `require_capability`): `organizations.review`, `users.manage`, `catalogs.manage`, `audit.read`. Hoy las tiene todas `platform_admin`; si mañana se separan funciones, se reparten capacidades entre roles nuevos sin tocar los endpoints. Todavía no se anuncian en `GET /auth/me`: una capacidad solo se anuncia cuando un endpoint la exige.

## Backoffice (`/admin`)

Las cuentas de Somos R entran por su propio conjunto de rutas, separado de la API de los clientes.

- **Dos pasos, segundo factor obligatorio.** `POST /admin/auth/login` (contraseña) responde con un token de desafío de 5 minutos, sin acceso a nada. La sesión solo se abre con `POST /admin/auth/mfa/verify` (código TOTP de la app autenticadora o un código de recuperación). En el primer ingreso la cuenta **debe** configurar el segundo factor (`/admin/auth/mfa/enroll` y `/enroll/confirm`), que entrega 10 códigos de recuperación de un solo uso.
- **Audiencia de token propia.** Los tokens llevan `aud`: `portal` (clientes) o `backoffice` (Somos R). Cada API acepta solo la suya, en ambos sentidos; los tokens de refresco también se renuevan solo en su audiencia.
- **Sesiones más cortas:** acceso de 10 minutos y refresco de 12 horas (clientes: 30 minutos y 30 días).
- **Límites estrictos** (5/min por IP en login y segundo factor) y **bloqueo por intentos fallidos**, contando también los fallos del segundo factor.
- **Restricción de red opcional:** `ADMIN_ALLOWED_CIDRS` limita todo `/admin` a esas direcciones.
- **Recuperación:** códigos de recuperación (regenerables con un código vigente), restablecimiento por la otra persona de Somos R (`POST /admin/users/{id}/mfa/reset`, capacidad `users.manage`; nadie el propio) o, como último recurso, `scripts/reset_platform_mfa.py`. Restablecer termina las sesiones de la cuenta.
- **Todo queda auditado:** `admin.login`, `admin.login_failed`, `admin.logout`, `admin.mfa_enrolled`, `admin.mfa_failed`, `admin.recovery_code_used`, `admin.recovery_codes_regenerated`, `admin.mfa_reset`.
- `GET /admin/me` devuelve el perfil, las capacidades del rol y el estado del segundo factor.
- **`GET /admin/audit-log`** (capacidad `audit.read`): el registro de auditoría **completo**, de todas las organizaciones y de las cuentas de Somos R, con los filtros de `GET /audit-log` más `actor_role` y `organization_id`. Cada consulta queda auditada (`admin.audit_viewed`, con los nombres de los filtros usados y no sus valores).

## Capacidades (`GET /auth/me`)

`GET /auth/me` devuelve el perfil y `capabilities`, la lista de lo que el usuario puede hacer, calculada con el mismo módulo de permisos que protege los endpoints (`CAPABILITIES` en `app/core/permissions.py`). Una capacidad existe solo si un endpoint la exige. Es una ayuda para la interfaz, no una barrera: los endpoints siguen respondiendo 403.

| Capacidad | Roles |
|---|---|
| `recyclers.view` | cualquier rol de personal (ECA o asociación) |
| `recyclers.verify` | assoc admin, assoc operativo |
| `weighings.view` | assoc admin, assoc operativo, eca admin, eca báscula, eca bodega |
| `weighings.create` | eca admin, eca báscula |
| `weighings.review` | assoc admin, assoc operativo, eca admin, eca báscula |
| `weighings.pay`, `transactions.pay` | assoc admin, eca admin |
| `inventory.view` | assoc admin, assoc operativo, eca admin, eca báscula, eca bodega |
| `inventory.edit` | eca admin, eca bodega |
| `transactions.view` | assoc admin, eca admin, eca báscula, eca bodega |
| `transactions.create`, `transactions.manage` | eca admin, eca bodega |
| `audit.view` | assoc admin |

Un usuario sin rol, o con un rol que no corresponde a su tipo, recibe la lista vacía.

## Supuestos a confirmar

0. **Auditoría:** solo `association_admin` puede consultar `GET /audit-log` (es el rol con más alcance de la matriz). El `eca_admin` no ve el registro, ni siquiera el de su propia organización; si hace falta, se puede abrir con un filtro por organización.

El documento técnico define los roles pero no los permisos por endpoint; estos puntos son **decisiones tomadas por criterio** y conviene validarlas con el equipo:

1. **Validar pesajes: ¿ECA, Asociación o ambos?** El documento lista "validar pesajes" en el portal de la Asociación y "registrar pesajes" en el de la ECA. Se permite validar/rechazar a ambos (báscula y admin de ECA; admin y operativo de asociación).
2. **Pagar** (pesajes y compras) se reserva a los administradores de cada organización, por mover dinero.
3. **El Encargado de rutas** no tiene acceso a pesajes, inventario ni transacciones; hoy solo puede consultar recicladores. Ganará permisos cuando exista el dominio de logística.
4. **Alcance de `GET /users`:** el `eca_admin` no ve ciudadanos ni empresas (no los gestiona); solo el `association_admin` ve todos los tipos.
5. **Las empresas B2B** no acceden a ningún endpoint operativo; consumirán el dominio de reportes cuando exista.
6. **Pregunta abierta del documento:** si el Operativo de ECA y el de Asociación comparten cuenta cuando una misma persona cumple ambos roles. Hoy un usuario tiene un solo tipo y un solo rol, así que serían cuentas separadas.
7. **Registro de personal:** un usuario `eca`/`association` puede registrarse sin rol (queda sin permisos) y un administrador se lo asigna después. El primer administrador de cada organización debe crearse fuera de la API (seed o script de operación): pendiente de definir.
