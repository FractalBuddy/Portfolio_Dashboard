"""Informe del retraso (scripts/fund_lag_report.py) sobre datos SINTÉTICOS con horas de publicación conocidas: los valores esperados salen de cómo se construye el mundo,
no del código que se prueba."""
import json
import pathlib
import subprocess
import sys
from datetime import date, datetime, timedelta, timezone

import pytest

import fund_lag_common as C
import fund_lag_report as R

ROOT = pathlib.Path(__file__).resolve().parent.parent
UTC = timezone.utc
FUND, ETF = "0P00000WLG.F", "IWDA.L"
SLOT_HOURS = (6, 9, 12, 15, 18, 21)


def bdays(start, end):
    d = start
    while d <= end:
        if d.weekday() < 5:
            yield d
        d += timedelta(days=1)


def add_bd(d, n):
    while n:
        d += timedelta(days=1)
        n -= d.weekday() < 5
    return d


def publish_time(kind, d):
    """FONDO: la barra del día hábil D aparece el día D+2 hábil a las 10:00 UTC. ETF: la barra de D aparece el propio D a las 16:30 UTC."""
    return datetime.combine(add_bd(d, 2), datetime.min.time(), UTC).replace(hour=10) if kind == "fund" else datetime.combine(d, datetime.min.time(), UTC).replace(hour=16, minute=30)


def visible(kind, now, window=5):
    pubs = sorted(d for d in bdays(date(2026, 11, 1), now.date()) if publish_time(kind, d) <= now)
    return pubs[-window:]


def world(first=date(2026, 11, 9), last=date(2026, 11, 20)):
    """Mediciones a las 06:17, 09:17, 12:17, 15:17, 18:17 y 21:17 UTC de cada día HÁBIL entre `first` y `last` (solo laborables: los valores esperados son exactos)."""
    recs = []
    for day in bdays(first, last):
        for h in SLOT_HOURS:
            now = datetime(day.year, day.month, day.day, h, 17, 0, tzinfo=UTC)
            for ticker, kind in ((FUND, "fund"), (ETF, "etf")):
                ds = visible(kind, now)
                recs.append(C.make_record(now, ticker, "ok", ds) if ds else C.make_record(now, ticker, "vacio"))
    return recs


def load(recs, extra_text=""):
    return R.load_records("".join(C.dumps_record(r) + "\n" for r in recs) + extra_text)


# ------------------------------------------------------------------ retraso por ticker y franja
def test_minimo_mediano_y_maximo_por_ticker_y_franja_en_el_mundo_sintetico():
    recs, skipped = load([r for r in world() if r["measured_at"] >= "2026-11-12"])      # sin los primeros días (aún sin historia suficiente)
    st = R.lag_stats(recs, slot_hours=3)
    assert skipped == 0
    # FONDO: la barra de D aparece a D+2 hábiles a las 10:00: hasta las 09:17 el último dato es de hace 3 días hábiles; desde las 12:17, de hace 2.
    assert {s: (v["min"], v["median"], v["max"]) for s, v in st[FUND].items()} == {
        "06-08": (3, 3, 3), "09-11": (3, 3, 3), "12-14": (2, 2, 2), "15-17": (2, 2, 2), "18-20": (2, 2, 2), "21-23": (2, 2, 2)}
    # ETF: la barra de D aparece el propio D a las 16:30: hasta las 15:17 el último dato es el día hábil anterior (1); desde las 18:17, el de hoy (0).
    assert {s: (v["min"], v["median"], v["max"]) for s, v in st[ETF].items()} == {
        "06-08": (1, 1, 1), "09-11": (1, 1, 1), "12-14": (1, 1, 1), "15-17": (1, 1, 1), "18-20": (0, 0, 0), "21-23": (0, 0, 0)}
    assert all(v["n"] == 7 for slots in st.values() for v in slots.values())           # 7 días hábiles del 12 al 20


def test_mediana_con_numero_par_de_mediciones_es_la_media_de_las_dos_centrales():
    recs = []
    for i, lag in enumerate((1, 2, 3, 4)):
        r = C.make_record(datetime(2026, 11, 17, 6, 17, i, tzinfo=UTC), "T", "ok", [date(2026, 11, 16)])
        r["lag_bd"] = lag
        recs.append(r)
    st = R.lag_stats(load(recs)[0])
    assert st["T"]["06-08"] == {"n": 4, "min": 1, "median": 2.5, "max": 4}


