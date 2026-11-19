# FIXES — Universo top 50 por capitalización (feed público v2)

Rama `claude/universo-top50` desde `main` (`91144cd`). Cuatro commits: (1) metadatos opcionales del registro + 7 filas nuevas en `dormant` + documento de la lista,
(2) sonda manual de símbolos, (3) procedimiento «cómo ampliar» con coste medido, (4) este informe. **Nada desplegado ni fusionado.** El merge no cambia ningún dato
publicado: las 7 filas nuevas entran en `dormant` (no se piden a Yahoo ni tienen ficheros; solo aparecen en `index.json` con `status: "dormant"`, `as_of: null`).

Entorno de las pruebas: Python 3.13 local (la CI usa 3.14), dependencias de `requirements.txt` y `requirements-dev.txt` instaladas con `--require-hashes`. **Sin red**:
Yahoo, Pages, Wikipedia, CompaniesMarketCap y el resto de webs devuelven 403/DNS desde aquí; solo funcionaron el buscador web y la API de GitHub (logs de Actions).

---

## 1. Comandos y salidas en bruto

### 1.1 Línea base (antes de cambiar nada)
```
$ python -m pytest -q -p no:cacheprovider
2115 passed, 1 warning in 190.64s (0:03:10)
$ ruff check --select F scripts tests
All checks passed!
```

### 1.2 Diagnóstico: runs reales de «Publish feed v2» (logs de Actions vía API, 19-nov-2026)
Run 45 (02:48 UTC, solo hot), paso «Build feed v2» 02:49:19 → 02:50:16 (57 s):
```
v2: 242 en el registro, 49 tocaban, 49 actualizados, 0 fallidos, 0 aplazados, 0 con descarga larga; 99 peticiones a Yahoo (98 llamadas); 33245148 bytes en total
With the provided path, there will be 2489 files uploaded
Artifact site-v2 has been successfully uploaded! Final size is 10131846 bytes.
```
Run 46 (16:47 UTC, hot + warm), paso «Build feed v2» 16:48:50 → 16:50:48 (118 s); job `fetch` 16:48:01 → 16:50:56; descarga del sitio anterior 20 s:
```
HTTP Error 404: {"quoteSummary":{"result":null,"error":{"code":"Not Found","description":"Quote not found for symbol: ROG.SW"}}}
$ROG.SW: No data found, symbol may be delisted
  ! ROG.SW: fail (empty)
  ! cripto del registro que no aparece en el lote de CoinGecko (dormant, not_listed): ['TON']
v2: 242 en el registro, 203 tocaban, 202 actualizados, 1 fallidos, 0 aplazados, 1 con descarga larga; 255 peticiones a Yahoo (252 llamadas); 33199271 bytes en total
With the provided path, there will be 2489 files uploaded
```
…y ese mismo run **publicó** (job `publish`: «Sanity-check feed v2» en verde, despliegue a Pages tras un reintento por «Fetching artifact metadata failed», `verify` y
`heartbeat` en verde). Es la prueba en real de que un símbolo caído no bloquea.

### 1.3 Experimento aislado (no versionado): símbolo muerto frente a split, contra el validador real
```
REBASED: ['AAPL']
ERRORES (3):
  - años cerrados reescritos (9): s/AAPL/d/2017.json, s/AAPL/d/2018.json, s/AAPL/d/2019.json, s/AAPL/d/2020.json
  - salto de nivel (precio nuevo / anterior fuera de (0.5, 2.0)) en 1 símbolos: AAPL x0.499
  - continuidad: la mediana de cierres solapados nuevo/anterior se aleja de 1 en 1 símbolos (rescalado): AAPL x0.5
CON allow_regression ERRORES: []
  ! AAPL: fail (http_error)
MUERTO: errores [] | avisos: ['1 de 7 símbolos con precio antiguo (stale): AAPL', '1 de 9 símbolos activos del registro sin publicar (más del 10 %): AAPL']
2 passed in 1.74s
```

### 1.4 Rojo del registro (test escrito antes del código)
```
$ python -m pytest -q tests/test_universo_top50.py
E   AttributeError: module 'feed_v2_common' has no attribute 'MAX_LISTS_PER_SYMBOL'
1 error in 0.08s
```
Tras el código (sin las filas): `6 failed, 37 passed` (las 6 dependían de las filas). Tras las filas: 3 fallos (uno de mi test nuevo y los dos tests antiguos del §4).

