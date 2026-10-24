# Arreglos del backend, segunda tanda (auditoría 08)

Rama `fix/backend-2`, creada desde `main` (`2d14c67`, ya con el merge de `fix/backend`). Un commit por hallazgo. Fuente: `auditorias/08_backend.md` (rama `audit/08-backend`) y `auditorias/FIXES_BACKEND.md`.

**Resultado:** `pytest` completo **477 passed** (395 al empezar; 332 en la auditoría), `ruff check --select F scripts tests` limpio. Todo con datos sintéticos.
Barrido temporal con `time-machine` (+40 y +400 días, sin `test_workflows.py`): 436 passed en ambos.
El validador nuevo pasa sobre el feed real de `origin/data` (`generated_at` 2026-10-24T21:04Z, 10,3 MB) con `rc=0`: `OK: 10 índices, 30 acciones`, también con ese mismo feed como `--prev`.

**No se contactó con Yahoo, iShares, CoinGecko, FRED, CNN ni Lido, ni con GitHub** (salvo `git fetch` de las ramas de este mismo repo). Para instalar herramientas y generar los lockfiles sí se contactó con PyPI, igual que en la auditoría.

## Aplicado

| Hallazgo | Commit | Qué se hizo |
|---|---|---|
| Hallazgo extra (red en tests) | `4156ab0` | `STAKING_PROVIDERS` guarda la referencia a `fetch_lido_apr` al importar, así que parchear solo el atributo del módulo no valía: `_patch_main` ahora parchea también la entrada del diccionario. `conftest.py` añade un fixture *autouse* que bloquea cualquier destino que no sea el bucle local (resolución DNS y `connect` para `requests`/`urllib`/`feedparser`, y `curl_cffi.Session.request` para `yfinance`), **quita los proxies del entorno** (si no, el destino real queda oculto tras `127.0.0.1`: mi primer intento dejaba pasar a Lido por eso) y hace fallar el test si intentó salir aunque el código de producción se haya tragado la excepción. Sin el parche: 7 errores (todos Lido); con él, 0. |
| BE-07 | `d121462` | (1) `requirements.in` (rangos) + `requirements.txt` generado con hashes (`uv pip compile --generate-hashes --universal --python-version 3.14`, 26 paquetes). El job `fetch` instala con `pip install --require-hashes --no-deps`. (2) `sanity_check.py`: lista blanca de claves por sección (raíz, filas de cada lista, barras, intradía, `facts`, `analytics`, `market_mood`, `news`, `health`…), regex de símbolo/ISIN/enlace, tope de longitud por campo (200; `link` 1000, `title` 300) y ningún string con marcado (ni tras des-escapar entidades) ni caracteres de control. Bloquea la publicación. Test R6 del apéndice A incluido. |
| BE-08 | `c764579` | 429 de Yahoo (`YFRateLimitError` o por texto): pausa de 30-60 s con jitter, recortada al presupuesto que quede. 3 seguidos (sin respuesta buena entre medias) o 6 en total abandonan Yahoo para el resto del run (`YahooStop`; `fetch_symbol` devuelve «sin datos» y se arrastra la fila anterior). Límite de ráfaga: 0,15-0,35 s entre peticiones y 5 s cada 40. También en `fetch_stock_profile`. |
| BE-12 | `19f3a8a` | Tope duro global de 18 min (`HARD_BUDGET_SECONDS`) que consultan iShares (NAV y ficha), FRED, CoinGecko, Lido, alternative.me y CNN; timeouts y esperas recortados al presupuesto restante. `health.budget_exhausted` (aditivo) y `Alert` en el validador si es `true`. |
| BE-14 | `08e72d2` | `_plain_text`: des-escapa entidades (hasta 3 pasadas), quita etiquetas, descarta `<` `>` sueltos y control, recorta. En `title`, `source` y `published`. |
| BE-15 | `0f2369f` | VIX, Fear & Greed de bolsa (y componentes) y Fear & Greed cripto que fallan hoy se arrastran del feed anterior como `stale` (no más de 3 días). Si la fuente trae fecha (alternative.me, Lido, CNN) y es de hace más de 3 días, se publica `stale`. De CNN se publica además `as_of`. El validador avisa, con `Alert` si llevan 2+ días así. |
| BE-16 | `4e30fa8` | `pytest`, `ruff` y `PyYAML` pasan de estar inline en `ci.yml` a `requirements-dev.in/.txt` con hashes, instalados con `--require-hashes`. Sin PyYAML, en CI (`CI=true`) los tests de workflows **fallan** en vez de saltarse (antes: «1 skipped», código 0). |
| BE-17 | `9d8df8e` | Una sola descarga por ficha de iShares y run (NAV y «Key Facts» salían de la misma URL), con la ficha heredando el reintento ante fallos transitorios. El ISIN debe aparecer como token entero. Aviso en el log si la página trae varios «NAV as of» distintos. |
| BE-20 | `b27b7ec` | `intraday_tz` (zona IANA) en cada fila con intradía; cron de la tarde 20:17 → 21:17 UTC; comentario de `concurrency` corregido (no es una cola). |

