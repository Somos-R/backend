# Flujo de negocio de punta a punta

Cómo se conectan los actores de Somos R, desde que una organización pide entrar hasta que el material pesado queda en inventario y se vende. Reemplaza a las secuencias por módulo de los sprints 2 y 3, que son anteriores a las organizaciones, a los vínculos y a "la ECA recibe de cualquiera". Estado de `main` a 3 de octubre de 2026.

Cada paso lleva una etiqueta: **[hecho]** existe en la API y en la pantalla que corresponde, **[solo API]** existe en el backend pero no tiene pantalla, **[en curso]** hay trabajo abierto, **[falta]** no existe todavía. Los estados de cada objeto están en [diagramas de estados](diagramas-de-estados.md).

## 1. El flujo, por carriles

Cada columna es un actor; las franjas son las etapas.

```mermaid
sequenceDiagram
    autonumber
    participant SOL as Solicitante
    participant SR as Somos R
    participant AA as Admin Asociación
    participant AE as Admin ECA
    participant OB as Báscula ECA
    participant EB as Bodega ECA
    participant REC as Reciclador
    participant SIS as Sistema

    rect rgba(11, 122, 86, 0.10)
    Note over SOL,SIS: 1. Incorporación de la organización
    SOL->>SIS: Inicia la solicitud (tipo, NIT, correo) [hecho, solo API]
    SIS-->>SOL: Enlace mágico por correo [hecho]
    SOL->>SIS: Completa datos, acepta el tratamiento y envía [hecho, solo API]
    SOL->>SR: Sube documentos [falta, 6.7]
    SR->>SIS: Revisa, pide correcciones o aprueba [en curso, 6.9]
    SIS-->>AA: Enlace de activación del primer administrador [falta, 6.9]
    Note over SR,SIS: Hoy el primer administrador se crea con seed_demo.py
    end

    rect rgba(11, 122, 86, 0.10)
    Note over SOL,SIS: 2. Equipo de cada organización
    AA->>SIS: Invita a su personal (operativo, rutas) [hecho]
    AE->>SIS: Invita a su personal (báscula, bodega) [hecho]
    SIS-->>AA: El invitado elige su contraseña con un enlace de un solo uso [hecho]
    end

    rect rgba(11, 122, 86, 0.10)
    Note over SOL,SIS: 3. Vínculo ECA - Asociación
    AE->>SIS: Solicita vínculo a una Asociación aprobada [hecho]
    AA->>SIS: Acepta o rechaza (motivo opcional) [hecho]
    Note over AE,AA: Cualquiera puede terminar un vínculo activo
    end

    rect rgba(11, 122, 86, 0.10)
    Note over SOL,SIS: 4. Padrón de recicladores
    AE->>SIS: Registra un reciclador y elige su Asociación [hecho]
    AA->>SIS: Registra o verifica al reciclador (rechazo con motivo) [hecho]
    SIS-->>REC: Enlace para elegir su contraseña [hecho]
    Note over REC,SIS: El reciclador no tiene pantalla propia todavía [falta, móvil]
    end

    rect rgba(11, 122, 86, 0.10)
    Note over SOL,SIS: 5. Pesaje en la ECA
    REC->>OB: Entrega el material
    OB->>SIS: Busca a quien entrega por documento [hecho]
    OB->>SIS: Registra el pesaje, si no está registrado, como vendedor no registrado [hecho]
    SIS-->>OB: Calcula la afiliación: linked, unlinked_association o independent [hecho]
    Note over OB,SIS: Identificar por QR [falta, ECA-03]
    end

    rect rgba(11, 122, 86, 0.10)
    Note over SOL,SIS: 6. Validación y pago
    OB->>SIS: Valida o rechaza con motivo [hecho]
    AA->>SIS: Si el pesaje es linked, también puede validarlo [hecho]
    SIS->>SIS: Al validar suma el stock y crea la compra [hecho]
    AE->>SIS: Paga el pesaje [hecho]
    AE->>SIS: Paga la compra, registro aparte [hecho]
    Note over SIS: Chequeos automáticos y bandeja de excepciones [falta, VAL-01 y 02]
    end

    rect rgba(11, 122, 86, 0.10)
    Note over SOL,SIS: 7. Inventario y venta
    EB->>SIS: Ajusta stock mínimo y precio de referencia [hecho]
    EB->>SIS: Registra una venta a una empresa, resta el stock [hecho]
    EB->>SIS: Entrega la venta, o la cancela y devuelve el stock [hecho]
    Note over EB,SIS: Crear bodegas desde pantalla [solo API]
    end

    rect rgba(11, 122, 86, 0.10)
    Note over SOL,SIS: 8. Reportes
    AE->>SIS: Descarga CSV de pesajes, transacciones e inventario [hecho]
    AA->>SIS: Descarga CSV de los pesajes que ve [hecho]
    Note over AA,SIS: PDF, tablero por periodo y reporte SUI [falta, REP-01 a 04]
    end
```

