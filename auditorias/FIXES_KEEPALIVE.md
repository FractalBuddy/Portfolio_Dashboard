# FIXES_KEEPALIVE — keepalive del proyecto gratuito de Supabase

Rama: `feat/supabase-keepalive` (desde `main`).

## Qué se ha hecho
* **`.github/workflows/supabase-keepalive.yml`** (nuevo): cron `37 3 */3 * *`, `workflow_dispatch`, `permissions: contents: read`,
  `concurrency`, `timeout-minutes: 5`, `ubuntu-24.04`. No usa ninguna acción de terceros (solo `curl`), así que no hay nada que fijar por SHA;
  el test genérico existente de SHA/`${{` en `run:` lo cubre si se añade alguna.
  * UNA lectura: `GET {SUPABASE_URL}/rest/v1/profiles?select=user_id&limit=1` con cabecera `apikey`.
  * Correctos: 200, 401, 403. Fallo: 000 (red), 5xx, 404, 540… cualquier otro código. 3 intentos (esperas 15 s y 30 s, ninguna tras el último).
  * Aborta SIN enviar la clave si empieza por `sb_secret_`, contiene `service_role`, o es un JWT cuyo payload tiene `role: service_role`
    (esto último es extra: una clave legacy service_role no contiene el texto «service_role», solo su payload en base64). También si falta alguna variable o la URL no es `https://`.
  * Healthchecks opcional (`HC_SUPABASE_URL`): ping en éxito, `/fail` en fallo; vacío → solo `::warning::`.
  * Logs: solo códigos HTTP; nunca URL, clave, secreto de HC ni cuerpo de respuesta.
* **Tests** (`tests/test_workflows.py`, 22 nuevos que fallan sin el workflow): estructura, códigos buenos/malos, reintentos y esperas,
  recuperación, claves secretas (sin petición ni eco de la clave), JWT anon aceptado, variables ausentes, URL sin https, Healthchecks ok/fail/vacío, ausencia de datos en logs.
  Ejecutan el bloque `run:` real en bash con `curl`/`sleep` falsos: no se contacta con ningún servicio real.
* **README.md**: sección del nuevo workflow con variables a crear y cómo comprobarlo.
* `tests/test_extra_D.py::test_D44`: el conjunto esperado de secretos incluye ahora `HC_SUPABASE_URL`.
* `tests/test_fund_facts.py`: eliminados 3 avisos F401/F841 que ya estaban en `main` y impedían que `ruff --select F` estuviera en verde.

## Resultado
`pytest` completo: 355 passed. `ruff check --select F .`: All checks passed. (`actionlint` no está instalado aquí: sin validar con él.)

## Discrepancia (resuelta tras el rebase)
Pedías corregir en el README el horario de Healthchecks a `17 6,11,15,21 * * *`. Pero `update-data.yml` tiene `17 6,11,15,20 * * *`
y el README ya decía `…,20`: coinciden. Cambiarlo a 21 los haría discrepar. Lo he dejado en 20 y añadido un test que exige que ambos coincidan.
Si has cambiado (o quieres cambiar) el cron real a las 21:17, hay que cambiar a la vez `update-data.yml` y el README; el test avisará si solo se toca uno.

## Limitaciones a tener en cuenta
* Un **401** puede responderlo el gateway sin llegar a Postgres (p. ej. clave errónea o revocada), así que el workflow lo da por bueno aunque
  esa petición no cuente como actividad de base de datos. Un **403/42501** sí pasa por Postgres. Si te importa, usa una clave válida y una tabla `profiles` existente con RLS (devuelve 200 con `[]` o 403).
* Un 404 se trata como fallo (puede ser proyecto pausado o que `profiles` no exista): es deliberado, avisa en vez de callar.
* No he podido comprobar contra un Supabase real (sin acceso ni credenciales, como se pedía). El código HTTP exacto de un proyecto pausado no está verificado: el criterio es «todo lo que no sea 200/401/403 falla».
* Al correr la suite completa, tests ya existentes de `fetch_market_data` intentan conectar con `eth-api.lido.fi` (el proxy lo rechaza; pasan igualmente). No es de este cambio.

**Actualización tras rebasar sobre `main`:** `update-data.yml` ya trae `17 6,11,15,21 * * *`, así que el README se ha corregido a `…,21` y el test de coincidencia pasa. La nota anterior describe el estado previo al rebase.
