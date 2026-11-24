"""Workflow fund-lag.yml: programado (6 al día, minutos no redondos) + manual, medición de solo lectura, ÚNICA escritura en un job aparte que solo empuja a la rama huérfana
`probe-data` desde main, sin secretos; y el guardia D45 existente con su excepción EXACTA (con controles negativos sobre YAML mutado)."""
import copy
import importlib
import pathlib
import re

import pytest

yaml = pytest.importorskip("yaml")

ROOT = pathlib.Path(__file__).resolve().parent.parent
WF = ROOT / ".github" / "workflows"
FILE = WF / "fund-lag.yml"
SCRIPT_GIT = ROOT / "scripts" / "fund_lag_git.sh"
MAIN = "github.ref == 'refs/heads/main'"


def wf():
    return yaml.safe_load(FILE.read_text(encoding="utf-8"))


def job(name):
    return wf()["jobs"][name]


def step(jobname, part):
    hits = [s for s in job(jobname)["steps"] if part.lower() in s.get("name", "").lower()]
    assert len(hits) == 1, part
    return hits[0]


def triggers(w=None):
    w = w or wf()
    return w.get("on", w.get(True))                 # PyYAML (YAML 1.1) interpreta `on` como True


def write_jobs(w):
    """Jobs con contents: write (a nivel de job o heredado del workflow), igual que hace D45."""
    return sorted(n for n, j in w["jobs"].items() if j.get("permissions", w["permissions"]).get("contents") == "write")


# ------------------------------------------------------------------ disparadores
def test_solo_schedule_y_workflow_dispatch():
    assert set(triggers()) == {"schedule", "workflow_dispatch"}


def test_seis_ejecuciones_al_dia_en_horas_no_en_punto():
    crons = [e["cron"] for e in triggers()["schedule"]]
    assert crons == ["17 6,9,12,15,18,21 * * *"]
    minute, hours, dom, mon, dow = crons[0].split()
    assert minute == "17" and [int(h) for h in hours.split(",")] == [6, 9, 12, 15, 18, 21] and (dom, mon, dow) == ("*", "*", "*")
    assert minute not in ("0", "15", "30", "45")


def test_hay_ruta_manual():
    assert "workflow_dispatch" in triggers()


# ------------------------------------------------------------------ permisos: la única escritura es el job publish
def test_permisos_workflow_vacios_measure_lee_publish_escribe():
    w = wf()
    assert w["permissions"] == {} and set(w["jobs"]) == {"measure", "publish"}
    assert job("measure")["permissions"] == {"contents": "read"} and job("publish")["permissions"] == {"contents": "write"}
    assert write_jobs(w) == ["publish"]


def test_control_negativo_el_detector_de_escritura_encuentra_cada_forma_de_colarla():
    w = wf()
    m = copy.deepcopy(w)
    m["jobs"]["measure"]["permissions"] = {"contents": "write"}
    assert write_jobs(m) == ["measure", "publish"]
    m = copy.deepcopy(w)
    m["permissions"] = {"contents": "write"}
    del m["jobs"]["measure"]["permissions"]
    assert "measure" in write_jobs(m)


def _d45_con(tmp_path, monkeypatch, mutate=None):
    """Ejecuta el D45 REAL sobre una copia de los workflows (con `mutate` aplicado a fund-lag.yml)."""
    for p in WF.glob("*.yml"):
        (tmp_path / p.name).write_text(p.read_text(encoding="utf-8"), encoding="utf-8")
    if mutate:
        w = yaml.safe_load((tmp_path / "fund-lag.yml").read_text(encoding="utf-8"))
        mutate(w)
        (tmp_path / "fund-lag.yml").write_text(yaml.safe_dump(w), encoding="utf-8")
    mod = importlib.import_module("test_extra_D")
    monkeypatch.setattr(mod, "WF", tmp_path)
    mod.test_D45_permisos_y_timeouts_de_todos_los_jobs()


def test_D45_pasa_con_el_workflow_real_y_su_excepcion(tmp_path, monkeypatch):
    _d45_con(tmp_path, monkeypatch)


def test_D45_rechaza_que_el_job_measure_escriba(tmp_path, monkeypatch):
    def m(w):
        w["jobs"]["measure"]["permissions"] = {"contents": "write"}
    with pytest.raises(AssertionError) as e:
        _d45_con(tmp_path, monkeypatch, m)
    assert "('fund-lag.yml', 'measure') in" in str(e.value)


def test_D45_rechaza_un_tercer_job_con_escritura_y_un_timeout_largo(tmp_path, monkeypatch):
    def m(w):
        w["jobs"]["otro"] = copy.deepcopy(w["jobs"]["publish"])
    with pytest.raises(AssertionError) as e:
        _d45_con(tmp_path, monkeypatch, m)
    assert "('fund-lag.yml', 'otro') in" in str(e.value)

    def m2(w):
        w["jobs"]["publish"]["timeout-minutes"] = 21
    with pytest.raises(AssertionError):
        _d45_con(tmp_path, monkeypatch, m2)


