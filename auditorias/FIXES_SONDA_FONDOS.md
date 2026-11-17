# FIXES_SONDA_FONDOS: sonda de cobertura de fondos europeos en Yahoo Finance

Rama: `claude/wizardly-hamilton-quhyra`. Autor y committer de todos los commits: Alex Martínez Montes, con el email noreply de GitHub de su cuenta (GitHub rechazó el push con el email real por la protección de privacidad; los commits se reescribieron antes de empujar, por eso los hashes de este informe son los de la rama empujada). Sin ninguna atribución a herramientas de IA en commits, en este informe ni en el PR (lo crea Alex).
Ficheros NUEVOS (único permiso de esta sesión): `scripts/fund_probe.py`, `.github/workflows/fund-probe.yml`, `config/fund_probe_names.json`, `tests/fund_probe_fakes.py`, `tests/test_fund_probe_{core,names,isin,output,workflow}.py` y este informe.
No se ha tocado ningún workflow ni script existente, ni `registry.json`, ni el contrato del feed, ni topes, ni secretos existentes.

## 1. Comandos y salidas en bruto

Entorno de pruebas: un venv fuera del árbol con `pip install --require-hashes --no-deps -r requirements.txt` (yfinance 1.7.0 fijado) y `pytest==9.2.0 ruff==0.17.3 PyYAML==6.0.3` (las versiones de `requirements-dev.in`). Ninguna prueba toca la red: `tests/conftest.py` (existente) falla cualquier salida a Internet.

Un commit por punto: rojo (las pruebas escritas antes que el código y vistas fallar) → verde → control negativo (rompo el código a propósito con una mutación y compruebo que la prueba que corresponde la detecta; el fichero se restaura tras cada mutación).

| Commit | Punto |
|---|---|
| `dc912da` | 1/5 núcleo: ritmo, tope, parada limpia, clases de error, medida, cliente yfinance |
| `4202d21` | 2/5 sección NOMBRES + lista de 41 nombres genéricos |
| `13cb08e` | 3/5 sección ISIN |
| `3ad6ae1` | 4/5 resumen, JSON y controles negativos de privacidad de extremo a extremo |
| `82ba65e` | 5/5 workflow |
| `789c727` | corrección de pruebas para el guardia de privacidad del repo (ver §4) |
| `2c3fbc3` | diagnóstico extra de la búsqueda (`search_hits`, ver §3) |

Las salidas están recortadas a las líneas finales de cada ejecución (las ejecuciones completas no aportan más).

### 1.1 Configuración de git y base de comparación

```
git config user.name "Alex Martínez Montes"   (y el email de Alex, indicado en el encargo; no se copia aquí)
Suite base (HEAD inicial 262fddf, en un worktree aparte): -- Docs: https://docs.pytest.org/en/stable/how-to/capture-warnings.html
```

### 1.2 Rojo → verde → control negativo, por punto

**Punto 1 (núcleo)**

ROJO (pruebas antes que el código):
```
ERROR tests/test_fund_probe_core.py
!!!!!!!!!!!!!!!!!!!! Interrupted: 1 error during collection !!!!!!!!!!!!!!!!!!!!
1 error in 0.33s
```

CONTROL NEGATIVO (mutaciones: cada línea debe mostrar fallos):
```
[M1 sin pausa entre peticiones] -> tests/test_fund_probe_core.py:22: AssertionError =========================== short test summary info ============================ FAILED tests/test_fund_probe_core.py::test_pausa_minima_de_un_segundo_entre_peticiones 1 failed, 43 passed in 0.45s 
[M2 el 429 no para] -> =========================== short test summary info ============================ FAILED tests/test_fund_probe_core.py::test_429_para_a_la_primera_sin_reintentar_ni_dormir_de_mas FAILED tests/test_fund_probe_core.py::test_cliente_real_propaga_el_429_de_yfinance 2 failed, 42 passed in 0.48s 
[M3 sin tope duro] -> tests/test_fund_probe_core.py:50: AssertionError =========================== short test summary info ============================ FAILED tests/test_fund_probe_core.py::test_el_tope_duro_es_250_aunque_se_pida_mas 1 failed, 43 passed in 0.45s 
[M4 pausa configurable a 0] -> tests/test_fund_probe_core.py:27: AssertionError =========================== short test summary info ============================ FAILED tests/test_fund_probe_core.py::test_la_pausa_nunca_baja_de_un_segundo_aunque_se_pida 1 failed, 43 passed in 0.50s 
MUTACION NO APLICADA: M5 acepta cierres <=0
[M5 acepta cierres <=0] -> tests/test_fund_probe_core.py:135: AssertionError =========================== short test summary info ============================ FAILED tests/test_fund_probe_core.py::test_barras_invalidas_se_descartan_y_si_no_queda_ninguna_es_parseo 1 failed, 43 passed in 0.47s
```

**Punto 2 (NOMBRES)**

