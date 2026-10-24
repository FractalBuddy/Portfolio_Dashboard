# Auditoría 08 — Backend de mercado de «My Portfolio Dashboard»

Auditor externo, independiente y escéptico. Fecha: 2026-10-24. Commit auditado: `2faeeaa` (`main`). Feed real analizado: rama `data`, commit `80df5ba` (`generated_at` 2026-10-23T23:14:42Z).

---

## 1. Resumen ejecutivo

1. **No hay ningún hallazgo Crítico.** El diseño de fondo es bueno: `fetch` sin permisos de escritura, `publish` valida con un script del propio repo, nada de `${{ }}` dentro de `run:`, acciones fijadas por SHA, sin `pull_request_target`, concurrencia serializada.
2. **Alto (BE-01):** las filas de precio no llevan divisa ni unidad. El feed real publica `SWDA.L = 10882.0` (GBp) junto a `EIMI.L = 50.79` (USD). Un cambio GBp↔GBP de Yahoo (×100) no lo detecta nada.
3. **Alto (BE-04):** el diseño actual (un `data.json` monolítico de 10,3 MB, ~158 KB por símbolo) no escala a un feed genérico multiusuario. Se agota el límite propio de 18 MB con unos 110 símbolos, y hay riesgo de ToS al redistribuir datos de Yahoo.
4. **Medio (BE-05/06):** `sanity_check.py` no valida el contenido de las series ni compara valores con el feed anterior (probado: ×100 en tres fondos, `close` 0/None/−5 y fechas +400 días pasan con rc=0). Además, si falla la descarga de `prev.json` se desactivan en silencio las reglas de regresión.
5. **Medio (BE-02):** la CI **ya está roja en `main`** (`ruff --select F`: 3 errores) y no se ejecuta en `push` a `main`. 50 de 51 commits entraron directos por la web, sin CI.
6. **Medio (BE-03/09):** el feed público y el código revelan los fondos clase S (ISIN), la lista de cripto y el universo de acciones. El `force-push` no borra los feeds antiguos: siguen accesibles por SHA.
7. **Medio (BE-07/08):** dependencias flotantes sin hashes en el job con secreto, y un validador sin lista blanca de campos, de modo que una dependencia comprometida podría publicar texto arbitrario hacia el dashboard.
8. **Bajo:** dos bombas de relojería en los tests (fallan desde 2026-11-08 y 2026-12-01), `verify-cdn` solo mira `meta.json`, y el presupuesto de tiempo solo cubre Yahoo.
9. **Verificación ejecutada:** `pytest` 332 OK (cobertura de líneas 89 %), `ruff --select F` falla, y el validador acepta el feed real (rc=0).
10. No pude verificar contra servicios reales (por mandato), los logs de Actions, la configuración del repo ni el HTML del dashboard.

---

## 2. Alcance y método

**Alcance:** `scripts/fetch_market_data.py` (2038 líneas), `scripts/sanity_check.py` (508), `.github/workflows/{update-data,ci,keepalive}.yml`, `.github/dependabot.yml`, `tests/` (7 ficheros, 226 funciones de test = 332 casos con parametrización), `requirements.txt`, `.gitignore` y `README.md` solo como contraste de lo que afirma la documentación.

**Método (todo local, sin contactar con Yahoo, iShares, CoinGecko, FRED ni CNN):**

- Lectura completa de los dos scripts y de los tres workflows.
- Entorno aislado (venv en scratchpad) con `requirements.txt` + `pytest==9.1.1`, `ruff==0.15.11`, `PyYAML==6.0.3` (los pines de la CI). Para instalarlos se contactó con PyPI, que es indispensable para poder ejecutar los tests. Solo se usó Python 3.13; la CI usa 3.14, y eso no se pudo contrastar.
- `pytest` con cobertura de líneas y de ramas; `ruff --select F` (el de la CI) y `ruff` con reglas por defecto.
- Viaje en el tiempo con `time-machine` (+5, +10, +14, +15, +20, +30, +45, +100 días) para buscar tests dependientes de la fecha.
- Scripts de reproducción, en scratchpad y fuera del repo, que reutilizan los generadores de feed de los propios tests (`healthy()`), contra el validador y los parsers reales con datos sintéticos.
- Revisión del código de `yfinance 1.7.0` instalado (timeouts por defecto, signatura de `get_valuation_measures`).
- Lectura del feed real publicado, con `git fetch origin data` (lectura de ramas del propio repo; es el mismo canal que se usa para empujar el informe). Se analizó el blob a mano, sin red adicional, y se pasó el validador sobre él.
- Escaneo del historial git en busca de secretos, ficheros personales y metadatos de autor.

**Limitaciones declaradas:**

- No he mirado logs reales de Actions: los tiempos de ejecución y la frecuencia de 429 son estimaciones por código.
- No he verificado que los SHA de las acciones correspondan al tag que dice el comentario (habría que consultar GitHub; ver BE-19).
- No he visto `portfolio.html` (privado), así que lo que haga el dashboard con los strings del feed queda como hipótesis.
- Los límites de cuota de Yahoo y CoinGecko los cito de memoria o como orden de magnitud. Deben contrastarse con la documentación vigente antes de dimensionar.

**Leyenda de estado:** *Confirmado* = reproducido o medido aquí. *Probable* = el código lo implica y el mecanismo es conocido, pero no lo ejecuté contra el servicio real. *Hipótesis* = depende de algo que no pude ver.

---

## 3. Tabla de hallazgos

