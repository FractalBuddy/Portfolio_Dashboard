# F6 (backend público): lista «top 50 por capitalización» completa, fechada y publicada

Rama `claude/explorar-f6-top50` desde `main` en `900b395`. Nada fusionado ni lanzado: el PR lo abre el dueño y se revisa antes de cualquier merge.

| # | Commit | Qué |
|---|---|---|
| 1 | `F6 punto 1: 8 simbolos del top 50 del 27-11 al registro v2 en dormant` | 8 filas `dormant` + tests; 5 tests anteriores cambiados a propósito |
| 2 | `F6 punto 2: anotacion top50-mcap@2026-11-27 en las 50 filas…` | anotación por fila, lista del 13-11 quitada, documento nuevo y antiguo marcado `SUSTITUIDA` |
| 3 | `F6 punto 3: definicion de top50-mcap@2026-11-27 en la raiz…` | `lists` en la raíz del registro; 4 tests cambiados a propósito; contrato y guía al día |
| 4 | `F6 punto 4: pruebas de extremo a extremo…` | e2e con el registro real + controles negativos |
| 5 | `F6 punto 5: limites antes/despues fijados por test` | test de límites, historial de altas, excepción de variantes en la guía |
| 6 | este informe | — |

## 1. Comandos y salidas en bruto

Entorno: venv con `pip install --require-hashes -r requirements.txt -r requirements-dev.txt` (el Python del sistema no tiene `yfinance` y la colección falla).

**Línea base** (`main` en `900b395`, worktree limpio aparte; una primera ejecución en la rama se invalidó porque leía `registry.json` mientras lo editaba):

```
$ python -m pytest -q -p no:cacheprovider          # en main
2413 passed, 1 warning in 223.81s (0:03:43)
```

**Final** (rama, tras el commit 5):

```
$ python -m pytest -q -p no:cacheprovider
2444 passed, 1 warning in 247.85s (0:04:07)
$ ruff check --select F scripts tests
All checks passed!
```

El aviso es el mismo en los dos (`DeprecationWarning` de `feedparser` en `test_backend_fixes2.py::test_BE14_…`). +31 tests = los de `tests/test_top50_F6.py`.

**Rojo → verde → control negativo, por punto** (`pytest -q tests/test_top50_F6.py`, salvo donde se indica):

```
# punto 1
rojo:      6 failed, 1 passed   (pasa solo test_la_lista_dada_tiene_50_simbolos_sin_repetir_y_validos, que no mira el registro)
verde:     7 passed
negativo:  GOOG activada y fila ANT borrada en registry.json -> 5 failed, 2 passed
           (siguen pasando: la lista dada y test_las_variantes_existentes_siguen_tal_cual_y_activas, que no mira esas filas)

# punto 2
rojo:      4 failed, 7 passed
verde:     11 passed
negativo:  rango de PM 49->48, SPCX con la lista del 13-11, «SUSTITUIDA» -> «Sustituida» en el doc antiguo -> 4 failed, 7 passed

# punto 3
rojo:      2 failed, 11 passed
verde:     13 passed
negativo:  campo «extra» en la lista de la raíz -> 11 failed, 78 passed, 4 errors (registro entero rechazado: test_top50_F6 + test_registry_v2)
           sin `lists` en la raíz -> 4 failed, 53 passed (2 de test_top50_F6 y 2 de test_listas_indice_F5)
suite completa antes de ajustar el último test de texto: 1 failed, 2425 passed
           (tests/test_simbolos_F5.py::test_el_texto_del_registro_sigue_sin_isin_ni_urls; ver § 3)

# punto 4  (rojo = mismos tests contra el registry.json del commit 2, sin definición en la raíz)
rojo:      13 failed, 2 passed, 12 deselected
verde:     27 passed (luego 29 con el caso grafia_distinta)
mutantes en scripts/feed_v2_common.py (se restauran con git checkout tras cada uno):
  completitud (len != size)        -> 2 failed  [49_simbolos x2]
  fuente https                     -> 2 failed  [fuente_http x2]
  repetido                         -> 2 failed  [duplicado, duplicado_otra_caja; ver «qué no demostró»]
  grafía exacta (by[key] != s)     -> 27 passed  <- SOBREVIVIÓ: faltaba el caso. Añadido `grafia_distinta`; repetido: 2 failed
  index_lists devuelve None        -> 3 failed  [los 2 e2e + el de fecha futura]

# punto 5
V2_MEASURE=1 pytest -s tests/test_top50_F6.py -k limites
  ficheros 1016 -> 1016; index.json 33894 -> 35494 B (+1600; lista ~498); health.json 20855 -> 21320 B; manifest.json 106059 -> 106059 B (tope 600000)
  2 passed
negativo:  GOOG activada (hot) en registry.json -> 1 failed (el conjunto de ficheros cambia)
```

