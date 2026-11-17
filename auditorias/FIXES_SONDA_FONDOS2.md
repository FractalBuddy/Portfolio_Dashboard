# FIXES_SONDA_FONDOS2: sección GENÉRICOS (ISIN públicos) en la sonda de fondos

Rama: `claude/tender-allen-8i5eqi`. Autor y committer de todos los commits de esta sesión: Alex Martinez, con el email noreply de GitHub que ya usan sus commits en el repo (comprobado con `git log 51a4c0f..HEAD`: todos con ese autor y committer, y ningún mensaje con líneas de coautoría ni de atribución a herramientas de IA). Sin atribución a herramientas de IA en commits, en este informe ni en el PR (lo crea Alex).
No he lanzado el workflow ni he tocado ningún secreto. Todo lo de Yahoo de este informe es **simulado**: no hay ningún dato real de Yahoo aquí.

Ficheros NUEVOS: `config/fund_probe_isins_generic.json`, `tests/test_fund_probe_generic_list.py`, `tests/test_fund_probe_generic.py`, `tests/test_fund_probe_generic_output.py` y este informe.
Ficheros MODIFICADOS: `scripts/fund_probe.py`, `.github/workflows/fund-probe.yml`, `tests/fund_probe_fakes.py` (el falso acepta `period`), `tests/test_fund_probe_core.py`, `tests/test_fund_probe_output.py`, `tests/test_fund_probe_workflow.py` (mis pruebas de la sesión anterior, ajustadas al tope 400, a los 2 inputs y a los 2 artefactos) y **`tests/test_privacidad_isin.py` (un guardia existente: ver §4.1)**. `test_extra_D.py` (D44) y los tests de workflows existentes **no se han tocado** y siguen en verde.

## 1. Comandos y salidas en bruto

Entorno: venv fuera del árbol con `pip install --require-hashes --no-deps -r requirements.txt` (yfinance 1.7.0) y `pytest==9.2.0 ruff==0.17.3 PyYAML==6.0.3` (las versiones de `requirements-dev.in`). Python 3.13 (el runner usa 3.14). Ninguna prueba toca la red (`tests/conftest.py` lo impide).

Un commit por bloque: rojo (pruebas antes que el código, vistas fallar) → verde → control negativo (mutaciones del código, restaurado tras cada una).

| Commit | Bloque |
|---|---|
| `2ee747b` | 1/5 lista pública de ISIN, dígito de control ISO 6166, excepción acotada del guardia de privacidad |
| `88790e0` | 2-3/5 sección GENÉRICOS (similitud, regla de elección, medida, agregados), `--sections`, JSON y resumen, separación de PROBE_ISINS, tope duro 400 |
| `e7475f5` | 4/5 workflow: input `sections`, tope hasta 400, segundo artefacto `fund-probe-generic` |
| `e215faa` | la prueba del tipo de ticker distingue «0P» al principio del símbolo (la mutación G13 no se detectó la primera vez, ver 1.3) |
| (este informe) | 5/5 informe |

### 1.1 Base de comparación

```
HEAD inicial 51a4c0f, en un git worktree aparte:
1855 passed, 1 warning in 169.43s (0:02:49)
```

### 1.2 Rojo (antes del código), tal como salió

```
bloque 1  tests/test_fund_probe_generic_list.py            -> 22 failed, 3 passed in 0.50s
bloque 2  tests/test_fund_probe_generic.py                 -> 33 failed in 0.66s
bloque 3  tests/test_fund_probe_generic_output.py          -> 27 failed, 1 passed in 11.37s
bloque 4  tests/test_fund_probe_workflow.py                -> 9 failed, 5 passed in 1.03s
```

### 1.3 Controles negativos (mutaciones del código; cada línea DEBE decir DETECTADA)

Script de mutaciones fuera del árbol; cada mutación se aplica, se corren los 8 ficheros de pruebas de la sonda y se restaura el fichero. Salida en bruto, recortada a la primera prueba que falla de cada línea (`+más` = fallan más; el resumen de pytest de la línea es completo):

