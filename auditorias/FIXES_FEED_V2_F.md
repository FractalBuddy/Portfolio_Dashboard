# Sesión F · NAV fresco y trazable (backend) — `feat/nav-fresco`

Rama nueva desde `main`. **Sin red real** desde la sesión (el proxy da 403 a ishares.com y a Yahoo): todo lo que sigue se ha probado con datos simulados y con **texto real de fichas de iShares ya guardado en el repo**
(`tests/fixtures_fund_facts.json`). Lo que no se ha podido medir en vivo lo digo explícitamente. No se ha lanzado ni publicado ningún workflow; no se ha tocado el SQL ni la app.

## T0 (tarea añadida, la primera): desfase de fecha del NAV — navegador «28/Oct», scraper «27/Oct»
**Estado: causa NO determinada; mitigación y diagnóstico entregados.** Desde la sesión no llego a ishares.com (el proxy responde 403, comprobado de nuevo con `curl`), así que no he podido ejecutar ninguna
variante ni ver la ficha real. No voy a dar por «arreglado» lo que no he visto: hay una mitigación razonable ya activa y un diagnóstico que te dirá la causa en un run manual.

**Hipótesis, de más a menos probable (ninguna verificada):**
1. **Caché del HTML** (CDN delante de la ficha): el navegador y `requests` reciben copias con distinta antigüedad. Se prueban `nocache`, `bust` (parámetro único) y `fresh` (ambas) y se imprimen las cabeceras
   `Age`/`Cache-Control`/`Date`/`Last-Modified`/`Expires`/`X-Cache` de cada respuesta.
2. **Contenido distinto por cabeceras / sesión / geografía** (User-Agent de navegador, cookie de entrada del sitio, IP de EE. UU. del runner): variantes `browser`, `browser_fresh`, `passthrough`
   (`siteEntryPassthrough=true`, parámetro que usa iShares para saltar su pantalla de entrada; *no verificado que siga vigente*) y la misma ficha en otros sitios (`alt:uk|de|ie|es|pro`, que pueden no existir: saldrá HTTP 404).
3. **El NAV del navegador viene de una XHR/JSON aparte** que el HTML no incluye. El diagnóstico busca en el HTML las URLs `*.ajax`/`*.json`, las describe sin id ni nombre del producto y las prueba (`xhr1`...), buscando
   un NAV con fecha en JSON, HTML o CSV. **No he implementado ninguna XHR en el scraper**: sin ver su forma real sería escribir un parser a ciegas; si el diagnóstico encuentra una más fresca y estable, es el siguiente paso.
4. **Varias fechas en la misma página** (por ejemplo, un «NAV as of 27/Oct» en una caja y el 28 en otra): el parser usa el primero. El diagnóstico imprime las fechas distintas junto a «NAV as of» (`x2`...) y el log del build ya cuenta
   `nav_varios`.
5. **Hora**: que el navegador se mirase después de publicarse el 28. Se descarta repitiendo la `base` (`base_again`) y mirando `Date`/`Age`.
(Descartado por lectura de código: no hay caché propia entre runs — `_ISHARES_PAGES` vive solo dentro del proceso — ni el NAV pasa por Supabase antes de leerse.)

**Qué hay hecho:**
* `fetch_market_data.ishares_request(url, variante)` define las variantes; `_ishares_plain` acepta `variant` (por defecto la petición de siempre, así que v1 no cambia; cada variante tiene su propia entrada de caché).
* **Mitigación activa** `fetch_ishares_nav_freshest`: el scraper de datos propios pide `base` y `fresh` y publica el NAV de **fecha más reciente** (empate: `fresh`; si una variante falla, vale la otra). La ficha ganadora es la que usan los *facts*.
  Cuesta **2 peticiones a iShares por ficha y run** (antes 1): con dos fondos, 136 al día. Si el diagnóstico demuestra que basta una variante (o que ninguna sirve), se deja `NAV_FETCH_VARIANTS` con una sola.
  No se usa User-Agent de navegador por defecto: hacerse pasar por un navegador para esquivar protección anti-bots es una decisión tuya (términos de uso), no un arreglo técnico neutro.
* **Log del build** (sin identificar fondos): fecha de NAV más reciente y más antigua con su edad, `variante de la petición ganadora: fresh x1` y `una variante distinta de la base adelantó la fecha en N fondos (hasta +K d)`.
  Ejemplo: `… fecha de NAV más reciente 2026-10-28 (0 d), más antigua 2026-10-28 (0 d), decimales del NAV: 2 (x2), variante de la petición ganadora: fresh x2, una variante distinta de la base adelantó la fecha en 2 fondos (hasta +1 d) [run …]`.