**Medición con el registro real** (`V2_MEASURE=1 pytest -s tests/test_feed_v2.py -k medicion`; Yahoo simulado, 6 runs; ese test pasa las filas pero **no** las listas):

```
main (251 filas):
  hot + warm (+21 h)   258 llamadas   38178207 B   2828 ficheros
  index 35102/35102, health 30653/30653
  total del sitio: 38178204 bytes en 2595 ficheros (+ manifest.json de 294022 bytes); símbolos hot 49, warm 162, cold 22
rama (259 filas):
  hot + warm (+21 h)   258 llamadas   38179773 B   2828 ficheros
  index 36203/36203, health 31118/31118
  total del sitio: 38179770 bytes en 2595 ficheros (+ manifest.json de 294022 bytes); símbolos hot 49, warm 170, cold 22
```

(«warm 170» cuenta también las `dormant`; las llamadas a Yahoo no cambian.)

**Banco sintético del manifest** (`python tests/bench_manifest_escala.py --n 251 259 --work … --out … --no-download`; **supone todas las filas activas**):

```
251 filas (233 yahoo): manifest 305181  index 41648  sitio 40634990 B  2861 ficheros  root 24291  rotos []  >50 %: MAX_REGISTRY_SYMBOLS 63 %, MAX_BYTES[manifest] 51 %
259 filas (241 yahoo): manifest 315406  index 42989  sitio 41994202 B  2957 ficheros  root 25102  rotos []  >50 %: MAX_REGISTRY_SYMBOLS 65 %, MAX_BYTES[manifest] 53 %
```

**Barrido de privacidad** (`git diff 900b395`, solo líneas añadidas, 540 líneas; funciones de `tests/privacy_guard.py` + expresiones regulares; se repitió con este informe incluido):

| Qué | Resultado |
|---|---|
| ISIN genérico (`AA` + 9 alfanuméricos + dígito) | 0 |
| `isin_leaks` / `product_id_leaks` / `zero_p_leaks` | 0 / 0 / 0 |
| claves (`sb_…`, `eyJ…`, `ghp_`, `sk-…`) | 0 |
| «supabase» / `*.supabase.co` | 0 / 0 (las 2 apariciones del diff son líneas de contexto sin cambiar: listas negativas de tests) |
| correos | 0 |
| huellas `PROXY_HASHES` / `CRYPTO_HASHES` | 0 / 0 |
| acciones de nicho de v1 | ninguna |
| URLs | solo `https://companiesmarketcap.com/` (la fuente) y `http://companiesmarketcap.com/` (el caso negativo «fuente http» de un test) |

Con este informe incluido salen 1 «clave», 3 «supabase» y 1 «`*.supabase.co`»: son las **etiquetas de la propia tabla** de arriba (los nombres de lo que se busca), no valores; el resto, igual.

El guardia `tests/test_registry_v2.py::test_el_registro_publico_no_lleva_nada_del_dueno` **no se ha tocado** y pasa con el registro nuevo.

## 2. Qué se hizo

### Punto 1: filas nuevas (8 de 50)

Cruce de los 50 con `registry.json` por grafía exacta: **42 ya estaban, faltaban 8**. Entran `dormant`, `tier: warm`, `fail_streak: 0`:

