"""Medición del retraso de fondos 0P: calendario de días hábiles, esquema/validación del registro, lista de tickers y autolímite (sin Yahoo, sin red)."""
import json
import pathlib
import re
from datetime import date, datetime, timedelta, timezone

import pytest

import fund_lag_common as C

ROOT = pathlib.Path(__file__).resolve().parent.parent
D = date.fromisoformat
UTC = timezone.utc


# ------------------------------------------------------------------ calendario
@pytest.mark.parametrize("year,iso", [(2024, "2024-03-31"), (2025, "2025-04-20"), (2026, "2026-04-05"), (2027, "2027-03-28"), (2028, "2028-04-16"),
                                      (2029, "2029-04-01"), (2030, "2030-04-21")])
def test_pascua_conocida(year, iso):
    assert C.easter_sunday(year) == D(iso)


def test_festivos_target_2026_son_exactamente_seis():
    assert C.target_holidays(2026) == {D("2026-01-01"), D("2026-04-03"), D("2026-04-06"), D("2026-05-01"), D("2026-12-25"), D("2026-12-26")}


def test_no_son_festivos_target_ni_el_24_ni_el_31_de_diciembre_ni_el_dia_de_reyes():
    for iso in ("2025-12-24", "2025-12-31", "2026-01-06", "2026-12-24", "2026-12-31"):
        assert D(iso) not in C.target_holidays(D(iso).year)


@pytest.mark.parametrize("last,today,target,weekdays", [
    ("2026-11-13", "2026-11-17", 2, 2),          # viernes -> martes: lunes y martes (el caso medido el 17-11)
    ("2026-11-13", "2026-11-14", 0, 0),          # sábado: no es hábil, no suma
    ("2026-11-13", "2026-11-15", 0, 0),          # domingo
    ("2026-11-13", "2026-11-16", 1, 1),          # lunes
    ("2026-11-17", "2026-11-17", 0, 0),          # barra de hoy (un ETF cotizado): 0
    ("2026-11-18", "2026-11-17", 0, 0),          # barra «del futuro» (desfase horario): nunca negativo
    ("2026-11-16", "2026-11-17", 1, 1),          # barra del día hábil anterior: 1
    ("2026-04-02", "2026-04-07", 1, 3),          # Viernes Santo (3-abr) y Lunes de Pascua (6-abr): solo cuenta el martes (L-V cuenta vie, lun y mar)
    ("2025-12-24", "2025-12-29", 1, 3),          # 25 y 26 de diciembre (jue, vie): solo cuenta el lunes 29
    ("2025-12-31", "2026-01-02", 1, 2),          # 1 de enero: cuenta solo el viernes 2
    ("2026-04-30", "2026-05-04", 1, 2),          # 1 de mayo (viernes): solo cuenta el lunes 4
])
def test_dias_habiles_entre_ultima_barra_y_hoy(last, today, target, weekdays):
    assert C.business_days_between(D(last), D(today), "target") == target
    assert C.business_days_between(D(last), D(today), "weekdays") == weekdays


def test_el_intervalo_es_abierto_por_la_izquierda_y_cerrado_por_la_derecha():
    assert C.business_days_between(D("2026-11-16"), D("2026-11-17")) == 1      # cuenta hoy
    assert C.business_days_between(D("2026-11-17"), D("2026-11-17")) == 0      # no cuenta la propia barra


def test_conteo_L_V_coincide_con_numpy_busday_count_en_muchos_pares():
    np = pytest.importorskip("numpy")
    start = D("2025-12-01")
    for i in range(0, 120, 3):
        for j in range(0, 20):
            last, today = start + timedelta(days=i), start + timedelta(days=i + j)
            assert C.business_days_between(last, today, "weekdays") == int(np.busday_count(last + timedelta(days=1), today + timedelta(days=1))), (last, today)


