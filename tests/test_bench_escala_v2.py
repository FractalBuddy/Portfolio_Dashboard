"""Que el banco de escala (tests/bench_escala_v2.py) siga midiendo algo válido: su registro sintético cumple el contrato del registro hasta el tope, su
Yahoo simulado produce un sitio que el validador acepta (0 errores) y su medición de topes detecta un tope roto. Las cifras de escala NO se fijan aquí
(son medición, van en auditorias/FIXES_FEED_REINTENTO_ESCALA.md)."""
import os

import pytest

import bench_escala_v2 as b
import feed_v2_common as c
import sanity_check_v2 as sc


def test_registro_sintetico_valido_y_con_el_reparto_del_actual():
    reg = b.make_registry(c.MAX_REGISTRY_SYMBOLS)
    assert c.registry_problems({"schema": 1, "symbols": reg}) == []
    tiers = [e["tier"] for e in reg]
    assert tiers.count("hot") == 84 and tiers.count("warm") == 280 and tiers.count("cold") == 36
    assert all(e["symbol"].startswith(("SYN", "^SYN", "SYC")) for e in reg)              # nombres inventados, nunca del dueño


def test_control_negativo_un_simbolo_mas_rompe_max_registry_symbols():
    probs = c.registry_problems({"schema": 1, "symbols": b.make_registry(c.MAX_REGISTRY_SYMBOLS + 1)})
    assert any("demasiados símbolos" in p for p in probs)


@pytest.fixture()
def fakes(monkeypatch):
    import feed_v2 as f2
    import fetch_market_data as fm
    for mod, names in ((fm, ("yf_history", "_DEADLINE", "_HARD_DEADLINE", "_nap", "_cg_markets", "parse_feed", "build_rates", "fetch_cpi_history",
                             "build_market_mood", "FRED_API_KEY")), (f2, ("BACKFILL_MAX_PER_RUN",))):
        for n in names:
            monkeypatch.setattr(mod, n, getattr(mod, n))                                  # se restauran al acabar la prueba
    return lambda reg, end: b.install_fakes(reg, end)


def test_el_sitio_sintetico_pasa_el_validador_y_la_medicion_cuadra(tmp_path, fakes):
    import feed_v2 as f2
    reg = b.make_registry(15)
    fakes(reg, b.END1)
    out = tmp_path / "run1" / "v2"
    f2.generate(reg, str(out), None, now=b.NOW1)
    errors, _w = sc.check_site(str(out), reg, None, today=b.NOW1.date())
    assert errors == []
    m = b.measure_site(str(out), reg, b.NOW1.year)
    assert m["caps_broken"] == [] and m["files"] == sum(len(fs) for _r, _d, fs in os.walk(out))
    assert m["by_kind"]["daily"]["files"] == c.HISTORY_YEARS * sum(e["kind"] == "yahoo" for e in reg)    # 10 años por símbolo de Yahoo


def test_control_negativo_la_medicion_detecta_un_tope_roto(tmp_path, fakes, monkeypatch):
    import feed_v2 as f2
    reg = b.make_registry(15)
    fakes(reg, b.END1)
    out = tmp_path / "run1" / "v2"
    f2.generate(reg, str(out), None, now=b.NOW1)
    monkeypatch.setattr(c, "MAX_SITE_FILES", 10)
    monkeypatch.setitem(c.MAX_BYTES, "daily", 1000)
    caps = b.measure_site(str(out), reg, b.NOW1.year)["caps_broken"]
    assert any(x.startswith("MAX_SITE_FILES") for x in caps) and any(x.startswith("MAX_BYTES[daily]") for x in caps)
