# Contrato del feed v2 (schema 2)

Qué espera el dashboard (u otro cliente) del feed **genérico** v2. Los ejemplos JSON de este documento los **genera** `tests/test_contract_docs_v2.py`
con el sitio sintético de los tests (datos inventados, listas recortadas a 2-3 elementos); si cambia el contrato, el test falla hasta que se regeneran
(`UPDATE_CONTRACT_DOCS=1 pytest tests/test_contract_docs_v2.py`). El validador `scripts/sanity_check_v2.py` es la definición ejecutable: lo que no está
aquí ni allí no se publica.

> El feed **v1** (`data.json` en la rama `data` vía jsDelivr) **no cambia** mientras exista: ni nombres de campo ni rutas. v2 convive con él y el corte
> lo decide el dueño.

## 1. Dónde está

GitHub Pages del repo, bajo `/v2/`:

```
https://<usuario>.github.io/<repo>/v2/index.json
https://<usuario>.github.io/<repo>/v2/s/<SYM>/latest.json       precio, divisa, unidad, as_of, cambios, sparkline        (~0,6 KB)
https://<usuario>.github.io/<repo>/v2/s/<SYM>/d/<AAAA>.json     diario del año; los años cerrados NO se reescriben        (~16 KB/año)
https://<usuario>.github.io/<repo>/v2/s/<SYM>/i.json           intradía de 5 min (solo símbolos de nivel hot)
https://<usuario>.github.io/<repo>/v2/crypto/latest.json        top 250 de CoinGecko
https://<usuario>.github.io/<repo>/v2/macro.json                FX, tipos, IPC, VIX, Fear & Greed, calendario
https://<usuario>.github.io/<repo>/v2/news.json                 titulares (campo `watchlist`, no `positions`)
https://<usuario>.github.io/<repo>/v2/health.json               salud del run y estado de cada símbolo
https://<usuario>.github.io/<repo>/v2/manifest.json             ruta -> bytes + sha256 de todo lo anterior (formato de manifiesto 1)
https://<usuario>.github.io/<repo>/v2/manifest-root.json        ficheros globales + huella del trozo de cada símbolo (formato 2, desde MANIFEST_ESCALA)
https://<usuario>.github.io/<repo>/v2/s/<SYM>/manifest.json     trozo del manifiesto: los ficheros de ese símbolo (formato 2)
```

Con dominio propio, la URL base es la variable de repo `FEED_V2_BASE_URL`. Pages sirve con `Cache-Control: max-age=600`: no se puede forzar la caché
desde aquí, así que el cliente decide cada cuánto mira `index.json` o `manifest.json` (cuyo `generated_at` dice si hay algo nuevo) y no debe tratar un
desfase de unos minutos como un fallo.

**Frescura real (no prometas más de lo que hay).** Los `cron` de GitHub Actions no son un reloj: GitHub los retrasa horas y se salta alguno. Hasta AU01-F1 lo programado era «4 veces al día» para el feed y «cada 30 min» para el NAV, pero lo
observado (auditoría del 2-nov-2026, una semana de muestra) fue ~3 runs al día del feed, con un retraso mediano de ~3 h 20 min y huecos de hasta 13 h 20 min entre dos runs, y unas pocas pasadas al día del NAV. **Un cliente no debe
considerar «viejo» el feed antes de unas 14 horas** sin que cambie `generated_at`, ni anunciar un NAV «cada 30 min». Todos los workflows se pueden lanzar a mano (`workflow_dispatch`). Desde AU01-F1 el reloj principal es un disparador externo (fuera de este repo) que lanza
`workflow_dispatch` cada ~60 min en sesión, con el cron de GitHub como respaldo (2-4 franjas al día), una puerta de frescura de 40 min en el feed y parada temprana en el NAV; su puntualidad real **no está medida**
todavía, así que el umbral de ~14 h sigue vigente. Detalle en el README, «Cadencia real de GitHub Actions».

## 2. Convenciones

* **Símbolo** (`s`, `symbol`): `^[A-Za-z0-9.^=\-]{1,15}$`, sin punto inicial ni `..`. **Codifícalo siempre en la URL** (`encodeURIComponent`): `^GSPC` →
  `%5EGSPC`, `EURUSD=X` → `EURUSD%3DX`. Nunca lo interpoles sin validar.
* **Divisa y unidad explícitas** (BE-01): toda serie lleva `currency` (tal cual la cotiza el mercado, **distingue mayúsculas**: `GBp` = peniques,
  `GBP` = libras) y `unit`:

  | `unit` | Qué es el número | Ejemplo |
  |---|---|---|
  | `currency` | importe en `currency` por acción/participación/contrato | `AAPL` en `USD` |
  | `subunit` | importe en la **subunidad** (peniques, centavos). Divide entre `price_unit_divisor` (100) para tener la divisa mayor | `SWDA.L` en `GBp`: 10882 = 108,82 £ |
  | `points` | nivel de un índice (no es un importe) | `^GSPC` |
  | `rate` | tipo de cambio: unidades de `currency` por unidad de la divisa base del par | `USDJPY=X` en `JPY` |

  Un símbolo cuya divisa o escala real no coincide con la del registro **no se publica** (se ve en `health.json.problems.unit_mismatch`: `{símbolo: {registro, yahoo}}`, ver abajo). Un cliente
  que multiplique cantidad × precio debe usar `price_unit_divisor`; si falta o no entiende la unidad, no debe valorar.
