"""F5: listas publicadas en `index.json` (`lists`: lista -> símbolos, con id fechado, fuente https y fecha).

La definición de una lista vive en la raíz de `registry.json` (clave opcional `lists`). Solo se publica si el validador del registro la da por
COMPLETA: tantos símbolos como `size`, todos en el registro, sin repetir, fuente https y fecha igual a la del id. Una lista incompleta, vacía o sin
fuente/fecha hace inválido el registro entero (CI en rojo, el generador y el validador del job `publish` no arrancan): no hay publicación a medias.
Las anotaciones por fila (`symbols[i].lists`) siguen siendo metadatos que NO se publican; la lista top 50 del 13-nov-2026 solo existe así (7 filas,
incompleta) y por eso no aparece en `index.json`. Informe: docs/F5_LISTAS_Y_SIMBOLOS.md."""
import copy
import json
import os
import pathlib
from datetime import timedelta

import pytest

import feed_v2_common as c

ROOT = pathlib.Path(__file__).resolve().parent.parent
REGISTRY = ROOT / "registry.json"
LID = "demo3@2026-11-20"
SRC = "https://example.org/lista-demo"


def row(sym, **kw):
    r = {"symbol": sym, "kind": "yahoo", "name": sym, "currency": "USD", "unit": "currency",
         "tier": "warm", "status": "active", "fail_streak": 0}
    r.update(kw)
    return r


def base_doc(**lists):
    rows = [row("AAA", lists={LID: 1}), row("BBB", lists={LID: 2}), row("CCC", lists={LID: 3}), row("DDD")]
    d = {"schema": 1, "symbols": rows}
    if lists:
        d["lists"] = lists
    return d


def good_list(**kw):
    l_ = {"source": SRC, "as_of": "2026-11-20", "size": 3, "symbols": ["AAA", "BBB", "CCC"]}
    l_.update(kw)
    return l_


# ------------------------------------------------------------------ registro: qué lista se acepta
def test_lista_completa_con_fuente_y_fecha_es_valida():
    assert c.registry_problems(base_doc(**{LID: good_list()})) == []


def test_sin_listas_el_registro_sigue_como_antes():
    assert c.registry_problems(base_doc()) == []
    assert c.index_lists({}) is None


@pytest.mark.parametrize("bad,frag", [
    (good_list(symbols=[], size=0), "size"),                                     # lista vacía
    (good_list(symbols=[]), "incompleta"),                                       # vacía con tamaño declarado
    (good_list(symbols=["AAA", "BBB"]), "incompleta"),                           # le falta un miembro
    (good_list(size=4), "incompleta"),
    ({k: v for k, v in good_list().items() if k != "source"}, "source"),        # sin fuente
    ({k: v for k, v in good_list().items() if k != "as_of"}, "as_of"),          # sin fecha
    (good_list(source=""), "source"), (good_list(source=None), "source"),
    (good_list(source="http://example.org/x"), "source"),                        # no https
    (good_list(source="ftp://example.org/x"), "source"),
    (good_list(source="https://example.org/<x>"), "source"),
    (good_list(source="https://exa mple.org"), "source"),
    (good_list(as_of="2026-11-21"), "as_of"),                                    # fecha distinta de la del id
    (good_list(as_of="20-11-2026"), "as_of"), (good_list(as_of=None), "as_of"),
    (good_list(symbols=["AAA", "BBB", "aaa"]), "repetido"),
    (good_list(symbols=["AAA", "BBB", "ZZZ"]), "no está en el registro"),        # símbolo fuera del registro
    (good_list(symbols=["AAA", "BBB", "../x"]), "no está en el registro"),
    (good_list(extra=1), "campos"),
    (good_list(size=True), "size"), (good_list(size="3"), "size"),
    ("x", "objeto"), (None, "objeto"),
])
def test_lista_mala_hace_invalido_el_registro(bad, frag):
    probs = c.registry_problems(base_doc(**{LID: bad}))
    assert probs and any(frag in p for p in probs), probs


@pytest.mark.parametrize("lid", ["demo3", "demo3@2026-13-01", "Demo3@2026-11-20", "<x>@2026-11-20", "demo 3@2026-11-20"])
def test_id_de_lista_sin_fecha_o_mal_formado_se_rechaza(lid):
    d = base_doc(**{lid: good_list()})
    for r in d["symbols"]:
        if "lists" in r:
            r["lists"] = {lid: r["lists"][LID]} if c.LIST_ID_RE.fullmatch(lid) else r["lists"]
    assert c.registry_problems(d)


def test_lists_de_la_raiz_mal_formado_se_rechaza():
    for v in ([], "x", None, {}, {("l%d@2026-11-20" % i): good_list() for i in range(c.MAX_LISTS + 1)}):
        d = base_doc()
        d["lists"] = v
        assert c.registry_problems(d), v


