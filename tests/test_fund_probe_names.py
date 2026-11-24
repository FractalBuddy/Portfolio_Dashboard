"""Sonda de fondos, punto 2: sección NOMBRES (regla de selección, medidas, ambigüedad, resumen por gestora, lista de nombres)."""
import json
import pathlib
import re

import pytest

import fund_probe as fp
from fund_probe_fakes import TODAY, FakeClient, FakeTime, quote, rows

ROOT = pathlib.Path(__file__).resolve().parent.parent
CFG = ROOT / "config" / "fund_probe_names.json"


def mk(max_requests=250):
    t = FakeTime()
    return fp.Gate(max_requests=max_requests, clock=t.clock, sleep=t.sleep)


def H(last="2026-11-13", cur="EUR", n=21):
    from datetime import date
    return {"rows": rows(date.fromisoformat(last), n), "currency": cur}


def entry(name, manager="Gestora A"):
    return {"manager": manager, "name": name}


# ------------------------------------------------------------------ regla de selección
def test_tokens_sin_acentos_ni_puntuacion_ni_palabras_vacias():
    assert fp.norm_tokens("Fidelity Funds - Global Technology Fund A-ACC-EUR (UCITS)") == {"fidelity", "global", "technology", "a", "acc", "eur"}
    assert fp.norm_tokens("Gestión Ibérica S&P 500") == {"gestion", "iberica", "s", "p", "500"}


def test_solo_fondos_y_etf_son_candidatos():
    qs = [quote("S1", "Alpha World Fund", "EQUITY"), quote("S2", "Alpha World Fund", "CURRENCY"), quote("S3", "Alpha World Fund", "MUTUALFUND"),
          quote("S4", "Alpha World Fund", "ETF"), {"symbol": "S5", "shortname": "Alpha World Fund"}]                # sin quoteType
    c = fp.fund_candidates("Alpha World Fund", qs)
    assert [x["symbol"] for x in c] == ["S3", "S4"]


def test_umbral_de_cobertura_60_por_ciento():
    q = "alpha beta gamma delta epsilon"                                   # 5 palabras
    ok = fp.fund_candidates(q, [quote("OK", "Alpha Beta Gamma Zeta")])      # 3/5 = 0.6 -> entra
    ko = fp.fund_candidates(q, [quote("KO", "Alpha Beta Zeta")])            # 2/5 = 0.4 -> no
    assert [x["symbol"] for x in ok] == ["OK"] and ok[0]["coverage"] == pytest.approx(0.6) and ko == []


def test_el_nombre_largo_tambien_cuenta():
    c = fp.fund_candidates("Alpha World Index", [quote("L1", "AWI", longname="Alpha World Index Fund Acc")])
    assert [x["symbol"] for x in c] == ["L1"]


def test_mejor_candidato_mayor_cobertura_y_empate_por_orden_de_yahoo():
    qs = [quote("B", "Alpha World"), quote("A", "Alpha World Index"), quote("C", "Alpha World Index")]
    c = fp.fund_candidates("Alpha World Index", qs)
    assert fp.pick_best(c)["symbol"] == "A"                                 # cobertura 1.0 gana a B (0.67); A y C empatan -> el primero que dio Yahoo


def test_candidatos_duplicados_por_simbolo_se_unen():
    c = fp.fund_candidates("Alpha World", [quote("A", "Alpha World"), quote("A", "Alpha World")])
    assert len(c) == 1


def test_candidatos_fuertes_son_los_cercanos_al_mejor_y_tope_de_3():
    qs = [quote(f"S{i}", "Alpha World Index") for i in range(5)] + [quote("W", "Alpha World")]
    c = fp.fund_candidates("Alpha World Index", qs)
    s = fp.strong_candidates(c)
    assert [x["symbol"] for x in s] == ["S0", "S1", "S2"]                    # W (0.67) queda a >0.1 del mejor; tope MAX_ALTS


def test_sin_candidatos_no_hay_mejor():
    assert fp.pick_best([]) is None and fp.strong_candidates([]) == []


