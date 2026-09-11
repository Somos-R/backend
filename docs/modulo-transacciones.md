# Módulo: Transacciones

Gestiona las transacciones económicas de la ECA: **compras** (material a recicladores) y **ventas** (material a empresas).

---

## Backend — `app/domains/transactions/`

### Modelo (`models.py`)

| Campo | Tipo | Descripción |
|---|---|---|
| `id` | UUID | PK |
| `type` | enum | `compra` / `venta` |
| `status` | enum | `pendiente` → `pagado` (compra) / `entregado` / `cancelado` |
| `material_code` | str FK | Material transado |
| `warehouse_id` | UUID FK | Bodega origen/destino |
| `recycler_id` | UUID FK | Reciclador (solo en compras) |
| `kg` | Decimal | Kilogramos |
| `precio_kg` | Decimal | Precio por kg |
| `total_value` | Decimal | Total = kg × precio_kg |
| `fecha` | datetime | Timestamp UTC |
| `buyer_name` | str | Empresa compradora (ventas) |
| `buyer_nit` | str | NIT empresa compradora |
| `buyer_email` | str | Email empresa compradora |

### Ciclo de vida por tipo

```
COMPRA (creada automáticamente al validar un pesaje):
    pendiente ──► pagado     (pago manual al reciclador)
    pendiente ──► cancelado

VENTA (creada manualmente por el operador):
    pendiente ──► entregado  (stock ya descontado al crear la venta)
    pendiente ──► cancelado  (restaura el stock descontado al crear)
```

### Endpoints (`router.py`)

#### `GET /transactions`
Lista con filtros: `type`, `status`, `material_code`, paginación (`limit` máx 100).

#### `GET /transactions/stats`
Estadísticas del mes: kg y valor total de compras y ventas, conteo de pendientes.

#### `POST /transactions`
Crea una venta manual. Descuenta el stock de inventario de inmediato (`inventory_service.subtract_stock`); si no hay stock suficiente responde `400`.

```json
{
  "material_code": "papel",
  "warehouse_id": "uuid",
  "kg": 500,
  "precio_kg": 800,
  "buyer_name": "Empresa XYZ",
  "buyer_nit": "900123456",
  "buyer_email": "compras@xyz.com"
}
```

**Corrección aplicada:** El botón "Nueva Venta" pre-selecciona la primera bodega disponible al abrir el modal, evitando que el campo quede vacío y la validación falle.

#### `GET /transactions/{id}`
Detalle de una transacción.

#### `PATCH /transactions/{id}/status`
Transiciones válidas:

| `status` solicitado | Aplica a | Efecto |
|---|---|---|
| `pagado` | compra en `pendiente` | Marca la compra como pagada al reciclador |
| `entregado` | venta en `pendiente` | Marca la venta como entregada |
| `cancelado` | cualquiera en `pendiente` | Cancela; si era venta, restaura el stock descontado al crearla |

---

## Frontend — `src/features/transactions/`

### `Transactions.tsx`

**Pestañas:** Compras / Ventas, cada una con tarjetas de estadísticas, tabla paginada (8 por página) y acciones contextuales.

**Acciones disponibles por fila según estado:**

| Tipo | Estado | Acción disponible |
|---|---|---|
| venta | pendiente | Botón "Marcar entregada" / "Cancelar" |
| compra | pendiente | Botón "Marcar pagada" |

**Corrección aplicada:** Al abrir "Nueva Venta", el campo `warehouse_id` se pre-rellena con `warehouses[0]?.id` para evitar que el formulario inicie con bodega vacía y la validación rechace el envío.

```typescript
onClick={() => {
  setVentaForm({ ...EMPTY_VENTA, warehouse_id: warehouses[0]?.id ?? '' })
  setShowVentaModal(true)
}}
```
