"""Reintento ÚNICO del despliegue de Pages (incidente 12-11: actions/deploy-pages falló con «Fetching artifact metadata failed», un error de la API de
GitHub, y el run mandó /fail; «Re-run failed jobs» no sirve porque sube un 2.º artefacto `github-pages`).

Qué se fija aquí, sin GitHub real:
  * Estructura del job `publish`: un solo upload del artefacto; la validación (sanity-check) antes de cualquier despliegue y sin continue-on-error;
    el 1.er deploy con continue-on-error; un paso que decide (espera corta, ¿fallo rápido?, ¿Pages ya sirve este sitio?) y un 2.º deploy, el ÚLTIMO,
    que solo corre si ese paso dice «retry». Nada más cambia: gate, fetch, verify y heartbeat quedan idénticos (huella del job).
  * Ejecución REAL en bash del paso que decide, con `date`/`curl`/`sleep` falsos.
  * Simulación de la secuencia de pasos (evaluando sus `if:` con la semántica de Actions que usamos: success() implícito y `steps.<id>.outcome`)
    en los escenarios del incidente, y su efecto en el heartbeat (ok / degraded / fail, mismo texto).
  * Controles negativos: mutaciones del YAML que el comprobador debe detectar.
Lo que NO se puede probar aquí (Actions real, deploy-pages real, la API de GitHub) va en auditorias/FIXES_FEED_REINTENTO_ESCALA.md."""
import copy
import hashlib
import json
import re

import pytest

from test_workflows import _exe, bash, calls, load, step

V2 = "publish-feed-v2.yml"
DEPLOY = "actions/deploy-pages"
UPLOAD = "actions/upload-pages-artifact"
GATE_NAME = "Decide deploy retry"
RETRY_NAME = "Deploy to GitHub Pages (retry)"
START_NAME = "Mark deploy start"
FAST_FAIL_S = 120          # un fallo del 1.er deploy más tarde que esto ya no es el fallo rápido de metadatos/API (el deploy llegó a crear y sondear)
WAIT_S = 30                # espera corta antes del reintento

# Huellas de los jobs que NO deben cambiar (tomadas de main en 262fddf, antes de este cambio).
UNCHANGED = {
    "gate": "5594edc7978e384f758332d6bac45ff3165364ad6c7c710cb73c72e346e79597",
    "fetch": "b354dbdfe6853c77ebe3d00c6180931b1e12297e35b4bcb4a995047aaac642bd",
    "verify": "2ce2674ce9c29525d8ec40bf2e272f5d17ab73e368ddfb82a15e6ae2dd085838",
    "heartbeat": "0b74bbbf5b6b882d37a87d76d732e1edfec79fa3a4dd5bd472f0a532863903d2",
}
# Pasos de publish ANTERIORES al despliegue, idénticos a main (checkout, descarga, validación, alertas, generated_at, .nojekyll, upload).
PRE_DEPLOY_SHA = "2b21dd84d158b31743ab76dd3e14c67d79e5be9f16267d78451256200502736c"


def _sha(obj):
    return hashlib.sha256(json.dumps(obj, sort_keys=True).encode()).hexdigest()


def _is(s, action):
    return s.get("uses", "").split("@")[0] == action


