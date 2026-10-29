"""Sesión F (NAV fresco y trazable): parser del NAV de iShares con texto REAL de la ficha (tests/fixtures_fund_facts.json: «dev» y «em» son las fichas de los
fondos clase S en EUR; «iwda» y «sgln» son fichas de ETF en USD) y casos nuevos; log de frescura sin datos privados; descarga única por símbolo entre
usuarios; salida temprana del workflow (work=true|false). Sin red: todo simulado. ISIN, nombres y símbolos de los usuarios son FALSOS."""
import datetime as dt
import io
import json
import pathlib
import re
import contextlib
from datetime import datetime, timezone

import pytest

import fetch_market_data as fm
import personal_v2 as pv
from test_personal_v2 import ISIN_A, ISIN_B, NOW, PUB_KEY, TOKEN, URL_A, URL_B, FakeSupabase, Spy, watch_row, run_all

FIX = json.loads((pathlib.Path(__file__).parent / "fixtures_fund_facts.json").read_text(encoding="utf-8"))
REAL_ISIN = {"dev": "IE000ZYRH0Q7", "em": "IE000QAZP7L2"}
T = dt.date(2026, 10, 21)


@pytest.fixture(autouse=True)
def _sin_esperas(monkeypatch):
    import time
    monkeypatch.setattr(time, "sleep", lambda s: None)


@pytest.fixture()
def sb():
    s = FakeSupabase()
    yield s
    s.close()


def parse(plain, isin="IE000TEST0001", today=T):
    with contextlib.redirect_stderr(io.StringIO()):
        return fm.parse_ishares_nav(plain, isin, today=today)


def page(nav="EUR 12.48", date="20/Oct/2026", isin="IE000TEST0001", extra=""):
    return "Fund NAV as of %s %s 52 WK: 10.27 - 12.66 1 Day NAV Change as of %s EUR -0.01 (-0.07%%) %s Key Facts ISIN %s Net Assets" % (date, nav, date, extra, isin)


# ====================================================================== 1. texto real de la ficha
@pytest.mark.parametrize("key,price,change,wk", [("dev", 12.48, -0.07, (10.27, 12.66)), ("em", 13.43, -1.39, (10.63, 14.76))])
def test_ficha_real_clase_s(key, price, change, wk):
    r = parse(FIX[key], REAL_ISIN[key])
    assert r == {"price": price, "nav_date": "20/Oct/2026", "nav_decimals": 2, "nav_date_iso": "2026-10-20", "nav_stale": False, "change_pct": change,
                 "wk52_low": wk[0], "wk52_high": wk[1]}


def test_precision_de_la_ficha_real_son_dos_decimales():
    """Evidencia de la precisión: TODAS las fichas reales guardadas escriben el NAV con exactamente 2 decimales (y la variación diaria también)."""
    navs = [m for text in FIX.values() for m in re.findall(r"NAV as of \d{2}/\w+/\d{4} (?:NAV as of \d{2}/\w+/\d{4} )?[A-Z]{3} ([\d.,]+)", text)]
    assert len(navs) == len(FIX) == 4
    assert {len(n.split(".")[1]) for n in navs} == {2}
    assert parse(FIX["dev"], REAL_ISIN["dev"])["nav_decimals"] == 2


@pytest.mark.parametrize("key", ["dev", "em"])
def test_ficha_real_cortada_en_cualquier_punto_nunca_da_un_nav_equivocado(key):
    """El ISIN va DESPUÉS del NAV en la ficha real: una página cortada a mitad del número («EUR 1», «EUR 12.») no tiene ISIN y se rechaza. Se prueba en TODOS los puntos de corte."""
    full = FIX[key]
    good = parse(full, REAL_ISIN[key])["price"]
    seen = set()
    for n in range(len(full)):
        r = parse(full[:n], REAL_ISIN[key])
        seen.add(None if r is None else r["price"])
    assert seen == {None, good}


