# Arreglos de la revisión completa (repo público) — noviembre 2026

Rama `claude/fix-revision-publico`, desde `main` en `69d5195` (incluye `ffe237c` y el informe `auditorias/REVISION_COMPLETA_NOV2026.md`, ya fusionado).
Un commit por punto, en el orden pedido. **No se ha fusionado nada** ni se ha abierto PR. Autor de los commits: Alex; sin atribución a Claude.

| # | Punto | Commit | ¿Se sostenía el hallazgo? |
|---|---|---|---|
| 1 | R-01 lista privada fuera del repo | `d617e00` | Sí. Las 8 huellas eran sha256 sin sal (`tests/privacy_guard.py:9-21`) y una de ellas casaba con un símbolo escrito en claro en `tests/test_fund_facts.py:83` y `tests/test_fund_probe_generic.py:134` (comprobado tokenizando los ficheros contra las huellas; el valor no se escribe aquí). |
| 2 | R-02 `nav_probe` sin valor de NAV | `e7f346f` | Sí: `scripts/nav_probe.py` imprimía `NAV <fecha> = <valor>` en la línea de cada variante y en las XHR. |
| 3 | R-03 `nav-probe` solo en `main` | `af40b19` | Sí: el job `probe` (único con `PERSONAL_FEED_TOKEN` sin la condición) no tenía `if:`; el test paramétrico solo miraba V2/PN/PF. |
| 4 | R-05 sin respaldo a `vars.*` | `ede3195` | Sí: 12 expresiones `secrets.X \|\| vars.X` en 4 workflows. |
| 5 | S-01 franjas de la sombra | `e89cc62` | Sí, y peor que lo que dice la tabla del informe: el resumen de runs lista **15** arranques `schedule` de `personal-full` (no 8), y los 15 caen fuera de 05-10 y 19-24 UTC. |
| 6 | R-06 guardia ampliado | `58f0f5d` | Sí: un ISIN `LU` inventado con dígito de control válido daba `isin_leaks = 0` con el guardia de `main` (salida abajo). |
| 7 | Hallazgo 5 / F-01 cripto diminuta | `3b0e3ce` | Sí: reproducido con el script del informe (precio publicado `0.0` y error de validación de todo el sitio). |

---

## 1. Comandos y salidas en bruto

Entorno: Python 3.13.18 local (CI usa 3.14), dependencias de `requirements.txt` y `requirements-dev.txt` instaladas con `--require-hashes`.
La lista privada local se obtuvo **en ejecución**: un script del scratchpad busca, entre los símbolos de la versión de `main` de `tests/test_fund_facts.py`,
el que casa con una de las huellas de `main`, y lo pasa a `PRIVATE_DENYLIST` sin imprimirlo. Ningún log ni este informe lo contienen (barrido al final).

### Línea base (`main`, en un worktree aparte, sin la lista)

```
$ env -u PRIVATE_DENYLIST python -m pytest -q        (en un worktree de main)
-- Docs: https://docs.pytest.org/en/stable/how-to/capture-warnings.html
2358 passed, 1 warning in 240.32s (0:04:00)
```

### Punto 1 — R-01

Rojo (tests nuevos sobre el árbol todavía con el símbolo en claro y sin el secreto en `ci.yml`):

```
$ PRIVATE_DENYLIST="<derivado, no impreso>,<entrada inventada>" python -m pytest -q -rs tests/test_privacidad_lista_privada.py   (filtrado a FAILED/E/resumen)
lista privada del dueño en el árbol (sin valores): tests/test_fund_facts.py: 1 coincidencias con la lista privada; tests/test_fund_probe_generic.py: 1 coincidencias con la lista privada
E           AssertionError: test_privacidad_lista_privada.py
E           assert ('PROXY_HASHES' not in '"""R-01 (re...not in run\n'
E             
E             'PROXY_HASHES' is contained here:
E               attr(pg, "PROXY_HASHES") and not hasattr(pg, "CRYPTO_HASHES")
E             ?           ++++++++++++
E                   for p in (ROOT / "tests").glob("*.py"):
E                       text = p.read_text(encoding="utf-8")
E                       assert "PROXY_HASHES" not in text and "CRYPTO_HASHES" not in text, p.name...
E             
E             ...Full output truncated, use '-vv' to show)
tests/test_privacidad_lista_privada.py:49: AssertionError
E       assert (0 == 1)
E        +  where 0 = len([])
tests/test_privacidad_lista_privada.py:97: AssertionError
3 failed, 4 passed in 0.52s
```

(Los fallos eran tres: el barrido encuentra los 2 ficheros —sin decir el valor—; el propio fichero de test llevaba el nombre de las constantes viejas, corregido
troceándolo; y `ci.yml` aún no pasaba el secreto.)

Verde, con y sin lista:

```
$ export PRIVATE_DENYLIST="<texto protegido derivado en ejecución de la huella de main, no impreso>,<entrada inventada>"
$ python -m pytest -q -p no:cacheprovider -rs tests/test_privacidad_lista_privada.py
.......                                                                  [100%]
7 passed in 0.62s
$ env -u PRIVATE_DENYLIST python -m pytest -q -p no:cacheprovider -rs tests/test_privacidad_lista_privada.py
s......                                                                  [100%]
=========================== short test summary info ============================
SKIPPED [1] tests/test_privacidad_lista_privada.py:38: NO EJECUTADO: falta la lista privada (secreto de CI o variable de entorno local PRIVATE_DENYLIST); la comprobación de la lista del dueño NO se ha hecho en este run
6 passed, 1 skipped in 0.73s
```

### Punto 2 — R-02

