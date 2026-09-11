# Módulo: Usuarios

Gestiona operadores y recicladores. Los recicladores tienen un flujo de verificación que determina si pueden participar en pesajes y transacciones.

---

## Backend — `app/domains/users/`

### Modelo (`models.py`)

| Campo | Tipo | Descripción |
|---|---|---|
| `id` | UUID | PK |
| `full_name` | str | Nombre completo |
| `id_number` | str | Número de cédula o NIT |
| `tax_id` | str | NIT tributario (puede diferir del id_number) |
| `user_type_code` | str | `"recycler"` o `"operator"` |
| `role_code` | str | Rol dentro del sistema |
| `verification_status` | enum | `pending` / `verified` / `rejected` |
| `rejection_reason` | str | Motivo si fue rechazado |
| `verified_at` | datetime | Cuándo fue verificado |
| `verified_by` | UUID | Quién lo verificó |
| `address` | str | Dirección de residencia |
| `phone` | str | Teléfono |
| `email` | str | Correo |
| `password_hash` | str | Hash bcrypt |

### Schemas (`schemas.py`)

`UserDetailResponse` expone los campos:
- `rejection_reason: str | None`
- `verified_at: datetime | None`

Estos campos fueron añadidos explícitamente porque son necesarios para mostrar el historial de verificación en el frontend.

### Endpoints (`router.py`)

#### `GET /users`
Lista usuarios con filtros.

| Query param | Tipo | Descripción |
|---|---|---|
| `user_type_code` | str | `"recycler"` / `"operator"` |
| `role_code` | str | Filtrar por rol |
| `verification_status` | str | `pending` / `verified` / `rejected` |
| `limit` | int (1–**500**) | Default 20 — límite elevado de 100 a 500 |
| `offset` | int | Paginación |

**Cambio importante:** El límite máximo se elevó de `le=100` a `le=500`. Esto fue necesario porque `RegisterWeighingDrawer` necesita cargar todos los recicladores verificados de una vez para el selector del formulario. Con el límite anterior de 100 se generaba un error 422 al pasar `limit=200`.

#### `GET /users/{id}`
Obtiene detalle completo de un usuario.

#### `PATCH /users/{id}/verification-status`
Cambia el estado de verificación de un reciclador.

```json
{ "status": "verified" }
```
o
```json
{ "status": "rejected", "rejection_reason": "Documento ilegible" }
```

**Lógica al verificar (`verified`):**
- Asigna `password_hash = hash(id_number)` — el reciclador puede ingresar usando su número de cédula como contraseña inicial
- Registra `verified_at = now(UTC)` y `verified_by = current_user.id`
- Limpia `rejection_reason = None`

**Lógica al rechazar (`rejected`):**
- Guarda el motivo en `rejection_reason`
- No elimina el usuario, permanece en el sistema

#### `PATCH /users/{id}`
Actualiza campos del usuario (nombre, dirección, teléfono, email, tax_id). Si `tax_id` ya está registrado por otro usuario retorna `409 Conflict`.

---

## Uso en otros módulos

### En Pesajes
El selector de recicladores en `RegisterWeighingDrawer` consulta:
```
GET /users?verification_status=verified
```
Solo recicladores con `verified` pueden ser asociados a un pesaje.

---

## Flujo de verificación

```
Reciclador se registra (POST /auth/register)
    → verification_status = "pending"

Operador revisa en pantalla Recicladores
    → PATCH /users/{id}/verification-status { status: "verified" }
        → reciclador puede aparecer en selector de pesajes
        → contraseña inicial = su número de cédula
    — o —
    → PATCH /users/{id}/verification-status { status: "rejected", rejection_reason: "..." }
        → no puede participar en pesajes
```