```
[G1 similitud = solapamiento (divide por el menor)] -> DETECTADA | 1 failed, 206 passed in 1.64s | test_fund_probe_generic.py::test_similitud_es_jaccard_de_palabras_significativas
[G2 similitud sin quitar palabras vacías] -> DETECTADA | 4 failed, 203 passed in 1.83s | test_fund_probe_names.py::test_tokens_sin_acentos_ni_puntuacion_ni_palabras_vacias (+más)
[G3 elección: liquidez antes que 0P] -> DETECTADA | 31 failed, 176 passed in 1.96s | test_fund_probe_generic.py::test_regla_prefiere_0P_aunque_otra_sea_mas_liquida_y_Yahoo_la_ponga_antes (+más)
[G4 elección: sin el paso 0P] -> DETECTADA | 9 failed, 198 passed in 1.91s | test_fund_probe_generic.py::test_regla_prefiere_0P_aunque_otra_sea_mas_liquida_y_Yahoo_la_ponga_antes (+más)
[G5 elección: la divisa nunca filtra] -> DETECTADA | 2 failed, 205 passed in 1.64s | test_fund_probe_generic.py::test_regla_entre_varias_0P_usa_la_divisa_del_nombre_esperado
[G6 elección: el empate se resuelve en silencio (sin marcar tie)] -> DETECTADA | 7 failed, 200 passed in 1.61s | test_fund_probe_generic.py::test_regla_sin_divisa_en_el_nombre_o_sin_candidato_con_esa_divisa_el_criterio_no_filtra (+más)
[G7 elección: bolsas desconocidas ordenadas como líquidas] -> DETECTADA | 5 failed, 202 passed in 1.86s | test_fund_probe_generic.py::test_regla_liquidez_conocida_si_no_hay_0P_ni_divisa (+más)
[G8 elección: alfabético en vez de orden de Yahoo] -> DETECTADA | 3 failed, 204 passed in 1.69s | test_fund_probe_generic.py::test_regla_sin_criterio_emite_todas_las_empatadas_y_mide_la_primera_de_Yahoo (+más)
[G9 agregado: ≥250 pasa a >250] -> DETECTADA | 1 failed, 206 passed in 1.66s | test_fund_probe_generic.py::test_agregado_resueltos_250_barras_y_recientes
[G10 agregado: «reciente» ≤4 d] -> DETECTADA | 6 failed, 201 passed in 1.94s | test_fund_probe_names.py::test_limite_del_retraso_3_dias_es_reciente_y_4_es_retrasado (+más)
[G11 agregado: por bolsa cuenta también los no resueltos] -> DETECTADA | 1 failed, 206 passed in 1.59s | test_fund_probe_generic.py::test_agregado_por_tipo_de_ticker_y_por_bolsa_sobre_los_resueltos
[G12 agregado: «cambiarían» cuenta los que no cambian] -> DETECTADA | 1 failed, 206 passed in 1.78s | test_fund_probe_generic_output.py::test_resumen_trae_agregados_reglas_y_empates
[G13 agregado: tipo de ticker 0P por substring] -> NO DETECTADA | 207 passed in 1.71s | 
[G14 búsqueda de GENÉRICOS pide 1 mes] -> DETECTADA | 1 failed, 206 passed in 1.80s | test_fund_probe_generic.py::test_la_busqueda_es_por_isin_y_el_historico_de_un_ano
[G15 tope duro vuelve a 250] -> DETECTADA | 4 failed, 203 passed in 1.70s | test_fund_probe_core.py::test_el_tope_duro_es_400_aunque_se_pida_mas (+más)
[G16 pausa mínima 0,5 s] -> DETECTADA | 5 failed, 202 passed in 1.62s | test_fund_probe_core.py::test_pausa_minima_de_un_segundo_entre_peticiones (+más)
[G17 el 429 no para] -> DETECTADA | 10 failed, 197 passed in 1.76s | test_fund_probe_core.py::test_429_para_a_la_primera_sin_reintentar_ni_dormir_de_mas (+más)
[P1 la sección privada corre ANTES que GENÉRICOS] -> DETECTADA | 7 failed, 200 passed in 1.70s | test_fund_probe_generic_output.py::test_la_salida_detallada_de_genericos_es_identica_con_y_sin_el_secreto (+más)
[P2 el JSON de GENÉRICOS recibe el agregado privado] -> DETECTADA | 22 failed, 185 passed in 1.77s | test_fund_probe_output.py::test_el_resumen_trae_tabla_por_gestora_detalle_y_clases_de_error (+más)
[P3 `stopped` de GENÉRICOS se toma al final del run] -> DETECTADA | 2 failed, 205 passed in 1.72s | test_fund_probe_generic_output.py::test_identica_tambien_con_presupuesto_justo_porque_los_genericos_van_antes_que_los_privados[30]
[P4 el bloque del resumen de GENÉRICOS cita el ticker de la sección privada] -> DETECTADA | 3 failed, 204 passed in 1.88s | test_fund_probe_generic_output.py::test_ningun_valor_de_la_seccion_privada_llega_a_la_salida_detallada[1] (+más)
[W1 input sections: por defecto names] -> DETECTADA | 1 failed, 206 passed in 1.58s | test_fund_probe_workflow.py::test_input_sections_es_una_eleccion_cerrada_con_both_por_defecto
[W2 artefacto genérico apunta al fichero de nombres] -> DETECTADA | 2 failed, 205 passed in 1.53s | test_fund_probe_workflow.py::test_dos_artefactos_nombres_y_genericos_cada_uno_un_unico_json
[W3 el input va dentro del run] -> DETECTADA | 1 failed, 206 passed in 1.63s | test_fund_probe_workflow.py::test_los_inputs_van_por_variable_de_entorno_nunca_dentro_del_run
[W4 timeout vuelve a 15] -> DETECTADA | 1 failed, 206 passed in 1.56s | test_fund_probe_workflow.py::test_timeout_de_20_minutos_y_guarda_de_rama_main
[W5 se pasa el secreto al paso de subida] -> DETECTADA | 3 failed, 204 passed in 1.80s | test_fund_probe_workflow.py::test_un_unico_secreto_en_un_unico_paso_y_nada_mas (+más)
```

