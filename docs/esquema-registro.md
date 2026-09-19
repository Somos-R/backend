# Esquema de registro — `POST /auth/register`

El campo `user_type_code` determina qué schema se aplica (unión discriminada de Pydantic).
Cada actor valida solo sus propios campos.

**Campos comunes a todos los actores (excepto `recycler`, que no requiere `password`):**
`email`, `password`, `full_name`, `phone` (opcional), `id_type`, `id_number`.

Valores válidos de `id_type`: `GET /catalogs/document-types` (`CC`, `CE`, `NIT`, `PA`, `PPT`).

---

## citizen

```json
{
  "user_type_code": "citizen",
  "email": "juan.perez@email.com",
  "password": "segura123",
  "full_name": "Juan Pérez",
  "phone": "3001234567",
  "id_type": "CC",
  "id_number": "1023456789",
  "address": "Calle 45 # 12-34, Bogotá",
  "latitude": 4.6097,
  "longitude": -74.0817
}
```

## building

```json
{
  "user_type_code": "building",
  "email": "admin@conjuntolaspalmas.com",
  "password": "segura123",
  "full_name": "María Torres",
  "phone": "3109876543",
  "id_type": "CC",
  "id_number": "52456789",
  "building_name": "Conjunto Las Palmas",
  "num_units": 48,
  "representation_document": null
}
```

`building_name` y `num_units` son requeridos.

## recycler

Lo registra la asociación; no requiere `password`.

```json
{
  "user_type_code": "recycler",
  "email": "carlos.recicla@email.com",
  "full_name": "Carlos Mendoza",
  "phone": "3156789012",
  "id_type": "CC",
  "id_number": "80234567"
}
```

Queda en estado `pending` y no puede iniciar sesión hasta ser verificado con
`PATCH /users/{id}/verification-status`.

## eca

```json
{
  "user_type_code": "eca",
  "email": "operador@ecabogota.com",
  "password": "segura123",
  "full_name": "Luisa Ramírez",
  "phone": "3187654321",
  "id_type": "CC",
  "id_number": "30567890",
  "employee_code": "ECA-2024-015",
  "association_id": null,
  "role_code": "eca_operator"
}
```

`role_code` es opcional; valores válidos: `eca_admin`, `eca_operator`
(ver `GET /catalogs/roles`). Si se envía y no existe o está inactivo, responde `422`.

## association

```json
{
  "user_type_code": "association",
  "email": "admin@asobeum.org",
  "password": "segura123",
  "full_name": "Roberto Gómez",
  "phone": "3012345678",
  "id_type": "CC",
  "id_number": "79345678",
  "association_nit": "900123456-7",
  "legal_representative": "Roberto Gómez Vargas",
  "role_code": "association_admin"
}
```

`association_nit` y `legal_representative` son requeridos.
`role_code` es opcional; valor válido: `association_admin`.

## b2b_client

```json
{
  "user_type_code": "b2b_client",
  "email": "compras@industriasverdes.com",
  "password": "segura123",
  "full_name": "Andrés Castillo",
  "phone": "3223456789",
  "id_type": "CC",
  "id_number": "1098765432",
  "company_name": "Industrias Verdes S.A.S.",
  "tax_id": "901234567-8",
  "commercial_contact": "Andrés Castillo - Jefe de Compras"
}
```

`company_name` y `tax_id` son requeridos.

---

Estos ejemplos también están disponibles como dropdown interactivo en Swagger
(`/docs`, endpoint `POST /auth/register`).
