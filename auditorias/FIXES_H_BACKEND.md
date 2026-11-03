# Correcciones H (backend) — respuesta a la auditoría del 2-nov-2026

Rama `fix/h-backend-auditoria`, a partir de `origin/main` (`0de18a9`, con G2 y los PR de Dependabot ya fusionados). Un commit por bloque. **Sin merge y sin PR**, a la espera de que el dueño lo diga.

Método de cada bloque: (1) un test que reproduce el problema y **falla**, (2) el arreglo mínimo, (3) verde, (4) control negativo. Cada hallazgo `AU-nn` se trató como hipótesis y se reprodujo antes de tocar nada;
lo que no se pudo reproducir o comprobar se dice abajo y no se «arregló» a ciegas.

> **Privacidad de este informe.** El repo es público. Aquí no hay ningún ISIN, id de ficha, URL de Supabase, clave ni token; donde hace falta se dice «los dos fondos del dueño», «ISIN sintético» o «id de ficha inventado».

## 1. Resumen

| Bloque | Hallazgos | Estado |
|---|---|---|
| 1 · Cierre del corte | (sesión «C») | hecho: v1 ya no publica `your_funds`; `news.positions` queda vacío |
| 2 · Sin ISIN del dueño en el árbol | AU-03 (parte del árbol) | hecho: `git grep` de los reales → **0 coincidencias**; guardia con control negativo |
| 3 · Seguridad del backend | AU-04, AU-05, AU-14, AU-15 | hecho; AU-05 era peor de lo informado (caso cúbico) |
| 4 · Privacidad en logs | AU-02 | **a medias, por diseño**: el código ya lee `secrets \|\| vars`; faltan los pasos manuales del dueño |
| 5 · Observabilidad y límites | AU-07, AU-08, AU-09 | hecho (con una decisión sobre el código de salida del modo NAV, ver §3) |
| 6 · Calidad del feed | AU-06 | hecho: picos aislados, rango de precio por unit y huecos; sin falsos positivos probados |
| 7 · Documentación | AU-10, AU-11, AU-01 | hecho; el disparador externo está **documentado, no implementado** |

## 2. Hallazgo a hallazgo: reproducción y arreglo