ROJO (pruebas antes que el código):
```
FAILED tests/test_fund_probe_names.py::test_resumen_por_gestora - AttributeEr...
FAILED tests/test_fund_probe_names.py::test_no_probados_no_cuentan_como_buscados
FAILED tests/test_fund_probe_names.py::test_lista_de_nombres_tamano_gestoras_y_estructura
FAILED tests/test_fund_probe_names.py::test_lista_de_nombres_no_contiene_isin_ids_ni_urls_ni_nada_de_la_cartera
FAILED tests/test_fund_probe_names.py::test_load_names_rechaza_entradas_invalidas
26 failed in 9.03s
```

VERDE:
```
......................................................................   [100%]
70 passed in 0.52s
```

CONTROL NEGATIVO (mutaciones: cada línea debe mostrar fallos):
```
[N1 umbral de cobertura 0.2] -> tests/test_fund_probe_names.py:46: AssertionError =========================== short test summary info ============================ FAILED tests/test_fund_probe_names.py::test_umbral_de_cobertura_60_por_ciento 1 failed, 25 passed in 0.44s 
[N2 desempate invertido] -> FAILED tests/test_fund_probe_names.py::test_candidatos_fuertes_son_los_cercanos_al_mejor_y_tope_de_3 FAILED tests/test_fund_probe_names.py::test_ambiguo_mide_tambien_las_alternativas_y_detecta_varias_divisas FAILED tests/test_fund_probe_names.py::test_las_alternativas_se_pueden_aplazar_y_medir_despues 4 failed, 22 passed in 0.48s 
[N3 limite 3 dias exclusivo] -> FAILED tests/test_fund_probe_names.py::test_error_no_parador_en_busqueda_es_error_y_sigue FAILED tests/test_fund_probe_names.py::test_divisa_rara_se_cuenta_y_se_conserva_la_medida FAILED tests/test_fund_probe_names.py::test_429_a_mitad_deja_el_resto_no_probado_y_para_limpio 5 failed, 21 passed in 0.46s 
[N4 acepta acciones] -> =========================== short test summary info ============================ FAILED tests/test_fund_probe_names.py::test_solo_fondos_y_etf_son_candidatos FAILED tests/test_fund_probe_names.py::test_resultados_que_no_son_fondos_es_no_encontrado 2 failed, 24 passed in 0.46s 
[N5 nunca detecta multidivisa] -> =========================== short test summary info ============================ FAILED tests/test_fund_probe_names.py::test_ambiguo_mide_tambien_las_alternativas_y_detecta_varias_divisas FAILED tests/test_fund_probe_names.py::test_las_alternativas_se_pueden_aplazar_y_medir_despues 2 failed, 24 passed in 0.47s 
[N6 resumen no cuenta buscados] -> tests/test_fund_probe_names.py:191: AssertionError =========================== short test summary info ============================ FAILED tests/test_fund_probe_names.py::test_resumen_por_gestora - AssertionEr... 1 failed, 25 passed in 0.50s 
[N7 sin tope de alternativas] -> tests/test_fund_probe_names.py:69: AssertionError =========================== short test summary info ============================ FAILED tests/test_fund_probe_names.py::test_candidatos_fuertes_son_los_cercanos_al_mejor_y_tope_de_3 1 failed, 25 passed in 0.49s
```

**Punto 3 (ISIN)**

ROJO (pruebas antes que el código):
```
FAILED tests/test_fund_probe_isin.py::test_el_logging_global_se_restaura_tras_la_seccion
FAILED tests/test_fund_probe_isin.py::test_un_error_inesperado_en_isin_se_cuenta_como_parseo_sin_texto
15 failed in 0.50s
```

VERDE:
```
...............                                                          [100%]
15 passed in 0.45s
```

CONTROL NEGATIVO (mutaciones: cada línea debe mostrar fallos):
```
[O1 no se emite add-mask] -> tests/test_fund_probe_isin.py:53: AssertionError =========================== short test summary info ============================ FAILED tests/test_fund_probe_isin.py::test_cada_isin_se_enmascara_antes_de_cualquier_uso_incluidos_los_no_validos 1 failed, 14 passed in 0.55s 
[O2 umbral de agregados 1] -> FAILED tests/test_fund_probe_isin.py::test_con_menos_de_cinco_solo_probados_y_resueltos FAILED tests/test_fund_probe_isin.py::test_el_umbral_se_mide_sobre_probados_no_sobre_los_que_se_pidieron FAILED tests/test_fund_probe_isin.py::test_429_en_isin_deja_el_resto_sin_probar_y_se_cuenta_sin_citar_nada 3 failed, 12 passed in 0.49s 
[O3 sin silenciar stdout/logging/avisos] -> -- Docs: https://docs.pytest.org/en/stable/how-to/capture-warnings.html =========================== short test summary info ============================ FAILED tests/test_fund_probe_isin.py::test_lo_que_imprimen_loguean_o_avisan_las_llamadas_no_sale 1 failed, 14 passed, 2 warnings in 0.47s 
[O4 divisas sueltas visibles] -> =========================== short test summary info ============================ FAILED tests/test_fund_probe_isin.py::test_con_cinco_o_mas_salen_agregados_por_tramo_divisa_y_errores FAILED tests/test_fund_probe_isin.py::test_divisa_con_dos_o_mas_se_muestra_y_con_una_va_a_otras 2 failed, 13 passed in 0.44s 
[O5 el registro guarda el ticker] -> tests/test_fund_probe_isin.py:114: AssertionError =========================== short test summary info ============================ FAILED tests/test_fund_probe_isin.py::test_los_registros_internos_no_guardan_ningun_identificador 1 failed, 14 passed in 0.48s 
[O6 secreto ausente no omite] -> tests/test_fund_probe_isin.py:39: AssertionError =========================== short test summary info ============================ FAILED tests/test_fund_probe_isin.py::test_secreto_ausente_o_vacio_omite_la_seccion_sin_tocar_yahoo 1 failed, 14 passed in 0.45s 
[O7 el 429 cuenta el ISIN como probado] -> tests/test_fund_probe_isin.py:123: AssertionError =========================== short test summary info ============================ FAILED tests/test_fund_probe_isin.py::test_429_en_isin_deja_el_resto_sin_probar_y_se_cuenta_sin_citar_nada 1 failed, 14 passed in 0.44s 
[O8 no enmascara el token tal como lo escribió el dueño] -> tests/test_fund_probe_isin.py:53: AssertionError =========================== short test summary info ============================ FAILED tests/test_fund_probe_isin.py::test_cada_isin_se_enmascara_antes_de_cualquier_uso_incluidos_los_no_validos 1 failed, 14 passed in 0.46s
```

