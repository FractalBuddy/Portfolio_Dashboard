"""AU01-F1, pieza 4: el cron de GitHub pasa a ser RESPALDO (2-4 franjas al día por workflow; el reloj principal es un disparador externo por
workflow_dispatch) y los jobs con secretos solo corren sobre la rama principal. Pruebas estáticas sobre los YAML, en el estilo de test_workflows*.py, y
una evaluación de las condiciones `if:` en los escenarios normales (cron, dispatch desde main, dispatch desde otra rama, salida por frescura)."""
import json
import pathlib
import re

import pytest

yaml = pytest.importorskip("yaml")

WF = pathlib.Path(__file__).resolve().parent.parent / ".github" / "workflows"
V2, PN, PF, V1 = "publish-feed-v2.yml", "personal-nav.yml", "personal-full.yml", "update-data.yml"
MAIN = "github.ref == 'refs/heads/main'"

# Tabla de respaldo (la misma que el informe auditorias/FIXES_AU01_F1.md). Minutos «feos»: ni :00 ni :30.
EXPECTED_CRON = {
    V2: ["47 7,13,21 * * 1-5", "47 9,18 * * 0,6"],      # L-V: mañana europea, tarde, tras el cierre de EE. UU.; fin de semana: cripto y noticias
    PN: ["23 7,11,16 * * 1-5"],                         # solo laborables (la ficha no cambia en fin de semana); parada temprana en cada pasada
    PF: ["38 6,13,21 * * *"],                           # todos los días (crea el payload de un usuario nuevo también en fin de semana)
}


def load(name):
    return yaml.safe_load((WF / name).read_text(encoding="utf-8"))


def triggers(name):
    wf = load(name)
    return wf.get("on", wf.get(True))                   # PyYAML (YAML 1.1) interpreta `on` como True


def crons(name):
    return [e["cron"] for e in triggers(name).get("schedule", [])]


def _field(spec, lo, hi):
    """Valores de un campo de cron con la sintaxis que usan estos workflows (números, listas y rangos; `*`). Cualquier otra cosa (pasos `/`) => error."""
    if spec == "*":
        return set(range(lo, hi + 1))
    out = set()
    for part in spec.split(","):
        assert re.fullmatch(r"\d+(-\d+)?", part), "sintaxis de cron no soportada en el test: %s" % spec
        a, _, b = part.partition("-")
        out |= set(range(int(a), int(b or a) + 1))
    assert out <= set(range(lo, hi + 1)), spec
    return out


def slots_by_dow(cron_list):
    """{día de la semana (0=domingo..6): [(hora, minuto), ...]} de una lista de cron (dom y month deben ser `*`)."""
    out = {d: [] for d in range(7)}
    for cr in cron_list:
        minute, hour, dom, month, dow = cr.split()
        assert (dom, month) == ("*", "*"), cr
        for d in _field(dow, 0, 6):
            out[d] += [(h, m) for h in sorted(_field(hour, 0, 23)) for m in sorted(_field(minute, 0, 59))]
    return out


# ------------------------------------------------------------------ cron de respaldo
@pytest.mark.parametrize("name", sorted(EXPECTED_CRON))
def test_los_cron_de_respaldo_son_los_esperados(name):
    assert crons(name) == EXPECTED_CRON[name]


@pytest.mark.parametrize("name", sorted(EXPECTED_CRON))
def test_ninguna_franja_en_punto_ni_a_y_media(name):
    for d, slots in slots_by_dow(crons(name)).items():
        assert all(m not in (0, 30) for _, m in slots), (name, d)


@pytest.mark.parametrize("name", sorted(EXPECTED_CRON))
def test_de_2_a_4_franjas_por_dia_que_corre_cubriendo_manana_y_tarde(name):
    by = slots_by_dow(crons(name))
    days = [d for d, s in by.items() if s]
    assert days, name
    for d in days:
        hours = [h for h, _ in by[d]]
        assert 2 <= len(by[d]) <= 4, (name, d, by[d])
        assert any(h < 12 for h in hours) and any(h >= 12 for h in hours), (name, d)          # mañana y tarde (UTC)
        assert len(set(by[d])) == len(by[d])                                                   # sin franjas repetidas


