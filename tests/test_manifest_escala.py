"""Manifiesto POR TROZOS del feed v2 (formato de manifiesto 2, aditivo) y avisos al 50 % de cada tope. Informe: auditorias/FIXES_MANIFEST_ESCALA.md.

  * `manifest.json` (formato 1, el de siempre) se sigue publicando IGUAL: ruta -> bytes + sha256 de todos los ficheros de datos. Lo que lee hoy el cliente
    del dueño, la puerta de frescura, `site_download.py` y el paso `verify` no cambia.
  * NUEVO: `manifest-root.json` (ficheros globales + una huella por símbolo) y `s/<SYM>/manifest.json` (los ficheros de ese símbolo). Un trozo NO lleva
    `generated_at`: si los ficheros de un símbolo no cambian, su trozo es idéntico byte a byte y su huella en la raíz también; el cliente pide solo lo que cambió.
  * Ningún manifiesto se lista a sí mismo ni a los otros (el formato 1 sigue midiendo lo mismo que antes).
"""
import functools
import hashlib
import json
import os
import shutil
import threading
from datetime import date, timedelta
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer

import pytest

import feed_v2_common as c
import sanity_check_v2 as sc
import site_download as sd
from v2_world import NOW, REG, gen, jl

TODAY = date(2026, 10, 26)
DATA_FILES = lambda site: {rel for rel in walk(site) if c.classify_path(rel)[0] not in c.MANIFEST_KINDS}       # noqa: E731


def walk(site):
    root = os.path.join(site, "v2")
    out = set()
    for r, _d, names in os.walk(root):
        for n in names:
            out.add(os.path.relpath(os.path.join(r, n), root).replace(os.sep, "/"))
    return out


def raw(site, rel):
    with open(os.path.join(site, "v2", *rel.split("/")), "rb") as f:
        return f.read()


def put(site, rel, obj):
    with open(os.path.join(site, "v2", *rel.split("/")), "wb") as f:
        f.write(c.dumps(obj).encode("ascii"))


def entry(site, rel):
    data = raw(site, rel)
    return {"bytes": len(data), "sha256": hashlib.sha256(data).hexdigest()}


def errors(site, prev=None, today=TODAY, registry=REG):
    return sc.check_site(str(site / "v2"), registry, str(prev / "v2") if prev else None, today=today)


@pytest.fixture()
def site(world, tmp_path):
    out, _ = gen(tmp_path, "run1")
    return out


@pytest.fixture()
def two_runs(world, tmp_path):
    """Run 1 (lunes 12:00) y run 2 seis horas después con el sitio anterior: los hot se refrescan, los warm (EIMI.L, USDJPY=X) se copian."""
    prev, _ = gen(tmp_path, "run1")
    new, _ = gen(tmp_path, "run2", prev=prev, now=NOW + timedelta(hours=6))
    return prev, new


# ------------------------------------------------------------------ generador
def test_el_generador_escribe_raiz_y_un_trozo_por_simbolo_con_ficheros(site):
    files = walk(site)
    assert c.MANIFEST_ROOT in files
    yahoo = [e["symbol"] for e in REG if e["kind"] == "yahoo"]
    root = jl(site, c.MANIFEST_ROOT)
    assert root["manifest_format"] == 2 and root["schema"] == c.SCHEMA
    assert sorted(root["symbols"]) == sorted(yahoo)
    assert set(root["files"]) == {rel for rel in DATA_FILES(site) if c.classify_path(rel)[1] is None}
    for sym in yahoo:
        shard = jl(site, c.shard_path(sym))
        assert shard == {"schema": c.SCHEMA, "manifest_format": 2, "s": sym,
                         "files": {rel: entry(site, rel) for rel in sorted(DATA_FILES(site)) if c.classify_path(rel)[1] == sym}}
        assert root["symbols"][sym] == entry(site, c.shard_path(sym))
    assert "generated_at" not in jl(site, c.shard_path("AAPL"))


def test_el_manifest_clasico_no_cambia_lista_solo_los_ficheros_de_datos(site):
    mf = jl(site, "manifest.json")
    assert set(mf) == {"schema", "generated_at", "total_bytes", "files"}
    assert mf["files"] == {rel: entry(site, rel) for rel in sorted(DATA_FILES(site))}
    assert mf["total_bytes"] == sum(v["bytes"] for v in mf["files"].values())
    root = jl(site, c.MANIFEST_ROOT)
    assert root["generated_at"] == mf["generated_at"] and root["total_bytes"] == mf["total_bytes"]


