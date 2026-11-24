"""Sonda de fondos, GENÉRICOS 2/5: similitud de nombres, regla de elección de cotización, medida por ISIN y agregados (Yahoo simulado, ISIN sintéticos)."""
from datetime import date

import fund_probe as fp
from fund_probe_fakes import TODAY, FakeClient, FakeTime, quote, rows


def mk(max_requests=400):
    t = FakeTime()
    return fp.Gate(max_requests=max_requests, clock=t.clock, sleep=t.sleep)


def H(last=date(2026, 11, 13), n=252, cur="EUR"):
    return {"rows": rows(last, n), "currency": cur}


def entry(isin, name="Alpha World Fund USD Acc", ok=True):
    return {"isin": isin, "name": name, "check_ok": ok}


def cand(symbol, exchange="FRA", qtype="MUTUALFUND", name="Alpha World Fund", rank=0):
    return {"symbol": symbol, "exchange": exchange, "quote_type": qtype, "name": name, "names": (name,), "rank": rank}


# ------------------------------------------------------------------ similitud de nombres (regla: Jaccard de palabras significativas)
def test_similitud_es_jaccard_de_palabras_significativas():
    assert fp.name_similarity("Alpha World Fund USD Acc", "Alpha World Fund USD Acc") == 1.0
    assert fp.name_similarity("Alpha World", "Gamma Delta") == 0.0
    # {alpha, world, usd, acc} frente a {alpha, world, etf, usd}: 3 en común / 5 en total = 0.6 (el coeficiente de solapamiento daría 0.75)
    assert fp.name_similarity("Alpha World Fund USD Acc", "Alpha World UCITS ETF USD") == 0.6


def test_similitud_ignora_mayusculas_acentos_puntuacion_y_palabras_vacias():
    assert fp.name_similarity("Fondo Índice Mundial", "FONDO INDICE MUNDIAL, the Fund of") == 1.0
    assert fp.name_similarity("S&P 500 Index", "S&P-500 index") == 1.0


def test_similitud_vacia_o_ausente_es_cero_nunca_error():
    for a, b in (("", "Alpha"), ("Alpha", None), (None, None), ("UCITS Fund", "UCITS fund")):
        assert fp.name_similarity(a, b) == 0.0


def test_similitud_castiga_clases_distintas_del_mismo_fondo():
    """«Acc» frente a «Dist» y USD frente a EUR bajan la similitud: la regla distingue clases, no solo gestoras."""
    base = "Vanguard FTSE All-World UCITS ETF USD Acc"
    assert fp.name_similarity(base, "Vanguard FTSE All-World UCITS ETF USD Acc") > fp.name_similarity(base, "Vanguard FTSE All-World UCITS ETF USD Dist") > fp.name_similarity(base, "Vanguard Global Bond")


# ------------------------------------------------------------------ divisa del nombre
def test_divisa_esperada_solo_si_el_nombre_trae_exactamente_una():
    assert fp.expected_currency("iShares Core MSCI World UCITS ETF USD Acc") == "USD"
    assert fp.expected_currency("Fidelity MSCI World Index Fund P-ACC-EUR") == "EUR"
    assert fp.expected_currency("Global Aggregate Bond UCITS ETF EUR Hedged Acc") == "EUR"
    assert fp.expected_currency("Cobas Internacional FI") is None
    assert fp.expected_currency("Mixed USD and EUR classes") is None                      # dos divisas: no hay criterio
    assert fp.expected_currency("EURO Stoxx 50") is None                                  # «EURO» no es la divisa EUR (palabra completa)


# ------------------------------------------------------------------ regla de elección (determinista)
def test_regla_un_solo_candidato_decide_por_unica():
    r = fp.choose_candidate([cand("A.F")], "Alpha World Fund USD Acc")
    assert r["pick"]["symbol"] == "A.F" and r["decided_by"] == "unica" and r["tie"] is False


def test_regla_prefiere_0P_aunque_otra_sea_mas_liquida_y_Yahoo_la_ponga_antes():
    cs = [cand("ALPH.DE", "GER", "ETF", rank=0), cand("0P0001.F", "FRA", "MUTUALFUND", rank=1)]
    r = fp.choose_candidate(cs, "Alpha World Fund USD Acc")
    assert r["pick"]["symbol"] == "0P0001.F" and r["decided_by"] == "0p" and r["tie"] is False