La primera pasada dejó **una NO DETECTADA (G13)**: la prueba del tipo de ticker no distinguía «0P» al principio del símbolo de «0P» en cualquier sitio. Añadí el caso (`X0P1.F` es «otras») y se repitió; en la misma repetición volví a correr P2 porque mi primera mutación de P2 rompía además el modo `names` (daba 22 fallos por un error mío en la mutación, no por la prueba de separación):

```
[G13 agregado: tipo de ticker 0P por substring] -> DETECTADA | 1 failed, 206 passed in 1.64s | test_fund_probe_generic.py::test_tipo_de_ticker_0P_etf_otras
[P2 el JSON de GENÉRICOS recibe el agregado privado] -> DETECTADA | 7 failed, 200 passed in 1.59s | test_fund_probe_generic_output.py::test_la_salida_detallada_de_genericos_es_identica_con_y_sin_el_secreto (+más)
```

### 1.4 Comprobaciones finales (comandos del CI)

```
python -m py_compile scripts/*.py tests/*.py   -> OK
ruff check --select F scripts tests   -> All checks passed!
python -m pytest -q   -> 1944 passed, 1 warning in 166.46s (0:02:46)
(Python 3.13; base HEAD inicial 51a4c0f: 1855 passed. Diferencia: +89 = 86 pruebas en 3 ficheros nuevos (25 lista, 33 núcleo, 28 salida/separación) + 1 del cliente real con `period` + 2 del workflow.)
```

### 1.5 Formato del resumen con Yahoo SIMULADO (reparto inventado: no son datos de Yahoo)

Ejecutado con la lista real de 43 ISIN (en este informe la columna ISIN se abrevia a «IE…xxxx» para no tropezar con el guardia de privacidad del repo) y un cliente falso que devuelve cosas inventadas, solo para ver el formato y la longitud del resumen (`--sections generic`):

