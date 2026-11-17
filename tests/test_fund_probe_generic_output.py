"""Sonda de fondos, GENÉRICOS 3/5: `main` con --sections, resumen y JSON de GENÉRICOS, y SEPARACIÓN total respecto de PROBE_ISINS (Yahoo simulado)."""
import json
import logging
import sys
import warnings
from datetime import date

import pytest

import fund_probe as fp
from fund_probe_fakes import TODAY, FakeClient, FakeTime, quote, rows

PRIV = [f"IE00SYNTH{i:03d}" for i in range(1, 7)]            # ISIN PRIVADOS sintéticos (construidos en ejecución)
PRIV_TICKER = "0P0PRIVATE9.F"
PRIV_NAME = "Nombre Privado Del Dueño"


def mkisin(n):
    base = f"ZZ00SYNTH{n:02d}"                                 # ISIN sintético con dígito de control VÁLIDO (calculado: la prueba de la lista lo verifica de forma independiente)
    return base + str(fp.isin_check_digit(base))


I1, I2, I3, I4, I5 = (mkisin(n) for n in range(1, 6))
GEN = [(I1, "Alpha World Fund USD Acc"), (I2, "Beta Index Fund EUR Acc"), (I3, "Gamma Bond Fund"), (I4, "Delta Fund"),
       (I5, "Epsilon Fund EUR")]
NAMES = [{"manager": "Gestora A", "name": "Alpha World Fund"}, {"manager": "Gestora B", "name": "Gamma Index Fund"}]


def H(last=date(2026, 11, 13), n=252, cur="EUR"):
    return {"rows": rows(last, n), "currency": cur}


class W(FakeClient):
    """Mundo sintético: nombres, GENÉRICOS (5) y PRIVADOS (que resuelven a un fondo «secreto»). `noisy` = el cliente cita el ISIN privado en stdout/stderr/log/avisos/excepción."""

    def __init__(self, noisy=False, priv=(), priv_429=False, generic_extra=None):
        s = {"Alpha World Fund": [quote("0P0NAMEA1.F", "Alpha World Fund")], "Gamma Index Fund": [],
             I1: [quote("0P0GENA01.F", "Alpha World Fund USD Acc"), quote("AWRD.L", "Alpha World UCITS ETF", "ETF", "LSE")],
             I2: [quote("BETA.DE", "Beta Index Fund EUR Acc", "ETF", "GER")],
             I3: [quote("GAM1.F", "Gamma Bond", "ETF", "FRA"), quote("GAM2.BE", "Gamma Bond", "ETF", "BER")],
             I4: [], I5: RuntimeError("fallo suelto")}
        h = {"0P0NAMEA1.F": H(), "0P0GENA01.F": H(), "BETA.DE": H(date(2026, 11, 10), 251), "GAM1.F": H(date(2026, 11, 13), 120), PRIV_TICKER: H()}
        super().__init__(s, h)
        self.noisy, self.priv_429 = noisy, priv_429
        for i in priv:
            self.searches[i] = [quote(PRIV_TICKER, PRIV_NAME)]
        if generic_extra:
            self.searches.update(generic_extra)

    def search(self, q):
        if q in PRIV:
            if self.noisy:
                print(f"buscando {q}")
                print(f"err {q}", file=sys.stderr)
                logging.getLogger("yfinance").error("$%s: possibly delisted", q)
                warnings.warn(f"aviso {q}")
            if self.priv_429:
                raise RuntimeError("HTTP Error 429: Too Many Requests")
        return super().search(q)

    def history(self, symbol, period=None):
        if self.noisy and symbol == PRIV_TICKER:
            print(f"hist {symbol} {PRIV_NAME}")
        return super().history(symbol, period)


def gfile(tmp_path, entries=GEN):
    p = tmp_path / "generic.json"
    p.write_text(json.dumps([{"isin": i, "name": n} for i, n in entries]), encoding="utf-8")
    return p


