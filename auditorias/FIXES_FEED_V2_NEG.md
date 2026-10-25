# Feed v2: precios <= 0 (WTI negativo) — `fix/feed-v2-negative-prices`

Rama desde `main` (`243bb46`). Origen: primer run de `publish-feed-v2`; `sanity_check_v2` bloqueó **todo el sitio** con
«s/CL=F/d/2020.json: precio no finito o no positivo». El WTI (`CL=F`) tuvo precios negativos en abril de 2020: es un dato real de Yahoo, no corrupción.

## Causa (matiz sobre el diagnóstico inicial)
`bars_from_df` **ya descartaba** las barras con cierre <= 0. Lo que llegaba al fichero era otra cosa: una barra con **cierre positivo pero `open`/`low`/`high` <= 0**
(p. ej. el mínimo negativo del 21-04-2020 con cierre 10,01). `bars_from_df` solo exigía un cierre válido y rellenaba/reparaba el resto, y el validador exige
`o,h,l,c` finitos y > 0. Reproducido antes del arreglo con una barra sintética de ese tipo (el código antiguo la dejaba pasar con `l = -40.32`).
No tengo acceso a Yahoo desde esta sesión, así que **no he verificado los valores exactos de `CL=F`**; el arreglo no depende de ellos (cubre cualquier combinación).

## Qué cambia
* **`scripts/feed_v2.py`**: `split_bars(df, today)` -> `(barras, fechas_descartadas)` (`bars_from_df` queda como envoltorio). Se descarta toda barra con `open/high/low/close` no
  finito (NaN, None, ±inf, no numérico) o <= 0 **antes de escribir**. Una fila sin ningún precio (fila vacía de Yahoo) no es barra ni cuenta como descartada; si falta la
  *columna* Open/High/Low (no la celda) se rellena con el cierre, como antes.
* **Registro**: `health.json` -> `problems.dropped_bars` `{símbolo: {n, dates(<=10)}}`, `symbols.<SYM>.dropped` (fechas, acumuladas), `counts.dropped_bars` y `counts.suspect`;
  en el log, una línea-contador `~ SÍM: n barras descartadas en este run (...), m en el histórico`.
* **Tope**: si las barras descartadas **de todo el histórico** son > 3 o > 1 % de las del símbolo (`MAX_DROPPED_BARS`, `MAX_DROPPED_FRACTION`) el símbolo NO se publica: falla con
  `suspect_data`, entra en `problems.suspect` y `failed`, cuenta como fallo aunque haya caída global (como `unit_mismatch`/`frozen`), conserva lo anterior marcado `stale` y
  `sanity_check_v2` emite un aviso (alerta) con la lista. Como la descarga incremental (~1 mes) ya no ve las barras antiguas, las fechas descartadas se **persisten por símbolo en
  `health.json`** y se unen por fecha en cada run (una fecha que Yahoo luego da bien deja de contar; una descarga completa no cuenta dos veces la misma).
* **Fusión/continuidad**: el hueco no cambia nada: `rebase_ratio` y la continuidad del validador solo comparan fechas comunes, y los años cerrados se siguen copiando byte a byte.
* **`scripts/sanity_check_v2.py`**: **no se relaja** (sigue rechazando cualquier `o,h,l,c` <= 0). Solo se añaden al esquema de `health.json` las claves nuevas y el aviso de `suspect`.

## Tests (`tests/test_feed_v2_neg.py`, +`tests/test_sanity_check_v2.py`)
Barra suelta con NaN/None/inf/negativo/cero/open NaN/mínimo negativo; fila vacía y columna ausente; serie CL=F con las dos barras de abril de 2020 (publicada, anotada, validador
verde); una sola barra; 3 barras publica y 4 no; > 1 % en serie corta; `suspect` penaliza con caída global; reintento incremental (año cerrado byte a byte, continuidad verde);
rebase por split sin contar dos veces; acumulación entre runs (la cuarta barra, ya en otra ventana, bloquea); barra que Yahoo corrige deja de contar; **el validador sigue
rechazando** un sitio con cierre / mínimo / apertura negativos (parametrizado). Los ejemplos del contrato se regeneraron (`UPDATE_CONTRACT_DOCS=1`).

## Otros casos análogos en el registro
Revisé las 242 entradas (futuros de energía/metales/agrícolas, 22 pares de divisas, 24 índices, 18 cripto). Los precios negativos reales en Yahoo son un caso de
futuros (el WTI en abril de 2020); divisas, índices y metales no los tienen, y ningún tipo de interés va por Yahoo (los tipos van por FRED). El mecanismo es genérico: cualquier
símbolo con barras <= 0/no finitas se trata igual, sin lista de excepciones. Esta afirmación sale del conocimiento del dominio, no de datos de Yahoo (sin red en la sesión).

## Riesgos / a vigilar tras el primer run
* Se descartan ahora también las barras con `open/high/low` **NaN** en una celda presente (antes se rellenaban con el cierre), tal como se pidió. Si Yahoo diera muchas, un símbolo
  podría pasar a `suspect`; se vería en `problems.suspect` y en el aviso del validador.
* El 1 % se calcula sobre las barras que el run conoce (publicadas + descartadas): un símbolo muy joven con una sola barra mala (< 100 barras) quedaría como sospechoso.
* Un símbolo `suspect` se reintenta como cualquier fallo (`invalid` tras 5 seguidos, reintento semanal) y sigue bloqueado mientras las fechas persistidas superen el tope.
