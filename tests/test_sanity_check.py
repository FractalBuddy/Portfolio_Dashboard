"""Tests de scripts/sanity_check.py con feeds sintéticos (sin red).

Cada caso de A1 (el feed degradado que antes PASABA) debe fallar ahora; el feed sano debe pasar.
"""
import copy
import datetime as dt
import json

import pytest

import sanity_check as sc

TODAY = dt.date(2026, 10, 6)  # martes
BYTES = 8_000_000


@pytest.fixture(autouse=True)
def _fixed_today(monkeypatch):
    """Los feeds sintéticos llevan fechas fijas (alrededor de TODAY): sin esto, los tests que pasan por sc.main() (que usa la fecha
    real) empezaban a fallar al cumplirse MAX_HISTORY_AGE_DAYS desde TODAY (bomba de relojería)."""
    real = sc.check
    monkeypatch.setattr(sc, "check", lambda *a, **kw: real(*a, **({"today": TODAY} | kw)))


def _hist(n=300, last=TODAY - dt.timedelta(days=1)):
    return [{"date": (last - dt.timedelta(days=n - 1 - i)).isoformat(), "close": 100.0 + i} for i in range(n)]


def _rows(prefix, n, hist=True):
    out = []
    for i in range(n):
        r = {"symbol": "%s%d" % (prefix, i), "name": "n", "price": 10.0 + i, "change_pct": 0.5}
        if hist:
            r["history"] = _hist()
        out.append(r)
    return out


def healthy():
    d = {
        "generated_at": "2026-10-06T06:30:00+00:00",
        "indices": _rows("I", 9) + [{"symbol": "^GSPC", "name": "S&P", "price": 5000.0, "change_pct": 0.3, "history": _hist()}],
        "stocks": _rows("S", 31),
        "funds": _rows("F", 4),
        "commodities": _rows("C", 8),
        "forex": _rows("X", 6),
        "heatmap": _rows("H", 17, hist=False),
        "sector_heatmap": _rows("K", 11, hist=False),
        "your_funds": [
            {"isin": "IE0000000001", "price": 12.3456, "change_pct": 0.4, "source": "ishares_nav_scrape", "nav_date": "03/Oct/2026"},
            {"isin": "IE0000000002", "price": 9.87, "change_pct": -0.2, "source": "ishares_nav_scrape", "nav_date": "03/Oct/2026"},
        ],
        "rates": [{"series": "DGS10", "value": 4.1, "prev": 4.0, "date": "2026-10-02"}],
        "cpi": [{"date": "2024-%02d-01" % (i % 12 + 1), "value": 100 + i} for i in range(12)]
               + [{"date": "2026-09-01", "value": 130.0}],
        "cpi_meta": {"stale": False, "last_date": "2026-09-01"},
        "news": {"general": [{"title": "t", "link": "https://x.test/a"}], "positions": []},
        "benchmarks": {"sp500": [{"date": "2026-10-01", "close": 1.0}] * 40, "msci_world": []},
        "market_mood": {"vix": {"value": 15.0}, "vix_50ma": {"value": 16.0}, "crypto_fear_greed": {"value": 50}},
    }
    m = {"generated_at": d["generated_at"], "data_bytes": BYTES, "schema": 1}
    return d, m


def run(d, m, prev=None, prev_bytes=BYTES, **kw):
    kw.setdefault("data_bytes", BYTES)
    kw.setdefault("today", TODAY)
    kw.setdefault("fred_key_set", True)
    return sc.check(d, m, prev, prev_bytes=prev_bytes, **kw)


def test_feed_sano_pasa_sin_y_con_anterior():
    d, m = healthy()
    errs, _ = run(d, m, None)
    assert errs == []
    prev, _ = healthy()
    errs, warns = run(d, m, prev)
    assert errs == []
    assert not any("sin feed anterior" in w for w in warns)


def test_sin_anterior_avisa_pero_no_falla():
    d, m = healthy()
    errs, warns = run(d, m, {})
    assert errs == []
    assert any("sin feed anterior" in w for w in warns)


