"""Feed v2, paso 4: validador del sitio (BE-05/06/07). Esquema estricto, series, continuidad con el sitio anterior y los casos R2-R6 del
apéndice A de auditorias/08_backend.md (R4, el NAV, se prueba en test_personal_v2.py: el feed público ya no lleva NAV)."""
import hashlib
import json
import os
from datetime import date, timedelta

import pytest

import feed_v2_common as c
import sanity_check_v2 as sc
from v2_world import NOW, REG, gen, mk

TODAY = date(2026, 10, 26)


@pytest.fixture(scope="module")
def _sites(tmp_path_factory):
    """Dos runs sanos y consecutivos, generados UNA vez por módulo (cada test trabaja sobre una copia)."""
    import shutil  # noqa: F401
    from v2_world import install_world
    mp = pytest.MonkeyPatch()
    try:
        w = install_world(mp)
        base = tmp_path_factory.mktemp("sitios")
        a, _ = gen(base, "prev")
        w.end = date(2026, 10, 27)
        b, _ = gen(base, "new", prev=a, now=NOW + timedelta(hours=6))
    finally:
        mp.undo()
    return str(a / "v2"), str(b / "v2")


@pytest.fixture()
def site(_sites, tmp_path):
    """(carpeta v2 del run anterior, carpeta v2 del run nuevo), copias que el test puede estropear."""
    import shutil
    out = []
    for name, src in zip(("prev", "new"), _sites):
        dst = str(tmp_path / name)
        shutil.copytree(src, dst, symlinks=True)
        out.append(dst)
    return tuple(out)


def run(new, prev=None, registry=REG, **kw):
    kw.setdefault("today", date(2026, 10, 27))
    return sc.check_site(new, registry, prev, **kw)


def path(base, rel):
    return os.path.join(base, *rel.split("/"))


def load(base, rel):
    with open(path(base, rel), encoding="utf-8") as f:
        return json.load(f)


def save(base, rel, doc, repack=True):
    p = path(base, rel)
    os.makedirs(os.path.dirname(p), exist_ok=True)
    with open(p, "w", encoding="utf-8") as f:
        f.write(c.dumps(doc))
    if repack:
        pack(base)


def pack(base):
    """Recalcula manifest.json con lo que hay en disco (el atacante que manipula ficheros también puede reescribir el manifiesto)."""
    files, total = {}, 0
    for root, _d, names in os.walk(base):
        for n in names:
            rel = os.path.relpath(os.path.join(root, n), base).replace(os.sep, "/")
            if rel == "manifest.json":
                continue
            data = open(os.path.join(root, n), "rb").read()
            files[rel] = {"bytes": len(data), "sha256": hashlib.sha256(data).hexdigest()}
            total += len(data)
    m = load(base, "manifest.json")
    m["files"], m["total_bytes"] = dict(sorted(files.items())), total
    with open(path(base, "manifest.json"), "w", encoding="utf-8") as f:
        f.write(c.dumps(m))


def edit(base, rel, fn):
    doc = load(base, rel)
    fn(doc)
    save(base, rel, doc)


def has(msgs, *words):
    return any(all(w in m for w in words) for m in msgs)


# ------------------------------------------------------------------ lo sano pasa
def test_dos_runs_sanos_pasan_con_y_sin_anterior(site):
    prev, new = site
    errs, warns = run(new, prev)
    assert errs == [] and not [w for w in warns if isinstance(w, sc.Alert)]
    errs, warns = run(new)
    assert errs == [] and has(warns, "primer run")


def test_el_sitio_anterior_tambien_es_valido(site):
    assert run(site[0], today=date(2026, 10, 26))[0] == []


# ------------------------------------------------------------------ R2: cambio de unidad / rescalado x100 contra el anterior
def _scale_symbol(base, sym, k, closed_years=False):
    def lat(d):
        d["price"] *= k
        d["sparkline"] = [x * k for x in d["sparkline"]]
    edit(base, "s/%s/latest.json" % sym, lat)
    years = [2026] + ([y for y in range(2017, 2026)] if closed_years else [])
    for y in years:
        def day(d):
            for col in "ohlc":
                d[col] = [x * k for x in d[col]]
        edit(base, "s/%s/d/%d.json" % (sym, y), day)


@pytest.mark.parametrize("k", [100, 0.01, 10, 2.5, 0.4])
def test_R2_fondo_x100_en_serie_y_latest_bloquea_por_salto_y_por_continuidad(site, k):
    prev, new = site
    _scale_symbol(new, "SWDA.L", k)
    errs, _ = run(new, prev)
    assert has(errs, "salto de nivel", "SWDA.L") and has(errs, "continuidad", "SWDA.L")


def test_R2_rescalado_tambien_de_los_anos_cerrados_lo_atrapa_igual(site):
    prev, new = site
    _scale_symbol(new, "SWDA.L", 100, closed_years=True)
    errs, _ = run(new, prev)
    assert has(errs, "salto de nivel") and has(errs, "años cerrados reescritos")


def test_R2_solo_cambia_el_historico_del_ano_sin_tocar_latest(site):
    prev, new = site
    def day(d):
        d["c"] = [x * 100 for x in d["c"]]
        d["h"] = [x * 100 for x in d["h"]]
        d["o"] = [x * 100 for x in d["o"]]
        d["l"] = [x * 100 for x in d["l"]]
    edit(new, "s/SWDA.L/d/2026.json", day)
    errs, _ = run(new, prev)
    assert has(errs, "no coincide con la última barra diaria") and has(errs, "continuidad")


