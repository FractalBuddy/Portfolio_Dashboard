"""Cadena del NAV de iShares de punta a punta (agente F, 20-10-2026): scraping -> your_funds -> sanity-check -> Alert.

Todo con datos simulados (el sandbox no llega a ishares.com). Cada guarda nueva tiene al menos un test que muere ante su mutante
(ver F_nav/INFORME.md, tabla de mutaciones).
"""
import datetime as dt
import json

import pytest
import requests

import fetch_market_data as fm
import sanity_check as sc
from test_fetch_market_data import _page, ISIN
from test_sanity_check import healthy, run as sc_run, TODAY   # TODAY = martes 2026-10-06


@pytest.fixture(autouse=True)
def _no_sleep(monkeypatch):
    monkeypatch.setattr(fm.time, "sleep", lambda *_a, **_k: None)


@pytest.fixture(autouse=True)
def _fixed_today(monkeypatch):
    real = sc.check          # igual que en test_sanity_check: los tests que pasan por sc.main() usan una fecha fija
    monkeypatch.setattr(sc, "check", lambda *a, **kw: real(*a, **({"today": TODAY} | kw)))


T = dt.date(2026, 10, 6)


def alerts(warns):
    return [w for w in warns if isinstance(w, sc.Alert)]


# ====================================================================== 1. parseo de la fecha del NAV
@pytest.mark.parametrize("raw,iso", [
    ("03/Oct/2026", "2026-10-03"),           # formato real de iShares CH
    ("03/oct/2026", "2026-10-03"),           # minúsculas
    ("03/OCT/2026", "2026-10-03"),
    ("3/Oct/2026", "2026-10-03"),            # día sin cero
    ("26/Sept/2026", "2026-09-26"),          # «Sept»: iShares lo usa en otras fechas de la misma página (fecha de inicio «26/Sept/2008»)
    ("26/Sep/2026", "2026-09-26"),
    ("03/October/2026", "2026-10-03"),       # mes completo
    ("03/Octubre/2026", "2026-10-03"),       # español
    ("04/ene/2026", "2026-01-04"),           # abreviaturas españolas que no existen en inglés
    ("04/abr/2026", "2026-04-04"),
    ("04/ago/2026", "2026-08-04"),
    ("04/dic/2026", "2026-12-04"),
    ("03-Oct-2026", "2026-10-03"),
    ("03 Oct 2026", "2026-10-03"),
    ("2026-10-03", "2026-10-03"),            # ISO
    ("25/12/2026", "2026-12-25"),            # numérica inequívoca (25 > 12)
    ("12.25.2026", "2026-12-25"),            # numérica inequívoca (mm.dd)
])
def test_nav_date_formatos_parseables(raw, iso):
    assert fm._parse_nav_date(raw).isoformat() == iso
    assert sc._nav_date({"nav_date": raw}).isoformat() == iso            # el parser del sanity coincide


@pytest.mark.parametrize("raw", ["03/10/2026", "01/02/2026", "31/Foo/2026", "31/Feb/2026", "Oct 03 2026", "", "hoy", "03/Oc/2026", "03/Ju/2026", "99/Oct/2026", "03/Mar/26"])
def test_nav_date_ambigua_o_ilegible_es_none(raw):
    assert fm._parse_nav_date(raw) is None
    assert sc._nav_date({"nav_date": raw}) is None


def test_nav_date_ambigua_no_inventa_frescura():
    r = fm.parse_ishares_nav(_page(date="03/10/2026"), ISIN, today=T)
    assert r["price"] == 12.3456 and "nav_stale" not in r and "nav_date_iso" not in r      # se queda sin verificar (el sanity avisa)


@pytest.mark.parametrize("date", ["2026-10-03", "03 Oct 2026", "03-Oct-2026", "26/Sept/2026"])
def test_nav_date_el_regex_del_nav_acepta_otros_formatos(date):
    r = fm.parse_ishares_nav(_page(date=date), ISIN, today=T)
    assert r is not None and r["nav_date"] == date and r["nav_date_iso"] == fm._parse_nav_date(date).isoformat()


