"""Sonda de fondos, punto 1: ritmo, tope de peticiones, parada limpia ante 429/sin red, clases de error y cliente real (con yfinance simulado)."""
import sys

import pandas as pd
import pytest

import fund_probe as fp
from fund_probe_fakes import TODAY, FakeTime, hist_df, rows


def mk_gate(max_requests=250, **kw):
    t = FakeTime()
    return fp.Gate(max_requests=max_requests, clock=t.clock, sleep=t.sleep, **kw), t


# ------------------------------------------------------------------ ritmo y tope
def test_pausa_minima_de_un_segundo_entre_peticiones():
    g, t = mk_gate()
    stamps = []
    for _ in range(5):
        g.call(lambda: stamps.append(t.clock()))
    assert all(b - a >= 1.0 for a, b in zip(stamps, stamps[1:]))


def test_la_pausa_nunca_baja_de_un_segundo_aunque_se_pida():
    g, _ = mk_gate(min_pause=0.0)
    assert g.min_pause >= 1.0


def test_tope_de_peticiones_se_para_y_no_hace_la_siguiente():
    g, _ = mk_gate(max_requests=3)
    ran = []
    for _ in range(3):
        g.call(lambda: ran.append(1))
    with pytest.raises(fp.Stopped) as e:
        g.call(lambda: ran.append(1))
    assert e.value.reason == "presupuesto" and len(ran) == 3 and g.stopped == "presupuesto"


def test_el_coste_cuenta_y_un_coste_que_no_cabe_no_se_ejecuta():
    g, _ = mk_gate(max_requests=3)
    g.call(lambda: 1, cost=2)
    with pytest.raises(fp.Stopped):
        g.call(lambda: 1, cost=2)
    assert g.used == 2


def test_el_tope_duro_es_250_aunque_se_pida_mas():
    g, _ = mk_gate(max_requests=10_000)
    assert g.max_requests == 250


# ------------------------------------------------------------------ 429 y sin red: parada limpia, sin reintentos
def test_429_para_a_la_primera_sin_reintentar_ni_dormir_de_mas():
    g, t = mk_gate()
    calls = []

    def boom():
        calls.append(1)
        raise fp.yf_exceptions.YFRateLimitError()

    with pytest.raises(fp.ProbeError) as e:
        g.call(boom)
    assert e.value.cls == "429" and g.stopped == "429" and len(calls) == 1
    with pytest.raises(fp.Stopped):                                   # ya parado: ni una petición más
        g.call(boom)
    assert len(calls) == 1 and sum(t.sleeps) < 5                      # nada de bucles de espera largos


def test_tres_fallos_de_red_seguidos_paran_y_un_exito_reinicia_la_racha():
    g, _ = mk_gate()

    def down():
        raise ConnectionError("x")
    for _ in range(2):
        with pytest.raises(fp.ProbeError):
            g.call(down)
    g.call(lambda: 1)                                                 # éxito: racha a cero
    for _ in range(2):
        with pytest.raises(fp.ProbeError):
            g.call(down)
    assert g.stopped is None
    with pytest.raises(fp.ProbeError):
        g.call(down)
    assert g.stopped == "sin_red"


def test_un_error_de_parseo_o_vacio_no_para_la_sonda():
    g, _ = mk_gate()
    for exc in (KeyError("k"), fp.yf_exceptions.YFPricesMissingError("T", "x")):
        with pytest.raises(fp.ProbeError):
            g.call(lambda e=exc: (_ for _ in ()).throw(e))
    assert g.stopped is None


# ------------------------------------------------------------------ clasificación cerrada
@pytest.mark.parametrize("exc,cls", [
    (fp.yf_exceptions.YFRateLimitError(), "429"),
    (RuntimeError("HTTP Error 429: Too Many Requests"), "429"),
    (RuntimeError("Too Many Requests. Rate limited"), "429"),
    (ConnectionError("x"), "sin_red"), (TimeoutError("x"), "sin_red"), (OSError("x"), "sin_red"),
    (fp.yf_exceptions.YFDataException("Yahoo down"), "sin_red"),
    (fp.yf_exceptions.YFPricesMissingError("T", "x"), "vacio"), (fp.yf_exceptions.YFTzMissingError("T"), "vacio"),
    (KeyError("k"), "parseo"), (ValueError("v"), "parseo"), (RuntimeError("cualquier cosa"), "parseo"),
])
def test_clasificacion_de_excepciones(exc, cls):
    assert fp.classify_exception(exc) == cls and cls in fp.ERROR_CLASSES


def test_las_clases_son_exactamente_las_cinco_cerradas():
    assert fp.ERROR_CLASSES == ("sin_red", "429", "vacio", "parseo", "divisa_rara")


# ------------------------------------------------------------------ medida de la serie
def test_medida_fecha_retraso_barras_y_divisa():
    m = fp.measure({"rows": rows(), "currency": "EUR"}, TODAY)
    assert (m["last_date"], m["lag_days"], m["bars"], m["currency"], m["flags"]) == ("2026-11-13", 3, 21, "EUR", [])


def test_retraso_cero_y_futuro_se_recorta_a_cero():
    from datetime import date, timedelta
    assert fp.measure({"rows": [(TODAY, 1.0)], "currency": "USD"}, TODAY)["lag_days"] == 0
    assert fp.measure({"rows": [(TODAY + timedelta(days=1), 1.0)], "currency": "USD"}, TODAY)["lag_days"] == 0
    assert fp.measure({"rows": [(date(2026, 11, 1), 1.0)], "currency": "USD"}, TODAY)["lag_days"] == 15