def test_R2_deriva_pequena_pasa_y_grande_no(site):
    prev, new = site
    _scale_symbol(new, "AAPL", 1.03)
    assert run(new, prev)[0] == []
    _scale_symbol(new, "AAPL", 1.06 / 1.03)
    errs, _ = run(new, prev)
    assert has(errs, "continuidad", "AAPL") and not has(errs, "salto de nivel")


def test_R2_con_allow_regression_son_avisos_pero_las_absolutas_siguen_bloqueando(site):
    prev, new = site
    _scale_symbol(new, "SWDA.L", 100)
    errs, warns = run(new, prev, allow_regression=True)
    assert errs == [] and has([str(w) for w in warns], "[regresión permitida]", "salto de nivel")
    edit(new, "s/AAPL/latest.json", lambda d: d.update(price=-1))
    assert run(new, prev, allow_regression=True)[0]


def test_el_doble_del_precio_ya_es_salto_y_con_mas_de_10_dias_de_separacion_no_se_aplica(site):
    prev, new = site
    _scale_symbol(new, "AAPL", 2.2)                      # (el límite es 2: «0,5 < nuevo/anterior < 2»)
    assert has(run(new, prev)[0], "salto de nivel", "AAPL")
    edit(prev, "s/AAPL/latest.json", lambda d: d.update(as_of="2026-10-01"))      # el anterior era de hace 26 días: un movimiento grande es plausible
    errs, _ = run(new, prev)
    assert not has(errs, "salto de nivel")


# ------------------------------------------------------------------ R3: series con ceros, nulos, negativos, duplicados, desorden
@pytest.mark.parametrize("col,idx,val,msg", [
    ("c", 10, 0, "no finito o no positivo"), ("c", 11, None, "no finito o no positivo"), ("c", 12, -5, "no finito o no positivo"),
    ("o", 3, 0, "no finito o no positivo"), ("c", 5, float("inf"), None),
])
def test_R3_close_no_valido_bloquea(site, col, idx, val, msg):
    prev, new = site
    p = path(new, "s/AAPL/d/2026.json")
    doc = load(new, "s/AAPL/d/2026.json")
    doc[col][idx] = val
    with open(p, "w", encoding="utf-8") as f:
        f.write(json.dumps(doc))                      # json.dumps deja pasar inf como «Infinity» (JSON inválido): el validador debe rechazarlo igual
    pack(new)
    errs, _ = run(new, prev)
    assert errs and (msg is None or has(errs, msg))


@pytest.mark.parametrize("cambios", [{"c": -37.63}, {"l": -40.32}, {"o": -14.0}, {"c": -37.63, "l": -40.32, "o": 17.65}])
def test_el_validador_sigue_estricto_un_sitio_con_la_barra_del_wti_negativa_se_rechaza(site, cambios):
    """El fix del WTI (2020-04-20/21) está en el GENERADOR, que descarta esas barras: el validador NO se relaja y rechaza cualquier precio <= 0."""
    prev, new = site
    doc = load(new, "s/AAPL/d/2026.json")
    for col, val in cambios.items():
        doc[col][10] = val
    save(new, "s/AAPL/d/2026.json", doc)
    errs, _ = run(new, prev)
    assert has(errs, "s/AAPL/d/2026.json", "precio no finito o no positivo")


def test_R3_fechas_duplicadas_o_en_orden_inverso_bloquean(site):
    prev, new = site
    edit(new, "s/AAPL/d/2026.json", lambda d: d.update(d=d["d"][:5] + [d["d"][4]] + d["d"][6:]))
    assert has(run(new, prev)[0], "repetidas o desordenadas")
    edit(new, "s/AAPL/d/2026.json", lambda d: [d.__setitem__(k, list(reversed(d[k]))) for k in "dohlcv"])
    assert has(run(new, prev)[0], "repetidas o desordenadas")


def test_R3_low_close_high_incoherente_o_columnas_desiguales(site):
    prev, new = site
    edit(new, "s/AAPL/d/2026.json", lambda d: d["h"].__setitem__(7, d["c"][7] * 0.9))
    assert has(run(new, prev)[0], "low <= open/close <= high")
    edit(new, "s/EIMI.L/d/2026.json", lambda d: d["v"].pop())
    assert has(run(new, prev)[0], "longitudes distintas")


def test_R3_volumen_invalido_fecha_ilegible_y_de_otro_ano(site):
    prev, new = site
    edit(new, "s/AAPL/d/2026.json", lambda d: d["v"].__setitem__(2, -1))
    assert has(run(new, prev)[0], "volumen inválido")
    edit(new, "s/AAPL/d/2026.json", lambda d: d["v"].__setitem__(2, 5))
    edit(new, "s/AAPL/d/2026.json", lambda d: d["d"].__setitem__(3, "2026-13-45"))
    assert has(run(new, prev)[0], "fecha ilegible")
    edit(new, "s/EIMI.L/d/2026.json", lambda d: d["d"].__setitem__(0, "2025-12-31"))
    assert has(run(new, prev)[0], "fecha de otro año")


# ------------------------------------------------------------------ R5: fechas futuras
def test_R5_barra_con_fecha_futura_bloquea(site):
    prev, new = site
    edit(new, "s/AAPL/d/2026.json", lambda d: d["d"].__setitem__(-1, "2026-12-31"))
    assert has(run(new, prev)[0], "fecha futura")


