"""Bloque 5: observabilidad y límites. AU-07 (avisar si personal-nav falla o el NAV se queda parado), AU-08 (usuarios recortados por MAX_USERS)
y AU-09 (FEED_V2_INITIALIZED: un 404 en manifest.json deja de ser «primer run»). Todo con servidores locales y datos inventados."""
import json
import os
import stat
import subprocess
import threading
from datetime import timedelta
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest
import yaml

import personal_v2 as pv
import site_download as sd
from test_personal_nav_only import NavSupabase, run_nav, twenty  # noqa: F401
from test_personal_v2 import NOW, FakeSupabase, Spy, _sin_esperas, run_all, watch_row  # noqa: F401  (_sin_esperas: fixture autouse)

ROOT = Path(__file__).resolve().parent.parent
WF = ROOT / ".github" / "workflows"


@pytest.fixture()
def nav():
    s = NavSupabase()
    yield s
    s.close()


@pytest.fixture()
def sb():
    s = FakeSupabase()
    yield s
    s.close()


# ----------------------------------------------------------------------------------------------------------------- AU-08
def _usuarios(n):
    return [watch_row("usuario-%d" % i) for i in range(n)]


def test_AU08_los_usuarios_recortados_por_max_users_se_cuentan_en_dropped():
    users, dropped = pv.parse_watch(_usuarios(pv.MAX_USERS + 5), pv._plain)
    assert len(users) == pv.MAX_USERS and dropped == 5


def test_AU08_sin_recorte_no_se_cuenta_nada():
    users, dropped = pv.parse_watch(_usuarios(pv.MAX_USERS), pv._plain)
    assert len(users) == pv.MAX_USERS and dropped == 0


def test_AU08_el_resumen_de_watch_avisa_de_los_recortados_y_no_cambia_el_tope(sb, tmp_path, capsys):
    sb.watch = _usuarios(pv.MAX_USERS + 3)
    assert pv.MAX_USERS == 20                                                  # el tope no se toca
    rc, _ = run_all(sb, tmp_path)
    out = capsys.readouterr().out
    assert rc == 0 and "::warning::" in out and "3 usuarios recortados" in out and "MAX_USERS=20" in out
    assert "usuario-" not in out                                               # ningún identificador en el log


def test_AU08_sin_recorte_el_resumen_no_trae_el_aviso(sb, tmp_path, capsys):
    sb.watch = _usuarios(2)
    run_all(sb, tmp_path)
    assert "recortados" not in capsys.readouterr().out


def test_AU08_el_resumen_de_build_nav_tambien_cuenta_los_recortados(nav, tmp_path, capsys):
    nav.watch = [watch_row("usuario-%d" % i, funds=[twenty(i + 1)]) for i in range(pv.MAX_USERS + 2)]
    run_nav(nav, tmp_path)
    assert "descartados x2" in capsys.readouterr().out


# ----------------------------------------------------------------------------------------------------------------- AU-07 (Python)
def test_AU07_step_publish_devuelve_distinto_de_0_si_fallan_TODOS_los_payloads(sb, tmp_path, capsys):
    sb.watch = [watch_row("usuario-1"), watch_row("usuario-2")]
    sb.publish_status = 500
    rc, _ = run_all(sb, tmp_path)
    assert rc == 1 and "0 payloads publicados, 2 fallidos" in capsys.readouterr().out


def test_AU07_si_al_menos_uno_se_publica_el_codigo_es_0(sb, tmp_path):
    sb.watch = [watch_row("usuario-1"), watch_row("usuario-2")]
    calls = []
    orig = pv.rpc

    def rpc_a_medias(cfg, fn, body, **k):
        if fn == pv.RPC_PUBLISH:
            calls.append(1)
            if len(calls) == 1:
                raise pv.RpcError("x", status=500)
        return orig(cfg, fn, body, **k)
    pv_rpc = pv.rpc
    pv.rpc = rpc_a_medias
    try:
        rc, _ = run_all(sb, tmp_path)
    finally:
        pv.rpc = pv_rpc
    assert rc == 0 and len(calls) == 2