```
$ python -m pytest -q tests/test_nav_probe_F2.py        (rojo: tests nuevos/cambiados, código viejo)
FAILED tests/test_nav_probe_F2.py::test_probe_imprime_fecha_y_si_el_valor_coincide_con_la_base_y_senala_la_ganadora
FAILED tests/test_nav_probe_F2.py::test_r02_el_log_no_lleva_ningun_valor_de_nav_en_ninguna_variante_ni_xhr
FAILED tests/test_nav_probe_F2.py::test_r02_valor_distinto_de_la_base_dice_no_y_sin_base_lo_dice
FAILED tests/test_nav_probe_F2.py::test_probe_descubre_y_prueba_la_xhr_y_la_considera_mas_fresca
4 failed, 28 passed in 0.62s
$ python -m pytest -q tests/test_nav_probe_F2.py tests/test_workflows.py tests/test_workflows_H4.py tests/test_symbol_probe.py   (verde)
390 passed in 47.06s
$ (mutante: la línea base vuelve a imprimir el valor) python -m pytest -q tests/test_nav_probe_F2.py
FAILED tests/test_nav_probe_F2.py::test_probe_imprime_fecha_y_si_el_valor_coincide_con_la_base_y_senala_la_ganadora
FAILED tests/test_nav_probe_F2.py::test_r02_el_log_no_lleva_ningun_valor_de_nav_en_ninguna_variante_ni_xhr
FAILED tests/test_nav_probe_F2.py::test_r02_valor_distinto_de_la_base_dice_no_y_sin_base_lo_dice
FAILED tests/test_nav_probe_F2.py::test_probe_descubre_y_prueba_la_xhr_y_la_considera_mas_fresca
4 failed, 28 passed in 0.76s
```

### Punto 3 — R-03

```
$ python -m pytest -q tests/test_workflows_F1.py        (rojo)
E           AssertionError: ('nav-probe.yml', 'probe')
E           assert "github.ref == 'refs/heads/main'" in ''
E       AssertionError: assert {'ci.yml', 'f...-feed-v2.yml'} == {'fund-probe....-feed-v2.yml'}
E         
E         Extra items in the left set:
E         'ci.yml'
E         Use -v to get more diff
FAILED tests/test_workflows_F1.py::test_los_jobs_con_secretos_solo_corren_en_main[nav-probe.yml]
FAILED tests/test_workflows_F1.py::test_todo_workflow_con_secretos_esta_en_la_lista_parametrizada
2 failed, 50 passed in 1.89s
$ python -m pytest -q tests/test_workflows_F1.py tests/test_nav_probe_F2.py tests/test_workflows.py tests/test_workflows_H4.py   (verde)
426 passed in 59.75s
```

El rojo enseñó además que `ci.yml` ya tenía un secreto (el de R-01): queda fuera del meta-test con nombre y motivo (ver decisiones).

### Punto 4 — R-05

```
$ python -m pytest -q tests/test_workflows.py tests/test_workflows_H4.py tests/test_nav_probe_F2.py      (rojo: tests con el requisito nuevo, workflows viejos)
FAILED tests/test_workflows.py::test_supabase_keepalive_estructura - Assertio...
FAILED tests/test_workflows.py::test_personal_nav_el_token_solo_en_los_pasos_de_biblioteca_estandar
FAILED tests/test_workflows.py::test_personal_full_el_token_solo_en_los_pasos_de_biblioteca_estandar
FAILED tests/test_workflows_H4.py::test_ningun_workflow_deja_salir_la_url_ni_la_clave_de_supabase[nav-probe.yml]
FAILED tests/test_workflows_H4.py::test_ningun_workflow_deja_salir_la_url_ni_la_clave_de_supabase[personal-full.yml]
FAILED tests/test_workflows_H4.py::test_ningun_workflow_deja_salir_la_url_ni_la_clave_de_supabase[personal-nav.yml]
FAILED tests/test_workflows_H4.py::test_ningun_workflow_deja_salir_la_url_ni_la_clave_de_supabase[supabase-keepalive.yml]
FAILED tests/test_workflows_H4.py::test_los_workflows_que_usan_supabase_son_los_esperados_y_todos_leen_solo_secretos
FAILED tests/test_nav_probe_F2.py::test_workflow_probe_solo_manual_sin_escritura_y_con_token_solo_al_leer
9 failed, 366 passed in 44.61s
$ python -m pytest -q tests/test_workflows.py tests/test_workflows_H4.py tests/test_nav_probe_F2.py tests/test_workflows_F1.py   (verde)
427 passed in 44.76s
```

### Punto 5 — S-01