| ID | Severidad | Estado | Título | Ubicación |
|---|---|---|---|---|
| BE-01 | **Alto** | Confirmado | Filas de precio sin divisa/unidad; GBp y USD mezclados en la misma lista; `GBp`→`GBP` por `.upper()` | `fetch_market_data.py:258-294`, `:480-482`, `:87-92` |
| BE-02 | Medio | Confirmado | CI roja en `main` (ruff F ×3) y la CI no corre en `push`; 50/51 commits directos sin CI | `ci.yml:4`, `ci.yml:34`, `tests/test_fund_facts.py:4,6,67` |
| BE-03 | Medio | Confirmado | El feed y el código públicos revelan fondos clase S, cripto y universo de acciones | `fetch_market_data.py:58-59`, `:71-84`, `:101-114`, `:1060-1098` |
| BE-04 | **Alto** | Confirmado (cálculo) / Probable (ToS) | Arquitectura monolítica no apta para feed genérico multiusuario; símbolos sin validar si fueran entrada de usuario; ToS de Yahoo | `fetch_market_data.py` completo; `sanity_check.py:45-46` |
| BE-05 | Medio | Confirmado | El validador no inspecciona las series ni compara valores con el feed anterior | `sanity_check.py:273-275`, `:363-374`, `:386-427` |
| BE-06 | Medio | Confirmado | Si falla la descarga de `prev.json` se desactivan en silencio las reglas de regresión | `update-data.yml:55`, `:102`; `sanity_check.py:386`, `:426-427` |
| BE-07 | Medio | Probable | Dependencias flotantes sin hashes en el job con secreto + validador sin lista blanca de strings | `requirements.txt`; `update-data.yml:49,59`; `sanity_check.py:67-77` |
| BE-08 | Medio | Probable | Yahoo: ráfaga de ≥280 peticiones por run desde IP compartida, histórico `max` completo 4×/día, backoff de ≤4 s sin tratar 429 | `fetch_market_data.py:233-255`, `:1926-1960` |
| BE-09 | Medio | Probable | Los feeds sobrescritos por `force-push` siguen accesibles por SHA; el README afirma que el repo «se queda en ~2 MB para siempre» | `update-data.yml:139-159`; README «Por qué una rama `data`» |
| BE-10 | Bajo | Confirmado | Dos tests dependen de la fecha real y fallarán el 2026-11-08 y el 2026-12-01 | `tests/test_fetch_market_data.py:671-683`, `:514-528` |
| BE-11 | Bajo | Confirmado | `verify-cdn` solo verifica `meta.json`, no `data.json` | `update-data.yml:179-192` |
| BE-12 | Bajo | Probable | El presupuesto de 14 min solo cubre Yahoo; el resto suma ~17,8 min en el peor caso frente a un timeout de job de 20 min | `fetch_market_data.py:190`, `:1230-1260`, `:835-849`, `:1560-1578` |
| BE-13 | Bajo | Confirmado | Cierre previo negativo (precedente WTI 2020) produce `change_pct` absurdo | `fetch_market_data.py:272-273` |
| BE-14 | Bajo | Hipótesis | Títulos de noticias: se quitan etiquetas pero no entidades; depende de cómo los pinte el dashboard | `fetch_market_data.py:1033` |
| BE-15 | Bajo | Confirmado | `market_mood` y `staking`: sin marca/validación de frescura; VIX y Fear & Greed sin arrastre del feed anterior | `fetch_market_data.py:1785-1851`, `sanity_check.py:185-188` |
| BE-16 | Bajo | Confirmado | Herramientas de CI pineadas inline (Dependabot no las ve); `actionlint` opcional; dependencias de test fuera de `requirements` | `ci.yml:31`, `ci.yml:39-46` |
| BE-17 | Bajo | Hipótesis | Scrape de iShares: doble GET a la misma URL, el primer «NAV as of» gana, `isin in plain` solo comprueba presencia | `fetch_market_data.py:1170-1260`, `:1346-1360`, `:1480-1481` |
| BE-18 | Bajo | Hipótesis | Keepalive: eficacia no probada, push directo a `main`, checkout con credencial persistente | `keepalive.yml:30-57` |
| BE-19 | Info | Hipótesis | SHA de acciones no contrastados con upstream en esta auditoría | `update-data.yml:38,43,64,94` |
| BE-20 | Info | Confirmado | Intradía sin zona horaria; semántica de `concurrency`; cron de las 20:17 UTC es previo al cierre de EE. UU. en invierno | `fetch_market_data.py:414`, `update-data.yml:22-24` |

---

## 4. Detalle de hallazgos

### BE-01 — Sin divisa/unidad en las filas de precio (Alto, Confirmado)

**Impacto.** El dashboard recibe `price` sin saber si son libras, peniques, dólares o euros. En el feed real conviven `SWDA.L` 10882.0 (GBp), `IMEU.L` 3335.5 (GBp), `SGLN.L` 5882.0 (GBp) y `EIMI.L` 50.79 (USD) en la misma lista `funds`. La única divisa publicada es `cap_currency`, y solo para acciones con capitalización. Índices, materias primas, divisas, fondos y ETF no la llevan. Hoy el daño es acotado (los `.L` son ETF de «Explorar»; las acciones no británicas sí llevan `cap_currency`), pero en cuanto el dashboard multiplique cantidad × precio de un símbolo `.L`, un error de ×100 es silencioso. En un feed genérico (BE-04) es un bloqueante.

**Evidencia.**
- `fetch_market_data.py:277-283`: el dict de `fetch_symbol` solo trae `price`, `change_pct`, `change_pct_7d`, `volume_ratio`, `sparkline`, `as_of`.
- `fetch_market_data.py:480-482`: `cap_currency` se obtiene con `cur.strip().upper()`, de modo que `GBp` pasa a `GBP`. Confirmado: `parse_stock_profile({"marketCap":5e9,"currency":"GBp","currentPrice":1234.0})["cap_currency"] == "GBP"`. La unidad de peniques desaparece justo donde el código «normaliza».
- Ninguna parte del código ni de los tests menciona `GBp`/pence fuera de un comentario sobre proxies (`:115-118`).
- Feed real (`origin/data`, `80df5ba`): `precios fondos .L: [('SWDA.L', 10882.0), ('EIMI.L', 50.79), ('IMEU.L', 3335.5), ('SGLN.L', 5882.0)]`; claves de la fila: `analytics, as_of, change_pct, change_pct_7d, facts, history, intraday, name, price, sparkline, symbol, volume_ratio`.

**Reproducción.**
```
python - <<'EOF'   # tras: sys.path.insert(0,"scripts"); import fetch_market_data as fm; fm.yf_history=<DataFrame falso>
row = fm.fetch_symbol("SWDA.L"); print("currency" in row)   # False
EOF
```
La detección de saltos de unidad está en BE-05 (reproducción R2 sobre el feed real: pasa con rc=0).

**Parche propuesto.** Leer `Ticker.fast_info["currency"]`, o `history_metadata["currency"]`, que no cuesta una petición extra porque ya llega con el `history`. Publicar `currency` (tal cual, `GBp` incluido) y `price_unit_divisor` (100 para `GBp`, `GBX`, `ZAc`, `ILA`). Normalizar a la divisa mayor en el backend o dejar que el dashboard lo haga con ese campo. Hacer `.upper()` solo para consultar tipos de cambio, no para el campo publicado. Validar en `sanity_check.py` que toda fila con `price` tenga `currency` de 3-4 letras.
**Esfuerzo:** 0,5 día más tests.

---

### BE-02 — CI roja en `main` y sin cobertura de `push` (Medio, Confirmado)

**Impacto.** El gate de calidad existe, pero no protege `main`. La CI solo se dispara con `pull_request` y `workflow_dispatch`. El historial muestra 50 de 51 commits con committer `GitHub` (edición/subida por la web) y un solo merge. Resultado: el último estado de `main` incumple su propia CI y nadie se enteró.

**Evidencia.**
- `ci.yml:4`: `pull_request: {}` (sin `push`).
- `ruff check --select F scripts tests` con `ruff==0.15.11` da exit 1:
  ```
  tests/test_fund_facts.py:4:8:  F401 `re` imported but unused
  tests/test_fund_facts.py:6:8:  F401 `types` imported but unused
  tests/test_fund_facts.py:67:5: F841 Local variable `facts` is assigned to but never used
  ```
  Los introdujo el commit `f37fd0c` (2026-10-22). Con `ruff` por defecto salen 39 incidencias (30× E702, 4× E701…); no se aplican, pero indican que el estilo no se hace cumplir.
- `git log --merges` = 1; `git log --format='%cn' | sort | uniq -c` = 50× `GitHub`.

**Reproducción.** `ruff check --select F scripts tests; echo $?` → 1.

