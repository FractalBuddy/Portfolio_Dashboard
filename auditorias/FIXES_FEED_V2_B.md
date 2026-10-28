# Feed v2 · Sesión B: histórico largo del proxy en el feed privado — `feat/personal-history`

Rama desde `main`. No se ha publicado ni desplegado nada, no se ha lanzado ningún workflow y no se ha tocado v1, `YOUR_FUNDS`, el SQL ni los workflows (`git diff main -- .github` vacío).
Sin red real: los tests usan un Supabase simulado local y fuentes inyectadas; los tamaños de abajo son de series sintéticas (recorrido aleatorio con cierres a 6 decimales, como los que deja `split_bars`).

## Qué cambió
1. **`scripts/personal_v2.py`**
   * Los símbolos que son `proxy_symbol` de algún fondo del usuario se descargan con `period="max"` y se publican con todo lo que haya hasta `PROXY_YEARS = 25` (≤ `MAX_PROXY_BARS = 6600` cierres),
     solo `d`/`c`, con `currency`/`unit`/`price_unit_divisor` explícitos. Los símbolos extra siguen con 2 años (`period="2y"`, ≤ 600).
   * `Fetchers.series(sym, period)` recibe el periodo (`"max"`/`"2y"`). Caché por run: un símbolo se descarga **una vez** aunque lo sigan varios usuarios (un `max` sirve también para un `2y`).
   * **Proxy en el universo público**: ahora se incluye **siempre** en `series` y nunca figura en `public` (ver decisión 2).
   * `series_entry`: si la descarga trae un hueco > 15 días entre dos cierres, se publica solo el tramo posterior al último hueco (mejor una serie más corta pero buena que una con un salto).
   * `fit_payload` reescrito (decisión 3). Orden de `series`: proxies primero (en el orden de los fondos), luego extra.
   * Nuevos avisos (solo contadores): `proxy_fallo` y `proxy_sin_divisa_o_datos` (los de siempre, `serie_fallo` y `serie_sin_divisa_o_datos`, quedan para los extra). La línea de resumen añade nº de barras, bytes del mayor
     payload y nº de descargas.
2. **Validador (`payload_problems`)**: además de lo anterior (esquema estricto, textos sin `<>`, `d`/`c` del mismo largo, `as_of == d[-1]`, `price == c[-1]`, finitos y `c > 0`, tope de bytes) ahora exige:
   fechas **estrictamente** crecientes, sin huecos > 15 días, largo máximo según clase (proxy 6600, extra 600), `currency` obligatoria, coherencia divisa/unidad/divisor (`subunit` ⇒ divisa en subunidad y divisor 100;
   `currency` con `GBp` ⇒ rechazado; `points` ⇒ `^…`; `rate` ⇒ `…=X`), sin símbolos repetidos y sin símbolo a la vez en `series` y `public`.
3. **`docs/CONTRATO_FEED_V2.md` §5** y ejemplo regenerado; **README** (sección de datos propios). No existe ningún `CHECKPOINT` en el repo: no hay nada que actualizar ahí.
4. **Tests** (`tests/test_personal_v2.py`, +37 casos; `test_contract_docs_v2.py` adaptado a la nueva firma). Controles negativos: serie truncada / columnas desiguales, fechas desordenadas y repetidas, hueco enorme, cierre 0 / negativo / NaN / inf / `None` /
   texto / bool, proxy sin divisa (ausente, vacía, mal formada), divisa-unidad incoherentes (7 variantes), serie demasiado larga por clase, símbolo duplicado, payload demasiado grande (validador, `fit_payload` y flujo completo: no se envía nada).

## Decisiones (y por qué)
1. **Descarga completa cada ejecución, no incremental.** Un incremental necesitaría leer el payload anterior (no hay RPC de lectura y no he tocado el SQL) o guardar estado fuera de Supabase (caché de Actions = estado
   nuevo que mantener, y los logs/artefactos son públicos). Coste real: **el mismo nº de peticiones que antes** (una por proxy; antes ya se pedía `2y` para los proxies fuera del público) pero con ~4-6× más datos por
   respuesta (≈ 4.300-6.300 filas frente a ≈ 500). Es además lo que ya hace v1 (`period="max"` por fondo en cada run). Y una descarga completa es la única forma de que un split/ajuste retroactivo no deje una serie remendada.
