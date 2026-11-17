# FIXES_LAG_FONDOS: medición del retraso real de los fondos «0P» en Yahoo Finance

Rama: `claude/wonderful-meitner-3t16kh`. Autor y committer de todos los commits: Alex Martinez, con el email noreply de GitHub que ya usan sus commits en el repo. Sin coautoría ni atribución a herramientas de IA en commits, en este informe ni en el PR (lo crea Alex).
No he lanzado el workflow, no he tocado ningún secreto y no he creado la rama `probe-data`. **Nada de lo que sigue es un dato real de Yahoo**: todo es simulado o sintético.

Ficheros NUEVOS: `.github/workflows/fund-lag.yml`, `config/fund_lag_tickers.json`, `scripts/fund_lag_common.py`, `scripts/fund_lag.py`, `scripts/fund_lag_report.py`, `scripts/fund_lag_git.sh`, `tests/test_fund_lag_{common,run,report,workflow,git}.py` y este informe.
Fichero MODIFICADO: `tests/test_extra_D.py` (D45: una línea, la excepción exacta `("fund-lag.yml", "publish")`). D44 no se ha tocado: el workflow no usa ningún secreto.

## 1. Comandos y salidas en bruto

Entorno: venv fuera del árbol con `pip install --require-hashes --no-deps -r requirements.txt` y `-r requirements-dev.txt` (yfinance 1.7.0, pytest 9.2.0, ruff 0.17.3, PyYAML 6.0.3). Python 3.13 aquí (el runner usa 3.14). Ninguna prueba toca la red (`tests/conftest.py` lo impide).

### 1.1 Comandos del CI

```
py_compile OK
All checks passed!
    warnings.warn(

-- Docs: https://docs.pytest.org/en/stable/how-to/capture-warnings.html
2115 passed, 1 warning in 225.42s (0:03:45)
........................                                                 [100%]
168 passed in 4.09s
168 tests collected in 0.48s
```

(168 pruebas nuevas: 76 común/calendario, 29 medición, 22 informe, 27 workflow, 14 git; `2115 passed` es la suite entera. No he vuelto a correr la suite de la base sin mis cambios; la diferencia con los 1944 del informe anterior no se reduce solo a mis 168 pruebas porque los guardias existentes se parametrizan por fichero de workflow/script y crecen con los ficheros nuevos.)

Un fallo intermedio: `test_privacidad_isin` cazó un literal con forma de ISIN en un control negativo mío (lista de tickers con un ISIN dentro). No toqué el guardia: el valor se construye ahora en ejecución.

### 1.2 Controles negativos (mutaciones del código y de los YAML; cada línea DEBE decir DETECTADA)

Script fuera del árbol: aplica una mutación, corre los 5 ficheros de pruebas nuevos y `test_extra_D.py` con `-x`, y restaura el fichero.