# --------------------------- casos de A1: todos deben FALLAR ---------------------------
def test_a1_acciones_e_indices_con_una_sola_barra():
    d, m = healthy()
    for k in ("indices", "stocks"):
        for x in d[k]:
            x["history"] = x["history"][-1:]
    errs, _ = run(d, m, None)  # incluso SIN feed anterior (regla absoluta de mediana)
    assert any("mediano" in e for e in errs)


def test_a1_historico_congelado_hace_40_dias():
    d, m = healthy()
    old = TODAY - dt.timedelta(days=40)
    for k in sc.HISTORY_LISTS:
        for x in d[k]:
            if x.get("symbol") != "^GSPC":  # el antiguo check solo miraba ^GSPC
                x["history"] = _hist(last=old)
    errs, _ = run(d, m, None)
    assert any("congelado" in e for e in errs)


def test_a1_cinco_de_treinta_acciones_ausentes_vs_anterior():
    prev, _ = healthy()
    prev["stocks"] = _rows("S", 31)
    d, m = healthy()
    d["stocks"] = d["stocks"][:26]  # 26 >= 25: pasaba la regla absoluta
    errs, _ = run(d, m, None)
    assert errs == []  # sin anterior solo hay reglas absolutas: demuestra que antes pasaba
    errs, _ = run(d, m, prev)
    assert any("han desaparecido" in e and e.startswith("stocks") for e in errs)


def test_a1_nueve_de_treinta_y_uno_sin_historico_pasaba_con_70pct_ajustado():
    d, m = healthy()
    for x in d["stocks"][:10]:
        x["history"] = []
    errs, _ = run(d, m, None)
    assert any("stocks no tiene histórico" in e for e in errs)  # 21/31 = 67 % < 70 %


def test_a1_your_funds_sin_nav_ni_variacion():
    d, m = healthy()
    for f in d["your_funds"]:
        f["price"] = None
        f["change_pct"] = None
        f["source"] = "proxy_approx"
    errs, warns = run(d, m, None)
    assert errs == []  # aviso, no bloqueo
    assert any("your_funds sin NAV" in w for w in warns)


def test_a1_your_funds_pierde_nav_exacto_vs_anterior():
    prev, _ = healthy()
    d, m = healthy()
    d["your_funds"][0]["price"] = None  # queda change_pct del proxy: antes pasaba
    d["your_funds"][0]["source"] = "proxy_approx"
    errs, _ = run(d, m, None)
    assert errs == []
    errs, warns = run(d, m, prev)
    assert errs == []  # solo aviso: el NAV es un scrape secundario
    assert any("NAV exacto" in w for w in warns)
    assert any("no viene del scrape" in w for w in warns)


@pytest.mark.parametrize("k", ["forex", "commodities", "funds"])
def test_a1_sin_historico_en_funds_commodities_forex(k):
    d, m = healthy()
    for x in d[k]:
        x["history"] = []
    errs, _ = run(d, m, None)
    assert any(k in e and "histórico" in e for e in errs)


def test_a1_market_mood_solo_cripto():
    d, m = healthy()
    d["market_mood"] = {"crypto_fear_greed": {"value": 40}}
    errs, _ = run(d, m, None)
    assert any("market_mood" in e for e in errs)


def test_a1_historico_acortado_mas_del_10pct():
    prev, _ = healthy()
    d, m = healthy()
    for x in d["stocks"][:5]:
        x["history"] = x["history"][-200:]  # 300 -> 200 barras (-33 %); la mediana (300) sigue pasando la regla absoluta
    assert run(d, m, None)[0] == []
    errs, _ = run(d, m, prev)
    assert any("acortado" in e for e in errs)


def test_a1_tamano_menor_del_60pct_del_anterior():
    prev, _ = healthy()
    d, m = healthy()
    m["data_bytes"] = 4_000_000
    errs, _ = run(d, m, prev, prev_bytes=8_000_000, data_bytes=4_000_000)  # 50 %
    assert any("del anterior" in e for e in errs)


def test_tamano_minimo_absoluto():
    d, m = healthy()
    m["data_bytes"] = 500_000
    errs, _ = run(d, m, None, data_bytes=500_000)
    assert any("pequeño" in e for e in errs)


