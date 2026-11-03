"""G2: modo SOLO NAV de los datos propios (publish_personal_nav) y compatibilidad del modo completo. Servidor HTTP LOCAL que simula Supabase (ninguna conexión real).
TODOS los ISIN, URL, nombres y tokens son FALSOS."""
import json
import os
import stat
import subprocess
from pathlib import Path

import pytest
import yaml

import personal_v2 as pv
from test_personal_v2 import ISIN_A, ISIN_B, NOW, PUB_KEY, TOKEN, URL_A, URL_B, FakeSupabase, Spy, _sin_esperas, long_bars, run_all, watch_row  # noqa: F401 (_sin_esperas: fixture autouse)

ROOT = Path(__file__).resolve().parent.parent
WF = ROOT / ".github" / "workflows"
CONTRACT_KEYS = {"isin", "name", "price", "currency", "unit", "source", "nav_date", "nav_date_iso", "nav_stale", "change_pct", "wk52_low", "wk52_high", "proxy_symbol", "facts"}
OK = {"ok": True, "updated": 1, "kept": 0, "added": 0}


class NavSupabase(FakeSupabase):
    """FakeSupabase + una RPC publish_personal_nav configurable: `nav_reply` = (estado HTTP, cuerpo JSON) o una función (n.º de llamada, cuerpo) -> (estado, cuerpo)."""
    def __init__(self, *a, **k):
        self.nav_reply = (200, OK)
        super().__init__(*a, **k)
        outer = self
        base_handler = self.srv.RequestHandlerClass

        class H(base_handler):
            def do_POST(self):
                if not self.path.endswith("/" + pv.RPC_PUBLISH_NAV):
                    return super().do_POST()
                body = self.rfile.read(int(self.headers.get("Content-Length", 0)))
                parsed = json.loads(body)
                outer.reqs.append((self.path, {k.lower(): v for k, v in self.headers.items()}, parsed))
                reply = outer.nav_reply(len(outer.calls(pv.RPC_PUBLISH_NAV)), parsed) if callable(outer.nav_reply) else outer.nav_reply
                out = json.dumps(reply[1]).encode()
                self.send_response(reply[0])
                self.send_header("Content-Length", str(len(out)))
                self.end_headers()
                self.wfile.write(out)
        self.srv.RequestHandlerClass = H


@pytest.fixture()
def nav():
    s = NavSupabase()
    yield s
    s.close()


def run_nav(sb, tmp_path, spy=None, step="all", extra_env=None, **over):
    spy = spy or Spy()
    env = sb.env(**over)
    env.update(extra_env or {})
    rc = pv.main([step, "--nav-only", "--dir", str(tmp_path / "work")], fetchers=spy.fetchers(), env=env, now=NOW)
    return rc, spy


def twenty(i):
    return {"isin": "IE0FAKE%05d" % i, "name": "Fondo Falso %d" % i, "url": "https://www.ishares.com/ch/individual/en/products/%06d/fake-%d" % (i, i)}


def _tiene_fallback(path):
    """¿Escribió el run la señal fallback_full en $GITHUB_OUTPUT? (H5: ahora el fichero también lleva nav_state, así que ya no basta con que exista.)"""
    return path.exists() and "fallback_full" in path.read_text()


# ------------------------------------------------------------------ el payload de NAV
def test_el_payload_de_nav_lleva_solo_las_claves_permitidas_y_nunca_series(nav, tmp_path):
    nav.watch = [watch_row("usuario-1", funds=[{**twenty(1), "proxy_symbol": "FAKEPX"}], symbols=["FAKE1", "AAPL"])]
    rc, spy = run_nav(nav, tmp_path)
    assert rc == 0
    [(path, headers, body)] = nav.calls(pv.RPC_PUBLISH_NAV)
    assert path == "/rest/v1/rpc/publish_personal_nav" and set(body) == {"p_token", "p_user", "p_funds"}      # firma fija del contrato
    assert headers["apikey"] == PUB_KEY and "authorization" not in headers and body["p_token"] == TOKEN and body["p_user"] == "usuario-1"
    assert isinstance(body["p_funds"], list) and len(body["p_funds"]) == 1
    f = body["p_funds"][0]
    assert set(f) <= CONTRACT_KEYS and {"isin", "name", "price", "currency", "unit", "source", "nav_date_iso", "proxy_symbol", "facts"} <= set(f)
    assert "series" not in json.dumps(body) and "public" not in body["p_funds"][0] and f["price"] == 25.5 and f["source"] == "ishares_nav_scrape"
    assert f["facts"] == {"ongoing_charges_pct": 0.1, "domicile": "Ireland"}                  # claves fuera de la lista blanca de facts, descartadas
    assert nav.calls(pv.RPC_PUBLISH) == [] and spy.series_calls == []                          # ni publish_personal_feed ni una sola petición a Yahoo
    assert pv.nav_problems(body["p_funds"], NOW.date()) == []


