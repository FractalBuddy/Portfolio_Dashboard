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
    row = lambda items, prev_rows=None: [{"symbol": i[0], "name": i[1], "price": 1.0, "change_pct": 0.0, "history": []} for i in items]  # noqa: E731
    monkeypatch.setattr(fm, "build_list", row)
    monkeypatch.setattr(fm, "attach_histories", lambda x, prev_rows=None: x)
    monkeypatch.setattr(fm, "attach_profiles", lambda stocks, prev=None: stocks)  # sin Yahoo (Ticker.info)
    monkeypatch.setattr(fm, "build_your_funds", lambda prev=None: [])
    monkeypatch.setattr(fm, "attach_fund_extras", lambda funds, prev=None, **k: funds)
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
    assert r == {"price": 12.3456, "nav_date": "03/Oct/2026", "nav_date_iso": "2026-10-03", "nav_stale": False,
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


@pytest.mark.parametrize("date,stale", [("03/Oct/2026", False), ("01/Oct/2026", False), ("30/Sep/2026", False), ("29/Sep/2026", True), ("01/Jan/2026", True)])
def test_ishares_nav_stale_si_mas_de_6_dias(date, stale):   # umbral 6 (antes 5): ver NAV_STALE_DAYS y F_nav/INFORME.md
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


# ============================================================ Altcoin Season Index
def _coin(i, symbol=None, name=None, price=1.0):
    return {"id": f"coin{i}", "symbol": symbol or f"c{i}", "name": name or f"Coin {i}", "current_price": price}


BTC = {"id": "bitcoin", "symbol": "btc", "name": "Bitcoin", "current_price": 100000.0}


def test_altcoin_season_cuenta_las_que_superan_a_btc_y_excluye_stables_y_envueltas():
    # 60 altcoins reales + 5 que NO cuentan (2 stables por símbolo/id, 1 envuelta por nombre, 1 por id de categoría...)
    alts = [_coin(i) for i in range(60)]
    junk = [_coin(900, "usdt", "Tether"), _coin(901, "wbtc", "Wrapped Bitcoin"), _coin(902, "zzz", "Wrapped Foo"),
            _coin(903, "abc", "Staked Thing"), _coin(904, "okc", "OK Coin")]
    coins = [BTC] + junk[:4] + alts[:20] + [junk[4]] + alts[20:]
    changes = {"BTC": 10.0}
    for i in range(60):
        changes[f"c{i}"] = 20.0 if i < 30 else 5.0   # las 50 primeras: 30 superan a BTC, 20 no
    changes["okc"] = 99.0                           # esta se excluye por id de categoría y no debe contar
    calls = []

    def change_fn(sym, price):
        calls.append(sym)
        return changes[sym.upper() if sym.upper() in changes else sym]

    r = fm.compute_altcoin_season(coins, {"coin904"}, change_fn=change_fn)
    assert r["total"] == 50 and r["outperforming"] == 30 and r["value"] == 60
    assert r["btc_change_pct"] == 10.0 and r["window_days"] == 90
    assert not ({"usdt", "wbtc", "zzz", "abc", "okc"} & set(calls))   # ni se consultan
    assert "c50" not in calls                                          # se para en 50 altcoins


def test_altcoin_season_empate_con_btc_no_cuenta_como_superar():
    coins = [BTC] + [_coin(i) for i in range(40)]
    r = fm.compute_altcoin_season(coins, set(), change_fn=lambda s, p: 10.0)
    assert r["outperforming"] == 0 and r["value"] == 0


def test_altcoin_season_sustituye_las_monedas_cuyo_ticker_no_se_puede_verificar():
    coins = [BTC] + [_coin(i) for i in range(70)]
    bad = {f"c{i}" for i in range(0, 70, 3)}               # un tercio sin dato fiable en Yahoo
    r = fm.compute_altcoin_season(coins, set(), change_fn=lambda s, p: None if s in bad or s == "xx" else (50.0 if s != "BTC" else 0.0))
    assert r["total"] == 46   # 70 candidatas - 24 sin dato fiable
    assert r["value"] == 100


def test_altcoin_season_sin_btc_o_sin_suficientes_monedas_devuelve_none():
    assert fm.compute_altcoin_season([_coin(1)], set(), change_fn=lambda s, p: 1.0) is None
    assert fm.compute_altcoin_season([BTC] + [_coin(i) for i in range(10)], set(), change_fn=lambda s, p: 1.0) is None
    assert fm.compute_altcoin_season([BTC] + [_coin(i) for i in range(60)], set(), change_fn=lambda s, p: None if s == "BTC" else 1.0) is None


def test_altcoin_season_timeout_corta_y_usa_lo_acumulado():
    coins = [BTC] + [_coin(i) for i in range(60)]
    n = {"k": 0}

    def change_fn(sym, price):
        if sym == "BTC":
            return 0.0
        n["k"] += 1
        if n["k"] > 35:
            raise TimeoutError("sin presupuesto")
        return 5.0

    r = fm.compute_altcoin_season(coins, set(), change_fn=change_fn)
    assert r["total"] == 35 and r["value"] == 100


def _hist(days, last, base_at_cutoff, last_date="2026-10-10", gap_days=0):
    import pandas as pd
    idx = pd.date_range(end=last_date, periods=days, freq="D", tz="UTC")
    vals = [base_at_cutoff] * days
    vals[-1] = last
    df = pd.DataFrame({"Close": vals}, index=idx)
    if gap_days:   # quita las barras de alrededor del corte (hueco en los datos)
        cutoff = idx[-1] - dt.timedelta(days=fm.ALT_WINDOW_DAYS)
        df = df[(df.index < cutoff - dt.timedelta(days=gap_days)) | (df.index > cutoff + dt.timedelta(days=gap_days))]
    return df


def test_alt_change_pct_calcula_a_90_dias(monkeypatch):
    monkeypatch.setattr(fm, "yf_history", lambda *a, **k: _hist(130, 150.0, 100.0))
    assert fm._alt_change_pct("eth", 150.0) == pytest.approx(50.0)


def test_alt_change_pct_descarta_ticker_de_otra_moneda_por_precio(monkeypatch):
    monkeypatch.setattr(fm, "yf_history", lambda *a, **k: _hist(130, 150.0, 100.0))
    assert fm._alt_change_pct("eth", 3.0) is None            # Yahoo dice 150, CoinGecko 3: no es la misma moneda
    assert fm._alt_change_pct("eth", 140.0) is not None      # dentro del 15 %


def test_alt_change_pct_sin_historial_suficiente_o_con_hueco(monkeypatch):
    monkeypatch.setattr(fm, "yf_history", lambda *a, **k: _hist(40, 150.0, 100.0))   # listada hace <90 d
    assert fm._alt_change_pct("new", 150.0) is None
    monkeypatch.setattr(fm, "yf_history", lambda *a, **k: _hist(130, 150.0, 100.0, gap_days=10))
    assert fm._alt_change_pct("gap", 150.0) is None


def test_build_altcoin_season_reutiliza_el_anterior_marcado_stale(monkeypatch):
    monkeypatch.setattr(fm, "_cg_markets", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("429")))
    monkeypatch.setattr(fm.time, "sleep", lambda s: None)
    prev = {"generated_at": "2026-10-10T20:17:00+00:00", "market_mood": {"altcoin_season": {"value": 42, "total": 50, "outperforming": 21}}}
    r = fm.build_altcoin_season(prev)
    assert r["value"] == 42 and r["stale"] is True and r["stale_since"] == "2026-10-10"
    assert fm.build_altcoin_season({}) is None


