# Sesión F · NAV fresco y trazable (backend) — `feat/nav-fresco`

Rama nueva desde `main`. **Sin red real** desde la sesión (el proxy da 403 a ishares.com y a Yahoo): todo lo que sigue se ha probado con datos simulados y con **texto real de fichas de iShares ya guardado en el repo**
(`tests/fixtures_fund_facts.json`). Lo que no se ha podido medir en vivo lo digo explícitamente. No se ha lanzado ni publicado ningún workflow; no se ha tocado el SQL ni la app.

## T0 (tarea añadida, la primera): desfase de fecha del NAV — navegador «28/Oct», scraper «27/Oct» — **RESUELTO: era un dato guardado en la app**
**Evidencia (del dueño, no verificada por mí):** tras lanzar el workflow, el dashboard ya mostraba la fecha de NAV de la ficha y sus valores exactos. Es decir, la petición de siempre (`base`) ya lee el NAV más reciente
y el desfase venía de un dato guardado en la app. Desde esta sesión no llego a ishares.com (proxy 403), así que no he podido reproducirlo; me fío de esa observación porque es directa (fecha y valor coinciden con la ficha).
Las hipótesis que había planteado (caché del HTML, cabeceras/sesión, XHR aparte, varias fechas en la página) quedan **descartadas por la evidencia, no por prueba mía**.

**Decisión aplicada:** `NAV_FETCH_VARIANTS = ("base",)` → **una sola petición a iShares por ficha y run** (no se dobla la carga). Se conserva, sin usarse por defecto:
* `fetch_market_data.ishares_request(url, variante)` y `fetch_ishares_nav_freshest(url, isin, variantes)`: si algún día hiciera falta, basta añadir `"fresh"` a `NAV_FETCH_VARIANTS` (se queda con la fecha de NAV más reciente).
  Con solo `base` el log no imprime la línea de variantes (no aporta nada).
* **`nav-probe.yml` + `scripts/nav_probe.py`: diagnóstico manual opcional, sin cron** (`workflow_dispatch` únicamente). Permisos `contents: read`, sin artefactos, no escribe en Supabase ni en ningún sitio; el token solo en el paso de lectura
  (`python3 -S`), no en el que hace las peticiones (`requests`). Las fichas salen como `#1`, `#2`; por variante imprime código HTTP, tamaño, fecha y valor del NAV y (desactivable) cabeceras de caché; nunca ISIN, nombre ni URL.
  Variantes: `base`, `nocache`, `bust`, `fresh`, `browser`, `browser_fresh`, `passthrough`, `base_again`, la misma ficha en `alt:uk|de|ie|es|pro` y las XHR/JSON que cite el HTML (`xhr1`...). Cada ejecución hace ~25 peticiones por ficha probada.
* **Log del build** (sin identificar fondos): fecha de NAV más reciente y más antigua con su edad, decimales, hora UTC del run y coste (ver §3).
* El User-Agent de navegador solo existe en el diagnóstico (hacerse pasar por un navegador ante la protección de un sitio es decisión del dueño).
* Tests: `tests/test_nav_probe_F2.py` (29): variantes, elección del NAV más reciente cuando se activan varias, **una sola petición por defecto**, diagnóstico completo con respuestas simuladas (texto real de ficha con la fecha cambiada),
  privacidad, XHR descubierta y redactada, y el workflow (solo manual, sin escritura, token solo al leer, SHA fijados).

## 0. Lo que NO está demostrado (leer primero)
*(Esta sección es del primer análisis; el desfase concreto de fecha ya está explicado en T0. Sigue siendo cierto que no he visto cuándo publica iShares.)*
* **La causa «el cron va por detrás de iShares» es una hipótesis razonable, no un hecho.** Cuadra con el diseño (el job `personal` solo corría 4 veces al día), pero no he podido ver cuándo publica iShares ni qué
  muestra tu dashboard. Los números que das (iShares 13,64 / 12,64 frente a dashboard 13,38 / 12,61) son **+1,94 %** y **+0,24 %**. En las fichas reales guardadas la variación diaria es −1,39 % (EM) y −0,07 % (dev):
  un 1,9 % en el fondo EM equivale a ≥ 1 día de movimiento, no a unas horas de retraso del cron.