def test_nav_date_sept_cuenta_como_stale_correctamente():
    # Antes «Sept» no casaba con el regex de 3 letras: nav_stale desaparecía en silencio y un NAV de septiembre pasaba por bueno.
    r = fm.parse_ishares_nav(_page(date="20/Sept/2026"), ISIN, today=T)
    assert r["nav_stale"] is True and r["nav_date_iso"] == "2026-09-20"


def test_nav_date_futura_no_es_fresca():
    r = fm.parse_ishares_nav(_page(date="09/Oct/2026"), ISIN, today=T)       # +3 días
    assert r["price"] == 12.3456 and "nav_stale" not in r and "nav_date_iso" not in r
    r = fm.parse_ishares_nav(_page(date="07/Oct/2026"), ISIN, today=T)       # +1 día: tolerancia de huso horario
    assert r["nav_stale"] is False and r["nav_date_iso"] == "2026-10-07"


@pytest.mark.parametrize("days,stale", [(0, False), (1, False), (4, False), (6, False), (7, True), (30, True)])
def test_nav_stale_frontera_6_dias(days, stale):
    d = T - dt.timedelta(days=days)
    r = fm.parse_ishares_nav(_page(date=d.strftime("%d/%b/%Y")), ISIN, today=T)
    assert r["nav_stale"] is stale


def test_nav_stale_usa_fecha_utc_aunque_dublin_vaya_una_hora_por_delante():
    # 00:30 UTC del martes = 01:30 en Dublín: la fecha UTC (la del runner) manda; el desfase máximo es de un día y NAV_FUTURE_DAYS lo absorbe.
    r = fm.parse_ishares_nav(_page(date="07/Oct/2026"), ISIN, today=dt.date(2026, 10, 6))
    assert r["nav_stale"] is False


# ====================================================================== 2. NAV inverosímil / página rota
@pytest.mark.parametrize("nav", ["0", "0.0", "0.05", "-5.2", "99999", "12.3.4", "1,23,456", "NaN"])
def test_nav_inverosimil_cae_al_proxy(nav):
    assert fm.parse_ishares_nav(_page(nav=nav), ISIN, today=T) is None


@pytest.mark.parametrize("nav,exp", [("12,3456", 12.3456), ("1,234.56", 1234.56), ("0.11", 0.11), ("9999.99", 9999.99)])
def test_nav_formatos_numericos_validos(nav, exp):
    assert fm.parse_ishares_nav(_page(nav=nav), ISIN, today=T)["price"] == exp


@pytest.mark.parametrize("plain", ["", "   ", "Access Denied", "Please verify you are human - captcha", "NAV as of", "<html></html>"])
def test_pagina_vacia_o_captcha_cae_al_proxy(plain):
    assert fm.parse_ishares_nav(plain, ISIN, today=T) is None


# ====================================================================== 3. red: 403, 5xx, timeout, captcha con 200
class _Resp:
    def __init__(self, status=200, text=""):
        self.status_code, self.text = status, text

    def raise_for_status(self):
        if self.status_code >= 400:
            e = requests.HTTPError("HTTP %d" % self.status_code)
            e.response = self
            raise e


def _seq(monkeypatch, *items):
    calls = []

    def get(*a, **k):
        calls.append(1)
        it = items[min(len(calls), len(items)) - 1]
        if isinstance(it, Exception):
            raise it
        return it
    monkeypatch.setattr(fm.requests, "get", get)
    return calls


GOOD = _Resp(200, "<p>%s</p>" % _page(date="03/Oct/2026"))


def test_red_403_no_se_reintenta(monkeypatch):
    calls = _seq(monkeypatch, _Resp(403), GOOD)
    assert fm.fetch_ishares_nav("https://x.test", ISIN) is None and len(calls) == 1


def test_red_captcha_200_no_se_reintenta(monkeypatch):
    calls = _seq(monkeypatch, _Resp(200, "<html>Pardon our interruption... captcha</html>"), GOOD)
    assert fm.fetch_ishares_nav("https://x.test", ISIN) is None and len(calls) == 1


