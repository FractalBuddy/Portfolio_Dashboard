# FIXES — Reintento del despliegue de Pages y banco de escala del feed v2

Rama `claude/affectionate-keller-fa34k1` (desde `main` en `262fddf`). Un commit por punto:

| Punto | Qué | Ficheros |
|---|---|---|
| 1 | Reintento único del despliegue (job `publish`) | `.github/workflows/publish-feed-v2.yml`, `tests/test_deploy_reintento.py` |
| 2 | Banco de escala (solo medición) + prueba de que el banco mide algo válido | `tests/bench_escala_v2.py`, `tests/test_bench_escala_v2.py` |
| 3 | Este informe | `auditorias/FIXES_FEED_REINTENTO_ESCALA.md` |

No se ha tocado: `registry.json`, `docs/CONTRATO_FEED_V2.md`, ningún tope (`feed_v2_common.py` y el resto de `scripts/` están intactos), secretos, el job
`gate` (puerta de frescura), `fetch`, `verify` ni `heartbeat` (las pruebas lo fijan por huella). Ninguna llamada a servicios reales: Yahoo, CoinGecko,
Healthchecks y Pages están simulados, y el banco bloquea la resolución de nombres no locales.

---

## 1. Comandos ejecutados y salidas en bruto

Entorno: Python 3.13 local (CI usa 3.14), venv con `pip install --require-hashes --no-deps -r requirements.txt` y `-r requirements-dev.txt`
(pytest, ruff 0.17.3, PyYAML). `actionlint` **no está instalado** en este entorno: **no se ha ejecutado** (en CI corre solo si el runner lo trae).

**Línea base.** El informe anterior (`FIXES_AU01_F1.md`) cierra `main` con `1697 passed`. Mi primera pasada de la base en segundo plano murió a medias
(la lancé con `&` dentro de un subshell que terminó; la salida se cortó en el 89 %), así que **no tengo una salida completa propia de la base**. Las
cifras de abajo cuadran con ella: 1697 + 33 nuevas = 1730.

### Punto 1 — reintento del despliegue

Rojo (pruebas escritas antes de tocar el YAML):

```
$ python -m pytest -q -p no:cacheprovider tests/test_deploy_reintento.py
FAILED tests/test_deploy_reintento.py::test_estructura_del_reintento
FAILED tests/test_deploy_reintento.py::test_control_negativo_el_comprobador_detecta_la_mutacion[_mut_segundo_upload]
... (8 mutaciones: el comprobador ya da problemas sobre el YAML viejo)
FAILED tests/test_deploy_reintento.py::test_fallo_rapido_y_pages_no_sirve_el_sitio_nuevo_reintenta_tras_espera_corta
FAILED tests/test_deploy_reintento.py::test_fallo_lento_no_reintenta_y_falla_el_job[121|600|900]
FAILED tests/test_deploy_reintento.py::test_simulacion_de_escenarios[deploys1-6-2026-11-12T09:47:00Z-success-2-1]
FAILED tests/test_deploy_reintento.py::test_simulacion_de_escenarios[deploys2-6-None-failure-2-0]
FAILED tests/test_deploy_reintento.py::test_simulacion_de_escenarios[deploys4-6-2026-11-12T10:47:00Z-success-1-0]
FAILED tests/test_deploy_reintento.py::test_heartbeat_tras_el_reintento_distingue_ok_degraded_fail[deploys0-6-None--ok]
...
27 failed, 6 passed in 1.73s
```

(Los 6 que ya pasaban con el YAML viejo: el caso normal de un solo deploy, el fallo lento sin reintento, el fallo de validación, «nunca más de dos»,
y dos casos del heartbeat que dan `fail` igual con o sin reintento. Son correctos en ambos: lo que cambia es el caso del incidente.)

Verde, tras el cambio del YAML:

```
$ python -m pytest -q -p no:cacheprovider tests/test_deploy_reintento.py
33 passed in 1.96s
$ python -m pytest -q -p no:cacheprovider tests/test_deploy_reintento.py tests/test_workflows.py tests/test_workflows_F1.py tests/test_workflows_H4.py tests/test_puerta_frescura_F1.py
502 passed (+1 failed: el control negativo de la simulación, ver §4, corregido antes del commit)
```

Controles negativos sobre el **código** (mutación del YAML real, pruebas, restauración desde copia):

