# FIXES_ACTIVAR_TOP50 — activar las 7 filas del top 50 que pasaron la sonda

Rama `claude/activar-top50` desde `main` `6a8c1d0`. Base del cambio: sonda «Symbol probe» ejecutada en `main` el **2026-11-20 a las 17:21 UTC**, 7 de 7 `OK`
(nombre de Yahoo = empresa esperada, divisa = la declarada, última barra del 19 o 20-11). La ejecución de la sonda no la he visto yo: el dato viene del brief de Alex.

**Cambio**: en `registry.json`, `"status":"dormant"` → `"status":"active"` en `SPCX`, `2222.SR`, `000660.KS`, `601288.SS`, `1398.HK`, `601988.SS` y `PM`. Nada más
(mismo `tier: warm`, misma divisa/unidad, `country` y `lists` intactos; ningún otro símbolo ni fichero de datos). Docs: `docs/universo/TOP50_MCAP_2026-11-13.md`
(sección «Activación (20-nov-2026)» y tabla) y `docs/AMPLIAR_UNIVERSO.md` (historial de activaciones; fila de ejemplo). Tests ajustados (abajo).

## 1. Comandos y salidas en bruto

Entorno: venv con `requirements.txt` + `requirements-dev.txt` (ruff 0.17.3), Python 3.13, sin red real (el `conftest.py` bloquea toda conexión externa).

### 1.1 Comprobación de que solo cambian 7 filas y solo `status`
Script en Python que compara `registry.json` de `main` con el de la rama fila a fila. Salida:
```
[('SPCX', {'status'}, 'dormant', 'active'), ('2222.SR', {'status'}, 'dormant', 'active'), ('000660.KS', {'status'}, 'dormant', 'active'), ('601288.SS', {'status'}, 'dormant', 'active'), ('1398.HK', {'status'}, 'dormant', 'active'), ('601988.SS', {'status'}, 'dormant', 'active'), ('PM', {'status'}, 'dormant', 'active')]
Counter({('yahoo', 'active'): 231, ('crypto', 'active'): 18})
```
(antes: yahoo activas 224, yahoo dormant 7, cripto 18; total 249 filas en ambos casos).

### 1.2 Rojo antes de ajustar los tests (solo registro cambiado)
```
FAILED tests/test_universo_top50.py::test_las_filas_nuevas_estan_y_son_coherentes
FAILED tests/test_universo_top50.py::test_e2e_en_dormant_no_se_piden_a_yahoo_ni_tienen_ficheros
FAILED tests/test_universo_top50.py::test_la_fila_de_ejemplo_del_procedimiento_es_valida_y_coincide_con_el_registro
FAILED tests/test_symbol_probe.py::test_seleccion_por_defecto_son_las_dormant_y_symbols_debe_estar_en_el_registro
4 failed, 189 passed in 42.42s
```
(Se ejecutó también la batería completa con `-x`: el primer fallo fue el de la sonda; con los 4 ficheros afectados aparecen estos 4 y ninguno más.)

### 1.3 Ruff
```
$ ruff check --select F scripts tests
All checks passed!
exit=0
```

### 1.4 Batería completa
```
$ python -m pytest -q
........................................................................ [  3%]
........................................................................ [  6%]
… (líneas de puntos omitidas: 2180 tests, todos «.»)
=============================== warnings summary ===============================
tests/test_backend_fixes2.py::test_BE14_source_y_published_tambien_son_texto_plano
  /tmp/claude-0/venv/lib/python3.13/site-packages/feedparser/util.py:80: DeprecationWarning: To avoid breaking existing software while fixing issue 310, a temporary mapping has been created from `updated` to `published` if `updated` doesn't exist. This fallback will be removed in a future version of feedparser.
    warnings.warn(

-- Docs: https://docs.pytest.org/en/stable/how-to/capture-warnings.html
2180 passed, 1 warning in 199.00s (0:03:18)
exit=0
```

### 1.5 Controles negativos (registro roto a propósito y restaurado)
```
## Control negativo 1: PM devuelto a dormant
FAILED tests/test_universo_top50.py::test_las_filas_nuevas_estan_y_son_coherentes
FAILED tests/test_universo_top50.py::test_e2e_filas_nuevas_activadas_divisa_distinta_no_se_publica_y_el_resto_si
FAILED tests/test_universo_top50.py::test_la_fila_de_ejemplo_del_procedimiento_es_valida_y_coincide_con_el_registro
FAILED tests/test_symbol_probe.py::test_seleccion_por_defecto_son_las_dormant_y_symbols_debe_estar_en_el_registro
4 failed, 58 passed in 16.28s
## Control negativo 2: otra fila (AAPL) pasada a dormant
FAILED tests/test_universo_top50.py::test_solo_las_filas_nuevas_llevan_metadatos_y_estan_en_dormant
FAILED tests/test_symbol_probe.py::test_seleccion_por_defecto_son_las_dormant_y_symbols_debe_estar_en_el_registro
2 failed, 60 passed in 7.82s
## Control negativo 3: SPCX activado pero con tier hot
FAILED tests/test_universo_top50.py::test_las_filas_nuevas_estan_y_son_coherentes
1 failed, 45 passed in 14.30s
## restaurado
 registry.json | 14 +++++++-------
 1 file changed, 7 insertions(+), 7 deletions(-)
idéntico
```

