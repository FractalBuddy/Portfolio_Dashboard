"""Sombra NAV Yahoo (F1), punto 6: canario con un fondo PÚBLICO (un ISIN de la lista genérica, nunca uno de la cartera del dueño), leído el
PRIMERO en cada run. Canario bien + fondo mal = problema de ese fondo; canario mal = problema de Yahoo => no se pide nada más (DISENO §2.7 y §3.2).
Si el canario no resuelve a un `0P…` (búsqueda correcta, pero la configuración apunta a un fondo que Yahoo no da como 0P) se sigue y se avisa."""
import json
import pathlib

import pytest

from test_yahoo_shadow_run import FUNDS3, line, run
from yahoo_shadow_fakes import (CANARY_ISIN, CANARY_NAME, ISIN_A, NAME_A, NAV_DAY, PRICE_A, SYM_A, SYM_CANARIO, bars, hist, quote)

ROOT = pathlib.Path(__file__).resolve().parent.parent


def _fund_a():
    return {ISIN_A: [quote(SYM_A, NAME_A)]}, {SYM_A: hist(bars(NAV_DAY, 3, PRICE_A))}


def test_el_canario_es_el_de_la_configuracion_y_va_el_primero(tmp_path, capsys):
    assert json.loads((ROOT / "config" / "yahoo_shadow.json").read_text(encoding="utf-8"))["canary_isin"] == CANARY_ISIN
    s, h = _fund_a()
    rc, out, _e, _s, y, _t = run(tmp_path, capsys, FUNDS3[:1], s, h)
    assert rc == 0 and y.calls[0] == ("search", CANARY_ISIN) and y.calls[1][:2] == ("history", SYM_CANARIO)
    assert "canario ok (última barra a 2 d hábiles)" in line(out) and "comparables 1" in line(out)     # el canario no cuenta como fondo


@pytest.mark.parametrize("fallo,estado", [("429", "429"), ("red", "sin_red"), ("vacio", "vacio"), ("basura", "dato_invalido")])
def test_canario_mal_no_se_pide_nada_mas(tmp_path, capsys, fallo, estado):
    from yfinance import exceptions as yfe
    s, h = _fund_a()
    s[CANARY_ISIN] = [quote(SYM_CANARIO, CANARY_NAME)]
    h[SYM_CANARIO] = {"429": yfe.YFRateLimitError(), "red": ConnectionError("x"), "vacio": hist([]), "basura": hist([(NAV_DAY, -1.0)])}[fallo]
    rc, out, _e, _s, y, _t = run(tmp_path, capsys, FUNDS3, s, h, canary=False)
    ln = line(out)
    assert rc == 0 and ("canario %s" % estado) in ln and "sin probar 3" in ln
    assert ("parada 429" if estado == "429" else "parada canario") in ln                # el 429 conserva su causa real
    assert [c[1] for c in y.calls] == [CANARY_ISIN, SYM_CANARIO]                       # ni una petición de los fondos del dueño
    assert "::warning::sombra Yahoo: el canario (fondo público) falla" in out and "::error::" not in out


def test_canario_con_429_en_la_busqueda(tmp_path, capsys):
    from yfinance import exceptions as yfe
    s, h = _fund_a()
    s[CANARY_ISIN] = yfe.YFRateLimitError()
    rc, out, _e, _s, y, _t = run(tmp_path, capsys, FUNDS3, s, h, canary=False)
    assert rc == 0 and "canario 429" in line(out) and [c[1] for c in y.calls] == [CANARY_ISIN]


@pytest.mark.parametrize("quotes,estado", [([], "sin_0p"), ([quote("VGWS.DE", CANARY_NAME, "ETF")], "sin_0p"),
                                           ([quote(SYM_CANARIO, "Otro Fondo Cualquiera Small Cap")], "sin_emparejar")])
def test_canario_sin_0p_se_sigue_y_se_avisa(tmp_path, capsys, quotes, estado):
    s, h = _fund_a()
    s[CANARY_ISIN] = quotes
    rc, out, _e, _s, y, _t = run(tmp_path, capsys, FUNDS3[:1], s, h, canary=False)
    assert rc == 0 and ("canario %s" % estado) in line(out) and "comparables 1" in line(out)
    assert "::warning::sombra Yahoo: el canario no resuelve a un 0P con su nombre (revisa canary_isin" in out
