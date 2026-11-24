# Revisión completa del backend público — noviembre 2026

Repo `FractalBuddy/Portfolio_Dashboard`, `main` en `ffe237c` (2026-11-23). Revisión **de solo lectura y medición**: no se ha cambiado código, tests,
workflows ni `registry.json`; solo se añaden este informe y `auditorias/logs_revision_nov2026/`.

Método: cada hallazgo lleva fichero:línea, evidencia, severidad (crítico / alto / medio / bajo / información) y si es **nuevo** (no aparece en
ninguna auditoría anterior o aparece con un supuesto distinto) o **antiguo** (ya conocido o aceptado). Lo deducido sin poder comprobarlo va marcado
**INFERIDO**; lo que no se pudo ejecutar, **NO PROBADO**. Contrastado con el código y, por primera vez, con los **runs reales** de Actions (API de solo
lectura: lista de runs y logs de 3 jobs; ver más abajo). Yahoo, Pages, CoinGecko y FRED no se han llamado.

---

## ⚠️ Primero: fuga parcial de datos privados (sin repetir el valor)

**No hay** ISIN personales, ids de ficha, `0P…` no listados, URL de Supabase, claves, JWT, tokens ni URL de Healthchecks con clave en el árbol,
en ninguno de los 49 commits de las 5 ramas (`main`, `data`, `probe-data`, la de Dependabot y la de esta sesión), ni en los logs reales revisados
(barrido en `logs_revision_nov2026/barrido_historial_git.txt` y `barrido_logs_reales.txt`).

**Sí hay una fuga parcial, que no es de ISIN sino de las listas del dueño:** `tests/privacy_guard.py:9-21` publica `PROXY_HASHES` y
`CRYPTO_HASHES`, huellas sha256 **sin sal** de textos muy cortos (tickers y nombres de criptomonedas). Con un diccionario de 297 candidatos escritos
de memoria se revierten **4 de las 8 huellas en menos de 1 segundo** (`huellas_reversibles.txt`; los valores no se escriben en ningún sitio).
Además, uno de los textos protegidos por `PROXY_HASHES` aparece **en claro** en `tests/test_fund_facts.py:83` y `tests/test_fund_probe_generic.py:134`
(es un ETF muy común, así que por sí solo dice poco; con la huella al lado, cualquiera sabe que es uno de los proxies del dueño).
Qué hacer: ver R-01. No es una fuga de ISIN ni de claves, por eso no se para la revisión, pero va la primera.

---

## (a) Resumen ejecutivo: los 10 hallazgos principales