def test_R5_ultima_barra_de_todos_a_mas_400_dias_no_enmascara_el_historico_congelado(site):
    prev, new = site
    far = (date(2026, 10, 27) + timedelta(days=400)).isoformat()
    for e in REG:
        if e["kind"] != "yahoo":
            continue
        s = e["symbol"]
        edit(new, "s/%s/latest.json" % s, lambda d: d.update(as_of=far))
    errs, _ = run(new)
    assert errs


def test_R5_control_historico_congelado_hace_40_dias_bloquea(world, tmp_path):
    world.end = date(2026, 10, 10)                       # 16 días: se publica pero con la barra vieja (stale)
    out, _ = gen(tmp_path, "viejo")
    errs, _ = run(str(out / "v2"), today=date(2026, 10, 26))
    assert has(errs, "histórico congelado")


def test_fecha_de_hoy_y_de_manana_pasan(site):
    _, new = site
    edit(new, "s/AAPL/d/2026.json", lambda d: d["d"].__setitem__(-1, "2026-10-28"))
    assert not has(run(new)[0], "fecha futura")


# ------------------------------------------------------------------ R6: claves y textos no permitidos
@pytest.mark.parametrize("rel,mutate,word", [
    ("s/AAPL/latest.json", lambda d: d.update(name="<img src=x onerror=alert(1)>"), "name"),
    ("s/AAPL/latest.json", lambda d: d.update(extra_blob="A" * 1000), "claves no permitidas"),
    ("index.json", lambda d: d.update(extra_blob="A" * 1000), "claves no permitidas"),
    ("index.json", lambda d: d["symbols"][0].update(name="<b>x</b>"), "'<'"),
    ("health.json", lambda d: d.update(note="hola"), "claves no permitidas"),
    ("macro.json", lambda d: d["market_mood"]["vix"].update(rating="<script>"), "claves no permitidas"),
    ("macro.json", lambda d: d.update(evil="x"), "claves no permitidas"),
    ("macro.json", lambda d: d["calendar"].append({"date": "2026-11-01", "event": "<img>"}), "'<'"),
    ("macro.json", lambda d: d["calendar"].append({"date": "2026-11-01", "event": "x", "html": "y"}), "claves no permitidas"),
    ("news.json", lambda d: d["general"].append({"title": "<img src=x onerror=alert(1)>", "link": "https://e.com", "source": "s", "published": "p"}), "'<'"),
    ("news.json", lambda d: d["general"].append({"title": "t", "link": "javascript:alert(1)", "source": "s", "published": "p"}), "http(s)"),
    ("news.json", lambda d: d["general"].append({"title": "t" * 301, "link": "https://e.com", "source": "s", "published": "p"}), "largo"),
    ("news.json", lambda d: d["general"].append({"title": "a\x00b", "link": "https://e.com", "source": "s", "published": "p"}), "control"),
    ("news.json", lambda d: d.update(positions=[]), "claves no permitidas"),
])
def test_R6_claves_o_textos_hostiles_bloquean(site, rel, mutate, word):
    prev, new = site
    if rel == "news.json":
        edit(new, rel, lambda d: d["general"].append({"title": "base", "link": "https://e.com/x", "source": "s", "published": "p"}))
    if rel == "macro.json":
        edit(new, rel, lambda d: d["market_mood"].setdefault("vix", {"value": 15.0}))
    edit(new, rel, mutate)
    errs, _ = run(new, prev)
    assert has(errs, word), errs


def test_R6_cadena_de_markup_con_entidades_se_rechaza_si_lleva_menor_que():
    ctx = sc.Ctx(REG, TODAY)
    assert sc.text(50)("&lt;img&gt;", ctx) is None                 # entidades sin descodificar: ya no hay marcado (el generador des-escapa antes)
    assert sc.text(50)("<img>", ctx) and sc.text(50)("a>b", ctx)


def test_R6_ficheros_y_rutas_fuera_del_contrato(site):
    prev, new = site
    for rel in ("evil.html", "s/AAPL/evil.json", "s/AAPL/d/24.json", "s/.hidden/latest.json", "extra/data.json", "s/AAPL/d/2026.json.bak"):
        p = path(new, rel)
        os.makedirs(os.path.dirname(p), exist_ok=True)
        open(p, "w").write("{}")
        assert has(run(new, prev)[0], "fuera del contrato"), rel
        os.remove(p)


def test_simbolo_fuera_del_registro_o_de_cripto_no_lleva_ficheros(site):
    prev, new = site
    save(new, "s/ZZZ/latest.json", load(new, "s/AAPL/latest.json"))
    assert has(run(new, prev)[0], "fuera del registro")
    os.remove(path(new, "s/ZZZ/latest.json"))
    os.rmdir(path(new, "s/ZZZ"))
    save(new, "s/BTC/latest.json", load(new, "s/AAPL/latest.json"))
    assert has(run(new, prev)[0], "no es de Yahoo")


def test_enlace_simbolico_no_permitido(site, tmp_path):
    prev, new = site
    os.symlink(path(new, "s/AAPL/latest.json"), path(new, "s/AAPL/enlace.json"))
    assert has(run(new, prev)[0], "enlace simbólico")


def test_nan_o_infinity_en_el_json_no_son_json_valido(site):
    prev, new = site
    p = path(new, "s/AAPL/latest.json")
    txt = open(p).read().replace('"volume_ratio":', '"volume_ratio":NaN,"x":', 1)
    open(p, "w").write(txt)
    pack(new)
    assert has(run(new, prev)[0], "ilegible")