def test_ficha_real_con_nav_duplicado_etiqueta_doble():
    """La ficha de SGLN trae «NAV as of 21/Oct/2026 NAV as of 21/Oct/2026 USD 80.04» (etiqueta duplicada): el patrón de EUR no se confunde, pero una versión en EUR sí se lee y no cuenta como «varios»."""
    p = FIX["sgln"].replace("USD 80.04", "EUR 80.04")
    r = parse(p, "IE00B4ND3602")
    assert r["price"] == 80.04 and "nav_multi" not in r


@pytest.mark.parametrize("key", ["iwda", "sgln"])
def test_ficha_real_en_usd_no_es_un_nav_en_eur(key):
    assert parse(FIX[key], {"iwda": "IE00B4L5Y983", "sgln": "IE00B4ND3602"}[key]) is None


# ====================================================================== 2. varios «NAV as of», separador decimal, fechas
def test_varios_nav_distintos_usa_el_primero_y_lo_cuenta():
    r = parse(page(extra="otra clase NAV as of 20/Oct/2026 EUR 99.99"))
    assert r["price"] == 12.48 and r["nav_multi"] == 2


def test_varios_nav_identicos_no_cuentan_como_varios():
    r = parse(page(extra="NAV as of 20/Oct/2026 EUR 12.48"))
    assert r["price"] == 12.48 and "nav_multi" not in r


def test_varios_nav_con_fechas_distintas_se_usa_el_primero():
    r = parse(page(extra="NAV as of 17/Oct/2026 EUR 12.30 NAV as of 16/Oct/2026 EUR 12.20"))
    assert r["price"] == 12.48 and r["nav_date_iso"] == "2026-10-20" and r["nav_multi"] == 3


@pytest.mark.parametrize("raw,price,decimals", [
    ("EUR 12.48", 12.48, 2), ("EUR 12,48", 12.48, 2), ("EUR 12.4800", 12.48, 4), ("EUR 12,3000", 12.3, 4), ("EUR12.48", 12.48, 2),
    ("EUR 1,234.56", 1234.56, 2), ("EUR 1’234.56", 1234.56, 2), ("EUR 1'234.56", 1234.56, 2), ("EUR 1.234,56", 1234.56, 2), ("EUR 100", 100.0, 0),
])
def test_separadores_decimales_y_de_miles(raw, price, decimals):
    r = parse(page(nav=raw))
    assert r["price"] == price and r["nav_decimals"] == decimals


@pytest.mark.parametrize("raw", ["EUR 12.48.", "EUR 1.2.3", "EUR 12,48,5", "EUR 0.05", "EUR 0.0001", "EUR 20000", "EUR 1’23.45", "EUR ", "EUR -"])
def test_numeros_ambiguos_o_inverosimiles_no_se_publican(raw):
    assert parse(page(nav=raw)) is None


@pytest.mark.parametrize("date,iso", [("20/Oct/2026", "2026-10-20"), ("20 Oct 2026", "2026-10-20"), ("20-oct-2026", "2026-10-20"), ("2026-10-20", "2026-10-20"),
                                      ("Oct 20, 2026", "2026-10-20"), ("October 20 2026", "2026-10-20"), ("20/Sept/2026", "2026-09-20")])
def test_formatos_de_fecha_del_nav(date, iso):
    r = parse(page(date=date))
    assert r is not None and r["nav_date_iso"] == iso and r["nav_stale"] is (iso < "2026-10-01")


@pytest.mark.parametrize("date", ["10/11/2026", "hoy", "20/Foo/2026", "31/Feb/2026"])
def test_fecha_ilegible_o_ambigua_conserva_el_nav_sin_inventar_frescura(date):
    r = parse(page(date=date))
    assert r is not None and r["price"] == 12.48 and "nav_date_iso" not in r and "nav_stale" not in r


def test_fecha_antigua_y_futura():
    assert parse(page(date="01/Oct/2026"))["nav_stale"] is True
    assert "nav_date_iso" not in parse(page(date="30/Oct/2026"))                  # >1 día en el futuro: no creíble