```
## Sonda de cobertura de fondos europeos en Yahoo Finance

Fecha: 2026-11-16 · yfinance 1.7.0 · peticiones: 115/400 · «reciente» = última barra a ≤ 3 días

### Sección NOMBRES: omitida (--sections generic).

### Sección GENÉRICOS (ISIN públicos, cobertura de Yahoo por ISIN)

| ISIN | Nombre esperado | Estado | Ticker | Tipo | Bolsa | Divisa | Nombre en Yahoo | Similitud | Candidatos | Barras 1 a | Última barra | Retraso (d) |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| IE…Y983 | iShares Core MSCI World UCITS ETF USD Acc | no_encontrado | — | — | — | — | — | — | 0 | — | — | — |
| IE…R087 | iShares Core S&P 500 UCITS ETF USD Acc | reciente | T1.DE | etf | GER | EUR | iShares Core S&P 500 UCITS ETF USD Acc (Acc) | 1.0 | 1 | 251 | 2026-11-13 | 3 |
| IE…8X80 | iShares Core MSCI Europe UCITS ETF EUR Acc | reciente | T2.DE | etf | GER | USD | iShares Core MSCI Europe UCITS ETF EUR Acc (Acc) | 1.0 | 1 | 250 | 2026-11-13 | 3 |
| IE…GZ66 | iShares Core MSCI EM IMI UCITS ETF | reciente | T3.DE | etf | GER | EUR | iShares Core MSCI EM IMI UCITS ETF (Acc) | 0.857 | 1 | 252 | 2026-11-13 | 3 |
| IE…ZB19 | iShares NASDAQ 100 UCITS ETF USD Acc | retrasado | T4.DE | etf | GER | USD | iShares NASDAQ 100 UCITS ETF USD Acc (Acc) | 1.0 | 1 | 251 | 2026-11-05 | 11 |
| ... (38 filas más) ...

- Resueltos: 36 / 43 · con ≥ 250 barras: 36 · recientes (≤ 3 d): 27 · sin probar: 0 · peticiones de esta sección: 115
- Estado: reciente 27, retrasado 9, sin_serie 0, no_encontrado 7, error 0, no_probado 0
- Por tipo de ticker (resueltos): 0P 7, etf 29, otras 0
- Por bolsa (resueltos): GER 29, FRA 7
- Regla de elección: 7 ISIN con varias candidatas · 0 sin criterio (empate) · habrían cambiado de ticker con otra regla: liquidez primero 7, solo orden de Yahoo 0
- Dígito de control no válido: ninguno
- Errores: sin_red 0, 429 0, vacio 7, parseo 0, divisa_rara 0

Reglas: Similitud de nombres = Jaccard de palabras significativas (minúsculas, sin acentos ni puntuación, sin fund/funds/ucits/the/of/and): |comunes| / |unión| entre el nombre esperado y el nombre largo (o, si no hay, el corto) que devuelve Yahoo; 0 si alguno …

### Sección ISIN (solo agregados)

Sección ISIN: omitida (no hay secreto PROBE_ISINS).

```

## 2. Qué demuestra cada prueba y qué NO

