# FIXES AU01-F1 — Puerta de frescura, hot cada 45 min, parada temprana del NAV y cron de respaldo

Rama `claude/au01-f1-puerta-frescura` (desde `main` en `b6b42b7`). Un commit por pieza:

| Pieza | Commit | Qué |
|---|---|---|
| 1 | `4d1c100` | puerta de frescura en `publish-feed-v2.yml` (`scripts/freshness_gate.py`, job `gate`, input `force`) |
| 2 | `a7a56da` | `TIER_MIN_INTERVAL_HOURS["hot"]` de 3 a 0.75 |
| 3 | `951bd05` | parada temprana del paso `watch --nav-only` de `personal-nav.yml` |
| 4 | `d108393` | cron de GitHub reducido a respaldo + `github.ref == 'refs/heads/main'` en los jobs con secretos |
| 5 | `5b0620c` | README («Cadencia real») y referencias de cadencia del contrato |
| 4 (corrección) | `31415f0` | prueba antigua que fijaba el cron viejo de personal-nav (ver abajo y §3) |
| — | (este) | este informe |

Contexto: los cron de GitHub se retrasan horas y se saltan franjas; un disparador externo (Cloudflare Worker, repo privado, otra sesión) lanzará los
`workflow_dispatch` por horario de mercado. Esta fase prepara ESTE repo para dos relojes (disparador externo + cron de respaldo) sin duplicar trabajo
ni cargar de más a Yahoo/iShares. `update-data.yml` (feed v1), `keepalive.yml`, `supabase-keepalive.yml`, `nav-probe.yml` y `ci.yml` **no se han tocado**.
No se ha cambiado ningún ajuste del repositorio.

---

## 1. Comandos ejecutados y salidas en bruto

Entorno: Python 3.13 local (CI usa 3.14), venv con `pip install --require-hashes --no-deps -r requirements.txt` y `-r requirements-dev.txt` (pytest 9.2.0, ruff 0.17.3, PyYAML 6.0.3).
`actionlint` no está instalado en este entorno: **no se ha ejecutado** (en CI se ejecuta solo si el runner lo trae).

**Línea base en `main` antes de tocar nada:**

```
$ python -m pytest -q
1489 passed, 1 warning in 160.95s (0:02:40)
$ ruff check --select F scripts tests
All checks passed!
```

**Final, en la punta de la rama:**

```
$ python -m pytest -q          # primera pasada completa, tras la pieza 5
FAILED tests/test_nav_fresco_F.py::test_readme_documenta_el_workflow_nuevo_su_cron_y_la_salida_temprana
1 failed, 1696 passed, 1 warning in 201.48s (0:03:21)

$ python -m pytest -q          # tras el commit 31415f0
1697 passed, 1 warning in 199.28s (0:03:19)
$ python -m pytest -q --co | tail -1
1697 tests collected in 0.67s
$ ruff check --select F scripts tests
All checks passed!
$ python -m py_compile scripts/*.py tests/*.py
(sin salida: OK)
```

Bloques: 1489 (base) + 208 nuevas = 1697, 0 fallos. El único aviso es un `DeprecationWarning` de `feedparser`, igual que en la base.

**Fallo encontrado en la batería completa (dicho aquí, no escondido):** tras la pieza 4 solo ejecuté los bloques de workflows (`test_workflows*.py`,
`test_puerta_frescura_F1.py`), no la batería entera; una prueba antigua de otro fichero (`test_nav_fresco_F.py`) fijaba el cron viejo de personal-nav y fallaba
desde ese commit. Se corrigió en `31415f0` (REQUISITO CAMBIADO, §3). Los commits `d108393` y `5b0620c`, por separado, tienen esa prueba en rojo.

### Rojo → verde → control negativo, por pieza (salidas en bruto, recortadas a las líneas de resumen)

**Pieza 1 (puerta).** Rojo de las pruebas del workflow antes de editar el YAML (el script se escribió antes que sus pruebas; su rojo se demuestra con las mutaciones):