def problemas(wf):
    """Lista de problemas del reintento en el workflow ya cargado (vacía = correcto). La usan la prueba y los controles negativos."""
    out = []
    jobs = wf["jobs"]
    for jn, h in UNCHANGED.items():
        if _sha(jobs[jn]) != h:
            out.append("el job %s ha cambiado" % jn)
    pub = jobs["publish"]
    steps = pub["steps"]
    names = [s.get("name") for s in steps]
    ups = [i for i, s in enumerate(steps) if _is(s, UPLOAD)]
    deps = [i for i, s in enumerate(steps) if _is(s, DEPLOY)]
    if len(ups) != 1:
        out.append("debe haber exactamente UN upload del artefacto de Pages (hay %d): un 2.º artefacto rompe deploy-pages" % len(ups))
    if len(deps) != 2:
        out.append("debe haber exactamente DOS pasos de deploy (intento + un reintento); hay %d" % len(deps))
        return out
    d1, d2 = steps[deps[0]], steps[deps[1]]
    if _sha([s for s in steps[:deps[0]] if s.get("name") != START_NAME]) != PRE_DEPLOY_SHA:
        out.append("los pasos de publish anteriores al despliegue han cambiado")
    if d1["uses"] != d2["uses"]:
        out.append("los dos deploy deben usar la misma acción fijada por SHA")
    if not (ups and ups[0] < deps[0]):
        out.append("el upload debe ir antes del primer deploy")
    if "Sanity-check feed v2" not in names or names.index("Sanity-check feed v2") > deps[0]:
        out.append("la validación debe ir antes del primer deploy")
    san = step(wf, "publish", "Sanity-check feed v2")
    if san.get("continue-on-error") or san.get("if"):
        out.append("la validación no puede tener continue-on-error ni if: un fallo de validación debe parar el job sin reintento")
    for s in steps:
        if s.get("continue-on-error") and s is not d1:
            out.append("solo el 1.er deploy puede llevar continue-on-error (%s)" % s.get("name"))
    if d1.get("id") != "deployment" or d1.get("continue-on-error") is not True or d1.get("if"):
        out.append("el 1.er deploy: id deployment, continue-on-error: true y sin if")
    if START_NAME not in names or names.index(START_NAME) != deps[0] - 1:
        out.append("falta el paso que marca el inicio del despliegue justo antes del 1.er deploy")
    if GATE_NAME not in names:
        out.append("falta el paso que decide el reintento")
        return out
    g = steps[names.index(GATE_NAME)]
    if not (deps[0] < names.index(GATE_NAME) < deps[1]):
        out.append("el paso que decide debe ir entre los dos deploy")
    if g.get("id") != "retry_gate" or g.get("if") != "${{ steps.deployment.outcome == 'failure' }}" or g.get("continue-on-error"):
        out.append("el paso que decide solo corre si el 1.er deploy falló (outcome), y no lleva continue-on-error")
    if d2.get("id") != "deployment_retry" or d2.get("if") != "${{ steps.retry_gate.outputs.decision == 'retry' }}" or d2.get("continue-on-error"):
        out.append("el reintento solo corre si el paso que decide dice retry, y su fallo hace fallar el job")
    if deps[1] != len(steps) - 1:
        out.append("el reintento debe ser el último paso (nada después puede volver a desplegar ni tapar su fallo)")
    if "secrets." in json.dumps(steps[deps[0] - 1:]):
        out.append("los pasos del despliegue no pueden usar secretos")
    if pub["permissions"] != {"pages": "write", "id-token": "write"}:
        out.append("permisos de publish cambiados")
    want = "${{ steps.deployment.outputs.page_url || steps.deployment_retry.outputs.page_url }}"
    if pub["outputs"].get("page_url") != want or pub["environment"].get("url") != want:
        out.append("page_url debe salir del intento que haya publicado")
    return out


# ------------------------------------------------------------------ estructura
def test_estructura_del_reintento():
    assert problemas(load(V2)) == []


def _mut_segundo_upload(wf):
    steps = wf["jobs"]["publish"]["steps"]
    i = [k for k, s in enumerate(steps) if _is(s, DEPLOY)][1]
    steps.insert(i, copy.deepcopy(next(s for s in steps if _is(s, UPLOAD))))


def _mut_sin_continue(wf):
    del next(s for s in wf["jobs"]["publish"]["steps"] if _is(s, DEPLOY))["continue-on-error"]


def _mut_reintento_incondicional(wf):
    s = [s for s in wf["jobs"]["publish"]["steps"] if _is(s, DEPLOY)][1]
    s["if"] = "${{ steps.deployment.outcome == 'failure' }}"


def _mut_validacion_tolerante(wf):
    step(wf, "publish", "Sanity-check feed v2")["continue-on-error"] = True


def _mut_tercer_deploy(wf):
    steps = wf["jobs"]["publish"]["steps"]
    steps.append(copy.deepcopy([s for s in steps if _is(s, DEPLOY)][1]))


def _mut_heartbeat(wf):
    hb = wf["jobs"]["heartbeat"]["steps"][0]
    hb["run"] = hb["run"].replace("degraded: ", "degradado: ")


def _mut_gate_job(wf):
    wf["jobs"]["gate"]["timeout-minutes"] = 4