def test_conteo_target_coincide_con_numpy_pasandole_los_festivos():
    np = pytest.importorskip("numpy")
    hol = sorted(h.isoformat() for y in (2025, 2026, 2027) for h in C.target_holidays(y))
    start = D("2025-12-15")
    for i in range(0, 400, 2):
        last, today = start + timedelta(days=i), start + timedelta(days=i + 9)
        assert C.business_days_between(last, today, "target") == int(np.busday_count(last + timedelta(days=1), today + timedelta(days=1), holidays=hol)), (last, today)


def test_la_regla_esta_documentada_en_el_modulo():
    doc = C.__doc__
    for frag in ("REGLA DE DÍAS HÁBILES", "última_barra < d <= hoy", "TARGET2", "Viernes Santo", "Lunes de Pascua", "weekdays", "lag_wd", "lag_bd", "Frankfurt"):
        assert frag in doc, frag


# ------------------------------------------------------------------ registro
def at(s):
    return datetime.fromisoformat(s.replace("Z", "+00:00")).astimezone(UTC)


def good(**kw):
    rec = C.make_record(at("2026-11-17T12:17:05Z"), "0P00000WLG.F", "ok", [D("2026-11-11"), D("2026-11-12"), D("2026-11-13")])
    rec.update(kw)
    return rec


def test_registro_ok_tiene_todas_las_claves_y_los_retrasos_correctos():
    r = good()
    assert tuple(r) == C.RECORD_KEYS
    assert r == {"measured_at": "2026-11-17T12:17:05Z", "ticker": "0P00000WLG.F", "status": "ok", "last_bar": "2026-11-13", "bars": 3,
                 "dates": ["2026-11-11", "2026-11-12", "2026-11-13"], "lag_bd": 2, "lag_wd": 2, "lag_days": 4}


def test_registro_de_error_lleva_nulos_y_las_mismas_claves():
    r = C.make_record(at("2026-11-17T12:17:05Z"), "IWDA.L", "429")
    assert tuple(r) == C.RECORD_KEYS and r["last_bar"] is None and r["dates"] == [] and r["lag_bd"] is None
    assert C.check_record(r) == []


def test_la_fecha_de_hoy_es_la_fecha_UTC_del_instante():
    r = C.make_record(at("2026-11-17T23:59:59Z"), "T", "ok", [D("2026-11-16")])
    assert r["lag_bd"] == 1
    r = C.make_record(at("2026-11-18T00:00:01Z"), "T", "ok", [D("2026-11-16")])
    assert r["lag_bd"] == 2
    r = C.make_record(datetime(2026, 11, 18, 0, 30, tzinfo=timezone(timedelta(hours=2))), "T", "ok", [D("2026-11-16")])      # 22:30 UTC del 17
    assert r["measured_at"] == "2026-11-17T22:30:00Z" and r["lag_bd"] == 1


def test_un_registro_valido_pasa_y_la_linea_es_estable():
    assert C.check_record(good()) == []
    line = C.dumps_record(good())
    assert "\n" not in line and json.loads(line) == good()


@pytest.mark.parametrize("mutacion", [
    lambda r: r.update(extra=1), lambda r: r.pop("dates"), lambda r: r.update(ticker="no es un ticker"), lambda r: r.update(ticker="lower.f"),
    lambda r: r.update(status="otro"), lambda r: r.update(measured_at="2026-11-17 12:17:05"), lambda r: r.update(measured_at="2026-13-17T12:17:05Z"),
    lambda r: r.update(last_bar="2026-W47-5"), lambda r: r.update(last_bar="20261113"), lambda r: r.update(last_bar=20261113),
    lambda r: r.update(bars=True), lambda r: r.update(bars=0), lambda r: r.update(bars="3"), lambda r: r.update(lag_bd=-1), lambda r: r.update(lag_bd=1.5),
    lambda r: r.update(lag_bd=None), lambda r: r.update(dates=["2026-11-13", "2026-11-11"]), lambda r: r.update(dates=["2026-11-12"]), lambda r: r.update(dates=[]),
    lambda r: r.update(dates="2026-11-13"), lambda r: r.update(dates=["2026-11-11", "x", "2026-11-13"]),
])
def test_control_negativo_registros_invalidos_se_rechazan(mutacion):
    r = good()
    mutacion(r)
    assert C.check_record(r) != []