def test_build_altcoin_season_no_deja_disparado_el_circuit_breaker_de_yahoo(monkeypatch):
    monkeypatch.setattr(fm.time, "sleep", lambda s: None)
    monkeypatch.setattr(fm, "_cg_markets", lambda *a, **k: [BTC] + [_coin(i) for i in range(5)])
    fm._YF_FAIL_STREAK = 2

    def boom(coins, ids):
        fm._YF_FAIL_STREAK = 40
        return None
    monkeypatch.setattr(fm, "compute_altcoin_season", boom)
    fm.build_altcoin_season({})
    assert fm._YF_FAIL_STREAK == 2


def test_cnn_put_call_ratio_toma_el_ultimo_punto_y_es_tolerante():
    assert fm.cnn_put_call_ratio({"score": 63.2, "rating": "greed", "data": [{"x": 1, "y": 0.9}, {"x": 2, "y": 0.78}]}) == 0.78
    assert fm.cnn_put_call_ratio({"score": 63.2}) is None                       # sin serie
    assert fm.cnn_put_call_ratio({"data": []}) is None
    assert fm.cnn_put_call_ratio({"data": [{"y": "abc"}]}) is None
    assert fm.cnn_put_call_ratio({"data": [{"y": 63.0}]}) is None               # un score colado como ratio: inverosímil
    assert fm.cnn_put_call_ratio(None) is None


LIDO_LAST = {"data": {"timeUnix": 1791721319, "apr": 2.203}, "meta": {"symbol": "stETH"}}
LIDO_SMA = {"data": {"aprs": [{"timeUnix": 1, "apr": 2.2}], "smaApr": 2.2533}, "meta": {"symbol": "stETH"}}


def test_parse_lido_apr_formato_real_y_tolerancia():
    assert fm.parse_lido_apr(LIDO_LAST, LIDO_SMA) == {"apr": 2.203, "as_of": 1791721319, "source": "lido", "apr_sma7": 2.253}
    assert "apr_sma7" not in fm.parse_lido_apr(LIDO_LAST, None)                    # sin SMA seguimos con el último
    assert "apr_sma7" not in fm.parse_lido_apr(LIDO_LAST, {"data": {"smaApr": 400}})  # SMA inverosímil: se descarta
    assert fm.parse_lido_apr({"data": {"timeUnix": 1, "apr": 0}}, None) is None      # APR 0 / fuera de rango
    assert fm.parse_lido_apr({"data": {"timeUnix": 1, "apr": 99}}, None) is None
    assert fm.parse_lido_apr({"foo": 1}, None) is None
    assert fm.parse_lido_apr(None, None) is None


class _FakeResp:
    def __init__(self, payload, fail=False):
        self.payload, self.fail = payload, fail
    def raise_for_status(self):
        if self.fail:
            raise RuntimeError("HTTP 500")
    def json(self):
        return self.payload


def test_build_staking_ok_y_reutiliza_stale_si_falla(monkeypatch):
    monkeypatch.setattr(fm.requests, "get", lambda url, **kw: _FakeResp(LIDO_LAST if url.endswith("/last") else LIDO_SMA))
    assert fm.build_staking({})["eth_lido"]["apr"] == 2.203

    def boom(url, **kw):
        raise RuntimeError("sin red")
    monkeypatch.setattr(fm.requests, "get", boom)
    prev = {"generated_at": "2026-10-10T06:00:00+00:00", "staking": {"eth_lido": {"apr": 2.3, "as_of": 5, "source": "lido"}}}
    out = fm.build_staking(prev)["eth_lido"]
    assert out["apr"] == 2.3 and out["stale"] is True and out["stale_since"] == "2026-10-10"
    assert fm.build_staking({}) == {}                                              # sin valor previo: sin sección
    assert "stale" not in prev["staking"]["eth_lido"]                              # no muta el feed previo


def test_build_staking_sma_caida_no_tumba_el_apr(monkeypatch):
    monkeypatch.setattr(fm.requests, "get", lambda url, **kw: _FakeResp(LIDO_LAST) if url.endswith("/last") else _FakeResp(None, fail=True))
    out = fm.build_staking({})["eth_lido"]
    assert out["apr"] == 2.203 and "apr_sma7" not in out


