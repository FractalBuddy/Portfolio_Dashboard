# F5 (backend público): listas en el índice, símbolos nuevos y ETF de referencia

Rama `claude/explorar-f5-publico` desde `main` en `69d5195`. **No depende** de `claude/fix-revision-publico` (sin fusionar). Nada fusionado: el PR lo
abre el dueño y se revisa antes de cualquier merge.

| # | Commit | Qué |
|---|---|---|
| 1 | `F5 punto 1: listas publicadas en index.json…` | mecanismo de listas (registro, generador, validador, contrato) + tests |
| 2 | `F5 punto 2: ENPH y ORSTED.CO al registro v2 en dormant` | 2 filas `dormant` + tests; 2 tests anteriores cambiados a propósito |
| 3 | `F5 punto 3: referencia S&P 500 en EUR = SXR8.DE ya activa…` | solo un test de anclaje (no hay cambio de registro) |
| 4 | `F5 punto 4: topes fijados por test…` | `MAX_LIST_SIZE` atado a `MAX_REGISTRY_SYMBOLS` + tests de topes |
| 5 | este informe | — |

**Resumen honesto**: el punto 1 y el 4 están hechos. Del punto 2 entran **2 de 9** símbolos y del punto 3 **0 de 2** filas nuevas. El resto no entra por la
regla de privacidad del registro público o porque ya estaba. Lo decidió el dueño durante la sesión, a la vista del conflicto (ver «Lo que NO se hizo»).

## 1. Listas en `index.json`

### Diseño

* La **definición** de una lista va en la raíz de `registry.json`, clave opcional `lists`. La leen ya el generador (job `fetch`) y el validador (job `publish`),
  así que **no hace falta tocar ningún workflow** ni añadir otro fichero que el job `publish` tenga que sacar del repo:

  ```json
  "lists": {"<lista>@AAAA-MM-DD": {"source": "https://…", "as_of": "AAAA-MM-DD", "size": N, "symbols": ["SYM1", "…"]}}
  ```

  `symbols` va en orden de rango (posición 1 = 1.º).
* **Reglas** (`feed_v2_common.list_problems`, llamada desde `registry_problems`). Si una lista falla, el **registro entero** es inválido: la CI se pone en rojo
  y el generador y el validador no arrancan, así que no hay publicación a medias. Es el mismo criterio que con una fila mala:
  * id `<lista>@AAAA-MM-DD` con fecha real (misma regex que la anotación por fila); de 1 a `MAX_LISTS` = 10 listas;
  * campos exactamente `source`, `as_of`, `size`, `symbols`;
  * `source` es una URL `https://` (host + ruta opcional; sin espacios, `<`, `>` ni controles; ≤ 300 caracteres). `http`, `ftp` o vacía, rechazada;
  * `as_of` **igual** a la fecha del id;
  * `size` entero de 1 a `MAX_LIST_SIZE` (= `MAX_REGISTRY_SYMBOLS`). **Lista vacía = rechazada**;
  * **completa**: `len(symbols) == size`;
  * cada símbolo **está en el registro** (misma grafía) y no se repite (tampoco cambiando mayúsculas y minúsculas);
  * coherencia con la anotación por fila (`symbols[i].lists`): cada miembro la lleva, ninguna fila ajena la lleva y, si la fila da un rango no nulo, es su posición.
* **Publicación** (`feed_v2.Run.build_index`): `index.json.lists = {id: {"id", "source", "as_of", "symbols"}}` (`feed_v2_common.index_lists`). **Si no hay
  listas, la clave no aparece** y `index.json` sale idéntico byte a byte al de hoy.
* **Validador del job `publish`** (`sanity_check_v2._check_index`): `index.json.lists` tiene que ser **exactamente** `index_lists(listas del registro del repo)`.
  No admite listas de más, listas quitadas ni listas cambiadas (símbolo, fuente). Además hay un esquema estricto (lista blanca, `source` empieza por `https://`).
  Hacen falta las dos barreras porque el `index.json` lo escribe el job `fetch`, que ejecuta código de terceros.
* Contrato (`docs/CONTRATO_FEED_V2.md` §3 `index.json`) y `docs/AMPLIAR_UNIVERSO.md` documentan la clave. Es un cambio **aditivo**: un cliente que no conozca
  `lists` la ignora.

### Estado real: ninguna lista publicada