* **Sin precios <= 0**: ninguna serie diaria lleva `o`, `h`, `l` ni `c` no finitos o <= 0 (el WTI, `CL=F`, cerró en negativo el 2020-04-20). El generador
  **descarta la barra solo si su cierre** es no finito o <= 0 (la serie tiene un hueco en esa fecha) y la **anota** en `health.json` con su motivo:
  `problems.dropped_bars` (`{símbolo: {n, dates, reasons}}`, acumulado de todo el histórico; `reasons` = `{fecha: motivo}` de las `dates` listadas) y
  `symbols.<SYM>.dropped` / `symbols.<SYM>.dropped_why`. Motivos (clase cerrada): `close_missing` (cierre vacío o ausente), `close_not_finite` (infinito o no numérico),
  `close_le_0` (cero o negativo), `spike` (pico aislado, ver abajo), `price_range` (cierre fuera del rango plausible de su `unit`) y `unknown` (anotada por una versión anterior que no guardaba el motivo). Una barra con `open`/`high`/`low` no finito o <= 0 **pero cierre
  válido** (el 2020-04-21 el WTI cerró en +10,01 $ con mínimo de -40,32) **no se descarta**: ese campo se sustituye por el cierre y el rango `l..h` se recalcula con los
  valores válidos. Si un símbolo pierde **más de 3 barras o más del 1 %** de las suyas, sus datos se consideran sospechosos y **no se publica** (`problems.suspect`,
  `counts.suspect`; conserva lo anterior marcado `stale`). Un cliente no debe interpolar el hueco ni esperar una barra en cada día hábil.
* **Picos aislados, rango de precio y huecos largos** (AU-06): un cierre que sube más de un 50 % o baja más de un 50 % respecto a sus dos barras vecinas, que se parecen entre sí (la serie vuelve al nivel de antes), es un tick roto y se
  trata como una barra con cierre <= 0: **se descarta** y se anota con el motivo `spike` (cuenta para el tope de arriba). Un crash real que se mantiene, un split o una tendencia **no** son picos. Un cierre fuera del rango plausible de su
  `unit` (`currency` 1e-4..1e7, `subunit` 1e-2..1e8, `points` 1e-2..1e7, `rate` 1e-5..1e6) se descarta con `price_range`. Un hueco de más de 370 días dentro de la serie deja el símbolo sin publicar (`suspect`). El validador del job
  `publish` rechaza además cierres, `price` y `sparkline` fuera del rango de su `unit`, y trata un pico aislado en un fichero diario nuevo o cambiado como regresión (aviso con `allow_regression`).
* **Fechas**: `YYYY-MM-DD` (día de la barra, hora local del mercado); marcas de generación `YYYY-MM-DDTHH:MM:SSZ` (UTC). El intradía lleva `YYYY-MM-DD HH:MM`
  en la hora local del mercado y `tz` (IANA) en el fichero.
* **`stale` / `stale_since`**: el símbolo falló en el último run y se sirve lo anterior (como máximo 30 días), o su última barra tiene más de 7 días. Van juntos.
* **Estado** (`status` en `index.json`): `active`, `dormant` (el dueño del registro lo pausó, **o** una cripto del registro que no aparece en el lote de CoinGecko de este run: `health.json.symbols.<SYM>.reason` = `not_listed`; vuelve a `active` sola cuando reaparece), `invalid` (5 fallos seguidos; se reintenta una vez por semana) y
  `delisted` (congelado >45 días). **Un símbolo muerto no bloquea la publicación**: si no hay `latest.json`, `as_of` es `null` y el cliente lo muestra sin precio.
* **Textos**: sin `<` ni `>` ni caracteres de control (los garantiza el validador); aun así, pinta con `textContent`, nunca con `innerHTML`.
* **Números**: finitos; `null` donde el esquema lo permite. Los cierres son `> 0`.

## 3. Ficheros

### `index.json`
Un elemento por símbolo del registro público (`registry.json`), en su orden. `kind` es `yahoo` (ficheros propios en `s/<SYM>/`) o `crypto` (las mayores monedas;
sus datos están en `crypto/latest.json`).

<!-- ejemplo:index -->
```json
{
  "schema": 2,
  "generated_at": "2026-10-26T18:00:00Z",
  "symbols": [
    {
      "s": "^GSPC",
      "kind": "yahoo",
      "name": "S&P 500",
      "currency": "USD",
      "unit": "points",
      "tier": "hot",
      "as_of": "2026-10-27",
      "status": "active"
    },
    {
      "s": "AAPL",
      "kind": "yahoo",
      "name": "Apple",
      "currency": "USD",
      "unit": "currency",
      "tier": "hot",
      "as_of": "2026-10-27",
      "status": "active"
    },
    {
      "s": "SWDA.L",
      "kind": "yahoo",
      "name": "iShares Core MSCI World UCITS ETF",
      "currency": "GBp",
      "unit": "subunit",
      "tier": "hot",
      "as_of": "2026-10-27",
      "status": "active"
    }
  ]
}
```
<!-- /ejemplo -->

### `s/<SYM>/latest.json`
Precio y cambios. `change_pct_7d` y `volume_ratio` pueden ser `null`. `sparkline` son los últimos ≤30 cierres (el último es `price`).

<!-- ejemplo:latest -->
```json
{
  "schema": 2,
  "s": "SWDA.L",
  "kind": "yahoo",
  "name": "iShares Core MSCI World UCITS ETF",
  "currency": "GBp",
  "unit": "subunit",
  "price_unit_divisor": 100,
  "price": 215.173239,
  "change_pct": -0.03,
  "change_pct_7d": -0.36,
  "volume_ratio": 1.0,
  "sparkline": [
    215.787689,
    215.642347,
    215.514067,
    215.232465,
    215.173239
  ],
  "as_of": "2026-10-27",
  "generated_at": "2026-10-26T18:00:00Z"
}
```
<!-- /ejemplo -->