def test_build_staking_proveedor_extra_y_fallo_aislado(monkeypatch):
    # un proveedor nuevo se registra en STAKING_PROVIDERS; si uno falla, los demás siguen publicándose
    def ok():
        return {"apr": 4.5, "source": "otro"}

    def roto():
        raise RuntimeError("API caída")
    monkeypatch.setattr(fm, "STAKING_PROVIDERS", {"eth_lido": roto, "dot_x": ok})
    prev = {"generated_at": "2026-10-10T06:00:00+00:00", "staking": {"eth_lido": {"apr": 2.3, "source": "lido"}}}
    out = fm.build_staking(prev)
    assert out["dot_x"]["apr"] == 4.5
    assert out["eth_lido"]["apr"] == 2.3 and out["eth_lido"]["stale"] is True
    monkeypatch.setattr(fm, "STAKING_PROVIDERS", {"dot_x": roto})
    assert fm.build_staking({}) == {}


# ============================================================ perfil de acción (sector, industria, empleados, market cap, EV)
def test_parse_stock_profile_campos_validos():
    info = {"sector": " Technology ", "industry": "Consumer Electronics", "fullTimeEmployees": 164000,
            "marketCap": 3_210_000_000_000.0, "enterpriseValue": 3_190_000_000_000, "currency": "usd"}
    assert fm.parse_stock_profile(info) == {
        "sector": "Technology", "industry": "Consumer Electronics", "employees": 164000,
        "market_cap": 3_210_000_000_000, "enterprise_value": 3_190_000_000_000, "cap_currency": "USD",
        "dividend_yield": 0.0}  # sin claves de dividendo en info => no paga


def test_parse_stock_profile_descarta_basura_sin_rellenar_con_null():
    info = {"sector": 5, "industry": "", "fullTimeEmployees": "x", "marketCap": float("nan"),
            "enterpriseValue": float("inf"), "currency": "dolares"}
    assert fm.parse_stock_profile(info) == {}
    assert fm.parse_stock_profile(None) == {} and fm.parse_stock_profile([]) == {}
    # EV sin market cap no se publica (el indicador caja/deuda necesita los dos); bool no es número
    assert fm.parse_stock_profile({"enterpriseValue": 1e9, "fullTimeEmployees": True}) == {}
    # EV negativo (caja > capitalización) es legítimo; empleados absurdos se descartan
    out = fm.parse_stock_profile({"marketCap": 1e9, "enterpriseValue": -2e8, "fullTimeEmployees": 9e9})
    assert out["enterprise_value"] == -200_000_000 and "employees" not in out


def test_attach_profiles_usa_anterior_si_yahoo_falla_y_corta_tras_racha(monkeypatch):
    monkeypatch.setattr(fm.time, "sleep", lambda *_: None)
    stocks = [{"symbol": s} for s in ("A", "B", "C", "D", "E", "F", "G")]
    prev = {"stocks": [{"symbol": "G", "sector": "Energy", "market_cap": 5, "profile_as_of": "2026-10-01", "price": 1}]}
    calls = []

    def fetch(sym):
        calls.append(sym)
        return {"sector": "Tech", "market_cap": 10} if sym == "A" else {}

    out = fm.attach_profiles(stocks, prev, fetch=fetch, max_fail_streak=3)
    assert out[0]["sector"] == "Tech" and out[0]["profile_as_of"] == fm.datetime.now(fm.timezone.utc).strftime("%Y-%m-%d")
    assert calls == ["A", "B", "C", "D"]  # A ok, luego 3 fallos seguidos y se deja de llamar a Yahoo
    g = out[6]
    assert g["sector"] == "Energy" and g["market_cap"] == 5 and g["profile_as_of"] == "2026-10-01" and "price" not in g or g.get("price") is None
    assert "sector" not in out[1]  # sin dato nuevo ni anterior: queda sin campos


def test_attach_profiles_nunca_lanza(monkeypatch):
    monkeypatch.setattr(fm.time, "sleep", lambda *_: None)

    def boom(sym):
        raise RuntimeError("yahoo caido")

    stocks = [{"symbol": "A"}]
    assert fm.attach_profiles(stocks, None, fetch=boom) == [{"symbol": "A"}]


def test_main_publica_el_perfil_de_las_acciones(tmp_path, monkeypatch):
    _patch_main(monkeypatch, tmp_path)
    monkeypatch.setattr(fm, "FRED_API_KEY", "")

    def fake(stocks, prev=None):
        stocks[0].update({"sector": "Technology", "market_cap": 10, "enterprise_value": 9, "cap_currency": "USD"})
        return stocks

    monkeypatch.setattr(fm, "attach_profiles", fake)
    fm.main()
    d = json.loads((tmp_path / "data.json").read_text())
    assert d["stocks"][0]["sector"] == "Technology" and d["stocks"][0]["enterprise_value"] == 9


def test_parse_stock_profile_ratios_margenes_beta_insiders():
    info = {"marketCap": 3e12, "trailingPE": 34.512, "forwardPE": 29.1, "enterpriseToEbitda": 24.456,
            "profitMargins": 0.2704, "operatingMargins": 0.3151, "beta": 1.1049, "heldPercentInsiders": 0.0207}
    out = fm.parse_stock_profile(info)
    assert out["pe_trailing"] == 34.51 and out["pe_forward"] == 29.1 and out["ev_ebitda"] == 24.46
    assert out["profit_margin"] == 27.04 and out["operating_margin"] == 31.51
    assert out["beta"] == 1.1 and out["insiders_pct"] == 2.07


def test_parse_stock_profile_descarta_ratios_invalidos():
    info = {"marketCap": 1e9, "trailingPE": "Infinity", "forwardPE": -12.0, "enterpriseToEbitda": float("nan"),
            "profitMargins": 7.0, "operatingMargins": "x", "beta": 99, "heldPercentInsiders": 1.7}
    out = fm.parse_stock_profile(info)
    for k in ("pe_trailing", "pe_forward", "ev_ebitda", "profit_margin", "operating_margin", "beta", "insiders_pct"):
        assert k not in out
    # margen negativo (pérdidas) sí es válido
    assert fm.parse_stock_profile({"marketCap": 1e9, "profitMargins": -0.35})["profit_margin"] == -35.0


