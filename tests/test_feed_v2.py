"""Feed v2, paso 2: generador (histórico incremental, niveles, símbolos muertos, divisa/unidad, cripto, macro, noticias, salud).
Todo con datos sintéticos y Yahoo/CoinGecko/FRED simulados: ningún test sale a Internet."""
import json
import os
from datetime import date, datetime, timedelta, timezone

import pandas as pd
import pytest

import feed_v2 as f2
import feed_v2_common as c
import fetch_market_data as fm

from v2_world import END, NOW, REG, exists, gen, jl, mk, sha


# ------------------------------------------------------------------ primer run
def test_primer_run_publica_el_contrato_v2(world, tmp_path):
    out, run = gen(tmp_path, "s1")
    for rel in ("index.json", "manifest.json", "macro.json", "news.json", "health.json", "s/AAPL/latest.json", "s/AAPL/d/2026.json",
                "s/AAPL/d/2017.json", "s/AAPL/i.json", "s/^GSPC/latest.json", "s/EURUSD=X/latest.json"):
        assert exists(out, rel), rel
    assert not exists(out, "s/EIMI.L/i.json")                                  # intradía solo hot
    assert not exists(out, "s/AAPL/d/2016.json")                               # solo HISTORY_YEARS años (2017..2026)
    lat = jl(out, "s/AAPL/latest.json")
    assert set(lat) == {"schema", "s", "kind", "name", "currency", "unit", "price_unit_divisor", "price", "change_pct", "change_pct_7d",
                        "volume_ratio", "sparkline", "as_of", "generated_at"}
    assert lat["schema"] == 2 and lat["as_of"] == "2026-10-23" and lat["generated_at"] == "2026-10-26T12:00:00Z"
    assert len(lat["sparkline"]) == 30 and lat["sparkline"][-1] == lat["price"]
    sw = jl(out, "s/SWDA.L/latest.json")                                       # BE-01: GBp explícito, nunca GBP
    assert (sw["currency"], sw["unit"], sw["price_unit_divisor"]) == ("GBp", "subunit", 100)
    assert (jl(out, "s/^GSPC/latest.json")["unit"], jl(out, "s/EURUSD=X/latest.json")["unit"]) == ("points", "rate")
    y = jl(out, "s/AAPL/d/2026.json")
    assert set(y) == {"schema", "s", "y", "currency", "unit", "d", "o", "h", "l", "c", "v"} and y["y"] == 2026 and y["s"] == "AAPL"
    assert y["d"] == sorted(set(y["d"])) and all(l <= cl <= h for l, cl, h in zip(y["l"], y["c"], y["h"]))
    ix = jl(out, "index.json")
    assert [r["s"] for r in ix["symbols"]] == [e["symbol"] for e in REG]
    assert set(ix["symbols"][0]) == {"s", "kind", "name", "currency", "unit", "tier", "as_of", "status"}
    mf = jl(out, "manifest.json")
    assert mf["total_bytes"] == run.total_bytes == sum(v["bytes"] for v in mf["files"].values())
    assert all(sha(out, rel) == v["sha256"] for rel, v in mf["files"].items()) and "manifest.json" not in mf["files"]


def test_cupo_de_descargas_largas_reparte_el_alta_y_no_penaliza(world, tmp_path, monkeypatch):
    monkeypatch.setattr(f2, "BACKFILL_MAX_PER_RUN", 3)
    out, run = gen(tmp_path, "s1")
    h = jl(out, "health.json")
    yahoo = [e["symbol"] for e in REG if e["kind"] == "yahoo"]
    done = [s for s in yahoo if exists(out, "s/%s/latest.json" % s)]
    assert done == ["^GSPC", "AAPL", "SWDA.L"]                                 # hot primero, en el orden del registro
    assert h["counts"]["backfilled"] == 3 and h["counts"]["deferred"] == 4 and h["counts"]["failed"] == 0
    assert all(h["symbols"][s]["fail_streak"] == 0 for s in yahoo)             # aplazar no cuenta como fallo
    nxt, _ = gen(tmp_path, "s2", prev=out, now=NOW + timedelta(hours=6))
    assert sum(exists(nxt, "s/%s/latest.json" % s) for s in yahoo) == 6      # los hot ya hechos se refrescan; entran 3 más (EURUSD=X, GC=F, EIMI.L)


