"""Sesión F, tarea 1: desfase de fecha del NAV (navegador 28/Oct, scraper 27/Oct). Variantes de la petición a la ficha, elección del NAV más reciente,
log del build y diagnóstico manual (nav_probe.py + nav-probe.yml). Sin red real: respuestas simuladas con el texto REAL de las fichas guardadas
(tests/fixtures_fund_facts.json) con la fecha cambiada."""
import io
import contextlib
import json
import pathlib
import re
from datetime import date, datetime, timezone

import pytest

import fetch_market_data as fm
import nav_probe as npb
import personal_v2 as pv
from test_personal_v2 import NOW, Spy, FakeSupabase, watch_row

ROOT = pathlib.Path(__file__).resolve().parent.parent
FIX = json.loads((ROOT / "tests" / "fixtures_fund_facts.json").read_text(encoding="utf-8"))
ISIN = "IE000ZYRH0Q7"                 # ISIN de la ficha real guardada (clase S de ejemplo, ya presente en el repo)
URL = "https://www.ishares.com/ch/individual/en/products/345277/ishares-developed-world-index-fund-ie"
OLD, NEW = "20/Oct/2026", "28/Oct/2026"


def html(date_str=OLD, extra=""):
    return "<html><head></head><body><script>var cfg = 1;%s</script><div>%s</div></body></html>" % (extra, FIX["dev"].replace(OLD, date_str))


class Resp:
    def __init__(self, text, status=200):
        self.text, self.status_code = text, status

    def raise_for_status(self):
        if self.status_code >= 400:
            import requests
            e = requests.HTTPError("x")
            e.response = self
            raise e


@pytest.fixture(autouse=True)
def _sin_esperas(monkeypatch):
    import time
    monkeypatch.setattr(time, "sleep", lambda s: None)
    monkeypatch.setattr(fm, "_nap", lambda s: None)


# ====================================================================== 1. variantes de la petición
def test_base_es_la_peticion_de_siempre():
    assert fm.ishares_request(URL) == (URL, {"User-Agent": fm.ISHARES_UA})


def test_variantes_de_cabeceras_y_parametro():
    u, h = fm.ishares_request(URL, "nocache")
    assert u == URL and h["Cache-Control"] == "no-cache" and h["Pragma"] == "no-cache" and h["User-Agent"] == fm.ISHARES_UA
    u, h = fm.ishares_request(URL, "bust", now_ms=1761600000000)
    assert u == URL + "?cb=1761600000000" and "Cache-Control" not in h
    u, h = fm.ishares_request(URL, "fresh", now_ms=5)
    assert u == URL + "?cb=5" and h["Cache-Control"] == "no-cache" and h["Pragma"] == "no-cache"
    u, h = fm.ishares_request(URL + "?a=1", "bust", now_ms=5)
    assert u == URL + "?a=1&cb=5"
    u, h = fm.ishares_request(URL, "browser")
    assert "Chrome" in h["User-Agent"] and h["Accept-Language"].startswith("en") and "Cache-Control" not in h
    u, h = fm.ishares_request(URL, "browser_fresh", now_ms=7)
    assert "Chrome" in h["User-Agent"] and u.endswith("?cb=7") and h["Cache-Control"] == "no-cache"
    assert fm.ishares_request(URL, "passthrough")[0] == URL + "?siteEntryPassthrough=true"
    with pytest.raises(ValueError):
        fm.ishares_request(URL, "inventada")


def test_el_scraper_no_se_hace_pasar_por_un_navegador_por_defecto():
    assert "browser" not in fm.NAV_FETCH_VARIANTS and fm.NAV_FETCH_VARIANTS == ("base", "fresh")


def test_plain_pide_cada_variante_con_su_url_y_cabeceras_y_las_cachea_por_separado(monkeypatch):
    calls = []
    monkeypatch.setattr(fm.requests, "get", lambda url, headers=None, timeout=None: calls.append((url, headers)) or Resp(html()))
    fm._ishares_plain(URL, ISIN)
    fm._ishares_plain(URL, ISIN)
    fm._ishares_plain(URL, ISIN, "fresh")
    fm._ishares_plain(URL, ISIN, "fresh")
    assert len(calls) == 2 and calls[0] == (URL, {"User-Agent": fm.ISHARES_UA})
    assert "?cb=" in calls[1][0] and calls[1][1]["Cache-Control"] == "no-cache"