* **Otras causas posibles que el cambio de cron NO arregla** (y que ahora se pueden distinguir con el log):
  1. El scrape falló y el dashboard enseña «último NAV manual × `change_pct` del proxy» (`source: "proxy_approx"/"none"`, `price: null`; el propio código lo dice). Eso deriva justo así. Ahora el log avisa
     `N de M fondos sin NAV exacto`.
  2. La página trae varios «NAV as of» y se usa el primero (decisión previa, BE-17). Ahora se cuenta (`nav_varios`).
  3. Un run descartado/retrasado de GitHub (puede pasar con cron en :47).
  4. El cliente: caché, o aplicar `change_pct` sobre un NAV ya fresco. **Fuera del backend.**
* **Cómo cerrarlo (pasos manuales, §6):** con el workflow nuevo activo, la línea de frescura del log dice la fecha de NAV más reciente y más antigua de lo publicado. Si coincide con la ficha y el dashboard sigue
  distinto, el problema está en el cliente.

## 1. Workflow nuevo `.github/workflows/personal-nav.yml`
* Un único job `personal`: `watch` (token, `python3 -S`) → instalar dependencias (con hashes) → `build` (terceros, **sin token**) → `publish` (token, `python3 -S`). Permisos `contents: read`, `permissions: {}` global, acciones fijadas
  por el mismo SHA que el resto, solo clave publicable, `timeout-minutes: 15`, sin artefactos ni Pages. `workflow_dispatch` sin entradas.
* **Gating** `github.event_name != 'schedule' || vars.FEED_V2_ENABLED == 'true'`, igual que el resto.
* **Salida temprana**: `watch --github-output` escribe `work=true|false` en `$GITHUB_OUTPUT`. `work=true` si hay algún fondo con URL de iShares válida **o algún símbolo**; si no (lista vacía, RPC caída, config
  ausente) se omiten instalación, build y publish. *Decisión mía a revisar:* pediste salir si «no hay fondos»; he incluido los símbolos para que una lista de solo símbolos (p. ej. la de un amigo) no deje de
  refrescarse al mover el job fuera de `publish-feed-v2`. Si prefieres solo fondos es cambiar una línea (`work = bool(n_funds)`).
* **Frecuencia propuesta: `23,53 5-21 * * 1-5`** = cada 30 min, lunes a viernes, 05:23–21:53 UTC (34 runs/día).
  * *Por qué 30 min y no menos:* la latencia media que añade el cron es la mitad del periodo (15 min); bajar a 15 min la reduciría a ~7 min, pero GitHub ya retrasa los cron varios minutos y a veces los descarta, así que
    no compensa. El coste por run es bajo (§3), pero la carga sobre iShares/Yahoo es proporcional al nº de runs.
  * *Por qué solo laborables:* la ficha no cambia en fin de semana. *Suposición no verificada* (sin red): si iShares actualizara en sábado, quitar `1-5`.
  * *Por qué 05–21 UTC:* cubre mañana y tarde europeas en verano e invierno. No sé a qué hora publica iShares: **medirlo** (§6) permitirá estrechar la ventana o pasar a cada hora.
  * *Minutos «feos»:* `:23` y `:53`. No son `:00/:30` (los que más retrasa GitHub) ni los de los otros workflows (`update-data` `:17`, `publish-feed-v2` `:47`).
* **¿`personal` sigue dentro de `publish-feed-v2`? → Se ha movido (borrado de allí).** Motivos: (1) cada run **sustituye** el payload en Supabase (no hay lectura/fusión), así que dos escritores con concurrencia distinta
  pueden pisar un NAV fresco con uno viejo; compartir grupo entre workflows no vale, porque GitHub guarda un solo run pendiente por grupo y cancelaría jobs del feed público; (2) los 4 runs diarios quedan
  subsumidos por los 34; (3) desacopla: un fallo de Pages ya no afecta al NAV ni al revés. *Coste:* lanzar a mano «Publish feed v2» ya no refresca lo personal (hay que lanzar «Personal NAV»), y `publish-feed-v2`
  ya no usa Supabase (sus tests lo exigen). El heartbeat de Healthchecks de v2 **no** cubre el workflow nuevo (ver riesgos).