| ID | ¿Reproducido? | Cómo | Arreglo | Test (falla antes / verde después) | Control negativo |
|---|---|---|---|---|---|
| (C) corte | sí | `main()` con la red sustituida generaba `your_funds` y `news.positions` con titulares | se eliminan la lista de fondos propios, la de cripto vigilada y sus funciones; `positions: []`; `sanity_check` deja de aceptar `your_funds` | `tests/test_corte_H1.py` (7 de 8 fallaban) | un feed con `your_funds` (con filas o vacío) se **rechaza** |
| AU-03 (árbol) | sí | `git grep` encontraba los ISIN y ids de ficha reales en 5 ficheros de código, fixtures y tests | ISIN sintéticos con dígito de control ISO 6166 válido e ids inventados; los dos ISIN de ETF públicos de las fixtures también | `tests/test_privacidad_isin.py` (falla si aparece un `IE…` fuera de la lista de sintéticos o un `/products/<id>` ajeno) | se prueba con un fichero con un ISIN ajeno (en el barrido real y en uno sintético) |
| AU-04 | sí | servidor local: una URL que redirige a otro puerto fue **seguida**; un cuerpo de 60 MB se leyó **entero** | `allow_redirects=False`, cada salto validado (`https`, `*.ishares.com`, sin credenciales ni puerto, máx. 3) y cuerpo en streaming con tope **4 MB** que aborta | `tests/test_seguridad_H3.py::test_AU04_*` (4 de 5 fallaban) | un cuerpo dentro del tope se lee entero; 8 destinos de redirección no permitidos no se piden |
| AU-05 | sí, **y peor** | con `"Benchmark Index A " * n`: cuadrático. Además, `Product Structure … Methodology …` es **cúbico**: **128 s con solo 30 000 caracteres** (el informe medía 16 s con 200 000 en otro patrón) | cotas `{1,120}` en los patrones perezosos de `scan_fund_facts`; `structure` y `methodology` se buscan con **una** sola expresión | 200 000 caracteres hostiles < 0,2 s por patrón (medido: 0,018–0,063 s) | los valores normales (benchmark, gestora, categoría) se siguen leyendo |
| AU-14 | sí | 200 000 dígitos seguidos → línea de log de 200 KB | `_clip()` recorta el `repr` a 40 caracteres en todos los avisos de `parse_ishares_nav` | `test_AU14_*` | — |
| AU-15 | sí | U+202E, U+200B, U+2028… se publicaban tal cual; `prev_price = 1e308` pasaba | se quitan las categorías Unicode `Cf` y `Zl`/`Zp` (a espacio) en `_plain_text` y `_plain`; `prev_price` acotado a 0,1–10000 | `test_AU15_*` (18 fallaban) | acentos, ñ, €, CJK y emojis simples se conservan; 0,1 y 10000 siguen valiendo |
| AU-02 | **no verificable el log**; sí el origen | no tengo acceso a los logs de Actions desde aquí. Sí es reproducible en el YAML: 4 workflows pasan `vars.SUPABASE_URL` / `vars.SUPABASE_PUBLISHABLE_KEY` por `env:`, y GitHub imprime ese bloque | los 4 workflows leen `${{ secrets.X \|\| vars.X }}` (funciona hoy con las variables; con secretos del mismo nombre GitHub los enmascara). `publish-feed-v2` y `update-data` no usan Supabase | `tests/test_workflows_H4.py` (5 de los 21 iniciales fallaban) | el detector se prueba contra 10 YAML sintéticos con fugas (echo, `GITHUB_ENV`/`OUTPUT`, `set -x`, `curl -v`, `env`…) y contra uno correcto |
| AU-07 | sí | `step_publish` devolvía siempre 0 aunque fallaran todos los payloads (se vio con 2 usuarios y la RPC dando 500); el latido solo existía para «todo bien» | `step_publish` sale con 1 si fallan **todos**; el latido corre siempre y manda `/fail` (texto fijo) si falla un paso, si algún payload falló (`nav_state=failed`) o si el NAV más reciente supera 6 días o no hay ninguno exacto (`nav_state=old`) | `tests/test_observabilidad_H5.py::test_AU07_*` | sin secreto no hace nada en ningún caso; una URL rara avisa sin mostrarla; 6 días = ok, 7 = old |
| AU-08 | sí | 23 usuarios → 20 procesados, `dropped = 0`, ningún aviso | los recortados suman a `dropped` y `watch` emite `::warning::` con el número (sin identificadores). **El tope no se toca** | `test_AU08_*` | con ≤ 20 no hay aviso |
| AU-09 | sí | `site_download.py` trataba cualquier 404 en `manifest.json` como primer run | con la variable de repo `FEED_V2_INITIALIZED=true` ese 404 sale con código 1 («no es un primer run»); los dos pasos de descarga de `publish-feed-v2.yml` reciben la variable | `test_AU09_*` | sin la variable (o con otro valor) se comporta como antes; un sitio sano con la variable pasa; un 503 sigue fallando |
| AU-06 | sí | pico ×11 en 2021 (sin detección), precio 1e-7 (sin rango) y hueco de 500 barras (sin aviso) | pico aislado = barra descartada con motivo `spike` (cuenta para el tope de 3; 4 o más ⇒ `suspect`); rango por unit con motivo `price_range`; hueco > 370 días ⇒ `suspect`; el validador del job `publish` rechaza precios fuera de rango y trata los picos en ficheros nuevos o cambiados como regresión | `tests/test_picos_H6.py` (29 fallaban) y 5 nuevos en `test_sanity_check_v2.py` (validador) | crash sostenido (−40 %, −60 %, −90 %), rebote parcial, gap de dos barras seguidas, split normalizado y split sin normalizar: **0 falsos positivos**; un fichero idéntico al ya publicado no se vuelve a juzgar |
| AU-10 | sí | las tres frases siguen en README/contrato | corregidas (el árbol está limpio; el historial no; `ci.yml` corre también en push a `main`) | `test_el_readme_y_el_contrato_no_prometen…` | — |
| AU-11 | sí (no había runbook) | — | sección «Runbook» en el README | idem | — |
| AU-01 | sí en cuanto a la documentación | README prometía 4/día y 30 min | sección «Cadencia real…», umbral > 14 h, todos los workflows con `workflow_dispatch` (test) y receta de disparador externo **sin implementar** | `test_todo_workflow_tiene_workflow_dispatch` (×8) | — |

### `news.positions` (Bloque 1): cómo se generaba

`build_position_news()` pedía un RSS de Yahoo por cada símbolo de `STOCKS + FUNDS` y, además, filtraba los feeds de cripto con `CRYPTO_WATCHLIST`, una lista de palabras clave de la cartera del dueño. Esa segunda parte
**dependía de tickers fijos de la cartera del dueño** y los titulares salían en el feed con `symbol`/`asset_name` igual a la palabra coincidente, así que se quitó toda la función: el campo queda `[]` y `sanity_check` sigue
validando el esquema (`general` basta para el mínimo de noticias). `STOCKS` y `FUNDS` **no se tocan**: son el universo público de v1 (la parte «de nicho» de `STOCKS` la retira el corte posterior; v1 sigue publicando `stocks`).
`CRYPTO_NEWS_FEEDS` se conserva porque `feed_v2.py` lo usa con una lista neutra del registro.