# ====================================================================== 2. se queda con el NAV más reciente
def serve(monkeypatch, base=OLD, fresh=OLD, base_status=200, fresh_status=200):
    def get(url, headers=None, timeout=None):
        if "cb=" in url:
            return Resp(html(fresh), fresh_status)
        return Resp(html(base), base_status)
    monkeypatch.setattr(fm.requests, "get", get)


def freshest(monkeypatch=None):
    with contextlib.redirect_stderr(io.StringIO()):
        return fm.fetch_ishares_nav_freshest(URL, ISIN)


def test_si_la_variante_fresca_trae_un_nav_mas_nuevo_gana(monkeypatch):
    serve(monkeypatch, base="27/Oct/2026", fresh=NEW)
    r = freshest()
    assert r["nav_date_iso"] == "2026-10-28" and r["nav_variant"] == "fresh" and r["nav_gain_days"] == 1 and r["price"] == 12.48


def test_empate_de_fechas_gana_la_fresca_sin_ganancia(monkeypatch):
    serve(monkeypatch, base=NEW, fresh=NEW)
    r = freshest()
    assert r["nav_variant"] == "fresh" and "nav_gain_days" not in r


def test_si_la_fresca_va_por_detras_se_queda_la_base(monkeypatch):
    serve(monkeypatch, base=NEW, fresh="27/Oct/2026")
    r = freshest()
    assert r["nav_variant"] == "base" and r["nav_date_iso"] == "2026-10-28" and "nav_gain_days" not in r


@pytest.mark.parametrize("kw,variant", [(dict(fresh_status=403), "base"), (dict(base_status=403), "fresh")])
def test_si_una_variante_falla_vale_la_otra(monkeypatch, kw, variant):
    serve(monkeypatch, base=NEW, fresh=NEW, **kw)
    assert freshest()["nav_variant"] == variant


def test_si_todas_fallan_es_none_y_no_lanza(monkeypatch):
    serve(monkeypatch, base_status=403, fresh_status=403)
    assert freshest() is None


def test_la_ficha_ganadora_es_la_que_usan_los_facts(monkeypatch):
    serve(monkeypatch, base="27/Oct/2026", fresh=NEW)
    freshest()
    assert NEW in fm._ishares_plain(URL, "facts") and "27/Oct/2026" not in fm._ishares_plain(URL, "facts")


def test_el_scraper_de_datos_propios_usa_la_busqueda_del_nav_mas_reciente():
    import inspect
    assert "fetch_ishares_nav_freshest" in inspect.getsource(pv.default_fetchers)


# ====================================================================== 3. el log del build
def test_log_indica_variante_ganadora_y_adelanto_sin_identificar_fondos(tmp_path, capsys):
    sb = FakeSupabase()
    try:
        sb.watch = [watch_row("usuario-1")]
        spy = Spy()
        base = spy.nav
        spy.nav = lambda url, isin: dict(base(url, isin), nav_variant="fresh", nav_gain_days=1)
        reg = tmp_path / "r.json"
        reg.write_text(json.dumps({"schema": 1, "symbols": [{"symbol": "AAPL", "kind": "yahoo", "name": "Apple", "currency": "USD", "unit": "currency", "tier": "hot",
                                                              "status": "active", "fail_streak": 0}]}))
        assert pv.main(["all", "--dir", str(tmp_path / "w"), "--registry", str(reg)], fetchers=spy.fetchers(), env=sb.env(), now=NOW) == 0
    finally:
        sb.close()
    out = capsys.readouterr().out
    assert "fecha de NAV más reciente 2026-10-23" in out and "más antigua 2026-10-23" in out
    assert "variante de la petición ganadora: fresh x1" in out and "adelantó la fecha en 1 fondos (hasta +1 d)" in out
    [pub] = sb.calls(pv.RPC_PUBLISH)
    f = pub[2]["p_payload"]["funds"][0]
    assert "nav_variant" not in f and "nav_gain_days" not in f


