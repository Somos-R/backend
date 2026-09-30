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
