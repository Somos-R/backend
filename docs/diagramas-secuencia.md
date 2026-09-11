# Diagramas de Secuencia por Módulo

Cada diagrama es independiente y cubre un módulo específico del sistema.

---

## 1. Recicladores — Registro y Verificación

```mermaid
sequenceDiagram
    actor Operador
    participant FE as Frontend React
    participant API as Backend FastAPI
    participant DB as PostgreSQL

    Note over Operador,DB: ── REGISTRO ──────────────────────────────────────

    Operador->>FE: Click "Registrar reciclador"
    FE->>FE: setDrawerOpen(true)
    FE->>FE: Renderiza RegisterRecyclerDrawer

    FE->>API: GET /catalogs/document-types
    alt Catálogo disponible
        API-->>FE: [{ code: "CC" }, { code: "CE" }, ...]
        FE->>FE: Carga opciones en selector de tipo de documento
    else Error / no disponible
        API-->>FE: Error
        FE->>FE: Usa FALLBACK_DOC_TYPES<br/>[CC, CE, TI, PA]
    end

    Operador->>FE: Completa formulario<br/>full_name, email, id_type,<br/>id_number, phone

    FE->>FE: Validaciones inline:<br/>· email con formato válido<br/>· id_number entre 6 y 20 caracteres<br/>· phone solo dígitos (opcional)

    FE->>API: POST /auth/register<br/>{ user_type_code: "recycler",<br/>  full_name, email,<br/>  id_type, id_number, phone }

    API->>DB: SELECT User WHERE email = email
    DB-->>API: null (no existe)

    API->>DB: INSERT User<br/>(verification_status: "pending",<br/> user_type_code: "recycler")
    DB-->>API: User { id, verification_status: "pending" }

    API-->>FE: 201 UserResponse
    FE->>FE: invalidateQueries(['recyclers'])
    FE-->>Operador: Snackbar "Reciclador registrado"
    FE->>FE: onClose() → drawer se cierra

    Note over Operador,DB: ── VERIFICACIÓN ──────────────────────────────────

    FE->>API: GET /users?user_type_code=recycler
    API->>DB: SELECT users WHERE user_type_code='recycler'
    DB-->>API: Lista de recicladores
    API-->>FE: { total, items: [...] }
    FE-->>Operador: Tabla con badge "Pendiente"<br/>y botones Validar / Rechazar

    alt Operador valida al reciclador
        Operador->>FE: Click "Validar"
        FE->>FE: setValidatingId(userId)

        FE->>API: PATCH /users/{id}/verification-status<br/>{ status: "verified" }

        API->>DB: SELECT User WHERE id = {id}
        DB-->>API: User (pending)

        API->>API: password_hash = bcrypt(id_number)
        Note over API: Contraseña inicial = número de cédula

        API->>DB: UPDATE User<br/>SET verification_status = "verified",<br/>    password_hash = bcrypt(id_number),<br/>    verified_at = now(UTC),<br/>    verified_by = current_user.id,<br/>    rejection_reason = null
        DB-->>API: User actualizado

        API-->>FE: UserDetailResponse (verified)
        FE->>FE: invalidateQueries(['recyclers'])
        FE-->>Operador: Snackbar "Reciclador verificado"<br/>Badge cambia a "Verificado"

    else Operador rechaza al reciclador
        Operador->>FE: Click "Rechazar"
        FE->>FE: Abre diálogo de motivo de rechazo

        Operador->>FE: Escribe motivo y confirma

        FE->>FE: Valida que motivo no esté vacío

        FE->>API: PATCH /users/{id}/verification-status<br/>{ status: "rejected",<br/>  rejection_reason: "Documento ilegible" }

        API->>DB: UPDATE User<br/>SET verification_status = "rejected",<br/>    rejection_reason = "Documento ilegible"
        DB-->>API: User actualizado

        API-->>FE: UserDetailResponse (rejected)
        FE->>FE: invalidateQueries(['recyclers'])
        FE->>FE: handleCloseRejectDialog()
        FE-->>Operador: Snackbar "Reciclador rechazado"<br/>Badge cambia a "Rechazado"
    end
```

---

## 2. Pesajes — Registro y Validación

