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

import pytest

yaml = pytest.importorskip("yaml")

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
