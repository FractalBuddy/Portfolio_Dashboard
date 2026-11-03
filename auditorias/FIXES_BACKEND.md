# Arreglos del backend (auditoría 08)

Rama `fix/backend`, creada desde `main` (`2faeeaa`). Un commit por hallazgo. Fuente: `auditorias/08_backend.md` (rama `audit/08-backend`).

**Resultado:** `pytest` completo 395 passed (332 antes), `ruff check --select F scripts tests` limpio (antes 3 errores). Todo con datos sintéticos.
Los tests nuevos se probaron contra el código de `main`: 52 fallan sin los arreglos y pasan con ellos.

## Aplicado

| Hallazgo | Commit | Qué se hizo |
|---|---|---|
| BE-02 | `3effcb8` | Quitados `import re`, `import types` y la variable `facts` sin uso en `tests/test_fund_facts.py`. `ci.yml` también se dispara en `push` a `main`. Test que lo exige. |
| BE-01 | `0b2f953` | `yf_history` toma la divisa de `history_metadata` (ya cargada por `history()`: sin petición extra). `fetch_symbol` publica `currency` tal cual (`GBp` sigue siendo `GBp`) y `price_unit_divisor` (100 para `GBp`, `GBX`, `ZAc`, `ILA`; 1 en el resto). Si Yahoo no da una divisa válida, no se publica nada. |
| BE-13 | `ee7c3aa` | `change_pct` y `change_pct_7d` pasan a `null` si el cierre de referencia es ≤ 0 (antes, WTI 2020: −148,65 %). |
| BE-10 | `e52eeca` | Los dos tests con fecha fija (fallaban desde 2026-11-08 y 2026-12-01) derivan sus fechas de «hoy». Barrido con `time-machine` de +0 a +800 días: 327 passed en todos. |
| BE-05 | `8fca3ad` | Validador: por barra, `close` finito y > 0, fecha ISO legible, ≥ 1900, ≤ hoy+1 y estrictamente creciente. Regresión: salto de nivel > ×5 (arriba o abajo) del precio o del último cierre por símbolo, y del NAV de `your_funds`, frente al feed anterior. |
| BE-06 | `95baca4` | Workflow: la descarga de `prev.json` distingue 200 / 404 / resto, usa `--retry-all-errors` y ya no esconde el error con `\|\| echo '{}'`. Con fallo (≠ 200/404) se marca `EXPECT_PREV=true`; el validador **bloquea**, o con `allow_regression` publica pero con `Alert` (llega a Healthchecks). 404 = primer run: solo aviso, como antes. |
| BE-01 (validador) | `2cfe0fd` | Si una fila trae `currency` o `price_unit_divisor`, deben venir los dos y ser coherentes. |
| BE-11 | `1847ee4` | `verify-cdn` descarga también `data.json` (`--compressed`) y compara su tamaño con `data_bytes` del `meta.json`. Sigue siendo informativo (no falla el run). |

Todos los cambios son **aditivos**: no se renombra ni quita ningún campo del contrato con el dashboard. Los únicos campos nuevos son `currency` y `price_unit_divisor`.

## No aplicado

- **BE-03, BE-04, BE-09**: por tu instrucción, requieren decisiones tuyas.
- **BE-07, BE-08, BE-12, BE-14 a BE-20**: fuera del alcance que pediste. En particular el test R6 del apéndice A (lista blanca de campos) es de BE-07 y no se incluyó.
- **BE-02 (c)**, ruleset en `main` que exija el check `CI/checks`: es configuración del repositorio en GitHub, no código.
- **BE-11, parte cliente** (validar `data.generated_at === meta.generated_at` en el dashboard): está en el otro repositorio/código.

## Decisiones que conviene que revises

