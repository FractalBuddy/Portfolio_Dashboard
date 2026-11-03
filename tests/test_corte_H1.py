"""Bloque 1 (cierre del corte): el feed v1 (`data.json`) ya no lleva NINGÚN dato propio del dueño.

El cliente toma los fondos propios solo de personal_feed (Supabase). Aquí se comprueba, sin red, que:
  * `main()` genera un data.json sin `your_funds` y con `news.positions` vacío (la clave se conserva: el esquema no cambia);
  * el módulo ya no tiene la lista de fondos ni la de criptomonedas vigiladas;
  * `sanity_check` acepta ese feed y RECHAZA uno que vuelva a traer `your_funds` (el job `fetch` ejecuta código de terceros).
"""
import copy
import json

import pytest

import fetch_market_data as fm
from test_sanity_check import _fixed_today, healthy, run  # noqa: F401  (el fixture autouse fija «hoy» en los feeds sintéticos)

PROPIOS = ("your_funds", "YOUR_FUNDS", "CRYPTO_WATCHLIST")


def _main_sin_red(monkeypatch, tmp_path):
    """main() con TODO lo de red sustituido por datos mínimos. No se parchea nada de lo que este bloque elimina."""
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("PREV_FEED_PATH", str(tmp_path / "no_existe.json"))
    monkeypatch.setattr(fm, "FRED_API_KEY", "")
    monkeypatch.setattr(fm, "build_list", lambda items, prev_rows=None: [{"symbol": i[0], "name": i[1], "price": 1.0, "change_pct": 0.0, "history": []} for i in items])
    monkeypatch.setattr(fm, "attach_histories", lambda x, prev_rows=None: x)
    monkeypatch.setattr(fm, "attach_profiles", lambda stocks, prev=None: stocks)
    monkeypatch.setattr(fm, "attach_fund_extras", lambda funds, prev=None, **k: funds)
    monkeypatch.setattr(fm, "build_general_news", lambda: [])
    monkeypatch.setattr(fm, "build_benchmarks", lambda: {"sp500": [], "msci_world": []})
    monkeypatch.setattr(fm, "build_market_mood", lambda: {})
    monkeypatch.setattr(fm, "fetch_lido_apr", lambda: None)
    monkeypatch.setitem(fm.STAKING_PROVIDERS, "eth_lido", fm.fetch_lido_apr)
    # lo que usaba la parte propia: si algo del v1 aún lo llamara, que no salga a internet y devuelva «sin dato»
    monkeypatch.setattr(fm, "fetch_ishares_nav", lambda url, isin: None)
    monkeypatch.setattr(fm, "fetch_ishares_fund_facts", lambda url: {})
    monkeypatch.setattr(fm, "compute_fund_analytics", lambda s: {})
    monkeypatch.setattr(fm, "fetch_full_history", lambda s, period="max": [])
    monkeypatch.setattr(fm, "fetch_symbol", lambda s: None)
    monkeypatch.setattr(fm, "parse_feed", lambda url, limit=8: [{"title": "bitcoin sube", "link": "https://x.test/1", "source": "s", "published": ""}])
    monkeypatch.setattr(fm, "_nap", lambda *a, **k: None)


def test_data_json_de_v1_no_lleva_ninguna_clave_de_datos_propios(monkeypatch, tmp_path):
    _main_sin_red(monkeypatch, tmp_path)
    fm.main()
    texto = (tmp_path / "data.json").read_text()
    d = json.loads(texto)
    assert "your_funds" not in d
    assert d["news"]["positions"] == []              # la clave se conserva (esquema de sanity_check); sin titulares de la cartera del dueño
    assert set(d["news"]) == {"general", "positions"}
    assert '"isin"' not in texto and "proxy_symbol" not in texto and "nav_date" not in texto


@pytest.mark.parametrize("nombre", PROPIOS[1:])
def test_el_modulo_v1_ya_no_define_listas_del_dueno(nombre):
    assert not hasattr(fm, nombre)


@pytest.mark.parametrize("nombre", ["build_your_funds", "build_position_news"])
def test_el_modulo_v1_ya_no_tiene_las_funciones_propias(nombre):
    assert not hasattr(fm, nombre)


def test_sanity_acepta_el_feed_v1_sin_fondos_propios():
    d, m = healthy()
    errs, _ = run(d, m)
    assert "your_funds" not in d and errs == []


FONDO_SINTETICO = {"isin": "IE0000000001", "price": 12.3456, "change_pct": 0.4, "source": "ishares_nav_scrape", "nav_date": "03/Oct/2026"}


@pytest.mark.parametrize("valor", [[FONDO_SINTETICO], []])
def test_sanity_rechaza_un_feed_que_vuelve_a_traer_your_funds(valor):
    """Control negativo: el job `fetch` ejecuta código de terceros; si el feed trae de nuevo datos propios (aunque sea una lista vacía),
    no se publica."""
    d, m = healthy()
    d["your_funds"] = copy.deepcopy(valor)
    errs, _ = run(d, m)
    assert any("your_funds" in e for e in errs)