`registry.json` **no lleva `lists` en la raíz**. `top50-mcap@2026-11-13` sigue existiendo solo como anotación en **7 filas** (incompleta: no hay un top 50
verificado y fechado; ver `docs/universo/TOP50_MCAP_2026-11-13.md`, «Huecos»). Por tanto, **no se publica**. Lo fijan dos tests:
`test_el_registro_real_no_publica_ninguna_lista_y_el_top50_sigue_solo_como_anotacion` y el control negativo
`test_control_negativo_el_top50_real_tal_cual_no_se_puede_publicar`: si alguien definiera la lista con esas 7 filas, el registro se rechaza por «lista incompleta (7 de 50)».

**Pendiente (otra sesión, en el chat)**: aportar el top 50 completo con fuente `https` y fecha de corte. Entonces: (1) añadir en `registry.json` las filas que falten
(en `dormant`, con la sonda) y anotar `lists` en las 50; (2) añadir la definición en la raíz; (3) el test del registro real que hoy exige «sin `lists`» cambia a
propósito. Si faltan miembros que todavía no tienen fila, la lista no se puede definir: los 50 símbolos tienen que estar en el registro (pueden estar en `dormant`).

### Rojo → verde → control negativo

* **Rojo**: `tests/test_listas_indice_F5.py` (44 tests) contra el código de `main`: 36 fallan y 8 pasan. Los 8 que pasan son casos de rechazo, que ya se
  rechazaban porque la raíz no admitía `lists`.
* **Verde**: 44/44.
* **Control negativo**: quitando la comprobación de `lists` en `_check_index` y la de completitud en `list_problems`, fallan **9** (las listas incompletas, el top 50
  real, `load_registry_doc` y 4 de los 5 de manipulación del `index.json`). El quinto (`fuente_http`) sigue en verde porque lo para el esquema estricto: es la segunda barrera.

Casos cubiertos: lista completa (válida y publicada de extremo a extremo, generador + validador, también por `main()` leyendo el fichero), sin listas (sin clave),
vacía, incompleta, sin fuente, sin fecha, `http`/`ftp`/vacía/con `<` o espacio, fecha distinta del id, id sin fecha o mal formado, símbolo fuera del registro,
repetido, campos de más, anotación por fila incoherente, e `index.json` con lista no registrada, símbolo cambiado, fuente `http`, lista quitada o listas sin
registro.

## 2. Símbolos nuevos (pedidos 9; entran 2)

| Símbolo | Nombre | Divisa / unidad | Mercado (sufijo) | Tier | Estado |
|---|---|---|---|---|---|
| `ENPH` | Enphase Energy | USD / currency | sin sufijo → EE. UU. | warm | `dormant` |
| `ORSTED.CO` | Orsted A/S | DKK / currency | `.CO` → Nasdaq Copenhague | warm | `dormant` |

* **Nombres**: **no verificados en Yahoo** (este entorno no llega a Yahoo). Salen de la lista pública del feed v1 (`fetch_market_data.STOCKS`, que v1 ya publica). El
  de Ørsted va transliterado a ASCII («Orsted A/S»), como el resto del registro, que es todo ASCII («Nestle»). La sonda informa el nombre de Yahoo:
  **compáralo antes de activar**.
* Divisa y unidad deducidas del sufijo, **sin verificar en Yahoo**. Ninguna cotiza en subunidad.
* Sin `country` ni `lists` (no salen de ninguna lista).
* **No se activa ninguno.** Siguen sin pedirse a Yahoo ni tener ficheros (test e2e). Control negativo: activadas, sí se piden.
* Tests que cambian **a propósito** (marcados `REQUISITO CAMBIADO`): `test_symbol_probe.py::test_seleccion_por_defecto_…`, que daba por hecho que el registro real no
  tenía filas `dormant`, y `test_universo_top50.py::test_solo_las_filas_nuevas_…`, que exigía `active` en todas las filas no top 50.
* Control negativo del guardia de privacidad: metiendo una de las acciones de nicho en el registro, `test_el_registro_publico_no_lleva_nada_del_dueno` falla, como debe.

### Cómo activarlas (lo hace el dueño, tras el merge)

1. Actions → **«Symbol probe»** → *Run workflow* sobre **`main`** (el job solo corre en `refs/heads/main`). Con `select = dormant` (por defecto) prueba justo
   estas 2; también vale `symbols = ENPH,ORSTED.CO`. `max_symbols` por defecto (60) basta. No publica nada ni usa secretos.