1. **`cap_currency` no cambia.** Sigue normalizado a mayúsculas (`GBP`), porque alimenta la consulta de tipos de cambio. El `.upper()` ya no afecta a `currency`. No he podido comprobar sin red si el `marketCap` de Yahoo para valores `.L` viene en libras o en peniques, así que no lo toco.
2. **Filas sin `currency`:** `your_funds` (NAV de iShares) y cripto (CoinGecko) no llevan `currency`: el NAV se etiqueta EUR en el dashboard pero no lo he podido verificar. Además, el validador **no exige** la presencia de `currency`: las filas arrastradas del feed anterior no la tienen y bloquearían el primer run tras desplegar. Cuando un feed completo la lleve, se puede exigir.
3. **Un split real dispara el salto de nivel.** Un 10:1 (tipo NVDA 2024) divide el precio por 10 y el validador lo bloqueará: es una regla de regresión, se desbloquea con `workflow_dispatch` + `allow_regression`. Es inevitable: un split y un cambio de unidad son indistinguibles con estos datos.
4. **Umbral ×5, no 5 %.** La auditoría propuso ±5 % para el NAV y 0,5–2 para precios; usé ×5 para ambos, que cubre ×10/×100 como pediste y evita falsos positivos tras un hueco de varios días entre runs. Si prefieres algo más estricto, es la constante `MAX_PRICE_JUMP`. No implementé la mediana de `close_new/close_prev` en fechas solapadas: un split la dispararía igual y no añade detección sobre el último cierre.
5. **`close ≤ 0` en materias primas se tolera** (solo se exige finito): el WTI cerró en −37,63 el 2020-04-20 y bloquearía la publicación mientras esa barra esté en el histórico. Nulo/no numérico sí bloquea en todas las listas.
6. **Fechas repetidas o desordenadas:** hasta 2 símbolos solo avisan; más de 2 bloquean (mismo criterio de tolerancia que `price/variación inverosímil`). Yahoo a veces repite la barra del día durante la sesión; sin red no he podido medir cuánto.
7. **BE-06 es fail-closed.** Si `raw.githubusercontent.com` falla de verdad, el run no publica (job rojo + `/fail`) hasta que se arregle o se lance a mano con `allow_regression`. La alternativa (solo avisar) mantiene la disponibilidad pero publica sin guardas de regresión justo cuando Yahoo va peor.
8. **`change_pct` nulo:** el dashboard debe aceptar `null` en `change_pct`/`change_pct_7d`. Ya recibía `null` en otros campos (`volume_ratio`, `change_pct` de fondos), pero esos dos no solían serlo.

## No verificado

- Sin red, no he pasado el validador nuevo por el feed real (`origin/data`). **Antes de fusionar, conviene correrlo a mano** (`git fetch origin data`, sacar `data.json`/`meta.json` y ejecutar `scripts/sanity_check.py --data … --meta … --prev …`) para descartar falsos positivos con el histórico real (fechas repetidas, ceros).
- Solo Python 3.13 en local (la CI usa 3.14); `actionlint` no estaba instalado, así que los YAML modificados no pasaron por él (sí por los tests de `test_workflows.py`).

## Hallazgo extra (no corregido, fuera de alcance)

**Los tests de la suite intentan contactar con `eth-api.lido.fi`.** Varios tests de `main()` (p. ej. `test_D24_main_e2e_…`, `test_main_publica_health`) no parchean `fetch_lido_apr`, así que con red hacen una petición real. Pasa también en `main`. Lo detecté porque el proxy del entorno rechazó 8 conexiones durante un `pytest`; no salió ningún dato. Desde entonces ejecuté la suite con un bloqueo de `requests` fuera del repo. Lido no estaba en tu lista de prohibidos, pero va contra «solo datos sintéticos»: conviene parchearlo en `_patch_main` y en el test D24. Con ese bloqueo (que cubre `requests`: iShares, CoinGecko, FRED, CNN, Lido) solo se registraron intentos a Lido y a servidores locales de los tests. Yahoo va por `yfinance`/`curl_cffi`, que mi bloqueo no cubre; los tests lo sustituyen (`yf_history`/`Ticker` parcheados) y el proxy no reportó rechazos a Yahoo, pero no lo he verificado con un bloqueo propio.

## Rama

El entorno indicaba desarrollar en `claude/elegant-einstein-n95f6h`; empujé a `fix/backend` porque lo pediste expresamente.