# --------------------------- reglas que ya existían (se mantienen) ---------------------------
def test_se_mantienen_reglas_absolutas_previas():
    d, m = healthy()
    d["stocks"] = d["stocks"][:9]
    assert run(d, m)[0]  # < 10 acciones

    d, m = healthy()
    d["indices"] = d["indices"][:4]
    assert run(d, m)[0]

    d, m = healthy()
    d["news"] = {"general": [], "positions": []}
    assert any("noticias" in e for e in run(d, m)[0])

    d, m = healthy()
    d["news"]["general"][0]["link"] = "javascript:alert(1)"
    assert any("http" in e for e in run(d, m)[0])

    d, m = healthy()
    m["generated_at"] = "otro"
    assert any("no coinciden" in e for e in run(d, m)[0])

    d, m = healthy()
    assert any("demasiado grande" in e for e in run(d, m, data_bytes=19_000_000)[0])

    d, m = healthy()
    d["benchmarks"]["sp500"] = []
    assert any("S&P" in e for e in run(d, m)[0])

    d, m = healthy()
    d["heatmap"] = d["heatmap"][:3]
    assert any("países" in e for e in run(d, m)[0])

    d, m = healthy()
    for x in d["stocks"][:5]:
        x["price"] = -1
    assert any("inverosímil" in e for e in run(d, m)[0])

    d, m = healthy()
    d["indices"] = [x for x in d["indices"] if x["symbol"] != "^GSPC"] + _rows("Z", 1)
    assert any("^GSPC" in e for e in run(d, m)[0])


def test_fred_con_clave_exige_tipos_e_ipc_sin_clave_no():
    d, m = healthy()
    d["rates"], d["cpi"] = [], []
    errs, _ = run(d, m, None, fred_key_set=True)
    assert any("tipos" in e for e in errs) and any("IPC" in e for e in errs)
    errs, _ = run(d, m, None, fred_key_set=False)
    assert errs == []


def test_fred_reutilizado_se_publica_con_aviso():
    d, m = healthy()
    for r in d["rates"]:
        r["stale"], r["stale_since"] = True, "2026-09-01"
    d["cpi_meta"].update({"stale": True, "reused_from_previous": True, "stale_since": "2026-09-01"})
    errs, warns = run(d, m, None)
    assert errs == []
    assert any("reutilizados" in w for w in warns) and any("más de 7 días" in w for w in warns)


# --------------------------- override y robustez ---------------------------
def test_allow_regression_convierte_regresion_en_aviso_pero_no_las_absolutas():
    prev, _ = healthy()
    d, m = healthy()
    d["stocks"] = d["stocks"][:26]
    errs, warns = run(d, m, prev, allow_regression=True)
    assert errs == [] and any("regresión permitida" in w for w in warns)
    d["stocks"] = d["stocks"][:9]
    assert run(d, m, prev, allow_regression=True)[0]


def test_inyeccion_de_comandos_no_pasa_a_la_salida(tmp_path, capsys):
    d, m = healthy()
    d["stocks"][0]["symbol"] = "A\n::set-env name=X::1"
    d["stocks"][0]["price"] = -5
    d["stocks"][1]["price"] = -5
    d["stocks"][2]["price"] = -5
    (tmp_path / "d.json").write_text(json.dumps(d))
    m["data_bytes"] = (tmp_path / "d.json").stat().st_size
    (tmp_path / "m.json").write_text(json.dumps(m))
    rc = sc.main(["--data", str(tmp_path / "d.json"), "--meta", str(tmp_path / "m.json")])
    out = capsys.readouterr().out
    assert rc == 1
    assert not any(line.startswith("::set-env") for line in out.splitlines())


def test_main_feed_ilegible_y_anterior_ilegible(tmp_path, capsys):
    (tmp_path / "d.json").write_text("{no json")
    (tmp_path / "m.json").write_text("{}")
    assert sc.main(["--data", str(tmp_path / "d.json"), "--meta", str(tmp_path / "m.json")]) == 1
    assert "ilegible" in capsys.readouterr().out