### 1.6 Medición de peticiones y tamaño — antes (`main`) y después (rama)
Test del repo `test_medicion_de_peticiones_y_tamano_con_el_registro_real` (registro real, Yahoo simulado). Se omiten las ~200 líneas `! X: deferred (cupo…)` y
`not_listed` del log; el resto, tal cual.
```
$ git rev-parse --short HEAD
6a8c1d0
$ V2_MEASURE=1 python -m pytest -s -q tests/test_feed_v2.py -k medicion

  run                      llamadas  diarias  intradía  largas   bytes totales   ficheros
  alta 1                        129       80        49      80        13230486        935
  alta 2                        178      129        49      80        26450922       1815
  alta 3                        162      113        49      64        37018960       2519
  solo hot (+6 h)                98       49        49       0        37018956       2519
  hot + warm (+21 h)            251      202        49       0        37018959       2519
  hot (+6 h)                     98       49        49       0        37018956       2519
  tamaño por tipo (máx / medio): crypto 5587/5587, daily 17050/16422, health 30065/30065, index 34786/34786, intraday 597/593, latest 632/592, macro 550/550, news 78/78
  total del sitio: 37018956 bytes (14502644 comprimido con zlib -6, fichero a fichero) en 2518 ficheros (+ manifest.json de 285126 bytes); símbolos hot 49, warm 160, cold 22
.
1 passed, 50 deselected in 19.77s
```
```
$ V2_MEASURE=1 python -m pytest -s -q tests/test_feed_v2.py -k medicion   # rama, registro con las 7 activas

  run                      llamadas  diarias  intradía  largas   bytes totales   ficheros
  alta 1                        129       80        49      80        13230506        935
  alta 2                        178      129        49      80        26450931       1815
  alta 3                        169      120        49      71        38177822       2596
  solo hot (+6 h)                98       49        49       0        38177818       2596
  hot + warm (+21 h)            258      209        49       0        38177821       2596
  hot (+6 h)                     98       49        49       0        38177818       2596
  tamaño por tipo (máx / medio): crypto 5587/5587, daily 17050/16424, health 30534/30534, index 34835/34835, intraday 597/593, latest 632/592, macro 550/550, news 78/78
  total del sitio: 38177818 bytes (14954445 comprimido con zlib -6, fichero a fichero) en 2595 ficheros (+ manifest.json de 294022 bytes); símbolos hot 49, warm 160, cold 22
.
1 passed, 50 deselected in 20.87s
```

### 1.7 Transición simulada: sitio poblado con el registro de `main` → primeros runs con las 7 activas
Fichero temporal `tests/zz_transicion_top50.py` (copiado, ejecutado y borrado; **no está en el commit**). Usa el `world` del repo (Yahoo simulado), genera con
`feed_v2.generate` y valida cada sitio con `sanity_check_v2.check_site` contra el anterior; además comprueba `MAX_REGISTRY_SYMBOLS`, `MAX_SITE_BYTES`,
`MAX_SITE_FILES` y `MAX_BYTES["manifest"]` en cada run.
```
$ python -m pytest -s -q tests/zz_transicion_top50.py   # fichero temporal, no se commitea

  topes: MAX_REGISTRY_SYMBOLS=400 MAX_SITE_BYTES=120000000 MAX_SITE_FILES=12000 MAX_BYTES[manifest]=600000 BACKFILL_MAX_PER_RUN=80
  run                        llamadas  de las 7  largas  largas(7)  bytes sitio ficheros   manifest errores 7 public.
  main: alta 1                    129         0      80          0     13230486      934     106059       0         0
  main: alta 2                    178         0      80          0     26450922     1814     205240       0         0
  main: alta 3                    162         0      64          0     37018960     2518     285126       0         0
  main: hot+warm (+21 h)          251         0       0          0     37018959     2518     285126       0         0
  rama: solo hot (+6 h)           105         7       7          7     38177819     2595     294022       0         7
  rama: hot+warm (+18 h)          251         0       0          0     38177821     2595     294022       0         7
  rama: solo hot (+6 h)           105         7       0          0     38177819     2595     294022       0         7
  rama: hot+warm (+18 h)          251         0       0          0     38177821     2595     294022       0         7
.
1 passed in 38.12s
```