def test_parse_stock_profile_dividendos_a_prueba_de_escala():
    base = {"marketCap": 3e12, "currentPrice": 200.0}
    # rentabilidad = tasa anual / precio, sin fiarse de la escala de dividendYield (0.44 ó 0.0044)
    a = fm.parse_stock_profile({**base, "dividendRate": 1.0, "dividendYield": 0.5, "fiveYearAvgDividendYield": 0.62})
    assert a["dividend_yield"] == 0.5 and a["dividend_rate"] == 1.0 and a["dividend_5y_avg"] == 0.62
    b = fm.parse_stock_profile({**base, "dividendRate": 1.0, "dividendYield": 0.005})
    assert b["dividend_yield"] == 0.5
    # sin tasa: trailingAnnualDividendYield (fracción) y, en último lugar, dividendYield (fracción o porcentaje)
    assert fm.parse_stock_profile({"marketCap": 1, "trailingAnnualDividendYield": 0.0123})["dividend_yield"] == 1.23
    assert fm.parse_stock_profile({"marketCap": 1, "dividendYield": 0.0123})["dividend_yield"] == 1.23
    assert fm.parse_stock_profile({"marketCap": 1, "dividendYield": 3.4})["dividend_yield"] == 3.4
    # rentabilidades absurdas se descartan
    assert "dividend_yield" not in fm.parse_stock_profile({**base, "dividendRate": 150.0})


def test_parse_stock_profile_no_paga_dividendo():
    out = fm.parse_stock_profile({"marketCap": 1e12, "currentPrice": 300.0})
    assert out["dividend_yield"] == 0.0 and "dividend_rate" not in out
    # sin marketCap (info casi vacío) no se afirma que no paga
    assert "dividend_yield" not in fm.parse_stock_profile({"sector": "Technology"})


def test_parse_stock_profile_estado_de_resultados_y_balance():
    info = {"marketCap": 1e12, "ebitda": 130_541_000_000.0, "earningsQuarterlyGrowth": 0.1234,
            "totalCash": 55_372_000_000.0, "totalDebt": 98_186_000_000.0, "financialCurrency": "twd"}
    out = fm.parse_stock_profile(info)
    assert out["ebitda"] == 130_541_000_000 and out["quarterly_earnings_growth"] == 12.34
    assert out["total_cash"] == 55_372_000_000 and out["total_debt"] == 98_186_000_000
    assert out["financial_currency"] == "TWD"  # divisa del balance (puede diferir de la de cotización)


def test_parse_stock_profile_balance_casos_limite():
    # EBITDA negativo y crecimiento negativo son legítimos; deuda 0 es "sin deuda", no ausencia de dato
    out = fm.parse_stock_profile({"marketCap": 1e9, "ebitda": -5e7, "earningsQuarterlyGrowth": -0.4,
                                  "totalCash": 2e8, "totalDebt": 0, "financialCurrency": "USD"})
    assert out["ebitda"] == -50_000_000 and out["quarterly_earnings_growth"] == -40.0 and out["total_debt"] == 0
    # basura: se omite, sin rellenar con null; sin divisa válida no se publica la divisa
    bad = fm.parse_stock_profile({"marketCap": 1e9, "ebitda": float("nan"), "earningsQuarterlyGrowth": 500,
                                  "totalCash": -3, "totalDebt": "x", "financialCurrency": "dolares"})
    for k in ("ebitda", "quarterly_earnings_growth", "total_cash", "total_debt", "financial_currency"):
        assert k not in bad
    # ebitda == 0 no aporta nada
    assert "ebitda" not in fm.parse_stock_profile({"marketCap": 1e9, "ebitda": 0})


def test_attach_profiles_registra_cobertura_por_campo(capsys):
    stocks = [{"symbol": "A"}, {"symbol": "B"}]
    fm.attach_profiles(stocks, fetch=lambda sym: {"pe_trailing": 20.0, "ebitda": 5} if sym == "A" else {"pe_trailing": 15.0})
    out = capsys.readouterr().out
    assert "profile coverage (of 2 stocks)" in out and "pe_trailing 2" in out and "ebitda 1" in out and "beta 0" in out


# ============================================================ auditoría 12-10: frescura, filas arrastradas, perfiles degradados
def _ohlc(closes, vols=None, last_date="2026-10-09", flat=()):
    import pandas as pd
    n = len(closes)
    idx = pd.date_range(end=last_date, periods=n, freq="D", tz="UTC")
    df = pd.DataFrame({"Open": closes, "High": [c * 1.01 for c in closes], "Low": [c * 0.99 for c in closes],
                       "Close": closes, "Volume": vols or [1000.0] * n}, index=idx)
    for i in flat:   # barra fantasma: volumen 0 y O=H=L=C
        c = df["Close"].iloc[i]
        df.iloc[i, df.columns.get_loc("High")] = c
        df.iloc[i, df.columns.get_loc("Low")] = c
        df.iloc[i, df.columns.get_loc("Volume")] = 0.0
    return df


def test_drop_phantom_bars_interior_y_extremos():
    # interior (como antes): salto >15 % respecto a ambos vecinos
    d = _ohlc([100, 100, 134, 100, 100], flat=(2,))
    assert list(fm._drop_phantom_bars(d)["Close"]) == [100, 100, 100, 100]
    # ÚLTIMA barra fantasma (+34 % respecto a la única vecina): se descarta, el precio de hoy no es esa barra
    d = _ohlc([100, 101, 100, 134], flat=(3,))
    assert list(fm._drop_phantom_bars(d)["Close"]) == [100, 101, 100]
    # primera barra
    d = _ohlc([60, 100, 101, 100], flat=(0,))
    assert list(fm._drop_phantom_bars(d)["Close"]) == [100, 101, 100]
    # un día real de +20 % (con volumen) no se toca, y una barra plana con movimiento pequeño tampoco
    d = _ohlc([100, 101, 121], vols=[1000, 1000, 5000])
    assert len(fm._drop_phantom_bars(d)) == 3
    d = _ohlc([100, 101, 102], flat=(2,))
    assert len(fm._drop_phantom_bars(d)) == 3