def test_entidades_html_nbsp_y_apostrofo_en_el_nav():
    assert parse(page(nav="EUR&nbsp;12.48"))["price"] == 12.48
    assert parse(page(nav="EUR 1&#8217;234.56"))["price"] == 1234.56
    assert parse(page(nav="EUR\xa012.48"))["price"] == 12.48


# ====================================================================== 3. ISIN ausente, página recortada o ajena
def test_isin_ausente_o_dentro_de_otro_codigo_rechaza_el_nav():
    assert parse("NAV as of 20/Oct/2026 EUR 12.48 52 WK: 10.27 - 12.66") is None
    assert parse(page(isin="XIE000TEST0001Y")) is None
    assert parse(page(isin="IE000TEST0002")) is None                          # otro ISIN


@pytest.mark.parametrize("plain", ["", "Access Denied", "Please verify you are human", "NAV as of", "NAV as of 20/Oct/2026 EUR", "NAV as of 20/Oct/2026 EUR IE000TEST0001"])
def test_paginas_vacias_bloqueadas_o_recortadas(plain):
    assert parse(plain) is None


def test_pagina_recortada_tras_el_nav_conserva_el_nav_y_pierde_los_secundarios():
    r = parse("NAV as of 20/Oct/2026 EUR 12.48 ISIN IE000TEST0001")
    assert r == {"price": 12.48, "nav_date": "20/Oct/2026", "nav_decimals": 2, "nav_date_iso": "2026-10-20", "nav_stale": False}


def test_rango_52_semanas_y_variacion_con_apostrofo_suizo():
    r = parse("NAV as of 20/Oct/2026 EUR 1’234.56 52 WK: 1’100.10 - 1’300.90 1 Day NAV Change as of 20/Oct/2026 EUR 1.20 (0.10%) ISIN IE000TEST0001")
    assert r["price"] == 1234.56 and (r["wk52_low"], r["wk52_high"]) == (1100.10, 1300.90) and r["change_pct"] == 0.10


# ====================================================================== 4. log de frescura
def entry(source="ishares_nav_scrape", nav_date_iso="2026-10-26"):
    e = {"isin": "IE0FAKE00001", "name": "Fondo Falso", "price": 10.0 if source != "none" else None, "currency": "EUR", "unit": "currency", "source": source}
    if nav_date_iso and source != "none":
        e["nav_date_iso"] = nav_date_iso
    return e


def report(*funds, now=NOW, stats=None, days=pv.NAV_WARN_DAYS):
    return pv.nav_report([{"user": "u", "payload": {"funds": list(funds)}}], now, days, stats)


def test_log_de_frescura_cuenta_fondos_nav_exactos_y_fechas():
    lines, warns = report(entry(nav_date_iso="2026-10-26"), entry(nav_date_iso="2026-10-23"), stats={"decimals": {2: 2}})
    assert lines == ["datos propios: NAV: 2 fondos, 2 con NAV exacto leído de la ficha, fecha de NAV más reciente 2026-10-26 (0 d), más antigua 2026-10-23 (3 d), "
                     "decimales del NAV: 2 (x2) [run 2026-10-26T12:00Z]"]
    assert warns == []


def test_log_avisa_si_hay_nav_con_mas_de_n_dias():
    lines, warns = report(entry(nav_date_iso="2026-10-26"), entry(nav_date_iso="2026-10-20"))
    assert warns == ["::warning::datos propios: 1 NAV con más de 5 días (la ficha de iShares puede ir retrasada, o el parseo falla)"]
    _, warns = report(entry(nav_date_iso="2026-10-20"), days=6)
    assert warns == []                                                        # justo 6 días no supera N=6
    _, warns = report(entry(nav_date_iso="2026-10-20"), days=5)
    assert len(warns) == 1


