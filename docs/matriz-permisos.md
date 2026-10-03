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
| `POST /users/invitations`, `POST /users/{id}/invitation/resend` | ✅ su org. | — | — | ✅ su org. | — | — | — | — |
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
| `GET /directory/associations`, `POST /links` (solicitar) | — | — | — | ✅ | — | — | — | — |
| `GET /links` (los de su organización), `POST /links/{id}/remove` | ✅ | — | — | ✅ | — | — | — | — |
| `POST /inventory/warehouses` (bodega propia) | — | — | — | ✅ | — | — | — | — |
| `POST /links/{id}/accept`, `/reject` | ✅ | — | — | — | — | — | — | — |

## Alcance por organización

Los permisos de arriba dicen *qué* puede hacer cada rol; el alcance dice *sobre quién*. El personal de una ECA o Asociación pertenece a **una** organización (`users.organization_id`) y solo se alcanza desde ella:

- **`GET /users`, `GET /users/{id}`, `PATCH /users/{id}`, asignación de roles:** un administrador ve y edita únicamente al personal de **su** organización. El personal de otra organización responde como un usuario inexistente (**404 `user_not_found`**, idéntico a un id que no existe) y en los listados y en la búsqueda `q` no aparece. Una asociación tampoco ve al personal de ninguna ECA.
- **`GET /audit-log`:** el administrador de asociación lee solo lo que hizo el personal de su organización y lo intentado contra sus cuentas (un login fallido no tiene actor, pero sí la cuenta a la que apuntó).
- **Recicladores:** un reciclador pertenece a **una asociación** (`organization_id`; la elige al registrarse en el directorio público `GET /catalogs/associations`, o hereda la del personal de asociación que lo registra). El personal de una asociación ve, edita y **verifica** solo a los suyos; los de otra asociación responden 404. Un reciclador sin asociación no lo alcanza ninguna; se le asigna una desde el backoffice (`PUT /admin/users/{id}/organization`). El propio reciclador no cambia.
- **Sin organización, sin alcance (falla cerrado):** una cuenta de personal sin organización no alcanza a nadie del personal (ni siquiera a otras cuentas sin organización), no puede crear personal (403 `no_organization`) y no lee la auditoría. Nadie más puede ver a esas cuentas.
- **Lo que hace cada persona sobre sí misma no cambia.**

**Datos operativos (bodegas, inventario, pesajes, transacciones).** Pertenecen a la **ECA dueña de la bodega** donde ocurren (`warehouses.organization_id`). Las reglas están en un solo lugar (`app/domains/organizations/scope.py`) y filtran listados, totales, estadísticas y consulta individual; lo que queda fuera de alcance responde **404**, idéntico a un id inexistente:

| Quién | Qué alcanza |
|---|---|
| Personal de una **ECA** | Lo de **sus** bodegas: inventario (lee y edita según su rol), pesajes y transacciones. Solo puede **pesar en sus bodegas**, pero **recibe el material de quien lo traiga** (ver «Quién puede vender a una ECA»). |
| Personal de una **asociación** | **Lee** los pesajes y las **compras** de **sus** recicladores **que llegaron por el vínculo** (`affiliation_status = linked`; el historial no se pierde si el vínculo termina) y las bodegas e inventario de las **ECA con vínculo activo**, sin escribir. No ve las ventas de la ECA. Puede validar y pagar lo de sus recicladores, según su rol. |
| Reciclador | Sus propios pesajes, como antes. |
| Sin organización | Nada (falla cerrado). |

- Una **bodega sin dueña** no la ve ningún cliente: se le asigna una ECA desde el backoffice (`PUT /admin/warehouses/{id}/organization`, capacidad `catalogs.manage`, una sola vez y solo a una ECA aprobada). Una ECA crea las suyas con `POST /inventory/warehouses` (solo `eca_admin`).
- En el directorio de usuarios (`GET /users`), el personal de ECA alcanza **solo a los recicladores de asociaciones vinculadas**; para pesar a cualquier otro se usa el buscador por documento (`GET /recyclers/lookup`), que devuelve lo mínimo.

### Quién puede vender a una ECA