### 1.5 Controles negativos por mutación (cada mutación aplicada, probada y revertida; `git diff` confirma que el código quedó como estaba)
```
== M1: cualquier campo extra se acepta            2 failed, 121 passed
== M2: country sin validar                         5 failed, 38 passed
== M3: lists siempre válido                        16 failed, 27 passed
== M4: se acepta GBp declarado como currency       4 failed, 39 passed
== M5: check_unit desactivado (feed_v2.py)         1 failed, 1 passed, 43 deselected
== N1: sonda sin exigir divisa                     1 failed, 15 passed
== N2: sonda sin check_unit                        2 failed, 14 passed
== N3: sonda sin pausa entre peticiones            1 failed, 15 passed
== N4: workflow de la sonda con un secreto         2 failed, 14 passed
== N5: workflow de la sonda sin guarda de main     1 failed, 15 passed
```

### 1.6 Medición con el registro real (Yahoo simulado) y banco de escala
```
$ V2_MEASURE=1 pytest -s tests/test_feed_v2.py -k medicion
  run                      llamadas  diarias  intradía  largas   bytes totales   ficheros
  alta 1                        129       80        49      80        13230486        935
  alta 2                        178      129        49      80        26450922       1815
  alta 3                        162      113        49      64        37018960       2519
  solo hot (+6 h)                98       49        49       0        37018956       2519
  hot + warm (+21 h)            251      202        49       0        37018959       2519
  hot (+6 h)                     98       49        49       0        37018956       2519
  total del sitio: 37018956 bytes (14502642 comprimido con zlib -6, fichero a fichero) en 2518 ficheros (+ manifest.json de 285126 bytes); símbolos hot 49, warm 160, cold 22

$ python tests/bench_escala_v2.py run --n 250 300 500 --work … --out …
n=250 | gen2 llamadas 273 (due 215) | sitio 40.1 MB, 2616 ficheros | manifest 303907 B | index 41480 B | health 30056 B | topes rotos [] | errores val2 0
n=300 | gen2 llamadas 311 (due 253) | sitio 48.1 MB, 3133 ficheros | manifest 364027 B | index 49810 B | health 36047 B | topes rotos [] | errores val2 0
n=500 | gen2 llamadas 519 (due 422) | sitio 80.0 MB, 5207 ficheros | manifest 605067 B | index 83028 B | health 59594 B
        | topes rotos ['MAX_REGISTRY_SYMBOLS (500 > 400)', 'MAX_BYTES[manifest] (605067 > 600000)'] | errores val2 3
```
(La medición cuenta las 7 `dormant` en «warm 160» pero no las pide: 251 llamadas = 202 diarias + 49 intradía, igual que sin ellas.)

### 1.7 Validador por su CLI sobre un sitio generado con el registro real (Yahoo y CoinGecko simulados)
```
$ sanity_check_v2.py --site <b> --registry registry.json
::warning::16 símbolos sin publicar en este run (detalle en health.json): ADA (not_listed), AVAX (not_listed), …
::warning::criptos del registro que no aparecen en el lote de CoinGecko (dormant, not_listed): ADA, AVAX, …
::warning::sin sitio anterior: primer run, solo reglas absolutas
OK v2: 2519 ficheros, 3 avisos (sin comparación con sitio anterior)
[exit 0]
$ sanity_check_v2.py --site <b> --registry registry.json --prev <a>      (EXPECT_PREV=true)
::warning::16 símbolos sin publicar en este run (detalle en health.json): ADA (not_listed), …
OK v2: 2519 ficheros, 2 avisos
[exit 0]
```
Los avisos de cripto son del CoinGecko simulado (30 monedas inventadas), no del cambio.

### 1.8 Final
```
$ python -m py_compile scripts/*.py tests/*.py
py_compile OK
$ ruff check --select F scripts tests
All checks passed!
$ python -m pytest -q -p no:cacheprovider
2180 passed, 1 warning in 189.08s (0:03:09)
```
`scripts/sanity_check.py` (validador del feed v1) no se ha tocado; sus tests están dentro de las 2180.

