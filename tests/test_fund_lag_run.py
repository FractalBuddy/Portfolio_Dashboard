"""Medición del retraso de fondos 0P con Yahoo SIMULADO: líneas, ritmo, tope, 429, autolímite y salidas (nada toca la red)."""
import json
import pathlib
import re
import subprocess
import sys
from datetime import date, datetime, timezone

import fund_lag as FL
import fund_lag_common as C
import fund_probe as fp
from fund_probe_fakes import FakeClient, FakeTime, hist_df, rows

ROOT = pathlib.Path(__file__).resolve().parent.parent
TICKERS = C.load_tickers(ROOT / "config" / "fund_lag_tickers.json")
NAMES = [t["ticker"] for t in TICKERS]
FUNDS, ETFS = NAMES[:7], NAMES[7:]
TUE_NOON = datetime(2026, 11, 17, 12, 17, 5, tzinfo=timezone.utc)          # martes: el caso medido el 17-11
FRI = date(2026, 11, 13)


def hist(last, n=5, cur="EUR"):
    return {"rows": rows(last, n), "currency": cur}


def world(fund_last=FRI, etf_last=date(2026, 11, 17)):
    h = {t: hist(fund_last) for t in FUNDS}
    h.update({t: hist(etf_last) for t in ETFS})
    return h


class TimedClient(FakeClient):
    """Guarda el instante (del reloj falso) de cada petición para comprobar el ritmo."""

    def __init__(self, ft, **kw):
        super().__init__(**kw)
        self.ft, self.at = ft, []

    def history(self, symbol, period=None):
        self.at.append(self.ft.now)
        return super().history(symbol, period)


def run(tmp_path, histories=None, argv=(), head=None, head_state="missing", now=TUE_NOON, env_extra=None, client="auto", default_history=None):
    ft = FakeTime()
    out, gh_out, summ = tmp_path / "new.jsonl", tmp_path / "gh_out", tmp_path / "summary.md"
    argv = ["--out", str(out), *argv]
    if head is not None:
        hp = tmp_path / "head.jsonl"
        hp.write_text(head)
        argv += ["--registry-head", str(hp)]
    argv += ["--head-state", head_state]
    cl = TimedClient(ft, histories=world() if histories is None else histories, default_history=default_history) if client == "auto" else client
    env = {"GITHUB_OUTPUT": str(gh_out), "GITHUB_STEP_SUMMARY": str(summ)}
    env.update(env_extra or {})
    rc = FL.main(argv, env=env, client=cl, now=lambda: now, clock=ft.clock, sleep=ft.sleep)
    return rc, cl, ft, out, gh_out, summ


def lines(out):
    return [json.loads(ln) for ln in out.read_text().splitlines()]


def outputs(p):
    return dict(ln.split("=", 1) for ln in p.read_text().splitlines()) if p.exists() else {}


# ------------------------------------------------------------------ medida
def test_una_linea_por_ticker_en_el_orden_de_la_lista_con_los_retrasos_esperados(tmp_path):
    rc, cl, ft, out, gh, summ = run(tmp_path)
    recs = lines(out)
    assert rc == 0 and [r["ticker"] for r in recs] == NAMES
    assert all(tuple(r) == C.RECORD_KEYS and r["status"] == "ok" and r["measured_at"] == "2026-11-17T12:17:05Z" for r in recs)
    for r in recs[:7]:                                                      # fondos: última barra el viernes 13, medido el martes 17
        assert (r["last_bar"], r["bars"], r["lag_bd"], r["lag_wd"], r["lag_days"]) == ("2026-11-13", 5, 2, 2, 4)
    for r in recs[7:]:                                                      # ETF de control: barra de hoy
        assert (r["last_bar"], r["lag_bd"], r["lag_wd"], r["lag_days"]) == ("2026-11-17", 0, 0, 0)
    assert C.check_lines(out.read_text(), set(NAMES))[1] == []
    assert outputs(gh) == {"append": "true", "expired": "false"}


def test_pide_5_dias_con_historico_y_nunca_busca(tmp_path):
    rc, cl, *_ = run(tmp_path)
    assert [c[0] for c in cl.calls] == ["history"] * 10 and [c[1] for c in cl.calls] == NAMES
    assert cl.periods == ["5d"] * 10


def test_pausa_de_al_menos_un_segundo_entre_peticiones_y_coste_dos_por_historico(tmp_path):
    rc, cl, ft, *_ = run(tmp_path)
    gaps = [b - a for a, b in zip(cl.at, cl.at[1:])]
    assert len(gaps) == 9 and all(g >= 1.0 for g in gaps)
    assert sum(ft.sleeps) >= 9.0