def test_la_excepcion_de_D45_es_exacta_y_solo_una_para_este_workflow():
    src = (ROOT / "tests" / "test_extra_D.py").read_text(encoding="utf-8")
    assert src.count('"fund-lag.yml"') == 1 and '("fund-lag.yml", "publish")' in src
    assert "fund-lag.yml\", \"measure" not in src


# ------------------------------------------------------------------ sin secretos
def test_sin_secretos_ni_variables_en_ninguna_parte_del_workflow():
    text = FILE.read_text(encoding="utf-8")
    assert re.findall(r"secrets\.[A-Za-z0-9_]+", text) == [] and "vars." not in text and "secrets:" not in text
    assert "SUPABASE" not in text and "PROBE_ISINS" not in text and "PERSONAL_FEED_TOKEN" not in text


def test_el_unico_token_es_el_efimero_del_job_de_escritura_y_en_un_unico_paso():
    text = FILE.read_text(encoding="utf-8")
    assert re.findall(r"\$\{\{\s*github\.token\s*\}\}", text) == ["${{ github.token }}"]
    assert step("publish", "append and push")["env"] == {"GH_TOKEN": "${{ github.token }}"}
    assert "token" not in str(job("measure")).lower().replace("persist-credentials", "")
    assert "GITHUB_TOKEN" not in text


def test_control_negativo_la_forma_de_buscar_secretos_encuentra_una_interpolacion_de_secreto():
    mal = FILE.read_text(encoding="utf-8") + "\n          X: ${{ secrets.ALGO }}\n"
    assert re.findall(r"secrets\.[A-Za-z0-9_]+", mal) == ["secrets.ALGO"]


def test_d44_sigue_igual_este_workflow_no_aporta_ningun_secreto():
    used = set()
    for f in WF.glob("*.yml"):
        used |= set(re.findall(r"secrets\.([A-Za-z0-9_]+)", f.read_text(encoding="utf-8")))
    assert used == {"FRED_API_KEY", "HC_UPDATE_URL", "HC_SUPABASE_URL", "HC_UPDATE_V2_URL", "PERSONAL_FEED_TOKEN", "HEALTHCHECK_PERSONAL_NAV_URL",
                    "SUPABASE_URL", "SUPABASE_PUBLISHABLE_KEY", "PROBE_ISINS", "PRIVATE_DENYLIST"}         # REQUISITO CAMBIADO (R-01): + PRIVATE_DENYLIST (ci.yml)
    assert "secrets." not in FILE.read_text(encoding="utf-8")


# ------------------------------------------------------------------ solo main, solo esa rama, la medición no depende del push
def test_los_dos_jobs_solo_corren_sobre_main():
    for n in ("measure", "publish"):
        assert MAIN in job(n)["if"] and "||" not in job(n)["if"], n
    assert job("publish")["needs"] == "measure" and "needs" not in job("measure")


def test_publish_solo_corre_si_la_medicion_dejo_lineas():
    cond = job("publish")["if"]
    assert "needs.measure.outputs.append == 'true'" in cond
    assert job("measure")["outputs"] == {"append": "${{ steps.measure.outputs.append }}"}
    up = step("measure", "upload new lines")
    assert "steps.measure.outputs.append == 'true'" in up["if"]


def test_el_push_no_hace_fallar_el_workflow_ni_la_medicion_y_avisa():
    push = step("publish", "append and push")
    assert push["continue-on-error"] is True and push["id"] == "push"
    warn = step("publish", "warn if the push failed")
    assert "steps.push.outcome == 'failure'" in warn["if"] and "::warning::" in warn["run"] and "GITHUB_STEP_SUMMARY" in warn["run"]
    names = [s["name"] for s in job("publish")["steps"]]
    assert names.index(push["name"]) < names.index(warn["name"])
    assert "continue-on-error" not in str(job("measure"))                   # la medición no necesita esconder nada: su resultado no depende del push
    assert "continue-on-error" not in step("publish", "validate lines")      # y validar sí puede (y debe) fallar en rojo


def test_la_validacion_va_antes_del_push_y_no_instala_nada_de_terceros():
    names = [s["name"] for s in job("publish")["steps"]]
    assert names.index(step("publish", "validate lines")["name"]) < names.index(step("publish", "append and push")["name"])
    text = str(job("publish"))
    for tercero in ("pip ", "setup-python", "requirements", "yfinance"):
        assert tercero not in text, tercero
    assert "python3 scripts/fund_lag_common.py validate" in step("publish", "validate lines")["run"]


def test_la_medicion_no_escribe_en_ningun_sitio_salvo_su_artefacto():
    text = str(job("measure"))
    for bad in ("git push", "git commit", "contents': 'write", "gh ", "curl ", "actions/cache", "GITHUB_ENV"):
        assert bad not in text, bad
    assert step("measure", "install")["run"] == "pip install --require-hashes --no-deps -r requirements.txt"


