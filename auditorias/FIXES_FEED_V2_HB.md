# Feed v2: heartbeat, cuándo se avisa, motivos en health.json — `claude/gracious-feynman-veh29q`

Rama desde `main` (`049775a`). **No existía `fix/feed-v2-heartbeat`** (ni local ni en el remoto), así que no había nada que continuar; el trabajo va en la rama de la sesión
(`claude/gracious-feynman-veh29q`), que arrancaba exactamente en `main`. Sin acceso a Yahoo, CoinGecko ni Healthchecks desde la sesión: todo se probó con datos simulados y `curl` falso.

## A) Secreto de Healthchecks (`publish-feed-v2.yml`, `update-data.yml`, `supabase-keepalive.yml`)
* Antes de usarlo, el secreto se **limpia**: se quitan del principio y del final espacios, tabuladores, `\r`, `\n`, comillas `"` `'` y acentos graves `` ` `` (en cualquier combinación y
  orden, p. ej. `` `'url'`\n ``). Es un bucle de `case` en bash puro (sin `sed`/`tr`, que perderían un `\n` interior sin avisar).
* Después debe cumplir, **entero** (`[[ =~ ]]` sobre toda la cadena, no línea a línea): `^https://hc-ping\.com/[A-Za-z0-9_-]+(/[A-Za-z0-9_-]+)*/?$`. Si no, `::warning::` **sin el valor**,
  `exit 0` y **ni una llamada a `curl`** (un secreto raro nunca llega a `curl`, ni siquiera para que falle y escriba la URL en el log).
* Se aplica a los tres heartbeats/pings y, por coherencia, a los pasos de aviso informativo (`/log`) de `verify-cdn` y `verify` (mismo secreto, mismo riesgo). En el keepalive de
  Supabase la limpieza se hace una vez antes de `hc_ping`; el aviso conserva el prefijo `HC_SUPABASE_URL`.
* Efecto lateral aceptado: un `HC_*` apuntando a un dominio propio de Healthchecks (autoalojado) ya **no funciona**; era lo pedido (regex estricta). Hay que ampliar la regex si algún día se usa.
* **Tests** (`tests/test_workflows.py`, 5 scripts × valores): 14 secretos «sucios» que deben acabar en la URL limpia exacta (y la llamada a `curl` usa solo esa); 26 valores hostiles
  (`http://`, `hc-ping.com.evil.com`, `evil.com/hc-ping.com`, `?x=`, `#`, `user@`, `@evil`, `;touch`, `$(touch)`, `` `touch` ``, `-o`/`--config`, `\n` o `\r\n` interiores con una segunda URL, no ASCII,
  mayúsculas en el host, `/../..`...) que deben dar `::warning::`, `exit 0`, **cero** llamadas a `curl` con el secreto, el marcador secreto ausente del log y ningún fichero creado.
  Se comprobó que **fallan contra los workflows originales** (178 fallos). Los tests antiguos usaban `https://hc.test/...`: ahora `https://hc-ping.com/...`.

## B) Cuándo avisa v2 a Healthchecks (`sanity_check_v2.py`, `_health_alerts`)
El heartbeat sigue mandando `/fail` si `alerts.txt` no está vacío; lo que cambia es **qué cuenta como alerta** (`Alert`). Ahora solo:
1. **(i)** fallo de `fetch`, `publish` o del validador: no pasa por las alertas, el job falla y el heartbeat ya manda `/fail` (sin cambios).
2. **(ii)** quedan sin publicar **más del 10 %** de los símbolos con `status: active` en el registro (`UNPUBLISHED_ALERT_FRACTION = 0.10`, estricto: justo el 10 % no avisa).
   «Sin publicar» = está en `problems.failed`, o es una cripto `not_listed`, o es de Yahoo y no tiene `latest.json`; se toma el mayor entre ese conjunto y `counts.failed` (la lista de `health.json` se recorta a 40).
3. **(iii)** caída global de Yahoo: `counts.outage` (fallan la mayoría de los intentados, ≥ 10 intentos) o `yahoo_abandoned` (429 sostenido).

Pasan de alerta a **solo `::warning::`** (con el detalle en `health.json`): `unit_mismatch`, `suspect`, un `failed` suelto (o un cold), presupuesto de tiempo agotado, muchos `stale`, `crypto/latest.json`
ausente o reutilizado, cripto `not_listed`, rebase. **Decisión mía a revisar:** traté el 429 sostenido (`yahoo_abandoned`) como «caída global de Yahoo» (iii), no como «lo demás»; si prefieres que sea solo aviso,
es borrar una línea. Los símbolos pausados a mano (`dormant` en el registro) no cuentan como activos.
* **Tests** (`tests/test_sanity_check_v2.py`): un símbolo con cada clase de fallo → sin alerta y con aviso; varios pero ≤ 10 %; exactamente 4 de 40 → no, 5 de 40 → sí; sin `latest.json` cuenta;
  recorte a 40 con `counts.failed`; pausados no cuentan; cripto no listada; caída global; presupuesto/unit/suspect solo aviso; tipos inesperados en `health.json` no rompen; e2e con el generador
  (1 de 40 falla → `alerts.txt` vacío; 6 de 40 → alerta «6 de 40»).