# ------------------------------------------------------------------ incremental
def test_segundo_run_pide_solo_un_mes_y_no_reescribe_los_anos_cerrados(world, tmp_path):
    out1, _ = gen(tmp_path, "s1")
    n1 = len(world.calls)
    world.calls.clear()
    world.end = date(2026, 10, 27)                                             # avanza el mercado: hay una barra nueva
    out2, run2 = gen(tmp_path, "s2", prev=out1, now=NOW + timedelta(hours=6))
    daily = [(s, p) for s, p, i in world.calls if i == "1d"]
    assert daily and all(p == "1mo" for _, p in daily)                         # histórico incremental: ~1 mes, no 10 años
    assert {s for s, _ in daily} == {"^GSPC", "AAPL", "SWDA.L", "EURUSD=X", "GC=F"}      # warm (EIMI.L, USDJPY=X) no toca a las 6 h
    assert n1 > len(world.calls)
    assert jl(out2, "s/AAPL/latest.json")["as_of"] == "2026-10-27"
    assert len(jl(out2, "s/AAPL/d/2026.json")["d"]) == len(jl(out1, "s/AAPL/d/2026.json")["d"]) + 2   # lunes y martes
    for y in range(2017, 2026):                                                # años cerrados: mismos bytes
        assert sha(out1, "s/AAPL/d/%d.json" % y) == sha(out2, "s/AAPL/d/%d.json" % y)
    assert sha(out1, "s/EIMI.L/d/2026.json") == sha(out2, "s/EIMI.L/d/2026.json")      # no tocaba: copia byte a byte
    assert run2.yahoo_calls == len(world.calls)


def test_warm_se_refresca_pasadas_20_horas(world, tmp_path):
    out1, _ = gen(tmp_path, "s1")
    world.calls.clear()
    gen(tmp_path, "s2", prev=out1, now=NOW + timedelta(hours=21))
    assert {"EIMI.L", "USDJPY=X"} <= {s for s, _, _ in world.calls}


def test_fusion_por_fecha_la_barra_nueva_corrige_a_la_antigua(world, tmp_path):
    out1, _ = gen(tmp_path, "s1")
    world.scale["AAPL"] = 1.0
    old_last = jl(out1, "s/AAPL/d/2026.json")["c"][-1]
    # Yahoo revisa el cierre de la última barra un 0,5 % (dentro de la tolerancia de rebase) y no hay barra nueva
    orig = world.price
    world.price = lambda sym, d: orig(sym, d) * (1.005 if (sym == "AAPL" and d == END) else 1.0)
    out2, _ = gen(tmp_path, "s2", prev=out1, now=NOW + timedelta(hours=6))
    y = jl(out2, "s/AAPL/d/2026.json")
    assert y["d"] == sorted(set(y["d"]))
    assert y["c"][-1] == pytest.approx(old_last * 1.005, rel=1e-5)


def test_rebase_por_split_baja_la_serie_entera_otra_vez(world, tmp_path):
    out1, _ = gen(tmp_path, "s1")
    before = jl(out1, "s/AAPL/d/2020.json")["c"][0]
    world.scale["AAPL"] = 0.5                                                  # split 2:1: Yahoo reescribe todo el pasado
    world.calls.clear()
    out2, run = gen(tmp_path, "s2", prev=out1, now=NOW + timedelta(hours=6))
    assert ("AAPL", "1mo", "1d") in world.calls and ("AAPL", "10y", "1d") in world.calls
    assert jl(out2, "s/AAPL/d/2020.json")["c"][0] == pytest.approx(before * 0.5, rel=1e-5)
    assert jl(out2, "health.json")["problems"]["rebased"] == ["AAPL"]


def test_cambios_pequenos_no_disparan_el_rebase(world, tmp_path):
    out1, _ = gen(tmp_path, "s1")
    world.scale["AAPL"] = 1.01                                                 # 1 % < tolerancia del 2 %
    world.calls.clear()
    _, run = gen(tmp_path, "s2", prev=out1, now=NOW + timedelta(hours=6))
    assert ("AAPL", "10y", "1d") not in world.calls


def test_hueco_largo_usa_un_periodo_mayor():
    assert [f2.period_for_gap(g) for g in (0, 20, 21, 80, 81, 170, 171, 350, 351, 1700, 1701)] == \
        ["1mo", "1mo", "3mo", "3mo", "6mo", "6mo", "1y", "1y", "5y", "5y", "10y"]