def test_dias_laborables_y_fin_de_semana_segun_corresponda():
    v2, pn, pf = (slots_by_dow(crons(n)) for n in (V2, PN, PF))
    assert all(v2[d] for d in range(7))                     # el feed tiene cripto y noticias también en fin de semana
    assert all(pn[d] for d in range(1, 6)) and not pn[0] and not pn[6]          # el NAV de la ficha no cambia en fin de semana
    assert all(pf[d] for d in range(7))


def test_menos_pasadas_que_antes():
    def per_week(name):
        return sum(len(s) for s in slots_by_dow(crons(name)).values())
    assert per_week(V2) == 3 * 5 + 2 * 2 < 4 * 7
    assert per_week(PN) == 3 * 5 < 34 * 5
    assert per_week(PF) == 3 * 7 < 4 * 7


def test_las_franjas_no_coinciden_con_el_feed_v1_ni_entre_si():
    v1 = {(h, m) for s in slots_by_dow(crons(V1)).values() for h, m in s}
    seen = {}
    for name in EXPECTED_CRON:
        for d, slots in slots_by_dow(crons(name)).items():
            for h, m in slots:
                assert (h, m) not in v1, (name, h, m)
                assert seen.setdefault((d, h, m), name) == name, (name, seen[(d, h, m)], d, h, m)
    minutes = {name: {m for s in slots_by_dow(crons(name)).values() for _, m in s} for name in EXPECTED_CRON}
    assert all(len(ms) == 1 for ms in minutes.values()) and len({next(iter(ms)) for ms in minutes.values()}) == 3          # un minuto propio por workflow


def test_el_feed_v1_no_se_toca():
    assert crons(V1) == ["17 6,11,15,21 * * *"]
    assert MAIN not in (WF / V1).read_text(encoding="utf-8")


@pytest.mark.parametrize("name", [V2, PN, PF])
def test_conservan_workflow_dispatch_y_concurrency_sin_cancelar(name):
    wf = load(name)
    assert "workflow_dispatch" in triggers(name)
    assert wf["concurrency"]["cancel-in-progress"] is False and wf["concurrency"]["group"] == {V2: "publish-feed-v2", PN: "personal-nav", PF: "personal-full"}[name]


# ------------------------------------------------------------------ solo sobre main los jobs con secretos
def _jobs_with_secrets(name):
    return [jn for jn, job in load(name)["jobs"].items() if "secrets." in json.dumps(job)]


@pytest.mark.parametrize("name", [V2, PN, PF])
def test_los_jobs_con_secretos_solo_corren_en_main(name):
    jobs = load(name)["jobs"]
    with_secrets = _jobs_with_secrets(name)
    assert with_secrets
    for jn in with_secrets:
        cond = jobs[jn].get("if", "")
        assert MAIN in cond, (name, jn)
        assert cond.startswith("${{") and cond.endswith("}}")


def test_que_jobs_tienen_secretos_y_condicion_de_main():
    assert sorted(_jobs_with_secrets(V2)) == ["fetch", "heartbeat", "verify"]
    assert _jobs_with_secrets(PN) == ["personal-nav"] and _jobs_with_secrets(PF) == ["personal-full"]
    jobs = load(V2)["jobs"]
    assert MAIN in jobs["publish"]["if"]                    # sin secretos, pero despliega en Pages: tampoco desde otra rama
    assert "secrets." not in json.dumps(jobs["gate"])       # la puerta no tiene secretos: no necesita la condición


@pytest.mark.parametrize("name,jobs", [(V2, ["gate", "fetch", "heartbeat"]), (PN, ["personal-nav"]), (PF, ["personal-full"])])
def test_se_mantiene_el_gating_por_feed_v2_enabled(name, jobs):
    for jn in jobs:
        cond = load(name)["jobs"][jn]["if"]
        assert "github.event_name != 'schedule' || vars.FEED_V2_ENABLED == 'true'" in cond, (name, jn)