def test_serie_vacia_es_vacio():
    with pytest.raises(fp.ProbeError) as e:
        fp.measure({"rows": [], "currency": "EUR"}, TODAY)
    assert e.value.cls == "vacio"


def test_barras_invalidas_se_descartan_y_si_no_queda_ninguna_es_parseo():
    ok = fp.measure({"rows": rows(n=5) + [(TODAY, float("nan")), (TODAY, -1.0), ("no-fecha", 1.0)], "currency": "EUR"}, TODAY)
    assert ok["bars"] == 5 and ok["last_date"] == "2026-11-13"
    with pytest.raises(fp.ProbeError) as e:
        fp.measure({"rows": [(TODAY, float("nan")), ("x", 1.0)], "currency": "EUR"}, TODAY)
    assert e.value.cls == "parseo"


@pytest.mark.parametrize("cur", [None, "", "XXX", "eur ", 5])
def test_divisa_rara_se_marca_pero_la_medida_se_conserva(cur):
    m = fp.measure({"rows": rows(), "currency": cur}, TODAY)
    assert m["flags"] == ["divisa_rara"] and m["bars"] == 21


@pytest.mark.parametrize("cur", ["EUR", "USD", "GBP", "GBp", "CHF", "SEK", "JPY"])
def test_divisas_conocidas_no_se_marcan(cur):
    assert fp.measure({"rows": rows(), "currency": cur}, TODAY)["flags"] == []


# ------------------------------------------------------------------ cliente real con yfinance SIMULADO (formas reales)
class _Search:
    def __init__(self, query, **kw):
        _Search.kw = kw
        _Search.query = query
        self.quotes = [{"symbol": "0P00000AAA.F", "shortname": "Synthetic Fund", "quoteType": "MUTUALFUND", "exchange": "FRA"}]


def _ticker_cls(df, meta, exc=None):
    class T:
        def __init__(self, sym):
            self.sym = sym

        def history(self, **kw):
            T.kw = kw
            if exc:
                raise exc
            return df

        @property
        def history_metadata(self):
            return meta
    return T


def test_cliente_real_traduce_search_y_history(monkeypatch):
    import yfinance as yf
    monkeypatch.setattr(yf, "Search", _Search)
    T = _ticker_cls(hist_df(), {"currency": "EUR", "exchangeName": "FRA"})
    monkeypatch.setattr(yf, "Ticker", T)
    c = fp.YahooClient()
    assert c.search("x")[0]["symbol"] == "0P00000AAA.F"
    assert _Search.kw.get("news_count") == 0                              # no se piden noticias: peticiones inútiles
    h = c.history("0P00000AAA.F")
    assert h["currency"] == "EUR" and len(h["rows"]) == 21 and h["rows"][-1][0].isoformat() == "2026-11-13"
    assert T.kw["period"] == "1mo" and T.kw["interval"] == "1d"
    assert fp.measure(h, TODAY)["lag_days"] == 3


def test_cliente_real_indice_naive_y_tz_con_hora_usa_la_fecha_local(monkeypatch):
    import yfinance as yf
    df = hist_df(tz="Europe/Madrid")
    monkeypatch.setattr(yf, "Ticker", _ticker_cls(df, {"currency": "EUR"}))
    h = fp.YahooClient().history("S")
    assert h["rows"][-1][0].isoformat() == "2026-11-13"
    df2 = hist_df(tz=None)
    monkeypatch.setattr(yf, "Ticker", _ticker_cls(df2, {"currency": "EUR"}))
    assert fp.YahooClient().history("S")["rows"][-1][0].isoformat() == "2026-11-13"


def test_cliente_real_dataframe_vacio_y_sin_close(monkeypatch):
    import yfinance as yf
    monkeypatch.setattr(yf, "Ticker", _ticker_cls(pd.DataFrame(), {"currency": "EUR"}))
    assert fp.YahooClient().history("S")["rows"] == []
    bad = hist_df().drop(columns=["Close"])
    monkeypatch.setattr(yf, "Ticker", _ticker_cls(bad, {"currency": "EUR"}))
    with pytest.raises(fp.ProbeError) as e:
        fp.YahooClient().history("S")
    assert e.value.cls == "parseo"


def test_cliente_real_sin_metadatos_da_divisa_none(monkeypatch):
    import yfinance as yf
    monkeypatch.setattr(yf, "Ticker", _ticker_cls(hist_df(), {}))
    assert fp.YahooClient().history("S")["currency"] is None


def test_cliente_real_propaga_el_429_de_yfinance(monkeypatch):
    import yfinance as yf
    monkeypatch.setattr(yf, "Ticker", _ticker_cls(None, {}, exc=fp.yf_exceptions.YFRateLimitError()))
    g, _ = mk_gate()
    c = fp.YahooClient()
    with pytest.raises(fp.ProbeError) as e:
        g.call(lambda: c.history("S"), cost=fp.HISTORY_COST)
    assert e.value.cls == "429" and g.stopped == "429"


def test_cliente_real_desactiva_hide_exceptions_para_ver_los_429():
    import yfinance as yf
    fp.YahooClient()
    assert yf.config.debug.hide_exceptions is False


def test_el_modulo_no_importa_cosas_de_la_cartera():
    src = open(fp.__file__, encoding="utf-8").read()
    for bad in ("personal_v2", "fetch_market_data", "supabase", "SUPABASE", "registry.json"):
        assert bad not in src, bad
    assert "fund_probe" in sys.modules