```mermaid
sequenceDiagram
    actor Operador
    participant FE as Frontend React
    participant API as Backend FastAPI
    participant DB as PostgreSQL

    Note over Operador,DB: ── CARGA INICIAL DE LA PÁGINA ───────────────────

    Operador->>FE: Navega a Pesajes
    par Consultas paralelas
        FE->>API: GET /weighings?limit=20
        API->>DB: SELECT weighings ORDER BY fecha DESC
        DB-->>API: Lista de pesajes
        API-->>FE: { total, items: [...] }
    and
        FE->>API: GET /weighings/stats
        API->>DB: SELECT weighings WHERE fecha >= inicio_mes
        DB-->>API: Aggregaciones del mes
        API-->>FE: { total_weighings_month,<br/>  total_kg_month,<br/>  pending_count,<br/>  by_material: [...] }
    end
    FE-->>Operador: Tabla de pesajes + tarjetas de estadísticas

    Note over Operador,DB: ── REGISTRO DE PESAJE ───────────────────────────

    Operador->>FE: Click "Nuevo pesaje"
    FE->>FE: setDrawerOpen(true)

    par Carga paralela al abrir drawer (enabled: open)
        FE->>API: GET /users?verification_status=verified
        API->>DB: SELECT users WHERE verification_status='verified'
        DB-->>API: Recicladores verificados
        API-->>FE: { items: [recicladores] }
        Note over FE: Solo recicladores verificados<br/>aparecen en el selector
    and
        FE->>API: GET /inventory/materials
        API->>DB: SELECT materials WHERE is_active=true
        DB-->>API: Catálogo de materiales
        API-->>FE: [{ code, label }]
    and
        FE->>API: GET /inventory/warehouses
        API->>DB: SELECT warehouses WHERE is_active=true
        DB-->>API: Bodegas activas
        API-->>FE: [{ id, name }]
    and
        FE->>API: GET /inventory
        API->>DB: SELECT inventory_items
        DB-->>API: Items con precio_kg por material+bodega
        API-->>FE: { items: [...] }
    end

    Operador->>FE: Selecciona reciclador de la lista
    Operador->>FE: Selecciona material + bodega

    FE->>FE: useEffect: busca en inventoryItems<br/>match(material_code + warehouse_id)
    FE->>FE: Auto-completa precio_kg<br/>con el precio del inventario

    Operador->>FE: Ajusta kg (y precio si necesario)
    FE->>FE: Muestra total = kg × precio_kg en tiempo real

    Operador->>FE: Click "Registrar pesaje"
    FE->>FE: validate():<br/>· recycler_id no vacío<br/>· material_code no vacío<br/>· warehouse_id no vacío<br/>· kg > 0<br/>· precio_kg > 0

    FE->>API: POST /weighings<br/>{ recycler_id, material_code,<br/>  warehouse_id, kg, precio_kg }

    API->>DB: GET User(recycler_id)<br/>→ valida user_type_code='recycler'
    API->>DB: GET Material(material_code)
    API->>DB: GET Warehouse(warehouse_id)
    DB-->>API: Entidades válidas

    API->>DB: INSERT Weighing<br/>(estado: pendiente, fecha: now())
    DB-->>API: Weighing { id, estado: "pendiente" }

    API-->>FE: 201 WeighingResponse
    FE->>FE: invalidateQueries(['weighings'])
    FE-->>Operador: Snackbar "Pesaje registrado"
    FE->>FE: onClose()

    Note over Operador,DB: ── VALIDACIÓN / RECHAZO ─────────────────────────

    Operador->>FE: Click "Validar" en pesaje pendiente
    FE->>API: PATCH /weighings/{id}/status<br/>{ status: "validado" }

    API->>DB: SELECT Weighing WHERE id={id}
    DB-->>API: Weighing (pendiente)

    API->>DB: UPDATE Weighing<br/>SET estado='validado',<br/>    validated_by=current_user.id

    Note over API,DB: Service crea la transacción de compra automáticamente
    API->>DB: INSERT Transaction<br/>(type: "compra",<br/> status: "pendiente",<br/> recycler_id, material_code,<br/> warehouse_id, kg, precio_kg,<br/> total_value = kg × precio_kg)
    DB-->>API: Transaction creada

    API-->>FE: WeighingResponse (validado)
    FE->>FE: invalidateQueries(['weighings','transactions'])
    FE-->>Operador: Badge "Validado" en la fila

    alt Rechazo en lugar de validación
        Operador->>FE: Click "Rechazar" → ingresa motivo
        FE->>API: PATCH /weighings/{id}/status<br/>{ status: "rechazado",<br/>  rejection_reason: "Peso inconsistente" }
        API->>DB: UPDATE Weighing<br/>SET estado='rechazado',<br/>    rejection_reason='Peso inconsistente'
        DB-->>API: OK
        API-->>FE: WeighingResponse (rechazado)
        FE-->>Operador: Badge "Rechazado" en la fila
    end
```