2. **Proxy en el universo público ⇒ se incluye igualmente en lo propio.** Comprobado en el código: el público **no** tiene todos los años; `HISTORY_YEARS = 10` poda `s/<SYM>/d/<AAAA>.json` a 10 años naturales (contrato §3). Con v1 la
   analítica usa `max` (~17 años), así que leerlo del público perdería los años 11-17 (rentabilidad a >10 años, gráfico «máx.»). Alternativas descartadas: coser público (10 años) + lo propio (resto) = dos fuentes con
   posibles rebases distintos y lógica de unión en el cliente; subir `HISTORY_YEARS` = engordar Pages para todos (+7 ficheros/símbolo) por un uso privado. El «duplicado» son ≤ 10 años de un solo símbolo (~60-100 KB)
   y solo si el proxy está en el registro. **Hoy no ocurre:** ninguno de los 2 proxies de `YOUR_FUNDS` está en `registry.json` (lo comprobé importando `fetch_market_data`; no cito los símbolos porque un test prohíbe datos del dueño en el repo).
3. **`fit_payload`**: primero los extra (260 → 60 → 1 cierres); después los proxies, todos al mismo nivel (2600 ≈ 10 a → 1300 ≈ 5 a → 780 ≈ 3 a → 260 → 60 → 1); luego sin series; si aún no cabe, `None` y ese usuario no se publica. Determinista
   (mismo input ⇒ mismos bytes, probado) y no toca nada si ya cabe. Antes recortaba **todas** las series a la vez y sin distinguir proxies.

## Medidas
Tamaño del payload (`c.dumps`, ASCII, bytes; ≈ 23 B por cierre: 13 de fecha + ~9-10 de cierre, más ~1 KB por fondo con facts):

| Caso | Sin recortar | Tras `fit_payload` | Proxy tras recorte |
|---|---:|---:|---|
| **Tus 2 fondos, ~17 años (4.300 cierres c/u)** | **199.709** | 199.709 (no recorta) | 4.300 |
| 2 fondos, 25 años (6.300) | 294.798 | 294.798 | 6.300 |
| 2 fondos 17 a + 10 símbolos extra (2 años) | 318.308 | 318.308 | 4.300 |
| **Peor caso: 20 fondos/20 proxies distintos + 20 extra (40 series), 17 a** | 2.237.183 | 626.413 | **1.300 (~5 años)**; extra a 1 |
| Peor caso con 25 años | 3.179.774 | 633.893 | 1.300; extra a 1 |
| 20 proxies de 10 años + 20 extra | 1.397.106 | 610.247 | 1.300; extra a 1 |

Con 2 fondos, ~20 % del tope. **1 MB no basta para el caso máximo sin recortar**: caben ≈ 43.000 cierres en total (~7 proxies de 25 años o ~10 de 17). El recorte conserva a todos los proxies, pero a ~5 años: volatilidad 1/3/5 años y gráfico de 5 años sí; rentabilidad
a 10 años y años naturales antiguos no. No he cambiado límites ni SQL. Si algún día hace falta el caso máximo íntegro, opciones (tú decides): (a) subir `MAX_PAYLOAD_BYTES` a ~3,5 MB, **tras comprobar el límite real de Supabase/PostgREST
para el cuerpo de una RPC** (no lo he podido contrastar); (b) redondear cierres a 4 decimales en lo propio (≈ -12 %; cambia el contrato de precisión); (c) una fila/serie por símbolo en vez de un jsonb por usuario (SQL nuevo, y de paso
no se repite un proxy compartido entre usuarios).

