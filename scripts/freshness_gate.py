#!/usr/bin/env python3
"""
Puerta de frescura de publish-feed-v2.yml (AU01-F1, pieza 1): evita trabajo duplicado cuando dos relojes lanzan el mismo workflow (el disparador externo
por `workflow_dispatch` y el cron de GitHub de respaldo).

    python3 scripts/freshness_gate.py --base-url https://usuario.github.io/repo/v2 --event "$EVENT" --force "$FORCE" [--max-age-min 40] [--github-output]

Lee el `manifest.json` del feed v2 YA publicado (solo `generated_at`) y escribe `skip=true|false` en $GITHUB_OUTPUT:
  * skip=true  -> el feed se generó hace MENOS de `--max-age-min` minutos (por defecto 40) y el run no es manual con force=true: el workflow termina con
                  éxito sin hacer nada (los jobs siguientes se saltan).
  * skip=false -> todo lo demás. FALLO CERRADO hacia «hacer el trabajo»: manifest ausente (404), ilegible, no JSON, sin `generated_at`, `generated_at`
                  malformado o en el futuro (reloj adelantado), parámetro inválido o cualquier error inesperado => se trabaja.

Solo biblioteca estándar (reutiliza la descarga de scripts/site_download.py: https, tamaño acotado, parámetro anti-caché). No recibe ni usa secretos. Sale
siempre con 0: la puerta nunca hace fallar el workflow, solo decide si se trabaja.
"""
import argparse
import json
import math
import os
import sys
from datetime import datetime, timezone

import site_download as sd

DEFAULT_MAX_AGE_MIN = 40
MANIFEST_LIMIT = 4_000_000          # el manifest real ronda 285 KB (tope del contrato: 600 KB); margen amplio, pero acotado
STAMP_FMT = "%Y-%m-%dT%H:%M:%SZ"    # el formato exacto con el que feed_v2.py escribe generated_at (iso_z)


def parse_stamp(value):
    """datetime UTC de un `generated_at` con el formato exacto del feed, o None si no lo tiene (malformado)."""
    if not isinstance(value, str) or len(value) != 20:
        return None
    try:
        return datetime.strptime(value, STAMP_FMT).replace(tzinfo=timezone.utc)
    except ValueError:
        return None


def decide(now, generated_at, max_age_min, force):
    """Función PURA. Devuelve (skip, motivo). skip=True solo si el feed publicado es fresco y no se fuerza el run. Cualquier duda => (False, motivo)."""
    if force:
        return False, "force=true: se trabaja aunque el feed sea fresco"
    if isinstance(max_age_min, bool) or not isinstance(max_age_min, (int, float)) or not math.isfinite(max_age_min) or max_age_min <= 0:
        return False, "umbral inválido: se trabaja"
    ga = parse_stamp(generated_at)
    if ga is None:
        return False, "manifest sin generated_at válido: se trabaja"
    age_min = (now - ga).total_seconds() / 60.0
    if age_min < 0:
        return False, "generated_at en el futuro (reloj adelantado): se trabaja"
    if age_min < max_age_min:
        return True, "feed fresco (generado hace %.0f min < %g min): no hay trabajo" % (age_min, max_age_min)
    return False, "feed de hace %.0f min (>= %g min): se trabaja" % (age_min, max_age_min)


def read_generated_at(base_url, fetch=None):
    """`generated_at` del manifest publicado, o (None, motivo) si no se puede leer. Nunca lanza."""
    fetch = fetch or (lambda base, rel: sd.http_get(base, rel, MANIFEST_LIMIT))
    try:
        base = sd._check_base(base_url)
        raw = fetch(base, "manifest.json")
    except sd.NotFound:
        return None, "manifest.json no existe (404)"
    except Exception as e:                                          # noqa: BLE001 - fallo cerrado: cualquier error => se trabaja
        return None, "manifest.json ilegible (%s)" % type(e).__name__

    def no_const(name):
        raise ValueError(name)
    try:
        doc = json.loads(raw.decode("utf-8"), parse_constant=no_const)
    except (ValueError, UnicodeDecodeError, AttributeError):
        return None, "manifest.json no es JSON válido"
    if not isinstance(doc, dict):
        return None, "manifest.json con formato inesperado"
    return doc.get("generated_at"), None


def force_requested(event, force):
    """force solo cuenta en un lanzamiento manual (workflow_dispatch) con el input a `true`. En un `schedule` no existe el input."""
    return event == "workflow_dispatch" and str(force).strip().lower() == "true"


def _set_output(env, name, value):
    path = env.get("GITHUB_OUTPUT")
    if path:
        with open(path, "a", encoding="utf-8") as f:
            f.write("%s=%s\n" % (name, value))


def main(argv=None, fetch=None, now=None, env=None):
    env = os.environ if env is None else env
    skip, why = False, "error inesperado: se trabaja"
    try:
        ap = argparse.ArgumentParser(description=__doc__.split("\n")[1])
        ap.add_argument("--base-url", required=True)
        ap.add_argument("--event", default="")
        ap.add_argument("--force", default="false")
        ap.add_argument("--max-age-min", default=str(DEFAULT_MAX_AGE_MIN))
        ap.add_argument("--github-output", action="store_true")
        a = ap.parse_args(argv)
        try:
            max_age = float(a.max_age_min)
        except ValueError:
            max_age = float("nan")                                  # decide() lo trata como inválido => se trabaja
        if force_requested(a.event, a.force):
            skip, why = decide(now or datetime.now(timezone.utc), None, max_age, True)
        else:
            ga, problem = read_generated_at(a.base_url, fetch)
            skip, why = decide(now or datetime.now(timezone.utc), ga, max_age, False)
            if problem:
                why = "%s: se trabaja" % problem
        github_output = a.github_output
    except SystemExit:                                              # argumentos inválidos: también fallo cerrado
        github_output = True
    except Exception:                                               # noqa: BLE001
        github_output = True
    print("puerta de frescura: %s" % why)
    if github_output:
        _set_output(env, "skip", "true" if skip else "false")
    if skip:
        print("::notice::Feed v2 fresco: este run termina sin trabajo (lanza con force=true para forzarlo)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