def test_log_fondos_sin_nav_exacto_o_sin_fecha():
    lines, warns = report(entry(), entry("none"), entry(nav_date_iso=None))
    assert "3 fondos, 2 con NAV exacto" in lines[0] and "1 sin fecha legible" in lines[0]
    assert warns == ["::warning::datos propios: 1 de 3 fondos sin NAV exacto (se usa el proxy o el último valor)"]
    lines, _ = report(entry("none"))
    assert "1 fondos, 0 con NAV exacto" in lines[0] and "fecha de NAV más reciente" not in lines[0]


def test_build_imprime_la_frescura_sin_isin_nombres_urls_ni_importes(sb, tmp_path, capsys):
    sb.watch = [watch_row("usuario-1"), watch_row("usuario-2", funds=[{"isin": ISIN_B, "name": "Fondo B Secreto", "url": URL_B, "prev_price": 25.0}], symbols=["FAKE1"])]
    spy = Spy()
    spy.noise = True                                                          # las funciones reutilizadas citan el ISIN en stderr: no debe llegar al log
    rc, _ = run_all(sb, tmp_path, spy=spy)
    out = capsys.readouterr().out
    assert rc == 0
    assert "datos propios: NAV: 2 fondos, 2 con NAV exacto leído de la ficha, fecha de NAV más reciente 2026-10-23 (3 d), más antigua 2026-10-23 (3 d)" in out
    assert "[run 2026-10-26T12:00Z]" in out
    assert "coste del run: 1 series pedidas a Yahoo" in out and "KB" in out
    for secret in (ISIN_A, ISIN_B, "Fondo Falso A", "Fondo B Secreto", URL_A, URL_B, TOKEN, PUB_KEY, "25.5", "usuario-1", "FAKE1"):
        assert secret not in out.replace("::add-mask::" + ISIN_A, "").replace("::add-mask::" + ISIN_B, ""), secret


def sb_with(sb, *rows):
    sb.watch = list(rows)
    return sb


def test_nav_varios_y_decimales_llegan_al_log_como_contadores(sb, tmp_path, capsys):
    spy = Spy()
    base = spy.nav
    spy.nav = lambda url, isin: dict(base(url, isin), nav_decimals=2, nav_multi=2)
    rc, _ = run_all(sb_with(sb, watch_row("usuario-1")), tmp_path, spy=spy)
    out = capsys.readouterr().out
    assert rc == 0 and "decimales del NAV: 2 (x1)" in out and "nav_varios x1" in out


def test_nav_multi_no_se_cuela_en_el_payload(sb, tmp_path):
    spy = Spy()
    base = spy.nav
    spy.nav = lambda url, isin: dict(base(url, isin), nav_decimals=2, nav_multi=2)
    run_all(sb_with(sb, watch_row("usuario-1")), tmp_path, spy=spy)
    [pub] = sb.calls(pv.RPC_PUBLISH)
    f = pub[2]["p_payload"]["funds"][0]
    assert "nav_multi" not in f and "nav_decimals" not in f and f["price"] == 25.5 and pv.payload_problems(pub[2]["p_payload"], NOW.date()) == []


# ====================================================================== 5. una sola descarga de Yahoo por símbolo entre usuarios
def test_un_simbolo_seguido_por_varios_usuarios_se_descarga_una_vez(sb, tmp_path, capsys):
    spy = Spy()
    rows = [watch_row("usuario-%d" % i, funds=[], symbols=["FAKE1", "FAKE2"]) for i in range(1, 6)]
    rc, _ = run_all(sb_with(sb, *rows), tmp_path, spy=spy)
    assert rc == 0 and sorted(spy.series_calls) == ["FAKE1", "FAKE2"]          # 10 símbolos seguidos, 2 peticiones
    assert [p[2]["p_user"] for p in sb.calls(pv.RPC_PUBLISH)] == ["usuario-%d" % i for i in range(1, 6)]
    for p in sb.calls(pv.RPC_PUBLISH):
        assert [s["symbol"] for s in p[2]["p_payload"]["series"]] == ["FAKE1", "FAKE2"]
    assert "coste del run: 2 series pedidas a Yahoo" in capsys.readouterr().out


