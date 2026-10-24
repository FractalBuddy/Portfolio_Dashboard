"""Arreglos de la auditoría 08 (backend), segunda tanda: BE-08, BE-12, BE-14, BE-15, BE-16, BE-17, BE-20. Datos sintéticos, sin red."""
import pytest
from yfinance.exceptions import YFRateLimitError

import fetch_market_data as fm
from test_fetch_market_data import _ohlc


@pytest.fixture
def sleeps(monkeypatch):
    """Captura time.sleep (sin dormir): lista de segundos pedidos."""
    out = []
    monkeypatch.setattr(fm.time, "sleep", lambda s: out.append(s))
    return out


class _Ticker:
    """yf.Ticker falso: `script` es una lista de resultados por llamada a history(): excepción o DataFrame."""
    calls = 0
    script = []

    def __init__(self, symbol):
        self.history_metadata = {"currency": "USD"}

    def history(self, **kw):
        type(self).calls += 1
        item = type(self).script[min(type(self).calls - 1, len(type(self).script) - 1)]
        if isinstance(item, Exception):
            raise item
        return item


@pytest.fixture
def ticker(monkeypatch):
    class T(_Ticker):
        calls = 0
        script = []
    monkeypatch.setattr(fm.yf, "Ticker", T)
    return T


# ------------------------------------------------------------------ BE-08: 429 con pausa larga, abandono limpio y ritmo acotado
def test_BE08_un_429_hace_una_pausa_larga_y_reintenta(ticker, sleeps):
    ticker.script = [YFRateLimitError(), _ohlc([10, 11, 12])]
    h = fm.yf_history("X", period="8d")
    assert not h.empty and ticker.calls == 2
    assert any(s >= fm.YF_RATE_LIMIT_PAUSE[0] for s in sleeps)          # antes: espera de 0,5-4 s, como para cualquier fallo


def test_BE08_el_429_tambien_se_reconoce_por_el_texto(ticker, sleeps):
    ticker.script = [RuntimeError("HTTP Error 429: Too Many Requests"), _ohlc([10, 11, 12])]
    fm.yf_history("X", period="8d")
    assert any(s >= fm.YF_RATE_LIMIT_PAUSE[0] for s in sleeps)


def test_BE08_tras_429_seguidos_se_abandona_yahoo_sin_mas_peticiones(ticker, sleeps):
    ticker.script = [YFRateLimitError()]
    with pytest.raises(YFRateLimitError):
        fm.yf_history("X", period="8d")
    n = ticker.calls
    assert n == fm.YF_RATE_LIMIT_MAX_CONSECUTIVE and fm._YF_ABANDONED
    with pytest.raises(fm.YahooStop):                       # el resto de símbolos ni toca la red
        fm.yf_history("Y", period="8d")
    assert ticker.calls == n
    assert fm.fetch_symbol("Z") is None and ticker.calls == n   # fetch_symbol degrada a «sin datos» (se arrastra la fila anterior)


def test_BE08_una_respuesta_buena_corta_la_racha(ticker, sleeps):
    ok = _ohlc([10, 11, 12])
    ticker.script = [YFRateLimitError(), ok, YFRateLimitError(), ok, YFRateLimitError(), ok]
    for _ in range(3):
        fm.yf_history("X", period="8d", tries=2)
    assert not fm._YF_ABANDONED and fm._YF_429_CONSECUTIVE == 0 and fm._YF_429_TOTAL == 3


def test_BE08_tope_total_de_429_aunque_no_sean_seguidos(ticker, sleeps):
    ok = _ohlc([10, 11, 12])
    ticker.script = [YFRateLimitError(), ok] * fm.YF_RATE_LIMIT_MAX_TOTAL
    for _ in range(fm.YF_RATE_LIMIT_MAX_TOTAL):
        try:
            fm.yf_history("X", period="8d", tries=2)
        except Exception:
            break
    assert fm._YF_ABANDONED


def test_BE08_la_pausa_no_pasa_del_presupuesto_que_queda(ticker, sleeps, monkeypatch):
    monkeypatch.setattr(fm, "_DEADLINE", fm.time.monotonic() + 7)
    ticker.script = [YFRateLimitError(), _ohlc([10, 11, 12])]
    fm.yf_history("X", period="8d")
    assert 5 <= max(sleeps) <= 7.5                 # pausa larga, pero recortada al presupuesto