### `s/<SYM>/d/<AAAA>.json`
Diario del año natural, en columnas paralelas (`d` fechas ISO estrictamente crecientes; `o,h,l,c` precios con `low ≤ open,close ≤ high`; `v` volumen entero). Se conservan
10 años naturales. Un año cerrado nunca se reescribe (salvo el anterior en enero-febrero, mientras se asientan las últimas barras, o si el símbolo se rebasa por un split).
Los precios son **sin ajustar por dividendos** (`Close` de Yahoo ajustado solo por splits), para que el histórico incremental sea coherente.

<!-- ejemplo:daily -->
```json
{
  "schema": 2,
  "s": "SWDA.L",
  "y": 2026,
  "currency": "GBp",
  "unit": "subunit",
  "d": [
    "2026-01-01",
    "2026-01-02",
    "2026-01-05"
  ],
  "o": [
    245.693893,
    245.076526,
    243.19752
  ],
  "h": [
    248.648128,
    248.023338,
    246.121739
  ],
  "l": [
    243.724402,
    243.111984,
    241.248041
  ],
  "c": [
    246.186265,
    245.567661,
    243.68489
  ],
  "v": [
    1000617,
    1000618,
    1000621
  ]
}
```
<!-- /ejemplo -->

### `s/<SYM>/i.json` (solo nivel hot)
Barras de 5 minutos de las últimas sesiones, con preapertura/postcierre si el mercado las tiene. Se elimina si el símbolo falla (no se arrastra el de ayer).

<!-- ejemplo:intraday -->
```json
{
  "schema": 2,
  "s": "AAPL",
  "currency": "USD",
  "unit": "currency",
  "t": [
    "2026-10-23 09:30",
    "2026-10-23 09:35",
    "2026-10-23 09:40"
  ],
  "c": [
    100.0,
    101.0,
    102.0
  ],
  "tz": "America/New_York"
}
```
<!-- /ejemplo -->

### `crypto/latest.json`
Top 250 de CoinGecko por capitalización, en USD, en **una** llamada (lotes de ≤250). Si CoinGecko falla se reutiliza el anterior con `stale: true` (máx. 7 días).

<!-- ejemplo:crypto -->
```json
{
  "schema": 2,
  "generated_at": "2026-10-26T18:00:00Z",
  "currency": "USD",
  "unit": "currency",
  "count": 30,
  "coins": [
    {
      "id": "bitcoin",
      "symbol": "BTC",
      "name": "Bitcoin",
      "rank": 1,
      "price": 1.5,
      "change_24h_pct": 1.23,
      "change_7d_pct": -2.5,
      "market_cap": 1000000000.0,
      "volume_24h": 5000000.0,
      "as_of": "2026-10-26"
    },
    {
      "id": "ethereum",
      "symbol": "ETH",
      "name": "Ethereum",
      "rank": 2,
      "price": 2.5,
      "change_24h_pct": 1.23,
      "change_7d_pct": -2.5,
      "market_cap": 999999999.0,
      "volume_24h": 5000000.0,
      "as_of": "2026-10-26"
    }
  ]
}
```
<!-- /ejemplo -->

### `macro.json`
`fx_usd`: USD por 1 unidad de cada divisa (sale de los pares del registro). `rates`/`cpi`: FRED (solo con `FRED_API_KEY`; si falla se reutiliza lo anterior con `stale`).
`market_mood`: VIX, Fear & Greed de bolsa y cripto (cada entrada puede llevar `stale`). `calendar`: próximas publicaciones macro de EE. UU. (FRED, mejor esfuerzo; puede estar vacío).
No incluye el índice de altcoins ni el staking de v1.

<!-- ejemplo:macro -->
```json
{
  "schema": 2,
  "generated_at": "2026-10-26T18:00:00Z",
  "fx_usd": {
    "USD": 1.0,
    "EUR": 189.964659,
    "JPY": 0.00353177
  },
  "rates": [
    {
      "series": "DGS10",
      "name": "US 10-Year Treasury",
      "value": 4.1,
      "prev": 4.08,
      "date": "2026-10-23"
    }
  ],
  "cpi": [
    {
      "date": "2026-08-01",
      "value": 121.4
    },
    {
      "date": "2026-09-01",
      "value": 121.9
    }
  ],
  "cpi_meta": {
    "source": "Eurostat HICP (IPCA) Spain, via FRED",
    "series": "CP0000ESM086NEST",
    "last_date": "2026-09-01",
    "stale": false
  },
  "market_mood": {
    "vix": {
      "value": 16.4,
      "change_pct": -1.2
    },
    "vix_50ma": {
      "value": 17.9
    },
    "stock_fear_greed": {
      "value": 61.0,
      "rating": "greed",
      "as_of": "2026-10-26"
    },
    "crypto_fear_greed": {
      "value": 54,
      "classification": "Neutral",
      "timestamp": 1792972800
    }
  },
  "calendar": [
    {
      "date": "2026-11-12",
      "event": "US CPI"
    },
    {
      "date": "2026-12-16",
      "event": "FOMC decision"
    }
  ]
}
```
<!-- /ejemplo -->

### `news.json`
`general`: titulares de mercado. `watchlist`: titulares de los símbolos hot que son acciones/ETF (2 por símbolo) y de las mayores criptomonedas del registro. Es el equivalente de
`news.positions` de v1 (renombrado: aquí no hay posiciones de nadie).