def test_log_con_fechas_distintas_da_la_mas_reciente_y_la_mas_antigua():
    f = lambda d: {"source": "ishares_nav_scrape", "nav_date_iso": d, "price": 10.0, "isin": "X", "name": "n", "currency": "EUR", "unit": "currency"}      # noqa: E731
    lines, _ = pv.nav_report([{"user": "u", "payload": {"funds": [f("2026-10-28"), f("2026-10-27")]}}], datetime(2026, 10, 28, 12, tzinfo=timezone.utc))
    assert "fecha de NAV más reciente 2026-10-28 (0 d), más antigua 2026-10-27 (1 d)" in lines[0]


# ====================================================================== 4. diagnóstico manual (nav_probe.py)
def write_watch(tmp_path, url=URL, isin=ISIN):
    d = tmp_path / "personal"
    d.mkdir()
    (d / "watch.json").write_text(json.dumps([{"user_id": "u1", "funds": [{"isin": isin, "name": "Nombre Secreto", "url": url}], "symbols": []}]))
    return str(d)


def probe(tmp_path, responder, **kw):
    d = write_watch(tmp_path)
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        assert npb.run(d, http_get=responder, sleep=lambda s: None, now=datetime(2026, 10, 28, 12, tzinfo=timezone.utc), **kw) == 0
    return buf.getvalue()


def responder(base=OLD, fresh=NEW, extra="", xhr=None, seen=None):
    def get(url, headers):
        if seen is not None:
            seen.append((url, headers))
        if xhr and ".ajax" in url and "/products/345277/" in url and "1521942788811" in url:
            return 200, {"content-type": "application/json"}, xhr
        if "cb=" in url:
            return 200, {"age": "0", "cache-control": "no-store"}, html(fresh, extra)
        return 200, {"age": "5400", "cache-control": "max-age=7200", "date": "Wed, 28 Oct 2026 10:00:00 GMT"}, html(base, extra)
    return get


def test_probe_imprime_fecha_y_valor_por_variante_y_senala_la_ganadora(tmp_path):
    out = probe(tmp_path, responder(base="27/Oct/2026", fresh=NEW))
    assert re.search(r"#1 base\s+HTTP 200\s+\d+ KB\s+NAV 27/Oct/2026 = 12\.48", out)
    assert re.search(r"#1 fresh\s+HTTP 200\s+\d+ KB\s+NAV 28/Oct/2026 = 12\.48", out)
    for v in ("nocache", "bust", "browser", "browser_fresh", "passthrough", "base_again", "alt:uk", "alt:de", "alt:pro"):
        assert re.search(r"#1 %s\s+HTTP" % re.escape(v), out), v
    assert "age=5400" in out and "cache-control=max-age=7200" in out                       # cabeceras de caché (no identifican nada)
    assert "RESUMEN: fecha de NAV más reciente 28/Oct/2026" in out and "<-- la base va por detrás" in out
    assert re.search(r"VEREDICTO: .*bust x1", out) and "fresh x1" in out


def test_probe_sin_desfase_lo_dice(tmp_path):
    out = probe(tmp_path, responder(base=NEW, fresh=NEW))
    assert "VEREDICTO: ninguna variante supera a la base" in out and "<-- la base va por detrás" not in out


def test_probe_no_filtra_isin_nombres_urls_ni_tokens(tmp_path):
    xhr = json.dumps({"navData": {"navAsOf": NEW, "nav": 13.64}})
    extra = ' var u="/ch/individual/en/products/345277/ishares-developed-world-index-fund-ie/1521942788811.ajax?fileType=json&fileName=SECRETTICKER&tab=overview";'
    out = probe(tmp_path, responder(extra=extra, xhr=xhr))
    for secret in (ISIN, "Nombre Secreto", "345277", "ishares-developed-world-index-fund-ie", "SECRETTICKER", "https://", "www.ishares.com", "usuario", "u1"):
        assert secret not in out, secret
    assert "/products/*/*/1521942788811.ajax?fileType=json&fileName=*&tab=overview" in out