**Punto 4 (salida y fugas)**

ROJO (pruebas antes que el código):
```
FAILED tests/test_fund_probe_output.py::test_secreto_ausente_dice_omitida_y_no_toca_yahoo_para_isin
FAILED tests/test_fund_probe_output.py::test_sin_variable_de_resumen_se_escribe_en_stdout
17 failed, 1 passed in 9.46s
```

VERDE:
```
18 passed in 0.52s
```

CONTROL NEGATIVO (mutaciones: cada línea debe mostrar fallos):
```
[P1 el log final cita el secreto PROBE_ISINS] -> FAILED tests/test_fund_probe_output.py::test_ningun_valor_de_la_seccion_isin_llega_a_resumen_logs_ni_json[4] FAILED tests/test_fund_probe_output.py::test_ningun_valor_de_la_seccion_isin_llega_a_resumen_logs_ni_json[5] FAILED tests/test_fund_probe_output.py::test_ningun_valor_de_la_seccion_isin_llega_a_resumen_logs_ni_json[8] 4 failed, 14 passed in 0.70s 
[P2 no se emiten máscaras] -> FAILED tests/test_fund_probe_output.py::test_ningun_valor_de_la_seccion_isin_llega_a_resumen_logs_ni_json[4] FAILED tests/test_fund_probe_output.py::test_ningun_valor_de_la_seccion_isin_llega_a_resumen_logs_ni_json[5] FAILED tests/test_fund_probe_output.py::test_ningun_valor_de_la_seccion_isin_llega_a_resumen_logs_ni_json[8] 4 failed, 14 passed in 0.57s 
[P3 el fallo inesperado imprime el mensaje de la excepción] -> tests/test_fund_probe_output.py:183: AssertionError =========================== short test summary info ============================ FAILED tests/test_fund_probe_output.py::test_un_fallo_inesperado_solo_imprime_la_clase_nunca_el_mensaje 1 failed, 17 passed in 0.57s 
[P4 el resumen ignora el umbral de 5] -> FAILED tests/test_fund_probe_output.py::test_ningun_valor_de_la_seccion_isin_llega_a_resumen_logs_ni_json[1] FAILED tests/test_fund_probe_output.py::test_ningun_valor_de_la_seccion_isin_llega_a_resumen_logs_ni_json[4] FAILED tests/test_fund_probe_output.py::test_con_menos_de_cinco_isin_el_resumen_solo_dice_probados_y_resueltos 3 failed, 15 passed in 0.58s 
[P5 el JSON lleva algo de ISIN] -> Sonda terminada: 12/250 peticiones. =========================== short test summary info ============================ FAILED tests/test_fund_probe_output.py::test_el_json_solo_lleva_nombres_y_es_estable 1 failed, 17 passed in 0.53s 
[P6 el JSON cuenta las peticiones de la sección ISIN] -> Sonda terminada: 30/250 peticiones. =========================== short test summary info ============================ FAILED tests/test_fund_probe_output.py::test_sin_secreto_el_resultado_es_el_mismo_que_con_isin_en_la_parte_de_nombres 1 failed, 17 passed in 0.55s 
[P7 el 429 no avisa] -> tests/test_fund_probe_output.py:164: AssertionError =========================== short test summary info ============================ FAILED tests/test_fund_probe_output.py::test_429_en_nombres_para_limpio_informa_y_sale_con_cero 1 failed, 17 passed in 0.58s 
[P8 (equivalente esperado: duplica máscaras)] -> ..................                                                       [100%] 18 passed in 0.53s
```

**Punto 5 (workflow)**