def test_solo_cuentan_las_mediciones_ok_y_el_resto_se_cuenta_aparte():
    recs = world(date(2026, 11, 17), date(2026, 11, 17))
    recs += [C.make_record(datetime(2026, 11, 17, 6, 20, tzinfo=UTC), FUND, "429"), C.make_record(datetime(2026, 11, 17, 6, 21, tzinfo=UTC), FUND, "no_probado")]
    loaded, _ = load(recs)
    rep = R.build_report(loaded)
    assert rep["by_status"]["429"] == 1 and rep["by_status"]["no_probado"] == 1 and rep["ok"] == len(loaded) - 2 - rep["by_status"].get("vacio", 0)
    assert rep["lag"][FUND]["06-08"]["n"] == 1                              # el 429 de las 06:20 no entra en las estadísticas


@pytest.mark.parametrize("hhmmss,label", [("06:00:00", "06-08"), ("08:59:59", "06-08"), ("09:00:00", "09-11"), ("23:59:59", "21-23"), ("00:00:00", "00-02"), ("02:59:59", "00-02")])
def test_los_bordes_de_las_franjas_de_3_horas(hhmmss, label):
    h, m, s = map(int, hhmmss.split(":"))
    r = C.make_record(datetime(2026, 11, 17, h, m, s, tzinfo=UTC), "T", "ok", [date(2026, 11, 17)])
    assert list(R.lag_stats(load([r])[0], 3)["T"]) == [label]


def test_franja_de_una_hora_y_de_24():
    r = C.make_record(datetime(2026, 11, 17, 9, 17, tzinfo=UTC), "T", "ok", [date(2026, 11, 17)])
    assert list(R.lag_stats(load([r])[0], 1)["T"]) == ["09-09"] and list(R.lag_stats(load([r])[0], 24)["T"]) == ["00-23"]


def test_calendario_weekdays_lee_lag_wd_y_target_lag_bd():
    r = C.make_record(datetime(2026, 4, 7, 12, 0, tzinfo=UTC), "T", "ok", [date(2026, 4, 2)])           # Viernes Santo y Lunes de Pascua en medio
    assert (r["lag_bd"], r["lag_wd"]) == (1, 3)
    recs = load([r])[0]
    assert R.lag_stats(recs, 3, "target")["T"]["12-14"]["max"] == 1 and R.lag_stats(recs, 3, "weekdays")["T"]["12-14"]["max"] == 3


# ------------------------------------------------------------------ primera aparición
def test_primera_aparicion_del_fondo_es_a_las_1217_de_D_mas_2_con_ausente_a_las_0917():
    recs, _ = load(world())
    fs = {e["date"]: e for e in R.first_seen(recs)[FUND]}
    e = fs["2026-11-12"]                                                    # jueves 12: publicada el lunes 16 a las 10:00 -> primera medición posterior: 12:17
    assert (e["first_seen"], e["absent_at"], e["bd_after"], e["days_after"], e["censored"]) == ("2026-11-16T12:17:00Z", "2026-11-16T09:17:00Z", 2, 4, False)
    e = fs["2026-11-13"]                                                    # viernes 13: publicada el martes 17 a las 10:00
    assert (e["first_seen"], e["absent_at"], e["bd_after"], e["days_after"]) == ("2026-11-17T12:17:00Z", "2026-11-17T09:17:00Z", 2, 4)
    assert all(v["first_seen"][11:16] == "12:17" and v["bd_after"] == 2 for v in fs.values() if not v["censored"])


def test_primera_aparicion_del_etf_es_el_mismo_dia_a_las_1817():
    recs, _ = load(world())
    fs = {e["date"]: e for e in R.first_seen(recs)[ETF]}
    e = fs["2026-11-17"]
    assert (e["first_seen"], e["absent_at"], e["bd_after"], e["days_after"]) == ("2026-11-17T18:17:00Z", "2026-11-17T15:17:00Z", 0, 0)


def test_las_fechas_ya_presentes_en_la_primera_medicion_son_cota_superior_censurada():
    recs, _ = load(world())
    fs = R.first_seen(recs)[FUND]
    first = recs[0]["measured_at"]
    cens = [e for e in fs if e["censored"]]
    assert cens and all(e["first_seen"] == first and e["absent_at"] is None for e in cens)
    assert not any(e["censored"] for e in fs if e["first_seen"] != first)


