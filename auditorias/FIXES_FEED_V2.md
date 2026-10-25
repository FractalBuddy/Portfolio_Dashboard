# Feed genérico v2 (auditoría 08: BE-01..BE-09, sección 5)

Rama `feat/feed-v2`, creada desde `main` (`3b16bb4`, con el merge de `fix/readme-hc`). Fuente: `auditorias/08_backend.md` (rama `audit/08-backend`) y el diseño del encargo.
Un commit por paso, la rama verde en cada commit (`pytest` + `ruff check --select F scripts tests`).

**Resultado:** `pytest` completo **878 passed** (500 al empezar), `ruff check --select F scripts tests` limpio. Todo con datos sintéticos.
**No se contactó con Yahoo, iShares, CoinGecko, FRED, CNN, Supabase ni GitHub** (solo con PyPI para instalar `requirements.txt`/`requirements-dev.txt` con `--require-hashes`, y con
los remotos de este repo para `git fetch`/`push`). No se tocaron secretos ni la configuración del repo, ni se añadieron dependencias (los hashes de `requirements.txt` siguen vigentes).
Los tests corrieron en **Python 3.13** (el único disponible en la sesión); la CI usa 3.14 y no se ha podido probar aquí.

## Qué se hizo

| Paso | Commit | Qué |
|---|---|---|
| 1. Registro | `4466b1c` | `registry.json`: **242 símbolos** públicos y neutros (24 índices, 17 materias primas, 22 divisas, 79 acciones de EE. UU. y 27 de otras bolsas, 42 ETF de EE. UU., 13 UCITS, 18 mayores cripto). Campos `symbol, kind, name, currency, unit, tier, status, fail_streak`. `scripts/feed_v2_common.py` (solo stdlib): regex de símbolo del contrato **más** rechazo de `.`, `..` y punto inicial (la regex los admite y serían recorrido de ruta), `quote_symbol`, familias de divisa (`GBp`≠`GBP`, `GBp`=`GBX`), validación del registro, contrato de rutas y topes de tamaño |
| 2. Generador | `1da60a5` | `scripts/feed_v2.py`: histórico incremental (~1 mes y fusión por fecha; alta con 10 años y cupo de 80 por run; años cerrados copiados byte a byte; detección de split por mediana de cierres solapados), niveles hot/warm/cold, intradía solo hot, símbolos muertos sin bloquear (`stale`→`invalid`→reintento semanal, `delisted`), caída global de Yahoo sin penalizar, divisa/unidad verificadas contra Yahoo (si no concuerdan **no se publica**), cripto en lotes de ≤250, macro (FX, tipos, IPC, VIX, Fear & Greed, calendario FRED), noticias (`watchlist`), `health.json`, `index.json`, `manifest.json`. Reutiliza `fetch_market_data.py` (`yf_history` con 429/jitter/interruptor de racha, presupuestos BE-12, FRED, CNN, CoinGecko, RSS, iShares). Cambio en v1: `build_market_mood(prev_feed=None, include_altcoin=True)` (por defecto, idéntico) |
| 4. Validador (antes del workflow) | `47d4ac9` | `scripts/sanity_check_v2.py` y `scripts/site_download.py` (ver abajo) |
| 3. Workflow | `3669ca0` | `.github/workflows/publish-feed-v2.yml`: `fetch` (`contents: read`) → `publish` (**solo** `pages: write` + `id-token: write`, sin código de terceros) → `verify` (informativo) y `heartbeat`. `concurrency`, `timeout-minutes`, acciones por SHA, `deploy-pages`, ningún commit a ninguna rama |
| 5. Datos propios | `27234a1` | `scripts/personal_v2.py` + job `personal` (ver abajo) |
| 6. Contrato | `2d7a39b` | `docs/CONTRATO_FEED_V2.md` con ejemplos JSON **generados por un test** (`tests/test_contract_docs_v2.py`; `UPDATE_CONTRACT_DOCS=1` los regenera) |
| 7. README | `3d34d71` | pipeline v2, activación de Pages, secretos y variables, mediciones, riesgo de ToS, purga BE-09, corte; un test comprueba que cada secreto/variable del workflow y los avisos clave están en el README |
| Revisión propia | `3b66aaa` | robustez ante datos externos raros (ver «Fallos que encontré») |

> Orden: el validador y el descargador van **antes** del workflow (el paso 3 del encargo los invoca; un workflow sin ellos no sería coherente ni verde).