```
$ python -m pytest -q tests/test_yahoo_shadow_franjas_S01.py        (rojo, config vieja)
FAILED tests/test_yahoo_shadow_franjas_S01.py::test_cada_hora_real_de_arranque_de_personal_full_lee_con_el_libro_vacio[2026-11-16T01:20:48Z]
FAILED tests/test_yahoo_shadow_franjas_S01.py::test_cada_hora_real_de_arranque_de_personal_full_lee_con_el_libro_vacio[2026-11-17T04:09:52Z]
FAILED tests/test_yahoo_shadow_franjas_S01.py::test_cada_hora_real_de_arranque_de_personal_full_lee_con_el_libro_vacio[2026-11-17T16:26:22Z]
FAILED tests/test_yahoo_shadow_franjas_S01.py::test_cada_hora_real_de_arranque_de_personal_full_lee_con_el_libro_vacio[2026-11-18T03:06:55Z]
FAILED tests/test_yahoo_shadow_franjas_S01.py::test_cada_hora_real_de_arranque_de_personal_full_lee_con_el_libro_vacio[2026-11-18T15:56:34Z]
FAILED tests/test_yahoo_shadow_franjas_S01.py::test_cada_hora_real_de_arranque_de_personal_full_lee_con_el_libro_vacio[2026-11-19T02:45:58Z]
FAILED tests/test_yahoo_shadow_franjas_S01.py::test_cada_hora_real_de_arranque_de_personal_full_lee_con_el_libro_vacio[2026-11-19T16:01:22Z]
FAILED tests/test_yahoo_shadow_franjas_S01.py::test_cada_hora_real_de_arranque_de_personal_full_lee_con_el_libro_vacio[2026-11-20T02:33:45Z]
FAILED tests/test_yahoo_shadow_franjas_S01.py::test_cada_hora_real_de_arranque_de_personal_full_lee_con_el_libro_vacio[2026-11-20T15:43:28Z]
FAILED tests/test_yahoo_shadow_franjas_S01.py::test_cada_hora_real_de_arranque_de_personal_full_lee_con_el_libro_vacio[2026-11-21T02:47:57Z]
FAILED tests/test_yahoo_shadow_franjas_S01.py::test_cada_hora_real_de_arranque_de_personal_full_lee_con_el_libro_vacio[2026-11-21T14:54:26Z]
FAILED tests/test_yahoo_shadow_franjas_S01.py::test_cada_hora_real_de_arranque_de_personal_full_lee_con_el_libro_vacio[2026-11-22T01:32:19Z]
FAILED tests/test_yahoo_shadow_franjas_S01.py::test_cada_hora_real_de_arranque_de_personal_full_lee_con_el_libro_vacio[2026-11-22T14:51:44Z]
FAILED tests/test_yahoo_shadow_franjas_S01.py::test_cada_hora_real_de_arranque_de_personal_full_lee_con_el_libro_vacio[2026-11-23T01:56:51Z]
FAILED tests/test_yahoo_shadow_franjas_S01.py::test_cada_hora_real_de_arranque_de_personal_full_lee_con_el_libro_vacio[2026-11-23T18:42:25Z]
FAILED tests/test_yahoo_shadow_franjas_S01.py::test_las_franjas_cubren_las_24_horas_y_ninguna_hora_queda_fuera
FAILED tests/test_yahoo_shadow_franjas_S01.py::test_semana_real_una_lectura_por_run_y_nunca_mas_de_dos_al_dia
FAILED tests/test_yahoo_shadow_franjas_S01.py::test_semana_real_con_todo_medido_la_parada_temprana_deja_una_lectura_al_dia
18 failed, 18 passed in 0.57s
$ python -m pytest -q tests/test_yahoo_shadow*.py tests/test_workflow_yahoo_shadow.py      (verde)
177 passed in 1.34s
```

Los 18 que pasaban en rojo son los 15 controles negativos «con las franjas viejas esa hora no leía», el del control negativo del tope diario, el del
disparador horario (no depende de la franja) y el de «la puerta y los topes no cambian».

### Punto 6 — R-06

```
$ python -m pytest -q tests/test_privacidad_isin.py      (rojo: las funciones nuevas no existían)
E   ImportError: cannot import name 'isin_any_leaks' from 'privacy_guard' (/home/user/Portfolio_Dashboard/tests/privacy_guard.py). Did you mean: 'isin_leaks'?
ERROR tests/test_privacidad_isin.py
1 error in 0.09s
main: guardia viejo, ISIN LU válido inventado -> isin_leaks = 0
$ python -m pytest -q tests/test_privacidad_isin.py tests/test_fund_probe_generic_list.py tests/test_privacidad_lista_privada.py tests/test_workflows_F1.py   (verde; el skipped es el NO EJECUTADO de R-01)
107 passed, 1 skipped in 1.93s
$ (mutante 1: ISIN_ANY solo IE) python -m pytest -q tests/test_privacidad_isin.py
FAILED tests/test_privacidad_isin.py::test_control_negativo_detecta_un_isin_valido_de_otro_pais
FAILED tests/test_privacidad_isin.py::test_control_negativo_el_barrido_falla_con_un_isin_valido_de_otro_pais
2 failed, 22 passed in 0.72s
$ (mutante 2: la forma JWT nunca coincide) python -m pytest -q tests/test_privacidad_isin.py
FAILED tests/test_privacidad_isin.py::test_control_negativo_el_barrido_de_secretos_encuentra_cada_forma_en_cualquier_fichero[jwt]
1 failed, 23 passed in 0.68s
```

### Punto 7 — F-01 (cripto diminuta)

```
$ python -m pytest -q -s tests/test_zz_repro_tmp.py   (copia temporal de auditorias/logs_revision_nov2026/repro_cripto_precio_diminuto.py, sobre el código actual)
PRECIO_PUBLICADO 0.0 ERRORES ['crypto/latest.json: coins: elemento: price: debe ser > 0']
1 passed in 0.90s
$ python -m pytest -q tests/test_feed_v2_cripto_diminuta.py         (rojo)
FAILED tests/test_feed_v2_cripto_diminuta.py::test_una_moneda_diminuta_ya_no_bloquea_el_feed_y_no_se_publica_a_cero
FAILED tests/test_feed_v2_cripto_diminuta.py::test_una_cripto_del_registro_diminuta_queda_dormant_not_listed_en_health
FAILED tests/test_feed_v2_cripto_diminuta.py::test_normalize_coins_descarta_y_cuenta_las_diminutas
FAILED tests/test_feed_v2_cripto_diminuta.py::test_control_negativo_precio_cero_o_negativo_se_descartaban_ya_y_siguen_igual
4 failed, 2 passed in 2.22s
$ python -m pytest -q tests/test_feed_v2_cripto_diminuta.py tests/test_feed_v2.py tests/test_feed_v2_neg.py tests/test_sanity_check_v2.py tests/test_contract_docs_v2.py   (verde)
186 passed in 79.78s (0:01:19)
$ python -m pytest -q -s tests/test_zz_repro_tmp.py   (el repro del informe, sobre el código arreglado: DEBE fallar, porque afirmaba el defecto)
E       IndexError: list index out of range
tests/test_zz_repro_tmp.py:16: IndexError
1 failed in 0.91s
```

### Verificación final (rama en `3b0e3ce`), log íntegro

