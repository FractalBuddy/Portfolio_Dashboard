"""Sombra NAV Yahoo (F1), punto 7: persistencia de los CONTADORES diarios (libro sin datos personales, guardado por el workflow en la caché de Actions),
franjas configurables con parada temprana (D6 A) y evaluación de la puerta (D7 A: ≥ 10 días hábiles; ≥ 95 % de comparables iguales a ≤ 1 pb y ninguno > 10 pb)."""
import json
import re
from datetime import date, datetime, timedelta, timezone

import pytest

import yahoo_shadow as ys
from test_yahoo_shadow_run import ROOT, line
from yahoo_shadow_fakes import (ISIN_A, ISIN_B, NAME_A, NAME_B, PRICE_A, PRICE_B, SYM_A, SYM_B, URL_A, URL_B, FakeTime, FakeYahoo, bars, canary_ok,
                                hist, quote, write_inputs)

GEN = str(ROOT / "config" / "fund_probe_isins_generic.json")


class World:
    """Un run de personal-full por llamada, con el mismo libro (lo que haría la caché de Actions entre runs)."""

    def __init__(self, tmp_path, capsys, cfg=None):
        self.tmp, self.capsys = tmp_path, capsys
        data = json.loads((ROOT / "config" / "yahoo_shadow.json").read_text(encoding="utf-8"))
        data.update(cfg or {})
        self.cfg = tmp_path / "cfg.json"
        self.cfg.write_text(json.dumps(data), encoding="utf-8")
        self.ledger = tmp_path / "cache" / "ledger.json"
        self.n = 0

    def run(self, now, nav_day, ya=None, yb=None, behind=False):
        """Fondos A y B con NAV oficial de `nav_day`; Yahoo da `ya`/`yb` en esa fecha (o va un día por detrás si behind)."""
        self.n += 1
        d = self.tmp / ("p%d" % self.n)
        write_inputs(d, [(ISIN_A, NAME_A, URL_A, PRICE_A, nav_day.isoformat()), (ISIN_B, NAME_B, URL_B, PRICE_B, nav_day.isoformat())])
        last = nav_day - timedelta(days=1 if nav_day.weekday() else 3) if behind else nav_day
        s, h = {ISIN_A: [quote(SYM_A, NAME_A)], ISIN_B: [quote(SYM_B, NAME_B)]}, {}
        h[SYM_A] = hist(bars(last, 4, PRICE_A if ya is None else ya))
        h[SYM_B] = hist(bars(last, 4, PRICE_B if yb is None else yb))
        canary_ok(s, h, last)
        self.y = FakeYahoo(s, h)
        out_file, summary = self.tmp / ("out%d" % self.n), self.tmp / ("sum%d.md" % self.n)
        t = FakeTime()
        rc = ys.main(["--dir", str(d), "--config", str(self.cfg), "--generic", GEN, "--ledger", str(self.ledger)],
                     env={"GITHUB_OUTPUT": str(out_file), "GITHUB_STEP_SUMMARY": str(summary)}, client=self.y, now=now, clock=t.clock, sleep=t.sleep)
        assert rc == 0
        self.out = self.capsys.readouterr().out
        self.gh = out_file.read_text(encoding="utf-8") if out_file.exists() else ""
        self.summary = summary.read_text(encoding="utf-8") if summary.exists() else ""
        return self


def at(day, hh, mm=40):
    return datetime(day.year, day.month, day.day, hh, mm, tzinfo=timezone.utc)


def business_days(start, n):
    out, d = [], start
    while len(out) < n:
        if d.weekday() < 5:
            out.append(d)
        d += timedelta(days=1)
    return out


def prev_bd(d):
    d -= timedelta(days=1)
    while d.weekday() >= 5:
        d -= timedelta(days=1)
    return d


def gate_line(out):
    [ln] = [x for x in out.splitlines() if x.startswith("sombra Yahoo: puerta D7: ")]
    return ln


