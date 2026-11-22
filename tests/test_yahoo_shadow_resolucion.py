"""Sombra NAV Yahoo (F1), punto 2: configuración versionada y resolución ISIN -> `0P…` con el NOMBRE devuelto validado.
Todo sintético (tests/yahoo_shadow_fakes.py). D1 A: en la sombra la resolución es automática y NO se guarda; un candidato cuyo nombre no cuadra es
«sin emparejar» y no se compara; D9 A: solo `0P…` (un ETF cotizado no cuenta)."""
import json
import pathlib

import pytest

import yahoo_shadow as ys
from fund_probe import Gate
from yahoo_shadow_fakes import (ISIN_A, NAME_A, SYM_A, SYM_OTRA_CLASE, FakeTime, FakeYahoo, quote)

ROOT = pathlib.Path(__file__).resolve().parent.parent
CFG = ROOT / "config" / "yahoo_shadow.json"
GEN = ROOT / "config" / "fund_probe_isins_generic.json"


def _gate():
    t = FakeTime()
    return Gate(max_requests=60, clock=t.clock, sleep=t.sleep)


# ------------------------------------------------------------------ configuración
def test_la_configuracion_del_repo_es_valida_y_la_sombra_queda_encendida():
    cfg, problem = ys.load_config(CFG, GEN)
    assert problem is None and cfg["enabled"] is True
    assert cfg["min_business_days"] == 10 and cfg["equal_bp"] == 1 and cfg["max_bp"] == 10 and cfg["gate_equal_pct"] == 95       # D7 A
    assert cfg["slots_utc"] == [[5, 10], [19, 24]] and cfg["max_reads_per_day"] == 2                                              # D6 A
    assert cfg["history_period"] == "5d" and cfg["canary_name"]          # el nombre del canario sale de la lista genérica pública


@pytest.mark.parametrize("cambio", [
    {"enabled": "true"}, {"min_business_days": 0}, {"equal_bp": -1}, {"max_bp": 0.5}, {"slots_utc": [[10, 5]]}, {"slots_utc": [[0, 25]]},
    {"slots_utc": []}, {"max_reads_per_day": 9}, {"max_requests_per_run": 500}, {"history_period": "max"}, {"canary_isin": "IE0000000001"},
    {"clave_desconocida": 1}, {"gate_equal_pct": 101}, {"name_min_coverage": 0}, {"ledger_days": 3}, {"min_official_decimals": 9}])
def test_una_configuracion_invalida_se_rechaza_sin_detalles(tmp_path, cambio):
    data = json.loads(CFG.read_text(encoding="utf-8"))
    data.update(cambio)
    p = tmp_path / "c.json"
    p.write_text(json.dumps(data), encoding="utf-8")
    cfg, problem = ys.load_config(p, GEN)
    assert cfg is None and problem and "IE" not in problem


def test_falta_una_clave_o_el_fichero_no_es_json(tmp_path):
    data = json.loads(CFG.read_text(encoding="utf-8"))
    del data["enabled"]
    p = tmp_path / "c.json"
    p.write_text(json.dumps(data), encoding="utf-8")
    assert ys.load_config(p, GEN)[0] is None
    p.write_text("{no es json", encoding="utf-8")
    assert ys.load_config(p, GEN)[0] is None
    assert ys.load_config(tmp_path / "no_existe.json", GEN)[0] is None


# ------------------------------------------------------------------ validación de nombres
@pytest.mark.parametrize("yahoo", [
    NAME_A,
    "Zeta Quimera Dvlpd Wld Idx Fd (IE) S Acc EUR",                    # abreviaturas habituales de Morningstar
    "ZETA QUIMERA DEVELOPED WORLD INDEX FUND (IE) S ACC EUR",
])
def test_nombre_que_cuadra(yahoo):
    assert ys.name_match(NAME_A, yahoo, 0.6)[0] is True


