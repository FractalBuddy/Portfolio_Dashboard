"""Sonda de símbolos: una línea por símbolo en el LOG del paso (stdout), no solo en el resumen del job. Solo se imprime lo que ya se calcula (veredicto,
nombre del registro, nombre/bolsa/tipo/divisa de Yahoo saneados con `clean`, última fecha y último cierre): ni el veredicto, ni los umbrales, ni el JSON
cambian. Yahoo simulado: ningún test sale a Internet."""
import json
import re
from datetime import timedelta

import symbol_probe as sp
from test_symbol_probe import TODAY, FakeYahoo, frame, mk

RAW_NAME = "Ev|il <b>Corp</b> `x`\r\n:: injected\t"          # basura típica para romper Markdown, JSON o el log de Actions (lado Yahoo)
REG_NAME = "Reg|istro `x` Ørsted :: ok"                           # lo máximo que deja pasar el registro (sin <, > ni controles), con acento


class MetaYahoo(FakeYahoo):
    """Como FakeYahoo pero con los metadatos que se le pidan por símbolo."""
    def __init__(self, table, meta=None):
        super().__init__(table)
        self.meta = meta or {}

    def __call__(self, sym):
        df, default = super().__call__(sym)
        return df, self.meta.get(sym, default)


def lines_of(out, sym):
    return [ln for ln in out.splitlines() if ln.startswith(sym + " ")]


def write_registry(tmp_path, entries):
    p = tmp_path / "r.json"
    p.write_text(json.dumps({"schema": 1, "symbols": entries}), encoding="utf-8")
    return str(p)


def run_main(tmp_path, entries, table, meta=None, extra=()):
    reg = write_registry(tmp_path, entries)
    js = tmp_path / "o" / "p.json"
    code = sp.main(["--registry", reg, "--json-out", str(js), *extra], fetcher=MetaYahoo(table, meta))
    return code, json.loads(js.read_text(encoding="utf-8"))


def test_linea_para_ok_con_todo_lo_necesario_para_comparar(tmp_path, capsys):
    ent = [mk("AAA", name="Alpha Corp")]
    code, doc = run_main(tmp_path, ent, {"AAA": frame()})
    out = capsys.readouterr().out
    (ln,) = lines_of(out, "AAA")
    last_close = doc["results"][0]["last_close"]
    for part in ("OK", "Alpha Corp", "Name of AAA", "NasdaqGS", "EQUITY", "USD", (TODAY - timedelta(days=1)).isoformat(), str(last_close)):
        assert part in ln, (part, ln)
    assert code == 0


def test_linea_para_cada_veredicto_no_ok_alcanzable_y_el_codigo_de_salida_sigue_siendo_1(tmp_path, capsys):
    ent = [mk("WRONGCUR", "SAR"), mk("NOCUR"), mk("EMPTY"), mk("FROZEN"), mk("PENCE", "GBp", "subunit"), mk("NET")]
    table = {"WRONGCUR": frame(cur="USD"), "NOCUR": frame(cur=None), "EMPTY": frame(n=1), "FROZEN": frame(end=TODAY - timedelta(days=60)),
             "PENCE": frame(cur="GBP"), "NET": ConnectionError("x")}
    code, doc = run_main(tmp_path, ent, table)
    out = capsys.readouterr().out
    want = {"WRONGCUR": "UNIT", "NOCUR": "CURRENCY_MISSING", "EMPTY": "EMPTY", "FROZEN": "FROZEN", "PENCE": "UNIT", "NET": "HTTP_ERROR"}
    for sym, verdict in want.items():
        lns = lines_of(out, sym)
        assert len(lns) == 1 and re.search(r"\b%s\b" % verdict, lns[0]) and "OK" not in lns[0].split(), (sym, lns)
    assert [r["verdict"] for r in doc["results"]] == ["unit", "currency_missing", "empty", "frozen", "unit", "http_error"]
    assert code == 1