def test_fetch_symbol_publica_as_of_y_marca_precio_antiguo(monkeypatch):
    today = dt.datetime.now(dt.timezone.utc).date()
    fresh = (today - dt.timedelta(days=1)).isoformat()
    monkeypatch.setattr(fm, "yf_history", lambda *a, **k: _ohlc([10, 11, 12, 13], last_date=fresh))
    r = fm.fetch_symbol("X")
    assert r["as_of"] == fresh and "stale" not in r and r["price"] == 13
    old = (today - dt.timedelta(days=20)).isoformat()
    monkeypatch.setattr(fm, "yf_history", lambda *a, **k: _ohlc([10, 11, 12, 13], last_date=old))
    r = fm.fetch_symbol("X")
    assert r["stale"] is True and r["stale_since"] == r["as_of"]


def test_build_list_conserva_la_fila_anterior_marcada_antigua(monkeypatch):
    today = dt.datetime.now(dt.timezone.utc).date()
    monkeypatch.setattr(fm, "fetch_symbol", lambda s: None if s == "BAD" else {"price": 2.0, "change_pct": 0.1, "as_of": "2026-10-09"})
    prev_rows = [{"symbol": "BAD", "name": "viejo", "price": 5.0, "as_of": "2026-10-08", "history": [{"date": "2026-10-08", "close": 5.0}],
                  "intraday": [{"time": "x", "close": 5}], "region": "Europe"}]
    out = fm.build_list([("OK", "Ok"), ("BAD", "Malo", "Asia")], prev_rows)
    assert [r["symbol"] for r in out] == ["OK", "BAD"]
    bad = out[1]
    assert bad["stale"] is True and bad["stale_since"] == "2026-10-08" and bad["price"] == 5.0
    assert bad["name"] == "Malo" and bad["region"] == "Asia" and "intraday" not in bad and bad["history"]
    # sucesivas reutilizaciones conservan stale_since
    out2 = fm.build_list([("BAD", "Malo")], out)
    assert out2[0]["stale_since"] == "2026-10-08"
    # sin fila anterior, o con una de hace más de STALE_CARRY_MAX_DAYS, el símbolo desaparece (lo detecta el sanity-check)
    assert fm.build_list([("BAD", "Malo")], []) == [] and fm.build_list([("BAD", "Malo")]) == []
    ancient = [{"symbol": "BAD", "price": 5.0, "stale_since": (today - dt.timedelta(days=fm.STALE_CARRY_MAX_DAYS + 1)).isoformat()}]
    assert fm.build_list([("BAD", "Malo")], ancient) == []
    # precio no numérico en la fila anterior: no se arrastra
    assert fm.build_list([("BAD", "Malo")], [{"symbol": "BAD", "price": "x"}, {"symbol": "BAD", "price": True}]) == []


def test_attach_histories_reutiliza_historico_anterior_pero_no_el_intradia(monkeypatch):
    monkeypatch.setattr(fm, "fetch_full_history", lambda s: [] if s == "A" else [{"date": "2026-10-09", "close": 1.0}])
    monkeypatch.setattr(fm, "fetch_intraday_history", lambda s: [])
    prev = [{"symbol": "A", "history": [{"date": "2026-10-08", "close": 9.0}], "intraday": [{"time": "t", "close": 9}]}]
    out = fm.attach_histories([{"symbol": "A"}, {"symbol": "B"}], prev)
    assert out[0]["history"] == [{"date": "2026-10-08", "close": 9.0}] and out[0]["history_stale"] is True and out[0]["intraday"] == []
    assert out[1]["history"][0]["close"] == 1.0 and "history_stale" not in out[1]
    # sin histórico anterior queda vacío (el sanity-check decide si es grave)
    assert fm.attach_histories([{"symbol": "A"}], [])[0]["history"] == []


def _rich_profile(**kw):
    p = {"market_cap": 1e11, "enterprise_value": 1.1e11, "cap_currency": "USD", "pe_trailing": 20.0, "pe_forward": 18.0,
         "ev_ebitda": 11.0, "profit_margin": 12.0, "operating_margin": 15.0, "beta": 1.0, "insiders_pct": 3.0,
         "dividend_yield": 1.0, "ebitda": 9e9, "total_cash": 1e10, "total_debt": 2e10, "sector": "Technology"}
    p.update(kw)
    return p


def test_attach_profiles_descarta_un_perfil_incompleto_de_yahoo(monkeypatch):
    monkeypatch.setattr(fm.time, "sleep", lambda *_: None)
    prev = {"stocks": [{"symbol": "A", **_rich_profile(), "profile_as_of": dt.datetime.now(dt.timezone.utc).date().isoformat()}]}
    # Yahoo contesta con un info casi vacío (solo market cap + sector): no debe borrar las métricas buenas
    stocks = fm.attach_profiles([{"symbol": "A"}], prev, fetch=lambda s: {"market_cap": 1.01e11, "sector": "Technology"})
    assert stocks[0]["pe_forward"] == 18.0 and stocks[0]["total_debt"] == 2e10 and stocks[0]["market_cap"] == 1e11
    # un perfil completo sí sustituye al anterior
    stocks = fm.attach_profiles([{"symbol": "A"}], prev, fetch=lambda s: _rich_profile(pe_forward=17.0))
    assert stocks[0]["pe_forward"] == 17.0
    # sin perfil anterior rico, cualquier perfil se acepta (no hay nada que proteger)
    stocks = fm.attach_profiles([{"symbol": "B"}], prev, fetch=lambda s: {"market_cap": 5e9})
    assert stocks[0]["market_cap"] == 5e9


def test_attach_profiles_no_reutiliza_perfiles_demasiado_antiguos(monkeypatch):
    monkeypatch.setattr(fm.time, "sleep", lambda *_: None)
    today = dt.datetime.now(dt.timezone.utc).date()
    old_ok = (today - dt.timedelta(days=fm.PROFILE_MAX_REUSE_DAYS - 1)).isoformat()
    too_old = (today - dt.timedelta(days=fm.PROFILE_MAX_REUSE_DAYS + 5)).isoformat()
    prev = {"stocks": [{"symbol": "A", **_rich_profile(), "profile_as_of": old_ok},
                       {"symbol": "B", **_rich_profile(), "profile_as_of": too_old},
                       {"symbol": "C", **_rich_profile()}]}   # sin fecha: tampoco se puede dar por vigente
    stocks = fm.attach_profiles([{"symbol": "A"}, {"symbol": "B"}, {"symbol": "C"}], prev, fetch=lambda s: {})
    assert stocks[0]["pe_forward"] == 18.0 and stocks[0]["profile_as_of"] == old_ok
    assert "pe_forward" not in stocks[1] and "profile_as_of" not in stocks[1]
    assert "pe_forward" not in stocks[2]