**Parche propuesto.** (a) Borrar `import re`, `import types` y la asignación `facts = ...` de `tests/test_fund_facts.py`. (b) Añadir a `ci.yml` `on: push: branches: [main]`. (c) Ruleset en `main` que exija el check `CI/checks`; el README ya advierte de que keepalive empuja directo, así que añadir al bot como *bypass actor* o mover keepalive a PR.
**Esfuerzo:** 0,25 día.

---

### BE-03 — Datos personales en el feed y en el código públicos (Medio, Confirmado)

**Impacto.** No hay importes, cantidades ni operaciones (verificado: ni en el feed ni en el historial git). Pero la composición de cartera e intereses sí es pública. Un tercero puede deducir qué cosas sigue el dueño, y combinarlo con el nombre de la cuenta y los metadatos de commit.

**Evidencia (feed real, `80df5ba`).**
- `your_funds`: ISIN `IE000ZYRH0Q7` y `IE000QAZP7L2`, nombres completos («… Acc EUR — Class S»), NAV exacto, `proxy_symbol` y `facts` (gestora, TER, patrimonio de la clase). Las clases S son poco frecuentes: acotan mucho quién puede tenerlas.
- `news.positions`: nombre engañoso, pues son las noticias de la lista STOCKS completa (30 símbolos), no de «posiciones». El universo incluye `COHR`, `LITE`, `IPGP`, `NOVT`, `AMS.SW` (fotónica) y `AMP.MC`, `NXT.MC` (small caps españolas): un perfil de interés muy específico.
- `CRYPTO_WATCHLIST` (`:58-59`) está en el código público con `unibright`, `rocket pool`, `mina`, `tezos`… Los titulares filtrados salen con `symbol`/`asset_name` = palabra coincidente (`:1084-1087`), si bien en el feed analizado no había noticias cripto.
- `staking.eth_lido` (solo tasas, sin saldos).
- Historial git: los 51 commits usan el correo `…@users.noreply.github.com` (bien), con el nombre completo «Alex Martinez» como autor. Sin claves ni tokens en el historial (regex sobre `git log --all -p`: 0 coincidencias).
- El README lo reconoce («Qué es público (y qué no)»), así que es riesgo asumido y documentado, no un descuido.

**Reproducción.** `git show origin/data:data.json | python3 -c "import json,sys;d=json.load(sys.stdin);print([f['isin'] for f in d['your_funds']])"`.

**Parche propuesto.** (1) Si se quiere privacidad real: sacar `your_funds` y `CRYPTO_WATCHLIST` del repo público (fichero de configuración privado o secreto, o repo privado + hosting estático) y servir el NAV por ISIN como un símbolo más del feed genérico (ver sección 5). (2) Renombrar `news.positions` a `news.watchlist` (cambia el contrato con el dashboard). (3) Para borrar lo ya publicado, ver BE-09. (4) Si se mantiene público: mostrar solo universos «amplios» (S&P 500, MSCI…) y no una lista idiosincrática.
**Esfuerzo:** 0,5-2 días según la opción.

---

### BE-04 — Diseño para feed genérico multiusuario: bloqueantes (Alto, Confirmado por cálculo / Probable en ToS)

Detalle en la sección 5. Resumen de evidencia:

- **Densidad medida:** 58 símbolos con histórico = 9,96 MB de 10,27 MB (history 9,15 MB, intradía 0,78 MB). Son **157,7 KB por símbolo** (92 % histórico), unos 39 KB comprimidos en gzip.
- **Techo propio:** `MAX_DATA_BYTES = 18_000_000` (`sanity_check.py:46`). Margen actual: 7,7 MB ≈ **+48 símbolos** (~110 en total). Con 1000 símbolos serían ~158 MB (el límite de jsDelivr para GitHub es 20 MB, según el propio comentario `:46`).
- **Coste por run:** ≈ 277 peticiones mínimas a Yahoo para 58 símbolos (recuento estático, ver BE-08). Crecen linealmente con N y con el histórico `max` completo en cada run.
- **Entradas de usuario hoy inexistentes:** `f"...headline?s={symbol}..."` (`:1069`), `f"{c}USD=X"` (`:786`), `f"{symbol.upper()}-USD"` (`:1605`) interpolan símbolos sin validar ni URL-encodear. Con símbolos constantes no hay problema; con símbolos de usuarios sería inyección de parámetros.
- **ToS (Probable):** la propia documentación de `yfinance` indica que las APIs de Yahoo son para uso personal. Redistribuir sus datos a varios usuarios en un CDN público es un riesgo de términos de servicio, al margen de la técnica. Debe revisarlo el dueño; no lo he podido contrastar.

**Esfuerzo:** 5-10 días el rediseño; ver sección 5.

---

### BE-05 — El validador no inspecciona series ni continuidad (Medio, Confirmado)

**Impacto.** `sanity_check.py` está bien pensado para fallos grandes (feed vacío, histórico congelado, universo encogido), pero deja pasar datos corruptos «plausibles»: un cambio de unidad, series con ceros o duplicados, o fechas futuras. Todo ello llega al dashboard con luz verde.

**Evidencia.** Reglas de contenido: solo `price > 0` y `|change_pct| ≤ 60` con tolerancia de 2 símbolos (`:273-275`); las series se evalúan por **número de barras** y **fecha de la última** (`:363-378`); `_last_date` toma el máximo sin comparar con el futuro (`:95-105`); la regresión mira solo desaparición de símbolos, acortamiento de histórico y pérdida de NAV (`:386-422`). Nunca se compara un precio con el del feed anterior.

**Reproducción** (`/…/scratchpad/repro_sanity.py`, reutiliza `healthy()` de los tests; salida real):
```
R2  fondo x100 (precio + histórico) vs prev                  -> PASA
R3  histórico con close 0/None/-5, duplicados, orden inverso  -> PASA
R4  NAV x10 respecto al anterior                              -> PASA
R5  última barra de TODOS los símbolos a +400 días            -> PASA
R5c control: histórico congelado -40 d                        -> BLOQUEA
```
Y sobre el **feed real** (`origin/data`) con `prev` = ese mismo feed: tres fondos `.L` divididos entre 100 (precio, sparkline, histórico e intradía) → `OK: 10 índices, 30 acciones` (rc=0). El propio feed real sin tocar también pasa (rc=0), así que el gate no da falsos positivos hoy.

**Parche propuesto** (en `check()`, solo stdlib):
1. Por barra: fecha ISO, estrictamente creciente, `≤ hoy+1`; `close` finito y `> 0`; `low ≤ close ≤ high` cuando existan.
2. Continuidad contra `prev`: para cada símbolo común, `0.5 < last_new/last_prev < 2` (o 0,8–1,25 en horizonte ≤ 1 día) y mediana de `close_new/close_prev` en las fechas solapadas ≈ 1 (tolerancia 5 %; `auto_adjust` retoca el pasado unas décimas al año). Eso detecta rescalados ×100 aunque cambie todo el histórico a la vez.
3. NAV de `your_funds`: `|NAV/NAV_prev − 1| ≤ 5 %` y coherente con la variación del proxy.
4. Fechas futuras de `as_of`, CPI, rates.
Excepción controlada vía `allow_regression`.
**Esfuerzo:** 1-1,5 días con tests (los R2-R5 del apéndice A son los tests).