def test_json_truncado_o_con_otro_tipo(site):
    prev, new = site
    open(path(new, "macro.json"), "w").write('{"schema":2,')
    pack(new)
    assert has(run(new, prev)[0], "ilegible")
    save(new, "macro.json", [1, 2])
    assert has(run(new, prev)[0], "no es un objeto")


# ------------------------------------------------------------------ divisa / unidad contra el registro (BE-01)
@pytest.mark.parametrize("cur,unit,div", [("GBP", "subunit", 100), ("GBp", "currency", 1), ("GBP", "currency", 1), ("USD", "subunit", 100)])
def test_el_feed_no_puede_cambiar_la_divisa_ni_la_unidad_del_registro(site, cur, unit, div):
    prev, new = site
    edit(new, "s/SWDA.L/latest.json", lambda d: d.update(currency=cur, unit=unit, price_unit_divisor=div))
    errs, _ = run(new, prev)
    assert has(errs, "distinto del registro")


def test_divisor_incoherente_y_series_con_otra_unidad(site):
    prev, new = site
    edit(new, "s/SWDA.L/latest.json", lambda d: d.update(price_unit_divisor=1))
    assert has(run(new, prev)[0], "price_unit_divisor")
    edit(new, "s/SWDA.L/latest.json", lambda d: d.update(price_unit_divisor=100))
    edit(new, "s/SWDA.L/d/2026.json", lambda d: d.update(currency="GBP"))
    assert has(run(new, prev)[0], "currency/unit")
    edit(new, "s/SWDA.L/d/2026.json", lambda d: d.update(currency="GBp"))
    edit(new, "s/SWDA.L/i.json", lambda d: d.update(unit="currency"))
    assert has(run(new, prev)[0], "distintos de los esperados")


def test_index_debe_coincidir_con_el_registro(site):
    prev, new = site
    edit(new, "index.json", lambda d: d["symbols"][2].update(currency="GBP"))
    assert has(run(new, prev)[0], "distinto del registro")
    edit(new, "index.json", lambda d: d["symbols"].pop())
    assert has(run(new, prev)[0], "no coinciden con los del registro")


def test_estado_fijado_en_el_registro_manda_sobre_el_feed(site):
    prev, new = site
    reg = [dict(e, status="dormant") if e["symbol"] == "GC=F" else e for e in REG]
    assert has(run(new, prev, registry=reg)[0], "estado distinto del fijado en el registro")


# ------------------------------------------------------------------ BE-06: sitio anterior
def test_BE06_se_esperaba_anterior_y_no_hay_no_es_primer_run(site):
    _, new = site
    errs, _ = run(new, None, expect_prev=True)
    assert has(errs, "EXPECT_PREV")
    errs, warns = run(new, None, expect_prev=True, allow_regression=True)
    assert errs == [] and any(isinstance(w, sc.Alert) and "EXPECT_PREV" in w for w in warns)


def test_BE06_carpeta_anterior_sin_manifest_tampoco_cuenta(site, tmp_path):
    _, new = site
    empty = tmp_path / "vacio"
    empty.mkdir()
    assert has(run(new, str(empty), expect_prev=True)[0], "EXPECT_PREV")


def test_BE06_primer_run_legitimo_pasa_con_aviso(site):
    _, new = site
    errs, warns = run(new, None, expect_prev=False)
    assert errs == [] and has(warns, "primer run")


# ------------------------------------------------------------------ continuidad: símbolos, años cerrados, tamaño, tiempo
def _drop_symbol(base, sym):
    for root, _d, names in os.walk(path(base, "s/" + sym), topdown=False):
        for n in names:
            os.remove(os.path.join(root, n))
        os.rmdir(root)
    pack(base)


def test_desaparecen_simbolos_activos_bloquea_pero_los_muertos_no_cuentan(site):
    prev, new = site
    for s in ("AAPL", "EIMI.L", "GC=F"):
        _drop_symbol(new, s)
    errs, _ = run(new, prev)
    assert has(errs, "desaparecen 3 símbolos")
    edit(new, "index.json", lambda d: [r.update(status="invalid") for r in d["symbols"] if r["s"] in ("AAPL", "EIMI.L", "GC=F")])
    assert not has(run(new, prev)[0], "desaparecen")


def test_desaparece_uno_o_dos_solo_avisa(site):
    prev, new = site
    _drop_symbol(new, "GC=F")
    errs, warns = run(new, prev)
    assert not has(errs, "desaparecen") and has(warns, "desaparecen 1")


def test_anos_cerrados_reescritos_o_desaparecidos(site):
    prev, new = site
    edit(new, "s/AAPL/d/2020.json", lambda d: d["c"].__setitem__(0, d["c"][0] * 1.0001) or d["h"].__setitem__(0, d["h"][0] * 1.01))
    assert has(run(new, prev)[0], "años cerrados reescritos")
    save(new, "s/AAPL/d/2020.json", load(prev, "s/AAPL/d/2020.json"))
    assert run(new, prev)[0] == []
    os.remove(path(new, "s/AAPL/d/2021.json"))
    pack(new)
    assert has(run(new, prev)[0], "años cerrados desaparecidos")


def test_en_enero_y_febrero_el_ano_anterior_puede_cambiar(site):
    prev, new = site
    edit(new, "s/AAPL/d/2025.json", lambda d: d["c"].__setitem__(-1, d["c"][-1]) or d["v"].__setitem__(-1, d["v"][-1] + 1))
    assert has(run(new, prev)[0], "años cerrados reescritos")
    assert not has(run(new, prev, today=date(2026, 2, 3))[0], "años cerrados reescritos")


