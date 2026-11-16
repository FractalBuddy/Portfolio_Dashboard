"""AU01-F1, pieza 1: puerta de frescura de publish-feed-v2.yml (scripts/freshness_gate.py + job `gate`).

Dos relojes (disparador externo por workflow_dispatch y cron de GitHub de respaldo) no deben repetir el trabajo: si el manifest.json publicado tiene menos de
N minutos (40 por defecto) y el run no es manual con force=true, el workflow termina con éxito sin hacer nada. Fallo CERRADO hacia «hacer el trabajo».
Ninguna prueba sale a la red: la lectura se inyecta o se sirve desde un servidor HTTP local (bucle 127.0.0.1)."""
import json
import threading
from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

import freshness_gate as fg
import site_download as sd
from test_workflows import WF, _exe, bash, calls, load, step

V2 = "publish-feed-v2.yml"
NOW = datetime(2026, 11, 16, 10, 0, 0, tzinfo=timezone.utc)
BASE = "https://usuario.github.io/repo/v2"


def stamp(dt):
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


def manifest(ga):
    return json.dumps({"schema": 2, "generated_at": ga, "total_bytes": 1, "files": {}}).encode()


def serve(body=None, exc=None):
    """`fetch` falso para read_generated_at: devuelve `body` o lanza `exc`; anota las rutas pedidas."""
    seen = []

    def fetch(base, rel):
        seen.append((base, rel))
        if exc is not None:
            raise exc
        return body
    fetch.seen = seen
    return fetch


def run_gate(tmp_path, fetch, event="schedule", force="", max_age=None, now=NOW):
    out = tmp_path / "gh_output"
    out.write_text("")
    argv = ["--base-url", BASE, "--event", event, "--force", force, "--github-output"]
    if max_age is not None:
        argv += ["--max-age-min", str(max_age)]
    assert fg.main(argv, fetch=fetch, now=now, env={"GITHUB_OUTPUT": str(out)}) == 0          # la puerta NUNCA hace fallar el workflow
    return out.read_text()


# ------------------------------------------------------------------ función pura decide()
@pytest.mark.parametrize("age_min,skip", [(0, True), (5, True), (39, True), (39.5, True), (40, False), (41, False), (600, False)])
def test_decide_fresco_y_viejo_con_frontera_en_40_min(age_min, skip):
    assert fg.decide(NOW, stamp(NOW - timedelta(minutes=age_min)), 40, False)[0] is skip


def test_decide_force_siempre_trabaja_aunque_el_feed_sea_fresco():
    assert fg.decide(NOW, stamp(NOW - timedelta(minutes=1)), 40, True)[0] is False


@pytest.mark.parametrize("ga", [None, "", "2026-11-16", "2026-11-16T09:59:00", "2026-11-16T09:59:00+00:00", "2026-11-16 09:59:00Z", "2026-13-40T99:99:99Z",
                                "x" * 20, 1731750000, ["2026-11-16T09:59:00Z"], {"v": 1}, True, "2026-11-16T09:59:00Z\nevil=1"])
def test_decide_generated_at_malformado_trabaja(ga):
    assert fg.decide(NOW, ga, 40, False) == (False, "manifest sin generated_at válido: se trabaja")


@pytest.mark.parametrize("ahead", [timedelta(seconds=1), timedelta(minutes=5), timedelta(days=400)])
def test_decide_reloj_adelantado_generated_at_en_el_futuro_trabaja(ahead):
    skip, why = fg.decide(NOW, stamp(NOW + ahead), 40, False)
    assert skip is False and "futuro" in why


@pytest.mark.parametrize("bad", [0, -5, float("nan"), float("inf"), "40", None, True])
def test_decide_umbral_invalido_trabaja(bad):
    assert fg.decide(NOW, stamp(NOW - timedelta(minutes=1)), bad, False)[0] is False


def test_decide_el_umbral_es_un_parametro():
    ga = stamp(NOW - timedelta(minutes=50))
    assert fg.decide(NOW, ga, 40, False)[0] is False and fg.decide(NOW, ga, 60, False)[0] is True


# ------------------------------------------------------------------ envoltorio: lectura del manifest + evento + force
def test_manifest_fresco_en_schedule_sale_sin_trabajo(tmp_path):
    f = serve(manifest(stamp(NOW - timedelta(minutes=10))))
    assert run_gate(tmp_path, f) == "skip=true\n"
    assert f.seen == [(BASE, "manifest.json")]                     # solo el manifest; nada más


def test_manifest_fresco_en_dispatch_sin_force_tambien_sale(tmp_path):
    """El disparador externo usa workflow_dispatch SIN force: la puerta también le aplica (si no, el reloj externo duplicaría al cron)."""
    assert run_gate(tmp_path, serve(manifest(stamp(NOW - timedelta(minutes=10)))), event="workflow_dispatch", force="false") == "skip=true\n"


