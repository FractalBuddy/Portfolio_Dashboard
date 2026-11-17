"""Sonda de fondos, punto 4: resumen, JSON, `main` de extremo a extremo y CONTROLES NEGATIVOS de privacidad (un ISIN sintético no puede llegar a ninguna salida)."""
import json
import logging
import sys
import warnings

import pytest

import fund_probe as fp
from fund_probe_fakes import TODAY, FakeClient, FakeTime, quote, rows

ISINS = [f"IE00SYNTH{i:03d}" for i in range(1, 9)]
RESOLVED_TICKER = "0P0SECRET9.F"                      # ticker al que «resuelven» los ISIN sintéticos (no puede salir jamás)
RESOLVED_NAME = "Nombre Secreto Del Fondo Privado"


def H(last="2026-11-13", cur="EUR"):
    from datetime import date
    return {"rows": rows(date.fromisoformat(last)), "currency": cur}


NAMES = [{"manager": "Gestora A", "name": "Alpha World Fund"}, {"manager": "Gestora A", "name": "Beta Growth Fund"},
         {"manager": "Gestora B", "name": "Gamma Index Fund"}, {"manager": "Gestora B", "name": "Delta Bond Fund"}]


def names_file(tmp_path, entries=NAMES):
    p = tmp_path / "names.json"
    p.write_text(json.dumps(entries), encoding="utf-8")
    return p


class World(FakeClient):
    """Nombres sintéticos + ISIN sintéticos que resuelven a un fondo «secreto». Ruidoso a propósito: imprime, loguea, avisa y lanza citando la consulta."""

    def __init__(self, noisy=False, isin_specs=None):
        super().__init__(
            searches={"Alpha World Fund": [quote("0P0ALPHA01.F", "Alpha World Fund A"), quote("0P0ALPHA02.L", "Alpha World Fund B USD")],
                      "Beta Growth Fund": [quote("0P0BETA001.F", "Beta Growth Fund")], "Gamma Index Fund": [quote("0P0GAMMA01.F", "Gamma Index Fund")],
                      "Delta Bond Fund": []},
            histories={"0P0ALPHA01.F": H(), "0P0ALPHA02.L": H(cur="USD", last="2026-11-10"), "0P0BETA001.F": H(last="2026-11-05"), "0P0GAMMA01.F": H()})
        self.noisy = noisy
        for i in isin_specs or []:
            self.searches[i] = [quote(RESOLVED_TICKER, RESOLVED_NAME)]
        self.histories[RESOLVED_TICKER] = H()

    def search(self, q):
        if self.noisy and q in ISINS:
            print(f"buscando {q}")
            print(f"err {q}", file=sys.stderr)
            logging.getLogger("yfinance").error("$%s: possibly delisted", q)
            warnings.warn(f"aviso {q}")
            if q == ISINS[1]:
                raise RuntimeError(f"fallo citando {q} y {RESOLVED_TICKER}")
        return super().search(q)

    def history(self, s):
        if self.noisy and s == RESOLVED_TICKER:
            print(f"hist {s} {RESOLVED_NAME}")
            logging.getLogger("yfinance").error("%s: no price data", s)
        return super().history(s)


def run_main(tmp_path, env=None, client=None, extra=(), capsys=None):
    t = FakeTime()
    summ, js = tmp_path / "summary.md", tmp_path / "out.json"
    full_env = {"GITHUB_STEP_SUMMARY": str(summ)}
    full_env.update(env or {})
    rc = fp.main(["--names", str(names_file(tmp_path)), "--json-out", str(js), *extra], env=full_env, client=client or World(), today=TODAY, clock=t.clock, sleep=t.sleep)
    return rc, (summ.read_text(encoding="utf-8") if summ.exists() else ""), (js.read_text(encoding="utf-8") if js.exists() else ""), t


def table_row(md, first_cell):
    for line in md.splitlines():
        cells = [c.strip() for c in line.strip().strip("|").split("|")]
        if line.startswith("|") and cells and cells[0] == first_cell:
            return cells
    raise AssertionError(f"no hay fila {first_cell!r}")


def strip_mask_commands(text):
    """Las órdenes ::add-mask:: son la ÚNICA línea de stdout donde aparece un ISIN: el runner las consume y las sustituye por *** (se comprueba en el primer run)."""
    return "\n".join(line for line in text.splitlines() if not line.startswith("::add-mask::"))


def assert_no_leak(outputs, secrets):
    blob = "\n".join(outputs)
    for s in secrets:
        assert s not in blob, "fuga de un valor de la sección ISIN"


SECRETS = ISINS + [RESOLVED_TICKER, RESOLVED_NAME, "Nombre Secreto"]