def nfile(tmp_path):
    p = tmp_path / "names.json"
    p.write_text(json.dumps(NAMES), encoding="utf-8")
    return p


def run_main(tmp_path, env=None, client=None, extra=(), sections="both", entries=GEN, tag="a"):
    d = tmp_path / tag
    d.mkdir(exist_ok=True)
    t = FakeTime()
    summ, js, gjs = d / "summary.md", d / "names.json", d / "generic.json"
    e = {"GITHUB_STEP_SUMMARY": str(summ), **(env or {})}
    rc = fp.main(["--names", str(nfile(d)), "--generic-isins", str(gfile(d, entries)), "--json-out", str(js), "--generic-json-out", str(gjs), "--sections", sections, *extra],
                 env=e, client=client or W(), today=TODAY, clock=t.clock, sleep=t.sleep)
    rd = lambda p: p.read_text(encoding="utf-8") if p.exists() else ""      # noqa: E731
    return rc, rd(summ), rd(js), rd(gjs), t


def generic_block(md):
    """La parte GENÉRICOS del resumen: desde su cabecera hasta la de la sección ISIN privada."""
    a = md.index("### Sección GENÉRICOS")
    b = md.index("### Sección ISIN")
    return md[a:b]


def table_row(md, first_cell):
    for line in md.splitlines():
        cells = [c.strip() for c in line.strip().strip("|").split("|")]
        if line.startswith("|") and cells and cells[0] == first_cell:
            return cells
    raise AssertionError(f"no hay fila {first_cell!r}")


# ------------------------------------------------------------------ --sections
def test_por_defecto_son_ambas_secciones_y_se_escriben_los_dos_json(tmp_path):
    rc, md, js, gjs, _ = run_main(tmp_path)
    assert rc == 0 and json.loads(js)["names"] and json.loads(gjs)["isins"]
    assert "### Por gestora" in md and "### Sección GENÉRICOS" in md


def test_sections_names_no_toca_los_isin_genericos_ni_pide_su_fichero(tmp_path):
    c = W()
    rc, md, js, gjs, _ = run_main(tmp_path, client=c, sections="names")
    assert rc == 0 and not any(q.startswith("ZZ") for k, q in c.calls if k == "search") and "Sección GENÉRICOS: omitida" in md
    assert json.loads(gjs)["section"] == "omitida" and json.loads(gjs)["isins"] == []


def test_sections_generic_no_busca_los_nombres_y_la_parte_de_nombres_queda_omitida(tmp_path):
    c = W()
    rc, md, js, gjs, _ = run_main(tmp_path, client=c, sections="generic")
    searched = [q for k, q in c.calls if k == "search"]
    assert rc == 0 and searched == [i for i, _ in GEN] and "Sección NOMBRES: omitida" in md and json.loads(js)["names"] == []
    assert "### Por gestora" not in md


def test_sections_invalido_sale_con_2(tmp_path, capsys):
    with pytest.raises(SystemExit) as ei:
        fp.main(["--sections", "todo"], env={}, client=W(), today=TODAY)
    assert ei.value.code == 2


def test_lista_generica_invalida_sale_con_2_y_lista_los_malos(tmp_path, capfd):
    rc, md, js, gjs, _ = run_main(tmp_path, entries=[("NOPE", "x"), GEN[0], GEN[0]])
    out = capfd.readouterr().out
    assert rc == 2 and "NOPE (formato)" in out and "(duplicado)" in out and md == ""


def test_con_sections_names_una_lista_generica_rota_no_molesta(tmp_path):
    rc, *_ = run_main(tmp_path, entries=[("NOPE", "x")], sections="names")
    assert rc == 0