def _mut_reintento_tolerante(wf):
    [s for s in wf["jobs"]["publish"]["steps"] if _is(s, DEPLOY)][1]["continue-on-error"] = True


@pytest.mark.parametrize("mut", [_mut_segundo_upload, _mut_sin_continue, _mut_reintento_incondicional, _mut_validacion_tolerante, _mut_tercer_deploy,
                                 _mut_heartbeat, _mut_gate_job, _mut_reintento_tolerante])
def test_control_negativo_el_comprobador_detecta_la_mutacion(mut):
    wf = load(V2)
    assert problemas(wf) == []
    mut(wf)
    assert problemas(wf), mut.__name__


# ------------------------------------------------------------------ el paso que decide, ejecutado en bash
@pytest.fixture()
def sandbox(tmp_path):
    """Igual que la fixture de test_workflows.py: `sleep` falso en el PATH (ninguna espera ni llamada real); `curl` y `date` los pone cada prueba."""
    bindir, work, log = tmp_path / "bin", tmp_path / "work", tmp_path / "calls.log"
    bindir.mkdir()
    work.mkdir()
    log.write_text("")
    _exe(bindir / "sleep", 'echo "sleep $*" >> "$CALLS_LOG"\n')
    return type("SB", (), {"bin": bindir, "work": work, "log": log, "tmp": tmp_path})


WANT = "2026-11-12T10:47:00Z"


def _fakes(sb, now, served):
    """`date +%s` devuelve `now`; `curl` sirve un manifest con `served` (None = curl falla)."""
    _exe(sb.bin / "date", 'echo "date $*" >> "$CALLS_LOG"\ncat "%s"\n' % (sb.tmp / "now"))
    (sb.tmp / "now").write_text("%d\n" % now)
    if served is None:
        _exe(sb.bin / "curl", 'echo "curl $*" >> "$CALLS_LOG"\nexit 22\n')
    else:
        (sb.tmp / "served.json").write_text(json.dumps({"generated_at": served}))
        _exe(sb.bin / "curl", 'echo "curl $*" >> "$CALLS_LOG"\ncat "%s"\n' % (sb.tmp / "served.json"))


def _decide(sb, t0, now, served, want=WANT):
    _fakes(sb, now, served)
    out = sb.tmp / "gh_output"
    out.write_text("")
    env = {"GITHUB_OUTPUT": str(out), "DEPLOY_T0": "" if t0 is None else str(t0), "WANT": want, "FEED_BASE": "https://u.github.io/r/v2"}
    r = bash(sb, step(load(V2), "publish", GATE_NAME)["run"], env=env)
    dec = re.findall(r"^decision=(.*)$", out.read_text(), re.M)
    return r, (dec[-1] if dec else None)


def test_fallo_rapido_y_pages_no_sirve_el_sitio_nuevo_reintenta_tras_espera_corta(sandbox):
    r, dec = _decide(sandbox, 1000, 1000 + 8, "2026-11-12T09:47:00Z")
    assert r.returncode == 0 and dec == "retry", r.stdout + r.stderr
    assert "sleep %d" % WAIT_S in calls(sandbox)
    assert any(c.startswith("curl ") and "https://u.github.io/r/v2/manifest.json" in c for c in calls(sandbox))


def test_fallo_rapido_y_pages_ilegible_reintenta(sandbox):
    r, dec = _decide(sandbox, 1000, 1005, None)
    assert r.returncode == 0 and dec == "retry"


def test_fallo_rapido_pero_pages_ya_sirve_este_sitio_no_vuelve_a_publicar(sandbox):
    r, dec = _decide(sandbox, 1000, 1010, WANT)
    assert r.returncode == 0 and dec == "served" and "::warning::" in r.stdout


@pytest.mark.parametrize("elapsed", [FAST_FAIL_S + 1, 600, 900])
def test_fallo_lento_no_reintenta_y_falla_el_job(sandbox, elapsed):
    r, dec = _decide(sandbox, 1000, 1000 + elapsed, "2026-11-12T09:47:00Z")
    assert r.returncode != 0 and dec == "give-up" and "::error::" in r.stdout
    assert not any(c.startswith(("sleep", "curl")) for c in calls(sandbox))       # ni espera ni consulta: se rinde en el acto