def test_el_script_de_git_solo_empuja_a_probe_data_sin_force_y_una_sola_vez():
    src = SCRIPT_GIT.read_text(encoding="utf-8")
    code = "\n".join(ln for ln in src.splitlines() if not ln.lstrip().startswith("#"))
    pushes = [ln for ln in code.splitlines() if re.search(r"\bpush\b", ln)]
    assert len(pushes) == 1 and 'HEAD:refs/heads/$BRANCH' in pushes[0] and '"$push_url"' in pushes[0]
    assert re.search(r'^BRANCH="probe-data"$', code, re.M) and 'BRANCH=' not in code.replace('BRANCH="probe-data"', "")
    assert not re.search(r"--force|\s-f\s|--force-with-lease|\+HEAD|\+refs|--mirror|--all|--delete|:refs/heads/main|refs/heads/data\b", code)
    assert 'FILE="lag/fund_lag.jsonl"' in code


def test_control_negativo_el_detector_de_force_y_de_otras_ramas_encuentra_las_formas_peligrosas():
    det = r"--force|\s-f\s|--force-with-lease|\+HEAD|\+refs|--mirror|--all|--delete|:refs/heads/main|refs/heads/data\b"
    for mal in ('git push --force "$u" HEAD:refs/heads/x', 'git push -f "$u" x', 'git push "$u" +HEAD:refs/heads/x', 'git push "$u" HEAD:refs/heads/main',
                'git push "$u" HEAD:refs/heads/data', "git push --mirror", "git push --all", "git push --delete origin x"):
        assert re.search(det, mal), mal


def test_el_workflow_llama_al_script_de_git_con_los_dos_subcomandos_y_nada_mas_toca_git():
    assert step("measure", "read registry head")["run"] == 'bash scripts/fund_lag_git.sh head "$RUNNER_TEMP/lag_head.jsonl"'
    assert step("publish", "append and push")["run"] == 'bash scripts/fund_lag_git.sh publish "$RUNNER_TEMP/new/fund_lag_new.jsonl"'
    for n in ("measure", "publish"):
        for s in job(n)["steps"]:
            assert not re.search(r"\bgit\b", s.get("run", "")) or "fund_lag_git.sh" in s["run"], s["name"]


# ------------------------------------------------------------------ higiene general
def test_acciones_fijadas_por_sha_checkout_sin_credenciales_y_ningun_run_interpola():
    for n in ("measure", "publish"):
        for s in job(n)["steps"]:
            assert "${{" not in s.get("run", ""), s["name"]
            if "uses" in s:
                assert re.fullmatch(r"[\w.-]+/[\w.-]+@[0-9a-f]{40}", s["uses"]), s["uses"]
            if str(s.get("uses", "")).startswith("actions/checkout"):
                assert s["with"]["persist-credentials"] is False
    assert job("measure")["runs-on"] == job("publish")["runs-on"] == "ubuntu-24.04"
    assert job("measure")["timeout-minutes"] <= 20 and job("publish")["timeout-minutes"] <= 20


def test_las_acciones_de_artefacto_usan_los_mismos_sha_que_el_resto_del_repo():
    others = "\n".join(p.read_text(encoding="utf-8") for p in WF.glob("*.yml") if p.name != "fund-lag.yml")
    for n in ("measure", "publish"):
        for s in job(n)["steps"]:
            if str(s.get("uses", "")).split("@")[0] in ("actions/upload-artifact", "actions/download-artifact", "actions/checkout", "actions/setup-python"):
                assert s["uses"] in others, s["uses"]


def test_el_artefacto_es_un_unico_fichero_con_el_mismo_nombre_en_subida_y_descarga():
    up, down = step("measure", "upload new lines"), step("publish", "download new lines")
    assert up["with"]["name"] == down["with"]["name"] == "fund-lag-new"
    assert up["with"]["path"] == "${{ runner.temp }}/fund_lag_new.jsonl" and "fund_lag_new.jsonl" in step("measure", "measure lag")["run"]
    assert step("publish", "validate lines")["run"].endswith('"$RUNNER_TEMP/new/fund_lag_new.jsonl"')


def test_el_input_de_estado_llega_por_variable_de_entorno_no_dentro_del_run():
    s = step("measure", "measure lag")
    assert s["env"] == {"HEAD_STATE": "${{ steps.head.outputs.state }}"} and '"$HEAD_STATE"' in s["run"]


def test_ningun_otro_workflow_llama_a_estos_scripts_ni_este_a_la_sonda():
    for p in WF.glob("*.yml"):
        text = p.read_text(encoding="utf-8")
        if p.name != "fund-lag.yml":
            assert "fund_lag" not in text and "fund-lag" not in text and "probe-data" not in text, p.name
    own = FILE.read_text(encoding="utf-8")
    assert "fund_probe" not in own and "fund-probe" not in own


def test_documenta_el_autolimite_y_la_rama_huerfana_en_la_cabecera():
    head = FILE.read_text(encoding="utf-8").split("\non:")[0]
    for frag in ("AUTOLÍMITE", "más de 7 días", "probe-data", "HUÉRFANA", "ni main ni la rama `data`", "Sin secretos"):
        assert frag in head, frag