def test_nav_fund_keys_son_exactamente_las_del_fondo_del_payload_completo():
    assert pv.NAV_FUND_KEYS == CONTRACT_KEYS
    full = {"isin": ISIN_A, "name": "F", "price": 1.5, "currency": "EUR", "unit": "currency", "source": "ishares_nav_scrape", "nav_date": "23/Oct/2026", "nav_date_iso": "2026-10-23",
            "nav_stale": False, "change_pct": 0.1, "wk52_low": 1.0, "wk52_high": 2.0, "proxy_symbol": "FAKEPX", "facts": {"domicile": "Ireland"}}
    assert set(full) == pv.NAV_FUND_KEYS and pv.nav_problems([full], NOW.date()) == []


def test_un_fondo_sin_nav_legible_se_envia_igualmente_con_source_none(nav, tmp_path):
    nav.watch = [watch_row("usuario-1", funds=[twenty(1), twenty(2), {**twenty(3), "url": "http://evil.example/x"}])]
    spy, pedidas = Spy(), []
    legible = spy.nav

    def nav_fn(url, isin):
        pedidas.append(url)
        return legible(url, isin) if isin.endswith("00001") else None                           # el 2º: ficha ilegible; el 3º: URL no permitida (ni se pide)
    spy.nav = nav_fn
    rc, _ = run_nav(nav, tmp_path, spy)
    assert rc == 0
    funds = nav.calls(pv.RPC_PUBLISH_NAV)[0][2]["p_funds"]
    assert [f["isin"] for f in funds] == ["IE0FAKE00001", "IE0FAKE00002", "IE0FAKE00003"]       # TODOS los de la lista
    assert [f["source"] for f in funds] == ["ishares_nav_scrape", "none", "none"] and [f["price"] for f in funds] == [25.5, None, None]
    assert all(set(f) <= CONTRACT_KEYS for f in funds) and pedidas == [twenty(1)["url"], twenty(2)["url"]]


def test_salto_de_nav_fuera_de_rango_va_como_none_con_price_null(nav, tmp_path):
    nav.watch = [watch_row("usuario-1", funds=[{**twenty(1), "prev_price": 2.0}])]            # 25,5 / 2 = x12: no creíble
    run_nav(nav, tmp_path)
    f = nav.calls(pv.RPC_PUBLISH_NAV)[0][2]["p_funds"][0]
    assert f["source"] == "none" and f["price"] is None and "nav_date_iso" not in f


def test_veinte_fondos_con_facts_grandes_caben_en_64_kb(nav, tmp_path):
    nav.watch = [watch_row("usuario-1", funds=[twenty(i) for i in range(1, 21)])]
    spy = Spy()
    spy.facts = lambda url: {k: "x" * 200 for k in pv.FACTS_KEYS}                                # 19 claves x 200 caracteres x 20 fondos ≈ 80 KB: no cabe
    run_nav(nav, tmp_path, spy)
    [(_p, _h, body)] = nav.calls(pv.RPC_PUBLISH_NAV)
    raw = json.dumps(body["p_funds"], separators=(",", ":")).encode()
    assert len(body["p_funds"]) == 20 and len(raw) <= 64 * 1024
    assert all("facts" not in f for f in body["p_funds"])                                  # 20 x ~2 KB de facts no cabían: se quitan los facts, no los fondos
    assert all(f["price"] == 25.5 for f in body["p_funds"])


