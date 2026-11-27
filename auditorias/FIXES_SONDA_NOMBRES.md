# Sonda de símbolos: una línea por símbolo en el log del paso

Rama `claude/sonda-nombres` desde `main` en `8c8c856`. Nada fusionado, nada lanzado, ninguna fila activada: el PR lo abre el dueño tras revisar.

**Problema**: el run del 27-11 dio `sonda: 10 símbolos; ok 10`, pero el log del paso solo traía el recuento; el nombre, la bolsa y el último cierre de Yahoo iban
solo al resumen del job (`$GITHUB_STEP_SUMMARY`), que no se ve desde el móvil.

## 1. Qué cambia

Solo `scripts/symbol_probe.py` (+ `tests/test_symbol_probe_nombres.py`, 8 tests nuevos y este informe):

* `log_line(r)`: formatea UNA línea con lo que `probe` ya calcula, sin datos nuevos:
  `SÍMBOLO · VEREDICTO · registro: nombre (divisa/unidad) · yahoo: nombre · bolsa · tipo · divisa · última: fecha cierre`.
  Sin respuesta de Yahoo (`http_error`, `not_tried`) va el motivo en lugar de los campos de Yahoo. Lo que falta sale como `—`.
* `probe(..., emit=None)`: parámetro opcional; si se da, se llama una vez por resultado en cuanto se tiene (el log avanza símbolo a símbolo y, si el job
  muere por tiempo, queda lo ya probado). Sin `emit` se comporta igual que antes y devuelve lo mismo.
* `main` pasa `emit` a `print(log_line(r), flush=True)`. El recuento final `sonda: N símbolos; …` sigue siendo la última línea.
* Saneado: los campos de Yahoo ya salían de `clean`; el nombre del registro (que `clean` desfiguraría: «Ørsted» → «rsted») pasa por `_log_text`: quita `<`, `>`,
  `` ` `` y controles, `|` → `/` (igual que el resumen), colapsa espacios y conserva acentos.

**No se ha tocado**: `judge` (veredictos), umbrales, JSON de salida, resumen Markdown, código de salida, `registry.json`, listas, guardia de privacidad,
`symbol-probe.yml` (permisos de solo lectura, sin secretos, sin despliegue) ni `docs/AMPLIAR_UNIVERSO.md`. `git diff --stat origin/main`: `scripts/symbol_probe.py`
(+41 −4), el test nuevo y este informe.

Ejemplo con el registro real y **Yahoo simulado** (no es una sonda real; Yahoo no se ha consultado):

```
ENPH · OK · registro: Enphase Energy (USD/currency) · yahoo: (simulado) ENPH · SIM · EQUITY · USD · última: 2026-11-18 101.9
ORSTED.CO · UNIT · registro: Orsted A/S (DKK/currency) · yahoo: (simulado) ORSTED.CO · SIM · EQUITY · USD · última: —
ANT · NOT_TRIED · registro: Anthropic (USD/currency) · yahoo: — (tope de símbolos por run) · última: —
sonda: 10 símbolos; ok 2, unit 1, not_tried 7
```

(`ORSTED.CO` sale `UNIT` porque el simulado devolvía USD frente a DKK declarada; ilustra el formato, no un hallazgo.)

## 2. Comandos y salidas en bruto

Entorno: venv con `pip install --require-hashes -r requirements.txt -r requirements-dev.txt`.

**Rojo → verde → control negativo** (`pytest -q -p no:cacheprovider tests/test_symbol_probe_nombres.py tests/test_symbol_probe.py`):

```
rojo (tests escritos, sin implementar):  6 failed, 1 passed   (el que pasa es el que fija «el JSON y el recuento no cambian»)
verde:                                   23 passed            (16 de la sonda + 7; luego 24 con el test de «—»; solo el fichero nuevo: 8 passed)

negativos (se aplica un cambio a scripts/symbol_probe.py, se corre, se restaura):
  N1 solo se imprime la línea de los OK            -> 2 failed (no-ok alcanzables y not_tried)
  N2 sin sanear el texto (str(v) a pelo)           -> 1 failed (nombres raros)
  N2b campos de Yahoo vacíos como str(None)        -> sobrevivía (7 passed) -> añadido test_campos_que_yahoo_no_da_… -> 1 failed
  N3 el código de salida siempre 0                 -> 4 failed (2 nuevos + test_json_y_recuento + test_main_escribe_resumen… de los de siempre)
  N4 se quita el último cierre de la línea         -> 1 failed (línea del OK)