def test_profile_warnings_detecta_ev_incoherente_y_saltos_de_capitalizacion(capsys):
    w = fm._profile_warnings("ASML", {"market_cap": 6.8e11, "enterprise_value": 3.8e13}, {})
    assert len(w) == 1 and "EV/MarketCap" in w[0]
    w = fm._profile_warnings("X", {"market_cap": 2e11, "enterprise_value": 2.1e11}, {"market_cap": 1e11})
    assert len(w) == 1 and "capitalización" in w[0]
    assert fm._profile_warnings("Y", {"market_cap": 1e11, "enterprise_value": 1.1e11}, {"market_cap": 1.05e11}) == []
    assert fm._profile_warnings("Z", {}, None) == []
    assert "ASML" in capsys.readouterr().err


def test_compute_health_resume_filas_antiguas_y_edad_de_perfiles(monkeypatch):
    monkeypatch.setattr(fm, "_LAST_VALUATION", {"table": 0, "fresh": 0})
    today = dt.date(2026, 10, 12)
    data = {"stocks": [{"symbol": "A", "market_cap": 1, "profile_as_of": "2026-10-12"},
                       {"symbol": "B", "stale": True, "history_stale": True, "profile_as_of": "2026-09-20", "pe_forward": 10},
                       {"symbol": "C"}, "basura"],
            "indices": [{"symbol": "^X", "stale": True}], "forex": None}
    h = fm.compute_health(data, today)
    assert h["stale_symbols"] == ["B", "^X"] and h["history_reused"] == ["B"]
    assert h["profiles"] == {"stocks": 3, "with_data": 2, "valuation_table": 0, "oldest_days": 22, "older_than_7d": 1}
    assert fm.compute_health({}, today)["profiles"]["oldest_days"] is None
    assert fm.compute_health(None, today)["stale_symbols"] == []   # nunca lanza


def test_main_publica_health(tmp_path, monkeypatch, capsys):
    _patch_main(monkeypatch, tmp_path)
    monkeypatch.setenv("PREV_FEED_PATH", str(tmp_path / "nada.json"))
    monkeypatch.setattr(fm, "FRED_API_KEY", "")
    fm.main()
    d = json.loads((tmp_path / "data.json").read_text())
    assert d["health"]["stale_symbols"] == [] and d["health"]["profiles"]["stocks"] == len(fm.STOCKS)
    assert "HEALTH:" in capsys.readouterr().out


# ============================================================ tabla "Valuation Measures" (misma fuente que la web de Yahoo)
def _val_df(**cur):
    """DataFrame con la forma de Ticker.get_valuation_measures(): filas = medidas, columna 'Current' (+ trimestres)."""
    import numpy as np
    import pandas as pd
    rows = {"Market Cap": 2.01e12, "Enterprise Value": 1.93e12, "Trailing P/E": 33.67, "Forward P/E": 21.41,
            "PEG Ratio (5yr expected)": 0.86, "Price/Sales": 16.81, "Price/Book": 11.6,
            "Enterprise Value/Revenue": 13.8, "Enterprise Value/EBITDA": 18.19}
    rows.update(cur)
    return pd.DataFrame({"Current": pd.Series(rows, dtype=float), "6/30/2026": pd.Series([np.nan] * len(rows), index=list(rows))})


def test_parse_valuation_measures_toma_la_columna_current():
    v = fm.parse_valuation_measures(_val_df())
    assert v == {"market_cap": 2010000000000, "enterprise_value": 1930000000000, "pe_trailing": 33.67, "pe_forward": 21.41, "ev_ebitda": 18.19,
                 "peg": 0.86, "ps": 16.81, "pb": 11.6, "ev_revenue": 13.8}


@pytest.mark.parametrize("bad", [None, {}, [], "x", 5])
def test_parse_valuation_measures_entradas_raras(bad):
    assert fm.parse_valuation_measures(bad) == {}


def test_parse_valuation_measures_vacia_sin_market_cap_o_con_basura():
    import numpy as np
    import pandas as pd
    assert fm.parse_valuation_measures(pd.DataFrame()) == {}
    assert fm.parse_valuation_measures(_val_df(**{"Market Cap": np.nan})) == {}   # sin capitalización no se fía de nada
    assert fm.parse_valuation_measures(_val_df(**{"Market Cap": -5})) == {}
    v = fm.parse_valuation_measures(_val_df(**{"Trailing P/E": np.nan, "Forward P/E": float("inf"), "Enterprise Value/EBITDA": -3}))
    assert "pe_trailing" not in v and "pe_forward" not in v and "ev_ebitda" not in v and v["market_cap"] == 2010000000000
    assert fm.parse_valuation_measures(pd.DataFrame({"Other": [1]}, index=["Market Cap"])) == {}


def test_merge_valuation_la_tabla_manda_y_quita_lo_que_yahoo_no_muestra():
    info = {"market_cap": 2318000000000, "enterprise_value": 16400000000000, "pe_trailing": 32.5, "pe_forward": 20.3, "ev_ebitda": 5.18, "beta": 1.2}
    m = fm.merge_valuation(info, {"market_cap": 2010000000000, "enterprise_value": 1930000000000, "pe_forward": 21.41})
    assert m["enterprise_value"] == 1930000000000 and m["market_cap"] == 2010000000000 and m["pe_forward"] == 21.41
    assert "pe_trailing" not in m and "ev_ebitda" not in m and m["beta"] == 1.2      # sin P/E en la tabla (pérdidas): "—" como en Yahoo
    assert fm.merge_valuation(info, {}) == info and fm.merge_valuation(info, {"pe_forward": 3}) == info   # sin tabla usable: info intacta
    assert info["ev_ebitda"] == 5.18                                                  # no muta la entrada


