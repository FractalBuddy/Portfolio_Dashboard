"""S-01 (revisión nov-2026): las franjas UTC de la sombra NAV Yahoo (config/yahoo_shadow.json) no coincidían con la hora REAL de los runs de
personal-full. El cron (06:38, 13:38 y 21:38 UTC) es solo respaldo y GitHub lo retrasa horas; el disparador externo (AU-01) aún no está activo. Las horas
de inicio de abajo son las 15 de `schedule` medidas entre el 16 y el 23-11-2026 (auditorias/logs_revision_nov2026/runs_reales_resumen.txt): todas caían
fuera de 05-10 y 19-24, así que la sombra no midió nunca.

Arreglo: dos franjas que cubren el día natural UTC entero (00-12 y 12-24). Con `max_reads_per_day` = 2 y la parada temprana ya existentes queda en
«hasta 2 lecturas por día natural, como mucho una por mitad del día, y ninguna más si la anterior ya lo midió todo». La puerta D7 y los topes no cambian.
"""
import json
import pathlib
from datetime import datetime, timezone

import pytest

import yahoo_shadow as ys

ROOT = pathlib.Path(__file__).resolve().parent.parent
CFG = ROOT / "config" / "yahoo_shadow.json"
GEN = ROOT / "config" / "fund_probe_isins_generic.json"
OLD_SLOTS = [[5, 10], [19, 24]]
REAL_STARTS = ["2026-11-16T01:20:48Z", "2026-11-17T04:09:52Z", "2026-11-17T16:26:22Z", "2026-11-18T03:06:55Z", "2026-11-18T15:56:34Z",
               "2026-11-19T02:45:58Z", "2026-11-19T16:01:22Z", "2026-11-20T02:33:45Z", "2026-11-20T15:43:28Z", "2026-11-21T02:47:57Z",
               "2026-11-21T14:54:26Z", "2026-11-22T01:32:19Z", "2026-11-22T14:51:44Z", "2026-11-23T01:56:51Z", "2026-11-23T18:42:25Z"]
N_FUNDS = 2


def cfg(**over):
    c, problem = ys.load_config(str(CFG), str(GEN))
    assert problem is None
    c.update(over)
    return c


def ts(s):
    return datetime.strptime(s, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)


def read(now, slot, pendientes, official_max="2026-11-13"):
    """Lo mínimo de una entrada del libro que mira `decide`."""
    return {"day": now.date().isoformat(), "slot": slot, "official_max": official_max,
            "c": {"pendientes": pendientes, "canario": "ok", "en_sombra": N_FUNDS}}


def simulate(starts, c, pendientes):
    """Runs en orden con el MISMO libro (lo que hace la caché de Actions). Devuelve {día natural: nº de lecturas}."""
    reads, per_day = [], {}
    for s in starts:
        now = ts(s) if isinstance(s, str) else s
        ok, why, slot = ys.decide(now, c, reads, N_FUNDS, "2026-11-13")
        if ok:
            reads.append(read(now, slot, pendientes))
            per_day[now.date()] = per_day.get(now.date(), 0) + 1
        else:
            per_day.setdefault(now.date(), 0)
    return per_day


# ------------------------------------------------------------------ un caso por hora real de arranque
@pytest.mark.parametrize("start", REAL_STARTS)
def test_cada_hora_real_de_arranque_de_personal_full_lee_con_el_libro_vacio(start):
    ok, why, slot = ys.decide(ts(start), cfg(), [], N_FUNDS, "2026-11-13")
    assert (ok, why) == (True, None) and slot == (0 if ts(start).hour < 12 else 1)


@pytest.mark.parametrize("start", REAL_STARTS)
def test_control_negativo_con_las_franjas_viejas_esa_hora_no_leia(start):
    assert ys.decide(ts(start), cfg(slots_utc=OLD_SLOTS), [], N_FUNDS, "2026-11-13") == (False, "fuera de franja", None)


def test_las_franjas_cubren_las_24_horas_y_ninguna_hora_queda_fuera():
    c = cfg()
    assert c["slots_utc"] == [[0, 12], [12, 24]]
    for h in range(24):
        ok, why, _ = ys.decide(datetime(2026, 11, 24, h, 5, tzinfo=timezone.utc), c, [], N_FUNDS, "2026-11-13")
        assert ok and why is None, h


# ------------------------------------------------------------------ frecuencia: como mucho 2 lecturas por día natural
def test_semana_real_una_lectura_por_run_y_nunca_mas_de_dos_al_dia():
    per_day = simulate(REAL_STARTS, cfg(), pendientes=1)
    assert all(1 <= n <= 2 for n in per_day.values()) and sum(per_day.values()) == len(REAL_STARTS)
    assert per_day == {d: 2 if d.day in (17, 18, 19, 20, 21, 22, 23) else 1 for d in per_day}


def test_semana_real_con_todo_medido_la_parada_temprana_deja_una_lectura_al_dia():
    per_day = simulate(REAL_STARTS, cfg(), pendientes=0)
    assert set(per_day.values()) == {1}


def test_disparador_externo_cada_hora_sigue_en_dos_lecturas_al_dia_como_mucho():
    """Cuando AU-01 funcione (un run por hora): 24 runs al día -> 2 lecturas si algo queda pendiente, 1 si la primera lo midió todo."""
    hourly = [datetime(2026, 11, 24 + d, h, 53, tzinfo=timezone.utc) for d in range(3) for h in range(24)]
    assert set(simulate(hourly, cfg(), pendientes=1).values()) == {2}
    assert set(simulate(hourly, cfg(), pendientes=0).values()) == {1}


def test_control_negativo_sin_tope_diario_con_mas_franjas_leeria_mas_de_dos_veces():
    """El tope de 2 lecturas/día de verdad limita: con 4 franjas y tope 4, el disparador horario leería 4 veces."""
    hourly = [datetime(2026, 11, 24, h, 53, tzinfo=timezone.utc) for h in range(24)]
    c = cfg(slots_utc=[[0, 6], [6, 12], [12, 18], [18, 24]], max_reads_per_day=4)
    assert set(simulate(hourly, c, pendientes=1).values()) == {4}
    c = cfg(slots_utc=[[0, 6], [6, 12], [12, 18], [18, 24]])                     # mismo reparto, tope del repo (2)
    assert set(simulate(hourly, c, pendientes=1).values()) == {2}


# ------------------------------------------------------------------ lo que NO cambia
def test_la_puerta_y_los_topes_no_cambian():
    raw = json.loads(CFG.read_text(encoding="utf-8"))
    assert (raw["min_business_days"], raw["gate_equal_pct"], raw["max_bp"], raw["equal_bp"]) == (10, 95, 10, 1)        # puerta D7
    assert raw["max_reads_per_day"] == 2 and raw["max_requests_per_run"] == 60 and ys.HARD_MAX_REQUESTS == 80
