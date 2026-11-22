"""Sombra NAV Yahoo (F1), punto 5: el recorrido completo con fakes de Yahoo y de lo que dejan los pasos watch/build de personal_v2 (que leen
Supabase). Fondo sin `0P…`, sin NAV oficial, Yahoo caído / 429 / vacío / presupuesto -> solo CONTADORES y salida 0 (nunca rojo)."""
import json
import pathlib

import pytest

import yahoo_shadow as ys
from yahoo_shadow_fakes import (ISIN_A, ISIN_B, ISIN_C, NAME_A, NAME_B, NAME_C, NAV_DAY, NOW, PRICE_A, PRICE_B, PRICE_C, SYM_A, SYM_B, URL_A, URL_B,
                                URL_C, FakeTime, FakeYahoo, bars, canary_ok, hist, quote, write_inputs)

ROOT = pathlib.Path(__file__).resolve().parent.parent
DAY = NAV_DAY.isoformat()


def run(tmp_path, capsys, funds, searches, histories, cfg=None, users=1, now=NOW, ledger=True, canary=True):
    d = tmp_path / "personal"
    write_inputs(d, funds, users)
    data = json.loads((ROOT / "config" / "yahoo_shadow.json").read_text(encoding="utf-8"))
    data.update(cfg or {})
    cp = tmp_path / "cfg.json"
    cp.write_text(json.dumps(data), encoding="utf-8")
    if canary:
        canary_ok(searches, histories)
    y = FakeYahoo(searches, histories)
    t = FakeTime()
    summary = tmp_path / "summary.md"
    env = {"GITHUB_STEP_SUMMARY": str(summary)}
    argv = ["--dir", str(d), "--config", str(cp), "--generic", str(ROOT / "config" / "fund_probe_isins_generic.json")]
    if ledger:
        argv += ["--ledger", str(tmp_path / "ledger" / "ledger.json")]
    rc = ys.main(argv, env=env, client=y, now=now, clock=t.clock, sleep=t.sleep)
    out = capsys.readouterr()
    return rc, out.out, out.err, summary.read_text(encoding="utf-8") if summary.exists() else "", y, t


def line(out):
    [ln] = [x for x in out.splitlines() if x.startswith("sombra Yahoo: ") and "fondos con NAV oficial" in x]
    return ln


FUNDS3 = [(ISIN_A, NAME_A, URL_A, PRICE_A, DAY), (ISIN_B, NAME_B, URL_B, PRICE_B, DAY), (ISIN_C, NAME_C, URL_C, PRICE_C, DAY)]


def test_recorrido_normal_solo_contadores(tmp_path, capsys):
    searches = {ISIN_A: [quote(SYM_A, NAME_A)], ISIN_B: [quote(SYM_B, NAME_B)], ISIN_C: [quote("ZQEG.DE", NAME_C, "ETF", "GER")]}
    histories = {SYM_A: hist(bars(NAV_DAY, 4, PRICE_A + 0.001)), SYM_B: hist(bars(NAV_DAY.replace(day=19), 4, 40.0))}
    rc, out, err, summary, y, t = run(tmp_path, capsys, FUNDS3, searches, histories)
    assert rc == 0
    ln = line(out)
    for frag in ("3 fondos con NAV oficial", "comparables 1", "iguales a ≤ 1 pb 1", "dif. máx. 0.00 pb", "sin 0P 1", "sin dato de la misma fecha 1",
                 "Yahoo por detrás 1", "retraso medio de Yahoo frente a iShares +0.50 d hábiles (n=2)", "canario ok", "peticiones 10/60"):
        assert frag in ln, frag
    # canario + una búsqueda por fondo + un histórico por fondo resuelto; nunca más
    searched = [c[1] for c in y.calls if c[0] == "search"]
    assert sorted(searched[1:]) == sorted([ISIN_A, ISIN_B, ISIN_C]) and len(searched) == 4
    assert all(c[2] == "5d" for c in y.calls if c[0] == "history")
    assert all(s >= 1.0 for s in t.sleeps)                                             # pausa mínima de la sonda entre peticiones


def test_fondo_sin_nav_oficial_no_se_pide_a_yahoo(tmp_path, capsys):
    funds = [(ISIN_A, NAME_A, URL_A, PRICE_A, DAY), (ISIN_B, NAME_B, URL_B, None, None)]
    rc, out, _e, _s, y, _t = run(tmp_path, capsys, funds, {ISIN_A: [quote(SYM_A, NAME_A)]}, {SYM_A: hist(bars(NAV_DAY, 3, PRICE_A))})
    assert rc == 0 and "1 fondos con NAV oficial (de 2 con ficha; 1 sin NAV oficial" in line(out)
    assert ISIN_B not in [c[1] for c in y.calls]


def test_sin_ningun_fondo_con_nav_oficial_no_se_pide_nada(tmp_path, capsys):
    rc, out, _e, _s, y, _t = run(tmp_path, capsys, [(ISIN_A, NAME_A, URL_A, None, None)], {}, {})
    assert rc == 0 and y.calls == [] and "0 fondos con NAV oficial" in out


