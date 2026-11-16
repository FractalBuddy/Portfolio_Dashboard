"""AU01-F1, pieza 3: parada temprana de personal-nav.yml (paso `watch --nav-only`).

Si TODOS los fondos con ficha de iShares tienen ya el NAV del último día hábil esperado, work=false y no se pide nada a iShares. Regla: el último día de lunes
a viernes estrictamente anterior a la fecha UTC del run; festivos desconocidos => se sigue intentando. Fallo CERRADO hacia «visitar la ficha».
Supabase simulado con un servidor HTTP LOCAL (bucle 127.0.0.1) y fixtures sintéticas: ningún ISIN, URL ni token reales."""
from datetime import date, datetime, timedelta, timezone

import pytest

import personal_v2 as pv
from test_personal_v2 import ISIN_A, ISIN_B, URL_A, URL_B, FakeSupabase, Spy, watch_row, _sin_esperas  # noqa: F401 (_sin_esperas: fixture autouse)

MON, TUE, WED, FRI, SAT, SUN = (date(2026, 11, 16), date(2026, 11, 17), date(2026, 11, 18), date(2026, 11, 20), date(2026, 11, 21), date(2026, 11, 22))


def at(day, hour=10):
    return datetime(day.year, day.month, day.day, hour, 23, tzinfo=timezone.utc)


def fund(isin=ISIN_A, url=URL_A, nav_date=None, **extra):
    f = {"isin": isin, "name": "Fondo Falso", "url": url}
    if nav_date is not None:
        f["prev_nav_date_iso"] = nav_date
    f.update(extra)
    return f


def users(*funds):
    return pv.parse_watch([watch_row(funds=list(funds), symbols=[])], pv._plain)[0]


@pytest.fixture()
def sb():
    s = FakeSupabase()
    yield s
    s.close()


def watch_out(sb, tmp_path, watch, now, nav_only=True):
    sb.watch = watch
    out = tmp_path / "gh_output"
    out.write_text("")
    argv = ["watch", "--dir", str(tmp_path / "w"), "--github-output"] + (["--nav-only"] if nav_only else [])
    assert pv.main(argv, env=sb.env(GITHUB_OUTPUT=str(out)), now=now) == 0
    return out.read_text()


# ------------------------------------------------------------------ regla del último día hábil esperado
@pytest.mark.parametrize("today,expected", [
    (MON, date(2026, 11, 13)),          # lunes -> viernes anterior
    (TUE, MON), (WED, TUE), (FRI, date(2026, 11, 19)),
    (SAT, FRI), (SUN, FRI),             # fin de semana -> viernes
    (date(2027, 1, 1), date(2026, 12, 31)),   # cambio de año (un festivo: no se conoce, ver test del festivo)
])
def test_ultimo_dia_habil_esperado(today, expected):
    assert pv.nav_expected_date(today) == expected and pv.nav_expected_date(today).weekday() < 5


def test_el_esperado_nunca_es_hoy_ni_futuro_en_todo_un_ano():
    d = date(2026, 1, 1)
    while d.year == 2026:
        e = pv.nav_expected_date(d)
        assert e < d and e.weekday() < 5 and (d - e).days <= 3
        d += timedelta(days=1)


# ------------------------------------------------------------------ función nav_all_current (con parse_watch real)
def test_todos_al_dia():
    assert pv.nav_all_current(users(fund(ISIN_A, URL_A, "2026-11-17"), fund(ISIN_B, URL_B, "2026-11-17")), WED) == (True, TUE)


def test_un_fondo_atrasado_basta_para_trabajar():
    assert pv.nav_all_current(users(fund(ISIN_A, URL_A, "2026-11-17"), fund(ISIN_B, URL_B, "2026-11-16")), WED)[0] is False


def test_lunes_con_el_nav_del_viernes_esta_al_dia_y_con_el_del_jueves_no():
    assert pv.nav_all_current(users(fund(nav_date="2026-11-13")), MON)[0] is True
    assert pv.nav_all_current(users(fund(nav_date="2026-11-12")), MON)[0] is False


def test_fin_de_semana_con_el_nav_del_viernes_esta_al_dia():
    assert pv.nav_all_current(users(fund(nav_date="2026-11-20")), SAT)[0] is True
    assert pv.nav_all_current(users(fund(nav_date="2026-11-20")), SUN)[0] is True