# ------------------------------------------------------------------ resumen y JSON
def test_el_resumen_trae_tabla_por_gestora_detalle_y_clases_de_error(tmp_path):
    rc, md, js, _ = run_main(tmp_path)
    assert rc == 0
    assert table_row(md, "Gestora A")[1:] == ["2", "2", "1", "1", "1", "0", "0", "0"]      # buscados, encontrados, recientes, retrasados, ambiguos, sin serie, errores, sin probar
    assert table_row(md, "Gestora B")[1:] == ["2", "1", "1", "0", "0", "0", "0", "0"]
    assert table_row(md, "Total")[1:] == ["4", "3", "2", "1", "1", "0", "0", "0"]
    for cls in fp.ERROR_CLASSES:
        assert f"| {cls} |" in md
    assert "Alpha World Fund" in md and "0P0ALPHA01.F" in md                                     # la sección de NOMBRES sí sale completa
    assert "ISIN: omitida" in md or "Sección ISIN: omitida" in md


def test_el_json_solo_lleva_nombres_y_es_estable(tmp_path):
    rc, md, js, _ = run_main(tmp_path)
    d = json.loads(js)
    assert set(d) == {"generated", "yfinance", "recent_days", "request_cap", "requests_names", "stopped", "errors", "per_manager", "names"}
    assert len(d["names"]) == 4 and d["errors"]["vacio"] == 1 and d["stopped"] is None and d["recent_days"] == 3
    assert "isin" not in js.lower()
    n = {x["name"]: x for x in d["names"]}
    assert n["Alpha World Fund"]["multi_currency"] is True and n["Delta Bond Fund"]["status"] == "no_encontrado"


def test_sin_secreto_el_resultado_es_el_mismo_que_con_isin_en_la_parte_de_nombres(tmp_path):
    a = tmp_path / "a"
    b = tmp_path / "b"
    a.mkdir()
    b.mkdir()
    _, _, js_a, _ = run_main(a)
    _, _, js_b, _ = run_main(b, env={"PROBE_ISINS": ",".join(ISINS[:6])}, client=World(isin_specs=ISINS))
    da, db = json.loads(js_a), json.loads(js_b)
    assert da["names"] == db["names"] and da["per_manager"] == db["per_manager"] and da["errors"] == db["errors"] and da["requests_names"] == db["requests_names"]


def test_el_ritmo_de_un_segundo_se_cumple_de_extremo_a_extremo(tmp_path):
    t = FakeTime()
    stamps = []

    class C(World):
        def search(self, q):
            stamps.append(t.clock())
            return super().search(q)

        def history(self, s):
            stamps.append(t.clock())
            return super().history(s)
    fp.main(["--names", str(names_file(tmp_path)), "--json-out", str(tmp_path / "o.json")], env={"PROBE_ISINS": ",".join(ISINS[:5])}, client=C(isin_specs=ISINS),
            today=TODAY, clock=t.clock, sleep=t.sleep)
    assert len(stamps) > 10 and all(b - a >= 1.0 for a, b in zip(stamps, stamps[1:]))


def test_max_requests_se_recorta_a_250_y_se_respeta(tmp_path):
    rc, md, js, _ = run_main(tmp_path, extra=["--max-requests", "4"])
    d = json.loads(js)
    assert rc == 0 and d["request_cap"] == 4 and d["stopped"] == "presupuesto" and d["requests_names"] <= 4
    assert any(x["status"] == "no_probado" for x in d["names"])
    rc, md, js, _ = run_main(tmp_path, extra=["--max-requests", "9999"])
    assert json.loads(js)["request_cap"] == 250


# ------------------------------------------------------------------ 429: parada limpia e informada
def test_429_en_nombres_para_limpio_informa_y_sale_con_cero(tmp_path, capfd):
    c = World()
    c.searches["Gamma Index Fund"] = fp.yf_exceptions.YFRateLimitError()
    rc, md, js, _ = run_main(tmp_path, client=c)
    d = json.loads(js)
    assert rc == 0 and d["stopped"] == "429" and d["errors"]["429"] == 1
    assert [x["status"] for x in d["names"]][-2:] == ["no_probado", "no_probado"]
    assert "429" in md and "parada" in md.lower()
    assert ("search", "Delta Bond Fund") not in c.calls
    assert "::warning::" in capfd.readouterr().out


# ------------------------------------------------------------------ config inválida y errores inesperados
def test_lista_invalida_sale_con_2_sin_citar_su_contenido(tmp_path, capfd):
    bad = tmp_path / "bad.json"
    bad.write_text('[{"manager": "A", "name": "IE00SYNTH001"}]', encoding="utf-8")
    t = FakeTime()
    rc = fp.main(["--names", str(bad), "--json-out", str(tmp_path / "o.json")], env={}, client=World(), today=TODAY, clock=t.clock, sleep=t.sleep)
    cap = capfd.readouterr()
    assert rc == 2 and "IE00SYNTH001" not in cap.out + cap.err and not (tmp_path / "o.json").exists()