```
$ git rev-parse --short HEAD
3b0e3ce
$ ruff check --select F scripts tests
All checks passed!
exit=0
$ env -u PRIVATE_DENYLIST python -m pytest -q -rs
........................................................................ [  2%]
........................................................................ [  5%]
........................................................................ [  8%]
........................................................................ [ 11%]
........................................................................ [ 14%]
........................................................................ [ 17%]
........................................................................ [ 20%]
........................................................................ [ 23%]
........................................................................ [ 26%]
........................................................................ [ 29%]
........................................................................ [ 32%]
........................................................................ [ 35%]
........................................................................ [ 38%]
........................................................................ [ 41%]
........................................................................ [ 44%]
........................................................................ [ 47%]
........................................................................ [ 50%]
........................................................................ [ 53%]
.....................................s.................................. [ 56%]
........................................................................ [ 59%]
........................................................................ [ 62%]
........................................................................ [ 65%]
........................................................................ [ 68%]
........................................................................ [ 71%]
........................................................................ [ 74%]
........................................................................ [ 77%]
........................................................................ [ 80%]
........................................................................ [ 83%]
........................................................................ [ 86%]
........................................................................ [ 89%]
........................................................................ [ 92%]
........................................................................ [ 95%]
........................................................................ [ 97%]
.................................................                        [100%]
=============================== warnings summary ===============================
tests/test_backend_fixes2.py::test_BE14_source_y_published_tambien_son_texto_plano
  /usr/local/lib/python3.13/site-packages/feedparser/util.py:80: DeprecationWarning: To avoid breaking existing software while fixing issue 310, a temporary mapping has been created from `updated` to `published` if `updated` doesn't exist. This fallback will be removed in a future version of feedparser.
    warnings.warn(

-- Docs: https://docs.pytest.org/en/stable/how-to/capture-warnings.html
=========================== short test summary info ============================
SKIPPED [1] tests/test_privacidad_lista_privada.py:38: NO EJECUTADO: falta la lista privada (secreto de CI o variable de entorno local PRIVATE_DENYLIST); la comprobación de la lista del dueño NO se ha hecho en este run
2424 passed, 1 skipped, 1 warning in 247.24s (0:04:07)
exit=0
```

```
$ PRIVATE_DENYLIST="<texto protegido derivado en ejecución de la huella de main, no impreso>,<entrada inventada>" python -m pytest -q -rs tests/test_privacidad_lista_privada.py tests/test_privacidad_isin.py
...............................                                          [100%]
31 passed in 0.79s
exit=0
```

Con este informe ya en el árbol (después de sustituir la entrada inventada, ver el barrido):

```
$ PRIVATE_DENYLIST="<derivado, no impreso>,<entrada inventada>" python -m pytest -q -rs tests/test_privacidad_lista_privada.py tests/test_privacidad_isin.py tests/test_fund_probe_generic_list.py   (con el informe ya en el árbol)
56 passed in 0.87s
$ ruff check --select F scripts tests
All checks passed!
exit=0
$ env -u PRIVATE_DENYLIST python -m pytest -q -rs   (árbol final, con este informe)
=========================== short test summary info ============================
SKIPPED [1] tests/test_privacidad_lista_privada.py:38: NO EJECUTADO: falta la lista privada (secreto de CI o variable de entorno local PRIVATE_DENYLIST); la comprobación de la lista del dueño NO se ha hecho en este run
2424 passed, 1 skipped, 1 warning in 236.45s (0:03:56)
exit=0
```

### Barrido de privacidad del diff, de los mensajes de commit, de los logs de la sesión y del árbol

Script en el scratchpad (no versionado). Reglas: IE de siempre, ISIN de cualquier país con dígito de control (excepciones de R-06), ids de ficha, `0P…`,
formas de secreto, y los **8 textos protegidos** de `main` comprobados por sus huellas (sin conocer sus valores: se tokeniza el texto y se compara la huella).