class _FakeTicker:
    def __init__(self, info, val=None, val_exc=None):
        self.info, self._val, self._exc = info, val, val_exc

    def get_valuation_measures(self, freq="quarterly", periods=5):
        assert freq == "trailing" and periods == 0
        if self._exc:
            raise self._exc
        return self._val


_ADR_INFO = {"marketCap": 2318000000000, "enterpriseValue": 16401145266176, "trailingPE": 32.48, "forwardPE": 20.31, "enterpriseToEbitda": 5.18,
             "currency": "USD", "financialCurrency": "TWD", "totalCash": 3518010228736, "totalDebt": 1068558516224, "ebitda": 3167467077632,
             "sector": "Technology", "beta": 1.24}


def test_fetch_stock_profile_arregla_el_ev_de_un_adr(monkeypatch, capsys):
    monkeypatch.setattr(fm.yf, "Ticker", lambda s: _FakeTicker(_ADR_INFO, _val_df()))
    p = fm.fetch_stock_profile("TSM")
    assert p["enterprise_value"] == 1930000000000 and p["market_cap"] == 2010000000000 and p["ev_ebitda"] == 18.19
    assert p["pe_trailing"] == 33.67 and p["pe_forward"] == 21.41 and p["_valuation"] is True
    assert p["ebitda"] == 3167467077632 and p["financial_currency"] == "TWD" and p["beta"] == 1.24   # el resto sigue viniendo de info
    assert "tabla de valoración" in capsys.readouterr().err


def test_fetch_stock_profile_si_la_tabla_falla_usa_info(monkeypatch, capsys):
    monkeypatch.setattr(fm.time, "sleep", lambda *_: None)
    monkeypatch.setattr(fm.yf, "Ticker", lambda s: _FakeTicker(_ADR_INFO, val_exc=RuntimeError("429")))
    p = fm.fetch_stock_profile("TSM")
    assert p["market_cap"] == 2318000000000 and p["_valuation"] is False and p["pe_forward"] == 20.31   # info como respaldo
    assert "enterprise_value" not in p                                                                      # ...pero su EV inverosímil no se publica
    assert "valuation table attempt 2 failed" in capsys.readouterr().err
    # tabla vacía = mismo comportamiento
    import pandas as pd
    monkeypatch.setattr(fm.yf, "Ticker", lambda s: _FakeTicker(_ADR_INFO, pd.DataFrame()))
    assert fm.fetch_stock_profile("TSM")["_valuation"] is False
    # un Ticker sin el método (yfinance antiguo) tampoco rompe
    class Old:
        info = _ADR_INFO
    monkeypatch.setattr(fm.yf, "Ticker", lambda s: Old())
    assert fm.fetch_stock_profile("TSM")["_valuation"] is False


def test_attach_profiles_no_publica_el_marcador_y_lo_cuenta(monkeypatch, capsys):
    monkeypatch.setattr(fm.time, "sleep", lambda *_: None)
    tick = {"A": _FakeTicker(_ADR_INFO, _val_df()), "B": _FakeTicker(_ADR_INFO, val_exc=RuntimeError("x"))}
    monkeypatch.setattr(fm.yf, "Ticker", lambda s: tick[s])
    stocks = fm.attach_profiles([{"symbol": "A"}, {"symbol": "B"}], {})
    assert all("_valuation" not in s for s in stocks)
    out = capsys.readouterr().out
    assert "valuation table" in out and "1 of 2 fresh profiles" in out and "Ticker.info" in out
    assert stocks[0]["enterprise_value"] == 1930000000000 and "enterprise_value" not in stocks[1] and stocks[1]["market_cap"] == 2318000000000


# ============================================================ robustez de la tabla de valoración
def test_tabla_parcial_solo_market_cap_no_se_considera_usable():
    import numpy as np
    nan = np.nan
    df = _val_df(**{"Enterprise Value": nan, "Trailing P/E": nan, "Forward P/E": nan, "Enterprise Value/EBITDA": nan})
    v = fm.parse_valuation_measures(df)
    assert v["market_cap"] == 2010000000000 and "pe_trailing" not in v and not fm._val_usable(v)   # P/S, P/B... solos no bastan
    info = {"market_cap": 1, "pe_forward": 20.0, "enterprise_value": 5}
    assert fm.merge_valuation(info, v) == info                         # una respuesta a medias no borra métricas buenas
    assert fm._val_usable({"market_cap": 1, "pe_forward": 2}) and not fm._val_usable({}) and not fm._val_usable(None)


def test_la_tabla_se_reintenta_una_vez(monkeypatch):
    monkeypatch.setattr(fm.time, "sleep", lambda *_: None)
    calls = []

    class Flaky(_FakeTicker):
        def get_valuation_measures(self, freq="quarterly", periods=5):
            calls.append(1)
            if len(calls) == 1:
                raise RuntimeError("429")
            return _val_df()
    monkeypatch.setattr(fm.yf, "Ticker", lambda s: Flaky(_ADR_INFO))
    p = fm.fetch_stock_profile("TSM")
    assert len(calls) == 2 and p["enterprise_value"] == 1930000000000 and p["_valuation"] is True


def test_sin_tabla_un_ev_inverosimil_de_info_no_se_publica(monkeypatch, capsys):
    monkeypatch.setattr(fm.time, "sleep", lambda *_: None)
    monkeypatch.setattr(fm.yf, "Ticker", lambda s: _FakeTicker(_ADR_INFO, val_exc=RuntimeError("down")))
    p = fm.fetch_stock_profile("TSM")
    assert "enterprise_value" not in p and "ev_ebitda" not in p and p["_valuation"] is False
    assert p["market_cap"] == 2318000000000 and p["pe_forward"] == 20.31      # lo demás de info se conserva
    assert "no es creíble" in capsys.readouterr().err
    # un EV coherente de info sí se conserva
    ok = dict(_ADR_INFO, enterpriseValue=2200000000000, enterpriseToEbitda=12.0)
    monkeypatch.setattr(fm.yf, "Ticker", lambda s: _FakeTicker(ok, val_exc=RuntimeError("down")))
    assert fm.fetch_stock_profile("TSM")["enterprise_value"] == 2200000000000


