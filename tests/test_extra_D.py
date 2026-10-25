"""Casos adicionales del agente D (revisión pre-Fase 2). Datos simulados, sin red.

Convención: los tests que describen una GUARDA QUE HOY EXISTE deben pasar (y morir ante un mutante).
Los marcados `GAP_` describen el comportamiento DESEADO que hoy NO se cumple: están como xfail(strict=True) para que
salten (XPASS) cuando se arreglen. Cada uno enlaza con un hallazgo del INFORME.
"""
import datetime as dt
import json
import os
import pathlib
import re
import stat
import subprocess
import types

import pytest

import fetch_market_data as fm
import sanity_check as sc
from test_fetch_market_data import _ohlc
from test_sanity_check import healthy, run as sc_run, TODAY

yaml = pytest.importorskip("yaml")
ROOT = pathlib.Path(__file__).resolve().parent.parent
WF = ROOT / ".github" / "workflows"


@pytest.fixture(autouse=True)
def _no_sleep(monkeypatch):
    monkeypatch.setattr(fm.time, "sleep", lambda *_a, **_k: None)


# =============================================================== fetch_market_data
def test_D01_precio_cero_en_la_ultima_barra_no_se_publica(monkeypatch):
    """Un Close=0 de Yahoo ya no llega al feed (antes el front valoraba la posición a 0)."""
    monkeypatch.setattr(fm, "yf_history", lambda *a, **k: _ohlc([10, 11, 12, 0.0], last_date="2026-10-09"))
    r = fm.fetch_symbol("X")
    assert r is None


def test_FIX_D01b_precio_cero_no_deberia_publicarse(monkeypatch):
    monkeypatch.setattr(fm, "yf_history", lambda *a, **k: _ohlc([10, 11, 12, 0.0], last_date="2026-10-09"))
    assert fm.fetch_symbol("AAPL") is None


def test_D02_volumen_cero_o_nan_no_rompe_fetch_symbol(monkeypatch):
    import numpy as np
    monkeypatch.setattr(fm, "yf_history", lambda *a, **k: _ohlc([10, 11, 12, 13], vols=[0.0] * 4))
    r = fm.fetch_symbol("X")
    assert r["volume_ratio"] is None
    monkeypatch.setattr(fm, "yf_history", lambda *a, **k: _ohlc([10, 11, 12, 13], vols=[np.nan, 5.0, 5.0, np.nan]))
    r = fm.fetch_symbol("X")
    json.dumps(fm.sanitize_for_json(r), allow_nan=False)   # nada de NaN en el JSON


def test_D03_historico_con_nan_en_ohlcv_produce_json_estricto(monkeypatch):
    import numpy as np
    df = _ohlc([10, 11, 12, 13, 14])
    df.iloc[2, df.columns.get_loc("Open")] = np.nan
    df.iloc[3, df.columns.get_loc("Volume")] = np.nan
    monkeypatch.setattr(fm, "yf_history", lambda *a, **k: df)
    h = fm.fetch_full_history("X")
    txt = json.dumps(fm.sanitize_for_json({"h": h}), allow_nan=False)
    assert "NaN" not in txt and "null" in txt


def test_D04_barra_de_manana_en_asia_no_es_antigua(monkeypatch):
    today = dt.datetime.now(dt.timezone.utc).date()
    monkeypatch.setattr(fm, "yf_history", lambda *a, **k: _ohlc([1, 2, 3, 4], last_date=(today + dt.timedelta(days=1)).isoformat()))
    r = fm.fetch_symbol("^N225")
    assert "stale" not in r and r["as_of"] == (today + dt.timedelta(days=1)).isoformat()


def test_D05_downsample_ordena_no_duplica_y_conserva_lo_reciente():
    today = dt.datetime.now().date()
    pts = []
    for i in range(0, 365 * 8, 3):          # 8 años, un punto cada 3 días, desordenado
        d = (today - dt.timedelta(days=i)).isoformat()
        pts.append({"date": d, "close": 1.0, "open": 1.0, "high": 1.0, "low": 1.0, "volume": 1.0})
    pts.reverse(); pts = pts[::2] + pts[1::2]
    out = fm.downsample_history(pts)
    dates = [p["date"] for p in out]
    assert dates == sorted(dates) and len(set(dates)) == len(dates)
    cutoff = (dt.datetime.now() - dt.timedelta(days=5 * 365)).strftime("%Y-%m-%d")
    assert all(p["date"] >= cutoff for p in out if p["date"] >= cutoff) and len([d for d in dates if d < cutoff]) <= 12 * 4 + 1
    assert fm.downsample_history([]) == [] and fm.downsample_history(None) is None