# ------------------------------------------------------------------ resumen y JSON de GENÉRICOS
def test_resumen_trae_una_fila_por_isin_con_todos_los_campos(tmp_path):
    rc, md, js, gjs, _ = run_main(tmp_path)
    blk = generic_block(md)
    row = table_row(blk, I1)
    # ISIN | esperado | estado | ticker | tipo | bolsa | divisa | nombre Yahoo | similitud | cands | barras | última | retraso
    assert row == [I1, "Alpha World Fund USD Acc", "reciente", "0P0GENA01.F", "0P", "FRA", "EUR", "Alpha World Fund USD Acc", "1.0", "2", "252", "2026-11-13", "3"]
    assert table_row(blk, I2)[2:7] == ["retrasado", "BETA.DE", "etf", "GER", "EUR"] and table_row(blk, I4)[2] == "no_encontrado"
    assert table_row(blk, I5)[2] == "error" and table_row(blk, I3)[2] == "reciente"


def test_resumen_trae_agregados_reglas_y_empates(tmp_path):
    _, md, _, _, _ = run_main(tmp_path)
    blk = generic_block(md)
    assert "Resueltos: 3 / 5" in blk                                                           # los 3 primeros
    assert "con ≥ 250 barras: 2" in blk and "recientes (≤ 3 d): 2" in blk                      # 252 y 251 barras; lag 3 y 3 (el de 6 días es «retrasado»)
    assert "0P 1" in blk and "etf 2" in blk and "otras 0" in blk
    assert "GER 1" in blk and "FRA 2" in blk
    assert "Candidatas sin criterio" in blk and "GAM1.F (FRA)" in blk and "GAM2.BE (BER)" in blk
    assert "Jaccard" in blk                                                                    # la regla de similitud está escrita en el propio resumen
    assert "Dígito de control no válido: ninguno" in blk
    # 1º: dos candidatas (0P en FRA y un ETF en LSE): la regla principal elige la 0P; «liquidez primero» elegiría el ETF; el orden de Yahoo coincide con la principal. 3º: empate, no cambia.
    assert "2 ISIN con varias candidatas · 1 sin criterio (empate)" in blk and "con otra regla: liquidez primero 1, solo orden de Yahoo 0" in blk


def test_digito_de_control_malo_se_lista_en_el_resumen_y_se_sonda_igual(tmp_path):
    bad = I3[:-1] + str((int(I3[-1]) + 1) % 10)
    c = W(generic_extra={bad: []})
    _, md, _, gjs, _ = run_main(tmp_path, client=c, entries=[GEN[0], (bad, "Gamma Bond Fund")], sections="generic")
    assert f"Dígito de control no válido: {bad}" in generic_block(md)
    d = json.loads(gjs)
    assert d["summary"]["check_digit_failures"] == [bad] and [x["status"] for x in d["isins"]] == ["reciente", "no_encontrado"] and ("search", bad) in c.calls


def test_json_generico_tiene_claves_fijas_y_todas_las_candidatas_de_los_empates(tmp_path):
    _, _, _, gjs, _ = run_main(tmp_path)
    d = json.loads(gjs)
    assert list(d) == ["section", "generated", "yfinance", "recent_days", "min_year_bars", "request_cap", "requests_generic", "stopped", "rules", "errors", "summary", "isins"]
    assert d["section"] == "ok" and d["request_cap"] == 400 and d["stopped"] is None and d["requests_generic"] == 3 + 3 + 3 + 1 + 1
    r = {x["isin"]: x for x in d["isins"]}
    assert r[I3]["tie"] is True and [c["ticker"] for c in r[I3]["all_candidates"]] == ["GAM1.F", "GAM2.BE"]
    assert set(r[I1]) >= {"isin", "expected_name", "ticker", "exchange", "currency", "yahoo_name", "name_similarity", "candidates", "bars_1y", "last_date", "lag_days", "status"}
    assert d["rules"] == fp.GENERIC_RULES_TEXT


def test_las_5_clases_de_error_estan_siempre_en_el_json_generico(tmp_path):
    _, _, _, gjs, _ = run_main(tmp_path)
    assert list(json.loads(gjs)["errors"]) == list(fp.ERROR_CLASSES)