```
[M1 días hábiles: hoy no cuenta] -> DETECTADA | 1 failed, 9 passed in 0.62s | FAILED tests/test_fund_lag_common.py::test_dias_habiles_entre_ultima_barra_y_hoy[2026-11-13-2026-11-17-2-2]
[M2 sin Lunes de Pascua] -> DETECTADA | 1 failed, 7 passed in 0.58s | FAILED tests/test_fund_lag_common.py::test_festivos_target_2026_son_exactamente_seis
[M3 el sábado es hábil] -> DETECTADA | 1 failed, 9 passed in 0.60s | FAILED tests/test_fund_lag_common.py::test_dias_habiles_entre_ultima_barra_y_hoy[2026-11-13-2026-11-17-2-2]
[M4 autolímite: 7 días exactos ya caduca] -> DETECTADA | 1 failed, 70 passed in 14.62s | FAILED tests/test_fund_lag_common.py::test_primera_linea_de_hace_exactamente_7_dias_todavia_mide
[M5 autolímite por defecto 8] -> DETECTADA | 1 failed, 71 passed in 0.71s | FAILED tests/test_fund_lag_common.py::test_primera_linea_de_hace_7_dias_y_un_segundo_caduca
[M6 no poder leer el registro => mide] -> DETECTADA | 1 failed, 73 passed in 0.60s | FAILED tests/test_fund_lag_common.py::test_sin_registro_mide_y_no_poder_leerlo_no_mide
[M7 primera línea ilegible => mide] -> DETECTADA | 1 failed, 74 passed in 0.60s | FAILED tests/test_fund_lag_common.py::test_registro_vacio_mide_y_primera_linea_ilegible_caduca
[M8 tope duro 400] -> DETECTADA | 1 failed, 79 passed in 0.62s | FAILED tests/test_fund_lag_run.py::test_usa_el_mismo_gate_que_la_sonda_y_su_pausa_no_baja_de_un_segundo
[M9 periodo 1mo] -> DETECTADA | 1 failed, 77 passed in 0.68s | FAILED tests/test_fund_lag_run.py::test_pide_5_dias_con_historico_y_nunca_busca
[M10 el 429 se registra como parseo] -> DETECTADA | 1 failed, 83 passed in 0.98s | FAILED tests/test_fund_lag_run.py::test_429_para_a_la_primera_sin_reintentar_y_el_resto_queda_no_probado
[M11 caducado igualmente mide] -> DETECTADA | 1 failed, 92 passed in 0.83s | FAILED tests/test_fund_lag_run.py::test_registro_con_mas_de_7_dias_sale_en_verde_sin_medir_ni_tocar_yahoo
[M12 append siempre false] -> DETECTADA | 1 failed, 76 passed in 0.70s | FAILED tests/test_fund_lag_run.py::test_una_linea_por_ticker_en_el_orden_de_la_lista_con_los_retrasos_esperado
[M13 lag_wd con calendario target] -> DETECTADA | 1 failed, 115 passed in 2.23s | FAILED tests/test_fund_lag_report.py::test_calendario_weekdays_lee_lag_wd_y_target_lag_bd
[M14 validación sin lista de tickers] -> DETECTADA | 1 failed, 51 passed in 0.61s | FAILED tests/test_fund_lag_common.py::test_lineas_ticker_fuera_de_la_lista_se_rechaza_con_lista_y_se_acepta_si
[M15 validación sin claves exactas] -> DETECTADA | 1 failed, 28 passed in 0.58s | FAILED tests/test_fund_lag_common.py::test_control_negativo_registros_invalidos_se_rechazan[<lambda>0]
[M16 informe: nada censurado] -> DETECTADA | 1 failed, 116 passed in 2.24s | FAILED tests/test_fund_lag_report.py::test_primera_aparicion_del_fondo_es_a_las_1217_de_D_mas_2_con_ausente_a_
[M17 informe: mediana = primer valor] -> DETECTADA | 1 failed, 106 passed in 2.35s | FAILED tests/test_fund_lag_report.py::test_mediana_con_numero_par_de_mediciones_es_la_media_de_las_dos_central
[M18 informe: franjas desplazadas] -> DETECTADA | 1 failed, 109 passed in 2.08s | FAILED tests/test_fund_lag_report.py::test_los_bordes_de_las_franjas_de_3_horas[08:59:59-06-08]
[M19 informe: sin 'ausente en'] -> DETECTADA | 1 failed, 116 passed in 2.17s | FAILED tests/test_fund_lag_report.py::test_primera_aparicion_del_fondo_es_a_las_1217_de_D_mas_2_con_ausente_a_
[M20 informe: cuenta también los 429] -> DETECTADA | 1 failed, 107 passed in 2.10s | FAILED tests/test_fund_lag_report.py::test_solo_cuentan_las_mediciones_ok_y_el_resto_se_cuenta_aparte
[W1 measure con escritura] -> DETECTADA | 1 failed, 130 passed in 2.44s | FAILED tests/test_fund_lag_workflow.py::test_permisos_workflow_vacios_measure_lee_publish_escribe
[W2 push sin continue-on-error] -> DETECTADA | 1 failed, 142 passed in 2.66s | FAILED tests/test_fund_lag_workflow.py::test_el_push_no_hace_fallar_el_workflow_ni_la_medicion_y_avisa
[W3 secreto en el job de escritura] -> DETECTADA | 1 failed, 136 passed in 2.42s | FAILED tests/test_fund_lag_workflow.py::test_sin_secretos_ni_variables_en_ninguna_parte_del_workflow
[W4 cron en punto] -> DETECTADA | 1 failed, 128 passed in 2.28s | FAILED tests/test_fund_lag_workflow.py::test_seis_ejecuciones_al_dia_en_horas_no_en_punto
[W5 publish sin guarda de main] -> DETECTADA | 1 failed, 140 passed in 2.47s | FAILED tests/test_fund_lag_workflow.py::test_los_dos_jobs_solo_corren_sobre_main
[W6 sin validar antes de publicar] -> DETECTADA | 1 failed, 143 passed in 2.70s | FAILED tests/test_fund_lag_workflow.py::test_la_validacion_va_antes_del_push_y_no_instala_nada_de_terceros
[W7 publish con pip install] -> DETECTADA | 1 failed, 143 passed in 2.71s | FAILED tests/test_fund_lag_workflow.py::test_la_validacion_va_antes_del_push_y_no_instala_nada_de_terceros
[W8 el push es --force] -> DETECTADA | 1 failed, 145 passed in 2.78s | FAILED tests/test_fund_lag_workflow.py::test_el_script_de_git_solo_empuja_a_probe_data_sin_force_y_una_sola_ve
[W9 el push va a main] -> DETECTADA | 1 failed, 145 passed in 2.71s | FAILED tests/test_fund_lag_workflow.py::test_el_script_de_git_solo_empuja_a_probe_data_sin_force_y_una_sola_ve
[W10 sin enmascarar el token] -> DETECTADA | 1 failed, 165 passed in 4.18s | FAILED tests/test_fund_lag_git.py::test_el_token_no_sale_en_la_salida_aunque_el_push_lo_cite
[W11 no crea la rama huérfana] -> NO DETECTADA | 217 passed in 5.25s | 
[W12 head devuelve todo el fichero] -> DETECTADA | 1 failed, 156 passed in 2.98s | FAILED tests/test_fund_lag_git.py::test_head_devuelve_solo_la_primera_linea
[W13 D45 sin la excepción de fund-lag] -> DETECTADA | 1 failed, 132 passed in 2.42s | FAILED tests/test_fund_lag_workflow.py::test_D45_pasa_con_el_workflow_real_y_su_excepcion
```

