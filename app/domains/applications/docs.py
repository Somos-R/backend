from typing import Any

_TOKEN = """
Se identifica con el enlace mágico que llegó al correo: el web lo envía en el encabezado **`X-Application-Token`**.
Un enlace inválido, vencido o de una solicitud ya cerrada responde `401 invalid_application_link`."""

START_DOCS: dict[str, Any] = {
    "summary": "Empezar la solicitud de una organización",
    "description": """
**Público, sin cuenta.** Una Asociación o una ECA pide unirse a Somos R. Se crea la solicitud en borrador y se
envía un **enlace mágico** al correo de quien la llena, con el que vuelve para completar, subir lo que falte y
enviarla. Usar el enlace verifica el correo.

Debe aceptarse el tratamiento de datos (`consent: true`; se guarda la fecha y la versión del texto). Responde
siempre **202** con el mismo mensaje: si ese correo ya tenía una solicitud abierta del mismo tipo, no se duplica,
solo recibe un enlace nuevo. `409 organization_already_registered` si el NIT ya es de una organización activa.
Límite de peticiones por IP.
""",
}

ACCESS_LINK_DOCS: dict[str, Any] = {
    "summary": "Pedir de nuevo el enlace de la solicitud",
    "description": """
**Público.** Si el correo tiene solicitudes abiertas, envía un enlace nuevo por cada una (el anterior deja de
servir). Responde siempre **202** con el mismo mensaje, haya o no solicitudes, para no revelar quién aplicó.
""",
}

CURRENT_DOCS: dict[str, Any] = {
    "summary": "Ver mi solicitud",
    "description": f"""
Devuelve la solicitud de quien trae el enlace: sus datos, el estado (`draft`, `submitted`, `in_review`,
`changes_requested`), cuántos envíos le quedan y qué falta para poder enviarla (`missing_fields`). Si el revisor
pidió correcciones, `feedback` trae su motivo (`summary`) y a qué envío responde.
{_TOKEN}
""",
}

UPDATE_DOCS: dict[str, Any] = {
    "summary": "Completar o corregir mi solicitud",
    "description": f"""
Cambia solo los campos enviados (`legal_name`, `tax_id`, `legal_representative`, `contact_email`,
`contact_phone`, `address`, `city`, `applicant_name`, y los de quien aplica: `applicant_id_type`,
`applicant_id_number`, `applicant_phone`, porque será el primer administrador); un texto vacío borra el campo
(menos los nombres). Solo se
puede mientras la solicitud está en borrador o con correcciones pedidas: si no, `409 application_locked`.
`409 organization_already_registered` si el NIT ya es de una organización activa.
{_TOKEN}
""",
}

SUBMIT_DOCS: dict[str, Any] = {
    "summary": "Enviar la solicitud a revisión",
    "description": f"""
Pasa la solicitud a `submitted` y manda un correo de confirmación. Exige los datos obligatorios
(`422 application_incomplete`, con los nombres de lo que falta) y que queden envíos disponibles (el primero más las
correcciones; `409 too_many_submissions`). Una solicitud ya enviada no se vuelve a enviar (`409 application_locked`).
{_TOKEN}
""",
}


DOCUMENTS_DOCS: dict[str, Any] = {
    "summary": "Documentos que se piden y lo que ya subí",
    "description": f"""
Lista los documentos que se le piden al tipo de organización (el catálogo lo edita Somos R) y, para cada uno,
el archivo subido (`document`) o `null`. Los obligatorios que faltan también aparecen en `missing_fields` como
`documents:<código>`. `status` es el veredicto del revisor: `pending`, `ok`, `missing` o `not_compliant`
(con su `review_comment`).
{_TOKEN}
""",
}

UPLOAD_DOCUMENT_DOCS: dict[str, Any] = {
    "summary": "Subir (o reemplazar) un documento",
    "description": f"""
Sube el archivo de un documento como `multipart/form-data` en el campo **`file`**. Un archivo por documento: subir
otro **reemplaza** el anterior y su veredicto vuelve a `pending`. Solo PDF, PNG o JPG, reconocidos por su
contenido (no por la extensión), de hasta 5 MB. El archivo se guarda en almacenamiento privado con un nombre
aleatorio; el nombre original es solo una etiqueta.

Errores: `404 document_type_not_found` (ese documento no se pide a esta organización), `409 application_locked`
(ya se envió), `413 file_too_large`, `415 unsupported_file_type`, `422 empty_file`. Límite de peticiones por IP.
{_TOKEN}
""",
}

DELETE_DOCUMENT_DOCS: dict[str, Any] = {
    "summary": "Quitar un documento subido",
    "description": f"""
Borra el archivo subido para ese documento (también del almacenamiento). Solo mientras la solicitud se puede
editar (`409 application_locked` si no). `404 document_not_found` si no había archivo.
{_TOKEN}
""",
}