| Símbolo | Rango | Nombre escrito (sin verificar) | Divisa / unidad (del sufijo) | `country` |
|---|---|---|---|---|
| `GOOG` | 4 | Alphabet (C) | USD / currency | US |
| `ANT` | 14 | Anthropic | USD / currency | US |
| `601939.SS` | 27 | China Construction Bank (Shanghai) | CNY / currency | CN |
| `RO.SW` | 33 | Roche (bearer) | CHF / currency | CH |
| `TCEHY` | 35 | Tencent (ADR) | USD / currency | CN |
| `688825.SS` | 40 | ChangXin Memory Technologies | CNY / currency | — (sin `country`) |
| `NVS` | 46 | Novartis (ADR) | USD / currency | CH |
| `AZN` | 50 | AstraZeneca (US) | USD / currency | GB |

* **Ningún nombre está verificado en Yahoo** ni contra la fuente: desde aquí no se llega ni a Yahoo ni a `companiesmarketcap.com` (el proxy rechaza la conexión, `403`; la herramienta de lectura web, `ENOTFOUND`). La sonda informa el nombre de Yahoo: compararlo antes de activar.
* `688825.SS` es la fila menos segura: el nombre sale de un **único fragmento de buscador** (TS2: «CXMT, the company behind SHA:688825»). Por eso va sin `country`.
* `AZN` va como «(US)» y no «(ADR)» porque no he comprobado si en EE. UU. cotiza como ADR o como acción ordinaria.
* Un fragmento de buscador de la página de Roche en CompaniesMarketCap confirma que esa web usa `RO.SW` como línea de Roche (dato de agosto de 2026).

**Variantes** (misma empresa, otra fila ya en el registro). No se ha renombrado ni duplicado nada; cada caso lo decide el dueño:

| Símbolo de la lista | Variante ya en el registro | Situación |
|---|---|---|
| `GOOG` (nueva, `dormant`) | `GOOGL` (Alphabet clase A, `hot`, activa) | dos clases de acciones de la misma empresa |
| `RO.SW` (nueva, `dormant`) | `ROG.SW` (Roche, Genussschein; `cold`, activa) | `ROG.SW` **falló en Yahoo** en el run real del 19-11 («Quote not found»); `RO.SW` podría sustituirla |
| `TCEHY` (nueva, `dormant`) | `0700.HK` (Tencent, HKD, activa) | ADR OTC frente a la línea principal de Hong Kong |
| `NVS` (nueva, `dormant`) | `NOVN.SW` (Novartis, CHF, activa) | ADR frente a SIX |
| `AZN` (nueva, `dormant`) | `AZN.L` (AstraZeneca, GBp, activa) | EE. UU. frente a Londres |
| `TSM` (ya estaba) | `2330.TW` (TSMC, TWD, activa) | heredado de antes de F6; sin cambio |

Estos casos chocan con la regla «una sola fila por empresa» de `docs/AMPLIAR_UNIVERSO.md`. He dejado la regla y añadido allí una **excepción abierta, pendiente del dueño** (ver § 4).

### Punto 2: anotación por fila y documentos

* Las **50 filas** llevan `"lists": {"top50-mcap@2026-11-27": rango}`, con el rango = posición en la lista (ya no hay `null`).
* Las 7 filas que llevaban `top50-mcap@2026-11-13` pasan a la nueva con su rango real: `SPCX` 7, `2222.SR` 8, `000660.KS` 24, `601288.SS` 29, `601988.SS` 37, `1398.HK` 39, `PM` 49 (antes `null`, `null`, `null`, 36, 43, 40, 46).
* El id `top50-mcap@2026-11-13` **ya no aparece** en `registry.json`. Sigue como id de ejemplo en los tests sintéticos de formato de `tests/test_universo_top50.py` (comentado así).
* `docs/universo/TOP50_MCAP_2026-11-27.md` (nuevo): fuente, fecha, criterio, tabla de los 50 (generada desde el registro, no transcrita a mano) con estado y variante, y cómo se renueva.
* `docs/universo/TOP50_MCAP_2026-11-13.md`: aviso `SUSTITUIDA` arriba; el resto se conserva como histórico.
* Respecto al documento del 13-11, `MS` y `AMAT` (puestos 49-50 de entonces, nunca anotados) no están en la lista del 27-11. Siguen en el registro sin cambios.

### Punto 3: definición en la raíz

