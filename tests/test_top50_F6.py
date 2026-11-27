"""F6: la lista «top 50 por capitalización» completa y fechada (`top50-mcap@2026-11-27`), publicada en `index.json.lists`.

Fuente: https://companiesmarketcap.com/ consultada el 27-11-2026 (la web no da fecha de corte: `as_of` = fecha de consulta). Lista, variantes y
decisiones: docs/universo/TOP50_MCAP_2026-11-27.md y auditorias/FIXES_F6_TOP50.md.

Punto 1: los 8 símbolos de la lista que no estaban en el registro (misma grafía) entran `dormant`, `warm`, con divisa y unidad deducidas del sufijo.
Sus nombres NO están verificados en Yahoo (la sonda informa el de Yahoo). Las variantes ya presentes de la misma empresa no se tocan."""
import json
import pathlib

import feed_v2_common as c

ROOT = pathlib.Path(__file__).resolve().parent.parent
REGISTRY = ROOT / "registry.json"

LID = "top50-mcap@2026-11-27"
SOURCE = "https://companiesmarketcap.com/"
# Los 50, en orden de rango, grafía de Yahoo, tal cual los dio el dueño.
TOP50 = [
    "AAPL", "MSFT", "NVDA", "GOOG", "AMZN", "TSM", "SPCX", "2222.SR", "META", "TSLA",
    "AVGO", "LLY", "BRK-B", "ANT", "WMT", "JPM", "005930.KS", "V", "MU", "AMD",
    "JNJ", "XOM", "MA", "000660.KS", "ABBV", "ASML", "601939.SS", "COST", "601288.SS", "KO",
    "CVX", "PG", "RO.SW", "PLTR", "TCEHY", "CSCO", "601988.SS", "HD", "1398.HK", "688825.SS",
    "MRK", "BAC", "GE", "ORCL", "UNH", "NVS", "PANW", "INTC", "PM", "AZN",
]
# Las que faltaban en el registro (misma grafía) -> (divisa, unidad) deducidas del sufijo: sin sufijo = EE. UU. (USD; TCEHY, NVS y AZN cotizan en
# EE. UU. en USD), `.SS` = Shanghái (CNY), `.SW` = SIX Suiza (CHF). Ninguna en subunidad.
NUEVAS_F6 = {
    "GOOG": ("USD", "currency"), "ANT": ("USD", "currency"), "601939.SS": ("CNY", "currency"), "RO.SW": ("CHF", "currency"),
    "TCEHY": ("USD", "currency"), "688825.SS": ("CNY", "currency"), "NVS": ("USD", "currency"), "AZN": ("USD", "currency"),
}
# Variantes de la misma empresa que ya estaban (otra línea, otra bolsa o ADR): no se renombran ni se tocan; decide el dueño.
VARIANTES = {"GOOG": "GOOGL", "RO.SW": "ROG.SW", "TCEHY": "0700.HK", "NVS": "NOVN.SW", "AZN": "AZN.L", "TSM": "2330.TW"}
F5_DORMANT = {"ENPH", "ORSTED.CO"}
FILAS_ANTES = 251


def _raw():
    return json.loads(REGISTRY.read_text(encoding="utf-8"))


def _rows():
    return c.load_registry(REGISTRY)


# ------------------------------------------------------------------ punto 1: filas nuevas
def test_la_lista_dada_tiene_50_simbolos_sin_repetir_y_validos():
    assert len(TOP50) == 50 and len({s.upper() for s in TOP50}) == 50
    assert all(c.safe_symbol(s) for s in TOP50)
    assert set(NUEVAS_F6) <= set(TOP50)


def test_las_filas_nuevas_estan_en_dormant_warm_con_divisa_y_unidad_del_sufijo():
    by = {r["symbol"]: r for r in _rows()}
    for sym, (cur, unit) in NUEVAS_F6.items():
        r = by[sym]
        assert (r["kind"], r["currency"], r["unit"], r["tier"], r["status"], r["fail_streak"]) == ("yahoo", cur, unit, "warm", "dormant", 0), sym
        assert 1 <= len(r["name"]) <= c.MAX_NAME_LEN and r["name"].isascii(), sym


def test_los_50_estan_en_el_registro_con_la_misma_grafia():
    syms = {r["symbol"] for r in _rows()}
    assert [s for s in TOP50 if s not in syms] == []


def test_tamano_del_registro_y_filas_dormant():
    rows = _rows()
    assert len(rows) == FILAS_ANTES + len(NUEVAS_F6)
    assert {r["symbol"] for r in rows if r["status"] == "dormant"} == F5_DORMANT | set(NUEVAS_F6)


def test_las_variantes_existentes_siguen_tal_cual_y_activas():
    """No se renombra ni se duplica: la variante sigue siendo una fila propia, activa, sin la anotación de la lista (no es el símbolo de la lista)."""
    by = {r["symbol"]: r for r in _rows()}
    for nueva, vieja in VARIANTES.items():
        assert nueva in by and vieja in by and nueva.upper() != vieja.upper(), nueva
        assert by[vieja]["status"] == "active" and "lists" not in by[vieja], vieja


def test_la_sonda_por_defecto_prueba_las_dormant_f5_y_f6():
    import symbol_probe as sp
    assert {e["symbol"] for e in sp.select(_rows())} == F5_DORMANT | set(NUEVAS_F6)


def test_e2e_las_filas_nuevas_no_se_piden_a_yahoo_ni_tienen_ficheros(world, tmp_path):
    from v2_world import REG, gen, jl
    nuevas = [dict(e) for e in _rows() if e["symbol"] in NUEVAS_F6]
    reg = [dict(e) for e in REG] + nuevas
    world.decl = {e["symbol"]: e for e in reg}
    out, _ = gen(tmp_path, "s1", registry=reg)
    assert not ({s for s, _p, _i in world.calls} & set(NUEVAS_F6))
    idx = {r["s"]: r for r in jl(out, "index.json")["symbols"]}
    for sym in NUEVAS_F6:
        assert idx[sym]["status"] == "dormant" and idx[sym]["as_of"] is None, sym
        assert not (out / "v2" / "s" / sym).exists(), sym
