# Feed de datos de mercado para My Portfolio Dashboard

Este repo hace una sola cosa: un workflow de GitHub Actions ejecuta un script de Python (programado 4 veces al día; en la práctica GitHub lo lanza menos, ver [«Cadencia real»](#cadencia-real-de-github-actions-y-qué-umbral-usar)), descarga
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

El repo y la rama `data` son **públicos**: cualquiera puede leer lo que contienen. El feed v1 (`data.json`) lleva solo precios y series de mercado de
símbolos públicos (índices, acciones, ETF, divisas, materias primas, mapa de países, IPC de España, tipos de FRED) y noticias generales. Desde el cierre del
corte **ya no lleva nada del dueño**: ni `your_funds`, ni ISIN, ni nombres de sus fondos, ni lista de criptomonedas vigiladas; `news.positions` se conserva
**vacío** para no cambiar el esquema, y `sanity_check.py` rechaza un feed que traiga `your_funds` otra vez (el job `fetch` ejecuta código de terceros).
Lo del dueño (NAV de sus fondos, series de sus proxies) vive en Supabase y lo publican `personal-nav.yml` y `personal-full.yml`.

No hay importes, cantidades, saldos ni operaciones. El dashboard, las posiciones y las copias de seguridad **no**
forman parte de este repo (`.gitignore` y el CI bloquean `*.html`, `*backup*`, `*export*`, `*.csv`, `*.xlsx`, `*.zip`,
`.env*` salvo `.env.example`, `*.pem` y `*.key`). Las copias de seguridad y los informes (mensual, anual, fiscal) los guarda el propio dashboard en las
carpetas locales que se configuran en Ajustes; no hay ningún job en la nube que los genere.

**Lo que NO está limpio todavía: el historial de git.** El árbol actual no contiene ningún ISIN ni id de ficha del dueño (los tests y fixtures usan datos
sintéticos y `tests/test_privacidad_isin.py` falla si reaparece un ISIN con forma `IE…` que no esté en su lista de inventados), pero los commits anteriores, la rama `data`
antigua y las referencias de los PR **siguen teniendo** los ISIN de sus dos fondos clase S, las fichas de iShares y la lista cripto. Quitarlos del código no los borra del
pasado: ver «Purga del historial (BE-09)».

### El sanity-check (`scripts/sanity_check.py`)

Se ejecuta en `publish`, antes del force-push. Si falla, no se publica nada y el dashboard sigue sirviendo el último
dato bueno (el heartbeat manda `/fail`).

* **Reglas absolutas** (el estado del feed por sí solo): mínimo de índices/acciones/fondos/materias primas/divisas,
  histórico en al menos el 70 % de cada lista, mediana de histórico de acciones e índices >= 250 barras, la mayoría de
  símbolos con una barra de los últimos ~6 días (histórico no congelado), precios plausibles, `market_mood` con VIX o Fear & Greed de bolsa, tamaño entre 3 y 18 MB, `meta.json` coherente...
* **Reglas de regresión** contra el feed publicado anteriormente (se descarga de
  `raw.githubusercontent.com/<repo>/data/data.json`): símbolos desaparecidos (tolerancia 5 %, mínimo 1), histórico
  de un símbolo acortado más de un 10 % y tamaño inferior al 60 % del anterior.
  Si no hay feed anterior (primer run, rama borrada, descarga fallida) solo se aplican las absolutas.
* **Cambios intencionados**: si quitas símbolos a propósito, las reglas de regresión bloquearán todos los runs programados. Lanza una vez **Run workflow** con
  `allow_regression` marcado: las reglas de regresión pasan a avisos (las absolutas siguen bloqueando) y el feed nuevo
  pasa a ser el de referencia.
* También emite `::warning::` si hay datos de FRED reutilizados. La alerta de **NAV congelado** (antes aquí, sobre `your_funds`) ya no existe en el feed v1: ahora la da el
  latido de `personal-nav.yml` (ver «Datos propios del dueño»).

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
| Contenido | llevaba `your_funds` y listas del dueño (ya no, desde el cierre del corte) | **genérico**: `registry.json` (242 símbolos públicos y neutros). Lo del dueño va a Supabase |
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
4. **`SUPABASE_URL` y `SUPABASE_PUBLISHABLE_KEY`**: los workflows las leen con `secrets.X || vars.X`. Hoy existen como *variables* (salen en **claro** en el log público del run); lo recomendado es crearlas como **secretos** con el mismo nombre
   (GitHub los enmascara), borrar después las variables y rotar la clave publicable (runbook). Opcional: variable `FEED_V2_BASE_URL` (dominio propio, URL de la carpeta `/v2`, https).
5. **Antes del primer run**, contrasta los SHA de las dos acciones nuevas (no se pudieron comprobar al escribir el workflow, BE-19):
   `gh api repos/actions/upload-pages-artifact/git/ref/tags/v3.0.1 --jq .object.sha` (debe ser `56afc609…`) y `gh api repos/actions/deploy-pages/git/ref/tags/v4.0.5 --jq .object.sha`
   (`d6db9016…`). Si Dependabot propone versiones nuevas, mejor.
6. **Actions → «Publish feed v2» → Run workflow.** El primer run es «primer run» (404 en `manifest.json`) y solo publica ~80 símbolos (cupo de altas); lánzalo 2-3 veces seguidas
   (o espera al cron) hasta que `health.json.counts.published` iguale a los símbolos de Yahoo del registro (224). Revisa `health.json.problems.unit_mismatch` (`{símbolo: {registro, yahoo}}`) y `problems.failed` (`{símbolo: motivo}`): las divisas/unidades del `registry.json` se
   escribieron sin acceso a Yahoo; un símbolo cuya divisa real no coincide **no se publica** hasta que se corrige su entrada (`currency`/`unit`).
7. Cuando esté a tu gusto: **variable de repo `FEED_V2_ENABLED = true`** para que el cron programado empiece a funcionar (también el de `personal-nav.yml`; hasta entonces el cron no hace nada y el lanzamiento manual sí).
8. **Cuando el sitio esté completo** (`health.json.counts.published` iguala a los símbolos de Yahoo del registro y el cliente ya lee de él): **variable de repo `FEED_V2_INITIALIZED = true`**. A partir de ahí un 404 en
   `manifest.json` (Pages caído o reiniciado, repo recreado) **deja de tratarse como «primer run»** y pasa a ser un fallo que impide publicar (con el 404 transitorio el sitio entero se reiniciaría con ~80 de 224 símbolos). Sin la
   variable, o con otro valor, el comportamiento es el de siempre. Si algún día hay que empezar de cero a propósito (repo nuevo), borra la variable o ponla a `false` antes del primer run.

Un split legítimo (o retirar símbolos a propósito) hace saltar las reglas de continuidad del validador (es justo lo que protege del ×100): lanza el workflow una vez con **allow_regression**.

### Datos propios del dueño (Supabase, fuera del repo)

El árbol actual del repo **no** lleva ISIN, ids de ficha, fondos clase S ni listas del dueño (el feed v2 es genérico; el historial anterior sí, ver «Purga del historial (BE-09)»). Lo suyo vive en Supabase y lo publican **dos workflows con dos frecuencias**
(ambos `watch` → `build` → `publish`, independientes de `publish-feed-v2.yml`):

| Workflow | Qué publica | RPC | Cuándo |
|---|---|---|---|
| **`personal-nav.yml`** (solo NAV) | únicamente el NAV, su fecha, `change_pct`, `wk52_*` y *facts* de cada fondo, leídos de **su ficha de iShares** (1 petición por ficha, sin Yahoo ni series; < 1 KB por fondo, tope 64 KB) | `publish_personal_nav(p_token, p_user, p_funds)` | programado cada 30 min, L-V (`23,53 5-21 * * 1-5`); **en la práctica GitHub lo lanza unas pocas veces al día** (ver «Cadencia real») |
| **`personal-full.yml`** (completo) | el payload completo de siempre: `funds` + `series` (proxies con histórico largo) | `publish_personal_feed(p_token, p_user, p_payload)` | programado 06:38, 11:38, 15:38 y 21:38 UTC, todos los días (`38 6,11,15,21 * * *`); en la práctica, con horas de retraso o alguna pasada menos |

**Lanzarlos a mano** (*Actions → «Personal NAV» / «Personal full» → Run workflow*, rama `main`; sin parámetros; funciona aunque `FEED_V2_ENABLED` no sea `true`). En local, con las tres variables de entorno
(`SUPABASE_URL`, `SUPABASE_PUBLISHABLE_KEY`, `PERSONAL_FEED_TOKEN`): `python scripts/personal_v2.py all --nav-only --dir /ruta/privada` (NAV) o `python scripts/personal_v2.py all --dir /ruta/privada` (completo).
El job completo **crea** el payload de un usuario nuevo (la RPC de NAV responde `no_payload` hasta entonces; el job NAV lo anota y sigue). **Hasta que el SQL con `publish_personal_nav` esté aplicado en Supabase**, el job NAV
falla con el error «RPC publish_personal_nav no existe todavía» y **no publica nada** (el completo sigue funcionando solo); con la variable de repo `PERSONAL_NAV_FALLBACK_FULL = 1` (apagada por defecto) ese run publicaría el
payload completo como alternativa. **Latido opcional** del job NAV: el secreto `HEALTHCHECK_PERSONAL_NAV_URL` (una URL `https://` de Healthchecks u otro; vacío o inexistente = no hace nada).
Corre siempre (salvo cancelación) al final de cada run: **ping normal** si todo fue bien y **`/fail`** (texto fijo, sin datos del dueño) si falló algún paso, si algún payload no se publicó
(`nav_state=failed`) o si el NAV más reciente tiene **más de 6 días** o ningún fondo tiene NAV exacto (`nav_state=old`: el cron sigue vivo pero iShares o el scrape llevan días parados).
Si no llega ningún ping, Healthchecks avisa de que el cron se ha parado. En Healthchecks: **Period = 3 días y Grace Time ≥ 14 horas** (el cron es de lunes a viernes y GitHub lo retrasa horas; con
Period = 1 día saltaría cada fin de semana). Es un secreto y no una variable porque las variables salen en claro en el log del run, que es público.
El payload completo (`personal-full.yml`) sale con código 1 si fallan **todos** los payloads (antes terminaba siempre en verde).

Lo que sigue describe sobre todo el job completo (`personal-nav.yml` lo era antes de separar las frecuencias):

* lee la lista con la RPC `personal_watch_for_backend(p_token)` y publica con `publish_personal_feed(p_token, p_user, p_payload)` (contrato y ejemplos en el
  [documento del contrato](docs/CONTRATO_FEED_V2.md#5-datos-propios-supabase-fuera-del-repo));
* **solo** la clave publicable en la cabecera `apikey` + un token aleatorio (secreto `PERSONAL_FEED_TOKEN`) en el cuerpo. **Nunca** `service_role` ni `sb_secret_`: si la clave lo
  parece, `scripts/personal_v2.py` aborta sin enviar nada (igual que `supabase-keepalive.yml`);
* el payload (≤ 1 MB) lleva el NAV por ISIN (scraping de iShares), los *facts* y las series de cierres diarios: los **proxies de los fondos** con todo el histórico disponible (hasta ~25 años,
  esté o no el proxy en el universo público, que solo guarda 10 años) y el resto de símbolos extra con ≤ 2 años, y estos últimos solo si **no** están en el universo público; si el
  payload pasa de 1 MB se recortan primero los extra y después los proxies (ver el contrato); un NAV con un salto fuera de 0,5–2× respecto al anterior no se publica;
* **frecuencia y concurrencia**: el NAV de iShares sale entre medias de los runs del feed, y el payload completo con series largas (~165 KB) no hace falta repetirlo cada 30 min: por eso NAV programado cada 30 min (minutos :23 y :53,
  «feos» a propósito) y completo 4 al día (minuto :38, a ≥ 15 min de cada run de NAV; ninguno coincide con `update-data.yml` (:17) ni `publish-feed-v2.yml` (:47)). Cada workflow tiene su **propia concurrencia**
  (`personal-nav` y `personal-full`, sin cancelar a medias): dos runs del mismo job nunca se pisan. Entre los dos jobs no se serializa (un grupo común perdería runs pendientes): la RPC de NAV no sustituye un NAV
  por otro de fecha más antigua y el siguiente run de NAV corrige cualquier cruce (razonamiento en `personal-full.yml` y en el contrato). Un cron retrasado o descartado por GitHub no importa;
* **salida temprana**: `watch` escribe `work=true|false` en `$GITHUB_OUTPUT`; si la lista no tiene ningún fondo con ficha de iShares ni símbolos, el job no instala dependencias ni construye ni publica;
* **gating**: igual que el resto, el cron solo corre con la variable de repo `FEED_V2_ENABLED = true` (el lanzamiento manual, *Run workflow*, funciona siempre);
* **variables y secretos que usa**: `SUPABASE_URL` y `SUPABASE_PUBLISHABLE_KEY` se leen con `secrets.X || vars.X` (secreto si existe, variable si no; **recomendado: secretos**, que GitHub enmascara en el log público);
  variables `FEED_V2_ENABLED` y (opcional, solo NAV) `PERSONAL_NAV_FALLBACK_FULL`; secretos `PERSONAL_FEED_TOKEN` y (opcional, solo NAV)
  `HEALTHCHECK_PERSONAL_NAV_URL`. El token va solo en los pasos `watch` y `publish`, que ejecutan únicamente biblioteca
  estándar con `python3 -S`; el paso `build`, con código de terceros, no lo recibe;
* es **opcional**: sin Supabase o sin token, o si algo falla, solo hay un `::warning::` y la salida es 0 (solo una clave secreta, una URL no https o que fallen **todos** los payloads del modo completo hacen fallar el job);
* **usuarios**: el tope es `MAX_USERS = 20`; los que sobran no reciben datos y el resumen de `watch` lo avisa con un `::warning::` (`N usuarios recortados`), sin identificadores;
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
* **coste** (medidas simuladas y estimaciones en `auditorias/FIXES_FEED_V2_F.md`): por run, **una** petición a iShares por ficha y una a Yahoo por símbolo **único** (una descarga `max` sirve también para 2 años): los proxies de los
  fondos con `period="max"` (histórico largo) y los símbolos extra con 2 años; los que siguen varios usuarios se bajan una sola vez. Con dos fondos y sus dos proxies: 2 peticiones a Yahoo por run
  (68 al día, frente a ~545 del feed público), pero con el histórico largo cada una baja ~3.000–6.000 barras (≈ 7.000 entre las dos) y el payload ronda **165 KB** (≈ 5,6 MB/día escritos en Supabase con 34 runs).
  ~1,5–2 min de runner por run (casi todo es `pip install`). Si algún día el repo pasara a **privado**, 34 runs × ~2 min × ~22 días son ~1.500 min/mes: casi todo el cupo gratuito.

#### Desfase de fecha del NAV (navegador 28/Oct, scraper 27/Oct): resuelto, y diagnóstico manual opcional

**Causa:** un dato guardado en la app, no el scraper. Tras lanzar el workflow, el dashboard mostró la fecha de NAV de la ficha y sus valores exactos: la petición de siempre (`base`) ya lee el NAV más
reciente. Por eso el scraper pide **una sola variante** por ficha (`NAV_FETCH_VARIANTS = ("base",)`) y no se doblan las peticiones a iShares. El log del build sigue dando la fecha de NAV más reciente y
la más antigua (sin identificar fondos), que es la comprobación a mano si vuelve a pasar.

`nav-probe.yml` se conserva como **diagnóstico manual opcional** (*Actions → NAV probe (diagnóstico) → Run workflow*; solo `workflow_dispatch`, sin cron; no escribe en ningún sitio: ni Supabase, ni artefactos,
ni Pages). Lee la lista de fichas con el mismo paso que el job personal (token, solo biblioteca estándar) y prueba cada una (#1, #2...) con variantes de la petición (`base`, `nocache`, `bust`, `fresh`, `browser`,
`browser_fresh`, `passthrough`, `base_again`), la misma ficha en otros sitios de iShares (`alt:uk|de|ie|es|pro`) y las XHR/JSON que cite el HTML (`xhr1`...; descritas sin id ni nombre del producto). Por variante
imprime el código HTTP, el tamaño, la **fecha y el valor del NAV** leídos y (opcional, `cache_headers`) las cabeceras de caché; nunca ISIN, nombres ni URLs. Termina con un `RESUMEN` por ficha y un `VEREDICTO`.
Si `base` va por detrás de `fresh`/`bust`/`nocache` → caché del HTML (en el código sigue `fetch_ishares_nav_freshest`, que elige la fecha más reciente entre las variantes de `NAV_FETCH_VARIANTS`: basta
añadir `"fresh"` a esa tupla); si solo `alt:*` o `xhr*` dan la fecha nueva → hay que cambiar de URL/fuente.

### Riesgos que conviene tener presentes

* **Términos de uso de Yahoo Finance e iShares.** Hacerse pasar por un navegador (User-Agent) para esquivar protecciones anti-bots podría chocar con sus términos: por eso solo está en el diagnóstico manual, no en el scraper (que pide una sola vez cada ficha). `yfinance` se documenta como herramienta de uso personal, y leer automáticamente ishares.com puede chocar con sus términos. Redistribuir sus
  datos por Pages **a un círculo reducido** (el dueño y amigos) es un riesgo asumible y acotado; **no escala a un servicio abierto**: para eso haría falta una fuente con licencia de redistribución
  (Stooq, Twelve Data, Alpha Vantage, EOD...). Revisarlo es una decisión del dueño; aquí no se ha podido contrastar con los términos vigentes.
* **Cuota de Yahoo**: sin SLA ni límite publicado, se limita por IP. El diseño incremental baja las peticiones a ~545 al día; aun así un 429 sostenido abandona Yahoo en ese run (los símbolos
  pendientes conservan sus datos anteriores) y no penaliza a nadie.
* **Pages**: límite blando de ancho de banda (orden de 100 GB/mes, a contrastar con la documentación vigente) y de tamaño del sitio (1 GB; aquí ~37 MB). Cada run de `fetch` descarga el sitio anterior completo: ~60 MB al día por la red (comprimido), unos 2 GB al mes.
* **Rutas con `^` y `=`** (`s/^GSPC/...`, `s/EURUSD=X/...`): son válidas y los clientes las codifican, pero no se han comprobado contra Pages real; si alguna fallara, es un cambio de nombre de ruta.
* **Años cerrados inmutables**: un split o un ajuste los cambia (se detecta y se baja la serie entera), pero el validador lo trata como regresión y hay que desbloquearlo con `allow_regression`.
* Python 3.14 solo en CI; los tests locales corren en la versión que haya.

### Purga del historial (BE-09)

La rama `data` del feed actual se sobrescribe con `force-push`, pero los commits anteriores **siguen accesibles por su SHA** y el historial de `main` (y las referencias de los PR, que no se pueden borrar desde la
interfaz) **contiene los ISIN, las fichas de iShares y la lista cripto del dueño**: quitarlos del árbol (hecho en el cierre del corte y en la rama de saneado de datos) no los borra del pasado. La única purga real
es **recrear el repositorio** (los SHA antiguos mueren con él). Lo hace el dueño, **después** de fusionar el árbol ya saneado, y arrastra: crear el repo nuevo con el código actual (sin historial), volver a poner secretos y
variables, activar Pages y Dependabot/ruleset, y actualizar la URL base del dashboard (ver el runbook «Tras recrear el repo»). Hasta entonces, trata como públicos esos datos: llevan semanas expuestos y copias,
cachés (jsDelivr) o forks no se pueden recuperar.

### Corte (retirar el feed viejo)

1. El dashboard lee de v2 (equivalencias en el contrato) y los datos propios de Supabase. **Hecho en el backend (bloque H1):** v1 ya no publica `your_funds` ni `news.positions` con contenido; el cliente
   (build ≥ 28b) los ignora.
2. Queda por borrar `update-data.yml`, la rama `data`, `sanity_check.py` y sus tests, y de `fetch_market_data.py` las listas de nicho de `STOCKS` y las funciones de v1 que v2 no usa. **El módulo se queda**:
   `feed_v2.py` reutiliza su lógica de Yahoo, FRED, CoinGecko, RSS e iShares (y `personal_v2.py`, la de iShares). Los tests que comprobaban que el registro público no lleva nada del dueño ya no leen listas del código:
   usan huellas (`tests/privacy_guard.py`).
3. Se recrea el repo (BE-09) si se quiere borrar el pasado.

### Cambiar lo que se sigue en v2

Edita `registry.json` (símbolo, `kind` `yahoo|crypto`, nombre, `currency`, `unit`, `tier` `hot|warm|cold`, `status`). `currency` es la divisa **tal cual la cotiza Yahoo** (`GBp` para las líneas de Londres
en peniques) y `unit` debe ser coherente (`subunit` solo con `GBp`/`GBX`/`ZAc`/`ILA`/`USd`/`USX`); un test valida el fichero y un símbolo con otra divisa real no se publica. El registro es público: no pongas nada tuyo.
Quitar símbolos hace saltar las reglas de regresión: lanza el workflow una vez con `allow_regression`.

## Cadencia real de GitHub Actions (y qué umbral usar)

**Los cron de GitHub Actions no son un reloj.** GitHub programa las ejecuciones `schedule` «cuando puede»: las retrasa (a veces horas), las agrupa y puede omitir alguna, sobre todo en los minutos con más carga,
y no avisa. Lo que dicen los `cron:` de los YAML es la **intención**, no una promesa:

| Workflow | Programado | Observado (auditoría del 2-nov-2026, API de Actions; una muestra de una semana, no una garantía) |
|---|---|---|
| `update-data.yml` | 4 al día (06:17, 11:17, 15:17, 21:17 UTC) | ~2,9 al día; retraso mediano ~3 h 20 min (de 1 h a 5 h 14 min); el hueco de las 06:xx no se ejecutó nunca; el mayor hueco entre dos runs fue de 13 h 20 min |
| `publish-feed-v2.yml` | 4 al día (06:47, 11:47, 15:47, 21:47 UTC) | ~3 al día; retraso mediano ~3 h 15 min (de 1 h 18 min a 4 h 21 min) |
| `personal-nav.yml` | cada 30 min, L-V, 05:23-21:53 UTC (34 pasadas) | el viernes 30-oct se ejecutaron **4** de 34 |
| `personal-full.yml` | 4 al día (06:38, 11:38, 15:38, 21:38 UTC) | sin medir (mismo mecanismo) |

Consecuencias, para no prometer lo que no existe:

* **No hay «NAV fresco cada 30 min»** ni una garantía de dato posterior al cierre de EE. UU. Con este mecanismo, el NAV de los fondos y el feed público se refrescan **unas pocas veces al día**, a horas que no controla este repo.
  `generated_at` es «último intento correcto», no «ahora».
* **El cliente no debe marcar el feed como viejo antes de ~14 horas** sin cambios (`index.json.generated_at` / `manifest.json.generated_at`); un umbral de 1-2 h daría falsas alarmas varias veces al día. Para el NAV de los fondos
  (`funds[].nav_date_iso`) manda la fecha de la ficha de iShares (normalmente 1-2 días hábiles por detrás), no la hora del run.
* Los símbolos de nivel `cold` se refrescan ~1 vez por semana: mira `as_of`, no solo `generated_at`.
* Los avisos de Healthchecks se configuran con esa realidad: Period = 1 día y Grace Time = 12 horas para el feed (alerta tras 36 h sin ping) y Period = 3 días y Grace ≥ 14 h para `personal-nav` (solo corre L-V).
* **Todos los workflows tienen `workflow_dispatch`** (un test lo comprueba): *Actions → el workflow → Run workflow* lanza una pasada cuando haga falta, desde la web, con `gh workflow run <archivo>` o por API.
* Un repo público tiene minutos de Actions ilimitados; si pasara a privado, el consumo teórico (34 pasadas/día de NAV) pasaría a importar y el real sería menor.

### Disparador externo (receta, NO implementada: decisión pendiente del dueño)

Si algún día hace falta frescura de verdad, la opción es **no confiar en `schedule`**: un cron **fuera de GitHub** (un `crontab` en un servidor propio, un cron de Cloudflare Workers, cron-job.org...) que llame a la
API de GitHub para lanzar el `workflow_dispatch` que ya existe. **Esto no está implementado ni activado en este repo**; es una receta para decidir:

```bash
# cada 30 min en el planificador externo (ejemplo; <usuario>/<repo> y el token son placeholders)
curl -fsS -X POST \
  -H "Accept: application/vnd.github+json" \
  -H "Authorization: Bearer $GITHUB_DISPATCH_TOKEN" \
  -H "X-GitHub-Api-Version: 2022-11-28" \
  https://api.github.com/repos/<usuario>/<repo>/actions/workflows/personal-nav.yml/dispatches \
  -d '{"ref":"main"}'          # responde 204 sin cuerpo
```

* **Token de permisos mínimos**: un *fine-grained personal access token* limitado a **este único repositorio**, con solo el permiso de repositorio **Actions: Read and write** (y ninguno más), con caducidad corta
  (p. ej. 90 días) y guardado **solo en el planificador externo** (nunca en este repo). Contrasta los nombres exactos de permisos con la documentación vigente de GitHub antes de crearlo.
* El `workflow_dispatch` salta la condición `FEED_V2_ENABLED` (solo gobierna el cron) y respeta los grupos de `concurrency` (si hay una pasada en curso, GitHub deja una pendiente y descarta el resto).
* Coste y riesgos: una dependencia más fuera del repo (su propio fallo y su propia caducidad de token), y más pasadas de NAV contra iShares (el README ya avisa del riesgo de términos de uso y de bloqueo por IP compartida).
  Si el token se filtra, el daño se limita a lanzar workflows de este repo; rótalo (runbook).
* `repository_dispatch` no está habilitado en ningún workflow; haría falta añadirlo si se prefiere ese evento.

## Runbook: qué hacer cuando algo falla

Pasos de Supabase y de los ajustes del repo **sin verificar contra el SQL ni contra la configuración real** (no están en el repo): comprueba cada uno donde corresponda.

**Cómo saber que algo falla.** Healthchecks (si están configurados: `HC_UPDATE_URL`, `HC_UPDATE_V2_URL`, `HC_SUPABASE_URL`, `HEALTHCHECK_PERSONAL_NAV_URL`), la pestaña *Actions* (rojo) y `health.json` del feed v2 (`problems.failed`,
`problems.suspect`, `problems.dropped_bars`: barras descartadas por cierre ≤ 0, picos aislados o precio fuera de rango).

**Token `PERSONAL_FEED_TOKEN` rotado o filtrado.**
1. Genera uno aleatorio de ≥ 32 caracteres imprimibles (el backend acepta 16-200, sin espacios).
2. Guarda su **hash** en Supabase como lo comprueban las RPC (comparación en tiempo constante).
3. Actualiza el secreto de Actions `PERSONAL_FEED_TOKEN`.
4. Lanza «Personal NAV» a mano y comprueba «… 0 fallidos» en el resumen del paso *Publish personal NAV*.
5. Invalida el token viejo en Supabase.

**Clave publicable de Supabase filtrada o rotada** (también el paso pendiente de AU-02). Rótala en el panel de Supabase; actualiza el secreto `SUPABASE_PUBLISHABLE_KEY` (y `SUPABASE_URL` si la creas como secreto; ver abajo);
relanza «Supabase keepalive» (debe terminar con HTTP 200/401/403) y «Personal NAV». Mientras existan **a la vez** el secreto y la variable del mismo nombre, manda el secreto: borra la variable para que no quede en claro.

**Pages caído o sitio corrupto.** Un fallo de Pages no corrompe el último sitio desplegado: el run falla en «Download previous site» y el latido manda `/fail`. Si el sitio quedó mal publicado: *Publish feed v2 → Run workflow* con `allow_regression`.
Si Pages se reinició y devuelve 404 en `manifest.json`:
* con la variable de repo **`FEED_V2_INITIALIZED = true`** el run **falla a propósito** («no es un primer run»): arregla Pages (Settings → Pages → Source: GitHub Actions) y relanza; no se publica a ciegas;
* **sin** esa variable el 404 se trata como primer run (solo reglas absolutas y cupo de ~80 altas por run): lánzalo 2-3 veces hasta que `health.json.counts.published` iguale a los símbolos de Yahoo del registro.
La variable la pone el dueño **cuando confirma que el sitio está completo**; si alguna vez hay que empezar de cero a propósito, bórrala o ponla a `false` antes.

**Workflow desactivado por GitHub (60 días sin actividad en un repo público) o a mano.** *Actions → el workflow → Enable workflow*; por API: `gh api -X PUT repos/<usuario>/<repo>/actions/workflows/<archivo>.yml/enable`.
`keepalive.yml` solo reactiva `update-data.yml`; comprueba a mano los demás (`publish-feed-v2.yml`, `personal-nav.yml`, `personal-full.yml`, `supabase-keepalive.yml`). Un commit a `main` reinicia el contador. Comprueba que la variable
`FEED_V2_ENABLED` sigue en `true` (sin ella el cron no hace nada).

**Healthchecks sin avisos.** El secreto debe empezar por `https://hc-ping.com/` (los workflows lo limpian y avisan con `::warning::` sin imprimir el valor). Para `personal-nav`, cualquier `https://` válido sirve.

**El NAV no se actualiza / cambió el HTML de iShares.** Mira el resumen del paso *Publish personal NAV* (fecha del NAV más reciente) y el latido (`/fail` si pasa de 6 días). Lanza «NAV probe (diagnóstico)» a mano; si el patrón cambió,
actualiza `parse_ishares_nav` / `scan_fund_facts` en `scripts/fetch_market_data.py` con una ficha nueva y un fixture nuevo (**con ISIN sintéticos**: el test de privacidad falla si pegas uno real).

**Un símbolo no se publica (`suspect`).** `health.json.problems.dropped_bars` dice qué barras se descartaron y por qué (`close_le_0`, `spike`, `price_range`…); más de 3 barras malas, o un hueco de más de un año dentro de
la serie, dejan el símbolo sin publicar (conserva lo anterior como `stale`). Si el dato de Yahoo es el correcto (un crash real o un split), no es un pico: no hace falta tocar nada.

**Rotación de claves (calendario sugerido: al menos una vez al año y siempre que haya sospecha).** `FRED_API_KEY` (nueva en FRED → secreto), URLs de Healthchecks (nuevo check → secreto), `PERSONAL_FEED_TOKEN` y la clave
publicable de Supabase (arriba) y, si algún día se usa el disparador externo, su token de GitHub (caducidad corta). Los secretos no se vuelven a leer: se sustituyen.

**Tras recrear el repo (BE-09).**
1. Crea el repo nuevo con un único commit del árbol ya saneado (`git grep` de los ISIN y fichas del dueño sin coincidencias).
2. *Settings → Pages → Source: GitHub Actions* y revisa el entorno `github-pages` (ramas permitidas).
3. Secretos: `FRED_API_KEY`, `HC_UPDATE_URL`, `HC_UPDATE_V2_URL`, `HC_SUPABASE_URL`, `PERSONAL_FEED_TOKEN`, `SUPABASE_URL`, `SUPABASE_PUBLISHABLE_KEY` y, si se usa, `HEALTHCHECK_PERSONAL_NAV_URL`. Variables: `FEED_V2_ENABLED`
   (y `FEED_V2_BASE_URL` / `PERSONAL_NAV_FALLBACK_FULL` si se usaban). **No pongas `FEED_V2_INITIALIZED` todavía.**
4. Activa Secret scanning + Push protection + Dependabot y el ruleset de `main` (sin exigir PR en `main` si sigues usando `keepalive.yml`).
5. Lanza «Publish feed v2» 2-3 veces hasta que el sitio esté completo; **entonces** pon `FEED_V2_INITIALIZED = true`.
6. Actualiza en el dashboard la URL base (cambia con el repo) y, si procede, rota el token y la clave publicable (el repo viejo y sus logs llevan semanas públicos).

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
  * pestaña *Secrets* (recomendado) o, si aún no existen, *Variables*: `SUPABASE_URL` (p. ej. `https://<ref>.supabase.co`) y `SUPABASE_PUBLISHABLE_KEY`
    (`sb_publishable_...`; es pública por diseño, pero como *variable* sale en claro en el log; **nunca** pongas aquí la `sb_secret_` ni la `service_role`). Si existen las dos, manda el secreto;
  * pestaña *Secrets* (opcional): `HC_SUPABASE_URL`, URL de ping de un check propio de Healthchecks.io (periodo 3 días
    con gracia de al menos 1 día). Si falta, está vacío o no tiene la forma `https://hc-ping.com/...` (se limpian antes espacios, saltos de línea y comillas envolventes), el workflow solo emite un `::warning::` (sin el valor) y sigue sin avisar.

  **Cómo comprobarlo**: Actions -> "Supabase keepalive" -> *Run workflow*; el job debe terminar en verde con
  «Supabase responde (HTTP 200|401|403)». Prueba negativa: pon una URL inventada en `SUPABASE_URL`, relanza y debe
  fallar con `HTTP 000`. Localmente: `curl -s -o /dev/null -w '%{http_code}\n' -H "apikey: $KEY" "$URL/rest/v1/profiles?select=user_id&limit=1"`.
* `ci.yml` (en cada PR y en cada push a `main`; también a mano): sintaxis de Python, `ruff --select F`, `pytest` (sanity-check, scrape de iShares, FRED,
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
3. Secretos (Settings -> Secrets and variables -> Actions; para los de Supabase y el token de datos propios, ver «Datos propios del dueño»): `FRED_API_KEY` (opcional, gratis en
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
FRED. Una acción solo tiene precio automático en el dashboard si su ticker está en `STOCKS`. Esas listas son **públicas**: no pongas nada tuyo (los fondos y
símbolos propios van a Supabase). Quitar símbolos hace saltar las reglas de regresión del sanity-check: lanza el workflow una vez con `allow_regression`.