def test_veinte_fondos_normales_ocupan_mucho_menos_de_64_kb(nav, tmp_path):
    nav.watch = [watch_row("usuario-1", funds=[twenty(i) for i in range(1, 21)])]
    run_nav(nav, tmp_path)
    raw = json.dumps(nav.calls(pv.RPC_PUBLISH_NAV)[0][2]["p_funds"], separators=(",", ":")).encode()
    assert len(raw) < 8 * 1024 and all(f["facts"] for f in nav.calls(pv.RPC_PUBLISH_NAV)[0][2]["p_funds"])      # si caben, los facts se conservan


def test_una_peticion_a_la_ficha_por_fondo_y_solo_eso(nav, tmp_path):
    nav.watch = [watch_row("usuario-1", funds=[twenty(1), twenty(2)]), watch_row("usuario-2", funds=[twenty(1)])]
    rc, spy = run_nav(nav, tmp_path)
    assert rc == 0 and len(spy.nav_calls) == 3 and spy.series_calls == [] and len(nav.calls(pv.RPC_PUBLISH_NAV)) == 2


def test_el_paso_build_nav_no_usa_el_registro_ni_escribe_payloads_json(nav, tmp_path):
    nav.watch = [watch_row()]
    work = tmp_path / "work"
    assert pv.main(["watch", "--nav-only", "--dir", str(work)], env=nav.env()) == 0
    assert pv.main(["build", "--nav-only", "--dir", str(work), "--registry", str(tmp_path / "no-existe.json")], fetchers=Spy().fetchers(), env={}, now=NOW) == 0
    assert (work / "nav_payloads.json").is_file() and not (work / "payloads.json").exists()
    assert stat.S_IMODE(os.stat(work / "nav_payloads.json").st_mode) == 0o600
    assert TOKEN not in (work / "nav_payloads.json").read_text()


# ------------------------------------------------------------------ respuestas de la RPC
def test_ok_suma_los_contadores_y_escribe_una_sola_linea(nav, tmp_path, capsys):
    nav.watch = [watch_row("usuario-1", funds=[twenty(1), twenty(2)]), watch_row("usuario-2", funds=[twenty(3)])]
    nav.nav_reply = (200, {"ok": True, "updated": 2, "kept": 1, "added": 3})
    rc, _ = run_nav(nav, tmp_path)
    out = capsys.readouterr().out
    assert rc == 0
    line = [ln for ln in out.splitlines() if "RPC:" in ln]
    assert len(line) == 1 and "NAV: 3 fondos, 3 con NAV exacto" in line[0] and "NAV más reciente 2026-10-23 (3 d)" in line[0]
    assert "2 usuarios ok (actualizados 4, conservados 2, añadidos 6), 0 sin payload completo, 0 fallidos" in line[0]
    assert "::error::" not in out and "::warning::" not in out


def test_no_payload_se_anota_y_no_falla(nav, tmp_path, capsys):
    nav.watch = [watch_row("usuario-1"), watch_row("usuario-2")]
    nav.nav_reply = lambda n, body: (200, {"ok": False, "reason": "no_payload"} if body["p_user"] == "usuario-1" else OK)
    rc, _ = run_nav(nav, tmp_path)
    out = capsys.readouterr().out
    assert rc == 0 and len(nav.calls(pv.RPC_PUBLISH_NAV)) == 2                              # sigue con el siguiente usuario
    assert "1 usuarios ok" in out and "1 sin payload completo, 0 fallidos" in out and "::error::" not in out


def test_token_rechazado_42501_falla_claro_y_no_sigue(nav, tmp_path, capsys):
    nav.watch = [watch_row("usuario-1"), watch_row("usuario-2")]
    nav.nav_reply = (403, {"code": "42501", "message": "forbidden", "details": "secreto del servidor 12345"})
    rc, _ = run_nav(nav, tmp_path)
    out = capsys.readouterr().out
    assert rc == 1 and "::error::" in out and "42501" in out and "rechazó el token" in out
    assert len(nav.calls(pv.RPC_PUBLISH_NAV)) == 1                                           # no se insiste con el segundo usuario
    assert "secreto del servidor" not in out and TOKEN not in out and nav.calls(pv.RPC_PUBLISH) == []