### Validador y descarga (BE-05/06/07)
* **Esquema estricto por fichero**: lista blanca de rutas (lo demás es error), de símbolos (solo los del registro), de claves, regex y topes por campo, ningún `<` ni `>` ni control en ningún string (claves incluidas), sin enlaces simbólicos, tope por fichero y total. `currency`, `unit`, `name` y `price_unit_divisor` de cada serie deben **coincidir con el registro**: un `fetch` comprometido no puede cambiar una unidad.
* **Series**: fechas ISO estrictamente crecientes y no futuras, `close` finito > 0, `low ≤ open,close ≤ high`, `latest.json` coherente con la última barra, `manifest.json` coherente con el disco (bytes y sha256).
* **Continuidad contra el sitio anterior**: `0,5 < nuevo/anterior < 2` por símbolo, mediana de cierres solapados ≈ 1 (±5 %; atrapa el ×100 aunque cambie todo el histórico), años cerrados sin tocar ni desaparecer, símbolos activos que no desaparecen (tolerancia máx(2, 3 %)), el sitio no encoge (≥90 %), `generated_at` no retrocede. `SANITY_ALLOW_REGRESSION` las convierte en avisos; las absolutas siguen bloqueando.
* **BE-06**: `site_download.py` (stdlib) trata **solo** un 404 en `manifest.json` como primer run; red, 5xx tras reintentos, JSON inválido, un fichero listado que da 404 o bytes/sha256 distintos del manifiesto → código 1 y no se publica. `publish` hace su **propia** descarga (no se fía de la de `fetch`) y `EXPECT_PREV=true` hace que el validador exija la comparación.

### Casos R2–R6 del apéndice A
| Caso | Test (`tests/test_sanity_check_v2.py`) |
|---|---|
| R2 ×100 contra el anterior | `test_R2_*` (×100, ×0,01, ×10, ×2,5, ×0,4; solo latest; solo el diario del año; también los años cerrados; deriva del 3 % pasa y la del 6 % no; `allow_regression`) |
| R3 close 0/None/−5, duplicados, orden inverso | `test_R3_*` (además `inf`/`NaN`, `low/high` incoherentes, columnas desiguales, volumen y fechas inválidas) |
| R4 NAV ×10 | el feed público v2 no lleva NAV: se prueba donde está, en `tests/test_personal_v2.py::test_R4_*` (×10, ×0,1, ×2 y ×2,02 no se publican; ×1,2 sí) |
| R5 fechas futuras / histórico congelado | `test_R5_*` (barra futura; todas las últimas barras a +400 días; control congelado) |
| R6 claves y strings hostiles | `test_R6_*` (14 mutaciones: `<img…>`, claves extra, `javascript:`, control, ficheros fuera del contrato…) |
| BE-06 | `test_BE06_*` y `tests/test_site_download.py` (42 tests con un servidor HTTP local) |

### Datos propios (Supabase)
* RPC `personal_watch_for_backend(p_token)` y `publish_personal_feed(p_token, p_user, p_payload)`; **solo** `apikey` con la clave publicable (sin `Authorization`, sin seguir redirecciones) y el token en el cuerpo. Una clave que parece secreta (`sb_secret_`, `service_role`, JWT con ese rol) aborta **sin enviar nada** (`::error::`, salida 1), igual que una URL no `https` o un token de formato raro.
* Payload ≤ 1 MB (se recorta: series a 1 año, sin histórico, sin series): NAV por ISIN (iShares), *facts* y series ≤ 2 años **solo** de lo que no está en el universo público; sin divisa conocida no se publica; NAV con salto fuera de 0,5–2× del anterior no se publica.
* Opcional: sin Supabase/token o ante cualquier fallo → `::warning::` y salida 0; el feed público sale igual (el job es independiente y `continue-on-error`).
* **Privacidad**: los logs de un repo público son públicos. Solo se escriben contadores; se descarta la salida de las funciones reutilizadas (que citan ISIN) y se emite `::add-mask::` para los ISIN; el payload nunca va en un artefacto; las URL de ficha solo se piden si son `https://*.ishares.com/...` (18 URL hostiles probadas: ninguna se pide); el token solo está en los dos pasos de biblioteca estándar (`python3 -S`) y no en el paso que ejecuta código de terceros.
* Prueba contra un Supabase simulado (servidor HTTP local, 96 tests). **Todos los ISIN, símbolos, nombres y tokens de tests y docs son falsos**; un test comprueba que ningún fichero nuevo contiene ISIN, URL ni nombres de `YOUR_FUNDS` (los lee del código v1 dentro del test, sin repetirlos).

## Mediciones (peticiones por run y tamaño total)

Del test `tests/test_feed_v2.py::test_medicion_de_peticiones_y_tamano_con_el_registro_real` (registro real: 224 símbolos de Yahoo = 49 hot / 153 warm / 22 cold; Yahoo simulado;
`V2_MEASURE=1 pytest -s tests/test_feed_v2.py -k medicion`):