* **Concurrencia propia** `personal-nav` (`cancel-in-progress: false`): único escritor de Supabase; los runs se serializan en orden.
* Ya no hay `continue-on-error`: aquí no hay feed público al que proteger. Los fallos suaves siguen siendo `::warning::` con salida 0; solo una clave secreta o URL no https hacen fallar el job (y GitHub te avisa).

## 2. Coste de cada run frecuente (payload completo, CON histórico largo del proxy) — medidas simuladas, rehechas tras integrar `main`
Con `feat/personal-history` los proxies de los fondos se piden con `period="max"` (hasta ~25 años, tope 6.600 barras) y siempre van en `series`, estén o no en el universo público; los símbolos extra siguen con 2 años.
Medido con el código real (`build_payload` + `payload_problems` + `fit_payload`) y series sintéticas; las **peticiones y los bytes del payload** son del código, los **bytes de descarga de Yahoo son una estimación** (≈ 80 B por barra sin
comprimir; sin red no los he medido). Supuesto del caso A: proxies de ~4.350 y ~2.850 barras (≈ 17 y 11 años de cotización en Xetra); la sintética da 137 KB, y **tu medida real es ~165 KB** (precios con más dígitos): uso tu cifra para A y
escalo el resto.

| Escenario | Peticiones a Yahoo/run (/día ×34) | Barras bajadas/run (≈ KB sin comprimir) | Payload/run | Escrituras a Supabase/día (34 runs) |
|---|---|---|---|---|
| **A) 2 fondos + sus 2 proxies (tu caso)** | **2 (68)** | ~7.200 (≈ 560 KB) | **~165 KB** (sintético 137) | **~5,6 MB** (≈ 170 MB/mes) |
| B) A + 10 símbolos extra (2 años) | 12 (408) | ~12.400 (≈ 970 KB) | ~285 KB (sint. 238) | ~9,5 MB |
| C) 5 usuarios × (2 fondos con proxy de 25 años, 10 símbolos, 5 comunes) | 32 (1.088) | ~28.200 (≈ 2,2 MB) | ~2,1 MB en total (máx. ~415 KB/usuario) | ~69 MB |
| D) tope: 20 usuarios × (20 fondos sin proxy, 40 símbolos) | 420 (14.280) | ~218.000 (≈ 17 MB) | ~9,8 MB en total (máx. 490 KB/usuario) | ~330 MB |
| E) 1 usuario × 20 fondos con 20 proxies de 25 años | 20 (680) | ~126.000 (≈ 9,8 MB) | ~600 KB (el recorte determinista de `fit_payload` baja de 25 a ~10 años para caber en 1 MB) | ~20 MB |

(B–E: las cifras sintéticas multiplicadas ×1,21, el factor entre tu 165 KB y mi 137 KB; CPU de build+validación ≤ 0,5 s salvo D, 1,7 s.)

* **Peticiones a Yahoo: igual que antes** (una por símbolo y periodo único; los proxies ya contaban). **Lo que cambia es el tamaño**: cada petición de un proxy baja ~3.000–6.000 barras en vez de ~520, así que el caso A pasa de
  ~40 KB a **~560 KB sin comprimir por run** (≈ 19 MB/día de descarga, estimado), y el payload de 21 KB a **165 KB**.
* Referencia: el feed público hace ~545 peticiones a Yahoo al día; el job `personal` antiguo hacía 2 × 4 = 8 (con 2 años de historia: ~40 KB/run).
* **Deduplicación (sin SQL):** una descarga por símbolo y run aunque lo sigan varios usuarios; una descarga `max` sirve también para `2y`. Esa caché la trajo `main` (envoltorio de `fetchers.series` en `step_build`); yo había escrito una equivalente y la he retirado para no tener dos. Añadido encima: **los fallos también se recuerdan** (un símbolo que falla no se vuelve a pedir a Yahoo por cada usuario). Antes: una petición por usuario y símbolo.
* **Tiempo real de Yahoo/iShares: no medido.** El primer run real lo dará: el log trae `coste del run: N series pedidas a Yahoo, X KB, T s`. Estimación: build ~15–40 s en A; run completo **1,5–2 min de runner** (casi todo `pip install`).
  34 × 2 min × ~22 días ≈ 1.500 min/mes: gratis en repo público.