```json
"lists":{"top50-mcap@2026-11-27":{"source":"https://companiesmarketcap.com/","as_of":"2026-11-27","size":50,"symbols":["AAPL", … ,"AZN"]}}
```

Pasa `list_problems` (completa, fuente `https`, `as_of` = fecha del id, los 50 en el registro, sin repetidos, anotación por fila coherente). `docs/CONTRATO_FEED_V2.md` §3 y `docs/AMPLIAR_UNIVERSO.md` ya no dicen «hoy no hay ninguna lista».

### Punto 4: pruebas

`tests/test_top50_F6.py` (31 tests). Lo que cubre el punto 4:

* **e2e con el registro real** (`generate` con filas y listas reales, Yahoo simulado): `index.json.lists` es exactamente `{id, source, as_of, symbols}` con los 50 en orden; los 50 están en `index.json.symbols` (las 8 nuevas con `status: dormant`, `as_of: null`); ninguna fila publica su anotación; el validador del job `publish` (`check_site`) da `[]`.
* **El camino de la CI**: `feed_v2.main --registry registry.json` y `sanity_check_v2.main --registry registry.json` devuelven 0 y el índice lleva la lista.
* **Controles negativos sobre la lista real** (mensajes exactos de `registry_problems`, y además ni el generador ni el validador arrancan ni se escribe `manifest.json`): símbolo quitado del registro (`ANT`), 49 símbolos, grafía distinta (`ant`), fuente `http`, duplicado (`AAPL` y `aapl` en lugar de `AZN`).
* **Lista con fecha futura**: con el reloj del mundo simulado (26-10-2026) el **único** error del sitio es `index.json: lists: top50-mcap@2026-11-27: as_of: fecha fuera de rango`.

### Punto 5: límites (antes → después)

| Medida | Antes (`main`, 251 filas) | Después (rama, 259 filas) | Tope | Fuente |
|---|---|---|---|---|
| Filas del registro | 251 (63 %) ⚠ | **259 (64,75 %)** ⚠ | 400 (80 % = 320) | `registry.json` |
| Filas `dormant` | 2 | **10** | — | idem |
| Símbolos de Yahoo activos | 231 | **231** | — | idem |
| Ficheros publicados (registro real, simulado) | 2595 (+ manifest) | **2595** | 12 000 | medición `test_feed_v2 -k medicion` |
| `manifest.json` (registro real, simulado) | 294 022 B (49 %) | **294 022 B** (49 %) | 600 000 | idem |
| `index.json` (registro real, simulado, sin lista) | 35 102 B | 36 203 B (+1 101: las 8 filas) | 120 000 | idem |
| `index.json` con la lista (1 run, simulado) | 33 894 B | 35 494 B (+1 600; la lista ≈ 498 B) | 120 000 | `test_limites_las_filas_dormant…` |
| `health.json` (simulado) | 30 653 B | 31 118 B | — | medición |
| Llamadas a Yahoo, run hot + warm (simulado) | 258 | **258** | — | idem |
| `manifest.json` si se activasen las 8 (banco, todo activo) | 305 181 B (51 %) ⚠ | 315 406 B (53 %) ⚠ | 600 000 | `bench_manifest_escala.py --n 251 259` |
| `manifest-root.json` (banco) | 24 291 B | 25 102 B | 600 000 | idem |

⚠ = por encima del 50 %: el validador ya avisaba; **el aviso no se ha cambiado**. **No se pasa del 80 %** de ningún tope. Las filas `dormant` y la lista no añaden ningún fichero (lo fija un test).

## 3. Tests cambiados a propósito (`REQUISITO CAMBIADO`)