### 1.9 Barrido de privacidad del diff (`git diff origin/main...HEAD`, 946 líneas añadidas)
```
patrón [\b[A-Z]{2}[A-Z0-9]{9}[0-9]\b] (ISIN): 0     patrón [supabase\.co]: 0        patrón [sb_(publishable|secret)_]: 0
patrón [service_role]: 0                             patrón [eyJ…] (JWT): 0          patrón [ghp_|github_pat_]: 0
patrón [hc-ping\.com]: 0                             patrón [/products/[0-9]] (ficha): 0
patrón [@gmail|correo del dueño]: 1  → es la cadena "@gmail" dentro de la lista de lo PROHIBIDO de un test
patrón [secrets\.]: 1                → es `assert "secrets." not in text` en el test del workflow
patrón [Co-Authored|Generated with|Claude]: 0
```
Los commits llevan autor `Alex <65519527+FractalBuddy@users.noreply.github.com>` y ninguna atribución.

---

## 2. Diagnóstico (paso 1)

**(a) Un símbolo que falla o desaparece NO bloquea la publicación del resto. Ya estaba resuelto**; la regla del diseño original («dos desaparecidos paran la
publicación») ya no es así. Evidencia:
* Código: `feed_v2.Run.run_symbols` captura cualquier fallo por símbolo; `carry()` sirve el último `latest.json` marcado `stale` (máx. `STALE_CARRY_MAX_DAYS` = 30 días) y
  copia sus años de histórico (nunca se borran por un fallo); `fail_streak` ≥ `FAIL_STREAK_INVALID` (5) → `invalid` (reintento semanal; vuelve solo); última barra > 45 días
  → `delisted`; > 50 % de fallos con ≥ 10 intentos = caída de Yahoo y no se penaliza a nadie.
* Validador: un símbolo sin publicar es un `::warning::`; solo es alerta (Healthchecks) si pasan del 10 % de los activos; nunca error. La regla «desaparecen símbolos» de
  `check_continuity` solo cuenta los que estaban publicados, siguen `active` y pierden su `latest.json`, con tolerancia `max(2, 3 % de los publicados)` (hoy 6); un
  símbolo que falla conserva su `latest` `stale` y pasa a `invalid` (que no cuenta) mucho antes de los 30 días.
* Tests ya existentes: `test_un_simbolo_que_falla_conserva_lo_anterior_marcado_stale_y_no_bloquea`, `test_cinco_fallos_seguidos_dejan_el_simbolo_invalid_y_se_reintenta_cada_semana`,
  `test_el_stale_caduca_a_los_30_dias_y_el_historico_se_conserva`, `test_desaparecen_simbolos_activos_bloquea_pero_los_muertos_no_cuentan`, `test_desaparece_uno_o_dos_solo_avisa`,
  `test_e2e_un_simbolo_que_falla_no_avisa_y_seis_de_cuarenta_si`.
* Real: `ROG.SW` falló en el run 46 y el sitio se publicó (§1.2). Experimento §1.3: muerto = 0 errores.

Por eso el paso 4 (robustez) **no ha requerido cambios de código**.

**Riesgo residual que sí bloquea todo (no es un símbolo caído, pero crece con el universo): un split.** El generador lo detecta y rebaja la serie entera
(`problems.rebased`), pero el validador lo ve como salto ×0,5, rescalado y años cerrados reescritos → 3 errores → **no se publica nada** hasta relanzar con
`SANITY_ALLOW_REGRESSION=true` (§1.3). Con ~230 acciones/ETF hay varios splits al año. **No lo he cambiado** (ver decisión D5).

**(b) Peticiones y tamaño (medido en real, §1.2):** run solo hot = 99 peticiones; run hot + warm = 255 peticiones; sitio 33,2 MB, 2.489 ficheros, artefacto 10,1 MB; paso
de construcción 57-118 s; ~2 runs al día observados. Un símbolo `warm` más = +1 petición en el run diario, ~150 KB y ~10,4 ficheros; una sola vez, 1 descarga de 10 años.
+N: ver §5 de `docs/AMPLIAR_UNIVERSO.md` y el resumen del §6 de este informe.

**(c) Niveles** (`feed_v2_common.TIER_MIN_INTERVAL_HOURS`): `hot` cada run (≥ 45 min entre refrescos) + intradía de 5 min (`i.json`) → 2 peticiones por run;
`warm` ≥ 20 h (~1 vez al día) → 1 petición; `cold` ≥ 6 días → 1 petición a la semana. Histórico: los tres conservan **10 años naturales** diarios (`HISTORY_YEARS`); la
descarga incremental es de 1 mes (más si hay hueco); el alta baja 10 años con un cupo de 80 por run (`BACKFILL_MAX_PER_RUN`). Hoy: 49 hot, 153 warm, 22 cold (Yahoo) + 18 cripto.