Una ECA **debe recibir el material sin importar la afiliación** de quien lo trae (no discriminación, Ley 142 de 1994). Un pesaje (`POST /weighings`) se registra con **uno** de estos dos:

- `recycler_id`: un reciclador registrado, de **cualquier asociación o de ninguna**, verificado o no. Solo se rechaza una cuenta que Somos R haya desactivado (`400 recycler_inactive`).
- `seller` (`full_name`, `id_type`, `id_number`): una persona **no registrada** (un cliente natural, un reciclador fuera de la plataforma). El pesaje queda con `recycler_id` nulo y los datos mínimos de la persona.

Cada pesaje guarda cómo se relaciona el vendedor con **esa** ECA en el momento de pesar (`affiliation_status`):

| Valor | Cuándo |
|---|---|
| `linked` | Reciclador **verificado** de una asociación con vínculo **activo** con la ECA |
| `unlinked_association` | Reciclador con asociación, pero no vinculada a esa ECA (o aún no verificado) |
| `independent` | Reciclador sin asociación, o persona no registrada |

**Solo los `linked` llegan a una asociación** (pesajes, compras, estadísticas y la posibilidad de validarlos o pagarlos). Los demás cuentan para el inventario, las compras y los reportes de la ECA, pero ninguna asociación los ve. `GET /weighings?affiliation=` filtra por este valor y `GET /weighings?q=` busca (sin distinguir mayúsculas ni tildes, mínimo 2 caracteres) por el nombre y el documento del reciclador registrado y por el nombre y el documento de quien no lo está; siempre dentro del alcance de quien pregunta. `GET /recyclers/lookup?document=` identifica a un reciclador registrado y devuelve su `affiliation` respecto a la ECA que pregunta.

Esto sustituye a la regla anterior («solo reciclador verificado y de asociación vinculada»). **Pregunta abierta de negocio:** si el pesaje de un vendedor fuera del vínculo cuenta para el reporte SUI de la ECA, de la asociación de origen o de ambas.

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
- **Gestión de usuarios (`/admin/users`, capacidad `users.manage`):** `GET /admin/users` (todas las organizaciones y tipos; lista resumida con búsqueda `q` y filtros por tipo, rol, organización, activo, bloqueado, activación pendiente y verificación), `GET /admin/users/{id}` (perfil completo y estado de seguridad; **abrirlo queda auditado** como `admin.user_viewed`), `PATCH .../status` (desactivar termina todas las sesiones; nadie desactiva su propia cuenta), `POST .../unlock`, `POST .../sessions/revoke`, `POST .../invitation/resend` (personal invitado o reciclador verificado sin contraseña), `PATCH .../role` (otro rol existente, del tipo de la persona; solo personal de ECA y Asociación) y `PUT .../organization` (solo para personal sin organización; no mueve a nadie entre organizaciones). Todo cambio queda auditado con quién y a quién: `user.activated`, `user.deactivated`, `user.unlocked`, `user.sessions_revoked`, `user.role_changed`, `user.invitation_resent`, `user.organization_assigned`.
- **Organizaciones (`/admin/organizations`, capacidad `organizations.review`), solo lectura:** `GET /admin/organizations` (todas; búsqueda `q` por nombre, NIT, correo o representante sin mayúsculas ni tildes, filtros `type` y `status`; cada fila trae cuántas personas tiene y cuántos vínculos activos) y `GET /admin/organizations/{id}` (perfil con su personal, sus vínculos con la organización del otro lado y, según el tipo, los recicladores de una asociación o las bodegas de una ECA). **Abrir el detalle queda auditado** (`admin.organization_viewed`) porque muestra nombres y correos. Cambiar el estado de una organización (aprobar, pedir correcciones, rechazar) pertenece a la revisión de solicitudes, no a este módulo.
- **Catálogos (`/admin/catalogs`, `PATCH /admin/warehouses/{id}`, capacidad `catalogs.manage`):** materiales (`/materials`) y tipos de documento (`/document-types`) se listan (todos, o `?is_active=`), se crean (el `code` es inmutable: materiales en minúsculas y `_`, tipos en mayúsculas) y se renombran o desactivan con `PATCH`; una bodega se renombra, cambia de dirección o se desactiva. **Nada se borra** (inventario, pesajes y cuentas apuntan a ellos): desactivar impide el **uso nuevo** (`400 material_inactive` en pesajes y ventas, `400 warehouse_inactive`, `422 invalid_id_type` en invitaciones y vendedores nuevos) y lo oculta de las listas públicas, pero lo que ya existe sigue funcionando y legible. Auditado: `catalog.created`, `catalog.updated`, `warehouse.updated`. Los motivos de rechazo llegarán con la revisión de solicitudes (6.9).
- **`GET /admin/audit-log`** (capacidad `audit.read`): el registro de auditoría **completo**, de todas las organizaciones y de las cuentas de Somos R, con los filtros de `GET /audit-log` más `actor_role` y `organization_id`. Cada consulta queda auditada (`admin.audit_viewed`, con los nombres de los filtros usados y no sus valores).

