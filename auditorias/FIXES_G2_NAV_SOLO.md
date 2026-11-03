# G2 · Dos frecuencias para el seguimiento privado: NAV solo cada ~30 min + completo 4 veces al día — `feat/g2-nav-solo`

Rama desde `main` (`0aeb0c1`). No hay PR ni merge. No se ha lanzado ningún workflow, no se ha tocado Supabase ni datos reales de usuarios, y no se han tocado `publish-feed-v2.yml`, el feed público ni su formato
(`git diff main -- .github/workflows/publish-feed-v2.yml docs/` solo toca el contrato en §5 de datos propios). No existe ningún `CHECKPOINT` en el repo (ya se dijo en `FIXES_FEED_V2_B.md`): no hay nada que actualizar ahí.

## Qué hay

| Pieza | Cambio |
|---|---|
| `scripts/personal_v2.py` | Modo `--nav-only` en `watch`, `build`, `publish` y `all`. Nuevo: `step_build_nav`, `step_publish_nav`, `build_nav_funds`, `nav_problems`, `fit_nav_funds`, `RPC_PUBLISH_NAV`. `RpcError` ahora guarda `status` y `code` (el `code` de PostgREST, solo si tiene forma de código; **nunca el cuerpo**). El modo completo no cambia. |
| `.github/workflows/personal-nav.yml` | Pasa a ser el job **solo NAV** (`personal-nav`, `timeout-minutes: 10`, `contents: read`, concurrencia propia `personal-nav`, mismo cron `23,53 5-21 * * 1-5`). Pasos de fallback (apagados) y latido opcional. |
| `.github/workflows/personal-full.yml` (nuevo) | Es el `personal-nav.yml` anterior (mismos pasos, sin tocar `personal_v2.py build|publish`) con cron `38 6,11,15,21 * * *`, concurrencia propia `personal-full`, `timeout-minutes: 15`. |
| Tests | `tests/test_personal_nav_only.py` (78 tests nuevos), `tests/fixtures_personal_full_ref.json` (referencia del modo completo, ver abajo), `tests/test_workflows.py` reescrito para los dos workflows. |
| Docs | `docs/CONTRATO_FEED_V2.md` §5 (dos modos, contrato de `publish_personal_nav`, razonamiento de concurrencia, log, latido) y README (tabla de los dos workflows, cómo lanzarlos a mano, variables/secretos). |

### Flujo del job NAV (cada ~30 min)
`watch --nav-only` (token, stdlib) → `work=true` solo si hay ≥ 1 fondo con ficha válida → instalar dependencias → `build --nav-only` (sin token, código de terceros: **solo** lee las fichas, una petición por ficha; el NAV, la fecha, `change_pct`,
`wk52_*` y los *facts* salen de la misma página) → `publish --nav-only` (token, stdlib): una llamada a `publish_personal_nav(p_token, p_user, p_funds)` por usuario. Se envían **siempre todos** los fondos de la lista del usuario; los ilegibles
(o con salto fuera de 0,5–2× respecto a `prev_price`, o sin URL de iShares válida) van con `source:"none"`, `price:null`. Medido: 2 fondos = 655 bytes, 20 fondos = 6.551 bytes (tope 64 KB = 65.536); si no cupiera, se quitan los `facts` antes que los fondos.

Una sola línea de log por run (la imprime `publish`, que ya conoce el resultado):
```
datos propios: NAV: 2 fondos, 2 con NAV exacto, NAV más reciente 2026-10-23 (3 d); RPC: 1 usuarios ok (actualizados 2, conservados 0, añadidos 0), 0 sin payload completo, 0 fallidos [run 2026-10-26T12:00Z]
```
`build` solo añade `::warning::` con contadores si algún fondo queda sin NAV exacto o se descartó algo. Nunca ISIN, URL, nombres, importes, clave ni token (probado en `test_el_log_del_modo_nav_no_contiene_...`).