@pytest.mark.parametrize("first", [requests.Timeout("lento"), requests.ConnectionError("reset"), _Resp(503), _Resp(502), _Resp(429)])
def test_red_fallo_transitorio_se_reintenta_una_vez(monkeypatch, first):
    calls = _seq(monkeypatch, first, GOOD)
    r = fm.fetch_ishares_nav("https://x.test", ISIN)
    assert r is not None and r["price"] == 12.3456 and len(calls) == 2


def test_red_dos_fallos_seguidos_caen_al_proxy_sin_bucle(monkeypatch):
    calls = _seq(monkeypatch, requests.Timeout("lento"))
    assert fm.fetch_ishares_nav("https://x.test", ISIN) is None and len(calls) == fm.ISHARES_TRIES == 2


# ====================================================================== 4. your_funds: qué ve el usuario según el caso
def _build(monkeypatch, scraped, chg=0.7):
    monkeypatch.setattr(fm, "fetch_ishares_nav", lambda url, isin: scraped)
    monkeypatch.setattr(fm, "fetch_ishares_fund_facts", lambda url: {})
    monkeypatch.setattr(fm, "compute_fund_analytics", lambda s: {})
    monkeypatch.setattr(fm, "fetch_full_history", lambda s: [])
    monkeypatch.setattr(fm, "fetch_symbol", lambda s: {"change_pct": chg})
    return fm.build_your_funds()


def test_your_funds_scrape_ok_lleva_fecha_iso_y_stale(monkeypatch):
    out = _build(monkeypatch, fm.parse_ishares_nav(_page(date="25/Sep/2026"), ISIN, today=T))
    f = out[0]
    assert f["source"] == "ishares_nav_scrape" and f["nav_date"] == "25/Sep/2026" and f["nav_date_iso"] == "2026-09-25" and f["nav_stale"] is True


def test_your_funds_scrape_caido_no_arrastra_nav_ni_fecha(monkeypatch):
    out = _build(monkeypatch, None)
    f = out[0]
    assert f["source"] == "proxy_approx" and f["price"] is None and f["change_pct"] == 0.7
    assert "nav_date" not in f and "nav_stale" not in f and "nav_date_iso" not in f      # el feed no hereda el NAV anterior: el front conserva su ancla


# ====================================================================== 5. calendario: ningún festivo normal dispara «antiguo»
def _easter(y):
    a = y % 19; b = y // 100; c = y % 100; d = b // 4; e = b % 4; f = (b + 8) // 25; g = (b - f + 1) // 3
    h = (19 * a + b - d - g + 15) % 30; i = c // 4; k = c % 4; l = (32 + 2 * e + 2 * i - h - k) % 7
    m = (a + 11 * h + 22 * l) // 451; mo = (h + l - 7 * m + 114) // 31; da = (h + l - 7 * m + 114) % 31 + 1
    return dt.date(y, mo, da)


def _holidays(y):
    e, s = _easter(y), set()
    s |= {e - dt.timedelta(2), e + dt.timedelta(1)}
    for d in (dt.date(y, 1, 1), dt.date(y, 12, 25), dt.date(y, 12, 26)):
        while d.weekday() >= 5 or d in s:
            d += dt.timedelta(1)
        s.add(d)
    return s


def _max_age(lag, years):
    hol = set()
    for y in range(min(years) - 1, max(years) + 2):
        hol |= _holidays(y)
    worst = 0
    for y in years:
        d = dt.date(y, 1, 1)
        while d.year == y:
            x, n = d, 0
            while n < lag:
                x -= dt.timedelta(1)
                if x.weekday() < 5 and x not in hol:
                    n += 1
            worst = max(worst, (d - x).days)
            d += dt.timedelta(1)
    return worst


@pytest.mark.parametrize("lag", [1, 2])
def test_calendario_festivos_normales_nunca_son_antiguos_ni_alerta(lag):
    """Página 1-2 días hábiles por detrás (lo observado en el feed real) durante 2026-2040: la edad máxima cabe en el umbral."""
    worst = _max_age(lag, range(2026, 2041))
    assert worst <= fm.NAV_STALE_DAYS and worst <= sc.NAV_STALE_DAYS < sc.NAV_ALERT_DAYS


