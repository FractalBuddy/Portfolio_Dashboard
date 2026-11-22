# FIXES_NAV_F1_SOMBRA: fase F1 «sombra» del NAV vía Yahoo/Morningstar (solo medida, no publica nada)

Rama: `claude/nav-f1-sombra`, desde `main` = `e52e1ca` (#11). Autor y committer de todos los commits: Alex Martinez, con el email noreply de GitHub que ya usan
sus commits en el repo. Sin coautoría ni atribución a herramientas de IA en commits, en este informe ni en el PR.
Diseño de referencia: `auditorias/DISENO_NAV_YAHOO.md` del repo privado de la app (`main` = `9679030`), leído entero; sobre todo §2, §3, §5 (fila F1), §7 y §8.
**No he lanzado ningún workflow, no he tocado secretos ni variables, no he hecho ninguna petición real a Yahoo ni a Supabase y no he publicado nada.**
**Nada de lo que sigue es un dato real**: Yahoo, Supabase y la ficha de iShares están simulados; los ISIN, símbolos, nombres y precios son inventados.

Ficheros NUEVOS: `scripts/yahoo_shadow.py`, `config/yahoo_shadow.json`, `tests/yahoo_shadow_fakes.py`, `tests/test_yahoo_shadow_{resolucion,comparacion,divisa,run,canario,libro,privacidad,integracion}.py`,
`tests/test_workflow_yahoo_shadow.py` y este informe.
Ficheros MODIFICADOS: `.github/workflows/personal-full.yml` (tres pasos al final y comentario de cabecera; nada de lo anterior cambia), `tests/privacy_guard.py` y
`tests/test_privacidad_isin.py` (guardia ampliado a `0P…`), `tests/test_workflows.py` (dos regex de «acción fijada por SHA» admiten una subruta del mismo repo,
ver §4 D-4) y `README.md` (sección «Sombra NAV Yahoo (F1)»).

## 0. Qué es, en cuatro líneas

Tras publicar, `personal-full` ejecuta `scripts/yahoo_shadow.py`. Para cada fondo que **ya** tiene ficha de iShares **y** NAV oficial con fecha en ese run, hace
**una** búsqueda por ISIN en Yahoo, se queda con un `0P…` cuyo nombre cuadre con el del fondo (resolución automática y **no se guarda**, D1 A), pide **un**
histórico `5d` sin ajustar y compara con el NAV oficial **solo** si hay barra de la **misma fecha**. Salen contadores al log y al resumen del job, y se acumulan en un
libro (solo números y fechas) que guarda la caché de Actions para poder evaluar la puerta de 10 días hábiles. No llama a Supabase, no escribe en el repo ni en
artefactos, no cambia nada de lo que ve un usuario, y no puede poner el job en rojo.

## 1. Comandos y salidas en bruto

Entorno: venv fuera del árbol con `pip install --require-hashes --no-deps -r requirements.txt` y `-r requirements-dev.txt` (yfinance 1.7.0, pytest 9.2.0,
ruff 0.17.3, PyYAML 6.0.3). **Python 3.13 aquí; el runner usa 3.14.** Ninguna prueba toca la red (`tests/conftest.py`).

### 1.1 Base (antes de cambiar nada)

```
$ python -m pytest -q
2180 passed, 1 warning in 208.93s (0:03:28)
```

### 1.2 Rojo -> verde -> control negativo, por punto (un commit por punto)

Las mutaciones las aplica un script fuera del árbol (`mut.sh`): cambia el código, corre las pruebas del punto (deben FALLAR) y restaura el fichero. Las salidas
son las del script, sin retocar; las tres NOTAS explican intentos fallidos míos que rehíce (no los oculto).

```
##### Punto 1 — guardia de privacidad ampliado a 0P… + configuración versionada   (commit b07129c)
--- rojo
FAILED tests/test_privacidad_isin.py::test_no_hay_simbolos_0p_no_permitidos_en_el_arbol
FAILED tests/test_privacidad_isin.py::test_control_negativo_detecta_un_0p_no_permitido
FAILED tests/test_privacidad_isin.py::test_los_0p_de_la_lista_fija_son_exactamente_los_congelados_por_huella
FAILED tests/test_privacidad_isin.py::test_control_negativo_el_barrido_0p_falla_si_aparece_un_fichero_con_un_0p_ajeno
FAILED tests/test_privacidad_isin.py::test_el_canario_de_la_sombra_es_un_isin_de_la_lista_generica_publica
5 failed, 9 passed in 0.51s
--- verde
14 passed in 10.75s
== M1 zero_p_leaks siempre 0
MUTACIÓN CAZADA: 2 failed, 12 passed in 0.56s
== M2 exención del canario abierta a cualquier ISIN
MUTACIÓN CAZADA: 1 failed, 13 passed in 0.46s
== M3 se quita la exención por huella de la lista fija (debe saltar el barrido del árbol)
MUTACIÓN CAZADA: 2 failed, 12 passed in 0.49s

##### Punto 2 — resolución ISIN -> 0P… con el nombre validado   (commit 8fe2a94)
--- rojo
ERROR tests/test_yahoo_shadow_resolucion.py
!!!!!!!!!!!!!!!!!!!! Interrupted: 1 error during collection !!!!!!!!!!!!!!!!!!!!     (el módulo aún no existía)
--- verde
42 passed in 0.49s
== M1 sin la comprobación de alcance (Small/ESG)
MUTACIÓN CAZADA: 3 failed, 39 passed in 0.46s
== M2 se acepta cualquier 0P sin mirar el nombre
MUTACIÓN CAZADA: 1 failed, 41 passed in 0.46s
== M3 el empate elige el primero (sin «ambiguo»)
MUTACIÓN CAZADA: 1 failed, 41 passed in 0.50s
== M4 regex de símbolo abierta (cualquier 0P…)
MUTACIÓN CAZADA: 6 failed, 36 passed in 0.53s
== M5 ETF cuenta como candidato (D9)
MUTACIÓN CAZADA: 2 failed, 40 passed in 0.50s
== M6 no se enmascara el 0P
MUTACIÓN CAZADA: 2 failed, 40 passed in 0.48s
== M7 canario fuera de la lista genérica aceptado
MUTACIÓN CAZADA: 1 failed, 41 passed in 0.48s

##### Punto 3 — comparación solo con fecha idéntica, pb y bandas   (commit ffe64a3)
--- rojo
21 failed in 0.55s
--- verde
21 passed in 0.41s
== M1 comparar con la barra más cercana ANTERIOR (dos líneas cambiadas a la vez)
MUTACIÓN CAZADA: 11 failed, 10 passed in 0.47s
== M2 sin redondear (se usa raw)
MUTACIÓN CAZADA: 4 failed, 17 passed in 0.47s
== M3 HALF_EVEN
MUTACIÓN CAZADA: 1 failed, 20 passed in 0.45s
== M4 pb en tanto por mil (×1000)
MUTACIÓN CAZADA: 4 failed, 17 passed in 0.44s
== M5 «igual» con < en vez de ≤
MUTACIÓN CAZADA: 1 failed, 20 passed in 0.49s
== M6 sin banda de precio válido
MUTACIÓN CAZADA: 6 failed, 15 passed in 0.51s
== M7 sin marcar fuera de banda
MUTACIÓN CAZADA: 1 failed, 20 passed in 0.46s
== M8 la barra de hoy cuenta
MUTACIÓN CAZADA: 1 failed, 20 passed in 0.55s
== M9 decimales fijos a 2 (ignora los del oficial)
MUTACIÓN CAZADA: 2 failed, 19 passed in 0.44s
NOTA: un primer intento de M1 cambiaba solo la línea 'y = valid[official_day]' y NO se cazó (21 passed): era una mutación equivalente, porque esa línea solo
se alcanza cuando la fecha idéntica existe. Se rehízo cambiando también la condición previa (arriba).

##### Punto 4 — divisa distinta y subunidad (GBp)   (commit 25d47b7)
--- rojo
12 failed in 0.49s
--- verde (con las del punto 3)
33 passed in 0.42s
== M1 sin comprobar la divisa
MUTACIÓN CAZADA: 12 failed in 0.45s
== M2 GBp se convierte en silencio (÷100) y se trata como EUR
MUTACIÓN CAZADA: 1 failed, 11 passed in 0.42s
== M3 subunidad sin marcar
MUTACIÓN CAZADA: 1 failed, 11 passed in 0.42s
== M4 divisa en minúsculas aceptada como EUR
MUTACIÓN CAZADA: 2 failed, 10 passed in 0.42s

##### Punto 5 — recorrido completo; sin 0P, sin NAV oficial, Yahoo caído/429/vacío   (commit afb91d7)
--- rojo
15 failed in 0.56s
--- verde
15 passed in 0.46s
== M1 un fallo de Yahoo sale con código 1
MUTACIÓN CAZADA: 2 failed, 13 passed in 0.48s
== M2 la excepción inesperada se propaga
MUTACIÓN CAZADA: 2 failed, 13 passed in 0.52s
== M3 se sigue pidiendo tras un 429
MUTACIÓN NO CAZADA (MAL): 15 passed in 0.43s
== M4 sin deduplicar ISIN entre usuarios
MUTACIÓN CAZADA: 1 failed, 14 passed in 0.47s
== M5 se pide a Yahoo también sin NAV oficial
MUTACIÓN CAZADA: 2 failed, 13 passed in 0.45s
== M6 histórico ajustado/periodo largo (period=max)
MUTACIÓN CAZADA: 1 failed, 14 passed in 0.49s
== M7 vacío contado como error
MUTACIÓN CAZADA: 1 failed, 14 passed in 0.50s
NOTA M3: equivalente — quitar la comprobación del bucle no cambia nada porque Gate.call ya lanza Stopped sin hacer la petición (la parada vive en la Gate
reutilizada de fund_probe).
== M3b la Gate reutilizada no para ante un 429
MUTACIÓN CAZADA: 1 failed, 14 passed in 0.50s

##### Punto 6 — canario   (commit 914a19f)
--- rojo
7 failed, 2 passed in 0.61s
--- verde (con las del punto 5)
24 passed in 0.59s
== M1 canario mal pero se sigue pidiendo
MUTACIÓN CAZADA: 3 failed, 6 passed in 0.50s
== M2 sin canario (no se lee)
MUTACIÓN CAZADA: 9 failed in 0.59s
== M3 canario sin_0p también para todo
MUTACIÓN CAZADA: 3 failed, 6 passed in 0.55s
== M4 canario que no valida el nombre
MUTACIÓN CAZADA: 5 failed, 4 passed in 0.52s

##### Punto 7 — libro de contadores, franjas con parada temprana y puerta   (commit 0f603cb)
--- rojo
19 failed, 2 passed in 0.71s
--- verde
21 passed in 0.60s
== M1 sin franjas (lee siempre)
MUTACIÓN CAZADA: 1 failed, 20 passed in 0.64s
== M2 sin «franja ya leída»
MUTACIÓN CAZADA: 1 failed, 20 passed in 0.66s
== M3 sin parada temprana
MUTACIÓN CAZADA: 1 failed, 20 passed in 0.64s
== M4 parada temprana sin mirar la fecha oficial
MUTACIÓN CAZADA: 1 failed, 20 passed in 0.66s
== M5 cuenta doble la misma fecha oficial (suma en vez de máximo)
MUTACIÓN CAZADA: 2 failed, 19 passed in 0.71s
== M6 sábados y domingos cuentan como hábiles
MUTACIÓN CAZADA: 1 failed, 20 passed in 0.68s
== M7 umbral estricto > en vez de ≥ 95 %
MUTACIÓN CAZADA: 1 failed, 20 passed in 0.70s
== M8 >10 pb no tumba la puerta hasta el día 10
MUTACIÓN CAZADA: 1 failed, 20 passed in 0.78s
== M9 el libro guarda el ISIN (fuga): se añade el ISIN de un fondo a la entrada (prueba del contenido del libro)
MUTACIÓN CAZADA: 1 failed, 20 deselected in 0.47s
NOTA: un primer intento de M9 usaba una variable inexistente y fallaba por NameError (16 failed), no por el contenido; se rehízo.
== M10 libro ilegible aceptado sin validar
MUTACIÓN CAZADA: 2 failed, 19 passed in 0.66s
== M11 no se poda por ledger_days
MUTACIÓN CAZADA: 1 failed, 20 passed in 0.68s
== M12 no se avisa a la caché (ledger=written)
MUTACIÓN CAZADA: 2 failed, 19 passed in 0.75s

##### Punto 8 — privacidad de extremo a extremo   (commit e7b67f2)
(sin rojo propio: el comportamiento se construyó en los puntos 2-7; aquí el «rojo» son las mutaciones, M1 es el control negativo pedido)
--- verde
9 passed, 3 warnings in 0.51s
== M1 (control negativo pedido) un print del ISIN en el módulo
MUTACIÓN CAZADA: 1 failed, 8 passed, 3 warnings in 0.49s
== M2 el aviso de fallo inesperado cita el texto de la excepción
MUTACIÓN CAZADA: 1 failed, 8 passed, 3 warnings in 0.68s
== M3 sin silenciar yfinance en el histórico
MUTACIÓN CAZADA: 1 failed, 8 passed, 15 warnings in 0.53s
== M4 sin máscara de los ISIN al empezar
MUTACIÓN CAZADA: 1 failed, 8 passed, 3 warnings in 0.60s
== M5 la Gate reutilizada conserva el texto de la excepción original como causa visible
MUTACIÓN CAZADA: 1 failed, 8 passed, 3 warnings in 0.55s
== M6 el libro guarda los nombres
MUTACIÓN CAZADA: 1 failed, 8 passed, 3 warnings in 0.60s
== M7 el resumen lleva el precio oficial
MUTACIÓN CAZADA: 2 failed, 7 passed, 3 warnings in 0.52s

##### Punto 9 — pasos en personal-full.yml   (commit 6a1e179; pruebas del punto + test_workflows*.py)
--- rojo
8 failed, 2 passed in 0.54s
--- verde
400 passed in 42.63s
== M1 sin continue-on-error en la medida
MUTACIÓN CAZADA: 1 failed, 363 passed in 44.77s
== M2 el token llega al paso de la sombra
MUTACIÓN CAZADA: 3 failed, 361 passed in 44.16s
== M3 sombra ANTES de publicar
MUTACIÓN CAZADA: 2 failed, 362 passed in 45.18s
== M4 guardar el libro siempre (sin mirar ledger=written)
MUTACIÓN CAZADA: 1 failed, 363 passed in 45.00s
== M5 acción de caché por etiqueta en vez de SHA
MUTACIÓN CAZADA: 3 failed, 361 passed in 45.50s
== M6 clave fija en la restauración (la caché nunca cambiaría de entrada)
MUTACIÓN CAZADA: 1 failed, 9 passed in 0.49s
NOTA: el primer intento de M6 tenía un patrón que no casaba y el script de mutaciones siguió sin aplicarla (364 passed); se corrigió el script para abortar en
ese caso y se repitió.
== M7 permiso de escritura en el job
MUTACIÓN CAZADA: 2 failed, 362 passed in 39.51s

##### Integración con el Supabase simulado y cliente real con yfinance simulado   (commit 91bd87d)
--- verde
2 passed in 0.53s
== M1 el cliente real pide el histórico ajustado
MUTACIÓN CAZADA: 1 failed, 1 passed in 0.47s
== M2 la sombra relee la lista en Supabase (petición de más)
MUTACIÓN CAZADA: 1 failed, 1 passed in 0.56s
== M3 el NAV oficial no es el de la ficha (desviado un 1 %)
MUTACIÓN CAZADA: 1 failed, 1 passed in 0.49s
```

Tres fallos intermedios más, contados tal cual: (1) la primera suite completa (tras el punto 9) dio `1 failed, 2323 passed`: el guardia ampliado en el punto 1
cazó el ISIN **público** del canario escrito a mano en `tests/yahoo_shadow_fakes.py` desde el punto 2 (yo solo corría las pruebas de cada punto). No relajé el
guardia: el fake lo lee ahora de la configuración (commit `4cd3eec`). Los commits 2-9, por tanto, tienen ese guardia en rojo; el PR final no. (2) En el punto 8,
una prueba daba «fuga» porque leía como salida el libro corrupto que ella misma escribe como entrada; se excluyó ese fichero de entrada en esa prueba. (3) Con este informe ya escrito, la suite dio `1 failed, 2325 passed`: el guardia cazó el ISIN público del canario dentro del barrido de §8
pegado aquí. Se sustituyó por una referencia; la suite volvió a `2326 passed` (§1.3).

### 1.3 Comandos del CI, al final (salida en bruto)

```
$ ruff check --select F scripts tests
All checks passed!
(exit 0)
$ python -m py_compile scripts/yahoo_shadow.py
py_compile OK
$ python -m pytest -q
tests/test_backend_fixes2.py::test_BE14_source_y_published_tambien_son_texto_plano
  <venv>/lib/python3.13/site-packages/feedparser/util.py:80: DeprecationWarning: To avoid breaking existing software while fixing issue 310, a temporary mapping has been created from `updated` to `published` if `updated` doesn't exist. This fallback will be removed in a future version of feedparser.
    warnings.warn(

-- Docs: https://docs.pytest.org/en/stable/how-to/capture-warnings.html
2326 passed, 1 warning in 215.44s (0:03:35)
(exit 0)
$ python -m pytest -q --collect-only (ficheros nuevos + guardia)
155 tests collected in 0.64s
```

`2326 passed` = 2180 de la base + 146 pruebas nuevas (155 recogidas en los ficheros nuevos y el guardia, de las que 9 ya existían).

### 1.4 Lo que verá el dueño (Yahoo, Supabase y fichas SIMULADOS; datos inventados)

Tres runs seguidos con el mismo libro (martes 24-11: 06:38, 13:38 y 21:38), tres fondos con NAV oficial del lunes; uno de ellos solo existe como ETF en Yahoo.
Por la mañana Yahoo aún va por el viernes; por la noche ya tiene el lunes. Salida del paso (sin las líneas `::add-mask::`, que el runner no muestra):

```
### run 1: martes 24-11 06:38 (NAV oficial del lunes 23; Yahoo aún en el viernes)
sombra Yahoo: 3 fondos con NAV oficial (de 3 con ficha; 0 sin NAV oficial; 1 usuarios) · comparables 0 (iguales a ≤ 1 pb 0; dif. máx. — pb, sin redondear — pb; fuera de banda 0) · sin 0P 1 · sin emparejar 0 · ambiguos 0 · distinta divisa 0 (subunidad 0) · sin divisa 0 · sin dato de la misma fecha 2 (Yahoo por detrás 2) · dato inválido 0 · vacíos 0 · errores de Yahoo 0 · sin probar 0 · retraso medio de Yahoo frente a iShares +1.00 d hábiles (n=2) · canario ok (última barra a 2 d hábiles) · peticiones 10/60 · parada ninguna [run 2026-11-24T06:38Z]
sombra Yahoo: puerta D7: EN CURSO · 0 de 10 días hábiles con comparables (1 con lectura) · 0 comparables únicos (0 fechas de NAV) · 0 iguales a ≤ 1 pb (—) · dif. máx. — pb · umbral: ≥ 95 % iguales y ninguno > 10 pb
### run 2: martes 24-11 13:38
sombra Yahoo: no se lee en este run (fuera de franja) [run 2026-11-24T13:38Z]
sombra Yahoo: puerta D7: EN CURSO · 0 de 10 días hábiles con comparables (1 con lectura) · 0 comparables únicos (0 fechas de NAV) · 0 iguales a ≤ 1 pb (—) · dif. máx. — pb · umbral: ≥ 95 % iguales y ninguno > 10 pb
### run 3: martes 24-11 21:38 (Yahoo ya tiene el lunes)
sombra Yahoo: 3 fondos con NAV oficial (de 3 con ficha; 0 sin NAV oficial; 1 usuarios) · comparables 2 (iguales a ≤ 1 pb 2; dif. máx. 0.00 pb, sin redondear 0.15 pb; fuera de banda 0) · sin 0P 1 · sin emparejar 0 · ambiguos 0 · distinta divisa 0 (subunidad 0) · sin divisa 0 · sin dato de la misma fecha 0 (Yahoo por detrás 0) · dato inválido 0 · vacíos 0 · errores de Yahoo 0 · sin probar 0 · retraso medio de Yahoo frente a iShares +0.00 d hábiles (n=2) · canario ok (última barra a 1 d hábiles) · peticiones 10/60 · parada ninguna [run 2026-11-24T21:38Z]
sombra Yahoo: puerta D7: EN CURSO · 1 de 10 días hábiles con comparables (1 con lectura) · 2 comparables únicos (1 fechas de NAV) · 2 iguales a ≤ 1 pb (100.0 %) · dif. máx. 0.00 pb · umbral: ≥ 95 % iguales y ninguno > 10 pb
```

Resumen del job del run 3, tal cual:

```
## Sombra NAV Yahoo (F1): solo contadores

**Puerta D7: EN CURSO**

puerta D7: EN CURSO · 1 de 10 días hábiles con comparables (1 con lectura) · 2 comparables únicos (1 fechas de NAV) · 2 iguales a ≤ 1 pb (100.0 %) · dif. máx. 0.00 pb · umbral: ≥ 95 % iguales y ninguno > 10 pb

**Este run:** 3 fondos con NAV oficial (de 3 con ficha; 0 sin NAV oficial; 1 usuarios) · comparables 2 (iguales a ≤ 1 pb 2; dif. máx. 0.00 pb, sin redondear 0.15 pb; fuera de banda 0) · sin 0P 1 · sin emparejar 0 · ambiguos 0 · distinta divisa 0 (subunidad 0) · sin divisa 0 · sin dato de la misma fecha 0 (Yahoo por detrás 0) · dato inválido 0 · vacíos 0 · errores de Yahoo 0 · sin probar 0 · retraso medio de Yahoo frente a iShares +0.00 d hábiles (n=2) · canario ok (última barra a 1 d hábiles) · peticiones 10/60 · parada ninguna [run 2026-11-24T21:38Z]

### Lecturas (las 30 más recientes)

| Lectura (UTC) | Fondos | Comparables | Iguales | Máx. pb | Sin 0P | Sin emparejar | Distinta divisa | Sin dato misma fecha | Retraso medio (d háb.) | Canario | Peticiones |
|---|---|---|---|---|---|---|---|---|---|---|---|
| 2026-11-24 21:38 | 3 | 2 | 2 | 0.00 | 1 | 0 | 0 | 0 | +0.00 | ok | 10 |
| 2026-11-24 06:38 | 3 | 0 | 0 | — | 1 | 0 | 0 | 2 | +1.00 | ok | 10 |

Cómo leerlo: «PASA» = ≥ 10 días hábiles con comparables, ≥ 95 % de comparables iguales a ≤ 1 pb y ninguno > 10 pb. «Retraso medio» > 0 = Yahoo va por detrás de iShares. Solo contadores: ningún ISIN, símbolo, nombre ni precio.
```

Libro tras el run 3 (lo que guarda la caché; solo números, fechas y estados cerrados):

```
{"schema":1,"reads":[{"day":"2026-11-24","slot":0,"at":"2026-11-24T06:38Z","official_max":"2026-11-23","c":{"en_sombra":3,"comparables":0,"iguales":0,"fuera_de_banda":0,"sin_0p":1,"sin_emparejar":0,"ambiguos":0,"distinta_divisa":0,"subunidad":0,"sin_divisa":0,"sin_fecha_comun":0,"yahoo_por_detras":2,"dato_invalido":0,"vacios":0,"no_probados":0,"lag_sum":2,"lag_n":2,"canario":"ok","peticiones":10,"errores":0,"max_bp":null,"pendientes":2},"by_date":[]},{"day":"2026-11-24","slot":1,"at":"2026-11-24T21:38Z","official_max":"2026-11-23","c":{"en_sombra":3,"comparables":2,"iguales":2,"fuera_de_banda":0,"sin_0p":1,"sin_emparejar":0,"ambiguos":0,"distinta_divisa":0,"subunidad":0,"sin_divisa":0,"sin_fecha_comun":0,"yahoo_por_detras":0,"dato_invalido":0,"vacios":0,"no_probados":0,"lag_sum":0,"lag_n":2,"canario":"ok","peticiones":10,"errores":0,"max_bp":0.0,"pendientes":0},"by_date":[["2026-11-23",2,2,0.0]]}]}
```

## 2. Qué demuestra cada prueba y qué NO

| Fichero | Demuestra | NO demuestra |
|---|---|---|
| `test_privacidad_isin.py` (ampliado) | Ningún `0P` + 8 caracteres en el árbol salvo los 7 de la lista fija (congelados por **huella**: uno nuevo en esa lista hace fallar) y los inventados; en `config/yahoo_shadow.json` solo pasan ISIN de la lista genérica pública | Que los 7 de la lista fija no sean del dueño (D8 sigue abierta); el historial de git |
| `test_yahoo_shadow_resolucion.py` | Configuración con esquema cerrado; nombre que cuadra (incluidas abreviaturas de Morningstar) y que no (alcance Small/ESG, Acc/Dist, divisa, letra de clase, Hedged, otro fondo); solo `0P…` con regex cerrada; ETF = sin 0P; empate = ambiguo; cada `0P…` enmascarado | Que la regla de nombres acierte con los nombres **reales** de Yahoo de los fondos del dueño (ver §5) |
| `test_yahoo_shadow_comparacion.py` | Solo fecha idéntica (Yahoo por detrás o con hueco ⇒ no se compara aunque la barra vecina valga lo mismo); pb exactos con `Decimal`; redondeo mitad-arriba a los decimales del oficial (mín. 2); 1 pb exacto cuenta como igual; bandas; barra de hoy/futura ignorada; retraso con signo en días hábiles | Que iShares redondee mitad-arriba (supuesto, §3 D-7) |
| `test_yahoo_shadow_divisa.py` | Otra divisa ⇒ «distinta divisa», fuera del porcentaje; GBp ⇒ subunidad, **sin** ÷100 aunque el número cuadre; sin divisa ⇒ no se compara | Qué divisa da Yahoo de verdad para los `0P…` del dueño |
| `test_yahoo_shadow_run.py` | Una búsqueda + un histórico `5d` por fondo; pausa ≥ 1 s; sin NAV oficial ⇒ ni se pide; ISIN repetido entre usuarios ⇒ una vez; 429 ⇒ para a la primera; 3 fallos de red ⇒ para; vacío/parseo ⇒ contadores; presupuesto propio; desactivada ⇒ cero peticiones; configuración rota, entradas ausentes o ilegibles y excepción inesperada ⇒ aviso sin texto y salida 0 | El comportamiento real de `yfinance` ante un 429 de verdad (lo clasifica la `Gate` de la sonda, ya probada allí) |
| `test_yahoo_shadow_canario.py` | El canario es el de la configuración y va el primero; canario con 429/red/vacío/inválido ⇒ ninguna petición de los fondos; canario sin `0P…` ⇒ se sigue y se avisa | Que el ISIN elegido como canario resuelva de verdad a un `0P…` (§5) |
| `test_yahoo_shadow_libro.py` | El libro solo lleva contadores, fechas y estados cerrados; franjas configurables; una lectura por franja; tope diario; parada temprana (y que no se aplica si cambió la fecha oficial o quedó algo pendiente); EN CURSO → PASA a los 10 días hábiles; fines de semana no cuentan; la misma fecha oficial no cuenta doble; uno > 10 pb ⇒ NO PASA en el acto; 19/20 = 95 % pasa y 18/20 no; libro corrupto ⇒ de cero con aviso sin citarlo; poda | Que la caché de Actions restaure y guarde de verdad en este repo (§5) |
| `test_yahoo_shadow_privacidad.py` | Con valores falsos distintivos y un cliente que imprime y registra como `yfinance`: ninguno sale por stdout/stderr (descriptor), resumen, `GITHUB_OUTPUT`, libro, logging, avisos ni texto de excepciones; cada ISIN y `0P…` enmascarado **antes** de su primera petición; controles negativos (print, stderr, resumen, sin silenciador) | Escrituras de código nativo directamente al descriptor sin pasar por Python (no las hace `yfinance` 1.7.0 que yo sepa; no lo he auditado) |
| `test_yahoo_shadow_integracion.py` | Con el `FakeSupabase` del repo: watch → build → publish → sombra; la sombra no hace **ninguna** petición a Supabase aunque el token esté en el entorno; compara con el NAV que dejó build; el cliente real pide `5d`, `1d` y `auto_adjust=False` | La API real de Yahoo |
| `test_workflow_yahoo_shadow.py` | Tres pasos al final, tras publicar; `continue-on-error` y `timeout-minutes` en cada uno; sin secretos, variables ni env; caché fijada por SHA con clave única por run; guardar solo si hubo lectura; el job sigue `contents: read`, solo `main`, sin artefactos ni commits; los pasos anteriores y el cron no cambian | Que GitHub ejecute el YAML como lo interpreta el evaluador de las pruebas |

## 3. Decisiones (con la postura contraria)

**D-1. Dónde va el paso: `personal-full` (confirmado), al final, después de publicar.** Es el único sitio que ya tiene, en el mismo run y sin pedir nada más, la
lista con los ISIN (paso `watch`, cliente RPC existente) **y** el NAV oficial recién leído de la ficha con su fecha (paso `build`). El paso de la sombra no recibe el
token: lee `$RUNNER_TEMP/personal`. Ir **después** de publicar garantiza que la sombra no puede retrasar ni bloquear la publicación.
*Contraria*: un workflow propio a las 06:30 y 19:30 exactas (D6) aislaría del todo la sombra. Pero necesitaría el NAV oficial: o volver a leer las fichas (el doble
de peticiones a iShares) o leerlo de Supabase, y la RPC existente no lo da (`prev_price` lo escribe el cliente, no es el NAV guardado; lo comprobé en
`sql/schema.sql`), así que haría falta SQL. `personal-nav` tampoco: su contrato es «sin Yahoo, sin series».

**D-2. Activación por fichero versionado (`config/yahoo_shadow.json`, `enabled: true` en este PR), como pidió el dueño.** *Postura contraria (motivo técnico a favor
de la variable)*: una variable de repo se apaga sin commit, sin pasar por CI y sin que nadie tenga que fusionar nada; con el fichero, apagarla exige un commit en
`main` (desde el móvil: editar el fichero en github.com, o el botón «Revert» del PR). A favor del fichero: queda en el historial, lo validan las pruebas (esquema
cerrado: un valor mal escrito apaga la sombra con aviso en vez de hacer algo raro) y no necesita permisos de administración. No lo he cambiado.

**D-3. Persistencia de los contadores: caché de Actions** (`actions/cache/restore` + `save`, v6.1.0 fijada por SHA; clave por run y restauración de la más
reciente). El libro es un JSON de números y fechas, validado con esquema estricto al leerlo. *Contraria*: la rama `probe-data` (como la sonda de retraso) sería
duradera y legible como fichero desde el móvil, pero exige un job con `contents: write` y un commit en un workflow que tiene secretos (las pruebas existentes lo
prohíben, y el encargo dice «sin commits»). Riesgos de la caché: GitHub borra una entrada sin uso en 7 días y la caché se puede perder; entonces la puerta vuelve a
contar desde cero (el resumen de cada run conserva su fila durante la retención de logs, así que se puede reconstruir a mano). Solo el resumen del job, sin libro,
obligaría al dueño a abrir 20 runs para evaluar la puerta.

**D-4. Franjas (D6 A) como parámetro: `slots_utc = [[5, 10], [19, 24]]`, máximo 2 lecturas al día, una por franja, con parada temprana.** Con el cron actual de
`personal-full` (06:38, 13:38, 21:38) las lecturas caen a las **06:38 y 21:38** (no 19:30: ver §4). Si el disparador externo lanza el job más veces, solo la
primera de cada franja lee. Parada temprana: la franja de la tarde no lee si la de la mañana ya lo midió todo (nada pendiente, mismos fondos, misma fecha oficial,
canario bien). *Contraria*: añadir un cron a las 19:3x en `personal-full` cumpliría D6 al minuto, pero cambiaría la frecuencia del job que publica (una
publicación completa más al día) por una medida.

**D-5. Validación del nombre conservadora** (D1 A sin persona: en la sombra no hay confirmación). Rechaza por divisa, Acc/Dist, Hedged, letra de clase distinta y
palabras de alcance (Small, ESG, Value…), y exige ≥ 60 % de las palabras del nombre esperado (el mismo 0,6 que la sonda), con abreviaturas de Morningstar
expandidas. *Contraria*: con nombres reales puede rechazar el fondo correcto (abreviaturas que no conozco) y la sombra se quedaría sin comparables. Es el lado bueno
del error: un «sin emparejar» solo resta muestras y se ve en el resumen; un emparejamiento falso ensuciaría la medida.

**D-6. Una discrepancia fuera de la banda ×0,5–×2 cuenta como comparable distinto** (y se marca «fuera de banda»), no se descarta. Si el nombre y la divisa
cuadran y el precio se va al doble, es justo lo que la sombra tiene que ver (otra clase, un ×100 mal etiquetado). *Contraria*: un dato basura puntual de Yahoo
tumbaría la puerta («ninguno > 10 pb»); por eso los precios no finitos, ≤ 0 o fuera de 0,1–10 000 sí se descartan como «dato inválido».

**D-7. «Igual» = Yahoo redondeado (mitad hacia arriba) a los decimales del NAV oficial (mínimo 2) a ≤ 1 pb; también se da la diferencia sin redondear.** La ficha
escribe el NAV con 2 decimales: sin redondear, 87,6512 frente a 87,65 saldría «distinto» por el redondeo de la ficha y no por el dato. *Contraria*: el diseño (fila
F1) dice «iguales a 4 decimales»; con un oficial de 2 decimales eso no se puede medir. Mitad-arriba es un **supuesto** (no sé cómo redondea iShares); solo importa en
el caso exacto de un 5 en el tercer decimal.

**D-8. Comparables únicos por fecha de NAV oficial** (de varias lecturas de una misma fecha, la de más comparables; la diferencia máxima, la peor de todas) y días
hábiles **TARGET** (el mismo calendario que la sonda de retraso). *Contraria*: sin ISIN en el libro no se puede deduplicar por fondo y fecha exactos; si un día
cambiase el conjunto de fondos, la cuenta sería aproximada.

**D-9. Canario por ISIN de la lista genérica pública** (`canary_isin`; su `0P…` no se escribe en ningún sitio). *Contraria*: fijar el símbolo ahorraría la búsqueda
(1 petición por run), pero metería otro `0P…` en el repo público.

## 4. Desviaciones

1. **Activación por fichero, no por la variable `YAHOO_SHADOW`** del diseño (§8): pedido por el dueño. Ver D-2.
2. **Franja de la tarde a las 21:38, no ~19:30** (D6): es el run de `personal-full` que hay. Ver D-4. Si el dueño quiere 19:30, puede cambiar `slots_utc`, pero
   solo leerá si algo lanza `personal-full` en esa franja.
3. **«Solo los fondos del dueño»**: el módulo no puede distinguir al dueño de un invitado sin un dato nuevo (su id de usuario) que no quiero poner en un repo
   público. Mide los fondos con ficha y NAV oficial de **todos** los usuarios de la lista (deduplicados) y lo dice en la línea («N usuarios»). Hoy la lista es solo
   del dueño; si hay invitados, sus fondos entran en los mismos contadores agregados (no salen de ningún sitio).
4. **Guardia de acciones fijadas por SHA**: dos regex de `tests/test_workflows.py` admiten ahora una subruta del mismo repo (`actions/cache/restore@<sha>`). Mismo
   SHA, misma garantía. Alternativa descartada: la acción `actions/cache` completa guardaría en cada run (también sin lectura) en su paso posterior.
5. **El canario no necesita exención de `0P…`** (se define por ISIN) y la lista genérica no contiene ningún `0P…`: no he abierto esas dos exenciones en el guardia de
   `0P…`. La única exención de `0P…` es la lista fija de la sonda de retraso (por huella). Sí hay una exención nueva de **ISIN**, estrecha: en
   `config/yahoo_shadow.json`, solo ISIN de la lista genérica.
6. **«Iguales a 4 decimales»** del diseño → «a ≤ 1 pb tras redondear a los decimales del oficial» (D-7).
7. El encargo cita `tests/feed_v2_site.py`: no existe. He seguido el patrón de fakes de `tests/test_personal_v2.py` (`FakeSupabase`) y `tests/fund_probe_fakes.py`.

## 5. Lo no probado (y el riesgo que más me preocupa)

* **Riesgo principal: pocos comparables.** Solo se conoce el NAV oficial **más reciente** de cada fondo (no se guarda el anterior: sería un dato personal). Si a
  la hora de cada lectura iShares ya tiene el NAV de D−1 y Yahoo aún va por D−2 (lo que sugieren los datos del diseño: Yahoo publica D hacia las 00:30 de D+2), la
  fecha nunca coincide y todo sale «sin dato de la misma fecha (Yahoo por detrás)». La lectura de las 06:38 coincide solo si iShares **aún no** ha publicado el NAV
  del día anterior a esa hora (no lo sé). El resumen lo enseña desde el primer día (columna «Sin dato misma fecha» y «Retraso medio» > 0). Si tras ~5 días hábiles
  hay 0 comparables, opciones (decisión del dueño, fuera de este PR): adelantar la franja de la mañana si el disparador externo lanza `personal-full` antes, o una
  F1b que lea el NAV del día anterior de la propia ficha de iShares (más peticiones a iShares).
* **Ninguna petición real**: ni a Yahoo (nombres, divisas, fechas reales de los `0P…` del dueño), ni a Supabase, ni a iShares. La regla de nombres puede ser
  demasiado estricta con nombres reales (D-5).
* **El canario**: elegí de la lista genérica un fondo indexado popular de clase EUR; **no he podido comprobar** que Yahoo lo dé como `0P…`. Si no, el resumen dirá
  «canario sin_0p» y avisará; se cambia `canary_isin` por otro ISIN de esa lista.
* **La caché de Actions en este repo**: no he comprobado en vivo que `restore`/`save` funcionen con `permissions: contents: read` (según la documentación de la
  acción, la caché usa el token de ejecución del runner, no `GITHUB_TOKEN`). Si no funcionase: los pasos son `continue-on-error`, la sombra sigue midiendo, pero el
  libro empezaría vacío en cada run (se vería «1 con lectura» siempre).
* **Cómo se ve el resumen en la app móvil de GitHub**: no comprobado; en el navegador del móvil (github.com) sí se ve el «Summary» de un run.
* Python 3.13 aquí, 3.14 en el runner. La rama `except Exception` genérica de `run_shadow` es defensiva: con un cliente que respete el contrato, la `Gate` ya
  convierte cualquier excepción en una clase cerrada; ninguna prueba la alcanza.
* `yfinance` 1.7.0: comprobé que `Ticker.history` acepta `auto_adjust` (firma del raspador de históricos), no cuántas peticiones HTTP hace de verdad (se cuentan 2 por
  histórico, como la sonda, NV-5 del diseño).

## 6. Peticiones por run y márgenes

Se cuentan como la sonda (`Gate`: búsqueda = 1, histórico = 2, pausa ≥ 1 s, parada sin reintentos ante 429, tope propio `max_requests_per_run` = 60, techo duro 80).

| | Peticiones contadas |
|---|---|
| Canario | 3 (búsqueda + histórico), siempre el primero |
| Por fondo con NAV oficial y `0P…` | 3 (búsqueda + histórico); sin `0P…`: 1 |
| Run con lectura, con F fondos | 3 + 3·F; con 2 fondos (los del ejemplo de log del README): **9** |
| Por día | ≤ 2 lecturas ⇒ ≤ 18 con 2 fondos; parada temprana ⇒ a menudo 9 |
| Runs sin lectura (13:38, fuera de franja, franja ya leída…) | **0** |

Frente a lo ya medido sin 429 desde los runners: 20 por run en la sonda de retraso (5 runs, 50/50 ok) y ~129 por run en la sonda 2 (cifra del dueño). 9 es el
45 % de 20 y el 7 % de 129: por debajo de la regla del 50 % del diseño. El tope de 60 deja hasta 19 fondos por lectura (los que sobren quedan «sin probar»). Ojo:
en el mismo job, el paso `build` ya pide a Yahoo las series de los proxies minutos antes; la sombra se suma a eso en la misma IP (el log de `build` dice cuántas).
Cada run de la sombra informa de sus peticiones («peticiones N/60») y de cualquier parada.

## 7. Cómo leer el resultado desde el móvil (10 días hábiles)

1. En el navegador del móvil: `github.com/FractalBuddy/Portfolio_Dashboard` → **Actions** → **Personal full** → el run más reciente de las 06:38 o 21:38 UTC (en
   cualquier run vale: todos enseñan la puerta) → **Summary**, bloque «Sombra NAV Yahoo (F1): solo contadores».
2. Primera línea en negrita: **Puerta D7: EN CURSO / PASA / NO PASA**. Debajo: «X de 10 días hábiles con comparables», «N comparables únicos», «% iguales» y
   «dif. máx.». **PASA** = ≥ 10 días hábiles con comparables, ≥ 95 % iguales a ≤ 1 pb y ninguno > 10 pb. **NO PASA** sale en cuanto aparece uno > 10 pb.
3. Para D10 (¿Yahoo de reserva de iShares?), la columna **«Retraso medio (d háb.)»**: > 0 = Yahoo va por detrás de iShares.
4. Señales de que la medida no sirve (más que de que Yahoo no coincida): muchos «Sin 0P» o «Sin emparejar» (cobertura o regla de nombres), «Distinta divisa»
   (D5 B), «Sin dato misma fecha» siempre > 0 con 0 comparables (§5, riesgo principal) o «Canario» distinto de `ok`.
5. Apagarla: en github.com, editar `config/yahoo_shadow.json` en `main` y poner `"enabled": false` (o revertir el commit). No hace falta tocar nada más.

## 8. Barrido de privacidad del diff (contra `origin/main`, todas las líneas añadidas)

```
$ git diff --cached --stat origin/main | tail -1
 18 files changed, 2444 insertions(+), 3 deletions(-)
$ ISIN con forma de cualquier país en las líneas añadidas (únicos):
      2 IE0000000001
      4 IE000SYNA013
      1 IE000SYNB011
      1 IE000SYNC019
      1 <ISIN del canario: el de config/yahoo_shadow.json>
$ símbolos 0P + 8 en las líneas añadidas (únicos):
      1 0P00000AAA
      1 0P0ALPHA01
      1 0P0ALPHA02
      1 0P0BETA001
      1 0P0GAMMA01
      1 0P0PRIVATE
      1 0P0SECRET9
      1 0PCANARY01
      1 0PCANARY01.F
      3 0PFAKE0001
      4 0PFAKE0001.F
      2 0PFAKE0001.FRA
      2 0PFAKE0002
      1 0PFAKE0002.F
      1 0PFAKE0003
      1 0PFAKE0003.F
      1 0PFAKE0004
      1 0PFAKE0004.F
      1 0PFAKE0005
$ URL en las líneas añadidas (únicas):
      1 https://docs.pytest.org/en/stable/how-to/capture-warnings.html
      1 https://www.ishares.com/es/individual/es/products/000001/zeta-quimera-a
      1 https://www.ishares.com/es/individual/es/products/000002/zeta-quimera-b
      1 https://www.ishares.com/es/individual/es/products/100001/zeta-quimera-c
$ ids de ficha /products/<n> en las líneas añadidas:
      1 /products/000001
      1 /products/000002
      1 /products/100001
$ ficheros con el ISIN público del canario (solo debe estar en las dos configuraciones públicas):
config/fund_probe_isins_generic.json
config/yahoo_shadow.json
$ huellas del dueño (proxies y cripto) en los ficheros tocados:
ninguna en los 18 ficheros tocados
$ python -m pytest -q tests/test_privacidad_isin.py
14 passed in 0.50s
```

(El ISIN público del canario aparece sustituido en este informe: el guardia de privacidad no admite ningún ISIN real fuera de las dos configuraciones
públicas, y cazó este bloque en la primera versión del informe.)

Lectura: los ISIN son los inventados de `tests/privacy_guard.py` más uno de formato (`IE0000000001`) y el ISIN **público** del canario, que solo está en
`config/yahoo_shadow.json` (exención estrecha) y en la lista genérica de siempre. Los `0P…` son todos inventados (los de la sonda de fondos ya existían en el árbol;
`0PFAKE0001.FRA` es el símbolo con sufijo largo que una prueba rechaza). Las URL son las inventadas de los fakes, con ids de ficha permitidos. No hay huellas del
dueño. El log, el resumen y el libro de la sombra no llevan ningún ISIN, símbolo, nombre, precio ni URL (punto 8).

## 9. Marcha atrás

`"enabled": false` en `config/yahoo_shadow.json`, o revertir el PR. La sombra no deja estado fuera de la caché de Actions (que caduca sola) ni en Supabase.
