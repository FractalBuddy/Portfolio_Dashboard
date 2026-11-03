"""Bloque 4 (AU-02): la URL y la clave publicable de Supabase no pueden salir en claro en los logs públicos de Actions.

Los valores se leen con `${{ secrets.NAME || vars.NAME }}`: hoy funciona con las variables; cuando el dueño cree los secretos con el mismo nombre,
GitHub los enmascara en el bloque `env:` de cada paso. Y ningún workflow puede imprimirlos ni exportarlos de otra forma."""
import pathlib
import re

import pytest

yaml = pytest.importorskip("yaml")

WF = pathlib.Path(__file__).resolve().parent.parent / ".github" / "workflows"
NAMES = ("SUPABASE_URL", "SUPABASE_PUBLISHABLE_KEY")
EXPR = re.compile(r"\$\{\{(.*?)\}\}", re.S)
# los alias con los que los workflows pasan esos valores al shell
ALIAS = r"(?:SUPABASE_URL|SUPABASE_PUBLISHABLE_KEY|SB_URL|SB_KEY)"
USER_WF = {"nav-probe.yml", "personal-full.yml", "personal-nav.yml", "supabase-keepalive.yml"}


def supabase_leaks(text):
    """Motivos por los que un workflow (texto YAML) deja salir esos valores. Lista vacía = bien."""
    out = []
    for m in EXPR.finditer(text):
        inner = " ".join(m.group(1).split())
        for n in NAMES:
            if re.search(r"\b(?:vars|secrets)\.%s\b" % n, inner) and inner != "secrets.%s || vars.%s" % (n, n):
                out.append("expresión no enmascarable: ${{ %s }}" % inner)
    wf = yaml.safe_load(text)
    for jname, job in (wf.get("jobs") or {}).items():
        for st in job.get("steps") or []:
            run = st.get("run") or ""
            where = "%s/%s" % (jname, st.get("name", "?"))
            for line in run.splitlines():
                code = line.split("#", 1)[0] if not re.search(r"""["'].*#""", line) else line
                if re.search(r"\$\{\{[^}]*SUPABASE", code):
                    out.append("%s: interpolación ${{ }} de Supabase dentro de run" % where)
                if re.search(r"\b(?:echo|printf|cat|tee)\b[^|\n]*\$\{?%s\b" % ALIAS, code) and not re.search(r"\|\s*\S", code.split("$", 1)[1] if "$" in code else ""):
                    out.append("%s: imprime un valor de Supabase" % where)
                if re.search(r"(?:GITHUB_ENV|GITHUB_OUTPUT|GITHUB_STEP_SUMMARY|::set-output|::set-env)", code) and re.search(r"\$\{?%s\b" % ALIAS, code):
                    out.append("%s: exporta un valor de Supabase" % where)
            if re.search(r"(?m)^\s*set\s+-[a-zA-Z]*x|\bbash\s+-[a-zA-Z]*x\b|\bsh\s+-[a-zA-Z]*x\b", run):
                if re.search(ALIAS, run):
                    out.append("%s: set -x con valores de Supabase" % where)
            if re.search(r"\bcurl\b[^\n]*\s(?:-v|--verbose|--trace(?:-ascii)?|-i|--include)\b", run) and re.search(ALIAS, run):
                out.append("%s: curl en modo verboso con valores de Supabase" % where)
            if re.search(r"(?m)^\s*(?:env|printenv|export\s+-p)\s*$", run):
                out.append("%s: vuelca todo el entorno" % where)
    return out


def _workflows():
    return sorted(p.name for p in WF.glob("*.yml"))


@pytest.mark.parametrize("name", _workflows())
def test_ningun_workflow_deja_salir_la_url_ni_la_clave_de_supabase(name):
    assert supabase_leaks((WF / name).read_text(encoding="utf-8")) == []


def test_los_workflows_que_usan_supabase_son_los_esperados_y_todos_leen_secret_o_var():
    usan = {n for n in _workflows() if re.search(r"\b(?:vars|secrets)\.SUPABASE_", (WF / n).read_text(encoding="utf-8"))}
    assert usan == USER_WF
    for n in usan:
        text = (WF / n).read_text(encoding="utf-8")
        assert "vars.SUPABASE_URL }}" not in text.replace("secrets.SUPABASE_URL || vars.SUPABASE_URL }}", "")
        assert "vars.SUPABASE_PUBLISHABLE_KEY }}" not in text.replace("secrets.SUPABASE_PUBLISHABLE_KEY || vars.SUPABASE_PUBLISHABLE_KEY }}", "")
        assert "${{ secrets.SUPABASE_URL || vars.SUPABASE_URL }}" in text and "${{ secrets.SUPABASE_PUBLISHABLE_KEY || vars.SUPABASE_PUBLISHABLE_KEY }}" in text


