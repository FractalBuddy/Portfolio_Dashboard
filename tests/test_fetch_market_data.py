"""Tests de scripts/fetch_market_data.py con datos sintéticos (sin Yahoo, sin FRED, sin internet)."""
import datetime as dt
import http.server
import json
import threading
import time

import pytest

import fetch_market_data as fm


# ============================================================ A4: FRED caído -> reutilizar el feed anterior
def _prev(stale=False):
    rates = [{"series": s, "name": n, "value": 4.0, "prev": 3.9, "date": "2026-10-01"} for s, n in fm.RATES]
    if stale:
        for r in rates:
            r.update({"stale": True, "stale_since": "2026-09-20"})
    cpi = [{"date": "2025-%02d-01" % m, "value": 100.0 + m} for m in range(1, 13)]
    meta = {"source": "x", "series": "S", "last_date": "2025-12-01", "stale": False}
    if stale:
        meta.update({"stale": True, "reused_from_previous": True, "stale_since": "2026-09-20"})
    return {"generated_at": "2026-10-05T20:17:00+00:00", "rates": rates, "cpi": cpi, "cpi_meta": meta}


EMPTY_META = {"source": fm.CPI_SOURCE_LABEL, "series": fm.CPI_SERIES_ID, "last_date": None, "stale": True}


def test_fred_caido_con_anterior_reutiliza_tipos_e_ipc_marcados_stale():
    rates, cpi, meta = fm.reuse_previous_fred([], [], dict(EMPTY_META), _prev())
    assert [r["series"] for r in rates] == [s for s, _ in fm.RATES]
    assert all(r["stale"] is True and r["stale_since"] == "2026-10-05" for r in rates)
    assert len(cpi) == 12
    assert meta["stale"] is True and meta["reused_from_previous"] is True
    assert meta["last_date"] == "2025-12-01" and meta["stale_since"] == "2026-10-05"


def test_reutilizaciones_sucesivas_conservan_stale_since():
    rates, cpi, meta = fm.reuse_previous_fred([], [], dict(EMPTY_META), _prev(stale=True))
    assert all(r["stale_since"] == "2026-09-20" for r in rates)
    assert meta["stale_since"] == "2026-09-20"


def test_fred_parcial_solo_rellena_las_series_que_faltan_y_no_toca_las_frescas():
    fresh = [{"series": "DGS10", "name": "10y", "value": 5.5, "prev": 5.4, "date": "2026-10-06"}]
    cpi_new = [{"date": "2026-09-01", "value": 130.0}] * 13
    meta_new = {"source": "s", "series": "S", "last_date": "2026-09-01", "stale": False}
    rates, cpi, meta = fm.reuse_previous_fred(fresh, cpi_new, dict(meta_new), _prev())
    by = {r["series"]: r for r in rates}
    assert by["DGS10"]["value"] == 5.5 and "stale" not in by["DGS10"]
    assert by["DGS2"]["stale"] is True
    assert [r["series"] for r in rates] == [s for s, _ in fm.RATES]  # orden de RATES
    assert cpi is cpi_new and meta["stale"] is False                  # IPC nuevo intacto


def test_sin_anterior_no_se_inventa_nada():
    for prev in ({}, None, {"rates": "basura", "cpi": [1, 2]}, {"generated_at": "x", "cpi": [{"date": "2025-01-01", "value": 1}] * 3}):
        rates, cpi, meta = fm.reuse_previous_fred([], [], dict(EMPTY_META), prev)
        assert rates == [] and cpi == [] and meta == EMPTY_META


def test_anterior_con_tipos_basura_no_explota():
    prev = {"generated_at": "2026-10-05T00:00:00", "rates": [None, 3, {"series": "DGS10", "value": "x"}, {"series": "DGS2", "value": 1.0}]}
    rates, _, _ = fm.reuse_previous_fred([], [], dict(EMPTY_META), prev)
    assert [r["series"] for r in rates] == ["DGS2"]


def test_load_prev_feed_tolerante(tmp_path, monkeypatch):
    monkeypatch.setenv("PREV_FEED_PATH", str(tmp_path / "no_existe.json"))
    assert fm._load_prev_feed() == {}
    p = tmp_path / "p.json"
    p.write_text("<html>404")
    monkeypatch.setenv("PREV_FEED_PATH", str(p))
    assert fm._load_prev_feed() == {}
    p.write_text("[1,2]")
    assert fm._load_prev_feed() == {}
    p.write_text(json.dumps({"a": 1}))
    assert fm._load_prev_feed() == {"a": 1}