def test_calendario_umbral_antiguo_5_daria_falsos_positivos():
    # Documenta por qué NAV_STALE_DAYS=6 y no 5: con lag 2 existe (Martes de Pascua, 29-dic) una edad de 6 días.
    assert _max_age(2, range(2026, 2041)) == 6 > 5


def test_umbrales_coherentes_entre_backend_y_sanity():
    assert fm.NAV_STALE_DAYS == sc.NAV_STALE_DAYS == 6 and sc.NAV_ALERT_DAYS == 8 and fm.NAV_FUTURE_DAYS == sc.NAV_FUTURE_DAYS


# ====================================================================== 6. sanity: el NAV congelado llega a Healthchecks
def _feed_nav(d0=None, d1=None, **extra):
    """healthy() con las fechas de NAV de los dos fondos (días de antigüedad respecto a TODAY)."""
    d, m = healthy()
    for f, age in zip(d["your_funds"], (d0, d1)):
        if age is not None:
            f["nav_date"] = (TODAY - dt.timedelta(days=age)).strftime("%d/%b/%Y")
    for k, v in extra.items():
        d["your_funds"][0][k] = v
    return d, m


def test_sanity_nav_normal_no_avisa():
    d, m = _feed_nav(1, 2)
    errs, warns = sc_run(d, m, None)
    assert errs == [] and not any("NAV" in w and "antiguo" in w for w in warns) and alerts(warns) == []


@pytest.mark.parametrize("age", [5, 6])
def test_sanity_hasta_6_dias_es_festivo_normal(age):
    d, m = _feed_nav(age, age)
    errs, warns = sc_run(d, m, None)
    assert errs == [] and alerts(warns) == [] and not any("antiguo" in w for w in warns)


def test_sanity_7_dias_un_fondo_es_solo_aviso():
    d, m = _feed_nav(7, 2)
    errs, warns = sc_run(d, m, None)
    assert errs == [] and alerts(warns) == [] and any("antiguo" in w and "7 días" in w for w in warns)


@pytest.mark.parametrize("age", [8, 9, 30, 400])
def test_sanity_8_dias_o_mas_en_un_fondo_es_alerta(age):
    d, m = _feed_nav(age, 1)
    errs, warns = sc_run(d, m, None)
    al = alerts(warns)
    assert errs == [] and len(al) == 1 and "congelado" in al[0] and ("%d días" % age) in al[0] and "IE0000000001" in al[0]


def test_sanity_todos_antiguos_a_la_vez_es_alerta_aunque_sean_7_dias():
    d, m = _feed_nav(7, 7)
    errs, warns = sc_run(d, m, None)
    al = alerts(warns)
    assert errs == [] and len(al) == 1 and "TODOS" in al[0]


def test_sanity_el_flag_nav_stale_del_feed_no_manda_si_la_fecha_es_buena():
    d, m = _feed_nav(1, 1, nav_stale=True)
    errs, warns = sc_run(d, m, None)
    assert errs == [] and alerts(warns) == [] and any("antiguo" in w for w in warns)       # solo reflejo en el log


def test_sanity_un_feed_que_miente_con_nav_stale_false_se_pilla_por_la_fecha():
    d, m = _feed_nav(20, 1, nav_stale=False)
    assert len(alerts(sc_run(d, m, None)[1])) == 1


def test_sanity_nav_date_iso_tiene_prioridad_y_sept_se_lee():
    d, m = _feed_nav(1, 1)
    d["your_funds"][0]["nav_date_iso"] = (TODAY - dt.timedelta(days=12)).isoformat()
    assert len(alerts(sc_run(d, m, None)[1])) == 1
    d, m = healthy()
    d["your_funds"][0]["nav_date"] = "26/Sept/2026"                                       # 10 días antes de TODAY
    al = alerts(sc_run(d, m, None)[1])
    assert len(al) == 1 and "2026-09-26" in al[0]


