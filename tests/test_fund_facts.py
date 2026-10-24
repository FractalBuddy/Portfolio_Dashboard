"""Fichas de fondos: scraper de «Key Facts» contra extractos REALES de iShares (22-10-2026), proxies EUR y extras de Explorar."""
import json
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import fetch_market_data as fm  # noqa: E402

FIX = json.load(open(os.path.join(os.path.dirname(__file__), "fixtures_fund_facts.json"), encoding="utf-8"))


def test_clase_s_desarrollados_real():
    f = fm.scan_fund_facts(FIX["dev"])
    assert f["class_aum"].startswith("EUR 1.") and f["class_aum"].endswith(" bn")        # «EUR 1’296’938’547» (apóstrofo tipográfico)
    assert f["fund_aum"].startswith("USD 29.") and f["fund_aum"].endswith(" bn")
    assert f["class_launch_date"] == "21/Aug/2025" and f["fund_launch_date"] == "15/Apr/2010"
    assert f["ongoing_charges_pct"] == 0.06 and f["management_fee_pct"] == 0.04
    assert "performance_fee_pct" not in f and "initial_charge_pct" not in f                # «-» en la ficha = sin dato, no se inventa 0
    assert f["share_class_currency"] == "EUR" and f["base_currency"] == "USD"
    assert f["benchmark"] == "MSCI World Index Net (EUR)"


def test_clase_s_emergentes_real_cero_sin_porcentaje():
    f = fm.scan_fund_facts(FIX["em"])
    assert f["initial_charge_pct"] == 0.0 and f["performance_fee_pct"] == 0.0               # «Initial Charge 0.00» (sin %) y «0.00%»
    assert f["class_aum"].endswith(" m") and f["fund_aum"].endswith(" bn")
    assert f["ongoing_charges_pct"] == 0.16 and f["management_fee_pct"] == 0.08


def test_etf_y_etc_reales_usan_otras_etiquetas():
    f = fm.scan_fund_facts(FIX["iwda"])
    assert f["ongoing_charges_pct"] == 0.20 and f["benchmark"] == "MSCI World Index (Net)"      # «Total Expense Ratio»
    assert f["structure"] == "Physical" and f["methodology"] == "Optimised" and f["use_of_income"] == "Accumulating"
    assert f["fund_aum"].startswith("USD 153.") and f["holdings"] > 1000
    g = fm.scan_fund_facts(FIX["sgln"])
    assert g["ongoing_charges_pct"] == 0.12 and g["benchmark"] == "LBMA Gold Price" and g["use_of_income"] == "No Income"
    assert "holdings" not in g


@pytest.mark.parametrize("txt,exp", [
    ("Net Assets of Fund as of 03/Oct/2026 EUR 1,293,192,323 Fund Launch Date 01/Jan/2010", "EUR 1.29 bn"),
    ("Net Assets of Fund as of 03/Oct/2026 EUR 1’296’938’547 Fund Launch Date 01/Jan/2010", "EUR 1.30 bn"),
    ("Net Assets of Fund as of 03/Oct/2026 USD 788'514'977 Fund Launch Date 01/Jan/2010", "USD 788.51 m"),
    ("Net Assets of Fund as of 03/Oct/2026 USD 29.5 bn Fund Launch Date 01/Jan/2010", "USD 29.5 bn"),
    ("Net Assets of Fund as of 03/Oct/2026 EUR 820.50 M Fund Launch Date 01/Jan/2010", "EUR 820.50 m"),
])
def test_aum_formatos(txt, exp):
    assert fm.scan_fund_facts(txt)["fund_aum"] == exp


def test_porcentajes_absurdos_o_malformados_no_se_publican():
    f = fm.scan_fund_facts("Total Expense Ratio 45% Management Fee 0.0.6 % Initial Charge 0.50 % Performance Fee 20.0 %")
    assert "ongoing_charges_pct" not in f and "management_fee_pct" not in f and f["initial_charge_pct"] == 0.5 and f["performance_fee_pct"] == 20.0


def test_proxies_de_tus_fondos_son_lineas_en_eur():
    assert [x["proxy_symbol"] for x in fm.YOUR_FUNDS] == ["EUNL.DE", "IS3N.DE"]               # no SWDA.L (GBp) ni EIMI.L (USD)


def test_extras_de_explorar_con_fallback_al_feed_anterior():
    funds = [{"symbol": "SWDA.L"}, {"symbol": "EIMI.L"}, {"symbol": "ZZZ"}]
    prev = [{"symbol": "EIMI.L", "facts": {"a": 1, "b": 2, "c": 3}, "analytics": {"period_returns": {"ytd": 1.0}}}]
    out = fm.attach_fund_extras(
        funds, prev,
        fetch_facts=lambda url: {"ongoing_charges_pct": 0.2, "x": 1, "y": 2} if "251882" in url else {},
        fetch_analytics=lambda sym: {"period_returns": {"ytd": 9.9}} if sym == "SWDA.L" else {})
    assert out[0]["facts"]["ongoing_charges_pct"] == 0.2 and out[0]["analytics"]["period_returns"]["ytd"] == 9.9
    assert out[1]["facts"] == {"a": 1, "b": 2, "c": 3} and out[1]["analytics"]["period_returns"]["ytd"] == 1.0   # scrape/cálculo caídos: lo anterior
    assert "facts" not in out[2] and "analytics" not in out[2]                                  # sin URL ni histórico: nada inventado


def test_extras_de_explorar_nunca_revienta():
    def boom(*a):
        raise RuntimeError("x")
    out = fm.attach_fund_extras([{"symbol": "SWDA.L"}], None, fetch_facts=boom, fetch_analytics=boom)
    assert out == [{"symbol": "SWDA.L"}]


def test_your_funds_conserva_la_ficha_anterior_si_el_scrape_falla(monkeypatch):
    monkeypatch.setattr(fm, "fetch_ishares_nav", lambda u, i: None)
    monkeypatch.setattr(fm, "fetch_ishares_fund_facts", lambda u: {})
    monkeypatch.setattr(fm, "compute_fund_analytics", lambda s: {})
    monkeypatch.setattr(fm, "fetch_full_history", lambda s: [])
    monkeypatch.setattr(fm, "fetch_symbol", lambda s: {"change_pct": 0.5})
    old_facts = {"ongoing_charges_pct": 0.06, "management_fee_pct": 0.04, "fund_aum": "USD 29.74 bn"}
    prev = [{"isin": fm.YOUR_FUNDS[0]["isin"], "facts": old_facts, "analytics": {"volatility": {"12m": 10.0}}}]
    out = fm.build_your_funds(prev)
    assert out[0]["facts"] == old_facts and out[0]["analytics"]["volatility"]["12m"] == 10.0
    assert out[1]["facts"] == {} and out[1]["analytics"] == {}


def test_analitica_pide_historico_maximo_y_recorta_los_anuales_a_10_anos(monkeypatch):
    import pandas as pd
    idx = pd.bdate_range("2009-10-01", "2026-10-21")
    df = pd.DataFrame({"Close": [100 + i * 0.01 for i in range(len(idx))]}, index=idx)
    seen = {}
    monkeypatch.setattr(fm, "yf_history", lambda sym, **kw: seen.update(kw) or df)
    a = fm.compute_fund_analytics("EUNL.DE")
    assert seen["period"] == "max"
    assert "10y" in a["period_returns"]                                           # antes con period="10y" faltaba (histórico < 10 años justos)
    ys = sorted(a["annual_returns"])
    assert ys[0] == "2017" and ys[-1] == "2026" and len(ys) == 10