### 1.8 Barrido de privacidad del diff (líneas añadidas, antes de añadir este informe; repetido después sobre diff + informe)
```
$ git diff origin/main -U0 | grep "^+" | grep -nEi <patrones>
69
-- forma de ISIN:
-- supabase|http|@|sb_|token|key|secret|jwt|eyJ|/products/|ishares|gmail|<usuario de correo del dueño, no reproducido>:
1:+{"symbol":"PM","kind":"yahoo","name":"Philip Morris International","currency":"USD","unit":"currency","tier":"warm","status":"active","fail_streak":0,"country":"US","lists":{"top50-mcap@2026-11-13":46}}
37:+{"symbol":"SPCX","kind":"yahoo","name":"SpaceX","currency":"USD","unit":"currency","tier":"warm","status":"active","fail_streak":0,"country":"US","lists":{"top50-mcap@2026-11-13":null}},
38:+{"symbol":"2222.SR","kind":"yahoo","name":"Saudi Aramco","currency":"SAR","unit":"currency","tier":"warm","status":"active","fail_streak":0,"country":"SA","lists":{"top50-mcap@2026-11-13":null}},
39:+{"symbol":"000660.KS","kind":"yahoo","name":"SK hynix","currency":"KRW","unit":"currency","tier":"warm","status":"active","fail_streak":0,"country":"KR","lists":{"top50-mcap@2026-11-13":null}},
40:+{"symbol":"601288.SS","kind":"yahoo","name":"Agricultural Bank of China (Shanghai)","currency":"CNY","unit":"currency","tier":"warm","status":"active","fail_streak":0,"country":"CN","lists":{"top50-mcap@2026-11-13":36}},
41:+{"symbol":"1398.HK","kind":"yahoo","name":"ICBC (HK)","currency":"HKD","unit":"currency","tier":"warm","status":"active","fail_streak":0,"country":"CN","lists":{"top50-mcap@2026-11-13":40}},
42:+{"symbol":"601988.SS","kind":"yahoo","name":"Bank of China (Shanghai)","currency":"CNY","unit":"currency","tier":"warm","status":"active","fail_streak":0,"country":"CN","lists":{"top50-mcap@2026-11-13":43}},
43:+{"symbol":"PM","kind":"yahoo","name":"Philip Morris International","currency":"USD","unit":"currency","tier":"warm","status":"active","fail_streak":0,"country":"US","lists":{"top50-mcap@2026-11-13":46}},
-- ids numéricos largos (>=7 dígitos):
fin barrido
```
Las únicas coincidencias son el `@` de la clave de lista `top50-mcap@2026-11-13` (formato del registro), no correos. Al repetirlo con el informe: sin forma de ISIN; aparecen la URL pública de la documentación de pytest (en la salida en bruto de 1.4), la propia línea de patrones y, como números de ≥ 7 cifras, solo los bytes de las mediciones. El patrón con el usuario de correo del dueño se quitó del informe para no publicarlo.

## 2. Qué demuestra cada prueba y qué NO

| Prueba | Demuestra | NO demuestra |
|---|---|---|
| 1.1 diff fila a fila | Solo cambian esas 7 filas y solo su `status`. | Nada sobre Yahoo. |
| Ruff + batería (2180 verdes) | El registro sigue siendo válido para el generador y el validador; no se rompe nada existente. | Que Yahoo sirva hoy los 7 (Yahoo está simulado en todos los tests). |
| `test_las_filas_nuevas_…` | Las 7 están `active`, `warm`, con su divisa/unidad/país. | Que el ticker sea la empresa correcta: eso lo dice la sonda real (nombre de Yahoo cotejado), no un test. |
| `test_e2e_filas_nuevas_activadas_…` (ahora con las filas reales tal cual) | Activas → se piden, se publican con su divisa y unidad, y una divisa distinta de Yahoo (Aramco en USD) no se publica y va a `unit_mismatch`. | Comportamiento de Yahoo real con `.SR`, `.KS`, `.SS`. |
| `test_e2e_en_dormant_…` (con las 7 devueltas a `dormant`) | El mecanismo `dormant` sigue sin pedir ni publicar. | — (ya no describe el registro real, que no tiene `dormant`). |
| Sonda (`test_seleccion_…`) | Con el registro real no queda nada que probar y `main` de la sonda sale 0 sin peticiones; con las 7 en `dormant`, la selección es **exactamente** esas 7 (antes era `>=`). | — |
| Controles negativos 1.5 | Si una de las 7 se queda en `dormant`, si otra fila cambia de estado o si se cambia el tier, los tests fallan. | Cambios en otros campos de otras filas fuera de `status` (los cubre `test_solo_las_filas_nuevas_…` para `country`/`lists`, no para todo). |
| Medición 1.6 y transición 1.7 | Con las 7 activas: validador sin errores, topes dentro, las 7 publicadas tras el primer run; coste en peticiones. | Tiempos y bytes reales: la serie sintética lleva 6 decimales (los bytes son cota superior) y el reparto real de runs al día puede ser otro. |