def test_control_negativo_un_error_con_datos_o_un_ok_sin_datos_no_es_valido():
    e = C.make_record(at("2026-11-17T12:17:05Z"), "IWDA.L", "vacio")
    e["last_bar"] = "2026-11-13"
    assert C.check_record(e) != []
    e = C.make_record(at("2026-11-17T12:17:05Z"), "IWDA.L", "vacio")
    e["dates"] = ["2026-11-13"]
    assert C.check_record(e) != []
    assert C.check_record(good(status="vacio")) != []                      # «vacio» con medida
    assert C.check_record(C.make_record(at("2026-11-17T12:17:05Z"), "T", "no_probado")) == []


def test_no_dict_no_es_registro():
    for x in (None, [], "x", 3):
        assert C.check_record(x) == ["claves"]


def test_lineas_ticker_fuera_de_la_lista_se_rechaza_con_lista_y_se_acepta_sin_ella():
    text = C.dumps_record(good(ticker="OTRO.F")) + "\n"
    assert C.check_lines(text, {"0P00000WLG.F"})[1] != []
    assert C.check_lines(text)[1] == []


def test_lineas_json_roto_longitud_vacio_y_demasiadas():
    assert C.check_lines("")[1] == ["vacio"]
    assert C.check_lines("no json\n")[1] == ["linea 1: json"]
    assert C.check_lines(C.dumps_record(good()) + "\n{\n")[1] == ["linea 2: json"]
    assert C.check_lines("x" * (C.MAX_LINE_CHARS + 1) + "\n")[1] == ["linea 1: longitud"]
    many = (C.dumps_record(good()) + "\n") * (C.MAX_NEW_LINES + 1)
    assert "demasiadas_lineas" in C.check_lines(many)[1]
    assert C.check_lines((C.dumps_record(good()) + "\n") * C.MAX_NEW_LINES)[1] == []


def test_los_problemas_nunca_citan_el_contenido_del_registro():
    probs = C.check_lines(json.dumps({"ticker": "SECRETO-123", "x": "VALOR-PRIVADO"}) + "\n")[1]
    assert probs and "SECRETO" not in " ".join(probs) and "VALOR-PRIVADO" not in " ".join(probs)


def test_validate_cli(tmp_path, capsys):
    ok = tmp_path / "ok.jsonl"
    ok.write_text(C.dumps_record(good()) + "\n")
    assert C.main(["validate", str(ok)]) == 0
    bad = tmp_path / "bad.jsonl"
    bad.write_text(C.dumps_record(good(ticker="NO.EN.LISTA")) + "\n")
    assert C.main(["validate", str(bad)]) == 1 and "::error::" in capsys.readouterr().out
    assert C.main(["validate", str(tmp_path / "no_existe")]) == 2
    assert C.main([]) == 2 and C.main(["otra", "x"]) == 2


# ------------------------------------------------------------------ lista de tickers
EXPECTED = ["0P00000WLG.F", "0P00012PP6.F", "0P0001CLDK.F", "0P0001CLDM.F", "0P00016YQ5.F", "0P0001DFE8.F", "0P000168OI.F", "IWDA.L", "VWCE.DE", "SPXS.L"]


def test_la_lista_fija_es_exactamente_la_pedida_y_en_ese_orden():
    lst = C.load_tickers(ROOT / "config" / "fund_lag_tickers.json")
    assert [e["ticker"] for e in lst] == EXPECTED
    assert [e["kind"] for e in lst] == ["fondo"] * 7 + ["control_etf"] * 3