W11 es una mutación **equivalente**, no un hueco: en un repositorio recién creado (sin commits) `git checkout -b` y `git checkout --orphan` producen lo mismo (rama sin nacer), así que ninguna prueba puede distinguirlos. Lo que sí se prueba es el resultado: la rama publicada es raíz sin padres y solo contiene `lag/fund_lag.jsonl`.

### 1.3 Prueba real del push a una rama huérfana, pero contra un repositorio LOCAL

`tests/test_fund_lag_git.py` ejecuta el script real `scripts/fund_lag_git.sh` contra repos bare locales (`file://`): crea la rama `probe-data` huérfana, añade en avance rápido, no cambia `main` ni `data` (SHA idénticos antes y después), firma como `github-actions[bot]`, y un push no-avance-rápido o rechazado por un hook falla sin forzar y sin cambiar la rama destino. Detalle en §2.

## 2. Qué demuestra cada prueba y qué NO

| Prueba | Demuestra | NO demuestra |
|---|---|---|
| común: calendario | Pascua correcta 2024-2030 (fechas conocidas); los 6 festivos TARGET de 2026; el 24/31-dic y el 6-ene NO son festivos; 11 casos a mano del conteo (viernes→martes = 2, sábado/domingo = 0, barra de hoy = 0, barra futura = 0, Semana Santa, Navidad, Año Nuevo, 1-mayo); el intervalo es abierto a la izquierda y cerrado a la derecha; el conteo L-V coincide con `numpy.busday_count` en ~800 pares y el TARGET también pasándole los festivos | Que TARGET sea el calendario que sigue Morningstar/Yahoo para publicar (ver §3). Los festivos de Frankfurt/Londres no se modelan |
| común: registro | Esquema exacto (9 claves), tipos y rangos, errores con nulos; la fecha de «hoy» es la fecha UTC del instante (incluido un instante con otra zona); 21 registros inválidos y 4 formas de «error con datos» se rechazan; los mensajes nunca citan el contenido | — |
| común: lista | Es exactamente la pedida, en orden, 7 `fondo` + 3 `control_etf`; sin forma de ISIN, sin coincidencias con las huellas de la cartera que guarda `tests/privacy_guard.py`; 12 listas inválidas se rechazan | Que esos tickers no sean de la cartera de Alex: el guardia solo tiene huellas de proxies y cripto (0 coincidencias), no puede decir más. La lista es pública por decisión de Alex |
| común: autolímite | Borde exacto: 7 d → mide, 7 d + 1 s → caduca; reciente/futura/sin registro/vacío → mide; no poder leer → no mide; primera línea ilegible → caduca; el límite es configurable | — |
| medición con Yahoo simulado | Una línea por ticker en orden, con retrasos exactos (fondos 2, ETF 0, medidos un martes); histórico `5d` y nunca búsqueda; ≥ 1 s entre TODAS las peticiones; coste 2 por histórico (20/30); instante propio por ticker; viernes medido en sábado = 0; 429 (clase o texto) para a la primera, sin reintento ni esperas de más, resto `no_probado`; 3 fallos de red paran, uno suelto no; vacío y parseo no paran; tope 5 y tope duro 30; el autolímite sale en verde sin construir el cliente ni tocar Yahoo (también como proceso real); salidas `append`/`expired`; resumen; el cliente real con yfinance simulado recibe `period="5d"` | Nada sobre lo que Yahoo devuelve de verdad |
| informe (datos sintéticos) | Se construye un «Yahoo» con horas de publicación conocidas (fondo: barra de D a D+2 hábiles 10:00 UTC; ETF: el propio D 16:30 UTC) y los valores esperados salen de esa construcción: mín/mediana/máx por franja (fondo 3 antes de las 10:00 y 2 después; ETF 1 y luego 0), primera aparición a las 12:17 con «ausente en» 09:17, ETF a las 18:17; mediana de número par; bordes de franjas; `weekdays` vs `target`; estados no `ok` excluidos; fechas de la primera medición marcadas como cota; líneas rotas saltadas y contadas | Que el informe sea útil con datos reales: la resolución es el intervalo de muestreo (3 h) y los fines de semana no están en el mundo sintético |
| workflow | Solo `schedule` + `workflow_dispatch`; un único cron `17 6,9,12,15,18,21 * * *`; permisos `{}`, `measure` lee, `publish` escribe y es el único; D45 REAL ejecutado sobre copias mutadas: pasa con el workflow real, y rechaza que `measure` escriba, un tercer job con escritura y un timeout largo; excepción exacta (una sola mención, solo `publish`); sin `secrets.` ni `vars.` en el fichero; el único token es `github.token` en un solo paso del job de escritura; ambos jobs solo sobre `main`; `publish` solo si hubo líneas; el push es `continue-on-error` con un paso posterior de aviso, la medición es otro job; el job de escritura no instala nada de terceros y valida antes de empujar; el script solo empuja una vez, a `probe-data`, sin force; acciones por SHA (los mismos que el resto del repo); ningún otro workflow lo llama | Que Actions acepte el YAML (no se ha corrido `actionlint`); el comportamiento real de `download-artifact` |
| git (repos locales) | `head`: missing/error/found y solo la primera línea; `publish`: rama huérfana con un fichero, `main` y `data` intactas, historia lineal, firma del bot, rechazo sin force, fallo ante un hook, ante repo inalcanzable, sin líneas o sin token; el token no sale en la salida (con un `git` de pega que sí lo imprime) | Autenticación real con `github.token`, protecciones de rama de GitHub ni permisos reales del repo |

