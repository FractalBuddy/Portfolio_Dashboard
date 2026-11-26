# Cómo ampliar el universo público (registry.json)

Ampliar = **añadir filas** a `registry.json`. El código no cambia mientras no se pase de los techos de abajo. El registro manda: lo que no está en él no se
pide ni se publica, y una fila mal formada hace fallar la CI antes del merge (y, si llegara a `main`, el generador y el validador se niegan a correr: no se
publica nada a medias).

## 1. Formato de una fila

```json
{"symbol":"PM","kind":"yahoo","name":"Philip Morris International","currency":"USD","unit":"currency","tier":"warm","status":"active","fail_streak":0,"country":"US","lists":{"top50-mcap@2026-11-13":46}}
```

(Es la fila real de `PM` **después** de activarla; una fila nueva se escribe igual pero con `"status":"dormant"`, ver § 2.)

| Campo | Obligatorio | Regla (la hace cumplir `feed_v2_common.registry_problems`) |
|---|---|---|
| `symbol` | sí | Símbolo de **Yahoo**, regex `^[A-Za-z0-9.^=\-]{1,15}$`, sin punto inicial ni `..`; único sin distinguir mayúsculas. |
| `kind` | sí | `yahoo` (ficheros propios) o `crypto` (sale del lote de CoinGecko). |
| `name` | sí | 1-80 caracteres, sin `<`, `>` ni controles. Se publica. |
| `currency` | sí | Divisa de cotización **tal cual** la da Yahoo, 3-4 letras, distingue mayúsculas: `GBp` (peniques) ≠ `GBP` (libras); `ZAc`, `ILA`, `USd` son subunidades. |
| `unit` | sí | `currency` · `subunit` (obligatoria si y solo si la divisa es una subunidad: `GBp`/`GBX`, `ZAc`/`ZAC`, `ILA`/`ILa`, `USd`/`USX`) · `points` (índices `^…`) · `rate` (pares `…=X`). |
| `tier` | sí | `hot` (cada run + intradía), `warm` (~1 vez al día), `cold` (~1 vez a la semana). |
| `status` | sí | Una fila **nueva** entra `dormant` (no se pide ni se publica) y pasa a `active` tras la sonda. `invalid`/`delisted` no se escriben a mano: los pone el generador en `health.json`. |
| `fail_streak` | sí | `0` (el contador real vive en `health.json`). |
| `country` | no | ISO 3166-1 alfa-2 en mayúsculas (`US`, `SA`, `KR`, `CN`). No se publica. |
| `lists` | no | `{"<lista>@<AAAA-MM-DD de corte>": rango o null}`. `null` = miembro de la lista con rango no confirmado. No se publica. |

Cualquier otro campo es un error (a propósito: nada de ISIN, URL, ids de ficha ni datos del dueño en el registro público).

**Qué tier elegir**: el más barato que cumpla. `warm` para acciones y ETF de un dashboard (1 petición al día). `hot` solo si hace falta intradía o
refresco en cada run (≥ 2 peticiones por run). `cold` para lo que cambia poco o se mira poco (1 petición a la semana).

**Divisa y unidad**: compruébalas en la ficha de Yahoo del símbolo. En Londres (`.L`), Johannesburgo (`.JO`) y Tel Aviv (`.TA`) casi todo cotiza en
subunidad (`GBp`, `ZAc`, `ILA`) — pero no todo: algunos ETF de Londres cotizan en `USD` o `GBP`. Nunca se adivina: si la sonda no ve la divisa, no se activa.

**Varias líneas de la misma empresa**: una sola fila por empresa. Se usa la línea que la fuente de la lista trate como principal; si la empresa ya está en el
registro (aunque sea por otra línea o por un ADR), no se añade otra.

**Listas publicadas (F5)**: la anotación `lists` de una fila **no se publica**. Para publicar una lista en `index.json.lists` se añade en la **raíz** del registro
`"lists": {"<lista>@<AAAA-MM-DD>": {"source": "https://…", "as_of": "AAAA-MM-DD", "size": N, "symbols": [N símbolos, en orden de rango]}}`. El validador
(`feed_v2_common.list_problems`) rechaza el registro entero si la lista está incompleta (`len(symbols) != size`) o vacía, si falta la fuente o no es `https`, si
`as_of` no es la fecha del id, si un símbolo no está en el registro o se repite, o si la anotación de las filas no coincide (cada miembro lleva la clave en su
`lists`, ninguna fila ajena la lleva y un rango no nulo es su posición). Hoy **no hay ninguna lista en la raíz**: el top 50 del 13-nov-2026 solo está anotado en 7
filas y está incompleto (ver `auditorias/FIXES_F5_LISTAS_Y_SIMBOLOS.md`).

## 2. Pasos para añadir N símbolos