```

Un primer intento de N2 no se aplicó (el `|` chocó con el delimitador de `sed`) y dio «23 passed»: no cuenta como control; se repitió con Python y falló como debía.

**Final** (rama, todo restaurado):

```
$ python -m pytest -q -p no:cacheprovider
2452 passed, 1 warning in 228.74s (0:03:48)
$ ruff check --select F scripts tests
All checks passed!
```

2444 (F6) + 8 = 2452. El aviso es el de siempre (`feedparser`, `test_backend_fixes2.py`).

## 3. Qué demostró cada prueba y qué no

| Prueba | Demuestra | NO demuestra |
|---|---|---|
| `…linea_para_ok_con_todo_lo_necesario…` | la línea de un OK trae veredicto, nombre del registro, nombre/bolsa/tipo/divisa de Yahoo, fecha y cierre; salida 0 | que los valores sean los de Yahoo real (fake) |
| `…cada_veredicto_no_ok_alcanzable…` | `unit` (×2), `currency_missing`, `empty`, `frozen`, `http_error` dan una sola línea con su veredicto; salida 1; veredictos del JSON iguales | — |
| `…not_tried_tope…` | los símbolos no probados también dejan línea (con motivo) y no se piden de más | la rama del 429 con `emit` (cubierta por los tests existentes de `probe`, que no usan `emit`; `flush` se llama en las mismas ramas) |
| `…cubre_todos_los_veredictos…` | `log_line` acepta los 8 de `VERDICTS`, incluido `price_range` | que `price_range` se pueda dar de verdad: `split_bars(…, unit)` ya descarta antes los cierres fuera de rango, así que hoy ese veredicto parece **inalcanzable** (pasaría a `empty`); no se ha tocado |
| `…nombres_raros…` | `\| < > ` \r \n \t` no llegan a la línea, una línea por símbolo, nada empieza por `::` (no se interpreta como comando de Actions), acentos del registro conservados | que el log de Actions no vaya a tratar otro patrón raro; solo se ha probado lo anterior |
| `…json_el_resumen_y_el_recuento…` | claves del JSON idénticas, recuento final igual y último | — |
| `…sin_emit_se_comporta_igual…` | `probe` devuelve lo mismo con y sin `emit`, una llamada por resultado y en orden | — |
| `…campos_que_yahoo_no_da…` | `—` y nunca `None` | — |

**Lo que NO se ha hecho ni demostrado**

* **No se ha lanzado la sonda real** (el workflow solo corre sobre `main` y a mano): el formato se ha visto con Yahoo simulado. Tras fusionar, hay que lanzar
  «Symbol probe» una vez y mirar el log del paso.
* **Que un símbolo sea la empresa correcta lo decide una persona**: la línea solo pone el nombre del registro junto al de Yahoo para comparar a ojo; un `OK` sigue
  sin significar «es la empresa». En especial las 8 de F6 con grafía distinta de las ya existentes (`GOOG`/`GOOGL`, `RO.SW`/`ROG.SW`, `TCEHY`/`0700.HK`, `NVS`/`NOVN.SW`,
  `AZN`/`AZN.L`) y `ANT`, cuyo nombre en el registro no se ha verificado contra Yahoo.
* **Ninguna fila activada**, ni registro, ni listas, ni guardia de privacidad tocados.
* Datos que antes no estaban en el log y ahora sí: nombre del registro, nombre/bolsa/tipo/divisa de Yahoo (saneados), fecha y cierre. Son los mismos que ya iban al resumen y al
  artefacto JSON de un repo público; no hay ISIN, ids de ficha, URL de Supabase ni claves en código, tests ni este informe.

## 4. Pendientes

* `docs/AMPLIAR_UNIVERSO.md` § 2 paso 4 sigue diciendo que la sonda «da» nombre/bolsa en el resumen; no se ha cambiado (fuera de alcance). Convendría añadir «y en el log del paso».
* Lanzar la sonda real sobre las 10 filas `dormant` y decidir fila a fila (PR aparte).