```
== M1 1.er deploy sin continue-on-error                    15 failed, 18 passed
== M2 umbral de fallo rápido a 900 s                        5 failed, 28 passed
== M3 sin la comprobación de «Pages ya sirve»               2 failed, 31 passed
== M4 reintento sin mirar la decisión                      10 failed, 23 passed
== M5 sin espera                                            1 failed, 32 passed
== M6 rendirse sin hacer fallar el job (exit 0)             5 failed, 28 passed
$ git diff --stat        (tras restaurar)
 .github/workflows/publish-feed-v2.yml | 47 +++++++++++++++++++++++++++++++++--
```

Batería completa y ruff tras el punto 1:

```
$ ruff check --select F scripts tests
All checks passed!
$ python -m pytest -q -p no:cacheprovider
1730 passed, 1 warning in 221.45s (0:03:41)
```

(El aviso es el `DeprecationWarning` de `feedparser` de siempre.)

### Punto 2 — banco de escala

Rojo de la prueba del banco (sin el banco):

```
$ python -m pytest -q -p no:cacheprovider tests/test_bench_escala_v2.py     # con tests/bench_escala_v2.py apartado
ERROR tests/test_bench_escala_v2.py
1 error in 0.07s
```

Verde: `4 passed in 3.29s`. Batería completa con el banco: ver al final de esta sección.

Ejecución del banco (una sola vez, 14 min 47 s de pared, cada fase en su subproceso):

```
$ python tests/bench_escala_v2.py run --n 200 500 1000 5000 --work <tmp> --out <tmp>/results.json --history5 500 1000
n=200 gen1 14.9s 112 MB
n=200 val1 2.0s 107 MB
n=200 gen2 1.9s 111 MB
n=200 val2 2.2s 108 MB
n=500 gen1 36.0s 115 MB
n=500 val1 5.2s 111 MB
n=500 gen2 4.8s 114 MB
n=500 val2 5.5s 115 MB
n=500 gen1 (HISTORY_YEARS=5) 35.7s
n=500 val1 (HISTORY_YEARS=5) 2.6s
n=1000 gen1 75.1s 119 MB
n=1000 val1 10.3s 118 MB
n=1000 gen2 9.9s 119 MB
n=1000 val2 11.4s 127 MB
n=1000 gen1 (HISTORY_YEARS=5) 71.5s
n=1000 val1 (HISTORY_YEARS=5) 5.5s
n=5000 gen1 397.1s 160 MB
n=5000 val1 60.8s 174 MB
n=5000 gen2 52.5s 162 MB
n=5000 val2 68.4s 220 MB

real	14m47.374s
```

Resumen en bruto de `results.json` (`site1` = sitio del primer run; `site2` = run del día siguiente con sitio anterior; `site1_h5` = primer run con
HISTORY_YEARS=5; «proyección warm/cold a 5 años» = ficheros y bytes diarios de símbolos warm/cold de años anteriores a 2022, es decir, lo que se
ahorraría si SOLO warm/cold guardasen 5 años):