def test_el_sitio_no_puede_encoger(site):
    prev, new = site
    for y in range(2017, 2026):
        for s in ("AAPL", "SWDA.L", "EIMI.L", "^GSPC", "EURUSD=X", "USDJPY=X", "GC=F"):
            os.remove(path(new, "s/%s/d/%d.json" % (s, y)))
    pack(new)
    errs, _ = run(new, prev)
    assert has(errs, "menos del 90 %") and has(errs, "años cerrados desaparecidos")


def test_generated_at_no_retrocede(site):
    prev, new = site
    edit(prev, "manifest.json", lambda d: d.update(generated_at="2026-10-28T00:00:00Z"))
    assert has(run(new, prev)[0], "anterior al del sitio publicado")


# ------------------------------------------------------------------ manifest y tamaños
def test_manifest_debe_coincidir_con_el_disco(site):
    prev, new = site
    open(path(new, "s/AAPL/latest.json"), "a").write(" ")                       # cambia el fichero sin tocar el manifiesto
    assert has(run(new, prev)[0], "manifest.json", "no coincide")
    pack(new)
    m = load(new, "manifest.json")
    m["files"].pop("s/AAPL/latest.json")
    save(new, "manifest.json", m, repack=False)
    assert has(run(new, prev)[0], "no coincide con los ficheros")
    m = load(new, "manifest.json")
    m["files"]["s/AAPL/latest.json"] = {"bytes": 1, "sha256": "0" * 64}
    m["total_bytes"] += 1
    save(new, "manifest.json", m, repack=False)
    assert has(run(new, prev)[0], "no coincide")


def test_tamano_maximo_por_fichero_y_del_sitio(site, monkeypatch):
    prev, new = site
    monkeypatch.setitem(c.MAX_BYTES, "latest", 100)
    assert has(run(new, prev)[0], "pesa")
    monkeypatch.undo()
    monkeypatch.setattr(c, "MAX_SITE_BYTES", 1000)
    assert has(run(new, prev)[0], "el sitio pesa")


def test_faltan_ficheros_obligatorios_o_sitio_vacio(site, tmp_path):
    prev, new = site
    os.remove(path(new, "health.json"))
    assert has(run(new, prev)[0], "falta health.json")
    assert has(run(str(tmp_path / "no_existe"))[0], "no existe")
    (tmp_path / "vacio").mkdir()
    assert has(run(str(tmp_path / "vacio"))[0], "vacío")


# ------------------------------------------------------------------ reglas absolutas de contenido
def test_pocos_simbolos_con_datos_bloquea(world, tmp_path, monkeypatch):
    from feed_v2 import BACKFILL_MAX_PER_RUN  # noqa: F401
    monkeypatch.setattr("feed_v2.BACKFILL_MAX_PER_RUN", 1)
    out, _ = gen(tmp_path, "poco")
    errs, _ = run(str(out / "v2"), today=date(2026, 10, 26))
    assert has(errs, "símbolos activos con datos publicados")


def test_variaciones_diarias_absurdas_bloquean_solo_si_son_muchas(site):
    prev, new = site
    for s in ("AAPL", "EIMI.L"):
        edit(new, "s/%s/latest.json" % s, lambda d: d.update(change_pct=75.0))
    assert not has(run(new)[0], "variación diaria")
    edit(new, "s/GC=F/latest.json", lambda d: d.update(change_pct=-80.0))
    assert has(run(new)[0], "variación diaria")


def test_latest_sin_historico_o_distinto_de_la_ultima_barra(site):
    prev, new = site
    for y in range(2017, 2027):
        os.remove(path(new, "s/GC=F/d/%d.json" % y))
    pack(new)
    assert has(run(new, prev)[0], "sin histórico diario")


def test_intradia_solo_en_hot(site):
    prev, new = site
    save(new, "s/EIMI.L/i.json", load(new, "s/AAPL/i.json") | {"s": "EIMI.L"})
    assert has(run(new, prev)[0], "solo los símbolos hot")


@pytest.mark.parametrize("mutate", [
    lambda d: d.update(t=list(reversed(d["t"]))), lambda d: d["c"].__setitem__(0, 0), lambda d: d.update(tz="no es una zona"),
    lambda d: d.update(t=d["t"][:-1]), lambda d: d["t"].__setitem__(-1, "2027-01-01 10:00"),
])
def test_intradia_invalido(site, mutate):
    prev, new = site
    edit(new, "s/AAPL/i.json", mutate)
    assert run(new, prev)[0]


def test_crypto_esquema_y_cuenta(site):
    prev, new = site
    coins = [{"id": "c%d" % i, "symbol": "C%d" % i, "name": "Coin", "rank": i + 1, "price": 1.5, "change_24h_pct": 1.0, "change_7d_pct": None,
              "market_cap": 1e9, "volume_24h": 1e6, "as_of": "2026-10-27"} for i in range(25)]
    doc = {"schema": 2, "generated_at": "2026-10-27T10:00:00Z", "currency": "USD", "unit": "currency", "count": 25, "coins": coins}
    save(new, "crypto/latest.json", doc)
    assert run(new, prev)[0] == []
    save(new, "crypto/latest.json", doc | {"count": 24})
    assert run(new, prev)[0] == []                    # count es informativo: solo se acota el rango
    for bad in ({"id": "Bad Id"}, {"price": 0}, {"symbol": "a/b"}, {"name": "<x>"}, {"extra": 1}, {"change_24h_pct": 5000}, {"currency": "EUR"}):
        d = json.loads(json.dumps(doc))
        if "currency" in bad:
            d.update(bad)
        else:
            d["coins"][3].update(bad)
        save(new, "crypto/latest.json", d)
        assert run(new, prev)[0], bad


