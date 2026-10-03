# Modelo de datos (MER)

Estado de la base de datos en `main`, migración **0022**. Verificado contra una base real con las migraciones aplicadas (tablas, columnas y llaves foráneas con su regla de borrado). GitHub dibuja el diagrama; en otro editor, pegar el bloque en <https://mermaid.live>.

Convenciones: `PK` llave primaria, `FK` llave foránea, `UK` única. Los tipos son los de PostgreSQL simplificados; `enum` indica un tipo enumerado (valores en la sección *Enumerados*).

## Diagrama

```mermaid
erDiagram
    user_types ||--o{ users : "user_type_code"
    roles |o--o{ users : "role_code"
    document_types ||--o{ users : "id_type"
    document_types |o--o{ weighings : "seller_id_type"
    organizations |o--o{ users : "organization_id (RESTRICT)"
    users |o--o{ users : "verified_by"

    organizations ||--o{ eca_association_links : "eca_id"
    organizations ||--o{ eca_association_links : "association_id"
    users |o--o{ eca_association_links : "requested_by"
    users |o--o{ eca_association_links : "decided_by"

    organizations |o--o{ warehouses : "organization_id (RESTRICT)"
    materials ||--o{ inventory_items : "material_code"
    warehouses ||--o{ inventory_items : "warehouse_id"

    materials ||--o{ weighings : "material_code"
    warehouses ||--o{ weighings : "warehouse_id"
    users |o--o{ weighings : "recycler_id"
    users |o--o{ weighings : "validated_by"

    materials ||--o{ transactions : "material_code"
    warehouses ||--o{ transactions : "warehouse_id"
    users |o--o{ transactions : "recycler_id"
    users ||--o{ transactions : "created_by"
    weighings |o--o| transactions : "weighing_id (UK)"

    users ||--o{ one_time_tokens : "CASCADE"
    users ||--o{ refresh_tokens : "CASCADE"
    users ||--o| mfa_credentials : "CASCADE"
    users ||--o{ recovery_codes : "CASCADE"

    user_types {
        varchar20 code PK
        varchar100 label
        bool is_active
    }
    roles {
        varchar20 code PK
        varchar100 label
        bool is_active
    }
    document_types {
        varchar10 code PK
        varchar100 label
        bool is_active
    }

    organizations {
        uuid id PK
        enum type "association | eca"
        enum status "draft ... approved"
        varchar255 legal_name
        varchar50 tax_id "UK con type, solo si no es nulo"
        varchar255 legal_representative
        varchar255 contact_email
        varchar20 contact_phone
        text address
        varchar100 city
        timestamptz approved_at
        timestamptz created_at
        timestamptz updated_at
    }

    users {
        uuid id PK
        varchar255 email UK
        varchar255 password_hash
        varchar255 full_name
        varchar20 phone
        varchar10 id_type FK
        varchar20 id_number UK
        varchar20 user_type_code FK
        varchar20 role_code FK
        uuid organization_id FK "personal y reciclador"
        bool is_active
        timestamptz email_verified_at
        int failed_login_attempts
        timestamptz locked_until
        int token_version
        smallint verification_status "reciclador"
        text rejection_reason
        timestamptz verified_at
        uuid verified_by FK
        varchar50 employee_code "eca"
        json permissions "eca"
        varchar50 association_nit "asociacion"
        geometry coverage_area "asociacion"
        varchar255 company_name "b2b"
        varchar50 tax_id UK "b2b"
        json rep_goals "b2b"
        varchar255 building_name "conjunto"
        int num_units "conjunto"
        text address
        float latitude
        float longitude
        uuid association_id "sin FK, sin uso"
        timestamptz created_at
        timestamptz updated_at
    }

    eca_association_links {
        uuid id PK
        uuid eca_id FK
        uuid association_id FK
        enum status "requested active rejected removed"
        uuid requested_by FK
        uuid decided_by FK
        timestamptz decided_at
        varchar200 rejection_reason
        timestamptz created_at
        timestamptz updated_at
    }

    warehouses {
        uuid id PK
        varchar100 name
        text address
        bool is_active
        uuid organization_id FK "ECA duena"
        timestamptz created_at
    }
    materials {
        varchar30 code PK
        varchar100 label
        varchar10 unit
        bool is_active
    }
    inventory_items {
        uuid id PK
        varchar30 material_code FK "UK con warehouse_id"
        uuid warehouse_id FK
        numeric stock_kg "CHECK >= 0"
        numeric stock_min_kg "CHECK >= 0"
        numeric price_per_kg "CHECK >= 0"
        timestamptz updated_at
    }

    weighings {
        uuid id PK
        uuid recycler_id FK "nulo si es vendedor no registrado"
        varchar255 seller_name
        varchar10 seller_id_type FK
        varchar20 seller_id_number
        enum affiliation_status "linked unlinked_association independent"
        varchar30 material_code FK
        uuid warehouse_id FK
        numeric kg "CHECK > 0"
        numeric price_per_kg "CHECK > 0"
        enum status "pending_validation validated paid rejected"
        text rejection_reason
        uuid validated_by FK
        timestamptz validated_at
        timestamptz occurred_at
        timestamptz created_at
        timestamptz updated_at
    }

    transactions {
        uuid id PK
        enum type "purchase | sale"
        enum status "pending paid cancelled delivered"
        varchar30 material_code FK
        uuid warehouse_id FK
        numeric kg "CHECK > 0"
        numeric price_per_kg "CHECK > 0"
        uuid recycler_id FK "compras"
        uuid weighing_id FK "UK, compras"
        varchar255 buyer_name "ventas"
        varchar20 buyer_nit "ventas"
        varchar255 buyer_email "ventas"
        uuid created_by FK
        timestamptz occurred_at
        timestamptz created_at
        timestamptz updated_at
    }

    one_time_tokens {
        uuid id PK
        uuid user_id FK
        varchar20 purpose "activar, verificar, resetear"
        varchar64 token_hash UK
        timestamptz expires_at
        timestamptz used_at
        timestamptz created_at
    }
    refresh_tokens {
        uuid id PK
        uuid user_id FK
        uuid family_id "rotacion"
        varchar20 audience "portal | backoffice"
        varchar64 token_hash UK
        timestamptz expires_at
        timestamptz used_at
        timestamptz revoked_at
        timestamptz created_at
    }
    mfa_credentials {
        uuid user_id PK, FK
        text secret_encrypted
        timestamptz enabled_at
        bigint last_step
        timestamptz created_at
    }
    recovery_codes {
        uuid id PK
        uuid user_id FK
        varchar64 code_hash
        timestamptz used_at
        timestamptz created_at
    }
    revoked_tokens {
        varchar36 jti PK
        timestamptz revoked_at
        timestamptz expires_at
    }

    audit_log {
        uuid id PK
        timestamptz occurred_at
        varchar50 action
        varchar10 outcome
        uuid actor_id "sin FK"
        varchar20 actor_role
        varchar30 target_type
        varchar64 target_id "sin FK"
        varchar45 ip
        varchar64 request_id
        jsonb details
    }
```