| Prueba (fichero) | Demuestra | NO demuestra | Control negativo |
|---|---|---|---|
| lista: dígito de control | Mi función da el dígito correcto en 4 ejemplos públicos de la norma y coincide con la implementación independiente del guardia en los 43 ISIN; cambiar un carácter lo rompe | Que el ISIN corresponda al fondo del nombre: un dígito válido solo descarta erratas de transcripción | alterar un dígito en el JSON: la prueba lo **lista** (visto) |
| lista: estructura | 43 entradas, claves `isin`/`name`, sin duplicados, formato ISO, todas pasan el dígito de control (**las 43 pasan**), sin URL ni `@` | Que no haya ninguno de la cartera de Alex (no puedo comprobarlo: el guardia solo tiene huellas de proxies y cripto) | — |
| lista: cargador | Estructuras inválidas → `ValueError`; formato malo y duplicados se **listan** en el mensaje; un dígito malo no se descarta: se marca y se sonda | — | — |
| guardia: excepción acotada | La exención es solo para la ruta exacta `config/fund_probe_isins_generic.json` (mismo nombre en otra carpeta y otro fichero de config siguen siendo fuga) y no exime de los ids de ficha | Que el guardia siga cazando en el resto del repo (lo hace su propia suite, que pasa) | quitar la exención: el guardia falla por la lista (visto) |
| generic: similitud | Jaccard de palabras significativas, valores exactos (0.6 en un caso a mano), mayúsculas/acentos/puntuación/palabras vacías, vacío → 0.0, castiga «Dist» frente a «Acc» | Que 0.6 sea «bastante parecido»: no hay umbral, solo la cifra | G1 (solapamiento en vez de Jaccard), G2 (sin palabras vacías) |
| generic: elección | 0P antes que nada; luego divisa del nombre (leída de cualquiera de los dos nombres de Yahoo, solo si el esperado trae exactamente una); luego liquidez; empate → se emiten todas y se mide la primera de Yahoo marcada `tie`; bolsas desconocidas no se ordenan; independiente del orden de la lista; las reglas alternativas SÍ eligen otro ticker donde toca | Que 0P sea de verdad la mejor cotización, ni que la tabla de liquidez sea correcta (ver §3) | G3 (orden cambiado), G4 (sin 0P), G5 (divisa no filtra), G6 (empate silencioso), G7 (desconocidas = líquidas), G8 (orden alfabético), G13 (0P por substring) |
| generic: medida por ISIN | Búsqueda con el ISIN, histórico `1y`, todos los campos pedidos, estados reciente/retrasado (borde 3/4 d)/sin_serie/no_encontrado/error, solo MUTUALFUND/ETF cuentan como candidatas, costes 1 y 2, 429 en búsqueda y en histórico, sin presupuesto, pausa ≥ 1 s | Nada sobre lo que Yahoo devuelva de verdad | G14 (1 mes), G15, G16, G17 |
| generic: agregados | Resueltos, ≥250 barras (borde 250/249), recientes (borde 3/4), por tipo de ticker y por bolsa (solo resueltos), cuántos habrían cambiado con otra regla (los empates no cuentan como cambio), no probados | — | G9, G10, G11, G12 |
| output: `--sections` | `both` por defecto; `names` no toca los genéricos ni su fichero; `generic` no busca nombres; valor inválido → salida 2; lista genérica inválida → salida 2 listando los malos; en `names` una lista genérica rota no molesta | — | — |
| output: resumen y JSON | Una fila por ISIN con los 13 campos, agregados, reglas escritas en el resumen y en el JSON, empates con todas las candidatas, claves fijas, 5 clases de error siempre | — | — |
| output: tope, ritmo, 429 | El tope sube hasta 400 y no más; cuenta los ISIN genéricos; ≥ 1 s entre TODAS las llamadas de las tres secciones; el 429 en GENÉRICOS para limpio, marca el resto `no_probado`, salida 0 | — | G15, G16, G17 |
| **output: separación de PROBE_ISINS** | (a) JSON y bloque del resumen de GENÉRICOS **byte a byte idénticos** con y sin secreto, también con presupuestos justos (400/30/14/12) y con un 429 que ocurre dentro de la sección privada; (b) con un cliente ruidoso (imprime, loguea, avisa y lanza citando el ISIN privado y su ticker) ningún valor privado aparece en el JSON genérico, el bloque, stdout (salvo las órdenes `::add-mask::`), stderr, logging, avisos ni el JSON de nombres; (c) el JSON genérico no tiene ninguna clave de la sección privada; (d) el detector de fugas del propio test salta si se le cuela un valor | Que GitHub sustituya por `***` las órdenes de máscara (sigue sin probarse, igual que en la sesión anterior); que no haya otro canal entre secciones que no se me haya ocurrido (los que vi: presupuesto, estado de parada, objetos compartidos, orden) | P1 (privada antes que genéricos), P2 (agregado privado en el JSON genérico), P3 (`stopped` al final del run), P4 (el bloque cita el ticker privado) |
| workflow | 2 inputs (`max_requests` por defecto 400, `sections` `choice` names/generic/both por defecto both), ambos por variable de entorno (nunca dentro del `run`); 2 artefactos (nombres, genéricos) cada uno un único JSON y el mismo fichero que escribe el run; un único secreto en un único paso, ningún paso de subida lo ve; permisos `{}`/`contents: read`, solo `main`, timeout 20, acciones por SHA | Que Actions acepte el YAML (no se ha corrido `actionlint`) ni el comportamiento real del runner | W1…W5 |

