"""F6: la lista «top 50 por capitalización» completa y fechada (`top50-mcap@2026-11-27`), publicada en `index.json.lists`.

Fuente: https://companiesmarketcap.com/ consultada el 27-11-2026 (la web no da fecha de corte: `as_of` = fecha de consulta). Lista, variantes y
decisiones: docs/universo/TOP50_MCAP_2026-11-27.md y auditorias/FIXES_F6_TOP50.md.

Punto 1: los 8 símbolos de la lista que no estaban en el registro (misma grafía) entran `dormant`, `warm`, con divisa y unidad deducidas del sufijo.
Sus nombres NO están verificados en Yahoo (la sonda informa el de Yahoo). Las variantes ya presentes de la misma empresa no se tocan."""
import json
import pathlib
from datetime import datetime, timezone

import pytest

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
# Reloj de los e2e: el de tests/v2_world.py (26-10-2026) es anterior a as_of y el validador rechaza fechas futuras (ver el control negativo).
NOW_F6 = datetime(2026, 11, 27, 12, 0, tzinfo=timezone.utc)


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


# ------------------------------------------------------------------ punto 2: anotación por fila y documento de la lista
OLD_LID = "top50-mcap@2026-11-13"
ANTES_ANOTADAS = {"SPCX", "2222.SR", "000660.KS", "601288.SS", "1398.HK", "601988.SS", "PM"}   # las 7 que llevaban la lista del 13-11


def test_las_50_filas_llevan_la_lista_nueva_con_su_rango_real():
    by = {r["symbol"]: r for r in _rows()}
    for pos, sym in enumerate(TOP50, 1):
        assert by[sym].get("lists") == {LID: pos}, sym


def test_ninguna_fila_ajena_lleva_la_lista_y_la_lista_antigua_ya_no_existe():
    rows = _rows()
    assert sorted(r["symbol"] for r in rows if LID in r.get("lists", {})) == sorted(TOP50)
    assert OLD_LID not in REGISTRY.read_text(encoding="utf-8")


def test_las_7_filas_de_la_lista_antigua_pasan_a_la_nueva_con_su_rango_real():
    by = {r["symbol"]: r for r in _rows()}
    rangos = {s: TOP50.index(s) + 1 for s in ANTES_ANOTADAS}
    assert rangos == {"SPCX": 7, "2222.SR": 8, "000660.KS": 24, "601288.SS": 29, "1398.HK": 39, "601988.SS": 37, "PM": 49}
    for sym, pos in rangos.items():
        assert by[sym]["lists"] == {LID: pos} and by[sym]["status"] == "active", sym


def test_el_documento_de_la_lista_nueva_trae_los_50_en_orden_con_la_fuente_y_el_antiguo_esta_sustituido():
    new = (ROOT / "docs" / "universo" / "TOP50_MCAP_2026-11-27.md").read_text(encoding="utf-8")
    assert SOURCE in new and "27-11-2026" in new and LID in new
    filas = [ln for ln in new.splitlines() if ln.startswith("| ") and ln.split("|")[1].strip().isdigit()]
    got = [ln.split("|")[2].strip().strip("`") for ln in filas]
    assert got == TOP50
    assert [int(ln.split("|")[1]) for ln in filas] == list(range(1, 51))
    old = (ROOT / "docs" / "universo" / "TOP50_MCAP_2026-11-13.md").read_text(encoding="utf-8")
    assert "SUSTITUIDA" in old.splitlines()[2] and "TOP50_MCAP_2026-11-27.md" in old.splitlines()[2]


# ------------------------------------------------------------------ punto 3: definición en la raíz del registro
def test_la_raiz_del_registro_define_la_lista_completa_y_fechada():
    raw = _raw()
    assert raw["lists"] == {LID: {"source": SOURCE, "as_of": "2026-11-27", "size": 50, "symbols": TOP50}}
    assert list(raw) == ["schema", "symbols", "lists"]


def test_la_lista_real_pasa_list_problems_y_load_registry_doc_la_devuelve():
    raw = _raw()
    assert c.list_problems(raw["lists"], raw["symbols"]) == []
    assert c.registry_problems(raw) == []
    rows, lists = c.load_registry_doc(REGISTRY)
    assert list(lists) == [LID] and lists[LID]["symbols"] == TOP50 and len(rows) == FILAS_ANTES + len(NUEVAS_F6)