def test_BE08_rafaga_acotada_separacion_entre_peticiones_y_pausa_periodica(ticker, sleeps):
    ticker.script = [_ohlc([10, 11, 12])]
    n = fm.YF_BURST_SIZE + 1
    for _ in range(n):
        fm.yf_history("X", period="8d")
    assert any(s >= fm.YF_BURST_PAUSE for s in sleeps)                  # una pausa cada YF_BURST_SIZE peticiones
    assert sum(1 for s in sleeps if 0 < s <= fm.YF_REQ_GAP[1]) >= n - 1  # y una separación mínima entre peticiones


def test_BE08_el_perfil_de_acciones_tambien_respeta_el_429(monkeypatch, sleeps):
    n = {"t": 0}

    class T:
        def __init__(self, s):
            n["t"] += 1

        @property
        def info(self):
            raise YFRateLimitError()
    monkeypatch.setattr(fm.yf, "Ticker", T)
    for _ in range(4):
        assert fm.fetch_stock_profile("AAPL") == {}
    assert fm._YF_ABANDONED and any(s >= fm.YF_RATE_LIMIT_PAUSE[0] for s in sleeps)
    antes = n["t"]
    assert fm.fetch_stock_profile("MSFT") == {} and n["t"] == antes     # abandonado: no se construye ni un Ticker más


# ------------------------------------------------------------------ BE-12: presupuesto global de tiempo
class _Reloj:
    """Reloj falso: sleep() lo adelanta y las peticiones colgadas consumen su timeout entero (el peor caso de la auditoría)."""
    def __init__(self, t0=1000.0):
        self.t = t0

    def monotonic(self):
        return self.t

    def sleep(self, s):
        self.t += max(0.0, s)


@pytest.fixture
def reloj(monkeypatch):
    import types
    r = _Reloj()
    monkeypatch.setattr(fm, "time", types.SimpleNamespace(monotonic=r.monotonic, sleep=r.sleep))
    monkeypatch.setattr(fm, "_HARD_DEADLINE", r.t + fm.HARD_BUDGET_SECONDS, raising=False)
    monkeypatch.setattr(fm, "_DEADLINE", r.t + 14 * 60)
    monkeypatch.setattr(fm, "_BUDGET_HIT", False, raising=False)
    return r


@pytest.fixture
def red_colgada(monkeypatch, reloj):
    """requests.get que siempre se cuelga hasta su timeout y falla: registra (url, timeout)."""
    import requests
    llamadas = []

    def get(url, *a, **k):
        t = k.get("timeout", 15)
        t = max(t) if isinstance(t, tuple) else t
        llamadas.append((url, t))
        reloj.t += t
        raise requests.Timeout("colgado")
    monkeypatch.setattr(fm.requests, "get", get)
    return llamadas


def test_BE12_con_el_presupuesto_agotado_ninguna_fase_toca_la_red(monkeypatch, reloj, red_colgada):
    reloj.t = fm._HARD_DEADLINE + 1
    monkeypatch.setattr(fm, "FRED_API_KEY", "clave")
    assert fm.fetch_ishares_nav("https://x.test/p", "IE0000000001") is None
    assert fm.fetch_ishares_fund_facts("https://x.test/p") == {}
    assert fm.fetch_fred_series("DGS10") is None
    assert fm.fetch_cpi_history()[0] == []
    with pytest.raises(TimeoutError):
        fm._cg_markets({"vs_currency": "usd"})
    with pytest.raises(TimeoutError):
        fm.fetch_lido_apr()
    monkeypatch.setattr(fm, "yf_history", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("sin yahoo")))
    monkeypatch.setattr(fm, "build_altcoin_season", lambda prev=None: None)
    fm.build_market_mood()
    assert red_colgada == []
    assert fm._BUDGET_HIT is True


def test_BE12_peor_caso_con_todo_colgado_cabe_en_el_timeout_del_job(monkeypatch, reloj, red_colgada):
    """Yahoo gasta sus 14 min enteros y TODAS las demás fuentes se cuelgan hasta su timeout: antes el job pasaba de 31 min."""
    inicio = reloj.t
    reloj.t = fm._DEADLINE                                          # Yahoo ya ha consumido su presupuesto
    monkeypatch.setattr(fm, "FRED_API_KEY", "clave")
    monkeypatch.setattr(fm, "YOUR_FUNDS", [{"isin": "IE0000000001", "name": "n", "product_url": "https://x.test/a", "proxy_symbol": "P"},
                                           {"isin": "IE0000000002", "name": "m", "product_url": "https://x.test/b", "proxy_symbol": "Q"}])
    monkeypatch.setattr(fm, "yf_history", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("sin yahoo")))
    fm.build_your_funds(None)
    fm.attach_fund_extras([{"symbol": s} for s in fm.FUND_FACT_URLS], None)
    fm.build_rates()
    fm.fetch_cpi_history()
    fm.build_altcoin_season(None)
    fm.build_staking(None)
    fm.build_market_mood()
    assert reloj.t - inicio <= fm.HARD_BUDGET_SECONDS + 5            # la última petición recortada a ≥ 1 s es todo lo que puede pasarse
    assert reloj.t - inicio < 20 * 60 - 60                           # y queda margen dentro del timeout-minutes: 20 del job