@pytest.mark.parametrize("raw", ["03/10/2026", "fecha rara", "", None, 20261003])
def test_sanity_fecha_ilegible_es_alerta(raw):
    d, m = healthy()
    d["your_funds"][0]["nav_date"] = raw
    al = alerts(sc_run(d, m, None)[1])
    assert len(al) == 1 and "ilegible" in al[0]


def test_sanity_fecha_futura_es_alerta_pero_manana_no():
    d, m = healthy()
    d["your_funds"][0]["nav_date"] = "09/Oct/2026"
    al = alerts(sc_run(d, m, None)[1])
    assert len(al) == 1 and "futuro" in al[0]
    d["your_funds"][0]["nav_date"] = "07/Oct/2026"
    assert alerts(sc_run(d, m, None)[1]) == []


def test_sanity_fondo_en_proxy_sin_fecha_no_dispara_las_reglas_de_fecha():
    d, m = healthy()
    f = d["your_funds"][0]
    f.update({"source": "proxy_approx", "price": None}); f.pop("nav_date")
    errs, warns = sc_run(d, m, None)
    assert errs == [] and alerts(warns) == []                                              # sin prev: solo el aviso de log del proxy


def test_sanity_scrape_roto_dos_runs_seguidos_es_alerta():
    prev, _ = healthy()
    for f in prev["your_funds"]:
        f.update({"source": "proxy_approx", "price": None}); f.pop("nav_date")
    d, m = healthy()
    for f in d["your_funds"]:
        f.update({"source": "proxy_approx", "price": None}); f.pop("nav_date")
    errs, warns = sc_run(d, m, prev)
    al = alerts(warns)
    assert errs == [] and len(al) == 1 and "sigue sin NAV exacto" in al[0] and "IE0000000001" in al[0] and "IE0000000002" in al[0]


def test_sanity_primer_fallo_del_scrape_sigue_siendo_la_alerta_de_nav_perdido_no_la_de_persistencia():
    prev, _ = healthy()                                                                    # prev con NAV
    d, m = healthy()
    for f in d["your_funds"]:
        f.update({"source": "proxy_approx", "price": None}); f.pop("nav_date")
    al = alerts(sc_run(d, m, prev)[1])
    assert len(al) == 1 and "se ha perdido el NAV exacto" in al[0]


def test_sanity_scrape_que_se_recupera_no_alerta():
    prev, _ = healthy()
    for f in prev["your_funds"]:
        f.update({"source": "proxy_approx", "price": None})
    d, m = healthy()
    assert alerts(sc_run(d, m, prev)[1]) == []


def test_sanity_prev_sin_your_funds_no_dispara_persistencia():
    prev, _ = healthy(); prev["your_funds"] = []
    d, m = healthy()
    for f in d["your_funds"]:
        f.update({"source": "proxy_approx", "price": None}); f.pop("nav_date")
    assert not any("sigue sin NAV" in w for w in alerts(sc_run(d, m, prev)[1]))


# ====================================================================== 7. integración: --alerts-out -> workflow
def test_main_alerts_out_lleva_el_nav_congelado(tmp_path, monkeypatch):
    monkeypatch.setattr(sc, "MIN_DATA_BYTES", 0)
    d, m = _feed_nav(15, 1)
    (tmp_path / "d.json").write_text(json.dumps(d))
    m["data_bytes"] = (tmp_path / "d.json").stat().st_size
    (tmp_path / "m.json").write_text(json.dumps(m))
    out = tmp_path / "alerts.txt"
    rc = sc.main(["--data", str(tmp_path / "d.json"), "--meta", str(tmp_path / "m.json"), "--alerts-out", str(out)])
    txt = out.read_text()
    assert rc == 0 and "congelado" in txt and "\n" not in txt and len(txt) <= 900


def test_workflow_manda_los_alert_a_healthchecks_como_degraded():
    # El Alert del NAV viaja por el mecanismo existente: alerts.txt -> output `alerts` -> heartbeat -> «degraded: …» en /fail.
    import pathlib
    wf = (pathlib.Path(__file__).resolve().parent.parent / ".github" / "workflows" / "update-data.yml").read_text()
    assert "--alerts-out" in wf and 'degraded: ${ALERTS}' in wf and '"$HC/fail"' in wf