def test_un_trozo_solo_cambia_si_cambian_los_ficheros_de_su_simbolo(two_runs):
    prev, new = two_runs
    r1, r2 = jl(prev, c.MANIFEST_ROOT), jl(new, c.MANIFEST_ROOT)
    for sym in ("EIMI.L", "USDJPY=X"):                        # warm: no tocaba en el run 2 (se copian sus ficheros)
        assert raw(prev, c.shard_path(sym)) == raw(new, c.shard_path(sym))
        assert r1["symbols"][sym] == r2["symbols"][sym]
    for sym in ("AAPL", "^GSPC", "SWDA.L"):                   # hot: latest.json lleva generated_at nuevo
        assert r1["symbols"][sym] != r2["symbols"][sym]
    changed = sorted(s for s in r2["symbols"] if r1["symbols"].get(s) != r2["symbols"][s])
    assert changed == sorted(e["symbol"] for e in REG if e["kind"] == "yahoo" and e["tier"] == "hot")


def test_el_sitio_pasa_el_validador_con_y_sin_sitio_anterior(two_runs):
    prev, new = two_runs
    assert errors(prev)[0] == []
    assert errors(new, prev)[0] == []


# ------------------------------------------------------------------ compatibilidad hacia atrás
def test_cliente_viejo_sigue_funcionando_con_el_manifest_clasico(site):
    """Algoritmo del cliente actual: index.json -> manifest.json entero -> latest.json de cada símbolo activo con as_of, verificado contra el manifest."""
    idx = jl(site, "index.json")
    mf = jl(site, "manifest.json")
    seen = 0
    for row in idx["symbols"]:
        if row["kind"] == "yahoo" and row["status"] == "active" and row["as_of"]:
            rel = c.latest_path(row["s"])
            assert mf["files"][rel] == entry(site, rel)
            seen += 1
    assert seen == sum(e["kind"] == "yahoo" for e in REG)
    assert sc.MANIFEST_SPEC(mf, sc.Ctx(REG, TODAY)) is None                     # el esquema de siempre lo acepta tal cual


def test_cliente_nuevo_pide_solo_los_trozos_que_cambiaron(two_runs):
    prev, new = two_runs
    cache = jl(prev, c.MANIFEST_ROOT)["symbols"]                                # lo que el cliente guardó en el run anterior
    root = json.loads(raw(new, c.MANIFEST_ROOT))
    pedidos = []
    for sym, v in root["symbols"].items():
        if cache.get(sym) != v:
            data = raw(new, c.shard_path(sym))
            assert len(data) == v["bytes"] and hashlib.sha256(data).hexdigest() == v["sha256"]
            pedidos.append(sym)
    assert sorted(pedidos) == sorted(e["symbol"] for e in REG if e["kind"] == "yahoo" and e["tier"] == "hot")


class _Quiet(SimpleHTTPRequestHandler):
    def log_message(self, *a):
        pass


def test_la_descarga_del_ci_y_el_run_siguiente_funcionan_con_el_sitio_nuevo(world, tmp_path, monkeypatch):
    """site_download (modo full, el del job fetch) baja el sitio publicado por el manifest clásico (no ve los trozos: no están listados), el run siguiente
    genera desde esa copia y el validador lo acepta contra el sitio anterior."""
    pub, _ = gen(tmp_path, "pub")
    srv = ThreadingHTTPServer(("127.0.0.1", 0), functools.partial(_Quiet, directory=str(pub / "v2")))
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    monkeypatch.setattr(sd.time, "sleep", lambda s: None)
    try:
        dest = tmp_path / "prev" / "v2"
        state, n, _b = sd.download("http://127.0.0.1:%d" % srv.server_address[1], str(dest), mode="full", today=TODAY)
    finally:
        srv.shutdown()
        srv.server_close()
    assert state == "ok" and n == len(DATA_FILES(pub)) + 1
    assert not any(c.classify_path(r)[0] in ("manifest_root", "manifest_shard") for r in walk(tmp_path / "prev"))
    new, _ = gen(tmp_path, "new", prev=tmp_path / "prev", now=NOW + timedelta(hours=6))
    assert errors(new, tmp_path / "prev")[0] == []


# ------------------------------------------------------------------ validador: controles negativos (cada uno sobre un sitio válido)
def _drop_root(s):
    os.remove(os.path.join(s, "v2", c.MANIFEST_ROOT))


def _shard_bad_sha(s):
    sh = jl(s, c.shard_path("AAPL"))
    rel = c.latest_path("AAPL")
    sh["files"][rel]["sha256"] = "0" * 64
    put(s, c.shard_path("AAPL"), sh)
    root = jl(s, c.MANIFEST_ROOT)                     # la raíz se rehace para que SOLO falle la entrada del trozo
    root["symbols"]["AAPL"] = entry(s, c.shard_path("AAPL"))
    put(s, c.MANIFEST_ROOT, root)