def test_BE12_timeout_de_peticion_y_esperas_se_recortan_al_presupuesto(monkeypatch, reloj, red_colgada):
    reloj.t = fm._HARD_DEADLINE - 3                                  # quedan 3 s
    monkeypatch.setattr(fm, "FRED_API_KEY", "clave")
    with pytest.raises(Exception):
        fm._fred_get({"series_id": "X"})
    assert red_colgada and all(t <= 3.01 for _, t in red_colgada)
    assert reloj.t <= fm._HARD_DEADLINE + 1.5


def test_BE12_main_publica_health_budget_exhausted(tmp_path, monkeypatch, reloj):
    import json
    from test_fetch_market_data import _patch_main
    _patch_main(monkeypatch, tmp_path)
    monkeypatch.setenv("PREV_FEED_PATH", str(tmp_path / "nada.json"))
    monkeypatch.setattr(fm, "FRED_API_KEY", "")
    fm.main()
    assert json.loads((tmp_path / "data.json").read_text())["health"]["budget_exhausted"] is False
    reloj.t = fm._HARD_DEADLINE + 1
    monkeypatch.setattr(fm, "build_market_mood", lambda: (fm._budget_exhausted(), {})[1])    # una fase choca con el tope
    fm.main()
    assert json.loads((tmp_path / "data.json").read_text())["health"]["budget_exhausted"] is True


# ------------------------------------------------------------------ BE-14: entidades HTML en los títulos de noticias
def _rss(titulo, canal="Canal", publicado=None):
    pub = "<pubDate>%s</pubDate>" % publicado if publicado else ""
    return ("<?xml version='1.0'?><rss version='2.0'><channel><title>%s</title><item><title>%s</title>%s"
            "<link>https://a.test/1</link></item></channel></rss>" % (canal, titulo, pub)).encode()


@pytest.mark.parametrize("titulo,esperado", [
    ("&amp;lt;img src=x onerror=alert(1)&amp;gt;", ""),                       # doble codificación (reproducción de la auditoría)
    ("&amp;amp;lt;script&amp;amp;gt;alert(1)&amp;amp;lt;/script&amp;amp;gt;Hola", "alert(1)Hola"),   # triple
    ("&lt;b&gt;negrita&lt;/b&gt; y más", "negrita y más"),
    ("AT&amp;amp;T sube un 3%", "AT&T sube un 3%"),                          # una entidad legítima se conserva como texto
    ("S&amp;P 500 &amp;lt; 5000", "S&P 500 5000"),                           # un «<» suelto se descarta
])
def test_BE14_titulos_sin_marcado_ni_entidades_de_marcado(monkeypatch, titulo, esperado):
    monkeypatch.setattr(fm, "_fetch_feed_bytes", lambda url: _rss(titulo))
    monkeypatch.setattr(fm, "_NEWS_DEADLINE", None)
    t = fm.parse_feed("https://x.test/rss")[0]["title"]
    assert t == esperado and "<" not in t and ">" not in t and "&lt;" not in t.lower() and "&gt;" not in t.lower()


def test_BE14_source_y_published_tambien_son_texto_plano(monkeypatch):
    monkeypatch.setattr(fm, "_fetch_feed_bytes", lambda url: _rss("t", canal="&amp;lt;i&amp;gt;Fuente&amp;lt;/i&amp;gt;",
                                                                 publicado="Mon, 05 Oct 2026 10:00:00 +0000 &amp;lt;b&amp;gt;"))
    monkeypatch.setattr(fm, "_NEWS_DEADLINE", None)
    it = fm.parse_feed("https://x.test/rss")[0]
    assert it["source"] == "Fuente" and "<" not in it["published"] and it["published"].startswith("Mon, 05 Oct 2026")


def test_BE14_el_titulo_limpio_pasa_el_validador():
    import sanity_check as sc
    assert sc._text_problem("title", "S&P 500 5000 y AT&T") is None