```
diff main..HEAD, líneas AÑADIDAS: {'IE_no_permitido': 0, 'ISIN_cualquier_pais': 0, 'ids_ficha': 0, '0P_no_permitido': 0, 'formas_secreto': [], 'texto_lista_dueno(huellas_de_main)': 0, 'huella_de_main_literal': 0}
diff main..HEAD, líneas QUITADAS: {'IE_no_permitido': 0, 'ISIN_cualquier_pais': 0, 'ids_ficha': 0, '0P_no_permitido': 0, 'formas_secreto': [], 'texto_lista_dueno(huellas_de_main)': 1, 'huella_de_main_literal': 8}
mensajes de commit main..HEAD: {'IE_no_permitido': 0, 'ISIN_cualquier_pais': 0, 'ids_ficha': 0, '0P_no_permitido': 0, 'formas_secreto': [], 'texto_lista_dueno(huellas_de_main)': 0, 'huella_de_main_literal': 0} | Co-Authored/Claude: 0
log baseline.log: {'IE_no_permitido': 0, 'ISIN_cualquier_pais': 0, 'ids_ficha': 0, '0P_no_permitido': 0, 'formas_secreto': [], 'texto_lista_dueno(huellas_de_main)': 0, 'huella_de_main_literal': 0}
log baseline_main.log: {'IE_no_permitido': 0, 'ISIN_cualquier_pais': 0, 'ids_ficha': 0, '0P_no_permitido': 0, 'formas_secreto': [], 'texto_lista_dueno(huellas_de_main)': 0, 'huella_de_main_literal': 0}
log f01_repro.log: {'IE_no_permitido': 0, 'ISIN_cualquier_pais': 0, 'ids_ficha': 0, '0P_no_permitido': 0, 'formas_secreto': [], 'texto_lista_dueno(huellas_de_main)': 0, 'huella_de_main_literal': 0}
log f01_rojo.log: {'IE_no_permitido': 0, 'ISIN_cualquier_pais': 0, 'ids_ficha': 0, '0P_no_permitido': 0, 'formas_secreto': [], 'texto_lista_dueno(huellas_de_main)': 0, 'huella_de_main_literal': 0}
log f01_verde.log: {'IE_no_permitido': 0, 'ISIN_cualquier_pais': 0, 'ids_ficha': 0, '0P_no_permitido': 0, 'formas_secreto': [], 'texto_lista_dueno(huellas_de_main)': 0, 'huella_de_main_literal': 0}
log final_con_lista.log: {'IE_no_permitido': 0, 'ISIN_cualquier_pais': 0, 'ids_ficha': 0, '0P_no_permitido': 0, 'formas_secreto': [], 'texto_lista_dueno(huellas_de_main)': 0, 'huella_de_main_literal': 0}
log final_sin_lista.log: {'IE_no_permitido': 0, 'ISIN_cualquier_pais': 0, 'ids_ficha': 0, '0P_no_permitido': 0, 'formas_secreto': [], 'texto_lista_dueno(huellas_de_main)': 0, 'huella_de_main_literal': 0}
log r01_rojo.log: {'IE_no_permitido': 0, 'ISIN_cualquier_pais': 0, 'ids_ficha': 0, '0P_no_permitido': 0, 'formas_secreto': [], 'texto_lista_dueno(huellas_de_main)': 0, 'huella_de_main_literal': 0}
log r01_verde.log: {'IE_no_permitido': 0, 'ISIN_cualquier_pais': 0, 'ids_ficha': 0, '0P_no_permitido': 0, 'formas_secreto': [], 'texto_lista_dueno(huellas_de_main)': 0, 'huella_de_main_literal': 0}
log r02_mutante.log: {'IE_no_permitido': 0, 'ISIN_cualquier_pais': 0, 'ids_ficha': 0, '0P_no_permitido': 0, 'formas_secreto': [], 'texto_lista_dueno(huellas_de_main)': 0, 'huella_de_main_literal': 0}
log r02_rojo.log: {'IE_no_permitido': 0, 'ISIN_cualquier_pais': 0, 'ids_ficha': 0, '0P_no_permitido': 0, 'formas_secreto': [], 'texto_lista_dueno(huellas_de_main)': 0, 'huella_de_main_literal': 0}
log r02_verde.log: {'IE_no_permitido': 0, 'ISIN_cualquier_pais': 0, 'ids_ficha': 0, '0P_no_permitido': 0, 'formas_secreto': [], 'texto_lista_dueno(huellas_de_main)': 0, 'huella_de_main_literal': 0}
log r03_rojo.log: {'IE_no_permitido': 0, 'ISIN_cualquier_pais': 0, 'ids_ficha': 0, '0P_no_permitido': 0, 'formas_secreto': [], 'texto_lista_dueno(huellas_de_main)': 0, 'huella_de_main_literal': 0}
log r03_verde.log: {'IE_no_permitido': 0, 'ISIN_cualquier_pais': 0, 'ids_ficha': 0, '0P_no_permitido': 0, 'formas_secreto': [], 'texto_lista_dueno(huellas_de_main)': 0, 'huella_de_main_literal': 0}
log r05_rojo.log: {'IE_no_permitido': 0, 'ISIN_cualquier_pais': 0, 'ids_ficha': 0, '0P_no_permitido': 0, 'formas_secreto': [], 'texto_lista_dueno(huellas_de_main)': 0, 'huella_de_main_literal': 0}
log r05_verde.log: {'IE_no_permitido': 0, 'ISIN_cualquier_pais': 0, 'ids_ficha': 0, '0P_no_permitido': 0, 'formas_secreto': [], 'texto_lista_dueno(huellas_de_main)': 0, 'huella_de_main_literal': 0}
log r06_mutantes.log: {'IE_no_permitido': 0, 'ISIN_cualquier_pais': 0, 'ids_ficha': 0, '0P_no_permitido': 0, 'formas_secreto': [], 'texto_lista_dueno(huellas_de_main)': 0, 'huella_de_main_literal': 0}
log r06_rojo.log: {'IE_no_permitido': 0, 'ISIN_cualquier_pais': 0, 'ids_ficha': 0, '0P_no_permitido': 0, 'formas_secreto': [], 'texto_lista_dueno(huellas_de_main)': 0, 'huella_de_main_literal': 0}
log r06_verde.log: {'IE_no_permitido': 0, 'ISIN_cualquier_pais': 0, 'ids_ficha': 0, '0P_no_permitido': 0, 'formas_secreto': [], 'texto_lista_dueno(huellas_de_main)': 0, 'huella_de_main_literal': 0}
log s01_rojo.log: {'IE_no_permitido': 0, 'ISIN_cualquier_pais': 0, 'ids_ficha': 0, '0P_no_permitido': 0, 'formas_secreto': [], 'texto_lista_dueno(huellas_de_main)': 0, 'huella_de_main_literal': 0}
log s01_verde.log: {'IE_no_permitido': 0, 'ISIN_cualquier_pais': 0, 'ids_ficha': 0, '0P_no_permitido': 0, 'formas_secreto': [], 'texto_lista_dueno(huellas_de_main)': 0, 'huella_de_main_literal': 0}
árbol de la rama (todas las reglas salvo formas de secreto, que ya cubre el test con sus exenciones): {'IE_no_permitido': 2}
huellas de main que siguen como texto literal en el árbol: []
```

Lectura: en las líneas **añadidas** no hay nada. En las **quitadas** están las 8 huellas y 1 aparición de un texto protegido: es lo que este cambio retira, y el
diff de la rama (y del PR) lo enseña igual que ya lo enseña el historial de `main`. En el árbol de la rama, las 2 coincidencias de la regla IE son las dos
exenciones documentadas (`config/fund_probe_isins_generic.json`, y el canario de `config/yahoo_shadow.json`, que es de esa lista). Ninguna huella vieja queda en el árbol.
Mensajes de commit: 0 coincidencias y 0 líneas de atribución. Además, al pasar el test de la lista privada con este informe ya escrito, **falló**: la lista local
llevaba una segunda entrada inventada (para comprobar el troceo por comas) y esa entrada aparecía en los comandos copiados aquí. Se sustituyó por
`<entrada inventada>`; el texto protegido real daba 0. Es el guardia funcionando sobre `auditorias/`. El log `r06_mutantes.log` se regeneró después de dar a la prueba de formas de secreto `ids` propios:
la primera versión repetía en el id de pytest un JWT inventado (no real, pero el barrido del árbol lo habría marcado si se copiaba aquí).