# ------------------------------------------------------------------ punto 4: publicación de extremo a extremo y controles negativos
def test_e2e_el_indice_generado_con_el_registro_real_lleva_la_lista_con_los_50_en_orden_y_el_validador_lo_acepta(world, tmp_path):
    """Registro real tal cual (259 filas, la lista de la raíz), Yahoo simulado: `index.json.lists` es exactamente la lista, con los 50 en orden de
    rango; los 50 aparecen en `index.json.symbols` (las `dormant` con `as_of: null`) y el validador del job `publish` acepta el sitio."""
    import os

    import sanity_check_v2 as sc
    from v2_world import gen, jl
    rows, lists = c.load_registry_doc(REGISTRY)
    world.decl = {e["symbol"]: e for e in rows}
    world.end = NOW_F6.date()
    out, _ = gen(tmp_path, "s1", now=NOW_F6, registry=rows, lists=lists)
    idx = jl(out, "index.json")
    assert idx["lists"] == {LID: {"id": LID, "source": SOURCE, "as_of": "2026-11-27", "symbols": TOP50}}
    assert list(idx["lists"][LID]["symbols"]) == TOP50
    by = {r["s"]: r for r in idx["symbols"]}
    assert set(TOP50) <= set(by)
    for sym in NUEVAS_F6:
        assert by[sym]["status"] == "dormant" and by[sym]["as_of"] is None, sym
    assert all("lists" not in r for r in idx["symbols"])                                  # la anotación por fila no se publica
    errs, _w = sc.check_site(os.path.join(out, "v2"), rows, None, today=NOW_F6.date(), lists=lists)
    assert errs == []


def test_control_negativo_la_lista_no_se_publica_antes_de_su_fecha(world, tmp_path):
    """El validador no admite fechas a más de MAX_FUTURE_DAYS (1) de «hoy». Con el reloj del mundo simulado (26-10-2026, un mes antes de as_of) el
    ÚNICO error del sitio es el `as_of` de la lista: una lista fechada en el futuro no se publica. (En producción «hoy» ya es >= 27-11-2026.)"""
    import os

    import sanity_check_v2 as sc
    from v2_world import NOW, gen
    rows, lists = c.load_registry_doc(REGISTRY)
    world.decl = {e["symbol"]: e for e in rows}
    out, _ = gen(tmp_path, "s1", registry=rows, lists=lists)
    errs, _w = sc.check_site(os.path.join(out, "v2"), rows, None, today=NOW.date(), lists=lists)
    assert errs == ["index.json: lists: %s: as_of: fecha fuera de rango" % LID]
    assert (datetime(2026, 11, 27).date() - NOW.date()).days > sc.MAX_FUTURE_DAYS


def test_e2e_main_de_generador_y_validador_con_el_fichero_real(world, tmp_path):
    """El camino de la CI: `feed_v2.main --registry registry.json` y `sanity_check_v2.main --registry registry.json` (el job `publish` saca el
    registro del repo) publican y aceptan la lista."""
    import feed_v2 as f2
    import sanity_check_v2 as sc
    from v2_world import jl
    world.decl = {e["symbol"]: e for e in _rows()}
    world.end = NOW_F6.date()
    orig = f2.datetime

    class _DT(orig):
        @classmethod
        def now(cls, tz=None):
            return NOW_F6
    f2.datetime = _DT
    try:
        assert f2.main(["--registry", str(REGISTRY), "--out", str(tmp_path / "o" / "v2")]) == 0
    finally:
        f2.datetime = orig
    assert jl(tmp_path / "o", "index.json")["lists"][LID]["symbols"] == TOP50
    assert sc.main(["--site", str(tmp_path / "o" / "v2"), "--registry", str(REGISTRY), "--today", NOW_F6.date().isoformat()]) == 0


def _bad(fn):
    raw = _raw()
    fn(raw)
    return raw


def _drop_row(sym):
    def f(raw):
        raw["symbols"] = [r for r in raw["symbols"] if r["symbol"] != sym]
    return f


def _set_last(sym):
    def f(raw):
        raw["lists"][LID]["symbols"][-1] = sym
    return f


def _drop_last_member(raw):
    gone = raw["lists"][LID]["symbols"].pop()
    for r in raw["symbols"]:
        if r["symbol"] == gone:
            del r["lists"]                                    # sin la anotación huérfana: el único problema es el tamaño


NEGATIVOS = {
    "simbolo_quitado_del_registro": (_drop_row("ANT"), ["lists[%s]: el símbolo ANT no está en el registro" % LID]),
    "49_simbolos": (_drop_last_member, ["lists[%s]: lista incompleta (49 de 50 símbolos)" % LID]),
    "grafia_distinta": (lambda raw: raw["lists"][LID]["symbols"].__setitem__(13, "ant"), ["lists[%s]: el símbolo ant no está en el registro" % LID]),
    "fuente_http": (lambda raw: raw["lists"][LID].__setitem__("source", "http://companiesmarketcap.com/"),
                    ["lists[%s]: source ausente o no es una URL https" % LID]),
    "duplicado": (_set_last("AAPL"), ["lists[%s]: símbolo repetido AAPL" % LID, "lists[%s]: AZN lleva la anotación de la lista pero no está en ella" % LID]),
    "duplicado_otra_caja": (_set_last("aapl"), ["lists[%s]: símbolo repetido aapl" % LID, "lists[%s]: AZN lleva la anotación de la lista pero no está en ella" % LID]),
}