## Lectura del modelo

**Cuatro bloques**

1. **Identidad y organizaciones:** `users` (una sola tabla para todos los actores, con columnas propias de cada tipo), los catálogos `user_types`, `roles` y `document_types`, `organizations` (la Asociación o la ECA como entidad) y `eca_association_links` (relación de varios a varios, siempre iniciada por la ECA).
2. **Operación:** `warehouses` (de una ECA), `materials`, `inventory_items` (stock por material y bodega), `weighings` y `transactions`.
3. **Sesión y seguridad:** `one_time_tokens` (activar, verificar correo, restablecer), `refresh_tokens` (con familia, para detectar reuso), `revoked_tokens`, `mfa_credentials` y `recovery_codes` (solo Somos R).
4. **Auditoría:** `audit_log`, sin llaves foráneas a propósito: un registro debe sobrevivir al usuario o recurso que describe.

**Cómo se decide quién ve qué (alcance por organización)**

- El personal y el reciclador pertenecen a **una** organización (`users.organization_id`).
- Los datos operativos pertenecen a la **ECA dueña de la bodega** (`warehouses.organization_id`): inventario, pesajes y transacciones heredan el alcance por `warehouse_id`.
- Una **Asociación** lee los pesajes de sus recicladores solo si `affiliation_status = linked`, y el inventario de las ECA con un vínculo `active`.
- Una bodega sin dueña (`organization_id` nulo) no la ve ningún cliente.