def test_regla_entre_varias_0P_usa_la_divisa_del_nombre_esperado():
    cs = [cand("0P0001.F", name="Alpha World Fund EUR Acc", rank=0), cand("0P0002.F", name="Alpha World Fund USD Acc", rank=1)]
    r = fp.choose_candidate(cs, "Alpha World Fund USD Acc")
    assert r["pick"]["symbol"] == "0P0002.F" and r["decided_by"] == "divisa"


def test_regla_la_divisa_se_lee_de_cualquiera_de_los_dos_nombres_de_Yahoo():
    c0 = cand("0P0001.F", name="Alpha World Fund", rank=0)
    c1 = cand("0P0002.F", name="Alpha World Fund", rank=1)
    c1["names"] = ("Alpha World Fund", "Alpha World Fund USD Acc")
    assert fp.choose_candidate([c0, c1], "Alpha World Fund USD Acc")["pick"]["symbol"] == "0P0002.F"


def test_regla_sin_divisa_en_el_nombre_o_sin_candidato_con_esa_divisa_el_criterio_no_filtra():
    cs = [cand("0P0001.F", name="Alpha World Fund", rank=0), cand("0P0002.F", name="Alpha World Fund", rank=1)]
    r = fp.choose_candidate(cs, "Cobas Internacional FI")                                 # el nombre esperado no trae divisa
    assert r["decided_by"] == "sin_criterio" and [c["symbol"] for c in r["tied"]] == ["0P0001.F", "0P0002.F"]
    r = fp.choose_candidate(cs, "Alpha World Fund USD Acc")                               # trae USD, pero ninguna candidata lo dice: no se inventa
    assert r["decided_by"] == "sin_criterio" and len(r["tied"]) == 2


def test_regla_liquidez_conocida_si_no_hay_0P_ni_divisa():
    cs = [cand("A.F", "FRA", "ETF", rank=0), cand("A.AS", "AMS", "ETF", rank=1), cand("A.DE", "GER", "ETF", rank=2)]
    r = fp.choose_candidate(cs, "Alpha World Fund")
    assert r["pick"]["symbol"] == "A.DE" and r["decided_by"] == "liquidez" and r["tie"] is False


def test_regla_sin_criterio_emite_todas_las_empatadas_y_mide_la_primera_de_Yahoo():
    cs = [cand("A.F", "FRA", "ETF", rank=3), cand("A.BE", "BER", "ETF", rank=1), cand("A.L", "LSE", "ETF", rank=2), cand("A.DE", "GER", "ETF", rank=5)]
    r = fp.choose_candidate(cs, "Alpha World Fund")
    assert r["decided_by"] == "sin_criterio" and r["tie"] is True
    assert [c["symbol"] for c in r["tied"]] == ["A.L", "A.DE"]                            # mismo nivel de liquidez (LSE y Xetra), por orden de Yahoo
    assert r["pick"]["symbol"] == "A.L"                                                   # solo para poder medirla; se marca tie


def test_regla_bolsas_desconocidas_no_se_ordenan_entre_si():
    cs = [cand("X1", "ZZZ", "ETF", rank=0), cand("X2", "YYY", "ETF", rank=1)]
    r = fp.choose_candidate(cs, "Alpha World Fund")
    assert r["tie"] is True and len(r["tied"]) == 2 and r["decided_by"] == "sin_criterio"


def test_regla_es_determinista_y_no_depende_del_orden_de_la_lista():
    cs = [cand("A.F", "FRA", "ETF", rank=0), cand("0P0001.F", "FRA", "MUTUALFUND", rank=1), cand("A.DE", "GER", "ETF", rank=2)]
    a = fp.choose_candidate(cs, "Alpha World Fund")
    b = fp.choose_candidate(list(reversed(cs)), "Alpha World Fund")
    assert a["pick"]["symbol"] == b["pick"]["symbol"] == "0P0001.F" and a == fp.choose_candidate(cs, "Alpha World Fund")


def test_regla_lista_vacia_no_elige_nada():
    assert fp.choose_candidate([], "x")["pick"] is None


