# Feed de datos de mercado para My Portfolio Dashboard

Este repo hace una sola cosa: un workflow de GitHub Actions ejecuta un script de Python 4 veces al día, descarga
datos de mercado **públicos** (índices, acciones, fondos, materias primas, divisas, mapa de países, noticias, IPC
de España y, opcionalmente, tipos de EE. UU. de FRED) y los publica como `data.json` (+ un `meta.json` diminuto)
en una rama dedicada **`data`**. El dashboard los lee a través de jsDelivr:

```
https://cdn.jsdelivr.net/gh/<usuario>/<repo>@data/meta.json   (unos bytes, se consulta cada pocos minutos)
https://cdn.jsdelivr.net/gh/<usuario>/<repo>@data/data.json   (~10 MB, ~2 MB comprimido)
```

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
* También emite `::warning::` si algún fondo de `your_funds` no viene de `ishares_nav_scrape`, si su NAV tiene más de
  5 días (`nav_stale`) o si hay datos de FRED reutilizados.

### Si FRED falla

Con `FRED_API_KEY` definida, una caída de FRED o una clave caducada ya no bloquea los precios de bolsa: el script
reutiliza `rates`, `cpi` y `cpi_meta` del feed anterior marcados `stale: true` (con `stale_since`) y se publica con un
aviso. Sin feed anterior no hay nada que reutilizar y se exige lo mínimo (tipos e IPC), como antes. El dashboard no
tiene por qué mostrar el campo `stale`: revisa los avisos del run si FRED lleva días caído.

## Por qué una rama `data` aparte

`data.json` pesa ~10 MB. Commitearlo en `main` cuatro veces al día añadiría ~0,5 GB de historial al año para datos
cuyo historial no le importa a nadie. La rama `data` se reemplaza con un commit nuevo en cada ejecución, así que el
repo se queda en ~2 MB para siempre. `main` solo lleva código y, como mucho, un commit mínimo cada ~30 días (keepalive).

## Otros workflows

* `keepalive.yml` (semanal): GitHub desactiva los workflows programados de un repo público tras 60 días sin actividad.
  Si el último commit de la rama por defecto tiene más de 30 días, añade un commit mínimo (una fecha en
  `.github/keepalive`) y además reactiva `update-data.yml` por si estuviera desactivado. Con commits normales o merges
  de Dependabot no hace nada. Que cuente como actividad para GitHub no está confirmado por su documentación:
  Healthchecks.io sigue siendo la red de seguridad.
* `ci.yml` (en cada PR): sintaxis de Python, `ruff --select F`, `pytest` (sanity-check, scrape de iShares, FRED,
  noticias y los bloques `run:` de los workflows), `actionlint` si está disponible y comprobación de que no hay ficheros
  versionados que `.gitignore` excluye (HTML, copias, exports, `.env`, claves...).
* `dependabot.yml`: PRs mensuales para actualizar las acciones (fijadas por SHA) y las dependencias de Python, con un
  `cooldown` de 7 días para no adoptar versiones recién publicadas. Revísalos antes de aceptarlos.

## Dependencias

`requirements.txt` fija una cota superior (`yfinance>=1.7.0,<2`...) para que un salto de versión mayor no entre solo en
un run programado. **No hay lock con hashes**: las versiones menores y parches de `yfinance`, `requests`, `feedparser`
y sus transitivas se instalan flotantes en cada run del job `fetch`, que corre con `FRED_API_KEY` en el entorno. Es un
riesgo de cadena de suministro asumido; si se quiere cerrar, genera un `requirements.lock` con
`pip-compile --generate-hashes` e instálalo con `pip install --require-hashes`.

## Configuración inicial

1. Repo **público** (necesario para jsDelivr; minutos de Actions ilimitados).
2. Archivos en `main`: `scripts/fetch_market_data.py`, `scripts/sanity_check.py`, `tests/`, `requirements.txt`,
   `.github/workflows/*.yml`, `.github/dependabot.yml`, `.gitignore`, este README.
3. Secretos (Settings -> Secrets and variables -> Actions): `FRED_API_KEY` (opcional, gratis en
   <https://fred.stlouisfed.org/docs/api/api_key.html>; sin ella no hay tipos ni IPC) y `HC_UPDATE_URL`
   (URL de ping de Healthchecks.io). En Healthchecks usa el horario cron `17 6,11,15,20 * * *` con una gracia de al
   menos 1 hora (los huecos entre runs llegan a 10 h, un periodo fijo de 6 h daría falsas alarmas).
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

pip install pytest ruff
ruff check --select F scripts tests
python -m pytest -q
```

## Cambiar lo que se sigue

Edita las listas de arriba de `scripts/fetch_market_data.py` (`INDICES`, `STOCKS`, `COMMODITIES`, `FOREX`,
`COUNTRY_HEATMAP`, `RATES`): listas de tuplas `(símbolo, nombre)` con tickers de Yahoo Finance o IDs de series de
FRED. Una acción solo tiene precio automático en el dashboard si su ticker está en `STOCKS`. Recuerda que `YOUR_FUNDS`
y `CRYPTO_WATCHLIST` acaban en el feed público (ver arriba), y que quitar símbolos hace saltar las reglas de regresión
del sanity-check: lanza el workflow una vez con `allow_regression`.
