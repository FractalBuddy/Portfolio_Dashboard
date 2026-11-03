"""Bloque 6 (AU-06): picos aislados, precios fuera de rango plausible y huecos dentro de una serie. Con World.mutate de tests/v2_world.py.

Un cierre que salta más de ±50 % respecto a sus vecinos y VUELVE al nivel anterior (pico aislado) se trata igual que una barra con cierre <= 0: se
descarta antes de escribir, se anota en health.json (problems.dropped_bars, motivo `spike`) y cuenta para el tope de MAX_DROPPED_BARS (si se pasa,
el símbolo es `suspect` y no se publica). Un precio fuera del rango plausible de su unit se descarta igual (`price_range`). Un hueco de más de un año
dentro de la serie hace `suspect` al símbolo."""
from datetime import date

import pandas as pd
import pytest

import feed_v2 as f2
import feed_v2_common as c
import sanity_check_v2 as sc
from v2_world import REG, gen, jl

SPIKE_DAY = "2021-03-15"                      # lunes
TODAY = date(2026, 10, 27)


def errors(out, registry=REG, prev=None):
    return sc.check_site(str(out / "v2"), registry, str(prev / "v2") if prev else None, today=TODAY)[0]


def x(day, factor):
    def mut(df):
        df.loc[pd.Timestamp(day), ["Open", "High", "Low", "Close"]] = df.loc[pd.Timestamp(day), "Close"] * factor
        return df
    return mut


def chain(*fns):
    def mut(df):
        for f in fns:
            df = f(df)
        return df
    return mut


def closes(out, sym):
    res = {}
    for y in range(2016, 2027):
        try:
            doc = jl(out, "s/%s/d/%d.json" % (sym, y))
        except FileNotFoundError:
            continue
        res.update(dict(zip(doc["d"], doc["c"])))
    return res


