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
| `GET /catalogs/*` | ✅ público | | | | | | | |
| `GET /users` | ✅ todos | ✅ recicladores | ✅ recicladores | ✅ eca + recicladores | ✅ recicladores | ✅ recicladores | — | — |
| `GET /users/{id}` | ✅ todos | ✅ recicladores | ✅ recicladores | ✅ eca + recicladores | ✅ recicladores | ✅ recicladores | 👤 | 👤 |
| `PATCH /users/{id}` (datos personales) | ✅ assoc + recicladores | — | — | ✅ eca | — | — | 👤 | 👤 |
| `PATCH /users/{id}` (`role_code`, `permissions`, `association_id`, `employee_code`) | ✅ otro usuario | — | — | ✅ otro usuario | — | — | — | — |
| `PATCH /users/{id}/verification-status` | ✅ | ✅ | — | — | — | — | — | — |
| `GET /weighings`, `/weighings/{id}` | ✅ | ✅ | — | ✅ | ✅ | ✅ | 👤 | — |
| `GET /weighings/stats` | ✅ | ✅ | — | ✅ | ✅ | ✅ | — | — |
| `POST /weighings` | — | — | — | ✅ | ✅ | — | — | — |
| `PATCH /weighings/{id}/status` → `validado` / `rechazado` | ✅ | ✅ | — | ✅ | ✅ | — | — | — |
| `PATCH /weighings/{id}/status` → `pagado` | ✅ | — | — | ✅ | — | — | — | — |
| `GET /inventory` (+ `/stats`, `/materials`, `/warehouses`, `/{id}`) | ✅ | ✅ | — | ✅ | ✅ | ✅ | — | — |
| `PATCH /inventory/{id}` | — | — | — | ✅ | — | ✅ | — | — |
| `GET /transactions` (+ `/stats`, `/{id}`) | ✅ | — | — | ✅ | ✅ | ✅ | — | — |
| `POST /transactions` (venta) | — | — | — | ✅ | — | ✅ | — | — |
| `PATCH /transactions/{id}/status` → `cancelado` / `entregado` | — | — | — | ✅ | — | ✅ | — | — |
| `PATCH /transactions/{id}/status` → `pagado` | ✅ | — | — | ✅ | — | — | — | — |

Notas de comportamiento:

- Un reciclador que consulta el pesaje de otro recibe **404** (no 403), para no revelar que existe. Al listar solo ve los suyos; pedir `recycler_id` ajeno da 403.
- `GET /users/{id}` sobre alguien fuera del alcance del rol da 403.

## Supuestos a confirmar

El documento técnico define los roles pero no los permisos por endpoint; estos puntos son **decisiones tomadas por criterio** y conviene validarlas con el equipo:

1. **Validar pesajes: ¿ECA, Asociación o ambos?** El documento lista "validar pesajes" en el portal de la Asociación y "registrar pesajes" en el de la ECA. Se permite validar/rechazar a ambos (báscula y admin de ECA; admin y operativo de asociación).
2. **Pagar** (pesajes y compras) se reserva a los administradores de cada organización, por mover dinero.
3. **El Encargado de rutas** no tiene acceso a pesajes, inventario ni transacciones; hoy solo puede consultar recicladores. Ganará permisos cuando exista el dominio de logística.
4. **Alcance de `GET /users`:** el `eca_admin` no ve ciudadanos ni empresas (no los gestiona); solo el `association_admin` ve todos los tipos.
5. **Las empresas B2B** no acceden a ningún endpoint operativo; consumirán el dominio de reportes cuando exista.
6. **Pregunta abierta del documento:** si el Operativo de ECA y el de Asociación comparten cuenta cuando una misma persona cumple ambos roles. Hoy un usuario tiene un solo tipo y un solo rol, así que serían cuentas separadas.
7. **Registro de personal:** un usuario `eca`/`association` puede registrarse sin rol (queda sin permisos) y un administrador se lo asigna después. El primer administrador de cada organización debe crearse fuera de la API (seed o script de operación): pendiente de definir.
