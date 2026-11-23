# FIXES — Manifest a escala (formato de manifiesto 2, aditivo) y aviso al 50 % de cada tope

Rama `claude/manifest-escala` desde `main` en `26c301f`. Commits:

| Commit | Qué | Ficheros |
|---|---|---|
| 1 | Banco de medición del manifiesto (solo medición) | `tests/bench_manifest_escala.py` |
| 2 | Formato de manifiesto 2 por trozos (generador, validador, contrato) + aviso al 50 % de cada tope | `scripts/feed_v2.py`, `scripts/feed_v2_common.py`, `scripts/sanity_check_v2.py`, `scripts/site_download.py` (1 línea), `docs/CONTRATO_FEED_V2.md`, `README.md`, `tests/test_manifest_escala.py` (nuevo), `tests/test_contract_docs_v2.py`, `tests/test_sanity_check_v2.py`, `tests/test_site_download.py` |
| 3 | Este informe | `auditorias/FIXES_MANIFEST_ESCALA.md` |

No se ha tocado: `registry.json` (ningún símbolo añadido), ningún tope existente (`MAX_BYTES` de los tipos de siempre, `MAX_SITE_*`, `MAX_REGISTRY_SYMBOLS` siguen igual), los
workflows, `freshness_gate.py`, secretos, el feed v1. Sin red real: Yahoo, CoinGecko y Pages simulados; los tests y el banco bloquean la resolución de nombres no locales.
Sin ISIN, ids de ficha, URL de Supabase ni claves (todo es sintético: `SYN...`, `AAPL`, `SWDA.L` de los tests).

---

## 1. Lectura y medición (lo que hay hoy y qué se rompe primero)

### Lo leído

- `docs/CONTRATO_FEED_V2.md` §3 `manifest.json`: ruta → bytes + sha256 de **todos** los ficheros; el run siguiente lo usa para bajar el sitio anterior y el validador lo compara
  con el disco. README: el cliente decide cuándo mira `index.json` / `manifest.json`.
- **Quién lee `manifest.json`** (todos en este repo salvo el cliente): (a) el cliente del dueño (repo privado): lo baja entero cuando cambia el índice; (b) `site_download.py`
  en el job `fetch` (modo `full`: baja **todo** lo listado) y en `publish` (modo `validate`); **lo descarga con el límite `MAX_BYTES["manifest"]`** (`http_get(..., c.MAX_BYTES["manifest"])`);
  (c) `freshness_gate.py` (job `gate`, solo `generated_at`, límite propio de 4 MB); (d) los pasos `Decide deploy retry` y `verify` (solo `generated_at`); (e) el validador
  (`_check_manifest`, `check_continuity`).
- `MAX_REGISTRY_SYMBOLS` = 400 también fija el largo máximo de `symbols` en `index.json` y `health.json` en el validador.

### Medido (banco sintético, `tests/bench_manifest_escala.py`; registro `SYN...` con el reparto del actual: 21 % hot, 70 % warm, 9 % cold, 7 % cripto)

Calibración: con 249 símbolos (231 de Yahoo) el `manifest.json` sintético pesa **302,6 KB**; el publicado hoy pesa ≈294 KB (dato del encargo, no medido por mí: Pages no es
accesible desde esta sesión). El sintético pesa ~3 % más (261 barras/año sin festivos frente a ~252 reales, todos los decimales).

**Desglose de `manifest.json` por símbolo de Yahoo** (idéntico de 249 a 3.000; ~1,30-1,31 KB por símbolo):

| Parte | B por símbolo de Yahoo | % |
|---|---|---|
| Entradas `d/<AAAA>.json` (10 años) | 1.164,9 | 89 % |
| Entrada `latest.json` | 114,5 | 9 % |
| Entrada `i.json` (solo hot: 112 B por hot) | 23,5-28 | 2 % |
| Ficheros globales + cabecera | ~0,6 KB en total | — |

**Por campo** (N=3.000; el reparto es el mismo a cualquier N): `sha256` 64,5 % · ruta 20,1 % · `bytes` 11,0 % · puntuación 4,3 %. Comprimido con gzip -6 (N=249):
`manifest.json` 302.612 → 118.474 B (39 %).

**Topes por N** (sitio del día siguiente, `gen2`; «→» = valor / tope):