## 3. Decisiones y supuestos (con la postura contraria)

| Decisión | Postura contraria y por qué no la tomé |
|---|---|
| Similitud = **Jaccard** de palabras significativas (reutiliza `norm_tokens`) con el nombre **largo** de Yahoo (o el corto si no hay) | Coeficiente de solapamiento (|comunes|/|menor|): da 1.0 si Yahoo añade palabras, no distingue «World» de «World Small Cap». O tomar el máximo entre nombre corto y largo: más tolerante pero arbitrario sobre cuál se muestra. Jaccard con un solo nombre tiene menos grados de libertad. |
| «Acc», «Dist», «USD», «EUR» cuentan como palabras | Quitarlas: la similitud sería más alta pero no distinguiría clases (justo lo que pide el criterio de divisa). |
| Candidatas = solo MUTUALFUND/ETF, sin duplicados (como en la sección privada) | Aceptar cualquier tipo: entrarían acciones. `quotes_total` y `search_types` por ISIN dejan ver qué se descarta. |
| Regla de elección: 0P → divisa → liquidez; cada paso solo filtra si queda ≥ 1 candidata | Que un criterio sin candidatas vacíe el conjunto: dejaría ISIN sin ticker. |
| **«Divisa del nombre esperado»** se compara con las divisas que aparecen **en el nombre** de cada candidata, no con su divisa real | La divisa real solo llega con el histórico (la búsqueda no la da, ya anotado en la sesión anterior); medirlas todas costaría 2 peticiones por candidata. Inferirla por la bolsa sería inventar. Después de medir sí se emite `currency` real y `currency_match` frente a la esperada. Solo se aplica si el nombre esperado trae exactamente una divisa. |
| **Tabla de liquidez** (SUPUESTO, de memoria): nivel 1 = `GER` (Xetra) y `LSE`; nivel 2 = `EBS`, `AMS`, `PAR`, `MIL`; el resto sin orden | No poner tabla y que el tercer criterio no exista (pero la pediste). No he verificado los códigos de bolsa de Yahoo ni la liquidez relativa: **verifícalo con la columna «Bolsa» y la lista de candidatas del primer run**. Es una constante (`LIQUIDITY_TIERS`). |
| Si tras los 3 pasos quedan varias: se **emiten todas** (JSON y resumen) y, solo para poder medir, se mide la primera que puso Yahoo, marcada `tie: true` / `decided_by: sin_criterio` | No medir nada (pierdo la cobertura) o inventar un cuarto criterio. El orden de Yahoo ya era el desempate de la sección de nombres. |
| «Cuántos habrían cambiado con otra regla» = 2 reglas alternativas: `liquidez_primero` (liquidez → divisa → 0P) y `orden_yahoo` (ningún criterio); se cuenta sobre los ISIN con ≥ 2 candidatas | Otras reglas posibles; estas dos son las dos extremas y no cuestan peticiones (solo cambian qué ticker se elegiría, no se miden). Ojo: no se mide el ticker alternativo, así que **no sé si habría dado mejor o peor serie**. |
| GENÉRICOS corre **antes** de la sección privada y su `stopped` se fija al terminar ella | Después de la privada (como estaba el orden «nombres → ISIN → alternativas»): el presupuesto compartido sería un canal entre secciones. Con este orden el secreto no puede quitarle peticiones ni cambiarle el estado de parada. Coste: si el presupuesto se agota, quien se queda sin medir es la sección privada, no la pública. |
| La sección PROBE_ISINS corre **siempre que exista el secreto**, también con `sections: generic` o `names` | Hacer que `sections` la apague: dijiste que esa sección no cambia y no se mezcla. Un input que ahorre peticiones sin el secreto lo hace simplemente no existiendo. |
| Tope duro 250 → 400, **por defecto 400** (workflow y script) | Dejar 250 por defecto. Con ambas secciones el peor caso supera 250: nombres ≤ 41 + 2·41 = 123; genéricos ≤ 43 + 2·43 = 129; suman 252 antes de alternativas y de PROBE_ISINS. Con 250 por defecto un run `both` se cortaría casi seguro. El 429 sigue parando limpio. |
| Timeout del job 15 → **20 min** | Dejar 15. La pausa cuenta por llamada (no por coste), el peor caso real son unas ~200 llamadas × (1 s + latencia), unos 8-14 min con latencias de 1-3 s: sin margen con 15. 20 es el máximo que admite el guardia D45 existente. Sin medir. |
| Un ISIN con dígito de control inválido **se sonda igualmente** y se lista en el resumen y en el JSON | Descartarlo: ahorra una petición pero es justo lo que pediste que no pasara en silencio. |
| `no_probado` como sexto estado (parada por 429/presupuesto) | Solo los cinco pedidos: no tendría dónde poner los ISIN que no se llegaron a medir. Es el mismo estado que ya usa la sección de nombres. |
| Resueltos = tiene ticker (incluye `sin_serie`); «con ≥ 250 barras» cuenta barras válidas de la serie de 1 año | — |
| Los dos JSON se escriben **siempre** (la sección omitida lleva `section: "omitida"` y listas vacías) | Escribir solo el de la sección elegida: el paso de subida tendría que tolerar ficheros ausentes (`if-no-files-found: ignore`) y un fallo real de escritura pasaría desapercibido. |
| El nombre del artefacto de GENÉRICOS es `fund-probe-generic`, el JSON `fund_probe_generic.json` | — |