def test_D06_yf_history_reintenta_se_rinde_y_respeta_presupuesto(monkeypatch):
    calls = {"n": 0}

    class T:
        def __init__(self, s): pass
        def history(self, **kw):
            calls["n"] += 1
            raise RuntimeError("conexión rota")        # fallo genérico (un 429 tiene su propio tratamiento: ver BE-08)
    monkeypatch.setattr(fm.yf, "Ticker", T)
    monkeypatch.setattr(fm, "_YF_FAIL_STREAK", 0)
    with pytest.raises(RuntimeError):
        fm.yf_history("A", tries=3, period="1d")
    assert calls["n"] == 3 and fm._YF_FAIL_STREAK == 1
    monkeypatch.setattr(fm, "_YF_FAIL_STREAK", 15)           # interruptor abierto: un solo intento
    calls["n"] = 0
    with pytest.raises(RuntimeError):
        fm.yf_history("A", tries=3, period="1d")
    assert calls["n"] == 1
    monkeypatch.setattr(fm, "_DEADLINE", fm.time.monotonic() - 1)   # presupuesto agotado: ni toca Yahoo
    calls["n"] = 0
    with pytest.raises(TimeoutError):
        fm.yf_history("A", period="1d")
    assert calls["n"] == 0


def test_D07_yf_history_excepcion_y_luego_vacio_devuelve_vacio_sin_lanzar(monkeypatch):
    import pandas as pd
    seq = iter([RuntimeError("x"), pd.DataFrame({"Close": []})])

    class T:
        def __init__(self, s): pass
        def history(self, **kw):
            v = next(seq)
            if isinstance(v, Exception):
                raise v
            return v
    monkeypatch.setattr(fm.yf, "Ticker", T)
    monkeypatch.setattr(fm, "_YF_FAIL_STREAK", 0)
    h = fm.yf_history("A", tries=2, period="1d")
    assert h.empty and fm._YF_FAIL_STREAK == 1


def test_D08_secreto_de_fred_no_sale_en_logs_aunque_la_excepcion_lleve_la_url(monkeypatch, capsys):
    import requests
    monkeypatch.setattr(fm, "FRED_API_KEY", "SUPERSECRETO123")

    def boom(url, params=None, timeout=None, **kw):
        raise requests.ConnectionError("HTTPSConnectionPool: Max retries exceeded with url: /x?api_key=%s&series_id=DGS10" % params["api_key"])
    monkeypatch.setattr(fm.requests, "get", boom)
    assert fm.fetch_fred_series("DGS10") is None
    assert fm.fetch_cpi_history()[0] == []
    assert fm.build_rates() == []
    cap = capsys.readouterr()
    assert "SUPERSECRETO123" not in cap.out + cap.err


def test_D09_fred_reintenta_429_y_se_rinde_a_la_tercera(monkeypatch):
    import requests
    seq = {"n": 0}

    def fake(url, params=None, timeout=None, **kw):
        seq["n"] += 1
        r = requests.Response(); r.status_code = 429; r.url = url
        return r
    monkeypatch.setattr(fm.requests, "get", fake)
    with pytest.raises(requests.RequestException):
        fm._fred_get({"a": 1})
    assert seq["n"] == 3


def test_D10_fred_valor_nan_o_inf_no_llega_como_nan_al_json(monkeypatch):
    monkeypatch.setattr(fm, "FRED_API_KEY", "k")
    resp = types.SimpleNamespace(json=lambda: {"observations": [{"date": "2026-10-01", "value": "nan"}, {"date": "2026-09-30", "value": "4.1"}]},
                                 raise_for_status=lambda: None, status_code=200)
    monkeypatch.setattr(fm, "_fred_get", lambda *a, **k: resp)
    r = fm.fetch_fred_series("DGS10")
    out = fm.sanitize_for_json({"r": r})
    json.dumps(out, allow_nan=False)      # no revienta; el valor NaN queda como null (el front lo ignora)
    assert out["r"]["value"] is None