Qué tendría que pasar para que el cambio fuese incorrecto: que el resultado de la sonda que motiva el cambio no fuese el descrito (no lo he visto yo), o que
Yahoo cambie la divisa/escala de alguno después de la sonda (el generador lo atraparía como `unit_mismatch` y no lo publicaría; no bloquea al resto).

## 3. Peticiones por run: antes y después (Yahoo simulado)

| Run | `main` | Rama | Diferencia |
|---|---|---|---|
| hot + warm (medición 1.6) | 251 | 258 | **+7** |
| solo hot (medición 1.6) | 98 | 98 | 0 |
| Primer run tras el merge (1.7, un «solo hot») | 98 | 105 | +7 (las 7 descargas largas de 10 años, dentro del cupo `BACKFILL_MAX_PER_RUN` = 80) |
| Por día en régimen (1.7: un hot + un hot+warm) | 98 + 251 = 349 | 105 + 251 = 356 | **+7 al día** |

**Desviación respecto a lo esperado**: el brief esperaba «unas +35 por run completo»; lo medido es **+7** (1 petición diaria por símbolo `warm`, como dice
`AMPLIAR_UNIVERSO.md` § 4: «+1 petición al día»). En la simulación de transición, las 7 caen en el run en que les toca por su cadencia de ~24 h (aquí, el «solo hot»), no
necesariamente en el de las 16:47. No veo de dónde saldrían +35 (¿7 × 5 runs al día? un `warm` se pide una vez al día, no en cada run); si Alex tenía otra cuenta, conviene
mirarla con los runs reales (paso 6 del procedimiento).

## 4. Topes con las 7 activas (simulación; en real los bytes son algo menores)

| Tope | Valor | Antes | Después | % del tope |
|---|---|---|---|---|
| `MAX_REGISTRY_SYMBOLS` | 400 | 249 filas | 249 filas (no se añaden filas) | 62 % (igual) |
| `MAX_BYTES["manifest"]` | 600.000 B | 285.126 | 294.022 | **49 %** ⚠ (a 1 punto del umbral de aviso del 50 %) |
| `MAX_SITE_BYTES` | 120 MB | 37,0 MB | 38,2 MB | 32 % |
| `MAX_SITE_FILES` | 12.000 | 2.518 | 2.595 | 22 % |

## 5. Decisiones y supuestos (los decide Alex)

1. **Fila de ejemplo de `AMPLIAR_UNIVERSO.md`**: el test exige que sea idéntica a la fila real de `PM`; al activarla, el ejemplo pasa a `"status":"active"` con una nota
   («una fila nueva se escribe igual pero con `dormant`»). Alternativa: comparar el ejemplo sin el campo `status` y mantener `dormant` en el doc; la descarté porque afloja
   el test (dejaría de detectar un ejemplo con un `status` que el validador rechace).
2. **`test_e2e_en_dormant_…`** ahora fuerza `dormant` sobre las 7 filas reales (mismas aserciones). Contraria: borrarlo por obsoleto; no lo hice (el mecanismo sigue
   valiendo y el brief pide no borrar aserciones).
3. **Las tablas de medición de `README.md` y de `AMPLIAR_UNIVERSO.md` § 4-5** (224 activos, 19-nov) son medidas fechadas: no las reescribo; solo añado en § 5 que hoy
   son 231 activos y 249 filas. Si Alex prefiere, se rehacen tras los runs reales.
4. En el documento del top 50 no figura la bolsa de las 5 no estadounidenses (el brief no la daba): «—».

## 6. Desviaciones

* Peticiones: +7 por run diario, no +35 (§ 3).
* Manifest al 49 % de su tope (antes 48 %): la siguiente tanda pasará del 50 %.
* El aserto `not any(... status == "dormant")` de `test_medicion_…` queda vacío con el registro real (ya no hay `dormant`); no lo toco. El mecanismo lo cubren
  `test_simbolo_dormant_del_registro_no_se_pide_nunca` y `test_e2e_en_dormant_…`.

## 7. Pendiente / sin probar

* Nada contra Yahoo real desde aquí (bloqueado). Tras el merge: 2-3 runs y mirar `health.json` (`run.yahoo_rate_limited` = 0, `problems.failed` y
  `problems.unit_mismatch` de las 7) y el log `v2: … peticiones a Yahoo …`.
* Sin cambios y sin confirmar, como estaban: las 4 no estadounidenses y ~10 estadounidenses de los puestos 25-49 sin identificar, China Construction Bank (`601939.SS`)
  y Anthropic (`ANT`).
* No se ha lanzado ningún workflow ni publicado nada.