@pytest.mark.parametrize("yahoo,motivo", [
    ("Zeta Quimera Developed World Small Cap Index Fund (IE) S Acc EUR", "alcance"),     # «MSCI World» frente a «World Small Cap»
    ("Zeta Quimera Developed World ESG Index Fund (IE) S Acc EUR", "alcance"),
    ("Zeta Quimera Developed World Index Fund (IE) S Dist EUR", "clase"),                # Acc frente a Dist
    ("Zeta Quimera Developed World Index Fund (IE) S Acc USD", "divisa"),                # otra divisa en el nombre
    ("Zeta Quimera Developed World Index Fund (IE) D Acc EUR", "letra"),                 # otra letra de clase
    ("Zeta Quimera Developed World Index Fund (IE) S Acc EUR Hedged", "cubierta"),
    ("Omega Pacific Tech Leaders", "cobertura"),                                         # otro fondo
    ("", "cobertura"),
])
def test_nombre_que_no_cuadra(yahoo, motivo):
    ok, why = ys.name_match(NAME_A, yahoo, 0.6)
    assert ok is False and why == motivo


# ------------------------------------------------------------------ resolución por ISIN
def _resolve(quotes, name=NAME_A):
    y = FakeYahoo(searches={ISIN_A: quotes})
    masks = []
    out = ys.resolve(ISIN_A, name, y, _gate(), masks.append, 0.6)
    return out, masks, y


def test_resuelve_el_0p_cuyo_nombre_cuadra_y_lo_enmascara_antes_de_usarlo():
    (status, sym), masks, y = _resolve([quote("ZQDW.DE", NAME_A, "ETF", "GER"), quote(SYM_A, "Zeta Quimera Dvlpd Wld Idx (IE) S Acc EUR", longname=NAME_A)])
    assert (status, sym) == ("ok", SYM_A)
    assert masks == [SYM_A] and y.calls == [("search", ISIN_A)]        # una sola petición: la búsqueda


def test_solo_etf_es_sin_0p():                                                         # D9 A
    (status, sym), _m, _y = _resolve([quote("ZQDW.DE", NAME_A, "ETF", "GER"), quote("ZQDW.L", NAME_A, "ETF", "LSE")])
    assert (status, sym) == ("sin_0p", None)


def test_busqueda_vacia_es_sin_0p():
    assert _resolve([])[0] == ("sin_0p", None)


def test_0p_con_nombre_que_no_cuadra_es_sin_emparejar_y_no_se_compara():
    (status, sym), masks, _y = _resolve([quote(SYM_A, "Zeta Quimera Developed World Small Cap Index Fund (IE) S Acc EUR")])
    assert (status, sym) == ("sin_emparejar", None)
    assert masks == [SYM_A]                                                             # también se enmascara lo que no se usa


def test_dos_0p_que_cuadran_igual_son_ambiguos():
    (status, sym), _m, _y = _resolve([quote(SYM_A, NAME_A), quote(SYM_OTRA_CLASE, NAME_A)])
    assert (status, sym) == ("ambiguo", None)


def test_entre_dos_0p_gana_el_unico_que_cuadra():
    (status, sym), _m, _y = _resolve([quote(SYM_OTRA_CLASE, "Zeta Quimera Developed World Index Fund (IE) D Acc EUR"), quote(SYM_A, NAME_A)])
    assert (status, sym) == ("ok", SYM_A)


@pytest.mark.parametrize("raro", ["0PFAKE0001", "0PFAKE0001.F/../x", "0PFAKE0001.F?q=1", "0pfake0001.f", "0PFAKE00012.F", "0PFAKE0001.FRAX"])
def test_un_simbolo_con_forma_rara_no_se_acepta(raro):
    """Regex cerrada ^0P[0-9A-Z]{8}\\.[A-Z]{1,3}$ (DISENO_NAV_YAHOO §3.3): sin '/', '?', '..', minúsculas ni sufijos largos."""
    assert _resolve([quote(raro, NAME_A)])[0] == ("sin_0p", None)


def test_resultados_basura_de_la_busqueda_no_rompen():
    assert _resolve([None, "x", {"symbol": 3}, {"quoteType": "MUTUALFUND"}, quote(SYM_A, NAME_A)])[0] == ("ok", SYM_A)