def _root_stale_shard(s):
    sh = jl(s, c.shard_path("AAPL"))
    sh["files"] = dict(reversed(list(sh["files"].items())))            # mismo contenido, otros bytes: la huella de la raíz ya no casa
    put(s, c.shard_path("AAPL"), sh)


def _shard_missing(s):
    os.remove(os.path.join(s, "v2", *c.shard_path("EIMI.L").split("/")))


def _shard_missing_file(s):
    sh = jl(s, c.shard_path("AAPL"))
    sh["files"].pop(c.intraday_path("AAPL"))
    put(s, c.shard_path("AAPL"), sh)
    root = jl(s, c.MANIFEST_ROOT)
    root["symbols"]["AAPL"] = entry(s, c.shard_path("AAPL"))
    put(s, c.MANIFEST_ROOT, root)


def _shard_other_symbol(s):
    sh = jl(s, c.shard_path("AAPL"))
    sh["s"] = "^GSPC"
    put(s, c.shard_path("AAPL"), sh)
    root = jl(s, c.MANIFEST_ROOT)
    root["symbols"]["AAPL"] = entry(s, c.shard_path("AAPL"))
    put(s, c.MANIFEST_ROOT, root)


def _root_missing_global(s):
    root = jl(s, c.MANIFEST_ROOT)
    root["files"].pop("index.json")
    put(s, c.MANIFEST_ROOT, root)


def _root_extra_symbol(s):
    root = jl(s, c.MANIFEST_ROOT)
    root["symbols"]["GC=F-X"] = root["symbols"]["AAPL"]
    put(s, c.MANIFEST_ROOT, root)


def _root_bad_total(s):
    root = jl(s, c.MANIFEST_ROOT)
    root["total_bytes"] += 1
    put(s, c.MANIFEST_ROOT, root)


def _root_other_generated_at(s):
    root = jl(s, c.MANIFEST_ROOT)
    root["generated_at"] = "2026-10-26T11:00:00Z"
    put(s, c.MANIFEST_ROOT, root)


def _root_bad_format(s):
    root = jl(s, c.MANIFEST_ROOT)
    root["manifest_format"] = 1
    put(s, c.MANIFEST_ROOT, root)


def _shard_with_generated_at(s):
    sh = jl(s, c.shard_path("AAPL"))
    sh["generated_at"] = "2026-10-26T12:00:00Z"
    put(s, c.shard_path("AAPL"), sh)
    root = jl(s, c.MANIFEST_ROOT)
    root["symbols"]["AAPL"] = entry(s, c.shard_path("AAPL"))
    put(s, c.MANIFEST_ROOT, root)


def _legacy_lists_shard(s):
    mf = jl(s, "manifest.json")
    mf["files"][c.shard_path("AAPL")] = entry(s, c.shard_path("AAPL"))
    mf["total_bytes"] += mf["files"][c.shard_path("AAPL")]["bytes"]
    put(s, "manifest.json", mf)


def _data_file_changed(s):
    """Un fichero de datos cambia y SOLO se actualiza el manifest clásico: el trozo queda viejo."""
    rel = c.latest_path("AAPL")
    doc = jl(s, rel)
    doc["volume_ratio"] = 9.99
    put(s, rel, doc)
    mf = jl(s, "manifest.json")
    old = mf["files"][rel]["bytes"]
    mf["files"][rel] = entry(s, rel)
    mf["total_bytes"] += mf["files"][rel]["bytes"] - old
    put(s, "manifest.json", mf)
    root = jl(s, c.MANIFEST_ROOT)
    root["total_bytes"] = mf["total_bytes"]
    put(s, c.MANIFEST_ROOT, root)


MUTATIONS = {
    "sin raíz": (_drop_root, "falta manifest-root.json"),
    "trozo con sha256 falso": (_shard_bad_sha, "no coincide"),
    "raíz con huella vieja del trozo": (_root_stale_shard, "no coincide"),
    "falta el trozo de un símbolo": (_shard_missing, "sin trozo"),
    "trozo sin un fichero del símbolo": (_shard_missing_file, "no listan exactamente los ficheros de su símbolo"),
    "trozo de otro símbolo": (_shard_other_symbol, "no es el de su ruta"),
    "raíz sin un fichero global": (_root_missing_global, "ficheros globales"),
    "raíz con un símbolo de más": (_root_extra_symbol, "símbolos"),
    "raíz con total_bytes falso": (_root_bad_total, "total_bytes"),
    "raíz con otro generated_at": (_root_other_generated_at, "generated_at"),
    "raíz con formato 1": (_root_bad_format, "manifest_format"),
    "trozo con generated_at": (_shard_with_generated_at, "claves no permitidas"),
    "manifest clásico que lista un trozo": (_legacy_lists_shard, "no coincide con los ficheros"),
    "fichero de datos cambiado sin rehacer el trozo": (_data_file_changed, "no coincide"),
}