---

### BE-06 — Regresión desactivada en silencio (Medio, Confirmado)

**Impacto.** Las reglas de regresión dependen de que `curl` baje `data.json`. Si falla, se escribe `{}` y solo se emite un aviso de log (no un `Alert`). En ese run no se detectan desapariciones de símbolos dentro del margen absoluto (≥25 acciones, ≥3 fondos). Combina mal con Yahoo degradado: precisamente cuando más hace falta el gate.

**Evidencia.** `update-data.yml:55,102`: `curl -fsS --max-time 60 --retry 2 … || echo '{}' > prev.json`. `--retry` cubre errores transitorios pero no todos los fallos de conexión. `sanity_check.py:386,426-427`: `has_prev` falso ⇒ `warnings.append("sin feed anterior utilizable…")` (no `Alert`, no llega a Healthchecks).

**Reproducción** (R1, `repro_sanity.py`):
```
R1a 5 acciones desaparecen, con prev real          -> BLOQUEA
R1b 5 acciones desaparecen, prev='{}' (curl falló) -> PASA
```
**Parche propuesto.** Distinguir primer run de fallo: guardar el código HTTP (`-w '%{http_code}'`), tratar `404` como «sin anterior» y cualquier otra cosa como error del job (o reintentar con `--retry-all-errors`). Y volver `Alert` el «sin feed anterior» cuando la rama `data` existe (`EXPECT_PREV=true`).
**Esfuerzo:** 0,25 día.

---

### BE-07 — Cadena de suministro y falta de esquema (Medio, Probable)

**Impacto.** El job `fetch` instala `yfinance`, `requests`, `feedparser` y sus transitivas **sin lock ni hashes** (`requirements.txt`: solo `>=x,<mayor`), y ejecuta ese código con `FRED_API_KEY` en el entorno. El README lo reconoce. Lo que lo agrava: aunque el job sin escritura limita el daño, un paquete comprometido puede producir un `data.json` que **sí pase el validador** y se publique. `sanity_check.py` valida números y longitudes, no la lista de claves ni los textos (`name`, `title`, `source`, `asset_name`, `classification`, `rating`…), y no hay tope por campo.

**Evidencia.** `update-data.yml:49` (`pip install -r requirements.txt`), `:57-61` (secreto solo en el paso de fetch, bien acotado), `sanity_check.py:67-77` (límites numéricos). Reproducción R6: `name="<img src=x onerror=alert(1)>"` y una clave extra `extra_blob` de 1000 caracteres → **PASA**. Si el dashboard (que según `.gitignore` incrusta claves de API) inserta algún string con `innerHTML`, el feed pasa a ser un vector contra una página con secretos. Eso último es Hipótesis (no he visto el HTML).

**Parche propuesto.** (1) `requirements.in` + `requirements.txt` generado con `pip-compile --generate-hashes`, instalación con `--require-hashes`; Dependabot sigue funcionando. (2) Esquema estricto en `sanity_check.py`: lista blanca de claves por sección, regex por string (símbolos `^[A-Z0-9.^=\-]{1,15}$`, nombres sin `<>`), tope de bytes por campo. (3) En el dashboard, `textContent`, nunca `innerHTML`, para cualquier string del feed.
**Esfuerzo:** 1 día (1+2), más lo que cueste el dashboard.

---

### BE-08 — Presión sobre Yahoo y backoff insuficiente (Medio, Probable)

**Impacto.** Cada run lanza una ráfaga desde IPs compartidas de runners de GitHub, la clase de origen que Yahoo suele limitar. El reintento es breve y no distingue un 429.

**Evidencia.**
- Recuento estático con las listas actuales: 86 símbolos × consulta de 8 d + 58 × (histórico `max` + intradía 5 m) + 30 × (`info` + tabla de valoración) + fondos propios/analytics (~8) + FX (~4) + VIX/benchmarks (~3) ≈ **277 peticiones mínimas**, más hasta **90** de altcoin season (`ALT_MAX_TRIES`), más 34 RSS por símbolo de Yahoo. Cada fallo triplica (3 intentos). Las 58 series `max` se bajan enteras 4 veces al día aunque solo cambie la última barra.
- `fetch_market_data.py:251`: espera `uniform(0.5, 2)` y `uniform(0.5, 4)` s; no hay tratamiento específico de `YFRateLimitError` (grep: 0 apariciones). Entre símbolos solo hay `sleep(0.25)` en perfiles (`:741`).
- Lo bueno: el interruptor `_YF_FAIL_STREAK >= 15` (`:240`) corta los reintentos cuando Yahoo está claramente caído, y `_DEADLINE`.

**Parche propuesto.** (1) Capturar `YFRateLimitError`: pausa larga (30-60 s) con jitter y abandono limpio tras N. (2) Histórico incremental: guardar el `max` en el feed anterior y pedir solo `period="1mo"`, fusionando por fecha (reduce las peticiones pesadas ~95 %). (3) `yf.download(tickers=[…], threads=False, group_by="ticker")` para las consultas de 8 d (menos cookies/crumbs). (4) Separar en dos jobs: precios 4×/día y perfiles 1×/día.
**Esfuerzo:** 1-2 días.

---

### BE-09 — La rama `data` «de un solo commit» no borra el pasado (Medio, Probable)

**Impacto.** Cada run hace `git init` + `git push --force`, de modo que la rama tiene siempre un commit. Pero los commits anteriores quedan sin referencia y, hasta el GC de GitHub, **siguen accesibles por su SHA**; además los eventos públicos del repo exponen los SHA de cada push. Dos consecuencias: (a) retirar `your_funds` del feed (BE-03) no elimina los feeds ya publicados; (b) el README promete que el repo «se queda en ~2 MB para siempre», cosa que no he podido comprobar (objetos huérfanos de ~2,2 MB comprimidos × 4 pushes/día ≈ 3 GB/año hasta GC). Comportamiento de GitHub conocido, no ejecutado aquí.

**Evidencia.** `update-data.yml:144-159`. Tamaño del blob actual: 10 275 461 B, 2 245 046 B en gzip -6 (medido).

**Parche propuesto.** Medir (`gh api repos/…` → `size`) tras un mes; si interesa privacidad real, o bien recrear el repo (los SHA antiguos mueren con él) o bien no poner datos sensibles en un repo público (sección 5). Para el volumen: publicar en GitHub Pages/R2 en lugar de empujar blobs de 10 MB.
**Esfuerzo:** 0,25 día para medir; el resto depende de la decisión de arquitectura.

---

### BE-10 — Tests con bombas de relojería (Bajo, Confirmado)

**Evidencia.** Con `time-machine` fallan sin tocar código:

| Test | Primer día de fallo | Causa |
|---|---|---|
| `test_build_list_conserva_la_fila_anterior_marcada_antigua` (`:671`) | **2026-11-08** | usa `as_of = "2026-10-08"` fijo con la fecha real; `STALE_CARRY_MAX_DAYS = 30` (`:200`) |
| `test_attach_profiles_usa_anterior_si_yahoo_falla_y_corta_tras_racha` (`:514`) | **2026-12-01** | `profile_as_of = "2026-10-01"` fijo; `PROFILE_MAX_REUSE_DAYS = 60` (`:202`) |