def test_AU07_sin_payloads_el_codigo_sigue_siendo_0(sb, tmp_path):
    sb.watch = []
    assert run_all(sb, tmp_path)[0] == 0


def _estado_nav(nav, tmp_path, monkeypatch, nav_price_date=None):
    out = tmp_path / "gh_output"
    out.write_text("")
    monkeypatch.setenv("GITHUB_OUTPUT", str(out))
    return out


def _salida(path):
    return dict(line.split("=", 1) for line in path.read_text().splitlines() if "=" in line)


def test_AU07_nav_state_ok_cuando_todo_va_bien(nav, tmp_path, monkeypatch):
    nav.watch = [watch_row("usuario-1", funds=[twenty(1)])]
    out = _estado_nav(nav, tmp_path, monkeypatch)
    rc, _ = run_nav(nav, tmp_path, extra_env={"GITHUB_OUTPUT": str(out)})
    assert rc == 0 and _salida(out).get("nav_state") == "ok"


def test_AU07_nav_state_failed_si_falla_algun_payload(nav, tmp_path, monkeypatch):
    nav.watch = [watch_row("usuario-1", funds=[twenty(1)]), watch_row("usuario-2", funds=[twenty(2)])]
    nav.nav_reply = lambda n, body: (200, {"ok": True, "updated": 1, "kept": 0, "added": 0}) if n == 1 else (500, {"message": "x"})
    out = _estado_nav(nav, tmp_path, monkeypatch)
    rc, _ = run_nav(nav, tmp_path, extra_env={"GITHUB_OUTPUT": str(out)})
    assert rc == 0 and _salida(out).get("nav_state") == "failed"                 # uno publicó y otro no: el job sigue en verde pero el latido avisa


def test_AU07_si_fallan_todos_los_payloads_de_nav_el_run_sigue_en_verde_pero_el_estado_es_failed(nav, tmp_path, monkeypatch):
    nav.watch = [watch_row("usuario-1", funds=[twenty(1)]), watch_row("usuario-2", funds=[twenty(2)])]
    nav.nav_reply = (500, {"message": "x"})
    out = _estado_nav(nav, tmp_path, monkeypatch)
    rc, _ = run_nav(nav, tmp_path, extra_env={"GITHUB_OUTPUT": str(out)})
    assert rc == 0 and _salida(out).get("nav_state") == "failed"           # el modo NAV nunca tumbó el run por un fallo de usuario (diseño de G2); el aviso lo da el latido


@pytest.mark.parametrize("dias,esperado", [(0, "ok"), (6, "ok"), (7, "old"), (30, "old")])
def test_AU07_nav_state_old_si_el_nav_mas_reciente_supera_6_dias(nav, tmp_path, monkeypatch, dias, esperado):
    nav.watch = [watch_row("usuario-1", funds=[twenty(1)])]
    spy = Spy()
    fecha = (NOW.date() - timedelta(days=dias)).isoformat()
    base = spy.nav

    def nav_fn(url, isin):
        r = base(url, isin)
        r["nav_date_iso"], r["nav_date"] = fecha, fecha
        return r
    spy.nav = nav_fn
    out = _estado_nav(nav, tmp_path, monkeypatch)
    rc, _ = run_nav(nav, tmp_path, spy, extra_env={"GITHUB_OUTPUT": str(out)})
    assert rc == 0 and _salida(out).get("nav_state") == esperado


def test_AU07_nav_state_old_si_ningun_fondo_tiene_nav_exacto(nav, tmp_path, monkeypatch):
    nav.watch = [watch_row("usuario-1", funds=[twenty(1)])]
    spy = Spy()
    spy.nav = lambda url, isin: None
    out = _estado_nav(nav, tmp_path, monkeypatch)
    rc, _ = run_nav(nav, tmp_path, spy, extra_env={"GITHUB_OUTPUT": str(out)})
    assert rc == 0 and _salida(out).get("nav_state") == "old"


# ----------------------------------------------------------------------------------------------------------------- AU-07 (paso del workflow)
def _hb_step():
    wf = yaml.safe_load((WF / "personal-nav.yml").read_text(encoding="utf-8"))
    return next(s for s in wf["jobs"]["personal-nav"]["steps"] if s.get("name") == "Heartbeat (optional)")