def _patch_main(monkeypatch, tmp_path):
    """main() sin red: todo lo que toca Yahoo/RSS/CNN se sustituye por datos mínimos."""
    monkeypatch.chdir(tmp_path)
    row = lambda items: [{"symbol": i[0], "name": i[1], "price": 1.0, "change_pct": 0.0, "history": []} for i in items]  # noqa: E731
    monkeypatch.setattr(fm, "build_list", row)
    monkeypatch.setattr(fm, "attach_histories", lambda x: x)
    monkeypatch.setattr(fm, "build_your_funds", lambda: [])
    monkeypatch.setattr(fm, "build_general_news", lambda: [])
    monkeypatch.setattr(fm, "build_position_news", lambda: [])
    monkeypatch.setattr(fm, "build_benchmarks", lambda: {"sp500": [], "msci_world": []})
    monkeypatch.setattr(fm, "build_market_mood", lambda: {})


def test_main_con_fred_caido_publica_tipos_e_ipc_del_anterior(tmp_path, monkeypatch):
    _patch_main(monkeypatch, tmp_path)
    (tmp_path / "prev.json").write_text(json.dumps(_prev()))
    monkeypatch.setenv("PREV_FEED_PATH", str(tmp_path / "prev.json"))
    monkeypatch.setattr(fm, "FRED_API_KEY", "clave-falsa")
    monkeypatch.setattr(fm, "build_rates", lambda: [])                       # FRED caído
    monkeypatch.setattr(fm, "fetch_cpi_history", lambda: ([], dict(EMPTY_META)))
    fm.main()
    d = json.loads((tmp_path / "data.json").read_text())
    assert len(d["rates"]) == len(fm.RATES) and all(r["stale"] for r in d["rates"])
    assert len(d["cpi"]) == 12 and d["cpi_meta"]["stale"] and d["cpi_meta"]["reused_from_previous"]
    assert "clave-falsa" not in (tmp_path / "data.json").read_text()


def test_main_sin_clave_fred_no_reutiliza(tmp_path, monkeypatch):
    _patch_main(monkeypatch, tmp_path)
    (tmp_path / "prev.json").write_text(json.dumps(_prev()))
    monkeypatch.setenv("PREV_FEED_PATH", str(tmp_path / "prev.json"))
    monkeypatch.setattr(fm, "FRED_API_KEY", "")
    fm.main()
    d = json.loads((tmp_path / "data.json").read_text())
    assert d["rates"] == [] and d["cpi"] == []


def test_main_con_fred_caido_y_sin_anterior_queda_vacio(tmp_path, monkeypatch):
    _patch_main(monkeypatch, tmp_path)
    monkeypatch.setenv("PREV_FEED_PATH", str(tmp_path / "nada.json"))
    monkeypatch.setattr(fm, "FRED_API_KEY", "clave-falsa")
    monkeypatch.setattr(fm, "build_rates", lambda: [])
    monkeypatch.setattr(fm, "fetch_cpi_history", lambda: ([], dict(EMPTY_META)))
    fm.main()
    d = json.loads((tmp_path / "data.json").read_text())
    assert d["rates"] == [] and d["cpi"] == []   # el sanity-check de publish lo bloqueará como hasta ahora


# ============================================================ A6: scrape de iShares
ISIN = "IE000TEST0001"
TODAY = dt.date(2026, 10, 6)


def _page(nav="12.3456", date="03/Oct/2026", change="-0.85", wk="10.10 - 13.90", isin=ISIN):
    return ("Fund %s NAV as of %s EUR %s 1 Day NAV Change as of %s EUR -0.10 (%s%%) 52 WK: %s Net Assets"
            % (isin, date, nav, date, change, wk))


def test_ishares_pagina_sana():
    r = fm.parse_ishares_nav(_page(), ISIN, today=TODAY)
    assert r == {"price": 12.3456, "nav_date": "03/Oct/2026", "nav_stale": False,
                 "change_pct": -0.85, "wk52_low": 10.10, "wk52_high": 13.90}