def test_ni_publish_feed_v2_ni_update_data_ni_ci_tocan_supabase():
    for n in ("publish-feed-v2.yml", "update-data.yml", "ci.yml", "keepalive.yml"):
        assert "SUPABASE" not in (WF / n).read_text(encoding="utf-8"), n


# ---- controles negativos: el detector SÍ encuentra cada forma de fuga (YAML sintético) ----
def _wf(run, env="SUPABASE_URL: ${{ secrets.SUPABASE_URL || vars.SUPABASE_URL }}"):
    return "on: workflow_dispatch\njobs:\n  j:\n    runs-on: x\n    steps:\n      - name: s\n        env:\n          %s\n        run: |\n          %s\n" % (env, run.replace("\n", "\n          "))


@pytest.mark.parametrize("env,run,motivo", [
    ("SUPABASE_URL: ${{ vars.SUPABASE_URL }}", "true", "no enmascarable"),
    ("SUPABASE_URL: ${{ secrets.SUPABASE_URL }}", "true", "no enmascarable"),
    ("SUPABASE_URL: ${{ vars.SUPABASE_URL || secrets.SUPABASE_URL }}", "true", "no enmascarable"),
    ("X: ${{ secrets.SUPABASE_URL || vars.SUPABASE_URL }}", 'echo "$SUPABASE_URL"', "imprime"),
    ("X: ${{ secrets.SUPABASE_URL || vars.SUPABASE_URL }}", 'echo "url=$SUPABASE_URL" >> "$GITHUB_OUTPUT"', "imprime|exporta"),
    ("X: ${{ secrets.SUPABASE_URL || vars.SUPABASE_URL }}", 'echo "SB=$SB_KEY" >> "$GITHUB_ENV"', "imprime|exporta"),
    ("X: ${{ secrets.SUPABASE_URL || vars.SUPABASE_URL }}", 'echo "${{ secrets.SUPABASE_URL || vars.SUPABASE_URL }}"', "interpolación"),
    ("X: ${{ secrets.SUPABASE_URL || vars.SUPABASE_URL }}", 'set -x\ncurl "$SUPABASE_URL"', "set -x"),
    ("X: ${{ secrets.SUPABASE_URL || vars.SUPABASE_URL }}", 'curl -v -H "apikey: $SUPABASE_PUBLISHABLE_KEY" "$SUPABASE_URL"', "verboso"),
    ("X: ${{ secrets.SUPABASE_URL || vars.SUPABASE_URL }}", "env", "entorno"),
])
def test_control_negativo_el_detector_encuentra_la_fuga(env, run, motivo):
    leaks = supabase_leaks(_wf(run, env))
    assert leaks and re.search(motivo, " | ".join(leaks)), leaks


def test_control_negativo_un_workflow_correcto_no_da_falsos_positivos():
    assert supabase_leaks(_wf('curl -s -H "apikey: ${SB_KEY}" "${SB_URL%/}/rest/v1/x" >/dev/null\nprintf \'%s\' "$SB_KEY" | cut -d. -f2 >/dev/null')) == []


# ---- AU-01: todos los workflows se pueden lanzar a mano (única forma de «disparar» una pasada cuando GitHub retrasa el cron)
@pytest.mark.parametrize("name", _workflows())
def test_todo_workflow_tiene_workflow_dispatch(name):
    wf = yaml.safe_load((WF / name).read_text(encoding="utf-8"))
    triggers = wf.get("on", wf.get(True))                    # PyYAML (YAML 1.1) interpreta `on` como True
    assert "workflow_dispatch" in triggers, name


def test_el_readme_y_el_contrato_no_prometen_la_cadencia_de_diseno_como_si_fuera_real():
    root = WF.parent.parent
    readme = (root / "README.md").read_text(encoding="utf-8")
    doc = (root / "docs" / "CONTRATO_FEED_V2.md").read_text(encoding="utf-8")
    for frag in ("Cadencia real de GitHub Actions", "14 horas", "no son un reloj", "workflow_dispatch", "Disparador externo", "NO implementada"):
        assert frag in readme or frag in doc, frag
    assert "Runbook" in readme and "Tras recrear el repo" in readme and "FEED_V2_INITIALIZED" in readme
    # las frases falsas de la auditoría (AU-10) ya no están
    assert "El backend **no** lleva en el repo ISIN" not in readme
    assert "El código del repo no contiene ningún ISIN" not in doc
    assert "(en cada PR)" not in readme
    assert "NAV fresco cada 30 min" not in readme.replace("No hay «NAV fresco cada 30 min»", "")