def test_reglas_alternativas_cambian_la_eleccion_donde_deben():
    """Control negativo de la regla principal: con el mismo mundo, otro orden de criterios elige OTRO ticker (si no, contar «cuántos habrían cambiado» no mediría nada)."""
    cs = [cand("A.DE", "GER", "ETF", rank=0), cand("0P0001.F", "FRA", "MUTUALFUND", rank=1)]
    principal = fp.choose_candidate(cs, "Alpha World Fund", fp.RULES["principal"])["pick"]["symbol"]
    liquidez = fp.choose_candidate(cs, "Alpha World Fund", fp.RULES["liquidez_primero"])["pick"]["symbol"]
    yahoo = fp.choose_candidate(cs, "Alpha World Fund", fp.RULES["orden_yahoo"])["pick"]["symbol"]
    assert (principal, liquidez, yahoo) == ("0P0001.F", "A.DE", "A.DE")


def test_tipo_de_ticker_0P_etf_otras():
    assert fp.ticker_type(cand("0P0001.F", qtype="MUTUALFUND")) == "0P" and fp.ticker_type(cand("0p0001.f", qtype="ETF")) == "0P"
    assert fp.ticker_type(cand("SYNW.DE", qtype="ETF")) == "etf" and fp.ticker_type(cand("ABC.MC", qtype="MUTUALFUND")) == "otras"
    assert fp.ticker_type(cand("X0P1.F", qtype="MUTUALFUND")) == "otras" and fp.ticker_type(cand("A0P.DE", qtype="ETF")) == "etf"       # «0P» solo cuenta al principio del símbolo


# ------------------------------------------------------------------ medida por ISIN
def run(entries, client, gate=None, today=TODAY):
    gate = gate or mk()
    results, errors = fp.run_generic(entries, client, gate, today)
    return results, errors, gate


def test_un_isin_resuelto_trae_todos_los_campos_pedidos():
    c = FakeClient({"ZZ00SYNTH011": [quote("0P0AAA.F", "ALPHA WORLD FUND USD", "MUTUALFUND", "FRA", longname="Alpha World Fund USD Acc"),
                                     quote("ALPH.L", "Alpha World ETF", "ETF", "LSE")]}, {"0P0AAA.F": H(cur="EUR")})
    (r,), errors, g = run([entry("ZZ00SYNTH011")], c)
    assert (r["isin"], r["expected_name"], r["status"], r["ticker"], r["exchange"], r["currency"]) == ("ZZ00SYNTH011", "Alpha World Fund USD Acc", "reciente", "0P0AAA.F", "FRA", "EUR")
    assert r["yahoo_name"] == "Alpha World Fund USD Acc" and r["name_similarity"] == 1.0           # prefiere el nombre largo
    assert (r["candidates"], r["quotes_total"], r["bars_1y"], r["last_date"], r["lag_days"]) == (2, 2, 252, "2026-11-13", 3)
    assert r["ticker_type"] == "0P" and r["expected_currency"] == "USD" and r["currency_match"] is False and r["decided_by"] == "0p"
    assert [a["ticker"] for a in r["all_candidates"]] == ["0P0AAA.F", "ALPH.L"]


def test_la_busqueda_es_por_isin_y_el_historico_de_un_ano():
    c = FakeClient({"ZZ00SYNTH011": [quote("0P0AAA.F", "Alpha")]}, {"0P0AAA.F": H()})
    run([entry("ZZ00SYNTH011")], c)
    assert c.calls == [("search", "ZZ00SYNTH011"), ("history", "0P0AAA.F")] and c.periods == ["1y"]


def test_estados_reciente_retrasado_sin_serie_no_encontrado_y_error():
    c = FakeClient({"ZZ00SYNTH001": [quote("0P01.F", "A")], "ZZ00SYNTH002": [quote("0P02.F", "B")], "ZZ00SYNTH003": [quote("0P03.F", "C")], "ZZ00SYNTH004": [],
                    "ZZ00SYNTH005": RuntimeError("boom ZZ00SYNTH005")},
                   {"0P01.F": H(date(2026, 11, 13)), "0P02.F": H(date(2026, 11, 12)), "0P03.F": {"rows": [], "currency": "EUR"}})
    res, errors, g = run([entry(f"ZZ00SYNTH00{i}") for i in range(1, 6)], c)
    assert [r["status"] for r in res] == ["reciente", "retrasado", "sin_serie", "no_encontrado", "error"]
    assert [r["lag_days"] for r in res[:2]] == [3, 4]                                           # borde: 3 d reciente, 4 d retrasado
    assert res[2]["error"] == "vacio" and res[4]["error"] == "parseo" and "boom" not in str(res[4])
    assert errors["vacio"] == 2 and errors["parseo"] == 1                                       # sin_serie y no_encontrado cuentan «vacio»; el fallo raro, «parseo»