def test_probe_descubre_y_prueba_la_xhr_y_la_considera_mas_fresca(tmp_path):
    xhr = json.dumps({"navData": {"navAsOf": NEW, "nav": 13.64}})
    extra = ' var u="/ch/individual/en/products/345277/ishares-developed-world-index-fund-ie/1521942788811.ajax?fileType=json&tab=overview";'
    out = probe(tmp_path, responder(base="27/Oct/2026", fresh="27/Oct/2026", extra=extra, xhr=xhr))
    assert "XHR/JSON citados en el HTML de la ficha: 1" in out
    assert re.search(r"#1 xhr1\s+/ch/individual/en/products/\*/\*/1521942788811\.ajax\?fileType=json&tab=overview HTTP 200.*NAV 28/Oct/2026 = 13\.64", out)
    assert "RESUMEN: fecha de NAV más reciente 28/Oct/2026; la dan: xhr1" in out and "VEREDICTO: variantes que dan una fecha más reciente que la base (nº de fichas): xhr1 x1" in out


def test_probe_sin_xhr_con_la_bandera_no_las_busca(tmp_path):
    extra = ' var u="/ch/individual/en/products/345277/x/1521942788811.ajax";'
    assert "XHR" not in probe(tmp_path, responder(extra=extra), try_xhr=False)


def test_probe_un_fallo_de_red_solo_imprime_el_nombre_de_la_excepcion(tmp_path):
    def get(url, headers):
        raise ConnectionError("no se pudo conectar a https://www.ishares.com/ch/individual/en/products/345277/x")
    out = probe(tmp_path, get)
    assert "ERR ConnectionError" in out and "345277" not in out and "https://" not in out and "ninguna variante dio un NAV" in out


def test_probe_pagina_sin_nav_explica_por_que(tmp_path):
    out = probe(tmp_path, lambda url, headers: (200, {}, "<html>Please verify you are human</html>"))
    assert "sin NAV legible; ISIN en la página: no; «NAV as of» con fecha: 0" in out


def test_probe_respeta_max_funds_y_deduplica(tmp_path):
    d = tmp_path / "p"
    d.mkdir()
    rows = [{"user_id": "u%d" % i, "funds": [{"isin": ISIN, "name": "n", "url": URL}, {"isin": "IE000QAZP7L2", "name": "m", "url": URL.replace("345277", "345276")}], "symbols": []}
            for i in range(3)]
    (d / "watch.json").write_text(json.dumps(rows))
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        npb.run(str(d), max_funds=1, http_get=responder(), sleep=lambda s: None)
    out = buf.getvalue()
    assert "2 fichas distintas en la lista; se prueban las 1 primeras" in out and "#2" not in out


def test_probe_sin_lista_avisa_y_no_falla(tmp_path):
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        assert npb.run(str(tmp_path), http_get=responder()) == 0
    assert "::warning::" in buf.getvalue()


def test_probe_solo_lee_ishares_y_no_hay_rpc_de_escritura():
    src = (ROOT / "scripts" / "nav_probe.py").read_text(encoding="utf-8")
    assert "RPC_PUBLISH" not in src and "pv.rpc" not in src and "publish_personal_feed" not in src and "open(" in src and '"w"' not in src and "write_text" not in src


def test_alt_urls_shape_y_discover():
    labels = dict(npb.alt_urls(URL))
    assert labels["alt:uk"] == "https://www.ishares.com/uk/individual/en/products/345277/ishares-developed-world-index-fund-ie" and "alt:ch" not in labels
    assert labels["alt:pro"].startswith("https://www.ishares.com/ch/professional/en/products/")
    assert npb.alt_urls("https://www.ishares.com/otra/cosa") == []
    assert npb.shape("https://www.ishares.com/ch/individual/en/products/1/abc/99999999.ajax?fileName=TICKER&fileType=csv&x=1") == \
        "/ch/individual/en/products/*/*/99999999.ajax?fileName=*&fileType=csv&x=*"
    page = ('"/ch/a/1000000000001.ajax?tab=overview" "https://evil.example.com/x.json" "https://www.ishares.com/yy.json" "/z.ajax?fileType=xls" '
            "'12345678901.ajax' \"/ch/a/1000000000001.ajax?tab=overview\"")
    urls = npb.discover_xhr(page, URL)
    assert urls == ["https://www.ishares.com/ch/a/1000000000001.ajax?tab=overview", "https://www.ishares.com/yy.json", URL + "/12345678901.ajax"]