```
=== N 200 {'hot': 42, 'warm': 140, 'cold': 18, 'crypto': 14} []
 gen1        14.9s  111.8 MB stats={'due': 186, 'updated': 186, 'failed': None, 'deferred': None}
 val1         2.0s  106.6 MB errores=0 []
 gen2         1.9s  110.9 MB stats={'due': 169, 'updated': 169, 'failed': None, 'deferred': None}
 val2         2.2s  108.5 MB errores=0 []
 site1    ficheros=2091 bytes=32085716 index=33190 manifest=242922 health=24212
          por tipo {'crypto': (1, 48396), 'daily': (1860, 31202683), 'health': (1, 24212), 'index': (1, 33190), 'intraday': (39, 418093), 'latest': (186, 115914), 'macro': (1, 228), 'manifest': (1, 242922), 'news': (1, 78)}
          máx por fichero {'health': 24212, 'news': 78, 'macro': 228, 'manifest': 242922, 'index': 33190, 'crypto': 48396, 'latest': 692, 'intraday': 11578, 'daily': 19373}
          topes rotos []
          proyección warm/cold a 5 años (lo que sobra) {'files': 735, 'bytes': 12601027}
 site2    ficheros=2091 bytes=32096166 index=33190 manifest=242921 health=24209
          topes rotos []

=== N 500 {'hot': 105, 'warm': 350, 'cold': 45, 'crypto': 36} ['demasiados símbolos (500 > 400)']
 gen1        36.0s  114.8 MB
 val1         5.2s  111.1 MB errores=3 ['manifest.json pesa 605068 bytes (máx. 600000)', 'health.json: symbols: no es un objeto o es demasiado grande', 'index.json: symbols: no es una lista o es demasiado larga']
 gen2         4.8s  114.2 MB
 val2         5.5s  115.3 MB errores=3 ['manifest.json pesa 605067 bytes (máx. 600000)', 'health.json: symbols: ...', 'index.json: symbols: ...']
 gen1_h5     35.7s  113.0 MB
 val1_h5      2.6s  109.1 MB errores=2 ['health.json: symbols: no es un objeto o es demasiado grande', 'index.json: symbols: no es una lista o es demasiado larga']
 site1    ficheros=5207 bytes=80015423 index=83028 manifest=605068 health=59597
          por tipo {'crypto': (1, 48528), 'daily': (4640, 77885594), 'health': (1, 59597), 'index': (1, 83028), 'intraday': (97, 1043704), 'latest': (464, 289598), 'macro': (1, 228), 'manifest': (1, 605068), 'news': (1, 78)}
          topes rotos ['MAX_REGISTRY_SYMBOLS (500 > 400)', 'MAX_BYTES[manifest] (605068 > 600000)']
          proyección warm/cold a 5 años (lo que sobra) {'files': 1835, 'bytes': 31424365}
 site1_h5 ficheros=2887 bytes=40046317 index=83028 manifest=334808 health=59597
          topes rotos ['MAX_REGISTRY_SYMBOLS (500 > 400)']

=== N 1000 {'hot': 210, 'warm': 700, 'cold': 90, 'crypto': 71} ['demasiados símbolos (1000 > 400)']
 gen1        75.1s  119.1 MB
 val1        10.3s  117.7 MB errores=5 ['manifest.json pesa 1210921 bytes (máx. 600000)', 'index.json pesa 166123 bytes (máx. 120000)', 'el sitio pesa 160223186 bytes (máx. 120000000)', 'health.json: symbols: ...', 'index.json: symbols: ...']
 gen2         9.9s  119.2 MB
 val2        11.4s  126.9 MB errores=5 (los mismos)
 gen1_h5     71.5s  116.3 MB
 val1_h5      5.5s  114.2 MB errores=4 ['manifest.json pesa 669815 bytes (máx. 600000)', 'index.json pesa 166123 bytes (máx. 120000)', 'health.json: symbols: ...', 'index.json: symbols: ...']
 site1    ficheros=10420 bytes=160223186 index=166123 manifest=1210921 health=118785
          por tipo {'crypto': (1, 48738), 'daily': (9290, 155998412), 'health': (1, 118785), 'index': (1, 166123), 'intraday': (195, 2099636), 'latest': (929, 580265), 'macro': (1, 228), 'manifest': (1, 1210921), 'news': (1, 78)}
          topes rotos ['MAX_REGISTRY_SYMBOLS (1000 > 400)', 'MAX_BYTES[manifest] (1210921 > 600000)', 'MAX_BYTES[index] (166123 > 120000)', 'MAX_SITE_BYTES (160223186 > 120000000)']
          proyección warm/cold a 5 años (lo que sobra) {'files': 3670, 'bytes': 62854810}
 site1_h5 ficheros=5775 bytes=80167563 index=166123 manifest=669815 health=118785
          topes rotos ['MAX_REGISTRY_SYMBOLS (1000 > 400)', 'MAX_BYTES[manifest] (669815 > 600000)', 'MAX_BYTES[index] (166123 > 120000)']

=== N 5000 {'hot': 1050, 'warm': 3500, 'cold': 450, 'crypto': 357} ['demasiados símbolos (5000 > 400)']
 gen1       397.1s  159.6 MB
 val1        60.8s  174.2 MB errores=8 ['health.json pesa 600373 bytes (máx. 200000)', 'manifest.json pesa 6050060 bytes (máx. 600000)', 'index.json pesa 834014 bytes (máx. 120000)', 'demasiados ficheros (52058 > 12000)', 'el sitio pesa 800106464 bytes (máx. 120000000)', 'health.json: symbols: ...', 'index.json: symbols: ...', 'manifest.json: files: no es un objeto o es demasiado grande']
 gen2        52.5s  161.8 MB
 val2        68.4s  219.7 MB errores=8 (los mismos)
 site1    ficheros=52058 bytes=800106464 index=834014 manifest=6050060 health=600373
          por tipo {'crypto': (1, 50142), 'daily': (46430, 779122940), 'health': (1, 600373), 'index': (1, 834014), 'intraday': (979, 10545566), 'latest': (4643, 2903063), 'macro': (1, 228), 'manifest': (1, 6050060), 'news': (1, 78)}
          topes rotos ['MAX_REGISTRY_SYMBOLS (5000 > 400)', 'MAX_BYTES[health] (600373 > 200000)', 'MAX_BYTES[manifest] (6050060 > 600000)', 'MAX_BYTES[index] (834014 > 120000)', 'MAX_SITE_BYTES (800106464 > 120000000)', 'MAX_SITE_FILES (52058 > 12000)']
          proyección warm/cold a 5 años (lo que sobra) {'files': 18320, 'bytes': 313434404}
```

