"""Sonda de símbolos (scripts/symbol_probe.py + .github/workflows/symbol-probe.yml): solo lectura, mismas reglas que el generador y divisa obligatoria
para activar. Yahoo simulado: ningún test sale a Internet."""
import json
import pathlib
import re
import subprocess
import sys
from datetime import date, timedelta

import pandas as pd
import pytest

import feed_v2_common as c
import symbol_probe as sp

ROOT = pathlib.Path(__file__).resolve().parent.parent
WFP = ROOT / ".github" / "workflows" / "symbol-probe.yml"
TODAY = date(2026, 11, 19)


def mk(sym, cur="USD", unit="currency", status="dormant", name=None):
    return {"symbol": sym, "kind": "yahoo", "name": name or sym, "currency": cur, "unit": unit, "tier": "warm", "status": status, "fail_streak": 0}


def frame(n=20, end=TODAY - timedelta(days=1), price=100.0, cur="USD"):
    days = pd.bdate_range(end=pd.Timestamp(end), periods=n)
    df = pd.DataFrame({"Open": price, "High": price * 1.01, "Low": price * 0.99, "Close": [price + i * 0.1 for i in range(n)], "Volume": 1000}, index=days)
    if cur:
        df.attrs["currency"] = cur
    return df


class FakeYahoo:
    def __init__(self, table):
        self.table, self.calls = table, []

    def __call__(self, sym):
        self.calls.append(sym)
        v = self.table[sym]
        if isinstance(v, Exception):
            raise v
        return v, {"longName": "Name of %s" % sym, "fullExchangeName": "NasdaqGS", "instrumentType": "EQUITY"}


def run(entries, table, max_symbols=60):
    clock = iter(range(0, 10_000, 5))                     # el reloj avanza 5 s por llamada: nunca hace falta dormir
    slept = []
    res = sp.probe(entries, max_symbols, fetcher=FakeYahoo(table), sleep=slept.append, clock=lambda: next(clock), today=TODAY)
    return res, slept


# ------------------------------------------------------------------ veredictos
def test_ok_y_cada_clase_de_fallo():
    entries = [mk("OK1"), mk("WRONGCUR", "SAR"), mk("NOCUR"), mk("EMPTY"), mk("FROZEN"), mk("PENCE", "GBp", "subunit"), mk("RANGE"), mk("NET")]
    table = {"OK1": frame(), "WRONGCUR": frame(cur="USD"), "NOCUR": frame(cur=None), "EMPTY": frame(n=1),
             "FROZEN": frame(end=TODAY - timedelta(days=60)), "PENCE": frame(cur="GBP"), "RANGE": frame(price=5e7), "NET": ConnectionError("x")}
    res, _ = run(entries, table)
    got = {r["symbol"]: r["verdict"] for r in res}
    assert got == {"OK1": "ok", "WRONGCUR": "unit", "NOCUR": "currency_missing", "EMPTY": "empty", "FROZEN": "frozen",
                   "PENCE": "unit", "RANGE": "empty", "NET": "http_error"}
    ok = res[0]
    assert ok["yahoo_currency"] == "USD" and ok["yahoo_name"] == "Name of OK1" and ok["last_date"] == (TODAY - timedelta(days=1)).isoformat()


def test_subunidad_correcta_pasa_y_alias_de_la_misma_subunidad_tambien():
    res, _ = run([mk("A.L", "GBp", "subunit"), mk("B.L", "GBp", "subunit")], {"A.L": frame(cur="GBp"), "B.L": frame(cur="GBX")})
    assert [r["verdict"] for r in res] == ["ok", "ok"]


def test_indices_y_divisas_no_exigen_divisa_como_en_el_generador():
    res, _ = run([mk("^X", "USD", "points"), mk("EURUSD=X", "USD", "rate")], {"^X": frame(cur=None), "EURUSD=X": frame(cur=None, price=1.1)})
    assert [r["verdict"] for r in res] == ["ok", "ok"]


def test_429_para_limpio_y_el_resto_queda_sin_probar():
    import fetch_market_data as fm

    class RL(Exception):
        pass
    entries = [mk("A"), mk("B"), mk("C")]
    orig = fm._is_rate_limit
    try:
        fm._is_rate_limit = lambda e: isinstance(e, RL)
        res, _ = run(entries, {"A": frame(), "B": RL("429"), "C": frame()})
    finally:
        fm._is_rate_limit = orig
    assert [r["verdict"] for r in res] == ["ok", "not_tried", "not_tried"]