| Run | Llamadas a Yahoo | Tamaño total |
|---|---|---|
| Alta 1 / 2 / 3 (cupo de 80 descargas largas por run) | 129 / 80 / 64 | 13,2 → 26,4 → 37,0 MB |
| Run normal solo hot (3 de los 4 del día) | 98 = 49 diarias de 1 mes + 49 intradía | +~30 KB |
| Run con warm (1 de los 4) | 251 = 202 diarias + 49 intradía | +~0 |
| **Por día** | **≈ 545** (v1: ~277 por run × 4 ≈ 1100 para 58 símbolos) | |
| Sitio en estado estable | | **37,0 MB en 2518 ficheros**; ~14,5 MB comprimidos (zlib -6, fichero a fichero); `manifest.json` 285 KB; por tipo (máx.): diario 17 KB, `latest` 0,6 KB, `index` 34 KB, `health` 28 KB |

Cotas honestas: (a) la serie sintética lleva 6 decimales en todos los precios, así que el tamaño real de Yahoo (2-4 decimales) será menor; (b) «llamadas» son llamadas de la aplicación
a `yf_history`; las peticiones reales a Yahoo (con reintentos) las cuenta `health.json.run.yahoo_requests` en cada run real; (c) el tiempo de la descarga del sitio anterior (~3 min con 16 hilos)
es una **estimación**, no está medido contra Pages. Por run, además: 1 llamada a CoinGecko, ~5 a FRED, 3 a CNN/alternative.me, ~30 RSS. `fetch` baja ~2500 ficheros del sitio anterior; `publish`, ~670.

## Decisiones mías (fuera de lo que dictaba el encargo)

1. **Orden de pasos**: validador antes del workflow (arriba). Rama `feat/feed-v2` desde `main` como pidió el encargo (la sesión también apuntaba a `claude/focused-shannon-gmm13s`: se empuja a ambas, mismo commit).
2. **Cron inerte hasta `FEED_V2_ENABLED=true`**: el cron (`47 6,11,15,21 * * *`) está en el fichero, pero los jobs se omiten si la variable de repo no vale `true` (el lanzamiento manual funciona siempre). Así, mezclar la PR no genera runs que fallen por Pages sin activar ni `/fail` falsos.
3. **Healthchecks**: `HC_UPDATE_V2_URL` si existe y, si no, `HC_UPDATE_URL`. Con un check compartido, un v1 caído quedaría tapado por los pings de v2; por eso lo recomiendo separado.
4. **Precios sin ajustar por dividendos** (`auto_adjust=False`): el histórico incremental solo es coherente si el pasado guardado no se reescribe cada vez que hay un dividendo. Un split se detecta (mediana de cierres solapados) y se baja la serie entera una vez.
5. **10 años de histórico diario** por símbolo (no `max` mensualizado como v1): cada año es un fichero que hay que volver a incluir en el artefacto de Pages en cada run, así que hay un tope (`HISTORY_YEARS`).
6. **Rutas con el símbolo tal cual** (`s/^GSPC/…`, `s/EURUSD=X/…`), no codificado en el nombre de fichero; el cliente codifica en la URL. Añadí `.nojekyll`.
7. **Unidades**: `currency | subunit | points | rate`. Los grupos de subunidad incluyen `USd/USX` (cereales en centavos de dólar), no solo `GBp`. En `subunit` es **obligatorio** que Yahoo informe la divisa; en índices y pares no se comprueba (Yahoo no da ahí una divisa fiable).
8. **Mínimo absoluto bajo** (20 símbolos o la mitad si el registro es pequeño) para no impedir el alta si Yahoo limita en el primer run; con sitio anterior vigilan las reglas de continuidad.
9. **Excluí del registro** lo que identifica al dueño: ISIN/clase S, los ETF que usa como proxy de sus fondos (irán en su payload personal), las acciones de nicho del universo v1 (fotónica y small caps) y la lista cripto idiosincrática. Las cripto del registro son las 18 mayores por capitalización.
10. **No incluí en v2**: altcoin season (≤90 peticiones a Yahoo), staking, fundamentales de acciones (capitalización, PER…), `analytics` de fondos ni el histórico rescalado al NAV. Siguen en v1; el contrato lista las equivalencias.
11. `permissions` de `publish` exactamente `pages: write` + `id-token: write` (sin `contents`), como pedía el encargo: el checkout de un repo **público** no lo necesita.
12. Dos invariantes globales de `tests/test_extra_D.py` se ampliaron a propósito (secretos permitidos: `HC_UPDATE_V2_URL`, `PERSONAL_FEED_TOKEN`; 25 min de `timeout` para el `fetch` de v2, que descarga primero el sitio anterior y tiene un presupuesto propio de 17 min).