# ------------------------------------------------------------------ tope, ritmo y 429
def test_el_tope_cuenta_los_isin_genericos_y_se_respeta(tmp_path):
    rc, md, js, gjs, _ = run_main(tmp_path, sections="generic", extra=["--max-requests", "4"])
    d = json.loads(gjs)
    assert rc == 0 and d["request_cap"] == 4 and d["stopped"] == "presupuesto" and d["requests_generic"] <= 4
    assert [x["status"] for x in d["isins"]].count("no_probado") >= 1


def test_el_tope_se_puede_subir_hasta_400_y_no_mas(tmp_path):
    for asked, got in (("400", 400), ("9999", 400), ("300", 300)):
        _, _, _, gjs, _ = run_main(tmp_path, sections="generic", extra=["--max-requests", asked], tag=f"c{asked}")
        assert json.loads(gjs)["request_cap"] == got


def test_ritmo_de_un_segundo_entre_todas_las_llamadas_de_ambas_secciones(tmp_path):
    stamps, t = [], FakeTime()

    class C(W):
        def search(self, q):
            stamps.append(t.clock())
            return super().search(q)

        def history(self, s, period=None):
            stamps.append(t.clock())
            return super().history(s, period)
    d = tmp_path
    fp.main(["--names", str(nfile(d)), "--generic-isins", str(gfile(d)), "--json-out", str(d / "n.json"), "--generic-json-out", str(d / "g.json")],
            env={"PROBE_ISINS": ",".join(PRIV[:5])}, client=C(priv=PRIV), today=TODAY, clock=t.clock, sleep=t.sleep)
    assert len(stamps) > 15 and all(b - a >= 1.0 - 1e-9 for a, b in zip(stamps, stamps[1:]))


def test_429_en_genericos_para_limpio_marca_el_resto_y_sale_con_cero(tmp_path, capfd):
    c = W(generic_extra={I2: RuntimeError("429 Too Many Requests")})
    rc, md, js, gjs, _ = run_main(tmp_path, client=c, sections="generic")
    d = json.loads(gjs)
    assert rc == 0 and d["stopped"] == "429" and [x["status"] for x in d["isins"]] == ["reciente", "no_probado", "no_probado", "no_probado", "no_probado"]
    assert "Sonda parada limpiamente" in capfd.readouterr().out and ("search", I3) not in c.calls


# ------------------------------------------------------------------ SEPARACIÓN respecto de PROBE_ISINS
SECRETS = PRIV + [PRIV_TICKER, PRIV_NAME, "Nombre Privado"]


def leaks(blob, secrets=SECRETS):
    return [s for s in secrets if s in blob]


def no_masks(text):
    return "\n".join(line for line in text.splitlines() if not line.startswith("::add-mask::"))


def test_la_salida_detallada_de_genericos_es_identica_con_y_sin_el_secreto(tmp_path):
    a = run_main(tmp_path, env={}, tag="sin")
    b = run_main(tmp_path, env={"PROBE_ISINS": ",".join(PRIV)}, client=W(priv=PRIV), tag="con")
    assert a[3] == b[3] and generic_block(a[1]) == generic_block(b[1])                    # JSON de GENÉRICOS y bloque del resumen, byte a byte
    assert a[3] != "" and "ISIN: 6 probados / 6 resueltos" in b[1]                       # y el secreto SÍ se usó (la sección privada corrió)


@pytest.mark.parametrize("cap", ["400", "30", "14", "12"])
def test_identica_tambien_con_presupuesto_justo_porque_los_genericos_van_antes_que_los_privados(tmp_path, cap):
    """El único canal posible entre las dos secciones es el presupuesto compartido: GENÉRICOS corre ANTES, así que el secreto no puede quitarle peticiones."""
    a = run_main(tmp_path, env={}, extra=["--max-requests", cap], tag="sin")
    b = run_main(tmp_path, env={"PROBE_ISINS": ",".join(PRIV)}, client=W(priv=PRIV), extra=["--max-requests", cap], tag="con")
    assert a[3] == b[3] and generic_block(a[1]) == generic_block(b[1])


