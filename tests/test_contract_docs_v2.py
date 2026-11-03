"""Los ejemplos JSON de docs/CONTRATO_FEED_V2.md los GENERA este test (sitio sintético de los tests, recortado a unas pocas filas) y comprueba que el
documento los lleva idénticos. Para regenerar tras un cambio de contrato:  UPDATE_CONTRACT_DOCS=1 pytest tests/test_contract_docs_v2.py"""
import json
import os
import pathlib
import re
from datetime import date, timedelta

import pytest

import personal_v2 as pv
import sanity_check_v2 as sc
from v2_world import NOW, REG, gen

ROOT = pathlib.Path(__file__).resolve().parent.parent
DOC = ROOT / "docs" / "CONTRATO_FEED_V2.md"
BLOCK = re.compile(r"(<!-- ejemplo:(\w+) -->\n)(.*?)(\n<!-- /ejemplo -->)", re.S)


def jl(base, rel):
    return json.loads((base / "v2" / pathlib.Path(*rel.split("/"))).read_text(encoding="utf-8"))


def cut(doc, n=3, keys=()):
    """Recorta las listas largas (series) a n elementos para que el ejemplo sea legible; sigue siendo JSON válido del mismo esquema."""
    out = dict(doc)
    for k in keys:
        v = out[k]
        out[k] = dict(list(v.items())[:n]) if isinstance(v, dict) else v[:n]
    return out


@pytest.fixture()
def examples(world, tmp_path, monkeypatch):
    import types
    import feed_v2 as f2
    import fetch_market_data as fm
    monkeypatch.setattr(f2, "time", types.SimpleNamespace(monotonic=lambda: 0.0))       # duración del run = 0: ejemplos y tamaños deterministas
    prev, _ = gen(tmp_path, "prev")
    world.end = date(2026, 10, 27)
    monkeypatch.setattr(fm, "parse_feed", lambda url, limit=8: [{"title": "Titular de ejemplo", "link": "https://example.com/noticia", "source": "Fuente",
                                                                 "published": "Mon, 26 Oct 2026 10:00:00 GMT"}][:limit])
    monkeypatch.setattr(fm, "build_market_mood", lambda prev_feed=None, include_altcoin=True: {
        "vix": {"value": 16.4, "change_pct": -1.2}, "vix_50ma": {"value": 17.9}, "stock_fear_greed": {"value": 61.0, "rating": "greed", "as_of": "2026-10-26"},
        "crypto_fear_greed": {"value": 54, "classification": "Neutral", "timestamp": 1792972800}})
    monkeypatch.setattr(fm, "build_rates", lambda: [{"series": "DGS10", "name": "US 10-Year Treasury", "value": 4.1, "prev": 4.08, "date": "2026-10-23"}])
    monkeypatch.setattr(fm, "fetch_cpi_history", lambda: ([{"date": "2026-08-01", "value": 121.4}, {"date": "2026-09-01", "value": 121.9}],
                                                           {"source": "Eurostat HICP (IPCA) Spain, via FRED", "series": "CP0000ESM086NEST",
                                                            "last_date": "2026-09-01", "stale": False}))
    monkeypatch.setattr(fm, "FRED_API_KEY", "x")
    monkeypatch.setattr("feed_v2.build_calendar", lambda today: [{"date": "2026-11-12", "event": "US CPI"}, {"date": "2026-12-16", "event": "FOMC decision"}])
    monkeypatch.setattr(fm, "reuse_previous_fred", lambda rates, cpi, meta, prev: (rates, cpi, meta))
    site, _ = gen(tmp_path, "new", prev=prev, now=NOW + timedelta(hours=6))
    # datos propios: ISIN y símbolos FALSOS
    spy_bars = [{"d": (date(2026, 10, 23) - timedelta(days=i)).isoformat(), "o": 1, "h": 1, "l": 1, "c": round(100 + i * 0.5, 2), "v": 0} for i in range(3)][::-1]
    fetchers = pv.Fetchers(lambda url, isin: {"price": 25.5, "nav_date": "23/Oct/2026", "nav_date_iso": "2026-10-23", "nav_stale": False, "change_pct": 0.4,
                                             "wk52_low": 20.0, "wk52_high": 30.0},
                           lambda url: {"ongoing_charges_pct": 0.1, "domicile": "Ireland"}, lambda sym, period="2y": (spy_bars, "USD"))
    user = {"user": "usuario-1", "funds": [{"isin": "IE0FAKE00001", "name": "Fondo Falso A", "url": "https://www.ishares.com/ch/individual/en/products/000001/fake-fund-a",
                                            "proxy_symbol": "FAKEP"}], "symbols": ["AAPL", "FAKE1"]}
    payload = pv.build_payload(user, {"AAPL"}, fetchers, NOW, lambda code: None)
    watch = [{"user_id": "usuario-1", "funds": [{"isin": "IE0FAKE00001", "name": "Fondo Falso A",
                                                 "url": "https://www.ishares.com/ch/individual/en/products/000001/fake-fund-a",
                                                 "proxy_symbol": "FAKEP", "prev_price": 25.4}], "symbols": ["AAPL", "FAKE1"]}]
    mf = jl(site, "manifest.json")
    health = jl(site, "health.json")
    latest = jl(site, "s/SWDA.L/latest.json")
    latest["sparkline"] = latest["sparkline"][-5:]          # (el real lleva 30; el último es siempre `price`)
    return {
        "index": cut(jl(site, "index.json"), 3, ("symbols",)),
        "latest": latest,
        "daily": cut(jl(site, "s/SWDA.L/d/2026.json"), 3, ("d", "o", "h", "l", "c", "v")),
        "intraday": cut(jl(site, "s/AAPL/i.json"), 3, ("t", "c")),
        "crypto": cut(jl(site, "crypto/latest.json"), 2, ("coins",)),
        "macro": jl(site, "macro.json"),
        "news": jl(site, "news.json") | {"general": jl(site, "news.json")["general"][:1], "watchlist": jl(site, "news.json")["watchlist"][:1]},
        "health": cut(health, 2, ("symbols",)),
        "manifest": mf | {"files": {k: mf["files"][k] for k in ("index.json", "s/SWDA.L/latest.json", "s/SWDA.L/d/2026.json")}},   # (health.json cambia en cada run)
        "watch": watch,
        "payload": payload,
    }


