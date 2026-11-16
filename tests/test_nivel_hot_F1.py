"""AU01-F1, pieza 2: nivel `hot` más frecuente (TIER_MIN_INTERVAL_HOURS["hot"] = 0.75 h = 45 min).

Con el disparador externo lanzando publish-feed-v2 cada ~60 min en sesión, un intervalo hot de 3 h haría que solo uno de cada tres runs refrescase los hot.
Todo con el Yahoo simulado de tests/v2_world.py: ningún test sale a Internet."""
import os
from datetime import timedelta

import pytest

import feed_v2 as f2
import feed_v2_common as c
import fetch_market_data as fm
import freshness_gate as fg

from v2_world import NOW, REG, gen, jl

HOT = [e["symbol"] for e in REG if e["kind"] == "yahoo" and e["tier"] == "hot"]
WARM = [e["symbol"] for e in REG if e["kind"] == "yahoo" and e["tier"] == "warm"]


def run_with_last_ok(minutes_ago, tier="hot", status="active"):
    run = f2.Run(REG, "/no/usado", None, NOW)
    entry = {"symbol": "AAPL", "tier": tier, "status": status}
    run.prev_state = {"AAPL": {"status": "active", "last_ok": f2.iso_z(NOW - timedelta(minutes=minutes_ago)),
                               "last_try": f2.iso_z(NOW - timedelta(minutes=minutes_ago))}}
    return run.is_due(entry, run.status_of(entry))


def test_intervalo_hot_es_45_min_y_los_demas_no_cambian():
    assert c.TIER_MIN_INTERVAL_HOURS == {"hot": 0.75, "warm": 20, "cold": 6 * 24}


@pytest.mark.parametrize("minutes,due", [(1, False), (30, False), (44, False), (44.98, False), (45, True), (46, True), (60, True), (180, True)])
def test_frontera_hot_44_no_46_si(minutes, due):
    assert run_with_last_ok(minutes) is due


@pytest.mark.parametrize("minutes,due", [(46, False), (60, False), (19 * 60, False), (20 * 60, True)])
def test_warm_sigue_en_20_h(minutes, due):
    assert run_with_last_ok(minutes, tier="warm") is due


def _daily_calls(world):
    return {s for s, p, i in world.calls if i == "1d"}


def test_un_run_una_hora_despues_refresca_todos_los_hot_y_solo_los_hot(world, tmp_path):
    """Con el reloj REAL del generador (generated_at = hora de inicio del run = last_ok de cada símbolo actualizado)."""
    out1, run1 = gen(tmp_path, "r1")
    assert all(run1.state[s]["last_ok"] == run1.generated_at == f2.iso_z(NOW) for s in HOT)     # last_ok = hora de inicio, no la de fin
    world.calls.clear()
    out2, run2 = gen(tmp_path, "r2", prev=out1, now=NOW + timedelta(minutes=60))
    assert _daily_calls(world) == set(HOT)
    assert all(jl(out2, "health.json")["symbols"][s]["last_ok"] == run2.generated_at for s in HOT)
    assert all(jl(out2, "health.json")["symbols"][s]["last_ok"] == run1.generated_at for s in WARM)      # warm no toca a la hora


def test_un_run_a_los_44_min_no_vuelve_a_pedir_los_hot(world, tmp_path):
    out1, _ = gen(tmp_path, "r1")
    world.calls.clear()
    gen(tmp_path, "r2", prev=out1, now=NOW + timedelta(minutes=44))
    assert _daily_calls(world) == set()


@pytest.mark.parametrize("delta_min", [-14, -10, -5, 0, 5, 20, 120])
def test_margen_un_run_horario_con_desfase_de_arranque_hasta_menos_14_min_refresca(world, tmp_path, delta_min):
    """El run B arranca 60 min después del A ± el desfase entre sus tiempos de preparación (checkout, pip, descarga del sitio anterior) y la latencia
    del dispatch. Con 45 min de intervalo, un desfase de hasta -15 min sigue refrescando los hot (margen: 15 min). Ver el informe."""
    out1, _ = gen(tmp_path, "r1")
    world.calls.clear()
    gen(tmp_path, "r2", prev=out1, now=NOW + timedelta(minutes=60 + delta_min))
    assert _daily_calls(world) == set(HOT)