def test_un_simbolo_que_falla_no_se_vuelve_a_pedir_y_cuenta_para_cada_usuario(sb, tmp_path, capsys):
    spy = Spy()
    rows = [watch_row("usuario-1", funds=[], symbols=["BOOM", "FAKE1"]), watch_row("usuario-2", funds=[], symbols=["BOOM"])]
    rc, _ = run_all(sb_with(sb, *rows), tmp_path, spy=spy)
    out = capsys.readouterr().out
    assert rc == 0 and spy.series_calls.count("BOOM") == 1 and "serie_fallo x2" in out


# ====================================================================== 6. salida temprana: work=true|false
def run_watch(sb, tmp_path, env_extra=None, flag=True):
    out = tmp_path / "gh_output"
    env = sb.env(GITHUB_OUTPUT=str(out), **(env_extra or {}))
    rc = pv.main(["watch", "--dir", str(tmp_path / "work")] + (["--github-output"] if flag else []), env=env)
    return rc, (out.read_text() if out.exists() else "")


@pytest.mark.parametrize("watch,work", [
    ([watch_row("u1")], "true"),                                                                                 # un fondo con ficha válida
    ([watch_row("u1", funds=[], symbols=["FAKE1"])], "true"),                                                     # solo símbolos: se sigue refrescando (no hay regresión)
    ([watch_row("u1", funds=[], symbols=[])], "false"),                                                          # lista vacía
    ([], "false"),
    (None, "false"),
    ([watch_row("u1", funds=[{"isin": ISIN_A, "name": "F", "url": "https://evil.example.com/x"}], symbols=[])], "false"),     # ficha no admitida: nada que pedir
])
def test_watch_escribe_work(sb, tmp_path, capsys, watch, work):
    sb.watch = watch
    rc, out = run_watch(sb, tmp_path)
    log = capsys.readouterr().out
    assert rc == 0 and out == "work=%s\n" % work
    assert ("nada que refrescar" in log) == (work == "false")
    for secret in (ISIN_A, URL_A, TOKEN):
        assert secret not in log.replace("::add-mask::" + ISIN_A, "")


def test_watch_con_la_rpc_caida_escribe_work_false_y_no_falla(sb, tmp_path, capsys):
    sb.watch_status = 500
    rc, out = run_watch(sb, tmp_path)
    assert rc == 0 and out == "work=false\n" and "::warning::" in capsys.readouterr().out


def test_watch_sin_la_bandera_o_sin_github_output_no_escribe_nada(sb, tmp_path):
    sb.watch = [watch_row("u1")]
    rc, out = run_watch(sb, tmp_path, flag=False)
    assert rc == 0 and out == ""
    assert pv.main(["watch", "--dir", str(tmp_path / "w2"), "--github-output"], env=sb.env()) == 0          # sin $GITHUB_OUTPUT: no revienta


def test_watch_sin_configuracion_no_escribe_work(tmp_path):
    out = tmp_path / "gh_output"
    assert pv.main(["watch", "--dir", str(tmp_path / "w"), "--github-output"], env={"GITHUB_OUTPUT": str(out)}) == 0
    assert not out.exists()                                                  # sin work=true los pasos condicionados no se ejecutan (== 'true')


def test_now_inyectable_y_un_nav_de_ayer_no_avisa(sb, tmp_path, capsys):
    reg = tmp_path / "r.json"
    reg.write_text(json.dumps({"schema": 1, "symbols": [{"symbol": "AAPL", "kind": "yahoo", "name": "Apple", "currency": "USD", "unit": "currency", "tier": "hot",
                                                          "status": "active", "fail_streak": 0}]}))
    sb.watch = [watch_row("u1")]
    rc = pv.main(["all", "--dir", str(tmp_path / "w"), "--registry", str(reg)], fetchers=Spy().fetchers(), env=sb.env(), now=datetime(2026, 10, 24, 6, 0, tzinfo=timezone.utc))
    assert rc == 0 and "NAV con más de" not in capsys.readouterr().out                # NAV del 23-10 visto el 24-10: 1 día