```
$ python -m pytest -q tests/test_puerta_frescura_F1.py
FAILED ...::test_input_force_booleano_por_defecto_false
FAILED ...::test_gate_es_el_primer_job_sin_secretos_ni_permisos_y_solo_stdlib
FAILED ...::test_los_jobs_de_trabajo_se_saltan_si_la_puerta_dice_skip_y_fallan_cerrado
FAILED ...::test_heartbeat_recibe_la_salida_de_la_puerta
FAILED ...::test_heartbeat_salida_por_frescura_manda_log_y_ni_ok_ni_fail
FAILED ...::test_control_negativo_sin_la_rama_de_frescura_el_latido_daria_un_falso_fail
7 failed, 82 passed            (el 7.º era un artefacto de la propia prueba: 39,99 min se trunca a segundos => 40,0; se cambió a 39,5)
```

Verde: `89 passed`. Controles negativos (mutación real del código, restaurado después):

```
M1 sin puerta (decide nunca devuelve skip)            -> 21 failed (entre ellos test_dos_disparos_seguidos_solo_trabaja_uno[1|10|25|39])
M2 fallo abierto (generated_at ilegible = fresco)      -> 36 failed (test_decide_generated_at_malformado_trabaja[*], test_manifest_malformado_trabaja[*]...)
M3 se acepta un generated_at en el futuro              -> 4 failed
M4 latido sin la rama SKIP                             -> 2 failed (test_heartbeat_salida_por_frescura_..., test_control_negativo_...)
M5 fetch con `skip == 'false'` (fallo abierto si gate no da salida) -> 1 failed (test_los_jobs_de_trabajo_se_saltan_...)
```

**Pieza 2 (hot).** Rojo con el valor 3 h: `test_intervalo_hot_es_45_min...`, `test_frontera_hot_44_no_46_si[45|46|60]`, `test_un_run_una_hora_despues...`,
`test_margen_...[-14..120]`, `test_la_puerta_de_frescura_y_el_intervalo_hot_son_coherentes`, `test_una_jornada_horaria...` y `test_con_429_sostenido...` fallaban. Verde: `25 passed`.
Batería completa tras el cambio: `1603 passed` (ninguna prueba antigua fijaba 3 h; ver §3). Controles negativos con valores vecinos:

```
hot = 0.5 (30 min) -> 6 failed (frontera 30/44/44,98 min, run a los 44 min, coherencia con la puerta)
hot = 1   (60 min) -> 7 failed (frontera 45/46, margen -14/-10/-5 min, coherencia con la puerta)
```

Medición (`V2_MEASURE=1`, Yahoo simulado, registro real):

```
jornada horaria: 14 runs, peticiones a Yahoo por run [251, 98, 98, 98, 98, 98, 98, 98, 98, 98, 98, 98, 98, 98], total 1525
medición antigua (tests/test_feed_v2.py -k medicion), antes -> después:
  alta 1   129 -> 129 ; alta 2  80 -> 178 ; alta 3  64 -> 162   (los runs de alta van a +1 h: ahora los hot también tocan)
  solo hot (+6 h) 98 -> 98 ; hot + warm (+21 h) 251 -> 251 ; hot (+6 h) 98 -> 98
```

**Pieza 3 (parada temprana).** Rojo contra el `personal_v2.py` anterior: `47 failed, 2 passed`. Verde: `49 passed`. Controles negativos:

```
N1 regla laxa (esperar el NAV de 2 días hábiles atrás)  -> 8+ failed (todos al día, uno atrasado, lunes, festivo, watch lunes/festivo, visitas por día)
N2 fallo abierto (sin fecha = al día)                    -> 8+ failed (test_fecha_malformada_o_ausente_fallo_cerrado[*])
N3 sin parada temprana (work siempre con fichas)          -> 2 failed (test_watch_todos_al_dia..., test_watch_lunes)
N4 «hoy» cuenta como último día hábil esperado            -> 14 failed
```

**Pieza 4 (cron y main).** Rojo antes de editar los YAML: `13 failed, 31 passed`. Verde: `44 passed` (+1 de la pieza 5 = 45). Controles negativos:

```
P1 quitar la condición de main de personal-nav      -> 2 failed
P2 personal-full en el minuto :30                   -> 2 failed
P3 heartbeat de v2 sin la condición de main         -> 2 failed
P4 cancel-in-progress: true en personal-full        -> 1 failed
P5 una URL de Healthchecks con clave en un comentario -> 1 failed
P6 personal-nav con 5 franjas                       -> 3 failed
```

**Pieza 5 (README).** README de `main` restaurado -> `1 failed`; una hora cambiada en la tabla del README (16:23 -> 15:23) -> `1 failed`.

---

## 2. Qué demuestra cada prueba y qué NO

### Pieza 1 — `tests/test_puerta_frescura_F1.py` (89)

* **Demuestra**: la función pura `decide(now, generated_at, max_age_min, force)` (frontera exacta en 40 min, `force`, malformados, reloj adelantado, umbral inválido);
  el envoltorio (lectura del manifest inyectada; 404, errores de red, JSON roto, `generated_at` ausente => `skip=false`; `force` solo cuenta en `workflow_dispatch`;
  URL no https => no pide nada); la lectura **real** con `urllib` contra un servidor HTTP local con el parámetro anti-caché `?cb=`; la simulación de **dos disparos
  seguidos** (con < 40 min solo trabaja uno; con >= 40 min trabajan los dos); la estructura del job `gate` (sin secretos, sin permisos, `python3 -S`, misma URL
  base que `fetch`); que `fetch` falla **cerrado** (`!cancelled()` y `skip != 'true'`); y el latido ejecutado en bash con `curl` falso (salida por frescura =>
  un único `/log`, ni OK ni `/fail`; sin salida de la puerta, todo igual que antes).
* **NO demuestra**: el comportamiento real de GitHub Actions con `needs`/`if` y `outputs` entre jobs (no se ha ejecutado en Actions; `actionlint` no se ha
  ejecutado); la caché real de Pages (si Pages sirviese un `manifest.json` viejo unos minutos tras un despliegue pese al `?cb=`, un run pendiente podría trabajar
  dos veces: solo cuesta peticiones, no corrompe nada); que `/log` de Healthchecks no cambie el estado del check (es lo que dice su documentación; no contrastado aquí).
* **Prueba que podría repetir el error del código**: la simulación de dos disparos usa el mismo `decide`; si la regla estuviese mal pensada (p. ej. 40 min fuese
  demasiado), la prueba lo daría por bueno. **Sería incorrecto si**: el feed publicado se considerase fresco sin serlo (la frescura se mide desde `generated_at`, que es la
  hora de **inicio** del run anterior, no la de publicación: ver supuesto S3).

### Pieza 2 — `tests/test_nivel_hot_F1.py` (25)

* **Demuestra**: fronteras 44 min (no), 44,98 (no), 45 (sí), 46 (sí); `warm` sigue en 20 h; con el **reloj real del generador** (`generated_at` = `last_ok` = hora de inicio
  del run, no la de fin), un run 60 min después refresca **todos** los hot y solo ellos, uno a 44 min no pide nada, y uno con desfase de arranque de −14 a +120 min
  sigue refrescándolos; una jornada horaria de 14 runs con el **registro real** (49 hot): 98 peticiones a Yahoo por run de solo hot, todas `1mo`; el limitador
  (`YF_REQ_GAP`, `YF_BURST_*`, abandono por 429, presupuestos de 13/17 min) conserva sus valores; con 429 sostenido un run horario abandona Yahoo, los hot quedan
  `deferred` y conservan sus datos, sin fallos.
* **NO demuestra**: el comportamiento real de Yahoo con ~1.525 peticiones al día desde IP compartidas de runners (2,8 veces más que antes; por run no cambia). El
  limitador es **por run**: no hay presupuesto diario, así que nada frena una cadencia mayor que la decidida. La jitter de `fetch_market_data` (pausas entre
  peticiones) ocurre **después** de fijar `now`, así que no desplaza `last_ok`; lo que sí lo desplaza es la duración variable de checkout + pip + descarga del sitio
  anterior y la latencia del dispatch, que **no están medidas**.