def test_main_anterior_corrupto_se_ignora_con_aviso(tmp_path, capsys, monkeypatch):
    d, m = healthy()
    # tamaño mínimo real: relleno para superar los 3 MB reales del fichero
    d["padding"] = "x" * 3_100_000
    p = tmp_path / "d.json"
    p.write_text(json.dumps(d))
    m["data_bytes"] = p.stat().st_size
    (tmp_path / "m.json").write_text(json.dumps(m))
    (tmp_path / "prev.json").write_text("<html>404")
    monkeypatch.setenv("FRED_KEY_SET", "true")
    rc = sc.main(["--data", str(p), "--meta", str(tmp_path / "m.json"), "--prev", str(tmp_path / "prev.json")])
    out = capsys.readouterr().out
    assert rc == 0, out
    assert "feed anterior ilegible" in out


def test_main_feed_con_tipos_raros_no_explota(tmp_path, capsys):
    d, m = healthy()
    d["stocks"] = "no soy una lista"
    d["news"] = {"general": None}
    p = tmp_path / "d.json"
    p.write_text(json.dumps(d))
    (tmp_path / "m.json").write_text(json.dumps(m))
    assert sc.main(["--data", str(p), "--meta", str(tmp_path / "m.json")]) == 1


def test_no_muta_el_feed():
    d, m = healthy()
    before = copy.deepcopy(d)
    run(d, m, healthy()[0])
    assert d == before


# ============================================================ auditoría 12-10: perfiles de acciones y filas con precio antiguo
def _with_profile(d, n=None, as_of=TODAY.isoformat()):
    for i, x in enumerate(d["stocks"][: n or len(d["stocks"])]):
        x.update({"market_cap": 1e11 + i, "enterprise_value": 1.1e11, "cap_currency": "USD", "financial_currency": "USD",
                  "pe_trailing": 20.0, "pe_forward": 18.0, "ev_ebitda": 11.0, "profit_margin": 12.0, "beta": 1.0,
                  "insiders_pct": 3.0, "dividend_yield": 0.0, "ebitda": 9e9, "total_cash": 1e10, "total_debt": 2e10,
                  "sector": "Technology", "profile_as_of": as_of})
    return d


def test_perfiles_sanos_pasan_sin_avisos_de_perfil():
    d, m = healthy()
    _with_profile(d)
    errs, warns = run(d, m, None)
    assert errs == [] and not any("perfil" in w or "fundamentales" in w for w in warns)


@pytest.mark.parametrize("campo,valor", [("pe_forward", 1e9), ("beta", "x"), ("insiders_pct", 250), ("market_cap", -5),
                                         ("total_debt", float("inf")), ("sector", "x" * 200), ("cap_currency", "usd"),
                                         ("profit_margin", True), ("profile_as_of", "ayer")])
def test_perfil_con_campos_inverosimiles_bloquea(campo, valor):
    d, m = healthy()
    _with_profile(d)
    d["stocks"][3][campo] = valor
    errs, _ = run(d, m, None)
    assert any("inverosímiles" in e and ("S3.%s" % campo) in e for e in errs)


def test_perfiles_antiguos_avisan_pero_no_bloquean():
    d, m = healthy()
    _with_profile(d, as_of=(TODAY - dt.timedelta(days=20)).isoformat())
    errs, warns = run(d, m, None)
    assert errs == [] and any("fundamentales de acciones desactualizados" in w and "20 días" in w for w in warns)


def test_caida_de_cobertura_de_perfiles_avisa():
    d, m = healthy()
    prev, _ = healthy()
    _with_profile(prev)
    _with_profile(d, n=10)   # de 31 a 10
    errs, warns = run(d, m, prev)
    assert errs == [] and any("perfiles de acciones: solo 10" in w for w in warns)


def test_filas_con_precio_antiguo_avisan_y_si_son_muchas_bloquean():
    d, m = healthy()
    d["stocks"][0].update({"stale": True, "stale_since": "2026-10-01"})
    errs, warns = run(d, m, None)
    assert errs == [] and any("stocks con precio antiguo" in w and "S0" in w for w in warns)
    for x in d["stocks"][:12]:   # 12 de 31 > 30 %
        x["stale"] = True
    errs, _ = run(d, m, None)
    assert any("stocks: 12 de 31 filas con precio antiguo" in e for e in errs)