<!-- ejemplo:news -->
```json
{
  "schema": 2,
  "generated_at": "2026-10-26T18:00:00Z",
  "general": [
    {
      "title": "Titular de ejemplo",
      "link": "https://example.com/noticia",
      "source": "Fuente",
      "published": "Mon, 26 Oct 2026 10:00:00 GMT"
    }
  ],
  "watchlist": [
    {
      "title": "Titular de ejemplo",
      "link": "https://example.com/noticia",
      "source": "Fuente",
      "published": "Mon, 26 Oct 2026 10:00:00 GMT",
      "symbol": "AAPL",
      "asset_name": "Apple"
    }
  ]
}
```
<!-- /ejemplo -->

### `health.json`
Contadores del run (`yahoo_requests` = peticiones reales a Yahoo incluidos reintentos; `yahoo_calls` = llamadas de la aplicación), problemas y el **estado de cada símbolo**
(`symbols`), que el run siguiente lee para decidir qué toca refrescar. Un cliente puede mirar `counts.published` y `problems` para avisar de datos degradados.

`problems` solo lleva datos estructurados, **nunca texto libre ni mensajes de excepciones**:

| Campo | Forma | Contenido |
|---|---|---|
| `unit_mismatch` | `{símbolo: {registro, yahoo}}` | `registro` = `"<divisa>/<unidad>"` declarado (`"GBp/subunit"`); `yahoo` = la divisa que informó Yahoo (3-4 letras, `"GBP"`) o `null` si no la informó o no tiene ese formato |
| `failed` | `{símbolo: motivo}` | motivo de la clase cerrada `empty` (sin datos suficientes o congelado), `http_error` (Yahoo falló), `timeout`, `parse` (datos o error interno al procesarlos), `unit` (ver `unit_mismatch`) o `suspect` (demasiadas barras descartadas) |
| `dropped_bars` | `{símbolo: {n, dates, reasons}}` | ver «Sin precios <= 0» |
| `rebased`, `deferred`, `frozen`, `suspect` | `[símbolo]` | listas (máx. 40) |

Una cripto del registro que no aparece en el lote de CoinGecko figura en `symbols` como `{"status": "dormant", "reason": "not_listed", ...}`; no bloquea nada.
Qué se avisa a Healthchecks lo decide el validador, no este fichero: un símbolo suelto sin publicar es solo un aviso en el log; se avisa si quedan sin publicar más del 10 % de los
símbolos activos del registro o si Yahoo cae a nivel global.

<!-- ejemplo:health -->
```json
{
  "schema": 2,
  "generated_at": "2026-10-26T18:00:00Z",
  "run": {
    "duration_s": 0.0,
    "yahoo_calls": 10,
    "yahoo_requests": 0,
    "http_requests": {},
    "yahoo_rate_limited": 0,
    "yahoo_abandoned": false,
    "budget_exhausted": false
  },
  "counts": {
    "due": 5,
    "updated": 5,
    "failed": 0,
    "carried_stale": 0,
    "deferred": 0,
    "backfilled": 0,
    "unit_mismatch": 0,
    "crypto": 30,
    "outage": 0,
    "suspect": 0,
    "registry": 9,
    "published": 7,
    "rebased": 0,
    "dropped_bars": 0
  },
  "problems": {
    "unit_mismatch": {},
    "failed": {},
    "rebased": [],
    "deferred": [],
    "frozen": [],
    "suspect": [],
    "dropped_bars": {}
  },
  "symbols": {
    "^GSPC": {
      "fail_streak": 0,
      "status": "active",
      "last_ok": "2026-10-26T18:00:00Z",
      "last_try": "2026-10-26T18:00:00Z",
      "tier": "hot"
    },
    "AAPL": {
      "fail_streak": 0,
      "status": "active",
      "last_ok": "2026-10-26T18:00:00Z",
      "last_try": "2026-10-26T18:00:00Z",
      "tier": "hot"
    }
  }
}
```
<!-- /ejemplo -->

### `manifest.json` (formato de manifiesto 1)
Ruta → bytes y sha256 de **todos** los ficheros de datos (menos él mismo y menos los manifiestos de formato 2, que no son datos). El run siguiente verifica con él cada descarga del sitio
anterior, y el validador comprueba que coincide con el disco. Un cliente que quiera integridad puede verificar lo que descarga. **No cambia** con el formato 2: mismas claves y
mismas entradas que antes de MANIFEST_ESCALA (los ficheros listados son los mismos; `total_bytes` mide lo mismo).

**Crece con el universo** (~1,3 KB por símbolo de Yahoo, medido con el sitio sintético; el 89 % son las entradas de los años diarios y la mitad de cada entrada es el
`sha256`). Su tope es `MAX_BYTES["manifest"]` = 600 KB: con el reparto actual se llega hacia los **~460 símbolos de Yahoo** y, por encima, no solo lo rechaza el
validador: `site_download.py` (job `fetch`) se niega a bajarlo y el workflow no puede ni empezar. El validador **avisa** (`::warning::`) desde el 50 % de este y de
todos los topes. Mientras haga falta (el cliente del dueño lo lee) se sigue publicando completo; un cliente nuevo debe usar el formato 2.