### Pieza 3 — `tests/test_parada_temprana_F1.py` (49)

* **Demuestra**: la regla del último día hábil esperado (lunes→viernes, martes→lunes, sábado/domingo→viernes, cambio de año; en todo 2026 nunca es hoy ni futuro y
  nunca más de 3 días atrás); todos al día => `work=false` sin visitar, uno atrasado => `work=true`, lunes, tras un festivo (25-dic) => se sigue intentando, sin
  fondos => igual que antes, varios usuarios, fondos sin ficha válida ignorados, fecha futura o malformada (13 formas) => se visita, campo ausente (el SQL de hoy)
  => igual que antes; el modo completo no para nunca; el campo nuevo no entra en `p_funds` ni en el payload completo; de punta a punta con Supabase simulado en local;
  simulación de visitas por día (3 en vez de 14 si el NAV aparece en la 3.ª pasada; 14 en un festivo, igual que antes).
* **NO demuestra**: que el SQL real devuelva `prev_nav_date_iso` (**no existe todavía**; ver D1); cuándo publica iShares el NAV de cada fondo (si la ficha va
  sistemáticamente 2 días hábiles por detrás, la parada temprana casi no ahorra visitas, aunque sigue sin dar falsos «al día»); la cifra real de visitas.
* **Sería incorrecto si**: el NAV de hoy pudiese aparecer en la ficha el mismo día y ya hubiese uno «del último día hábil» guardado: la parada temprana lo recogería
  como tarde al día hábil siguiente (y `personal-full`, que siempre lee la ficha, antes). Es un retraso acotado, no un dato falso.

### Pieza 4 — `tests/test_workflows_F1.py` (44 + 1 de la pieza 5)

* **Demuestra**: los cron son exactamente los de la tabla, sin franjas en :00/:30, 2-4 franjas por día que corre con mañana y tarde, L-V/fin de semana según
  corresponda, menos pasadas que antes, sin coincidir con v1 ni entre sí; `update-data.yml` intacto; `workflow_dispatch` y `concurrency` con `cancel-in-progress:
  false`; todos los jobs con `secrets.` llevan la condición de `main`; se mantiene el `FEED_V2_ENABLED`; **evaluación de las condiciones `if:`** en escenarios
  (cron en main, dispatch en main, dispatch desde otra rama o tag, salida por frescura, puerta sin salida) con un evaluador del subconjunto de expresiones que
  usan estos YAML; ningún YAML contiene URL de Supabase, de Healthchecks con clave, claves, JWT, tokens de GitHub, ISIN ni ids de ficha (con controles negativos del detector).
* **NO demuestra**: el evaluador no es el de GitHub (solo cubre `==`, `!=`, `&&`, `||`, `always()`, `!cancelled()` y contextos); no se ha lanzado ningún run real
  desde otra rama. Tampoco que GitHub programe los cron de respaldo a su hora (no lo hace: ese es el motivo del disparador externo).

### Lo que ninguna prueba mide (fase en sombra)

La **puntualidad y la latencia reales de `workflow_dispatch`** (cola de GitHub, arranque del runner), la caché real de Pages tras un despliegue, la cadencia
efectiva con los dos relojes y el comportamiento de Yahoo/iShares con la frecuencia nueva. Hasta medirlo, el README mantiene el umbral de cliente de ~14 h.

---

## 3. Pruebas adaptadas (REQUISITO CAMBIADO) y pruebas nuevas

Ninguna comprobación se ha quitado; donde cambia un valor fijo se dice por qué.