# ------------------------------------------------------------------ una entrada de la lista
def run_one(client, name="Alpha World Fund", **kw):
    errors = fp.new_error_counter()
    res = fp.run_names([entry(name)], client, kw.pop("gate", None) or mk(), TODAY, errors, **kw)
    return res[0], errors


def test_encontrado_con_dato_reciente():
    c = FakeClient({"Alpha World Fund": [quote("0P00000AAA.F", "Alpha World Fund A", exch="FRA")]}, {"0P00000AAA.F": H("2026-11-13")})
    r, e = run_one(c)
    assert (r["status"], r["ticker"], r["exchange"], r["currency"], r["lag_days"], r["bars"], r["last_date"]) == \
        ("reciente", "0P00000AAA.F", "FRA", "EUR", 3, 21, "2026-11-13")
    assert r["shortname"] == "Alpha World Fund A" and r["quote_type"] == "MUTUALFUND" and r["ambiguous"] is False and sum(e.values()) == 0


def test_limite_del_retraso_3_dias_es_reciente_y_4_es_retrasado():
    c = FakeClient({"Alpha World Fund": [quote("T1", "Alpha World Fund")]}, {"T1": H("2026-11-13")})
    assert run_one(c)[0]["status"] == "reciente"
    c = FakeClient({"Alpha World Fund": [quote("T1", "Alpha World Fund")]}, {"T1": H("2026-11-12")})
    r, _ = run_one(c)
    assert r["status"] == "retrasado" and r["lag_days"] == 4


def test_sin_resultados_de_busqueda_es_no_encontrado_y_cuenta_vacio():
    r, e = run_one(FakeClient({"Alpha World Fund": []}))
    assert r["status"] == "no_encontrado" and r["ticker"] is None and e["vacio"] == 1


def test_resultados_que_no_son_fondos_es_no_encontrado():
    r, e = run_one(FakeClient({"Alpha World Fund": [quote("X", "Alpha World Fund", "EQUITY")]}))
    assert r["status"] == "no_encontrado" and e["vacio"] == 1


def test_encontrado_pero_historico_vacio_es_sin_serie():
    c = FakeClient({"Alpha World Fund": [quote("T1", "Alpha World Fund")]}, {"T1": {"rows": [], "currency": "EUR"}})
    r, e = run_one(c)
    assert r["status"] == "sin_serie" and r["ticker"] == "T1" and r["error"] == "vacio" and e["vacio"] == 1


def test_error_no_parador_en_busqueda_es_error_y_sigue():
    c = FakeClient({"Alpha World Fund": KeyError("x"), "Beta Fund": [quote("T2", "Beta Fund")]}, {"T2": H()})
    errors = fp.new_error_counter()
    res = fp.run_names([entry("Alpha World Fund"), entry("Beta Fund")], c, mk(), TODAY, errors)
    assert [r["status"] for r in res] == ["error", "reciente"] and res[0]["error"] == "parseo" and errors["parseo"] == 1


def test_divisa_rara_se_cuenta_y_se_conserva_la_medida():
    c = FakeClient({"Alpha World Fund": [quote("T1", "Alpha World Fund")]}, {"T1": H(cur="XXX")})
    r, e = run_one(c)
    assert r["status"] == "reciente" and r["flags"] == ["divisa_rara"] and e["divisa_rara"] == 1


# ------------------------------------------------------------------ ambigüedad (varias clases / divisas)
def ambiguous_world():
    return FakeClient({"Alpha World Fund": [quote("EUR1", "Alpha World Fund EUR"), quote("USD1", "Alpha World Fund USD"), quote("OTH", "Other Thing")]},
                      {"EUR1": H(cur="EUR"), "USD1": H(cur="USD", last="2026-11-10")})