# ------------------------------------------------------------------ avisos que llegan a Healthchecks (Alert)
def test_alertas_por_datos_degradados(site):
    """Solo la caída global de Yahoo (429 sostenido o caída de la mayoría) es alerta de Healthchecks; el resto es ::warning:: en el log."""
    prev, new = site
    def health(d):
        d["counts"]["unit_mismatch"] = 2
        d["problems"]["unit_mismatch"] = {"SWDA.L": {"registro": "GBp/subunit", "yahoo": "GBP"}, "EIMI.L": {"registro": "USD/currency", "yahoo": None}}
        d["run"]["yahoo_abandoned"] = True
        d["run"]["budget_exhausted"] = True
        d["counts"]["outage"] = 1
        d["problems"]["rebased"] = ["AAPL"]
    edit(new, "health.json", health)
    _, warns = run(new, prev)
    alerts = " | ".join(str(w) for w in warns if isinstance(w, sc.Alert))
    for frag in ("429", "caída de Yahoo"):
        assert frag in alerts
    for frag in ("divisa/unidad distinta", "presupuesto de tiempo", "rebase"):
        assert not any(frag in str(w) and isinstance(w, sc.Alert) for w in warns), frag
        assert any(frag in str(w) for w in warns), frag
    assert any("SWDA.L (registro GBp/subunit, Yahoo GBP)" in str(w) and "EIMI.L (registro USD/currency, Yahoo sin divisa)" in str(w) for w in warns)


def test_muchos_stale_ya_no_es_alerta_solo_aviso(site):
    prev, new = site
    for s in ("AAPL", "EIMI.L", "GC=F", "^GSPC"):
        edit(new, "s/%s/latest.json" % s, lambda d: d.update(stale=True, stale_since="2026-10-20"))
    _, warns = run(new, prev)
    assert not any(isinstance(w, sc.Alert) for w in warns) and has([str(w) for w in warns], "stale")


def test_stale_sin_stale_since_es_error(site):
    prev, new = site
    edit(new, "s/AAPL/latest.json", lambda d: d.update(stale=True))
    assert has(run(new, prev)[0], "stale y stale_since")


def test_sin_crypto_y_crypto_stale_son_aviso_no_alerta(site):
    prev, new = site
    edit(new, "crypto/latest.json", lambda d: d.update(stale=True, stale_since="2026-10-25"))
    warns = run(new, prev)[1]
    assert has([str(w) for w in warns], "reutilizado") and not any(isinstance(w, sc.Alert) for w in warns)
    os.remove(path(new, "crypto/latest.json"))
    pack(new)
    warns = run(new, prev)[1]
    assert has([str(w) for w in warns], "sin crypto/latest.json") and not any(isinstance(w, sc.Alert) for w in warns)


# ------------------------------------------------------------------ qué avisa a Healthchecks (alertas): solo >10 % sin publicar o caída global de Yahoo
def _reg(n_yahoo, n_crypto=0):
    return [mk("S%d" % i) for i in range(n_yahoo)] + [mk("C%d" % i, kind="crypto") for i in range(n_crypto)]


def _health(failed=None, unit=None, not_listed=(), **flags):
    d = {"run": {"yahoo_abandoned": False, "budget_exhausted": False}, "counts": {"failed": len(failed or {}), "outage": 0},
         "problems": {"failed": failed or {}, "unit_mismatch": unit or {}, "suspect": [], "rebased": []},
         "symbols": {s: {"status": "dormant", "reason": "not_listed", "fail_streak": 0, "tier": "warm"} for s in not_listed}}
    for k, v in flags.items():
        d["run"][k] = v
    return d


def _alerts(registry, health, published=None):
    ctx = sc.Ctx(registry, date(2026, 10, 27))
    latest = {e["symbol"]: {} for e in registry if e["kind"] == "yahoo"} if published is None else {s: {} for s in published}
    sc._health_alerts(health, ctx, latest)
    return [str(w) for w in ctx.warnings if isinstance(w, sc.Alert)], [str(w) for w in ctx.warnings if not isinstance(w, sc.Alert)]


@pytest.mark.parametrize("failed", [
    {"S3": "unit"}, {"S3": "suspect"}, {"S3": "http_error"}, {"S3": "empty"}, {"S3": "timeout"}, {"S3": "parse"},      # un símbolo suelto, sea cual sea el motivo
    {"S1": "unit", "S2": "suspect", "S3": "empty"},                                                                      # varios, pero <= 10 % de 40
    {"S%d" % i: "http_error" for i in range(4)},                                                                         # justo el 10 %: no es «más del 10 %»
])
def test_un_simbolo_suelto_sin_publicar_no_es_alerta_pero_si_aviso(failed):
    reg = _reg(40)
    unit = {s: {"registro": "GBp/subunit", "yahoo": "GBP"} for s, r in failed.items() if r == "unit"}
    alerts, warns = _alerts(reg, _health(failed, unit), published=[e["symbol"] for e in reg if e["symbol"] not in failed])
    assert alerts == []
    assert has(warns, "sin publicar") and any(s in " ".join(warns) for s in failed)