| Prueba | Cambio | Justificación |
|---|---|---|
| `test_workflows.py::test_v2_estructura_permisos_minimos_y_dependencias` | jobs `{fetch, publish, verify, heartbeat}` → `+gate`; heartbeat `needs` `+gate`; **añade** `gate` sin permisos ni `needs` y `fetch.needs == gate` | REQUISITO CAMBIADO (pieza 1): la puerta es un job nuevo |
| `test_workflows.py::test_v2_el_cron_esta_gobernado_por_la_variable_y_el_workflow_viejo_no_cambia` | cron `47 6,11,15,21 * * *` → `47 7,13,21 * * 1-5` + `47 9,18 * * 0,6`; inputs `+force` | REQUISITO CAMBIADO (piezas 1 y 4). Se mantiene la comprobación de que el cron viejo de v1 no cambia |
| `test_workflows.py::test_personal_nav_cron_frecuente_en_minutos_feos_solo_laborables_y_con_dispatch` | 2 minutos y rango de horas → 1 minuto y lista de horas; **añade** 2-4 franjas con mañana y tarde | REQUISITO CAMBIADO (pieza 4). Se mantienen: solo L-V, minutos prohibidos, tope de 40, v1 intacto |
| `test_nav_fresco_F.py::test_readme_documenta_el_workflow_nuevo_su_cron_y_la_salida_temprana` | cron `23,53 5-21 * * 1-5` → `23 7,11,16 * * 1-5` | REQUISITO CAMBIADO (pieza 4). Sigue exigiendo el mismo cron en el YAML, el README y el contrato, y los demás fragmentos |
| `test_contract_docs_v2.py` (fixture `examples["watch"]`) | el ejemplo de `personal_watch_for_backend` lleva `prev_nav_date_iso` | El campo opcional nuevo se documenta con el ejemplo que el propio test regenera (`UPDATE_CONTRACT_DOCS=1`) y valida con `parse_watch` |

**Pruebas que fijaban 3 h para hot: ninguna.** Las existentes usan saltos de 6 h o 21 h, que valen igual con 45 min. Los dos asserts de la medición antigua
(`rows[3]` y `rows[5]` <= 2 × hot) siguen igual; solo cambian las cifras impresas de los runs de alta (ver §1).

Nuevas: `tests/test_puerta_frescura_F1.py` (89), `tests/test_nivel_hot_F1.py` (25), `tests/test_parada_temprana_F1.py` (49), `tests/test_workflows_F1.py` (45). Total 208.

---

## 4. Decisiones y supuestos (con la postura contraria; decide Alex)

* **S1. Latido al salir por frescura = `/log` (ni OK ni `/fail`).** El run que trabajó hace < 40 min ya mandó su OK o su `/fail` «degraded»; un OK ahora podría tapar
  ese aviso y un `/fail` sería falso. `/log` es el mismo mecanismo que ya usa `verify` (`pages-stale`). *Contraria*: un OK (o ningún ping) es más simple; un OK mantiene
  vivo el contador del check si el disparador solo produce salidas por frescura (no puede pasar: alguien tuvo que publicar hace < 40 min, y ese run pingó).
* **S2. La puerta es un job aparte (`gate`), no un paso de `fetch`.** Así ningún paso con secretos llega a arrancar y `publish`/`verify` se saltan solos. Coste: un
  runner más (~10-20 s). *Contraria*: un paso dentro de `fetch` ahorra un arranque, pero obliga a poner `if:` en cada paso y a pasar la salida a `publish`.
* **S3. Edad medida desde `generated_at` (hora de INICIO del run anterior).** Es lo que publica el manifest. Un run que tarda 15 min deja, tras publicar, solo ~25 min
  de ventana de bloqueo. *Contraria*: medir desde la publicación exigiría otro campo en el manifest (contrato de salida: no se toca).
* **S4. Umbral 40 min (parámetro `--max-age-min`, sin variable de repo).** No se añade una variable para no tocar ajustes. *Contraria*: una variable `vars.X` permitiría
  ajustarlo sin commit.