Todos los cambios son **aditivos**: no se quita ni renombra ningún campo del contrato. Campos nuevos: `intraday_tz` (por fila), `health.budget_exhausted`, `as_of` en los componentes de CNN y `stale`/`stale_since` en `market_mood`/`staking` (estos dos ya existían en otras secciones).

Tests nuevos que fallan **antes** del arreglo (comprobado restaurando el código anterior de `scripts/` o del workflow):

| Hallazgo | Fallan sin el arreglo |
|---|---|
| Red en tests | 7 errores de teardown (Lido) |
| BE-07 | 13 de validador (incl. R6) + 2 de lock/workflow; el de CI con requirements separados lo escribí junto con el cambio de `ci.yml` y no lo comprobé aparte |
| BE-08 | 8 de 8 |
| BE-12 | 5 de 5 (los 4 de `test_backend_fixes2.py` por no existir el presupuesto; el del validador por comportamiento) |
| BE-14 | 5 de 7 (los otros 2 fijan lo que no debe cambiar) |
| BE-15 | 15 de 18 (los otros 3 fijan lo que no debe cambiar) |
| BE-16 | el de `requirements-dev` falla contra el `ci.yml` anterior; los de PyYAML ausente se comprobaron a mano con el módulo anterior («1 skipped», rc 0) |
| BE-17 | 7 de 12 |
| BE-20 | 8 de 11 |

## No aplicado

- **BE-03, BE-04, BE-09**: por tu instrucción.
- **BE-19: no contacté con GitHub.** Queda el comando para verificarlo tú (ver abajo).
- **BE-10, BE-11, BE-13, BE-01, BE-02, BE-05, BE-06**: ya estaban en la tanda anterior. **BE-18** (keepalive) quedó fuera de tu lista.
- **BE-07 (3), `textContent` en el dashboard**, y **BE-11 parte cliente**: están en el otro código/repositorio.
- **BE-08 (2) histórico incremental, (3) `yf.download` por lotes y (4) separar jobs**: son rediseño, no pedidos. Solo apliqué (1) y el límite de ráfaga.
- **BE-16, `actionlint`**: sigue opcional. Hacerlo obligatorio exige descargar un binario de terceros en la CI (rompería el criterio de «no se descarga nada»); `test_workflows.py` ya cubre la estructura y los `run:` críticos. Si quieres cerrarlo, decide cómo fijarlo (SHA256 del binario).
- **BE-17, rango de NAV `0.1 < x < 10000` y UA/ToS de iShares**: la continuidad ya la valida el sanity-check (BE-05); lo de ToS no es código.
- **BE-20, regla `concurrency`**: solo se corrigió el comentario; el comportamiento de GitHub (un solo pendiente por grupo) no se puede cambiar desde el YAML.

## Cambios en tests existentes (para que los revises)

1. `test_D06_yf_history_reintenta_se_rinde_y_respeta_presupuesto` usaba el texto `"429"` como fallo genérico; ahora tiene tratamiento propio, así que usa «conexión rota».
2. `test_fetch_ishares_nav_con_html_sintetico` pedía dos veces la misma URL y esperaba otro resultado; con la caché de BE-17 la segunda usa otra URL.
3. Dos tests de `test_sanity_check.py` rellenaban el tamaño con una clave `padding` en la raíz; el esquema la rechaza (justo lo que debe), así que rellenan con un `sparkline` largo.

## Decisiones que conviene que revises