Barrido: +5, +10, +14 d → 309 passed; +15 y +20 y +30 → 1 failed; +38 y +45 y +100 → 2 failed. (Se excluyó `test_workflows.py` del barrido porque `time-machine` no afecta al `date` de bash que usan sus fakes; ese fallo era artefacto del método.)

Impacto práctico: el primer PR (o `workflow_dispatch`) posterior a esas fechas fallará sin que haya cambiado nada. En `sanity_check` esto ya está resuelto con un `autouse` que congela `check(today=…)`.

**Parche.** Derivar las fechas de `today` (`today - timedelta(days=2)`) o fijar el reloj en un fixture (`monkeypatch` de `fm.datetime`).
**Esfuerzo:** 0,25 día.

---

### BE-11 — `verify-cdn` solo comprueba `meta.json` (Bajo, Confirmado)

`update-data.yml:187` consulta únicamente `…@data/meta.json`. Se purga primero `data.json` y luego `meta.json` (`:179`), pero el éxito se decide solo con el segundo. El dashboard consulta `meta.json` y, si cambia `generated_at`, descarga `data.json`: si el edge sirve el `meta` nuevo con el `data` viejo, el cliente cree tener el feed nuevo. Parche: comparar también `curl --compressed -s …data.json | wc -c` con `data_bytes` de `meta.json` (ya está en el feed). En el cliente, validar `data.generated_at === meta.generated_at` antes de aceptar.
**Esfuerzo:** 0,25 día.

---

### BE-12 — Presupuesto de tiempo incompleto (Bajo, Probable)

`_DEADLINE = 14 min` (`:190`) solo lo consultan `yf_history`, `fetch_stock_profile` y las noticias. Las fases sin presupuesto, en su peor caso teórico con todo fallando: iShares ~208 s (`:1230-1260`, `:1346`), FRED+CPI ~306 s (`:835-849`, 3 intentos × 15 s + esperas), CoinGecko ~492 s (`:1560-1578`, 5 llamadas × 3 intentos + esperas de 8/16 s), Lido/CNN/alt.me ~60 s: **≈ 1066 s ≈ 17,8 min** sobre el hasta 14 min de Yahoo, frente a `timeout-minutes: 20` (`update-data.yml:33`). Es fail-closed (el job muere, el heartbeat manda `/fail`), así que el efecto es perder el run en vez de publicar parcial. Además, `socket.setdefaulttimeout(25)` (`:39`) no afecta a `yfinance` 1.7.0 (usa `curl_cffi` con sus propios timeouts: 10 s en `history`, 30 s en `get`, confirmado en su código), por lo que en la práctica no hay cuelgue indefinido.
**Parche.** Un único `_DEADLINE` global consultado también en iShares/FRED/CoinGecko, y publicar lo que haya antes del límite. **Esfuerzo:** 0,5 día.

---

### BE-13 — Cierre previo negativo (Bajo, Confirmado)

`fetch_symbol` rechaza un último cierre `≤ 0` (`:269`) pero no valida `prev`. Con cierres `[…, -37, 18]` (precedente real: WTI, 2020-04-20) resulta `change_pct = -148.65`. El validador solo bloquea si hay más de 2 símbolos así (`sanity_check.py:273-275`). Parche: `if prev > 0 and last > 0 else None`. **Esfuerzo:** 10 min.

---

### BE-14 — Entidades HTML en títulos (Bajo, Hipótesis)

`parse_feed` hace `re.sub(r"<[^>]*>", "", title)` (`:1033`). Con un título doblemente codificado (`&amp;lt;img src=x onerror=alert(1)&amp;gt;`) el feed publica `&lt;img src=x onerror=alert(1)&gt;` (reproducido). Es seguro si el dashboard usa `textContent` y XSS si hiciera `innerHTML`. El cuerpo de `link` sí se filtra a `http(s)` (bien). Parche: `html.unescape` en bucle limitado antes de quitar etiquetas y tratar el resultado como texto plano; o `html.escape` en la salida y documentarlo. **Esfuerzo:** 0,25 día.

---

### BE-15 — Sin frescura en `market_mood`/`staking` (Bajo, Confirmado)

CNN Fear & Greed (endpoint no documentado) se publica sin marca temporal (`:1821-1843`); el de alternative.me guarda `timestamp` pero nadie lo comprueba; Lido guarda `as_of` sin comprobación. Un endpoint «congelado» pasaría por actual. Además, a diferencia de rates/CPI/altcoin/staking, **VIX y Fear & Greed no se arrastran del feed anterior**: si VIX falla (Yahoo) y CNN falla, `market_mood sin VIX ni Fear & Greed de bolsa` bloquea toda la publicación (`sanity_check.py:187-188`). Parche: arrastre con `stale` como el resto + comprobar la antigüedad (≤ 3 días) con el `timestamp` de la fuente cuando exista (no verificado con CNN real).
**Esfuerzo:** 0,5 día.

---

### BE-16 — Herramientas de CI fuera del alcance de Dependabot (Bajo, Confirmado)

`ci.yml:31` fija `pytest`, `ruff` y `PyYAML` inline: Dependabot de `pip` solo lee `requirements.txt`. Quedarán obsoletos sin aviso. `actionlint` es opcional (`:39-46`): si el runner no lo trae (y no se descarga nada), la comprobación es un `notice`, es decir, no hace nada. `tests/test_workflows.py` usa `pytest.importorskip("yaml")`: sin PyYAML se salta en silencio el módulo entero (19 funciones de test). Parche: `requirements-dev.txt` (con hashes) leído por Dependabot, y fallar si falta PyYAML en CI. **Esfuerzo:** 0,25 día.

---

### BE-17 — Scrape de iShares (Bajo, Hipótesis)

- `fetch_ishares_nav` y `fetch_ishares_fund_facts` bajan **dos veces** la misma URL (`:1245`, `:1353`, llamadas en `:1480-1481`), y las fichas de «Explorar» una vez más. Con el HTML en caché bastaría una petición por fondo.
- `parse_ishares_nav` toma el **primer** «NAV as of» y comprueba que el ISIN aparece en algún sitio de la página (`:1178-1179`), no que el NAV sea de esa clase. Con una página con varias clases, podría coger otra.
- Rango de NAV `0.1 < price < 10000` (`:1184`), sin continuidad (BE-05).
- Los `facts` no tienen reintento (a diferencia del NAV).
- El UA «Mozilla/5.0 (compatible; …)» y la lectura automatizada de ishares.com pueden chocar con sus términos de uso (no verificado).
Hoy funciona (feed real: ambos NAV con `source=ishares_nav_scrape`, fecha 2026-10-22). Es fragilidad ante cambios de maquetación, que el diseño ya mitiga con proxy y `Alert` tras 2 runs (bien).
**Esfuerzo:** 0,5 día.

---

### BE-18 — Keepalive (Bajo, Hipótesis)