def _run_hb(tmp_path, hc, status="success", state=""):
    b = tmp_path / "bin"
    b.mkdir(exist_ok=True)
    curl = b / "curl"
    curl.write_text('#!/bin/bash\nprintf "%s\\n" "$*" >> "$CALLS"\n')
    curl.chmod(curl.stat().st_mode | stat.S_IEXEC)
    calls = tmp_path / "calls.log"
    calls.write_text("")
    script = tmp_path / "hb.sh"
    script.write_text(_hb_step()["run"])
    env = {"PATH": str(b) + ":" + os.environ["PATH"], "HOME": str(tmp_path), "CALLS": str(calls), "JOB_STATUS": status, "NAV_STATE": state}
    if hc is not None:
        env["HC"] = hc
    r = subprocess.run(["bash", "-e", str(script)], cwd=tmp_path, env=env, capture_output=True, text=True, timeout=30)
    return r, calls.read_text()


HC = "https://hc.example.test/ping/abc-123"


def test_AU07_el_paso_corre_siempre_salvo_cancelado_y_recibe_estado_y_resultado():
    st = _hb_step()
    assert st["if"] == "${{ always() && !cancelled() }}"
    assert st["env"] == {"HC": "${{ secrets.HEALTHCHECK_PERSONAL_NAV_URL }}", "JOB_STATUS": "${{ job.status }}", "NAV_STATE": "${{ steps.publish.outputs.nav_state }}"}


def test_AU07_sin_secreto_no_hace_nada_en_ningun_caso(tmp_path):
    for status, state in (("success", "ok"), ("failure", ""), ("success", "failed"), ("success", "old")):
        for hc in (None, ""):
            r, calls = _run_hb(tmp_path, hc, status, state)
            assert r.returncode == 0 and calls == "" and r.stdout == "" and r.stderr == ""


@pytest.mark.parametrize("state", ["ok", ""])
def test_AU07_todo_bien_hace_ping_normal(tmp_path, state):
    r, calls = _run_hb(tmp_path, HC, "success", state)
    assert r.returncode == 0 and calls.strip().endswith(HC) and "/fail" not in calls


@pytest.mark.parametrize("status,state,palabra", [("failure", "", "falló"), ("success", "failed", "payloads"), ("success", "old", "6 días")])
def test_AU07_si_algo_falla_hace_ping_a_fail_con_un_texto_fijo(tmp_path, status, state, palabra):
    r, calls = _run_hb(tmp_path, HC, status, state)
    assert r.returncode == 0 and HC + "/fail" in calls and palabra in calls
    assert r.stdout.count(HC) == 0 and "::warning::" not in r.stdout


def test_AU07_una_url_rara_sigue_avisando_sin_mostrar_el_valor(tmp_path):
    r, calls = _run_hb(tmp_path, "http://hc.example.test/x", "failure", "")
    assert calls == "" and "::warning::" in r.stdout and "hc.example.test" not in r.stdout + r.stderr