### Comportamiento ante cada respuesta de la RPC (salida real de una ejecución con Supabase simulado)
```
### RPC ok
datos propios: NAV: 2 fondos, 2 con NAV exacto, NAV más reciente 2026-10-23 (3 d); RPC: 1 usuarios ok (actualizados 2, conservados 0, añadidos 0), 0 sin payload completo, 0 fallidos [run 2026-10-26T12:00Z]
-> exit 0
### usuario sin payload completo   ({"ok":false,"reason":"no_payload"})
datos propios: NAV: 2 fondos, ...; RPC: 0 usuarios ok (...), 1 sin payload completo, 0 fallidos [...]
-> exit 0
### RPC inexistente (SQL de G1 sin aplicar), sin fallback   (HTTP 404 / PGRST202)
::error::datos propios: RPC publish_personal_nav no existe todavía (aplica la migración SQL en Supabase; HTTP/código: 404/PGRST202). No se ha publicado nada; el job completo sigue publicando por su cuenta.
-> exit 1
### RPC inexistente con PERSONAL_NAV_FALLBACK_FULL=1
::warning::datos propios: RPC publish_personal_nav no existe todavía; PERSONAL_NAV_FALLBACK_FULL=1 => este run publica el payload COMPLETO como alternativa
-> exit 0; GITHUB_OUTPUT: fallback_full=true      (los pasos «Fallback - build/publish full…» del workflow se ejecutan solo entonces)
### token rechazado   (HTTP 403, code 42501)
::error::datos propios: publish_personal_nav rechazó el token (42501 forbidden / HTTP 403). No se ha publicado nada más.
-> exit 1
```
* «RPC inexistente» = HTTP 404, o `code` `PGRST202`/`42883`. «Token rechazado» = HTTP 401/403 o `code` `42501`. En ambos se corta el run (no se insiste con el resto de usuarios).
* Un fallo transitorio (red, 5xx) o una respuesta rara de un usuario es aviso y se sigue con el siguiente (salida 0, igual que el modo completo). Una respuesta `ok:true` se acepta solo si `updated/kept/added` son enteros ≥ 0.
* **Fallback**: solo con la variable de repo `PERSONAL_NAV_FALLBACK_FULL` igual a `1` *exactamente* (`0`, `true`, `yes`, vacío… no lo activan) **y** solo si la RPC no existe (no por 42501 ni por 5xx). El script de NAV nunca publica el completo él mismo: escribe `fallback_full=true` y
  el workflow ejecuta después el `build`/`publish` completos (con la misma separación token / código de terceros). No hay `inputs` en `workflow_dispatch`, así que no se activa a mano por accidente.

### Concurrencia y no solape (razonamiento)
* Dos runs del **mismo** job nunca se pisan: grupos `personal-nav` y `personal-full`, `cancel-in-progress: false`.
* Entre jobs **no** hay grupo común, a propósito: GitHub guarda un único run pendiente por grupo, y con un grupo compartido un run de NAV pendiente descartaría al completo (4 al día, y es el que crea los payloads).
* Daño posible de un cruce: el completo lee la ficha al principio de su run y publica ~2 min después; si un NAV nuevo se publicase justo en medio, el completo lo sobrescribiría con el que leyó antes. Se corrige solo en el siguiente run de NAV (≤ 30 min; la RPC no sustituye un NAV por otro con `nav_date_iso` más
  antigua, pero este caso es el inverso, por eso se evita por horario). Mitigación: el completo corre en el minuto **:38**, a 15 min de cada run de NAV (:23, :53), y no coincide con `update-data` (:17) ni `publish-feed-v2` (:47).
* Horas del completo: 06:38, 11:38, 15:38, 21:38 UTC, **todos los días** (L-V no bastaría para que un usuario nuevo tenga payload en ≤ 6 h un fin de semana). Mismas horas «base» que el feed público v2 (06/11/15/21) con distinto minuto; el 21:38 recoge el cierre de EE. UU. en verano e invierno.

## Puntos abiertos / desviaciones del enunciado (léelos)