def test_manifest_viejo_trabaja(tmp_path):
    assert run_gate(tmp_path, serve(manifest(stamp(NOW - timedelta(minutes=95))))) == "skip=false\n"


def test_force_true_en_dispatch_trabaja_sin_leer_siquiera_el_manifest(tmp_path):
    f = serve(manifest(stamp(NOW - timedelta(minutes=1))))
    assert run_gate(tmp_path, f, event="workflow_dispatch", force="true") == "skip=false\n"
    assert f.seen == []


@pytest.mark.parametrize("force", ["true", "True", " true "])
def test_force_en_un_schedule_no_cuenta(tmp_path, force):
    """En un `schedule` no existe el input: aunque llegase un valor, solo vale en un lanzamiento manual."""
    assert run_gate(tmp_path, serve(manifest(stamp(NOW - timedelta(minutes=1)))), event="schedule", force=force) == "skip=true\n"


@pytest.mark.parametrize("force", ["", "false", "0", "yes", "1"])
def test_force_distinto_de_true_no_fuerza(tmp_path, force):
    assert run_gate(tmp_path, serve(manifest(stamp(NOW - timedelta(minutes=1)))), event="workflow_dispatch", force=force) == "skip=true\n"


@pytest.mark.parametrize("exc", [sd.NotFound("manifest.json"), sd.DownloadError("HTTP 503"), OSError("red"), TimeoutError(), RuntimeError("raro")])
def test_manifest_ausente_o_ilegible_trabaja(tmp_path, exc, capsys):
    assert run_gate(tmp_path, serve(exc=exc)) == "skip=false\n"
    assert "se trabaja" in capsys.readouterr().out


@pytest.mark.parametrize("body", [b"", b"no json", b"[1,2]", b'"2026-11-16T09:59:00Z"', b"{}", b'{"generated_at": NaN}', b"\xff\xfe", None,
                                  manifest("2026-11-16T09:59:00+00:00"), manifest(None)])
def test_manifest_malformado_trabaja(tmp_path, body):
    assert run_gate(tmp_path, serve(body)) == "skip=false\n"


def test_manifest_con_reloj_adelantado_trabaja(tmp_path):
    assert run_gate(tmp_path, serve(manifest(stamp(NOW + timedelta(minutes=30))))) == "skip=false\n"


@pytest.mark.parametrize("max_age", ["abc", "0", "-1", "nan", "inf"])
def test_umbral_invalido_por_linea_de_ordenes_trabaja(tmp_path, max_age):
    assert run_gate(tmp_path, serve(manifest(stamp(NOW - timedelta(minutes=1)))), max_age=max_age) == "skip=false\n"


def test_umbral_por_linea_de_ordenes(tmp_path):
    body = manifest(stamp(NOW - timedelta(minutes=50)))
    assert run_gate(tmp_path, serve(body)) == "skip=false\n" and run_gate(tmp_path, serve(body), max_age=60) == "skip=true\n"


@pytest.mark.parametrize("base", ["http://usuario.github.io/repo/v2", "file:///etc/passwd", "https://u:p@x.example/v2", ""])
def test_url_base_no_https_trabaja_sin_pedir_nada(tmp_path, base):
    f = serve(manifest(stamp(NOW)))
    out = tmp_path / "o"
    out.write_text("")
    assert fg.main(["--base-url", base, "--github-output"], fetch=f, now=NOW, env={"GITHUB_OUTPUT": str(out)}) == 0
    assert out.read_text() == "skip=false\n" and f.seen == []


def test_argumentos_invalidos_trabaja_y_sale_0(tmp_path):
    out = tmp_path / "o"
    out.write_text("")
    assert fg.main(["--no-existe"], env={"GITHUB_OUTPUT": str(out)}) == 0
    assert out.read_text() == "skip=false\n"