def test_mismo_isin_en_dos_usuarios_se_pide_una_vez(tmp_path, capsys):
    rc, out, _e, _s, y, _t = run(tmp_path, capsys, FUNDS3[:1], {ISIN_A: [quote(SYM_A, NAME_A)]}, {SYM_A: hist(bars(NAV_DAY, 3, PRICE_A))}, users=2)
    assert rc == 0 and [c[1] for c in y.calls].count(ISIN_A) == 1 and "1 fondos con NAV oficial" in line(out) and "2 usuarios" in line(out)


def test_429_en_la_primera_busqueda_de_un_fondo_para_y_sale_0(tmp_path, capsys):
    from yfinance import exceptions as yfe
    searches = {ISIN_A: yfe.YFRateLimitError(), ISIN_B: [quote(SYM_B, NAME_B)]}
    rc, out, _e, _s, y, _t = run(tmp_path, capsys, FUNDS3, searches, {})
    assert rc == 0
    ln = line(out)
    assert "errores de Yahoo 1 (429 1)" in ln and "sin probar 2" in ln and "parada 429" in ln and "comparables 0" in ln
    assert [c[1] for c in y.calls if c[0] == "search"][1:] == [ISIN_A]                 # nada más después del 429
    assert "::warning::sombra Yahoo: parada limpia (429)" in out and "::error::" not in out


def test_yahoo_caido_tres_fallos_de_red_para(tmp_path, capsys):
    searches = {ISIN_A: ConnectionError("caído"), ISIN_B: ConnectionError("caído"), ISIN_C: ConnectionError("caído")}
    rc, out, _e, _s, _y, _t = run(tmp_path, capsys, FUNDS3, searches, {})
    assert rc == 0 and "errores de Yahoo 3 (sin_red 3)" in line(out) and "parada sin_red" in line(out)


def test_historico_vacio_y_error_de_parseo_son_contadores(tmp_path, capsys):
    searches = {ISIN_A: [quote(SYM_A, NAME_A)], ISIN_B: [quote(SYM_B, NAME_B)]}
    histories = {SYM_A: hist([]), SYM_B: ValueError("algo raro " + SYM_B)}
    rc, out, _e, _s, _y, _t = run(tmp_path, capsys, FUNDS3[:2], searches, histories)
    ln = line(out)
    assert rc == 0 and "vacíos 1" in ln and "errores de Yahoo 1 (parseo 1)" in ln and "parada ninguna" in ln


def test_presupuesto_propio_por_run(tmp_path, capsys):
    searches = {ISIN_A: [quote(SYM_A, NAME_A)], ISIN_B: [quote(SYM_B, NAME_B)]}
    histories = {SYM_A: hist(bars(NAV_DAY, 3, PRICE_A)), SYM_B: hist(bars(NAV_DAY, 3, PRICE_B))}
    rc, out, _e, _s, y, _t = run(tmp_path, capsys, FUNDS3, searches, histories, cfg={"max_requests_per_run": 6})
    ln = line(out)
    assert rc == 0 and "peticiones 6/6" in ln and "parada presupuesto" in ln and "sin probar 2" in ln and "comparables 1" in ln


def test_desactivada_no_pide_nada(tmp_path, capsys):
    rc, out, _e, summary, y, _t = run(tmp_path, capsys, FUNDS3, {}, {}, cfg={"enabled": False})
    assert rc == 0 and y.calls == [] and "desactivada" in out and "fondos con NAV oficial" not in out


def test_configuracion_rota_aviso_y_0(tmp_path, capsys):
    rc, out, _e, _s, y, _t = run(tmp_path, capsys, FUNDS3, {}, {}, cfg={"enabled": "sí"})
    assert rc == 0 and y.calls == [] and "::warning::sombra Yahoo:" in out and "::error::" not in out


def test_sin_ficheros_de_entrada_sale_0(tmp_path, capsys):
    t = FakeTime()
    rc = ys.main(["--dir", str(tmp_path / "nada"), "--config", str(ROOT / "config" / "yahoo_shadow.json")], env={}, client=FakeYahoo(), now=NOW,
                 clock=t.clock, sleep=t.sleep)
    assert rc == 0 and "sin datos de esta ejecución" in capsys.readouterr().out


def test_excepcion_inesperada_sale_0_sin_texto(tmp_path, capsys, monkeypatch):
    def boom(*a, **k):
        raise RuntimeError("detalle con " + ISIN_A)
    monkeypatch.setattr(ys, "run_shadow", boom)
    rc, out, err, _s, _y, _t = run(tmp_path, capsys, FUNDS3, {}, {})
    assert rc == 0 and "::warning::sombra Yahoo: fallo inesperado (RuntimeError en " in out
    assert "detalle con" not in out + err


@pytest.mark.parametrize("roto", ["{no json", "[1, 2]", '[{"user": "u", "payload": 3}]'])
def test_payloads_ilegibles_sale_0(tmp_path, capsys, roto):
    d = tmp_path / "personal"
    write_inputs(d, FUNDS3)
    (d / "payloads.json").write_text(roto, encoding="utf-8")
    t = FakeTime()
    rc = ys.main(["--dir", str(d), "--config", str(ROOT / "config" / "yahoo_shadow.json")], env={}, client=FakeYahoo(), now=NOW, clock=t.clock, sleep=t.sleep)
    out = capsys.readouterr().out
    assert rc == 0 and "::error::" not in out