def test_lectura_de_json_y_csv_de_las_xhr():
    j = {"a": {"navAsOf": "28/Oct/2026", "nav": {"raw": 13.64}}, "b": {"navChange": 0.1, "date": "2026-10-27", "navPrice": "EUR 12.3"}, "c": [{"asOf": 1793145600000, "NAV": 5}]}
    got = npb.json_nav_candidates(j)
    assert (date(2026, 10, 28), 13.64) in got and (date(2026, 10, 27), 12.3) in got and (date(2026, 10, 28), 5.0) in got and len(got) == 3
    assert npb.json_nav_candidates({"x": [1, 2]}) == []
    assert npb.csv_latest('Date,NAV\n27/Oct/2026,12.30\n28/Oct/2026,12.48\n') == (date(2026, 10, 28), 12.48)
    assert npb.csv_latest("nada") is None


def test_el_diagnostico_no_acepta_hosts_ajenos():
    assert npb.host_ok("https://www.ishares.com/x") and not npb.host_ok("http://www.ishares.com/x") and not npb.host_ok("https://ishares.com.evil.com/x")
    assert not npb.host_ok("https://user:pw@www.ishares.com/x")


# ====================================================================== 5. el workflow de diagnóstico
def wf():
    import yaml
    return yaml.safe_load((ROOT / ".github" / "workflows" / "nav-probe.yml").read_text(encoding="utf-8"))


def test_workflow_probe_solo_manual_sin_escritura_y_con_token_solo_al_leer():
    w = wf()
    on = w.get("on", w.get(True))
    assert set(on) == {"workflow_dispatch"} and "schedule" not in on                                         # nunca programado
    assert w["permissions"] == {} and set(w["jobs"]) == {"probe"} and w["jobs"]["probe"]["permissions"] == {"contents": "read"}
    steps = w["jobs"]["probe"]["steps"]
    text = (ROOT / ".github" / "workflows" / "nav-probe.yml").read_text(encoding="utf-8")
    assert "upload-artifact" not in text and "pages" not in text.replace("pages: ", "") and "publish" not in " ".join(s.get("run", "") for s in steps)
    assert "contents: write" not in text and "service_role" not in text and "sb_secret" not in text
    by = {s["name"]: s for s in steps}
    watch, probe_step = by["Read personal watch list (token, stdlib only)"], by["Probe NAV date per request variant (third-party code, NO token)"]
    assert watch["run"].startswith("python3 -S scripts/personal_v2.py watch ") and "--github-output" not in watch["run"]
    assert set(watch["env"]) == {"SUPABASE_URL", "SUPABASE_PUBLISHABLE_KEY", "PERSONAL_FEED_TOKEN"}
    assert set(probe_step["env"]) == {"MAX_FUNDS", "CACHE_HEADERS", "TRY_XHR"} and "nav_probe.py" in probe_step["run"]
    assert text.count("secrets.PERSONAL_FEED_TOKEN") == 1 and text.count("vars.SUPABASE_PUBLISHABLE_KEY") == 1
    names = [s["name"] for s in steps]
    assert names.index(watch["name"]) < names.index(probe_step["name"])
    for s in steps:
        if "uses" in s:
            assert re.fullmatch(r"[\w.\-]+/[\w.\-]+@[0-9a-f]{40}", s["uses"]), s["uses"]
    assert set(on["workflow_dispatch"]["inputs"]) == {"max_funds", "cache_headers", "try_xhr"}


def test_el_resto_de_workflows_no_llaman_al_diagnostico():
    for p in (ROOT / ".github" / "workflows").glob("*.yml"):
        if p.name != "nav-probe.yml":
            assert "nav_probe" not in p.read_text(encoding="utf-8"), p.name