@pytest.mark.parametrize("status,body", [(404, {"code": "PGRST202", "message": "Could not find the function public.publish_personal_nav"}), (404, {}), (400, {"code": "42883"})])
def test_rpc_inexistente_mensaje_claro_sin_fallback_y_sin_publicar_el_completo(nav, tmp_path, capsys, status, body):
    nav.watch = [watch_row("usuario-1"), watch_row("usuario-2")]
    nav.nav_reply = (status, body)
    out_file = tmp_path / "gh_output"
    rc, spy = run_nav(nav, tmp_path, extra_env={"GITHUB_OUTPUT": str(out_file)})
    out = capsys.readouterr().out
    assert rc == 1 and "::error::" in out and "RPC publish_personal_nav no existe todavía" in out
    assert nav.calls(pv.RPC_PUBLISH) == [] and len(nav.calls(pv.RPC_PUBLISH_NAV)) == 1 and spy.series_calls == []
    assert not _tiene_fallback(out_file)                                                     # sin la señal de fallback: el workflow no construye ni publica el completo


@pytest.mark.parametrize("value", ["", "0", "true", "yes", " 2 ", "off"])
def test_el_fallback_solo_se_activa_con_exactamente_1(nav, tmp_path, value):
    nav.watch = [watch_row()]
    nav.nav_reply = (404, {"code": "PGRST202"})
    out_file = tmp_path / "gh_output"
    rc, _ = run_nav(nav, tmp_path, extra_env={"GITHUB_OUTPUT": str(out_file), pv.FALLBACK_FULL_ENV: value})
    assert rc == 1 and not _tiene_fallback(out_file)


def test_con_el_fallback_explicito_avisa_y_pide_el_completo_pero_sale_0(nav, tmp_path, capsys):
    nav.watch = [watch_row()]
    nav.nav_reply = (404, {"code": "PGRST202"})
    out_file = tmp_path / "gh_output"
    rc, _ = run_nav(nav, tmp_path, extra_env={"GITHUB_OUTPUT": str(out_file), pv.FALLBACK_FULL_ENV: "1"})
    out = capsys.readouterr().out
    assert rc == 0 and out_file.read_text() == "fallback_full=true\nnav_state=ok\n" and "::warning::" in out and "COMPLETO" in out and "::error::" not in out
    assert nav.calls(pv.RPC_PUBLISH) == []                  # el script de NAV nunca publica el completo él mismo: lo hacen los pasos siguientes del workflow


def test_el_fallback_no_se_activa_por_un_fallo_que_no_es_rpc_inexistente(nav, tmp_path):
    nav.watch = [watch_row()]
    for status, body in ((403, {"code": "42501"}), (500, {}), (200, {"ok": False, "reason": "otra"})):
        nav.nav_reply = (status, body)
        out_file = tmp_path / ("o%d" % status)
        run_nav(nav, tmp_path, extra_env={"GITHUB_OUTPUT": str(out_file), pv.FALLBACK_FULL_ENV: "1"})
        assert not _tiene_fallback(out_file), status


def test_un_fallo_transitorio_de_un_usuario_es_aviso_y_se_sigue(nav, tmp_path, capsys):
    nav.watch = [watch_row("usuario-1"), watch_row("usuario-2")]
    nav.nav_reply = lambda n, body: (500, {}) if body["p_user"] == "usuario-1" else (200, OK)
    rc, _ = run_nav(nav, tmp_path)
    out = capsys.readouterr().out
    assert rc == 0 and "::warning::" in out and "HTTP 500" in out and "1 usuarios ok" in out and "1 fallidos" in out


@pytest.mark.parametrize("reply", [{"ok": True}, {"ok": True, "updated": "1", "kept": 0, "added": 0}, {"ok": True, "updated": -1, "kept": 0, "added": 0},
                                   {"ok": True, "updated": True, "kept": 0, "added": 0}, {"ok": False, "reason": "otra"}, {"ok": False}, [], None, "ok"])
def test_respuesta_rara_de_la_rpc_cuenta_como_fallo_sin_inventar_exito(nav, tmp_path, capsys, reply):
    nav.watch = [watch_row()]
    nav.nav_reply = (200, reply)
    rc, _ = run_nav(nav, tmp_path)
    out = capsys.readouterr().out
    assert rc == 0 and "0 usuarios ok" in out and "1 fallidos" in out


