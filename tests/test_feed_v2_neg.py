"""Feed v2: precios <= 0 o no finitos (el WTI cerró en negativo el 2020-04-20). El generador descarta la barra SOLO si su CIERRE es no finito o <= 0 y anota el
motivo; un open/high/low malo con cierre bueno (WTI, 2020-04-21: cierre +10,01 $, mínimo -40,32) se repara con el cierre y la barra se publica. El validador
sigue estricto (su test está en test_sanity_check_v2.py). Informes: auditorias/FIXES_FEED_V2_NEG.md y FIXES_FEED_V2_HB.md."""
import os
from datetime import date, timedelta

import pandas as pd

import feed_v2 as f2
import sanity_check_v2 as sc
from v2_world import NOW, REG, gen, jl, mk, sha

NAN = float("nan")
CL = mk("CL=F", name="WTI Crude Oil")
REG_CL = REG + [CL]


def setv(df, day, **cols):
    """Estropea la barra de `day` (columnas Open/High/Low/Close)."""
    for k, v in cols.items():
        df.loc[pd.Timestamp(day), k] = v
    return df


def wti_2020(df):
    """Las dos barras de abril de 2020 (sintéticas, con la forma de las reales): cierre negativo el 20 (se descarta) y apertura y mínimo negativos con
    cierre positivo (+10,01 $) el 21 (se publica, reparada)."""
    setv(df, "2020-04-20", Open=17.65, High=17.65, Low=-40.32, Close=-37.63)
    setv(df, "2020-04-21", Open=-14.0, High=13.0, Low=-40.32, Close=10.01)
    return df


def errors(out, registry=REG_CL, prev=None, today=date(2026, 10, 27)):
    return sc.check_site(str(out / "v2"), registry, str(prev / "v2") if prev else None, today=today)[0]


def dated(out, sym, year):
    return jl(out, "s/%s/d/%d.json" % (sym, year))["d"]


# ------------------------------------------------------------------ barras sueltas
def test_split_bars_descarta_solo_por_el_cierre_y_anota_el_motivo():
    inf = float("inf")
    idx = pd.DatetimeIndex([pd.Timestamp("2026-10-%02d" % d) for d in range(12, 24)])
    df = pd.DataFrame({"Open": [10.0] * 12, "High": [11.0] * 12, "Low": [9.0] * 12, "Close": [10.5] * 12, "Volume": [5] * 12}, index=idx)
    setv(df, "2026-10-13", Close=NAN)                       # cierre NaN
    setv(df, "2026-10-14", Close=None)                      # cierre None (pandas lo guarda como NaN)
    setv(df, "2026-10-15", Close=inf)                       # cierre infinito
    setv(df, "2026-10-16", Close=-37.63)                    # cierre negativo
    setv(df, "2026-10-19", Close=0.0)                       # cierre cero
    setv(df, "2026-10-20", Open=NAN)                        # apertura NaN con cierre bueno: SE PUBLICA (apertura = cierre)
    setv(df, "2026-10-21", High=-inf)                       # máximo infinito con cierre bueno: se publica
    setv(df, "2026-10-22", Low=-40.32)                      # mínimo negativo con cierre positivo (WTI 2020-04-21): se publica
    bars, dropped = f2.split_bars(df, date(2026, 10, 26))
    assert dropped == {"2026-10-13": "close_missing", "2026-10-14": "close_missing", "2026-10-15": "close_not_finite",
                       "2026-10-16": "close_le_0", "2026-10-19": "close_le_0"}
    by = {b["d"]: b for b in bars}
    assert [b["d"] for b in bars] == ["2026-10-12", "2026-10-17", "2026-10-18", "2026-10-20", "2026-10-21", "2026-10-22", "2026-10-23"]
    assert (by["2026-10-20"]["o"], by["2026-10-20"]["c"]) == (10.5, 10.5)
    assert by["2026-10-21"]["h"] == 10.5 and by["2026-10-22"]["l"] == 10.0 and by["2026-10-22"]["c"] == 10.5
    assert all(b["o"] > 0 and b["h"] > 0 and b["l"] > 0 and b["c"] > 0 and b["l"] <= min(b["o"], b["c"]) and b["h"] >= max(b["o"], b["c"]) for b in bars)
    assert f2.bars_from_df(df, date(2026, 10, 26)) == bars