def test_tras_un_festivo_se_sigue_intentando_y_nunca_se_declara_al_dia_por_error():
    """Viernes 25-dic-2026 festivo: la ficha no trae NAV de ese día. El lunes 28 se espera el del viernes, el guardado es del jueves 24 => se visita la
    ficha (visitas de más, pero nunca un «al día» falso). El martes 29, con el NAV del lunes 28 publicado, ya está al día."""
    assert pv.nav_all_current(users(fund(nav_date="2026-12-24")), date(2026, 12, 28))[0] is False
    assert pv.nav_all_current(users(fund(nav_date="2026-12-24")), date(2026, 12, 25))[0] is True       # el propio festivo: se esperaba el jueves 24
    assert pv.nav_all_current(users(fund(nav_date="2026-12-28")), date(2026, 12, 29))[0] is True


def test_sin_fondos_con_ficha_no_esta_al_dia():
    assert pv.nav_all_current([], WED)[0] is False
    assert pv.nav_all_current(users(fund(url="http://evil.example/x", nav_date="2026-11-17")), WED)[0] is False


def test_los_fondos_sin_ficha_valida_no_cuentan():
    assert pv.nav_all_current(users(fund(ISIN_A, URL_A, "2026-11-17"), fund(ISIN_B, "http://evil.example/x")), WED)[0] is True


def test_varios_usuarios_todos_deben_estar_al_dia():
    rows = [watch_row("u1", funds=[fund(nav_date="2026-11-17")], symbols=[]), watch_row("u2", funds=[fund(ISIN_B, URL_B, "2026-11-13")], symbols=[])]
    assert pv.nav_all_current(pv.parse_watch(rows, pv._plain)[0], WED)[0] is False


@pytest.mark.parametrize("bad", [None, "", "2026-11-17T00:00:00Z", "17/11/2026", "2026-13-01", "2026-02-30", "2026-1-17", 20261117, 1.5, True, ["2026-11-17"],
                                 {"d": "2026-11-17"}, "2026-11-17\nwork=false", " 2026-11-17"])
def test_fecha_malformada_o_ausente_fallo_cerrado(bad):
    f = fund()
    if bad is not None:
        f["prev_nav_date_iso"] = bad
    us, dropped = pv.parse_watch([watch_row(funds=[f], symbols=[])], pv._plain)
    assert dropped == 0 and len(us[0]["funds"]) == 1                       # el fondo se conserva (se visitará); solo se ignora la fecha
    assert "prev_nav_date_iso" not in us[0]["funds"][0]
    assert pv.nav_all_current(us, WED)[0] is False


@pytest.mark.parametrize("future", ["2026-11-19", "2027-01-01"])
def test_fecha_futura_reloj_raro_fallo_cerrado(future):
    assert pv.nav_all_current(users(fund(nav_date=future)), WED)[0] is False


def test_el_nav_de_hoy_cuenta_como_al_dia():
    assert pv.nav_all_current(users(fund(nav_date="2026-11-18")), WED)[0] is True


# ------------------------------------------------------------------ el paso watch de punta a punta (Supabase simulado en local)
def test_watch_todos_al_dia_work_false_sin_visitar_y_con_motivo(sb, tmp_path, capsys):
    out = watch_out(sb, tmp_path, [watch_row(funds=[fund(nav_date="2026-11-17"), fund(ISIN_B, URL_B, "2026-11-17")], symbols=[])], at(WED))
    assert out == "work=false\n"
    log = capsys.readouterr().out
    assert "NAV al día" in log and "2026-11-17" in log and "IE0FAKE" not in log.replace("::add-mask::IE0FAKE00001", "").replace("::add-mask::IE0FAKE00002", "")


def test_watch_uno_atrasado_work_true(sb, tmp_path):
    assert watch_out(sb, tmp_path, [watch_row(funds=[fund(nav_date="2026-11-17"), fund(ISIN_B, URL_B, "2026-11-13")], symbols=[])], at(WED)) == "work=true\n"


def test_watch_lunes(sb, tmp_path):
    assert watch_out(sb, tmp_path, [watch_row(funds=[fund(nav_date="2026-11-13")], symbols=[])], at(MON)) == "work=false\n"
    assert watch_out(sb, tmp_path, [watch_row(funds=[fund(nav_date="2026-11-12")], symbols=[])], at(MON)) == "work=true\n"


def test_watch_tras_un_festivo(sb, tmp_path):
    assert watch_out(sb, tmp_path, [watch_row(funds=[fund(nav_date="2026-12-24")], symbols=[])], at(date(2026, 12, 28))) == "work=true\n"


def test_watch_sin_fondos_sigue_igual(sb, tmp_path):
    assert watch_out(sb, tmp_path, [watch_row(funds=[], symbols=["FAKE1"])], at(WED)) == "work=false\n"
    assert watch_out(sb, tmp_path, [], at(WED)) == "work=false\n"