# ------------------------------------------------------------------ privacidad
def test_el_log_del_modo_nav_no_contiene_isin_url_nombres_clave_ni_token(nav, tmp_path, capsys):
    nav.watch = [watch_row("usuario-1", funds=[{**twenty(1), "proxy_symbol": "FAKEPX"}, twenty(2)], symbols=["FAKE1"])]
    spy = Spy()
    spy.noise = True                                             # las funciones reutilizadas imprimen el ISIN en stderr: debe descartarse
    for reply in ((200, OK), (404, {"code": "PGRST202"}), (403, {"code": "42501"}), (500, {}), (200, {"ok": False, "reason": "no_payload"})):
        nav.nav_reply = reply
        run_nav(nav, tmp_path, spy)
    cap = capsys.readouterr()
    text = (cap.out + cap.err).replace("::add-mask::IE0FAKE00001", "").replace("::add-mask::IE0FAKE00002", "")
    for secret in ("IE0FAKE", "FAKE1", "FAKEPX", "Fondo Falso", "ishares.com", TOKEN, PUB_KEY, nav.url, "usuario-1", "25.5", "could not find expected NAV"):
        assert secret not in text, secret
    assert "datos propios: NAV:" in text


def test_el_watch_en_modo_nav_enmascara_los_isin_y_solo_cuenta(nav, tmp_path, capsys):
    nav.watch = [watch_row("usuario-1", funds=[twenty(1)], symbols=["FAKE1"])]
    assert pv.main(["watch", "--nav-only", "--dir", str(tmp_path / "w")], env=nav.env()) == 0
    out = capsys.readouterr().out
    assert "::add-mask::IE0FAKE00001" in out and out.replace("::add-mask::IE0FAKE00001", "").count("IE0FAKE") == 0


# ------------------------------------------------------------------ salida temprana (watch --nav-only)
def test_work_en_modo_nav_depende_solo_de_las_fichas_no_de_los_simbolos(nav, tmp_path):
    for watch, expected in (([watch_row(funds=[twenty(1)], symbols=[])], "true"), ([watch_row(funds=[], symbols=["FAKE1", "AAPL"])], "false"),
                            ([watch_row(funds=[{**twenty(1), "url": "http://evil.example/x"}], symbols=["FAKE1"])], "false"), ([], "false")):
        nav.watch = watch
        f = tmp_path / ("o%s" % expected)
        f.write_text("")
        assert pv.main(["watch", "--nav-only", "--dir", str(tmp_path / "w"), "--github-output"], env=nav.env(GITHUB_OUTPUT=str(f))) == 0
        assert f.read_text() == "work=%s\n" % expected
    nav.watch = [watch_row(funds=[], symbols=["FAKE1"])]                                      # el modo completo sí trabaja solo con símbolos (sin cambios)
    f = tmp_path / "full"
    pv.main(["watch", "--dir", str(tmp_path / "w"), "--github-output"], env=nav.env(GITHUB_OUTPUT=str(f)))
    assert f.read_text() == "work=true\n"


# ------------------------------------------------------------------ controles negativos del validador
def _fund(**over):
    f = {"isin": ISIN_A, "name": "F", "price": 1.5, "currency": "EUR", "unit": "currency", "source": "ishares_nav_scrape"}
    f.update(over)
    return f


@pytest.mark.parametrize("bad", [
    [_fund(series=[])], [_fund(url="https://www.ishares.com/x")], [_fund(prev_price=1.0)], [_fund(wk52=[1, 2])],
    [_fund(price=0)], [_fund(price=-1.0)], [_fund(price=float("nan"))], [_fund(price=float("inf"))], [_fund(price="1.5")], [_fund(price=True)], [_fund(price=1e9)],
    [_fund(source="yahoo")], [_fund(currency="USD")], [_fund(isin="no-es-un-isin")], [_fund(name="<script>")], [_fund(facts={"evil": "x"})], [_fund(nav_date_iso="23/10/2026")],
    [_fund() for _ in range(21)], {"funds": []}, "texto", [None], [[]],
    [_fund(name="x" * 5000)],
])
def test_nav_problems_rechaza_lo_que_no_cumple_el_contrato(bad):
    assert pv.nav_problems(bad, NOW.date())


def test_nav_problems_acepta_price_null_y_20_fondos_y_lista_vacia():
    assert pv.nav_problems([_fund(price=None, source="none")], NOW.date()) == []
    assert pv.nav_problems([_fund(isin="IE0FAKE%05d" % i) for i in range(20)], NOW.date()) == []
    assert pv.nav_problems([], NOW.date()) == []


