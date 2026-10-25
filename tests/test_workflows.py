"""Pruebas de los workflows: estructura (needs, permisos, orden) y ejecución REAL en bash de los bloques `run:`
críticos con `git`/`curl`/`sleep` falsos en el PATH. No sustituyen a una ejecución real en Actions (ver CAMBIOS.md)."""
import datetime
import json
import os
import pathlib
import re
import shutil
import stat
import subprocess
import sys

import pytest

# Sin PyYAML estos tests (los de los workflows) se saltaban EN SILENCIO y la CI salía verde sin comprobarlos (BE-16): en CI es un fallo.
try:
    import yaml
except ImportError:
    if os.environ.get("CI"):
        pytest.fail("PyYAML no está instalado en CI: los tests de los workflows no se pueden ejecutar (requirements-dev.txt)", pytrace=False)
    pytest.skip("PyYAML no instalado: se omiten los tests de los workflows", allow_module_level=True)

ROOT = pathlib.Path(__file__).resolve().parent.parent
WF = ROOT / ".github" / "workflows"
REAL_GIT = shutil.which("git")


def load(name):
    return yaml.safe_load((WF / name).read_text(encoding="utf-8"))


def step(wf, job, name):
    for s in wf["jobs"][job]["steps"]:
        if s.get("name") == name:
            return s
    raise KeyError(name)


# ------------------------------------------------------------------ estructura
def test_update_data_estructura_de_jobs_y_permisos():
    wf = load("update-data.yml")
    jobs = wf["jobs"]
    assert set(jobs) == {"fetch", "publish", "verify-cdn", "heartbeat"}
    assert wf["permissions"] == {"contents": "read"}
    assert jobs["fetch"]["permissions"] == {"contents": "read"}
    assert jobs["publish"]["permissions"] == {"contents": "write"}
    assert jobs["verify-cdn"]["permissions"] == {} and jobs["heartbeat"]["permissions"] == {}
    # el heartbeat solo depende de fetch y publish; verify-cdn no bloquea nada y no marca el run como fallido
    assert set(jobs["heartbeat"]["needs"]) == {"fetch", "publish"}
    assert jobs["verify-cdn"]["needs"] == "publish" and jobs["verify-cdn"]["continue-on-error"] is True
    assert all("verify-cdn" not in (j.get("needs") if isinstance(j.get("needs"), list) else [j.get("needs")])
               for n, j in jobs.items() if n != "verify-cdn")
    assert jobs["publish"]["needs"] == "fetch"


def test_sanity_check_esta_en_publish_antes_del_push_y_no_en_fetch():
    jobs = load("update-data.yml")["jobs"]
    fetch_text = json.dumps(jobs["fetch"])
    assert "sanity_check" not in fetch_text
    names = [s.get("name") for s in jobs["publish"]["steps"]]
    assert names.index("Sanity-check feed") < names.index("Publish to data branch")
    assert names.index("Check out scripts/ only") < names.index("Sanity-check feed")
    co = step(load("update-data.yml"), "publish", "Check out scripts/ only")
    assert co["with"]["sparse-checkout"] == "scripts" and co["with"]["persist-credentials"] is False
    run = step(load("update-data.yml"), "publish", "Sanity-check feed")["run"]
    assert "scripts/sanity_check.py" in run and "--prev prev.json" in run


def test_ningun_run_interpola_expresiones_y_las_acciones_van_fijadas_por_sha():
    for f in WF.glob("*.yml"):
        wf = yaml.safe_load(f.read_text(encoding="utf-8"))
        for jn, job in wf["jobs"].items():
            for s in job["steps"]:
                assert "${{" not in s.get("run", ""), "%s/%s interpola una expresión en run:" % (f.name, jn)
                if "uses" in s:
                    assert re.fullmatch(r"[\w.-]+/[\w.-]+@[0-9a-f]{40}", s["uses"]), s["uses"]


def test_keepalive_permisos_a_nivel_de_job():
    wf = load("keepalive.yml")
    assert wf["permissions"] == {}
    assert wf["jobs"]["keepalive"]["permissions"] == {"contents": "write", "actions": "write"}
    run = step(wf, "keepalive", "Commit de actividad si hace >30 días del último")
    assert run["env"]["BRANCH"] == "${{ github.event.repository.default_branch }}"
    assert 'git push origin "HEAD:refs/heads/${BRANCH}"' in run["run"]


def test_ci_ejecuta_ruff_pytest_y_actionlint():
    text = (WF / "ci.yml").read_text(encoding="utf-8")
    assert "ruff check --select F" in text and "pytest" in text and "actionlint" in text


def test_ci_tambien_corre_en_push_a_main():
    # BE-02: la mayoría de commits entran directos a main por la web; sin `push` la CI no los vigilaba.
    wf = load("ci.yml")
    triggers = wf.get("on", wf.get(True))            # PyYAML (YAML 1.1) interpreta `on` como True
    assert "pull_request" in triggers and "workflow_dispatch" in triggers
    assert triggers["push"]["branches"] == ["main"]


# ------------------------------------------------------------------ ejecución en bash con comandos falsos
def _exe(path, body):
    path.write_text("#!/bin/bash\n" + body)
    path.chmod(path.stat().st_mode | stat.S_IEXEC)


@pytest.fixture()
def sandbox(tmp_path):
    bindir = tmp_path / "bin"
    bindir.mkdir()
    work = tmp_path / "work"
    work.mkdir()
    log = tmp_path / "calls.log"
    log.write_text("")
    _exe(bindir / "sleep", 'echo "sleep $*" >> "$CALLS_LOG"\n')
    return type("SB", (), {"bin": bindir, "work": work, "log": log, "tmp": tmp_path})


def bash(sb, script, env=None, cwd=None):
    e = {"PATH": "%s:%s" % (sb.bin, os.environ["PATH"]), "CALLS_LOG": str(sb.log), "HOME": str(sb.tmp),
         "GITHUB_REPOSITORY": "usuario/repo"}
    e.update(env or {})
    f = sb.tmp / "script.sh"
    f.write_text(script)
    return subprocess.run(["bash", "-e", str(f)], cwd=cwd or sb.work, env=e, capture_output=True, text=True, timeout=60)


def calls(sb):
    return sb.log.read_text().splitlines()


# ---- publish: reintentos del force-push
def _publish_script(sb):
    s = step(load("update-data.yml"), "publish", "Publish to data branch")["run"]
    return s.replace("/tmp/publish", str(sb.tmp / "publish"))   # no escribir fuera del sandbox