def test_la_puerta_de_frescura_y_el_intervalo_hot_son_coherentes():
    """Un run que pasa la puerta (manifest con >= 40 min) arranca el generador más tarde que la puerta (checkout + pip + descarga del sitio anterior,
    varios minutos). La franja muerta (pasa la puerta pero no toca ningún hot) es [40, 45) min de edad en el momento de la puerta, antes de restar
    la preparación del propio run: con >= 5 min de preparación no existe."""
    gap = c.TIER_MIN_INTERVAL_HOURS["hot"] * 60 - fg.DEFAULT_MAX_AGE_MIN
    assert 0 <= gap <= 5


def test_una_jornada_horaria_peticiones_por_run_acotadas_y_sin_tocar_el_limitador(world, tmp_path):
    """Una jornada de 14 runs cada 60 min con el registro REAL: cada run (salvo el de alta) refresca todos los hot y hace <= 2 peticiones por hot
    (1 diaria de 1 mes + 1 intradía) más los warm que tocan; el ritmo, la pausa por ráfaga y el abandono por 429 no cambian de valor."""
    reg = c.load_registry(os.path.join(os.path.dirname(__file__), "..", "registry.json"))
    world.decl = {e["symbol"]: e for e in reg}
    hot = [e["symbol"] for e in reg if e["kind"] == "yahoo" and e["tier"] == "hot"]
    assert len(hot) == 49
    prev, t = None, NOW
    for i in range(3):                                                         # alta (cupo de 80 descargas largas por run)
        prev, _ = gen(tmp_path, "alta%d" % i, prev=prev, now=t, registry=reg)
        t += timedelta(hours=21)                                               # que en la jornada no caiga ningún warm de rebote del alta
    per_run = []
    for i in range(14):
        t += timedelta(minutes=60)
        world.calls.clear()
        prev, run = gen(tmp_path, "h%02d" % i, prev=prev, now=t, registry=reg)
        daily = {s for s, p, k in world.calls if k == "1d"}
        assert set(hot) <= daily, i                                            # todos los hot, en cada run horario
        hot_calls = sum(1 for s, p, k in world.calls if s in hot)
        assert hot_calls <= 2 * len(hot) and all(p == "1mo" for s, p, k in world.calls if k == "1d" and s in hot)
        per_run.append(len(world.calls))
    assert max(per_run) <= 2 * len(hot) + sum(e["tier"] == "warm" for e in reg if e["kind"] == "yahoo")
    if os.environ.get("V2_MEASURE"):
        print("\n  jornada horaria: %d runs, peticiones a Yahoo por run %s, total %d" % (len(per_run), per_run, sum(per_run)))
    # el limitador sigue intacto (valores de BE-08) y ninguna prueba lo da por roto
    assert fm.YF_REQ_GAP == (0.15, 0.35) and fm.YF_BURST_SIZE == 40 and fm.YF_BURST_PAUSE == 5.0
    assert fm.YF_RATE_LIMIT_MAX_CONSECUTIVE == 3 and fm.YF_RATE_LIMIT_MAX_TOTAL == 6 and fm.YF_RATE_LIMIT_PAUSE == (30.0, 60.0)
    assert f2.V2_YAHOO_BUDGET_SECONDS == 13 * 60 and f2.V2_HARD_BUDGET_SECONDS == 17 * 60


def test_con_429_sostenido_un_run_horario_abandona_yahoo_y_conserva_los_datos(world, tmp_path, monkeypatch):
    """El interruptor ante 429 sigue funcionando con la frecuencia nueva: abandona Yahoo y los hot pendientes se arrastran (aplazados), sin fallo."""
    out1, _ = gen(tmp_path, "r1")
    monkeypatch.setattr(fm, "_YF_ABANDONED", True)
    out2, run2 = gen(tmp_path, "r2", prev=out1, now=NOW + timedelta(minutes=60))
    h = jl(out2, "health.json")
    assert set(HOT) <= set(run2.problems["deferred"]) and h["counts"]["failed"] == 0
    assert all(jl(out2, "s/%s/latest.json" % s)["as_of"] == jl(out1, "s/%s/latest.json" % s)["as_of"] for s in HOT)