2. Para cada `OK`: comprobar que el nombre que informa Yahoo es la empresa esperada y la divisa es `USD` / `DKK`.
3. PR que cambia `"status":"dormant"` → `"active"` de los `OK`, añade una fila a «Historial de activaciones» de `docs/AMPLIAR_UNIVERSO.md` y ajusta
   `tests/test_simbolos_F5.py` (hoy exige `dormant`) y `tests/test_symbol_probe.py`.
4. Tras 2-3 runs, revisar `health.json` (`problems.failed`, `problems.unit_mismatch`, `run.yahoo_rate_limited`).

## 3. ETF de referencia del benchmark (pedidos 2; filas nuevas 0)

* **`SXR8.DE`** (S&P 500 en EUR) **ya estaba** en el registro: `active`, `warm`, EUR/currency, publicado desde antes de F5. No se añade otra fila (sería un duplicado
  que el registro rechaza) ni se pasa a `dormant` (dejaría de publicarse un símbolo que hoy se publica). Lo fija
  `test_la_referencia_sp500_en_eur_es_una_sola_fila_activa_y_ya_existia`. **No he comprobado si Yahoo lo sirve hoy**: lo dice `health.json` del sitio publicado
  (no accesible desde aquí).
* **El ETF de referencia MSCI World en EUR pedido no se añade y no se nombra en este informe.** Su símbolo coincide con una huella de
  `tests/privacy_guard.py::PROXY_HASHES` (proxies EUR de los fondos del dueño, que según `FIXES_FEED_V2.md` §9 van a su payload personal, no al registro público), y
  `test_el_registro_publico_no_lleva_nada_del_dueno` lo rechazaría. **No está verificado en Yahoo.**
  *Corrección de lo que dije en la sesión*: dije que escribir ese símbolo «en cualquier sitio del árbol» rompía los guardias. Era exagerado. El guardia por
  huella vigila `registry.json` (y los ficheros del payload personal en `test_personal_v2`), y el símbolo **ya aparece** en un test anterior a F5
  (`tests/test_fund_facts.py`, en `main`). La regla que lo excluye es la del **registro público**, y esa sigue en pie.
* Si el benchmark necesita una referencia MSCI World en EUR, opciones (decide el dueño): leerla de su payload personal (Supabase), donde ya viven sus proxies;
  usar la línea del registro que ya existe en otra divisa con conversión (`SWDA.L`, GBp); o cambiar la regla de privacidad a sabiendas. Esto último **no** lo he hecho.

## 4. Límites (antes → después)

Topes (sin cambios, ahora fijados por `tests/test_limites_F5.py`): `MAX_REGISTRY_SYMBOLS` = 400, `MAX_BYTES["manifest"]` = `MAX_BYTES["manifest_root"]` = 600 000 B,
aviso (`::warning::`, no bloquea) desde el 50 % (`HEADROOM_WARN_FRACTION`). Cambio: **`MAX_LIST_SIZE` = `MAX_REGISTRY_SYMBOLS`**. El test de topes encontró que el
primer commit lo había dejado en 500, por encima del tope del registro.

| Medida | Antes (249 filas) | Después (251 filas, real) | Plan original (260 filas) | Tope | Fuente |
|---|---|---|---|---|---|
| Filas del registro | 249 (62 %) ⚠ | **251 (63 %)** ⚠ | 260 (65 %) ⚠ | 400 | `registry.json` (medido) |
| Símbolos de Yahoo **activos** | 231 | **231** (las 2 nuevas están `dormant`) | — | — | `registry.json` |
| `manifest.json` (banco sintético, todo activo) | 302 611 B (50,4 %) ⚠ | 305 181 B (50,9 %) ⚠ *si se activan las 2* | 315 406 B (52,6 %) ⚠ | 600 000 | `tests/bench_manifest_escala.py --n 249 251 260 --no-download` |
| `manifest-root.json` (banco) | 24 087 B (4 %) | 24 291 B (4 %) | 25 102 B (4 %) | 600 000 | idem |
| `index.json` (banco) | 41 311 B (34 %) | 41 648 B (35 %) | 43 137 B (36 %) | 120 000 | idem |
| Sitio (banco) | 40,3 MB / 2 837 ficheros | 40,6 MB / 2 861 | 42,0 MB / 2 957 | 120 MB / 12 000 | idem |

