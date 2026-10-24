"""Arreglos de la auditoría 08 (backend): BE-01 (divisa/unidad en las filas de precio) y BE-13 (cierre previo no positivo).
Datos sintéticos, sin red."""
import pytest

import fetch_market_data as fm
from test_fetch_market_data import _ohlc


@pytest.fixture(autouse=True)
def _no_sleep(monkeypatch):
    monkeypatch.setattr(fm.time, "sleep", lambda *_a, **_k: None)


def _con_divisa(closes, currency):
    df = _ohlc(closes)
    if currency is not None:
        df.attrs["currency"] = currency
    return df


# ------------------------------------------------------------------ BE-01
@pytest.mark.parametrize("cur,divisor", [("GBp", 100), ("GBX", 100), ("ZAc", 100), ("ILA", 100),
                                         ("GBP", 1), ("USD", 1), ("EUR", 1)])
def test_BE01_fetch_symbol_publica_currency_tal_cual_y_divisor(monkeypatch, cur, divisor):
    monkeypatch.setattr(fm, "yf_history", lambda *a, **k: _con_divisa([10, 11, 12, 13], cur))
    row = fm.fetch_symbol("X")
    assert row["currency"] == cur                    # sin .upper(): GBp sigue siendo GBp
    assert row["price_unit_divisor"] == divisor
    assert row["price"] == 13.0                      # campos previos intactos (cambio aditivo)


@pytest.mark.parametrize("cur", [None, "", "12", "GBPX€", 5])
def test_BE01_sin_divisa_valida_no_se_inventa_nada(monkeypatch, cur):
    df = _ohlc([10, 11, 12, 13])
    if cur is not None:
        df.attrs["currency"] = cur
    monkeypatch.setattr(fm, "yf_history", lambda *a, **k: df)
    row = fm.fetch_symbol("X")
    assert "currency" not in row and "price_unit_divisor" not in row and row["price"] == 13.0


def test_BE01_yf_history_toma_la_divisa_de_history_metadata_sin_peticion_extra(monkeypatch):
    seen = {"history": 0}

    class T:
        def __init__(self, s): pass
        @property
        def history_metadata(self):
            assert seen["history"] == 1              # ya cargada por history(): no dispara otra petición
            return {"currency": "GBp"}
        def history(self, **kw):
            seen["history"] += 1
            return _ohlc([10, 11, 12, 13])
    monkeypatch.setattr(fm.yf, "Ticker", T)
    monkeypatch.setattr(fm, "_YF_FAIL_STREAK", 0)
    h = fm.yf_history("SWDA.L", period="8d")
    assert h.attrs["currency"] == "GBp"


def test_BE01_yf_history_sobrevive_si_no_hay_metadata(monkeypatch):
    class T:
        def __init__(self, s): pass
        @property
        def history_metadata(self):
            raise RuntimeError("sin metadata")
        def history(self, **kw):
            return _ohlc([10, 11, 12, 13])
    monkeypatch.setattr(fm.yf, "Ticker", T)
    monkeypatch.setattr(fm, "_YF_FAIL_STREAK", 0)
    h = fm.yf_history("X", period="8d")
    assert not h.empty and "currency" not in h.attrs


def test_BE01_la_fila_arrastrada_conserva_currency(monkeypatch):
    monkeypatch.setattr(fm, "yf_history", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("caído")))
    old = {"symbol": "SWDA.L", "price": 10882.0, "as_of": "2999-01-01", "currency": "GBp", "price_unit_divisor": 100}
    rows = fm.build_list([("SWDA.L", "iShares World")], [old])
    assert rows[0]["stale"] is True and rows[0]["currency"] == "GBp" and rows[0]["price_unit_divisor"] == 100


def test_BE01_cap_currency_no_cambia_de_contrato():
    # El campo ya publicado (para tipos de cambio) sigue normalizado; la divisa de COTIZACIÓN va en `currency`.
    out = fm.parse_stock_profile({"marketCap": 5e9, "currency": "GBp", "currentPrice": 1234.0})
    assert out["cap_currency"] == "GBP"


# ------------------------------------------------------------------ BE-13
def test_BE13_cierre_previo_negativo_no_produce_variacion_absurda(monkeypatch):
    monkeypatch.setattr(fm, "yf_history", lambda *a, **k: _ohlc([20, 20, 20, -37, 18]))   # precedente: WTI, 2020-04-20
    row = fm.fetch_symbol("CL=F")
    assert row["change_pct"] is None                  # antes: -148.65
    assert row["price"] == 18.0


def test_BE13_cierre_previo_normal_sigue_igual(monkeypatch):
    monkeypatch.setattr(fm, "yf_history", lambda *a, **k: _ohlc([10, 10, 10, 10, 11]))
    row = fm.fetch_symbol("X")
    assert row["change_pct"] == 10.0


# ------------------------------------------------------------------ BE-01 (validador)
def test_BE01_el_validador_y_el_backend_comparten_la_lista_de_subunidades():
    import sanity_check as sc                      # solo-stdlib: copia a propósito, este test evita que diverjan
    assert sc.SUBUNIT_CURRENCIES == frozenset(fm.PRICE_UNIT_DIVISORS)
    assert all(fm.price_unit_divisor(c) == 100 for c in sc.SUBUNIT_CURRENCIES) and fm.price_unit_divisor("GBP") == 1