def test_un_429_dentro_de_la_seccion_privada_no_contamina_el_json_generico(tmp_path):
    a = run_main(tmp_path, env={}, tag="sin")
    b = run_main(tmp_path, env={"PROBE_ISINS": ",".join(PRIV)}, client=W(priv=PRIV, priv_429=True), tag="con")
    assert a[3] == b[3] and json.loads(b[3])["stopped"] is None                            # la parada ocurrió después y es de la sección privada
    assert json.loads(b[2])["stopped"] == "429"                                            # (el JSON de NOMBRES ya reflejaba el estado final del run: decisión anterior, sin cambios)


@pytest.mark.parametrize("n", [1, 4, 6])
def test_ningun_valor_de_la_seccion_privada_llega_a_la_salida_detallada(tmp_path, capfd, caplog, n):
    caplog.set_level(logging.DEBUG)
    with warnings.catch_warnings(record=True) as wrec:
        warnings.simplefilter("always")
        rc, md, js, gjs, _ = run_main(tmp_path, env={"PROBE_ISINS": ",".join(PRIV[:n])}, client=W(noisy=True, priv=PRIV))
    cap = capfd.readouterr()
    assert rc == 0
    for name, blob in (("json_generico", gjs), ("bloque_genericos", generic_block(md)), ("stdout", no_masks(cap.out)), ("stderr", cap.err), ("log", caplog.text),
                       ("avisos", "\n".join(str(w.message) for w in wrec)), ("json_nombres", js)):
        assert leaks(blob) == [], f"fuga en {name}"


def test_el_json_generico_no_tiene_ni_una_clave_de_la_seccion_privada(tmp_path):
    _, _, _, gjs, _ = run_main(tmp_path, env={"PROBE_ISINS": ",".join(PRIV)}, client=W(priv=PRIV))
    text = gjs.lower()
    for bad in ("probe_isins", "lag_buckets", "not_probed", "tested", "invalid", "small"):
        assert bad not in text, bad


def test_control_negativo_el_detector_de_fugas_salta_si_se_cuela_un_valor_privado(tmp_path):
    _, md, _, gjs, _ = run_main(tmp_path, env={"PROBE_ISINS": ",".join(PRIV)}, client=W(priv=PRIV))
    for value in (PRIV[0], PRIV_TICKER, PRIV_NAME):
        assert value in leaks(gjs + f" {value}") and value in leaks(generic_block(md).replace(I1, value, 1))


def test_control_negativo_los_valores_privados_solo_salen_en_las_ordenes_de_mascara(tmp_path, capfd):
    run_main(tmp_path, env={"PROBE_ISINS": ",".join(PRIV)}, client=W(priv=PRIV))
    out = capfd.readouterr().out
    assert leaks(out) != [] and leaks(no_masks(out)) == []                                  # los valores solo aparecen en las órdenes ::add-mask::, que el runner sustituye por ***


def test_el_orden_de_ejecucion_es_genericos_antes_que_privada(tmp_path, monkeypatch):
    """Es lo que hace que el presupuesto compartido no sea un canal entre secciones (la mutación «privada primero» se prueba aparte, en el informe)."""
    real_generic, real_private = fp.run_generic, fp.run_isin_section
    order = []
    monkeypatch.setattr(fp, "run_generic", lambda *a, **k: (order.append("g"), real_generic(*a, **k))[1])
    monkeypatch.setattr(fp, "run_isin_section", lambda *a, **k: (order.append("p"), real_private(*a, **k))[1])
    run_main(tmp_path, env={"PROBE_ISINS": ",".join(PRIV)}, client=W(priv=PRIV), tag="o")
    assert order == ["g", "p"]                                                              # el orden real: GENÉRICOS antes que PRIVADA
