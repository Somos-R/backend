# Decisión: paginación por offset o por cursor

**Decisión (2026-09-29): se mantiene `limit`/`offset` con `total`.** No hace falta paginación por cursor todavía. Se revisa cuando una tabla de listado supere ~1 millón de filas, o cuando el frontend necesite scroll infinito o exportaciones completas.

## Medición

`GET /weighings` con 200.000 pesajes (50 recicladores, 7 materiales, 4 estados), PostgreSQL 15 local con los índices reales, mediana de 7 ejecuciones, página de 20 filas:

| Consulta | Tiempo |
|---|---|
| Offset 0 | 0,7 ms |
| Offset 20.000 | 2,5 ms |
| Offset 100.000 | 10 ms |
| Offset 199.980 (la última página) | 20 ms |
| Un reciclador + estado, offset 0 a 2.000 (usa `ix_weighings_recycler_estado_fecha`) | 0,5 ms |
| `count(*)` sin filtro (se ejecuta en cada listado para calcular `total`) | 10 ms |
| `count(*)` de un reciclador | 1,7 ms |
| Cursor `(fecha, id)`, en cualquier posición | 0,7 ms |

## Lectura

- El offset se degrada de forma lineal (~1 ms cada 10.000 filas saltadas), pero incluso en el peor caso, saltar 200.000 filas, son 20 ms. Nadie navega hasta la última página de una lista sin filtrar, y las consultas filtradas (las habituales: por reciclador, por estado) están planas gracias al índice.
- El cursor sería constante, pero exige cambiar el contrato de la API (`next_cursor` en vez de `offset`), impide saltar a una página arbitraria y complica los filtros combinados con el orden. Hoy no se justifica ese costo.
- El costo fijo más grande es el `count(*)` sin filtro (10 ms con 200.000 filas; crece linealmente). Si llega a molestar, las opciones son omitir el `total` cuando no hay filtros, o usar una estimación (`reltuples`).

## Si se adopta el cursor más adelante

Orden estable `ORDER BY fecha DESC, id`, cursor opaco con el `(fecha, id)` de la última fila, y consulta `WHERE (fecha, id) < (:fecha, :id)`. Los índices que ya existen (`idx_weighings_fecha` y el compuesto por reciclador/estado/fecha) la sirven sin cambios. Los listados ya se ordenan con `id` como desempate, de modo que la migración no cambiaría el orden que ven los usuarios.