## 3. Decisiones que se apartan de lo pedido (a revisar)

1. **Tope de la ficha de iShares: 4 MB, no 2 MB.** No pude medir una ficha real (sin red a iShares desde esta sesión) y no quise cortar páginas legítimas; el tope está en la constante `ISHARES_MAX_BYTES`. Si prefieres 2 MB, es un cambio de una línea, pero
   mídelo antes con una ficha real.
2. **Redirecciones: se validan los saltos en vez de prohibirlas todas.** `allow_redirects=False` a secas convertiría en fallo silencioso cualquier redirección legítima de iShares (p. ej. de idioma) si existe; se siguen hasta 3 saltos solo si
   cada uno cumple `https` + `*.ishares.com` + sin credenciales/puerto. `nav_probe.py` (diagnóstico manual, `allow_redirects=True`) **no se tocó**: no estaba en el alcance de AU-04 y es deliberadamente exploratorio.
3. **Código de salida del modo NAV.** Pediste que `step_publish` (modo completo) salga ≠ 0 si fallan todos los payloads: hecho. En el modo **NAV** (`step_publish_nav`) no cambié el código de salida: su diseño (G2, con sus tests) es «un fallo
   de un usuario no tumba el run». El aviso lo da el latido (`nav_state=failed` ⇒ `/fail`). Si prefieres que también salga ≠ 0, son tres líneas, pero cambia unos 10 tests de G2.
4. **`HEALTHCHECK_PERSONAL_NAV_URL` solo como secreto**, no como variable (una variable saldría en claro en el log público, lo contrario de AU-02). Sin él no hace nada.
5. **Hueco largo ⇒ `suspect` (> 370 días):** el informe de auditoría lo cita como evidencia, pero su recomendación no lo pedía; lo pediste en los tests, así que lo implementé. Un símbolo con un hueco legítimo de más de un año no se publicaría (no hay ninguno en el registro).
6. **El validador de `publish` no bloquea por picos ya publicados:** un pico en un fichero diario idéntico al del sitio anterior no se vuelve a juzgar (si no, un pico anterior a este arreglo bloquearía todos los runs); solo los ficheros nuevos o cambiados, y con
   `allow_regression` pasa a aviso. El **generador** es quien descarta los picos nuevos. Un pico **ya publicado** en un año cerrado sigue ahí: no se reescribe solo (haría falta un rebase manual).
7. **Quitar la categoría Unicode `Cf` también elimina el ZWJ** de los emojis compuestos (familia, profesión…); se ven como emojis sueltos. Se prefirió a dejar pasar caracteres invisibles en nombres y titulares.
8. **Las respuestas HTTP falsas de 5 ficheros de tests** pasaron a `tests/http_fakes.py` (el scraper ya lee en streaming); el test sintético del WTI lleva ahora los cierres reales de los días vecinos, porque en una serie sintética a ~100 $ su 10,01 $
   sí sería un pico (en los datos reales 18,27 → 10,01 → 11,57 no lo es). El detector sobre esa secuencia real **no** marca nada.

## 4. Verificación de esta sesión (salidas reales)

Entorno: Python 3.13.18 (la CI usa 3.14), `pytest 9.1.1`, `ruff 0.17.2`, `time-machine 3.6.0`.

| Comprobación | Resultado |
|---|---|
| `pytest` completo, al empezar (línea base, `origin/main`) | `1356 passed` en 95 s |
| `pytest` completo, al final | `1489 passed, 1 warning in 126.94s` (la advertencia es un `DeprecationWarning` de `feedparser`, preexistente) |
| `ruff check --select F scripts tests` | `All checks passed!` |
| `pip-audit -r requirements.txt --no-deps --disable-pip` | `No known vulnerabilities found` (el primer intento dio `ReadTimeout` contra pypi.org; con `--timeout 90` terminó bien) |
| `pip-audit -r requirements-dev.txt --no-deps --disable-pip` | `No known vulnerabilities found` |
| `git grep -F` de los dos ISIN y los dos ids de ficha reales sobre el árbol | **0 coincidencias** |
| Mensajes de commit de la rama con valores reales | 0 |
| Líneas **añadidas** por los commits que contienen valores reales | 0 |
| Líneas **eliminadas** que los contienen | 23 (es inherente a borrarlos: ya están en el historial de `main`; ver pasos manuales) |
| `git grep` de una URL de proyecto Supabase o de claves/tokens reales en lo añadido | 0 (las claves de los tests son `…FAKE…`) |

**Prueba con el reloj desplazado** (como en la auditoría, con `time-machine`, un plugin fuera del repo que mueve el reloj de Python con `tick=True`):