def test_historico_reutilizado_avisa():
    d, m = healthy()
    d["stocks"][2]["history_stale"] = True
    errs, warns = run(d, m, None)
    assert errs == [] and any("histórico reutilizado" in w and "S2" in w for w in warns)


def _feed_with_profiles(n=10, table=10, **fields):
    d, m = healthy()
    for x in d["stocks"][:n]:
        x.update({"market_cap": 1e11, "enterprise_value": 1.1e11, "pe_forward": 18.0, "profile_as_of": TODAY.isoformat()})
        x.update(fields)
    d["health"] = {"stale_symbols": [], "history_reused": [],
                   "profiles": {"stocks": 31, "with_data": n, "valuation_table": table, "oldest_days": 0, "older_than_7d": 0}}
    return d, m


def test_aviso_si_la_tabla_de_valoracion_cubre_pocas_acciones():
    d, m = _feed_with_profiles(n=10, table=10)
    errs, warns = run(d, m, None)
    assert errs == [] and not any("tabla de valoración" in w for w in warns)
    d, m = _feed_with_profiles(n=10, table=2)
    errs, warns = run(d, m, None)
    assert errs == [] and any("tabla de valoración" in w and "2 de 10" in w for w in warns)
    # sin el campo (feed antiguo) o con basura no se avisa ni se rompe
    d, m = _feed_with_profiles(n=10, table=2)
    del d["health"]["profiles"]["valuation_table"]
    assert not any("tabla de valoración" in w for w in run(d, m, None)[1])
    d["health"]["profiles"]["valuation_table"] = "x"
    assert run(d, m, None)[0] == []
    d["health"] = "raro"
    assert run(d, m, None)[0] == []


def test_aviso_de_ev_incoherente_con_la_capitalizacion():
    d, m = _feed_with_profiles(n=3, table=3)
    d["stocks"][0].update({"market_cap": 6.7e11, "enterprise_value": 3.8e13})
    errs, warns = run(d, m, None)
    assert errs == [] and any("EV incoherente" in w and "S0" in w for w in warns)
    d["stocks"][0].update({"market_cap": "x", "enterprise_value": True})
    errs, _ = run(d, m, None)                       # tipos erróneos: los frena la validación de perfiles, sin romper la nueva comprobación
    assert all("EV incoherente" not in e for e in errs)


# ---------------------------------------------------------------- avisos que llegan al dueño (Alert -> Healthchecks)
def _alerts(warns):
    return [w for w in warns if isinstance(w, sc.Alert)]


def test_un_tropiezo_puntual_de_yahoo_no_es_alerta():
    d, m = healthy()
    d["stocks"][0].update({"stale": True, "stale_since": TODAY.isoformat()})   # solo hoy, 1 de 31
    errs, warns = run(d, m, None)
    assert errs == [] and any("precio antiguo" in w for w in warns) and _alerts(warns) == []


def test_precio_antiguo_persistente_o_masivo_es_alerta():
    d, m = healthy()
    d["stocks"][0].update({"stale": True, "stale_since": (TODAY - dt.timedelta(days=3)).isoformat()})
    errs, warns = run(d, m, None)
    assert errs == [] and len(_alerts(warns)) == 1 and "S0" in _alerts(warns)[0]
    d, m = healthy()
    for x in d["stocks"][:7]:                       # 7 de 31 = 22 %: bajo el umbral que bloquea (30 %), pero ya es alerta
        x.update({"stale": True, "stale_since": TODAY.isoformat()})
    errs, warns = run(d, m, None)
    assert errs == [] and len(_alerts(warns)) == 1