# ------------------------------------------------------------------ reproducción: el pico ×11 se publicaba
def test_un_cierre_x11_aislado_en_2021_no_se_publica_y_queda_anotado(world, tmp_path):
    world.mutate["AAPL"] = x(SPIKE_DAY, 11)
    out, _ = gen(tmp_path, "s1")
    cl = closes(out, "AAPL")
    assert SPIKE_DAY not in cl, "el pico ×11 se ha publicado en un año cerrado"
    vals = sorted(cl.values())
    assert vals[-1] < 3 * vals[len(vals) // 2]                                     # ni este ni ningún otro cierre se aleja de la mediana
    h = jl(out, "health.json")
    assert h["problems"]["dropped_bars"]["AAPL"] == {"n": 1, "dates": [SPIKE_DAY], "reasons": {SPIKE_DAY: "spike"}}
    assert h["counts"]["suspect"] == 0 and jl(out, "s/AAPL/latest.json")["s"] == "AAPL"
    assert errors(out) == []


def test_un_pico_a_la_baja_tambien_cuenta(world, tmp_path):
    world.mutate["AAPL"] = x(SPIKE_DAY, 0.1)
    out, _ = gen(tmp_path, "s1")
    assert SPIKE_DAY not in closes(out, "AAPL")
    assert jl(out, "health.json")["problems"]["dropped_bars"]["AAPL"]["reasons"] == {SPIKE_DAY: "spike"}


def test_cuatro_picos_hacen_el_simbolo_suspect_como_las_barras_negativas(world, tmp_path):
    dias = ("2018-02-05", "2019-04-08", "2020-06-08", "2021-03-15")
    world.mutate["AAPL"] = chain(*(x(d, 11) for d in dias))
    out, _ = gen(tmp_path, "s1")
    h = jl(out, "health.json")
    assert h["problems"]["suspect"] == ["AAPL"] and "AAPL" in h["problems"]["failed"] and h["counts"]["suspect"] == 1
    with pytest.raises(FileNotFoundError):
        jl(out, "s/AAPL/latest.json")
    assert errors(out) == []                                                       # un símbolo sin publicar no bloquea el sitio


# ------------------------------------------------------------------ rango de precio plausible por unit
def test_un_precio_de_1e_7_se_descarta_por_fuera_de_rango(world, tmp_path):
    world.mutate["AAPL"] = x("2024-05-06", 1e-9)
    out, _ = gen(tmp_path, "s1")
    assert "2024-05-06" not in closes(out, "AAPL")
    assert jl(out, "health.json")["problems"]["dropped_bars"]["AAPL"]["reasons"] == {"2024-05-06": "price_range"}


def test_precio_1e_7_en_la_ultima_barra_no_llega_a_latest(world, tmp_path):
    def ultimo(df):
        df.iloc[-1, df.columns.get_indexer(["Open", "High", "Low", "Close"])] = 1e-7
        return df
    world.mutate["AAPL"] = ultimo
    out, _ = gen(tmp_path, "s1")
    assert jl(out, "s/AAPL/latest.json")["price"] > 1


@pytest.mark.parametrize("unit,ok,mal", [("currency", 150.0, 1e-7), ("currency", 7e5, 1e8), ("subunit", 8000.0, 1e-4), ("points", 5000.0, 1e-3),
                                         ("rate", 1.1, 1e-7), ("rate", 16000.0, 1e7)])
def test_rangos_plausibles_por_unit(unit, ok, mal):
    assert c.price_plausible(unit, ok) and not c.price_plausible(unit, mal)
    assert not c.price_plausible(unit, 0) and not c.price_plausible(unit, float("nan")) and not c.price_plausible("desconocida", 1.0)


def test_el_validador_rechaza_un_precio_fuera_de_rango_aunque_pase_el_esquema(world, tmp_path):
    from test_sanity_check_v2 import edit
    out, _ = gen(tmp_path, "s1")
    base = str(out / "v2")
    edit(base, "s/AAPL/latest.json", lambda d: d.update(price=1e-7, sparkline=[1e-7] * 5))
    errs = sc.check_site(base, REG, None, today=TODAY)[0]
    assert any("rango" in e and "AAPL" in e for e in errs), errs


# ------------------------------------------------------------------ hueco largo dentro de la serie
def test_un_hueco_de_500_barras_hace_el_simbolo_suspect(world, tmp_path):
    def hueco(df):
        i = df.index.get_loc(pd.Timestamp("2021-03-15"))
        return df.drop(df.index[i:i + 500])
    world.mutate["AAPL"] = hueco
    out, _ = gen(tmp_path, "s1")
    h = jl(out, "health.json")
    assert h["problems"]["suspect"] == ["AAPL"] and "AAPL" in h["problems"]["failed"]


def test_un_hueco_normal_de_unas_semanas_no_es_sospechoso(world, tmp_path):
    def vacaciones(df):
        i = df.index.get_loc(pd.Timestamp("2021-03-15"))
        return df.drop(df.index[i:i + 40])                                         # ~2 meses sin cotizar (suspensión)
    world.mutate["AAPL"] = vacaciones
    out, _ = gen(tmp_path, "s1")
    assert jl(out, "health.json")["problems"]["suspect"] == [] and jl(out, "s/AAPL/latest.json")["s"] == "AAPL"


# ------------------------------------------------------------------ controles negativos: nada de falsos positivos
def escalon(factor, dia="2021-03-15"):
    """Un cambio de nivel que SE MANTIENE desde `dia` (crash real sostenido, o split sin normalizar)."""
    def mut(df):
        sel = df.index >= pd.Timestamp(dia)
        df.loc[sel, ["Open", "High", "Low", "Close"]] = df.loc[sel, ["Open", "High", "Low", "Close"]] * factor
        return df
    return mut


@pytest.mark.parametrize("factor", [0.6, 0.4, 0.1])
def test_un_crash_real_sostenido_no_es_un_pico(world, tmp_path, factor):
    world.mutate["AAPL"] = escalon(factor)
    out, _ = gen(tmp_path, "s1")
    h = jl(out, "health.json")
    assert "AAPL" not in h["problems"]["dropped_bars"] and h["problems"]["suspect"] == []
    assert SPIKE_DAY in closes(out, "AAPL")


def test_un_crash_de_40_por_ciento_con_rebote_parcial_no_es_un_pico(world, tmp_path):
    def crash(df):
        d = df.index.get_loc(pd.Timestamp("2021-03-15"))
        for k, f in enumerate((0.6, 0.65, 0.7, 0.8, 0.85)):
            df.iloc[d + k, df.columns.get_indexer(["Open", "High", "Low", "Close"])] *= f
        return df
    world.mutate["AAPL"] = crash
    out, _ = gen(tmp_path, "s1")
    assert "AAPL" not in jl(out, "health.json")["problems"]["dropped_bars"]


@pytest.mark.parametrize("ratio", [10, 5, 2])
def test_un_split_ya_normalizado_por_yahoo_no_se_toca(world, tmp_path, ratio):
    """Yahoo devuelve la serie ya ajustada: el cierre es continuo. Y si NO viniera ajustada (escalón que se mantiene) tampoco es un pico."""
    out, _ = gen(tmp_path, "s1")
    assert "AAPL" not in jl(out, "health.json")["problems"]["dropped_bars"]
    world.mutate["AAPL"] = escalon(1.0 / ratio, "2022-08-25")
    out2, _ = gen(tmp_path, "s2")
    assert "AAPL" not in jl(out2, "health.json")["problems"]["dropped_bars"]


def test_un_gap_de_mercado_de_mas_del_50_por_ciento_que_se_mantiene_dos_dias_no_se_descarta(world, tmp_path):
    def gap(df):
        d = df.index.get_loc(pd.Timestamp("2021-03-15"))
        df.iloc[d, df.columns.get_indexer(["Open", "High", "Low", "Close"])] *= 1.8
        df.iloc[d + 1, df.columns.get_indexer(["Open", "High", "Low", "Close"])] *= 1.7          # la 2.ª barra también está alta: no es un tick suelto
        return df
    world.mutate["AAPL"] = gap
    out, _ = gen(tmp_path, "s1")
    assert "AAPL" not in jl(out, "health.json")["problems"]["dropped_bars"]


# ------------------------------------------------------------------ la función pura
@pytest.mark.parametrize("serie,idx", [
    ([10, 10, 110, 10, 10], [2]),                       # ×11
    ([10, 10, 1, 10, 10], [2]),                         # ÷10
    ([10, 10, 15.1, 10, 10], [2]),                      # justo por encima de +50 %
    ([10, 10, 14.9, 10, 10], []),                       # justo por debajo
    ([10, 10, 4.9, 10, 10], [2]),                       # justo por debajo de -50 %
    ([10, 10, 5.1, 10, 10], []),
    ([10, 10, 40, 40, 10], []),                         # dos barras seguidas: no es un tick suelto
    ([10, 20, 10, 10], [1]),                            # +100 % que vuelve
    ([10, 10, 10, 10, 100], []),                        # el último no se puede juzgar (sin vecino posterior)
    ([100, 10, 10, 10, 10], []),                        # ni el primero
    ([10, 10, 6, 6, 6], []),                            # -40 % sostenido
    ([10, 10, 4, 4, 4], []),                            # -60 % sostenido
    ([10, 12, 11, 25, 12, 11], [3]),                    # vecinos distintos pero ambos bajos
    ([10, 20, 40, 10], []),                             # tendencia alcista: los vecinos no se parecen
])
def test_spike_indices(serie, idx):
    assert c.spike_indices([float(v) for v in serie]) == idx


def test_las_barras_de_una_serie_limpia_de_10_anos_no_dan_ningun_pico():
    import math
    serie = [100 * (1 + 0.1 * math.sin(i / 37)) for i in range(2600)]
    assert c.spike_indices(serie) == []


def test_split_bars_acepta_la_unit_y_descarta_fuera_de_rango():
    idx = pd.DatetimeIndex([pd.Timestamp("2026-10-%02d" % d) for d in range(12, 17)])
    df = pd.DataFrame({"Open": [10.0] * 5, "High": [11.0] * 5, "Low": [9.0] * 5, "Close": [10.5, 10.5, 1e-9, 10.5, 10.5], "Volume": [5] * 5}, index=idx)
    bars, dropped = f2.split_bars(df, date(2026, 10, 26), "currency")
    assert dropped == {"2026-10-14": "price_range"} and len(bars) == 4
    bars, dropped = f2.split_bars(df, date(2026, 10, 26))                           # sin unit (llamadas antiguas): igual que antes
    assert dropped == {} and len(bars) == 5