def test_anotacion_de_fila_y_lista_tienen_que_coincidir():
    """Un miembro de la lista sin la anotación en su fila, una fila anotada que no está en la lista o un rango distinto de su posición: error
    (las dos fuentes no pueden divergir sin que la CI lo vea)."""
    d = base_doc(**{LID: good_list()})
    del d["symbols"][2]["lists"]
    assert any("anotación" in p for p in c.registry_problems(d))
    d = base_doc(**{LID: good_list()})
    d["symbols"][3]["lists"] = {LID: None}
    assert any("anotación" in p for p in c.registry_problems(d))
    d = base_doc(**{LID: good_list()})
    d["symbols"][0]["lists"] = {LID: 2}
    assert any("rango" in p for p in c.registry_problems(d))
    d = base_doc(**{LID: good_list()})
    d["symbols"][0]["lists"] = {LID: None}                                         # rango sin confirmar en la fila: vale (la posición manda)
    assert c.registry_problems(d) == []


def test_load_registry_doc_devuelve_filas_y_listas(tmp_path):
    p = tmp_path / "r.json"
    p.write_text(json.dumps(base_doc(**{LID: good_list()})), encoding="utf-8")
    rows, lists = c.load_registry_doc(p)
    assert [r["symbol"] for r in rows] == ["AAA", "BBB", "CCC", "DDD"] and list(lists) == [LID]
    assert c.load_registry(p) == rows
    p.write_text(json.dumps(base_doc(**{LID: good_list(symbols=["AAA"])})), encoding="utf-8")
    with pytest.raises(c.RegistryError, match="incompleta"):
        c.load_registry_doc(p)


def test_index_lists_forma_publicada():
    assert c.index_lists({LID: good_list()}) == {LID: {"id": LID, "source": SRC, "as_of": "2026-11-20", "symbols": ["AAA", "BBB", "CCC"]}}


# ------------------------------------------------------------------ el registro real
def test_el_registro_real_no_publica_ninguna_lista_y_el_top50_sigue_solo_como_anotacion():
    """REQUISITO: el top 50 del 13-nov-2026 está INCOMPLETO (7 filas anotadas, sin top 50 verificado y fechado). No se define en la raíz y, por
    tanto, no se publica. Cuando llegue completo (fuente https + fecha + 50 símbolos) se añadirá en la raíz y este test cambiará a propósito."""
    raw = json.loads(REGISTRY.read_text(encoding="utf-8"))
    assert "lists" not in raw
    rows, lists = c.load_registry_doc(REGISTRY)
    assert lists == {}
    anotadas = [r["symbol"] for r in rows if "top50-mcap@2026-11-13" in r.get("lists", {})]
    assert 0 < len(anotadas) < 50


def test_control_negativo_el_top50_real_tal_cual_no_se_puede_publicar(tmp_path):
    """Si alguien intentara publicar el top 50 con lo que hay (las 7 filas anotadas), el registro se rechaza: está incompleto."""
    raw = json.loads(REGISTRY.read_text(encoding="utf-8"))
    tid = "top50-mcap@2026-11-13"
    members = [r["symbol"] for r in raw["symbols"] if tid in r.get("lists", {})]
    bad = copy.deepcopy(raw)
    bad["lists"] = {tid: {"source": "https://example.org/top50", "as_of": "2026-11-13", "size": 50, "symbols": members}}
    probs = c.registry_problems(bad)
    assert any("incompleta" in p for p in probs), probs


# ------------------------------------------------------------------ generador + validador del job publish
def _reg_world(world, lists=None):
    from v2_world import REG
    reg = [dict(e) for e in REG]
    syms = [e["symbol"] for e in reg if e["kind"] == "yahoo"][:3]
    lid = "demo3@2026-10-20"
    for i, e in enumerate(reg):
        if e["symbol"] in syms:
            e["lists"] = {lid: syms.index(e["symbol"]) + 1}
    world.decl = {e["symbol"]: e for e in reg}
    if lists is None:
        lists = {lid: {"source": SRC, "as_of": "2026-10-20", "size": 3, "symbols": syms}}
    assert c.registry_problems({"schema": 1, "symbols": reg, "lists": lists}) == []
    return reg, lists, lid, syms


def test_e2e_lista_valida_se_publica_en_index_y_el_validador_la_acepta(world, tmp_path):
    import sanity_check_v2 as sc
    from v2_world import NOW, gen, jl
    reg, lists, lid, syms = _reg_world(world)
    out, _ = gen(tmp_path, "s1", registry=reg, lists=lists)
    idx = jl(out, "index.json")
    assert idx["lists"] == {lid: {"id": lid, "source": SRC, "as_of": "2026-10-20", "symbols": syms}}
    assert all("lists" not in r for r in idx["symbols"])                                # las anotaciones por fila no se publican
    errs, _w = sc.check_site(os.path.join(out, "v2"), reg, None, today=NOW.date(), lists=lists)
    assert errs == []