def _fake_git(sb, fail_first):
    _exe(sb.bin / "git", '''if [ "$1" = push ]; then
  echo "push" >> "$CALLS_LOG"
  n=$(grep -c '^push$' "$CALLS_LOG")
  if [ "$n" -le %d ]; then echo "fatal: unable to access" >&2; exit 1; fi
  exit 0
fi
exec %s "$@"
''' % (fail_first, REAL_GIT))


def _feed_files(sb):
    (sb.work / "feed").mkdir()
    (sb.work / "feed" / "data.json").write_text("{}")
    (sb.work / "feed" / "meta.json").write_text("{}")


@pytest.mark.parametrize("fail_first,rc,pushes", [(0, 0, 1), (2, 0, 3), (3, 1, 3)])
def test_publish_reintenta_el_force_push_3_veces(sandbox, fail_first, rc, pushes):
    _fake_git(sandbox, fail_first)
    _feed_files(sandbox)
    r = bash(sandbox, _publish_script(sandbox), env={"GH_TOKEN": "tok"})
    assert r.returncode == rc, r.stdout + r.stderr
    assert calls(sandbox).count("push") == pushes
    assert calls(sandbox).count("sleep 5") == min(fail_first, 2)   # no duerme tras el último intento
    if rc:
        assert "no se pudo publicar" in r.stdout
    # el commit se hizo y solo contiene los dos ficheros del feed
    out = subprocess.run([REAL_GIT, "-C", str(sandbox.tmp / "publish"), "ls-files"], capture_output=True, text=True).stdout.split()
    assert sorted(out) == ["data.json", "meta.json"]


# ---- publish: salida generated_at validada
def test_read_generated_at_valida_el_formato(sandbox):
    script = step(load("update-data.yml"), "publish", "Read generated_at")["run"]
    _feed_files(sandbox)
    out = sandbox.tmp / "gh_output"
    (sandbox.work / "feed" / "meta.json").write_text(json.dumps({"generated_at": "2026-10-06T06:30:00.123+00:00"}))
    r = bash(sandbox, script, env={"GITHUB_OUTPUT": str(out)})
    assert r.returncode == 0 and out.read_text() == "generated_at=2026-10-06T06:30:00.123+00:00\n"
    (sandbox.work / "feed" / "meta.json").write_text(json.dumps({"generated_at": "x\nevil=1"}))
    out.write_text("")
    r = bash(sandbox, script, env={"GITHUB_OUTPUT": str(out)})
    assert r.returncode != 0 and out.read_text() == ""


# ---- verify-cdn
def _verify_script():
    return step(load("update-data.yml"), "verify-cdn", "Purge jsDelivr CDN cache and verify")["run"]


DATA_BYTES = 1234


def _fake_curl_cdn(sb, mode, want="2026-10-06T06:30:00+00:00"):
    # meta.json y data.json se piden por separado: `fresh` = ambos nuevos; `meta_nuevo_data_viejo` = el borde sirve el meta nuevo
    # con el data.json antiguo (otro tamaño), el caso de BE-11.
    _exe(sb.bin / "curl", '''echo "curl $*" >> "$CALLS_LOG"
case "$*" in
  *purge.jsdelivr.net*) echo '{"status":"finished"}'; exit 0;;
  *cdn.jsdelivr.net*data.json*)
    case "%s" in
      fresh) head -c %d /dev/zero | tr '\\0' x; exit 0;;
      meta_nuevo_data_viejo) head -c 999 /dev/zero | tr '\\0' x; exit 0;;
      down) exit 22;;
      *) head -c 5 /dev/zero | tr '\\0' x; exit 0;;
    esac;;
  *cdn.jsdelivr.net*)
    case "%s" in
      fresh|meta_nuevo_data_viejo) echo '{"generated_at":"%s","data_bytes":%d}'; exit 0;;
      stale) echo '{"generated_at":"2000-01-01T00:00:00+00:00","data_bytes":1}'; exit 0;;
      down) exit 22;;
    esac;;
  */log*) exit ${HC_LOG_RC:-0};;
esac
''' % (mode, DATA_BYTES, mode, want, DATA_BYTES))


WANT = "2026-10-06T06:30:00+00:00"


def test_verify_cdn_fresco_sale_sin_dormir(sandbox):
    _fake_curl_cdn(sandbox, "fresh")
    r = bash(sandbox, _verify_script(), env={"WANT": WANT, "HC": "https://hc.test/ping/abc"})
    assert r.returncode == 0 and "CDN sirve la versión nueva" in r.stdout
    assert not any(c.startswith("sleep") for c in calls(sandbox))
    assert not any("/log" in c for c in calls(sandbox))


def test_verify_cdn_comprueba_tambien_data_json(sandbox):
    _fake_curl_cdn(sandbox, "fresh")
    r = bash(sandbox, _verify_script(), env={"WANT": WANT, "HC": ""})
    assert r.returncode == 0 and "CDN sirve la versión nueva" in r.stdout
    cdn = [c for c in calls(sandbox) if "cdn.jsdelivr.net" in c]
    assert any("data.json" in c for c in cdn) and any("meta.json" in c for c in cdn)
    assert any("data.json" in c and "--compressed" in c for c in cdn)      # el tamaño real, descomprimido, como data_bytes


def test_verify_cdn_meta_nuevo_con_data_viejo_no_da_el_cdn_por_bueno(sandbox):   # BE-11
    _fake_curl_cdn(sandbox, "meta_nuevo_data_viejo")
    r = bash(sandbox, _verify_script(), env={"WANT": WANT, "HC": "https://hc.test/ping/abc/"})
    assert r.returncode == 0, r.stdout + r.stderr                          # informativo: nunca falla el run
    assert "CDN sirve la versión nueva" not in r.stdout
    assert "::warning::" in r.stdout and "data.json" in r.stdout
    logs = [c for c in calls(sandbox) if "/log" in c]
    assert len(logs) == 1 and "cdn-stale" in logs[0]
    assert "/fail" not in "".join(calls(sandbox))