def test_usa_el_mismo_gate_que_la_sonda_y_su_pausa_no_baja_de_un_segundo():
    assert FL.Gate is fp.Gate and FL.HARD_MAX_REQUESTS == 30
    ft = FakeTime()
    assert fp.Gate(max_requests=30, min_pause=0.0, clock=ft.clock, sleep=ft.sleep).min_pause >= 1.0


def test_peticiones_usadas_son_20_de_30_en_el_resumen(tmp_path):
    rc, cl, ft, out, gh, summ = run(tmp_path)
    assert "peticiones: 20/30" in summ.read_text()


def test_instante_por_ticker_es_el_de_la_medicion_no_el_del_arranque(tmp_path):
    ft = FakeTime()
    ticks = iter(datetime(2026, 11, 17, 12, 17, s, tzinfo=timezone.utc) for s in range(60))
    out = tmp_path / "n.jsonl"
    FL.main(["--out", str(out)], env={}, client=TimedClient(ft, histories=world()), now=lambda: next(ticks), clock=ft.clock, sleep=ft.sleep)
    stamps = [r["measured_at"] for r in lines(out)]
    assert len(set(stamps)) == 10 and stamps == sorted(stamps)


def test_fin_de_semana_un_viernes_con_barra_del_viernes_da_retraso_cero(tmp_path):
    sat = datetime(2026, 11, 14, 9, 17, tzinfo=timezone.utc)
    rc, cl, ft, out, *_ = run(tmp_path, histories={t: hist(FRI) for t in NAMES}, now=sat)
    assert {r["lag_bd"] for r in lines(out)} == {0} and {r["lag_days"] for r in lines(out)} == {1}


# ------------------------------------------------------------------ errores y paradas
def test_429_para_a_la_primera_sin_reintentar_y_el_resto_queda_no_probado(tmp_path, capsys):
    h = world()
    h[NAMES[2]] = fp.yf_exceptions.YFRateLimitError()
    rc, cl, ft, out, gh, summ = run(tmp_path, histories=h)
    recs = lines(out)
    assert rc == 0 and [r["status"] for r in recs] == ["ok", "ok", "429"] + ["no_probado"] * 7
    assert len(cl.calls) == 3 and ft.sleeps == [1.0, 1.0]                   # ninguna petición más ni espera de más
    assert all(tuple(r) == C.RECORD_KEYS for r in recs) and C.check_lines(out.read_text(), set(NAMES))[1] == []
    assert "::warning::" in capsys.readouterr().out and "parada: 429" in summ.read_text()
    assert outputs(gh)["append"] == "true"                                  # lo medido hasta el 429 se publica


def test_429_en_el_primero_no_hace_mas_peticiones(tmp_path):
    h = world()
    h[NAMES[0]] = fp.yf_exceptions.YFRateLimitError()
    rc, cl, ft, out, *_ = run(tmp_path, histories=h)
    assert len(cl.calls) == 1 and ft.sleeps == [] and [r["status"] for r in lines(out)] == ["429"] + ["no_probado"] * 9


def test_texto_429_tambien_se_clasifica_como_429_sin_reintento(tmp_path):
    h = world()
    h[NAMES[1]] = RuntimeError("HTTP Error 429: Too Many Requests")
    rc, cl, ft, out, *_ = run(tmp_path, histories=h)
    assert lines(out)[1]["status"] == "429" and len(cl.calls) == 2


def test_tres_fallos_de_red_seguidos_paran_y_uno_suelto_no(tmp_path):
    h = world()
    for t in NAMES[:3]:
        h[t] = ConnectionError("x")
    rc, cl, ft, out, *_ = run(tmp_path, histories=h)
    assert [r["status"] for r in lines(out)] == ["sin_red"] * 3 + ["no_probado"] * 7 and len(cl.calls) == 3
    h = world()
    h[NAMES[1]] = ConnectionError("x")
    rc, cl, ft, out, *_ = run(tmp_path, histories=h)
    assert [r["status"] for r in lines(out)].count("ok") == 9 and len(cl.calls) == 10


def test_vacio_y_parseo_no_paran(tmp_path):
    h = world()
    h[NAMES[0]] = {"rows": [], "currency": "EUR"}
    h[NAMES[1]] = {"rows": [(FRI, float("nan")), (date(2026, 11, 12), -1.0), (date(2026, 11, 11), 0)], "currency": "EUR"}
    h[NAMES[2]] = fp.yf_exceptions.YFPricesMissingError("x", "5d")
    rc, cl, ft, out, *_ = run(tmp_path, histories=h)
    st = [r["status"] for r in lines(out)]
    assert st[:3] == ["vacio", "parseo", "vacio"] and st[3:] == ["ok"] * 7 and len(cl.calls) == 10
    assert all(r["last_bar"] is None and r["dates"] == [] for r in lines(out)[:3])