| # | Sev. | Hallazgo | Dónde |
|---|---|---|---|
| 1 | **alto** | Las huellas de `privacy_guard.py` son reversibles por diccionario: se recuperan 4 de 8 elementos de la lista cripto/proxies del dueño (y uno de ellos está además en claro en dos tests). | `tests/privacy_guard.py:9-21` |
| 2 | **alto** | **La cadencia real no es la diseñada.** En 7 días: 0 lanzamientos del disparador externo desde el 17-11; el feed v2 corre ~2 veces al día (no ~14), con el cron de respaldo llegando entre 3 y 8 h tarde; `personal-nav` ~1 vez al día laborable. El «hot cada 45 min» y el NAV horario **no existen hoy**. | `runs_reales_resumen.txt`; README.md:277-293 |
| 3 | **alto** | La **sombra NAV Yahoo no ha medido nunca** y con esta cadencia no medirá: sus franjas son 05-10 y 19-24 UTC y los 8 runs reales de `personal-full` empezaron a 01:20-04:09 o 14:51-18:42 UTC. El libro de contadores nunca se ha guardado («Cache not found»). | `config/yahoo_shadow.json:7`, `personal-full.yml:37`, log del run 45241219684 |
| 4 | **alto** (latente) | `nav-probe.yml` imprime en el log **público** la fecha y el **valor del NAV** de cada ficha del dueño (#1, #2…). Un NAV con su fecha identifica el fondo entre los de iShares. No se ha ejecutado nunca (0 runs), así que hoy no hay fuga; la habría al primer uso. Además es el único job con el token sin la condición `refs/heads/main`. | `scripts/nav_probe.py:263,269,288`; `.github/workflows/nav-probe.yml:31-54` |
| 5 | **medio** | Una sola criptomoneda del top 250 con precio < 5·10⁻⁹ USD bloquea **todo** el feed v2: `normalize_coins` redondea a 8 decimales (→ 0.0) y el validador exige `price > 0`. Reproducido. | `scripts/feed_v2.py:870`; `scripts/sanity_check_v2.py:277`; `repro_cripto_precio_diminuto.log` |
| 6 | **medio** | En `personal-full`, `personal-nav` y `nav-probe` el código de terceros (yfinance/requests) corre **en el mismo job y antes** del paso que lleva el token: puede reescribir `scripts/personal_v2.py` en disco y el paso «stdlib only, con token» ejecutaría lo reescrito. La separación «paso sin token» no aísla nada dentro de un job. Mitigado por los hashes de `requirements.txt`. | `personal-full.yml:83-93`, `personal-nav.yml:75-88` |
| 7 | **medio** | Avisos al 50 %: solo existen para los topes del feed v2 y solo como `::warning::` en el log (nadie los ve; no van a Healthchecks). Hoy salta `MAX_REGISTRY_SYMBOLS 62 %`. El feed v1 **ya está por encima del 50 %** de su tope (`data.json` 9,83 MB de 18 MB = 55 %; 49 % del límite duro de 20 MB de jsDelivr) y **no tiene aviso**. Ningún aviso para minutos, caché, Pages o Supabase. | `sanity_check_v2.py:852-868`; `sanity_check.py:50,546` |
| 8 | **medio** | Fallback `secrets.X || vars.X` para `SUPABASE_URL` y la clave: si algún día se guardan como **variables**, la URL de Supabase sale en claro en la cabecera `env:` de cada paso en logs públicos (el propio repo lo advierte en `personal-nav.yml:104` para Healthchecks). Hoy son secretos (`***` en el log real). | `personal-nav.yml:64-65,84-85,98-99`, `personal-full.yml:72-73,90-91`, `nav-probe.yml:51-52`, `supabase-keepalive.yml:32-33`, README.md:397 |
| 9 | **medio** | El guardia de privacidad tiene huecos: solo ISIN **irlandeses** (`IE…`), solo el árbol de trabajo (ni historial, ni ramas `data`/`probe-data`, ni artefactos, ni resúmenes de jobs), y las formas de secreto (Supabase, JWT, Healthchecks, tokens) solo se buscan en `.github/workflows/*.yml`. | `tests/privacy_guard.py:41`; `tests/test_privacidad_isin.py:40-56`; `tests/test_workflows_F1.py:224-243` |
| 10 | **medio** | Sin vigilancia en `personal-full` (ni latido de Healthchecks ni `/fail`): es el único que crea payloads nuevos y donde corre la sombra. Ningún workflow manda `/start`, así que un run colgado solo se detecta por el timeout. | `personal-full.yml` (sin `HC`); todos |

Lo bueno, en una línea: pytest **2358 en verde**, ruff limpio, los 7 SHA de acciones coinciden con sus tags, `permissions: {}` por defecto con
mínimos por job, nada de `pull_request_target` ni `workflow_run`, entradas de `workflow_dispatch` siempre vía `env:`, `pip --require-hashes`,
validador separado del código de terceros en el feed v2, y 0 datos privados en el historial y en los logs reales revisados.

---

## (b) Tabla completa

Coste: **XS** < 30 min · **S** ~1 h · **M** media sesión · **L** sesión completa.

### 1. Privacidad y secretos

| id | sev. | nuevo/antiguo | fichero:línea | evidencia | arreglo propuesto | coste |
|---|---|---|---|---|---|---|
| R-01 | alto | **nuevo** | `tests/privacy_guard.py:9-21`; en claro en `tests/test_fund_facts.py:83`, `tests/test_fund_probe_generic.py:134` | sha256 sin sal de tickers/monedas: 4 de 8 revertidas con 297 candidatos (`huellas_reversibles.txt`). Un texto de `PROXY_HASHES` está en claro en 2 tests. | Sacar las huellas del repo público: pasar la lista a un **secreto** de CI (`PRIVATE_DENYLIST`) que el test lea si existe (en PR de forks se omite con aviso), o HMAC con clave secreta. Cambiar el ticker en claro de esos 2 tests por uno sintético. El historial ya las contiene: valorar si basta con dejar de usarlas (el valor ya es público) o reescribir historia (no recomendado; es poco sensible). | S |
| R-02 | alto (latente) | **nuevo** | `scripts/nav_probe.py:263,269,288`; `nav-probe.yml:4-7` | Imprime `NAV <fecha> = <valor>` por ficha en el log público. El comentario del workflow lo da por anónimo («#1, #2»), pero NAV+fecha identifica el fondo. 0 runs hasta hoy (API). | Imprimir solo la fecha y si el valor coincide con la base (sí/no), nunca el número; o `::add-mask::` del valor antes de imprimir. | XS |
| R-03 | medio | **nuevo** | `nav-probe.yml:31-33` | Único job con `PERSONAL_FEED_TOKEN` sin `if: github.ref == 'refs/heads/main'` (AU01-F1 pieza 4 dice «jobs con secretos solo en main»). Ningún test lo cubre (`test_workflows_F1.py:130` solo mira V2/PN/PF). | Añadir la condición y meter `nav-probe.yml` y `fund-probe.yml` en el test paramétrico. | XS |
| R-04 | medio | **nuevo** (los FIXES dan el paso «sin token» por aislado) | `personal-full.yml:83-93`, `personal-nav.yml:75-88`, `nav-probe.yml:56-69` | El paso de terceros corre antes del de token en el mismo runner; el workspace es escribible (`scripts/personal_v2.py`). `python3 -S` evita `site-packages` pero no un fichero de script reescrito. | Mínimo (XS): antes del paso con token, comprobar que `scripts/personal_v2.py` no ha cambiado (`git diff --exit-code -- scripts/` o sha256 contra el commit) y hacer el checkout de `scripts/` de nuevo. Completo (M): job aparte para publicar, como en el feed v2; el intercambio de `payloads.json` no puede ir en un artefacto en claro (los artefactos de un repo público los baja cualquiera con cuenta), así que tendría que ir cifrado con una clave en secreto | XS (hash) / M (jobs) |
| R-05 | medio | **nuevo** | ver tabla resumen #8 | Fallback a `vars.*` para la URL y la clave de Supabase; las variables salen en claro en el log. Hoy se usan secretos (log real: `SUPABASE_URL: ***`). | Quitar `|| vars.SUPABASE_URL` y `|| vars.SUPABASE_PUBLISHABLE_KEY`; README.md:397 que diga «solo Secrets». | XS |
| R-06 | medio | **nuevo** | `tests/privacy_guard.py:41`, `test_privacidad_isin.py:40-56`, `test_workflows_F1.py:224-243` | Solo ISIN `IE`; un ISIN `LU`/`DE`/`FR` del dueño pasaría. Sin barrido de historial/ramas/artefactos. Formas de secreto solo en workflows (README, docs y `auditorias/` no se miran). | Ventana `[A-Z]{2}[A-Z0-9]{9}[0-9]` **con dígito de control válido** (evita falsos positivos) y lista de excepciones; aplicar `SECRET_SHAPES` a todo el árbol; un workflow semanal (sin secretos) que pase el barrido por `git rev-list --all` y por `data`/`probe-data`. El script `logs_revision_nov2026/barrido_historial_git.py` sirve de base. | S |
| R-07 | bajo | **nuevo** | `scripts/personal_v2.py:548,618`; log real del run 45241219684 | El log público dice nº de usuarios (1), de fondos con ficha (2), nº de series y **nº total de barras** de las series de proxies y bytes del payload. Las barras dan la antigüedad del histórico de los proxies: huella débil. | Dejar usuarios/fondos (operativo) y quitar barras y bytes o redondearlos (p. ej., «~165 KB»). | XS |
| R-08 | bajo | antiguo | `fund-probe.yml:58-64`; `publish-feed-v2.yml:141-145`; `update-data.yml:71-75` | `PROBE_ISINS` (ISIN privados) y `FRED_API_KEY` están en el entorno del **mismo paso** que ejecuta yfinance. Documentado y mitigado con hashes. El log real de fund-probe (run 44097280860) está limpio: 35 ISIN, todos de la lista genérica pública. | Aceptable con hashes + cooldown de Dependabot. Opcional: pasar `PROBE_ISINS` por fichero en `$RUNNER_TEMP` y borrar la variable antes de importar yfinance (no protege contra código malicioso, sí contra volcados accidentales del entorno). | XS |
| R-09 | bajo (INFERIDO) | **nuevo** | `personal-full.yml:96-122` | El libro de la sombra va a la caché de Actions de `main`; un workflow de `pull_request` (de un fork, con un `ci.yml` modificado) puede **restaurar** cachés de la rama base. Contenido: solo contadores y fechas de NAV, sin identificadores (`yahoo_shadow.py:446-527`). | Aceptar (no hay nada que identifique un fondo) y anotarlo en el diseño; si algún día lleva algo más, no usar caché. Revisar que Settings → Actions exija aprobación para PR de colaboradores externos (NO PROBADO). | XS |
| R-10 | info | **nuevo** | `config/yahoo_shadow.json:12` | El canario es un fondo de la lista genérica pública (test lo exige). Pregunta para Alex: ¿es también uno de sus fondos? Si lo fuera, el canario lo delata. | Confirmar; si coincide, cambiarlo por otro de la lista. | XS |
| R-11 | info | antiguo | metadatos de git | 12 commits con autor «nombre completo» y 1 commit con autor `Claude <noreply@anthropic.com>`; e-mails siempre `noreply`. | Nada (decisión del dueño). | — |

### 2. Workflows y cadena de suministro

| id | sev. | nuevo/antiguo | fichero:línea | evidencia | arreglo propuesto | coste |
|---|---|---|---|---|---|---|
| W-01 | **alto** | **nuevo** | README.md:277-293; `runs_reales_resumen.txt` | 100 runs (16-11 a 23-11): `Publish feed v2` 15 `schedule` + 3 `workflow_dispatch` (el último el 17-11); horas reales de inicio 01:24, 02:36, 02:48, 03:08, 04:10, 16:22, 16:43, 16:47, 17:04, 17:24, 19:28, 23:36, 23:52… frente a crons 07:47/13:47/21:47. `Personal NAV`: 8 `schedule` en 6 laborables (crons 07:23/11:23/16:23). El disparador externo (Cloudflare Worker, repo privado) **no está lanzando nada** o no llega. | Revisar el Worker (token caducado, permisos `actions:write`, ref `main`) — NO PROBADO desde aquí. Mientras tanto, el README debe decir la cadencia real. Añadir alerta: Healthchecks del feed con periodo 2 h en horario de mercado avisaría de esto. | S |
| W-02 | medio | antiguo (decisión «no tocar v1») | `update-data.yml:87-94`; `test_workflows_F1.py:112-114` | El job `publish` (`contents: write`, force-push a `data`) no exige `refs/heads/main`: un `workflow_dispatch` desde otra rama publica con el código de esa rama. Requiere permiso de escritura en el repo. | Añadir la condición cuando se toque v1 (o al retirarlo). | XS |
| W-03 | bajo | **nuevo** | `keepalive.yml:22-60` | Empuja directamente a `main`. Si se activa un ruleset que exija PR o checks en `main`, el keepalive falla en silencio (solo `::error::` en un run semanal). README.md:380,437 lo advierte. Ruleset real NO PROBADO. | Al configurar `protect-main`: excepción para `github-actions[bot]` o mover el commit de actividad a otra rama (los cron solo cuentan actividad del repo, INFERIDO). | XS |
| W-04 | bajo | **nuevo** | `ci.yml:44-50` | `actionlint` «si está disponible»: no viene en `ubuntu-24.04` (INFERIDO, el log de CI no se ha leído), así que el paso nunca valida nada. | Quitarlo o instalar un binario fijado por hash. | XS |
| W-05 | bajo | **nuevo** | `publish-feed-v2.yml:166,172,286-289,329` | Si el primer deploy falla pero Pages ya sirve el sitio (`decision=served`), `page_url` queda vacío y `verify` termina con «sin generated_at o page_url: no se verifica». Inocuo. | Calcular `page_url` desde `FEED_BASE` como respaldo. | XS |
| W-06 | info | **nuevo** | `publish-feed-v2.yml:244-245` | Nota BE-19 («SHA no contrastados»): contrastados hoy, los 7 SHA de todas las acciones coinciden (`sha_acciones.txt`). | Borrar la nota. | XS |
| W-07 | info | antiguo | rama `dependabot/pip/soupsieve-4.0` | PR de Dependabot abierto desde el 13-11 (soupsieve 3.0.4 → 4.0, versión mayor). `requirements.txt` sigue en 3.0.4. | Revisar y fusionar o cerrar. | XS |
| W-08 | info | — | todos | Sin `pull_request_target`, sin `workflow_run`, sin `environment` salvo `github-pages`; entradas de `workflow_dispatch` solo vía `env:` (sin inyección en `run:`); `permissions: {}` o `contents: read` por defecto. | — | — |

### 3. Corrección del feed v2

| id | sev. | nuevo/antiguo | fichero:línea | evidencia | arreglo propuesto | coste |
|---|---|---|---|---|---|---|
| F-01 | medio | **nuevo** | `scripts/feed_v2.py:861-870`; `scripts/sanity_check_v2.py:277` | `normalize_coins` acepta `price > 0` y luego `round(price, 8)`; < 5·10⁻⁹ → `0.0` → «price: debe ser > 0» → error absoluto → no se publica nada. Reproducido (`repro_cripto_precio_diminuto.*`). Que haya hoy una moneda así en el top 250: NO PROBADO. | Redondear a 8 **cifras significativas** (o descartar la moneda si queda en 0) y que un problema de una moneda sea aviso, no error del sitio. Test de regresión con 3·10⁻⁹. | XS |
| F-02 | info | — | `feed_v2.py:847-849`; `sanity_check_v2.py:277-278` | `_pct` anula variaciones > 1000 % y el validador acota ±1000 %: coherentes (una moneda con +1500 % en 24 h se publica con `null`, no bloquea). | — | — |
| F-03 | info | — | log real del run 45249393927 | Hoy: 249 en el registro, 202 tocaban, 202 actualizados, **252 peticiones a Yahoo**, 33,9 MB, 2786 ficheros, avisos: `MAX_REGISTRY_SYMBOLS 62 %`, `ROG.SW` sin datos, `TON` `not_listed` en CoinGecko; `calendario macro: ReadTimeout`. | Mirar por qué `ROG.SW` y `TON` fallan (símbolo/ids) — NO PROBADO sin red. | S |
| F-04 | info | antiguo | `feed_v2.py:157` | Se aceptan barras con fecha de **mañana** UTC (Asia) y la barra de **hoy** con la sesión abierta: `latest.price` es intradía y el año en curso se reescribe en cada run, así que se corrige solo. Correcto para el uso; documentarlo en el contrato si no está. | — | — |
| F-05 | info | — | `freshness_gate.py:41-55`, `site_download.py:133-145` | Puerta de frescura con fallo cerrado hacia «trabajar»; descarga verificada por bytes+sha256 contra el manifiesto; manifiestos 1 y 2 describen el mismo conjunto (los tests `test_manifest_escala.py` lo comprueban byte a byte). Deploy con un reintento acotado. Bien. | — | — |

### 4. Datos

| id | sev. | nuevo/antiguo | fichero:línea | evidencia | arreglo propuesto | coste |
|---|---|---|---|---|---|---|
| D-01 | bajo (INFERIDO) | **nuevo** | `scripts/fetch_market_data.py:1152-1168` | `_plain_text` quita `<`, `>` y controles, pero deja `"` y `'`. Si el cliente (privado, no visto) mete un titular en un **atributo** con `innerHTML` (`title="…"`), hay rotura de atributo. Con `textContent` no pasa nada. | Confirmar en el cliente; si hay plantillas con atributos, escapar en el cliente (no en el feed). | XS |
| D-02 | info | — | `feed_v2_common.py:47-75`, `feed_v2.py:255-268` | GBp/GBX/ZAc/ILA/USd con divisor 100; `subunit` exige que Yahoo informe la divisa; `same_unit` distingue escala. 10 `GBp`, 6 `USd` (futuros agrícolas) y 2 `GBP` (VUSA.L, VWRL.L) en el registro: coherente con Yahoo (INFERIDO, sin red). | — | — |
| D-03 | info | — | `fund_lag_common.py:39-63` | Festivos TARGET calculados (Pascua por algoritmo): no caducan. `personal_v2.nav_expected_date` no conoce festivos, documentado (solo cuesta visitas). | — | — |
| D-04 | bajo | **nuevo** | log real del feed v2; `pytest_completo.log` | yfinance 1.7.0 emite `Pandas4Warning` (`inplace` desaparece en pandas 4) y feedparser avisa de que el respaldo `updated→published` se quitará; `fetch_market_data.py:1187` depende de él. | Fijar `pandas<4` en `requirements.in` hasta que yfinance lo soporte; leer `updated` explícitamente. | XS |

### 5. Sombra NAV

| id | sev. | nuevo/antiguo | fichero:línea | evidencia | arreglo propuesto | coste |
|---|---|---|---|---|---|---|
| S-01 | **alto** | **nuevo** | `config/yahoo_shadow.json:7`; `yahoo_shadow.py:534-536`; `personal-full.yml:37` | 8 runs reales de `personal-full` (16-11 a 23-11): todos fuera de 05-10 y 19-24 UTC. Desde la fusión (#12) la sombra ha dicho siempre «fuera de franja» y la caché del libro no existe. La puerta D7 (10 días hábiles) no puede avanzar. | Quitar la franja fija y limitar por «una lectura por día hábil» (o franjas anchas 00-12 / 12-24), o lanzar `personal-full` desde el disparador externo cuando funcione. | XS |
| S-02 | info | — | `yahoo_shadow.py:13-19,626-627,682-685`; `fund_probe.py:74-89,613-622` | No publica nada (sin Supabase, artefactos ni commits), enmascara cada ISIN y `0P…` antes de pedir, silencia stdout/stderr/logging de yfinance, errores en clase cerrada, nunca rojo. **429**: comprobado en el código de yfinance 1.7.0 que `YFRateLimitError` se relanza también dentro de `history()` (`scrapers/history.py:297-298`), así que la parada limpia funciona. Tope 60/run (máx. 80). Cambio de formato de Yahoo → `parseo`/`vacio`, contado, sin romper. | — | — |
| S-03 | bajo | **nuevo** | `yahoo_shadow.py:280-285` | `t.history_metadata` puede disparar una petición extra si yfinance no la tiene ya en caché (INFERIDO); va dentro de la misma llamada al `Gate`, que la cuenta como un solo `HISTORY_COST`: el contador podría quedarse corto en 1 por fondo. | Leer la divisa del propio `history()` o contar el coste. | XS |

### 6. Coste y límites (medido / estimado)

| id | sev. | nuevo/antiguo | evidencia | estado frente a límites gratuitos |
|---|---|---|---|---|
| C-01 | medio | **nuevo** | `data.json` (rama `data`) = **9 829 684 B**; tope propio 18 MB, límite duro de jsDelivr 20 MB (`sanity_check.py:50`). | **55 % del tope propio, 49 % del límite duro. Ya pasa el 50 %, sin aviso.** |
| C-02 | info | — | Repo público: minutos de Actions en runners estándar gratuitos. Duración real del feed v2: gate 5 s, fetch 2 min 49 s, publish 36 s. | Sin coste. Si el repo pasara a privado: con la cadencia diseñada (~14 runs/día × ~6 min facturables) ≈ 2 500 min/mes > 2 000 gratuitos (INFERIDO). |
| C-03 | info | — | Sitio v2 real 33,9 MB y 2786 ficheros; banco a 249 símbolos sintéticos 40,3 MB/2837 ficheros; a 400 → 64,5 MB. | Pages 1 GB: 3-4 %. `MAX_SITE_FILES` 12 000: 23 %. |
| C-04 | info | — | Ancho de banda propio: `fetch` baja el sitio entero (~34 MB) en cada run. Hoy ~2 runs/día → ~2 GB/mes; con ~14/día → ~15 GB/mes. | Pages 100 GB/mes (límite blando): 2 % hoy, ~15 % con la cadencia diseñada. El 50 % se pasaría con ~45 runs/día o ~3× símbolos (INFERIDO). |
| C-05 | info | — | Artefactos por run: `site-v2` 10,5 MB + `github-pages` 10,2 MB, retención 1 día. Caché: pip ~50 MB (+ libro de sombra, hoy inexistente). | Repo público: artefactos sin coste; caché 10 GB: ~0,5 %. |
| C-06 | info | — | Peticiones a Yahoo: feed v2 real **252/run** (banco: 272 en el run del día siguiente, 289 en el alta). Con la cadencia diseñada: ~100/run hot + warm 1×/día ≈ 1 500/día (INFERIDO). Sombra ≤ 60/run (hoy 0). fund-lag ~10/run. | Yahoo no publica límite: el riesgo es 429, ya tratado. |
| C-07 | medio | **nuevo** (ver #7) | Avisos al 50 %: hoy saltaría `MAX_REGISTRY_SYMBOLS 62 %` (real) y, con nombres largos, `MAX_BYTES[manifest] 50 %` (banco). Solo en log. | Llevar los avisos a Healthchecks como `/log` (no `/fail`) y añadir: `data.json` v1, tamaño del sitio, nº de runs/día, caché. |

### 7. Pruebas

* `pytest` completo, una vez, Python 3.13 local (CI usa 3.14): **2358 passed, 1 warning, 4 min 5 s** (`pytest_completo.log`). `ruff check --select F scripts tests`
  (la regla de CI), una vez: **All checks passed** (`ruff_F.log`).
* **10 mutantes al azar** (semilla 20261123, sobre una copia en el scratchpad, cada uno contra la suite completa con `-x`; `mutantes.json`):

| id | fichero | mutación | ¿muerto? | primer test que falla |
|---|---|---|---|---|
| M02 | `scripts/feed_v2_common.py:72` | `same_unit` ignora la escala (GBp = GBP) | sí | `test_feed_v2.py::test_divisa_o_unidad_distinta_a_la_declarada_no_se_publica` |
| M03 | `scripts/feed_v2_common.py:326` | `price_plausible` sin tope superior | sí | `test_picos_H6.py::test_rangos_plausibles_por_unit` |
| M04 | `scripts/feed_v2.py:233` | `period_for_gap` con `<` en vez de `<=` | sí | `test_feed_v2.py::test_hueco_largo_usa_un_periodo_mayor` |
| M05 | `scripts/feed_v2.py:415` | tolerancia de rebase ×10 (2 % → 20 %) | **NO** | — (2358 pasan) |
| M06 | `tests/privacy_guard.py:65` | el guardia ignora los ISIN `IE0…` | sí | `test_fund_probe_generic_list.py::test_el_guardia_sigue_activo_en_todo_el_arbol…` |
| M08 | `scripts/yahoo_shadow.py:156` | cobertura mínima de nombre a la mitad | **NO** | — (2358 pasan) |
| M10 | `scripts/feed_v2.py:409` | símbolo congelado a 450 días en vez de 45 | sí | `test_feed_v2.py::test_simbolo_congelado_se_marca_delisted` |
| M11 | `scripts/feed_v2.py:465` | tope de barras descartadas ×10 | sí | `test_feed_v2_neg.py::test_tres_barras_malas_aun_se_publica_y_cuatro_no` |
| M13 | `scripts/freshness_gate.py:51` | la puerta tolera `generated_at` 1 h en el futuro | sí | `test_puerta_frescura_F1.py::test_decide_reloj_adelantado…` |
| M14 | `scripts/freshness_gate.py:82` | `force` también cuenta en `schedule` | sí | `test_puerta_frescura_F1.py::test_force_en_un_schedule_no_cuenta` |

**8 de 10 muertos.** Los 2 supervivientes son huecos reales: ningún test pone un desajuste del pasado entre 2 % y 20 % (un ajuste de
precios del 5-15 % pasaría sin re-descarga y dejaría un salto en la serie), y ningún test de la sombra está en el borde de `name_min_coverage`
(con la mitad de cobertura, nombres parecidos de otra clase del mismo fondo se emparejarían: ensucia la medida F1).

* Huecos de cobertura encontrados (todos **nuevos**):
  * T-01 (medio) Ningún test con una moneda de precio < 5·10⁻⁹ (F-01).
  * T-02 (medio) `test_workflows_F1.py:130` solo comprueba la condición de `main` en V2/PN/PF; `nav-probe.yml` y `fund-probe.yml` no están (R-03).
  * T-03 (medio) Ningún test de privacidad recorre el historial ni las ramas `data`/`probe-data`, ni ISIN no irlandeses (R-06).
  * T-04 (bajo) Ningún test liga las franjas de la sombra con los crons de `personal-full` ni con la cadencia real (S-01): pasan todos y la sombra no mide nunca.
  * T-05 (bajo) Ningún test del validador v1 avisa al 50 % de `MAX_DATA_BYTES` (C-01).
  * T-06 (bajo) Ningún test impide que un script de diagnóstico imprima valores numéricos de NAV (R-02).
  * T-07 (medio) Superviviente M05: test de rebase con un desajuste del 5 % (debe re-descargar) y del 1 % (no).
  * T-08 (bajo) Superviviente M08: test de `name_match` justo por debajo y por encima de `name_min_coverage` (0,6).

### 8. Mantenimiento

| id | sev. | nuevo/antiguo | fichero:línea | evidencia | arreglo propuesto | coste |
|---|---|---|---|---|---|---|
| M-01 | medio | **nuevo** | README.md:91,277-301 | Describe «hot cada 45 min, 98 peticiones por run» y NAV horario; la realidad medida es ~2 runs/día y 252 peticiones/run. README.md:301 sí dice que la puntualidad «no está medida»: ya lo está. | Añadir «Cadencia observada (16-23 nov)» con los números de `runs_reales_resumen.txt`. | XS |
| M-02 | bajo | **nuevo** | `publish-feed-v2.yml:244-245` | Nota BE-19 desfasada (W-06). | Borrar. | XS |
| M-03 | bajo | antiguo | `personal-nav.yml:18-20,90-101` | Ruta `PERSONAL_NAV_FALLBACK_FULL` probablemente muerta (la RPC ya existe: el run real publica). INFERIDO. | Retirar cuando se confirme. | XS |
| M-04 | bajo | **nuevo** | `registry.json:160,252` | `ROG.SW` sin datos y `TON` fuera del lote de CoinGecko en el run real de hoy. | Pasar `symbol-probe` sobre esos dos. | XS |
| M-05 | info | — | Datos externos que caducan: ninguno con fecha fija (festivos calculados). Dependen de terceros sin contrato: Yahoo (yfinance), iShares (HTML de la ficha), CoinGecko, FRED, CNN/alternative.me, RSS de Yahoo, jsDelivr. | Tenerlo en el README (ya está en buena parte). | — |

---

## (c) Lo que está bien y por qué

* **Historial limpio.** 49 commits, 204 pares blob/ruta, 5 ramas: 0 ISIN reales (cualquier país, con dígito de control), 0 `0P…` no listados,
  0 ids de ficha, 0 URL de Supabase, 0 claves/JWT/tokens reales (las coincidencias son valores de prueba inventados: `sb_secret_FAKE…`, `abc.supabase.co`).
* **Logs reales limpios.** `personal-full` muestra los secretos como `***` y solo contadores; `fund-probe` solo ISIN de la lista genérica pública.
* **Separación terceros / publicación en el feed v2**: `fetch` (yfinance, `contents: read`) → artefacto → `publish` (sin terceros, validador sacado
  del repo y no del artefacto, `pages: write`). Es el patrón correcto y está bien hecho.
* **Cadena de suministro**: acciones por SHA verificado (los 7), `pip --require-hashes --no-deps`, Dependabot mensual con *cooldown* de 7 días,
  `persist-credentials: false` en todos los checkouts que no empujan.
* **Validador estricto y con fallo cerrado**: esquemas cerrados, topes por tipo, continuidad contra una descarga propia del sitio anterior,
  verificación sha256, puerta de frescura que ante la duda trabaja.
* **Sombra NAV**: diseño de privacidad muy cuidado (máscaras antes de la primera petición, salida en clase cerrada, nunca rojo); el manejo del 429 se
  ha comprobado contra el código real de yfinance 1.7.0.
* **Pruebas**: 2358 tests, red bloqueada en `conftest.py`, controles negativos en los guardias. 8 de 10 mutantes muertos (ver §7; los 2 supervivientes son huecos T-07 y T-08).

## (d) Lo no comprobable aquí

* **NO PROBADO**: llamadas reales a Yahoo, iShares, CoinGecko, FRED, Pages y jsDelivr (sin red para esos hosts); contenido actual servido en Pages
  (solo el log del run); resúmenes de job (`GITHUB_STEP_SUMMARY`) y artefactos (la API no da su contenido aquí); configuración del repo
  (rulesets de `main`, protección de `data`, aprobación de workflows de forks, secret scanning, Pages); el Cloudflare Worker del disparador
  externo (repo privado); el cliente (dashboard privado) y cómo pinta los textos; el uso real de Supabase frente a su plan gratuito.
* Leído sí (solo lectura, API de Actions): lista de los 100 runs más recientes y los logs de 3 jobs (personal-full, publish del feed v2,
  fund-probe). No se ha lanzado ningún workflow.
* Python local 3.13 frente a 3.14 en CI: la suite pasa en 3.13; en 3.14 lo dice CI (todos los runs de CI de la ventana en verde).

## (e) Sesiones propuestas, por orden

1. **Privacidad (S, urgente)**: R-01 (huellas fuera del repo, ticker en claro), R-02 + R-03 (nav-probe sin valores y solo en `main`), R-05
   (quitar `vars.*`), R-07. Tests T-02, T-06.
2. **Cadencia real (S)**: W-01 (revisar el Worker; medir), S-01 (franjas de la sombra), #10 (latido de `personal-full`), M-01 (README con la
   cadencia observada). Test T-04.
3. **Feed v2 robusto (XS-S)**: F-01 + T-01 (cripto diminuta), T-07 y T-08 (supervivientes de los mutantes), M-04 (`ROG.SW`, `TON`), D-04 (pandas < 4).
4. **Avisos al 50 % de verdad (S)**: C-01/C-07: aviso v1 de `data.json`, avisos como `/log` de Healthchecks, contador de runs/día. Test T-05.
5. **Guardia ampliado (S)**: R-06 + T-03 (ISIN con dígito de control de cualquier país, formas de secreto en todo el árbol, workflow semanal
   que barra historial y ramas de datos).
6. **Endurecer jobs personales (M)**: R-04 (verificación de hash del script antes del paso con token, o jobs separados), W-02, W-03, W-04,
   W-05, W-06/M-02, W-07.