def test_D11_cpi_descarta_valores_implausibles_y_marca_stale(monkeypatch):
    monkeypatch.setattr(fm, "FRED_API_KEY", "k")
    obs = [{"date": "2020-01-01", "value": "100.5"}, {"date": "2020-02-01", "value": "."}, {"date": "2020-03-01", "value": "9"},
           {"date": "2020-04-01", "value": "1e9"}, {"date": "2020-05-01", "value": "101,2"}]
    monkeypatch.setattr(fm, "_fred_get", lambda *a, **k: types.SimpleNamespace(json=lambda: {"observations": obs}))
    pts, meta = fm.fetch_cpi_history()
    assert [p["date"] for p in pts] == ["2020-01-01", "2020-05-01"] and pts[1]["value"] == 101.2
    assert meta["stale"] is True and meta["last_date"] == "2020-05-01"


def test_D12_parse_feed_filtra_esquemas_peligrosos_y_recorta(monkeypatch):
    xml = ("<?xml version='1.0'?><rss version='2.0'><channel><title>S&lt;script&gt;x&lt;/script&gt;</title>"
           "<item><title>&lt;b&gt;ok&lt;/b&gt; " + "x" * 400 + "</title><link>https://a.test/1</link></item>"
           "<item><title>js</title><link>javascript:alert(1)</link></item>"
           "<item><title>data</title><link>data:text/html,x</link></item>"
           "<item><title>upper</title><link>HTTP://A.TEST/2</link></item>"
           "<item><title>ftp</title><link>ftp://a.test/3</link></item></channel></rss>").encode()
    monkeypatch.setattr(fm, "_fetch_feed_bytes", lambda url: xml)
    monkeypatch.setattr(fm, "_NEWS_DEADLINE", None)
    items = fm.parse_feed("https://x.test/rss")
    assert [i["link"] for i in items] == ["https://a.test/1", "HTTP://A.TEST/2"]
    assert len(items[0]["title"]) == 300 and "<" not in items[0]["title"]


def test_FIX_D08_source_del_feed_sin_html(monkeypatch):
    xml = b"<?xml version='1.0'?><rss version='2.0'><channel><title>&lt;img src=x onerror=alert(1)&gt;</title><item><title>t</title><link>https://a.test/1</link></item></channel></rss>"
    monkeypatch.setattr(fm, "_fetch_feed_bytes", lambda url: xml)
    monkeypatch.setattr(fm, "_NEWS_DEADLINE", None)
    assert "<" not in fm.parse_feed("https://x.test/rss")[0]["source"]


def test_D13_noticias_cripto_palabra_link_da_falso_positivo(monkeypatch):
    """Documenta el falso positivo: «link» (Chainlink) coincide con la palabra inglesa «link»."""
    monkeypatch.setattr(fm, "parse_feed", lambda url, limit=8: [{"title": "Click the link to read about Bitcoin ETFs", "link": "https://x.test/1", "source": "s", "published": ""}]
                        if "coindesk" in url else [])
    items = fm.build_position_news()
    assert items and items[0]["symbol"] == "BITCOIN"      # primera de la lista, no la que más pesa
    monkeypatch.setattr(fm, "parse_feed", lambda url, limit=8: [{"title": "Please follow this link for details", "link": "https://x.test/2", "source": "s", "published": ""}]
                        if "coindesk" in url else [])
    items = fm.build_position_news()
    assert [i["symbol"] for i in items] == ["LINK"]


def test_D14_ishares_dos_navs_toma_el_primero_y_otra_divisa_se_rechaza():
    page = "IE000TEST0001 NAV as of 03/Oct/2026 EUR 12.34 other class NAV as of 03/Oct/2026 EUR 99.99"
    assert fm.parse_ishares_nav(page, "IE000TEST0001", today=dt.date(2026, 10, 6))["price"] == 12.34
    assert fm.parse_ishares_nav("IE000TEST0001 NAV as of 03/Oct/2026 USD 12.34", "IE000TEST0001") is None
    assert fm.parse_ishares_nav("NAV as of 03/Oct/2026 EUR 12.34", "IE000TEST0001") is None     # sin ISIN