## 2. Qué parte del flujo existe

Misma ruta, vista por cobertura. Verde es lo que funciona hoy, ámbar lo que tiene una parte, rojo lo que falta.

```mermaid
flowchart LR
    classDef done fill:#d8f0e4,stroke:#0b7a56,color:#0b3d2b
    classDef part fill:#fbe9c8,stroke:#a85d00,color:#4a2a00
    classDef todo fill:#f6d6d2,stroke:#a8321f,color:#4a130b

    A["Solicitud pública<br/>enlace mágico y envío"]:::part --> B["Revisión y aprobación<br/>Somos R"]:::todo
    B --> C["Primer administrador<br/>enlace de activación"]:::todo
    C --> D["Personal por invitación<br/>activar, desactivar"]:::done
    D --> E["Vínculo ECA - Asociación<br/>solicitar, decidir, terminar"]:::done
    E --> F["Padrón de recicladores<br/>registrar y verificar"]:::done
    F --> G["Pesaje en la ECA<br/>por documento, cualquier vendedor"]:::done
    G --> H["Validación y pago<br/>manual"]:::done
    H --> I["Inventario y compra<br/>por evento al validar"]:::done
    I --> J["Venta a empresa<br/>entregar o cancelar"]:::done
    J --> K["Reportes<br/>CSV por tabla"]:::part

    G -.-> G2["Identificar por QR"]:::todo
    G -.-> G3["Pesaje por lote con hash"]:::todo
    H -.-> H2["Chequeos y excepciones"]:::todo
    J -.-> J2["Certificado PDF B2B"]:::todo
    K -.-> K2["Tableros y reporte SUI"]:::todo
    F -.-> F2["App del reciclador y ciudadano"]:::todo
    F -.-> F3["Recolecciones y rutas"]:::todo
```

Fuera del camino principal, sin empezar: solicitudes de recolección del ciudadano, motor de rutas, sincronización móvil sin conexión y módulo educativo (fases 3, 4 y parte de la 2 del backlog).

## 3. ¿A dónde llega este pesaje?

Una ECA recibe el material de quien lo traiga. Lo que cambia es si el pesaje llega a una Asociación. Se decide en el instante de pesar y queda guardado en `affiliation_status`.

```mermaid
flowchart TD
    classDef lnk fill:#d8f0e4,stroke:#0b7a56,color:#0b3d2b
    classDef oth fill:#eceff0,stroke:#5b665f,color:#1d2420

    S{"¿Quien entrega<br/>está registrado?"}
    S -- "No: vendedor no registrado" --> I["independent<br/>solo la ECA lo ve"]:::oth
    S -- Sí --> O{"¿Tiene asociación?"}
    O -- No --> I
    O -- Sí --> V{"¿Está verificado y su asociación<br/>tiene vínculo activo con esta ECA?"}
    V -- Sí --> L["linked<br/>la Asociación lo ve, valida y paga"]:::lnk
    V -- No --> U["unlinked_association<br/>solo la ECA lo ve"]:::oth
```

- Los tres casos **cuentan para el inventario, las compras y los reportes de la ECA**. Solo `linked` llega a una Asociación (pesajes, compras y estadísticas).
- Un reciclador **desactivado por Somos R** sí se rechaza: `400 recycler_inactive`.
- **Pregunta abierta de negocio:** si el pesaje de un vendedor fuera del vínculo cuenta para el reporte SUI de la ECA, de la asociación de origen o de ambas.

## 4. Quién hace qué, en una línea

| Actor | Lo que hace en este flujo |
|---|---|
| Solicitante | Pide el ingreso de su organización y corrige lo que Somos R observe |
| Somos R | Revisa y aprueba organizaciones, gestiona usuarios y catálogos, audita |
| Admin de Asociación | Decide vínculos, verifica recicladores, valida y paga lo de sus recicladores `linked` |
| Admin de ECA | Solicita vínculos, invita personal, paga pesajes y compras, vende, ve todo lo de sus bodegas |
| Báscula (ECA) | Identifica, pesa, valida y rechaza |
| Bodega (ECA) | Edita inventario y gestiona ventas |
| Reciclador | Entrega el material; todavía sin pantalla propia |