def test_el_aviso_de_edad_se_dispara_cuando_el_reloj_se_aleja(sb, tmp_path, capsys):
    reg = tmp_path / "r.json"
    reg.write_text(json.dumps({"schema": 1, "symbols": [{"symbol": "AAPL", "kind": "yahoo", "name": "Apple", "currency": "USD", "unit": "currency", "tier": "hot",
                                                          "status": "active", "fail_streak": 0}]}))
    sb.watch = [watch_row("u1")]
    pv.main(["all", "--dir", str(tmp_path / "w"), "--registry", str(reg)], fetchers=Spy().fetchers(), env=sb.env(), now=datetime(2026, 10, 30, 6, 0, tzinfo=timezone.utc))
    assert "1 NAV con más de 5 días" in capsys.readouterr().out                       # NAV del 23-10 visto el 30-10: 7 días


def test_watch_real_con_python_S_solo_biblioteca_estandar_y_work(sb, tmp_path):
    """Como lo ejecuta el workflow: `python3 -S scripts/personal_v2.py watch ... --github-output` (sin site-packages) contra un Supabase local falso."""
    import os
    import subprocess
    import sys
    sb.watch = [watch_row("u1")]
    out = tmp_path / "gh_output"
    env = {"PATH": os.environ.get("PATH", ""), "GITHUB_OUTPUT": str(out), **sb.env()}
    script = pathlib.Path(__file__).resolve().parent.parent / "scripts" / "personal_v2.py"
    r = subprocess.run([sys.executable, "-S", str(script), "watch", "--dir", str(tmp_path / "w"), "--github-output"], env=env, capture_output=True, text=True, timeout=60)
    assert r.returncode == 0, r.stderr
    assert out.read_text() == "work=true\n" and "1 usuarios, 1 fondos con ficha, 2 símbolos" in r.stdout
    assert TOKEN not in r.stdout + r.stderr and PUB_KEY not in r.stdout + r.stderr


def test_readme_documenta_el_workflow_nuevo_su_cron_y_la_salida_temprana():
    root = pathlib.Path(__file__).resolve().parent.parent
    readme = (root / "README.md").read_text(encoding="utf-8")
    doc = (root / "docs" / "CONTRATO_FEED_V2.md").read_text(encoding="utf-8")
    cron = "23,53 5-21 * * 1-5"
    assert cron in (root / ".github" / "workflows" / "personal-nav.yml").read_text(encoding="utf-8")
    for frag in ("personal-nav.yml", cron, "work=true|false", "2 decimales", "FEED_V2_ENABLED", "PERSONAL_FEED_TOKEN", "propia concurrencia", "único escritor", "nunca ISIN"):
        assert frag in readme, frag
    for frag in ("personal-nav.yml", cron, "único escritor", "2 decimales", "payload completo"):
        assert frag in doc, frag


def test_una_descarga_max_de_un_proxy_sirve_tambien_como_extra_de_otro_usuario(sb, tmp_path, capsys):
    """Integración con el histórico largo: el proxy se pide con period=max y la misma descarga sirve al usuario que lo sigue como símbolo extra (2y)."""
    spy = Spy()
    rows = [watch_row("u1", funds=[{"isin": ISIN_A, "name": "A", "url": URL_A, "proxy_symbol": "PROXY1"}], symbols=["PROXY1", "FAKE1"]),
            watch_row("u2", funds=[{"isin": ISIN_B, "name": "B", "url": URL_B, "proxy_symbol": "PROXY1"}], symbols=["FAKE1"]),
            watch_row("u3", funds=[], symbols=["PROXY1"])]
    rc, _ = run_all(sb_with(sb, *rows), tmp_path, spy=spy)
    assert rc == 0 and sorted(spy.series_calls) == ["FAKE1", "PROXY1"] and spy.periods == {"PROXY1": "max", "FAKE1": "2y"}
    assert "coste del run: 2 series pedidas a Yahoo" in capsys.readouterr().out
