# Guía de uso y prueba manual

Cómo levantar el sistema en local y probar, paso a paso, lo que hoy hace cada tipo de usuario. Corresponde al estado de `main` del 3 de octubre de 2026. La matriz completa de permisos está en [`matriz-permisos.md`](matriz-permisos.md) y el modelo de datos en [`modelo-de-datos.md`](modelo-de-datos.md).

## 1. Preparar el entorno

```bash
# Backend (desde backend/)
docker compose up -d
docker compose exec app poetry run alembic upgrade head

# Portal de ECA y Asociación (desde somos-r-web/)
pnpm install && pnpm dev                    # http://localhost:5173

# Backoffice de Somos R (desde somos-r-backoffice/)
pnpm install && pnpm dev --port 5174        # http://localhost:5174
```

- **CORS:** por defecto el backend solo acepta `localhost:5173` y `:3000`. Para el backoffice agrega `CORS_ORIGINS=http://localhost:5173,http://localhost:5174` al `.env` del backend y reinicia el contenedor.
- **Correos:** con `EMAIL_BACKEND=console` no se envía nada. Los enlaces de activación, verificación y recuperación aparecen en `docker compose logs -f app`.
- **Swagger:** <http://localhost:8000/docs> (solo en `APP_ENV=dev`).

### Crear los usuarios de prueba

Hoy **no existe un camino por pantalla para crear una organización**: la solicitud pública (tarea 6.8) está en revisión y la bandeja de aprobación (6.9) no existe. Mientras tanto, un script carga datos de demostración:

```bash
# Somos R (pide la contraseña; guarda los códigos de recuperación del TOTP en el primer ingreso al backoffice)
docker compose exec app poetry run python scripts/create_platform_admin.py \
    --email admin@somosr.test --name "Admin Somos R" --id-number 1000000001

# Asociación y ECA demo, vínculo activo, un usuario por rol y un reciclador verificado
docker compose exec app poetry run python scripts/seed_demo.py
```

`seed_demo.py` solo corre con `APP_ENV=dev` y se puede repetir sin duplicar nada. Crea:

| Correo | Rol | Organización |
|---|---|---|
| `admin.asociacion@demo.test` | `association_admin` | Asociación Demo |
| `operativo.asociacion@demo.test` | `association_operator` | Asociación Demo |
| `rutas.asociacion@demo.test` | `route_manager` | Asociación Demo |
| `admin.eca@demo.test` | `eca_admin` | ECA Demo |
| `bascula.eca@demo.test` | `eca_operator` | ECA Demo |
| `bodega.eca@demo.test` | `eca_warehouse` | ECA Demo |
| `reciclador@demo.test` | reciclador verificado | Asociación Demo |

La contraseña de todas es la que imprime el script (variable `DEMO_PASSWORD`, solo para desarrollo). El vínculo entre las dos organizaciones queda **activo**; para probar el flujo de solicitud y aceptación, termínalo desde Vinculaciones y vuelve a solicitarlo. También asigna a la ECA demo las bodegas que crearon las migraciones y que no tenían dueña (sin dueña, ninguna ECA ve su inventario).

## 2. Somos R (`platform_admin`) — backoffice

1. Inicia sesión con correo y contraseña. La primera vez configura el segundo factor: escanea el QR con una app autenticadora y **guarda los 10 códigos de recuperación** (se muestran una sola vez).
2. **Organizaciones:** el backend ya expone el listado, la búsqueda y el detalle con personal y vínculos (`GET /admin/organizations`, solo lectura), pero el backoffice aún no tiene pantalla: pruébalo por Swagger con un token de Somos R.
3. **Usuarios:** busca, filtra, abre el detalle (queda auditado), desactiva o reactiva, desbloquea, cierra sesiones, reenvía invitación, cambia el rol y asigna organización a personal que no la tiene.
4. **Catálogos:** crea un material, renómbralo y desactívalo; comprueba que desaparece de las listas del portal. El código no se puede cambiar. Lo mismo con los tipos de documento.
5. **Auditoría:** filtra por actor, rol u organización y comprueba que tus acciones aparecen.
6. Prueba el aislamiento: una cuenta de Somos R no entra al portal (el login público responde como contraseña incorrecta) y recibe 403 en la API de los clientes.

