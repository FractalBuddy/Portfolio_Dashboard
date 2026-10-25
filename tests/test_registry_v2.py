"""Feed v2, paso 1: registro de símbolos (registry.json) y reglas compartidas (símbolo, divisa/unidad, rutas)."""
import copy
import json
import pathlib
import re

import pytest

import feed_v2_common as c

ROOT = pathlib.Path(__file__).resolve().parent.parent
REGISTRY = ROOT / "registry.json"


@pytest.fixture(scope="module")
def reg():
    return c.load_registry(REGISTRY)


def doc(*rows):
    return {"schema": 1, "symbols": list(rows)}


def row(**kw):
    r = {"symbol": "AAPL", "kind": "yahoo", "name": "Apple", "currency": "USD", "unit": "currency",
         "tier": "hot", "status": "active", "fail_streak": 0}
    r.update(kw)
    return r


# ------------------------------------------------------------------ el registro del repo
def test_el_registro_del_repo_es_valido_y_del_tamano_previsto(reg):
    assert 150 <= len(reg) <= 250
    assert {r["kind"] for r in reg} == {"yahoo", "crypto"}
    tiers = {t: sum(1 for r in reg if r["tier"] == t) for t in c.TIERS}
    assert all(tiers[t] > 0 for t in c.TIERS) and tiers["hot"] < tiers["warm"]       # hot = lo que se refresca 4 veces al día: pocos
    assert all(r["status"] == "active" and r["fail_streak"] == 0 for r in reg)       # el estado en marcha vive en health.json, no aquí


def test_todos_los_simbolos_son_validos_y_unicos(reg):
    syms = [r["symbol"] for r in reg]
    assert all(c.safe_symbol(s) for s in syms)
    assert len({s.upper() for s in syms}) == len(syms)


def test_las_unidades_son_coherentes_con_la_divisa(reg):
    for r in reg:
        assert (r["unit"] == "subunit") == (r["currency"] in c.SUBUNIT_FAMILIES), r["symbol"]
    by = {r["symbol"]: r for r in reg}
    assert (by["SWDA.L"]["currency"], by["SWDA.L"]["unit"]) == ("GBp", "subunit")        # BE-01: peniques, no libras
    assert (by["EIMI.L"]["currency"], by["EIMI.L"]["unit"]) == ("USD", "currency")
    assert by["^GSPC"]["unit"] == "points" and by["EURUSD=X"]["unit"] == "rate"
    assert all(r["currency"] == "USD" and r["unit"] == "currency" for r in reg if r["kind"] == "crypto")


def test_el_registro_publico_no_lleva_nada_del_dueno(reg):
    """Sin ISIN, sin los proxies de sus fondos clase S y sin la lista cripto idiosincrática. Los identificadores se leen del código v1 EN
    EL TEST (no se repiten aquí): este test no contiene ningún dato del dueño."""
    import fetch_market_data as fm
    text = REGISTRY.read_text(encoding="utf-8")
    assert not re.search(r"\b[A-Z]{2}[A-Z0-9]{9}[0-9]\b", text), "hay algo con forma de ISIN"
    for f in fm.YOUR_FUNDS:
        assert f["isin"] not in text and f["proxy_symbol"] not in text
        assert f["name"] not in text
    names = " ".join(r["name"].lower() for r in reg)
    for kw in ("unibright", "rocket pool", "tezos", "mina"):
        assert kw not in names
    # símbolos del universo v1 que no son de mercado general (small caps de nicho): fuera del registro público
    general = {s for s, _ in fm.STOCKS[:10]}                      # las 10 primeras de v1 son grandes caps generales
    niche = {s for s, _ in fm.STOCKS} - general
    in_reg = {r["symbol"] for r in reg}
    assert {"IPGP", "NOVT", "AMS.SW", "AMP.MC", "NXT.MC"} & in_reg == set()
    assert len(in_reg & niche) < len(niche)                       # el registro no es el universo v1 copiado


# ------------------------------------------------------------------ símbolo
@pytest.mark.parametrize("sym", ["AAPL", "^GSPC", "BRK-B", "EURUSD=X", "DX-Y.NYB", "SAN.MC", "000001.SS", "A", "A" * 15])
def test_simbolos_validos(sym):
    assert c.safe_symbol(sym)


@pytest.mark.parametrize("sym", ["", ".", "..", ".DJI", "A..B", "A" * 16, "A/B", "A B", "A\n", "A;B", "A%2F", "a\\b", "<x>", "Ñ", None, 5, "../x"])
def test_simbolos_invalidos(sym):
    assert not c.safe_symbol(sym)


def test_quote_symbol_codifica_siempre_y_no_acepta_basura():
    assert c.quote_symbol("^GSPC") == "%5EGSPC" and c.quote_symbol("EURUSD=X") == "EURUSD%3DX" and c.quote_symbol("AAPL") == "AAPL"
    with pytest.raises(ValueError):
        c.quote_symbol("A&s=EVIL")