def test_yahoo_info_y_tabla_degradados_son_alerta():
    d, m = _feed_with_profiles(n=10, table=2)
    errs, warns = run(d, m, None)
    assert errs == [] and any("tabla de valoración" in w for w in _alerts(warns))
    d, m = _feed_with_profiles(n=3, table=3)
    d["stocks"][0].update({"market_cap": 6.7e11, "enterprise_value": 3.8e13})
    assert any("EV incoherente" in w for w in _alerts(run(d, m, None)[1]))
    d, m = _feed_with_profiles(n=10, table=10)
    for x in d["stocks"][:10]:
        x["profile_as_of"] = (TODAY - dt.timedelta(days=30)).isoformat()
    assert any("desactualizados" in w for w in _alerts(run(d, m, None)[1]))


def test_feed_sano_no_genera_alertas():
    d, m = healthy()
    assert _alerts(run(d, m, None)[1]) == []


def test_main_escribe_alerts_out_en_una_linea(tmp_path, capsys, monkeypatch):
    monkeypatch.setattr(sc, "MIN_DATA_BYTES", 0)
    d, m = _feed_with_profiles(n=10, table=2)
    d["stocks"][0].update({"stale": True, "stale_since": (TODAY - dt.timedelta(days=5)).isoformat()})
    out = tmp_path / "alerts.txt"
    write = lambda n, o: (tmp_path / n).write_text(json.dumps(o))
    write("d.json", d); write("m.json", m)
    m["data_bytes"] = (tmp_path / "d.json").stat().st_size
    write("m.json", m)
    rc = sc.main(["--data", str(tmp_path / "d.json"), "--meta", str(tmp_path / "m.json"), "--alerts-out", str(out)])
    txt = out.read_text()
    assert "\n" not in txt and len(txt) <= 900
    assert rc == 0, capsys.readouterr().out
    assert "tabla de valoración" in txt and "precio antiguo" in txt


def test_fx_usd_forma_capitalizacion_sin_usd_y_tipos_reutilizados():
    d, m = _feed_with_profiles(n=3, table=3)
    d["fx_usd"] = {"USD": 1.0, "DKK": 0.15}
    for x in d["stocks"][:3]:
        x.update({"cap_currency": "USD", "market_cap_usd": 1e11})
    assert run(d, m, None)[0] == [] and _alerts(run(d, m, None)[1]) == []
    del d["stocks"][0]["market_cap_usd"]                            # sin market_cap_usd: alerta
    errs, warns = run(d, m, None)
    assert errs == [] and any("sin capitalización en USD" in w and "S0" in w for w in _alerts(warns))
    d["stocks"][0]["market_cap_usd"] = 1.5e11
    d["health"]["fx_stale"] = ["DKK"]                               # tipo reutilizado: solo aviso
    errs, warns = run(d, m, None)
    assert errs == [] and any("reutilizados" in w for w in warns) and _alerts(warns) == []
    for bad in ({"DKK": -1}, {"DKK": "x"}, {"DKKK": 1}, [1], {"DKK": float("nan")}):
        d["fx_usd"] = bad
        assert any("fx_usd" in e for e in run(d, m, None)[0]), bad
    d["fx_usd"] = {"DKK": 0.15}
    d["stocks"][0]["market_cap_usd"] = -3
    assert any("perfil de acción" in e for e in run(d, m, None)[0])


# =============================================================== BE-05: series y continuidad (auditoría 08)
def _scale(x, k):
    x["price"] *= k
    for b in x["history"]:
        b["close"] *= k


def test_BE05_salto_de_unidad_x100_con_anterior_debe_bloquear():          # R2 del apéndice A
    d, m = healthy(); prev, _ = healthy()
    _scale(d["funds"][0], 100)
    errs, _ = run(d, m, prev)
    assert any("salto" in e and d["funds"][0]["symbol"] in e for e in errs)


@pytest.mark.parametrize("k", [10, 100, 0.1, 0.01])
def test_BE05_salto_x10_x100_en_cualquier_direccion(k):
    d, m = healthy(); prev, _ = healthy()
    _scale(d["stocks"][3], k)
    assert any("salto" in e for e in run(d, m, prev)[0])