* **S5. hot = 45 min y puerta = 40 min.** Franja «muerta» (pasa la puerta pero no toca ningún hot): edad del manifest en [40, 45) min en el momento de la puerta, antes de
  sumar la preparación del propio run (checkout + pip + descarga del sitio, varios minutos): en la práctica no existe. Margen del intervalo hot con runs horarios: 15 min
  de desfase de arranque. **Si la fase en sombra muestra desfases > 15 min** (cola de dispatch, descargas lentas), el valor a proponer es **0.65 h (39 min)**: por
  debajo de la puerta, todo run que la pase refresca los hot. *Contraria*: 39 min permite que un run de respaldo que pase la puerta a los 40 min repita los hot
  (49 peticiones de más), justo lo que se quiere evitar; por eso se deja 45 hasta medir.
* **S6. Regla del último día hábil esperado** = último día L-V **estrictamente anterior** a la fecha UTC del run; el NAV de hoy o del esperado cuenta como al día; una fecha
  futura o inválida, no. *Contraria*: depender de la hora (p. ej. antes de las 10:00 UTC esperar el de antes de ayer) ahorraría visitas por la mañana, pero un error ahí
  sí daría falsos «al día»; se prefiere visitar de más.
* **S7. «Respuesta de Supabase malformada» = fechas malformadas o ausentes** (=> se visita). Si la RPC entera falla o no es una lista, el comportamiento sigue siendo el
  de antes (`work=false`): sin lista no hay fichas que visitar. *Contraria*: hacer fallar el job en ese caso para que lo vea el latido (hoy solo es un `::warning::`).
* **S8. Cron de respaldo** (tabla en §6): personal-full 3 al día todos los días (antes 4); el feed 2 franjas en fin de semana (cripto y noticias). *Contraria*: 2 al día
  para personal-full si el disparador externo también lo lanza.
* **S9. Condición de main también en `publish`** (no tiene secretos, pero despliega en Pages) y **no** en `gate` (sin secretos; no puede hacer daño). Los jobs se
  saltan en verde desde otra rama. *Contraria*: un dispatch desde otra rama podría fallar en rojo para que se note.
* **S10. `force` solo vale en `workflow_dispatch`**; un dispatch del disparador externo sin `force` pasa por la puerta (si no, el reloj externo duplicaría al cron).
* **S11. Nombre de rama**: el encargo pide `claude/au01-f1-puerta-frescura`; el entorno de la sesión traía otra asignada (con sufijo). Se ha usado la del encargo.

## 5. Desviaciones

* **D1. La parada temprana necesita un cambio de SQL que no está en este repo.** `personal_watch_for_backend` hoy devuelve por fondo `isin, name, url, proxy_symbol,
  prev_price`, **sin la fecha del NAV guardado**: el paso `watch` no «consulta lo necesario». Se ha añadido el campo **opcional** `prev_nav_date_iso` (contrato §5) y,
  mientras el SQL no lo envíe, **todo sigue como antes** (se visita siempre). **Orden obligatorio**: primero fusionar esta rama, después el SQL (`parse_watch`
  descarta entero un fondo con una clave desconocida). *Contraria*: deducir la frescura sin SQL (no hay ninguna fuente en el repo: el payload guardado solo se lee por esa RPC).
* **D2. `publish` y `verify` llevan ahora `if:` explícito** (`needs.<anterior>.result == 'success'`), no el `success()` implícito, para que un `gate` fallido (fallo
  cerrado: `fetch` corre igualmente) no deje `publish` saltado. Con `gate` correcto el resultado es el mismo que antes.
* **D3. Contrato `docs/CONTRATO_FEED_V2.md`**: solo cambian el contrato de **entrada** (`prev_nav_date_iso` opcional) y frases de cadencia. El contrato de salida del feed
  (manifest y ficheros publicados) no cambia.
* **D4. No se tocan `nav-probe.yml`, `supabase-keepalive.yml`, `keepalive.yml`** (fuera de alcance): siguen sin la condición de main. `nav-probe.yml` usa
  `PERSONAL_FEED_TOKEN` en un `workflow_dispatch` y se puede lanzar desde otra rama (ver pasos manuales).
* No ha hecho falta tocar `ci.yml`.

## 6. Tabla de cron antes / después (UTC)