## 4. Desviaciones

1. **He tocado un guardia existente: `tests/test_privacidad_isin.py`.** La lista pedida lleva ISIN irlandeses reales y ese guardia barre TODO el árbol y rechaza cualquier ventana `IE` + 10 caracteres que no esté en su lista de sintéticos: sin tocarlo, el CI falla. No he escondido los ISIN (partidos, codificados…): eso habría sido esquivar el guardia. Cambio mínimo: `ISIN_EXENTO = "config/fund_probe_isins_generic.json"`, comparado con la **ruta relativa exacta** (no por nombre de fichero) y solo para la regla de ISIN (los ids de ficha siguen vigilados también ahí). Tres pruebas nuevas lo verifican (mismo nombre en otra carpeta, otro fichero de config y ids de ficha: siguen siendo fuga) y quitar la exención hace fallar el guardia. **Esto afloja una regla de privacidad: conviene que lo revises tú.** Mis ficheros de prueba construyen en ejecución cualquier literal con forma de ISIN irlandés (como en la sesión anterior).
2. **«Cada ISIN cuenta 2 peticiones» no es exactamente lo que cuenta el tope.** La puerta (`Gate`) cuenta 1 por búsqueda y 2 por histórico (peor caso: yfinance pide primero la zona horaria y luego la serie; decisión de la sesión anterior que no he cambiado). Un ISIN resuelto cuesta **3** y uno no resuelto **1** (2 operaciones por ISIN, hasta 3 peticiones). Con la lista de 43: como mucho 129. Si en realidad el histórico es 1 petición, el tope cuenta de más y se corta antes de lo necesario (es el lado seguro).
3. **Defecto del tope 250 → 400 y timeout 15 → 20** (ver §3). Mis tests de la sesión anterior que fijaban 250/15/1 input/1 artefacto se actualizaron (`test_fund_probe_core`, `_output`, `_workflow`); ninguno se debilitó: los controles negativos G15 y W4 los hacen fallar si se vuelve atrás.
4. Los tests existentes de la sonda (`test_fund_probe_output.py`) llaman ahora a `main` con `--sections names` y `--generic-json-out` en tmp: sin eso, el modo por defecto (`both`) habría leído la lista real y **escrito `fund_probe_generic.json` en la raíz del repo** (lo vi aparecer en `git status` y lo borré antes de commitear).
5. La primera pasada de mutaciones dejó **G13 sin detectar** (ver 1.3): corregí la prueba, no el código.
6. En mis propias pruebas nuevas tuve que corregir expectativas que había escrito mal antes de ver el código en verde (anotado por honestidad; ninguna se cambió para ocultar un fallo del código): el recuento de recientes/≥250 del mundo de prueba (calculé mal que un ISIN era «retrasado»), que «probed» no puede estar prohibido en el JSON genérico (es una clave mía legítima: la lista prohibida son las claves de la sección privada), y que el detector de fugas devuelve dos valores cuando el nombre privado contiene a otro.
7. **Observación fuera de alcance:** el commit `020ab6a` (D44, ya en `main` antes de esta sesión) figura con la identidad de la herramienta de IA como autor y committer (no la de Alex). No lo he tocado (reescribir `main` no es mío); lo digo por tu regla de no atribución.
8. Los ISIN van escritos exactamente como los dio Alex («de memoria, SIN verificar»). Que pasen el dígito de control (43/43) solo dice que no hay erratas de un carácter; que cada ISIN sea el fondo del nombre lo dirá la sonda (columna «Similitud» y «Nombre en Yahoo»).