def test_solo_candidatos_de_tipo_fondo_y_se_cuentan_los_devueltos_por_tipo():
    c = FakeClient({"ZZ00SYNTH011": [quote("ACC1", "Alpha", "EQUITY"), quote("0P0AAA.F", "Alpha"), quote("0P0AAA.F", "Alpha dup"), quote("ALPH.L", "Alpha", "ETF", "LSE")]},
                   {"0P0AAA.F": H()})
    (r,), _, _ = run([entry("ZZ00SYNTH011")], c)
    assert r["quotes_total"] == 4 and r["candidates"] == 2 and r["search_types"] == {"EQUITY": 1, "MUTUALFUND": 2, "ETF": 1}


def test_costes_busqueda_1_y_historico_2_y_no_resuelto_solo_cuesta_1():
    c = FakeClient({"ZZ00SYNTH001": [quote("0P01.F", "A")], "ZZ00SYNTH002": []}, {"0P01.F": H()})
    _, _, g = run([entry("ZZ00SYNTH001"), entry("ZZ00SYNTH002")], c)
    assert g.used == 3 + 1


def test_429_en_la_busqueda_para_limpio_y_el_resto_queda_no_probado_sin_mas_llamadas():
    c = FakeClient({"ZZ00SYNTH001": [quote("0P01.F", "A")], "ZZ00SYNTH002": RuntimeError("HTTP Error 429: Too Many Requests"), "ZZ00SYNTH003": [quote("0P03.F", "C")]},
                   {"0P01.F": H(), "0P03.F": H()})
    res, errors, g = run([entry(f"ZZ00SYNTH00{i}") for i in (1, 2, 3)], c)
    assert g.stopped == "429" and [r["status"] for r in res] == ["reciente", "no_probado", "no_probado"] and errors["429"] == 1
    assert c.calls == [("search", "ZZ00SYNTH001"), ("history", "0P01.F"), ("search", "ZZ00SYNTH002")]


def test_429_en_el_historico_deja_el_ticker_pero_no_probado():
    c = FakeClient({"ZZ00SYNTH001": [quote("0P01.F", "A")], "ZZ00SYNTH002": [quote("0P02.F", "B")]}, {"0P01.F": RuntimeError("429 Too Many Requests")})
    res, _, g = run([entry("ZZ00SYNTH001"), entry("ZZ00SYNTH002")], c)
    assert g.stopped == "429" and res[0]["status"] == "no_probado" and res[0]["ticker"] == "0P01.F" and res[0]["error"] == "429" and res[1]["status"] == "no_probado"
    assert ("search", "ZZ00SYNTH002") not in c.calls


def test_sin_presupuesto_para_el_historico_queda_no_probado():
    c = FakeClient({"ZZ00SYNTH001": [quote("0P01.F", "A")]}, {"0P01.F": H()})
    res, _, g = run([entry("ZZ00SYNTH001")], c, gate=mk(max_requests=2))
    assert g.stopped == "presupuesto" and res[0]["status"] == "no_probado" and ("history", "0P01.F") not in c.calls


def test_el_digito_de_control_malo_se_arrastra_al_resultado_y_se_sonda_igual():
    c = FakeClient({"ZZ00SYNTH001": []})
    (r,), _, _ = run([entry("ZZ00SYNTH001", ok=False)], c)
    assert r["check_ok"] is False and r["status"] == "no_encontrado" and c.calls == [("search", "ZZ00SYNTH001")]


def test_pausa_de_un_segundo_entre_todas_las_llamadas_de_la_seccion():
    t = FakeTime()
    g = fp.Gate(max_requests=400, clock=t.clock, sleep=t.sleep)
    c = FakeClient({f"ZZ00SYNTH00{i}": [quote(f"0P0{i}.F", "A")] for i in (1, 2)}, {"0P01.F": H(), "0P02.F": H()})
    fp.run_generic([entry("ZZ00SYNTH001"), entry("ZZ00SYNTH002")], c, g, TODAY)
    assert len(t.sleeps) == 3 and all(s >= 1.0 - 1e-9 for s in t.sleeps)