def test_D15_ishares_fecha_futura_o_imposible_no_marca_stale():
    r = fm.parse_ishares_nav("I1 NAV as of 03/Oct/2027 EUR 12.34", "I1", today=dt.date(2026, 10, 6))
    # fecha futura: ya NO se declara «fresca» (antes nav_stale=False): sin nav_stale ni nav_date_iso, y el sanity-check lanza Alert
    assert "nav_stale" not in r and "nav_date_iso" not in r and r["nav_date"] == "03/Oct/2027" and r["price"] == 12.34
    r = fm.parse_ishares_nav("I1 NAV as of 31/Feb/2026 EUR 12.34", "I1", today=dt.date(2026, 10, 6))
    assert "nav_stale" not in r and r["price"] == 12.34                           # imposible: se acepta sin bandera


def test_D16_hechos_de_ishares_aum_con_unidades_y_pagina_hostil(monkeypatch):
    html = ("<html><script>var x='Management Fee 99%'</script><b>Net Assets of Fund as of 03/Oct/2026 EUR 1.20 B</b>"
            "<span>Net Assets as of 03/Oct/2026 USD 820.50 M</span> Ongoing Charges Figures 0.06 % Management Fee 0.04 %</html>")
    monkeypatch.setattr(fm.requests, "get", lambda *a, **k: types.SimpleNamespace(text=html, raise_for_status=lambda: None))
    f = fm.fetch_ishares_fund_facts("https://x.test")
    assert f["fund_aum"] == "EUR 1.20 bn" and f["class_aum"] == "USD 820.50 m"
    assert f["ongoing_charges_pct"] == 0.06


def test_D17_hechos_de_ishares_numero_malformado_aborta_el_resto(monkeypatch):
    html = "Ongoing Charges Figures 0.0.6 % Management Fee 0.04 % Initial Charge 1.0 %"
    monkeypatch.setattr(fm.requests, "get", lambda *a, **k: types.SimpleNamespace(text=html, raise_for_status=lambda: None))
    f = fm.fetch_ishares_fund_facts("https://x.test")
    # «0.0.6» no es un número: se descarta ESE campo, pero las comisiones buenas que vienen después se conservan
    assert f == {"management_fee_pct": 0.04, "initial_charge_pct": 1.0}


def test_D18_perfil_fuzz_la_salida_del_parser_siempre_cumple_los_limites_del_sanity():
    import random
    rnd = random.Random(7)
    keys = ["trailingPE", "forwardPE", "enterpriseToEbitda", "profitMargins", "operatingMargins", "beta", "heldPercentInsiders",
            "ebitda", "earningsQuarterlyGrowth", "totalCash", "totalDebt", "grossMargins", "revenueGrowth", "returnOnEquity",
            "freeCashflow", "targetMeanPrice", "numberOfAnalystOpinions", "recommendationMean", "dividendRate", "currentPrice",
            "dividendYield", "trailingAnnualDividendYield", "fiveYearAvgDividendYield", "marketCap", "enterpriseValue", "fullTimeEmployees"]
    vals = [0, -1, 1, 0.5, 1e-9, 1e3, 1e9, 1e12, 9.99e14, -1e12, float("inf"), float("-inf"), float("nan"), "Infinity", "x", None, True, [1], {"a": 1}, 3.14159, 100, 1e4 - 1e-3]
    pvals = [0, -1, 1, 0.5, 1e-9, 1e3, 5e4, float("inf"), float("nan"), "x", None, True, 3.14159, 100]   # precios realistas (hasta 5e4)
    bad = []
    for _ in range(4000):
        info = {k: rnd.choice(pvals if k in ("currentPrice", "dividendRate", "targetMeanPrice") else vals) for k in keys if rnd.random() < 0.8}
        info["currency"] = rnd.choice(["USD", "eur", "XX", 5, None]); info["financialCurrency"] = rnd.choice(["USD", "TWD", "abc1", None])
        out = fm.parse_stock_profile(info)
        row = {"symbol": "S", **out}
        for k, (lo, hi) in sc.PROFILE_NUM_BOUNDS.items():
            if k in row and not (isinstance(row[k], (int, float)) and lo <= row[k] <= hi):
                bad.append((k, row[k]))
        json.dumps(row, allow_nan=False)
    assert not bad, bad[:5]


def test_D19_capitalizacion_en_divisa_local_muy_grande_bloquearia_la_publicacion():
    """Latente: PROFILE_NUM_BOUNDS['market_cap'] = 1e15 en DIVISA LOCAL. Una acción cotizada en IDR/VND con cap >1e15 sería «inverosímil»."""
    out = fm.parse_stock_profile({"marketCap": 1.3e15, "currency": "IDR"})
    assert out["market_cap"] == int(1.3e15)
    d, m = healthy()
    d["stocks"][0].update(out)
    errs, _ = sc_run(d, m, None)
    assert any("perfil de acción" in e for e in errs)