**(d) Acciones del registro y cuáles del top 50 ya estaban:** el registro tenía 242 filas: 24 índices, 22 pares de divisas, 17 futuros de materias primas, 106 acciones (incluidos 3 ADR:
`ASML`, `TSM`, `NVO`), 55 ETF/ETC y 18 cripto (contado por bloques del fichero en `main`). Del top 50 (corte 13-nov, sin Anthropic) **ya estaban 43**: todas las estadounidenses identificadas, TSMC (`2330.TW` y `TSM`),
Samsung, Tencent, ASML (ADR), Roche (`ROG.SW`). **Faltaban 7**, que son las añadidas. Detalle en `docs/universo/TOP50_MCAP_2026-11-13.md`.

**Dónde está el registro y quién lo lee:** `registry.json` en la raíz; lo cargan y validan `feed_v2_common.load_registry` (generador `feed_v2.py`, validador
`sanity_check_v2.py` y `personal_v2.py`, que lo usa para no repetir en los datos propios lo que ya es público). `fetch_market_data.py` **no lee el registro**: es el feed v1
con sus listas fijas; el v2 solo reutiliza de él las funciones de red (ritmo y 429 de Yahoo, presupuesto, CoinGecko, FRED, RSS).

---

## 3. Qué demuestra cada prueba y qué NO

| Prueba | Demuestra | NO demuestra |
|---|---|---|
| `test_universo_top50.py` (46) | El validador acepta `country`/`lists` bien formados y rechaza 21 formas malas; un campo desconocido (isin, url…) sigue rechazado; el registro real + 1 fila duplicada (otra caja) o con símbolo inválido falla; sin `currency` o sin `unit` el generador y el validador por CLI salen con error y **no se escribe ningún sitio**; divisa/unidad incoherentes (GBp/currency, GBP/subunit, ZAc, USd, vacías) se rechazan; las 7 filas tienen la divisa/unidad/país esperados, `warm`, `dormant` y la lista; las filas antiguas no llevan metadatos y siguen `active`; extremo a extremo con las 7 activadas: si Yahoo informa USD para Aramco, Aramco no se publica (queda en `unit_mismatch`) y las otras 6 sí, con divisor 1 y sin metadatos en el feed, y el validador acepta el sitio; en `dormant` no se piden a Yahoo; la fila de ejemplo del procedimiento es válida e idéntica a la del registro. | Que Yahoo sirva esos símbolos (Yahoo simulado). Que el rango de la lista sea correcto (sale de fragmentos de búsqueda). |
| `test_symbol_probe.py` (16) | Cada veredicto (ok, unit, currency_missing, empty, frozen, http_error, not_tried); GBp/GBX aceptados y GBp frente a GBP rechazado; índices/pares sin divisa pasan como en el generador; ≥ 1 s entre peticiones; 429 → parada limpia; 3 fallos de red → parada; tope de símbolos; texto de Yahoo saneado para Markdown; selección por defecto = las `dormant`; `--symbols` solo admite símbolos del registro; códigos de salida 0/1/2; el workflow es solo manual, solo `main`, solo lectura, sin secretos ni despliegue, inputs por entorno, artefacto = el JSON del script. | Cómo responde el Yahoo real (formato de `history_metadata`, nombres, divisas). El ritmo real frente al límite de Yahoo. |
| Tests antiguos modificados (2, ver §4) | Siguen comprobando lo mismo para las filas activas; además, que las `dormant` no tienen ficheros. | — |
| Mutaciones M1-M5, N1-N5 | Que esas pruebas detectan la regresión concreta que pretenden vigilar. | Otras regresiones no probadas por mutación. |
| Banco y medición (§1.6) | Tamaños, ficheros y topes con series sintéticas (6 decimales: cota superior del tamaño). | El tiempo real de un run (Yahoo simulado responde al instante). |

**Qué prueba podría repetir el mismo error que el código:** la sonda usa `f2.check_unit` y `f2.split_bars` del propio generador: si esas funciones tuvieran un error de
criterio, la sonda daría «OK» por la misma razón. Lo único independiente que añade es exigir la divisa y el nombre de Yahoo para la revisión humana.

**Qué tendría que cumplirse para que el cambio fuese incorrecto:** que alguno de los 7 símbolos no exista en Yahoo con ese ticker o cotice en otra divisa (lo detecta la sonda
antes de activar); que Yahoo sirva para un ticker **otra empresa** con la misma divisa (solo lo detecta la comparación de nombres a ojo); que la lista del 13-nov tenga
miembros no identificados que falten en el registro (ver §5).