def test_linea_para_not_tried_tope_y_no_peticiones_de_mas(tmp_path, capsys):
    ent = [mk("A1"), mk("B1"), mk("C1")]
    code, _doc = run_main(tmp_path, ent, {"A1": frame(), "B1": frame(), "C1": frame()}, extra=("--max-symbols", "1"))
    out = capsys.readouterr().out
    assert len(lines_of(out, "A1")) == len(lines_of(out, "B1")) == len(lines_of(out, "C1")) == 1
    assert "NOT_TRIED" in lines_of(out, "B1")[0] and "tope" in lines_of(out, "B1")[0] and code == 1


def test_la_linea_cubre_todos_los_veredictos_incluido_price_range_que_el_filtro_previo_hace_inalcanzable():
    base = {"symbol": "S1", "name": "Reg", "currency": "USD", "unit": "currency", "status": "dormant"}
    for v in sp.VERDICTS:
        ln = sp.log_line({**base, "verdict": v, "yahoo_name": "Y", "yahoo_exchange": "X", "yahoo_type": "EQUITY", "yahoo_currency": "USD",
                          "last_date": "2026-11-18", "last_close": 12.5, "why": "motivo"})
        assert "\n" not in ln and ln.startswith("S1 ") and v.upper() in ln, (v, ln)


def test_nombres_raros_salen_saneados_y_una_sola_linea_por_simbolo(tmp_path, capsys):
    ent = [mk("WEIRD", name=REG_NAME)]
    meta = {"WEIRD": {"longName": RAW_NAME, "fullExchangeName": "Bo|lsa<>\n", "instrumentType": "EQ`UITY"}}
    run_main(tmp_path, ent, {"WEIRD": frame()}, meta)
    out = capsys.readouterr().out
    lns = lines_of(out, "WEIRD")
    assert len(lns) == 1
    ln = lns[0]
    assert not re.search(r"[|<>`\r\n\t]", ln) and "Ev" in ln and "Corp" in ln and "Bolsa" in ln and "EQUITY" in ln
    assert not any(row.startswith("::") for row in out.splitlines())          # nada se interpreta como comando del log de Actions
    assert "Reg/istro" in ln and "Ørsted" in ln                               # el nombre del registro conserva los acentos (se compara a ojo)
    assert "injected" in ln and ln.count("WEIRD") == 1                        # el texto raro sigue ahí (saneado) y no parte la línea


def test_el_json_el_resumen_y_el_recuento_final_no_cambian(tmp_path, capsys):
    ent = [mk("AAA"), mk("BBB", "SAR")]
    code, doc = run_main(tmp_path, ent, {"AAA": frame(), "BBB": frame(cur="USD")})
    out = capsys.readouterr().out
    assert code == 1 and out.rstrip().splitlines()[-1] == "sonda: 2 símbolos; ok 1, unit 1"          # el recuento sigue siendo la última línea
    assert set(doc) == {"schema", "generated_at", "period", "results"}
    assert set(doc["results"][0]) == {"symbol", "name", "currency", "unit", "status", "verdict", "yahoo_currency", "yahoo_name", "yahoo_exchange",
                                      "yahoo_type", "last_date", "last_close"}
    assert set(doc["results"][1]) == set(doc["results"][0])


def test_probe_sin_emit_se_comporta_igual_y_con_emit_se_llama_una_vez_por_resultado_en_orden():
    ent = [mk("A1"), mk("B1", "SAR")]
    table = {"A1": frame(), "B1": frame(cur="USD")}
    seen = []
    clock = iter(range(0, 10_000, 5))
    with_emit = sp.probe(ent, 60, fetcher=FakeYahoo(table), sleep=lambda s: None, clock=lambda: next(clock), today=TODAY, emit=seen.append)
    clock = iter(range(0, 10_000, 5))
    plain = sp.probe(ent, 60, fetcher=FakeYahoo(table), sleep=lambda s: None, clock=lambda: next(clock), today=TODAY)
    assert with_emit == plain and seen == with_emit


def test_campos_que_yahoo_no_da_salen_como_raya_y_nunca_como_none(tmp_path, capsys):
    run_main(tmp_path, [mk("BARE")], {"BARE": frame()}, {"BARE": {}})
    (ln,) = lines_of(capsys.readouterr().out, "BARE")
    assert "None" not in ln and "yahoo: — · — · — · USD" in ln, ln