`keepalive.yml:30-57` empuja un commit a la rama por defecto con `GITHUB_TOKEN`. (a) No está documentado si ese commit cuenta como actividad que reinicie los 60 días (el README lo admite). (b) El workflow podría desactivarse a la vez que el resto (circular, también lo reconoce). (c) El checkout usa `persist-credentials` por defecto (`:30-33`), de modo que el token queda en `.git/config` durante comandos propios; no ejecuta código de terceros, así que el riesgo es mínimo. (d) Un ruleset que exija PR en `main` (lo recomendado en BE-02) lo rompería. Healthchecks es la red de seguridad real. **Esfuerzo:** 0,25 día (`persist-credentials: false` + token explícito en el push).

---

### BE-19 — SHA de acciones sin contrastar (Info, Hipótesis)

Las cuatro acciones están fijadas a SHA de 40 hex, y un test lo exige (`test_ningun_run_interpola_expresiones_y_las_acciones_van_fijadas_por_sha`). No verifiqué (sin contactar con GitHub) que cada SHA pertenezca al tag del comentario ni que pertenezca al repo upstream y no a un fork (ataque *imposter commit*). Recomendado: `pinact` o `zizmor` en CI, o comprobarlo una vez a mano.

### BE-20 — Observaciones menores (Info, Confirmado)

- Intradía sin zona horaria (`"time": "2026-10-22 09:00"` para `^N225`, `"2026-10-22 09:30"` para `^GSPC`): el cliente no puede alinear mercados distintos sin una tabla externa (`:414`).
- `concurrency` con `cancel-in-progress: false` mantiene **un** run pendiente: un tercer run cancela el pendiente anterior. Inofensivo (cada run baja datos frescos), pero el comentario «Queue instead of cancel» no es literal.
- 20:17 UTC es 16:17 en horario de verano de EE. UU. (post-cierre) pero 15:17 en invierno (con la sesión abierta): la barra diaria de un día invernal es parcial. El feed real trae `generated_at` 23:14 UTC, que no coincide con ningún cron (06:17, 11:17, 15:17, 20:17): o fue manual o GitHub retrasó el programado más de 2 h.

---

## 5. Privacidad y diseño de un feed GENÉRICO por símbolo, bajo demanda, con registro

### 5.1 Qué expone hoy (resumen de BE-03)
Fondos clase S por ISIN, universo de acciones con sesgo temático, lista cripto en el código, nombre real en el autor de los commits y feeds antiguos accesibles por SHA (BE-09). Nada con importes.

### 5.2 Por qué el diseño actual no se puede «abrir» a más usuarios (cifras medidas)
| Magnitud | Hoy (58 símbolos) | 500 símbolos | 2000 símbolos |
|---|---|---|---|
| `data.json` sin comprimir | 10,3 MB | ~79 MB | ~315 MB |
| gzip | 2,2 MB | ~19 MB | ~78 MB |
| Peticiones Yahoo / run (mín.) | ~280 | ~2400 | ~9600 |
| Push (gzip) por run | 2,2 MB | ~19 MB | ~78 MB |
Un único fichero superaría el tope de 18 MB con ~110 símbolos. Cada cliente descargaría todo para ver 30 símbolos. Una ráfaga de miles de peticiones por run desde un runner compartido tropezaría con el límite no documentado de Yahoo.

### 5.3 Arquitectura propuesta

**Datos.** Un objeto por símbolo, cacheable e inmutable donde se pueda:
```
/v1/index.json                    # versión, generated_at, lista de símbolos activos (sin quién los pide)
/v1/s/<SYM>/latest.json           # precio, divisa, unidad, as_of, cambios, sparkline (~1 KB)
/v1/s/<SYM>/d/<AAAA>.json         # histórico diario del año (el año cerrado es inmutable → cache 1 año)
/v1/s/<SYM>/i/<AAAA-MM-DD>.json   # intradía de la última sesión (opcional)
/v1/crypto/latest.json            # CoinGecko por lotes (hasta 250 ids por llamada)
```
Alojamiento: GitHub Pages o R2/S3 + CDN. No jsDelivr ni una rama que se fuerza cada vez. Los años cerrados no se vuelven a subir: el trabajo incremental baja a unos pocos KB por símbolo y run.

**Registro de símbolos** (`registry.json`, en repo o KV):
```
{ "SYM": { "kind":"yahoo|crypto", "currency":"GBp", "unit_divisor":100, "exchange":"LSE",
           "added":"…", "last_requested":"…", "tier":"hot|warm|cold",
           "status":"active|dormant|invalid|delisted", "fail_streak":0, "last_ok":"…" } }
```
- **Alta:** regex estricta `^[A-Za-z0-9.^=\-]{1,15}$`, URL-encode siempre, una sonda de existencia (1 petición), tope global (p. ej. 1500) y tope por cliente (p. ej. 100). Un usuario no puede introducir cadenas libres en el feed.
- **Baja:** sin peticiones en 30 días → *dormant* (no se refresca; el último valor se conserva con `stale`). `fail_streak ≥ 5` → *invalid*, reintento semanal. Deslistados: no bloquean la publicación (hoy 2 símbolos desaparecidos paran el run: `sanity_check.py:393-396`).
- **Tiers:** *hot* (pedidos en 7 días) 4×/día; *warm* 1×/día; *cold* bajo demanda.

**Bajo demanda.** Dos opciones:
1. *Sin servidor propio.* El cliente pide el alta con una issue de GitHub (formulario) o un PR al registro; un workflow la procesa. **Cuidado:** si se usa `issues:`, nunca interpolar `${{ github.event.issue.* }}` en `run:` (inyección); pasar por `env:` y validar con regex.
2. *Worker (Cloudflare Workers/KV).* `GET /s/<SYM>` devuelve la caché; en fallo de caché encola un alta. Más cómodo, pero el worker **ve** qué símbolos pide cada IP (ver 5.4).

### 5.4 Privacidad del diseño genérico
- **Feed no personalizado:** nada de `your_funds`; los ISIN pasan a ser símbolos como los demás (NAV por ISIN: scraping genérico por URL de producto). La posición, cantidades y notas se quedan en el cliente.
- **No correlacionar quién pide qué:** sin cookies ni *logs* de IP+símbolo; si hay Worker, no registrar el símbolo por petición (solo contadores agregados). Con Pages/R2 la petición por símbolo la ve el CDN: aceptable si se publica un **universo amplio** (p. ej. todo S&P 500, ETF UCITS populares, top 250 cripto) y los símbolos raros se piden agrupados.
- **Anonimato por prefijo (opcional):** `/v1/b/<hash(SYM)[:2]>.json` con unos cientos de símbolos por cubo, al estilo *k*-anonimato; cuesta más ancho de banda.
- **Registro público = unión de todos los usuarios**, sin autor: publicar *solo* los símbolos con ≥ k solicitantes (k≥5) o ya presentes en el universo base, para que un símbolo único (p. ej. `unibright`) no delate a nadie.
- **Retención:** si algo sensible llegó a publicarse, la única purga real es recrear el repo (BE-09).