## C) `health.json`: estructura y motivos cerrados
* `problems.unit_mismatch`: `{símbolo: {registro: "GBp/subunit", yahoo: "GBP"|null}}`. `yahoo` es solo la divisa que informó Yahoo si tiene forma de 3-4 letras; si no la informó o no tiene esa forma, `null`. Sin texto libre.
* `problems.failed`: `{símbolo: motivo}` con la clase cerrada `empty`, `http_error`, `timeout`, `parse`, `unit`, `suspect`; nunca el texto de una excepción. Mapeo: sin datos suficientes **y símbolo congelado >45 días** → `empty`
  (la lista `problems.frozen` sigue separando los congelados); excepción de Yahoo → `timeout` si es un timeout, `parse` si es `ValueError/KeyError/TypeError/...`, `http_error` en el resto; error interno inesperado → `parse`.
* Validador (`HEALTH_SPEC`) y `docs/CONTRATO_FEED_V2.md` actualizados (tabla de formas); ejemplos regenerados con `UPDATE_CONTRACT_DOCS=1`. **Cambio de contrato no compatible hacia atrás** para quien lea esas dos listas
  como arrays (el dashboard de v2 aún no existe; el feed v1 no se toca).
* **ROG.SW** no se tocó: su motivo de fallo (clase) aparecerá en `problems.failed` en el próximo run. La clase dirá si es `http_error`/`empty`/`unit`..., pero **no** el detalle; para ese detalle queda la línea del log.

## D) Barras descartadas: motivo, y la regla era demasiado ancha
* **Sí era demasiado ancha**: `split_bars` descartaba la barra si cualquiera de open/high/low/close era no finito o ≤ 0, así que el 2020-04-21 (cierre +10,01, apertura y mínimo negativos) se perdía.
  Ahora **solo se descarta por el cierre**: `close_missing` (vacío/ausente), `close_not_finite`, `close_le_0`. Un open/high/low malo con cierre bueno se **sustituye por el cierre** y el rango se recalcula con los valores válidos
  (21-04-2020: `o=10,01 h=13,0 l=10,01 c=10,01`), de modo que el validador (que no se relaja) sigue conforme. **Esa reparación no queda anotada en `health.json`** (solo cuenta el descarte); si la quieres visible, es un campo más.
* `split_bars` devuelve `{fecha: motivo}`; `health.json`: `problems.dropped_bars[SYM].reasons` y `symbols.<SYM>.dropped_why` (persistido entre runs, como las fechas; lo anotado por la versión anterior sale como `unknown`).
* **Efecto en el tope**: como ya no se descartan las barras con solo open/high/low malos, hay menos barras contadas para `suspect` (3 / 1 %). Revierte lo que pidió el PR anterior sobre NaN en celdas open/high/low.
* **Tests** (`tests/test_feed_v2_neg.py`): serie sintética de CL=F (20-04 fuera por `close_le_0`, 21-04 dentro con sus valores reparados) a nivel de función y de run completo (`health.json`, validador verde, incremental, rebase);
  cada motivo; columna `Close` ausente; motivos que sobreviven al run incremental; `unknown` para estado antiguo. **No he verificado los valores reales de CL=F en Yahoo** (sin red): la serie es sintética con la forma descrita.

## E) Registro y criptos
* **VUSA.L y VWRL.L**: Yahoo informa `GBP` (confirmado por el dueño; no verificado desde la sesión). Registro cambiado a `currency: "GBP"`, `unit: "currency"` (antes `GBp`/`subunit`). El precio se publica ya en libras, sin divisor.
* **Cripto no listada**: una cripto `active` del registro que no aparece en el lote de CoinGecko queda en `health.json.symbols.<SYM>` como `{status: "dormant", reason: "not_listed", fail_streak: 0, tier}`, con `index.json`
  `status: "dormant"`, una línea en el log y un `::warning::` del validador. No bloquea nada, no es alerta (salvo que sean > 10 % del registro, caso (ii)), y se recupera sola cuando la moneda reaparece. Si CoinGecko falla
  entero no se marca ninguna (no se sabe). El registro no se modifica (el test de registro exige `status: active` en todas).
* **TON no se ha sustituido.** El generador empareja las criptos por **símbolo** (`BTC`, no por id de CoinGecko), y no hay en el repo ni en los tests ningún lote real de CoinGecko: sin red no puedo verificar qué ids/símbolos
  hay hoy en el top 250. Proponer una sustitución de memoria sería adivinar; solo queda `TON` marcada `dormant`/`not_listed` en el próximo run. Para elegir sustituta, contrastar un `/coins/markets?per_page=250` real (símbolo y id) antes de añadirla.
* Para que los tests sigan siendo realistas, el lote simulado (`tests/v2_world.py`) ahora incluye BTC y ETH (el registro de pruebas los lleva).

## Verificación
`python -m pytest -q` y `ruff check --select F scripts tests` (lo que corre `ci.yml`): ver el resultado en el PR. Lo único que no se probó es una ejecución real en Actions (el repo y los secretos son del dueño).

## Qué mirar tras el primer run con este PR
* Que `health.json.problems.failed` traiga clases y `unit_mismatch` el objeto `{registro, yahoo}`: ahí está la respuesta a VUSA.L/VWRL.L y ROG.SW.
* Que un run con 1-2 símbolos caídos ya no ponga el check de Healthchecks en rojo, y que un secreto mal pegado deje un `::warning::` en vez de un fallo de `curl`.