Todavía no existe: aprobar o rechazar solicitudes de organizaciones, ni crear bodegas desde pantalla (solo `PUT /admin/warehouses/{id}/organization` por la API).

## 3. Administrador de ECA (`eca_admin`) — portal

1. **Vinculaciones:** si el vínculo demo sigue activo, termínalo y vuelve a **Solicitar** desde el directorio; queda pendiente hasta que la Asociación decida.
2. **Personal:** invita a un operador y a un encargado de bodega con un correo propio; abre el enlace del log para activar. Prueba reenviar, desactivar y reactivar.
3. **Recicladores:** registra uno nuevo. La ECA debe elegir su asociación.
4. **Pesajes:**
   - Identifica a quien entrega por documento: verás su estado de verificación y su afiliación.
   - Si no existe, regístralo como vendedor no registrado (nombre y documento).
   - Pesa con un material y un precio. Prueba filtros, orden por columna, periodo y **Descargar CSV**.
5. **Validar, rechazar con motivo y pagar** el pesaje. Al pagar nace una compra.
6. **Transacciones:** marca la compra como pagada; registra una venta (el stock baja), entrégala o cancélala; descarga el CSV de la pestaña.
7. **Inventario:** edita el stock mínimo y el precio; ordena y descarga el CSV.
8. **Configuración:** cambia la contraseña (cierra todas las sesiones).

## 4. Operador de báscula (`eca_operator`)

Puede pesar y validar o rechazar. **No puede pagar ni vender:** el botón no aparece y la API responde 403. Ve inventario y transacciones sin editarlos.

## 5. Encargado de bodega (`eca_warehouse`)

Ve pesajes; edita inventario; registra ventas y las entrega o cancela. **No puede pesar ni validar.** El menú no muestra Personal ni Vinculaciones.

## 6. Administrador de Asociación (`association_admin`)

1. **Vinculaciones:** en pendientes, **acepta o rechaza** la solicitud de la ECA (motivo de hasta 200 caracteres). Prueba también terminar un vínculo activo.
2. **Personal:** invita al operativo y al encargado de rutas.
3. **Recicladores:** solo ves los de tu asociación. **Verifica o rechaza** con motivo.
4. Con el vínculo activo y un reciclador verificado, la ECA le pesa: **tú ves ese pesaje y puedes validarlo y pagarlo**. Si el vendedor no está registrado, o su afiliación no es `linked`, tú no lo ves.
5. **Inventario:** lees las bodegas de las ECA con vínculo activo, sin editar.
6. **Inicio:** indicadores de recicladores y pesajes del mes.

## 7. Operativo de Asociación (`association_operator`)

Verifica recicladores, valida y rechaza pesajes y ve inventario. **No paga**, no ve transacciones ni Personal.

## 8. Encargado de rutas (`route_manager`)

Solo ve Recicladores (lectura). Es lo esperado: el dominio de rutas aún no existe.

## 9. Reciclador, ciudadano y empresa B2B

No tienen pantalla. El reciclador activa su cuenta con el enlace de invitación (`/activate`) pero no entra al portal; el móvil aún no tiene pantallas. Lo único que se puede probar es por Swagger: `POST /auth/login` y `GET /weighings` (solo ve los suyos).

## 10. Pruebas negativas para todos los roles

- Abrir una URL a la que el rol no tiene acceso: pantalla 403.
- Pedir por API un recurso de otra organización: **404**, no 403.
- Cinco intentos fallidos de login bloquean la cuenta.
- Cambiar la contraseña cierra todas las sesiones.