def test_BE05_salto_solo_en_el_historico_tambien_se_detecta():
    d, m = healthy(); prev, _ = healthy()
    for b in d["indices"][0]["history"]:
        b["close"] *= 100                                      # el precio de la fila no cambia
    assert any("salto" in e for e in run(d, m, prev)[0])


def test_BE05_movimientos_normales_no_bloquean():
    d, m = healthy(); prev, _ = healthy()
    _scale(d["stocks"][0], 1.4)                                # +40 %: raro, pero no es un cambio de unidad
    _scale(d["stocks"][1], 0.7)
    assert run(d, m, prev)[0] == []


def test_BE05_el_salto_es_regresion_y_se_puede_permitir():
    d, m = healthy(); prev, _ = healthy()
    _scale(d["funds"][0], 100)
    errs, warns = run(d, m, prev, allow_regression=True)       # split real: workflow_dispatch con allow_regression
    assert errs == [] and any("salto" in w for w in warns)


def test_BE05_sin_anterior_no_hay_regla_de_salto():
    d, m = healthy()
    _scale(d["funds"][0], 100)
    assert run(d, m, None)[0] == []


@pytest.mark.parametrize("bad", [0, None, -5, "12.3", True, float("inf")])
def test_BE05_historico_con_close_no_positivo_o_nulo_debe_bloquear(bad):   # R3
    d, m = healthy(); h = d["stocks"][0]["history"]
    h[10]["close"] = bad
    errs, _ = run(d, m, None)
    assert any("close" in e and d["stocks"][0]["symbol"] in e for e in errs)


def test_BE05_materias_primas_pueden_cotizar_negativo_pero_no_nulo():
    # WTI cerró en -37,63 el 2020-04-20: bloquear un cierre negativo de futuros pararía la publicación para siempre.
    d, m = healthy()
    d["commodities"][0]["history"][5]["close"] = -37.63
    assert run(d, m, None)[0] == []
    d["commodities"][0]["history"][6]["close"] = None
    assert any("close" in e for e in run(d, m, None)[0])


@pytest.mark.parametrize("fecha", ["no-es-fecha", "", None, "1850-01-01", 20261001])
def test_BE05_fecha_de_barra_ilegible_o_absurda_bloquea(fecha):
    d, m = healthy()
    d["stocks"][0]["history"][20]["date"] = fecha
    assert any("fecha" in e for e in run(d, m, None)[0])


def test_BE05_barra_con_fecha_futura_no_enmascara_historico_congelado():   # R5
    d, m = healthy()
    for k in sc.HISTORY_LISTS:
        for x in d[k]:
            x["history"][-1]["date"] = (TODAY + dt.timedelta(days=400)).isoformat()
    errs, _ = run(d, m, None)
    assert any("futura" in e for e in errs)


def test_BE05_manana_si_es_razonable_por_la_zona_horaria():
    d, m = healthy()
    d["stocks"][0]["history"][-1]["date"] = (TODAY + dt.timedelta(days=1)).isoformat()
    assert run(d, m, None)[0] == []


def test_BE05_fechas_repetidas_o_desordenadas_toleran_dos_simbolos():
    d, m = healthy()
    h = d["stocks"][0]["history"]; h[50]["date"] = h[49]["date"]     # duplicado
    h = d["stocks"][1]["history"]; h[60], h[61] = h[61], h[60]       # orden inverso local
    errs, warns = run(d, m, None)
    assert errs == [] and any("orden" in w for w in warns)
    for i in (2, 3):
        h = d["stocks"][i]["history"]; h[50]["date"] = h[49]["date"]
    assert any("orden" in e for e in run(d, m, None)[0])


def test_BE05_nav_x10_respecto_al_anterior_debe_bloquear_o_alertar():      # R4
    d, m = healthy(); prev, _ = healthy()
    d["your_funds"][0]["price"] *= 10
    errs, warns = run(d, m, prev)
    assert any("NAV" in e and "salto" in e for e in errs)


def test_BE05_nav_normal_o_sin_anterior_no_molesta():
    d, m = healthy(); prev, _ = healthy()
    d["your_funds"][0]["price"] *= 1.02
    prev["your_funds"][1]["price"] = None
    assert run(d, m, prev)[0] == []