def test_las_barras_del_wti_de_abril_de_2020_el_20_se_descarta_y_el_21_no():
    """Serie sintética con las dos barras de CL=F: el 20 (cierre -37,63) fuera por close_le_0; el 21 (cierre +10,01, apertura y mínimo negativos) dentro."""
    idx = pd.DatetimeIndex([pd.Timestamp(d) for d in ("2020-04-17", "2020-04-20", "2020-04-21", "2020-04-22")])
    df = pd.DataFrame({"Open": [20.0, 17.65, -14.0, 11.0], "High": [20.5, 17.65, 13.0, 13.0], "Low": [18.0, -40.32, -40.32, 10.0],
                       "Close": [18.27, -37.63, 10.01, 11.57], "Volume": [1, 2, 3, 4]}, index=idx)
    bars, dropped = f2.split_bars(df, date(2026, 10, 26))
    assert dropped == {"2020-04-20": "close_le_0"}
    b21 = next(b for b in bars if b["d"] == "2020-04-21")
    assert (b21["o"], b21["h"], b21["l"], b21["c"]) == (10.01, 13.0, 10.01, 10.01)
    assert [b["d"] for b in bars] == ["2020-04-17", "2020-04-21", "2020-04-22"]


def test_fila_vacia_no_es_barra_ni_cuenta_como_descartada_y_sin_columna_se_rellena_con_el_cierre():
    idx = pd.DatetimeIndex([pd.Timestamp("2026-10-21"), pd.Timestamp("2026-10-22"), pd.Timestamp("2026-10-23")])
    df = pd.DataFrame({"Open": [1.0, NAN, 1.0], "High": [2.0, NAN, 2.0], "Low": [0.5, NAN, 0.5], "Close": [1.5, NAN, 1.5], "Volume": [3, NAN, 3]}, index=idx)
    bars, dropped = f2.split_bars(df, date(2026, 10, 26))
    assert [b["d"] for b in bars] == ["2026-10-21", "2026-10-23"] and dropped == {}
    solo_cierre = pd.DataFrame({"Close": [4.0, 5.0]}, index=idx[:2])
    bars, dropped = f2.split_bars(solo_cierre, date(2026, 10, 26))
    assert dropped == {} and [(b["o"], b["h"], b["l"], b["c"]) for b in bars] == [(4.0, 4.0, 4.0, 4.0), (5.0, 5.0, 5.0, 5.0)]
    sin_cierre = pd.DataFrame({"Open": [4.0, 5.0], "High": [4.0, 5.0], "Low": [4.0, 5.0]}, index=idx[:2])                 # falta la COLUMNA Close
    bars, dropped = f2.split_bars(sin_cierre, date(2026, 10, 26))
    assert bars == [] and dropped == {"2026-10-21": "close_missing", "2026-10-22": "close_missing"}


# ------------------------------------------------------------------ un símbolo con la barra del WTI
def test_wti_con_cierre_negativo_se_publica_sin_esas_barras_y_queda_anotado(world, tmp_path):
    world.mutate["CL=F"] = wti_2020
    out, run = gen(tmp_path, "s1", registry=REG_CL)
    d2020 = dated(out, "CL=F", 2020)
    assert "2020-04-20" not in d2020 and "2020-04-21" in d2020 and "2020-04-17" in d2020 and "2020-04-22" in d2020
    i21 = d2020.index("2020-04-21")
    doc = jl(out, "s/CL=F/d/2020.json")
    assert doc["c"][i21] == 10.01 and doc["o"][i21] == 10.01 and doc["l"][i21] == 10.01 and doc["h"][i21] == 13.0
    assert jl(out, "s/CL=F/latest.json")["as_of"] == "2026-10-23"
    h = jl(out, "health.json")
    assert h["problems"]["dropped_bars"] == {"CL=F": {"n": 1, "dates": ["2020-04-20"], "reasons": {"2020-04-20": "close_le_0"}}}
    assert h["counts"]["dropped_bars"] == 1 and h["counts"]["suspect"] == 0 and h["problems"]["suspect"] == []
    assert h["symbols"]["CL=F"]["status"] == "active" and h["symbols"]["CL=F"]["dropped"] == ["2020-04-20"]
    assert h["symbols"]["CL=F"]["dropped_why"] == {"2020-04-20": "close_le_0"}
    assert "CL=F" not in h["problems"]["failed"]
    assert errors(out) == []                                           # el validador (estricto) deja pasar el sitio resultante


def test_una_sola_barra_negativa_basta_y_el_resto_de_simbolos_no_se_toca(world, tmp_path):
    world.mutate["CL=F"] = lambda df: setv(df, "2020-04-20", Close=-37.63)
    out, _ = gen(tmp_path, "s1", registry=REG_CL)
    assert "2020-04-20" not in dated(out, "CL=F", 2020)
    assert list(jl(out, "health.json")["problems"]["dropped_bars"]) == ["CL=F"]
    assert "dropped" not in jl(out, "health.json")["symbols"]["AAPL"]


def test_la_descarta_en_el_log_como_contador(world, tmp_path, capsys):
    world.mutate["CL=F"] = wti_2020
    gen(tmp_path, "s1", registry=REG_CL)
    assert "~ CL=F: 1 barras descartadas en este run" in capsys.readouterr().err