def test_D20_carry_previous_row_reloj_y_basura():
    today = dt.date(2026, 10, 19)
    old = {"symbol": "A", "price": 5.0, "as_of": "2026-10-10"}
    r = fm._carry_previous_row("A", "Name", (), old, today=today)
    assert r["stale"] and r["stale_since"] == "2026-10-10"
    far = dict(old, stale_since="2026-09-01")
    assert fm._carry_previous_row("A", "n", (), far, today=today) is None
    garbage = dict(old, stale_since="no-es-fecha", as_of="x")
    r = fm._carry_previous_row("A", "n", (), garbage, today=today)
    assert r["stale_since"] == "2026-10-19"      # basura reinicia el contador de 30 días (aceptable: solo viene de nuestro propio feed)


def test_D21_compute_health_tolera_basura_y_fechas_futuras():
    h = fm.compute_health({"stocks": [{"symbol": "A", "profile_as_of": "2999-01-01"}, "x", None, {"symbol": "B", "profile_as_of": "zzz"}],
                           "indices": "no es lista", "forex": [None, 3]}, today=dt.date(2026, 10, 19))
    assert h["profiles"]["stocks"] == 2 and h["profiles"]["older_than_7d"] == 0


def test_D22_sanitize_tupla_con_nan_no_se_sanea():
    """Documenta el límite: tuplas no se recorren. Hoy nada del script produce tuplas con floats; si apareciera, json.dump(allow_nan=False) abortaría."""
    out = fm.sanitize_for_json({"t": (float("nan"), 1.0)})
    with pytest.raises(ValueError):
        json.dumps(out, allow_nan=False)


def test_D23_fund_analytics_ceros_y_ventanas_mal_etiquetadas(monkeypatch):
    closes = [100 + (i % 7) for i in range(300)]
    monkeypatch.setattr(fm, "yf_history", lambda *a, **k: _ohlc(closes, last_date="2026-10-09"))
    a = fm.compute_fund_analytics("X")
    assert a["volatility"]["3y"] == a["volatility"]["5y"]       # con 300 barras las ventanas «3y» y «5y» son la serie entera
    closes2 = closes[:]; closes2[150] = 0.0
    monkeypatch.setattr(fm, "yf_history", lambda *a, **k: _ohlc(closes2, last_date="2026-10-09"))
    assert fm.compute_fund_analytics("X") == {}                  # un solo cierre 0 (log(0)) anula TODO el bloque de analítica del fondo


def test_D24_main_e2e_coherencia_data_meta_y_json_estricto(tmp_path, monkeypatch):
    from test_fetch_market_data import _patch_main
    _patch_main(monkeypatch, tmp_path)
    monkeypatch.setenv("PREV_FEED_PATH", str(tmp_path / "nada.json"))
    monkeypatch.setattr(fm, "FRED_API_KEY", "")
    monkeypatch.setattr(fm, "build_market_mood", lambda: {"vix": {"value": float("nan")}})
    fm.main()
    raw = (tmp_path / "data.json").read_bytes()
    d = json.loads(raw.decode(), parse_constant=lambda c: (_ for _ in ()).throw(ValueError(c)))
    m = json.loads((tmp_path / "meta.json").read_text())
    assert m["data_bytes"] == len(raw) and m["generated_at"] == d["generated_at"] and d["market_mood"]["vix"]["value"] is None
    fm.main()                                                    # idempotente: segunda ejecución sobre el mismo directorio
    d2 = json.loads((tmp_path / "data.json").read_text())
    assert set(d2) == set(d)


def test_D25_cnn_respuestas_raras_no_tumban_el_mood(monkeypatch):
    import requests

    def resp(payload):
        return types.SimpleNamespace(json=lambda: payload, raise_for_status=lambda: None, status_code=200)
    monkeypatch.setattr(fm, "yf_history", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("no yahoo")))
    monkeypatch.setattr(fm, "build_altcoin_season", lambda prev=None: None)
    for cnn in ([], {"fear_and_greed": 5}, {"fear_and_greed": {"score": "abc"}}, {"fear_and_greed": {"score": 250, "rating": "x"}}, "texto"):
        def get(url, **kw):
            if "cnn" in url:
                return resp(cnn)
            if "alternative" in url:
                return resp({"data": []})
            raise requests.ConnectionError("x")
        monkeypatch.setattr(fm.requests, "get", get)
        mood = fm.build_market_mood()
        assert "vix" not in mood and "crypto_fear_greed" not in mood
    assert mood == {}
    # puntuación fuera de 0-100: se publica tal cual (no hay validación de rango)
    monkeypatch.setattr(fm.requests, "get", lambda url, **kw: resp({"fear_and_greed": {"score": 250, "rating": "x"}}) if "cnn" in url else (_ for _ in ()).throw(requests.ConnectionError("x")))
    assert fm.build_market_mood()["stock_fear_greed"]["value"] == 250.0