---

## 4. Decisiones y supuestos (con la postura contraria; decide Alex)

**D1. Las 7 filas entran en `dormant`, no en `active`.** Así el merge no cambia datos publicados y se activan solo tras la sonda (un cambio de una palabra por fila).
*Contraria:* entrar `active` ahorra un PR: un símbolo malo no bloquea (probado), y una divisa distinta tampoco se publica. Pero un ticker que exista con la misma divisa y sea
**otra empresa** se publicaría sin que nadie lo mire; y no se podría verificar antes del merge (un `workflow_dispatch` solo se puede lanzar si el workflow ya está en `main`).
**Efecto colateral a conocer:** `personal_v2.py` trata como «público» todo símbolo del registro, también los `dormant`. Si alguno de los 7 estuviera en la lista personal de
símbolos extra, durante la ventana `dormant` dejaría de ir en la serie personal y el público aún no lo tendría. No he tocado `personal_v2.py` (es lo propio del dueño);
arreglo de una línea propuesto: `public = {… for e in registry if e["status"] == "active"}`.

**D2. Tier `warm` para las 7.** Es el más barato que da precio diario (1 petición al día); `hot` doblaría el coste por run y añadiría intradía; `cold` (semanal) es poco para
las mayores empresas del mundo. *Contraria:* `hot` para SpaceX/Aramco por estar en el top 10 (el dashboard las vería con intradía).

**D3. Línea de cotización = la que usa la fuente del ranking (CompaniesMarketCap).** ICBC → `1398.HK` (HKD); Agricultural Bank of China → `601288.SS` (CNY); Bank of China →
`601988.SS` (CNY). Regla objetiva y trazable, aunque mezcla H y A. *Contraria:* usar siempre la línea H de Hong Kong (HKD), que es la que puede comprar un inversor desde
España y es coherente con `0700.HK`/`9988.HK` ya en el registro; o siempre la A (la de mayor capitalización).

**D4. Anthropic fuera.** A 13-nov no cotizaba (la tabla usa su valoración de OPV, no capitalización); además, el primer día Yahoo no tiene las 2 barras que exige el
generador. Con ella fuera entra Applied Materials (ya en el registro). *Contraria:* la fuente la incluye y estará en cualquier top 50 de diciembre.

**D5. No toco el bloqueo por split** (riesgo del §2). Arreglo posible: que el validador excuse salto/rescalado/años reescritos de los símbolos que el generador declara en
`problems.rebased`, si el factor es «de split» (½, ⅓, 2, 10…). *Contraria (la que me hace no aplicarlo):* el validador trata la salida de `fetch` como no fiable; si se
fía de `health.json` para excusar, un fetch comprometido podría colar un ×100 declarándolo «rebased». Hace falta diseño (p. ej. comprobar que **toda** la serie cambia por
el mismo factor exacto y que no es 100 ni 0,01). Lo dejo para que lo decidas tú.

**D6. Rango `null` cuando no está confirmado** (SpaceX, Aramco, SK hynix: miembros seguros, puesto exacto no visible). *Contraria:* poner el rango de otra fuente cercana
(p. ej. SpaceX 6.º o 7.º según el día); lo descarté por no mezclar fuentes y fechas.

**D7. La sonda es más estricta que el generador:** exige que Yahoo informe la divisa en `currency`/`subunit`. El generador, en `currency`, publica aunque Yahoo calle (solo lo
exige en `subunit`). No he endurecido el generador: podría dejar sin publicar símbolos que hoy funcionan. *Contraria:* endurecerlo también cerraría el caso «fila declarada
`GBP/currency` por error con Yahoo mudo».

**D8. Metadatos solo en las filas nuevas.** No he anotado `lists`/`country` en las 43 que ya estaban (la tarea pedía no tocar los existentes); la lista completa está en el
documento. *Contraria:* anotarlas haría el registro autoexplicativo (¿qué filas son del top 50?) y es un cambio inocuo (no se publica).

**REQUISITO CAMBIADO (2 tests antiguos):**
* `tests/test_registry_v2.py::test_el_registro_del_repo_es_valido_y_del_tamano_previsto`: el techo pasa de 250 a `c.MAX_REGISTRY_SYMBOLS` (si no, la próxima ampliación
  fallaría por un número puesto a mano) y se admite `dormant` además de `active`; se añade que haya ≥ 150 activas. `invalid`/`delisted` siguen prohibidos en el registro.