1. Añadir las filas en `dormant`, con `tier`, `currency` y `unit` decididos según lo de arriba. Si salen de una lista, anotar `lists` y dejar el documento de la
   lista en `docs/universo/` (fuentes con URL y fecha de consulta).
2. `python -m pytest -q` y `ruff check --select F scripts tests` en verde (el test del registro real valida todas las filas).
3. PR, revisión y merge. Al estar en `dormant`, **el merge no cambia ningún dato publicado** (solo aparecen en `index.json` con `status: "dormant"` y `as_of: null`).
4. **Sonda** (`.github/workflows/symbol-probe.yml`; Actions → «Symbol probe» → Run workflow, en `main`): por defecto prueba todas las filas `dormant`. No publica nada. Para cada símbolo da el veredicto
   y el nombre/bolsa/tipo/divisa que informa Yahoo:
   * `OK`: hay datos recientes, la divisa de Yahoo coincide con la declarada (misma escala) y el precio es plausible para la `unit`.
   * `UNIT`: Yahoo informa otra divisa o escala (p. ej. `GBP` frente a `GBp`): corregir la fila o descartarla.
   * `CURRENCY_MISSING`: Yahoo no informa la divisa: no se activa (el riesgo ×100 es justo este).
   * `EMPTY` / `FROZEN` / `PRICE_RANGE` / `HTTP_ERROR` / `NOT_TRIED`: sin datos, congelado, precio absurdo, fallo de red o no se llegó a probar (429, tope).
   **Antes de activar un OK, compara a ojo «nombre en el registro» con «nombre en Yahoo»**: la sonda no sabe si el ticker es la empresa correcta.
5. PR que cambia `"status":"dormant"` → `"status":"active"` de los OK. El siguiente run los baja (como mucho `BACKFILL_MAX_PER_RUN` = 80 descargas largas de
   10 años por run; el resto, en los runs siguientes) y los publica.
6. Tras 2-3 runs, mirar `health.json`: `run.yahoo_rate_limited` (debe seguir en 0), `problems.failed` y `problems.unit_mismatch` de los nuevos.

**Historial de activaciones**

| Fecha | Sonda | Activados | Pendientes |
|---|---|---|---|
| 2026-11-20 | «Symbol probe» en `main`, 17:21 UTC: 7 de 7 `OK`, nombre de Yahoo = empresa esperada, divisa = la declarada, última barra del 19 o 20-11 | `SPCX`, `2222.SR`, `000660.KS`, `601288.SS`, `1398.HK`, `601988.SS`, `PM` (top 50, `warm`; ver `docs/universo/TOP50_MCAP_2026-11-13.md`) | ninguna fila `dormant` en el registro |
| 2026-11-26 (F5) | **pendiente**: no lanzada todavía | — | `ENPH`, `ORSTED.CO` entran `dormant` (`warm`; divisa del sufijo, sin verificar en Yahoo). Ver `auditorias/FIXES_F5_LISTAS_Y_SIMBOLOS.md` |

## 3. Qué pasa si un símbolo muere después

No bloquea la publicación del resto (código: `feed_v2.Run.run_symbols/carry`; tests: `test_un_simbolo_que_falla_conserva_lo_anterior_marcado_stale_y_no_bloquea`,
`test_cinco_fallos_seguidos_dejan_el_simbolo_invalid_y_se_reintenta_cada_semana`; visto en real el 19-nov-2026 con `ROG.SW`):

* fallo puntual → se sirve el último dato marcado `stale` (como máximo 30 días); el histórico no se borra;
* 5 fallos seguidos → `invalid` en `health.json`/`index.json`, se reintenta una vez por semana y vuelve solo a `active` si Yahoo lo vuelve a servir;
* última barra de hace > 45 días → `delisted`;
* si fallan más de la mitad a la vez es Yahoo, no los símbolos: no se penaliza a nadie.

**Lo que sí bloquea la publicación entera** (por diseño del validador, contra un rescalado ×100): un salto de precio ≥ ×2 o ≤ ×0,5, un rescalado del histórico o
años cerrados reescritos en **un** símbolo — que es exactamente lo que provoca un **split**. Hoy se resuelve relanzando el workflow con
`SANITY_ALLOW_REGRESSION=true` tras comprobar que es un split. Con más acciones habrá más splits al año (ver el informe `FIXES_UNIVERSO_TOP50.md`).

## 4. Cómo se mide el coste (y cuánto cuesta hoy)

Fuentes de la medida: la línea `v2: … peticiones a Yahoo … bytes en total` del paso «Build feed v2» de cada run (log de Actions), `health.json` (`run.yahoo_requests`,
`run.duration_s`) y `manifest.json` (`total_bytes`). En local, sin red: `V2_MEASURE=1 pytest -s tests/test_feed_v2.py -k medicion` (registro real, Yahoo
simulado) y `python tests/bench_escala_v2.py run --n … --work … --out …` (registros sintéticos de N filas).