| Test | Qué comprobaba antes | Qué comprueba ahora |
|---|---|---|
| `test_listas_indice_F5.py::test_el_registro_real_no_publica_ninguna_lista_y_el_top50_sigue_solo_como_anotacion` → renombrado `test_el_registro_real_publica_exactamente_el_top50_del_27_11` | que el registro real **no** tuviera `lists` en la raíz (`load_registry_doc` → `{}`) y que el top 50 del 13-11 solo existiera como anotación incompleta (1-49 filas). Era la barrera contra publicar una lista incompleta | que hay exactamente una lista, la del 27-11, con fuente, fecha, 50 símbolos, anotación en esas 50 filas y ninguna fila con la del 13-11 |
| `test_listas_indice_F5.py::test_control_negativo_el_top50_real_tal_cual_no_se_puede_publicar` → `…_recortado_no_se_puede_publicar` | que definir el top 50 del 13-11 con sus 7 filas se rechazaba por incompleto | lo mismo con la lista real del 27-11 recortada a 7 (único problema: `incompleta (7 de 50)`) |
| `test_universo_top50.py::test_el_registro_publico_sigue_sin_nada_personal` | ninguna aparición de `http` en todo `registry.json` | la **única** URL es la fuente exacta (1 aparición, en `lists.<id>.source`); quitada, el resto sigue sin `http`, `supabase`, `/products/`, `sb_`, `token`, `@gmail`, `ishares.com`, y las filas no llevan ninguna URL |
| `test_simbolos_F5.py::test_el_texto_del_registro_sigue_sin_isin_ni_urls` | `"lists" not in raw` y ningún `http` | la única lista es la del 27-11 y la única URL su fuente; el resto de cadenas, igual de prohibidas |
| `test_universo_top50.py::test_load_registry_acepta…`, `test_las_filas_nuevas_estan…`, `test_solo_las_filas_nuevas…` | anotación `@2026-11-13` en las 7; ninguna otra fila con `lists`; solo `ENPH`/`ORSTED.CO` `dormant` | anotación `@2026-11-27`; las filas del top 50 solo llevan esa; las 8 de F6 `dormant` |
| `test_simbolos_F5.py` (2), `test_symbol_probe.py::F5_DORMANT`, `test_limites_F5.py` (aviso «63 % (251 de 400)») | tamaño 251 y `dormant` = {ENPH, ORSTED.CO} | las de F5 siguen; tamaño y conjunto exactos en `test_top50_F6.py`; aviso «65 % (259 de 400)» |

Los dos tests de texto son guardias de privacidad del registro, **distintos** de `test_el_registro_publico_no_lleva_nada_del_dueno` (ese no se toca). Los he aflojado lo mínimo que exige el requisito (fuente `https` obligatoria en el registro): solo se admite esa URL, exacta y una vez. **Revísalo**: es un cambio en una barrera de privacidad.

## 4. Qué demostró cada prueba y qué no

**Demostrado:**
* Los 50 símbolos están en el registro con su grafía, las 8 nuevas en `dormant`, y la lista de la raíz es exactamente la dada (orden incluido).
* Con el registro real, el generador publica la lista en `index.json` y el validador del job `publish` la acepta, también por `main()` leyendo el fichero, que es el camino de la CI.
* Una lista estropeada (los 6 casos) invalida el registro entero: no arranca ni el generador ni el validador.
* Las 8 `dormant` no se piden a Yahoo ni crean ficheros, y no cambian `manifest.json` (simulado).
* Los mutantes de completitud, `https`, repetido, grafía exacta (tras añadir el caso) y publicación de la lista los detecta algún test.

**No demostrado:**
* **Que la lista sea correcta**: no he podido abrir la fuente. El orden y los símbolos son los que dio el dueño; el validador solo comprueba la forma.
* **Nombres, divisas y existencia en Yahoo** de las 8 nuevas: deducidos, no comprobados. En particular `ANT` (cotiza desde hace pocos días; puede tener pocas barras), `TCEHY` (ADR OTC) y `688825.SS`.
* **El rojo del punto 4 en los controles negativos es poco informativo**: contra el registro del commit 2 fallan por `KeyError: 'lists'` (la raíz no tenía la clave), no porque se acepte una lista mala. Lo que sí demuestra que vigilan las reglas son los mutantes.
* **Mutante «repetido»**: el test «ni el generador ni el validador arrancan» sigue en verde con la regla de repetidos quitada, porque el mismo registro también falla por otra regla (`AZN lleva la anotación pero no está en ella`). Lo detecta el test de mensajes exactos. Es defensa en profundidad, no un hueco, pero conviene saberlo.
* **No he mutado `sanity_check_v2._check_index`** (que `index.json.lists` sea exactamente la del registro). Lo cubren los tests de F5 (`test_control_negativo_el_validador_rechaza_listas_que_no_son_las_del_registro`), que siguen en verde.
* **Tamaños reales**: todas las cifras salen de Yahoo simulado o del banco sintético. El `manifest.json` publicado **no está medido** (Pages no es accesible desde aquí).
* **Fecha futura**: el validador rechaza una lista con `as_of` a más de 1 día de «hoy». En producción no afecta (hoy ya es 27-11), pero si alguna vez se fecha una lista por adelantado, el job `publish` fallará hasta ese día.

