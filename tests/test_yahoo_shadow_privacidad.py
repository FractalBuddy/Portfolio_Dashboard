"""Sombra NAV Yahoo (F1), punto 8: PRIVACIDAD. El repo y los logs de Actions son públicos: ningún ISIN, símbolo `0P…`, nombre de fondo, precio ni URL
de ficha puede salir por NINGUNA vía (stdout, stderr a nivel de descriptor, resumen del job, $GITHUB_OUTPUT, libro de contadores, registros de logging,
avisos de Python, texto de las excepciones capturadas). La única aparición admitida es la línea EXACTA `::add-mask::<valor>`, que el runner consume y no
muestra. Con controles negativos: un `print` que filtre un dato hace fallar el detector."""
import json
import logging
import traceback

import pytest

import yahoo_shadow as ys
from fund_probe import Gate, ProbeError
from test_yahoo_shadow_run import ROOT
from yahoo_shadow_fakes import (ISIN_A, ISIN_B, ISIN_C, NAME_A, NAME_B, NAME_C, NAV_DAY, NOW, PRICE_A, PRICE_B, PRICE_C, SYM_A, SYM_B, SYM_C,
                                SYM_OTRA_CLASE, URL_A, URL_B, URL_C, FakeTime, FakeYahoo, bars, canary_ok, hist, quote, write_inputs)

YAHOO_A = PRICE_A + 0.0013                     # 87.6513: precio de Yahoo, tan secreto como el oficial
SECRETS = [ISIN_A, ISIN_B, ISIN_C, SYM_A, SYM_B, SYM_C, SYM_OTRA_CLASE, SYM_A[:10], SYM_B[:10], SYM_C[:10], NAME_A, NAME_B, NAME_C, "Zeta Quimera",
           "Quimera", URL_A, URL_B, URL_C, "/products/", "zeta-quimera", "%.2f" % PRICE_A, "%.2f" % PRICE_B, "%.2f" % PRICE_C, "87.6513", repr(YAHOO_A)]


def leaks(texts, secrets=SECRETS):
    """Secretos que aparecen en `texts` fuera de una línea EXACTA `::add-mask::<valor>` (devuelve los valores: solo se usa en las pruebas)."""
    found = set()
    for text in texts:
        for ln in str(text).splitlines():
            if ln.startswith("::add-mask::") and ln[len("::add-mask::"):] in secrets:
                continue
            found |= {s for s in secrets if s in ln}
    return sorted(found)


class LoudYahoo(FakeYahoo):
    """Además de lo que imprime FakeYahoo (stdout, stderr y warnings, citando el símbolo y la consulta), escribe en logging como yfinance."""

    def search(self, query):
        logging.getLogger("yfinance").error("búsqueda de %s", query)
        return super().search(query)

    def history(self, symbol, period="5d"):
        logging.getLogger("yfinance").warning("histórico de %s", symbol)
        return super().history(symbol, period)


def scenario(tmp_path, client_factory=LoudYahoo, extra_funds=()):
    """Un run con TODOS los caminos: comparable (A), nombre que no cuadra con un 0P de otra clase y error con el ISIN en el mensaje (B), histórico que
    lanza con el símbolo en el mensaje (C), y libro previo corrupto que cita un ISIN."""
    d = tmp_path / "personal"
    write_inputs(d, [(ISIN_A, NAME_A, URL_A, PRICE_A, NAV_DAY.isoformat()), (ISIN_B, NAME_B, URL_B, PRICE_B, NAV_DAY.isoformat()),
                     (ISIN_C, NAME_C, URL_C, PRICE_C, NAV_DAY.isoformat())] + list(extra_funds), users=2)
    s = {ISIN_A: [quote(SYM_A, NAME_A), quote(SYM_OTRA_CLASE, NAME_A.replace(" S ", " D "))], ISIN_B: ValueError("fallo con " + ISIN_B + " " + NAME_B),
         ISIN_C: [quote(SYM_C, NAME_C)]}
    h = {SYM_A: hist(bars(NAV_DAY, 4, YAHOO_A)), SYM_C: RuntimeError("roto " + SYM_C + " " + repr(PRICE_C))}
    canary_ok(s, h)
    y = client_factory(s, h)
    ledger = tmp_path / "cache" / "ledger.json"
    ledger.parent.mkdir(parents=True)
    ledger.write_text('{"schema": 1, "reads": [{"day": "%s"}]}' % ISIN_A, encoding="utf-8")
    gh_out, summary = tmp_path / "gh_output", tmp_path / "summary.md"
    t = FakeTime()
    rc = ys.main(["--dir", str(d), "--config", str(ROOT / "config" / "yahoo_shadow.json"), "--generic", str(ROOT / "config" / "fund_probe_isins_generic.json"),
                  "--ledger", str(ledger)], env={"GITHUB_OUTPUT": str(gh_out), "GITHUB_STEP_SUMMARY": str(summary)}, client=y, now=NOW,
                 clock=t.clock, sleep=t.sleep)
    return rc, y, [f.read_text(encoding="utf-8") if f.exists() else "" for f in (summary, gh_out, ledger)]