### 5.5 Capacidad y cuotas (a contrastar con la documentación vigente; no contacté con los proveedores)
- **Yahoo (vía `yfinance`):** sin SLA ni cuota publicada; en la práctica se limita por IP. Orden de magnitud con este diseño de `max` completo: 280 peticiones/run para 58 símbolos. Con histórico incremental (BE-08) cada símbolo cuesta ~2 peticiones/run (1 mes de barras + 1 `info` diario) y 500 símbolos → ~1000 peticiones/run, ya en el borde de lo razonable desde un solo runner. Mitigación: repartir en lotes por hora con *jitter*, caché de crumbs, y una fuente alternativa de respaldo con licencia de redistribución (Stooq, Twelve Data, Alpha Vantage, EOD) si el servicio crece. **Y el riesgo de ToS (BE-04) manda más que la cuota.**
- **CoinGecko:** `coins/markets` y `simple/price` aceptan lotes (hasta 250); con ~5 llamadas por run y 4 runs/día (≈20/día) sobra holgura frente a un plan Demo, cuyos límites hay que comprobar. El coste real hoy son las ≤90 peticiones a Yahoo de «altcoin season» (`:1638-1654`), no CoinGecko.
- **GitHub:** minutos de Actions ilimitados en repos públicos, pero 6 h por job, y empujar cientos de MB al día a una rama que se fuerza es mala práctica. Pages tiene un límite de ancho de banda suave (orden de 100 GB/mes) a contrastar.
- **jsDelivr:** pensado para código; servir un API de datos con ~10 MB por cliente puede incumplir su política de uso razonable (no verificado). Con Pages/R2 el problema desaparece.

### 5.6 Hoja de ruta mínima (esfuerzo orientativo)
1. BE-01 (divisa/unidad) + BE-05 (continuidad) → imprescindible antes de nada. 1,5-2 días.
2. Histórico incremental + particionado por símbolo/año (mismo script, nuevo *writer*). 2-3 días.
3. Registro con regex, topes, tiers y estados. 1-2 días.
4. Alojamiento en Pages/R2 con *cache-control* por tipo. 0,5-1 día.
5. Decisión legal sobre fuente de datos (ToS). Pendiente del dueño.

---

## 6. Tests, cobertura y calidad

**Ejecución** (Python 3.13.17, `pytest 9.1.1`, `ruff 0.15.11`):

| Comprobación | Resultado |
|---|---|
| `pytest -q` | **332 passed**, ~16 s |
| Cobertura de líneas + ramas | `fetch_market_data.py` **87 %**, `sanity_check.py` **95 %**, total **89 %** (156 + 16 líneas sin cubrir) |
| `ruff check --select F scripts tests` (el de CI) | **FALLA** (3 errores, BE-02) |
| `ruff check` (reglas por defecto) | 39 incidencias (E702/E701/E731/E741) fuera del gate |
| Barrido temporal con `time-machine` | **2 bombas** (BE-10) |
| `main()` de extremo a extremo con mocks | Sí (`test_fetch_market_data.py:82-103`, `test_extra_D.py:318`) |

**Puntos fuertes:** los tests de workflows ejecutan en bash real los bloques `run:` con `git`/`curl`/`sleep` falsos (`test_workflows.py`), incluyendo contenido hostil del CDN. El validador tiene un caso por cada defecto histórico («A1»). Se comprueba que el parser de fechas de NAV del backend y el del validador coincidan. Los tests de `sanity_check` congelan «hoy» con un `autouse`.

**Huecos concretos (priorizados):**
1. **Sin tests para R2-R7** (cambio de unidad, series con ceros/duplicados/futuro, NAV ×10, strings hostiles): ver apéndice A (listos para pegar).
2. Líneas sin cubrir con lógica de negocio: VIX (`:1795-1803`), Fear & Greed cripto (`:1811-1815`), put/call de CNN (`:1836-1839`), reintentos de CoinGecko (`_cg_markets`, `:1562-1578`), **reescalado del histórico al NAV** (`:1499-1506`, el que evita el «−69 %» del comentario), `fetch_intraday_history` (`:408-419`), `build_benchmarks` (`:1856-1867`), `build_general_news` (`:1046-1057`).
3. **Ningún test de contrato con `yfinance` real**: todo se mockea con DataFrames a mano. `requirements.txt` permite cualquier 1.x (`<2`); una renombrada de `Ticker.info` o de columnas pasaría la CI y rompería en producción. Verificado hoy que `get_valuation_measures(freq, periods)` existe en 1.7.0. Propuesta: fixtures con respuestas reales (datos públicos) grabadas, y un job semanal `continue-on-error` que ejecute las firmas sin red (`inspect.signature`).
4. Sin umbral de cobertura en CI (`--cov-fail-under`).
5. Tests dependientes de PyYAML se saltan sin él (BE-16).
6. Sin test de que el README y los workflows no se contradigan (p. ej. «3 jobs + 1 de verificación» con cuatro filas; menor).
7. Python 3.14 solo en CI: no se ejecuta en local; cualquier incompatibilidad solo se vería allí.

---

## 7. Falsos positivos descartados

| Sospecha inicial | Por qué se descarta |
|---|---|
| `_drop_phantom_bars` es O(n²) con iloc en bucle | Medido: 0,37 s con 17 000 barras (el máximo real es 2382). |
| Inyección vía `${{ }}` en `run:` | Cero ocurrencias dentro de `run:` (grep); las expresiones están en `env:`/`with:`/`outputs:`; además lo hace cumplir un test. `_line()` impide inyectar comandos con `\n` en `::warning::`. |
| `pull_request_target` / `workflow_run` / `issue_comment` | No aparecen en ningún workflow. |
| Fuga de `FRED_API_KEY` por excepciones de `requests` | Se imprime solo `type(e).__name__` (`:864`, `:913`), con comentario explícito; Actions además enmascara secretos. |
| Token de escritura persistente en `.git/config` en `fetch`/`publish` | `persist-credentials: false`; el push usa un `git init` temporal sin remoto guardado (`:147-155`). |
| `fetch` puede alterar el validador | `publish` hace checkout de `scripts/` del repo; el validador no sale del artefacto. |
| Cuelgue indefinido de `yfinance` pese a que `socket.setdefaulttimeout` no aplique | `yfinance 1.7.0` fija timeouts propios (10 s en `history`, 30 s en `get`). Es BE-12 solo por el techo agregado. |
| Carrera entre runs / push parcial | `concurrency` serializa; `data.json` y `meta.json` van en el mismo commit y el push es atómico. |
| NaN/Infinity rompe el JSON del navegador | `sanitize_for_json`, `allow_nan=False` al escribir y `parse_constant` al validar. |
| `nav_stale` del feed se acepta sin más | El validador lo recalcula con `nav_date_iso` (`sanity_check.py:211-235`). |
| `downsample_history` usa `datetime.now()` naive | Desfase < 1 día en un recorte mensual: irrelevante. |
| El gate bloquea el feed bueno actual | No: el feed real pasa con rc=0 y sin avisos (`OK: 10 índices, 30 acciones`). |
| API `get_valuation_measures` inexistente en `yfinance` 1.7 | Existe con la signatura usada (`freq`, `periods`). |
| El historial git contiene secretos o ficheros personales | Búsqueda de claves/tokens/PEM: 0 coincidencias; `*.html`, `*backup*` y `data.json` nunca se versionaron. |

---

## 8. Lo bien hecho