# ------------------------------------------------------------------ símbolos muertos y averiados
def test_un_simbolo_que_falla_conserva_lo_anterior_marcado_stale_y_no_bloquea(world, tmp_path):
    out1, _ = gen(tmp_path, "s1")
    world.fail.add("AAPL")
    out2, run = gen(tmp_path, "s2", prev=out1, now=NOW + timedelta(hours=6))
    lat = jl(out2, "s/AAPL/latest.json")
    assert lat["stale"] is True and lat["stale_since"] == "2026-10-23" and lat["price"] == jl(out1, "s/AAPL/latest.json")["price"]
    assert not exists(out2, "s/AAPL/i.json")                                   # el intradía de ayer no se arrastra
    assert sha(out1, "s/AAPL/d/2024.json") == sha(out2, "s/AAPL/d/2024.json")
    h = jl(out2, "health.json")
    assert h["symbols"]["AAPL"]["fail_streak"] == 1 and h["symbols"]["AAPL"]["status"] == "active" and h["problems"]["failed"] == ["AAPL"]
    assert exists(out2, "s/^GSPC/latest.json") and not jl(out2, "s/^GSPC/latest.json").get("stale")      # el resto sigue


def test_cinco_fallos_seguidos_dejan_el_simbolo_invalid_y_se_reintenta_cada_semana(world, tmp_path):
    prev, t = gen(tmp_path, "s0")[0], NOW
    world.fail.add("AAPL")
    for i in range(1, 6):
        t += timedelta(hours=6)
        prev, _ = gen(tmp_path, "s%d" % i, prev=prev, now=t)
    st = jl(prev, "health.json")["symbols"]["AAPL"]
    assert (st["status"], st["fail_streak"]) == ("invalid", 5)
    assert jl(prev, "index.json")["symbols"][1]["status"] == "invalid"
    world.calls.clear()
    t += timedelta(hours=6)
    prev, _ = gen(tmp_path, "s6", prev=prev, now=t)
    assert "AAPL" not in {s for s, _, _ in world.calls}                        # invalid: no se pide a diario
    world.fail.clear()
    world.end = date(2026, 10, 30)
    t += timedelta(days=7)
    prev, _ = gen(tmp_path, "s7", prev=prev, now=t)
    st = jl(prev, "health.json")["symbols"]["AAPL"]
    assert (st["status"], st["fail_streak"]) == ("active", 0)                  # se recupera solo
    assert not jl(prev, "s/AAPL/latest.json").get("stale")


def test_el_stale_caduca_a_los_30_dias_y_el_historico_se_conserva(world, tmp_path):
    out1, _ = gen(tmp_path, "s1")
    world.fail.add("AAPL")
    out2, _ = gen(tmp_path, "s2", prev=out1, now=NOW + timedelta(days=35))
    assert not exists(out2, "s/AAPL/latest.json") and exists(out2, "s/AAPL/d/2026.json")
    assert jl(out2, "index.json")["symbols"][1]["as_of"] is None


def test_caida_global_de_yahoo_no_penaliza_a_nadie(world, tmp_path):
    reg = [mk("S%d" % i) for i in range(12)]
    world.decl.update({e["symbol"]: e for e in reg})
    out1, _ = gen(tmp_path, "s1", registry=reg)
    world.fail.update(e["symbol"] for e in reg)
    out2, run = gen(tmp_path, "s2", prev=out1, now=NOW + timedelta(hours=6), registry=reg)
    h = jl(out2, "health.json")
    assert h["counts"]["outage"] == 1 and all(v["fail_streak"] == 0 for v in h["symbols"].values())
    assert all(jl(out2, "s/S%d/latest.json" % i)["stale"] for i in range(12))


def test_429_sostenido_aplaza_el_resto_sin_penalizar(world, tmp_path):
    out1, _ = gen(tmp_path, "s1")
    world.stop_after = len(world.calls) + 2
    out2, run = gen(tmp_path, "s2", prev=out1, now=NOW + timedelta(hours=6))
    h = jl(out2, "health.json")
    assert h["counts"]["deferred"] >= 1 and h["counts"]["failed"] == 0
    assert all(v["fail_streak"] == 0 for v in h["symbols"].values())
    assert all(exists(out2, "s/%s/latest.json" % e["symbol"]) for e in REG if e["kind"] == "yahoo")