<!-- ejemplo:manifest -->
```json
{
  "schema": 2,
  "generated_at": "2026-10-26T18:00:00Z",
  "total_bytes": 1160565,
  "files": {
    "index.json": {
      "bytes": 1304,
      "sha256": "9f84091ab01c4b47cb881e81a7973d58588c880fe916f693b4b82b9fdf2ea37f"
    },
    "s/SWDA.L/latest.json": {
      "bytes": 621,
      "sha256": "965c326af7e5504731eeee52c7de93a8958e9c2c377c3c51ee6f6385ab56676b"
    },
    "s/SWDA.L/d/2026.json": {
      "bytes": 13914,
      "sha256": "a0f889d9468ef67323b3e2d93f9a49a09587eeaa0ba75a4397fca8240c4278d5"
    }
  }
}
```
<!-- /ejemplo -->

### Formato de manifiesto 2: `manifest-root.json` + `s/<SYM>/manifest.json` (aditivo, desde MANIFEST_ESCALA)
Describe **los mismos ficheros de datos** que `manifest.json`, partidos en dos niveles, para que el cliente no tenga que bajar ~1,3 KB por símbolo cada vez que cambia el índice:

* **Raíz** `manifest-root.json`: `manifest_format` = `2`; `generated_at` y `total_bytes` **iguales** a los de `manifest.json` (mismo run, mismos datos); `files` = bytes y sha256
  de los ficheros **globales** (`index.json`, `health.json`, `macro.json`, `news.json`, `crypto/latest.json`); `symbols` = por cada símbolo **con ficheros**, bytes y sha256 de
  **su trozo** (~105 B por símbolo: ~0,3 MB a 3.000 símbolos; tope `MAX_BYTES["manifest_root"]` = 600 KB). Un símbolo sin ficheros (cripto, o de Yahoo aún sin datos) no aparece.
* **Trozo** `s/<SYM>/manifest.json`: `manifest_format` = `2`, `s` y `files` = bytes y sha256 de **todos** los ficheros de ese símbolo (`latest.json`, `d/<AAAA>.json`, `i.json`),
  con la ruta completa desde `/v2/`. **No lleva `generated_at`** a propósito: si los ficheros del símbolo no cambian, el trozo es idéntico byte a byte y su huella en la raíz
  también. Tope `MAX_BYTES["manifest_shard"]` = 4 KB (≤ 16 ficheros).
* Ningún manifiesto (de formato 1 ni 2) aparece en otro manifiesto. El validador comprueba los dos formatos **contra el disco** (no uno contra el otro): un trozo que falta,
  sobra, lista otros ficheros o tiene una huella que no casa **bloquea la publicación** igual que hoy un `manifest.json` incoherente.

<!-- ejemplo:manifest_root -->
```json
{
  "schema": 2,
  "manifest_format": 2,
  "generated_at": "2026-10-26T18:00:00Z",
  "total_bytes": 1160565,
  "files": {
    "index.json": {
      "bytes": 1304,
      "sha256": "9f84091ab01c4b47cb881e81a7973d58588c880fe916f693b4b82b9fdf2ea37f"
    },
    "crypto/latest.json": {
      "bytes": 5587,
      "sha256": "97a6c503b1cbdead223e742cd84d98374c5d836526105bc7bb212e57beff749f"
    }
  },
  "symbols": {
    "AAPL": {
      "bytes": 1400,
      "sha256": "a36498f36d6a18e3939171a64c5bd4deafcfd8650832d96b147965b526d2eee1"
    },
    "SWDA.L": {
      "bytes": 1426,
      "sha256": "bb9f39ae063ceb2f5eb78039a748f48057abdc1c5d92f04444bcb2960277b5ff"
    }
  }
}
```
<!-- /ejemplo -->

<!-- ejemplo:manifest_shard -->
```json
{
  "schema": 2,
  "manifest_format": 2,
  "s": "SWDA.L",
  "files": {
    "s/SWDA.L/latest.json": {
      "bytes": 621,
      "sha256": "965c326af7e5504731eeee52c7de93a8958e9c2c377c3c51ee6f6385ab56676b"
    },
    "s/SWDA.L/d/2026.json": {
      "bytes": 13914,
      "sha256": "a0f889d9468ef67323b3e2d93f9a49a09587eeaa0ba75a4397fca8240c4278d5"
    }
  }
}
```
<!-- /ejemplo -->

**Cómo usarlo (cliente nuevo):** cuando cambie `generated_at` de `index.json`, `GET manifest-root.json`; compara cada `symbols.<SYM>` con el que guardaste la vez anterior y pide
`s/<SYM>/manifest.json` **solo** de los símbolos que sigues y cuya huella cambió (verifica bytes y sha256 contra la raíz); con el trozo, pide y verifica solo los ficheros que
cambiaron. En un run normal cambian los trozos de los símbolos refrescados (los hot en cada run, los warm ~1 vez al día, los cold ~1 vez por semana); los años cerrados no
mueven el trozo. Si `manifest-root.json` da 404 (sitio de antes de MANIFEST_ESCALA), usa `manifest.json`.

**Compatibilidad y retirada:** el formato 2 es **aditivo**: `manifest.json` se sigue publicando igual, con su tope, mientras haga falta. Retirarlo (o dejar de listar en él los años
cerrados) cambia el contrato y exige antes: (1) que el cliente del dueño lea el formato 2; (2) que `site_download.py` (CI) baje el sitio anterior por el formato 2; (3) una nueva
versión de este documento. Hasta entonces el universo de Yahoo no puede pasar de ~460 símbolos (ver `manifest.json`).

## 4. Cómo leerlo (algoritmo del cliente)