# ---- evaluación de las condiciones (subconjunto de expresiones de Actions que usan estos workflows)
def evaluate(cond, ctx, cancelled=False):
    """Evalúa `${{ ... }}` con el contexto `ctx` ({'github.ref': ..., 'needs.gate.outputs.skip': ...}); lo ausente vale '' como en Actions."""
    expr = cond.strip()
    assert expr.startswith("${{") and expr.endswith("}}"), cond
    expr = expr[3:-2]
    expr = expr.replace("!cancelled()", " (not CANCELLED) ").replace("always()", " True ").replace("&&", " and ").replace("||", " or ")
    expr = re.sub(r"\b(github|vars|needs|inputs|steps)((?:\.[A-Za-z0-9_\-]+)+)", lambda m: "CTX(%r)" % (m.group(1) + m.group(2)), expr)
    assert "!" not in expr.replace("!=", ""), expr

    def CTX(k):
        return ctx.get(k, "")
    return bool(eval(expr, {"__builtins__": {}}, {"CTX": CTX, "CANCELLED": cancelled, "True": True}))   # noqa: S307 - solo expresiones de los YAML del repo


def ctx(event="schedule", ref="refs/heads/main", enabled="true", **extra):
    c = {"github.event_name": event, "github.ref": ref, "vars.FEED_V2_ENABLED": enabled}
    c.update({k.replace("__", "."): v for k, v in extra.items()})
    return c


def runs(name, job, c):
    j = load(name)["jobs"][job]
    return evaluate(j["if"], c) if "if" in j else True


@pytest.mark.parametrize("name,job", [(PN, "personal-nav"), (PF, "personal-full")])
def test_personal_cron_y_dispatch_normales_corren_y_desde_otra_rama_no(name, job):
    assert runs(name, job, ctx("schedule"))
    assert not runs(name, job, ctx("schedule", enabled=""))
    assert runs(name, job, ctx("workflow_dispatch", enabled=""))                  # el lanzamiento manual funciona aunque la variable no esté
    assert not runs(name, job, ctx("workflow_dispatch", ref="refs/heads/rama-vieja"))
    assert not runs(name, job, ctx("workflow_dispatch", ref="refs/tags/v1"))


def test_v2_cron_y_dispatch_normales_corren():
    for c in (ctx("schedule"), ctx("workflow_dispatch", enabled="")):
        assert runs(V2, "gate", c)
        c2 = dict(c, **{"needs.gate.outputs.skip": "false"})
        assert runs(V2, "fetch", c2)
        assert runs(V2, "publish", dict(c2, **{"needs.fetch.result": "success"}))
        assert runs(V2, "verify", dict(c2, **{"needs.publish.result": "success"}))
        assert runs(V2, "heartbeat", c2)


def test_v2_desde_otra_rama_no_corre_ningun_job_con_secretos_ni_despliega():
    c = ctx("workflow_dispatch", ref="refs/heads/rama-vieja", **{"needs__gate__outputs__skip": "false", "needs__fetch__result": "success",
                                                                  "needs__publish__result": "success"})
    for job in ("fetch", "publish", "verify", "heartbeat"):
        assert not runs(V2, job, c), job


def test_v2_salida_por_frescura_salta_el_trabajo_y_el_latido_corre():
    c = ctx("workflow_dispatch", **{"needs__gate__outputs__skip": "true", "needs__fetch__result": "skipped", "needs__publish__result": "skipped"})
    assert not runs(V2, "fetch", c) and not runs(V2, "publish", c) and not runs(V2, "verify", c)
    assert runs(V2, "heartbeat", c)


def test_v2_si_la_puerta_falla_sin_salida_se_trabaja():
    assert runs(V2, "fetch", ctx("schedule"))                                        # needs.gate.outputs.skip ausente => ''
    assert not evaluate(load(V2)["jobs"]["fetch"]["if"], ctx("schedule"), cancelled=True)


