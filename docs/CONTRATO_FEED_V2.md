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
https://<usuario>.github.io/<repo>/v2/manifest.json             ruta -> bytes + sha256 de todo lo anterior
```

Con dominio propio, la URL base es la variable de repo `FEED_V2_BASE_URL`. Pages sirve con `Cache-Control: max-age=600`: no se puede forzar la caché
desde aquí, así que el cliente decide cada cuánto mira `index.json` o `manifest.json` (cuyo `generated_at` dice si hay algo nuevo) y no debe tratar un
desfase de unos minutos como un fallo.

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
  `close_le_0` (cero o negativo) y `unknown` (anotada por una versión anterior que no guardaba el motivo). Una barra con `open`/`high`/`low` no finito o <= 0 **pero cierre
  válido** (el 2020-04-21 el WTI cerró en +10,01 $ con mínimo de -40,32) **no se descarta**: ese campo se sustituye por el cierre y el rango `l..h` se recalcula con los
  valores válidos. Si un símbolo pierde **más de 3 barras o más del 1 %** de las suyas, sus datos se consideran sospechosos y **no se publica** (`problems.suspect`,
  `counts.suspect`; conserva lo anterior marcado `stale`). Un cliente no debe interpolar el hueco ni esperar una barra en cada día hábil.
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

### `manifest.json`
Ruta → bytes y sha256 de **todos** los ficheros (menos él mismo). El run siguiente verifica con él cada descarga del sitio anterior, y el validador comprueba que coincide con el
disco. Un cliente que quiera integridad puede verificar lo que descarga.

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

## 4. Cómo leerlo (algoritmo del cliente)

1. `GET index.json` (cada pocos minutos; compara `generated_at`). Para cada símbolo que sigues y tiene `status` `active` y `as_of`, `GET s/<SYM>/latest.json`.
2. Para un gráfico: `GET s/<SYM>/d/<AAAA>.json` del año en curso y de los anteriores que necesites (los cerrados son inmutables: caché larga en el cliente, validando contra el
   `sha256` del manifiesto si quieres). Une las columnas por posición.
3. Para el gráfico de 1 día: `s/<SYM>/i.json` si existe.
4. Valora posiciones con `price / price_unit_divisor` **solo** si `unit` es `currency` o `subunit`; nunca mezcles `GBp` con `GBP` sin dividir.
5. Si `stale` es `true`, enséñalo (el dato es anterior); si el símbolo no está en `index.json` o no tiene `latest.json`, enséñalo como «sin datos».
6. Las criptomonedas del top están en `crypto/latest.json` (`kind: crypto` en el índice).

## 5. Datos propios (Supabase, fuera del repo)

Lo que **no** es público —qué fondos sigues por ISIN, su NAV exacto y las series de lo que no está en el universo público— nunca pasa por este repo ni por Pages: vive en
Supabase y lo escribe el backend con dos RPC. El código del repo no contiene ningún ISIN ni lista del dueño (los ejemplos usan ISIN y símbolos **falsos**).

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
        "prev_price": 25.4
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
  0,5–2× su valor se considera un error de parseo y no se publica. `symbols` y `proxy_symbol` siguen la regla de símbolo del §2. Máximos: 20 usuarios, 20 fondos y 40 símbolos por usuario.
* **`publish_personal_feed(p_token text, p_user text, p_payload jsonb)`** recibe, por usuario, este payload (≤ 1 MB; el backend lo recorta si hace falta). Solo lleva series de símbolos
  que **no** están en el universo público (`public` lista los que sí, para que el cliente los lea del feed público); `d`/`c` son fechas y cierres (≤ 2 años diarios):

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
    },
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
    }
  ],
  "public": [
    "AAPL"
  ]
}
```
<!-- /ejemplo -->

* **Es opcional**: si Supabase o el token faltan, o algo falla, el run solo emite un `::warning::` (el feed público no depende de esto).
* **Quién publica y con qué cadencia**: el workflow `personal-nav.yml` (no `publish-feed-v2.yml`), cada 30 min de lunes a viernes entre 05:23 y 21:53 UTC (`23,53 5-21 * * 1-5`), con concurrencia
  propia: es el **único escritor** de `publish_personal_feed`. Cada llamada **sustituye** el payload del usuario (no hay lectura del anterior ni fusión), así que **cada run envía el payload completo**
  (`funds` y `series`): si una serie de Yahoo falla en un run, ese run publica sin ella hasta el siguiente. Un cliente debe tratar `generated_at` como «último intento correcto», no como «NAV de hoy».
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
| `your_funds` (ISIN, NAV, facts, analytics) | **datos propios** (§5): `funds` del payload; sin `analytics` ni `history` rescalado |
| `news.positions` | `news.watchlist` |
| `rates`, `cpi`, `cpi_meta`, `market_mood` (sin altcoin), `fx_usd` | `macro.json` (mismas claves) |
| `market_mood.altcoin_season`, `staking` | no están en v2 |
| `stocks[].market_cap`, `pe_*`, perfil de fundamentales | no están en v2 (siguen en v1) |
| `health` | `health.json` |
| `meta.json` (`generated_at`, `data_bytes`) | `manifest.json` (`generated_at`, `total_bytes`) |
