"""Sonda de fondos, punto 5: el workflow (solo manual, solo lectura, solo main, tope de tiempo, un único secreto en un único paso, dos artefactos: NOMBRES y GENÉRICOS, ninguno con datos del secreto)."""
import pathlib
import re
import subprocess
import sys

import pytest

yaml = pytest.importorskip("yaml")

ROOT = pathlib.Path(__file__).resolve().parent.parent
WFP = ROOT / ".github" / "workflows" / "fund-probe.yml"


def wf():
    return yaml.safe_load(WFP.read_text(encoding="utf-8"))


def job():
    return wf()["jobs"]["probe"]


def step(name_part):
    hits = [s for s in job()["steps"] if name_part.lower() in s.get("name", "").lower()]
    assert len(hits) == 1, name_part
    return hits[0]


def test_solo_workflow_dispatch_con_dos_inputs_max_requests_y_sections():
    on = wf().get("on", wf().get(True))
    assert set(on) == {"workflow_dispatch"}
    assert set(on["workflow_dispatch"]["inputs"]) == {"max_requests", "sections"}


def test_input_sections_es_una_eleccion_cerrada_con_both_por_defecto():
    sec = (wf().get("on", wf().get(True)))["workflow_dispatch"]["inputs"]["sections"]
    assert sec["type"] == "choice" and sec["options"] == ["names", "generic", "both"] and sec["default"] == "both"


def test_input_max_requests_llega_hasta_400_y_por_defecto_400():
    mr = (wf().get("on", wf().get(True)))["workflow_dispatch"]["inputs"]["max_requests"]
    assert mr["default"] == "400" and "400" in mr["description"] and "250" not in mr["description"]


def test_permisos_solo_lectura_y_sin_despliegue_ni_escritura():
    w = wf()
    text = WFP.read_text(encoding="utf-8")
    assert w["permissions"] == {} and set(w["jobs"]) == {"probe"} and job()["permissions"] == {"contents": "read"}
    for bad in ("contents: write", "id-token", "pages:", "deployments", "pull-requests", "issues:", "git push", "gh ", "curl ", "supabase", "SUPABASE", "environment:"):
        assert bad not in text, bad


def test_timeout_de_20_minutos_y_guarda_de_rama_main():
    assert job()["timeout-minutes"] == 20
    cond = job()["if"]
    assert "github.ref == 'refs/heads/main'" in cond and "||" not in cond


def test_todas_las_acciones_fijadas_por_sha_y_checkout_sin_credenciales():
    for s in job()["steps"]:
        if "uses" in s:
            assert re.fullmatch(r"[\w.\-]+/[\w.\-]+@[0-9a-f]{40}", s["uses"]), s["uses"]
    assert step("check out")["with"]["persist-credentials"] is False


def test_un_unico_secreto_en_un_unico_paso_y_nada_mas():
    text = WFP.read_text(encoding="utf-8")
    assert re.findall(r"secrets\.[A-Za-z_]+", text) == ["secrets.PROBE_ISINS"] and "vars." not in text and "GITHUB_TOKEN" not in text
    probe = step("run probe")
    assert set(probe["env"]) == {"PROBE_ISINS", "MAX_REQUESTS", "SECTIONS"}
    assert "env" not in job() and "env" not in wf()
    for s in job()["steps"]:
        if s["name"] != probe["name"]:
            assert "PROBE_ISINS" not in str(s.get("env", {})) + str(s.get("run", ""))


def test_los_inputs_van_por_variable_de_entorno_nunca_dentro_del_run():
    probe = step("run probe")
    assert "${{" not in probe["run"] and '"$MAX_REQUESTS"' in probe["run"] and '--sections "$SECTIONS"' in probe["run"]
    assert probe["env"]["SECTIONS"] == "${{ inputs.sections }}" and probe["env"]["MAX_REQUESTS"] == "${{ inputs.max_requests }}"


def test_dependencias_con_hashes_y_orden_de_pasos():
    install = step("install")
    assert "--require-hashes" in install["run"] and "requirements.txt" in install["run"]
    names = [s["name"] for s in job()["steps"]]
    assert names.index(install["name"]) < names.index(step("run probe")["name"]) < names.index(step("upload names")["name"]) < names.index(step("upload generic")["name"])


def test_el_script_es_el_unico_codigo_que_se_ejecuta():
    probe = step("run probe")
    assert probe["run"].strip().startswith("python scripts/fund_probe.py") and probe["run"].count("python") == 1


def test_dos_artefactos_nombres_y_genericos_cada_uno_un_unico_json():
    for part, fname, art in (("upload names", "fund_probe_names.json", "fund-probe-names"), ("upload generic", "fund_probe_generic.json", "fund-probe-generic")):
        up = step(part)
        assert up["uses"].startswith("actions/upload-artifact@") and up["with"]["name"] == art
        assert up["with"]["path"].strip().endswith(fname) and "\n" not in up["with"]["path"].strip()
        assert up["with"]["if-no-files-found"] == "error" and int(up["with"]["retention-days"]) <= 14
        assert "summary" not in up["with"]["path"].lower() and "PROBE_ISINS" not in str(up)
    assert len([s for s in job()["steps"] if "upload-artifact" in s.get("uses", "")]) == 2


def test_el_json_del_run_y_el_de_cada_artefacto_son_el_mismo_fichero():
    probe = step("run probe")["run"]
    for flag, part in (("--json-out", "upload names"), ("--generic-json-out", "upload generic")):
        m = re.search(flag + r"\s+\"?(\$RUNNER_TEMP/[\w./-]+)\"?", probe)
        assert m and m.group(1).replace("$RUNNER_TEMP", "${{ runner.temp }}") == step(part)["with"]["path"].strip(), flag


def test_ningun_otro_workflow_llama_a_la_sonda():
    for p in (ROOT / ".github" / "workflows").glob("*.yml"):
        if p.name != "fund-probe.yml":
            assert "fund_probe" not in p.read_text(encoding="utf-8"), p.name


def test_el_script_arranca_sin_red_y_con_ayuda():
    r = subprocess.run([sys.executable, "-I", str(ROOT / "scripts" / "fund_probe.py"), "--help"], capture_output=True, text=True, timeout=60)
    assert r.returncode == 0 and "--max-requests" in r.stdout