1. `GET index.json` (cada pocos minutos; compara `generated_at`). Para cada símbolo que sigues y tiene `status` `active` y `as_of`, `GET s/<SYM>/latest.json`.
2. Para un gráfico: `GET s/<SYM>/d/<AAAA>.json` del año en curso y de los anteriores que necesites (los cerrados son inmutables: caché larga en el cliente, validando contra el
   `sha256` del manifiesto si quieres). Une las columnas por posición.
3. Para el gráfico de 1 día: `s/<SYM>/i.json` si existe.
4. Valora posiciones con `price / price_unit_divisor` **solo** si `unit` es `currency` o `subunit`; nunca mezcles `GBp` con `GBP` sin dividir.
5. Si `stale` es `true`, enséñalo (el dato es anterior); si el símbolo no está en `index.json` o no tiene `latest.json`, enséñalo como «sin datos».
6. Las criptomonedas del top están en `crypto/latest.json` (`kind: crypto` en el índice).
7. Integridad y «qué ha cambiado»: `manifest.json` (formato 1, completo) o, mejor a partir de unos cientos de símbolos, `manifest-root.json` + los trozos que cambiaron (formato 2, §3).

## 5. Datos propios (Supabase, fuera del repo)

Lo que **no** es público —qué fondos sigues por ISIN, su NAV exacto y las series de lo que no está en el universo público— nunca pasa por este repo ni por Pages: vive en
Supabase y lo escribe el backend con dos RPC. El árbol actual del repo no contiene ningún ISIN, id de ficha ni lista del dueño (los ejemplos, los tests y las fixtures usan ISIN y símbolos **sintéticos**; un test lo vigila). El historial de git anterior sí los contiene hasta que se recree el repo (README, «Purga del historial»).

* **Autenticación**: cabecera `apikey` con la clave **publicable** (`sb_publishable_...`) y un token aleatorio (`p_token`, secreto de Actions `PERSONAL_FEED_TOKEN`) en el cuerpo.
  Nunca `service_role` ni `sb_secret_` (el backend aborta si la clave lo parece). Sin cabecera `Authorization`.
* **`personal_watch_for_backend(p_token text)`** devuelve la lista a seguir, un elemento por usuario (también se admite un único objeto). Campos desconocidos se ignoran:

<!-- ejemplo:watch -->
```json
[
  {
    "user_id": "usuario-1",
    "funds": [
      {
        "isin": "IE0FAKE00001",
        "name": "Fondo Falso A",
        "url": "https://www.ishares.com/ch/individual/en/products/000001/fake-fund-a",
        "proxy_symbol": "FAKEP",
        "prev_price": 25.4,
        "prev_nav_date_iso": "2026-10-23"
      }
    ],
    "symbols": [
      "AAPL",
      "FAKE1"
    ]
  }
]
```
<!-- /ejemplo -->

  `funds[].url` debe ser `https://*.ishares.com/...` (cualquier otra URL se descarta y no se pide jamás); `prev_price` (opcional) es el último NAV publicado: un NAV nuevo fuera de
  0,5–2× su valor se considera un error de parseo y no se publica. `prev_nav_date_iso` (opcional, AAAA-MM-DD; AU01-F1) es la `nav_date_iso` del NAV ya guardado de ese fondo: la usa **solo**
  la parada temprana de `personal-nav.yml` (si **todos** los fondos con ficha tienen el NAV del último día hábil esperado, no se visita iShares); si falta, no es una fecha válida o es
  posterior a hoy, se ignora y la ficha se visita como siempre (fallo cerrado). **Orden de despliegue:** primero el backend que la acepta y después el SQL que la envía (un fondo con una
  clave desconocida se descarta entero). `symbols` y `proxy_symbol` siguen la regla de símbolo del §2. Máximos: 20 usuarios, 20 fondos y 40 símbolos por usuario.
* **`publish_personal_feed(p_token text, p_user text, p_payload jsonb)`** recibe, por usuario, este payload (≤ 1 MB; el backend lo recorta si hace falta, ver «Series»). Campos:
  `funds` (NAV y facts por ISIN), `series` (solo cierres diarios `d`/`c`, sin `o`/`h`/`l`/`v`) y `public` (símbolos extra que sí están en el universo público y por eso **no** se repiten).

  **Series** (`series[]`, claves exactas `symbol, currency, unit, price_unit_divisor, price, change_pct, as_of, d, c`):
  * **Proxy de un fondo** (`funds[].proxy_symbol`): **siempre** va en `series`, con todo el histórico diario disponible hasta ~25 años (≤ 6.600 cierres), **esté o no en el universo
    público**. Motivo: el público solo conserva 10 años naturales por símbolo (`HISTORY_YEARS`; §3 `d/<AAAA>.json`), insuficiente para reproducir la analítica de v1 (serie `max`). Un proxy
    nunca aparece en `public`. Es la serie completa y sin ajustar por dividendos (`auto_adjust=False`, igual que el público), escalada tal cual: reescalar al NAV es cosa del cliente.
  * **Símbolo extra** (`symbols[]` de la lista) fuera del universo público: ≤ 2 años de cierres (≤ 600). Si está en el universo público no se incluye y figura en `public`.
  * Las series de proxies van **primero**, en el orden de los fondos; después las de los símbolos extra.
  * `currency` es obligatoria y se publica tal cual (`GBp` ≠ `GBP`); `unit` ∈ `currency|subunit|points|rate` y `price_unit_divisor` coherentes con ella (`subunit` ⇒ divisa en subunidad y
    divisor 100; `currency` con divisa en subunidad **no** es válido; `points` ⇒ símbolo `^…`; `rate` ⇒ símbolo `…=X`). Una serie sin divisa conocida **no se publica**: el fondo conserva su
    `proxy_symbol` pero no habrá serie para él (el cliente debe tolerarlo; el backend lo cuenta como `proxy_sin_divisa_o_datos` / `proxy_fallo` en el log).
  * `d` estrictamente creciente (sin repetidas), `c > 0` y finitos (nada de NaN/Infinity), mismo largo, `d[-1] == as_of` y `c[-1] == price`. Sin huecos de más de 15 días: si la descarga trae uno,
    el backend publica solo el tramo posterior al último hueco.
  * Un símbolo no aparece dos veces en `series` ni a la vez en `series` y en `public`.
  * **Recorte si el payload pasa de 1 MB** (determinista; lo hace `fit_payload`): primero los símbolos extra (260 → 60 → 1 cierres); si no basta, los proxies, todos al mismo nivel
    (2.600 ≈ 10 años → 1.300 ≈ 5 → 780 ≈ 3 → 260 ≈ 1 año → 60 → 1); si ni así cabe, sin series; si sigue sin caber, ese usuario no se publica. Un cliente no debe suponer que un proxy trae 25 años:
    mire `d[0]`.
  * Una descarga del proxy por ejecución (histórico completo, `period=max`); nada incremental (el backend no lee lo publicado antes).