@pytest.mark.parametrize("name", sorted(MUTATIONS))
def test_control_negativo_el_validador_rechaza(site, tmp_path, name):
    assert errors(site)[0] == []                                                # verde antes de estropearlo
    bad = tmp_path / "bad"
    shutil.copytree(site, bad)
    mutate, needle = MUTATIONS[name]
    mutate(bad)
    errs = errors(bad)[0]
    assert errs, name
    assert any(needle in e for e in errs), (name, errs[:5])


def test_control_negativo_trozo_por_encima_de_su_tope(site, monkeypatch):
    monkeypatch.setitem(c.MAX_BYTES, "manifest_shard", 200)
    assert any("s/AAPL/manifest.json pesa" in e for e in errors(site)[0])


def test_control_negativo_raiz_por_encima_de_su_tope(site, monkeypatch):
    monkeypatch.setitem(c.MAX_BYTES, "manifest_root", 500)
    assert any("manifest-root.json pesa" in e for e in errors(site)[0])


def test_trozo_de_un_simbolo_fuera_del_registro_es_error(site):
    os.makedirs(os.path.join(site, "v2", "s", "ZZZ"))
    put(site, c.shard_path("ZZZ"), {"schema": 2, "manifest_format": 2, "s": "ZZZ", "files": {}})
    assert any("símbolo fuera del registro" in e for e in errors(site)[0])


# ------------------------------------------------------------------ avisos al 50 % de cada tope
def _headroom(warnings):
    return [w for w in warnings if "50 %" in w]


def test_sin_aviso_de_tope_cuando_todo_queda_por_debajo_del_50(site):
    errs, warns = errors(site)
    assert errs == [] and _headroom(warns) == []


def test_aviso_al_pasar_del_50_de_un_tope_sin_bloquear(site, monkeypatch):
    size = len(raw(site, "manifest.json"))
    monkeypatch.setitem(c.MAX_BYTES, "manifest", int(size / 0.6))               # el manifest queda al ~60 % de su tope
    errs, warns = errors(site)
    assert errs == []
    hits = _headroom(warns)
    assert len(hits) == 1 and "MAX_BYTES[manifest] 60 %" in hits[0] and "manifest-root.json" in hits[0]
    assert not any(isinstance(w, sc.Alert) for w in hits)                       # aviso en el log, no /fail de Healthchecks


def test_aviso_justo_en_el_50_y_no_por_debajo(site, monkeypatch):
    size = len(raw(site, "index.json"))
    monkeypatch.setitem(c.MAX_BYTES, "index", size * 2)                          # exactamente el 50 %
    assert any("MAX_BYTES[index] 50 %" in w for w in _headroom(errors(site)[1]))
    monkeypatch.setitem(c.MAX_BYTES, "index", size * 2 + 1)                      # un byte por debajo del 50 %
    assert not any("MAX_BYTES[index]" in w for w in _headroom(errors(site)[1]))


def test_aviso_de_registro_ficheros_y_peso_del_sitio(site, monkeypatch):
    files = len(walk(site))
    total = sum(len(raw(site, r)) for r in walk(site))
    monkeypatch.setattr(c, "MAX_REGISTRY_SYMBOLS", 15)                           # 9 de 15 = 60 %
    monkeypatch.setattr(c, "MAX_SITE_FILES", files + files // 2)                 # ~67 %
    monkeypatch.setattr(c, "MAX_SITE_BYTES", total + total // 2)
    hits = " ".join(_headroom(errors(site)[1]))
    for name in ("MAX_REGISTRY_SYMBOLS 60 %", "MAX_SITE_FILES 67 %", "MAX_SITE_BYTES 67 %"):
        assert name in hits, hits


def test_por_encima_del_tope_es_error_no_aviso(site, monkeypatch):
    size = len(raw(site, "manifest.json"))
    monkeypatch.setitem(c.MAX_BYTES, "manifest", size - 1)
    errs, warns = errors(site)
    assert any("manifest.json pesa" in e for e in errs)
    assert not any("MAX_BYTES[manifest]" in w for w in _headroom(warns))


@pytest.mark.parametrize("rel", ["manifest.json", "manifest-root.json", "s/AAPL/manifest.json"])
def test_site_download_rechaza_un_manifest_clasico_que_lista_un_manifiesto(rel):
    ok = {"bytes": 10, "sha256": "a" * 64}
    good = {"schema": 2, "generated_at": "2026-10-26T12:00:00Z", "total_bytes": 10, "files": {"index.json": ok}}
    assert set(sd.parse_manifest(json.dumps(good).encode())) == {"index.json"}                # control: el sano pasa
    bad = dict(good, files={"index.json": ok, rel: ok})
    with pytest.raises(sd.DownloadError):
        sd.parse_manifest(json.dumps(bad).encode())