**Reglas de integridad en la base**

- `inventory_items`: único por `(material_code, warehouse_id)`; stock, mínimo y precio no negativos.
- `weighings` y `transactions`: `kg > 0` y `price_per_kg > 0`; una compra por pesaje (`weighing_id` único).
- Un pesaje tiene **un** vendedor: un `recycler_id` o los datos del vendedor no registrado (restricción `CHECK` en `weighings`).
- `organizations`: un NIT por tipo de organización (índice único parcial). `users.organization_id` y `warehouses.organization_id` son `RESTRICT`: una organización con personal o bodegas no se borra.
- Los tokens y credenciales MFA se borran en cascada con su usuario.

## Enumerados

| Enumerado | Valores |
|---|---|
| `organization_type` | `association`, `eca` |
| `organization_status` | `draft`, `submitted`, `in_review`, `changes_requested`, `approved`, `rejected`, `suspended` (solo `approved` opera) |
| `link_status` | `requested`, `active`, `rejected`, `removed` |
| `weighing_status` | `pending_validation`, `validated`, `paid`, `rejected` |
| `affiliation_status` | `linked`, `unlinked_association`, `independent` |
| `transaction_type` | `purchase`, `sale` |
| `transaction_status` | `pending`, `paid`, `cancelled`, `delivered` |
| `verification_status` (en `users`, smallint) | `pending`, `verified`, `rejected` |

## Observaciones de diseño

- **`users` es una tabla ancha** (39 columnas): mezcla campos de reciclador, ECA, Asociación, conjunto y B2B. La tarea 5.5 del plan de hardening la separa en identidad más perfiles por tipo con `CHECK`.
- **`users.association_id` es residual:** columna `uuid` sin llave foránea y sin uso en el código. La asociación de un reciclador vive en `organization_id`. Candidata a eliminarse en una migración.
- **Datos duplicados de la Asociación en `users`** (`association_nit`, `legal_representative`, `coverage_area`) conviven con `organizations`; la migración 0018 los usó para crear las organizaciones.
- **Sin libro de movimientos:** `inventory_items.stock_kg` es el único registro del stock. Las tareas 5.7 y 7.3 piden un libro inmutable de movimientos.
- **Un pesaje es un solo renglón** (un material, un peso). El rediseño por lote con hash encadenado es la tarea 7.2.
- **`audit_log.target_id` es texto** (admite ids de cualquier tipo de recurso), por eso no hay llave foránea.

## En curso: solicitud pública de organización (PR #60, migración 0023)

Aún no está en `main`. Agrega una tabla y cambia un índice de `organizations`.

```mermaid
erDiagram
    organizations ||--o| organization_applications : "organization_id (UK)"
    organization_applications {
        uuid id PK
        uuid organization_id FK "UK"
        varchar255 applicant_name
        varchar255 applicant_email
        varchar64 access_token_hash UK "enlace magico, solo el hash"
        timestamptz access_token_expires_at
        timestamptz email_verified_at
        varchar20 consent_version
        timestamptz consent_at
        timestamptz submitted_at
        int submission_count
        timestamptz created_at
        timestamptz updated_at
    }
```

- El NIT único de `organizations` pasa a contarse **solo entre organizaciones que operan** (`approved` o `suspended`): un borrador no debe bloquear a la organización real.
- Falta (tareas 6.7 a 6.9): `organization_document_types` y los documentos de cada solicitud, la revisión con su checklist y los motivos de rechazo.
