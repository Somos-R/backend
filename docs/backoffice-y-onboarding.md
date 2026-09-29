# Backoffice de Somos R e incorporación de organizaciones

Diseño propuesto (2026-09-29). Parte de dos documentos de producto: *Flujo Detallado: Asociación y ECA* (Sebas, 20 sep 2026) y el *Documento Técnico de Plataforma* (26 sep 2026). **Es una propuesta para acordar, no código**: las decisiones abiertas están al final.

## 1. Qué se necesita y por qué

Hoy Somos R no existe como actor en el sistema: no hay forma de aprobar una organización ni de gestionar usuarios sin llamar a endpoints a mano. Los documentos de producto piden:

- Un **formulario público autogestionable** donde una Asociación o una ECA se registra y sube su documentación, sin que Somos R les cree la cuenta.
- Que **Somos R revise la documentación y apruebe o rechace**, con el detalle específico de lo que falta.
- Que al aprobar, el administrador de la organización reciba un **enlace de activación de un solo uso** y defina su propia contraseña.
- Un **backoffice** para gestionar usuarios, roles, tipos y catálogos sin depender de la API en crudo.

## 2. Decisiones ya tomadas

| Tema | Decisión |
|---|---|
| Quién aprueba | **Una sola persona basta.** Somos R son dos personas, cada una con su usuario y el mismo acceso |
| Aprobación | Una organización se aprueba **una vez**. La revalidación periódica queda por definir a nivel de negocio |
| ECA y Asociación | Se registran **de forma independiente**. La ECA no pertenece a una Asociación: la relación es de varios a varios y la inicia siempre la ECA |
| Lista de documentos | **En construcción**. El diseño no la fija: es un catálogo editable |
| Dónde vive | Backend: el mismo proyecto. Frontend: una aplicación aparte (ver 6) |

## 3. Somos R como actor: tipo `platform`

Un tipo de usuario nuevo, `platform`, separado de los clientes, con un único rol por ahora: **`platform_admin`** (las dos personas de Somos R).

- **Sin autoregistro.** El primer `platform_admin` lo crea un script de operación (esto resuelve el problema del primer administrador que teníamos pendiente); los siguientes los crea otro `platform_admin`.
- **Permisos por capacidad, no por rol.** Cada endpoint pide una capacidad (`organizations.review`, `users.manage`, `catalogs.manage`, `audit.read`) y `platform_admin` las tiene todas. Si mañana hay que separar a quien aprueba de quien gestiona usuarios, se reparten las capacidades entre roles nuevos **sin tocar los endpoints**.
- **Verificación en dos pasos (TOTP) obligatoria**: son las cuentas de más riesgo del sistema. Sesiones más cortas que las de clientes.
- **Audiencia de token propia** (`aud=backoffice`): un token de cliente no sirve en `/admin` ni al revés.
- **Todo queda auditado**, incluida la *lectura* de documentos sensibles (`document.viewed`).

## 4. Modelo de datos

Hoy cada persona del staff es un `User` con los datos de su organización repetidos (`association_nit`, etc.). No existe la organización como entidad, y sin ella no se puede aprobar, guardar documentos, ni aislar datos entre organizaciones. Es la pieza que falta. Tu documento técnico ya la nombra (`Organization`, dominio `users`).

| Entidad | Contenido |
|---|---|
| `organizations` | `type` (`association` / `eca`), razón social, NIT, representante legal, contacto, ubicación, **`status`**: `draft` → `submitted` → `in_review` → `changes_requested` / `approved` / `rejected`; `suspended` reservado para después |
| `organization_document_types` | **Catálogo** (`code`, `label`, `organization_type`, `is_required`, `is_active`). Aquí se carga la lista de documentos cuando esté lista, sin cambiar código. Ya se sabe que la ECA necesita RUT y certificado de habilitación ante la SSPD (Decreto 596/2016) y que la Asociación necesita NIT, representante legal y personería jurídica |
| `organization_documents` | Archivo (clave en almacenamiento privado, nombre, tipo, tamaño, hash) y su estado de revisión: `pending`, `ok`, `missing`, `not_compliant`, con comentario |
| `organization_reviews` | Solo anexar: quién revisó, decisión, resumen y el detalle por documento. Es lo que se le envía al solicitante ("qué falta o no cumple") |
| `users.organization_id` | Toda persona de una ECA o Asociación pertenece a una organización. Reemplaza al `association_id` suelto |
| `eca_association_links` | Relación varios-a-varios con estado (`requested`, `active`, `rejected`, `removed`): la ECA solicita, la Asociación acepta o rechaza, cualquiera puede retirarla. Una tabla, no una llave foránea |

**Aislamiento por organización.** Hoy la autorización es por tipo: un `association_admin` puede gestionar a *todos* los usuarios de asociación y a *todos* los recicladores. Con `organization_id` pasa a ser "los de su organización". Tu documento pide expresamente "no atarse a un modelo de un solo tenant", así que conviene hacerlo antes de construir más funcionalidad encima.