def test_simbolo_dormant_del_registro_no_se_pide_nunca(world, tmp_path):
    reg = [dict(e, status="dormant") if e["symbol"] == "GC=F" else e for e in REG]
    out1, _ = gen(tmp_path, "s1", registry=reg)
    assert not any(s == "GC=F" for s, _, _ in world.calls) and not exists(out1, "s/GC=F/latest.json")
    assert [r["status"] for r in jl(out1, "index.json")["symbols"] if r["s"] == "GC=F"] == ["dormant"]


def test_el_presupuesto_de_tiempo_corta_el_run(world, tmp_path, monkeypatch):
    monkeypatch.setattr(fm, "_DEADLINE", 0.0)                                  # ya vencido
    out, _ = gen(tmp_path, "s1")
    assert world.calls == [] and jl(out, "health.json")["run"]["budget_exhausted"] is True
    assert jl(out, "health.json")["counts"]["deferred"] == 7


# ------------------------------------------------------------------ divisa y unidad (BE-01)
@pytest.mark.parametrize("sym,observed", [("SWDA.L", "GBP"), ("AAPL", "EUR"), ("SWDA.L", None), ("EIMI.L", "GBp")])
def test_divisa_o_unidad_distinta_a_la_declarada_no_se_publica(world, tmp_path, sym, observed):
    world.currency[sym] = observed
    out, run = gen(tmp_path, "s1")
    assert not exists(out, "s/%s/latest.json" % sym) and not exists(out, "s/%s/d/2026.json" % sym)
    h = jl(out, "health.json")
    assert h["problems"]["unit_mismatch"] == [sym] and h["symbols"][sym]["fail_streak"] == 1
    assert exists(out, "s/^GSPC/latest.json")


def test_unit_mismatch_cuenta_como_fallo_aunque_haya_caida_global(world, tmp_path):
    reg = [mk("S%d" % i) for i in range(12)]
    world.decl.update({e["symbol"]: e for e in reg})
    for e in reg:
        world.currency[e["symbol"]] = "GBP"
    out, _ = gen(tmp_path, "s1", registry=reg)
    h = jl(out, "health.json")
    assert h["counts"]["unit_mismatch"] == 12 and all(v["fail_streak"] == 1 for v in h["symbols"].values())


def test_alias_de_la_misma_subunidad_se_acepta(world, tmp_path):
    world.currency["SWDA.L"] = "GBX"                                           # misma unidad que GBp
    out, _ = gen(tmp_path, "s1")
    assert jl(out, "s/SWDA.L/latest.json")["currency"] == "GBp"


def test_indices_y_divisas_no_se_comprueban_contra_yahoo(world, tmp_path):
    world.currency["^GSPC"] = "XXX"
    world.currency["EURUSD=X"] = None
    out, _ = gen(tmp_path, "s1")
    assert exists(out, "s/^GSPC/latest.json") and exists(out, "s/EURUSD=X/latest.json")


def test_check_unit_directo():
    assert f2.check_unit(mk("A", "subunit", "GBp"), "GBp") is None
    assert "GBP" in f2.check_unit(mk("A", "subunit", "GBp"), "GBP")
    assert f2.check_unit(mk("A", "currency", "USD"), None) is None
    assert f2.check_unit(mk("A", "subunit", "GBp"), None)


# ------------------------------------------------------------------ barras
def _df(rows, tz=None):
    idx = pd.DatetimeIndex([pd.Timestamp(r[0]) for r in rows])
    return pd.DataFrame({"Open": [r[1] for r in rows], "High": [r[2] for r in rows], "Low": [r[3] for r in rows], "Close": [r[4] for r in rows],
                         "Volume": [r[5] for r in rows]}, index=idx)


def test_bars_from_df_descarta_y_repara():
    nan = float("nan")
    today = date(2026, 10, 26)
    df = _df([("2026-10-19", 10, 11, 9, 10.5, 100),
              ("2026-10-20", 10, 10.2, 9.9, 0.0, 100),            # cierre 0: fuera
              ("2026-10-21", 10, 11, 9, nan, 100),                 # NaN: fuera
              ("2026-10-22", 10, 10.1, 10.05, 10.5, nan),          # high < close y low > open: se repara; volumen NaN -> 0
              ("2026-10-23", 5, 5, 5, -3.0, 1),                    # cierre negativo: fuera
              ("2026-10-24", 9, 12, 8, 10, 7),
              ("2026-12-31", 9, 12, 8, 10, 7)])                    # fecha futura: fuera
    bars = f2.bars_from_df(df, today)
    assert [b["d"] for b in bars] == ["2026-10-19", "2026-10-22", "2026-10-24"]
    assert all(b["l"] <= b["o"] and b["l"] <= b["c"] <= b["h"] and b["o"] <= b["h"] for b in bars) and bars[1]["v"] == 0