def test_control_la_lista_real_sin_tocar_no_tiene_problemas():
    assert c.registry_problems(_raw()) == []


@pytest.mark.parametrize("case", sorted(NEGATIVOS))
def test_control_negativo_la_lista_real_estropeada_se_rechaza(case):
    fn, expected = NEGATIVOS[case]
    assert c.registry_problems(_bad(fn)) == expected


@pytest.mark.parametrize("case", sorted(NEGATIVOS))
def test_control_negativo_con_la_lista_estropeada_ni_el_generador_ni_el_validador_arrancan(case, tmp_path):
    """Lista mala = registro inválido: el generador no escribe nada y el validador del job `publish` no acepta el registro (no hay publicación a medias)."""
    import feed_v2 as f2
    import sanity_check_v2 as sc
    p = tmp_path / "r.json"
    p.write_text(json.dumps(_bad(NEGATIVOS[case][0])), encoding="utf-8")
    with pytest.raises(c.RegistryError):
        c.load_registry_doc(p)
    assert f2.main(["--registry", str(p), "--out", str(tmp_path / "o" / "v2")]) != 0
    assert not (tmp_path / "o" / "v2" / "manifest.json").exists()
    assert sc.main(["--site", str(tmp_path / "o" / "v2"), "--registry", str(p)]) != 0


# ------------------------------------------------------------------ punto 5: límites
def test_limites_el_registro_queda_bajo_el_80_por_ciento_de_su_tope():
    n = len(_rows())
    assert n == 259 and n / c.MAX_REGISTRY_SYMBOLS < 0.8                                  # 259 de 400 = 64,75 %; parar si pasara de 320
    assert c.MAX_REGISTRY_SYMBOLS == 400 and c.HEADROOM_WARN_FRACTION == 0.5               # el aviso al 50 % no cambia


def test_limites_las_filas_dormant_y_la_lista_no_anaden_ficheros_y_el_manifest_apenas_cambia(world, tmp_path):
    """Mismo mundo simulado, dos registros: el real (F6) y el real sin lo de F6 (sin las 8 filas nuevas, sin la lista de la raíz ni las anotaciones,
    que no se publican). Los ficheros publicados son los mismos; solo crecen index.json (8 filas + la lista) y health.json (las 8 en `dormant`).
    `V2_MEASURE=1 pytest -s -k limites_las_filas` imprime los bytes."""
    import os

    from v2_world import gen
    rows, lists = c.load_registry_doc(REGISTRY)
    antes = [{k: v for k, v in r.items() if k != "lists"} for r in rows if r["symbol"] not in NUEVAS_F6]
    world.decl = {e["symbol"]: e for e in rows}
    world.end = NOW_F6.date()
    o1, _ = gen(tmp_path, "antes", now=NOW_F6, registry=antes)
    o2, _ = gen(tmp_path, "despues", now=NOW_F6, registry=rows, lists=lists)

    def files(o):
        base = os.path.join(o, "v2")
        return {os.path.relpath(os.path.join(d, f), base): os.path.getsize(os.path.join(d, f)) for d, _s, fs in os.walk(base) for f in fs}
    f1, f2_ = files(o1), files(o2)
    assert set(f1) == set(f2_)
    changed = {k for k in f1 if f1[k] != f2_[k]}
    assert changed <= {"index.json", "health.json", "manifest.json", c.MANIFEST_ROOT}, changed
    d_index = f2_["index.json"] - f1["index.json"]
    d_lists = len(c.dumps({"lists": c.index_lists(lists)}).encode()) - len("{}")
    assert 0 < d_lists < d_index < 2500
    assert abs(f2_["manifest.json"] - f1["manifest.json"]) <= 4 and f2_["index.json"] < c.MAX_BYTES["index"] / 2
    if os.environ.get("V2_MEASURE"):
        print("\n  ficheros %d -> %d; index.json %d -> %d B (+%d; lista ~%d); health.json %d -> %d B; manifest.json %d -> %d B (tope %d)" % (
            len(f1), len(f2_), f1["index.json"], f2_["index.json"], d_index, d_lists, f1["health.json"], f2_["health.json"],
            f1["manifest.json"], f2_["manifest.json"], c.MAX_BYTES["manifest"]))