def test_ningun_dato_del_fondo_sale_por_ninguna_via(tmp_path, capfd, caplog, recwarn):
    caplog.set_level(logging.DEBUG)
    rc, y, files = scenario(tmp_path)
    out, err = capfd.readouterr()
    assert rc == 0 and any(c[0] == "history" for c in y.calls)                 # el escenario sí ha llegado a pedir datos
    assert "comparables 1" in out and "sin emparejar" in out                    # y sí ha medido (no es una prueba vacía)
    texts = [out, err] + files + [r.getMessage() for r in caplog.records] + [str(w.message) for w in recwarn]
    assert leaks(texts) == []


def test_cada_isin_y_cada_0p_se_enmascara_antes_de_usarlo(tmp_path, monkeypatch):
    events = []
    monkeypatch.setattr(ys, "say", lambda msg: events.append(("say", msg)))

    class Rec(FakeYahoo):
        def search(self, query):
            events.append(("search", query))
            return super().search(query)

        def history(self, symbol, period="5d"):
            events.append(("history", symbol))
            return super().history(symbol, period)
    scenario(tmp_path, Rec)
    masks = {e[1][len("::add-mask::"):]: i for i, e in enumerate(events) if e[0] == "say" and e[1].startswith("::add-mask::")}
    for kind, value in [e for e in events if e[0] in ("search", "history")]:
        assert value in masks and masks[value] < events.index((kind, value)), (kind, value == ISIN_A)
    assert {ISIN_A, ISIN_B, ISIN_C, SYM_A, SYM_OTRA_CLASE, SYM_C} <= set(masks)     # también el 0P que no se usa (nombre de otra clase)


def test_las_excepciones_capturadas_no_llevan_el_dato():
    class Boom(FakeYahoo):
        def search(self, query):
            raise ValueError("no encuentro " + query + " " + NAME_A)
    t = FakeTime()
    with pytest.raises(ProbeError) as ei:
        ys.resolve(ISIN_A, NAME_A, Boom(), Gate(max_requests=10, clock=t.clock, sleep=t.sleep), lambda v: None, 0.6)
    shown = "".join(traceback.format_exception(ei.value))                      # lo que se vería si alguien imprimiese la traza
    assert leaks([str(ei.value), repr(ei.value), shown]) == [] and ei.value.cls == "parseo"


def test_el_fallo_inesperado_no_cita_el_dato(tmp_path, capfd, monkeypatch):
    def boom(funds, *a, **k):
        raise KeyError(funds[0]["isin"] + " " + funds[0]["name"])
    monkeypatch.setattr(ys, "run_shadow", boom)
    rc, _y, files = scenario(tmp_path)
    out, err = capfd.readouterr()
    # files[2] es el libro corrupto que ESTA prueba escribió como entrada (cita un ISIN a propósito): el run falla antes de reescribirlo
    assert rc == 0 and "fallo inesperado (KeyError en " in out and leaks([out, err] + files[:2]) == []


# ------------------------------------------------------------------ controles negativos: el detector SÍ caza una fuga por cada vía
def test_control_negativo_un_print_que_filtra_un_isin_hace_fallar(tmp_path, capfd, monkeypatch):
    real = ys.resolve

    def chivato(isin, *a, **k):
        print("procesando", isin)                                              # la fuga que el guardia debe cazar
        return real(isin, *a, **k)
    monkeypatch.setattr(ys, "resolve", chivato)
    scenario(tmp_path)
    out, err = capfd.readouterr()
    assert ISIN_A in leaks([out, err])


def test_control_negativo_una_fuga_por_stderr_o_en_el_resumen_tambien(tmp_path, capfd, monkeypatch):
    import sys
    real = ys.counters_line

    def chivato(c, counts, cfg, now):
        print("símbolo " + SYM_A, file=sys.stderr)
        return real(c, counts, cfg, now) + " " + "%.2f" % PRICE_A
    monkeypatch.setattr(ys, "counters_line", chivato)
    _rc, _y, files = scenario(tmp_path)
    out, err = capfd.readouterr()
    assert SYM_A in leaks([err]) and "%.2f" % PRICE_A in leaks([files[0]]) and "%.2f" % PRICE_A in leaks([out])


def test_control_negativo_la_exencion_de_add_mask_es_exacta():
    assert leaks(["::add-mask::" + ISIN_A]) == []
    assert leaks(["::add-mask::" + ISIN_A + " y algo más"]) == [ISIN_A]
    assert leaks(["::warning::" + ISIN_A]) == [ISIN_A]
    assert leaks(["x ::add-mask::" + ISIN_A]) == [ISIN_A]


def test_control_negativo_sin_silenciar_yfinance_hay_fuga(tmp_path, capfd, monkeypatch):
    import contextlib
    monkeypatch.setattr(ys, "_quiet", contextlib.nullcontext)
    scenario(tmp_path)
    out, err = capfd.readouterr()
    assert leaks([out, err])                                                    # FakeYahoo imprime el símbolo y la consulta: sin _quiet, se ve


def test_el_resumen_y_la_salida_del_paso_son_solo_contadores(tmp_path, capfd):
    _rc, _y, files = scenario(tmp_path)
    capfd.readouterr()
    summary, gh, ledger = files
    assert gh.strip() == "ledger=written"
    assert json.loads(ledger)["reads"][0]["c"]["comparables"] == 1
    assert "|" in summary and leaks([summary]) == []