* **Veredicto actualizado:**
  * **A (tu caso) sigue siendo aceptable** a 30 min: 2 peticiones por run, ~0,5 MB de descarga y 165 KB de subida. Pero ahora es **muy redundante**: ~165 KB × 34 reescritos al día para unos datos cuyo histórico cambia **una barra al día**
    (el 99,9 % de lo que sube cada run es idéntico a lo del run anterior); solo el NAV cambia durante el día. No es un problema de cuota (5,6 MB/día), sí de desperdicio y de churn en la base (cada escritura reescribe el `jsonb` de 165 KB).
  * **B** es aceptable; **C** es el límite (2× el feed público en peticiones y ~70 MB/día a Supabase); **D no es viable** (420 peticiones ≈ 7–14 min, rozando el presupuesto de Yahoo); **E** funciona gracias al recorte pero pierde años de histórico.
  * Recomendación reforzada: **la RPC de solo `funds`** (requiere SQL, **no tocado**, p. ej. `publish_personal_nav(p_token, p_user, p_funds jsonb)` que fusione `funds` en el payload guardado). El workflow de 30 min leería solo iShares
    (≈ 2 peticiones, <1 KB) y el payload completo con el histórico largo se publicaría 1–4 veces al día. Evita además los riesgos 1 y 2. **Pregunta pendiente para ti.** Sin SQL, la alternativa es bajar la frecuencia (p. ej. cada hora: 17 runs/día, ~2,8 MB/día).

## 3. Trazabilidad sin datos privados (`personal_v2.py build`)
Ejemplo de lo que imprime (salida de un test con datos falsos):
```
datos propios: NAV: 2 fondos, 2 con NAV exacto leído de la ficha, fecha de NAV más reciente 2026-10-23 (3 d), más antigua 2026-10-23 (3 d), decimales del NAV: 2 (x2) [run 2026-10-26T12:00Z]
datos propios: coste del run: 1 series pedidas a Yahoo (únicas entre usuarios), payload 6.7 KB, 0.0 s
::warning::datos propios: 2 NAV con más de 5 días (la ficha de iShares puede ir retrasada, o el parseo falla)
```
* Nº de fondos, nº con NAV exacto, fecha más reciente y más antigua (con su edad en días), decimales con los que escribe el NAV la ficha, hora UTC del run, coste. Aviso si algún NAV supera `--nav-warn-days` (por defecto
  **5**: coherente con la calibración de `NAV_STALE_DAYS`, donde lo normal llega a 5 días en puentes largos; >5 es raro), si hay fondos sin NAV exacto y si la página trae varios «NAV as of» (`nav_varios`).
* **Nunca** ISIN, nombres, URL, importes ni token (test: con ISIN/nombres/URL falsos y ruido en stderr, ninguno aparece en el stdout). Fechas de NAV y contadores son públicos.
* Para ver **a qué hora** pasa la fecha más reciente a la del día en la ficha, compara `fecha más reciente` y `[run …Z]` entre runs consecutivos: eso mide la ventana de publicación de iShares.

## 4. Parser del NAV (`parse_ishares_nav`) — revisión
**Precisión de la ficha: 2 decimales.** Todas las fichas reales guardadas (dev `EUR 12.48`, em `EUR 13.43`, iwda `USD 147.26`, sgln `USD 80.04`) escriben el NAV con exactamente 2 decimales, y tus cifras (13,64 / 12,64) también.
El backend guarda 4 (`round(price, 4)`) por prudencia, pero **no añade precisión**: el dashboard no puede coincidir con la ficha más allá del céntimo. (Es lo que veo en 4 fichas de 20–21/10; que iShares nunca cambie
el formato no se puede garantizar. El log imprime `decimales del NAV` en cada run por si cambia.)