(`site2` de cada N es idéntico a `site1` salvo unos cientos de bytes; recortado aquí. El máximo por fichero `daily` es 19,4 KB y `latest` 0,7 KB en
todos los N: no dependen de N.)

Peticiones a Yahoo por run (llamadas de la `Run`, no el tiempo real):

```
200  gen1 yahoo_calls 225   gen2 yahoo_calls 208   (due2 169)
500  gen1 yahoo_calls 561   gen2 yahoo_calls 519   (due2 422)
1000 gen1 yahoo_calls 1124  gen2 yahoo_calls 1040  (due2 845)
5000 gen1 yahoo_calls 5622  gen2 yahoo_calls 5208  (due2 4229)
```

Afinado del N exacto en que se rompe cada tope (solo gen1 + val1, a ambos lados del umbral estimado por la pendiente):

```
$ python tests/bench_escala_v2.py run --only-first --n 400 495 497 721 723 748 750 1150 1152 1682 1685 ...
400  files 4164  bytes  63961552 index  66408 manifest  483850 health  47760 | val1 errores 0 | topes []
495  files 5163  bytes  79342150 index  82209 manifest  599928 health  59085 | val1 errores 2 [health/index: symbols demasiado largo] | topes ['MAX_REGISTRY_SYMBOLS (495 > 400)']
497  files 5185  bytes  79683852 index  82545 manifest  602476 health  59339 | val1 errores 3 [+ manifest.json pesa 602476 (máx. 600000)]
721  files 7533  bytes 115656182 index 119749 manifest  875312 health  85800 | val1 errores 3
723  files 7544  bytes 115827143 index 120065 manifest  876586 health  85927 | val1 errores 4 [+ index.json pesa 120065 (máx. 120000)]
748  files 7808  bytes 119946409 index 124240 manifest  907283 health  88986 | val1 errores 4
750  files 7819  bytes 120096829 index 124554 manifest  908579 health  89115 | val1 errores 5 [+ el sitio pesa 120096829 (máx. 120000000)]
1150 files 11988 bytes 184255757 index 191181 manifest 1393115 health 136472 | val1 errores 5
1152 files 12010 bytes 184597421 index 191519 manifest 1395663 health 136726 | val1 errores 7 [+ demasiados ficheros (12010 > 12000)]
1682 files 17520 bytes 269350138 index 280117 manifest 2036054 health 199355 | val1 errores 7
1685 files 17553 bytes 269852408 index 280624 manifest 2039876 health 199736 | val1 errores 7 (health aún < 200000)
```

Transición real de 10 a 5 años con sitio anterior (N=200, run del día siguiente con `HISTORY_YEARS=5` sobre el sitio de 10 años, validado con
`expect_prev=True` como en el job `publish`; script de un solo uso en el scratchpad, no en el repo):

```
gen2 10->5 años: 2.4s
errores 1 ['el sitio pesa 15949094 bytes, menos del 90 % del anterior (31842794)']
avisos 0 []
ficheros 1161 bytes 16083680 manifest 134586 daily {'files': 930, 'bytes': 15309504}
años por símbolo de ejemplo: ['2022.json', '2023.json', '2024.json', '2025.json', '2026.json']
```

Batería completa, ruff y barrido de privacidad al final (tras el punto 2): ver §1 bis al final del informe.

---

## 2. Qué demostró cada prueba y qué NO

### Punto 1