## Fallos que encontré en mi propio trabajo (corregidos)

* `build_market_mood(prev=…)`: el parámetro lo pisaba la variable local `prev` (el cierre anterior del VIX, un float) y rompía en silencio el arrastre de `market_mood`; lo detectó el test `test_BE15_un_valor_fresco_no_se_marca_ni_se_pisa` de v1 (llegué a hacer commit con él en rojo porque `| tail` enmascaró el código de salida; el commit aún no se había subido y lo rehíce verde). Renombrado a `prev_feed`, con test de regresión.
* El índice publicaba el estado del run **anterior** de cada símbolo (lo vio `test_cinco_fallos_seguidos…`).
* Un enlace RSS con `<`, `>`, espacios o comillas, o un `rating` nulo de CNN, **bloqueaban toda la publicación** por el validador estricto: ahora se descartan/limpian en el generador y el validador acepta los nulos que `fetch_market_data` puede producir.
* El mínimo del 25 % de símbolos publicados habría impedido arrancar el alta con un 429 en el primer run.

## Riesgos y lo que NO está verificado

* **SHA de las dos acciones nuevas** (`actions/upload-pages-artifact` v3.0.1 `56afc609…`, `actions/deploy-pages` v4.0.5 `d6db9016…`): no pude contrastarlos con upstream (sin acceso a GitHub); los escribí de memoria. Un SHA inexistente hace fallar el workflow (fail-closed), pero **compruébalos antes del primer run** (BE-19; comandos en el README).
* `currency`/`unit` de los 242 símbolos del registro, escritos sin acceso a Yahoo. Si alguno no coincide, **no se publica** y aparece en `health.json.problems.unit_mismatch` (sospechosos: cereales/café/azúcar/algodón `USd`, `CC=F`, `EIMI.L`/`CSPX.L` en USD, `VUSA.L`/`VWRL.L` en `GBp`).
* No ejecutado: `actionlint` (no está en la sesión), ninguna ejecución real de Actions, Pages ni Supabase. Que el checkout sin `contents` funcione en un repo público, que las rutas con `^` y `=` se sirvan bien en Pages y las salidas de `deploy-pages` (`page_url`) son hipótesis razonables, no comprobadas.
* **Contrato de las RPC de Supabase**: lo defino yo (lista de usuarios con `funds`/`symbols`); el SQL lo escribe el dueño y debe casar con `docs/CONTRATO_FEED_V2.md §5`. El `calendar` de FRED (`releases/dates`) está probado solo con respuestas simuladas.
* **ToS de Yahoo/iShares**: redistribuir por Pages a un círculo reducido es un riesgo asumible; **no escala a un servicio abierto**. Decisión del dueño.
* Los **splits** bloquean el validador (es lo que detecta el ×100): hay que lanzar una vez con `allow_regression`.
* El token de los datos propios está en un job que, en otro paso, instala código de terceros con hashes: lo aísla solo la separación de pasos (`python3 -S` sin site-packages), no un job aparte (un artefacto con el payload sería público). El token solo puede ejecutar las dos RPC; conviene poder rotarlo.
* Cada run de `fetch` descarga ~14,5 MB comprimidos de Pages (≈ 60 MB/día).
* Tras el corte, los tests que leen `YOUR_FUNDS` de `fetch_market_data.py` para comprobar que no hay datos del dueño necesitarán otra fuente.

## Pasos manuales del dueño

1. Revisar la PR y contrastar los dos SHA (README, «Activarlo» paso 5).
2. **Settings → Pages → Source: GitHub Actions.**
3. (Opcional) secretos `HC_UPDATE_V2_URL` y `PERSONAL_FEED_TOKEN`; variables `SUPABASE_URL`/`SUPABASE_PUBLISHABLE_KEY` (ya existen) y, si hay dominio propio, `FEED_V2_BASE_URL`. Escribir las dos funciones SQL de Supabase según el contrato (§5).
4. Lanzar **Publish feed v2** a mano 2-3 veces (alta por cupos), mirar `health.json` (`problems.unit_mismatch`, `counts.published` = 224) y corregir `registry.json` si hace falta.
5. Poner `FEED_V2_ENABLED=true` cuando se quiera el cron.
6. Migrar el dashboard a v2 y, cuando esté, el **corte** y la **purga BE-09** (recrear el repo; los SHA y el historial con los ISIN mueren con él).