# ----------------------------------------------------------------------------------------------------------------- AU-09
class _Pages:
    """Pages falso que responde siempre 404 (sitio reiniciado / recién recreado)."""
    def __init__(self, status=404):
        self.status = status
        self.hits = 0
        outer = self

        class H(BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def do_GET(self):
                outer.hits += 1
                self.send_response(outer.status)
                self.send_header("Content-Length", "0")
                self.end_headers()
        self.srv = ThreadingHTTPServer(("127.0.0.1", 0), H)
        self.url = "http://127.0.0.1:%d/v2" % self.srv.server_address[1]
        threading.Thread(target=self.srv.serve_forever, daemon=True).start()

    def close(self):
        self.srv.shutdown()
        self.srv.server_close()


@pytest.fixture()
def pages(monkeypatch):
    monkeypatch.setattr(sd, "BACKOFF", 0)
    p = _Pages()
    yield p
    p.close()


def _dl(pages, tmp_path, monkeypatch, initialized, mode="validate"):
    gh_env = tmp_path / "gh_env"
    gh_env.write_text("")
    monkeypatch.setenv("GITHUB_ENV", str(gh_env))
    if initialized is None:
        monkeypatch.delenv("FEED_V2_INITIALIZED", raising=False)
    else:
        monkeypatch.setenv("FEED_V2_INITIALIZED", initialized)
    rc = sd.main(["--base-url", pages.url, "--dest", str(tmp_path / "prev"), "--mode", mode, "--github-env"])
    return rc, gh_env.read_text()


@pytest.mark.parametrize("valor", [None, "", "false", "0", "no", "   "])
def test_AU09_sin_la_variable_o_distinta_de_true_un_404_sigue_siendo_primer_run(pages, tmp_path, monkeypatch, capsys, valor):
    rc, env = _dl(pages, tmp_path, monkeypatch, valor)
    assert rc == 0 and "EXPECT_PREV=false" in env and "primer run" in capsys.readouterr().out


@pytest.mark.parametrize("valor", ["true", "TRUE", " true ", "True"])
@pytest.mark.parametrize("mode", ["full", "validate"])
def test_AU09_con_FEED_V2_INITIALIZED_true_un_404_en_manifest_es_un_fallo(pages, tmp_path, monkeypatch, capsys, valor, mode):
    rc, env = _dl(pages, tmp_path, monkeypatch, valor, mode)
    out = capsys.readouterr().out
    assert rc == 1 and "EXPECT_PREV=false" not in env and "::error::" in out and "FEED_V2_INITIALIZED" in out and "primer run" not in out.replace("no es un primer run", "")


def test_AU09_con_la_variable_y_un_sitio_sano_todo_sigue_igual(tmp_path, monkeypatch):
    """Control negativo: la marca solo cambia el trato del 404; con manifest válido el comportamiento es el de siempre."""
    import hashlib
    import feed_v2_common as c
    latest = b'{"x":1}'
    manifest = json.dumps({"schema": c.SCHEMA, "generated_at": "2026-10-26T12:00:00Z",
                           "files": {"s/AAPL/latest.json": {"bytes": len(latest), "sha256": hashlib.sha256(latest).hexdigest()}}}).encode()

    class H(BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def do_GET(self):
            body = manifest if self.path.startswith("/v2/manifest.json") else latest if "latest.json" in self.path else None
            self.send_response(200 if body else 404)
            self.send_header("Content-Length", str(len(body or b"")))
            self.end_headers()
            self.wfile.write(body or b"")
    srv = ThreadingHTTPServer(("127.0.0.1", 0), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    try:
        monkeypatch.setenv("FEED_V2_INITIALIZED", "true")
        gh_env = tmp_path / "gh_env"
        gh_env.write_text("")
        monkeypatch.setenv("GITHUB_ENV", str(gh_env))
        rc = sd.main(["--base-url", "http://127.0.0.1:%d/v2" % srv.server_address[1], "--dest", str(tmp_path / "prev"), "--github-env"])
        assert rc == 0 and "EXPECT_PREV=true" in gh_env.read_text()
    finally:
        srv.shutdown()
        srv.server_close()


def test_AU09_un_fallo_distinto_de_404_sigue_fallando_igual(tmp_path, monkeypatch):
    p = _Pages(status=503)
    monkeypatch.setattr(sd, "BACKOFF", 0)
    try:
        monkeypatch.setenv("FEED_V2_INITIALIZED", "")
        assert _dl(p, tmp_path, monkeypatch, None)[0] == 1
    finally:
        p.close()


def test_AU09_los_dos_pasos_de_descarga_del_workflow_pasan_la_variable():
    wf = yaml.safe_load((WF / "publish-feed-v2.yml").read_text(encoding="utf-8"))
    pasos = [s for j in wf["jobs"].values() for s in j.get("steps", []) if "site_download.py" in (s.get("run") or "")]
    assert len(pasos) == 2
    for s in pasos:
        assert s["env"] == {"FEED_V2_INITIALIZED": "${{ vars.FEED_V2_INITIALIZED }}"}