---

## 3. Inventario — Consulta y Edición

```mermaid
sequenceDiagram
    actor Operador
    participant FE as Frontend React
    participant API as Backend FastAPI
    participant DB as PostgreSQL

    Note over Operador,DB: ── CARGA INICIAL ────────────────────────────────

    Operador->>FE: Navega a Inventario

    par Consultas paralelas al montar el componente
        FE->>API: GET /inventory
        API->>DB: SELECT inventory_items<br/>ORDER BY material_code
        DB-->>API: Items de inventario
        Note over API: Calcula estado por item:<br/>stock_kg = 0 → agotado<br/>stock_kg < stock_min_kg → bajo_stock<br/>stock_kg >= stock_min_kg → disponible
        API-->>FE: { total, items: [InventoryItemResponse] }
    and
        FE->>API: GET /inventory/stats
        API->>DB: SELECT inventory_items (todos)
        DB-->>API: Todos los items
        API->>API: Calcula aggregaciones:<br/>total_stock_kg = SUM(stock_kg)<br/>total_value = SUM(stock_kg × precio_kg)<br/>available_count, low_stock_count,<br/>out_of_stock_count
        API-->>FE: InventoryStatsResponse
    end

    FE->>FE: toViewModel(): mapea API → ViewModel<br/>(convierte Decimals a Number)
    FE-->>Operador: 4 tarjetas de estadísticas<br/>+ tabla con material, bodega, stock,<br/>estado (badge) y botón editar

    Note over Operador,DB: ── FILTRADO (frontend) ───────────────────────────

    Operador->>FE: Filtra por material o estado
    FE->>FE: Filtra items en memoria<br/>(no hace nueva request)
    FE-->>Operador: Tabla actualizada sin llamada al backend

    Note over Operador,DB: ── EDICIÓN DE UN ÍTEM ───────────────────────────

    Operador->>FE: Click "Editar" en un ítem
    FE->>FE: handleOpenEdit(item)<br/>→ setEditTarget(item)<br/>→ setEditMinKg(item.stock_min_kg)<br/>→ setEditPrecioKg(item.precio_kg)
    FE-->>Operador: Modal con campos:<br/>· Stock mínimo (kg)<br/>· Precio por kg ($)

    Operador->>FE: Modifica valores y confirma

    FE->>FE: handleEditSubmit():<br/>· min > 0<br/>· precio > 0

    FE->>API: PATCH /inventory/{id}<br/>{ stock_min_kg: 150,<br/>  precio_kg: 1400 }

    API->>DB: SELECT InventoryItem WHERE id={id}
    DB-->>API: Item existente

    API->>DB: UPDATE InventoryItem<br/>SET stock_min_kg=150,<br/>    precio_kg=1400,<br/>    fecha_actualizacion=now()
    DB-->>API: InventoryItemResponse actualizado

    API-->>FE: 200 InventoryItemResponse
    FE->>FE: invalidateQueries(['inventory'])
    FE->>FE: setEditTarget(null) → cierra modal
    FE-->>Operador: Snackbar "Ítem actualizado"<br/>Tabla se refresca con nuevo precio y umbral

    Note over Operador,DB: ── IMPACTO INDIRECTO DEL INVENTARIO ─────────────

    Note over FE,DB: Cuando se valida un pesaje:<br/>service.validate_weighing() → INSERT Transaction (compra)<br/>Cuando se crea una venta:<br/>inventory_service.subtract_stock() → UPDATE stock_kg -= kg vendidos<br/>El estado (disponible/bajo_stock/agotado) se recalcula automáticamente
```

---

## 4. Transacciones — Compras y Ventas