Probado con texto real de las fichas (`dev`, `em`; `iwda`/`sgln` son USD y se rechazan correctamente) y casos nuevos (`tests/test_nav_fresco_F.py`, 73 tests):
* **Página cortada en cualquier punto** (se prueba cada posición de las dos fichas reales): nunca sale un NAV equivocado. Motivo estructural: el ISIN aparece *después* del NAV en la ficha, así que un corte a mitad del
  número («EUR 12.») no tiene ISIN y se rechaza. (Si algún día el ISIN apareciera antes, este guarda desaparecería: está cubierto por el test.)
* **Varios «NAV as of»**: idénticos (etiqueta duplicada de la ficha de SGLN) no cuentan; distintos → se usa el primero (sin cambiar la decisión de BE-17) y se anota `nav_multi`/`nav_varios` para el log.
* **ISIN ausente, dentro de otro código u otro ISIN** → sin NAV (cae al proxy). Páginas vacías/bloqueadas/«NAV as of» sin número → sin NAV.
* **Separadores**: `12.48`, `12,48`, `12.4800`, `1,234.56`, `1.234,56`, y **`1’234.56`** (apóstrofo suizo, el que usa la ficha `/ch/` para los miles). Hallazgo/arreglo: antes `EUR 1’234.56` se leía como **1,0** y
  pasaba el filtro de verosimilitud; ahora se lee bien (afectaba también a la variación y al rango 52 sem.). Ambiguos o inverosímiles (`12.48.`, `1.2.3`, `0.05`, `20000`, `1’23.45`) → sin NAV.
* **«Robo» del 52 de «52 WK»**: con `EUR  52 WK: …` (sin NAV) el regex leía **52** como NAV. Arreglado (el número no puede ir seguido de `WK` ni de más dígitos).
* **Entidades HTML** (`EUR&nbsp;12.48`, `1&#8217;234.56`): el parser ahora las deshace (solo ahí, no en los facts).
* **Fechas**: `20/Oct/2026`, `20 Oct 2026`, `20-oct-2026`, ISO, `Sept`, y nuevo estilo US `Oct 20, 2026` (también en `sanity_check.py`). Ilegible/ambigua (`10/11/2026`, `hoy`, `31/Feb`) → el NAV se conserva **sin** inventar
  `nav_date_iso` ni `nav_stale`; fecha futura > 1 día → ídem.
* Nuevo campo `nav_decimals` (y `nav_multi` solo si > 1) en el resultado del parser; **no** entra en el payload (el esquema es estricto; hay test) ni en `your_funds` de v1 (copia claves explícitas).
* Los tests no tocan la red real (`conftest.py` lo prohíbe y hay un test que ejecuta `python3 -S … watch` contra un Supabase local falso).

## 5. Verificación y otros hallazgos
* **Integración con `main` (`df99c60`, `feat/personal-history`)**: merge de `origin/main` en `feat/nav-fresco` (sin rebase; el historial publicado no se reescribe). Conflictos solo en `scripts/personal_v2.py` (`build_payload`) y `README.md`.
  Resueltos **conservando las dos cosas**: de `main`, proxies siempre en `series` con `period="max"`, `MAX_PROXY_BARS`, `series_entry(long=...)`, `fit_payload` determinista (extra → proxies a ~10/5/3/1 años/3 meses/último cierre) y el validador con huecos
  (`MAX_GAP_DAYS`), fechas crecientes y tope por clase; de la sesión F, `personal-nav.yml`, el log de frescura, el parser y el recuerdo de los fallos de Yahoo en la caché de series de `main` (mi caché propia, que duplicaba la suya, se retiró).
  Los tests de `main` (`test_personal_v2.py`, contrato) se ejecutan tal cual.
* `python -m pytest -q`: **1270 passed** (tras integrar `main`). `ruff check --select F scripts tests`: limpio. `python -m py_compile scripts/*.py tests/*.py`: ok. `sanity_check_v2` pasa dentro de `tests/test_sanity_check_v2.py` y los tests
  de generación (no hay CLI que ejecutar sin un sitio generado). `actionlint` no está disponible en la sesión (CI lo omite con aviso); YAML validado con PyYAML.
* **Fallo previo en `main`** (no mío): `test_flujo_completo_lee_la_lista_y_publica_por_usuario` dependía del reloj real (`generated_at` > fecha fijada del test) y falla con el reloj real actual (el sandbox marca 29-10-2026). `main()` acepta ahora `now` y el
  test lo inyecta. (Si lo ves en rojo en otras ramas, es eso.)