## 5. Flujos

### 5.1 Solicitud de incorporación (público)

1. La landing pública ofrece "Regístrate" → elegir Asociación o ECA.
2. El solicitante llena el formulario (con validación de campos) y sube sus documentos. **Aún no tiene cuenta**: se identifica con un **enlace mágico** enviado a su correo, que le permite volver a editar y subir archivos mientras la solicitud no esté aprobada. Es lo que hace posible "corregir y reenviar".
3. Acepta el tratamiento de datos (se guarda la fecha y la versión del texto aceptado).
4. Envía la solicitud (`submitted`).

### 5.2 Revisión (backoffice)

1. Cola de solicitudes por estado, más antiguas primero.
2. El revisor abre cada documento (URL firmada de pocos minutos; queda auditado) y marca cada uno como `ok`, `missing` o `not_compliant` con comentario.
3. Decide:
   - **Aprobar** → se crea el primer administrador de la organización con su rol y se le envía el **enlace de activación de un solo uso** (es el mismo mecanismo que ya usan los recicladores). Define su contraseña en el primer ingreso.
   - **Pedir correcciones** → correo con el detalle específico por documento; el solicitante corrige y reenvía por su enlace.
   - **Rechazar** (definitivo) → correo con el motivo.

### 5.3 Personal de la organización (cambio respecto a hoy)

Los documentos piden que el administrador cree usuarios y que cada uno **defina su propia contraseña por un enlace**, sin contraseñas en texto plano. **Hoy es al revés**: `POST /auth/register` exige que quien crea la cuenta escriba la contraseña. Se propone un endpoint de **invitación** (`email`, `nombre`, `role_code`) que crea la cuenta sin contraseña y envía el enlace de activación.

### 5.4 Vinculación ECA ↔ Asociación

La ECA busca a la Asociación en el directorio y le envía una solicitud; el Administrativo de la Asociación la acepta o rechaza (con motivo); cualquiera de las dos puede retirar el vínculo. La Asociación nunca inicia. Las ECAs vinculadas son los destinos posibles de sus rutas.

## 6. Backoffice

**Backend, el mismo proyecto.** Un módulo con rutas bajo `/admin/...`, su propia audiencia de token y límites más estrictos. Misma base de datos y misma lógica de negocio: un servicio aparte duplicaría reglas y crearía dos fuentes de verdad, y el documento técnico ya define un monolito modular.

**Frontend, una aplicación aparte** (`somos-r-backoffice`), no una sección del portal de ECA y Asociación:

- Otra audiencia y mucho más riesgo, en otro dominio (`admin.…`), que se puede restringir por IP o con un acceso condicional.
- Un bundle compartido llevaría el código y las rutas de administración al navegador de todos los clientes; ocultar un menú por rol no es seguridad.
- Puede compartir la librería de componentes con el portal (monorepo).

**Alcance de la primera versión:**

| Módulo | Funciones |
|---|---|
| Solicitudes | Cola, revisión de documentos, aprobar / pedir correcciones / rechazar |
| Usuarios | Buscar, ver, activar o desactivar, desbloquear, cerrar sesiones, reenviar activación, asignar roles existentes |
| Organizaciones | Ver, con sus usuarios y vínculos ECA↔Asociación |
| Catálogos | Materiales, bodegas, tipos de documento, motivos de rechazo |
| Auditoría | Visor del registro (`audit_log`) |

**Fuera de alcance, a propósito:**

- **Editar permisos de un rol desde pantalla.** Exige RBAC dinámico, difícil de probar y con riesgo de escalada. La matriz vive en código, versionada y con tests; el backoffice *asigna* roles, no los redefine.
- **Cambiar el tipo de un usuario.** Se crea otra cuenta.
- **Actuar como otro usuario.** Si hace falta más adelante: solo lectura, con motivo y auditado.
- **Herramientas que escriban directo en la base** (Supabase Studio, SQL en Retool): se saltan las reglas y la auditoría. Para análisis, solo lectura.

## 7. Seguridad de la parte pública

El formulario y la subida de archivos son la superficie más expuesta del sistema: cualquiera en internet puede llegar.

- Archivos: lista de tipos permitidos verificada por su **contenido** (no por la extensión), tamaño y cantidad máximos, nombre aleatorio, guardado en almacenamiento **privado** (nunca servido directamente) y análisis antivirus.
- Anti-abuso: límite de peticiones, verificación de correo antes de la revisión y un captcha (por ejemplo Cloudflare Turnstile).
- Los documentos contienen cédulas y RUT: aplica la Ley 1581 de 2012 (Habeas Data). Hace falta una **política de retención** y un registro de quién abrió cada documento.

## 8. Brechas entre los documentos de producto y lo construido

Al contrastar ambos documentos con el código aparecen diferencias. Algunas son del backoffice; otras son de dominios que aún no se han tocado.