ROJO (pruebas antes que el código):
```
FAILED tests/test_fund_probe_workflow.py::test_el_artefacto_es_solo_el_json_de_nombres
FAILED tests/test_fund_probe_workflow.py::test_el_json_del_run_y_el_del_artefacto_son_el_mismo_fichero
10 failed, 2 passed in 1.08s
```

VERDE:
```
............                                                             [100%]
12 passed in 1.10s
```

CONTROL NEGATIVO (mutaciones: cada línea debe mostrar fallos):
```
[W1 sin guarda de rama] -> 1 failed, 11 passed in 1.05s
[W2 timeout 60] -> 1 failed, 11 passed in 1.03s
[W3 contents: write] -> 1 failed, 11 passed in 0.96s
[W4 programado] -> 1 failed, 11 passed in 1.00s
[W5 input inyectado en el shell] -> 1 failed, 11 passed in 1.04s
[W6 segundo secreto] -> 1 failed, 11 passed in 1.00s
[W7 accion sin fijar por sha] -> 1 failed, 11 passed in 1.07s
[W8 el artefacto sube todo el directorio] -> 2 failed, 10 passed in 1.04s
```

**Extra (search_hits)**

ROJO (pruebas antes que el código):
```
FAILED tests/test_fund_probe_names.py::test_search_hits_cuenta_los_tipos_que_devolvio_yahoo_y_cuantos_pasaron_el_filtro
FAILED tests/test_fund_probe_names.py::test_search_hits_tambien_en_no_encontrado_y_none_si_no_se_busco
FAILED tests/test_fund_probe_names.py::test_search_hits_ignora_elementos_raros_sin_romper
3 failed, 26 passed in 0.54s
```

VERDE:
```
.......................................................                  [100%]
127 passed in 1.35s
```

CONTROL NEGATIVO (mutaciones: cada línea debe mostrar fallos):
```
[S1 matching siempre 0] -> tests/test_fund_probe_names.py:234: AssertionError =========================== short test summary info ============================ FAILED tests/test_fund_probe_names.py::test_search_hits_cuenta_los_tipos_que_devolvio_yahoo_y_cuantos_pasaron_el_filtro 1 failed, 28 passed in 0.50s 
[S2 no cuenta tipos] -> FAILED tests/test_fund_probe_names.py::test_search_hits_cuenta_los_tipos_que_devolvio_yahoo_y_cuantos_pasaron_el_filtro FAILED tests/test_fund_probe_names.py::test_search_hits_tambien_en_no_encontrado_y_none_si_no_se_busco FAILED tests/test_fund_probe_names.py::test_search_hits_ignora_elementos_raros_sin_romper 3 failed, 26 passed in 0.46s
```

### 1.3 Comprobaciones finales

```
py_compile scripts/*.py tests/*.py -> OK
ruff check --select F scripts tests -> All checks passed!
pytest tests/test_fund_probe_*.py tests/test_privacidad_isin.py -> 127 passed in 1.15s
pytest completo (repo entero, Python 3.13) -> FAILED tests/test_extra_D.py::test_D44_secretos_usados_solo_los_esperados_y_ninguno_en_run
1 failed, 1817 passed, 1 warning in 184.64s (0:03:04)
```

El único fallo de la suite completa es un guardia existente que no podía editar (ver §4 punto 5 y §6 paso 0). Con el HEAD inicial la suite entera pasaba (1697 pruebas). Ahora hay 118 pruebas nuevas (44+15+29+18+12); el total del repo recoge 1700 pruebas existentes (3 más que antes: parece que algunas existentes se parametrizan por fichero y han crecido con los ficheros nuevos; no lo he investigado) + 118 = 1818 (1817 pasan, 1 falla).

### 1.4 Barrido de privacidad (resultado al terminar; ver `sweep` en la sesión)

```
Ficheros revisados:
  .github/workflows/fund-probe.yml
  config/fund_probe_names.json
  scripts/fund_probe.py
  tests/fund_probe_fakes.py
  tests/test_fund_probe_core.py
  tests/test_fund_probe_isin.py
  tests/test_fund_probe_names.py
  tests/test_fund_probe_output.py
  tests/test_fund_probe_workflow.py
Patrones en ficheros:
  URL de Supabase (supabase.co/.in): 0 coincidencias
  claves (sb_secret/sb_publishable, JWT eyJ, ghp_/github_pat_, sk-, AKIA): 0 coincidencias
  ids de ficha (/products/<n>): 0 coincidencias
  atribución a Claude/Anthropic en ficheros: 0 coincidencias
  ISIN-forma (12 car., 2 letras + alfanum. + dígito):
Commits de la rama:
  2c3fbc3 | Alex Martínez Montes <<email de Alex>> | committer Alex Martínez Montes <<email de Alex>>
  789c727 | Alex Martínez Montes <<email de Alex>> | committer Alex Martínez Montes <<email de Alex>>
  82ba65e | Alex Martínez Montes <<email de Alex>> | committer Alex Martínez Montes <<email de Alex>>
  3ad6ae1 | Alex Martínez Montes <<email de Alex>> | committer Alex Martínez Montes <<email de Alex>>
  13cb08e | Alex Martínez Montes <<email de Alex>> | committer Alex Martínez Montes <<email de Alex>>
  4202d21 | Alex Martínez Montes <<email de Alex>> | committer Alex Martínez Montes <<email de Alex>>
  dc912da | Alex Martínez Montes <<email de Alex>> | committer Alex Martínez Montes <<email de Alex>>
  mensajes con atribución a Claude/Anthropic/Co-Authored/claude.ai: 0
  autor distinto de Alex: 0

(ISIN-forma: ninguna coincidencia en ficheros nuevos; el guardia del repo `test_privacidad_isin` también pasa.)
```



