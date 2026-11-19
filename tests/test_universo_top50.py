"""Universo top 50: metadatos opcionales del registro (`country`, `lists`), reglas para ampliar el universo añadiendo filas y controles negativos
(duplicado, símbolo inválido, divisa/unidad ausente o incoherente). La lista y sus fuentes: docs/universo/TOP50_MCAP_2026-11-13.md."""
import copy
import json
import pathlib
import re

import pytest

import feed_v2_common as c

ROOT = pathlib.Path(__file__).resolve().parent.parent
REGISTRY = ROOT / "registry.json"
LIST_ID = "top50-mcap@2026-11-13"
# Las 7 filas que añade esta tanda (empresas del top 50 que faltaban), con su divisa y unidad esperadas. Ninguna cotiza en subunidad.
NUEVAS = {
    "SPCX": ("USD", "currency", "US"), "2222.SR": ("SAR", "currency", "SA"), "000660.KS": ("KRW", "currency", "KR"),
    "1398.HK": ("HKD", "currency", "CN"), "601288.SS": ("CNY", "currency", "CN"), "601988.SS": ("CNY", "currency", "CN"),
    "PM": ("USD", "currency", "US"),
}


def doc(*rows):
    return {"schema": 1, "symbols": list(rows)}


def row(**kw):
    r = {"symbol": "AAPL", "kind": "yahoo", "name": "Apple", "currency": "USD", "unit": "currency",
         "tier": "warm", "status": "active", "fail_streak": 0}
    r.update(kw)
    return r


@pytest.fixture(scope="module")
def raw():
    return json.loads(REGISTRY.read_text(encoding="utf-8"))


# ------------------------------------------------------------------ metadatos opcionales
def test_country_y_lists_son_opcionales_y_validos():
    assert c.registry_problems(doc(row())) == []                                            # sin metadatos: igual que antes
    assert c.registry_problems(doc(row(country="US", lists={LIST_ID: 1}))) == []
    assert c.registry_problems(doc(row(country="SA", lists={LIST_ID: None}))) == []          # miembro de la lista, rango sin confirmar
    assert c.registry_problems(doc(row(lists={LIST_ID: 50, "top100-mcap@2027-01-31": 77}))) == []


@pytest.mark.parametrize("bad", [
    {"country": "us"}, {"country": "USA"}, {"country": ""}, {"country": None}, {"country": 1},
    {"lists": []}, {"lists": {}}, {"lists": None}, {"lists": "top50"},
    {"lists": {"top50-mcap": 1}},                                   # sin fecha de corte
    {"lists": {"top50-mcap@2026-13-01": 1}},                        # fecha imposible
    {"lists": {"Top50@2026-11-13": 1}},                             # mayúsculas
    {"lists": {"top50 mcap@2026-11-13": 1}},                        # espacio
    {"lists": {"<x>@2026-11-13": 1}},
    {"lists": {LIST_ID: 0}}, {"lists": {LIST_ID: -3}}, {"lists": {LIST_ID: 1.5}}, {"lists": {LIST_ID: True}}, {"lists": {LIST_ID: "7"}},
    {"lists": {LIST_ID: 100001}},
    {"lists": {"l%d@2026-11-13" % i: 1 for i in range(c.MAX_LISTS_PER_SYMBOL + 1)}},
])
def test_metadatos_malos_se_rechazan(bad):
    assert c.registry_problems(doc(row(**bad)))


def test_un_campo_desconocido_sigue_rechazado():
    """Control negativo: abrir dos campos opcionales no abre la puerta a cualquier campo (p. ej. un ISIN o una URL)."""
    for k in ("isin", "url", "owner", "rank", "list"):
        assert c.registry_problems(doc({**row(), k: "x"})), k


def test_load_registry_acepta_los_metadatos_y_los_devuelve():
    entries = c.load_registry(REGISTRY)
    by = {e["symbol"]: e for e in entries}
    for sym, (_cur, _unit, country) in NUEVAS.items():
        assert by[sym]["country"] == country and LIST_ID in by[sym]["lists"], sym