| Prueba | Demuestra | NO demuestra |
|---|---|---|
| `test_estructura_del_reintento` (+ 8 mutaciones del comprobador) | Un solo upload del artefacto; dos `deploy-pages` con el mismo SHA; validación antes de desplegar y sin `continue-on-error`; solo el 1.er deploy con `continue-on-error`; el paso que decide entre ambos con su `if`; el reintento es el último paso y depende de `decision == 'retry'`; `page_url` sale del intento que publicó; **gate, fetch, verify y heartbeat idénticos** a `main` (huella sha256 del job) y los pasos previos al despliegue también. | Que GitHub interprete esas expresiones como espero (`steps.X.outcome` con `continue-on-error`, `||` en `environment.url`). Lo afirma la documentación de Actions; aquí no se ejecuta Actions. |
| Paso «Decide deploy retry» en bash real con `date`/`curl`/`sleep` falsos (11 casos) | Fallo rápido (≤ 120 s) + Pages sin el sitio nuevo ⇒ `sleep 30` y `retry`; Pages ilegible ⇒ `retry`; Pages ya sirve este `generated_at` ⇒ `served`, sin reintento; fallo lento ⇒ `give-up`, `exit 1`, sin dormir ni consultar; sin marca de inicio válida (vacía, texto, inyección) ⇒ `give-up`; un manifest hostil no inyecta salidas. | El tiempo real que tarda deploy-pages en fallar por metadatos (supongo segundos; ver §3). |
| Simulación de la secuencia de pasos (5 escenarios + «nunca más de dos» + validación fallida) | Con el `if:` real de cada paso y el success() implícito: caso normal = 1 deploy; incidente 12-11 = 2 llamadas, 1 publicación, job verde; API caída de verdad = 2 llamadas, job rojo; fallo lento = 1 llamada, job rojo; ya publicado = 1 llamada, sin 2.ª publicación; validación fallida = 0 deploys. | La simulación es **mi modelo** de la semántica de Actions (evaluador mínimo de `if:` escrito en la propia prueba): si mi modelo está mal, la prueba y el YAML pueden compartir el mismo error. Es la prueba que «podría repetir el mismo error que el código». |
| Heartbeat tras el reintento (4 casos) | Con el heartbeat **sin cambios**: reintento con éxito ⇒ ping OK; con alertas ⇒ `degraded: …` + `/fail`; reintento fallido o fallo lento ⇒ `/fail`. Mismo contrato de texto. | Que Healthchecks reciba de verdad el ping (curl falso). |
| Controles negativos M1-M6 sobre el YAML | Cada pieza del diseño tiene al menos una prueba que falla si se quita. | — |

**El cambio sería incorrecto si:** (a) deploy-pages pudiese fallar por metadatos/API **después** de crear un despliegue que luego se completa (entonces el
reintento podría publicar dos veces el mismo artefacto; la comprobación «Pages ya sirve este `generated_at`» lo reduce pero no lo cierra si la caché de Pages
tarda más de ~30 s); (b) el fallo de metadatos tardase más de 120 s (no se reintentaría: mismo comportamiento que antes, /fail); (c) GitHub no
evaluase `steps.deployment.outcome` como `failure` en un paso con `continue-on-error: true` (entonces el reintento nunca correría y el job saldría
**verde sin publicar**: el peor caso, ver §5, punto a comprobar en el primer run real).

### Punto 2

| Prueba / medición | Demuestra | NO demuestra |
|---|---|---|
| `test_bench_escala_v2.py` (4) | El registro sintético cumple el contrato del registro hasta 400 y lo rompe con 401; el sitio sintético pasa el validador con 0 errores (el banco mide un sitio válido, no basura); la medición detecta topes rotos (control negativo con topes bajados). | Que las series sintéticas pesen lo mismo que las reales (ver §3). |
| Banco 200/500/1.000/5.000 | Bytes, ficheros, tamaños de index/manifest/health, tiempo de CPU de generación y validación, memoria máxima por fase, y qué tope rompe el validador. | **El tiempo real de un run**: Yahoo simulado responde al instante; en real el run lo dominan las peticiones (pausas de `_yf_pace` + latencia) y la descarga del sitio anterior fichero a fichero (§5). |
| Transición 10 → 5 años | Que bajar `HISTORY_YEARS` sobre un sitio existente **bloquea la publicación** por la regla de continuidad (−50 % de bytes). | Cómo lo vería la app (el contrato §3 promete 10 años naturales). |

---

## 3. Decisiones y supuestos (con la postura contraria; decide Alex)

### Punto 1

1. **Reintento dentro del mismo job, por pasos, no relanzando el job.** Es la única forma de reutilizar el MISMO artefacto `github-pages` (un 2.º
   upload rompe deploy-pages, como en el incidente). *Contraria:* un job `publish-retry` separado sería más legible; pero tendría que volver a subir el
   artefacto o depender de que deploy-pages encuentre el del job anterior, y vuelve el problema del 2.º artefacto.
2. **«Solo ante el error de metadatos/API» se aproxima por posición y por tiempo, no por el texto del error.** Actions no deja que un paso lea el
   mensaje de error de otro. Lo que sí garantiza el diseño: (i) un fallo de **validación** nunca se reintenta (la validación va antes, sin
   `continue-on-error`: el job para ahí); (ii) solo se reintenta un fallo **rápido** del deploy (≤ 120 s desde que empieza), que es el perfil del
   fallo de metadatos (ocurre antes de crear el despliegue). Un fallo tras sondear un despliegue (timeout de 10 min de deploy-pages) no se reintenta.
   *Supuesto no verificado aquí:* que en deploy-pages v5.0.1 la lectura de metadatos va antes de crear el despliegue y falla en segundos (lo sé de
   versiones anteriores de la acción; no he leído el código de esa versión: sin acceso a GitHub en esta sesión). *Contraria:* reintentar cualquier
   fallo del deploy sin filtro de tiempo es más simple y cubre más errores transitorios; pero un fallo lento ya consumió casi todo el `timeout-minutes: 15`
   del job, y un 2.º intento largo acabaría cortado por el timeout del job a mitad de despliegue.