* `tests/test_feed_v2.py::test_medicion_de_peticiones_y_tamano_con_el_registro_real`: «tras el alta, todos publicados» pasa a «todos los **activos**», y se añade que las
  `dormant` no tienen ficheros (comprobación más fuerte, no más débil).

---

## 5. Desviaciones

1. **La lista no está completa con fuentes visibles.** Las webs no se pueden abrir desde aquí; los fragmentos del buscador dejan ver unos 30 de los 50 puestos. Están
   identificadas todas las que **faltaban** y eran identificables, pero quedan **4 no estadounidenses sin identificar** (F1 dice 14 no estadounidenses; veo 10) y ~10
   estadounidenses de los puestos 25-49 sin nombre (las candidatas por tamaño ya están en el registro). Detalle y candidatas en el documento de la lista.
2. **Ningún símbolo verificado contra Yahoo** (bloqueado aquí). Es la razón de D1 y de la sonda.
3. **`symbol-probe.yml` es un workflow nuevo** (la tarea lo permitía como propuesta). No toca `nav-probe.yml` ni sus guardas; mismas acciones fijadas por SHA que el resto.
4. **Un commit (3/4) se creó con un test en rojo** (una cadena `pytest | tail && git commit` sin `pipefail`) y lo corregí con `--amend` antes de empujar; el commit
   publicado está en verde. En mi test nuevo corregí dos expectativas antes de su primer verde («bank of china» casaba también con «Agricultural Bank of China»; el saneado
   de texto esperaba otra cadena exacta). Ninguna prueba antigua se debilitó (ver §4).
5. **Python 3.13 en local** frente a 3.14 en la CI.

## 6. Pendiente o sin probar

**Pendientes de confirmar (NO están en el registro):** Anthropic (`ANT`; ver D4); China Construction Bank (`601939.SS` según CompaniesMarketCap; 32.ª en agosto, sin dato de
noviembre); las 4 no estadounidenses y las estadounidenses sin identificar de los puestos 25-49 (cotejar con la tabla completa de Visual Capitalist en un navegador).

**Sin confirmar contra el proveedor (están en el registro, en `dormant`):** `SPCX`, `2222.SR`, `000660.KS`, `601288.SS`, `1398.HK`, `601988.SS`, `PM`.
Cómo se confirman tras el merge: Actions → «Symbol probe» → Run workflow (rama `main`, `select: dormant`) → leer la tabla del resumen: veredicto `OK` y nombre de Yahoo que
corresponda a la empresa → PR que cambie esas filas a `active`. Lo que no sea `OK` se corrige o se queda en `dormant`.

**Aviso del run real: `ROG.SW` (Roche, 33.ª del top 50) falla en Yahoo** («Quote not found») en el run del 19-nov. Si sigue así, en 5 runs pasará a `invalid` sin
bloquear nada. Conviene pasarle la sonda (`symbols: ROG.SW`) y, si Yahoo ha cambiado el ticker, decidir la fila nueva (no he inventado una alternativa).

**Coste estimado (detalle en `docs/AMPLIAR_UNIVERSO.md` §5):** los límites de GitHub quedan lejos (Pages 1 GB: 3 % hoy, ~4 % con +50, ~7 % con +250; minutos de
Actions gratis en repo público; despliegue ~5 s de 10 min). **Pasan del 50 % topes propios del repo:** `MAX_REGISTRY_SYMBOLS` (hoy 249/400 = 62 %; +50 → 75 %; +250 → se
rompe), `MAX_BYTES["manifest"]` (+50 → ~61 %; +250 → 96-101 %, se rompe), `MAX_SITE_BYTES` (+250 → ~58 %). **Ancho de banda de Pages (100 GB/mes) no medido**: depende de
lo que descargue el dashboard, no del run. Techo propuesto sin tocar código: ~350 filas y ≤ 450 peticiones en el run diario, en tandas de ≤ 80.

**No probado:** nada se ha ejecutado en GitHub Actions (ni la sonda ni el feed con las filas nuevas); `actionlint` no está instalado aquí (la CI lo corre si está); el tiempo
real de la sonda con Yahoo; el comportamiento de Yahoo con 2222.SR/601288.SS (horarios y festivos distintos: la regla de «barra de los últimos 6 días» del validador se aplica al
conjunto, no por símbolo, así que un mercado cerrado una semana no bloquea).