def test_ambiguo_mide_tambien_las_alternativas_y_detecta_varias_divisas():
    r, _ = run_one(ambiguous_world())
    assert r["ambiguous"] is True and r["ticker"] == "EUR1" and r["candidates"] == 2
    assert r["multi_currency"] is True and sorted(r["currencies"]) == ["EUR", "USD"]
    alt = r["alternatives"][0]
    assert (alt["ticker"], alt["currency"], alt["lag_days"], alt["status"]) == ("USD1", "USD", 6, "retrasado")


def test_ambiguo_misma_divisa_no_es_multidivisa():
    c = FakeClient({"Alpha World Fund": [quote("A1", "Alpha World Fund A"), quote("A2", "Alpha World Fund B")]}, {"A1": H(), "A2": H()})
    r, _ = run_one(c)
    assert r["ambiguous"] is True and r["multi_currency"] is False and r["currencies"] == ["EUR"]


def test_las_alternativas_se_pueden_aplazar_y_medir_despues():
    c = ambiguous_world()
    gate = mk()
    errors = fp.new_error_counter()
    res = fp.run_names([entry("Alpha World Fund")], c, gate, TODAY, errors, with_alternatives=False)
    assert ("history", "USD1") not in c.calls and res[0]["ambiguous"] is True and res[0]["alternatives"][0]["status"] == "pendiente"
    fp.probe_alternatives(res, c, gate, TODAY, errors)
    assert ("history", "USD1") in c.calls and res[0]["alternatives"][0]["currency"] == "USD" and res[0]["multi_currency"] is True


def test_sin_presupuesto_las_alternativas_quedan_pendientes_no_se_inventan():
    c = ambiguous_world()
    gate = mk(max_requests=fp.SEARCH_COST + fp.HISTORY_COST)               # justo busca + mide el mejor
    r, _ = run_one(c, gate=gate)
    assert r["alternatives"][0]["status"] == "pendiente" and r["alternatives"][0]["currency"] is None and r["multi_currency"] is False
    assert gate.stopped == "presupuesto"


# ------------------------------------------------------------------ paradas
def test_429_a_mitad_deja_el_resto_no_probado_y_para_limpio():
    c = FakeClient({"A One": [quote("T1", "A One")], "B Two": fp.yf_exceptions.YFRateLimitError(), "C Three": [quote("T3", "C Three")]}, {"T1": H(), "T3": H()})
    gate, errors = mk(), fp.new_error_counter()
    res = fp.run_names([entry("A One"), entry("B Two"), entry("C Three")], c, gate, TODAY, errors)
    assert [r["status"] for r in res] == ["reciente", "no_probado", "no_probado"]
    assert errors["429"] == 1 and gate.stopped == "429" and ("search", "C Three") not in c.calls


def test_el_ritmo_se_respeta_en_toda_la_seccion():
    c = FakeClient({"A One": [quote("T1", "A One")], "B Two": [quote("T2", "B Two")]}, {"T1": H(), "T2": H()})
    gate = mk()
    fp.run_names([entry("A One"), entry("B Two")], c, gate, TODAY, fp.new_error_counter())
    assert gate.used == 2 * (fp.SEARCH_COST + fp.HISTORY_COST)


# ------------------------------------------------------------------ resumen por gestora
def test_resumen_por_gestora():
    res = [
        {"manager": "G1", "status": "reciente", "ambiguous": False}, {"manager": "G1", "status": "retrasado", "ambiguous": True},
        {"manager": "G1", "status": "no_encontrado", "ambiguous": False}, {"manager": "G2", "status": "reciente", "ambiguous": True},
        {"manager": "G2", "status": "sin_serie", "ambiguous": False}, {"manager": "G2", "status": "no_probado", "ambiguous": False},
        {"manager": "G2", "status": "error", "ambiguous": False},
    ]
    s = {x["manager"]: x for x in fp.summarize_by_manager(res)}
    assert s["G1"] == {"manager": "G1", "searched": 3, "found": 2, "recent": 1, "delayed": 1, "ambiguous": 1, "no_series": 0, "errors": 0, "not_probed": 0}
    assert s["G2"] == {"manager": "G2", "searched": 3, "found": 2, "recent": 1, "delayed": 0, "ambiguous": 1, "no_series": 1, "errors": 1, "not_probed": 1}