## 3. Decisiones y supuestos (con la postura contraria)

| Decisión | Postura contraria y por qué no la tomé |
|---|---|
| Retraso = días hábiles en (última barra, hoy UTC]; hoy cuenta aunque su sesión no haya terminado | Contar desde el día siguiente al cierre real o usar la hora: más fino, pero exige suponer una hora de cierre. Con esta regla «NAV de D disponible el día hábil siguiente» es retraso 1; retraso 2 = no llega a tiempo |
| Se guardan DOS retrasos: `lag_bd` (TARGET) y `lag_wd` (solo L-V), más días naturales | Elegir uno. El encargo dice «TARGET/lunes-viernes»; guardar ambos cuesta dos enteros y evita decidir antes de ver datos. El informe usa `target` por defecto |
| Se guarda además `dates` (fechas de todas las barras de la ventana de 5 d), no pedido | Solo la última barra. Sin `dates`, la «primera aparición» de cada día solo se puede inferir de la última barra y fallaría si Yahoo se salta un día |
| «Hoy» = fecha UTC de la medición | Fecha de Madrid. Las 6 horas del cron (06-21 UTC) caen el mismo día en ambas zonas; con 23:xx UTC difiriría |
| Franja = bloque de 3 h de la hora UTC de la medición (configurable) | Hora exacta. El cron de GitHub se retrasa decenas de minutos y a veces horas; el bloque de 3 h aguanta un retraso de ~2 h 40 min sin cambiar de franja |
| Autolímite leído de la rama `probe-data` con una lectura anónima (el repo es público), antes de medir | Guardar la fecha de inicio en una variable del repo o en el propio workflow: requeriría escritura/permisos extra, y el encargo dice «primera línea del registro» |
| Si NO se puede leer el registro (red/GitHub) → no se mide en esa ejecución | Medir igualmente: si el registro ya había caducado, un fallo de lectura alargaría el experimento sin fin. Coste: una muestra perdida |
| Primera línea ilegible → se considera caducado (con aviso) | Seguir midiendo. Un experimento sin fecha de inicio comprobable no debe alargarse solo |
| La expiración se comprueba solo en `measure`, no otra vez en `publish` | Repetirla en el job de escritura. Con la regla anterior el hueco es mínimo y `publish` quedaría con más lógica y más superficie |
| Dos jobs; el relevo entre ellos es un artefacto (`fund-lag-new`, 14 días) y a la vez copia de respaldo si el push falla | Un solo job con un paso de push `continue-on-error`: el token de escritura estaría en el mismo job que ejecuta yfinance (código de terceros). Con dos jobs, el que tiene escritura no instala nada |
| El job de escritura valida el fichero antes de empujar (claves, tipos, tickers de la lista, máx. 30 líneas de 1000 caracteres) con solo biblioteca estándar | Confiar en el artefacto. Viene de un job que ejecuta código de terceros; validarlo es barato. Aun así, un yfinance comprometido podría escribir datos «válidos pero falsos» en la rama: el daño se limita a esa rama de datos |
| El push no usa `--force` y no reintenta; con `concurrency: fund-lag` las ejecuciones son secuenciales | Reintento con rebase: más código para una carrera casi imposible. Si falla, las líneas siguen en el artefacto 14 días y el workflow avisa |
| Identidad de los commits de la rama `probe-data`: `github-actions[bot]` (como `keepalive.yml`) | Alex: los commits automáticos no deben aparecer como suyos |
| Tope 30 peticiones, histórico cuenta 2, timeout 10 min (measure) y 5 (publish) | Más margen. 10 tickers × 2 = 20; el tope duro deja 10 de holgura. Sin medir la duración real: ~10 s de pausas + latencia |
| Reutilizo `Gate` y `YahooClient` de `fund_probe.py` (importa yfinance) | Duplicarlos. `fund_probe.py` no se ha tocado. `measure()` de la sonda no devuelve las fechas, así que `valid_dates` repite su regla de validez en 4 líneas |
| `config/fund_lag_tickers.json` con `kind` (`fondo`/`control_etf`) | Lista plana de tickers. El `kind` permite resumir fondos frente a control sin inferirlo del prefijo |
| Un registro por ticker aunque falle (estado `429`, `vacio`, `sin_red`, `parseo`, `no_probado`) | Solo escribir los exitosos. Los fallos son información (a qué hora Yahoo bloquea) y el informe los cuenta aparte |