def test_la_primera_aparicion_ignora_mediciones_con_error_y_usa_la_siguiente_ok():
    mk = lambda h, m, ds, st="ok": C.make_record(datetime(2026, 11, 17, h, m, tzinfo=UTC), "T", st, ds)         # noqa: E731
    d0, d1 = date(2026, 11, 12), date(2026, 11, 13)
    recs = [mk(6, 0, [d0]), mk(9, 0, [d0], "ok"), mk(10, 0, None, "429"), mk(12, 0, [d0, d1])]
    fs = {e["date"]: e for e in R.first_seen(load(recs)[0])["T"]}
    assert (fs["2026-11-13"]["first_seen"], fs["2026-11-13"]["absent_at"]) == ("2026-11-17T12:00:00Z", "2026-11-17T09:00:00Z")


def test_una_barra_que_nunca_aparece_no_figura():
    recs = [C.make_record(datetime(2026, 11, 17, 6, 0, tzinfo=UTC), "T", "ok", [date(2026, 11, 12)])]
    assert [e["date"] for e in R.first_seen(load(recs)[0])["T"]] == ["2026-11-12"]


# ------------------------------------------------------------------ carga tolerante y salida
def test_las_lineas_rotas_o_con_otro_esquema_se_saltan_y_se_cuentan():
    good = C.dumps_record(C.make_record(datetime(2026, 11, 17, 6, 0, tzinfo=UTC), "T", "ok", [date(2026, 11, 17)]))
    junk = "no json\n" + json.dumps({"ticker": "T"}) + "\n" + "\n" + json.dumps({**json.loads(good), "extra": 1}) + "\n"
    recs, skipped = R.load_records(good + "\n" + junk)
    assert len(recs) == 1 and skipped == 3


def test_los_registros_se_ordenan_por_instante_aunque_el_fichero_no_lo_este():
    a = C.make_record(datetime(2026, 11, 17, 12, 0, tzinfo=UTC), "T", "ok", [date(2026, 11, 13)])
    b = C.make_record(datetime(2026, 11, 17, 6, 0, tzinfo=UTC), "T", "ok", [date(2026, 11, 12)])
    recs, _ = load([a, b])
    assert [r["measured_at"] for r in recs] == ["2026-11-17T06:00:00Z", "2026-11-17T12:00:00Z"]
    assert R.first_seen(recs)["T"][0]["censored"] is True


def test_informe_vacio_no_rompe():
    rep = R.build_report(*load([]))
    assert rep["records"] == 0 and rep["lag"] == {} and rep["first_seen"] == {} and rep["first_measurement"] is None
    assert "Registros: 0" in R.render_markdown(rep)


def test_markdown_trae_las_dos_tablas_con_las_columnas_pedidas():
    recs, sk = load(world())
    md = R.render_markdown(R.build_report(recs, sk))
    for frag in ("Retraso por ticker y franja", "| Ticker | Franja UTC | Mediciones | Mín | Mediana | Máx |", "Primera aparición de cada barra",
                 "Primera aparición (UTC)", "Ausente en (UTC)", f"| {FUND} | 12-14 |", "| 0P00000WLG.F | 2026-11-13 | 2026-11-17T12:17:00Z | 2026-11-17T09:17:00Z | 2 | 4 |", "≤ "):
        assert frag in md, frag


def test_cli_markdown_json_y_errores(tmp_path, capsys):
    p = tmp_path / "fund_lag.jsonl"
    p.write_text("".join(C.dumps_record(r) + "\n" for r in world(date(2026, 11, 16), date(2026, 11, 18))))
    assert R.main([str(p)]) == 0 and "Retraso por ticker y franja" in capsys.readouterr().out
    assert R.main([str(p), "--json", "--slot-hours", "6", "--calendar", "weekdays"]) == 0
    rep = json.loads(capsys.readouterr().out)
    assert rep["slot_hours"] == 6 and rep["calendar"] == "weekdays" and set(rep["lag"]) == {FUND, ETF} and "06-11" in rep["lag"][FUND]
    assert R.main([str(tmp_path / "no_existe")]) == 2
    assert R.main([str(p), "--slot-hours", "0"]) == 2 and R.main([str(p), "--slot-hours", "25"]) == 2


def test_punto_de_entrada_real_sin_dependencias(tmp_path):
    p = tmp_path / "x.jsonl"
    p.write_text("".join(C.dumps_record(r) + "\n" for r in world(date(2026, 11, 16), date(2026, 11, 17))))
    r = subprocess.run([sys.executable, str(ROOT / "scripts" / "fund_lag_report.py"), str(p)], capture_output=True, text=True, timeout=60)
    assert r.returncode == 0 and "Primera aparición" in r.stdout