def test_barras_invalidas_se_descartan_pero_la_serie_vale(tmp_path):
    h = world()
    h[NAMES[0]] = {"rows": [(date(2026, 11, 11), 10.0), (date(2026, 11, 12), float("nan")), (FRI, 10.2)], "currency": "EUR"}
    rc, cl, ft, out, *_ = run(tmp_path, histories=h)
    r = lines(out)[0]
    assert r["dates"] == ["2026-11-11", "2026-11-13"] and r["bars"] == 2


def test_tope_de_peticiones_se_respeta_y_el_resto_no_probado(tmp_path):
    rc, cl, ft, out, gh, summ = run(tmp_path, argv=["--max-requests", "5"])
    assert [r["status"] for r in lines(out)] == ["ok", "ok"] + ["no_probado"] * 8 and len(cl.calls) == 2     # 2 x 2 = 4; el tercero costaría 6 > 5
    assert "parada: presupuesto" in summ.read_text()


def test_tope_duro_30_aunque_se_pida_mas(tmp_path):
    many = [{"ticker": "T%02d.F" % i, "kind": "fondo"} for i in range(C.MAX_TICKERS)]
    p = tmp_path / "many.json"
    p.write_text(json.dumps(many))
    ft = FakeTime()
    cl = TimedClient(ft, default_history=hist(FRI))
    out = tmp_path / "o.jsonl"
    FL.main(["--out", str(out), "--tickers", str(p), "--max-requests", "999"], env={}, client=cl, now=lambda: TUE_NOON, clock=ft.clock, sleep=ft.sleep)
    assert len(cl.calls) == 15 and [r["status"] for r in lines(out)].count("no_probado") == 5      # 30 / 2 = 15 históricos


def test_lista_invalida_sale_con_2_sin_tocar_yahoo(tmp_path, capsys):
    p = tmp_path / "t.json"
    p.write_text("[]")
    cl = FakeClient()
    rc = FL.main(["--out", str(tmp_path / "o"), "--tickers", str(p)], env={}, client=cl, now=lambda: TUE_NOON)
    assert rc == 2 and cl.calls == [] and "::error::" in capsys.readouterr().out


# ------------------------------------------------------------------ autolímite
def first_line(started):
    return json.dumps({"measured_at": started, "ticker": "X"}) + "\n" + json.dumps({"measured_at": "2026-11-17T09:17:00Z", "ticker": "Y"}) + "\n"


def test_registro_con_mas_de_7_dias_sale_en_verde_sin_medir_ni_tocar_yahoo(tmp_path, monkeypatch, capsys):
    def boom(*a, **k):
        raise AssertionError("no debe construirse el cliente de Yahoo")
    monkeypatch.setattr(FL, "YahooClient", boom)
    rc, cl, ft, out, gh, summ = run(tmp_path, head=first_line("2026-11-09T06:17:00Z"), head_state="found", client=None)
    assert rc == 0 and out.read_text() == "" and ft.sleeps == []
    assert outputs(gh) == {"append": "false", "expired": "true"}
    assert "terminado" in summ.read_text() and "::notice::" in capsys.readouterr().out


def test_registro_de_hace_exactamente_7_dias_todavia_mide(tmp_path):
    rc, cl, ft, out, gh, summ = run(tmp_path, head=first_line("2026-11-10T12:17:05Z"), head_state="found")
    assert len(lines(out)) == 10 and outputs(gh)["append"] == "true"


def test_registro_reciente_o_inexistente_mide(tmp_path):
    for head, state in ((first_line("2026-11-16T06:17:00Z"), "found"), (None, "missing"), ("", "found")):
        rc, cl, ft, out, gh, summ = run(tmp_path, head=head, head_state=state)
        assert len(lines(out)) == 10 and outputs(gh) == {"append": "true", "expired": "false"}


def test_no_poder_leer_el_registro_no_mide_y_avisa(tmp_path, capsys):
    cl = FakeClient()
    rc, _, ft, out, gh, summ = run(tmp_path, head_state="error", client=cl)
    assert rc == 0 and cl.calls == [] and out.read_text() == "" and outputs(gh) == {"append": "false", "expired": "false"}
    assert "::warning::" in capsys.readouterr().out


def test_primera_linea_ilegible_cuenta_como_caducado(tmp_path):
    cl = FakeClient()
    rc, _, ft, out, gh, summ = run(tmp_path, head="esto no es json\n", head_state="found", client=cl)
    assert cl.calls == [] and outputs(gh)["expired"] == "true"