## 4. Desviaciones

1. **Un cron único** `17 6,9,12,15,18,21 * * *` en lugar de seis entradas: equivalente y más simple.
2. **`tests/test_extra_D.py` modificado**: una línea (la excepción pedida). Comprobado que sin ella D45 y mi prueba fallan (W13).
3. **Tres scripts y un `.sh` en vez de uno**: `fund_lag_common.py` es solo biblioteca estándar para que el job de escritura y el informe no necesiten instalar nada.
4. **Campos extra** `dates` y `lag_wd`/`lag_days` en el registro (ver §3).
5. Un literal con forma de ISIN en un control negativo mío fue cazado por `test_privacidad_isin`; lo corregí construyéndolo en ejecución, sin tocar el guardia.
6. Mi prueba de la firma del bot falló la primera vez por un montaje de prueba (las variables `GIT_AUTHOR_*` del entorno de test pisaban el `-c user.name` del script). Lo arreglé quitándolas del entorno de la prueba, lo que además demuestra que el script no depende de ninguna identidad ambiente. No fue un fallo del script.
7. Observación (no tocada): el commit `020ab6a` en `main` figura con la identidad de la herramienta como autor; ya lo había anotado el informe anterior.

## 5. Lo no probado

- **Yahoo real, todo**: cuántas barras trae `5d` para un `0P…`, qué fecha tiene su última barra, si `.F` tiene huecos, qué ETF de control responde con barra de hoy, si el runner de GitHub recibe 429, y el retraso real de los fondos. La hipótesis «~2 días hábiles» sigue sin medir.
- **El push real a la rama huérfana** con `github.token` y las protecciones del repo (solo se ha probado el script contra repos locales). Si el repo tiene una regla que bloquee la creación de ramas nuevas, el push fallará y el workflow avisará.
- `actions/download-artifact` y la transferencia entre jobs en Actions; `actionlint`; que el cron de GitHub dispare en esas horas (se retrasa, y GitHub puede saltarse ejecuciones).
- **Sesgo de la medición**: 6 muestras al día dan una resolución de 3 h para saber cuándo publica Yahoo; el informe da la ventana `(ausente en, primera aparición]`, no la hora exacta. La cadencia no se retrasa con la hora del cron, así que la franja puede desplazarse.
- Que los festivos TARGET sean los que sigue Yahoo/Morningstar. Frankfurt cierra el 24 y el 31 de diciembre y TARGET no; en esas fechas `lag_bd` puede sobreestimar. Por eso se guardan los dos calendarios.
- El tamaño de la historia git de `probe-data`: un commit por ejecución (≈ 42 en 7 días); irrelevante.

## 6. Cómo usarlo (pasos de Alex)

1. Revisa el PR y fusiona (lo creas tú). El workflow solo corre desde `main`; antes de fusionar no se puede lanzar.
2. Primera prueba: Actions → *Fund lag* → *Run workflow* (rama `main`). Debe terminar en verde, crear la rama `probe-data` con `lag/fund_lag.jsonl` (10 líneas) y mostrar la tabla en *Summary*. Si el push falla, verás el aviso y las líneas están en el artefacto `fund-lag-new`.
3. Se autolimita: pasados 7 días desde la primera línea, cada ejecución sale en verde con «el experimento ha terminado». No hace falta desactivar nada.
4. Informe: `git fetch origin probe-data && git show origin/probe-data:lag/fund_lag.jsonl > /tmp/fund_lag.jsonl && python scripts/fund_lag_report.py /tmp/fund_lag.jsonl` (opciones `--slot-hours`, `--calendar weekdays`, `--json`).
5. Interpretar: «Primera aparición» con «Días hábiles tras la barra» = 1 antes de la hora límite de la app significa que Yahoo puede ser la primera fuente de NAV; 2 o más, no. Las filas con `≤` son solo cota superior.