## Solicitud de incorporación (público)

Una Asociación o una ECA pide unirse **sin tener cuenta** (`/applications`, sin autenticación, con límite de peticiones por IP). Quien llena la solicitud se identifica con un **enlace mágico** enviado a su correo: el web lo manda en el encabezado `X-Application-Token`. Del token solo se guarda su SHA-256, un enlace nuevo invalida el anterior y vence a los 30 días (`APPLICATION_LINK_DAYS`); usarlo la primera vez **verifica el correo**.

| Paso | Endpoint | Notas |
|---|---|---|
| Empezar | `POST /applications` | Crea la organización en `draft` y envía el enlace. Exige aceptar el tratamiento de datos (se guarda la fecha y la versión). Responde siempre 202 con el mismo mensaje; si ese correo ya tenía una solicitud abierta del mismo tipo, solo recibe un enlace nuevo |
| Recuperar el enlace | `POST /applications/access-link` | Igual de genérico: no revela quién aplicó |
| Ver / completar | `GET` / `PATCH /applications/current` | Solo en `draft` o `changes_requested`; el tipo y el correo de quien aplica no cambian |
| Enviar | `POST /applications/current/submit` | `draft`/`changes_requested` → `submitted`. Exige los datos obligatorios y que queden envíos (el primero más 2 correcciones, `APPLICATION_MAX_SUBMISSIONS`) |

Un borrador **no bloquea un NIT**: la unicidad de (tipo, NIT) solo cuenta entre organizaciones que operan (`approved`, `suspended`); si el NIT ya es de una que opera, se rechaza al editar y al enviar (`409 organization_already_registered`). Todo queda auditado sin datos personales (`application.created`, `application.link_sent`, `application.updated` con los nombres de los campos, `application.submitted`). La revisión, la aprobación y los documentos son los siguientes pasos.

## Vínculos ECA ↔ Asociación

Relación de varios a varios **entre organizaciones**: una ECA puede vincularse a varias asociaciones y una asociación recibir a varias ECA. **La ECA siempre inicia** (`POST /links` con el id de una asociación aprobada, que sale del directorio) y **el administrador de la asociación decide** (`accept` / `reject`, con motivo opcional). Cualquiera de las dos partes puede retirar un vínculo activo; la ECA también puede cancelar su solicitud sin respuesta. Estados: `requested` → `active` / `rejected` → (`removed`); tras un rechazo o un retiro la ECA puede volver a solicitar (se reabre la misma fila; el historial está en la auditoría: `link.requested`, `link.accepted`, `link.rejected`, `link.removed`).

Solo concierne a sus dos organizaciones: cualquier otra recibe **404 `link_not_found`**. Del otro lado solo se ve el nombre y la ciudad. Exige organización aprobada (403 `organization_not_active`). El vínculo **activo** es lo que permite a una ECA pesar a los recicladores de la asociación y a la asociación leer el inventario de la ECA (ver «Alcance por organización»).

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
| `staff.invite`, `staff.view`, `staff.manage` | assoc admin, eca admin |
| `links.request` | eca admin |
| `links.decide` | assoc admin |
| `links.view` | assoc admin, eca admin |

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