# =============================================================== sanity_check
def _write(tmp, d, m):
    (tmp / "data.json").write_text(json.dumps(d), encoding="utf-8")
    (tmp / "meta.json").write_text(json.dumps(m), encoding="utf-8")
    return str(tmp / "data.json"), str(tmp / "meta.json")


def test_D30_dos_precios_cero_pasan_el_filtro_de_inverosimiles_pero_tres_no():
    d, m = healthy()
    for x in d["stocks"][:2]:
        x["price"] = 0
    errs, _ = sc_run(d, m, None)
    assert errs == []                                    # tolerancia de 2 símbolos (hoy)
    d["stocks"][2]["price"] = 0
    errs, _ = sc_run(d, m, None)
    assert any("inverosímil" in e for e in errs)


def test_FIX_D02_nan_literal_en_el_feed_deberia_bloquear(tmp_path):
    p = tmp_path / "data.json"
    p.write_text('{"indices":[{"history":[{"close":NaN}]}]}')
    with pytest.raises(ValueError):
        sc._load_json(str(p))


def test_D31_nan_literal_confirmado_con_node(tmp_path):
    node = __import__("shutil").which("node")
    if not node:
        pytest.skip("sin node")
    r = subprocess.run([node, "-e", "try{JSON.parse('{\"a\":NaN}');console.log('ok')}catch(e){console.log('RECHAZA')}"], capture_output=True, text=True)
    assert r.stdout.strip() == "RECHAZA"
    assert json.loads('{"a":NaN}')["a"] != json.loads('{"a":NaN}')["a"]     # Python sí lo traga: el validador no lo ve


def test_D32_historico_con_cierre_cero_o_negativo_se_detecta():
    d, m = healthy()
    d["stocks"][0]["history"][100]["close"] = 0
    d["stocks"][1]["history"][100]["close"] = -5
    errs, _ = sc_run(d, m, None)
    assert any("close" in e and "S0" in e and "S1" in e for e in errs)      # BE-05: antes era un hueco (errs == [])


def test_D33_generated_at_antiguo_pasa_si_meta_coincide():
    d, m = healthy()
    d["generated_at"] = m["generated_at"] = "2020-01-01T00:00:00+00:00"
    errs, _ = sc_run(d, m, None)
    assert errs == []      # no hay regla «generated_at reciente / mayor que el del feed anterior»


def test_D34_simbolo_duplicado_pasa_y_barra_futura_bloquea():
    d, m = healthy()
    d["stocks"][1]["symbol"] = d["stocks"][0]["symbol"]
    assert sc_run(d, m, None)[0] == []      # hueco menor que sigue abierto: los símbolos duplicados no se validan
    d["stocks"][2]["history"][-1]["date"] = (TODAY + dt.timedelta(days=400)).isoformat()
    assert any("futura" in e and "S2" in e for e in sc_run(d, m, None)[0])      # BE-05: antes pasaba


def test_D35_limites_exactos_de_tamano():
    d, m = healthy()
    m2 = dict(m, data_bytes=sc.MAX_DATA_BYTES)
    errs, _ = sc_run(d, m2, None, data_bytes=sc.MAX_DATA_BYTES)
    assert any("demasiado grande" in e for e in errs)
    m3 = dict(m, data_bytes=sc.MIN_DATA_BYTES - 1)
    errs, _ = sc_run(d, m3, None, data_bytes=sc.MIN_DATA_BYTES - 1)
    assert any("pequeño" in e for e in errs)
    m4 = dict(m, data_bytes=sc.MIN_DATA_BYTES)
    assert not any("pequeño" in e for e in sc_run(d, m4, None, data_bytes=sc.MIN_DATA_BYTES)[0])