# ------------------------------------------------------------------ agregados
def world():
    s = {"ZZ00SYNTH001": [quote("0P01.F", "Alpha", "MUTUALFUND", "FRA")],
         "ZZ00SYNTH002": [quote("ALPH.L", "Alpha", "ETF", "LSE")],
         "ZZ00SYNTH003": [quote("ALPH.DE", "Alpha", "ETF", "GER")],
         "ZZ00SYNTH004": [quote("XYZ", "Alpha", "MUTUALFUND", "OTH")],
         "ZZ00SYNTH005": [],
         "ZZ00SYNTH006": RuntimeError("fallo"),
         "ZZ00SYNTH007": [quote("ALPH.MI", "Alpha", "ETF", "MIL"), quote("0P02.F", "Alpha", "MUTUALFUND", "FRA")],
         "ZZ00SYNTH008": [quote("A1.F", "Alpha", "ETF", "FRA"), quote("A2.BE", "Alpha", "ETF", "BER")]}
    h = {"0P01.F": H(date(2026, 11, 13), 252), "ALPH.L": H(date(2026, 11, 12), 250), "ALPH.DE": H(TODAY, 249), "XYZ": {"rows": [], "currency": "EUR"},
         "0P02.F": H(date(2026, 11, 13), 252), "A1.F": H(date(2026, 11, 6), 100)}
    return FakeClient(s, h)


def agg_of(entries=None, client=None):
    entries = entries or [entry(f"ZZ00SYNTH00{i}") for i in range(1, 9)]
    res, errors, g = run(entries, client or world())
    return res, fp.aggregate_generic(res)


def test_agregado_resueltos_250_barras_y_recientes():
    res, a = agg_of()
    assert a["total"] == 8 and a["probed"] == 8 and a["resolved"] == 6
    assert a["with_250_bars"] == 3                                                  # 252, 250 (borde) y 252; 249 no cuenta
    assert a["recent"] == 3                                                         # lag 3, 0 y 1; el de 4 días y el de 10 no
    assert a["by_status"] == {"reciente": 3, "retrasado": 2, "sin_serie": 1, "no_encontrado": 1, "error": 1, "no_probado": 0}


def test_agregado_por_tipo_de_ticker_y_por_bolsa_sobre_los_resueltos():
    _, a = agg_of()
    assert a["by_ticker_type"] == {"0P": 2, "etf": 3, "otras": 1}
    assert a["by_exchange"] == {"FRA": 3, "GER": 1, "LSE": 1, "OTH": 1}            # por nº descendente y luego por nombre


def test_agregado_cuantos_isin_habrian_cambiado_de_ticker_con_otra_regla():
    _, a = agg_of()
    assert a["with_several_candidates"] == 2 and a["ties"] == 1
    assert a["rule_changes"] == {"liquidez_primero": 1, "orden_yahoo": 1}           # solo el 7º (0P en FRA frente a un ETF en MIL); el 8º empata y no cambia


def test_agregado_lista_los_digitos_de_control_malos_y_los_empates_sin_descartar_nada():
    entries = [entry(f"ZZ00SYNTH00{i}", ok=(i != 3)) for i in range(1, 9)]
    _, a = agg_of(entries)
    assert a["check_digit_failures"] == ["ZZ00SYNTH003"] and a["tie_isins"] == ["ZZ00SYNTH008"]


def test_agregado_no_probados_no_cuentan_como_probados():
    c = world()
    res, errors, g = run([entry(f"ZZ00SYNTH00{i}") for i in range(1, 9)], c, gate=mk(max_requests=5))
    a = fp.aggregate_generic(res)
    assert a["total"] == 8 and a["probed"] < 8 and a["by_status"]["no_probado"] == 8 - a["probed"]


def test_las_reglas_documentadas_estan_escritas_en_una_constante_para_el_resumen_y_el_json():
    for needle in ("Jaccard", "0P", "divisa", "líquida"):
        assert needle in fp.GENERIC_RULES_TEXT