| Workflow | Antes | Después (respaldo) | Pasadas/semana |
|---|---|---|---|
| `publish-feed-v2.yml` | `47 6,11,15,21 * * *` (4 al día, todos los días) | `47 7,13,21 * * 1-5` + `47 9,18 * * 0,6` | 28 → 19 |
| `personal-nav.yml` | `23,53 5-21 * * 1-5` (34 al día, L-V) | `23 7,11,16 * * 1-5` | 170 → 15 |
| `personal-full.yml` | `38 6,11,15,21 * * *` (4 al día) | `38 6,13,21 * * *` | 28 → 21 |
| `update-data.yml` (v1) | `17 6,11,15,21 * * *` | **sin cambios** | 28 |

A esto se suma lo que lance el disparador externo (plan: feed y NAV cada ~60 min en sesión). Coste en Yahoo del feed con runs horarios: (N − 1) × 98 + 251 al día
(≈ 1.525 con 14 runs; antes ≈ 545). Coste en iShares del NAV: 1 visita por fondo y run **que no para pronto**.

## 7. Pasos manuales para Alex (no hechos aquí)

1. Revisar y abrir el PR de `claude/au01-f1-puerta-frescura` contra `main`; con «Squash and merge», borrar del mensaje cualquier `Co-authored-by` que añada GitHub.
2. **Después** de fusionar: en el SQL de `personal_watch_for_backend`, añadir por fondo `prev_nav_date_iso` = `nav_date_iso` del fondo en el payload guardado (o nada si no hay).
   Nunca antes de fusionar (D1).
3. Disparador externo: lanzar siempre con `{"ref":"main"}` (con otra rama los jobs se saltan); `force` solo para pruebas a mano. Token *fine-grained* de este repo con
   solo **Actions: Read and write**, guardado solo en el Worker.
4. **Secretos limitados a main (decisión ya tomada)**: la condición `github.ref` es una defensa en el YAML, no un control de GitHub. Para que lo garantice GitHub:
   crear un Environment (p. ej. `feed-main`) con *Deployment branches* = solo `main` y mover ahí `FRED_API_KEY`, `HC_UPDATE_V2_URL`, `HC_UPDATE_URL`, `PERSONAL_FEED_TOKEN`,
   `HEALTHCHECK_PERSONAL_NAV_URL`, `SUPABASE_URL`, `SUPABASE_PUBLISHABLE_KEY`. **Ojo: los jobs solo ven los secretos de un Environment si declaran `environment:`**; ese
   cambio de YAML NO está en esta rama (sería una fase siguiente). No muevas los secretos hasta que exista, o los workflows se quedarán sin ellos.
5. Comprobar en *Settings → Environments → github-pages* que *Deployment branches* sigue restringido a `main`.
6. Decidir si `nav-probe.yml` debe llevar también la condición de main o ir a ese Environment (D4).
7. Tras fusionar, prueba a mano: *Run workflow* de «Publish feed v2» dos veces seguidas → el segundo debe terminar en segundos con «Feed v2 fresco» y un evento `fresh-skip` (Log) en
   Healthchecks; después uno con `force` = true → debe trabajar. «Personal NAV» con el SQL nuevo y el NAV ya al día → debe salir en `watch` con «NAV al día».
8. Fase en sombra: medir latencia de `workflow_dispatch`, desfase de arranque entre runs (S5), duplicados por caché de Pages y peticiones reales a Yahoo/iShares; con eso,
   revisar el umbral de ~14 h del cliente y, si hace falta, hot = 0.65.

## 8. Pendiente o sin probar

* Nada se ha ejecutado en GitHub Actions real ni contra Pages, Supabase, Yahoo, iShares o Healthchecks reales (todo simulado en local; ninguna prueba sale a la red).
* `actionlint` no ejecutado (no disponible en este entorno).
* Python 3.14 (CI) no probado aquí; las pruebas se han ejecutado con 3.13.
* El SQL de `prev_nav_date_iso` (D1) y el Environment para los secretos (paso 4) quedan para Alex.
