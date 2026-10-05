# Feed de datos de mercado para My Portfolio Dashboard

Este repo hace una sola cosa: un workflow de GitHub Actions ejecuta un script de Python 4 veces al día, descarga
datos de mercado **públicos** (índices, acciones, fondos, materias primas, divisas, mapa de países, noticias, IPC
de España y, opcionalmente, tipos de EE. UU. de FRED) y los publica como `data.json` (+ un `meta.json` diminuto)
en una rama dedicada **`data`**. El dashboard los lee a través de jsDelivr:

```
https://cdn.jsdelivr.net/gh/<usuario>/<repo>@data/meta.json   (unos bytes, se consulta cada pocos minutos)
https://cdn.jsdelivr.net/gh/<usuario>/<repo>@data/data.json   (~10 MB, ~2 MB comprimido)
```

Aquí solo viven precios genéricos que cualquiera podría consultar. El dashboard, las posiciones, los saldos y las
copias de seguridad **no** forman parte de este repo (`.gitignore` bloquea `*.html` y `*backup*.json`).
Las copias de seguridad y los informes (mensual, anual, fiscal) los guarda el propio dashboard en las carpetas
locales que se configuran en Ajustes; no hay ningún job en la nube que los genere.

## Cómo funciona `update-data.yml` (3 jobs)

| Job | Permisos | Qué hace |
|---|---|---|
| `fetch` | solo lectura | instala dependencias, ejecuta el script, valida el resultado (sanity-check) y sube `data.json`/`meta.json` como artifact |
| `publish` | `contents: write` | solo si `fetch` fue bien: sustituye la rama `data` por **un único commit nuevo** (force-push) y purga/verifica jsDelivr |
| `heartbeat` | ninguno | avisa a Healthchecks.io (secreto `HC_UPDATE_URL`) de si todo fue bien; `/fail` si algo falló, y si no llega el aviso a tiempo, Healthchecks te escribe |

Las ejecuciones se serializan (`concurrency`) y tienen límite de tiempo. Si la comprobación de sanidad falla, no se
publica nada y el dashboard sigue sirviendo el último dato bueno.

## Por qué una rama `data` aparte

`data.json` pesa ~10 MB. Commitearlo en `main` cuatro veces al día añadiría ~0,5 GB de historial al año para datos
cuyo historial no le importa a nadie. La rama `data` se reemplaza con un commit nuevo en cada ejecución, así que el
repo se queda en ~2 MB para siempre. `main` solo lleva código.

## Otros workflows

* `keepalive.yml` (semanal): reactiva el workflow programado para que GitHub no lo desactive por inactividad.
* `ci.yml` (en cada PR): sintaxis de Python, resolución de dependencias y comprobación de que no hay HTML, copias
  ni `.env` versionados.
* `dependabot.yml`: PRs mensuales para actualizar las acciones (fijadas por SHA) y las dependencias de Python.
  Revísalos antes de aceptarlos.

## Configuración inicial

1. Repo **público** (necesario para jsDelivr; minutos de Actions ilimitados).
2. Archivos en `main`: `scripts/fetch_market_data.py`, `requirements.txt`, `.github/workflows/*.yml`,
   `.github/dependabot.yml`, `.gitignore`, este README.
3. Secretos (Settings -> Secrets and variables -> Actions): `FRED_API_KEY` (opcional, gratis en
   <https://fred.stlouisfed.org/docs/api/api_key.html>; sin ella no hay tipos ni IPC) y `HC_UPDATE_URL`
   (URL de ping de Healthchecks.io).
4. Actions -> "Update market data" -> **Run workflow**. La primera ejecución crea la rama `data`.
5. Settings -> Code security: Secret scanning + Push protection + Dependabot alerts.
6. Settings -> Rules: ruleset sobre `main` con "Restrict deletions" y "Block force pushes". **No** proteger la rama
   `data`: el workflow la sobrescribe a propósito.

## Ejecutarlo en local

```bash
pip install -r requirements.txt
export FRED_API_KEY=tu_clave   # opcional
python scripts/fetch_market_data.py   # escribe data.json + meta.json
```

## Cambiar lo que se sigue

Edita las listas de arriba de `scripts/fetch_market_data.py` (`INDICES`, `STOCKS`, `COMMODITIES`, `FOREX`,
`COUNTRY_HEATMAP`, `RATES`): listas de tuplas `(símbolo, nombre)` con tickers de Yahoo Finance o IDs de series de
FRED. Una acción solo tiene precio automático en el dashboard si su ticker está en `STOCKS`.
