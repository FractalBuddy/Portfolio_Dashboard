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