3. **Umbral de 120 s y espera de 30 s.** Valores por defecto míos. *Contraria:* 30 s puede ser poco para una incidencia de la API de GitHub (el
   incidente 12-11 no sé cuánto duró); 2-3 min aumentaría la probabilidad de éxito a costa de alargar el job. Con 30 s el peor caso sigue cabiendo en
   15 min.
4. **«No publicar dos veces»: comprobación de que Pages no sirve ya este `generated_at` antes de reintentar.** Si lo sirve, no se reintenta y el job
   queda verde (el sitio nuevo está publicado). *Contraria:* esa consulta añade una dependencia de red a la decisión; si falla, se reintenta (fallo
   hacia «reintentar»), porque el peor caso es volver a desplegar el **mismo** artefacto (contenido idéntico), no publicar algo distinto.
5. **Sin cambios en el heartbeat.** El job `publish` termina verde (deploy recuperado) o rojo (reintento fallido, fallo lento, validación): el heartbeat
   existente ya traduce eso a OK / `degraded:` / `/fail`. El reintento solo deja un `::warning::` en el log del run. *Contraria:* mandar un `/log` a
   Healthchecks cuando hubo reintento daría visibilidad de incidencias recuperadas; lo dejé fuera porque el encargo pedía el mismo contrato de texto.

### Punto 2: supuestos del banco

- **Reparto** copiado del registro actual (solo las proporciones): 21 % hot, 70 % warm, 9 % cold; 7 % cripto; unidades 70/10/10/10. Nombres de 30-40
  caracteres y símbolos de 7-10 caracteres (los reales son parecidos; un nombre de 80 caracteres engordaría index.json).
- **Series**: 10 años de barras diarias de lunes a viernes (unas 261 al año, sin festivos: algo MÁS que las reales, ~252) con todos los decimales
  (redondeo a 6 del generador), volumen de 7-8 cifras. Peso por fichero anual: 19,4 KB. Intradía: 2 días × 192 barras de 5 min (pre/post) de
  `fetch_intraday_history`.
- **Alta**: `BACKFILL_MAX_PER_RUN` (80) se levanta SOLO dentro del banco para medir el sitio en régimen; en real, N símbolos nuevos tardan ⌈N/80⌉ runs
  en tener su histórico.
- Tiempos = CPU local, sin red (ver §5). Memoria = `ru_maxrss` del subproceso de cada fase (incluye ~100 MB de pandas/yfinance importados).

### Qué tope se rompe primero y con qué N

| Tope | Valor | Se rompe con N ≈ | Medida |
|---|---|---|---|
| `MAX_REGISTRY_SYMBOLS` (también fija el largo máximo de `symbols` en index.json y health.json del validador) | 400 | **401** | por definición; 400 pasa todo con 0 errores |
| `MAX_BYTES["manifest"]` | 600.000 | **496-497** | 495 → 599.928 B; 497 → 602.476 B (~1,21 KB por símbolo) |
| `MAX_BYTES["index"]` | 120.000 | **722-723** | 721 → 119.749; 723 → 120.065 (~166 B por símbolo) |
| `MAX_SITE_BYTES` | 120.000.000 | **749-750** | 748 → 119,95 MB; 750 → 120,10 MB (~160 KB por símbolo) |
| `MAX_SITE_FILES` | 12.000 | **1.151-1.152** | 1.150 → 11.988; 1.152 → 12.010 (~10,4 ficheros por símbolo) |
| `MAX_BYTES["health"]` | 200.000 | **> 1.685** | 1.685 → 199.736 (~119 B por símbolo); a 5.000 pesa 600 KB |
| `MAX_BYTES` de `daily`, `latest`, `intraday`, `crypto` | 90.000 / 8.000 / 60.000 / 400.000 | **nunca** | no dependen de N (19,4 KB / 0,7 KB / 11,6 KB / 50 KB) |