def test_lectura_real_por_http_local_y_salida_en_github_output(tmp_path, monkeypatch):
    """De punta a punta con urllib contra un servidor HTTP LOCAL (bucle): la descarga real de site_download, el parámetro anti-caché y el GITHUB_OUTPUT."""
    state = {"body": manifest(stamp(NOW - timedelta(minutes=3))), "status": 200, "paths": []}

    class H(BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def do_GET(self):
            state["paths"].append(self.path)
            self.send_response(state["status"])
            self.send_header("Content-Length", str(len(state["body"])))
            self.end_headers()
            self.wfile.write(state["body"])
    srv = ThreadingHTTPServer(("127.0.0.1", 0), H)
    threading.Thread(target=lambda: srv.serve_forever(poll_interval=0.01), daemon=True).start()
    monkeypatch.setattr(sd.time, "sleep", lambda s: None)
    try:
        base = "http://127.0.0.1:%d/repo/v2" % srv.server_address[1]
        out = tmp_path / "o"
        for status, want in ((200, "skip=true\n"), (404, "skip=false\n"), (503, "skip=false\n")):
            out.write_text("")
            state["status"] = status
            assert fg.main(["--base-url", base, "--event", "schedule", "--github-output"], now=NOW, env={"GITHUB_OUTPUT": str(out)}) == 0
            assert out.read_text() == want, status
        assert all(p.startswith("/repo/v2/manifest.json?cb=") for p in state["paths"])
    finally:
        srv.shutdown()


# ------------------------------------------------------------------ dos disparos seguidos (dos relojes)
def two_shots(tmp_path, gap_min, decide=None, monkeypatch=None):
    """Simula el disparador externo y el cron de respaldo lanzando el workflow con `gap_min` minutos de diferencia sobre el MISMO sitio publicado.
    Cada run que pasa la puerta «trabaja» y publica un manifest nuevo con generated_at = su hora. Devuelve cuántos runs trabajaron."""
    if decide is not None:
        monkeypatch.setattr(fg, "decide", decide)
    site = {"manifest": None}
    worked = 0
    for i, t in enumerate((NOW, NOW + timedelta(minutes=gap_min))):
        f = serve(exc=sd.NotFound("manifest.json")) if site["manifest"] is None else serve(site["manifest"])
        out = run_gate(tmp_path, f, event=("workflow_dispatch", "schedule")[i], now=t)
        if out == "skip=false\n":
            worked += 1
            site["manifest"] = manifest(stamp(t))
    return worked


def duplicated(worked):
    return worked > 1


@pytest.mark.parametrize("gap", [1, 10, 25, 39])
def test_dos_disparos_seguidos_solo_trabaja_uno(tmp_path, gap):
    assert not duplicated(two_shots(tmp_path, gap))


@pytest.mark.parametrize("gap", [40, 61, 180])
def test_dos_disparos_separados_mas_del_umbral_trabajan_los_dos(tmp_path, gap):
    assert two_shots(tmp_path, gap) == 2


def test_control_negativo_sin_la_puerta_se_detecta_el_trabajo_duplicado(tmp_path, monkeypatch):
    """Sin la puerta (decide siempre «trabaja»), la misma simulación de dos disparos seguidos SÍ da trabajo duplicado: el detector lo ve."""
    assert duplicated(two_shots(tmp_path, 5, decide=lambda *a: (False, "sin puerta"), monkeypatch=monkeypatch))


def test_control_negativo_con_fallo_abierto_falla_la_prueba_del_manifest_malformado(tmp_path, monkeypatch):
    """Si la puerta fallara ABIERTA (un generated_at ilegible contase como «fresco»), la prueba del manifest malformado lo detectaría."""
    orig = fg.decide

    def fail_open(now, ga, max_age, force):
        return (True, "fallo abierto") if fg.parse_stamp(ga) is None else orig(now, ga, max_age, force)
    monkeypatch.setattr(fg, "decide", fail_open)
    assert run_gate(tmp_path, serve(b'{"generated_at": "basura"}')) == "skip=true\n"          # == lo que test_manifest_malformado_trabaja rechaza


# ------------------------------------------------------------------ el workflow
def _trig():
    wf = load(V2)
    return wf.get("on", wf.get(True))


def test_input_force_booleano_por_defecto_false():
    force = _trig()["workflow_dispatch"]["inputs"]["force"]
    assert force["type"] == "boolean" and force["default"] is False and force["description"]


def test_gate_es_el_primer_job_sin_secretos_ni_permisos_y_solo_stdlib():
    wf = load(V2)
    gate = wf["jobs"]["gate"]
    assert "needs" not in gate and gate["permissions"] == {} and gate["timeout-minutes"] <= 5
    assert gate["outputs"]["skip"] == "${{ steps.gate.outputs.skip }}"
    text = json.dumps(gate)
    assert "secrets." not in text and "pip " not in text and "setup-python" not in text
    co = gate["steps"][0]
    assert co["uses"].startswith("actions/checkout@") and co["with"]["persist-credentials"] is False and co["with"]["sparse-checkout"].split() == ["scripts"]
    g = step(wf, "gate", "Freshness gate (stdlib only, no secrets)")
    assert g["id"] == "gate" and g["run"].startswith("python3 -S scripts/freshness_gate.py ")
    assert "--github-output" in g["run"] and '--base-url "$FEED_BASE"' in g["run"]
    assert g["env"] == {"EVENT": "${{ github.event_name }}", "FORCE": "${{ inputs.force }}"}
    # misma URL base que usa el workflow para descargar el sitio anterior
    assert step(wf, "gate", "Resolve published feed URL")["run"] == step(wf, "fetch", "Resolve published feed URL")["run"]


def test_los_jobs_de_trabajo_se_saltan_si_la_puerta_dice_skip_y_fallan_cerrado():
    jobs = load(V2)["jobs"]
    assert jobs["fetch"]["needs"] == "gate"
    cond = jobs["fetch"]["if"]
    # fallo cerrado: si el job gate fallase (sin salida), fetch corre igualmente (!cancelled() y comparación con 'true', no con 'false')
    assert "needs.gate.outputs.skip != 'true'" in cond and "!cancelled()" in cond and "FEED_V2_ENABLED" in cond
    assert "needs.fetch.result == 'success'" in jobs["publish"]["if"] and "!cancelled()" in jobs["publish"]["if"]
    assert "needs.publish.result == 'success'" in jobs["verify"]["if"]


def test_el_script_de_la_puerta_solo_importa_biblioteca_estandar():
    import subprocess
    import sys
    code = ("import sys; sys.path.insert(0, %r); import freshness_gate; "
            "bad = [m for m in ('requests', 'yfinance', 'feedparser', 'pandas', 'fetch_market_data', 'feed_v2') if m in sys.modules]; "
            "assert not bad, bad" % str(WF.parent.parent / "scripts"))
    r = subprocess.run([sys.executable, "-S", "-c", code], capture_output=True, text=True, timeout=60)
    assert r.returncode == 0, r.stderr


# ---- latido cuando se sale por frescura: ni /fail ni OK, un /log informativo
@pytest.fixture()
def sandbox(tmp_path):
    """Igual que la fixture de test_workflows.py: `sleep` y `curl` falsos en el PATH (ninguna llamada real)."""
    bindir, work, log = tmp_path / "bin", tmp_path / "work", tmp_path / "calls.log"
    bindir.mkdir()
    work.mkdir()
    log.write_text("")
    _exe(bindir / "sleep", 'echo "sleep $*" >> "$CALLS_LOG"\n')
    return type("SB", (), {"bin": bindir, "work": work, "log": log, "tmp": tmp_path})


def _hb(sb, hc, fetch, publish, skip="", alerts=""):
    _exe(sb.bin / "curl", 'echo "curl $*" >> "$CALLS_LOG"\n')
    script = step(load(V2), "heartbeat", "Ping Healthchecks.io")["run"]
    return bash(sb, script, env={"HC": hc, "FETCH": fetch, "PUBLISH": publish, "ALERTS": alerts, "SKIP": skip})


def test_heartbeat_recibe_la_salida_de_la_puerta():
    hb = load(V2)["jobs"]["heartbeat"]
    assert set(hb["needs"]) == {"gate", "fetch", "publish"} and "always()" in hb["if"]
    assert hb["steps"][0]["env"]["SKIP"] == "${{ needs.gate.outputs.skip }}"


def test_heartbeat_salida_por_frescura_manda_log_y_ni_ok_ni_fail(sandbox):
    r = _hb(sandbox, "https://hc-ping.com/abc", "skipped", "skipped", skip="true")
    assert r.returncode == 0
    [c] = calls(sandbox)
    assert c.endswith("https://hc-ping.com/abc/log") and "/fail" not in c and "fresh-skip" in c


def test_heartbeat_sin_puerta_sigue_igual_que_antes(sandbox):
    for skip in ("", "false"):
        sandbox.log.write_text("")
        _hb(sandbox, "https://hc-ping.com/abc", "success", "success", skip=skip)
        assert calls(sandbox)[-1].endswith("https://hc-ping.com/abc") and "/log" not in calls(sandbox)[-1]
        sandbox.log.write_text("")
        _hb(sandbox, "https://hc-ping.com/abc", "skipped", "skipped", skip=skip)       # sin la salida de la puerta, fetch saltado ES un fallo
        assert calls(sandbox)[-1].endswith("https://hc-ping.com/abc/fail")


def test_control_negativo_sin_la_rama_de_frescura_el_latido_daria_un_falso_fail(sandbox):
    """Con el latido de antes (sin mirar SKIP), una salida por frescura (fetch y publish saltados) se convertía en /fail."""
    script = step(load(V2), "heartbeat", "Ping Healthchecks.io")["run"]
    start = script.index('if [ "${SKIP:-}" = "true" ]')
    end = script.index("fi\n", start) + 3
    _exe(sandbox.bin / "curl", 'echo "curl $*" >> "$CALLS_LOG"\n')
    bash(sandbox, script[:start] + script[end:], env={"HC": "https://hc-ping.com/abc", "FETCH": "skipped", "PUBLISH": "skipped", "ALERTS": "", "SKIP": "true"})
    assert calls(sandbox)[-1].endswith("/fail")