---

## 2. Qué demuestra cada prueba y qué NO

**R-01.** Demuestra: el árbol no lleva ninguna de las 8 huellas ni el texto protegido que se conocía; con la lista cargada, el barrido de todo el árbol falla
con fichero y nº (control negativo con un canario inventado) y nunca repite el valor; sin lista sale `skipped` con «NO EJECUTADO» (nunca `passed`); `ci.yml`
pasa el secreto solo al paso de tests, imprime el motivo de los `skipped` (`-rs`) y deja un `::warning::`. **No demuestra**: que los otros 7 textos no
estén en el árbol **con la lista real** (lo he comprobado por huella en el barrido de arriba, pero el test del repo solo lo hará cuando exista el secreto); que
GitHub inyecte el secreto (Actions no se ha ejecutado); que la lista que cree Alex esté completa. **Podría repetir el error del código**: el barrido usa el mismo
tokenizador que el guardia (`[A-Za-z0-9.^=\-]+`, palabra o par); un texto protegido partido de otra forma (p. ej. con `_` o `/` en medio) no casaría en ninguno de los dos.
**Sería incorrecto si** el secreto se guarda con un formato que el lector no entiende (p. ej. JSON): `load_denylist` lo trocearía mal y el barrido pasaría en verde sin
proteger; por eso el formato va documentado en el docstring y en el README.

**R-02.** Demuestra: con HTML real de ficha (fixture) y una XHR simulada, el log no lleva ningún valor de NAV con 2 a 6 decimales, punto o coma; dice sí / no /
sin base; el mutante que vuelve a imprimir el valor muere en 4 tests. **No demuestra**: lo que haga iShares real (el script no se ha ejecutado contra la web). Los
nombres de campos JSON de las XHR sí se siguen imprimiendo (no son valores). **Sería incorrecto si** un número de la cabecera de caché (`age=…`) coincidiese
con el NAV: el detector lo marcaría como fuga (falso positivo, no fuga real).

**R-03.** Demuestra: los 5 workflows con jobs que llevan secretos exigen `refs/heads/main` en la condición del job; un workflow nuevo con secretos rompe el
meta-test hasta que se añade; quitar la condición de `nav-probe.yml` hace fallar el test (control negativo). **No demuestra**: el comportamiento real de GitHub con
un `workflow_dispatch` desde otra rama (lo evalúa el propio test con el evaluador de condiciones del repo, no Actions).

**R-05.** Demuestra: ningún workflow lee `vars.SUPABASE_*`; el detector marca ahora el respaldo viejo como fuga (control negativo con las dos expresiones).
**No demuestra**: que los secretos existan en el repo (el informe de la revisión vio `***` en el log real de `personal-full`, así que al menos la URL existe como secreto;
la clave publicable no se puede ver desde aquí). Si alguno faltase, los jobs personales avisarían y saldrían sin publicar (comportamiento existente sin Supabase).

**S-01.** Demuestra: con la config del repo, cada una de las 15 horas reales de arranque lee con el libro vacío (y con las franjas viejas no leía: control negativo
por hora); la semana real con el mismo libro da 1 lectura el 16-11 (un solo run) y 2 los demás días, nunca más de 2; con todo medido, la parada temprana deja 1
lectura al día; un disparador horario (AU-01) sigue en 2 como mucho; el tope diario de verdad limita (control negativo con 4 franjas y tope 4). Puerta D7,
`max_reads_per_day` y `max_requests_per_run` intactos (test). **No demuestra**: que la sombra mida bien en un run real (Yahoo, caché de Actions); que el libro se guarde
(el paso de guardado solo corre con `ledger=written`, que ahora sí debería darse).

**R-06.** Demuestra: ISIN inventados de LU/DE/FR/US con dígito válido se detectan en cualquier fichero; con dígito inválido o en minúsculas no; la lista genérica solo
pasa si se da como excepción; cada forma de secreto se detecta en un fichero cualquiera (`docs/…`) y no en un test exento; mutantes «solo IE» y «sin JWT» mueren.
**No demuestra**: nada del historial de git ni de las ramas `data`/`probe-data`, artefactos o resúmenes de jobs (fuera de lo pedido; ver pendientes). La regla de
cualquier país va **anclada** (no pegada a letras, dígitos o `_`): un ISIN no irlandés pegado a otro texto no se detecta (la regla IE sí, sin anclar).

**F-01.** Demuestra: con 3·10⁻⁹ el sitio valida sin errores, la moneda no se publica y el log lo avisa; una cripto del registro diminuta queda `dormant/not_listed`
en `health.json` (canal que ya existe en el contrato); por encima del umbral el redondeo es el de siempre; el validador sigue rechazando un `0.0` (control negativo);
el script de reproducción del informe, sobre el código nuevo, ya no encuentra la moneda (`IndexError`: afirmaba el defecto). **No demuestra**: que hoy exista una
moneda así en el top 250 (sin red a CoinGecko).

---

## 3. Decisiones y supuestos (con la postura contraria; decide Alex)

1. **R-01, secreto en claro en vez de HMAC.** Elegido: la lista entera en el secreto `PRIVATE_DENYLIST`, una línea, entradas separadas por comas.
   *Contraria*: HMAC con clave secreta dejaría las huellas versionadas (se ve en un diff cuándo cambia la lista) sin revelar los valores. Exige un secreto igualmente y
   más código; no compensa para 8 palabras.