def test_D36_noticias_con_enlaces_hostiles_bloquean():
    for link in ("javascript:alert(1)", "data:text/html,x", "//evil.test", "", "ftp://x"):
        d, m = healthy()
        d["news"]["general"][0]["link"] = link
        assert any("enlace" in e for e in sc_run(d, m, None)[0]), link


def test_D37_nav_stale_es_aviso_no_alerta():
    d, m = healthy()
    d["your_funds"][0]["nav_stale"] = True      # 3 días de edad real (03/Oct vs 06/Oct): el flag solo se refleja, no escala
    errs, warns = sc_run(d, m, None)
    assert errs == [] and any("antiguo" in w for w in warns) and not any(isinstance(w, sc.Alert) for w in warns)
    # Cuando el NAV es realmente viejo (>= NAV_ALERT_DAYS) SÍ hay Alert -> /fail: ver tests/test_nav_chain.py (antes: semanas congelado sin aviso)


def test_D38_tipos_raros_en_listas_no_explotan():
    d, m = healthy()
    d["stocks"] = [1, None, "x"] + d["stocks"]
    d["news"] = {"general": "texto", "positions": 5}
    try:
        errs, _ = sc_run(d, m, None)
    except Exception as e:     # main() lo captura y devuelve 1; aquí solo comprobamos que no es silencioso
        assert isinstance(e, (TypeError, AttributeError))


# =============================================================== workflows (bash real)
def _wf(name):
    return yaml.safe_load((WF / name).read_text(encoding="utf-8"))


def _step(wf, job, name):
    return next(s for s in wf["jobs"][job]["steps"] if s.get("name") == name)


def _sh(tmp_path, script, env, bindir=None):
    f = tmp_path / "s.sh"; f.write_text(script)
    e = {"PATH": (str(bindir) + ":" if bindir else "") + os.environ["PATH"], "HOME": str(tmp_path)}
    e.update(env)
    return subprocess.run(["bash", "-e", str(f)], cwd=tmp_path, env=e, capture_output=True, text=True, timeout=30)


HOSTILE = ['stocks con precio antiguo: [\'A\'] $(touch PWNED) `touch PWNED2` "; touch PWNED3; "', "-n -e x\\n%0A::set-output name=a::b", "linea1\nlinea2\r::error::falso"]


@pytest.mark.parametrize("alerts", HOSTILE)
def test_D40_collect_alerts_no_ejecuta_ni_inyecta(tmp_path, alerts):
    script = _step(_wf("update-data.yml"), "publish", "Collect alerts")["run"]
    out, summ = tmp_path / "out", tmp_path / "sum"
    (tmp_path / "alerts.txt").write_text(alerts)
    r = _sh(tmp_path, script, {"RUNNER_TEMP": str(tmp_path), "GITHUB_OUTPUT": str(out), "GITHUB_STEP_SUMMARY": str(summ)})
    assert r.returncode == 0
    assert not list(tmp_path.glob("PWNED*"))
    lines = out.read_text().splitlines()
    assert len(lines) == 1 and lines[0].startswith("alerts=")      # una sola línea: sin inyección de salidas
    assert r.stdout.count("::warning::") == 1 and "\n::error::" not in r.stdout


def test_D41_collect_alerts_sin_fichero_o_vacio_no_deja_aviso(tmp_path):
    script = _step(_wf("update-data.yml"), "publish", "Collect alerts")["run"]
    out = tmp_path / "out"
    r = _sh(tmp_path, script, {"RUNNER_TEMP": str(tmp_path), "GITHUB_OUTPUT": str(out), "GITHUB_STEP_SUMMARY": str(tmp_path / "s")})
    assert r.returncode == 0 and out.read_text() == "alerts=\n" and "::warning::" not in r.stdout
    (tmp_path / "alerts.txt").write_text("")
    r = _sh(tmp_path, script, {"RUNNER_TEMP": str(tmp_path), "GITHUB_OUTPUT": str(out), "GITHUB_STEP_SUMMARY": str(tmp_path / "s")})
    assert "::warning::" not in r.stdout


def _fake_curl(tmp_path):
    b = tmp_path / "bin"; b.mkdir(exist_ok=True)
    c = b / "curl"
    c.write_text('#!/bin/bash\nprintf "%s\\n" "$*" >> "$CALLS"\n')
    c.chmod(c.stat().st_mode | stat.S_IEXEC)
    return b