El primero es `MAX_REGISTRY_SYMBOLS` (401) y, si se sube solo ese, el siguiente es el **manifest** (~496), mucho antes que el peso total o el número de
ficheros. Fuera de los topes del validador, en un run real lo más probable es que mande antes el **presupuesto de tiempo de Yahoo** (13 min,
`V2_YAHOO_BUDGET_SECONDS`): el run diario (hot + warm) hace ~1,04 peticiones por símbolo; con la separación mínima de `_yf_pace` (0,15-0,35 s + 5 s cada
40) el suelo es ~0,375 s por petición sin contar la latencia ⇒ ~2.000 símbolos como techo teórico; con 0,25-0,5 s de latencia real, ~1.200-1.500.
**Estimación, no medida** (necesita Yahoo real). Lo que no tocaba en el run se aplaza y se publica el resto; el banco no lo simula.

### Efecto de HISTORY_YEARS = 5

- Global (todos los niveles), medido: a N=500 el sitio baja de 80,0 a 40,0 MB y de 5.207 a 2.887 ficheros, manifest de 605 a 335 KB (vuelve a caber);
  a N=1.000, de 160,2 a 80,2 MB y de 10.420 a 5.775 ficheros (caben `MAX_SITE_BYTES` y `MAX_SITE_FILES`), manifest de 1,21 MB a 670 KB (sigue
  sin caber en 600 KB). index.json y health.json no cambian (no dependen de los años).
- Solo warm/cold (proyección por resta; necesitaría cambiar el código para que el corte dependa del nivel): a N=1.000 sobran 3.670 ficheros y 62,9 MB ⇒
  ~97,4 MB y ~6.750 ficheros (cabe), manifest ~0,79 MB (no cabe). A N=500: ~48,6 MB y ~3.370 ficheros.
- **Transición**: el validador rechaza el primer run tras el cambio («el sitio pesa menos del 90 % del anterior»); haría falta UN lanzamiento manual con
  `allow_regression=true`. Y el contrato (§3 de `CONTRATO_FEED_V2.md`) promete «10 años naturales»: cambiarlo es cambiar el contrato (fuera de alcance).

### Propuestas (no aplicadas; ningún tope tocado)

**Para 500 símbolos**
- `MAX_REGISTRY_SYMBOLS` 400 → 600 y `MAX_BYTES["manifest"]` 600.000 → 900.000 (a 500 pesa 605 KB; 900 KB deja margen hasta ~740). Nada más hace falta:
  index 83 KB, sitio 80 MB, 5.207 ficheros, health 60 KB caben.
- *Postura contraria:* cada tope es una alarma contra un generador desbocado; subirlos a ojo debilita esa alarma. Alternativa sin tocar topes: mantener
  400, pasar a `dormant` lo que no se mira y crecer solo cuando haga falta. Y +25 % de símbolos son +25 % de peticiones a Yahoo por run (más riesgo de
  429), coste que el banco no mide.

**Para 1.000 símbolos**
- `MAX_REGISTRY_SYMBOLS` → 1.200; `MAX_BYTES["manifest"]` → 1.500.000; `MAX_BYTES["index"]` → 200.000; y una de dos para el peso:
  (a) `MAX_SITE_BYTES` → 200 MB y `MAX_SITE_FILES` → 15.000 (a 1.000 hay 160 MB y 10.420 ficheros: 12.000 aguanta, pero a solo un 15 % del límite), o
  (b) 5 años de histórico para warm/cold (cambio de código y de contrato) y dejar `MAX_SITE_BYTES`/`MAX_SITE_FILES` como están.
- *Postura contraria a (a):* el coste real no es el validador sino que **cada run vuelve a descargar todo el sitio anterior fichero a fichero** y lo
  vuelve a subir como artefacto de Pages: con 160 MB y 10.000+ ficheros ese tiempo (no medido) puede comerse el `timeout-minutes: 25` del job `fetch`.
  Subir topes esconde ese coste. *Postura contraria a (b):* rompe la promesa de 10 años del contrato y exige una publicación con `allow_regression`;
  además el manifest seguiría pasando de 600 KB. *Postura contraria a subir index a 200 KB:* index.json es lo que la app descarga al abrir;
  166 KB sin comprimir es poco con gzip de Pages, pero partirlo (p. ej. por nivel) sería más sano que engordarlo; eso cambia el contrato.
- A 1.000, además, es probable que mande el presupuesto de Yahoo (ver arriba): el run diario rozaría los 13 min.

---

## 4. Desviaciones

1. **Una prueba nueva corregida antes de su primer verde (no es una prueba antigua).** `test_control_negativo_la_simulacion_detecta_un_reintento_sin_condicion`
   mutaba solo el `if` del reintento y esperaba que la simulación reintentase un fallo lento; pero el paso que decide seguía ahí y cortaba el job, así
   que la premisa era falsa (falló con el YAML bueno: `assert 1 == 2`). La corregí para que la mutación quite también el paso que decide (que es lo que
   dice su docstring). Ninguna prueba que ya pasase se ha modificado; ninguna prueba antigua se ha tocado.