| | 400 (371 Yahoo) | 800 (743) | 1.500 (1.393) | 3.000 (2.786) | Se rompe hacia |
|---|---|---|---|---|---|
| `manifest.json` (600 KB) | 484 KB (81 %) | **969 KB ✗** | 1,82 MB ✗ | 3,63 MB ✗ | **~460 Yahoo** (~1,30 KB/símbolo) |
| `site_download.py` baja el manifest | sí (6,1 s local) | **NO: «respuesta demasiado grande»** | NO | NO | **igual que el anterior: el job `fetch` falla antes de generar** |
| `MAX_REGISTRY_SYMBOLS` (400) | 400 (100 %) | ✗ | ✗ | ✗ | 401 |
| `index.json` (120 KB) | 66 KB (55 %) | **133 KB ✗** | 250 KB ✗ | 500 KB ✗ | ~720 (bench anterior) |
| Sitio (120 MB) | 64,0 MB (53 %) | **128 MB ✗** | 240 MB ✗ | 480 MB ✗ | ~750 |
| Ficheros (12.000) | 4.164 | 8.336 (69 %) | **15.623 ✗** | 31.239 ✗ | ~1.150 (≈1.060 con el formato 2, ver §3) |
| `health.json` (200 KB) | 48 KB | 95 KB | 178 KB (89 %) | **355 KB ✗** | ~1.690 |
| Peticiones a Yahoo, run diario (hot+warm; simuladas) | 414 | 834 | 1.563 | 3.124 | — |
| Peticiones a Yahoo, run horario (solo hot = 2 × hot; **derivado del código**) | 154 | 314 | 588 | 1.174 | — |
| Alta inicial (cupo de 80 descargas largas por run) | 5 runs | 10 runs | 18 runs | 35 runs | — |
| CPU local: generar / validar (`gen2` / `val2`) | 3,6 s / 3,9 s | 7,6 / 7,8 s | 14,3 / 15,1 s | 26,7 / 33,0 s | — |

**Orden en que se rompe al crecer** (con el registro de hoy, 249): `MAX_REGISTRY_SYMBOLS` (401) → **`manifest.json` (~460 de Yahoo), que además deja al job `fetch` sin poder
bajar el sitio anterior** → `index.json` (~720) → peso del sitio (~750) → número de ficheros (~1.060-1.150) → presupuesto de Yahoo (~1.100-1.900, estimado) → `health.json` (~1.690).
Sin cambios, el manifiesto es el primer tope real después del del registro, y el único que **para el workflow** en vez de solo rechazar la publicación.

**Estimado, NO medido** (marcado así a propósito):