# ------------------------------------------------------------------ controles negativos pedidos (registro real + 1 fila mala)
def test_registro_real_con_un_simbolo_duplicado_falla(raw, tmp_path):
    bad = copy.deepcopy(raw)
    dup = dict(next(r for r in bad["symbols"] if r["symbol"] == "SPCX"))
    dup["symbol"] = "spcx"                                                                   # mismo símbolo, otra caja
    bad["symbols"].append(dup)
    p = tmp_path / "r.json"
    p.write_text(json.dumps(bad), encoding="utf-8")
    with pytest.raises(c.RegistryError, match="repetido"):
        c.load_registry(p)


@pytest.mark.parametrize("sym", ["2222 .SR", "../SPCX", "A" * 16, "SPCX;rm", "", "ÑAPA"])
def test_registro_real_con_un_simbolo_invalido_falla(raw, tmp_path, sym):
    bad = copy.deepcopy(raw)
    bad["symbols"].append(row(symbol=sym, name="x"))
    p = tmp_path / "r.json"
    p.write_text(json.dumps(bad), encoding="utf-8")
    with pytest.raises(c.RegistryError, match="no admisible"):
        c.load_registry(p)


@pytest.mark.parametrize("drop", ["currency", "unit"])
def test_registro_real_con_divisa_o_unidad_ausente_falla_y_nada_se_genera(raw, tmp_path, drop):
    """Sin `currency` o sin `unit` la fila no es válida: el generador no arranca (no publica nada a medias) y el validador del job `publish`
    tampoco acepta el registro. La CI lo atrapa antes del merge (test del registro real)."""
    import feed_v2 as f2
    import sanity_check_v2 as sc
    bad = copy.deepcopy(raw)
    r = next(x for x in bad["symbols"] if x["symbol"] == "PM")
    del r[drop]
    p = tmp_path / "r.json"
    p.write_text(json.dumps(bad), encoding="utf-8")
    with pytest.raises(c.RegistryError, match="faltan campos"):
        c.load_registry(p)
    assert f2.main(["--registry", str(p), "--out", str(tmp_path / "o" / "v2")]) != 0
    assert not (tmp_path / "o" / "v2" / "manifest.json").exists()
    assert sc.main(["--site", str(tmp_path / "o" / "v2"), "--registry", str(p)]) != 0


@pytest.mark.parametrize("cur,unit", [("GBp", "currency"), ("GBP", "subunit"), ("ZAc", "currency"), ("USd", "currency"), ("", "currency"), ("SAR", "")])
def test_divisa_y_unidad_incoherentes_se_rechazan(cur, unit):
    assert c.registry_problems(doc(row(symbol="X.L", currency=cur, unit=unit)))


# ------------------------------------------------------------------ las filas nuevas
def test_las_filas_nuevas_estan_y_son_coherentes(raw):
    by = {r["symbol"]: r for r in raw["symbols"]}
    for sym, (cur, unit, country) in NUEVAS.items():
        r = by[sym]
        assert (r["kind"], r["currency"], r["unit"], r["country"]) == ("yahoo", cur, unit, country), sym
        assert r["tier"] == "warm" and r["fail_streak"] == 0, sym
        assert r["status"] == "dormant", sym                                               # no se piden ni se publican hasta pasar la sonda
        assert c.SYMBOL_RE.fullmatch(sym) and re.fullmatch(r"^[A-Za-z0-9.^=\-]{1,15}$", sym)
        assert list(r["lists"]) == [LIST_ID]


def test_solo_las_filas_nuevas_llevan_metadatos_y_estan_en_dormant(raw):
    """Las filas existentes no se han tocado: ni metadatos ni estado distinto de `active`."""
    for r in raw["symbols"]:
        if r["symbol"] in NUEVAS:
            continue
        assert "country" not in r and "lists" not in r, r["symbol"]
        assert r["status"] == "active", r["symbol"]