def test_tres_fallos_de_red_seguidos_paran_y_el_tope_de_simbolos_se_respeta():
    entries = [mk("S%d" % i) for i in range(6)]
    res, _ = run(entries, {"S0": OSError(), "S1": OSError(), "S2": OSError(), "S3": frame(), "S4": frame(), "S5": frame()})
    assert [r["verdict"] for r in res] == ["http_error"] * 3 + ["not_tried"] * 3
    res, _ = run(entries, {s["symbol"]: frame() for s in entries}, max_symbols=2)
    assert [r["verdict"] for r in res] == ["ok", "ok"] + ["not_tried"] * 4


def test_ritmo_al_menos_un_segundo_entre_peticiones():
    t = [0.0]
    slept = []

    def clock():
        return t[0]

    def sleep(s):
        slept.append(s)
        t[0] += s
    sp.probe([mk("A"), mk("B"), mk("C")], 60, fetcher=FakeYahoo({"A": frame(), "B": frame(), "C": frame()}), sleep=sleep, clock=clock, today=TODAY)
    assert len(slept) == 2 and all(s >= sp.MIN_GAP_SECONDS for s in slept)


def test_texto_de_yahoo_saneado_para_markdown_y_json():
    s = sp.clean("Evil | <script>`x`\n| name")
    assert s and not re.search(r"[|<>`\n\r]", s) and "script" in s and s.startswith("Evil")
    assert sp.clean("x" * 200) == "x" * 60 and sp.clean(None) is None and sp.clean("|||") is None
    md = sp.summary_md([{"symbol": "A", "verdict": "ok", "name": "Ap|ple", "currency": "USD", "unit": "currency", "yahoo_name": "N", "yahoo_exchange": None,
                         "yahoo_type": None, "yahoo_currency": "USD", "last_date": "2026-11-18", "last_close": 1.0}], "t")
    assert "Ap/ple" in md and md.count("\n| `A`") == 1


# ------------------------------------------------------------------ selección y CLI
TOP50_ACTIVADAS = {"SPCX", "2222.SR", "000660.KS", "1398.HK", "601288.SS", "601988.SS", "PM"}


def test_seleccion_por_defecto_son_las_dormant_y_symbols_debe_estar_en_el_registro(capsys):
    # REQUISITO CAMBIADO (activar top 50, auditorias/FIXES_ACTIVAR_TOP50.md): las 7 filas pasaron la sonda el 2026-11-20 y están `active`, así que el
    # registro real ya no tiene ninguna `dormant`. La selección por defecto se sigue comprobando igual de estricta sobre el registro real con esas 7
    # devueltas a `dormant` (el estado que tenían cuando se sondearon), y además: con el registro real tal cual no queda nada que probar.
    real = c.load_registry(ROOT / "registry.json")
    assert sp.select(real) == []
    assert sp.main(["--registry", str(ROOT / "registry.json")], fetcher=FakeYahoo({})) == 0                # sin peticiones: el fake no tiene nada
    assert "nada que probar" in capsys.readouterr().out
    reg = [dict(e, status="dormant") if e["symbol"] in TOP50_ACTIVADAS else e for e in real]
    dormant = sp.select(reg)
    assert dormant and all(e["status"] == "dormant" and e["kind"] == "yahoo" for e in dormant)
    assert {e["symbol"] for e in dormant} == TOP50_ACTIVADAS
    assert [e["symbol"] for e in sp.select(reg, "pm, PM ,SPCX")] == ["PM", "SPCX"]
    for bad in ("NOEXISTE", "BTC", "../x", "A;B"):                       # fuera del registro, cripto (no es yahoo) o inválido
        with pytest.raises(ValueError):
            sp.select(reg, bad)
    assert len(sp.select(reg, mode="all")) == sum(e["kind"] == "yahoo" for e in reg)