⚠ = por encima del 50 %: el validador ya avisaba antes de F5 (registro y manifest). **Ningún tope se rompe.** El aviso de `MAX_REGISTRY_SYMBOLS` con el registro real lo
comprueba un test (`MAX_REGISTRY_SYMBOLS 63 % (251 de 400)`); con 199 filas no avisa (control negativo).

Matices:
* Mientras las filas estén `dormant`, el `manifest.json` **real** no cambia: no tienen ficheros. El banco supone todos los símbolos activos y su serie sintética
  pesa algo más que la real. El publicado se estimó en ≈ 294 KB en informes anteriores; **no lo he medido** (Pages no es accesible desde aquí).
* Una lista de 50 símbolos en `index.json` añadiría ≈ 0,6 KB (**estimado**, no medido): no mueve ningún porcentaje.

## 5. Feed v1

`scripts/fetch_market_data.py` **no se ha tocado** (`git diff 69d5195 --stat` no lo lista). Solo se lee desde los tests, como antes.

## Verificación

* `pytest` completo: **2 413 passed** (línea base en `main`: 2 358; +55 de F5) y `ruff check --select F scripts tests`: **All checks passed**.
* `tests/privacy_guard.py`: en `main` es un **módulo** de huellas que importan los tests. No tiene punto de entrada ni lee `PRIVATE_DENYLIST` (ese mecanismo no está en
  `main`). Sus comprobaciones por huella e ISIN corrieron dentro de `pytest`. **La comprobación con `PRIVATE_DENYLIST`: NO EJECUTADA** (la variable no está definida
  y `main` no tiene ese paso). No es un «pasado».
* **Barrido de privacidad del diff** (`git diff 69d5195`, solo líneas añadidas), con las funciones de `privacy_guard.py` y expresiones regulares:

  | Qué | Resultado |
  |---|---|
  | ISIN (forma genérica `AA` + 9 + dígito) | 0 |
  | ISIN irlandés fuera de los sintéticos (`isin_leaks`) | 0 |
  | ids de ficha `/products/<n>` (`product_id_leaks`) | 0 |
  | símbolos `0P…` | 0 |
  | claves (`sb_…`, `eyJ…`, `ghp_`, `sk-…`) | 0 |
  | correos | 0 |
  | huellas `PROXY_HASHES` / `CRYPTO_HASHES` | 0 |
  | acciones de nicho de v1 | 0 |
  | «supabase» | 1: la palabra dentro de la lista negativa de un test (`for bad in (…, "supabase", …)`) |
  | URLs | solo `example.org` en tests y la regex del validador |

## Supuestos

* El ETF de referencia S&P 500 en EUR pedido es la fila `SXR8.DE` que ya existe. No se crea otra.
* «Mercado» se deduce del sufijo de Yahoo, pero el registro no tiene campo de mercado (y no lo he añadido): queda implícito en el símbolo y en la divisa.
* Una lista es «completa» cuando tiene exactamente los `size` símbolos declarados. El validador **no puede** comprobar que la fuente diga eso: la fuente y la fecha
  son la trazabilidad para la revisión humana.

## Lo que NO se hizo, y por qué

* **7 de los 9 símbolos del punto 2 no se añaden**. Son exactamente las «acciones de nicho» de v1 (`fetch_market_data.STOCKS[10:15] + STOCKS[-2:]`) que
  `FIXES_FEED_V2.md` §9 dejó fuera del registro público a propósito porque identifican al dueño, y que `test_el_registro_publico_no_lleva_nada_del_dueno` prohíbe.
  No se nombran aquí para no volver a asociarlas al dueño en un documento.
* **El ETF MSCI World en EUR del punto 3** (ver §3).
* **No he activado nada ni lanzado la sonda** (solo el dueño, desde Actions, tras el merge).
* **No he publicado ninguna lista**: no hay ninguna completa.
* No he creado el PR ni fusionado nada.

## Pendiente

1. **Top 50** con fuente `https` y fecha (chat, otra sesión). Después, filas que falten + definición en la raíz (§1).
2. **Sonda** de `ENPH` y `ORSTED.CO` y PR de activación (§2).
3. **Decisión del dueño** sobre la referencia MSCI World en EUR del benchmark y sobre las 7 acciones de nicho: o fuera del registro público (como hoy), o
   cambiar la regla de privacidad a sabiendas.
4. Medir el `manifest.json` **real** tras la próxima activación. Ya está por encima del 50 % de su tope; el formato 2 por trozos existe para cuando haga falta.