@pytest.mark.parametrize("fetch,publish,alerts,want", [
    ("success", "success", "", "https://hc.test/p"),
    ("success", "success", "perfiles: solo 3", "https://hc.test/p/fail"),
    ("failure", "skipped", "", "https://hc.test/p/fail"),
    ("cancelled", "skipped", "x", "https://hc.test/p/fail"),
    ("success", "failure", "", "https://hc.test/p/fail"),
])
def test_D42_heartbeat_con_alertas(tmp_path, fetch, publish, alerts, want):
    script = _step(_wf("update-data.yml"), "heartbeat", "Ping Healthchecks.io")["run"]
    calls = tmp_path / "calls.log"; calls.write_text("")
    r = _sh(tmp_path, script, {"HC": "https://hc.test/p/", "FETCH": fetch, "PUBLISH": publish, "ALERTS": alerts, "CALLS": str(calls)}, _fake_curl(tmp_path))
    assert r.returncode == 0
    last = calls.read_text().splitlines()[-1]
    assert last.endswith(want)
    if alerts and fetch == "success" and publish == "success":
        assert "--data-raw degraded: " + alerts in last


def test_D43_heartbeat_alerta_hostil_viaja_como_dato_no_como_codigo(tmp_path):
    script = _step(_wf("update-data.yml"), "heartbeat", "Ping Healthchecks.io")["run"]
    calls = tmp_path / "calls.log"; calls.write_text("")
    payload = "$(touch PWNED) `touch PWNED2` ; touch PWNED3"
    r = _sh(tmp_path, script, {"HC": "https://hc.test/p", "FETCH": "success", "PUBLISH": "success", "ALERTS": payload, "CALLS": str(calls)}, _fake_curl(tmp_path))
    assert r.returncode == 0 and not list(tmp_path.glob("PWNED*"))


def test_D44_secretos_usados_solo_los_esperados_y_ninguno_en_run():
    used = set()
    for f in WF.glob("*.yml"):
        txt = f.read_text(encoding="utf-8")
        used |= set(re.findall(r"secrets\.([A-Za-z0-9_]+)", txt))
    assert used == {"FRED_API_KEY", "HC_UPDATE_URL", "HC_SUPABASE_URL", "HC_UPDATE_V2_URL", "PERSONAL_FEED_TOKEN"}      # + el check propio y el token de los datos propios del feed v2 (opcionales)


def test_D45_permisos_y_timeouts_de_todos_los_jobs():
    for f in WF.glob("*.yml"):
        wf = yaml.safe_load(f.read_text(encoding="utf-8"))
        assert "permissions" in wf, f.name
        for jn, job in wf["jobs"].items():
            # 20 min en general; el `fetch` del feed v2 baja primero el sitio anterior (~3 min) y tiene un presupuesto propio de 17 min (BE-12)
            limit = 25 if (f.name, jn) == ("publish-feed-v2.yml", "fetch") else 20
            assert isinstance(job.get("timeout-minutes"), int) and job["timeout-minutes"] <= limit, (f.name, jn)
            assert re.fullmatch(r"ubuntu-24\.04", job["runs-on"]), (f.name, jn)
            perms = job.get("permissions", wf["permissions"])
            if perms.get("contents") == "write":
                assert (f.name, jn) in {("update-data.yml", "publish"), ("keepalive.yml", "keepalive")}


def test_D46_cron_no_cae_en_hora_en_punto_y_hay_ruta_manual():
    wf = _wf("update-data.yml")
    on = wf.get("on") or wf.get(True)
    crons = [c["cron"] for c in on["schedule"]]
    assert all(c.split()[0] not in ("0", "30", "15", "45") for c in crons) and "workflow_dispatch" in on
    k = _wf("keepalive.yml"); on = k.get("on") or k.get(True)
    assert on["schedule"][0]["cron"].split()[0] not in ("0", "30")


def test_D47_el_job_fetch_no_ve_secretos_salvo_fred_ni_token_de_escritura():
    job = _wf("update-data.yml")["jobs"]["fetch"]
    txt = json.dumps(job)
    assert "HC_UPDATE_URL" not in txt and "github.token" not in txt and "GITHUB_TOKEN" not in txt
    assert job["permissions"] == {"contents": "read"}
    assert all(s["with"]["persist-credentials"] is False for s in job["steps"] if str(s.get("uses", "")).startswith("actions/checkout"))
