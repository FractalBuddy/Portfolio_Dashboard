"""Sombra NAV Yahoo (F1): integración con el Supabase SIMULADO del repo (tests/test_personal_v2.FakeSupabase, servidor HTTP local) y con yfinance
simulado. Reproduce el orden del workflow: watch (lee la lista con el token) -> build (NAV oficial de la ficha, simulada) -> publish -> sombra.
Comprueba que la sombra no hace NI UNA petición a Supabase y que el cliente real pide el histórico SIN AJUSTAR y con el periodo de la configuración."""
import json
from datetime import date, datetime, timezone

import pandas as pd

import personal_v2 as pv
import yahoo_shadow as ys
from fund_probe_fakes import hist_df
from test_personal_v2 import FakeSupabase
from test_yahoo_shadow_run import ROOT, line
from yahoo_shadow_fakes import (ISIN_A, ISIN_B, NAME_A, NAME_B, NAV_DAY, NOW, PRICE_A, PRICE_B, SYM_A, SYM_B, URL_A, URL_B, FakeTime, FakeYahoo, bars,
                                canary_ok, hist, quote)


# Fechas PASADAS propias: publish valida el payload con la fecha real del reloj (un generated_at «futuro» no se envía); así la prueba no depende de hoy.
RUN = datetime(2026, 10, 27, 6, 40, tzinfo=timezone.utc)          # martes
DAY = date(2026, 10, 23)                                           # viernes: fecha del NAV oficial


class Ficha:
    """La ficha de iShares simulada: NAV oficial por ISIN, con 2 decimales y la fecha del viernes."""
    def nav(self, url, isin):
        price = {ISIN_A: PRICE_A, ISIN_B: PRICE_B}[isin]
        return {"price": price, "nav_date": "23/Oct/2026", "nav_date_iso": DAY.isoformat(), "nav_stale": False, "change_pct": 0.1, "nav_decimals": 2}

    def facts(self, url):
        return {}

    def series(self, sym, period="2y"):
        raise AssertionError("sin símbolos en esta prueba")


def test_watch_build_publish_y_sombra_sin_tocar_supabase(tmp_path, capsys, monkeypatch):
    import time
    monkeypatch.setattr(time, "sleep", lambda s: None)
    sb = FakeSupabase(watch=[{"user_id": "usuario-1", "symbols": [],
                              "funds": [{"isin": ISIN_A, "name": NAME_A, "url": URL_A}, {"isin": ISIN_B, "name": NAME_B, "url": URL_B}]}])
    try:
        work, reg = tmp_path / "personal", tmp_path / "registry.json"
        reg.write_text(json.dumps({"schema": 1, "symbols": [{"symbol": "AAPL", "kind": "yahoo", "name": "Apple", "currency": "USD", "unit": "currency",
                                                             "tier": "hot", "status": "active", "fail_streak": 0}]}), encoding="utf-8")
        assert pv.main(["watch", "--dir", str(work), "--github-output"], env=sb.env(GITHUB_OUTPUT=str(tmp_path / "o")), now=RUN) == 0
        assert pv.main(["build", "--dir", str(work), "--registry", str(reg)], fetchers=pv.Fetchers(Ficha().nav, Ficha().facts, Ficha().series), now=RUN) == 0
        assert pv.main(["publish", "--dir", str(work)], env=sb.env()) == 0
        before = len(sb.reqs)
        s = {ISIN_A: [quote(SYM_A, NAME_A)], ISIN_B: [quote(SYM_B, NAME_B)]}
        h = {SYM_A: hist(bars(DAY, 4, PRICE_A)), SYM_B: hist(bars(DAY, 4, PRICE_B + 0.01))}
        canary_ok(s, h, DAY)
        t = FakeTime()
        rc = ys.main(["--dir", str(work), "--config", str(ROOT / "config" / "yahoo_shadow.json"),
                      "--generic", str(ROOT / "config" / "fund_probe_isins_generic.json"), "--ledger", str(tmp_path / "c" / "ledger.json")],
                     env=sb.env(), client=FakeYahoo(s, h), now=RUN, clock=t.clock, sleep=t.sleep)     # peor caso: aunque el entorno tuviera el token
        out = capsys.readouterr().out
        assert rc == 0 and len(sb.reqs) == before                                     # la sombra no habla con Supabase
        ln = line(out)
        assert "2 fondos con NAV oficial (de 2 con ficha" in ln and "comparables 2 (iguales a ≤ 1 pb 1;" in ln and "dif. máx. 2.31 pb" in ln
        assert [r[0].rsplit("/", 1)[-1] for r in sb.reqs] == [pv.RPC_WATCH, pv.RPC_PUBLISH]     # una lectura (watch) y una publicación, como siempre
    finally:
        sb.close()


class FakeTicker:
    calls = []

    def __init__(self, symbol):
        self.symbol = symbol
        self.history_metadata = {"currency": "EUR"}

    def history(self, **kw):
        FakeTicker.calls.append((self.symbol, kw))
        return hist_df(last=NAV_DAY, n=4)


def test_el_cliente_real_pide_5d_sin_ajustar_y_lee_la_divisa(monkeypatch):
    import yfinance
    monkeypatch.setattr(yfinance, "Ticker", FakeTicker)
    FakeTicker.calls = []
    out = ys.ShadowYahooClient().history(SYM_A, period="5d")
    [(sym, kw)] = FakeTicker.calls
    assert sym == SYM_A and kw["period"] == "5d" and kw["auto_adjust"] is False and kw["interval"] == "1d"
    assert out["currency"] == "EUR" and len(out["rows"]) == 4 and out["rows"][-1][0] == NAV_DAY
    assert isinstance(hist_df().index, pd.DatetimeIndex) and NOW.tzinfo is timezone.utc