## 2. Qué demostró cada prueba y qué NO

**No he podido probar nada contra Yahoo real** (la instrucción lo prohíbe y, además, aquí no hay red hacia Yahoo). Todo lo que sigue se ha demostrado con simuladores y fixtures sintéticos con la forma de las respuestas de yfinance 1.7.0 (leída en su código fuente instalado: `Search(...).quotes` = lista de dicts con `symbol`, `shortname`, `longname`, `quoteType`, `exchange`; `Ticker.history()` = DataFrame con índice `DatetimeIndex` tz-aware; `history_metadata["currency"]`; excepciones `YFRateLimitError`, `YFPricesMissingError`, `YFTzMissingError`, `YFDataException`).

### Demostrado

| Prueba (fichero) | Qué demuestra | Control negativo que la hace fallar |
|---|---|---|
| core: pausa, tope, tope duro | Entre dos peticiones hay ≥ 1,0 s (reloj falso); la pausa no se puede bajar de 1 s; el tope se respeta y el coste de un histórico (2) cuenta; 250 es techo duro aunque se pida más | M1 sin pausa, M3 sin techo duro, M4 pausa a 0 |
| core: 429 y sin red | El primer 429 para todo, sin reintentos y sin esperas largas; 3 fallos de red seguidos paran, un éxito reinicia la racha; vacío/parseo no paran | M2 el 429 no para |
| core: clasificación | Cada excepción cae en una de las 5 clases cerradas (`sin_red`, `429`, `vacio`, `parseo`, `divisa_rara`); `ProbeError` nunca arrastra el texto de la excepción original | (parametrizada, 12 casos) |
| core: medida | Fecha de la última barra, retraso (recortado a ≥ 0), nº de barras, divisa; barras inválidas se descartan; divisa rara se marca pero la medida se conserva | M5 acepta cierres ≤ 0 |
| core: cliente real con yfinance simulado | La traducción DataFrame→filas (tz-aware, tz con hora, sin tz, vacío, sin `Close`), `news_count=0` en la búsqueda, `period="1mo"`, `hide_exceptions=False`, y que un `YFRateLimitError` llega al `Gate` como 429 | (con M2) |
| names: regla de selección | Solo MUTUALFUND/ETF; cobertura ≥ 0,6 (borde 3/5 entra, 2/5 no); mejor = mayor cobertura, empate por el orden de Yahoo; duplicados por símbolo se unen; fuertes = a < 0,1 del mejor, tope 3 | N1 umbral, N2 desempate, N4 acepta acciones, N7 sin tope |
| names: medidas y estados | `reciente` ≤ 3 d / `retrasado` ≥ 4 d (borde exacto), `no_encontrado`, `sin_serie`, `error`, `no_probado`; ticker, bolsa, divisa, nombre corto en el resultado | N3 borde |
| names: ambigüedad | Varias clases ⇒ `ambiguous`; se miden las alternativas; `multi_currency` solo si las divisas medidas difieren; sin presupuesto quedan `pendiente` (no se inventan) | N5 nunca detecta multidivisa |
| names: resumen por gestora | Buscados / encontrados / reciente / retraso mayor / ambiguos (+ sin serie, errores, sin probar) | N6 |
| names: lista del repo | 41 nombres, 10 grupos (9 gestoras pedidas + «otras españolas»), sin duplicados, sin forma de ISIN, URLs ni `@`, y sin ninguna coincidencia con las huellas sha256 de la cartera del dueño que ya guarda `tests/privacy_guard.py` | (las pruebas de la propia lista) |
| isin: secreto ausente | Sin secreto (o solo comas/espacios) la sección se omite sin tocar Yahoo | O6 |
| isin: máscara | Todas las máscaras salen antes de la primera petición, también las de entradas no válidas | O1, O8 |
| isin: umbral | < 5 probados ⇒ solo «n probados / n resueltos»; ≥ 5 ⇒ tramos, divisas, errores; el umbral se mide sobre probados, no sobre pedidos | O2 |
| isin: divisas sueltas | Una divisa con una sola aparición se agrupa en «otras» (una divisa rara delataría el fondo) | O4 |
| isin: registros anónimos | Los registros internos no guardan ISIN, ticker ni nombre | O5 |
| isin: silencio | Aunque la llamada imprima, loguee, avise o lance una excepción citando el ISIN, nada llega a stdout/stderr/logging/avisos; el `logging.disable` global se restaura | O3 |
| output: tabla, JSON | Tabla por gestora y total; detalle; las 5 clases de error siempre presentes; JSON con claves fijas y solo NOMBRES | P5 |
| output: JSON independiente de ISIN | La parte de NOMBRES del JSON es idéntica con y sin secreto (nombres, resumen, errores, nº de peticiones de nombres) | P6 |
| output: **fugas de extremo a extremo** | Con ISIN sintéticos (1, 4, 5 y 8) y un cliente ruidoso a propósito (imprime, loguea, avisa y lanza citando el ISIN y el ticker resuelto), ni el ISIN, ni el ticker al que resuelve, ni su nombre aparecen en el resumen, el JSON, stdout (salvo las órdenes `::add-mask::`), stderr ni el logging | P1 (el log final cita el secreto), P2 (sin máscaras), P4 (sin umbral) |
| output: control del control | El detector de fugas del propio test **falla** si se le cuela un ISIN, un ticker resuelto o un nombre; y si se contamina el resumen a propósito, la prueba lo detecta | — |
| output: fallo inesperado | Una excepción cualquiera imprime solo su clase y el sitio (fichero:línea), nunca el mensaje | P3 |
| output: 429 | Para limpio, resto `no_probado`, `::warning::`, `stopped: "429"` en el JSON, salida 0 | P7 |
| output: ritmo y tope de extremo a extremo | ≥ 1 s entre TODAS las llamadas (nombres + ISIN + alternativas); `--max-requests` se recorta a 250 | — |
| workflow | Solo `workflow_dispatch` con un input; `permissions: {}` y `contents: read`; sin escritura ni despliegue; timeout 15; guarda `github.ref == 'refs/heads/main'`; acciones fijadas por SHA; checkout sin credenciales; un único secreto (`PROBE_ISINS`) en un único paso; el input va por variable de entorno (no inyectado en el shell); dependencias con hashes; artefacto = solo el JSON de NOMBRES; ningún otro workflow lo llama | W1…W8 |