def test_fichero_de_cabecera_ausente_con_found_equivale_a_no_poder_leerlo(tmp_path):
    cl = FakeClient()
    out = tmp_path / "o"
    rc = FL.main(["--out", str(out), "--registry-head", str(tmp_path / "no_existe"), "--head-state", "found"], env={}, client=cl, now=lambda: TUE_NOON)
    assert rc == 0 and cl.calls == [] and out.read_text() == ""


def test_el_fichero_de_salida_se_vacia_en_cada_ejecucion(tmp_path):
    out = tmp_path / "new.jsonl"
    out.write_text("basura de una ejecución anterior\n")
    rc, *_ = run(tmp_path, head=first_line("2026-11-01T00:00:00Z"), head_state="found")
    assert (tmp_path / "new.jsonl").read_text() == ""


# ------------------------------------------------------------------ salida
def test_resumen_con_tabla_por_ticker_y_agregados(tmp_path):
    rc, cl, ft, out, gh, summ = run(tmp_path)
    md = summ.read_text()
    assert "2026-11-17T12:17:05Z" in md and md.count("| ") > 10
    for t in NAMES:
        assert f"| {t} |" in md
    assert "Fondos 0P medidos: 7 (retraso hábil mín/máx: 2/2)" in md and "ETF de control medidos: 3 (mín/máx: 0/0)" in md
    assert "ok 10" in md and "429 0" in md


def test_sin_variables_de_github_no_escribe_en_ningun_sitio_salvo_el_fichero_de_salida(tmp_path):
    ft = FakeTime()
    out = tmp_path / "o.jsonl"
    rc = FL.main(["--out", str(out)], env={}, client=TimedClient(ft, histories=world()), now=lambda: TUE_NOON, clock=ft.clock, sleep=ft.sleep)
    assert rc == 0 and len(lines(out)) == 10 and sorted(p.name for p in tmp_path.iterdir()) == ["o.jsonl"]


def test_las_lineas_no_llevan_nada_de_nadie_solo_ticker_fechas_y_numeros(tmp_path):
    rc, cl, ft, out, *_ = run(tmp_path)
    text = out.read_text()
    assert not re.search(r"\b[A-Z]{2}(?=[A-Z0-9]*\d)[A-Z0-9]{10}\b", text.replace("0P", "").replace(".F", ""))
    for r in lines(out):
        assert set(r) == set(C.RECORD_KEYS)


# ------------------------------------------------------------------ cliente real con yfinance simulado y punto de entrada
def test_cliente_real_con_yfinance_simulado_pide_5d_y_traduce_la_serie(monkeypatch, tmp_path):
    import yfinance as yf
    seen = []

    class T:
        def __init__(self, sym):
            self.sym = sym

        def history(self, **kw):
            seen.append(kw)
            return hist_df(FRI, n=5, tz="Europe/Berlin")

        @property
        def history_metadata(self):
            return {"currency": "EUR"}
    monkeypatch.setattr(yf, "Ticker", T)
    ft = FakeTime()
    out = tmp_path / "o.jsonl"
    rc = FL.main(["--out", str(out)], env={}, now=lambda: TUE_NOON, clock=ft.clock, sleep=ft.sleep)       # sin cliente: usa YahooClient (yfinance simulado)
    assert rc == 0 and len(seen) == 10 and all(kw["period"] == "5d" and kw["interval"] == "1d" for kw in seen)
    assert {r["last_bar"] for r in lines(out)} == {"2026-11-13"} and {r["lag_bd"] for r in lines(out)} == {2}


def test_punto_de_entrada_real_con_registro_caducado_no_hace_red_y_sale_con_0(tmp_path):
    head = tmp_path / "h.jsonl"
    head.write_text(json.dumps({"measured_at": "2020-01-01T00:00:00Z"}) + "\n")
    out = tmp_path / "o.jsonl"
    r = subprocess.run([sys.executable, str(ROOT / "scripts" / "fund_lag.py"), "--out", str(out), "--registry-head", str(head), "--head-state", "found"],
                       capture_output=True, text=True, timeout=60)
    assert r.returncode == 0 and out.read_text() == "" and "terminado" in r.stdout


def test_ayuda_y_el_modulo_comun_y_el_informe_no_importan_terceros():
    r = subprocess.run([sys.executable, str(ROOT / "scripts" / "fund_lag.py"), "--help"], capture_output=True, text=True, timeout=60)
    assert r.returncode == 0 and "--registry-head" in r.stdout
    for mod in ("fund_lag_common", "fund_lag_report"):                      # el job de escritura y el informe corren solo con la biblioteca estándar
        code = f"import sys; sys.path.insert(0, {str(ROOT / 'scripts')!r}); import {mod}; assert 'yfinance' not in sys.modules and 'pandas' not in sys.modules"
        assert subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, timeout=60).returncode == 0, mod