def test_ningun_simbolo_nuevo_choca_con_uno_existente_ni_es_otra_linea_de_una_empresa_ya_presente(raw):
    syms = [r["symbol"].upper() for r in raw["symbols"]]
    assert len(syms) == len(set(syms))
    names = [r["name"].lower() for r in raw["symbols"]]
    for rx in (r"aramco", r"hynix", r"spacex", r"icbc", r"^agricultural bank of china", r"^bank of china", r"philip morris"):
        assert sum(bool(re.search(rx, n)) for n in names) == 1, rx


def test_e2e_filas_nuevas_activadas_divisa_distinta_no_se_publica_y_el_resto_si(world, tmp_path):
    """Con las filas reales ya activadas: si Yahoo informa otra divisa (Aramco en USD en vez de SAR) ese símbolo NO se publica y queda en
    health.json (unit_mismatch); el resto de nuevos sí, con su divisa/unidad del registro; y el validador del job `publish` acepta el sitio."""
    from datetime import timedelta

    import sanity_check_v2 as sc
    from v2_world import NOW, REG, gen, jl
    reg = [dict(e) for e in REG]
    by = {e["symbol"]: e for e in c.load_registry(REGISTRY)}
    for sym in NUEVAS:
        e = dict(by[sym], status="active")
        reg.append(e)
    world.decl = {e["symbol"]: e for e in reg}
    world.currency["2222.SR"] = "USD"
    out, run = gen(tmp_path, "s1", registry=reg)
    out2, _ = gen(tmp_path, "s2", prev=out, now=NOW + timedelta(hours=21), registry=reg)
    import os
    assert not os.path.exists(os.path.join(out2, "v2", "s", "2222.SR", "latest.json"))
    h = jl(out2, "health.json")
    assert h["problems"]["unit_mismatch"]["2222.SR"] == {"registro": "SAR/currency", "yahoo": "USD"} and h["problems"]["failed"]["2222.SR"] == "unit"
    for sym in set(NUEVAS) - {"2222.SR"}:
        lat = jl(out2, "s/%s/latest.json" % sym)
        assert (lat["currency"], lat["unit"], lat["price_unit_divisor"]) == (NUEVAS[sym][0], "currency", 1), sym
        assert "country" not in lat and "lists" not in lat                              # los metadatos no se publican
    idx = {r["s"]: r for r in jl(out2, "index.json")["symbols"]}
    assert set(idx["PM"]) == {"s", "kind", "name", "currency", "unit", "tier", "as_of", "status"}
    errs, _w = sc.check_site(os.path.join(out2, "v2"), reg, os.path.join(out, "v2"), today=(NOW + timedelta(hours=21)).date())
    assert errs == []


def test_e2e_en_dormant_no_se_piden_a_yahoo_ni_tienen_ficheros(world, tmp_path):
    from v2_world import REG, gen, jl
    reg = [dict(e) for e in REG] + [e for e in c.load_registry(REGISTRY) if e["symbol"] in NUEVAS]
    world.decl = {e["symbol"]: e for e in reg}
    out, _ = gen(tmp_path, "s1", registry=reg)
    asked = {s for s, _p, _i in world.calls}
    assert not (asked & set(NUEVAS))
    idx = {r["s"]: r for r in jl(out, "index.json")["symbols"]}
    for sym in NUEVAS:
        assert idx[sym]["status"] == "dormant" and idx[sym]["as_of"] is None, sym


def test_el_registro_publico_sigue_sin_nada_personal(raw):
    text = REGISTRY.read_text(encoding="utf-8")
    assert not re.search(r"\b[A-Z]{2}[A-Z0-9]{9}[0-9]\b", text), "algo con forma de ISIN"
    for bad in ("supabase", "http", "/products/", "sb_", "token", "@gmail", "ishares.com"):
        assert bad not in text.lower(), bad