@pytest.mark.parametrize("mode", ["stale", "down"])
def test_verify_cdn_desfasado_avisa_pero_no_falla_y_no_duerme_tras_el_ultimo_intento(sandbox, mode):
    _fake_curl_cdn(sandbox, mode)
    r = bash(sandbox, _verify_script(), env={"WANT": WANT, "HC": "https://hc.test/ping/abc/"})
    assert r.returncode == 0, r.stdout + r.stderr
    assert "::warning::jsDelivr sigue sirviendo" in r.stdout
    sleeps = [c for c in calls(sandbox) if c.startswith("sleep")]
    assert sleeps == ["sleep 20", "sleep 40", "sleep 60", "sleep 80", "sleep 100"]  # 6 intentos, 5 esperas (300 s)
    logs = [c for c in calls(sandbox) if "/log" in c]
    assert len(logs) == 1 and "https://hc.test/ping/abc/log" in logs[0] and "cdn-stale" in logs[0]
    assert "/fail" not in "".join(calls(sandbox))   # informativo: nunca marca fallo


def test_verify_cdn_sin_hc_url_solo_warning(sandbox):
    _fake_curl_cdn(sandbox, "stale")
    r = bash(sandbox, _verify_script(), env={"WANT": WANT, "HC": ""})
    assert r.returncode == 0 and "::warning::" in r.stdout
    assert not any("/log" in c for c in calls(sandbox))


def test_verify_cdn_fallo_del_ping_log_no_hace_fallar(sandbox):
    _fake_curl_cdn(sandbox, "stale")
    r = bash(sandbox, _verify_script(), env={"WANT": WANT, "HC": "https://hc.test/x", "HC_LOG_RC": "22"})
    assert r.returncode == 0 and "no se pudo enviar el aviso" in r.stdout


def test_verify_cdn_sin_want_no_hace_nada(sandbox):
    _fake_curl_cdn(sandbox, "stale")
    r = bash(sandbox, _verify_script(), env={"WANT": "", "HC": ""})
    assert r.returncode == 0 and calls(sandbox) == []


def test_verify_cdn_contenido_hostil_del_cdn_no_inyecta_comandos(sandbox):
    _exe(sandbox.bin / "curl", '''echo "curl $*" >> "$CALLS_LOG"
case "$*" in *cdn.jsdelivr.net*) printf '{"generated_at":"x\\\\n::add-mask::secreto"}';; *) echo ok;; esac
''')
    r = bash(sandbox, _verify_script(), env={"WANT": WANT, "HC": ""})
    assert r.returncode == 0
    assert not any(line.startswith("::add-mask") for line in r.stdout.splitlines())


# ---- heartbeat
def _hb(sb, hc, fetch, publish):
    _exe(sb.bin / "curl", 'echo "curl $*" >> "$CALLS_LOG"\n')
    script = step(load("update-data.yml"), "heartbeat", "Ping Healthchecks.io")["run"]
    return bash(sb, script, env={"HC": hc, "FETCH": fetch, "PUBLISH": publish})


def test_heartbeat_ok_y_fail(sandbox):
    r = _hb(sandbox, "https://hc.test/ping/abc/", "success", "success")
    assert r.returncode == 0 and calls(sandbox)[-1].endswith("https://hc.test/ping/abc")
    for f, p in (("success", "failure"), ("failure", "skipped"), ("cancelled", "skipped"), ("success", "skipped")):
        sandbox.log.write_text("")
        _hb(sandbox, "https://hc.test/ping/abc", f, p)
        assert calls(sandbox)[-1].endswith("https://hc.test/ping/abc/fail"), (f, p)


def test_heartbeat_sin_url_avisa(sandbox):
    r = _hb(sandbox, "", "success", "success")
    assert r.returncode == 0 and "::warning::HC_UPDATE_URL" in r.stdout and calls(sandbox) == []


# ---- keepalive
def _git(*a, cwd, env=None):
    e = dict(os.environ, GIT_CONFIG_GLOBAL="/dev/null", GIT_CONFIG_SYSTEM="/dev/null", **(env or {}))
    return subprocess.run([REAL_GIT, *a], cwd=cwd, capture_output=True, text=True, check=True, env=e).stdout.strip()


def _repo_with_remote(tmp, age_days, branch="main"):
    remote, work = tmp / "origin.git", tmp / "clone"
    _git("init", "-q", "--bare", "-b", branch, str(remote), cwd=tmp)
    work.mkdir()
    _git("init", "-q", "-b", branch, cwd=work)
    _git("remote", "add", "origin", str(remote), cwd=work)
    (work / "README.md").write_text("x")
    _git("add", "-A", cwd=work)
    when = (datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(days=age_days)).strftime("%Y-%m-%dT%H:%M:%S+0000")
    _git("-c", "user.name=t", "-c", "user.email=t@t", "commit", "-q", "-m", "viejo", cwd=work,
         env={"GIT_AUTHOR_DATE": when, "GIT_COMMITTER_DATE": when})
    _git("push", "-q", "-u", "origin", branch, cwd=work)
    return remote, work


def _keepalive_script():
    return step(load("keepalive.yml"), "keepalive", "Commit de actividad si hace >30 días del último")["run"]


@pytest.mark.parametrize("branch_env", ["main", ""])   # "" = el evento no trae default_branch
def test_keepalive_commit_si_hay_mas_de_30_dias(sandbox, branch_env):
    remote, work = _repo_with_remote(sandbox.tmp, 66)
    before = _git("rev-parse", "main", cwd=remote)
    r = bash(sandbox, _keepalive_script(), env={"BRANCH": branch_env, "GIT_CONFIG_GLOBAL": "/dev/null"}, cwd=work)
    assert r.returncode == 0, r.stdout + r.stderr
    after = _git("rev-parse", "main", cwd=remote)
    assert after != before
    assert "keepalive" in _git("log", "-1", "--format=%s", "main", cwd=remote)
    assert _git("show", "main:.github/keepalive", cwd=remote).endswith("Z")


def test_keepalive_sin_commit_si_hay_actividad_reciente(sandbox):
    remote, work = _repo_with_remote(sandbox.tmp, 3)
    before = _git("rev-parse", "main", cwd=remote)
    r = bash(sandbox, _keepalive_script(), env={"BRANCH": "main"}, cwd=work)
    assert r.returncode == 0 and "no hace falta commit" in r.stdout
    assert _git("rev-parse", "main", cwd=remote) == before


def test_keepalive_en_rama_distinta_de_main(sandbox):
    remote, work = _repo_with_remote(sandbox.tmp, 40, branch="trunk")
    before = _git("rev-parse", "trunk", cwd=remote)
    r = bash(sandbox, _keepalive_script(), env={"BRANCH": "trunk"}, cwd=work)
    assert r.returncode == 0 and _git("rev-parse", "trunk", cwd=remote) != before