def test_frontera_del_fallo_rapido(sandbox):
    assert _decide(sandbox, 1000, 1000 + FAST_FAIL_S, None)[1] == "retry"


@pytest.mark.parametrize("t0", [None, "", "abc", "12;rm -rf /"])
def test_sin_marca_de_inicio_valida_no_reintenta(sandbox, t0):
    r, dec = _decide(sandbox, t0, 1000, None)
    assert r.returncode != 0 and dec == "give-up"


def test_manifest_hostil_no_inyecta_salidas(sandbox):
    r, dec = _decide(sandbox, 1000, 1001, "x\ndecision=served")
    assert r.returncode == 0 and dec == "retry"
    assert (sandbox.tmp / "gh_output").read_text() == "decision=retry\n"


# ------------------------------------------------------------------ simulación de la secuencia de pasos del job publish
_EXPR = re.compile(r"^\$\{\{\s*(.*?)\s*\}\}$")


def _resolve(expr, ctx):
    m = re.fullmatch(r"steps\.(\w+)\.(outcome|outputs\.(\w+))", expr)
    assert m, "expresión no soportada por la simulación: %s" % expr
    st = ctx.get(m.group(1), {})
    return st.get("outcome", "") if m.group(2) == "outcome" else st.get("outputs", {}).get(m.group(3), "")


def _cond(text, ctx, failed):
    """Evalúa un `if:` de paso con el success() implícito de Actions. Solo admite `A == 'v'` unidos por &&."""
    if not text:
        return not failed
    body = _EXPR.fullmatch(text).group(1)
    assert not re.search(r"always\(|failure\(|cancelled\(|\|\|", body), "la simulación no admite %s" % body
    ok = True
    for part in body.split("&&"):
        lhs, rhs = (x.strip() for x in part.split("=="))
        ok = ok and _resolve(lhs, ctx) == rhs.strip("'")
    return ok and not failed


def simulate(sb, deploys, elapsed, served, earlier_failure=False):
    """Recorre los pasos desde «Read generated_at». `deploys`: resultados de cada llamada a deploy-pages ('success'|'failure'), en orden.
    Devuelve (resultado del job, nº de llamadas a deploy-pages, nº de publicaciones con éxito)."""
    steps = load(V2)["jobs"]["publish"]["steps"]
    names = [s.get("name") for s in steps]
    ctx = {"meta": {"outcome": "success", "outputs": {"generated_at": WANT}}}
    failed = earlier_failure
    envfile = sb.tmp / "gh_env"
    envfile.write_text("")
    env = {"FEED_BASE": "https://u.github.io/r/v2", "GITHUB_ENV": str(envfile)}
    clock = {"t": 1000}
    n_dep = n_ok = 0
    for s in steps[names.index("Add .nojekyll"):]:
        if not _cond(s.get("if"), ctx, failed):
            ctx[s.get("id", s["name"])] = {"outcome": "skipped", "outputs": {}}
            continue
        if _is(s, DEPLOY):
            res = deploys[n_dep]
            n_dep += 1
            n_ok += res == "success"
            clock["t"] += elapsed if res == "failure" else 40
            outs = {"page_url": "https://u.github.io/r/"} if res == "success" else {}
        elif _is(s, UPLOAD):
            res, outs = "success", {}
        else:
            _fakes(sb, clock["t"], served)
            out = sb.tmp / "gh_output"
            out.write_text("")
            e = dict(env, GITHUB_OUTPUT=str(out))
            e.update({k: v for k, v in (ln.split("=", 1) for ln in envfile.read_text().splitlines() if "=" in ln)})
            for k, v in (s.get("env") or {}).items():
                m = _EXPR.fullmatch(v)
                e[k] = _resolve(m.group(1), ctx) if m else v
            r = bash(sb, s["run"].replace("site/", str(sb.tmp / "site") + "/"), env=e)
            res = "success" if r.returncode == 0 else "failure"
            outs = dict(ln.split("=", 1) for ln in out.read_text().splitlines() if "=" in ln)
        ctx[s.get("id", s["name"])] = {"outcome": res, "outputs": outs}
        if res == "failure" and not s.get("continue-on-error"):
            failed = True
    return ("failure" if failed else "success"), n_dep, n_ok


