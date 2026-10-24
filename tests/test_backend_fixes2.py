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