- **Tiempo de Yahoo por run**: cada petición cuesta ~max(separación 0,15-0,35 s, latencia real) + 5 s cada 40 ≈ **0,38-0,63 s** con 0,25-0,5 s de latencia (supuesta).
  Run diario: 400 → 2,6-4,3 min; 800 → 5,3-8,8; 1.500 → 9,9-16,4; 3.000 → 20-33 min frente al presupuesto de 13 min (`V2_YAHOO_BUDGET_SECONDS` = 780 s ⇒ ~1.240-2.050 peticiones ⇒ **hacia 1.100-1.900 símbolos
  el run diario ya no cabe** y se aplazan símbolos (no rompe nada, retrasa los warm). Run horario solo hot a 3.000: 7-12 min.
- **Descarga del sitio anterior** (job `fetch`, modo `full`): medida en local a 249 (2.605 ficheros, 4,2 s) y 400 (4.164, 6,1 s): es una cota inferior sin la latencia de Pages.
  Por encima de ~460 no se puede medir porque el manifiesto ya no se descarga.
- **Límites gratuitos** ([GitHub Pages limits](https://docs.github.com/en/pages/getting-started-with-github-pages/github-pages-limits)): sitio publicado ≤ **1 GB**; ancho de
  banda **100 GB/mes (blando)**; despliegue ≤ **10 min**; el límite de 10 builds/h no aplica a un workflow propio. ([GitHub Actions billing](https://docs.github.com/en/billing/concepts/product-billing/github-actions),
  [Actions runner pricing](https://docs.github.com/en/billing/reference/actions-runner-pricing)): los runners estándar son **gratis e ilimitados en repos públicos**.

  | | 249 | 400 | 800 | 1.500 | 3.000 |
  |---|---|---|---|---|---|
  | Sitio frente a 1 GB de Pages | 40 MB (4 %) | 64 MB (6 %) | 129 MB (13 %) | 242 MB (24 %) | **484 MB (48 %)** |
  | Ancho de banda/mes (solo la descarga `full` del job `fetch`, gzip 43 % medido, **~12 runs/día supuestos**) | ~6 GB (6 %) | ~10 GB | ~20 GB | ~37 GB | **~75 GB (75 %) ⚠** |

  **Aviso al 50 %**: el ancho de banda de Pages pasaría del 50 % hacia **~2.000 símbolos**, y lo consume sobre todo **el propio CI** (cada run vuelve a bajar el sitio entero), no
  el cliente; el tamaño del sitio pasa del 50 % de 1 GB hacia ~3.100. **Artefactos de Actions**: los docs consultados dan una cuota de almacenamiento (500 MB en Free) sin aclarar
  si los repos públicos están exentos, y la página de facturación no es accesible desde esta sesión: **no verificado**. Cada run sube dos artefactos del tamaño del sitio
  (`site-v2` y el de Pages, retención 1 día); si contasen, a 1.500-3.000 símbolos se pasaría de esa cuota. Comprobarlo en Settings → Billing antes de crecer.

---

## 2. Opciones de diseño (coste, postura contraria, recomendación)

Criterio común: el cliente actual y la build pública siguen sin cambios; el formato viejo se sigue publicando con su tope; el validador decide.

**A. Manifiesto por trozos, uno por símbolo, con raíz de huellas** — `manifest-root.json` (globales + bytes/sha256 del trozo de cada símbolo) + `s/<SYM>/manifest.json`
(ficheros del símbolo, **sin `generated_at`**, así que solo cambia si cambian sus ficheros). *Recomendada e implementada.*
- Coste medido: raíz **~102-104 B por símbolo** (24 KB a 249; 283 KB a 3.000; tope nuevo de 600 KB ⇒ ~5.500 símbolos), 12,6 veces menos que el formato 1; trozo 1,36 KB de media,
  1,47 KB el mayor; **+1 fichero por símbolo de Yahoo** (+9 % de ficheros: 2.605 → 2.837 a 249) y +0,85 % de bytes. CPU de generación y validación sin cambio apreciable.
- Postura contraria: sigue siendo O(N) (la raíz también crece) y añade N ficheros, que adelantan el tope de 12.000 de ~1.150 a ~1.060 símbolos. Y el ahorro para el cliente
  es menor de lo que parece: los trozos de los hot cambian en cada run (su `latest.json` lleva `generated_at`), así que a 3.000 símbolos un cliente que siguiera todos pediría
  ~590 trozos por run. En la práctica el cliente sigue a pocos símbolos y solo necesita el trozo para verificar años diarios, pero ese matiz es real.

**B. Trozos fijos por nivel o por cubo** (p. ej. `manifest-hot.json`, `-warm`, `-cold`, o 16 cubos por hash).
- Coste: +3-16 ficheros; cada trozo sigue llevando ~1,3 KB por símbolo de su grupo. A 3.000, el trozo hot (587 símbolos) pesaría ~0,77 MB y cambiaría en **cada** run; con cubos
  por hash, todos los cubos tienen algún hot y cambian todos en cada run.
- Postura contraria (a favor de B): muchos menos ficheros y peticiones que A; para el CI (que lo baja todo) es más barato. Pero no arregla el tamaño ni el «pedir solo lo
  que cambió».

**C. Separar lo mutable de lo inmutable** — `manifest-live.json` (latest, año en curso, intradía: ~254 B por símbolo) + `manifest-closed.json` (años cerrados: ~1.050 B por
símbolo, solo cambia en enero, con un alta o con un rebase).
- Coste: 2 ficheros. A 3.000 el «live» pesaría ~0,71 MB (no cabe en 600 KB) y el «closed» ~2,9 MB, que el cliente cachearía.
- Postura contraria (a favor de C): el cambio más pequeño y el que mejor cuadra con la caché del cliente. En contra: sigue O(N), se rompe hacia ~2.350 símbolos y el cambio de año
  reescribe el «closed» entero de golpe.

**D (mencionada, no recomendada). Subir el tope** a 4 MB (y el límite de descarga de `site_download.py`). Cero código; a 3.000 el cliente bajaría 3,6 MB (~1,4 MB con gzip)
en cada cambio del índice. Postura contraria: es honesto y para un único cliente basta; en contra, el tope deja de ser una alarma útil. Recortar el `sha256` a 16 caracteres
(−48 %) **no** es compatible hacia atrás si el cliente verifica el hash.

**Recomendación: A**, por ser la única que acota lo que baja el cliente al conjunto de símbolos que sigue y que cambiaron, ser aditiva y validarse fichero a fichero. Si al
crecer molesta el número de ficheros, se puede pasar a una variante de A con trozos por cubo de símbolos sin cambiar la raíz.

**Cambio de cliente que exige A (repo privado; descrito, no hecho):** al cambiar `index.json.generated_at`, pedir `manifest-root.json` en vez de `manifest.json`; guardar
`symbols` de la raíz; para cada símbolo seguido cuya huella cambió, pedir `s/<SYM>/manifest.json`, verificar bytes y sha256 contra la raíz y usarlo para verificar lo que baje de
ese símbolo; si la raíz da 404, volver a `manifest.json`. Sin ese cambio el cliente sigue funcionando igual (lee el formato 1). **Para pasar de ~460 símbolos hacen falta además**
(fuera de esta sesión): que `site_download.py` baje el sitio anterior por el formato 2 (en modo `full` son N peticiones más, o un índice de rutas propio para el CI), decidir qué
pasa con el formato 1 (retirarlo o dejar en él solo lo mutable: cambio de contrato), y resolver los topes siguientes (registro 400, `index.json` ~720, peso ~750).

---

## 3. Qué se implementó

- **Generador** (`feed_v2.py`, `build_manifest` / `build_sharded_manifest`): escribe `manifest.json` como siempre (ahora excluye explícitamente cualquier manifiesto) y después
  un trozo por símbolo con ficheros y la raíz. Salida determinista (rutas ordenadas, `c.dumps`).
- **Contrato de rutas** (`feed_v2_common.py`): clases `manifest_root` y `manifest_shard`, `shard_path()`, `MANIFEST_KINDS`, `MANIFEST_FORMAT = 2`, topes nuevos
  `MAX_BYTES["manifest_root"]` = 600.000 y `["manifest_shard"]` = 4.000, `HEADROOM_WARN_FRACTION = 0.5`.
- **Validador** (`sanity_check_v2.py`): esquemas estrictos de raíz y trozo (lista blanca; el trozo no admite `generated_at`); `manifest-root.json` obligatorio; formato 1 =
  exactamente los ficheros de datos (sin manifiestos); formato 2 comprobado **contra el disco** (globales, conjunto de símbolos, trozo por símbolo, huellas de trozos y ficheros,
  `total_bytes`, `generated_at` igual al del formato 1). Errores agregados (uno por regla). Cada fichero se lee y se hashea una sola vez.
- **Aviso al 50 %** de cada tope (`_check_headroom`): `MAX_BYTES` de cada tipo (el fichero mayor), `MAX_SITE_FILES`, `MAX_SITE_BYTES`, `MAX_REGISTRY_SYMBOLS`, en la franja
  [50 %, 100 %]: un `::warning::` en una línea; **no bloquea ni es `/fail`** de Healthchecks. Si el que pasa es el de `manifest.json`, el texto dice que el cliente debería pasar
  al formato 2 y que `site_download.py` no podrá bajarlo por encima del tope.
- **`site_download.py`**: una línea; `manifest.json` no puede listar ningún manifiesto (antes rechazaba solo «él mismo»; las rutas nuevas eran «fuera del contrato» y ahora no).
- **Contrato** §1, §3 (formato 1 explicado y con su límite; formato 2 con ejemplos generados por el test) y §4; README (tabla del feed).

---

## 4. Comandos ejecutados y salidas en bruto

Entorno: Python 3.13 local (CI usa 3.14), venv con `pip install --require-hashes --no-deps -r requirements.txt` y `-r requirements-dev.txt` (ruff 0.17.3).

**Línea base** (`main` en `26c301f`, antes de tocar nada):
```
$ python -m pytest -q -p no:cacheprovider
2326 passed, 1 warning in 193.32s (0:03:13)
```

**Medición «antes»** (copia de `26c301f` en un worktree aparte para que el código en edición no se mezclase):
```
$ python tests/bench_manifest_escala.py --n 249 400 800 1500 3000 --work <tmp> --out medicion_antes.json
```
(La primera pasada se cortó a 800 con `site_download.DownloadError: respuesta demasiado grande` en la descarga local: así encontré que el job `fetch` no puede bajar un
manifest > 600 KB. Añadí al banco la captura de ese error y repetí 800/1.500/3.000.) Resumen en bruto (`resumen.py`, al final de la sección) de `gen2`/`val2`:
```
N=249 yahoo=231 hot=58 warm=156 cold=17
  manifest.json total=302611 entradas=2604 B/sym_yahoo=1310.0 por_sym={'daily': 1164.9, 'intraday': 28.0, 'latest': 114.5}
  por_campo {'ruta': 60824, 'bytes': 33384, 'sha256': 195300, 'puntuacion': 13020}
  sitio bytes=39940904 files=2605 index=41311 health=29927
  topes_50 ['MAX_REGISTRY_SYMBOLS 62 %', 'MAX_BYTES[manifest] 50 %']
   gen2 2.2s 111.5MB yahoo_calls=272 due=214 backfills=0
   val2 2.3s 109.8MB errores=0 []
  descarga_local_full {'segundos': 4.17, 'ficheros': 2605, 'bytes': 39927578}
N=400 yahoo=371 hot=77 warm=260 cold=34
  manifest.json total=483849 entradas=4163 B/sym_yahoo=1304.2
  sitio bytes=63982954 files=4164 index=66408 health=47757
  topes_50 ['MAX_REGISTRY_SYMBOLS 100 %', 'MAX_SITE_BYTES 53 %', 'MAX_BYTES[manifest] 81 %', 'MAX_BYTES[index] 55 %']
   gen2 3.6s yahoo_calls=414 due=337 ; val2 3.9s errores=0
  descarga_local_full {'segundos': 6.1, 'ficheros': 4164, 'bytes': 63961552}
N=800 yahoo=743 hot=157 warm=520 cold=66
  manifest.json total=968720 entradas=8335 B/sym_yahoo=1303.8
  sitio bytes=128180561 files=8336 index=132885 health=95105
  topes_rotos ['MAX_REGISTRY_SYMBOLS (800 > 400)', 'MAX_BYTES[manifest] (968720 > 600000)', 'MAX_BYTES[index] (132885 > 120000)', 'MAX_SITE_BYTES (128180561 > 120000000)']
   gen2 7.6s yahoo_calls=834 due=677 ; val2 7.8s errores=5
  descarga_local_full {'error': 'respuesta demasiado grande'}
N=1500 yahoo=1393 hot=294 warm=975 cold=124
  manifest.json total=1815612 entradas=15622 B/sym_yahoo=1303.4
  sitio bytes=240275982 files=15623 index=249698 health=177844
  topes_rotos [... 'MAX_BYTES[manifest] (1815612 > 600000)', 'MAX_BYTES[index] (249698 > 120000)', 'MAX_SITE_BYTES (240275982 > 120000000)', 'MAX_SITE_FILES (15623 > 12000)']
  topes_50 ['MAX_BYTES[health] 89 %']
   gen2 14.3s yahoo_calls=1563 due=1269 ; val2 15.1s errores=7
  descarga_local_full {'error': 'respuesta demasiado grande'}
N=3000 yahoo=2786 hot=587 warm=1950 cold=249
  manifest.json total=3630488 entradas=31238 B/sym_yahoo=1303.1
  por_campo {'ruta': 730846, 'bytes': 400518, 'sha256': 2342850, 'puntuacion': 156190}
  sitio bytes=480230917 files=31239 index=500442 health=355147
  topes_rotos [... 'MAX_BYTES[health] (355147 > 200000)', 'MAX_BYTES[manifest] (3630488 > 600000)', 'MAX_BYTES[index] (500442 > 120000)', 'MAX_SITE_BYTES (...)', 'MAX_SITE_FILES (31239 > 12000)']
   gen1 200.2s 141.0MB yahoo_calls=3373 backfills=2786 ; gen2 26.7s 142.1MB yahoo_calls=3124 due=2537 ; val2 33.0s 175.8MB errores=8
  descarga_local_full {'error': 'respuesta demasiado grande'}
```
(Recortado solo en las líneas repetidas `por_sym`/`por_campo`, idénticas en proporción a las mostradas.)

**Rojo** (los tests nuevos contra el código de `main`, worktree en `26c301f`):
```
$ python -m pytest -q -p no:cacheprovider tests/test_manifest_escala.py
25 failed, 7 passed in 18.08s
PASSED ...::test_el_sitio_pasa_el_validador_con_y_sin_sitio_anterior
PASSED ...::test_cliente_viejo_sigue_funcionando_con_el_manifest_clasico
PASSED ...::test_sin_aviso_de_tope_cuando_todo_queda_por_debajo_del_50
PASSED ...::test_por_encima_del_tope_es_error_no_aviso
PASSED ...::test_site_download_rechaza_un_manifest_clasico_que_lista_un_manifiesto[manifest.json|manifest-root.json|s/AAPL/manifest.json]
```
Los 7 que pasan en `main` son **guardas de regresión** (deben seguir verdes): lo que ya era cierto y no debe dejar de serlo. Los fallos son `AttributeError` (no existen
`MANIFEST_ROOT`, `shard_path`, `MANIFEST_KINDS`) y avisos que no salen.

**Verde** (tras el cambio):
```
$ python -m pytest -q -p no:cacheprovider tests/test_manifest_escala.py
32 passed
$ ruff check --select F scripts tests
All checks passed!
$ python -m pytest -q -p no:cacheprovider
2358 passed, 1 warning in 220.66s (0:03:40)
```
(2326 + 32 nuevos = 2358. El aviso es el `DeprecationWarning` de `feedparser` de siempre.)

**Controles negativos sobre el CÓDIGO** (mutación del fichero real, `test_manifest_escala.py` + `test_site_download.py` + `test_contract_docs_v2.py`, restauración tras cada una;
`git status` limpio después):
```
== M1 trozo con generated_at                                      24 failed, 52 passed
== M2 sin formato 2 en el generador                               26 failed, 48 passed, 2 errors
== M3 filtro de manifiestos del formato 1 quitado (generador)     76 passed            <- sobrevive: mutante EQUIVALENTE (ver §5)
== M4 validador sin _check_manifest_v2                            10 failed, 66 passed
== M5 validador no mira la huella de los ficheros del trozo       2 failed, 74 passed
== M6 validador no exige la raíz                                  1 failed, 75 passed
== M7 aviso con < en vez de <=                                    1 failed, 75 passed
== M8 validador: formato 1 vuelve a exigir todos los ficheros     19 failed, 57 passed
== M9 site_download acepta manifiestos de formato 2 en manifest.json 2 failed, 74 passed
== M10 validador sin comprobar generated_at de la raíz            1 failed, 75 passed
```

**Contrato** (ejemplos regenerados y comprobados):
```
$ UPDATE_CONTRACT_DOCS=1 python -m pytest -q -p no:cacheprovider tests/test_contract_docs_v2.py
2 passed
$ python -m pytest -q -p no:cacheprovider tests/test_contract_docs_v2.py
2 passed
$ git diff docs | grep '^-'
--- a/docs/CONTRATO_FEED_V2.md
-https://<usuario>.github.io/<repo>/v2/manifest.json             ruta -> bytes + sha256 de todo lo anterior
-### `manifest.json`
-Ruta → bytes y sha256 de **todos** los ficheros (menos él mismo). El run siguiente verifica con él cada descarga del sitio anterior, y el validador comprueba que coincide con el
-disco. Un cliente que quiera integridad puede verificar lo que descarga.
```
Ninguna línea de ningún ejemplo JSON cambia: **el ejemplo de `manifest.json` es idéntico byte a byte** (mismo `total_bytes` 1160565, mismos sha256).

**Medición «después»** (worktree en el commit 2; `--no-download`):
```
N=249   manifest.json total=302611  formato2 {'root_bytes': 24087,  'shards': 231,  'shard_max': 1467, 'shard_media': 1363.8}  sitio bytes=40280040  files=2837  val2 2.4s errores=0
N=400   manifest.json total=483849  formato2 {'root_bytes': 38296,  'shards': 371,  'shard_max': 1467, 'shard_media': 1359.0}  sitio bytes=64525446  files=4536  val2 3.8s errores=0
N=800   manifest.json total=968720  formato2 {'root_bytes': 76054,  'shards': 743,  'shard_max': 1467, 'shard_media': 1359.5}  sitio bytes=129266696 files=9080  val2 7.5s
N=1500  manifest.json total=1815612 formato2 {'root_bytes': 142025, 'shards': 1393, 'shard_max': 1467, 'shard_media': 1359.4} sitio bytes=242311699 files=17017 val2 14.6s
N=3000  manifest.json total=3630488 formato2 {'root_bytes': 283403, 'shards': 2786, 'shard_max': 1467, 'shard_media': 1359.4} sitio bytes=484301581 files=34026 val2 31.0s 203.6MB
```
`manifest.json` pesa **exactamente lo mismo** antes y después a todos los N (302.611 … 3.630.488): el formato 1 no cambia a escala.

**Compresión** (N=249, gzip -6 por fichero):
```
manifest.json                302612 B  gzip -6  118474 B  (39 %)
manifest-root.json            24087 B  gzip -6   10326 B  (43 %)
index.json                    41311 B  gzip -6    1925 B  (5 %)      <- nombres sintéticos repetitivos: NO representativo
s/SYN0000/manifest.json        1441 B  gzip -6     656 B  (46 %)
sitio entero 40266715 B  gzip -6 (por fichero) 17383038 B (43 %)
```

`resumen.py` (script de lectura del JSON del banco; no se versiona):
```python
import json, sys
for f in sys.argv[1:]:
  for r in json.load(open(f)):
    m=r["manifest"]; s=r["site2"]
    print("N=%d yahoo=%d hot=%d warm=%d cold=%d"%(r["n"],r["yahoo"],r["hot"],r["warm"],r["cold"]))
    print("  manifest.json total=%d entradas=%d B/sym_yahoo=%s por_sym=%s"%(m["total"],m["entradas"],m["por_simbolo_yahoo_total"],m["por_simbolo_yahoo"]))
    print("  por_campo",m["por_campo"]); print("  formato2",r.get("sharded"))
    print("  sitio bytes=%d files=%d index=%d health=%d"%(s["total_bytes"],s["files"],s["index_bytes"],s["health_bytes"]))
    print("  topes_rotos",r["topes_rotos"]); print("  topes_50",r["topes_50"])
    for ph in ("gen1","gen2","val2"):
        x=r[ph]; print("   %s %.1fs %sMB yahoo_calls=%s due=%s backfills=%s errores=%s %s"%(ph,x["seconds"],x["max_rss_mb"],x.get("yahoo_calls"),x.get("stats",{}).get("due"),x.get("backfills"),x.get("errors"),(x.get("errors_sample") or [])[:4]))
    print("  descarga_local_full",r.get("descarga_local_full"))
```

---

## 5. Qué demuestra cada prueba y qué NO

| Prueba | Demuestra | NO demuestra |
|---|---|---|
| `test_el_generador_escribe_raiz_y_un_trozo...` | Raíz y trozos existen, listan exactamente lo que hay en disco (recalculado en el test con `hashlib`, no con el código del generador) y el trozo no lleva `generated_at`. | Nada sobre Pages real. |
| `test_el_manifest_clasico_no_cambia...` + ejemplo del contrato idéntico + banco antes/después idéntico en bytes | El formato 1 lista **los mismos ficheros** que antes y pesa lo mismo; sus claves no cambian. | Que el cliente privado no tenga otra dependencia (p. ej. contar ficheros del sitio por otra vía): no he visto su código. |
| `test_un_trozo_solo_cambia...` / `test_cliente_nuevo_pide_solo...` | Dos runs seguidos: los trozos de los warm no refrescados son idénticos byte a byte; solo cambian los hot; un cliente que compare la raíz pide solo esos. | Que en real no cambie algo más (p. ej. un rebase reescribe años: el trozo cambia, correcto). |
| `test_cliente_viejo_sigue_funcionando...` | El algoritmo del cliente actual (índice → manifest entero → latest de cada activo verificado) funciona sobre un sitio nuevo y el esquema viejo acepta el manifest. | Es mi **reconstrucción** del algoritmo descrito en el encargo y en el contrato §4, no el código real del cliente. |
| `test_la_descarga_del_ci_y_el_run_siguiente...` | `site_download` (modo full) baja un sitio nuevo por HTTP local, no ve los trozos, el run siguiente genera desde esa copia y el validador lo acepta con sitio anterior. Es la primera publicación tras fusionar. | Pages real (gzip, CDN, tiempos). |
| 14 mutaciones del sitio (`test_control_negativo_el_validador_rechaza`) + topes de raíz/trozo + trozo de un símbolo fuera del registro | Cada incoherencia del formato 2 **bloquea** (`errors` no vacío, con el motivo esperado), partiendo de un sitio verde. | Mutaciones que no he imaginado. |
| Avisos al 50 % (5 tests) | Avisa a partir del 50 % exacto (y no un byte por debajo), de cada tipo de tope; no es `Alert`; por encima del tope es error y no aviso. | Que alguien lea el aviso: es una anotación del run, no una notificación. |
| `test_site_download_rechaza...` | Un `manifest.json` que liste un manifiesto se rechaza; pasa también en `main` (guarda) y el mutante M9 demuestra que la línea nueva es la que lo sostiene. | — |
| Mutantes M1-M10 | Cada pieza nueva del código tiene al menos un test que muere si se rompe, salvo M3. | **M3 es equivalente**: cuando se escribe `manifest.json` aún no se ha escrito ningún manifiesto en ese run, así que quitar el filtro no cambia la salida. Es defensivo (si un día cambia el orden). Ningún test puede matarlo sin cambiar el orden del código. |
| Banco | Bytes, ficheros, topes, peticiones simuladas, CPU y memoria por fase. | Tiempo real de Yahoo y de Pages, peso de series reales. |

**¿Qué prueba podría repetir el mismo error que el código?** El validador **no** reconstruye el formato 2 con el código del generador: lo comprueba contra el disco con reglas
propias, y los tests calculan los hashes con `hashlib` por su cuenta. El helper `pack()` de `test_sanity_check_v2.py` sí reconstruye el formato 2, pero escrito aparte (no
importa el generador). Lo que comparten generador, validador y tests es `feed_v2_common.classify_path`: si esa función clasificase mal una ruta (p. ej. un trozo como fichero
de datos), los tres se equivocarían igual; lo mitiga que los tests comparan contra listas de rutas escritas a mano (`s/AAPL/manifest.json`, `manifest-root.json`).

**¿Qué tendría que pasar para que el cambio fuese incorrecto?** (1) Que el cliente privado trate como error un fichero que no esté en `manifest.json` pero sí en el sitio
(no puede listar el sitio, así que no lo veo posible, pero no he visto su código). (2) Que Pages o el despliegue no admitan ~230 ficheros más (muy improbable: hoy son ~2.600 de
un límite de 1 GB sin límite de ficheros documentado). (3) Que algún consumidor dependa de que `manifest.json` liste **literalmente todo** el sitio: el contrato decía «todos los
ficheros (menos él mismo)» y ahora dice «todos los ficheros de datos»; lo listado es lo mismo que antes, pero el sitio tiene ficheros nuevos fuera de esa lista.

---

## 6. Decisiones y supuestos (con la postura contraria; decide Alex)

1. **Trozo por símbolo bajo `s/<SYM>/manifest.json`** (y no en una carpeta aparte). Todo lo de un símbolo queda junto y reutiliza la validación del símbolo de la ruta.
   *Contraria:* `m/<SYM>.json` separaría «datos» de «índices» y facilitaría excluirlos; el nombre `manifest.json` repetido puede confundir al leer rutas.
2. **Los manifiestos no se listan entre sí** (el formato 1 no incluye los trozos ni la raíz). Así el formato 1 no crece y el cliente viejo no ve nada nuevo. *Contraria:*
   listarlos daría integridad a los trozos desde el formato 1, a costa de ~+9 % de tamaño del formato 1 justo en el tope que nos preocupa.
3. **Raíz obligatoria** desde ya (falta = no se publica). *Contraria:* hacerla opcional durante una transición; no hace falta porque generador y validador salen del mismo commit.
4. **Huella completa (sha256 de 64 caracteres)** en la raíz, no recortada. *Contraria:* 16 caracteres bastan para «¿cambió?» y dejarían la raíz en ~55 B/símbolo; pero el
   mismo campo se usa para verificar la descarga del trozo.
5. **Topes nuevos**: raíz 600 KB (~5.500 símbolos), trozo 4 KB (≤ 16 ficheros). Valores por defecto míos con holgura sobre lo medido (1,47 KB el mayor trozo).
6. **El aviso al 50 % es `::warning::`, no `Alert`.** Hoy saltará en cada run por `MAX_REGISTRY_SYMBOLS` (249/400 = 62 %) y, en cuanto el manifest real pase de 300 KB
   (~6 símbolos más), también por el manifest. *Contraria:* un aviso que sale siempre se acaba ignorando; mandarlo a Healthchecks como `/fail` taparía los avisos de datos
   degradados de verdad. Alternativa: escribirlo en el resumen del run (`$GITHUB_STEP_SUMMARY`) — cambia el workflow, fuera de este encargo.
7. **Límites gratuitos sin aviso automático** (Pages 1 GB y 100 GB/mes, artefactos): no se pueden medir desde el validador; los dejo estimados en §1. El 1 GB de Pages ya lo
   cubre de sobra `MAX_SITE_BYTES` (120 MB).

---

## 7. Desviaciones (listadas desde el principio)

1. **REQUISITO CAMBIADO — `tests/test_site_download.py::test_modo_full_baja_todo_y_verifica`.** Afirmaba «el modo full baja el sitio entero» (`listing(dest) == listing(pub)`).
   Ahora el modo full baja lo que lista `manifest.json` más él mismo; los manifiestos de formato 2 no se bajan (el generador los rehace en cada run). El test compara contra el
   sitio menos esos ficheros **y** añade la aserción de que el sitio publicado sí los lleva. No se ha debilitado la verificación de bytes.
2. **`tests/test_sanity_check_v2.py`, helper `pack()`** (simula a un atacante que reescribe el manifiesto tras manipular ficheros, para aislar las reglas de continuidad):
   ahora reescribe también el formato 2. Medido: con el `pack()` de `main` (y solo la limpieza del punto 3), `tests/test_sanity_check_v2.py` da `5 failed, 107 passed`: esos 5 fallarían por «el manifiesto no coincide» en vez de por la regla que prueban.
3. **`tests/test_sanity_check_v2.py::test_simbolo_fuera_del_registro_o_de_cripto_no_lleva_ficheros`**: su limpieza (`os.remove` + `os.rmdir`) pasa a `shutil.rmtree`, porque
   la carpeta lleva el trozo que escribe `pack()`. Las aserciones no cambian.
4. **Un test nuevo corregido antes de su primer verde:** la aguja de texto de «trozo sin un fichero del símbolo» decía «no lista exactamente» y el mensaje real es «no listan
   exactamente…». Se corrigió la aguja, no el código.
5. **`site_download.py`** no estaba en la lista de ficheros a tocar: una línea, para que conserve el comportamiento de `main` (rechazar un `manifest.json` que liste un manifiesto);
   sin ella, las rutas nuevas pasarían a ser aceptadas ahí. Lo cubre M9.
6. **La medición «antes» se repitió**: la primera pasada se cortó en 800 por la excepción de descarga (hallazgo de §1); el banco se ajustó para registrar ese error.

---

## 8. Pendiente, no probado y riesgo para lo publicado hoy

- **No probado contra servicios reales**: Pages (despliegue con ~230 ficheros más, gzip, CDN, tiempos de descarga) y Yahoo (latencia, 429). Todo es simulado o local.
- **No verificado**: si el almacenamiento de artefactos de Actions cuenta en repos públicos (§1); el número real de runs al día (supuse ~12 para el ancho de banda).
- **No hecho (fuera del encargo):** el cambio del cliente (repo privado); que `site_download.py` use el formato 2; retirar o recortar el formato 1; subir cualquier tope.
  **Mientras no se hagan, el universo de Yahoo no puede pasar de ~460 símbolos** sin parar el workflow; y antes llega el tope del registro (400).
- **Riesgo para lo publicado hoy — evaluado, no encuentro nada que parar:** el primer run tras fusionar publica los mismos ficheros de datos y el mismo `manifest.json`,
  más `manifest-root.json` y ~232 trozos (+0,85 % de bytes). La continuidad («el sitio no encoge», símbolos que desaparecen, años cerrados) usa solo el formato 1, que no cambia;
  la puerta de frescura y `verify` solo leen `generated_at` de `manifest.json`. Probado en local el ciclo publicar → bajar con `site_download` → generar → validar con sitio
  anterior. Efecto visible: un `::warning::` nuevo en cada run (registro al 62 %), sin `/fail` en Healthchecks.