## 5. Decisiones y supuestos (con su postura contraria)

| Decisión / supuesto | Por qué | Postura contraria |
|---|---|---|
| `as_of` = 2026-11-27 (fecha de consulta) | la web no da fecha de corte; lo pidió el dueño | el dato real es, como mucho, del cierre del 26-11; fecharla el 26 sería más exacto, pero no se puede comprobar |
| Añadir la grafía exacta aunque haya variante (`GOOG` junto a `GOOGL`, etc.) | lo exige la lista (los símbolos deben estar en el registro con esa grafía) y lo pidió el dueño | contradice «una sola fila por empresa»; la alternativa era cambiar el símbolo **de la lista** a la variante existente (`GOOGL`, `0700.HK`…), sin tocar el registro, a costa de que la lista ya no fuese literalmente la de la fuente. Decide el dueño |
| Rango = posición en la lista, en las 50 filas | la lista está completa y ordenada; un `null` ya no aporta nada | ninguna relevante: si el orden de la fuente fuera dudoso, `null` sería más honesto, pero la posición la impone igualmente la lista de la raíz |
| `country` en 7 de las 8 nuevas, no en `688825.SS` | son empresas conocidas; la 8.ª sale de un solo fragmento | el dueño pidió «sin `country` si no lo sabes»; también son deducciones. No se publica |
| Nombres «(ADR)», «(C)», «(bearer)», «(US)» | distinguir la fila de su variante en el `index.json` publicado | el nombre se **publica** aunque la fila esté `dormant`: un nombre equivocado sale al índice hasta que se corrija. Alternativa: un nombre neutro con el símbolo |
| La lista antigua se quita del registro (no se conserva con la nueva) | lo pidió el dueño; era incompleta y no publicable | se podía dejar la anotación como histórico, pero dos ids del mismo top 50 confunden y el documento ya guarda la historia |
| Excepción de variantes anotada en `AMPLIAR_UNIVERSO.md` | que la guía no contradiga el registro sin decirlo | se podía dejar la guía intacta y resolverlo en el PR; preferí que el conflicto quede escrito |

## 6. Lo que NO se hizo

* **No se ha activado ningún símbolo** ni se ha lanzado la sonda ni ningún workflow.
* **No se ha tocado** `scripts/fetch_market_data.py` (feed v1): `git diff 900b395 --stat` no lo lista. Tampoco ningún script de `scripts/` ni ningún workflow.
* **No se ha tocado el guardia** `test_el_registro_publico_no_lleva_nada_del_dueno`.
* No se ha renombrado, borrado ni desactivado ninguna fila (tampoco las variantes, `MS` ni `AMAT`).
* No se han verificado los nombres ni la lista contra Yahoo o la fuente (sin acceso desde aquí).
* No he creado el PR.

## 7. Pendiente

1. **Sonda** (`symbol-probe.yml`, en `main`, tras el merge): por defecto prueba las 10 `dormant` (8 de F6 + `ENPH`, `ORSTED.CO`). Para cada `OK`, comparar el nombre de Yahoo con el del registro (sobre todo `688825.SS`, `ANT`, `TCEHY`, `AZN`) y corregir el `name` si hace falta.
2. **Decidir las variantes** (§ 2): para cada par, qué línea se queda activa. Ojo con `ROG.SW`, que ya falla en Yahoo.
3. **Medir el `manifest.json` real** tras activar: el banco da 53 % del tope con las 8 activas (aviso, no bloquea).
4. Si la sonda da `UNIT` o `CURRENCY_MISSING` para alguna, corregir su divisa antes de activar. La lista se puede publicar con esa fila en `dormant`: no hace falta activarla para que la lista sea válida.