def test_nav_problems_rechaza_mas_de_64_kb_aunque_el_esquema_sea_valido(monkeypatch):
    monkeypatch.setattr(pv, "MAX_NAV_BYTES", 500)
    assert any("64" in p or "bytes" in p for p in pv.nav_problems([_fund(isin="IE0FAKE%05d" % i) for i in range(10)], NOW.date()))


def test_publish_nav_no_envia_un_fichero_manipulado(nav, tmp_path, capsys):
    work = tmp_path / "work"
    work.mkdir()
    stats = {"funds": 1, "exact": 1, "latest": "2026-10-23", "built_at": "x"}
    bad = [{"user": "usuario-1", "funds": [_fund(series=[{"symbol": "X"}])]}, {"user": "con espacios", "funds": [_fund()]}, {"user": "usuario-3", "funds": [_fund(price=0)]}]
    (work / "nav_payloads.json").write_text(json.dumps({"stats": stats, "items": bad}))
    assert pv.main(["publish", "--nav-only", "--dir", str(work)], env=nav.env(), now=NOW) == 0
    assert nav.reqs == [] and "3 fallidos" in capsys.readouterr().out


@pytest.mark.parametrize("var", ["SUPABASE_URL", "SUPABASE_PUBLISHABLE_KEY", "PERSONAL_FEED_TOKEN"])
def test_modo_nav_sin_configuracion_solo_avisa_y_no_envia_nada(nav, tmp_path, capsys, var):
    rc, spy = run_nav(nav, tmp_path, **{var: None})
    assert rc == 0 and nav.reqs == [] and spy.nav_calls == [] and capsys.readouterr().out.count("::warning::") == 1


def test_modo_nav_clave_secreta_aborta_sin_enviar(nav, tmp_path):
    rc, _ = run_nav(nav, tmp_path, SUPABASE_PUBLISHABLE_KEY="sb_secret_FAKE0123456789")
    assert rc == 1 and nav.reqs == []


def test_rpc_error_guarda_estado_y_codigo_pero_nunca_el_cuerpo(nav):
    nav.nav_reply = (404, {"code": "PGRST202", "message": "secreto del servidor 12345"})
    with pytest.raises(pv.RpcError) as ei:
        pv.rpc({"url": nav.url, "key": PUB_KEY, "token": TOKEN}, pv.RPC_PUBLISH_NAV, {"p_token": TOKEN})
    e = ei.value
    assert (e.status, e.code, str(e)) == (404, "PGRST202", "HTTP 404") and "secreto" not in repr(e.__dict__)
    nav.nav_reply = (400, {"code": "esto no es un código ni de lejos"})
    with pytest.raises(pv.RpcError) as ei:
        pv.rpc({"url": nav.url, "key": PUB_KEY, "token": TOKEN}, pv.RPC_PUBLISH_NAV, {"p_token": TOKEN})
    assert ei.value.code is None


# ------------------------------------------------------------------ el job completo no ha cambiado
def test_el_job_completo_publica_exactamente_lo_mismo_que_antes_de_g2(tmp_path):
    """La referencia (tests/fixtures_personal_full_ref.json) se generó con scripts/personal_v2.py de `main` ANTES de este cambio, con el mismo escenario."""
    sb = FakeSupabase()
    try:
        sb.watch = [watch_row("usuario-1", funds=[{"isin": ISIN_A, "name": "Fondo Falso A", "url": URL_A, "proxy_symbol": "FAKEPX"}], symbols=["FAKE1", "AAPL"]),
                    watch_row("usuario-2", funds=[{"isin": ISIN_B, "name": "Fondo B", "url": URL_B, "prev_price": 25.0},
                                                  {"isin": "IE0FAKE00003", "name": "Sin ficha", "url": "http://evil.example/x"}], symbols=[])]
        spy = Spy()
        spy.long = long_bars(5)
        rc, _ = run_all(sb, tmp_path, spy)
        got = [{"p_user": r[2]["p_user"], "p_payload": r[2]["p_payload"]} for r in sb.calls(pv.RPC_PUBLISH)]
        assert rc == 0 and sb.calls(pv.RPC_PUBLISH_NAV) == []
    finally:
        sb.close()
    ref = json.loads((ROOT / "tests" / "fixtures_personal_full_ref.json").read_text(encoding="utf-8"))
    assert got == ref and len(got[0]["p_payload"]["series"]) == 2                                   # proxy (histórico largo) + extra
    assert set(got[0]["p_payload"]) == {"schema", "generated_at", "funds", "series", "public"}