def render(obj):
    return "```json\n" + json.dumps(obj, indent=2, ensure_ascii=False) + "\n```"


def test_los_ejemplos_del_documento_son_los_que_genera_el_codigo(examples):
    text = DOC.read_text(encoding="utf-8")
    names = [m.group(2) for m in BLOCK.finditer(text)]
    assert sorted(names) == sorted(examples), "el documento debe llevar un bloque <!-- ejemplo:NOMBRE --> por cada ejemplo"
    if os.environ.get("UPDATE_CONTRACT_DOCS"):
        new = BLOCK.sub(lambda m: m.group(1) + render(examples[m.group(2)]) + m.group(4), text)
        DOC.write_text(new, encoding="utf-8")
        text = new
    for m in BLOCK.finditer(text):
        assert m.group(3) == render(examples[m.group(2)]), "ejemplo '%s' desactualizado: UPDATE_CONTRACT_DOCS=1 pytest tests/test_contract_docs_v2.py" % m.group(2)


def test_los_ejemplos_pasan_los_validadores_de_esquema(examples):
    """Lo que enseña la documentación es lo que el validador acepta (los recortados se validan como están: el esquema no exige longitud mínima)."""
    ctx = sc.Ctx(REG, date(2026, 10, 27))
    assert sc.INDEX_SPEC(examples["index"], ctx) is None
    assert sc.LATEST_SPEC(examples["latest"], ctx) is None
    for k, spec in (("crypto", sc.CRYPTO_SPEC), ("macro", sc.MACRO_SPEC), ("news", sc.NEWS_SPEC), ("health", sc.HEALTH_SPEC), ("manifest", sc.MANIFEST_SPEC)):
        assert spec(examples[k], ctx) is None, k
    assert pv.payload_problems(examples["payload"], date(2026, 10, 27)) == []
    assert examples["watch"] and pv.parse_watch(examples["watch"], lambda t, n: t)[1] == 0
