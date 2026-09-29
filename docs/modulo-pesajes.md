# Módulo: Pesajes

Gestiona el registro de pesajes de material reciclado entregados por recicladores a la ECA. Es el punto de entrada del flujo de compras: un pesaje validado genera automáticamente una transacción de tipo `purchase`.

---

## Backend — `app/domains/weighings/`

### Modelo (`models.py`)

| Campo | Tipo | Descripción |
|---|---|---|
| `id` | UUID | PK |
| `recycler_id` | UUID FK → users | Reciclador que entrega el material |
| `material_code` | str FK → materials | Código del material (papel, plastico, etc.) |
| `warehouse_id` | UUID FK → warehouses | Bodega de recepción |
| `kg` | Decimal | Peso registrado |
| `price_per_kg` | Decimal | Precio pactado por kg |
| `status` | enum | `pending_validation` → `validated` → `paid` / `rejected` |
| `occurred_at` | datetime | Timestamp UTC de registro |
| `validated_by` | UUID FK → users | Operador que validó |
| `rejection_reason` | str | Motivo de rechazo (solo si `rejected`) |

### Endpoints (`router.py`)

#### `GET /weighings`
Lista pesajes con filtros opcionales.

| Query param | Tipo | Descripción |
|---|---|---|
| `recycler_id` | UUID | Filtrar por reciclador |
| `material_code` | str | Filtrar por material |
| `warehouse_id` | UUID | Filtrar por bodega |
| `status` | str | Filtrar por estado |
| `limit` | int (1–100) | Default 20 |
| `offset` | int | Paginación |

Respuesta: `{ total, items[] }` ordenado por `occurred_at DESC`.

#### `GET /weighings/stats`
Estadísticas del mes en curso:
- Total de pesajes y kg del mes
- Conteo de pesajes pendientes
- Desglose de kg por material (`by_material`)

#### `POST /weighings`
Crea un pesaje en estado `pending_validation`.

```json
{
  "recycler_id": "uuid",
  "material_code": "paper",
  "warehouse_id": "uuid",
  "kg": 45.5,
  "price_per_kg": 1200
}
```

Validaciones:
- El `recycler_id` debe existir y tener `user_type_code = "recycler"`
- El `material_code` debe existir en catálogo
- El `warehouse_id` debe existir

#### `GET /weighings/{id}`
Obtiene un pesaje por UUID.

#### `PATCH /weighings/{id}/status`
Cambia el estado del pesaje. Las transiciones válidas son:

```
pending_validation → validated   (crea transacción de compra automáticamente)
pending_validation → rejected  (requiere rejection_reason)
validated  → paid
```

Al validar (`validated`) el service ejecuta `validate_weighing()` que:
1. Cambia `status` a `validated`
2. Registra `validated_by` con el ID del usuario autenticado
3. Crea automáticamente una `Transaction` de tipo `purchase` con los mismos datos de kg, precio y reciclador

Al rechazar (`rejected`), el campo `rejection_reason` es **obligatorio**.

### Service (`service.py`)

- `validate_weighing(db, weighing, user_id)`: Valida el pesaje y crea la transacción de compra.
- `reject_weighing(db, weighing, reason)`: Marca como rechazado con motivo.
- `mark_paid(db, weighing)`: Marca el pesaje como pagado.

---

## Frontend — `src/features/weighings/`

### `Weighings.tsx`

Página principal del módulo. Muestra la lista de pesajes con opciones de validar, rechazar y ver detalle. Incluye el botón **"Nuevo pesaje"** que abre el drawer de registro.

**Estado manejado:**
- `drawerOpen: boolean` — controla la apertura de `RegisterWeighingDrawer`
- `rejectDialog` — estado del diálogo de rechazo (pesajeId + motivo)

**Flujo de validación:**
```
Click "Validar" → PATCH /weighings/{id}/status { status: "validated" }
→ invalidateQueries(['weighings']) + invalidateQueries(['transactions'])
```

**Flujo de rechazo:**
```
Click "Rechazar" → abre diálogo → ingresa motivo
→ PATCH /weighings/{id}/status { status: "rejected", rejection_reason }
```

### `RegisterWeighingDrawer.tsx`

Drawer lateral (440px) para registrar nuevos pesajes.

**Datos que carga al abrir (`enabled: open`):**
| Query | Endpoint | Para qué |
|---|---|---|
| `['recyclers', 'verified']` | `GET /users?verification_status=verified` | Selector de recicladores |
| `['inventory', 'materials']` | `GET /inventory/materials` | Selector de materiales |
| `['inventory', 'warehouses']` | `GET /inventory/warehouses` | Selector de bodegas |
| `['inventory']` | `GET /inventory` | Auto-sugerir precio/kg |

**Comportamiento de auto-precio:**
Cuando el usuario selecciona material + bodega, busca en `inventoryItems` la entrada que coincida con ambos y pre-completa el campo `price_per_kg` con el precio registrado en inventario.

**Corrección aplicada:** El query de recicladores no envía `limit` explícito. Antes enviaba `limit: 200`, lo que generaba un error `422` porque el backend tenía un tope de `le=100`. El límite del backend para `/users` se elevó a `le=500` para soportar carga real.

**Validaciones de formulario:**
- Todos los campos son obligatorios
- `kg > 0` y `price_per_kg > 0`
- Muestra errores inline por campo

**Resumen visual:** Mientras el usuario completa `kg` y `price_per_kg`, calcula y muestra el total a pagar al reciclador en tiempo real.

### `WeighingsTable.tsx`

Tabla reutilizable que renderiza la lista de pesajes con badges de estado y acciones contextuales.

---

## Flujo completo

```
Operador abre drawer
    → Selecciona reciclador verificado, material, bodega, kg, precio
    → POST /weighings (status: pending_validation)

Supervisor revisa lista
    → PATCH /weighings/{id}/status { status: "validated" }
        → Se crea automáticamente Transaction tipo "purchase"
    — o —
    → PATCH /weighings/{id}/status { status: "rejected", rejection_reason: "..." }

Después del pago físico:
    → PATCH /transactions/{purchase_id}/status { status: "paid" }  ← marca la compra como pagada
    → PATCH /weighings/{id}/status { status: "paid" }
```