def test_merge_y_rebase_ratio():
    old = [{"d": "2026-01-0%d" % i, "c": 10.0 * i} for i in range(1, 6)]
    new = [{"d": "2026-01-0%d" % i, "c": 5.0 * i} for i in range(3, 8)]
    assert f2.rebase_ratio(old, new) == 2.0 and f2.rebase_ratio(old, new[3:]) is None
    m = f2.merge_bars([{"d": "a", "c": 1}, {"d": "b", "c": 2}], [{"d": "b", "c": 3}, {"d": "c", "c": 4}])
    assert [(b["d"], b["c"]) for b in m] == [("a", 1), ("b", 3), ("c", 4)]


# ------------------------------------------------------------------ cripto
def coin(i, **kw):
    r = {"id": "coin-%d" % i, "symbol": "c%d" % i, "name": "Coin %d" % i, "current_price": 1.5 + i, "market_cap": 1e9 - i, "market_cap_rank": i + 1,
         "total_volume": 5e6, "price_change_percentage_24h_in_currency": 1.234, "price_change_percentage_7d_in_currency": -2.5,
         "last_updated": "2026-10-26T11:58:00.000Z"}
    r.update(kw)
    return r


def test_cripto_una_llamada_de_250_y_campos_validados(world, tmp_path, monkeypatch):
    seen = []

    def cg(params, tries=3):
        seen.append(dict(params))
        return [coin(i) for i in range(250)] + [coin(900, id="Bad Id!"), coin(901, current_price=0), coin(902, symbol="x/y"),
                                                 coin(903, name="<script>alert(1)</script>"), "basura", coin(904, current_price="1e500")]
    monkeypatch.setattr(fm, "_cg_markets", cg)
    out, run = gen(tmp_path, "s1")
    assert len(seen) == 1 and seen[0]["per_page"] == 250 and seen[0]["vs_currency"] == "usd"
    cr = jl(out, "crypto/latest.json")
    assert (cr["currency"], cr["unit"], cr["count"]) == ("USD", "currency", 250) and len(cr["coins"]) == 250
    assert cr["coins"][0] == {"id": "coin-0", "symbol": "C0", "name": "Coin 0", "rank": 1, "price": 1.5, "change_24h_pct": 1.23,
                              "change_7d_pct": -2.5, "market_cap": 1e9, "volume_24h": 5e6, "as_of": "2026-10-26"}
    assert not any("<" in co["name"] for co in cr["coins"])


def test_normalize_coins_descarta_lo_hostil():
    rows = [coin(1, id="../../etc"), coin(2, symbol="A" * 16), coin(3, current_price=float("nan")), coin(4, current_price=-1),
            coin(5, name="<img src=x onerror=alert(1)>"), coin(6, name=""), coin(7, price_change_percentage_24h_in_currency=1e9),
            coin(8, market_cap_rank="1"), coin(9, id=None), None, 5]
    out = f2.normalize_coins(rows, date(2026, 10, 26))
    assert [co["id"] for co in out] == ["coin-7", "coin-8"]                    # coin-5: el nombre era solo marcado y queda vacío
    assert out[0]["change_24h_pct"] is None and out[1]["rank"] is None
    assert f2.normalize_coins([coin(1, name="Ok <b>bold</b> &lt;i&gt;")], date(2026, 10, 26))[0]["name"] == "Ok bold"


def test_cripto_caida_reutiliza_el_anterior_marcado_stale_hasta_7_dias(world, tmp_path, monkeypatch):
    monkeypatch.setattr(fm, "_cg_markets", lambda params, tries=3: [coin(i) for i in range(30)])
    out1, _ = gen(tmp_path, "s1")
    monkeypatch.setattr(fm, "_cg_markets", lambda params, tries=3: (_ for _ in ()).throw(TimeoutError("x")))
    out2, _ = gen(tmp_path, "s2", prev=out1, now=NOW + timedelta(days=2))
    assert jl(out2, "crypto/latest.json")["stale"] is True and jl(out2, "crypto/latest.json")["count"] == 30
    out3, _ = gen(tmp_path, "s3", prev=out1, now=NOW + timedelta(days=9))
    assert not exists(out3, "crypto/latest.json")