* **Diagnóstico `nav-probe.yml`** (+ `scripts/nav_probe.py`): solo `workflow_dispatch`, permisos `contents: read`, sin artefactos, sin escribir en Supabase ni en ningún sitio. *Matiz sobre «sin secretos»:* necesita `PERSONAL_FEED_TOKEN` para leer tus fichas
  (lo pediste), pero solo en el paso de lectura (`python3 -S`, biblioteca estándar); el paso que hace las peticiones (`requests`) no lo recibe. Entradas: `max_funds` (4), `cache_headers` (true), `try_xhr` (true).
  *Matiz sobre «imprime SOLO fecha y valor»:* además imprimo código HTTP, tamaño y —desactivable con `cache_headers=false`— cabeceras de caché de la respuesta, porque son lo que distingue «caché» de «otra fuente» y no identifican ninguna ficha.
  Las fichas salen como `#1`, `#2`; nunca ISIN, nombre ni URL (tests con ISIN/nombre/URL/ticker falsos y fallos de red cuyo mensaje lleva la URL). Con 4 fichas hace ~100 peticiones a ishares.com con 1 s entre ellas.
  Salida de ejemplo (simulada):
  ```
  #1 base           HTTP 200    63 KB  NAV 27/Oct/2026 = 12.48  [age=5400 date=Wed, 28 Oct 2026 10:00:00 GMT cache-control=max-age=7200]
  #1 fresh          HTTP 200    63 KB  NAV 28/Oct/2026 = 12.48  [age=0 cache-control=no-store]
  #1 xhr1           /ch/individual/en/products/*/*/1521942788811.ajax?fileType=json&tab=overview HTTP 200  1 KB  NAV 28/Oct/2026 = 13.64
  #1 RESUMEN: fecha de NAV más reciente 28/Oct/2026; la dan: fresh bust browser_fresh; la base da 27/Oct/2026  <-- la base va por detrás
  VEREDICTO: variantes que dan una fecha más reciente que la base (nº de fichas): fresh x1, bust x1, browser_fresh x1
  ```
* Tests: `tests/test_nav_probe_F2.py` (29): variantes, elección del NAV más reciente (gana la fresca / empate / la fresca va por detrás / una variante falla / todas fallan), log, diagnóstico completo con respuestas simuladas (texto real de ficha con la fecha
  cambiada), privacidad, XHR descubierta y redactada, y el workflow (solo manual, sin escritura, token solo al leer, SHA fijados).

**Qué haces tú:** Actions → **NAV probe (diagnóstico)** → Run workflow (valores por defecto). Pégame (o léete) el `RESUMEN`/`VEREDICTO`. Según salga: caché → ya mitigado (quitar la variante sobrante); XHR/`alt` → cambio de fuente (lo implemento con la forma real a la vista);
nada supera a la base → el navegador usa sesión/JS: mira en su pestaña *Red* (DevTools) la petición que trae el NAV y dime su ruta sin ISIN.

## 0. Lo que NO está demostrado (leer primero)
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

## 2. Coste de cada run frecuente (payload completo) — **medidas simuladas**
Sin red no se pueden medir las descargas reales; he medido con el código real (`build_payload` + validador) y series sintéticas de ~520 cierres (2 años), y cuento peticiones, bytes y CPU. Cada run baja **dos variantes de la ficha de
iShares por fondo (ver T0)** y **una serie de Yahoo (2 años, 1 petición) por símbolo único** que no esté en el universo público (los proxies `EUNL.DE` e `IS3N.DE` **no** están en `registry.json`, así que cuentan).

| Escenario | Peticiones a Yahoo/run | Yahoo/día (×34) | Payload/run | Escrituras a Supabase/día | CPU build+valida |
|---|---|---|---|---|---|
| A) 2 fondos + sus 2 proxies (tu caso probable) | 2 | **68** | 20,6 KB | 0,7 MB | 0,01 s |
| B) A + 10 símbolos propios | 12 | 408 | 119,5 KB | 4,1 MB | 0,04 s |
| C) 5 usuarios × (2 fondos, 10 símbolos, 5 comunes) | 32 (60 sin dedup) | 1.088 | 603 KB | 20 MB | 0,12 s |
| D) tope: 20 usuarios × (20 fondos, 40 símbolos) | 420 (1.200 sin dedup) | 14.280 | 8,1 MB | 276 MB | 1,9 s |

* Referencia: el feed público hace ~545 peticiones a Yahoo al día; el job `personal` antiguo hacía 2 × 4 = 8.
* **Optimización incluida (sin SQL):** los símbolos que siguen varios usuarios (y los que fallan) se piden **una sola vez por run** (`series_cache`). Antes: una petición por usuario y símbolo.
* **Tiempo y bytes reales de Yahoo/iShares: no medidos.** Estimación a verificar: ficha de iShares (~cientos de KB de HTML) y serie 2 años de Yahoo (~decenas de KB); el ritmo mínimo entre peticiones a Yahoo es 0,15–0,35 s.
  Run A: ~10–30 s de build; el run completo ronda **1,5–2 min de runner**, casi todo `pip install` (con caché). 34 × 2 min × ~22 días ≈ **1.500 min/mes**: gratis en repo público; en un repo privado se comería ~75 % del
  cupo gratuito. **El primer run real lo medirá:** el log trae `coste del run: N series pedidas a Yahoo, X KB, T s` (solo contadores).
* **Veredicto:** A y B son aceptables con 30 min. C está en el límite de lo razonable para la IP compartida del runner (≈2× el feed público solo en Yahoo). D **no es viable** con payload completo: 420 peticiones ≈ 7–14 min,
  rozando el presupuesto de 14 min de Yahoo.