def test_la_lista_no_tiene_isin_ni_nada_de_la_cartera_del_dueno():
    import privacy_guard as pg
    text = (ROOT / "config" / "fund_lag_tickers.json").read_text(encoding="utf-8")
    assert not pg.ISIN_WINDOW.search(text) and not re.search(r"\b[A-Z]{2}(?=[A-Z0-9]*\d)[A-Z0-9]{10}\b", text)
    assert pg.hits(text, pg.PROXY_HASHES) == [] and pg.hits(text, pg.CRYPTO_HASHES) == []
    assert set(json.loads(text)[0]) == {"ticker", "kind"}                  # ni nombres, ni cantidades, ni nada más


@pytest.mark.parametrize("content", [
    "[]", "{}", "no json", '[{"ticker": "A.F"}]', '[{"ticker": "A.F", "kind": "otro"}]', '[{"ticker": "A.F", "kind": "fondo", "x": 1}]',
    '[{"ticker": "a.f", "kind": "fondo"}]', '[{"ticker": "A F", "kind": "fondo"}]', json.dumps([{"ticker": "I" + "E" + "0" * 9 + "1", "kind": "fondo"}]),      # forma de ISIN construida en ejecución: el guardia del repo no admite el literal
    '[{"ticker": "A.F", "kind": "fondo"}, {"ticker": "A.F", "kind": "fondo"}]', '[{"ticker": 3, "kind": "fondo"}]',
    json.dumps([{"ticker": "T%d.F" % i, "kind": "fondo"} for i in range(C.MAX_TICKERS + 1)]),
])
def test_control_negativo_listas_invalidas_se_rechazan(tmp_path, content):
    p = tmp_path / "t.json"
    p.write_text(content)
    with pytest.raises(ValueError):
        C.load_tickers(p)


def test_lista_ilegible(tmp_path):
    with pytest.raises(ValueError):
        C.load_tickers(tmp_path / "no_existe.json")


# ------------------------------------------------------------------ autolímite
NOW = at("2026-11-24T12:00:00Z")


def head(started):
    return json.dumps({"measured_at": started, "ticker": "X"}) + "\n"


def test_primera_linea_de_hace_exactamente_7_dias_todavia_mide():
    assert C.decide_run("found", head("2026-11-17T12:00:00Z"), NOW)[0] == "medir"


def test_primera_linea_de_hace_7_dias_y_un_segundo_caduca():
    assert C.decide_run("found", head("2026-11-17T11:59:59Z"), NOW)[0] == "caducado"


def test_primera_linea_reciente_o_futura_mide():
    assert C.decide_run("found", head("2026-11-23T12:00:00Z"), NOW)[0] == "medir"
    assert C.decide_run("found", head("2026-12-01T12:00:00Z"), NOW)[0] == "medir"


def test_sin_registro_mide_y_no_poder_leerlo_no_mide():
    assert C.decide_run("missing", "", NOW)[0] == "medir"
    assert C.decide_run("error", "", NOW)[0] == "sin_registro_legible"
    assert C.decide_run("lo-que-sea", head("2026-11-24T00:00:00Z"), NOW)[0] == "sin_registro_legible"


def test_registro_vacio_mide_y_primera_linea_ilegible_caduca():
    assert C.decide_run("found", "", NOW)[0] == "medir" and C.decide_run("found", "\n", NOW)[0] == "medir"
    for bad in ("no json\n", "[]\n", '{"measured_at": 5}\n', '{"otro": "2026-11-24T00:00:00Z"}\n', '{"measured_at": "ayer"}\n'):
        assert C.decide_run("found", bad, NOW)[0] == "caducado", bad


def test_el_limite_es_configurable_y_por_defecto_7():
    assert C.MAX_AGE_DAYS == 7
    assert C.decide_run("found", head("2026-11-20T11:59:59Z"), NOW, max_age_days=4)[0] == "caducado"
    assert C.decide_run("found", head("2026-11-20T12:00:00Z"), NOW, max_age_days=4)[0] == "medir"