<!-- ejemplo:payload -->
```json
{
  "schema": 2,
  "generated_at": "2026-10-26T12:00:00Z",
  "funds": [
    {
      "isin": "IE0FAKE00001",
      "name": "Fondo Falso A",
      "price": 25.5,
      "currency": "EUR",
      "unit": "currency",
      "source": "ishares_nav_scrape",
      "nav_date": "23/Oct/2026",
      "nav_date_iso": "2026-10-23",
      "nav_stale": false,
      "change_pct": 0.4,
      "wk52_low": 20.0,
      "wk52_high": 30.0,
      "proxy_symbol": "FAKEP",
      "facts": {
        "ongoing_charges_pct": 0.1,
        "domicile": "Ireland"
      }
    }
  ],
  "series": [
    {
      "symbol": "FAKEP",
      "currency": "USD",
      "unit": "currency",
      "price_unit_divisor": 1,
      "price": 100.0,
      "change_pct": -0.5,
      "as_of": "2026-10-23",
      "d": [
        "2026-10-21",
        "2026-10-22",
        "2026-10-23"
      ],
      "c": [
        101.0,
        100.5,
        100.0
      ]
    },
    {
      "symbol": "FAKE1",
      "currency": "USD",
      "unit": "currency",
      "price_unit_divisor": 1,
      "price": 100.0,
      "change_pct": -0.5,
      "as_of": "2026-10-23",
      "d": [
        "2026-10-21",
        "2026-10-22",
        "2026-10-23"
      ],
      "c": [
        101.0,
        100.5,
        100.0
      ]
    }
  ],
  "public": [
    "AAPL"
  ]
}
```
<!-- /ejemplo -->

* **Es opcional**: si Supabase o el token faltan, o algo falla, el run solo emite un `::warning::` (el feed público no depende de esto).
* **Quién publica y con qué cadencia**: dos workflows, dos modos (ninguno es `publish-feed-v2.yml`), cada uno con su propia concurrencia (sin cancelar a medias):
  * **Completo** (`personal-full.yml`, grupo `personal-full`): cron de respaldo 06:38, 13:38 y 21:38 UTC (`38 6,13,21 * * *`), más lo que lance el disparador externo. Llama a `publish_personal_feed`, que **sustituye** el payload del usuario (no hay lectura del
    anterior ni fusión): cada run envía `funds` **y** `series` (si una serie de Yahoo falla en un run, ese run publica sin ella hasta el siguiente). Es el único que **crea** el payload de un usuario nuevo.
  * **Solo NAV** (`personal-nav.yml`, grupo `personal-nav`): disparador externo cada ~60 min en sesión y cron de respaldo L-V 07:23, 11:23 y 16:23 UTC (`23 7,11,16 * * 1-5`), con **parada temprana** si todos los fondos ya tienen el NAV del
    último día hábil esperado (`prev_nav_date_iso` de la lista; ver arriba). Solo visita las fichas de iShares (una petición por ficha, sin Yahoo, sin series) y llama a
    `publish_personal_nav`. Ver «Modo solo NAV» abajo.
  Un cliente debe tratar `generated_at` como «último intento correcto», no como «NAV de hoy».