def test_un_cold_que_falla_y_no_tiene_datos_previos_tampoco_es_alerta():
    reg = [mk("S%d" % i) for i in range(39)] + [mk("COLD", tier="cold")]
    alerts, warns = _alerts(reg, _health({"COLD": "http_error"}), published=[e["symbol"] for e in reg if e["symbol"] != "COLD"])
    assert alerts == [] and has(warns, "COLD (http_error)")


def test_mas_del_10_por_ciento_sin_publicar_es_alerta():
    reg = _reg(40)
    failed = {"S%d" % i: "http_error" for i in range(5)}                                   # 5 de 40 = 12,5 %
    alerts, _ = _alerts(reg, _health(failed), published=[e["symbol"] for e in reg if e["symbol"] not in failed])
    assert len(alerts) == 1 and "5 de 40" in alerts[0] and "10 %" in alerts[0]


def test_sin_publicar_cuenta_tambien_los_que_ni_siquiera_tienen_latest():
    reg = _reg(40)
    alerts, _ = _alerts(reg, _health(), published=["S%d" % i for i in range(35)])        # 5 sin latest.json, aunque health no los nombre
    assert len(alerts) == 1 and "5 de 40" in alerts[0]
    alerts, _ = _alerts(reg, _health(), published=["S%d" % i for i in range(36)])        # 4 de 40: no
    assert alerts == []


def test_los_simbolos_pausados_en_el_registro_no_cuentan_como_activos():
    reg = _reg(40) + [mk("D%d" % i, status="dormant") for i in range(20)]
    alerts, _ = _alerts(reg, _health(), published=["S%d" % i for i in range(40)])
    assert alerts == []                                                                    # 20 dormant no publicados: el dueño los pausó


def test_cripto_no_listada_es_aviso_y_solo_alerta_si_pasa_del_10_por_ciento():
    reg = _reg(30, 10)
    alerts, warns = _alerts(reg, _health(not_listed=["C1"]))
    assert alerts == [] and has(warns, "not_listed", "C1")
    alerts, _ = _alerts(reg, _health(not_listed=["C1", "C2", "C3", "C4", "C5"]))        # 5 de 40
    assert len(alerts) == 1


def test_caida_global_de_yahoo_es_alerta_aunque_cada_simbolo_se_trate_aparte():
    reg = _reg(40)
    h = _health()
    h["counts"]["outage"] = 1
    alerts, _ = _alerts(reg, h)
    assert has(alerts, "caída de Yahoo")
    alerts, _ = _alerts(reg, _health(yahoo_abandoned=True))
    assert has(alerts, "429")


def test_presupuesto_agotado_unit_mismatch_y_suspect_son_solo_aviso():
    reg = _reg(40)
    h = _health({"S1": "unit", "S2": "suspect"}, {"S1": {"registro": "GBp/subunit", "yahoo": "GBP"}}, budget_exhausted=True)
    h["problems"]["suspect"] = ["S2"]
    alerts, warns = _alerts(reg, h, published=[e["symbol"] for e in reg if e["symbol"] not in ("S1", "S2")])
    assert alerts == [] and has(warns, "presupuesto") and has(warns, "divisa/unidad", "S1") and has(warns, "cierre <= 0", "S2")


def test_el_conteo_de_fallos_cubre_el_tope_de_40_de_la_lista():
    reg = _reg(300)
    h = _health({"S%d" % i: "http_error" for i in range(40)})                              # health recorta a 40 nombres...
    h["counts"]["failed"] = 45                                                             # ...pero el contador real es 45 (> 10 % de 300 = 30)
    alerts, _ = _alerts(reg, h, published=["S%d" % i for i in range(40, 300)])
    assert len(alerts) == 1 and "45 de 300" in alerts[0]


def test_health_con_tipos_inesperados_no_rompe_las_alertas():
    reg = _reg(10)
    for bad in ({"problems": []}, {"problems": {"failed": ["S1"], "unit_mismatch": ["S1"]}}, {"symbols": []}, {"counts": {"failed": "x"}}):
        sc._health_alerts(bad, sc.Ctx(reg, date(2026, 10, 27)), {})


def test_e2e_un_simbolo_que_falla_no_avisa_y_seis_de_cuarenta_si(world, tmp_path):
    reg = [mk("S%d" % i) for i in range(40)]
    world.decl.update({e["symbol"]: e for e in reg})
    out, _ = gen(tmp_path, "ok", registry=reg)
    for s in ("S0",):
        world.fail.add(s)
    out2, _ = gen(tmp_path, "uno", prev=out, now=NOW + timedelta(hours=6), registry=reg)
    errs, warns = sc.check_site(str(out2 / "v2"), reg, str(out / "v2"), today=date(2026, 10, 27))
    assert errs == [] and not [w for w in warns if isinstance(w, sc.Alert)] and has([str(w) for w in warns], "S0 (http_error)")
    for s in ("S1", "S2", "S3", "S4", "S5"):
        world.fail.add(s)
    out3, _ = gen(tmp_path, "seis", prev=out, now=NOW + timedelta(hours=7), registry=reg)
    errs, warns = sc.check_site(str(out3 / "v2"), reg, str(out / "v2"), today=date(2026, 10, 27))
    assert errs == [] and has([str(w) for w in warns if isinstance(w, sc.Alert)], "6 de 40")