* Tests de workflows reorganizados: el job `personal` ya no está en `publish-feed-v2`; hay tests propios para `personal-nav.yml` (permisos, concurrencia, cron, gating, token en solo 2 pasos, SHA fijados, salida temprana,
  stdlib-only con `python3 -S`).

## 6. Riesgos
1. **Una serie de Yahoo que falla borra esa serie en Supabase hasta el siguiente run** (el payload se sustituye entero y `build_payload` descarta la serie fallida). Con el histórico largo esto pesa más: un proxy que falle quita hasta ~25 años de
   histórico durante ≤30 min, y **una descarga `max` parcial (Yahoo devolviendo menos barras) sobrescribiría el histórico largo con uno corto sin que nada lo detecte** (no hay comparación con lo publicado antes; el validador solo mira huecos y orden).
   Con 34 runs/día hay más oportunidades de un 429 puntual; el NAV no se ve afectado. No lo he cambiado (decidir entre «publicar sin la serie» y «no publicar a ese usuario» es tuyo y depende de cómo lo trate la app). La RPC de solo
   `funds` (§2) lo resuelve de raíz.
2. **Tope de series sin avisar** (ya existía): `series` admite ≤ 40 y el payload lleva símbolos + proxies de los fondos. Un usuario con > 40 en total queda con payload inválido y **no se publica nada de él, ni el NAV**
   (cuenta `payload_invalido` en el log). No afecta a una lista como la tuya.
3. **Sin heartbeat** de Healthchecks para este workflow: si el cron deja de dispararse (60 días sin actividad en el repo, FEED_V2_ENABLED mal puesta, GitHub descartando runs) solo lo notarás por la fecha del NAV. Opcional:
   un check propio con periodo ~1 h y un secreto nuevo (no lo he añadido para no inventar secretos).
4. **Solo corren los `schedule` del workflow en la rama por defecto**: hasta que se fusione en `main`, no hay cron (el lanzamiento manual sí).
5. **Términos de uso de iShares/Yahoo**: 34 lecturas al día de la misma ficha (una por run, ya sin variante extra) es más tráfico que 4; sigue siendo pequeño pero el riesgo documentado en el README se mantiene y aumenta con la frecuencia.
6. **Minutos de Actions**: gratuitos en repo público; ~1.500 min/mes si algún día fuera privado.
7. **Suposiciones sin verificar por falta de red:** hora de publicación de iShares, que no haya cambios en fin de semana, tamaños/tiempos reales de Yahoo/iShares, y que el formato de 2 decimales sea estable.

## 7. Pasos manuales para ti
1. Revisar el diff y fusionar en `main` (sin eso el cron no existe). Comprobar que la variable `FEED_V2_ENABLED` es `true` si quieres el cron; `SUPABASE_URL`, `SUPABASE_PUBLISHABLE_KEY` y `PERSONAL_FEED_TOKEN` ya existen.
2. Actions → **Personal NAV** → *Run workflow* (una vez, a mano). Abrir el paso «Build personal payloads» y leer la línea `datos propios: NAV: …`: fondos, con NAV exacto, fecha más reciente/más antigua, decimales (debe decir `2`),
   `coste del run` (te da los números reales de peticiones, KB y segundos que aquí solo pude estimar) y que **no** haya `::warning::` de «sin NAV exacto».
3. Comparar esa fecha de NAV y los valores con la ficha pública de iShares y con el dashboard. Si el log y la ficha coinciden y el dashboard no → el fallo está en la app (caché o `change_pct` sobre un NAV fresco).
4. (Opcional) Si vuelve a verse un desfase, lanza **NAV probe** a mano. Durante 3–5 días laborables, mirar a qué hora (`[run …Z]`) pasa la fecha más reciente a la del día. Con eso se decide si 30 min sobran (pasar a cada hora o estrechar 05–21) o si hay que afinar.
5. Si quieres evitar re-publicar series cada 30 min: decidir la RPC de solo `funds` (§2). **No** he escrito SQL.
6. Opcional: un check de Healthchecks para este workflow (§6.3).