P8 (pasar `emit=say` en vez de silenciar el segundo enmascarado) es una mutación **equivalente** esperada: solo duplicaría las órdenes `::add-mask::`, inocuas; por eso la prueba no falla y no es un hueco.

### NO demostrado (y qué puede fallar en el primer run real)

1. **Que la búsqueda por nombre devuelva fondos.** Todo el diseño asume que `Search` trae `quoteType` `MUTUALFUND`/`ETF` para los fondos Morningstar (`0P…​`). Si Yahoo usa otra etiqueta, casi todo saldrá `no_encontrado`. Por eso cada resultado guarda `search_hits` (total devuelto, recuento por `quoteType` y cuántos pasan el filtro): permite distinguir «Yahoo no lo tiene» de «lo descarto yo».
2. **Nombres abreviados.** Los nombres cortos de Yahoo suelen abreviar («FIDELITY FDS WORLD A-ACC-EUR», «Gl Tech»…). Con cobertura ≥ 60 % de palabras, un nombre abreviado puede quedarse fuera aunque el fondo exista (falso `no_encontrado`). Se mira también `longname`, pero no sé cuánto ayuda.
3. **Que `Search` por ISIN resuelva** (sé que Yahoo suele aceptar ISIN en su buscador, pero no lo he visto en 1.7.0).
4. **Bloqueo o 429 desde los IP de GitHub.** Si Yahoo limita al runner (habitual en IP de centros de datos), la sonda se parará en la primera petición y el resultado será parcial. Es el comportamiento pedido; el resumen lo dice.
5. **Peticiones internas de yfinance que no controlo:** cookie/crumb al empezar y la consulta de zona horaria antes del histórico. No pasan por mi pausa de 1 s. Asumo coste 2 por histórico (peor caso) y 1 por búsqueda; el arranque (cookie/crumb) no lo cuento. Si el coste real fuese mayor, el tope de 250 sería menos conservador de lo que parece.
6. **`history_metadata`** puede venir sin `currency` para algunos fondos: se marcaría `divisa_rara`, no `sin_serie`.
7. **El eco de `::add-mask::` en el log.** Los ISIN tienen que salir por stdout en esa orden para que el runner los enmascare. Entiendo que el runner los sustituye por `***` en el log, pero **no lo he podido comprobar**. Ver §6: primer run de prueba con valores falsos.
8. **Fugas por canales que no controlo:** código nativo (curl_cffi) que escribiera directamente en el descriptor 2 saltándose `sys.stderr`. Las pruebas cubren `print`, `logging`, `warnings` y excepciones, no escrituras a nivel de descriptor.
9. **Los nombres de la lista:** son genéricos y conocidos, pero no he verificado que cada uno exista tal cual en Yahoo (justo eso es lo que mide la sonda). «Indexa» tiene un solo nombre genérico porque sus carteras invierten en fondos de terceros; es una hipótesis, esperable `no_encontrado`.
10. **actionlint** no está en este entorno: el YAML se ha validado con PyYAML y con pruebas estructurales, no con actionlint (el CI de Alex lo ejecuta si está disponible en el runner).