@pytest.mark.parametrize("bad", ["ayer", "2026-11-31", 0, None])
def test_watch_respuesta_malformada_intenta(sb, tmp_path, bad):
    f = fund()
    f["prev_nav_date_iso"] = bad
    assert watch_out(sb, tmp_path, [watch_row(funds=[f], symbols=[])], at(WED)) == "work=true\n"


def test_watch_sin_el_campo_nuevo_sigue_como_antes_y_visita(sb, tmp_path):
    """Hasta que el SQL añada prev_nav_date_iso a personal_watch_for_backend, nada cambia: siempre se visita (como antes de F1)."""
    assert watch_out(sb, tmp_path, [watch_row(funds=[fund()], symbols=[])], at(WED)) == "work=true\n"


def test_el_modo_completo_nunca_para_por_frescura_del_nav(sb, tmp_path):
    """personal-full trae las series y CREA payloads: la parada temprana es solo del modo NAV."""
    assert watch_out(sb, tmp_path, [watch_row(funds=[fund(nav_date="2026-11-17")], symbols=[])], at(WED), nav_only=False) == "work=true\n"


def test_el_campo_nuevo_no_cambia_lo_que_se_publica(tmp_path):
    """prev_nav_date_iso solo se lee: no entra en p_funds ni en el payload completo (las listas blancas no cambian)."""
    us = users(fund(nav_date="2026-11-17", prev_price=25.4))
    spy = Spy()
    funds = pv.build_nav_funds(us[0], spy.fetchers(), lambda code: None)
    assert funds and all("prev_nav_date_iso" not in f and set(f) <= pv.NAV_FUND_KEYS for f in funds)
    assert "prev_nav_date_iso" not in pv.NAV_FUND_KEYS
    full = pv.build_payload(us[0], set(), spy.fetchers(), at(WED), lambda code: None)
    assert all("prev_nav_date_iso" not in f for f in full["funds"]) and pv.payload_problems(full, WED) == []


# ------------------------------------------------------------------ visitas por fondo y día
def visits_in_a_day(today, stored, appears_at_run, runs=14, rule=None):
    """Simula `runs` pasadas en el día: el NAV del último día hábil aparece en la ficha a partir de la pasada `appears_at_run` (None = nunca, p. ej. festivo).
    Cada pasada que no para pronto visita la ficha (1 visita) y, si el NAV ya está publicado, lo guarda. Devuelve el nº de visitas."""
    rule = rule or pv.nav_all_current
    visits = 0
    for i in range(runs):
        if rule(users(fund(nav_date=stored)), today)[0]:
            continue
        visits += 1
        if appears_at_run is not None and i >= appears_at_run:
            stored = pv.nav_expected_date(today).isoformat()
    return visits


def test_un_dia_normal_baja_de_14_visitas_a_las_necesarias():
    assert visits_in_a_day(WED, "2026-11-16", appears_at_run=2) == 3          # visita hasta que aparece el NAV del martes, luego para
    assert visits_in_a_day(WED, "2026-11-17", appears_at_run=0) == 0          # ya estaba al día: ninguna visita


def test_en_un_festivo_no_para_nunca_es_decir_igual_que_antes():
    assert visits_in_a_day(date(2026, 12, 28), "2026-12-24", appears_at_run=None) == 14


def test_control_negativo_una_regla_laxa_declararia_al_dia_un_nav_atrasado():
    """Con una regla laxa (esperar el NAV de 2 días hábiles atrás), el lunes con el NAV del jueves se daría por «al día»: la prueba del lunes lo detecta."""
    def lax(us, today):
        e = pv.nav_expected_date(pv.nav_expected_date(today))
        return all((pv._iso_day(f.get("prev_nav_date_iso")) or date.min) >= e for f in us[0]["funds"]), e
    assert lax(users(fund(nav_date="2026-11-12")), MON)[0] is True                    # == lo que test_lunes_... rechaza
    assert visits_in_a_day(WED, "2026-11-16", appears_at_run=2, rule=lax) == 0          # y no visitaría nunca: NAV viejo todo el día


def test_control_negativo_fallo_abierto_daria_al_dia_un_fondo_sin_fecha():
    def fail_open(us, today):
        return all(pv._iso_day(f.get("prev_nav_date_iso")) is None or pv._iso_day(f["prev_nav_date_iso"]) >= pv.nav_expected_date(today)
                   for f in us[0]["funds"]), None
    assert fail_open(users(fund()), WED)[0] is True                                     # == lo que test_fecha_malformada_o_ausente_fallo_cerrado rechaza