def test_cripto_con_pocas_monedas_no_se_publica(world, tmp_path, monkeypatch):
    monkeypatch.setattr(fm, "_cg_markets", lambda params, tries=3: [coin(i) for i in range(5)])
    out, _ = gen(tmp_path, "s1")
    assert not exists(out, "crypto/latest.json")


# ------------------------------------------------------------------ macro y noticias
def test_macro_fx_usd_sale_de_los_pares_del_registro(world, tmp_path):
    out, _ = gen(tmp_path, "s1")
    m = jl(out, "macro.json")
    assert set(m) == {"schema", "generated_at", "fx_usd", "rates", "cpi", "cpi_meta", "market_mood", "calendar"}
    jpy = jl(out, "s/USDJPY=X/latest.json")["price"]
    assert m["fx_usd"]["USD"] == 1.0 and m["fx_usd"]["EUR"] == jl(out, "s/EURUSD=X/latest.json")["price"]
    assert m["fx_usd"]["JPY"] == pytest.approx(1 / jpy, rel=1e-6)
    assert m["market_mood"]["vix"]["value"] == 15.0 and m["calendar"] == []


def test_macro_no_usa_pares_stale_ni_fuera_de_rango(world, tmp_path):
    run = f2.Run(REG, str(tmp_path / "x"), None, NOW)
    run.latest = {"EURUSD=X": {"price": 1.1, "stale": True}, "USDJPY=X": {"price": 0.0}, "GBPUSD=X": {"price": 1.3}}
    reg = REG + [mk("GBPUSD=X", "rate", "USD")]
    run.registry = reg
    assert run.fx_usd() == {"USD": 1.0, "GBP": 1.3}


def test_calendario_macro_solo_con_fred_y_filtrado(world, tmp_path, monkeypatch):
    class Res:
        def raise_for_status(self): pass
        def json(self): return {"release_dates": [
            {"release_id": 10, "release_name": "Consumer Price Index", "date": "2026-11-12"},
            {"release_id": 11, "release_name": "Something Irrelevant", "date": "2026-11-13"},
            {"release_id": 50, "release_name": "Employment Situation", "date": "2026-11-06"},
            {"release_id": 10, "release_name": "Consumer Price Index", "date": "2026-10-01"},      # pasada
            {"release_id": 10, "release_name": "Consumer Price Index", "date": "mañana"}]}
    seen = []
    monkeypatch.setattr(f2.requests, "get", lambda url, **k: seen.append((url, k)) or Res())
    assert f2.build_calendar(date(2026, 10, 26)) == []                          # sin clave: ni una petición
    assert seen == []
    monkeypatch.setattr(fm, "FRED_API_KEY", "clave")
    cal = f2.build_calendar(date(2026, 10, 26))
    assert cal == [{"date": "2026-11-12", "event": "US CPI"}, {"date": "2026-11-06", "event": "US Employment Situation"}]
    monkeypatch.setattr(f2.requests, "get", lambda url, **k: (_ for _ in ()).throw(RuntimeError("clave=clave")))
    assert f2.build_calendar(date(2026, 10, 26)) == []


def test_noticias_usan_simbolos_codificados_y_el_campo_watchlist(world, tmp_path, monkeypatch):
    urls = []

    def feed(url, limit=8):
        urls.append(url)
        return [{"title": "Titular %d %s" % (len(urls), url[-12:]), "link": "https://e.com/%d" % len(urls), "source": "S", "published": "p"}]
    monkeypatch.setattr(fm, "parse_feed", feed)
    reg = REG + [mk("BRK-B")]
    out, _ = gen(tmp_path, "s1", registry=reg)
    n = jl(out, "news.json")
    assert set(n) == {"schema", "generated_at", "general", "watchlist"} and "positions" not in n
    assert any("s=BRK-B&" in u for u in urls) and any("s=AAPL&" in u for u in urls)
    assert sum("s=%5EGSPC&" in u for u in urls) == 1                            # solo el feed general constante: los índices no llevan titulares propios
    assert {w["symbol"] for w in n["watchlist"]} >= {"AAPL", "BRK-B"} and all(set(w) >= {"symbol", "asset_name", "title", "link"} for w in n["watchlist"])


