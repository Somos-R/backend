from typing import Any

_PARTIES = """
El vínculo solo concierne a sus dos organizaciones: cualquier otra persona recibe `404 link_not_found`, igual
que si no existiera. Todo cambio queda auditado (`link.requested`, `link.accepted`, `link.rejected`,
`link.removed`)."""

DIRECTORY_DOCS: dict[str, Any] = {
    "summary": "Directorio de asociaciones",
    "description": """
Lista las **asociaciones aprobadas** con las que una ECA puede pedir vincularse, con el estado de su vínculo con
cada una (`link_status`: `requested`, `active`, `rejected`, `removed` o `null` si no hay). Solo trae el nombre y
la ciudad: ningún dato de contacto ni tributario.

**Quién puede:** administradores de ECA (`eca_admin`). `q` busca por nombre sin distinguir mayúsculas ni tildes
(mínimo 2 caracteres).
""",
}

REQUEST_LINK_DOCS: dict[str, Any] = {
    "summary": "Solicitar vínculo con una asociación",
    "description": f"""
La ECA pide vincularse a una asociación; el administrador de la asociación decide. **La asociación nunca inicia.**

**Quién puede:** administradores de ECA (`eca_admin`) de una ECA aprobada.

Si ya había un vínculo rechazado o retirado, la solicitud lo reabre. Errores: `409 link_already_requested`,
`409 link_already_active`, `404 organization_not_found`, `409 organization_not_active`, `403 no_organization`.
{_PARTIES}
""",
}

LIST_LINKS_DOCS: dict[str, Any] = {
    "summary": "Vínculos de mi organización",
    "description": f"""
Los vínculos en los que participa tu organización: una ECA ve los suyos con las asociaciones; una asociación,
los que le solicitaron las ECA. Filtro opcional `status`. Del otro lado solo se ve el nombre y la ciudad.

**Quién puede:** administradores de organización (`eca_admin`, `association_admin`).
{_PARTIES}
""",
}

ACCEPT_LINK_DOCS: dict[str, Any] = {
    "summary": "Aceptar un vínculo",
    "description": f"""
El administrador de la asociación acepta la solicitud de una ECA, que pasa a `active`.

**Quién puede:** administradores de asociación (`association_admin`), solo sobre solicitudes hechas a su
asociación. Errores: `409 link_not_pending`, `409 organization_not_active`, `404 link_not_found`.
{_PARTIES}
""",
}

REJECT_LINK_DOCS: dict[str, Any] = {
    "summary": "Rechazar un vínculo",
    "description": f"""
El administrador de la asociación rechaza la solicitud, con un motivo opcional (hasta 200 caracteres). La ECA
puede volver a solicitar.

**Quién puede:** administradores de asociación (`association_admin`). Errores: `409 link_not_pending`,
`404 link_not_found`.
{_PARTIES}
""",
}

REMOVE_LINK_DOCS: dict[str, Any] = {
    "summary": "Retirar un vínculo",
    "description": f"""
Termina un vínculo `active`; cualquiera de las dos partes puede hacerlo. Además, la ECA puede **cancelar** su
solicitud mientras no tenga respuesta. El vínculo queda `removed` (no se borra la fila).

**Quién puede:** administradores de las dos organizaciones del vínculo. Errores: `409 link_not_removable`,
`404 link_not_found`.
{_PARTIES}
""",
}

_ROUTING_PROFILE = """
Son los datos de la ECA que usa el cálculo de rutas (RUT-01): su **ubicación** (punto de entrega donde cierran
las rutas), su **horario de recepción** y los **materiales que recibe**. Cada parte es `null` hasta que la ECA
la declara.

Siempre es la ECA de quien llama: no hay forma de nombrar otra organización. Errores comunes:
`403 no_organization` (la cuenta no tiene organización), `403 organization_not_active` (la ECA no está aprobada)."""

GET_ECA_ROUTING_PROFILE_DOCS: dict[str, Any] = {
    "summary": "Datos de ruta de mi ECA",
    "description": f"""
Devuelve la ubicación, el horario de recepción y los materiales que recibe tu ECA.

**Quién puede:** personal de ECA (`eca_admin`, `eca_operator`, `eca_warehouse`); capacidad
`eca_routing_profile.view`.
{_ROUTING_PROFILE}
""",
}

UPDATE_ECA_ROUTING_PROFILE_DOCS: dict[str, Any] = {
    "summary": "Actualizar los datos de ruta de mi ECA",
    "description": f"""
**Reemplaza** el perfil completo (todos los campos son obligatorios):

- `latitude` / `longitude`: grados WGS 84, hasta 6 decimales.
- `reception_hours`: de 1 a 21 franjas `{{"day", "opens_at", "closes_at"}}`. `day` es `monday` … `sunday`; las
  horas van en `HH:MM` (24 h, hora de Colombia) y la apertura debe ser anterior al cierre. Un día puede tener
  varias franjas (p. ej. con pausa de almuerzo); se pueden tocar pero no cruzar. Se devuelven ordenadas por día
  y hora.
- `accepted_materials`: de 1 a 20 códigos **activos** del catálogo de materiales (los repetidos se ignoran).
  `404 material_not_found`, `400 material_inactive`.

**Quién puede:** administradores de ECA (`eca_admin`); capacidad `eca_routing_profile.edit`. El cambio queda
auditado (`eca.routing_profile_updated`, con los códigos de material y cuántas franjas, sin coordenadas).
{_ROUTING_PROFILE}
""",
}

LIST_NEARBY_ECAS_DOCS: dict[str, Any] = {
    "summary": "ECA vinculadas cercanas a un punto",
    "description": """
Las ECA a las que puede llevar material tu asociación, dentro de un radio, de la **más cercana a la más lejana**.
Sirve para elegir a dónde entregar lo recogido y, más adelante, para el cálculo de rutas.

**Quién puede:** personal de asociación (`association_admin`, `association_operator`, `route_manager`); capacidad
`ecas.nearby`. Cualquier otra cuenta recibe `403`.

**Alcance:** solo las ECA **aprobadas y con vínculo activo** con tu asociación; el radio nunca lo amplía. Una ECA sin
ubicación declarada (ver `PUT /ecas/me/routing-profile`) no aparece.

Parámetros: `lat` (-90 a 90) y `lng` (-180 a 180) del punto; `radius` en metros (1 a 50 000, por defecto 5 000);
`material` (opcional: código del catálogo; solo las ECA que declararon recibirlo, y las que no declararon materiales
quedan fuera); `limit` (1 a 100, por defecto 20) y `offset`.

Cada ECA trae `distance_meters` (línea recta sobre el esferoide), su ubicación, `reception_hours` y
`accepted_materials`. `total` cuenta todas las que caen en el radio, no solo la página.
""",
}