2. **Fixture `sandbox` duplicada** en `test_deploy_reintento.py` (igual que hace `test_puerta_frescura_F1.py`): importarla de `test_workflows.py` daba
   F811 en ruff.
3. **El banco vive en `tests/`, no en `scripts/`** (`bench_escala_v2.py` no empieza por `test_`: pytest no lo recoge). Así `scripts/` queda intacto.
4. **No tengo salida completa propia de la batería en la base** (ver §1).
5. El punto 1 añade 3 pasos al job `publish` (marca de inicio, decisión, reintento) y cambia `page_url`/`environment.url` a «el del intento que publicó».
   Ninguna prueba antigua lo fijaba de otra forma (pasan todas sin tocarlas).
6. **Autor de los commits.** Configuré al principio el correo personal de Alex; antes de empujar vi que el repo usa la dirección *noreply* de GitHub
   y, al ser público, reescribí el autor de mis dos commits locales (sin empujar) con esa dirección. Solo cambian los metadatos y los hashes.

---

## 5. Lo no probado (y cómo comprobarlo)

- **Nada del punto 1 se ha ejecutado en GitHub Actions.** En el primer run real conviene mirar: que un deploy correcto deja el job verde con UN solo
  deploy y `page_url` relleno (el `verify` lo usa); y, si alguna vez falla el 1.er deploy, que el paso «Decide deploy retry» corre (prueba de que
  `steps.deployment.outcome` vale `failure` con `continue-on-error`). Si ese paso NO corriese tras un fallo, el job saldría verde sin publicar: el
  heartbeat mandaría OK y solo `verify` avisaría (por `/log`, informativo). Es el riesgo principal y no se puede probar sin Actions.
- **El error real «Fetching artifact metadata failed»** no se ha reproducido; el supuesto de que es rápido y anterior a crear el despliegue no se ha
  contrastado con el código de deploy-pages v5.0.1.
- **Dos llamadas a deploy-pages en el mismo job** (segundo token OIDC, mismo artefacto): debería funcionar, no está probado.
- `actionlint` no se ha ejecutado.
- **Banco:** tiempos sin red. No se ha medido la descarga del sitio anterior (`site_download.py --mode full`, fichero a fichero), la subida del artefacto,
  el despliegue ni las pausas de Yahoo; tampoco los límites propios de GitHub Pages / artefactos (no los he verificado en su documentación en esta sesión).
  Las series sintéticas no tienen festivos, huecos, splits ni barras descartadas.
- La regla de continuidad con sitios grandes solo se ha probado en el run «día siguiente» (sin bajas ni altas de símbolos).

---

## 1 bis. Comprobaciones finales (salidas en bruto)

Commits: punto 1 `2a811bd`, punto 2 `669ef66` (el contenido es el mismo con el que se pasó la batería; ver §4.6).

Controles negativos del banco (mutación de `tests/bench_escala_v2.py`, prueba, restauración):

```
== B1 la medición no mira MAX_SITE_FILES                    1 failed, 3 passed
== B2 reparto de niveles distinto del registro              1 failed, 3 passed
== B3 series sintéticas inválidas (high x3)                 4 passed
```

B3 **no se detecta, y es correcto**: un máximo 3 veces por encima del cierre es una barra válida para el contrato (el generador solo repara un
máximo/mínimo que no contiene apertura y cierre). La mutación estaba mal elegida; no indica un hueco de la prueba, pero tampoco demuestra nada.

```
$ ruff check --select F scripts tests
All checks passed!
$ python -m pytest -q -p no:cacheprovider
1734 passed, 1 warning in 231.74s (0:03:51)
$ python -m pytest -q -p no:cacheprovider tests/test_privacidad_isin.py tests/test_workflows_H4.py
39 passed in 0.75s
$ git diff 262fddf -- . ':!auditorias' (+ este informe) | grep -E "IE[0-9A-Z]{10}"
(sin salida)
$ ... | grep -iE "supabase|/products/[0-9]|eyJ...|sb_(secret|publishable)|service_role|@gmail|hc-ping\.com/<clave de 20+>|api[_-]?key=|FRED_API_KEY="
(sin salida)
```

Las únicas URL de Healthchecks en las pruebas nuevas son las sintéticas de siempre (`https://hc-ping.com/abc`); los símbolos del banco son inventados
(`SYN…`, `^SYN…`, `SYC…`); no hay ISIN, ids de ficha, URL de Supabase ni claves.