def test_noticias_url_de_simbolo_peligroso_no_se_construye():
    with pytest.raises(ValueError):
        c.quote_symbol("AAPL&s=EVIL")
    assert f2.YAHOO_RSS.format(c.quote_symbol("^GSPC")).count("%5EGSPC") == 1


def test_noticias_cripto_filtradas_por_el_registro_publico(world, tmp_path, monkeypatch):
    def feed(url, limit=8):
        if "coindesk" in url or "cointelegraph" in url:
            return [{"title": "Bitcoin sube", "link": "https://e.com/a", "source": "S", "published": "p"},
                    {"title": "Tezos y Mina suben", "link": "https://e.com/b", "source": "S", "published": "p"}]
        return []
    monkeypatch.setattr(fm, "parse_feed", feed)
    out, _ = gen(tmp_path, "s1")
    w = jl(out, "news.json")["watchlist"]
    assert [x["symbol"] for x in w] == ["BTC"]                                  # solo lo que está en el registro neutro


# ------------------------------------------------------------------ salud y utilidades
def test_health_lleva_peticiones_estado_y_problemas(world, tmp_path):
    out, run = gen(tmp_path, "s1")
    h = jl(out, "health.json")
    assert set(h) == {"schema", "generated_at", "run", "counts", "problems", "symbols"}
    assert h["run"]["yahoo_calls"] == len(world.calls) and h["counts"]["registry"] == 9 and h["counts"]["published"] == 7
    assert set(h["symbols"]["AAPL"]) == {"status", "fail_streak", "last_ok", "last_try", "tier"}
    assert set(h["problems"]) == {"unit_mismatch", "failed", "rebased", "deferred", "frozen"}


def test_simbolo_congelado_se_marca_delisted(world, tmp_path):
    world.end = date(2026, 8, 1)                                               # última barra de hace 86 días
    out, _ = gen(tmp_path, "s1")
    h = jl(out, "health.json")
    assert h["symbols"]["AAPL"]["status"] == "delisted" and "AAPL" in h["problems"]["frozen"]
    assert not exists(out, "s/AAPL/latest.json")


def test_generate_no_borra_un_directorio_con_contenido(world, tmp_path):
    d = tmp_path / "x" / "v2"
    d.mkdir(parents=True)
    (d / "importante.txt").write_text("no tocar")
    with pytest.raises(FileExistsError):
        f2.generate(REG, str(d), None, now=NOW)
    assert (d / "importante.txt").read_text() == "no tocar"


def test_salida_determinista_para_lo_que_no_cambia(world, tmp_path):
    a, _ = gen(tmp_path, "a")
    b, _ = gen(tmp_path, "b")
    assert sha(a, "s/AAPL/d/2024.json") == sha(b, "s/AAPL/d/2024.json") and sha(a, "s/AAPL/d/2026.json") == sha(b, "s/AAPL/d/2026.json")


def test_main_registro_invalido_sale_con_error(tmp_path, capsys):
    p = tmp_path / "r.json"
    p.write_text(json.dumps({"schema": 1, "symbols": [mk("A", "currency", "GBp")]}))
    assert f2.main(["--registry", str(p), "--out", str(tmp_path / "o")]) == 1
    assert "::error::registro inválido" in capsys.readouterr().out


def test_main_de_extremo_a_extremo(world, tmp_path, capsys, monkeypatch):
    p = tmp_path / "r.json"
    p.write_text(json.dumps({"schema": 1, "symbols": REG}))
    assert f2.main(["--registry", str(p), "--out", str(tmp_path / "o" / "v2"), "--prev", str(tmp_path / "no_existe")]) == 0
    assert "v2: 9 en el registro" in capsys.readouterr().out
    assert (tmp_path / "o" / "v2" / "manifest.json").is_file()