**Medido en real (runs 45 y 46 de «Publish feed v2», 19-nov-2026, registro de 242 filas, 224 de Yahoo activas):**

| Run | Símbolos que tocaban | Peticiones a Yahoo | Paso «Build feed v2» | Sitio | Ficheros | Artefacto (zip) |
|---|---|---|---|---|---|---|
| solo hot (02:48 UTC) | 49 | 99 | 57 s | 33,25 MB | 2.489 | 10,1 MB |
| hot + warm (16:47 UTC) | 203 | 255 | 118 s | 33,20 MB | 2.489 | 10,1 MB |

Hoy hay ~2 runs al día (cron de respaldo; el disparador externo no se ha visto en esa muestra). Coste marginal de un símbolo `warm`: **+1 petición al día**, ~150 KB
de sitio (10 años diarios) y ~10,4 ficheros; una sola vez, 1 descarga larga (10 años) al darlo de alta.

## 5. Techos (no pagar infraestructura; quedarse lejos de los límites gratuitos)

Regla del dueño: nada de pago y **avisar cuando una estimación pase del ~50 % de un límite**. Estimaciones para añadir N símbolos `warm` a lo medido el 19-nov (224 activos de Yahoo; desde la activación del 20-nov son **231**; con F5 el registro pasa de 249 a **251** filas (2 `dormant`), 63 % de `MAX_REGISTRY_SYMBOLS`):

| Límite | Valor | Hoy | +50 | +250 | Fuente del límite |
|---|---|---|---|---|---|
| Tamaño publicado de Pages | 1 GB | 33 MB (3 %) | ~41 MB (4 %) | ~70 MB (7 %) | docs.github.com «GitHub Pages limits» |
| Despliegue de Pages | 10 min | ~5 s | ~5 s | ~10 s | idem |
| Ancho de banda de Pages (blando) | 100 GB/mes | **no medido** (depende de cuánto lee el dashboard, no del run) | igual | igual | idem |
| Minutos de Actions (repo público, runner estándar) | gratis e ilimitados | — | — | — | docs.github.com «About billing for GitHub Actions» |
| `timeout-minutes` del job `fetch` | 25 min | ~3 min (12 %) | ~3,5 min (14 %) | ~5-6 min (≤ 24 %) | `publish-feed-v2.yml` |
| Presupuesto de Yahoo por run | 13 min | 118 s (15 %) | ~2,3 min (18 %) | ~4 min (31 %) | `feed_v2.V2_YAHOO_BUDGET_SECONDS` |
| `MAX_SITE_BYTES` (tope propio) | 120 MB | 28 % | ~34 % | **~58 %** ⚠ | `feed_v2_common` |
| `MAX_SITE_FILES` (tope propio) | 12.000 | 21 % | ~25 % | ~42 % | idem |
| `MAX_BYTES["manifest"]` (tope propio) | 600 KB | ~285 KB (48 %) | ~364 KB (**61 %** ⚠) | ~575-605 KB (**96-101 %**, se rompe) | idem; banco: 605 KB con 500 filas |
| `MAX_REGISTRY_SYMBOLS` (tope propio) | 400 filas | 249 (62 %) ⚠ | ~299 (**75 %** ⚠) | ~499 (**se rompe**) | idem |
| Yahoo (sin SLA ni cuota publicada) | desconocido | 255 pet./run diario; 0 × 429 en los runs vistos | ~305 | ~505 | — |

⚠ = pasa del 50 % de su límite. **Todos los que lo pasan son topes propios del repo**, no límites de GitHub (que quedan por debajo del 10 %). Los topes propios son
alarmas contra un generador desbocado: subirlos es una decisión del dueño, no un efecto de añadir filas.

**Techo propuesto (sin tocar código):** hasta **~350 filas** en el registro (≤ 87 % de `MAX_REGISTRY_SYMBOLS`, manifest ≈ 420 KB ≈ 70 % de su tope) y
**≤ 450 peticiones a Yahoo en el run diario** (≈ 3,5 min, 27 % del presupuesto de 13 min a ~0,46 s por petición medido). Crecer en tandas de **≤ 80 símbolos**
(un cupo de descargas largas), con 2-3 runs de observación entre tandas (`yahoo_rate_limited = 0`).

**Para ir a +250 (≈ 500 filas)** hace falta un cambio de código decidido por el dueño, ya estudiado en `auditorias/FIXES_FEED_REINTENTO_ESCALA.md`:
`MAX_REGISTRY_SYMBOLS` 400 → 600 y `MAX_BYTES["manifest"]` 600 KB → 900 KB (con 500 filas: sitio 80 MB, 5.207 ficheros, index 83 KB, health 60 KB caben). A 1.000
filas el sitio (160 MB) ya no cabe en `MAX_SITE_BYTES` y habría que recortar histórico de warm/cold o partir `index.json`.