def test_control_negativo_la_referencia_detecta_un_cambio_en_el_payload_completo(tmp_path):
    sb = FakeSupabase()
    try:
        sb.watch = [watch_row("usuario-1", funds=[{"isin": ISIN_A, "name": "Fondo Falso A", "url": URL_A, "proxy_symbol": "FAKEPX"}], symbols=["FAKE1", "AAPL"])]
        spy = Spy(nav_price=26.0)                                                                    # un NAV distinto => payload distinto
        spy.long = long_bars(5)
        run_all(sb, tmp_path, spy)
        got = [{"p_user": r[2]["p_user"], "p_payload": r[2]["p_payload"]} for r in sb.calls(pv.RPC_PUBLISH)]
    finally:
        sb.close()
    ref = json.loads((ROOT / "tests" / "fixtures_personal_full_ref.json").read_text(encoding="utf-8"))
    assert got[0] != ref[0]


def test_el_modo_completo_no_toca_nav_payloads_ni_la_rpc_de_nav(nav, tmp_path):
    nav.watch = [watch_row()]
    rc, _ = run_all(nav, tmp_path)
    assert rc == 0 and nav.calls(pv.RPC_PUBLISH_NAV) == [] and not (tmp_path / "work" / "nav_payloads.json").exists()


# ------------------------------------------------------------------ latido opcional (paso del workflow)
def _hb_script():
    wf = yaml.safe_load((WF / "personal-nav.yml").read_text(encoding="utf-8"))
    step = next(s for s in wf["jobs"]["personal-nav"]["steps"] if s.get("name") == "Heartbeat (optional)")
    assert step["env"]["HC"] == "${{ secrets.HEALTHCHECK_PERSONAL_NAV_URL }}" and step["if"] == "${{ always() && !cancelled() }}"      # H5: avisa también si falla (ver test_observabilidad_H5)
    return step["run"]


def _run_hb(tmp_path, hc):
    b = tmp_path / "bin"
    b.mkdir(exist_ok=True)
    curl = b / "curl"
    curl.write_text('#!/bin/bash\nprintf "%s\\n" "$*" >> "$CALLS"\n')
    curl.chmod(curl.stat().st_mode | stat.S_IEXEC)
    calls = tmp_path / "calls.log"
    calls.write_text("")
    script = tmp_path / "hb.sh"
    script.write_text(_hb_script())
    env = {"PATH": str(b) + ":" + os.environ["PATH"], "HOME": str(tmp_path), "CALLS": str(calls)}
    if hc is not None:
        env["HC"] = hc
    r = subprocess.run(["bash", "-e", str(script)], cwd=tmp_path, env=env, capture_output=True, text=True, timeout=30)
    return r, calls.read_text()


def test_latido_vacio_o_inexistente_no_hace_nada(tmp_path):
    for hc in (None, ""):
        r, calls = _run_hb(tmp_path, hc)
        assert r.returncode == 0 and calls == "" and r.stdout == "" and r.stderr == ""


def test_latido_con_url_https_hace_un_ping(tmp_path):
    r, calls = _run_hb(tmp_path, "https://hc.example.test/ping/abc-123")
    assert r.returncode == 0 and calls.strip().endswith("https://hc.example.test/ping/abc-123") and "-fsS" in calls


@pytest.mark.parametrize("hc", ["http://hc.example.test/x", "ftp://x", "hc.example.test/x", "https://", "https://hc.example.test/x y", "$(touch PWNED)", "-o /tmp/x"])
def test_latido_con_url_rara_avisa_sin_mostrar_el_valor_y_no_llama_a_curl(tmp_path, hc):
    r, calls = _run_hb(tmp_path, hc)
    assert r.returncode == 0 and calls == "" and "::warning::" in r.stdout and hc not in (r.stdout + r.stderr).replace("https://...", "") and not list(tmp_path.glob("PWNED*"))