Peticiones y tiempo del job (sin red real, solo estimación):
* Peticiones a Yahoo: **1 por símbolo distinto** y run (los proxies compartidos entre usuarios se descargan una vez). Con tus 2 fondos: 2 (proxies) + los extra fuera del público; igual que antes. Peor caso: ≤ 40 por usuario × 20 usuarios, con la caché del run
  y el pacing de `fetch_market_data` (0,15-0,35 s entre peticiones, pausa de 5 s cada 40): del orden de un minuto sumando la descarga de respuestas de ~100-200 KB. El job `personal` tiene `timeout-minutes: 15`, `continue-on-error: true`.
* CPU local de procesar 6.300 filas (`bars_from_df` 0,13 s, recorte 3 ms, validación 18 ms): despreciable.
* Cupo de Yahoo: sin cambio en nº de peticiones; no cuenta contra el presupuesto del feed público (job aparte, proceso aparte).
* **No he medido el tiempo real de Yahoo**; mira la duración del paso `Build personal payloads` en el primer run.

## Riesgos
* **Un fallo de Yahoo en el proxy en un run deja ese run sin historia larga**: `publish_personal_feed` sobrescribe el payload anterior y no hay incremental. El NAV sí se publica. La app debe tolerar `series` sin el proxy (caer a lo público o a v1).
  El resumen del log lo cuenta (`proxy_fallo`). Mitigación posible (no hecha, requiere decisión): no publicar el payload de un usuario si falta un proxy que antes estaba (necesita leer el anterior).
* **Dividendos**: la serie es sin ajustar (`auto_adjust=False`), como el público, mientras v1 usa el `Close` por defecto de yfinance (ajustado). Para un ETF **de acumulación** da lo mismo (sin dividendos); si algún proxy fuera de
  distribución, la rentabilidad histórica saldría menor que en v1. No he podido verificar en Yahoo si tus dos proxies son de acumulación: compruébalo, o dime si prefieres `auto_adjust=True` solo para los proxies.
* El hueco > 15 días recorta la historia al tramo posterior: si Yahoo tuviera un hueco real antiguo, se perdería lo anterior (visible en `d[0]`; el cliente no debe suponer 25 años).
* **Fallo previo en `main`** (corregido aquí): `test_flujo_completo_lee_la_lista_y_publica_por_usuario` comparaba un payload generado con el reloj real contra una fecha fija (2026-10-26) y fallaba desde el 27-10; ahora usa la fecha real.
* Privacidad intacta: los logs siguen siendo solo contadores (los tests de log/ISIN/símbolos pasan); sin cambios de secretos: solo clave publicable + `PERSONAL_FEED_TOKEN`; el workflow `publish-feed-v2` no se ha tocado (warning, no failure).

## Verificación
`pytest` completo, `ruff check --select F scripts tests`, `py_compile` y `sanity_check_v2` (sus tests y la validación del sitio sintético): resultado: **1160 passed** (la suite completa, incluidos `test_sanity_check_v2`, `test_feed_v2*`, `test_workflows` y `test_contract_docs_v2`), `ruff` y `py_compile` sin errores. Antes de tocar nada `main` daba 1122 passed + 1 failed (el fallo de fecha de abajo). Lo único sin probar es una ejecución real en Actions contra Yahoo/Supabase.

## Pasos manuales para ti
1. Revisar y mergear la rama (no he abierto PR). Los cambios son compatibles hacia atrás con la app actual salvo que antes asumía que un proxy público **no** venía en `series`: ahora viene (y no está en `public`).
2. Comprobar que tus 2 proxies son de acumulación (o decidir `auto_adjust=True` para ellos).
3. Tras el primer run, mirar en el log del paso `Build personal payloads`: `N series, M barras, X bytes máx.` (esperable ≈ 200 KB y ≈ 8.600 barras con tus 2 fondos) y que no haya `proxy_fallo` / `proxy_sin_divisa_o_datos`.
4. Decidir si el tope de 1 MB te vale (con 2 fondos sobra) o si quieres alguna de las opciones (a)-(c) para el caso máximo.
5. La sesión de la app debe leer `series[]` del proxy (histórico largo, ya sin ajustar), reescalarlo para que el último cierre coincida con el NAV y no asumir 25 años (mirar `d[0]`).