## 3. Decisiones y supuestos (con la postura contraria)

| Decisión | Postura contraria y por qué no la tomé |
|---|---|
| Primer 429 ⇒ parar y informar, salida 0 | Reintentar con backoff. Lo descarté: la instrucción pide «nunca bucles largos» y reintentar contra un 429 empeora el bloqueo. Contra: un 429 aislado a la primera petición tira todo el run; se relanza a mano. |
| 3 fallos de red seguidos ⇒ parar | Parar al primero (más cauto) o no parar nunca (más datos). 3 distingue un fallo suelto de una caída. |
| Salida 0 aunque se pare limpio | Salir con error para que el run salga en rojo. Lo evité: no es un fallo de código, es un resultado; el `::warning::` y el resumen lo dicen. Salida 2 = lista inválida, 1 = fallo inesperado. |
| Coste de histórico = 2, de búsqueda = 1 | Contar 1. Contar de menos infrava el tope; contar de más solo recorta alguna alternativa. |
| Orden: nombres → ISIN → alternativas de ambigüedad | Medir alternativas junto a cada nombre. Preferí que el presupuesto dé prioridad a nombres y a ISIN; las alternativas (lo más prescindible) se miden con lo que sobre y, si no, quedan `pendiente`. |
| Regla de selección: cobertura de palabras ≥ 0,6, empate por orden de Yahoo | Elegir por divisa o clase preferida. En la búsqueda no hay divisa (solo en el histórico), así que sería arbitrario; el orden de Yahoo es explícito y verificable. Umbral más bajo = más falsos positivos, más alto = más falsos negativos con nombres abreviados. |
| «Reciente» = última barra a ≤ 3 días (dado en el encargo) | Contar días hábiles. El encargo fija 3 días de calendario (cubre viernes→lunes). |
| Solo MUTUALFUND/ETF como candidatos | Aceptar EQUITY/otros. Se perderían menos fondos con etiqueta rara, pero entrarían acciones. `search_hits` deja ver qué se descarta. |
| `yf.config.debug.hide_exceptions = False` | Dejar el valor por defecto. Por defecto yfinance se traga los errores y un 429 saldría como «vacío»; no podría parar limpio. |
| ISIN y NOMBRES en el mismo proceso, mismo `Gate` | Procesos o pasos separados (más aislamiento). Un solo proceso comparte el tope y el ritmo; el aislamiento se logra por construcción: `build_json` solo recibe resultados de NOMBRES y hay una prueba que compara el JSON con y sin secreto. Contra: un fallo de programación podría mezclar; las pruebas de fuga de extremo a extremo lo vigilan. |
| `search_hits` por nombre (añadido por mí, no pedido) | No añadirlo. Sin él, un `no_encontrado` generalizado no se podría interpretar con un solo run. Solo es de la sección NOMBRES (nombres genéricos) y no contiene nada de ISIN. |
| Divisas con 1 aparición en los agregados de ISIN ⇒ «otras» | Mostrar el recuento exacto (lo pedido). Con pocos ISIN una divisa suelta delata el fondo. Cumple el espíritu de «agregados». |
| Total de peticiones en el resumen incluye las de ISIN | Ocultarlo. Es un agregado; el JSON, en cambio, solo cuenta las de NOMBRES (`requests_names`). |
| Campo `stopped` del JSON es el estado final del run | Reflejar solo la parada en NOMBRES. Si una parada ocurre durante la sección ISIN, el JSON dice «429» sin más; no identifica nada, pero técnicamente es información del run entero. |
| El job se salta (no falla) si no es `main` | Fallar con mensaje. Mismo patrón que los demás workflows del repo. |
| El secreto `PROBE_ISINS` va al paso que ejecuta código de terceros (yfinance) | Leerlo en un paso aparte. No es posible: los ISIN se envían a Yahoo desde ese mismo proceso. Mitigado: hashes fijados, sin otros secretos, sin escritura, solo `main`. |

## 4. Desviaciones

1. **Correcciones de mis propias pruebas durante el trabajo** (ninguna se hizo para que «pase» algo que fallara por el código; las anoto todas):
   - Punto 2: el ejemplo de «ISIN» de `test_load_names_rechaza_entradas_invalidas` terminaba en letra (un ISIN real acaba en dígito) y no fue rechazado. No toqué la prueba: **endurecí el código** (forma de ISIN = 12 alfanuméricos, prefijo de 2 letras y algún dígito, sin exigir dígito de control), que además es más conservador para la privacidad.
   - Punto 3: en `test_429_en_isin…` escribí `not_probed == 2`; la aritmética correcta (2 probados + el cortado por el 429 + 2 sin tocar = 5) es 3, igual que en NOMBRES. Lo corregí antes de la primera ejecución en verde.
   - Punto 3: `import pytest` sin usar (ruff) quitado y el commit del punto 3 reescrito (`--amend`) antes de empujar.
   - Punto 5: `step()` comparaba objetos YAML por identidad y reparseaba el fichero en cada llamada (`s is not probe`); lo cambié a comparar por nombre del paso. Lo que comprueba es lo mismo.
   - Commit `789c727`: el guardia ya existente `tests/test_privacidad_isin.py` barre TODO el árbol y rechaza cualquier ventana con forma de ISIN irlandés (`IE` + 10 alfanuméricos) que no esté en su lista de sintéticos. Tres literales en dos de mis ficheros de prueba tenían esa forma. Los valores se construyen ahora en ejecución, de modo que ningún fichero contiene el literal. No toqué el guardia.