@pytest.mark.parametrize("change", ["0.8.0", "abc", "1.2.3"])
def test_ishares_variacion_malformada_no_pierde_el_nav(change):
    r = fm.parse_ishares_nav(_page(change=change), ISIN, today=TODAY)
    assert r is not None and r["price"] == 12.3456 and "change_pct" not in r


@pytest.mark.parametrize("wk", ["1.2.3 - 4", "x - y", "20.0 - 10.0", "1.00 - 2.00"])
def test_ishares_rango_52_semanas_malformado_o_inverosimil_no_pierde_el_nav(wk):
    r = fm.parse_ishares_nav(_page(wk=wk), ISIN, today=TODAY)
    assert r is not None and r["price"] == 12.3456 and "wk52_low" not in r and "wk52_high" not in r
    assert r["change_pct"] == -0.85  # el otro campo secundario sigue vivo


@pytest.mark.parametrize("change,ok", [("19.9", True), ("-19.9", True), ("35.0", False), ("-20.5", False)])
def test_ishares_plausibilidad_de_variacion_diaria(change, ok):
    r = fm.parse_ishares_nav(_page(change=change), ISIN, today=TODAY)
    assert ("change_pct" in r) is ok and r["price"] == 12.3456


def test_ishares_coma_decimal_en_variacion():
    assert fm.parse_ishares_nav(_page(change="0,85"), ISIN, today=TODAY)["change_pct"] == 0.85


@pytest.mark.parametrize("date,stale", [("03/Oct/2026", False), ("01/Oct/2026", False), ("30/Sep/2026", True), ("01/Jan/2026", True)])
def test_ishares_nav_stale_si_mas_de_5_dias(date, stale):
    assert fm.parse_ishares_nav(_page(date=date), ISIN, today=TODAY)["nav_stale"] is stale


def test_ishares_fecha_ilegible_no_marca_stale_ni_falla():
    r = fm.parse_ishares_nav(_page(date="32/Foo/2026"), ISIN, today=TODAY)
    assert r["price"] == 12.3456 and "nav_stale" not in r


@pytest.mark.parametrize("plain", [_page(nav="0.0001"), _page(nav="99999"), _page(nav="1.2.3"), _page(isin="IE000OTRO"), "nada de nada"])
def test_ishares_nav_invalido_devuelve_none(plain):
    assert fm.parse_ishares_nav(plain, ISIN, today=TODAY) is None


def test_fetch_ishares_nav_con_html_sintetico(monkeypatch):
    html = "<html><body><div>%s</div><span>NAV as of</span> <b>%s</b></body></html>" % (
        "Fund " + ISIN, "03/Oct/2026 EUR 12.3456")
    class R:  # noqa: E301
        text = html
        def raise_for_status(self): pass  # noqa: E704
    monkeypatch.setattr(fm.requests, "get", lambda *a, **k: R())
    r = fm.fetch_ishares_nav("https://x.test", ISIN)
    assert r["price"] == 12.3456 and r["nav_date"] == "03/Oct/2026"
    def boom(*a, **k): raise RuntimeError("red caída")  # noqa: E301,E704
    monkeypatch.setattr(fm.requests, "get", boom)
    assert fm.fetch_ishares_nav("https://x.test", ISIN) is None


def test_build_your_funds_propaga_nav_stale(monkeypatch):
    monkeypatch.setattr(fm, "fetch_ishares_nav", lambda url, isin: {"price": 10.0, "nav_date": "01/Jan/2026", "nav_stale": True})
    monkeypatch.setattr(fm, "fetch_ishares_fund_facts", lambda url: {})
    monkeypatch.setattr(fm, "compute_fund_analytics", lambda s: {})
    monkeypatch.setattr(fm, "fetch_full_history", lambda s: [])
    out = fm.build_your_funds()
    assert all(f["source"] == "ishares_nav_scrape" and f["nav_stale"] is True for f in out)


# ============================================================ A5: presupuesto de tiempo de las noticias
RSS = b"""<?xml version="1.0"?><rss version="2.0"><channel><title>Feed de prueba</title>
<item><title>Hola</title><link>https://x.test/1</link></item>
<item><title>Mala</title><link>javascript:alert(1)</link></item></channel></rss>"""