def test_health_cuenta_las_acciones_con_tabla(monkeypatch):
    monkeypatch.setattr(fm.time, "sleep", lambda *_: None)
    monkeypatch.setattr(fm.yf, "Ticker", lambda s: _FakeTicker(_ADR_INFO, _val_df()))
    stocks = fm.attach_profiles([{"symbol": "A"}, {"symbol": "B"}], {})
    h = fm.compute_health({"stocks": stocks})
    assert h["profiles"]["valuation_table"] == 2 and h["profiles"]["with_data"] == 2


# ============================================================ campos para el veredicto + tipos de cambio a USD
INFO_V = {"currentPrice": 100.0, "marketCap": 5e11, "currency": "USD", "grossMargins": 0.55, "revenueGrowth": 0.12,
          "returnOnEquity": 0.31, "freeCashflow": 2.5e10, "financialCurrency": "USD", "targetMeanPrice": 120.0,
          "numberOfAnalystOpinions": 38, "recommendationMean": 1.9}


def test_parse_perfil_campos_del_veredicto():
    p = fm.parse_stock_profile(dict(INFO_V))
    assert p["gross_margin"] == 55.0 and p["revenue_growth"] == 12.0 and p["roe"] == 31.0
    assert p["free_cashflow"] == 25000000000 and p["financial_currency"] == "USD"
    assert p["target_mean"] == 120.0 and p["analyst_count"] == 38 and p["recommendation_mean"] == 1.9


def test_parse_perfil_descarta_basura_y_objetivo_en_otra_divisa():
    bad = dict(INFO_V, grossMargins=7, revenueGrowth=-5, returnOnEquity="x", freeCashflow=True, numberOfAnalystOpinions=0,
               recommendationMean=9, targetMeanPrice=900.0)      # objetivo 9x el precio: otra divisa -> no se publica
    p = fm.parse_stock_profile(bad)
    for k in ("gross_margin", "revenue_growth", "roe", "free_cashflow", "analyst_count", "recommendation_mean", "target_mean"):
        assert k not in p, k
    assert fm.parse_stock_profile(dict(INFO_V, freeCashflow=-3e9))["free_cashflow"] == -3000000000   # FCF negativo es legítimo


def test_merge_valuation_trae_peg_ps_pb_y_quita_los_que_faltan():
    prof = {"market_cap": 1, "pe_trailing": 50.0}
    val = fm.parse_valuation_measures(_val_df(**{"PEG Ratio (5yr expected)": float("nan")}))
    m = fm.merge_valuation(prof, val)
    assert m["ps"] == 16.81 and m["pb"] == 11.6 and m["ev_revenue"] == 13.8 and "peg" not in m


class _H:
    def __init__(self, v):
        self._v = v
        self.empty = v is None

    @property
    def Close(self):  # noqa: N802
        return self

    def __getitem__(self, k):
        return self

    @property
    def iloc(self):
        return self

    def __call__(self, *a):
        return self


def _fake_yf(rates):
    import pandas as pd

    def f(symbol, tries=3, **kw):
        ccy = symbol[:3]
        if ccy not in rates or rates[ccy] is None:
            raise RuntimeError("sin datos")
        return pd.DataFrame({"Close": [rates[ccy] * 0.99, rates[ccy]]})
    return f


def test_fetch_fx_usd_normal_fallback_y_salto_absurdo(monkeypatch):
    monkeypatch.setattr(fm, "yf_history", _fake_yf({"EUR": 1.1157, "DKK": 0.1495, "CHF": None, "TWD": 3.1}))
    fx, stale = fm.fetch_fx_usd(["USD", "EUR", "DKK", "CHF", "TWD", "abc1", None, "eur"], {"CHF": 1.2, "TWD": 0.031})
    assert fx["USD"] == 1.0 and fx["EUR"] == 1.1157 and fx["DKK"] == 0.1495
    assert fx["CHF"] == 1.2 and "CHF" in stale                      # falló: reutiliza el anterior
    assert fx["TWD"] == 0.031 and "TWD" in stale                    # 3.1 vs 0.031: salto de 100x -> se descarta
    assert "ABC" not in fx
    fx2, st2 = fm.fetch_fx_usd(["CHF"], None)                       # sin anterior y sin dato: simplemente no hay tipo
    assert fx2 == {} and st2 == []


def test_attach_usd_convierte_capitalizacion_y_fcf_con_cada_divisa(monkeypatch):
    stocks = [{"symbol": "A", "market_cap": 100_000_000_000, "cap_currency": "DKK", "free_cashflow": 4_000_000_000, "financial_currency": "DKK"},
              {"symbol": "B", "market_cap": 2_000_000_000_000, "cap_currency": "USD", "free_cashflow": 1_000_000_000_000, "financial_currency": "TWD"},
              {"symbol": "C", "market_cap": 5e9, "cap_currency": "XXX"},
              {"symbol": "D"}]
    out, fx, stale = fm.attach_usd(stocks, {}, fetch_fx=lambda c, p: ({"USD": 1.0, "DKK": 0.15, "TWD": 0.031}, ["TWD"]))
    a, b, c, d = out
    assert a["market_cap_usd"] == 15_000_000_000 and a["fcf_yield"] == 4.0
    assert b["market_cap_usd"] == 2_000_000_000_000 and b["fcf_yield"] == 1.55   # FCF en TWD, capitalización en USD: ambos a USD antes de dividir
    z = fm.attach_usd([{"symbol": "Z", "market_cap": 1e9, "cap_currency": "USD", "free_cashflow": 5e9, "financial_currency": "USD"}], {},
                      fetch_fx=lambda c, p: ({"USD": 1.0}, []))[0][0]
    assert "fcf_yield" not in z                                                  # 500 %: absurdo, no se publica
    assert "market_cap_usd" not in c and "market_cap_usd" not in d
    assert fx["DKK"] == 0.15 and stale == ["TWD"]


def test_attach_usd_nunca_lanza(monkeypatch):
    def boom(*a):
        raise RuntimeError("x")
    out, fx, stale = fm.attach_usd([{"symbol": "A", "market_cap": 1}], {}, fetch_fx=boom)
    assert fx == {} and stale == [] and out[0]["symbol"] == "A"