# ------------------------------------------------------------------ divisa / unidad (BE-01)
def test_gbp_y_gbp_no_son_la_misma_unidad():
    assert c.currency_family("GBp") == ("GBP", 100) and c.currency_family("GBP") == ("GBP", 1)
    assert not c.same_unit("GBp", "GBP") and not c.same_unit("USd", "USD")
    assert c.same_unit("GBp", "GBX") and c.same_unit("USd", "USX")                       # dos grafías de la misma subunidad
    assert c.price_unit_divisor("GBp", "subunit") == 100 and c.price_unit_divisor("GBP", "currency") == 1
    assert c.price_unit_divisor("GBp", "points") == 1


@pytest.mark.parametrize("bad", [
    row(currency="GBp"),                                         # peniques declarados como unidad mayor: el error x100
    row(currency="GBP", unit="subunit"),                         # libras declaradas como subunidad
    row(unit="porcentaje"), row(currency="US"), row(currency="USDDD"), row(currency=None),
    row(kind="otro"), row(tier="tibio"), row(status="muerto"), row(fail_streak=-1), row(fail_streak=True), row(fail_streak="0"),
    row(symbol=".."), row(symbol="A/B"), row(name="<b>Apple</b>"), row(name=""), row(name="x" * 81), row(name="a\x00b"),
])
def test_registro_rechaza_filas_malas(bad):
    assert c.registry_problems(doc(bad))


def test_registro_rechaza_duplicados_campos_extra_o_faltantes_y_raiz_rara():
    assert c.registry_problems(doc(row(), row(symbol="aapl")))                    # repetido sin distinguir mayúsculas
    assert c.registry_problems(doc({**row(), "owner": "x"}))
    r = row(); del r["tier"]
    assert c.registry_problems(doc(r))
    assert c.registry_problems({"schema": 1, "symbols": [row()], "extra": 1})
    assert c.registry_problems({"schema": 2, "symbols": [row()]})
    assert c.registry_problems({"schema": 1, "symbols": []})
    assert c.registry_problems([])
    assert c.registry_problems(doc(*[row(symbol="S%d" % i) for i in range(c.MAX_REGISTRY_SYMBOLS + 1)]))
    assert c.registry_problems(doc(row())) == []


def test_load_registry_lista_los_problemas(tmp_path):
    p = tmp_path / "r.json"
    p.write_text(json.dumps(doc(row(currency="GBp"), row(symbol="..", name="n"))))
    with pytest.raises(c.RegistryError) as e:
        c.load_registry(p)
    assert "no es coherente" in str(e.value) and "símbolo no admisible" in str(e.value)
    p.write_text("{no json")
    with pytest.raises(c.RegistryError):
        c.load_registry(p)
    with pytest.raises(c.RegistryError):
        c.load_registry(tmp_path / "no_existe.json")


# ------------------------------------------------------------------ rutas
def test_rutas_del_contrato():
    assert c.latest_path("^GSPC") == "s/^GSPC/latest.json"
    assert c.year_path("AAPL", 2025) == "s/AAPL/d/2025.json" and c.intraday_path("AAPL") == "s/AAPL/i.json"
    with pytest.raises(AssertionError):
        c.latest_path("../x")


@pytest.mark.parametrize("rel,expected", [
    ("index.json", ("index", None, None)), ("manifest.json", ("manifest", None, None)), ("macro.json", ("macro", None, None)),
    ("news.json", ("news", None, None)), ("health.json", ("health", None, None)), ("crypto/latest.json", ("crypto", None, None)),
    ("s/AAPL/latest.json", ("latest", "AAPL", None)), ("s/^GSPC/d/2024.json", ("daily", "^GSPC", 2024)),
    ("s/EURUSD=X/i.json", ("intraday", "EURUSD=X", None)),
])
def test_classify_path_acepta_el_contrato(rel, expected):
    assert c.classify_path(rel) == expected


@pytest.mark.parametrize("rel", [
    "", "x.json", "s/../latest.json", "s/.x/latest.json", "s/A/d/24.json", "s/A/d/1800.json", "s/A/d/2024.json.bak", "/index.json",
    "s/A/latest.json/", "s\\A\\latest.json", "S/A/latest.json", "index.html", "../index.json", "s/A B/latest.json", "s/A/d/2024.json\n", None, 7,
])
def test_classify_path_rechaza_el_resto(rel):
    assert c.classify_path(rel) is None


def test_dumps_es_determinista_y_no_admite_nan():
    a = {"b": 1.5, "a": [1, 2], "n": "ñ"}
    assert c.dumps(a) == c.dumps(copy.deepcopy(a)) == '{"b":1.5,"a":[1,2],"n":"\\u00f1"}'
    with pytest.raises(ValueError):
        c.dumps({"x": float("nan")})