class _Handler(http.server.BaseHTTPRequestHandler):
    hits = 0
    mode = "ok"

    def log_message(self, *a):
        pass

    def do_GET(self):  # noqa: N802
        type(self).hits += 1
        if self.mode == "500":
            self.send_response(500); self.end_headers(); return  # noqa: E702
        self.send_response(200)
        self.send_header("Content-Type", "application/rss+xml")
        if self.mode == "ok":
            self.send_header("Content-Length", str(len(RSS))); self.end_headers(); self.wfile.write(RSS); return  # noqa: E702
        self.end_headers()
        try:
            if self.mode == "drip":      # gotea 1 byte cada 0,3 s sin terminar nunca
                for _ in range(200):
                    self.wfile.write(b" "); self.wfile.flush(); time.sleep(0.3)  # noqa: E702
            elif self.mode == "huge":
                for _ in range(100):
                    self.wfile.write(b"x" * 100_000); self.wfile.flush()  # noqa: E702
        except OSError:
            pass


@pytest.fixture()
def server(monkeypatch):
    monkeypatch.setenv("NO_PROXY", "127.0.0.1,localhost")
    monkeypatch.setenv("no_proxy", "127.0.0.1,localhost")
    monkeypatch.setattr(fm, "_NEWS_DEADLINE", None)
    _Handler.hits, _Handler.mode = 0, "ok"
    srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield "http://127.0.0.1:%d/feed" % srv.server_address[1]
    srv.shutdown()


def test_parse_feed_normal_y_filtra_enlaces_no_http(server):
    items = fm.parse_feed(server)
    assert [i["title"] for i in items] == ["Hola"] and items[0]["source"] == "Feed de prueba"


def test_parse_feed_error_http_devuelve_vacio(server):
    _Handler.mode = "500"
    assert fm.parse_feed(server) == []


def test_parse_feed_servidor_que_gotea_se_corta_por_tiempo_total(server, monkeypatch):
    _Handler.mode = "drip"
    monkeypatch.setattr(fm, "FEED_MAX_SECONDS", 1)
    t0 = time.monotonic()
    assert fm.parse_feed(server) == []
    assert time.monotonic() - t0 < 5   # sin el tope total tardaría ~60 s (200 x 0,3 s)


def test_parse_feed_respuesta_enorme_se_corta_por_tamano(server, monkeypatch):
    _Handler.mode = "huge"
    monkeypatch.setattr(fm, "FEED_MAX_BYTES", 500_000)
    assert fm.parse_feed(server) == []


def test_parse_feed_salta_sin_tocar_la_red_pasado_el_deadline(server, monkeypatch):
    monkeypatch.setattr(fm, "_DEADLINE", time.monotonic() - 1)
    assert fm.parse_feed(server) == [] and _Handler.hits == 0
    monkeypatch.setattr(fm, "_DEADLINE", time.monotonic() + 600)
    monkeypatch.setattr(fm, "_NEWS_DEADLINE", time.monotonic() - 1)   # presupuesto propio de noticias agotado
    assert fm.parse_feed(server) == [] and _Handler.hits == 0
    monkeypatch.setattr(fm, "_NEWS_DEADLINE", time.monotonic() + 60)
    assert len(fm.parse_feed(server)) == 1 and _Handler.hits == 1


def test_main_fija_el_presupuesto_de_noticias(tmp_path, monkeypatch):
    _patch_main(monkeypatch, tmp_path)
    monkeypatch.setattr(fm, "FRED_API_KEY", "")
    monkeypatch.setattr(fm, "_NEWS_DEADLINE", None)
    monkeypatch.setattr(fm, "_DEADLINE", time.monotonic() + 3600)
    t0 = time.monotonic()
    fm.main()
    assert fm._NEWS_DEADLINE is not None and fm._NEWS_DEADLINE - t0 <= fm.NEWS_BUDGET_SECONDS + 5


# ============================================================ funciones puras ya existentes (regresión)
def test_parse_num_y_sanitize():
    assert fm._parse_num("1,234.56") == 1234.56 and fm._parse_num("12,3000") == 12.3 and fm._parse_num("1.2.3") is None
    assert fm._parse_signed("-0.85") == -0.85 and fm._parse_signed("+1.5") == 1.5 and fm._parse_signed("0.8.0") is None
    assert fm.sanitize_for_json({"a": [float("nan"), float("inf"), 1.5]}) == {"a": [None, None, 1.5]}