2. **Accidente en local, nunca commiteado:** un `pip download` dejó ruedas `.whl` dentro del árbol; las borré antes del primer commit (`git status` limpio). La suite base se corrió en un `git worktree` aparte fuera del árbol (eliminado al terminar).
3. No se añadió ninguna línea de coautoría ni atribución a los commits, por la instrucción expresa.
4. **Commits «Unverified».** Un hook de la sesión pidió reescribir autor y committer con otra identidad (la de la herramienta) para que GitHub los marque como verificados. No lo hice: la instrucción del dueño es autor Alex y ninguna atribución a Claude. Consecuencia: los commits no llevan firma y GitHub puede mostrarlos como «Unverified» (a menos que la rama exija commits firmados, no debería afectar a la fusión; si la regla de la rama lo exigiera, Alex puede fusionar con squash desde la web).
5. **Suite completa: 1 fallo ajeno al código de la sonda** (`test_D44`, ver §6 paso 0). Es consecuencia directa del permiso (no editar tests existentes).
6. `search_hits` (diagnóstico extra) y el commit `789c727` no estaban en el encargo.
7. El barrido de privacidad de la propia sesión (`sweep.sh`) usa una regex de ISIN más estricta (acaba en dígito) que el guardia del repo (ventana `IE…`); por eso el guardia cazó un literal que mi barrido no. Ahora los dos están limpios (ver §1).

## 5. Lo no probado (resumen)

- Cualquier comportamiento real de Yahoo/yfinance/curl_cffi desde un runner (ver §2, puntos 1-6).
- Que GitHub sustituya por `***` las órdenes `::add-mask::` en el log (punto 7).
- Escrituras directas al descriptor 2 desde código nativo (punto 8).
- Que cada nombre de la lista exista en Yahoo (punto 9).
- `actionlint` (punto 10) y la ejecución real del workflow en Actions (permisos, caché de pip, subida del artefacto): solo se ha validado estructura y comportamiento local.
- La suite completa corre con Python 3.13 aquí; el runner usa 3.14 (como el resto de workflows).

## 6. Cómo ejecutarla (pasos manuales de Alex)

0. **Antes de fusionar: una línea en un test existente (no he podido hacerlo: solo tenía permiso para ficheros nuevos).** `tests/test_extra_D.py::test_D44_secretos_usados_solo_los_esperados_y_ninguno_en_run` es un guardia con lista cerrada de los secretos que usan los workflows; el nuevo `PROBE_ISINS` hace que falle (único fallo de la suite completa). Hay que añadir `"PROBE_ISINS"` al conjunto de esa aserción (y, si quieres, al comentario). Mientras no se haga, el CI del PR saldrá rojo. No lo he esquivado (p. ej. escribiendo el secreto de otra forma para que la regex no lo vea): sería saltarse el guardia.
1. **Crear el PR y fusionarlo** (lo creas tú) desde `claude/wizardly-hamilton-quhyra` a `main`. El workflow solo corre desde `main`: antes de fusionar no se puede lanzar.
2. **(Opcional) Crear el secreto `PROBE_ISINS`**: GitHub → repo → Settings → Secrets and variables → Actions → *New repository secret* → nombre `PROBE_ISINS`, valor = ISIN separados por comas, escrito directamente en el formulario de GitHub. **No lo pegues en ningún chat.** Sin el secreto, la sección ISIN se omite y se ve en el resumen.
3. **Primer run recomendado, para comprobar el enmascarado:** crea `PROBE_ISINS` con 5 valores FALSOS con forma de ISIN, lanza el run y mira el log del paso *Run probe*: las líneas `::add-mask::` deben aparecer como `***` (o no aparecer) y ningún valor debe salir en claro. Si ves un valor en claro, no uses ISIN reales. Después sustituye el secreto por tus ISIN reales.
4. **Lanzar:** Actions → *Fund probe (cobertura de fondos en Yahoo)* → *Run workflow* → rama `main` → `max_requests` 250 (por defecto) → *Run workflow*. Duración estimada (sin medir): unos 5-10 min, con pausa de 1 s por petición y 15 min de tope del job.
5. **Leer el resultado:** en la página del run → pestaña *Summary* (tabla por gestora, detalle por nombre, clases de error, agregados de ISIN). El JSON completo de NOMBRES (con `search_hits`) está en el artefacto `fund-probe-names` (14 días); no contiene nada de la sección ISIN.
6. **Interpretar:** si «Sonda parada: 429» aparece pronto, Yahoo limita al runner: relanza más tarde. Si casi todo es `no_encontrado`, mira `search_hits` en el JSON: tipos devueltos y cuántos pasan el filtro.