def test_keepalive_con_head_desacoplado_y_sin_rama_falla_claro(sandbox):
    remote, work = _repo_with_remote(sandbox.tmp, 66)
    _git("checkout", "-q", "--detach", cwd=work)
    r = bash(sandbox, _keepalive_script(), env={"BRANCH": ""}, cwd=work)
    assert r.returncode != 0 and "no se pudo determinar la rama" in r.stdout


# ---- BE-06: descarga del feed anterior sin desactivar en silencio las reglas de regresión
def _fake_curl_prev(sb, mode):
    _exe(sb.bin / "curl", '''echo "curl $*" >> "$CALLS_LOG"
out=""
while [ $# -gt 0 ]; do
  if [ "$1" = "-o" ]; then out="$2"; shift; fi
  shift
done
case "%s" in
  ok)  echo '{"indices":[{"symbol":"^GSPC"}]}' > "$out"; printf 200;;
  nf)  echo 'Not Found' > "$out"; printf 404;;
  s500) echo 'oops' > "$out"; printf 500;;
  down) exit 6;;
esac
''' % mode)


def _run_prev_step(sb, job, name, mode):
    _fake_curl_prev(sb, mode)
    genv = sb.tmp / "github_env"
    genv.write_text("")
    r = bash(sb, step(load("update-data.yml"), job, name)["run"], env={"GITHUB_ENV": str(genv)})
    prev = (sb.work / "prev.json").read_text()
    return r, prev, genv.read_text()


@pytest.mark.parametrize("job,name", [("publish", "Download previous feed"),
                                      ("fetch", "Download previous feed (for FRED fallback)")])
def test_BE06_descarga_ok_espera_feed_anterior(sandbox, job, name):
    r, prev, genv = _run_prev_step(sandbox, job, name, "ok")
    assert r.returncode == 0 and "^GSPC" in prev and "EXPECT_PREV=true" in genv
    assert "--retry-all-errors" in " ".join(calls(sandbox))     # --retry solo no cubre los fallos de conexión


@pytest.mark.parametrize("job,name", [("publish", "Download previous feed"),
                                      ("fetch", "Download previous feed (for FRED fallback)")])
def test_BE06_404_es_primer_run_legitimo(sandbox, job, name):
    r, prev, genv = _run_prev_step(sandbox, job, name, "nf")
    assert r.returncode == 0 and prev.strip() == "{}" and "EXPECT_PREV" not in genv


@pytest.mark.parametrize("mode", ["s500", "down"])
@pytest.mark.parametrize("job,name", [("publish", "Download previous feed"),
                                      ("fetch", "Download previous feed (for FRED fallback)")])
def test_BE06_cualquier_otro_fallo_no_es_primer_run(sandbox, job, name, mode):
    r, prev, genv = _run_prev_step(sandbox, job, name, mode)
    assert r.returncode == 0 and prev.strip() == "{}"           # el paso no tira el job: lo decide el sanity-check
    assert "EXPECT_PREV=true" in genv                           # ...que ya sabe que faltaba un anterior
    assert "::warning::" in r.stdout


def test_BE06_el_sanity_check_recibe_expect_prev():
    run = step(load("update-data.yml"), "publish", "Sanity-check feed")
    assert "EXPECT_PREV" in run["env"] or "EXPECT_PREV" in run["run"]
    assert "|| echo '{}'" not in (WF / "update-data.yml").read_text(encoding="utf-8")


# ------------------------------------------------------------------ BE-07: lockfile con hashes en el job con secreto
def _lock_pins():
    txt = (ROOT / "requirements.txt").read_text(encoding="utf-8")
    blocks = re.split(r"\n(?=[A-Za-z0-9_.\-]+==)", txt.strip())
    return {re.match(r"([A-Za-z0-9_.\-]+)==", b).group(1).lower().replace("_", "-"): b for b in blocks if re.match(r"[A-Za-z0-9_.\-]+==", b)}


def test_BE07_fetch_instala_solo_lo_fijado_con_hashes():
    run = step(load("update-data.yml"), "fetch", "Install dependencies (hash-pinned)")["run"]
    assert "--require-hashes" in run and "--no-deps" in run and "-r requirements.txt" in run
    fetch_text = json.dumps(load("update-data.yml")["jobs"]["fetch"])
    assert not re.search(r"pip install(?![^\"]*--require-hashes)", fetch_text)       # ninguna otra instalación sin hashes en ese job


def test_BE07_requirements_txt_tiene_hash_en_cada_paquete_y_cubre_requirements_in():
    pins = _lock_pins()
    assert len(pins) >= 10
    for name, block in pins.items():
        assert re.search(r"--hash=sha256:[0-9a-f]{64}", block), name
    directas = [l for l in (ROOT / "requirements.in").read_text(encoding="utf-8").splitlines() if l.strip() and not l.lstrip().startswith("#")]
    assert {"yfinance", "requests", "feedparser"} <= {re.split(r"[<>=!~ ]", l)[0].lower() for l in directas}
    for l in directas:
        m = re.match(r"([A-Za-z0-9_.\-]+)>=([\d.]+),<([\d.]+)", l)
        assert m, l
        v = re.search(re.escape(m.group(1).lower()) + r"==([\d.]+)", "\n".join(b.split("\\")[0].lower() for b in pins.values()))
        assert v, m.group(1)
        t = lambda s: tuple(int(x) for x in s.split("."))        # noqa: E731
        assert t(m.group(2)) <= t(v.group(1)) < t(m.group(3)), l   # el pin respeta el rango declarado


def test_BE07_la_ci_no_mezcla_herramientas_sin_hash_con_requirements_txt():
    # con hashes en requirements.txt, pip exige hash en TODO lo que se instala en esa misma orden: las herramientas van aparte.
    run = step(load("ci.yml"), "checks", "Install dependencies")["run"]
    lineas = [l.strip() for l in run.splitlines() if l.strip().startswith("pip install")]
    con_req = [l for l in lineas if "requirements.txt" in l]
    assert con_req and all("--require-hashes" in l and "pytest" not in l and "ruff" not in l for l in con_req)