def test_main_escribe_resumen_y_json_y_el_codigo_de_salida(tmp_path, capsys):
    reg = tmp_path / "r.json"
    reg.write_text(json.dumps({"schema": 1, "symbols": [mk("AAA"), mk("BBB", "SAR")]}), encoding="utf-8")
    fake = FakeYahoo({"AAA": frame(), "BBB": frame(cur="SAR")})
    js, md = tmp_path / "o" / "p.json", tmp_path / "s.md"
    assert sp.main(["--registry", str(reg), "--json-out", str(js), "--summary-out", str(md)], fetcher=fake) == 0
    doc = json.loads(js.read_text(encoding="utf-8"))
    assert [r["verdict"] for r in doc["results"]] == ["ok", "ok"] and "| `BBB` | OK |" in md.read_text(encoding="utf-8")
    fake = FakeYahoo({"AAA": frame(), "BBB": frame(cur="USD")})
    assert sp.main(["--registry", str(reg), "--json-out", str(js)], fetcher=fake) == 1
    for args in (["--max-symbols", "0"], ["--max-symbols", "301"], ["--max-symbols", "x"], ["--symbols", "ZZZ"], ["--symbols", "A" * 2001]):
        assert sp.main(["--registry", str(reg)] + args, fetcher=fake) == 2, args


def test_el_script_arranca_sin_red_y_con_ayuda():
    r = subprocess.run([sys.executable, str(ROOT / "scripts" / "symbol_probe.py"), "--help"], capture_output=True, text=True, timeout=120,
                       cwd=str(ROOT / "scripts"))
    assert r.returncode == 0 and "--max-symbols" in r.stdout


def test_la_sonda_no_escribe_en_el_sitio_ni_importa_nada_de_publicacion():
    text = (ROOT / "scripts" / "symbol_probe.py").read_text(encoding="utf-8")
    for bad in ("generate(", "build_manifest", "write_json", "deploy", "supabase", "SUPABASE", "requests.post", "git "):
        assert bad not in text, bad


# ------------------------------------------------------------------ workflow
yaml = pytest.importorskip("yaml")


def wf():
    return yaml.safe_load(WFP.read_text(encoding="utf-8"))


def job():
    return wf()["jobs"]["probe"]


def step(part):
    hits = [s for s in job()["steps"] if part.lower() in s.get("name", "").lower()]
    assert len(hits) == 1, part
    return hits[0]


def test_wf_solo_manual_con_tres_inputs():
    on = wf().get("on", wf().get(True))
    assert set(on) == {"workflow_dispatch"} and set(on["workflow_dispatch"]["inputs"]) == {"symbols", "select", "max_symbols"}
    sel = on["workflow_dispatch"]["inputs"]["select"]
    assert sel["type"] == "choice" and sel["options"] == ["dormant", "all"] and sel["default"] == "dormant"


def test_wf_solo_lectura_sin_secretos_sin_despliegue_solo_main():
    text = WFP.read_text(encoding="utf-8")
    w = wf()
    assert w["permissions"] == {} and set(w["jobs"]) == {"probe"} and job()["permissions"] == {"contents": "read"}
    assert "secrets." not in text and "vars." not in text and "GITHUB_TOKEN" not in text
    for bad in ("contents: write", "id-token", "pages:", "deploy-pages", "upload-pages-artifact", "git push", "curl ", "SUPABASE", "environment:", "schedule"):
        assert bad not in text, bad
    assert "github.ref == 'refs/heads/main'" in job()["if"] and "||" not in job()["if"]
    assert job()["timeout-minutes"] <= 20 and step("check out")["with"]["persist-credentials"] is False


def test_wf_inputs_por_entorno_y_un_unico_comando():
    run_ = step("run symbol probe")
    assert "${{" not in run_["run"] and run_["run"].strip().startswith("python scripts/symbol_probe.py") and run_["run"].count("python") == 1
    assert run_["env"] == {"SYMBOLS": "${{ inputs.symbols }}", "SELECT": "${{ inputs.select }}", "MAX_SYMBOLS": "${{ inputs.max_symbols }}"}
    assert "--require-hashes" in step("install")["run"]


def test_wf_el_artefacto_es_el_json_de_la_sonda():
    up = step("upload")
    m = re.search(r"--json-out \"(\$RUNNER_TEMP/[\w./-]+)\"", step("run symbol probe")["run"])
    assert m and m.group(1).replace("$RUNNER_TEMP", "${{ runner.temp }}") == up["with"]["path"]
    assert up["with"]["name"] == "symbol-probe" and int(up["with"]["retention-days"]) <= 14


def test_ningun_otro_workflow_llama_a_la_sonda_y_nav_probe_no_cambia():
    for p in WFP.parent.glob("*.yml"):
        if p.name != WFP.name:
            assert "symbol_probe" not in p.read_text(encoding="utf-8"), p.name