2. **R-01, sin secreto = `skipped` + `::warning::` en todos los eventos.** *Contraria (la recomiendo valorar)*: en `push` a `main` el secreto debe existir siempre;
   si alguien lo borra, la comprobación quedaría desactivada en silencio (solo un aviso amarillo). Se podría hacer **fallar** el paso cuando
   `github.event_name == 'push'` y falte el secreto. No lo he hecho porque el brief decía «NO EJECUTADO, nunca aprobado», no «rojo».
3. **R-01, quién ve el secreto.** El paso de tests de `ci.yml` también importa código de terceros (yfinance, pandas…); con el secreto en su entorno, un paquete
   comprometido podría leerlo. Mitigado por `--require-hashes` y el *cooldown* de Dependabot (igual que `PROBE_ISINS` en R-08). GitHub enmascara la cadena entera del
   secreto, **no cada palabra**: si algún día un test imprimiese una palabra suelta de la lista, no saldría como `***`. Los tests nuevos nunca la imprimen.
4. **R-01, barrido de todo el árbol en vez de por fichero.** Los 4 tests que comprobaban ficheros concretos con las huellas ahora solo hacen sus otras comprobaciones
   (ISIN, «Class S»…), y la lista privada la comprueba un único test sobre todo el árbol (incluidos `auditorias/` y `docs/`). Más amplio que antes.
5. **R-02, sí/no frente a `::add-mask::`.** *Contraria*: enmascarar el valor dejaría el log igual de útil. Pero un número corto enmascarado tacha también cualquier otra
   aparición del mismo número en el log, y el «sí/no» es lo que el diagnóstico necesita (¿la variante trae el mismo NAV?).
6. **R-03, excepciones del meta-test.** Fuera, con nombre: `update-data.yml` (v1, «no tocar v1», W-02), `supabase-keepalive.yml` (solo clave publicable y latido; fuera
   del alcance pedido) y `ci.yml` (su secreto es el de R-01 y tiene que correr en cada PR).
7. **S-01, dos franjas anchas (00-12 y 12-24) en vez de quitar las franjas.** Solo cambia `config/yahoo_shadow.json`: el código, el esquema del libro y los tests del
   mecanismo siguen valiendo. *Contrarias*: (a) dos lecturas pueden caer muy juntas (11:50 y 12:10); una regla de «separación mínima» lo evitaría; la parada temprana lo
   mitiga si la primera lo midió todo. (b) Leer a las 01-04 UTC puede dar muchos «Yahoo por detrás» (aún sin el NAV del día anterior): no ensucia la puerta, que solo
   cuenta comparables de la misma fecha, pero cuesta peticiones. (c) Lanzar `personal-full` desde el disparador externo (AU-01) cuando funcione.
8. **R-06, excepciones de ISIN.** Además de lo pedido (lista genérica y sintéticos) he exceptuado el país ficticio `ZZ` (código de usuario en ISO 3166, ningún ISIN real
   lo lleva; lo usan los tests de la sonda) y los **4 ejemplos públicos de la norma ISO 6166** (Apple, BHP, BAE Systems, Bayer: empresas cotizadas, no fondos) que usan
   los tests del dígito de control. *Contraria*: construirlos en ejecución en esos tests (como ya se hace con un ISIN irlandés) y no abrir excepción.
9. **R-06, regla anclada para cualquier país.** Sin anclar, el árbol daba falsos positivos con trozos de textos inventados (claves `FAKEKEY…`, etc.). La regla IE de
   siempre sigue sin anclar.
10. **F-01, descartar frente a 8 cifras significativas.** Seguido lo pedido (descartar, no publicar 0). *Contraria* (la del informe): 8 cifras significativas conservaría la
    moneda; cambia la representación numérica de `price` y el cliente podría pintar `0.00000000` igualmente.
11. **F-01, aviso en `health.json`: no se hace, cambiaría el contrato.** `HEALTH_SPEC` es un esquema cerrado (`obj` con lista blanca) y el contrato documenta las claves
    de `problems` y `counts`. Un aviso propio (p. ej. `problems.crypto_dropped: [ids]` y `counts.crypto_dropped`) exigiría: añadir la clave en `HEALTH_SPEC`, en el
    ejemplo y la tabla de `docs/CONTRATO_FEED_V2.md`, y que el cliente la tolere (si el cliente valida con esquema cerrado, rompería). Lo que sí hay sin cambiar
    contrato: aviso en el log del run, `counts.crypto` baja en uno y, si es una cripto del registro, `symbols.<SYM>` = `dormant/not_listed`.

## 4. Desviaciones (desde el principio)

* **Primera pasada completa con 2 fallos** (inventario de secretos D44 en `test_extra_D.py` y `test_fund_lag_workflow.py`): R-01 añade el secreto `PRIVATE_DENYLIST` y esos
  tests fijan la lista exacta de secretos. Se arreglaron (REQUISITO CAMBIADO) dentro del commit de R-01 con `git commit --fixup` + `rebase --autosquash` sobre mi propia
  rama sin publicar. Fallo mío: entre puntos solo corrí los tests del área, no la suite entera.
* **REQUISITO CAMBIADO** en tests viejos (todos marcados en el código): R-02 (`NAV <fecha> = <valor>` → fecha + sí/no, 2 tests); R-05 (los tests exigían
  `secrets.X || vars.X`; ahora solo `secrets.X`, y el caso `secrets.SUPABASE_URL` sin respaldo, que era control negativo, pasa a ser lo correcto); S-01 (franjas fijadas
  en `test_yahoo_shadow_resolucion.py`; el test de «fuera de franja» pasa a usar las franjas viejas explícitas para seguir probando el mecanismo); D44 (+ `PRIVATE_DENYLIST`).
* **Rojo de R-06** fue un `ImportError` (las funciones no existían); el rojo con contenido es la línea de `main`: el guardia viejo da 0 con un ISIN `LU` válido.
* `tree_files` pasa de `test_privacidad_isin.py` a `privacy_guard.py` (lo comparten el barrido de ISIN y el de la lista privada).
* `auditorias/logs_revision_nov2026/barrido_historial_git.py` (evidencia de la revisión, no es un test) importa las constantes de huellas retiradas: si se vuelve a
  ejecutar, falla. No lo he tocado (es el registro de la revisión).