def test_un_fallo_inesperado_solo_imprime_la_clase_nunca_el_mensaje(tmp_path, capfd, monkeypatch):
    def boom(*a, **k):
        raise RuntimeError(f"mensaje con {ISINS[0]} y {RESOLVED_TICKER}")
    monkeypatch.setattr(fp, "render_summary", boom)
    rc, _, _, _ = run_main(tmp_path, env={"PROBE_ISINS": ISINS[0]}, client=World(isin_specs=ISINS))
    cap = capfd.readouterr()
    assert rc == 1 and "RuntimeError" in cap.out + cap.err
    assert_no_leak([strip_mask_commands(cap.out), cap.err], SECRETS)


# ------------------------------------------------------------------ CONTROLES NEGATIVOS DE PRIVACIDAD
@pytest.mark.parametrize("n_isins", [1, 4, 5, 8])
def test_ningun_valor_de_la_seccion_isin_llega_a_resumen_logs_ni_json(tmp_path, capfd, caplog, n_isins):
    raw = ",".join(ISINS[:n_isins])
    with caplog.at_level(logging.DEBUG):
        rc, md, js, _ = run_main(tmp_path, env={"PROBE_ISINS": raw}, client=World(noisy=True, isin_specs=ISINS))
    cap = capfd.readouterr()
    assert rc == 0
    assert_no_leak([md, js, strip_mask_commands(cap.out), cap.err, caplog.text, "\n".join(r.getMessage() for r in caplog.records)], SECRETS)
    assert f"::add-mask::{ISINS[0]}" in cap.out                         # y SÍ se enmascaró antes de usarlo
    assert cap.out.index(f"::add-mask::{ISINS[0]}") < cap.out.index("[1/")     # antes de cualquier línea de progreso


def test_el_detector_de_fugas_salta_si_algo_se_cuela():
    """Control del control: si un ISIN, un ticker resuelto o un nombre llegara a una salida, el detector FALLA."""
    for leak in (ISINS[2], RESOLVED_TICKER, RESOLVED_NAME):
        with pytest.raises(AssertionError):
            assert_no_leak(["todo normal", f"linea con {leak} dentro"], SECRETS)


def test_si_el_resumen_se_contaminara_con_el_ticker_de_un_isin_la_prueba_lo_detecta(tmp_path, monkeypatch):
    real = fp.render_summary

    def leaky(*a, **k):
        return real(*a, **k) + f"\nresuelto: {RESOLVED_TICKER}\n"
    monkeypatch.setattr(fp, "render_summary", leaky)
    rc, md, js, _ = run_main(tmp_path, env={"PROBE_ISINS": ",".join(ISINS[:5])}, client=World(isin_specs=ISINS))
    with pytest.raises(AssertionError):
        assert_no_leak([md, js], SECRETS)


def test_con_menos_de_cinco_isin_el_resumen_solo_dice_probados_y_resueltos(tmp_path):
    rc, md, js, _ = run_main(tmp_path, env={"PROBE_ISINS": ",".join(ISINS[:3])}, client=World(isin_specs=ISINS))
    assert "3 probados / 3 resueltos" in md
    for banned in ("0-3", "4-7", "8-30", ">30", "Divisas", "divisas"):
        assert banned not in md.split("ISIN")[-1], banned


def test_con_cinco_o_mas_isin_salen_los_agregados(tmp_path):
    rc, md, js, _ = run_main(tmp_path, env={"PROBE_ISINS": ",".join(ISINS[:6])}, client=World(isin_specs=ISINS))
    tail = md.split("ISIN")[-1]
    assert "6 probados" in tail and "6 resueltos" in tail and "0-3" in tail and "EUR" in tail


def test_secreto_ausente_dice_omitida_y_no_toca_yahoo_para_isin(tmp_path):
    c = World(isin_specs=ISINS)
    rc, md, js, _ = run_main(tmp_path, client=c)
    assert "omitida" in md and not any(call[1] in ISINS for call in c.calls)


def test_sin_variable_de_resumen_se_escribe_en_stdout(tmp_path, capfd):
    t = FakeTime()
    rc = fp.main(["--names", str(names_file(tmp_path)), "--json-out", str(tmp_path / "o.json")], env={}, client=World(), today=TODAY, clock=t.clock, sleep=t.sleep)
    assert rc == 0 and "| Gestora |" in capfd.readouterr().out