| Reloj | Comando | Resultado |
|---|---|---|
| +40 días | `SHIFT_DAYS=40 PYTHONPATH=<plugin> pytest -q -p shift_clock --ignore=tests/test_workflows.py` | `1183 passed, 1 warning in 96.37s` |
| +400 días | `SHIFT_DAYS=400 …` (igual) | `1183 passed, 1 warning in 96.97s` |

(1183 = 1489 − las 306 pruebas de `test_workflows.py`.)

Se excluyó `tests/test_workflows.py`: sus pruebas ejecutan `bash` con `date` del sistema, que `time-machine` no mueve, así que un test del keepalive falla **por construcción** y no por un defecto (la auditoría excluyó lo mismo).
Ese fichero sí pasa en la ejecución normal (306 pruebas dentro de las 1489).

## 5. Lo que no he podido hacer o comprobar

* **Logs reales de Actions** (AU-02): no tengo acceso; el origen se ve en el YAML, pero que el log muestre hoy la URL y la clave lo tomo del informe de auditoría, no lo he visto.
* **Ejecución real en GitHub**: ninguno de los workflows modificados se ha ejecutado en Actions (solo sus pasos `run:` con comandos falsos en bash local y sus YAML con PyYAML). El paso `Heartbeat` usa `job.status` y
  `steps.publish.outputs.nav_state` en una condición `always() && !cancelled()`: la lógica está probada en bash, la sintaxis de contextos de Actions **no** se ha validado contra GitHub (`actionlint` no está instalado aquí).
* **Fichas reales de iShares** (tamaño real, redirecciones reales) y **Supabase** (SQL de las RPC, RLS, rate limit): sin acceso. Los pasos de rotación del runbook están sin verificar contra ese SQL.
* **Python 3.14** (la CI): solo he usado 3.13.18.
* **El feed v2 vivo**: sin acceso; no sé si el sitio publicado contiene ya algún pico antiguo (si lo tiene, este arreglo no lo retira de los años cerrados).
* **No hecho, por instrucción**: purga del historial / recrear el repo, cambiar secretos o variables, NAV de otras gestoras, fase 3, implementar el disparador externo.
* **AU-12, AU-13, AU-16 e info** no estaban en el alcance de esta sesión.

## 6. Pasos manuales del dueño (en orden)

1. **Revisar y fusionar** esta rama cuando quieras (no hay PR abierto). Tras la fusión, `main` ya no contiene los ISIN ni las fichas en el árbol.
2. **Crear los dos secretos** `SUPABASE_URL` y `SUPABASE_PUBLISHABLE_KEY` (Settings → Secrets and variables → Actions → *Secrets*) con **los mismos valores y nombres** que las variables. Desde ese momento los workflows usan el secreto y GitHub lo enmascara en los logs.
3. Lanzar a mano «Supabase keepalive» y «Personal NAV» y comprobar que siguen en verde (HTTP 200/401/403 y «0 fallidos»).
4. **Borrar las dos variables** `SUPABASE_URL` y `SUPABASE_PUBLISHABLE_KEY` (si no, siguen en el repo; mandan los secretos, pero la variable quedaría a la vista de quien mire los ajustes de un fork o un log antiguo).
5. **Rotar la clave publicable** en Supabase (los logs anteriores, públicos, ya la mostraron) y actualizar el secreto del paso 2; relanzar el keepalive y «Personal NAV». Confirmar en Supabase que las dos RPC tienen límite de tasa y que `anon` no ejecuta nada más (no se puede ver desde el repo).
6. *(Opcional)* Crear el secreto `HEALTHCHECK_PERSONAL_NAV_URL` con un check de Healthchecks con **Period = 3 días y Grace ≥ 14 h**. Sin él no hay latido.
7. Cuando confirmes que el sitio v2 está completo (`health.json.counts.published` = símbolos de Yahoo del registro): crear la variable de repo **`FEED_V2_INITIALIZED = true`**.
8. Decidir si se quiere el **disparador externo** (receta en el README; no implementado) y, si sí, crear el token de mínimos permisos.
9. **Decidir el corte restante** (borrar `update-data.yml`, la rama `data`, `sanity_check.py` y sus tests) y, **después de fusionar el árbol saneado**, **recrear el repositorio** (BE-09) siguiendo la lista «Tras recrear el repo» del runbook: es la única forma de que desaparezcan los ISIN y las fichas
   del historial, de las referencias de los PR y de la rama `data` antigua. Asume los datos como ya expuestos.
10. Tras recrear: volver a poner secretos y variables (**sin** `FEED_V2_INITIALIZED` hasta que el sitio esté completo), Pages, Dependabot, secret scanning y el ruleset de `main`, y actualizar la URL base del dashboard.
