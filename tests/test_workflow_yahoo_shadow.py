"""Sombra NAV Yahoo (F1), punto 9: los pasos en personal-full.yml. Van DESPUÉS de publicar, sin token ni secretos nuevos, con `continue-on-error`
y tope de tiempo por paso (un fallo o un cuelgue de la sombra nunca pone en rojo ni retrasa el NAV personal ni el feed), sin permisos de escritura,
sin commits, sin artefactos, y con el libro de contadores en la caché de Actions (clave única por run)."""
import json
import pathlib
import re

import pytest

from test_workflows_F1 import evaluate

yaml = pytest.importorskip("yaml")

WF = pathlib.Path(__file__).resolve().parent.parent / ".github" / "workflows"
PF = WF / "personal-full.yml"
RESTORE, MEASURE, SAVE = ("Yahoo shadow - restore aggregate counters (cache)", "Yahoo shadow - measure (third-party code, NO token, publishes nothing)",
                          "Yahoo shadow - save aggregate counters (cache)")
CACHE_SHA = "55cc8345863c7cc4c66a329aec7e433d2d1c52a9"           # actions/cache v6.1.0 (git ls-remote, 22-11-2026)


def job():
    return yaml.safe_load(PF.read_text(encoding="utf-8"))["jobs"]["personal-full"]


def steps():
    return {s["name"]: s for s in job()["steps"]}


def test_los_tres_pasos_existen_en_orden_y_despues_de_publicar():
    names = [s["name"] for s in job()["steps"]]
    assert names.index("Publish personal payloads (token, stdlib only)") < names.index(RESTORE) < names.index(MEASURE) < names.index(SAVE)
    assert names[-3:] == [RESTORE, MEASURE, SAVE]


@pytest.mark.parametrize("name", [RESTORE, MEASURE, SAVE])
def test_nunca_rojo_ni_cuelgue_y_sin_secretos(name):
    s = steps()[name]
    assert s["continue-on-error"] is True and 1 <= s["timeout-minutes"] <= 5
    text = json.dumps(s)
    for bad in ("secrets.", "SUPABASE", "PERSONAL_FEED_TOKEN", "vars.", "GITHUB_TOKEN", "github.token"):
        assert bad not in text, bad


def test_el_paso_de_medida_ejecuta_el_modulo_con_la_carpeta_privada_y_el_libro():
    s = steps()[MEASURE]
    assert s["id"] == "shadow" and "env" not in s and "uses" not in s
    assert s["run"].split() == ["python", "scripts/yahoo_shadow.py", "--dir", '"$RUNNER_TEMP/personal"', "--config", "config/yahoo_shadow.json",
                                "--ledger", '"$RUNNER_TEMP/yahoo-shadow/ledger.json"']


def test_la_cache_va_fijada_por_sha_con_clave_unica_por_run_y_solo_la_carpeta_del_libro():
    r, w = steps()[RESTORE], steps()[SAVE]
    assert r["uses"] == "actions/cache/restore@%s" % CACHE_SHA and w["uses"] == "actions/cache/save@%s" % CACHE_SHA
    for s in (r, w):
        assert s["with"]["path"] == "${{ runner.temp }}/yahoo-shadow"
        assert s["with"]["key"] == "yahoo-shadow-ledger-v1-${{ github.run_id }}-${{ github.run_attempt }}"
    assert r["with"]["restore-keys"] == "yahoo-shadow-ledger-v1-" and set(r["with"]) == {"path", "key", "restore-keys"} and set(w["with"]) == {"path", "key"}


def test_condiciones():
    s = steps()
    for name in (RESTORE, MEASURE):
        cond = s[name]["if"]
        assert evaluate(cond, {"steps.watch.outputs.work": "true"})
        assert not evaluate(cond, {"steps.watch.outputs.work": "false"})
        assert not evaluate(cond, {"steps.watch.outputs.work": "true"}, cancelled=True)
    cond = s[SAVE]["if"]
    assert evaluate(cond, {"steps.shadow.outputs.ledger": "written"})
    assert not evaluate(cond, {"steps.shadow.outputs.ledger": ""})                 # sin lectura (fuera de franja, desactivada...) no se guarda nada
    assert not evaluate(cond, {"steps.shadow.outputs.ledger": "written"}, cancelled=True)


def test_el_job_sigue_igual_solo_lectura_solo_main_y_sin_artefactos_ni_commits():
    wf_text = PF.read_text(encoding="utf-8")
    j = job()
    assert j["permissions"] == {"contents": "read"} and "refs/heads/main" in j["if"] and j["timeout-minutes"] <= 15
    assert "upload-artifact" not in wf_text and "contents: write" not in wf_text and "git push" not in wf_text and "git commit" not in wf_text
    assert "YAHOO_SHADOW" not in wf_text                                       # el interruptor es config/yahoo_shadow.json, no una variable del repo
    assert sum(s.get("timeout-minutes", 0) for s in j["steps"] if s["name"] in (RESTORE, MEASURE, SAVE)) <= 9


def test_los_pasos_anteriores_no_cambian():
    names = [s["name"] for s in job()["steps"]]
    assert names[:6] == ["Check out repo", "Set up Python", "Read personal watch list (token, stdlib only)", "Install dependencies (hash-pinned)",
                         "Build personal payloads (third-party code, NO token)", "Publish personal payloads (token, stdlib only)"]
    assert not any("continue-on-error" in s for s in job()["steps"][:6])


def test_el_workflow_explica_la_sombra_en_sus_comentarios():
    text = PF.read_text(encoding="utf-8")
    for frag in ("SOMBRA NAV Yahoo", "config/yahoo_shadow.json", "NO PUBLICA NADA", "continue-on-error", "caché de Actions"):
        assert frag in text, frag
    assert re.search(r"(?m)^\s+- cron: '38 6,13,21 \* \* \*'$", text)            # la frecuencia del job no cambia