# ------------------------------------------------------------------ tope de seguridad
def bad_closes(*days):
    def mut(df):
        for d in days:
            setv(df, d, Close=-1.0)
        return df
    return mut


DAYS4 = ("2020-04-20", "2021-05-03", "2022-06-06", "2023-07-03")


def test_tres_barras_malas_aun_se_publica_y_cuatro_no(world, tmp_path):
    world.mutate["CL=F"] = bad_closes(*DAYS4[:3])
    out, _ = gen(tmp_path, "s3", registry=REG_CL)
    assert jl(out, "health.json")["problems"]["dropped_bars"]["CL=F"]["n"] == 3
    assert jl(out, "s/CL=F/latest.json")["s"] == "CL=F"
    world.mutate["CL=F"] = bad_closes(*DAYS4)
    out, run = gen(tmp_path, "s4", registry=REG_CL)
    h = jl(out, "health.json")
    assert not os.path.exists(os.path.join(str(out), "v2", "s", "CL=F")) and "CL=F" not in {r["s"] for r in jl(out, "index.json")["symbols"] if r["as_of"]}
    assert h["problems"]["suspect"] == ["CL=F"] and "CL=F" in h["problems"]["failed"] and h["counts"]["suspect"] == 1
    assert h["problems"]["dropped_bars"]["CL=F"]["n"] == 4
    assert h["symbols"]["CL=F"]["fail_streak"] == 1
    assert errors(out) == []                                           # un símbolo sin publicar no bloquea el sitio
    assert any("suspect" in str(a) or "sospechosos" in str(a) for a in sc.check_site(str(out / "v2"), REG_CL, None, today=date(2026, 10, 27))[1])


def test_mas_del_1_por_ciento_de_las_barras_tambien_bloquea(world, tmp_path):
    world.mutate["CL=F"] = lambda df: setv(df.iloc[-60:], df.index[-30].strftime("%Y-%m-%d"), Close=-1.0)     # 1 mala de 60 = 1,6 %
    out, _ = gen(tmp_path, "s1", registry=REG_CL)
    h = jl(out, "health.json")
    assert h["problems"]["suspect"] == ["CL=F"] and not os.path.exists(os.path.join(str(out), "v2", "s", "CL=F"))


def test_suspect_cuenta_como_fallo_aunque_haya_caida_global(world, tmp_path):
    world.mutate["CL=F"] = bad_closes(*DAYS4)
    extra = [mk("X%d" % i) for i in range(4)]                           # el umbral de caída global necesita >= 10 intentos
    for e in extra:
        world.decl[e["symbol"]] = e
    for s in ("^GSPC", "AAPL", "SWDA.L", "EIMI.L", "EURUSD=X", "USDJPY=X", "GC=F", *(e["symbol"] for e in extra)):
        world.fail.add(s)
    out, _ = gen(tmp_path, "s1", registry=REG_CL + extra)
    h = jl(out, "health.json")
    assert h["counts"]["outage"] == 1 and h["symbols"]["CL=F"]["fail_streak"] == 1 and h["symbols"]["AAPL"]["fail_streak"] == 0


# ------------------------------------------------------------------ incremental
def test_reintento_incremental_con_el_sitio_anterior_sin_esa_barra_es_coherente(world, tmp_path):
    world.mutate["CL=F"] = wti_2020
    out1, _ = gen(tmp_path, "s1", registry=REG_CL)
    world.end = date(2026, 10, 27)
    out2, run2 = gen(tmp_path, "s2", prev=out1, now=NOW + timedelta(hours=6), registry=REG_CL)
    assert sha(out1, "s/CL=F/d/2020.json") == sha(out2, "s/CL=F/d/2020.json")           # año cerrado: copia byte a byte
    h = jl(out2, "health.json")
    assert h["problems"]["dropped_bars"]["CL=F"] == {"n": 1, "dates": ["2020-04-20"], "reasons": {"2020-04-20": "close_le_0"}}
    assert jl(out2, "s/CL=F/latest.json")["as_of"] == "2026-10-27" and "CL=F" not in h["problems"]["failed"]
    assert h["problems"]["rebased"] == [] and h["problems"]["suspect"] == []
    assert errors(out2, prev=out1) == []                                # continuidad con el sitio anterior: el hueco no cuenta como cambio


def test_rebase_baja_la_serie_entera_otra_vez_sin_contar_dos_veces_las_barras_malas(world, tmp_path):
    world.mutate["CL=F"] = wti_2020
    out1, _ = gen(tmp_path, "s1", registry=REG_CL)
    world.end = date(2026, 10, 27)
    world.scale["CL=F"] = 2.0                                           # split: el pasado guardado ya no casa -> descarga de 10 años
    mal = wti_2020
    world.mutate["CL=F"] = lambda df: mal(df)
    out2, run2 = gen(tmp_path, "s2", prev=out1, now=NOW + timedelta(hours=6), registry=REG_CL)
    h = jl(out2, "health.json")
    assert h["problems"]["rebased"] == ["CL=F"]
    assert h["problems"]["dropped_bars"]["CL=F"]["n"] == 1 and h["problems"]["suspect"] == []
    assert "2020-04-20" not in dated(out2, "CL=F", 2020) and "2020-04-21" in dated(out2, "CL=F", 2020)