1. **`wk52` vs `wk52_low`/`wk52_high`.** El contrato fijo cita una clave `wk52`, pero el payload completo actual, el scraper (`parse_ishares_nav`), el validador y el dashboard usan **dos** claves: `wk52_low` y `wk52_high`. No existe `wk52` en ningún sitio del repo.
   He enviado `wk52_low` y `wk52_high` (si la RPC fusiona los fondos en el payload completo, son las únicas claves coherentes). **Si el SQL de G1 valida con una lista blanca estricta que contiene `wk52` y no esas dos, rechazará todos los envíos.** Compruébalo con G1 antes
   de mergear; el cambio, si hiciera falta, es una sola constante (`NAV_FUND_KEYS` y la clave en `FUND_SPEC`) más el test `test_nav_fund_keys_son_exactamente_...`. No he cambiado la firma de la RPC.
2. **Latido: secreto, no variable.** Lo pediste como variable de repositorio `HEALTHCHECK_PERSONAL_NAV_URL`. Las variables de Actions salen **en claro** en el log (cabecera `env:` del paso) y en un repo público ese log es público, con lo que cualquiera podría pinguear tu check.
   Lo he implementado como **secreto** con ese mismo nombre (vacío o inexistente = no hace nada; solo URL `https://` sin espacios; si no, aviso sin mostrar el valor). Si prefieres variable, se cambia en una línea, pero no lo recomiendo.
3. **Tests existentes modificados** (no por cambiar su intención): `test_extra_D::test_D44` (añadido el nuevo secreto a la lista esperada), `test_workflows.py` (los tests de `personal-nav.yml` ahora cubren los dos workflows) y `test_nav_fresco_F::test_readme_documenta_...`
   (exigía la frase «único escritor», que ya no es cierta: hay dos escritores con distinta RPC; ahora exige `personal-full.yml`).
4. **Entorno**: `actionlint` no está en este contenedor (el CI lo ejecuta si existe); solo he validado el YAML con PyYAML y los tests de estructura. Los workflows no se han ejecutado (no hay red/secretos aquí).

## Pruebas
Comandos y salida reales de esta sesión (Python 3.13, dependencias de `requirements*.txt` con hashes):
```
$ ruff check --select F scripts tests
All checks passed!
$ python -m py_compile scripts/*.py tests/*.py                    # sin salida
$ python -m pytest -q
1356 passed, 1 warning in 147.78s (0:02:27)             # en main antes del cambio: 1270 passed
```
`tests/test_personal_nav_only.py` (78) cubre, con un servidor HTTP local que simula Supabase y fuentes inyectadas (ninguna red real):
payload con solo las claves permitidas y nunca `series` ni `publish_personal_feed`/Yahoo · firma exacta `{p_token,p_user,p_funds}` y solo cabecera `apikey` · fondo sin NAV legible / URL no permitida / salto de NAV → `source:"none"` enviado igualmente ·
`no_payload` · 42501 (403) con cuerpo hostil que no sale en el log · RPC inexistente (404+PGRST202, 404 sin cuerpo, 42883) con mensaje claro, sin fallback y sin llamar a `publish_personal_feed` · fallback solo con `1` exacto y solo para RPC inexistente (6 valores falsos, 3 causas distintas) ·
5xx transitorio · 9 respuestas raras de la RPC · 20 fondos ≤ 64 KB (con *facts* enormes se quitan los *facts*, no los fondos) · una petición por ficha, cero a Yahoo · sin ISIN/URL/nombre/clave/token/importes en los logs (5 escenarios) · `work` por fichas, no por símbolos ·
22 casos negativos del validador (`series`, `url`, `prev_price`, `wk52`, precio 0/negativo/NaN/inf/texto/bool, 21 fondos, `<script>`, `source` inválido…) · fichero `nav_payloads.json` manipulado no se envía · latido (vacío, ping correcto, 7 URL raras sin ejecutar nada) ·
configuración ausente/clave secreta como en el modo completo.