def test_control_negativo_el_evaluador_detecta_una_condicion_sin_main():
    cond = "${{ github.event_name != 'schedule' || vars.FEED_V2_ENABLED == 'true' }}"
    assert evaluate(cond, ctx("workflow_dispatch", ref="refs/heads/rama-vieja"))      # sin la condición de main SÍ correría desde otra rama


# ------------------------------------------------------------------ ningún YAML lleva URL privadas ni valores de secretos
SECRET_SHAPES = [
    (r"[a-z0-9]{20}\.supabase\.co", "URL de proyecto de Supabase"),
    (r"hc-ping\.com/[0-9a-f]{8}-[0-9a-f]{4}-", "URL de Healthchecks con clave"),
    (r"hc-ping\.com/[A-Za-z0-9_-]{16,}", "URL de Healthchecks con clave"),
    (r"sb_(?:publishable|secret)_[A-Za-z0-9_-]{8,}", "clave de Supabase"),
    (r"eyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}", "JWT"),
    (r"\b(?:ghp|gho|ghs|ghu|github_pat)_[A-Za-z0-9_]{20,}", "token de GitHub"),
    (r"\bIE[0-9A-Z]{10}\b", "ISIN"),
    (r"/products/\d{6}", "id de ficha"),
]


def _all_workflows():
    return sorted(p.name for p in WF.glob("*.yml"))


@pytest.mark.parametrize("name", _all_workflows())
def test_ningun_workflow_contiene_urls_ni_valores_de_secretos(name):
    text = (WF / name).read_text(encoding="utf-8")
    found = [why for pat, why in SECRET_SHAPES if re.search(pat, text)]
    assert found == [], (name, found)                      # el mensaje dice qué forma, nunca el valor


@pytest.mark.parametrize("sample,why", [
    ("x: https://abcdefghijklmnopqrst.supabase.co/rest", "Supabase"), ("u: https://hc-ping.com/0123abcd-45ef-6789-abcd-0123456789ab", "Healthchecks"),
    ("k: sb_publishable_FAKEKEY01234", "clave"), ("t: ghp_" + "a" * 30, "GitHub"), ("i: IE0FAKE00001", "ISIN"), ("p: /products/000001/x", "ficha"),
])
def test_control_negativo_el_detector_encuentra_cada_forma(sample, why):
    assert any(re.search(pat, sample) and why.lower() in w.lower() for pat, w in SECRET_SHAPES), sample


# ------------------------------------------------------------------ pieza 5: documentación
def test_el_readme_documenta_los_dos_relojes_la_puerta_force_y_la_parada_temprana():
    root = WF.parent.parent
    readme = (root / "README.md").read_text(encoding="utf-8")
    cadencia = readme[readme.index("## Cadencia real de GitHub Actions"):readme.index("## Runbook")]
    for frag in ("dos relojes", "Disparador externo", "respaldo", "Puerta de frescura", "40 min", "**`force`**", "Parada temprana", "último día hábil esperado",
                 "prev_nav_date_iso", "no están medidas", "fase en sombra", '{"ref":"main"}', "refs/heads/main", "45 min"):
        assert frag in cadencia, frag
    for name, crs in EXPECTED_CRON.items():                       # la tabla del README dice las mismas horas que los YAML
        row = next(line for line in cadencia.splitlines() if line.startswith("| `%s`" % name))
        for cr in crs:
            minute, hour = cr.split()[:2]
            for h in hour.split(","):
                assert "%02d:%s" % (int(h), minute) in row, (name, h, minute)
    assert "23,53 5-21" not in readme and "47 6,11,15,21" not in readme and "38 6,11,15,21" not in readme
    doc = (root / "docs" / "CONTRATO_FEED_V2.md").read_text(encoding="utf-8")
    assert "prev_nav_date_iso" in doc and "23,53 5-21" not in doc and "38 6,11,15,21" not in doc
