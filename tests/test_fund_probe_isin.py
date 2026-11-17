"""Sonda de fondos, punto 3: sección ISIN (secreto opcional, máscara previa, solo agregados, umbral de 5, silencio total de logs/avisos/excepciones)."""
import json
import logging
import warnings


import fund_probe as fp
from fund_probe_fakes import TODAY, FakeClient, FakeTime, quote, rows

# ISIN SINTÉTICOS (forma válida: 2 letras + 9 alfanuméricos + 1 dígito); no corresponden a ningún instrumento.
ISINS = [f"IE00SYNTH{i:03d}" for i in range(1, 9)]


def mk(max_requests=250):
    t = FakeTime()
    return fp.Gate(max_requests=max_requests, clock=t.clock, sleep=t.sleep)


def H(last="2026-11-13", cur="EUR"):
    from datetime import date
    return {"rows": rows(date.fromisoformat(last)), "currency": cur}


def world(specs):
    """specs: lista de (isin, ticker, last, cur) -> FakeClient donde cada ISIN resuelve a un fondo sintético."""
    s, h = {}, {}
    for isin, tick, last, cur in specs:
        s[isin] = [quote(tick, "Synthetic Fund Secret Name")]
        h[tick] = H(last, cur)
    return FakeClient(s, h)


# ------------------------------------------------------------------ lectura del secreto y máscara
def test_secreto_ausente_o_vacio_omite_la_seccion_sin_tocar_yahoo():
    for raw in (None, "", "   ", " , ,, "):
        c, g, out = FakeClient(), mk(), []
        agg = fp.run_isin_section(raw, c, g, TODAY, emit=out.append)
        assert agg == {"status": "omitida"} and c.calls == [] and g.used == 0 and out == []


def test_cada_isin_se_enmascara_antes_de_cualquier_uso_incluidos_los_no_validos():
    out = []
    order = []

    class C(FakeClient):
        def search(self, q):
            order.append(("search", len(out)))
            return []
    raw = f" {ISINS[0]} ,{ISINS[1].lower()}, NOVALIDO-1 ,{ISINS[0]}"
    fp.run_isin_section(raw, C(), mk(), TODAY, emit=out.append)
    masks = [o for o in out if o.startswith("::add-mask::")]
    assert {m[len("::add-mask::"):] for m in masks} >= {ISINS[0], ISINS[1].lower(), "NOVALIDO-1"}      # también el no válido (podría ser un ISIN mal escrito)
    assert all(n >= len(masks) for _, n in order)                                                      # al empezar la primera búsqueda ya estaban TODAS las máscaras


def test_parse_normaliza_duplica_y_cuenta_invalidos():
    valid, invalid, tokens = fp.parse_isins(f"{ISINS[0]}, {ISINS[0].lower()} ,XX,{ISINS[1]},'{ISINS[2]}'")
    assert valid == [ISINS[0], ISINS[1], ISINS[2]] and invalid == 1 and ISINS[0] in tokens


# ------------------------------------------------------------------ agregados y umbral de 5
def specs6():
    return [(ISINS[0], "T0", "2026-11-13", "EUR"), (ISINS[1], "T1", "2026-11-13", "EUR"), (ISINS[2], "T2", "2026-11-12", "EUR"),   # lag 3,3,4
            (ISINS[3], "T3", "2026-11-05", "USD"), (ISINS[4], "T4", "2026-09-01", "GBP")]                                         # lag 11, 76


def test_con_cinco_o_mas_salen_agregados_por_tramo_divisa_y_errores():
    c = world(specs6())
    c.searches[ISINS[5]] = []                                                        # sin resolver
    agg = fp.run_isin_section(",".join(ISINS[:6]), c, mk(), TODAY, emit=lambda s: None)
    assert agg["status"] == "ok" and (agg["tested"], agg["resolved"], agg["recent"]) == (6, 5, 2)
    assert agg["lag_buckets"] == {"0-3": 2, "4-7": 1, "8-30": 1, ">30": 1}
    assert agg["currencies"] == {"EUR": 3, "otras": 2}                               # USD y GBP (1 cada una) se agrupan: una divisa suelta delataría el fondo
    assert agg["errors"]["vacio"] == 1 and agg["not_probed"] == 0 and agg["invalid"] == 0


def test_con_menos_de_cinco_solo_probados_y_resueltos():
    c = world(specs6()[:4])
    agg = fp.run_isin_section(",".join(ISINS[:4]), c, mk(), TODAY, emit=lambda s: None)
    assert agg == {"status": "ok", "tested": 4, "resolved": 4, "small": True}


def test_el_umbral_se_mide_sobre_probados_no_sobre_los_que_se_pidieron():
    c = world(specs6())
    agg = fp.run_isin_section(",".join(ISINS[:6]), c, mk(max_requests=3 * 4), TODAY, emit=lambda s: None)      # presupuesto para 4 ISIN
    assert agg["small"] is True and agg["tested"] == 4 and "lag_buckets" not in agg