* **Alternativa si el coste molesta (requiere SQL, NO tocado):** una RPC nueva que actualice **solo `funds`** del payload guardado (p. ej. `publish_personal_nav(p_token, p_user, p_funds jsonb)` con fusión), de modo que el
  workflow frecuente solo lea iShares (sin Yahoo ni series: ~2 peticiones, <1 KB) y el histórico largo se refresque con el cron antiguo (4/día). Además evita el riesgo 2 de abajo.
  **Pregunta para ti:** ¿quieres esa RPC? Con tu lista actual (A) no hace falta; sí si vas a seguir muchos símbolos o más usuarios.

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
* `python -m pytest -q`: **1232 passed**. `ruff check --select F scripts tests`: limpio. `python -m py_compile scripts/*.py tests/*.py`: ok. `sanity_check_v2` pasa dentro de `tests/test_sanity_check_v2.py` y los tests
  de generación (no hay CLI que ejecutar sin un sitio generado). `actionlint` no está disponible en la sesión (CI lo omite con aviso); YAML validado con PyYAML.
* **Fallo previo en `main`** (no mío): `test_flujo_completo_lee_la_lista_y_publica_por_usuario` dependía del reloj real (`generated_at` > fecha fijada del test) y falla con el reloj real actual (el sandbox marca 29-10-2026). `main()` acepta ahora `now` y el
  test lo inyecta. (Si lo ves en rojo en otras ramas, es eso.)
* Tests de workflows reorganizados: el job `personal` ya no está en `publish-feed-v2`; hay tests propios para `personal-nav.yml` (permisos, concurrencia, cron, gating, token en solo 2 pasos, SHA fijados, salida temprana,
  stdlib-only con `python3 -S`).

## 6. Riesgos
1. **Una serie de Yahoo que falla borra esa serie en Supabase hasta el siguiente run** (el payload se sustituye entero y `build_payload` descarta la serie fallida). Con 34 runs/día hay más oportunidades de un 429
   puntual; el NAV no se ve afectado, el histórico sí durante ≤30 min. No lo he cambiado (decidir entre «publicar sin la serie» y «no publicar a ese usuario» es tuyo y depende de cómo lo trate la app). La RPC de solo
   `funds` (§2) lo resuelve de raíz.
2. **Tope de series sin avisar** (ya existía): `series` admite ≤ 40 y el payload lleva símbolos + proxies de los fondos. Un usuario con > 40 en total queda con payload inválido y **no se publica nada de él, ni el NAV**
   (cuenta `payload_invalido` en el log). No afecta a una lista como la tuya.
3. **Sin heartbeat** de Healthchecks para este workflow: si el cron deja de dispararse (60 días sin actividad en el repo, FEED_V2_ENABLED mal puesta, GitHub descartando runs) solo lo notarás por la fecha del NAV. Opcional:
   un check propio con periodo ~1 h y un secreto nuevo (no lo he añadido para no inventar secretos).
4. **Solo corren los `schedule` del workflow en la rama por defecto**: hasta que se fusione en `main`, no hay cron (el lanzamiento manual sí).
5. **Términos de uso de iShares/Yahoo**: 34 lecturas al día de la misma ficha es más tráfico que 4; sigue siendo pequeño (2 peticiones por run) pero el riesgo documentado en el README se mantiene y aumenta con la frecuencia.
6. **Minutos de Actions**: gratuitos en repo público; ~1.500 min/mes si algún día fuera privado.
7. **Suposiciones sin verificar por falta de red:** hora de publicación de iShares, que no haya cambios en fin de semana, tamaños/tiempos reales de Yahoo/iShares, y que el formato de 2 decimales sea estable.

## 7. Pasos manuales para ti
1. Revisar el diff y fusionar en `main` (sin eso el cron no existe). Comprobar que la variable `FEED_V2_ENABLED` es `true` si quieres el cron; `SUPABASE_URL`, `SUPABASE_PUBLISHABLE_KEY` y `PERSONAL_FEED_TOKEN` ya existen.
2. Actions → **Personal NAV** → *Run workflow* (una vez, a mano). Abrir el paso «Build personal payloads» y leer la línea `datos propios: NAV: …`: fondos, con NAV exacto, fecha más reciente/más antigua, decimales (debe decir `2`),
   `coste del run` (te da los números reales de peticiones, KB y segundos que aquí solo pude estimar) y que **no** haya `::warning::` de «sin NAV exacto».
3. Comparar esa fecha de NAV y los valores con la ficha pública de iShares y con el dashboard. Si el log y la ficha coinciden y el dashboard no → el fallo está en la app (caché o `change_pct` sobre un NAV fresco).
4. Durante 3–5 días laborables, mirar a qué hora (`[run …Z]`) pasa la fecha más reciente a la del día. Con eso se decide si 30 min sobran (pasar a cada hora o estrechar 05–21) o si hay que afinar.
5. Si quieres evitar re-publicar series cada 30 min: decidir la RPC de solo `funds` (§2). **No** he escrito SQL.
6. Opcional: un check de Healthchecks para este workflow (§6.3).