```mermaid
sequenceDiagram
    actor Operador
    participant FE as Frontend React
    participant API as Backend FastAPI
    participant DB as PostgreSQL

    Note over Operador,DB: ── CARGA INICIAL ────────────────────────────────

    Operador->>FE: Navega a Transacciones

    par Consultas paralelas
        FE->>API: GET /transactions?type=compra&limit=100
        API->>DB: SELECT transactions WHERE type='compra'<br/>ORDER BY fecha DESC
        DB-->>API: Lista de compras
        API-->>FE: { total, items: [compras] }
    and
        FE->>API: GET /transactions?type=venta&limit=100
        API->>DB: SELECT transactions WHERE type='venta'
        DB-->>API: Lista de ventas
        API-->>FE: { total, items: [ventas] }
    and
        FE->>API: GET /transactions/stats
        API->>DB: SELECT transactions WHERE fecha >= inicio_mes
        DB-->>API: Transacciones del mes
        API->>API: Calcula: kg compras/ventas,<br/>valor total, pendientes
        API-->>FE: TransactionStatsResponse
    and
        FE->>API: GET /inventory/warehouses
        API-->>FE: [{ id, name }]
        Note over FE: Usadas para pre-seleccionar<br/>bodega en "Nueva Venta"
    end

    FE-->>Operador: Pestaña Compras + pestaña Ventas<br/>con tarjetas de estadísticas y tablas

    Note over Operador,DB: ── CREAR VENTA MANUAL ───────────────────────────

    Operador->>FE: Click "Nueva Venta"
    FE->>FE: setVentaForm({<br/>  ...EMPTY_VENTA,<br/>  warehouse_id: warehouses[0].id<br/>})
    Note over FE: Primera bodega pre-seleccionada<br/>para evitar validación vacía
    FE-->>Operador: Modal con campos:<br/>material, bodega, kg,<br/>precio_kg, empresa compradora

    Operador->>FE: Completa datos y confirma
    FE->>FE: Valida warehouse_id, kg, precio_kg no vacíos

    FE->>API: POST /transactions<br/>{ material_code, warehouse_id,<br/>  kg, precio_kg, buyer_name,<br/>  buyer_nit, buyer_email }

    API->>DB: GET Material, GET Warehouse → valida existencia
    DB-->>API: OK

    API->>DB: subtract_stock()<br/>UPDATE InventoryItem SET stock_kg -= kg

    alt Stock insuficiente
        API-->>FE: 400 "Stock insuficiente:<br/>disponible X kg, solicitado Y kg"
        FE-->>Operador: Alert con el error
    else Stock disponible
        API->>DB: INSERT Transaction<br/>(type: "venta", status: "pendiente",<br/> total_value = kg × precio_kg)
        DB-->>API: Transaction { id, status: "pendiente" }
        API-->>FE: 201 TransactionResponse
        FE->>FE: invalidateQueries(['transactions','inventory','weighings'])
        FE->>FE: setShowVentaModal(false)
        FE-->>Operador: Venta aparece en tabla con botones<br/>"Marcar entregada" / "Cancelar"
    end

    Note over Operador,DB: ── ENTREGAR O CANCELAR VENTA ────────────────────

    Operador->>FE: Click "Marcar entregada" en venta pendiente
    FE->>API: PATCH /transactions/{id}/status<br/>{ status: "entregado" }
    API->>API: Verifica type=venta, status=pendiente
    API->>DB: UPDATE Transaction SET status = "entregado"
    DB-->>API: OK
    API-->>FE: TransactionResponse (entregado)
    FE->>FE: invalidateQueries(['transactions'])
    FE-->>Operador: Tabla actualiza estado

    Operador->>FE: Click "Cancelar" en venta pendiente
    FE->>API: PATCH /transactions/{id}/status<br/>{ status: "cancelado" }
    API->>API: Verifica status=pendiente
    API->>DB: add_stock()<br/>UPDATE InventoryItem SET stock_kg += kg<br/>(se restaura el stock descontado al crear)
    API->>DB: UPDATE Transaction SET status = "cancelado"
    DB-->>API: OK
    API-->>FE: TransactionResponse (cancelado)
    FE->>FE: invalidateQueries(['transactions','inventory'])
    FE-->>Operador: Tabla actualiza estado, stock restaurado

    Note over Operador,DB: ── MARCAR COMPRA COMO PAGADA (pago manual) ──────

    Operador->>FE: Click "Marcar pagada" en compra pendiente
    FE->>API: PATCH /transactions/{id}/status<br/>{ status: "pagado" }
    API->>API: Verifica type=compra, status=pendiente
    API->>DB: UPDATE Transaction SET status = "pagado"
    DB-->>API: OK
    API-->>FE: TransactionResponse (pagado)
    FE->>FE: invalidateQueries(['transactions'])
    FE-->>Operador: Tabla actualiza estado
```