| # | Qué dicen los documentos | Qué hay hoy | Impacto |
|---|---|---|---|
| 1 | Existe el **Administrador de Somos R** | No existe el tipo ni el rol | Núcleo de este diseño |
| 2 | ECA y Asociación se registran por formulario y los aprueba Somos R | `POST /auth/register` deja registrar `eca` y `association` **de forma anónima**, sin revisión (quedan sin rol, así que sin permisos) | Hay que cerrar ese registro cuando exista la solicitud |
| 3 | El personal crea su propia contraseña por enlace | El admin escribe la contraseña de cada persona | Endpoint de invitación (5.3) |
| 4 | Vinculación ECA↔Asociación de varios a varios, iniciada por la ECA | Un solo campo `association_id` en `users` | Tabla de vínculos |
| 5 | El reciclador **elige su asociación** al registrarse y esa asociación lo verifica | El registro de reciclador no pide asociación | Directorio de asociaciones + campo |
| 6 | Un pesaje solo procede con **reciclador verificado** | `POST /weighings` solo comprueba que sea de tipo reciclador, no que esté verificado | Corrección pequeña y urgente |
| 7 | **Pesaje inmutable** con hash SHA-256 encadenado, con **renglones por material**, correcciones como registros nuevos (`corrects_weighing_id`) y estados propios | Un pesaje es de **un** material, se le cambia el estado, sin hash ni correcciones, ligado a `pagado` (pago) que los documentos solo mencionan | El dominio de pesajes es un rediseño, no un ajuste |
| 8 | `InventoryMovement` (movimientos de entrada y salida) | Saldo por material y bodega, más `transactions` | El libro de movimientos (tarea 5.7) coincide con lo que piden |
| 9 | Ciudadano entra con **OTP** por correo o teléfono | Solo correo y contraseña; sin OTP | Falta el proveedor de SMS y el flujo |
| 10 | Eventos entre dominios por Redis Pub/Sub | Llamadas directas entre servicios | Ya previsto (tarea 5.2); se puede reutilizar el mismo Redis para el rate limit |
| 11 | Quién verifica recicladores: el documento técnico dice **Operativo**; el flujo detallado dice **Administrativo** | El sistema deja a **ambos** | **Los dos documentos se contradicen**: decidir uno |
| 12 | Endpoint `PATCH /users/{id}/verify` | Es `PATCH /users/{id}/verification-status` | Solo nombre; actualizar el documento |
| 13 | Rol "Operador de báscula" | El código del rol es `eca_operator` y su etiqueta en la base dice "Operador ECA" | Corregir la etiqueta |
| 14 | Nombres en inglés, `pending_validation`… | `estado`, `pendiente`, `precio_kg`, `fecha` | Tarea 5.4, que cambia el contrato de la API |
| 15 | FastAPI "async-first", tipado estricto | Endpoints síncronos, mypy no estricto | Decisión consciente y suficiente para el piloto |
| 16 | Roles "embebidos en el token" | El token lleva el rol, pero el servidor **valida contra la base de datos**, no contra el token | Mejor que lo descrito; el documento puede reflejarlo |

**Lo construido y que los documentos no mencionan:** tokens de refresco rotativos, bloqueo de cuentas, registro de auditoría, métricas y logs estructurados, y el CI de seguridad. Conviene añadirlos al documento técnico.

## 9. Fases

| Fase | Contenido | Depende de |
|---|---|---|
| A | Tipo `platform` y `platform_admin`, script del primer admin, TOTP, namespace `/admin` con audiencia propia, visor de auditoría para `platform` | — |
| B | Entidad `Organization`, `users.organization_id`, aislamiento por organización | A |
| C | Solicitud pública, catálogo de documentos, almacenamiento privado, flujo de revisión, correos | B, tarea 5.6 |
| D | Invitación de personal, cierre del registro anónimo de ECA y Asociación, vínculos ECA↔Asociación, directorio | B |
| E | Gestión de usuarios y catálogos desde `/admin` | A, B |
| F | Aplicación `somos-r-backoffice` | Empieza en paralelo con C y E |

La corrección de la brecha 6 (reciclador verificado al pesar) no depende de nada de esto y se puede hacer ya.

## 10. Decisiones pendientes

1. **Lista de documentos por tipo de organización** (en construcción): se carga en el catálogo cuando esté.
2. **Quién verifica recicladores**: Administrativo u Operativo (brecha 11).
3. **Recicladores sin correo**: el flujo entrega el acceso "por correo", pero muchos recicladores de oficio no tienen. ¿Se acepta un canal alternativo (SMS o WhatsApp) desde el piloto?
4. **Plazo de revisión** de una solicitud (SLA) y cuántas veces puede corregir y reenviar el solicitante.
5. **Proveedor de SMS** para el OTP del ciudadano.
6. **Política de retención** de documentos y del registro de auditoría (Habeas Data).
7. **Revalidación** de organizaciones aprobadas: por ahora no existe; el estado `suspended` queda reservado.
8. **Una persona en dos organizaciones**: se propone no soportarlo al inicio (un usuario, una organización).