## 5. Lo no probado

- **Yahoo real, todo:** que `Search(isin)` devuelva candidatos por ISIN; que los resultados traigan `longname`/`shortname`/`exchange`/`quoteType` como los simula `tests/fund_probe_fakes.py` (la forma «real» de la sesión anterior tampoco se contrastó en vivo); que los códigos de bolsa sean `GER`, `LSE`, `EBS`, `AMS`, `PAR`, `MIL`…; que existan símbolos `0P…` para estos fondos; cuántas barras trae `period="1y"` (el umbral 250 es el del encargo; no sé cuántas esperar con festivos); que el histórico cueste 2 peticiones en la práctica.
- Duración real del run (estimada, no medida) frente al timeout de 20 min.
- Que `type: choice` con `options` en `workflow_dispatch` y la subida de dos artefactos funcionen en Actions (`actionlint` no se ha corrido; solo se validó la estructura con PyYAML).
- Que GitHub sustituya por `***` las órdenes `::add-mask::` (igual que en la sesión anterior).
- Que no exista un canal entre la sección privada y la pública que no se me haya ocurrido: las pruebas cubren presupuesto, parada, orden, datos y salida, no el código de terceros (yfinance) ni escrituras directas al descriptor 2 desde código nativo.
- Python 3.13 aquí; el runner usa 3.14.

## 6. Cómo ejecutarla (pasos manuales de Alex)

1. **Revisa la excepción del guardia (§4.1)**, crea el PR a `main` (lo creas tú) y fusiona. El workflow solo corre desde `main`.
2. **Primer run recomendado:** Actions → *Fund probe (cobertura de fondos en Yahoo)* → *Run workflow* → rama `main` → `sections: generic` → `max_requests: 400`. Son unas ~129 peticiones como máximo (43 ISIN × hasta 3). Si existe el secreto `PROBE_ISINS`, su sección corre igualmente (aparte, solo agregados).
3. **Leer el resultado:** pestaña *Summary* → «Sección GENÉRICOS» (tabla de 43 filas, agregados, reglas, empates). El JSON completo está en el artefacto `fund-probe-generic` (14 días); no contiene nada de `PROBE_ISINS`.
4. **Interpretar:** (a) muchos `no_encontrado`: mira `quotes_total` y `search_types` de esa fila en el JSON (¿Yahoo no lo tiene o el filtro MUTUALFUND/ETF lo descarta?); (b) similitud baja: mira «Nombre en Yahoo» (puede ser otro fondo o un ISIN mal recordado); (c) «Candidatas sin criterio» y «Bolsa»: sirven para validar o corregir `LIQUIDITY_TIERS`; (d) «habrían cambiado de ticker» dice cuánto importa la regla, no cuál es mejor.
5. Si «Sonda parada: 429» aparece pronto, Yahoo limita al runner: relanza más tarde.
