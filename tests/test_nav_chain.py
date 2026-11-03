"""Cadena del NAV de iShares (agente F, 20-10-2026): scraping, fechas y red. (Cierre del corte H1: las reglas de v1 sobre `your_funds`
y su sanity-check desaparecieron con los fondos propios; el NAV vive ahora en personal_v2.)

Todo con datos simulados (el sandbox no llega a ishares.com). Cada guarda nueva tiene al menos un test que muere ante su mutante
(ver F_nav/INFORME.md, tabla de mutaciones).
"""
import datetime as dt
import json

import pytest
import requests

import fetch_market_data as fm
from http_fakes import FakeResp
import sanity_check as sc
from test_fetch_market_data import _page, ISIN
from test_sanity_check import healthy, TODAY   # TODAY = martes 2026-10-06


@pytest.fixture(autouse=True)
def _no_sleep(monkeypatch):
    monkeypatch.setattr(fm.time, "sleep", lambda *_a, **_k: None)


@pytest.fixture(autouse=True)
def _fixed_today(monkeypatch):
    real = sc.check          # igual que en test_sanity_check: los tests que pasan por sc.main() usan una fecha fija
    monkeypatch.setattr(sc, "check", lambda *a, **kw: real(*a, **({"today": TODAY} | kw)))


T = dt.date(2026, 10, 6)


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
    ("Oct 03 2026", "2026-10-03"),           # estilo US (mes primero): inequívoco porque el mes va en letras
    ("Oct 3, 2026", "2026-10-03"),
    ("October 03, 2026", "2026-10-03"),
    ("2026-10-03", "2026-10-03"),            # ISO
    ("25/12/2026", "2026-12-25"),            # numérica inequívoca (25 > 12)
    ("12.25.2026", "2026-12-25"),            # numérica inequívoca (mm.dd)
])
def test_nav_date_formatos_parseables(raw, iso):
    assert fm._parse_nav_date(raw).isoformat() == iso


@pytest.mark.parametrize("raw", ["03/10/2026", "01/02/2026", "31/Foo/2026", "31/Feb/2026", "Foo 03 2026", "", "hoy", "03/Oc/2026", "03/Ju/2026", "99/Oct/2026", "03/Mar/26"])
def test_nav_date_ambigua_o_ilegible_es_none(raw):
    assert fm._parse_nav_date(raw) is None


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
class _Resp(FakeResp):
    def __init__(self, status=200, text=""):
        super().__init__(text, status)


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
    assert worst <= fm.NAV_STALE_DAYS


def test_calendario_umbral_antiguo_5_daria_falsos_positivos():
    # Documenta por qué NAV_STALE_DAYS=6 y no 5: con lag 2 existe (Martes de Pascua, 29-dic) una edad de 6 días.
    assert _max_age(2, range(2026, 2041)) == 6 > 5


# ====================================================================== 6. integración: --alerts-out -> workflow
def test_main_alerts_out_lleva_las_alertas(tmp_path, monkeypatch):
    """El mecanismo de Alert -> alerts.txt sigue vivo sin fondos propios (aquí con un precio antiguo persistente)."""
    monkeypatch.setattr(sc, "MIN_DATA_BYTES", 0)
    d, m = healthy()
    d["stocks"][0].update({"stale": True, "stale_since": (TODAY - dt.timedelta(days=3)).isoformat()})
    (tmp_path / "d.json").write_text(json.dumps(d))
    m["data_bytes"] = (tmp_path / "d.json").stat().st_size
    (tmp_path / "m.json").write_text(json.dumps(m))
    out = tmp_path / "alerts.txt"
    rc = sc.main(["--data", str(tmp_path / "d.json"), "--meta", str(tmp_path / "m.json"), "--alerts-out", str(out)])
    txt = out.read_text()
    assert rc == 0 and "S0" in txt and "\n" not in txt and len(txt) <= 900


def test_workflow_manda_los_alert_a_healthchecks_como_degraded():
    # Los Alert viajan por el mecanismo existente: alerts.txt -> output `alerts` -> heartbeat -> «degraded: …» en /fail.
    import pathlib
    wf = (pathlib.Path(__file__).resolve().parent.parent / ".github" / "workflows" / "update-data.yml").read_text()
    assert "--alerts-out" in wf and 'degraded: ${ALERTS}' in wf and '"$HC/fail"' in wf