def test_las_barras_malas_se_acumulan_entre_runs_y_el_tope_cuenta_todo_el_historico(world, tmp_path):
    world.mutate["CL=F"] = bad_closes("2020-04-20", "2021-05-03", "2022-06-06")
    out1, _ = gen(tmp_path, "s1", registry=REG_CL)
    assert jl(out1, "health.json")["problems"]["dropped_bars"]["CL=F"]["n"] == 3
    # dos días después aparece una cuarta barra mala dentro de la ventana de 1 mes; las anteriores ya no se ven en la descarga incremental
    world.end = date(2026, 10, 27)
    world.mutate["CL=F"] = bad_closes("2026-10-26")
    out2, _ = gen(tmp_path, "s2", prev=out1, now=NOW + timedelta(hours=6), registry=REG_CL)
    h = jl(out2, "health.json")
    assert h["problems"]["suspect"] == ["CL=F"] and h["problems"]["dropped_bars"]["CL=F"]["n"] == 4
    lat = jl(out2, "s/CL=F/latest.json")
    assert lat["stale"] is True and lat["as_of"] == "2026-10-23"       # se conserva lo publicado, marcado stale
    assert sha(out1, "s/CL=F/d/2020.json") == sha(out2, "s/CL=F/d/2020.json")
    assert errors(out2, prev=out1) == []


def test_una_barra_mala_reciente_que_yahoo_corrige_deja_de_contar(world, tmp_path):
    world.mutate["CL=F"] = bad_closes("2026-10-21")
    out1, _ = gen(tmp_path, "s1", registry=REG_CL)
    assert jl(out1, "health.json")["problems"]["dropped_bars"]["CL=F"]["dates"] == ["2026-10-21"]
    world.end = date(2026, 10, 27)
    world.mutate.clear()                                                # Yahoo ya da el 21 bueno
    out2, _ = gen(tmp_path, "s2", prev=out1, now=NOW + timedelta(hours=6), registry=REG_CL)
    h = jl(out2, "health.json")
    assert h["problems"]["dropped_bars"] == {} and "dropped" not in h["symbols"]["CL=F"] and "2026-10-21" in dated(out2, "CL=F", 2026)


# ------------------------------------------------------------------ motivo de cada barra descartada
def test_cada_barra_descartada_lleva_su_motivo_y_se_conserva_entre_runs(world, tmp_path):
    def mal(df):
        setv(df, "2020-04-20", Close=-37.63)
        setv(df, "2021-05-03", Close=NAN)
        setv(df, "2022-06-06", Close=float("inf"))
        return df
    world.mutate["CL=F"] = mal
    out1, _ = gen(tmp_path, "s1", registry=REG_CL)
    want = {"2020-04-20": "close_le_0", "2021-05-03": "close_missing", "2022-06-06": "close_not_finite"}
    h = jl(out1, "health.json")
    assert h["problems"]["dropped_bars"]["CL=F"]["reasons"] == want and h["symbols"]["CL=F"]["dropped_why"] == want
    assert errors(out1) == []                                           # el validador acepta el esquema nuevo
    world.end = date(2026, 10, 27)
    world.mutate.clear()                                                # run incremental: ya no ve esas fechas, pero el motivo se arrastra
    out2, _ = gen(tmp_path, "s2", prev=out1, now=NOW + timedelta(hours=6), registry=REG_CL)
    assert jl(out2, "health.json")["problems"]["dropped_bars"]["CL=F"]["reasons"] == want


def test_barras_anotadas_antes_de_registrar_el_motivo_salen_como_unknown(world, tmp_path):
    world.mutate["CL=F"] = lambda df: setv(df, "2020-04-20", Close=-37.63)
    out1, _ = gen(tmp_path, "s1", registry=REG_CL)
    import json
    hp = os.path.join(str(out1), "v2", "health.json")
    h = json.load(open(hp))
    del h["symbols"]["CL=F"]["dropped_why"]                              # estado de un sitio publicado por la versión anterior
    json.dump(h, open(hp, "w"))
    world.end = date(2026, 10, 27)
    world.mutate.clear()
    out2, _ = gen(tmp_path, "s2", prev=out1, now=NOW + timedelta(hours=6), registry=REG_CL)
    assert jl(out2, "health.json")["problems"]["dropped_bars"]["CL=F"]["reasons"] == {"2020-04-20": "unknown"}