# ------------------------------------------------------------------ BE-16: herramientas de CI visibles para Dependabot y PyYAML obligatorio
def test_BE16_las_herramientas_de_ci_estan_en_requirements_dev_con_hashes():
    text = (WF / "ci.yml").read_text(encoding="utf-8")
    assert not re.search(r"pip install[^\n]*\"(pytest|ruff|PyYAML)==", text)             # nada fijado a mano dentro del workflow
    run = step(load("ci.yml"), "checks", "Install dependencies")["run"]
    assert "--require-hashes" in run and "-r requirements-dev.txt" in run
    dev = (ROOT / "requirements-dev.txt").read_text(encoding="utf-8")
    ini = (ROOT / "requirements-dev.in").read_text(encoding="utf-8")
    for tool in ("pytest", "ruff", "PyYAML"):
        assert re.search(r"^%s==[\d.]+" % tool, ini, re.M | re.I), tool
        assert re.search(r"^%s==[\d.]+ \\\n\s+--hash=sha256:[0-9a-f]{64}" % tool, dev, re.M | re.I), tool


def _pytest_sin_yaml(tmp_path, ci):
    stub = tmp_path / "stub"
    stub.mkdir()
    (stub / "yaml.py").write_text("raise ModuleNotFoundError(\"No module named 'yaml'\", name='yaml')\n")
    env = {k: v for k, v in os.environ.items() if k != "CI"}
    env["PYTHONPATH"] = str(stub)
    if ci:
        env["CI"] = "true"
    return subprocess.run([sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider", str(ROOT / "tests" / "test_workflows.py")],
                          cwd=ROOT, env=env, capture_output=True, text=True, timeout=120)


def test_BE16_sin_pyyaml_en_ci_los_tests_de_workflows_fallan_en_vez_de_saltarse(tmp_path):
    r = _pytest_sin_yaml(tmp_path, ci=True)
    assert r.returncode != 0 and "PyYAML no está instalado en CI" in r.stdout + r.stderr


def test_BE16_sin_pyyaml_en_local_se_saltan_con_aviso(tmp_path):
    r = _pytest_sin_yaml(tmp_path, ci=False)
    assert r.returncode in (0, 5) and "skipped" in r.stdout

# ---- supabase-keepalive
SB_STEP = "Lectura a la REST API de Supabase"
SB_ENV = {"SB_URL": "https://abc.supabase.co/", "SB_KEY": "sb_publishable_xyz", "HC": ""}


def _sb_script():
    return step(load("supabase-keepalive.yml"), "ping", SB_STEP)["run"]


def test_supabase_keepalive_estructura():
    wf = load("supabase-keepalive.yml")
    cron = wf[True]["schedule"][0]["cron"].split()   # PyYAML lee la clave `on` como True
    assert cron[0] not in ("0", "00") and cron[2] == "*/3"
    assert wf["permissions"] == {"contents": "read"}
    assert wf["jobs"]["ping"]["permissions"] == {"contents": "read"}
    assert wf["concurrency"]["cancel-in-progress"] is False
    assert wf["jobs"]["ping"]["timeout-minutes"] <= 5
    env = step(wf, "ping", SB_STEP)["env"]
    assert env == {"SB_URL": "${{ vars.SUPABASE_URL }}", "SB_KEY": "${{ vars.SUPABASE_PUBLISHABLE_KEY }}",
                   "HC": "${{ secrets.HC_SUPABASE_URL }}"}


def test_readme_documenta_supabase_keepalive_y_horario_de_healthchecks():
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    for needle in ("SUPABASE_URL", "SUPABASE_PUBLISHABLE_KEY", "HC_SUPABASE_URL", "supabase-keepalive.yml"):
        assert needle in readme
    # configuración de Healthchecks del README: modo Simple, Period de 1 día y gracia de 12 h (sin acoplarse al cron)
    assert "Period = 1 día" in readme
    assert "Grace Time = 12 horas" in readme


def _fake_curl_sb(sb, codes):
    """Cada petición a /rest/v1 devuelve el siguiente código de `codes` (el último se repite)."""
    (sb.tmp / "codes").write_text("\n".join(codes))
    _exe(sb.bin / "curl", '''echo "curl $*" >> "$CALLS_LOG"
case "$*" in
  *rest/v1*)
    n=$(grep -c '^curl .*rest/v1' "$CALLS_LOG")
    c=$(sed -n "${n}p" "%(f)s"); [ -n "$c" ] || c=$(tail -n1 "%(f)s")
    printf '%%s' "$c"; [ "$c" != 000 ] || exit 6; exit 0;;
  *) exit ${HC_RC:-0};;
esac
''' % {"f": sb.tmp / "codes"})


def _rest_calls(sb):
    return [c for c in calls(sb) if "rest/v1" in c]


@pytest.mark.parametrize("code", ["200", "401", "403"])
def test_supabase_keepalive_codigos_correctos(sandbox, code):
    _fake_curl_sb(sandbox, [code])
    r = bash(sandbox, _sb_script(), env=SB_ENV)
    assert r.returncode == 0, r.stdout + r.stderr
    assert len(_rest_calls(sandbox)) == 1 and not any(c.startswith("sleep") for c in calls(sandbox))
    call = _rest_calls(sandbox)[0]
    assert "https://abc.supabase.co/rest/v1/profiles?select=user_id&limit=1" in call
    assert "apikey: sb_publishable_xyz" in call


@pytest.mark.parametrize("code", ["000", "404", "500", "503", "521", "540", "429"])
def test_supabase_keepalive_codigos_de_fallo_reintentan_y_fallan(sandbox, code):
    _fake_curl_sb(sandbox, [code])
    r = bash(sandbox, _sb_script(), env=SB_ENV)
    assert r.returncode == 1
    assert len(_rest_calls(sandbox)) == 3
    assert [c for c in calls(sandbox) if c.startswith("sleep")] == ["sleep 15", "sleep 30"]   # sin dormir tras el último
    assert "::error::" in r.stdout


def test_supabase_keepalive_se_recupera_en_un_reintento(sandbox):
    _fake_curl_sb(sandbox, ["503", "200"])
    r = bash(sandbox, _sb_script(), env=SB_ENV)
    assert r.returncode == 0 and len(_rest_calls(sandbox)) == 2


@pytest.mark.parametrize("key", [
    "sb_secret_abc", "eyJ.service_role.x",
    "eyJhbGciOiJIUzI1NiJ9." + "eyJyb2xlIjoic2VydmljZV9yb2xlIn0" + ".firma",   # JWT {"role":"service_role"}
])
def test_supabase_keepalive_aborta_con_clave_secreta_sin_enviarla(sandbox, key):
    _fake_curl_sb(sandbox, ["200"])
    r = bash(sandbox, _sb_script(), env=dict(SB_ENV, SB_KEY=key))
    assert r.returncode == 1 and "SECRETA" in r.stdout + "SECRETA" * ("service_role" in r.stdout)
    assert _rest_calls(sandbox) == []
    assert key not in r.stdout + r.stderr


def test_supabase_keepalive_acepta_jwt_anon(sandbox):
    _fake_curl_sb(sandbox, ["200"])
    anon = "eyJhbGciOiJIUzI1NiJ9.eyJyb2xlIjoiYW5vbiJ9.firma"   # {"role":"anon"}
    assert bash(sandbox, _sb_script(), env=dict(SB_ENV, SB_KEY=anon)).returncode == 0


@pytest.mark.parametrize("var", ["SB_URL", "SB_KEY"])
def test_supabase_keepalive_falla_si_faltan_variables(sandbox, var):
    _fake_curl_sb(sandbox, ["200"])
    r = bash(sandbox, _sb_script(), env=dict(SB_ENV, **{var: ""}))
    assert r.returncode == 1 and "SUPABASE_URL" in r.stdout and _rest_calls(sandbox) == []


def test_supabase_keepalive_url_sin_https_aborta(sandbox):
    _fake_curl_sb(sandbox, ["200"])
    r = bash(sandbox, _sb_script(), env=dict(SB_ENV, SB_URL="http://abc.supabase.co"))
    assert r.returncode == 1 and _rest_calls(sandbox) == []


def test_supabase_keepalive_healthchecks_ok_fail_y_vacio(sandbox):
    hc = {"HC": "https://hc.test/ping/abc/"}
    _fake_curl_sb(sandbox, ["200"])
    assert bash(sandbox, _sb_script(), env=dict(SB_ENV, **hc)).returncode == 0
    pings = [c for c in calls(sandbox) if "hc.test" in c]
    assert len(pings) == 1 and pings[0].endswith("https://hc.test/ping/abc")
    sandbox.log.write_text("")
    _fake_curl_sb(sandbox, ["500"])
    assert bash(sandbox, _sb_script(), env=dict(SB_ENV, **hc)).returncode == 1
    pings = [c for c in calls(sandbox) if "hc.test" in c]
    assert len(pings) == 1 and pings[0].endswith("https://hc.test/ping/abc/fail")
    sandbox.log.write_text("")
    r = bash(sandbox, _sb_script(), env=SB_ENV)   # HC vacío
    assert r.returncode == 1 and "::warning::HC_SUPABASE_URL" in r.stdout
    assert not any("hc.test" in c for c in calls(sandbox))


def test_supabase_keepalive_fallo_del_ping_ok_no_hace_fallar(sandbox):
    _fake_curl_sb(sandbox, ["200"])
    r = bash(sandbox, _sb_script(), env=dict(SB_ENV, HC="https://hc.test/x", HC_RC="22"))
    assert r.returncode == 0 and "no se pudo avisar" in r.stdout


def test_supabase_keepalive_no_filtra_datos_en_los_logs(sandbox):
    _fake_curl_sb(sandbox, ["500"])
    r = bash(sandbox, _sb_script(), env=dict(SB_ENV, HC="https://hc.test/secreto"))
    out = r.stdout + r.stderr
    for s in ("abc.supabase.co", "sb_publishable_xyz", "hc.test/secreto"):
        assert s not in out


# ====================================================================================================================================
# Feed v2: publish-feed-v2.yml (GitHub Pages con deploy-pages, sin commits a ninguna rama)
# ====================================================================================================================================
V2 = "publish-feed-v2.yml"


def _v2_triggers():
    wf = load(V2)
    return wf.get("on", wf.get(True))


def test_v2_estructura_permisos_minimos_y_dependencias():
    wf = load(V2)
    jobs = wf["jobs"]
    assert set(jobs) == {"fetch", "personal", "publish", "verify", "heartbeat"}
    assert wf["permissions"] == {}                                                       # nada por defecto
    assert jobs["fetch"]["permissions"] == {"contents": "read"}
    assert jobs["publish"]["permissions"] == {"pages": "write", "id-token": "write"}      # los dos y solo esos
    assert jobs["verify"]["permissions"] == {} and jobs["heartbeat"]["permissions"] == {}
    assert jobs["publish"]["needs"] == "fetch" and jobs["verify"]["needs"] == "publish"
    assert set(jobs["heartbeat"]["needs"]) == {"fetch", "publish"} and jobs["verify"]["continue-on-error"] is True
    assert all("verify" not in (j.get("needs") if isinstance(j.get("needs"), list) else [j.get("needs")]) for n, j in jobs.items() if n != "verify")
    assert jobs["publish"]["environment"]["name"] == "github-pages"
    assert wf["concurrency"] == {"group": "publish-feed-v2", "cancel-in-progress": False}
    assert all(isinstance(j.get("timeout-minutes"), int) and j["timeout-minutes"] <= 25 for j in jobs.values())


def test_v2_no_hace_commits_ni_pushes_a_ninguna_rama():
    text = (WF / V2).read_text(encoding="utf-8")
    assert "git push" not in text and "contents: write" not in text and "git commit" not in text
    uses = [s["uses"].split("@")[0] for j in load(V2)["jobs"].values() for s in j["steps"] if "uses" in s]
    assert "actions/deploy-pages" in uses and "actions/upload-pages-artifact" in uses


def test_v2_fetch_instala_con_hashes_y_publish_no_instala_nada_de_terceros():
    jobs = load(V2)["jobs"]
    fetch_runs = " ".join(s.get("run", "") for s in jobs["fetch"]["steps"])
    assert "pip install --require-hashes --no-deps -r requirements.txt" in fetch_runs
    for s in jobs["publish"]["steps"]:
        assert "pip " not in s.get("run", "") and "actions/setup-python" not in s.get("uses", "")
    assert "sanity_check_v2" not in json.dumps(jobs["fetch"])                             # el validador solo vive en publish
    assert "id-token" not in json.dumps(jobs["fetch"]) and "pages" not in jobs["fetch"]["permissions"]


def test_v2_el_secreto_de_fred_solo_esta_en_el_paso_que_construye_el_feed():
    jobs = load(V2)["jobs"]
    for jn, job in jobs.items():
        for s in job["steps"]:
            has = "FRED_API_KEY" in json.dumps(s.get("env", {}))
            assert has == (jn == "fetch" and s.get("name") == "Build feed v2"), (jn, s.get("name"))


def test_v2_publish_valida_antes_de_subir_y_desplegar_y_con_descarga_propia():
    jobs = load(V2)["jobs"]
    names = [s.get("name") for s in jobs["publish"]["steps"]]
    assert names.index("Download previous site (validation subset)") < names.index("Sanity-check feed v2") < names.index("Upload Pages artifact") \
        < names.index("Deploy to GitHub Pages")
    co = step(load(V2), "publish", "Check out scripts and registry only")
    assert co["with"]["persist-credentials"] is False and "scripts" in co["with"]["sparse-checkout"] and "registry.json" in co["with"]["sparse-checkout"]
    dl = step(load(V2), "publish", "Download previous site (validation subset)")["run"]
    assert "site_download.py" in dl and "--mode validate" in dl and "--github-env" in dl
    fetch_dl = step(load(V2), "fetch", "Download previous site (full)")["run"]
    assert "--mode full" in fetch_dl and "--github-env" not in fetch_dl
    san = step(load(V2), "publish", "Sanity-check feed v2")
    assert san["env"]["EXPECT_PREV"] == "${{ env.EXPECT_PREV }}" and san["env"]["SANITY_ALLOW_REGRESSION"] == "${{ inputs.allow_regression }}"
    assert "sanity_check_v2.py" in san["run"] and "--prev prev_site/v2" in san["run"] and "--registry registry.json" in san["run"]
    assert step(load(V2), "publish", "Deploy to GitHub Pages")["id"] == "deployment"


def test_v2_el_cron_esta_gobernado_por_la_variable_y_el_workflow_viejo_no_cambia():
    wf = load(V2)
    for jn in ("fetch", "personal", "heartbeat"):
        cond = wf["jobs"][jn]["if"]
        assert "FEED_V2_ENABLED" in cond and "schedule" in cond
    assert "always()" in wf["jobs"]["heartbeat"]["if"]
    t = _v2_triggers()
    assert t["schedule"] == [{"cron": "47 6,11,15,21 * * *"}] and set(t["workflow_dispatch"]["inputs"]) == {"allow_regression", "force_all"}
    old = load("update-data.yml")
    old_t = old.get("on", old.get(True))
    assert old_t["schedule"] == [{"cron": "17 6,11,15,21 * * *"}]                           # el cron viejo, intacto
    assert set(old["jobs"]) == {"fetch", "publish", "verify-cdn", "heartbeat"}


def test_v2_sale_por_https_y_no_pisa_la_rama_data():
    text = (WF / V2).read_text(encoding="utf-8")
    assert "refs/heads/data" not in text and "@data" not in text


def _url_script():
    return step(load(V2), "fetch", "Resolve published feed URL")["run"]


@pytest.mark.parametrize("override,repo,want", [
    ("", "FractalBuddy/Portfolio_Dashboard", "https://fractalbuddy.github.io/Portfolio_Dashboard/v2"),
    ("", "usuario/repo", "https://usuario.github.io/repo/v2"),
    ("https://feed.example.com/v2/", "usuario/repo", "https://feed.example.com/v2"),
])
def test_v2_url_del_sitio_publicado(sandbox, override, repo, want):
    env = sandbox.tmp / "env"
    for script in (_url_script(), step(load(V2), "publish", "Resolve published feed URL")["run"]):
        env.write_text("")
        r = bash(sandbox, script, env={"OVERRIDE": override, "GITHUB_REPOSITORY": repo, "GITHUB_ENV": str(env)})
        assert r.returncode == 0, r.stdout + r.stderr
        assert env.read_text() == "FEED_BASE=%s\n" % want


@pytest.mark.parametrize("override", ["http://feed.example.com/v2", "ftp://x/v2", "feed.example.com", "file:///etc/passwd"])
def test_v2_url_no_https_aborta(sandbox, override):
    env = sandbox.tmp / "env"
    env.write_text("")
    r = bash(sandbox, _url_script(), env={"OVERRIDE": override, "GITHUB_REPOSITORY": "u/r", "GITHUB_ENV": str(env)})
    assert r.returncode != 0 and "https://" in r.stdout and env.read_text() == ""


def test_v2_recoger_alertas(sandbox):
    script = step(load(V2), "publish", "Collect alerts")["run"]
    out, summ, tmp = sandbox.tmp / "out", sandbox.tmp / "summary", sandbox.tmp / "rt"
    tmp.mkdir()
    env = {"GITHUB_OUTPUT": str(out), "GITHUB_STEP_SUMMARY": str(summ), "RUNNER_TEMP": str(tmp)}
    out.write_text("")
    assert bash(sandbox, script, env=env).returncode == 0 and out.read_text() == "alerts=\n"
    (tmp / "alerts.txt").write_text("429 de Yahoo | 2 simbolos con otra unidad\n::error::x")
    r = bash(sandbox, script, env=env)
    assert r.returncode == 0 and out.read_text().count("\n") == 2 and "DATOS DEGRADADOS" in r.stdout
    assert "\r" not in out.read_text()
    assert "- 429 de Yahoo" in summ.read_text()


def test_v2_leer_generated_at_valida_el_formato(sandbox):
    script = step(load(V2), "publish", "Read generated_at")["run"]
    (sandbox.work / "site" / "v2").mkdir(parents=True)
    mf = sandbox.work / "site" / "v2" / "manifest.json"
    out = sandbox.tmp / "gh_output"
    mf.write_text(json.dumps({"generated_at": "2026-10-26T12:00:00Z"}))
    assert bash(sandbox, script, env={"GITHUB_OUTPUT": str(out)}).returncode == 0 and out.read_text() == "generated_at=2026-10-26T12:00:00Z\n"
    mf.write_text(json.dumps({"generated_at": "x\nevil=1"}))
    out.write_text("")
    assert bash(sandbox, script, env={"GITHUB_OUTPUT": str(out)}).returncode != 0 and out.read_text() == ""


def _hb2(sb, hc, fetch, publish, alerts=""):
    _exe(sb.bin / "curl", 'echo "curl $*" >> "$CALLS_LOG"\n')
    script = step(load(V2), "heartbeat", "Ping Healthchecks.io")["run"]
    return bash(sb, script, env={"HC": hc, "FETCH": fetch, "PUBLISH": publish, "ALERTS": alerts})


def test_v2_heartbeat_ok_fail_y_degradado(sandbox):
    assert _hb2(sandbox, "https://hc.test/ping/abc/", "success", "success").returncode == 0
    assert calls(sandbox)[-1].endswith("https://hc.test/ping/abc") and "/fail" not in calls(sandbox)[-1]
    for f, p in (("success", "failure"), ("failure", "skipped"), ("cancelled", "skipped"), ("success", "skipped")):
        sandbox.log.write_text("")
        _hb2(sandbox, "https://hc.test/ping/abc", f, p)
        assert calls(sandbox)[-1].endswith("https://hc.test/ping/abc/fail"), (f, p)
    sandbox.log.write_text("")
    _hb2(sandbox, "https://hc.test/ping/abc", "success", "success", alerts="Yahoo limitó (429)")
    assert "degraded: Yahoo limitó (429)" in calls(sandbox)[-1] and calls(sandbox)[-1].endswith("/fail")


def test_v2_heartbeat_sin_url_avisa(sandbox):
    r = _hb2(sandbox, "", "success", "success")
    assert r.returncode == 0 and "::warning::" in r.stdout and calls(sandbox) == []


def test_v2_el_heartbeat_usa_el_check_propio_si_existe_y_si_no_el_de_siempre():
    jobs = load(V2)["jobs"]
    assert jobs["heartbeat"]["steps"][0]["env"]["HC"] == "${{ secrets.HC_UPDATE_V2_URL || secrets.HC_UPDATE_URL }}"
    assert jobs["verify"]["steps"][0]["env"]["HC"] == "${{ secrets.HC_UPDATE_V2_URL || secrets.HC_UPDATE_URL }}"


def _verify2():
    return step(load(V2), "verify", "Check Pages serves the new manifest")["run"]


def _fake_curl_pages(sb, served):
    _exe(sb.bin / "curl", '''echo "curl $*" >> "$CALLS_LOG"
case "$*" in
  */log*) exit 0;;
  *manifest.json*) echo '{"generated_at":"%s"}'; exit 0;;
esac
''' % served)


def test_v2_verify_pages_fresco_sale_sin_dormir(sandbox):
    want = "2026-10-26T12:00:00Z"
    _fake_curl_pages(sandbox, want)
    r = bash(sandbox, _verify2(), env={"WANT": want, "PAGE": "https://u.github.io/r/", "HC": "https://hc.test/x"})
    assert r.returncode == 0 and "Pages sirve el sitio nuevo" in r.stdout and not any(c_.startswith("sleep") for c_ in calls(sandbox))
    assert "https://u.github.io/r/v2/manifest.json?cb=" in " ".join(calls(sandbox))


def test_v2_verify_pages_desfasado_avisa_pero_no_falla_ni_duerme_tras_el_ultimo(sandbox):
    _fake_curl_pages(sandbox, "2000-01-01T00:00:00Z")
    r = bash(sandbox, _verify2(), env={"WANT": "2026-10-26T12:00:00Z", "PAGE": "https://u.github.io/r/", "HC": "https://hc.test/x"})
    assert r.returncode == 0 and "::warning::Pages sigue sirviendo" in r.stdout
    assert calls(sandbox).count("sleep 20") == 1 and sum(1 for c_ in calls(sandbox) if c_.startswith("sleep")) == 5
    assert any("/log" in c_ and "pages-stale" in c_ for c_ in calls(sandbox))


def test_v2_verify_sin_datos_no_hace_nada(sandbox):
    r = bash(sandbox, _verify2(), env={"WANT": "", "PAGE": "", "HC": ""})
    assert r.returncode == 0 and calls(sandbox) == []


# ---- datos propios (Supabase): personal_v2.py
def test_v2_personal_es_independiente_opcional_y_no_toca_pages_ni_artefactos():
    jobs = load(V2)["jobs"]
    p = jobs["personal"]
    assert p["permissions"] == {"contents": "read"} and p["continue-on-error"] is True and "needs" not in p
    assert all("personal" not in (j.get("needs") if isinstance(j.get("needs"), list) else [j.get("needs")]) for n, j in jobs.items() if n != "personal")
    uses = [s.get("uses", "") for s in p["steps"]]
    assert not any("upload-artifact" in u or "pages" in u for u in uses)          # el payload personal NUNCA va en un artefacto (en un repo público es descargable)
    assert "id-token" not in json.dumps(p) and "pages" not in p["permissions"]


def test_v2_personal_el_token_solo_en_los_pasos_de_biblioteca_estandar():
    p = load(V2)["jobs"]["personal"]
    by = {s["name"]: s for s in p["steps"]}
    watch, build, publish = (by["Read personal watch list (token, stdlib only)"], by["Build personal payloads (third-party code, NO token)"],
                             by["Publish personal payloads (token, stdlib only)"])
    for s in (watch, publish):
        assert s["env"] == {"SUPABASE_URL": "${{ vars.SUPABASE_URL }}", "SUPABASE_PUBLISHABLE_KEY": "${{ vars.SUPABASE_PUBLISHABLE_KEY }}",
                            "PERSONAL_FEED_TOKEN": "${{ secrets.PERSONAL_FEED_TOKEN }}"}
        assert s["run"].startswith("python3 -S scripts/personal_v2.py ")                  # sin site-packages: ningún paquete de terceros puede cargarse
    assert "env" not in build and build["run"].startswith("python scripts/personal_v2.py build")
    names = [s["name"] for s in p["steps"]]
    assert names.index(watch["name"]) < names.index(build["name"]) < names.index(publish["name"])
    # el token y las variables de Supabase no aparecen en ningún otro job ni paso
    for jn, job in load(V2)["jobs"].items():
        for s in job["steps"]:
            if (jn, s.get("name")) not in {("personal", watch["name"]), ("personal", publish["name"])}:
                assert "PERSONAL_FEED_TOKEN" not in json.dumps(s) and "SUPABASE" not in json.dumps(s), (jn, s.get("name"))


def test_v2_personal_los_pasos_con_token_importan_solo_biblioteca_estandar():
    code = ("import sys; sys.path.insert(0, %r); import personal_v2; "
            "bad = [m for m in ('requests', 'yfinance', 'feedparser', 'pandas', 'fetch_market_data', 'feed_v2') if m in sys.modules]; "
            "assert not bad, bad" % str(ROOT / "scripts"))
    r = subprocess.run([sys.executable, "-S", "-c", code], capture_output=True, text=True, timeout=60)
    assert r.returncode == 0, r.stderr


def test_v2_el_token_se_referencia_en_exactamente_dos_pasos_y_no_hay_claves_de_servidor():
    text = (WF / V2).read_text(encoding="utf-8")
    assert text.count("secrets.PERSONAL_FEED_TOKEN") == 2 and text.count("vars.SUPABASE_PUBLISHABLE_KEY") == 2
    assert "service_role" not in text and "sb_secret" not in text