# ------------------------------------------------------------------ mediciones con el registro REAL (peticiones por run y tamaño total)
def test_medicion_de_peticiones_y_tamano_con_el_registro_real(world, tmp_path, capsys):
    """Cifras que se documentan en auditorias/FIXES_FEED_V2.md. Se imprimen con `pytest -s tests/test_feed_v2.py -k medicion`.
    OJO: la serie sintética lleva 6 decimales en todos los precios (más bytes que los precios reales de Yahoo, que casi siempre tienen 2-4),
    así que el tamaño es una cota superior razonable."""
    reg = c.load_registry(os.path.join(os.path.dirname(__file__), "..", "registry.json"))
    world.decl = {e["symbol"]: e for e in reg}
    yahoo = [e for e in reg if e["kind"] == "yahoo"]
    hot = [e for e in yahoo if e["tier"] == "hot"]
    rows, prev, t = [], None, NOW
    for label, hours in (("alta 1", 0), ("alta 2", 1), ("alta 3", 1), ("solo hot (+6 h)", 6), ("hot + warm (+21 h)", 21), ("hot (+6 h)", 6)):
        t += timedelta(hours=hours)
        world.calls.clear()
        out, run = gen(tmp_path, label.replace(" ", "_").replace("+", "p").replace("(", "").replace(")", ""), prev=prev, now=t, registry=reg)
        daily = sum(1 for _, p, i in world.calls if i == "1d")
        intra = sum(1 for _, p, i in world.calls if i == "5m")
        long_ = sum(1 for _, p, i in world.calls if i == "1d" and p in ("5y", "10y"))
        rows.append((label, len(world.calls), daily, intra, long_, run.total_bytes, len(run.written)))
        prev = out
    mf = jl(prev, "manifest.json")
    by_kind = {}
    for rel, v in mf["files"].items():
        by_kind.setdefault(c.classify_path(rel)[0], []).append(v["bytes"])
    if os.environ.get("V2_MEASURE"):                                            # V2_MEASURE=1 pytest -s tests/test_feed_v2.py -k medicion
        with capsys.disabled():
            print("\n  run                      llamadas  diarias  intradía  largas   bytes totales   ficheros")
            for r in rows:
                print("  %-24s %8d %8d %9d %7d %15d %10d" % r)
            print("  tamaño por tipo (máx / medio): " + ", ".join("%s %d/%d" % (k, max(v), sum(v) // len(v)) for k, v in sorted(by_kind.items())))
            print("  total del sitio: %d bytes en %d ficheros (+ manifest.json de %d bytes); símbolos hot %d, warm %d, cold %d" % (
                mf["total_bytes"], len(mf["files"]), os.path.getsize(os.path.join(prev, "v2", "manifest.json")), len(hot),
                sum(e["tier"] == "warm" for e in yahoo), sum(e["tier"] == "cold" for e in yahoo)))
    assert rows[0][4] == f2.BACKFILL_MAX_PER_RUN and rows[1][4] == f2.BACKFILL_MAX_PER_RUN         # el cupo reparte el alta
    assert rows[3][1] <= 2 * len(hot) and rows[3][4] == 0                                          # run de solo hot: ≤ 2 peticiones por símbolo, ninguna larga
    assert rows[5][1] <= 2 * len(hot)
    assert all(jl(prev, "s/%s/latest.json" % e["symbol"]) for e in yahoo)                          # tras el alta, todos publicados
    assert mf["total_bytes"] <= c.MAX_SITE_BYTES and len(mf["files"]) <= c.MAX_SITE_FILES
    for rel, v in mf["files"].items():
        assert v["bytes"] <= c.MAX_BYTES[c.classify_path(rel)[0]], rel


def test_build_market_mood_acepta_prev_feed_y_omite_altcoin(monkeypatch):
    """Regresión: el parámetro se llamó `prev` y lo pisaba el cierre anterior del VIX (un float), con lo que el arrastre fallaba en silencio."""
    monkeypatch.setattr(fm, "yf_history", lambda *a, **k: pd.DataFrame({"Close": [15.0, 16.0]}))
    monkeypatch.setattr(fm.requests, "get", lambda *a, **k: (_ for _ in ()).throw(fm.requests.ConnectionError("caído")))
    monkeypatch.setattr(fm, "build_altcoin_season", lambda prev=None: (_ for _ in ()).throw(AssertionError("no debe llamarse")))
    prev = {"generated_at": (datetime.now(timezone.utc) - timedelta(days=1)).isoformat(), "market_mood": {"stock_fear_greed": {"value": 55.0}}}
    mood = fm.build_market_mood(prev_feed=prev, include_altcoin=False)
    assert mood["vix"]["value"] == 16.0 and mood["stock_fear_greed"]["stale"] is True and "altcoin_season" not in mood