def test_divisa_con_dos_o_mas_se_muestra_y_con_una_va_a_otras():
    sp = [(ISINS[i], f"T{i}", "2026-11-13", "USD" if i < 2 else "EUR") for i in range(5)] + []
    sp[4] = (ISINS[4], "T4", "2026-11-13", "CHF")
    agg = fp.run_isin_section(",".join(ISINS[:5]), world(sp), mk(), TODAY, emit=lambda s: None)
    assert agg["currencies"] == {"USD": 2, "EUR": 2, "otras": 1}


def test_ambiguos_se_cuentan_en_agregado():
    c = world(specs6()[:5])
    c.searches[ISINS[0]] = [quote("T0", "Synthetic A"), quote("T0b", "Synthetic B")]
    agg = fp.run_isin_section(",".join(ISINS[:5]), c, mk(), TODAY, emit=lambda s: None)
    assert agg["ambiguous"] == 1


def test_resolucion_ignora_resultados_que_no_son_fondos_y_usa_el_primero():
    c = FakeClient({ISINS[0]: [quote("EQ", "Acme Equity", "EQUITY"), quote("F1", "First"), quote("F2", "Second")]}, {"F1": H(), "F2": H()})
    fp.run_isin_section(ISINS[0], c, mk(), TODAY, emit=lambda s: None)
    assert ("history", "F1") in c.calls and ("history", "EQ") not in c.calls and ("history", "F2") not in c.calls


def test_los_registros_internos_no_guardan_ningun_identificador():
    c = world(specs6())
    recs = fp.run_isins(ISINS[:5], c, mk(), TODAY)
    blob = json.dumps(recs)
    assert not any(x in blob for x in ISINS) and not any(f"T{i}" in blob for i in range(5)) and "Synthetic" not in blob


# ------------------------------------------------------------------ paradas
def test_429_en_isin_deja_el_resto_sin_probar_y_se_cuenta_sin_citar_nada():
    c = world(specs6())
    c.searches[ISINS[2]] = fp.yf_exceptions.YFRateLimitError()
    g = mk()
    agg = fp.run_isin_section(",".join(ISINS[:5]), c, g, TODAY, emit=lambda s: None)
    assert g.stopped == "429" and agg["not_probed"] == 3 and agg["tested"] == 2 and agg["small"] is True        # 2 probados + el cortado por el 429 + 2 sin tocar = 5


def test_el_tope_de_peticiones_se_respeta_en_isin():
    g = mk(max_requests=6)
    fp.run_isin_section(",".join(ISINS[:5]), world(specs6()), g, TODAY, emit=lambda s: None)
    assert g.used <= 6


# ------------------------------------------------------------------ silencio: nada de lo que la librería o las excepciones cuenten sale a ningún lado
def test_lo_que_imprimen_loguean_o_avisan_las_llamadas_no_sale(capfd, caplog):
    leaked = ISINS[0]

    class Noisy(FakeClient):
        def search(self, q):
            print(f"buscando {q}")
            print(f"err {q}", file=__import__("sys").stderr)
            logging.getLogger("yfinance").error("$%s: possibly delisted", q)
            logging.getLogger().warning("x %s", q)
            warnings.warn(f"aviso con {q}")
            if q == ISINS[1]:
                raise RuntimeError(f"fallo con {q}")
            return [quote("TNOISY", "Nombre Secreto")]

        def history(self, s):
            print(f"hist {s}")
            logging.getLogger("yfinance").error("%s: no price data", s)
            return H()
    with caplog.at_level(logging.DEBUG):
        agg = fp.run_isin_section(f"{leaked},{ISINS[1]}", Noisy(), mk(), TODAY, emit=lambda s: None)
    cap = capfd.readouterr()
    everything = cap.out + cap.err + caplog.text + "\n".join(r.getMessage() for r in caplog.records) + json.dumps(agg)
    for secret in (leaked, ISINS[1], "TNOISY", "Nombre Secreto"):
        assert secret not in everything, secret
    assert agg["tested"] == 2


def test_el_logging_global_se_restaura_tras_la_seccion():
    prev = logging.root.manager.disable
    fp.run_isin_section(ISINS[0], world([(ISINS[0], "T0", "2026-11-13", "EUR")]), mk(), TODAY, emit=lambda s: None)
    assert logging.root.manager.disable == prev


def test_un_error_inesperado_en_isin_se_cuenta_como_parseo_sin_texto():
    class Bad(FakeClient):
        def search(self, q):
            raise ZeroDivisionError(f"boom {q}")
    out = []
    agg = fp.run_isin_section(ISINS[0], Bad(), mk(), TODAY, emit=out.append)
    assert agg["tested"] == 1 and agg["resolved"] == 0 and ISINS[0] not in json.dumps(agg) + "".join(m for m in out if not m.startswith("::add-mask::"))
