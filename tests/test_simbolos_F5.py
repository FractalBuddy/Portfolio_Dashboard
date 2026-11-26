"""F5: símbolos nuevos del registro público, en `dormant` hasta pasar la sonda `symbol-probe.yml`.

De los 11 pedidos solo entran 2 (ENPH, ORSTED.CO). Los demás no, por privacidad (repo público) o porque ya estaban; el motivo está en
docs/F5_LISTAS_Y_SIMBOLOS.md y lo vigilan los guardias de tests/test_registry_v2.py (sin repetir aquí ningún dato del dueño)."""
import json
import pathlib

import feed_v2_common as c

ROOT = pathlib.Path(__file__).resolve().parent.parent
REGISTRY = ROOT / "registry.json"
# símbolo -> (divisa, unidad) derivadas del sufijo: sin sufijo = EE. UU. (USD); `.CO` = Nasdaq Copenhague (DKK). Ninguna cotiza en subunidad.
NUEVAS_F5 = {"ENPH": ("USD", "currency"), "ORSTED.CO": ("DKK", "currency")}
FILAS_ANTES = 249


def _rows():
    return c.load_registry(REGISTRY)


def test_las_filas_f5_estan_en_dormant_con_divisa_y_unidad_del_sufijo():
    by = {r["symbol"]: r for r in _rows()}
    for sym, (cur, unit) in NUEVAS_F5.items():
        r = by[sym]
        assert (r["kind"], r["currency"], r["unit"], r["tier"], r["status"], r["fail_streak"]) == ("yahoo", cur, unit, "warm", "dormant", 0), sym
        assert "lists" not in r and "country" not in r, sym
        assert 1 <= len(r["name"]) <= c.MAX_NAME_LEN and r["name"].isascii(), sym


def test_tamano_del_registro_y_solo_estas_filas_estan_en_dormant():
    rows = _rows()
    assert len(rows) == FILAS_ANTES + len(NUEVAS_F5)
    assert {r["symbol"] for r in rows if r["status"] == "dormant"} == set(NUEVAS_F5)


def test_la_sonda_por_defecto_prueba_exactamente_las_filas_f5():
    import symbol_probe as sp
    assert {e["symbol"] for e in sp.select(_rows())} == set(NUEVAS_F5)


def test_ninguna_fila_f5_duplica_una_empresa_ya_presente():
    names = [r["name"].lower() for r in _rows()]
    for frag in ("enphase", "orsted"):
        assert sum(frag in n for n in names) == 1, frag


def test_e2e_las_filas_f5_no_se_piden_a_yahoo_ni_tienen_ficheros(world, tmp_path):
    from v2_world import REG, gen, jl
    nuevas = [dict(e) for e in _rows() if e["symbol"] in NUEVAS_F5]
    reg = [dict(e) for e in REG] + nuevas
    world.decl = {e["symbol"]: e for e in reg}
    out, _ = gen(tmp_path, "s1", registry=reg)
    assert not ({s for s, _p, _i in world.calls} & set(NUEVAS_F5))
    idx = {r["s"]: r for r in jl(out, "index.json")["symbols"]}
    for sym in NUEVAS_F5:
        assert idx[sym]["status"] == "dormant" and idx[sym]["as_of"] is None, sym
        assert not (out / "v2" / "s" / sym).exists(), sym


def test_control_negativo_una_fila_f5_activa_si_se_pediria(world, tmp_path):
    """Contraprueba del test anterior: el estado `dormant` es lo único que las mantiene fuera (activadas sin sonda, se piden a Yahoo)."""
    from v2_world import REG, gen
    reg = [dict(e) for e in REG] + [dict(e, status="active") for e in _rows() if e["symbol"] in NUEVAS_F5]
    world.decl = {e["symbol"]: e for e in reg}
    gen(tmp_path, "s1", registry=reg)
    assert set(NUEVAS_F5) <= {s for s, _p, _i in world.calls}


def test_el_texto_del_registro_sigue_sin_isin_ni_urls():
    text = REGISTRY.read_text(encoding="utf-8")
    raw = json.loads(text)
    assert "lists" not in raw
    for bad in ("http", "supabase", "/products/", "sb_", "token"):
        assert bad not in text.lower(), bad