def test_e2e_sin_listas_index_no_lleva_la_clave(world, tmp_path):
    import sanity_check_v2 as sc
    from v2_world import NOW, REG, gen, jl
    out, _ = gen(tmp_path, "s1")
    assert "lists" not in jl(out, "index.json")
    errs, _w = sc.check_site(os.path.join(out, "v2"), REG, None, today=NOW.date())
    assert errs == []


def _tamper_index(out, fn):
    p = os.path.join(out, "v2", "index.json")
    with open(p, encoding="utf-8") as f:
        idx = json.load(f)
    fn(idx)
    data = c.dumps(idx).encode("ascii")
    with open(p, "wb") as f:
        f.write(data)
    # el manifiesto ya no casa con el fichero; se rehace para que el único error sea el de las listas
    import hashlib
    for name in ("manifest.json", c.MANIFEST_ROOT):
        mp = os.path.join(out, "v2", name)
        with open(mp, encoding="utf-8") as f:
            m = json.load(f)
        old = m["files"]["index.json"]["bytes"]
        m["files"]["index.json"] = {"bytes": len(data), "sha256": hashlib.sha256(data).hexdigest()}
        m["total_bytes"] += len(data) - old
        with open(mp, "wb") as f:
            f.write(c.dumps(m).encode("ascii"))


@pytest.mark.parametrize("case", ["lista_no_registrada", "simbolo_cambiado", "fuente_http", "lista_quitada"])
def test_control_negativo_el_validador_rechaza_listas_que_no_son_las_del_registro(world, tmp_path, case):
    """El job `fetch` ejecuta código de terceros: el validador no se fía de su index.json y exige que `lists` sea EXACTAMENTE lo que valida el
    registro del repo (nada más, nada menos, nada cambiado)."""
    import sanity_check_v2 as sc
    from v2_world import NOW, gen
    reg, lists, lid, syms = _reg_world(world)
    out, _ = gen(tmp_path, "s1", registry=reg, lists=lists)
    other = [e["symbol"] for e in reg if e["kind"] == "yahoo" and e["symbol"] not in syms][0]
    fns = {
        "lista_no_registrada": lambda i: i["lists"].update({"otra@2026-10-20": {"id": "otra@2026-10-20", "source": SRC, "as_of": "2026-10-20",
                                                                                 "symbols": syms[:1]}}),
        "simbolo_cambiado": lambda i: i["lists"][lid]["symbols"].__setitem__(2, other),
        "fuente_http": lambda i: i["lists"][lid].__setitem__("source", "http://example.org/x"),
        "lista_quitada": lambda i: i.pop("lists"),
    }
    _tamper_index(out, fns[case])
    errs, _w = sc.check_site(os.path.join(out, "v2"), reg, None, today=NOW.date(), lists=lists)
    assert any("lists" in e for e in errs), errs


def test_control_negativo_index_con_listas_y_registro_sin_ellas(world, tmp_path):
    import sanity_check_v2 as sc
    from v2_world import NOW, gen
    reg, lists, _lid, _s = _reg_world(world)
    out, _ = gen(tmp_path, "s1", registry=reg, lists=lists)
    errs, _w = sc.check_site(os.path.join(out, "v2"), reg, None, today=NOW.date())       # el validador no tiene listas: no se publica ninguna
    assert any("lists" in e for e in errs), errs


def test_main_de_generador_y_validador_leen_las_listas_del_fichero(world, tmp_path):
    import feed_v2 as f2
    import sanity_check_v2 as sc
    from v2_world import NOW, jl
    reg, lists, lid, _s = _reg_world(world)
    p = tmp_path / "reg.json"
    p.write_text(json.dumps({"schema": 1, "symbols": reg, "lists": lists}), encoding="utf-8")
    import feed_v2_common  # noqa: F401
    orig = f2.datetime

    class _DT(orig):
        @classmethod
        def now(cls, tz=None):
            return NOW
    f2.datetime = _DT
    try:
        assert f2.main(["--registry", str(p), "--out", str(tmp_path / "o" / "v2")]) == 0
    finally:
        f2.datetime = orig
    assert jl(tmp_path / "o", "index.json")["lists"][lid]["source"] == SRC
    assert sc.main(["--site", str(tmp_path / "o" / "v2"), "--registry", str(p), "--today", (NOW + timedelta(hours=1)).date().isoformat()]) == 0