def test_e2e_la_cli_escribe_en_alerts_out_solo_las_alertas(world, tmp_path, capsys):
    reg = [mk("S%d" % i) for i in range(40)]
    world.decl.update({e["symbol"]: e for e in reg})
    out, _ = gen(tmp_path, "ok", registry=reg)
    world.fail.add("S0")
    out2, _ = gen(tmp_path, "uno", prev=out, now=NOW + timedelta(hours=6), registry=reg)
    regfile = tmp_path / "registry.json"
    regfile.write_text(json.dumps({"schema": 1, "symbols": reg}))
    alerts_file = tmp_path / "alerts.txt"
    rc = sc.main(["--site", str(out2 / "v2"), "--registry", str(regfile), "--prev", str(out / "v2"), "--alerts-out", str(alerts_file), "--today", "2026-10-27"])
    assert rc == 0 and alerts_file.read_text() == ""
    assert "::warning::1 símbolos sin publicar" in capsys.readouterr().out


# ------------------------------------------------------------------ CLI
def _write_registry(tmp_path, rows=None):
    p = tmp_path / "registry.json"
    p.write_text(json.dumps({"schema": 1, "symbols": rows or REG}))
    return str(p)


def test_cli_ok_error_y_alertas(site, tmp_path, capsys, monkeypatch):
    prev, new = site
    reg = _write_registry(tmp_path)
    alerts = tmp_path / "alerts.txt"
    assert sc.main(["--today", "2026-10-27", "--site", new, "--registry", reg, "--prev", prev, "--alerts-out", str(alerts)]) == 0
    out = capsys.readouterr().out
    assert "OK v2" in out and alerts.read_text() == ""
    edit(new, "s/AAPL/latest.json", lambda d: d.update(name="<b>x</b>\n::error::inyectado"))
    assert sc.main(["--today", "2026-10-27", "--site", new, "--registry", reg, "--prev", prev]) == 1
    out = capsys.readouterr().out
    assert "FALLIDO" in out and "\n::error::inyectado" not in out


def test_cli_registro_invalido_y_entorno(site, tmp_path, capsys, monkeypatch):
    prev, new = site
    bad = tmp_path / "bad.json"
    bad.write_text("{}")
    assert sc.main(["--site", new, "--registry", str(bad)]) == 1
    assert "registro inválido" in capsys.readouterr().out
    reg = _write_registry(tmp_path)
    monkeypatch.setenv("EXPECT_PREV", "true")
    assert sc.main(["--today", "2026-10-27", "--site", new, "--registry", reg]) == 1                     # BE-06 desde la línea de órdenes
    monkeypatch.setenv("SANITY_ALLOW_REGRESSION", "true")
    assert sc.main(["--today", "2026-10-27", "--site", new, "--registry", reg]) == 0
    assert "[regresión permitida]" in capsys.readouterr().out


def test_cli_alerts_out_no_escribible_no_rompe(site, tmp_path, capsys, monkeypatch):
    prev, new = site
    assert sc.main(["--today", "2026-10-27", "--site", new, "--registry", _write_registry(tmp_path), "--alerts-out", str(tmp_path / "no" / "existe" / "a.txt")]) == 0
    assert "no se pudo escribir" in capsys.readouterr().out


def test_el_registro_real_y_un_sitio_generado_con_el_pasan(world, tmp_path):
    reg = c.load_registry(os.path.join(os.path.dirname(__file__), "..", "registry.json"))
    world.decl = {e["symbol"]: e for e in reg}
    out, _ = gen(tmp_path, "real", registry=reg, force_all=True)          # cupo de 80: parcial, pero por encima del mínimo (25 %)
    errs, warns = run(str(out / "v2"), registry=reg, today=date(2026, 10, 26))
    assert errs == [], errs


def test_registry_con_pocos_simbolos_exige_la_mitad(world, tmp_path):
    reg = [mk("S%d" % i) for i in range(10)]
    world.decl = {e["symbol"]: e for e in reg}
    world.fail.update("S%d" % i for i in range(6))
    out, _ = gen(tmp_path, "pocos", registry=reg)
    errs, _ = run(str(out / "v2"), registry=reg, today=date(2026, 10, 26))
    assert has(errs, "símbolos activos con datos publicados (mínimo 5)")


def test_alta_inicial_parcial_pasa_si_hay_al_menos_20_simbolos(world, tmp_path, monkeypatch):
    """Con un registro grande el alta se reparte en varios runs: el primero no necesita publicarlo todo (solo el mínimo absoluto)."""
    reg = c.load_registry(os.path.join(os.path.dirname(__file__), "..", "registry.json"))
    world.decl = {e["symbol"]: e for e in reg}
    monkeypatch.setattr("feed_v2.BACKFILL_MAX_PER_RUN", 25)
    out, _ = gen(tmp_path, "parcial", registry=reg)
    errs, _ = run(str(out / "v2"), registry=reg, today=date(2026, 10, 26))
    assert errs == []
    monkeypatch.setattr("feed_v2.BACKFILL_MAX_PER_RUN", 10)
    out, _ = gen(tmp_path, "muy_parcial", registry=reg)
    assert has(run(str(out / "v2"), registry=reg, today=date(2026, 10, 26))[0], "(mínimo 20)")


def test_macro_acepta_rating_nulo_y_stale_since_nulo(site):
    prev, new = site
    edit(new, "macro.json", lambda d: d["market_mood"].update(
        stock_fear_greed={"value": 50.0, "rating": None, "stale": True, "stale_since": None}, vix={"value": 15.0, "stale": True, "stale_since": None}))
    assert run(new, prev)[0] == []