* **Modo solo NAV — `publish_personal_nav(p_token text, p_user text, p_funds jsonb) returns jsonb`** (contrato fijo; la implementa el SQL, el backend solo lo llama). `personal_v2.py watch|build|publish|all --nav-only`.
  * `p_funds`: array JSON de ≤ 20 fondos, con **solo** estas claves por fondo: `isin`, `name`, `price`, `currency`, `unit`, `source` (`ishares_nav_scrape` | `none`), `nav_date`, `nav_date_iso`, `nav_stale`, `change_pct`,
    `wk52_low`, `wk52_high`, `proxy_symbol`, `facts` (las mismas del fondo del payload completo, con las mismas reglas; el enunciado del contrato cita `wk52`: el backend envía las dos claves reales, ver el informe de G2).
    `price` finito > 0 o `null`; tamaño total ≤ 64 KB (si no cabe se quitan los `facts`; si aun así no cabe, ese usuario no se envía). **Nunca** `series`, `funds` anidados ni otra clave: el validador local lo rechaza antes de enviar.
  * El backend envía **siempre todos** los fondos de la lista del usuario con lo que haya podido leer: un fondo sin ficha o con NAV ilegible (o con un salto fuera de 0,5-2× respecto a `prev_price`) va con `source: "none"`
    y `price: null`. Es la RPC la que conserva el último NAV bueno en ese caso y la que no sustituye un NAV por otro con `nav_date_iso` más antigua.
  * Respuesta: `{"ok":true,"updated":n,"kept":m,"added":k}`, o `{"ok":false,"reason":"no_payload"}` si el usuario aún no tiene payload completo (el job NAV lo anota y sigue; el completo lo creará). Token incorrecto: error `42501 forbidden`
    (el job falla con un error claro). RPC inexistente (HTTP 404 / `PGRST202`): el job falla con «RPC publish_personal_nav no existe todavía» **sin publicar nada**, salvo que la variable de repo
    `PERSONAL_NAV_FALLBACK_FULL` valga `1` (apagada por defecto): entonces ese run construye y publica el payload completo con `publish_personal_feed`.
  * Log (público): **una línea por run**, solo contadores y la fecha del NAV más reciente, p. ej.
    `datos propios: NAV: 2 fondos, 2 con NAV exacto, NAV más reciente 2026-10-27 (1 d); RPC: 1 usuarios ok (actualizados 2, conservados 0, añadidos 0), 0 sin payload completo, 0 fallidos [run 2026-10-28T10:23Z]`. Nunca ISIN, URL, nombres ni importes.
  * **Por qué los dos workflows no comparten concurrencia**: GitHub guarda un único run pendiente por grupo; con un grupo común un run de NAV pendiente descartaría al completo (es el que crea los payloads).
    El daño posible de un cruce es acotado: si un run de NAV publicara entre la lectura de la ficha del run completo y su publicación, el completo reescribiría ese NAV con el que leyó unos minutos antes, y el siguiente run
    de NAV lo corrige (la RPC no admite un NAV más antiguo que el guardado; con la parada temprana, si el NAV reescrito es anterior al último día hábil esperado se vuelve a visitar la ficha, y si es
    el esperado, el más nuevo se recupera como tarde al día hábil siguiente). El minuto :38 deja ≥ 15 min a cada run de NAV, y un run completo dura ~2 min.
  * **Latido opcional**: secreto `HEALTHCHECK_PERSONAL_NAV_URL` (vacío o inexistente = no hace nada); si existe y es `https://`, el job NAV lo pinga al terminar bien.
* **Petición a la ficha**: una sola por ficha y run (la variante `base`); el desfase de fecha que se vio en el dashboard venía de un dato guardado en la app. El diagnóstico manual `nav-probe.yml` existe por si vuelve a pasar.
* **Frescura del NAV**: `funds[].nav_date_iso` es la fecha del NAV tal como la escribe la ficha de iShares (normalmente 1-2 días hábiles por detrás; `nav_stale` si supera 6 días). La ficha da el NAV con **2 decimales**
  (`EUR 12.48`); el backend lo guarda con 4 por prudencia, pero la precisión real es la de la ficha. Si la ficha trae varios «NAV as of» distintos se usa el primero. Sin NAV exacto (ficha ilegible, ISIN ausente,
  salto fuera de 0,5–2×) `source` es `"none"` y `price` es `null`: el cliente conserva su último valor. El log del paso de construcción solo imprime contadores y fechas de NAV (ver README), nunca ISIN ni nombres.
* **`personal_watch_for_backend` vacía** (sin fondos con ficha válida ni símbolos): el workflow sale pronto sin instalar nada ni publicar (`work=false`).
* **Seguridad de la tabla** (a cargo del dueño, en SQL): las dos funciones deben validar `p_token` (comparación en tiempo constante contra un hash), ser `security definer` con
  `search_path` fijo y no devolver nada con un token inválido; la clave publicable solo puede ejecutar esas dos funciones.

## 6. Equivalencias con v1 (para migrar el dashboard)

| v1 (`data.json`, rama `data`) | v2 |
|---|---|
| `indices`, `stocks`, `funds`, `commodities`, `forex`, `heatmap`, `sector_heatmap` (filas con `price`, `change_pct`...) | `index.json` + `s/<SYM>/latest.json` (cada símbolo es independiente; los mapas de calor se montan con los símbolos que quiera el cliente) |
| `price` sin divisa, `price_unit_divisor` solo si Yahoo la informa | `currency` + `unit` + `price_unit_divisor` siempre |
| `history` (objetos con `date/close/open/high/low/volume`, `max` reducido a mensual) | `s/<SYM>/d/<AAAA>.json` en columnas, 10 años diarios |
| `intraday` (`time`/`close`) + `intraday_tz` | `s/<SYM>/i.json` (`t`/`c` + `tz`), solo hot |
| `your_funds` (ISIN, NAV, facts, analytics) — **v1 ya no lo publica** | **datos propios** (§5): `funds` del payload; `analytics` y `history` los calcula el cliente con `series` del proxy (histórico largo, a reescalar al NAV) |
| `news.positions` — **v1 lo deja vacío** | `news.watchlist` |
| `rates`, `cpi`, `cpi_meta`, `market_mood` (sin altcoin), `fx_usd` | `macro.json` (mismas claves) |
| `market_mood.altcoin_season`, `staking` | no están en v2 |
| `stocks[].market_cap`, `pe_*`, perfil de fundamentales | no están en v2 (siguen en v1) |
| `health` | `health.json` |
| `meta.json` (`generated_at`, `data_bytes`) | `manifest.json` (`generated_at`, `total_bytes`) |