* El informe de la revisión dice «8 runs reales de `personal-full`»; su propio `runs_reales_resumen.txt` lista 15 `schedule`. He usado los 15.
* Rama: la pedida, `claude/fix-revision-publico`.

## 5. Pendiente o sin probar

* **NO PROBADO**: GitHub Actions real (inyección del secreto, el `::warning::`, el `skipped` en PR de forks y de Dependabot), Pages, Yahoo, iShares y CoinGecko reales.
  Ningún workflow se ha lanzado.
* **Consumo de Yahoo de la sombra tras S-01 (INFERIDO, no medido)**: por lectura, canario 3 + 3 por fondo (búsqueda 1 + histórico 2, el peor caso que cuenta el `Gate`);
  con los 2 fondos con ficha que vio la revisión, **~9 peticiones por lectura, ≤ 2 lecturas al día → ~18 al día**. Frente a lo medido (feed v2: 252 por run, ~2-3 runs al
  día ≈ 500-650 al día; fund-lag ~10 por run): **~3 %** más. Peor caso teórico con el tope propio (60 por lectura): 120 al día (~20 %). Frente a su propio tope por run:
  9/60 = **15 %**. Ningún indicador llega al 50 %. Antes de S-01 el consumo real era 0 (la sombra no leía); el diseñado era el mismo (2 lecturas al día).
* Historial y ramas `data`/`probe-data`, artefactos y resúmenes de jobs siguen sin barrido automático (parte de R-06/T-03 que no se pidió: workflow semanal).
* Hallazgos de la revisión fuera de este encargo: R-04, R-07, W-01…W-07, M-0x, T-07/T-08, C-01/C-07, S-03, D-04, #10.

## 6. R-01: lo que queda en el historial y opciones (decide Alex; NO se ha reescrito nada)

Queda en el historial de `main` (y en las ramas que lo contienen): las **8 huellas** en todas las versiones de `tests/privacy_guard.py` desde que se añadieron, y el
**símbolo en claro** en las versiones anteriores de los 2 tests. El diff de esta rama (y el del PR) también enseña esas líneas como quitadas. Las 4 huellas que la
revisión revirtió con un diccionario siguen siendo reversibles para cualquiera; las otras 4 no se revirtieron con 297 candidatos, pero son igual de cortas.

1. **No hacer nada más (recomendado).** El dato es poco sensible (qué símbolos comunes sigue el dueño, sin cantidades ni ISIN) y ya es público: clones, forks y la
   caché de GitHub (los commits siguen accesibles por SHA) lo conservan aunque se reescriba. Con este cambio deja de crecer y deja de estar «al lado» del texto en claro.
2. **Reescribir el historial** (`git filter-repo` en `main`, `data`, `probe-data` y la de Dependabot + `push --force`). Cambian todos los SHA; las referencias de PR
   (`refs/pull/*`) no las puede reescribir el dueño y siguen enseñando los commits viejos hasta que lo pida a GitHub Support; los forks no se tocan; hay que rehacer las
   ramas abiertas y comprobar `keepalive.yml`. Mucho coste para borrar algo que ya pudo copiarse.
3. **Recrear el repo** (lo que el README llama BE-09). Borra el pasado del repo nuevo, pero hay que reconfigurar secretos, Pages, variables y el disparador externo, y el
   repo viejo (y sus forks) siguen hasta que se borran. Solo tiene sentido si ya se iba a hacer por otro motivo; entonces conviene juntarlo.
4. **Cambiar la propia lista** (dejar de usar esos proxies o esas monedas): no aplica en la práctica; lo dejo por completitud.

## 7. Qué tiene que hacer Alex a mano

1. **Crear el secreto `PRIVATE_DENYLIST`** (Settings → Secrets and variables → Actions → *New repository secret*): **una sola línea**, entradas separadas por comas, una o
   dos palabras cada una, da igual mayúsculas (p. ej. `simbolo1.de, nombre de moneda, ...`): los proxies de tus fondos y tu lista cripto idiosincrática (las 8 que antes
   iban como huella y las que quieras añadir). No lo pegues en el chat.
   **Cómo comprobarlo**: en el siguiente run de CI sobre `main`, el paso de tests **no** debe enseñar el `::warning::` de `PRIVATE_DENYLIST` y la línea final de
   pytest no debe enseñar ningún `SKIPPED … NO EJECUTADO`. **Control negativo** (en una rama de usar y tirar, sin fusionar): añade
   una de las palabras a un `.md` y abre un PR desde una rama del propio repo: CI debe fallar con «tests/…: 1 coincidencias con la lista privada», sin la palabra.
   En local: `export PRIVATE_DENYLIST="..."` y `python -m pytest -q -rs tests/test_privacidad_lista_privada.py`.
2. **Borrar las *variables* de repo `SUPABASE_URL` y `SUPABASE_PUBLISHABLE_KEY`** si aún existen (ya no se leen) y confirmar que existen como **secretos**. Tras fusionar,
   lanza a mano «Supabase keepalive»: verde con «Supabase responde (HTTP 200|401|403)»; si falta un secreto, sale «faltan los secretos …».
3. **Decidir sobre el historial** (sección 6).
4. Tras fusionar, mirar el siguiente run de `personal-full`: la línea de la sombra debe ser de una lectura (contadores, no «fuera de franja») y el paso «save aggregate
   counters» debe correr; en el siguiente run, «restore» debe encontrar la caché. La puerta D7 necesita ≥ 10 días hábiles con comparables.
5. Decidir los puntos de criterio 2 (¿rojo en `push` a `main` sin secreto?), 7 (franjas) y 11 (aviso en `health.json`, que cambia el contrato).