def test_no_probados_no_cuentan_como_buscados():
    s = fp.summarize_by_manager([{"manager": "G", "status": "no_probado", "ambiguous": False}])[0]
    assert s["searched"] == 0 and s["not_probed"] == 1


# ------------------------------------------------------------------ la lista de nombres del repo
def names():
    return json.loads(CFG.read_text(encoding="utf-8"))


def test_lista_de_nombres_tamano_gestoras_y_estructura():
    es = fp.load_names(CFG)
    assert 35 <= len(es) <= 60
    assert {"Fidelity", "Amundi", "Vanguard", "iShares", "SPDR", "Xtrackers", "Invesco", "MyInvestor", "Indexa"} <= {e["manager"] for e in es}
    assert all(set(e) == {"manager", "name"} and e["name"].strip() and e["manager"].strip() for e in es)
    assert len({e["name"].lower() for e in es}) == len(es)                 # sin duplicados


def test_lista_de_nombres_no_contiene_isin_ids_ni_urls_ni_nada_de_la_cartera():
    text = CFG.read_text(encoding="utf-8")
    assert not re.search(r"\b[A-Z]{2}[A-Z0-9]{9}\d\b", text)               # forma de ISIN (cualquier país)
    assert not re.search(r"https?://|/products/\d|supabase|@", text, re.I)
    # lista privada del dueño: tests/test_privacidad_lista_privada.py (todo el árbol, lista desde el secreto PRIVATE_DENYLIST)


def test_load_names_rechaza_entradas_invalidas(tmp_path):
    p = tmp_path / "n.json"
    synthetic = "IE00" + "A" * 8                                              # construido aquí: el guardia de privacidad del repo no admite literales con forma de ISIN
    for bad in ('{"x": 1}', '[{"manager": "A"}]', '[{"manager": "A", "name": ""}]', "no json", json.dumps([{"manager": "A", "name": synthetic}])):
        p.write_text(bad, encoding="utf-8")
        with pytest.raises(ValueError):
            fp.load_names(p)


# ------------------------------------------------------------------ diagnóstico de la búsqueda (para interpretar el primer run real)
def test_search_hits_cuenta_los_tipos_que_devolvio_yahoo_y_cuantos_pasaron_el_filtro():
    qs = [quote("E1", "Alpha World Fund", "EQUITY"), quote("E2", "Alpha World Fund", "EQUITY"), quote("F1", "Alpha World Fund"), quote("X", "Otra cosa", "ETF")]
    r, _ = run_one(FakeClient({"Alpha World Fund": qs}, {"F1": H()}))
    assert r["search_hits"] == {"total": 4, "types": {"EQUITY": 2, "MUTUALFUND": 1, "ETF": 1}, "matching": 1}


def test_search_hits_tambien_en_no_encontrado_y_none_si_no_se_busco():
    r, _ = run_one(FakeClient({"Alpha World Fund": [quote("E1", "Alpha World Fund", "EQUITY")]}))
    assert r["status"] == "no_encontrado" and r["search_hits"] == {"total": 1, "types": {"EQUITY": 1}, "matching": 0}
    c = FakeClient({"A One": fp.yf_exceptions.YFRateLimitError()})
    res = fp.run_names([entry("A One"), entry("B Two")], c, mk(), TODAY, fp.new_error_counter())
    assert [x["search_hits"] for x in res] == [None, None]


def test_search_hits_ignora_elementos_raros_sin_romper():
    r, _ = run_one(FakeClient({"Alpha World Fund": [None, "x", {"symbol": "Z"}, quote("F1", "Alpha World Fund")]}, {"F1": H()}))
    assert r["search_hits"]["total"] == 4 and r["search_hits"]["types"] == {"MUTUALFUND": 1, "desconocido": 1} and r["status"] == "reciente"