- Separación de privilegios real: `fetch` (terceros, sin escritura) → artefacto no fiable → `publish` (sin terceros, validación con código del repo, único con `contents: write`). `permissions: {}` en `heartbeat`/`verify-cdn`.
- Acciones fijadas por SHA, `ubuntu-24.04` fijo, Dependabot con `cooldown`, `persist-credentials: false`, timeouts por job y secretos acotados al paso que los usa.
- `Alert` y `/fail` con motivo en Healthchecks (el texto pasa por `_line()` y se recorta a 900 caracteres, sin inyección en `$GITHUB_OUTPUT`).
- Degradación escalonada y honesta: fila anterior marcada `stale`, `history_stale`, perfiles reutilizables 60 días, FRED con `stale_since`, altcoin y staking con arrastre, corte de reintentos con interruptor, descarte de «barras fantasma» y de perfiles degradados.
- Los parseos de iShares son defensivos (cada campo en su `try`, rangos de plausibilidad, fecha del NAV sin ambigüedad `03/10` y sin depender del *locale*); si falla el scrape se cae al proxy y se avisa tras 2 runs.
- Límite de tamaño y de tiempo por feed RSS en un hilo daemon (un servidor que gotea bytes no puede alargar el run).
- La documentación es franca con sus riesgos asumidos (dependencias sin hash, qué es público, keepalive no confirmado).

---

## 9. Preguntas al dueño

1. ¿Qué parte del dashboard usa `price` de los símbolos `.L` y de `AMS.SW`/`ORSTED.CO`/`AMP.MC`/`NXT.MC` para valorar posiciones? ¿Convierte la divisa en el cliente y cómo trata `GBp`? (BE-01)
2. ¿El HTML del dashboard pinta `name`, `title`, `source` o `asset_name` con `innerHTML` o con `textContent`? (BE-07, BE-14)
3. ¿Aceptas que los ISIN de los fondos clase S y la lista cripto sean públicos, o prefieres privacidad real aunque implique mover el repo o recrearlo? (BE-03, BE-09)
4. ¿El generador de feed genérico va a ser un servicio con otros usuarios o solo tuyo + amigos? Eso decide la fuente de datos y el riesgo de ToS de Yahoo/iShares. (BE-04)
5. ¿Los commits de «Add files via upload» van directos a `main` por costumbre? ¿Te parece bien exigir PR y CI verde en `main`, y mover keepalive a otro mecanismo? (BE-02, BE-18)
6. El último `generated_at` es 23:14 UTC y no coincide con ningún cron: ¿fue un run manual o el programado llegó con ~3 h de retraso? ¿Con qué frecuencia ves retrasos así? (BE-20)
7. ¿Cuál es la configuración de Healthchecks (periodo/gracia) y cuántos `/fail` «degraded» reciben al mes? Para calibrar `Alert` frente a ruido.
8. ¿Cuánto vale el `size` del repo en GitHub tras ~1 mes de pushes? Confirmaría o descartaría BE-09. (Requiere consulta autenticada que yo no hice.)
9. ¿Tienes (o quieres) un `requirements.lock` con hashes? ¿Aceptas que Dependabot lo regenere? (BE-07)
10. ¿Qué plan de repercusión tendría el renombrar `news.positions`, o cambiar `schema` en `meta.json`, para el dashboard? (BE-03)

---

## 10. Resumen final (10 líneas)

1. Sin hallazgos Críticos; la arquitectura de privilegios y el validador previo al push son sólidos.
2. **BE-01 (Alto):** falta divisa/unidad en los precios; el feed real mezcla GBp y USD y ninguna regla detecta un salto ×100.
3. **BE-04 (Alto):** el `data.json` monolítico (158 KB/símbolo, tope ~110 símbolos) y los ToS de Yahoo impiden abrirlo a un feed multiusuario sin rediseño.
4. **BE-05/06 (Medio):** el validador no mira series ni continuidad, y se apaga la regresión si falla la descarga de `prev.json`.
5. **BE-02 (Medio):** `ruff --select F` falla hoy en `main` (3 errores) y la CI no corre en `push`.
6. **BE-03/09 (Medio):** fondos clase S, cripto y universo temático son públicos, y los feeds antiguos siguen accesibles por SHA.
7. **BE-07/08 (Medio):** dependencias sin hashes con secreto, sin esquema de strings, y una ráfaga de ≥280 peticiones a Yahoo sin tratamiento de 429.
8. **BE-10 (Bajo):** los tests fallarán el 2026-11-08 y el 2026-12-01 por fechas fijas, aunque nada cambie.
9. Ejecutado: `pytest` 332 OK (cobertura 89 %), validador OK sobre el feed real; no se contactó con ningún servicio real.
10. Prioridad: BE-01 + BE-05 + BE-02 (≈ 2 días) antes de cualquier ampliación del alcance o de los usuarios.

---

## Apéndice A — Reproducciones y tests propuestos

Los scripts se ejecutaron desde el scratchpad de la sesión (fuera del repo). Como tests para `tests/test_sanity_check.py` (usan `healthy()` y `run()` ya existentes; hoy **fallan**, por eso son los tests que cierran BE-05/06/07):

```python
def test_salto_de_unidad_x100_con_anterior_debe_bloquear():          # R2
    d, m = healthy(); prev, _ = healthy()
    f = d["funds"][0]; f["price"] *= 100
    for b in f["history"]: b["close"] *= 100
    errs, _ = run(d, m, prev)
    assert any("salto" in e or "continuidad" in e for e in errs)

def test_historico_con_close_no_positivo_o_nulo_debe_bloquear():     # R3
    d, m = healthy(); h = d["stocks"][0]["history"]
    h[10]["close"] = 0; h[11]["close"] = None
    assert run(d, m, None)[0]

def test_barra_con_fecha_futura_no_enmascara_historico_congelado():  # R5
    d, m = healthy()
    for k in sc.HISTORY_LISTS:
        for x in d[k]: x["history"][-1]["date"] = (TODAY + dt.timedelta(days=400)).isoformat()
    assert run(d, m, None)[0]

def test_nav_x10_respecto_al_anterior_debe_bloquear_o_alertar():     # R4
    d, m = healthy(); prev, _ = healthy(); d["your_funds"][0]["price"] *= 10
    errs, warns = run(d, m, prev)
    assert errs or any(isinstance(w, sc.Alert) for w in warns)

def test_clave_o_string_no_permitidos_deben_bloquear():              # R6
    d, m = healthy(); d["stocks"][0]["name"] = "<img src=x onerror=alert(1)>"; d["extra_blob"] = "A" * 1000
    assert run(d, m, None)[0]
```

Reproducción de BE-01/BE-13/BE-14 (parte de `repro_fetch.py`): `parse_stock_profile({... "currency":"GBp"})` → `cap_currency == "GBP"`; `fetch_symbol` sin clave `currency`; cierres `[…,-37,…,18]` → `change_pct == -148.65`; título doblemente codificado → `&lt;img …&gt;`.

Reproducción del barrido temporal: `pip install time-machine` y un plugin `autouse` con `time_machine.travel(now + N días, tick=True)`; `pytest --ignore=tests/test_workflows.py -p tm_plugin`.