# ------------------------------------------------------------------ el libro: solo números, fechas y estados cerrados
def test_el_libro_se_escribe_tras_una_lectura_y_solo_lleva_contadores(tmp_path, capsys):
    w = World(tmp_path, capsys).run(at(date(2026, 11, 24), 6), date(2026, 11, 23))
    assert "ledger=written" in w.gh
    text = w.ledger.read_text(encoding="utf-8")
    data = json.loads(text)
    assert data["schema"] == 1 and len(data["reads"]) == 1
    r = data["reads"][0]
    assert r["day"] == "2026-11-24" and r["slot"] == 0 and r["c"]["comparables"] == 2 and r["c"]["iguales"] == 2
    # NINGÚN texto que no sea una fecha, una hora del run o un estado de una lista cerrada
    strings = re.findall(r'"((?:[^"\\]|\\.)*)"', text)
    allowed = set(ys.LEDGER_C_KEYS) | {"schema", "reads", "day", "slot", "at", "official_max", "c", "by_date"} | set(ys.CANARY_STATES)
    for s in strings:
        assert s in allowed or re.fullmatch(r"\d{4}-\d{2}-\d{2}(T\d{2}:\d{2}Z)?", s), s
    for secret in (ISIN_A, ISIN_B, SYM_A, SYM_B, NAME_A, "87.65", "43.21", "ishares"):
        assert secret not in text


def test_fuera_de_franja_no_lee_ni_escribe_el_libro_pero_ensena_la_puerta(tmp_path, capsys):
    w = World(tmp_path, capsys).run(at(date(2026, 11, 24), 13), date(2026, 11, 23))           # el run de las 13:38
    assert w.y.calls == [] and "ledger=written" not in w.gh and not w.ledger.exists()
    assert "no se lee en este run (fuera de franja)" in w.out and "puerta D7: EN CURSO" in w.out and "puerta D7" in w.summary


def test_la_frecuencia_es_configurable(tmp_path, capsys):
    w = World(tmp_path, capsys, {"slots_utc": [[13, 14]]}).run(at(date(2026, 11, 24), 13), date(2026, 11, 23))
    assert w.y.calls and "ledger=written" in w.gh


def test_la_misma_franja_no_se_lee_dos_veces(tmp_path, capsys):
    w = World(tmp_path, capsys)
    w.run(at(date(2026, 11, 24), 6), date(2026, 11, 23), behind=True)
    w.run(at(date(2026, 11, 24), 8), date(2026, 11, 23), behind=True)                       # disparador externo en la misma franja
    assert w.y.calls == [] and "no se lee en este run (franja ya leída hoy)" in w.out


def test_parada_temprana_tarde_si_por_la_manana_todo_quedo_medido(tmp_path, capsys):
    w = World(tmp_path, capsys)
    w.run(at(date(2026, 11, 24), 6), date(2026, 11, 23))
    w.run(at(date(2026, 11, 24), 21), date(2026, 11, 23))
    assert w.y.calls == [] and "no se lee en este run (todo medido en la franja anterior)" in w.out


def test_por_la_tarde_se_vuelve_a_leer_si_algo_quedo_pendiente(tmp_path, capsys):
    w = World(tmp_path, capsys)
    w.run(at(date(2026, 11, 24), 6), date(2026, 11, 23), behind=True)                       # Yahoo aún sin la fecha oficial
    w.run(at(date(2026, 11, 24), 21), date(2026, 11, 23))
    assert w.y.calls and "comparables 2" in line(w.out)
    assert len(json.loads(w.ledger.read_text())["reads"]) == 2


def test_parada_temprana_no_aplica_si_cambio_la_fecha_oficial(tmp_path, capsys):
    w = World(tmp_path, capsys)
    w.run(at(date(2026, 11, 24), 6), date(2026, 11, 20))
    w.run(at(date(2026, 11, 24), 21), date(2026, 11, 23))                                   # iShares ya publicó el NAV del lunes
    assert w.y.calls


def test_tope_de_lecturas_por_dia(tmp_path, capsys):
    w = World(tmp_path, capsys, {"max_reads_per_day": 1})
    w.run(at(date(2026, 11, 24), 6), date(2026, 11, 23), behind=True)
    w.run(at(date(2026, 11, 24), 21), date(2026, 11, 23))
    assert w.y.calls == [] and "no se lee en este run (tope de lecturas del día)" in w.out


# ------------------------------------------------------------------ la puerta de 10 días hábiles
def test_puerta_en_curso_y_luego_pasa_a_los_10_dias_habiles(tmp_path, capsys):
    w = World(tmp_path, capsys)
    days = business_days(date(2026, 11, 24), 10)
    for i, d in enumerate(days):
        w.run(at(d, 6), prev_bd(d))
        if i < 9:
            assert "puerta D7: EN CURSO · %d de 10 días hábiles con comparables" % (i + 1) in gate_line(w.out)
    g = gate_line(w.out)
    assert "puerta D7: PASA" in g and "20 comparables únicos" in g and "20 iguales a ≤ 1 pb (100.0 %)" in g and "dif. máx. 0.00 pb" in g
    assert "PASA" in w.summary and w.summary.count("| 2026-") >= 10                    # una fila por lectura en el resumen