1. **El validador es estricto.** Cualquier clave no listada bloquea la publicación: si añades un campo al backend y se te olvida la lista blanca, el run deja de publicar. Hay un test (`test_BE07_la_lista_blanca_cubre_lo_que_publica_el_backend`) que compara con `PROFILE_FIELDS` y con las fichas reales de `tests/fixtures_fund_facts.json`, pero no cubre todos los campos que podrías añadir. Es el compromiso entre seguridad y disponibilidad. Los strings con marcado (`<img…`) también bloquean: un `<` suelto («< 5000») no cuenta, y `_plain_text` (BE-14) ya los elimina en origen.
2. **El lock se generó con Python 3.14 como objetivo pero solo lo instalé con 3.13** (venv limpio: instala, `pip check` limpio, y un hash alterado se rechaza). Para 3.14/linux la resolución en `--dry-run` es correcta, pero **no descargué las ruedas cp314**, así que no he comprobado sus hashes. Si el primer run de `fetch` falla en «Install dependencies», es lo primero a mirar. `--no-deps` hace que sea todo lo que hay en el lock y nada más.
3. **Dependabot con el par `.in`/`.txt`:** no tengo forma de comprobarlo sin GitHub. Debería reconocer `requirements.in`/`requirements.txt` (formato `pip-compile`) y `requirements-dev.*`, pero no lo he visto funcionar.
4. **BE-08 es agresivo a propósito.** 3 × 429 seguidos abandonan Yahoo para ese run: el resto de símbolos se arrastra de ayer como `stale`, y si es mucho, el sanity-check bloquea (más del 30 % de una lista con precio antiguo). Prefiero eso a seguir golpeando una IP ya limitada. Constantes: `YF_RATE_LIMIT_MAX_CONSECUTIVE`, `YF_RATE_LIMIT_MAX_TOTAL`, `YF_RATE_LIMIT_PAUSE`. La ráfaga añade ~1 minuto en total (280 peticiones × ~0,25 s + una pausa de 5 s cada 40).
5. **BE-12, reparto del tiempo.** Yahoo manda hasta el minuto 14; el resto hasta el 18; quedan ~2 min de margen del `timeout-minutes: 20`. Si Yahoo gasta sus 14 min, lo que queda (iShares, FRED, CoinGecko, Lido, CNN) comparte 4 min. El orden de `main()` pone el NAV de iShares antes que FRED y CoinGecko.
6. **BE-17, sigue ganando el primer «NAV as of».** Lo decidiste en un test anterior (D14); cambiarlo a «ambiguo ⇒ proxy» sin ver una página real con varias clases sería adivinar. Ahora deja rastro en el log. Dime si prefieres lo conservador.
7. **BE-20, cron 21:17.** Es un cambio de comportamiento visible: los datos de la tarde llegan una hora más tarde en verano. Revisa que la ventana/gracia de Healthchecks siga bien (los huecos pasan de 5/9/6/4 h a 5/4/6/9 h). Si prefieres no moverlo, es una línea.
8. **BE-15, arrastre de 3 días.** Un VIX arrastrado es un VIX de ayer, no el de hoy; el dashboard debe respetar `stale` en `market_mood` igual que en las otras secciones.

## No verificado

- **Nada contra servicios reales.** Yahoo, iShares, CoinGecko, FRED, CNN y Lido solo se simularon.
- **CNN:** el formato del `timestamp` (ISO / epoch) lo parseo defensivamente, pero no lo he visto en una respuesta real; sin fecha entendible no se publica `as_of` ni se juzga la frescura.
- **`intraday_tz`:** supongo que `yfinance` devuelve el índice del intradía en la zona local del mercado con nombre IANA (lo hace así en las versiones que conozco); con un desfase fijo o un índice naive no se publica nada. No lo he visto con datos reales.
- **Primer run en Actions:** ni el lock con hashes, ni la CI con `requirements-dev.txt`, ni el cron nuevo se han ejecutado en GitHub. Conviene lanzar un `workflow_dispatch` antes de fiarse.
- Solo Python 3.13 en local (la CI usa 3.14); `actionlint` no estaba instalado.

## BE-19: comando para verificar los SHA (ejecútalo tú; hace falta red a GitHub)

Compara cada SHA fijado con el tag de su comentario **en el repositorio upstream** (`git ls-remote` del tag, desreferenciando los anotados), que es lo que descarta un *imposter commit* de un fork. No uses `gh api repos/…/commits/<sha>` para esto: GitHub resuelve también commits de forks por la URL del repo padre.

```bash
grep -hoE 'uses: [^ ]+@[0-9a-f]{40} +# +v?[0-9][^ ]*' .github/workflows/*.yml | sort -u | while read -r _ ref _ tag; do
  repo=${ref%@*}; sha=${ref#*@}
  out=$(git ls-remote "https://github.com/${repo}" "refs/tags/${tag}" "refs/tags/${tag}^{}")
  want=$(printf '%s\n' "$out" | awk '/\^\{\}$/{print $1}')
  [ -n "$want" ] || want=$(printf '%s\n' "$out" | awk 'NR==1{print $1}')
  if [ "$want" = "$sha" ]; then echo "OK    ${repo} ${tag} ${sha}"; else echo "MAL   ${repo} ${tag}: fijado ${sha}, upstream ${want:-<tag no encontrado>}"; fi
done
```

Hoy hay cuatro acciones: `actions/checkout` v7.0.1, `actions/setup-python` v7.0.0, `actions/upload-artifact` v7.0.1 y `actions/download-artifact` v8.0.1. El extractor del comando lo probé aquí sin red (con un `git` simulado: tag anotado, tag ligero, SHA distinto y tag inexistente); la comprobación real contra GitHub **no** se ha hecho. Un `MAL` con «tag no encontrado» puede ser simplemente que el comentario no coincida con el nombre real del tag.

## Rama

El entorno indicaba desarrollar en `claude/youthful-clarke-3w0fr4`; trabajé y empujé a `fix/backend-2` porque lo pediste expresamente. No hay PR abierto.