**El job completo publica exactamente lo mismo que antes.** `tests/fixtures_personal_full_ref.json` se generó ejecutando el `scripts/personal_v2.py` de **`main` (antes del cambio)** con un escenario fijo (2 usuarios, proxy con 5 años, extra, fondo sin ficha, `prev_price`);
el test `test_el_job_completo_publica_exactamente_lo_mismo_que_antes_de_g2` compara byte a byte (como JSON) los dos cuerpos enviados a `publish_personal_feed` con esa referencia, y comprobé además que la salida del código nuevo es idéntica (`cmp`) a la del antiguo.
Control negativo: con otro NAV el payload difiere de la referencia (`test_control_negativo_...`). Controles de mutación que hice a mano sobre `personal_v2.py` (cada uno hace fallar tests y luego se revirtió): fallback siempre activo, 404 no tratado como RPC inexistente, `no_payload` contado como fallo.
(La barrera explícita de claves de `nav_problems` es redundante con el esquema estricto `FUND_SPEC`: es defensa en profundidad, por eso quitarla sola no rompe ningún test.)

## Pasos manuales para Alex (en este orden)
1. **Aplicar el SQL de G1** en Supabase (crea `publish_personal_nav`). Antes: confirmar con G1 la cuestión de `wk52` (punto 1).
2. **Mergear esta rama** (puede ir antes del paso 1 sin romper nada: hasta entonces el job NAV falla con «RPC publish_personal_nav no existe todavía» sin publicar, y el completo sigue solo; las alertas rojas cada 30 min son esperadas en ese intervalo).
   Mientras `FEED_V2_ENABLED` sea `true`, el cron empieza solo; el completo ya no corre cada 30 min sino 4 al día, así que **hasta el paso 1 el NAV del dashboard se actualiza solo 4 veces al día**. Si no quieres ese bache, activa temporalmente `PERSONAL_NAV_FALLBACK_FULL = 1` (y quítala tras el paso 1).
3. **Lanzar a mano** (*Actions → Run workflow*, rama `main`):
   a. **Personal full** → comprobar en el log `datos propios: N payloads publicados, 0 fallidos` (así todo usuario tiene payload completo).
   b. **Personal NAV** → comprobar la línea `datos propios: NAV: ... RPC: N usuarios ok (...), 0 sin payload completo, 0 fallidos`. Si sale «sin payload completo» > 0, repetir (a).
4. Opcional: crear el secreto `HEALTHCHECK_PERSONAL_NAV_URL` (URL `https://` de un check con periodo ≥ 1 h y margen) y, si se quita el fallback, borrar la variable `PERSONAL_NAV_FALLBACK_FULL`.
5. Comprobar en el dashboard que la fecha/valor del NAV coincide con la ficha de iShares tras un run de NAV.

## Riesgos
* Contrato SQL: cualquier diferencia con lo que implementó G1 (claves permitidas —`wk52`—, forma de la respuesta, código de error para token inválido distinto de 42501/401/403) hace fallar el job NAV con un error claro, sin publicar basura. No lo he podido probar contra el SQL real (no está en este repo).
* Cruce NAV/completo (descrito arriba): ventana de ~2 min 4 veces al día, autocorregida en ≤ 30 min.
* Si la RPC nueva devolviera `ok:true` pero no fusionara bien (bug del SQL), el dashboard mostraría un NAV estancado hasta el siguiente completo: el log solo da contadores, así que compara siempre la fecha de NAV del log con la de la ficha.
* Dos escritores sobre el mismo payload: si en el futuro `publish_personal_feed` dejase de sustituir el payload entero, habría que revisar el razonamiento de concurrencia.
* iShares: se mantiene una petición por ficha y run (34 al día por fondo, igual que antes). Sin cambios de User-Agent ni variantes.

## Cómo volver atrás
* Rápido, sin tocar código: variable de repo `FEED_V2_ENABLED` ≠ `true` (para los crons de ambos; el feed público v2 también depende de ella, ojo) o desactivar solo «Personal NAV» en *Actions → Disable workflow* (el completo sigue 4 al día).
* Volver a la cadencia anterior (completo cada 30 min): `git revert` del merge de esta rama; restaura `personal-nav.yml` con el cron `23,53 5-21 * * 1-5` y el payload completo, y elimina `personal-full.yml`. `personal_v2.py` no pierde nada (el modo completo es idéntico) y la RPC nueva queda sin uso (inocua).
* Sin migración de datos: este cambio no escribe nada en el repo ni cambia el formato del payload; Supabase solo recibe otra RPC.