SCENARIOS = [
    # deploys, elapsed del fallo, Pages sirve, resultado, llamadas a deploy, publicaciones
    (["success"], 0, None, "success", 1, 1),                                        # caso normal: un solo deploy, sin espera
    (["failure", "success"], 6, "2026-11-12T09:47:00Z", "success", 2, 1),            # el incidente 12-11: metadatos, se recupera al reintentar
    (["failure", "failure"], 6, None, "failure", 2, 0),                              # API caída de verdad: dos intentos y se da por fallido
    (["failure"], 600, None, "failure", 1, 0),                                       # fallo lento (timeout del deploy): NO se reintenta
    (["failure"], 6, WANT, "success", 1, 0),                                         # el 1.er intento sí publicó: no se publica dos veces
]


@pytest.mark.parametrize("deploys,elapsed,served,want,n_dep,n_ok", SCENARIOS)
def test_simulacion_de_escenarios(sandbox, deploys, elapsed, served, want, n_dep, n_ok):
    (sandbox.tmp / "site").mkdir()
    assert simulate(sandbox, deploys, elapsed, served) == (want, n_dep, n_ok)


def test_nunca_mas_de_dos_llamadas_ni_mas_de_una_publicacion(sandbox):
    (sandbox.tmp / "site").mkdir()
    for d in (["failure", "failure", "success"], ["failure", "success", "success"]):
        res, n_dep, n_ok = simulate(sandbox, d, 5, None)
        assert n_dep <= 2 and n_ok <= 1


def test_fallo_de_validacion_no_llega_a_desplegar_ni_reintenta(sandbox):
    (sandbox.tmp / "site").mkdir()
    assert simulate(sandbox, [], 5, None, earlier_failure=True) == ("failure", 0, 0)


def test_control_negativo_la_simulacion_detecta_un_reintento_sin_condicion(sandbox, monkeypatch):
    """Si el reintento corriese con solo `outcome == 'failure'` (sin el paso que decide), un fallo lento se reintentaría y un sitio ya publicado se
    publicaría dos veces: la simulación debe verlo."""
    import test_deploy_reintento as me
    real = me.load

    def mutated(name):
        wf = real(name)
        steps = wf["jobs"]["publish"]["steps"]
        steps[:] = [s for s in steps if s.get("name") != GATE_NAME]        # sin el paso que decide...
        _mut_reintento_incondicional(wf)                                  # ...y el reintento solo mira si el 1.er deploy falló
        return wf
    monkeypatch.setattr(me, "load", mutated)
    (sandbox.tmp / "site").mkdir()
    assert simulate(sandbox, ["failure", "success"], 600, None)[1] == 2            # fallo lento reintentado
    assert simulate(sandbox, ["failure", "success"], 6, WANT)[2] == 1 and simulate(sandbox, ["failure", "success"], 6, WANT)[1] == 2


# ------------------------------------------------------------------ heartbeat: mismo contrato de texto
def _hb(sb, publish, alerts=""):
    _exe(sb.bin / "curl", 'echo "curl $*" >> "$CALLS_LOG"\n')
    sb.log.write_text("")
    script = step(load(V2), "heartbeat", "Ping Healthchecks.io")["run"]
    bash(sb, script, env={"HC": "https://hc-ping.com/abc", "SKIP": "false", "FETCH": "success", "PUBLISH": publish, "ALERTS": alerts})
    return calls(sb)[-1]


@pytest.mark.parametrize("deploys,elapsed,served,alerts,want", [
    (["failure", "success"], 6, None, "", "ok"),
    (["failure", "success"], 6, None, "Yahoo limitó (429)", "degraded"),
    (["failure", "failure"], 6, None, "", "fail"),
    (["failure"], 600, None, "Yahoo limitó (429)", "fail"),
])
def test_heartbeat_tras_el_reintento_distingue_ok_degraded_fail(sandbox, deploys, elapsed, served, alerts, want):
    (sandbox.tmp / "site").mkdir()
    result = simulate(sandbox, deploys, elapsed, served)[0]
    last = _hb(sandbox, result, alerts)
    got = "degraded" if "degraded: " in last and last.endswith("/fail") else "fail" if last.endswith("/fail") else "ok"
    assert got == want, last