def test_los_fines_de_semana_no_cuentan_como_dias_habiles(tmp_path, capsys):
    w = World(tmp_path, capsys)
    w.run(at(date(2026, 11, 28), 6), date(2026, 11, 27))                                   # sábado
    w.run(at(date(2026, 11, 29), 6), date(2026, 11, 27))                                   # domingo, misma fecha oficial
    g = gate_line(w.out)
    assert "0 de 10 días hábiles con comparables" in g and "2 comparables únicos" in g      # el viernes, una vez (sin duplicar)


def test_la_misma_fecha_oficial_leida_dos_veces_no_se_cuenta_doble(tmp_path, capsys):
    w = World(tmp_path, capsys)
    w.run(at(date(2026, 11, 24), 6), date(2026, 11, 23))
    w.run(at(date(2026, 11, 25), 6), date(2026, 11, 23))                                   # iShares no avanzó: mismo NAV
    assert "2 comparables únicos" in gate_line(w.out)


def test_uno_por_encima_de_10_pb_tumba_la_puerta_en_cuanto_aparece(tmp_path, capsys):
    w = World(tmp_path, capsys)
    w.run(at(date(2026, 11, 24), 6), date(2026, 11, 23), ya=PRICE_A * 1.0011)               # ~11 pb
    g = gate_line(w.out)
    assert "puerta D7: NO PASA" in g and "ninguno > 10 pb" in g


@pytest.mark.parametrize("malos,veredicto", [(1, "PASA"), (2, "NO PASA")])
def test_umbral_del_95_por_ciento(tmp_path, capsys, malos, veredicto):
    """20 comparables: 19 iguales = 95,0 % (pasa, el umbral es ≥); 18 = 90 % (no pasa). Las diferencias son de 1 céntimo (< 10 pb)."""
    w = World(tmp_path, capsys)
    for i, d in enumerate(business_days(date(2026, 11, 24), 10)):
        w.run(at(d, 6), prev_bd(d), yb=PRICE_B + 0.01 if i < malos else None)
    assert ("puerta D7: %s" % veredicto) in gate_line(w.out)


# ------------------------------------------------------------------ robustez del libro
@pytest.mark.parametrize("roto", ["{no json", "[]", '{"schema": 2, "reads": []}', '{"schema": 1, "reads": [{"day": "IE000SYNA013"}]}',
                                  '{"schema": 1, "reads": [{"day": "2026-11-24", "slot": 0, "at": "x", "official_max": null, "c": {}, "by_date": []}]}'])
def test_libro_ilegible_se_empieza_de_cero_con_aviso_y_sin_citarlo(tmp_path, capsys, roto):
    w = World(tmp_path, capsys)
    w.ledger.parent.mkdir(parents=True)
    w.ledger.write_text(roto, encoding="utf-8")
    w.run(at(date(2026, 11, 24), 6), date(2026, 11, 23))
    assert "::warning::sombra Yahoo: libro de contadores ilegible: se empieza de cero" in w.out and "IE000SYNA013" not in w.out.replace("::add-mask::IE000SYNA013", "")
    assert len(json.loads(w.ledger.read_text())["reads"]) == 1


def test_el_libro_olvida_lo_que_pasa_de_ledger_days(tmp_path, capsys):
    w = World(tmp_path, capsys, {"ledger_days": 14})
    w.run(at(date(2026, 11, 2), 6), date(2026, 10, 30))
    w.run(at(date(2026, 11, 24), 6), date(2026, 11, 23))
    assert [r["day"] for r in json.loads(w.ledger.read_text())["reads"]] == ["2026-11-24"]


def test_sin_libro_configurado_mide_igual(tmp_path, capsys):
    from test_yahoo_shadow_run import FUNDS3, run
    rc, out, _e, _s, y, _t = run(tmp_path, capsys, FUNDS3[:1], {ISIN_A: [quote(SYM_A, NAME_A)]}, {SYM_A: hist(bars(date(2026, 11, 20), 3, PRICE_A))},
                                 ledger=False)
    assert rc == 0 and y.calls and "comparables 1" in line(out)
