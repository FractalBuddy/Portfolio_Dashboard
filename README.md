# Feed de datos de mercado para My Portfolio Dashboard

Este repo hace una sola cosa: un workflow de GitHub Actions ejecuta un script de Python 4 veces al día, descarga
datos de mercado **públicos** (índices, acciones, fondos, materias primas, divisas, mapa de países, noticias, IPC
de España y, opcionalmente, tipos de EE. UU. de FRED) y los publica como `data.json` (+ un `meta.json` diminuto)
en una rama dedicada **`data`**. El dashboard los lee a través de jsDelivr:

```
https://cdn.jsdelivr.net/gh/<usuario>/<repo>@data/meta.json   (unos bytes, se consulta cada pocos minutos)
https://cdn.jsdelivr.net/gh/<usuario>/<repo>@data/data.json   (~10 MB, ~2 MB comprimido)
```

> **Feed v2 (nuevo, en paralelo).** Además del feed de arriba, el repo publica un feed **genérico** v2 en GitHub Pages: un fichero por símbolo, sin datos del
> dueño, con divisa y unidad explícitas. Ver [Feed v2](#feed-v2-genérico-en-github-pages) y [`docs/CONTRATO_FEED_V2.md`](docs/CONTRATO_FEED_V2.md).
> El feed actual sigue funcionando sin cambios hasta el corte que decida el dueño.

## Qué es público (y qué no)

El repo y la rama `data` son **públicos**: cualquiera puede leer lo que contienen. Hay que ser honestos con lo que eso
significa. Además de precios y series de mercado de símbolos públicos, el feed incluye:

* los **ISIN y nombres de los fondos que sigues** (`your_funds` en `data.json`; son los de `YOUR_FUNDS` en el script)
  y su NAV exacto de ishares.com. Quien lea el feed sabe qué fondos sigues;
* la **lista de criptomonedas vigiladas** (`CRYPTO_WATCHLIST`): está en el código público, y los titulares filtrados salen
  en el feed con `symbol`/`asset_name` igual a la palabra clave que coincidió.

No hay importes, cantidades, saldos ni operaciones. El dashboard, las posiciones y las copias de seguridad **no**
forman parte de este repo (`.gitignore` y el CI bloquean `*.html`, `*backup*`, `*export*`, `*.csv`, `*.xlsx`, `*.zip`,
`.env*` salvo `.env.example`, `*.pem` y `*.key`). El historial de git ya contiene esos ISIN: quitarlos del script no los
borra del pasado. Las copias de seguridad y los informes (mensual, anual, fiscal) los guarda el propio dashboard en las
carpetas locales que se configuran en Ajustes; no hay ningún job en la nube que los genere.

## Cómo funciona `update-data.yml` (3 jobs + 1 de verificación)

| Job | Permisos | Qué hace |
|---|---|---|
| `fetch` | solo lectura | instala dependencias, descarga el feed publicado anteriormente, ejecuta el script y sube `data.json`/`meta.json` como artifact. Es el único job que ejecuta código de terceros, así que su salida se trata como **no fiable** |
| `publish` | `contents: write` | **valida** el artifact con `scripts/sanity_check.py` (solo biblioteca estándar, sacado del repo con un checkout de `scripts/` sin credenciales persistentes, no del artifact) y solo si pasa sustituye la rama `data` por **un único commit nuevo** (force-push, con 3 reintentos) |
| `verify-cdn` | ninguno | purga jsDelivr y comprueba que sirve el `meta.json` nuevo. Es **informativo**: `continue-on-error`, no está en el `needs` del heartbeat y no duerme tras el último intento. Si el CDN sigue desfasado deja un `::warning::` y un ping a `<HC_UPDATE_URL>/log` (no marca fallo) |
| `heartbeat` | ninguno | depende solo de `fetch` y `publish`: avisa a Healthchecks.io (secreto `HC_UPDATE_URL`) de si todo fue bien, `/fail` si algo falló, y si no llega el aviso a tiempo, Healthchecks te escribe. Si el secreto está vacío deja un `::warning::` |

Las ejecuciones se serializan (`concurrency`) y tienen límite de tiempo.

### El sanity-check (`scripts/sanity_check.py`)

Se ejecuta en `publish`, antes del force-push. Si falla, no se publica nada y el dashboard sigue sirviendo el último
dato bueno (el heartbeat manda `/fail`).

* **Reglas absolutas** (el estado del feed por sí solo): mínimo de índices/acciones/fondos/materias primas/divisas,
  histórico en al menos el 70 % de cada lista, mediana de histórico de acciones e índices >= 250 barras, la mayoría de
  símbolos con una barra de los últimos ~6 días (histórico no congelado), precios plausibles, `your_funds` con NAV o
  variación, `market_mood` con VIX o Fear & Greed de bolsa, tamaño entre 3 y 18 MB, `meta.json` coherente...
* **Reglas de regresión** contra el feed publicado anteriormente (se descarga de
  `raw.githubusercontent.com/<repo>/data/data.json`): símbolos desaparecidos (tolerancia 5 %, mínimo 1), histórico
  de un símbolo acortado más de un 10 %, `your_funds` que pierde un NAV exacto y tamaño inferior al 60 % del anterior.
  Si no hay feed anterior (primer run, rama borrada, descarga fallida) solo se aplican las absolutas.
* **Cambios intencionados**: si quitas símbolos a propósito, o el scrape de iShares deja de funcionar de forma
  permanente, las reglas de regresión bloquearán todos los runs programados. Lanza una vez **Run workflow** con
  `allow_regression` marcado: las reglas de regresión pasan a avisos (las absolutas siguen bloqueando) y el feed nuevo
  pasa a ser el de referencia.
* También emite `::warning::` si algún fondo de `your_funds` no viene de `ishares_nav_scrape`, si su NAV tiene 7 días
  (un solo fondo) o si hay datos de FRED reutilizados.
* **NAV congelado = alerta a Healthchecks** (`degraded:`): NAV de 8 o más días en cualquier fondo, de más de 6 días en
  todos a la vez, fecha del NAV ilegible o futura, o scrape de iShares roto 2 o más runs seguidos. El umbral normal es de
  6 días porque los festivos de Irlanda/UK dejan hasta 6 días sin NAV nuevo. La edad se recalcula en `sanity_check.py`
  con `nav_date_iso`; no se fía del flag `nav_stale` del feed.

### Si FRED falla

Con `FRED_API_KEY` definida, una caída de FRED o una clave caducada ya no bloquea los precios de bolsa: el script
reutiliza `rates`, `cpi` y `cpi_meta` del feed anterior marcados `stale: true` (con `stale_since`) y se publica con un
aviso. Sin feed anterior no hay nada que reutilizar y se exige lo mínimo (tipos e IPC), como antes. El dashboard no
tiene por qué mostrar el campo `stale`: revisa los avisos del run si FRED lleva días caído.

## Por qué una rama `data` aparte

`data.json` pesa ~10 MB. Commitearlo en `main` cuatro veces al día añadiría ~0,5 GB de historial al año para datos
cuyo historial no le importa a nadie. La rama `data` se reemplaza con un commit nuevo en cada ejecución, así que el
repo se queda en ~2 MB para siempre. `main` solo lleva código y, como mucho, un commit mínimo cada ~30 días (keepalive).

## Feed v2 (genérico, en GitHub Pages)

Qué cambia respecto al feed actual (auditoría 08, BE-01..BE-09):

| | Feed actual (`update-data.yml`) | Feed v2 (`publish-feed-v2.yml`) |
|---|---|---|
| Publicación | rama `data` (force-push) + jsDelivr | GitHub Pages con `actions/deploy-pages` (artefacto; **ningún commit a ninguna rama**) |
| Forma | un `data.json` de ~10 MB | un fichero por símbolo: `index.json`, `s/<SYM>/latest.json`, `s/<SYM>/d/<AAAA>.json`, `s/<SYM>/i.json`, `crypto/latest.json`, `macro.json`, `news.json`, `health.json`, `manifest.json` |
| Contenido | incluye `your_funds` y listas del dueño | **genérico**: `registry.json` (242 símbolos públicos y neutros). Lo del dueño va a Supabase |
| Divisa | `price` sin divisa | `currency` + `unit` (+ `price_unit_divisor`) explícitos; `GBp` ≠ `GBP`; sin divisa/unidad verificada no se publica |
| Histórico | `max` entero en cada run | **incremental**: ~1 mes por símbolo y run, fusionado por fecha; los años cerrados no se reescriben |
| Símbolo muerto | 2 desaparecidos paran el run | no bloquea: `stale` hasta 30 días, `invalid`/`delisted`, reintento semanal |
| Validación | `sanity_check.py` | `sanity_check_v2.py`: esquema estricto, series, continuidad con el sitio anterior (mediana de cierres ≈ 1: detecta el ×100) |
| Sitio anterior | `curl` a `raw.githubusercontent.com` | descarga propia con `scripts/site_download.py`, verificada contra el manifiesto; **404 = primer run, cualquier otro fallo = no se publica** (BE-06) |

Contrato para el cliente (campos, ejemplos JSON reales, algoritmo de lectura, equivalencias con v1): **[`docs/CONTRATO_FEED_V2.md`](docs/CONTRATO_FEED_V2.md)**.

### Cómo funciona `publish-feed-v2.yml` (4 jobs)

| Job | Permisos | Qué hace |
|---|---|---|
| `fetch` | `contents: read` | instala con `--require-hashes`, baja el sitio publicado **completo** (hay que volver a incluir los años cerrados en el artefacto de Pages), ejecuta `scripts/feed_v2.py` y sube el sitio como artefacto. Ejecuta código de terceros: su salida se trata como no fiable |
| `publish` | solo `pages: write` + `id-token: write` | **no ejecuta código de terceros**. Hace su propia descarga del sitio anterior (modo `validate`), valida con `scripts/sanity_check_v2.py` (solo stdlib, sacado del repo) y solo entonces sube el artefacto de Pages y despliega |
| `verify` | ninguno | informativo (`continue-on-error`): comprueba que Pages ya sirve el `manifest.json` nuevo |
| `heartbeat` | ninguno | Healthchecks.io: ping normal, `/fail` si falla `fetch`, `publish` o el validador, y `/fail` con el motivo (`degraded: ...`) solo si se publicó con **más del 10 % de los símbolos activos sin publicar** o con **caída global de Yahoo** (429 sostenido o fallo de la mayoría a la vez). Un símbolo suelto sin publicar (`unit_mismatch`, `failed`, `suspect`...) es solo un `::warning::` en el log y el detalle queda en `health.json`. El secreto se limpia (espacios, tabuladores, `\r`, `\n`, comillas o acentos graves envolventes) y debe ser `https://hc-ping.com/...`; si no, `::warning::` sin el valor y no se llama a `curl` |

`concurrency` serializa los runs, todos los jobs tienen `timeout-minutes`, las acciones van fijadas por SHA y el cron es `47 6,11,15,21 * * *` (media hora después del
del feed actual para no coincidir en la IP del runner). **El cron del workflow viejo no se ha tocado.** Los datos propios (NAV de iShares en Supabase) ya **no** se publican desde aquí: viven en `personal-nav.yml` (ver «Datos propios»).

`scripts/feed_v2.py` reutiliza la lógica probada de `fetch_market_data.py` (ritmo y 429 de Yahoo con pausa y jitter, interruptor de racha de fallos, presupuesto de tiempo
global de BE-12, FRED, CNN, alternative.me, CoinGecko, RSS) y añade: histórico incremental, niveles `hot` (en cada run, con intradía de 5 min) / `warm` (~1 vez al día) /
`cold` (~1 vez por semana), cupo de 80 descargas largas por run para repartir el alta de símbolos nuevos, detección de splits (si el pasado guardado difiere >2 % del que da
Yahoo ahora se baja la serie entera una vez), y estado por símbolo en `health.json`. Todo símbolo se valida con `^[A-Za-z0-9.^=\-]{1,15}$` (más: sin punto inicial ni `..`) y se
codifica en URL siempre.

**El feed nunca contiene precios <= 0 ni no finitos.** Algún dato real de Yahoo los tiene (el WTI, `CL=F`, cerró en negativo el 2020-04-20), y `sanity_check_v2.py` seguirá rechazando
cualquier sitio que los lleve: el arreglo está en el generador, que **descarta** esas barras antes de escribir y las **anota** en `health.json` (`problems.dropped_bars`: símbolo, nº y fechas;
y una línea `~ SÍMBOLO: n barras descartadas` en el log). Un símbolo que pierde más de 3 barras (o más del 1 %) se considera sospechoso y no se publica (`problems.suspect`, aviso en el
resumen del run). Detalle en `docs/CONTRATO_FEED_V2.md` y `auditorias/FIXES_FEED_V2_NEG.md`.

**Mediciones** (datos sintéticos del test `tests/test_feed_v2.py::test_medicion_...`, con el `registry.json` real: 224 símbolos de Yahoo, 49 hot / 153 warm / 22 cold;
`V2_MEASURE=1 pytest -s tests/test_feed_v2.py -k medicion` las imprime):

| Run | Llamadas a Yahoo | Tamaño total |
|---|---|---|
| Alta (3 runs seguidos por el cupo de 80 descargas largas) | 129 + 80 + 64 | 13 → 26 → 37 MB |
| Run normal solo hot (3 de los 4 del día) | 98 (49 diarias de 1 mes + 49 intradía) | sin cambios (+~30 KB) |
| Run con warm (1 de los 4; normalmente el de las 21:47, el primero tras ≥20 h) | 251 (202 diarias + 49 intradía) | sin cambios |
| **Por día** (3 × 98 + 251) | **~545** (el feed actual: ~277 × 4 ≈ 1100 para 58 símbolos) | |
| Sitio completo en estado estable | | 37 MB sin comprimir en 2518 ficheros (~14,5 MB con zlib -6; la serie sintética lleva 6 decimales en todos los precios, así que los reales pesarán menos); `manifest.json` 285 KB |

Además, en cada run: 1 llamada a CoinGecko (250 monedas), ~5 a FRED (con clave), 3 a CNN/alternative.me, ~30 RSS. El run de `fetch` baja del sitio anterior ~2500 ficheros
(~37 MB sin comprimir, ~14,5 MB por la red con gzip; unos 3 minutos con 16 hilos, **estimación sin medir contra Pages real**) y el de `publish` solo ~670 (manifest + `latest.json` + los dos últimos años de cada símbolo).

### Activarlo (lo hace el dueño, a mano)

1. **Settings → Pages → Source: GitHub Actions.** (Sin esto el despliegue falla.)
2. **Settings → Environments → github-pages**: ese entorno suele restringir el despliegue a la rama por defecto (`main`); si lanzas el workflow desde otra rama, hay que permitirla ahí.
3. *(Opcional)* **Secrets** (Settings → Secrets and variables → Actions): `HC_UPDATE_V2_URL` (check propio de Healthchecks; recomendado, para que el feed viejo y el nuevo
   no se tapen entre sí; si no existe se usa `HC_UPDATE_URL`) y `PERSONAL_FEED_TOKEN` (lo usa solo `personal-nav.yml`; ver «Datos propios»). `FRED_API_KEY` ya existe y se reutiliza.
4. **Variables**: `SUPABASE_URL` y `SUPABASE_PUBLISHABLE_KEY` (ya existen para el keepalive; las usa `personal-nav.yml`). Opcional: `FEED_V2_BASE_URL` (dominio propio, URL de la carpeta `/v2`, https).
5. **Antes del primer run**, contrasta los SHA de las dos acciones nuevas (no se pudieron comprobar al escribir el workflow, BE-19):
   `gh api repos/actions/upload-pages-artifact/git/ref/tags/v3.0.1 --jq .object.sha` (debe ser `56afc609…`) y `gh api repos/actions/deploy-pages/git/ref/tags/v4.0.5 --jq .object.sha`
   (`d6db9016…`). Si Dependabot propone versiones nuevas, mejor.
6. **Actions → «Publish feed v2» → Run workflow.** El primer run es «primer run» (404 en `manifest.json`) y solo publica ~80 símbolos (cupo de altas); lánzalo 2-3 veces seguidas
   (o espera al cron) hasta que `health.json.counts.published` iguale a los símbolos de Yahoo del registro (224). Revisa `health.json.problems.unit_mismatch` (`{símbolo: {registro, yahoo}}`) y `problems.failed` (`{símbolo: motivo}`): las divisas/unidades del `registry.json` se
   escribieron sin acceso a Yahoo; un símbolo cuya divisa real no coincide **no se publica** hasta que se corrige su entrada (`currency`/`unit`).
7. Cuando esté a tu gusto: **variable de repo `FEED_V2_ENABLED = true`** para que el cron programado empiece a funcionar (también el de `personal-nav.yml`; hasta entonces el cron no hace nada y el lanzamiento manual sí).

Un split legítimo (o retirar símbolos a propósito) hace saltar las reglas de continuidad del validador (es justo lo que protege del ×100): lanza el workflow una vez con **allow_regression**.

### Datos propios del dueño (Supabase, fuera del repo)

El backend **no** lleva en el repo ISIN, fondos clase S ni listas del dueño (el feed v2 es genérico). Lo suyo vive en Supabase y lo publica **`personal-nav.yml`**
(un solo job: `watch` → `build` → `publish`, independiente de `publish-feed-v2.yml`):

* lee la lista con la RPC `personal_watch_for_backend(p_token)` y publica con `publish_personal_feed(p_token, p_user, p_payload)` (contrato y ejemplos en el
  [documento del contrato](docs/CONTRATO_FEED_V2.md#5-datos-propios-supabase-fuera-del-repo));
* **solo** la clave publicable en la cabecera `apikey` + un token aleatorio (secreto `PERSONAL_FEED_TOKEN`) en el cuerpo. **Nunca** `service_role` ni `sb_secret_`: si la clave lo
  parece, `scripts/personal_v2.py` aborta sin enviar nada (igual que `supabase-keepalive.yml`);
* el payload (≤ 1 MB) lleva el NAV por ISIN (scraping de la ficha de iShares), los *facts* y las series (≤ 2 años) solo de lo que **no** está en el universo público; un NAV con un salto fuera de 0,5–2×
  respecto al anterior no se publica;
* **frecuencia**: cada 30 min, **lunes a viernes**, de 05:23 a 21:53 UTC (`23,53 5-21 * * 1-5`: 34 runs al día; minutos «feos» a propósito, y distintos de los de `update-data.yml` (:17) y
  `publish-feed-v2.yml` (:47)). Antes el job vivía dentro de `publish-feed-v2` (4 runs al día) y el NAV de iShares, que sale entre medias, llegaba al dashboard con horas de retraso. Cada run publica
  el payload **completo** (la RPC lo sustituye), por eso `personal-nav.yml` tiene su **propia concurrencia** (`personal-nav`) y es el **único escritor** en Supabase: un run viejo que acabase
  después de uno nuevo pisaría datos frescos con datos viejos. Un cron retrasado o descartado por GitHub no importa: el siguiente lo corrige;
* **salida temprana**: `watch` escribe `work=true|false` en `$GITHUB_OUTPUT`; si la lista no tiene ningún fondo con ficha de iShares ni símbolos, el job no instala dependencias ni construye ni publica;
* **gating**: igual que el resto, el cron solo corre con la variable de repo `FEED_V2_ENABLED = true` (el lanzamiento manual, *Run workflow*, funciona siempre);
* **variables y secretos que usa**: variables `SUPABASE_URL`, `SUPABASE_PUBLISHABLE_KEY` y `FEED_V2_ENABLED`; secreto `PERSONAL_FEED_TOKEN` (solo en los pasos `watch` y `publish`, que ejecutan únicamente biblioteca
  estándar con `python3 -S`; el paso `build`, con código de terceros, no lo recibe);
* es **opcional**: sin Supabase o sin token, o si algo falla, solo hay un `::warning::` y la salida es 0 (solo una clave secreta o una URL no https hacen fallar el job);
* **cómo comprobar que el NAV es el de la ficha** (log del paso *Build personal payloads*, solo contadores y fechas de NAV, nunca ISIN, nombres ni importes):

  ```
  datos propios: NAV: 2 fondos, 2 con NAV exacto leído de la ficha, fecha de NAV más reciente 2026-10-27 (1 d), más antigua 2026-10-26 (2 d), decimales del NAV: 2 (x2) [run 2026-10-28T10:23Z]
  datos propios: coste del run: 2 series pedidas a Yahoo (únicas entre usuarios), payload 20.6 KB, 3.4 s
  ```

  Compara la fecha de NAV con la de la ficha pública: la ficha de iShares escribe el NAV con **2 decimales** (`EUR 12.48`; lo muestran todas las fichas reales guardadas en `tests/fixtures_fund_facts.json`, y el
  log lo confirma en cada run), así que el dashboard debe coincidir **exactamente** con la ficha, no con el redondeo de otra fuente. `::warning::` si algún NAV tiene más de 5 días (`--nav-warn-days`),
  si algún fondo se queda sin NAV exacto, o si la página trae varios «NAV as of» distintos (`nav_varios`: se usa el primero);
* privacidad: los logs de un repo público son públicos, así que `personal_v2.py` solo escribe contadores (nunca ISIN, símbolos, URL, clave ni token); el payload nunca va en un artefacto;
  el token solo está en los pasos de biblioteca estándar (`python3 -S`), no en el paso que ejecuta código de terceros;
* **coste** (medidas simuladas y estimaciones en `auditorias/FIXES_FEED_V2_F.md`): por run, una petición a iShares por ficha y una a Yahoo por símbolo **único** (los proxies de los fondos y los símbolos
  fuera del universo público; los que siguen varios usuarios se bajan una sola vez), ~1,5–2 min de runner (casi todo es `pip install`). Con dos fondos y sus dos proxies: ~70 peticiones a Yahoo al día
  frente a ~545 del feed público. Si algún día el repo pasara a **privado**, 34 runs × ~2 min × ~22 días son ~1.500 min/mes: casi todo el cupo gratuito.

### Riesgos que conviene tener presentes

* **Términos de uso de Yahoo Finance e iShares.** `yfinance` se documenta como herramienta de uso personal, y leer automáticamente ishares.com puede chocar con sus términos. Redistribuir sus
  datos por Pages **a un círculo reducido** (el dueño y amigos) es un riesgo asumible y acotado; **no escala a un servicio abierto**: para eso haría falta una fuente con licencia de redistribución
  (Stooq, Twelve Data, Alpha Vantage, EOD...). Revisarlo es una decisión del dueño; aquí no se ha podido contrastar con los términos vigentes.
* **Cuota de Yahoo**: sin SLA ni límite publicado, se limita por IP. El diseño incremental baja las peticiones a ~545 al día; aun así un 429 sostenido abandona Yahoo en ese run (los símbolos
  pendientes conservan sus datos anteriores) y no penaliza a nadie.
* **Pages**: límite blando de ancho de banda (orden de 100 GB/mes, a contrastar con la documentación vigente) y de tamaño del sitio (1 GB; aquí ~37 MB). Cada run de `fetch` descarga el sitio anterior completo: ~60 MB al día por la red (comprimido), unos 2 GB al mes.
* **Rutas con `^` y `=`** (`s/^GSPC/...`, `s/EURUSD=X/...`): son válidas y los clientes las codifican, pero no se han comprobado contra Pages real; si alguna fallara, es un cambio de nombre de ruta.
* **Años cerrados inmutables**: un split o un ajuste los cambia (se detecta y se baja la serie entera), pero el validador lo trata como regresión y hay que desbloquearlo con `allow_regression`.
* Python 3.14 solo en CI; los tests locales corren en la versión que haya.

### Purga del historial (BE-09)

La rama `data` del feed actual se sobrescribe con `force-push`, pero los commits anteriores **siguen accesibles por su SHA** y el historial de `main` contiene los ISIN. El feed v2 ya no publica nada del
dueño, pero eso no borra lo ya publicado. La única purga real es **recrear el repositorio** (los SHA antiguos mueren con él). Lo hace el dueño, tras el corte, y arrastra: crear el repo nuevo con el código actual
(sin historial), volver a poner secretos y variables, activar Pages y Dependabot/ruleset, y actualizar la URL base del dashboard. Hasta entonces, trata como públicos los ISIN y la lista cripto del código v1.

### Corte (retirar el feed viejo)

1. El dashboard lee de v2 (equivalencias en el contrato) y los datos propios de Supabase.
2. Se borran `update-data.yml`, la rama `data`, `sanity_check.py` y sus tests, y de `fetch_market_data.py` las listas del dueño (`YOUR_FUNDS`, `CRYPTO_WATCHLIST`, `STOCKS` de nicho) y las funciones de v1 que v2 no usa. **El módulo se queda**: `feed_v2.py` reutiliza su lógica de Yahoo, FRED, CoinGecko, RSS e iShares. Algunos tests de v2 leen `YOUR_FUNDS` de ese módulo para comprobar que el registro público no contiene nada del dueño; al borrarlo hay que darles otra fuente (p. ej. un hash).
3. Se recrea el repo (BE-09) si se quiere borrar el pasado.

### Cambiar lo que se sigue en v2

Edita `registry.json` (símbolo, `kind` `yahoo|crypto`, nombre, `currency`, `unit`, `tier` `hot|warm|cold`, `status`). `currency` es la divisa **tal cual la cotiza Yahoo** (`GBp` para las líneas de Londres
en peniques) y `unit` debe ser coherente (`subunit` solo con `GBp`/`GBX`/`ZAc`/`ILA`/`USd`/`USX`); un test valida el fichero y un símbolo con otra divisa real no se publica. El registro es público: no pongas nada tuyo.
Quitar símbolos hace saltar las reglas de regresión: lanza el workflow una vez con `allow_regression`.

## Otros workflows

* `keepalive.yml` (semanal): GitHub desactiva los workflows programados de un repo público tras 60 días sin actividad.
  Si el último commit de la rama por defecto tiene más de 30 días, añade un commit mínimo (una fecha en
  `.github/keepalive`) y además reactiva `update-data.yml` por si estuviera desactivado. Con commits normales o merges
  de Dependabot no hace nada. Que cuente como actividad para GitHub no está confirmado por su documentación:
  Healthchecks.io sigue siendo la red de seguridad.
* `supabase-keepalive.yml` (cada 3 días, `37 3 */3 * *`): evita que el proyecto gratuito de Supabase se pause por
  inactividad con **una** lectura a la REST API (`GET /rest/v1/profiles?select=user_id&limit=1`, cabecera `apikey`).
  200, 401 y 403 cuentan como correctos (la tabla está protegida por RLS); red caída, 5xx o cualquier otro código
  (404, 540... un proyecto pausado) fallan tras 3 intentos. Aborta si la clave configurada empieza por `sb_secret_`,
  contiene `service_role` o es un JWT con ese rol. Los logs solo muestran códigos HTTP.
  **Configuración** (Settings -> Secrets and variables -> Actions):
  * pestaña *Variables*: `SUPABASE_URL` (p. ej. `https://<ref>.supabase.co`) y `SUPABASE_PUBLISHABLE_KEY`
    (`sb_publishable_...`; es pública por diseño, **nunca** pongas aquí la `sb_secret_` ni la `service_role`);
  * pestaña *Secrets* (opcional): `HC_SUPABASE_URL`, URL de ping de un check propio de Healthchecks.io (periodo 3 días
    con gracia de al menos 1 día). Si falta, está vacío o no tiene la forma `https://hc-ping.com/...` (se limpian antes espacios, saltos de línea y comillas envolventes), el workflow solo emite un `::warning::` (sin el valor) y sigue sin avisar.

  **Cómo comprobarlo**: Actions -> "Supabase keepalive" -> *Run workflow*; el job debe terminar en verde con
  «Supabase responde (HTTP 200|401|403)». Prueba negativa: pon una URL inventada en `SUPABASE_URL`, relanza y debe
  fallar con `HTTP 000`. Localmente: `curl -s -o /dev/null -w '%{http_code}\n' -H "apikey: $KEY" "$URL/rest/v1/profiles?select=user_id&limit=1"`.
* `ci.yml` (en cada PR): sintaxis de Python, `ruff --select F`, `pytest` (sanity-check, scrape de iShares, FRED,
  noticias y los bloques `run:` de los workflows), `actionlint` si está disponible y comprobación de que no hay ficheros
  versionados que `.gitignore` excluye (HTML, copias, exports, `.env`, claves...).
* `dependabot.yml`: PRs mensuales para actualizar las acciones (fijadas por SHA) y las dependencias de Python, con un
  `cooldown` de 7 días para no adoptar versiones recién publicadas. Revísalos antes de aceptarlos.

## Dependencias

`requirements.in` lista las dependencias directas con rango (`yfinance>=1.7.0,<2`...: un salto de versión mayor no entra solo).
`requirements.txt` se **genera** desde él con versiones exactas y hashes de todas las transitivas, y el job `fetch` (el único que
ejecuta código de terceros, con `FRED_API_KEY` en el entorno) lo instala con `pip install --require-hashes --no-deps`: un archivo
cuyo hash no coincida no se instala. Para regenerarlo tras cambiar `requirements.in`:

```bash
uv pip compile requirements.in --generate-hashes --universal --python-version 3.14 --no-header -o requirements.txt
```

Dependabot lee el par `.in`/`.txt`. Además, `sanity_check.py` valida la **forma** del feed (lista blanca de claves, strings sin
marcado ni caracteres de control, tope de longitud): si añades un campo al backend, añádelo también ahí (hay un test que lo recuerda).

## Configuración inicial

1. Repo **público** (necesario para jsDelivr; minutos de Actions ilimitados).
2. Archivos en `main`: `scripts/` (v1: `fetch_market_data.py`, `sanity_check.py`; v2: `feed_v2*.py`, `sanity_check_v2.py`, `site_download.py`, `personal_v2.py`), `registry.json`, `docs/`, `tests/`, `requirements.txt`,
   `.github/workflows/*.yml`, `.github/dependabot.yml`, `.gitignore`, este README.
3. Secretos (Settings -> Secrets and variables -> Actions): `FRED_API_KEY` (opcional, gratis en
   <https://fred.stlouisfed.org/docs/api/api_key.html>; sin ella no hay tipos ni IPC) y `HC_UPDATE_URL`
   (URL de ping de Healthchecks.io). En Healthchecks configura el check en modo **Simple** con Period = 1 día y
   Grace Time = 12 horas (alerta tras 36 h sin ping). Es deliberado: GitHub Actions omite ejecuciones programadas con
   frecuencia y solo queremos aviso ante una caída real, no por un salto puntual.
4. Actions -> "Update market data" -> **Run workflow**. La primera ejecución crea la rama `data`.
5. Settings -> Code security: Secret scanning + Push protection + Dependabot alerts.
6. Settings -> Rules: ruleset sobre `main` con "Restrict deletions" y "Block force pushes". **No** proteger la rama
   `data`: el workflow la sobrescribe a propósito. Tampoco exijas PR ni checks obligatorios en `main` sin revisar el
   keepalive: su commit mínimo se empuja directamente.

## Ejecutarlo en local

```bash
pip install -r requirements.txt
export FRED_API_KEY=tu_clave   # opcional
python scripts/fetch_market_data.py   # escribe data.json + meta.json
python3 scripts/sanity_check.py --data data.json --meta meta.json   # añade --prev prev.json para las reglas de regresión

# feed v2 (genera el sitio en site/v2; usa --prev con una descarga del sitio anterior para el histórico incremental)
python scripts/feed_v2.py --registry registry.json --out site/v2
python3 scripts/sanity_check_v2.py --site site/v2 --registry registry.json   # añade --prev prev_site/v2 para la continuidad

pip install -r requirements-dev.txt   # pytest, ruff y PyYAML con la versión exacta de la CI
ruff check --select F scripts tests
python -m pytest -q
```

## Cambiar lo que se sigue

Edita las listas de arriba de `scripts/fetch_market_data.py` (`INDICES`, `STOCKS`, `COMMODITIES`, `FOREX`,
`COUNTRY_HEATMAP`, `RATES`): listas de tuplas `(símbolo, nombre)` con tickers de Yahoo Finance o IDs de series de
FRED. Una acción solo tiene precio automático en el dashboard si su ticker está en `STOCKS`. Recuerda que `YOUR_FUNDS`
y `CRYPTO_WATCHLIST` acaban en el feed público (ver arriba), y que quitar símbolos hace saltar las reglas de regresión
del sanity-check: lanza el workflow una vez con `allow_regression`.
