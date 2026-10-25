"""Feed v2: descarga del sitio publicado anteriormente (BE-06, fail-closed). Servidor HTTP local; ninguna petición real."""
import gzip
import hashlib
import json
import os
import threading
from datetime import date
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

import feed_v2_common as c
import site_download as sd
from v2_world import gen


class Site:
    """Sirve una carpeta en 127.0.0.1 con fallos inyectables: `rules` = {ruta: función(petición_n) -> None | (código, cuerpo)}."""
    def __init__(self, root):
        self.root, self.rules, self.hits, self.lock = root, {}, {}, threading.Lock()
        outer = self

        class H(BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def do_GET(self):
                rel = self.path.split("?", 1)[0].lstrip("/")
                rel = __import__("urllib.parse").parse.unquote(rel)
                with outer.lock:
                    outer.hits[rel] = outer.hits.get(rel, 0) + 1
                    n = outer.hits[rel]
                rule = outer.rules.get(rel)
                forced = rule(n) if rule else None
                if forced:
                    code, body = forced
                    self.send_response(code)
                    self.send_header("Content-Length", str(len(body)))
                    self.end_headers()
                    self.wfile.write(body)
                    return
                p = os.path.join(outer.root, *rel.split("/"))
                if ".." in rel or not os.path.isfile(p):
                    self.send_response(404)
                    self.send_header("Content-Length", "0")
                    self.end_headers()
                    return
                data = open(p, "rb").read()
                if "gzip" in self.headers.get("Accept-Encoding", ""):
                    data = gzip.compress(data)
                    self.send_response(200)
                    self.send_header("Content-Encoding", "gzip")
                else:
                    self.send_response(200)
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

        self.srv = ThreadingHTTPServer(("127.0.0.1", 0), H)
        self.url = "http://127.0.0.1:%d" % self.srv.server_address[1]
        threading.Thread(target=lambda: self.srv.serve_forever(poll_interval=0.01), daemon=True).start()

    def close(self):
        self.srv.shutdown()
        self.srv.server_close()


@pytest.fixture(scope="module")
def _published(tmp_path_factory):
    from v2_world import install_world
    mp = pytest.MonkeyPatch()
    try:
        install_world(mp)
        out, _ = gen(tmp_path_factory.mktemp("pub"), "pub")
    finally:
        mp.undo()
    return out / "v2"


@pytest.fixture()
def served(_published):
    s = Site(str(_published))
    yield s, _published
    s.close()


@pytest.fixture(autouse=True)
def _sin_esperas(monkeypatch):
    monkeypatch.setattr(sd.time, "sleep", lambda s: None)


def dl(s, tmp_path, mode="full", name="dest"):
    return sd.download(s.url, str(tmp_path / name), mode, workers=4, today=date(2026, 10, 26), sleep=lambda x: None)


def listing(d):
    return sorted(os.path.relpath(os.path.join(r, n), d).replace(os.sep, "/") for r, _, ns in os.walk(d) for n in ns)


# ------------------------------------------------------------------ camino feliz
def test_modo_full_baja_todo_y_verifica(served, tmp_path):
    s, pub = served
    state, n, size = dl(s, tmp_path)
    assert state == "ok" and listing(tmp_path / "dest") == listing(pub) and n == len(listing(pub))
    for rel in listing(pub):
        assert (tmp_path / "dest" / rel).read_bytes() == (pub / rel).read_bytes()
    assert size == sum(os.path.getsize(pub / r) for r in listing(pub))


def test_modo_validate_solo_manifest_latest_y_dos_ultimos_anos(served, tmp_path):
    s, pub = served
    assert dl(s, tmp_path, "validate")[0] == "ok"
    got = listing(tmp_path / "dest")
    assert "manifest.json" in got and "s/AAPL/latest.json" in got and "s/AAPL/d/2026.json" in got and "s/AAPL/d/2025.json" in got
    assert "s/AAPL/d/2024.json" not in got and "index.json" not in got and "s/AAPL/i.json" not in got and "macro.json" not in got


def test_simbolos_especiales_se_piden_codificados(served, tmp_path):
    s, _ = served
    dl(s, tmp_path)
    assert s.hits["s/^GSPC/latest.json"] == 1 and s.hits["s/EURUSD=X/latest.json"] == 1


def test_las_peticiones_llevan_gzip_y_salto_de_cache(served, tmp_path):
    s, _ = served
    seen = []
    orig = sd.urllib.request.urlopen
    sd.urllib.request.urlopen = lambda req, timeout=None: (seen.append(req), orig(req, timeout=timeout))[1]
    try:
        dl(s, tmp_path, "validate")
    finally:
        sd.urllib.request.urlopen = orig
    assert all("cb=" in r.full_url and r.get_header("Accept-encoding") == "gzip" for r in seen)


# ------------------------------------------------------------------ BE-06: 404 es primer run; lo demás NO
def test_404_en_el_manifest_es_primer_run_legitimo(tmp_path):
    s = Site(str(tmp_path / "vacio"))
    try:
        assert sd.download(s.url, str(tmp_path / "d"), "full", sleep=lambda x: None)[0] == "first_run"
        assert not (tmp_path / "d").exists()
    finally:
        s.close()


@pytest.mark.parametrize("code", [500, 502, 503, 403, 401, 429])
def test_otros_codigos_en_el_manifest_no_son_primer_run(served, tmp_path, code):
    s, _ = served
    s.rules["manifest.json"] = lambda n: (code, b"")
    with pytest.raises(sd.DownloadError):
        dl(s, tmp_path)
    assert s.hits["manifest.json"] == (sd.TRIES if code in sd.RETRY_CODES else 1)


def test_fallo_transitorio_se_reintenta(served, tmp_path):
    s, _ = served
    s.rules["manifest.json"] = lambda n: (503, b"") if n <= 2 else None
    s.rules["s/AAPL/latest.json"] = lambda n: (500, b"") if n == 1 else None
    assert dl(s, tmp_path)[0] == "ok" and s.hits["manifest.json"] == 3


def test_red_caida_es_error_no_primer_run(tmp_path):
    srv = Site(str(tmp_path))
    url = srv.url
    srv.close()
    with pytest.raises(sd.DownloadError):
        sd.download(url, str(tmp_path / "d"), "full", sleep=lambda x: None)


@pytest.mark.parametrize("body", [b"", b"no es json", b"[]", b'{"schema":2}', b'{"schema":2,"files":{}}', b'{"schema":1,"files":{"a":1}}',
                                  b'{"schema":2,"files":{"../x":{"bytes":1,"sha256":"' + b"0" * 64 + b'"}}}',
                                  b'{"schema":2,"files":{"manifest.json":{"bytes":1,"sha256":"' + b"0" * 64 + b'"}}}',
                                  b'{"schema":2,"files":{"index.json":{"bytes":-1,"sha256":"' + b"0" * 64 + b'"}}}',
                                  b'{"schema":2,"files":{"index.json":{"bytes":1,"sha256":"corto"}}}',
                                  b'{"schema":2,"files":{"index.json":{"bytes":NaN,"sha256":"' + b"0" * 64 + b'"}}}'])
def test_manifest_invalido_es_error(served, tmp_path, body):
    s, _ = served
    s.rules["manifest.json"] = lambda n: (200, body)
    with pytest.raises(sd.DownloadError):
        dl(s, tmp_path)


def test_fichero_listado_que_da_404_es_error(served, tmp_path):
    s, _ = served
    s.rules["s/AAPL/d/2024.json"] = lambda n: (404, b"")
    with pytest.raises(sd.DownloadError) as e:
        dl(s, tmp_path)
    assert "404" in str(e.value) and "inconsistente" in str(e.value)


def test_contenido_distinto_del_manifest_es_error_tras_un_reintento(served, tmp_path):
    s, pub = served
    s.rules["s/AAPL/latest.json"] = lambda n: (200, b'{"price":1}')
    with pytest.raises(sd.DownloadError) as e:
        dl(s, tmp_path)
    assert "no coincide" in str(e.value) and s.hits["s/AAPL/latest.json"] == 2


def test_cdn_con_version_vieja_una_vez_se_recupera(served, tmp_path):
    s, pub = served
    good = (pub / "s/AAPL/latest.json").read_bytes()
    s.rules["s/AAPL/latest.json"] = lambda n: (200, b'{"viejo":1}') if n == 1 else (200, good)
    assert dl(s, tmp_path)[0] == "ok"


def test_mismo_tamano_distinto_contenido_se_detecta_por_sha(served, tmp_path):
    s, pub = served
    good = (pub / "s/AAPL/latest.json").read_bytes()
    s.rules["s/AAPL/latest.json"] = lambda n: (200, good.replace(b"AAPL", b"XXXX", 1))
    with pytest.raises(sd.DownloadError):
        dl(s, tmp_path)


def test_respuesta_demasiado_grande_se_corta(served, tmp_path):
    s, _ = served
    s.rules["s/AAPL/latest.json"] = lambda n: (200, b"x" * (c.MAX_BYTES["latest"] + 10))
    with pytest.raises(sd.DownloadError):
        dl(s, tmp_path)


def test_bomba_gzip_se_corta(served, tmp_path):
    s, _ = served
    class H2(BaseHTTPRequestHandler):
        pass
    bomb = gzip.compress(b"0" * (c.MAX_BYTES["manifest"] * 20))
    srv = ThreadingHTTPServer(("127.0.0.1", 0), type("B", (BaseHTTPRequestHandler,), {
        "log_message": lambda self, *a: None,
        "do_GET": lambda self: (self.send_response(200), self.send_header("Content-Encoding", "gzip"),
                                self.send_header("Content-Length", str(len(bomb))), self.end_headers(), self.wfile.write(bomb))}))
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    try:
        with pytest.raises(sd.DownloadError):
            sd.download("http://127.0.0.1:%d" % srv.server_address[1], str(tmp_path / "d"), "full", sleep=lambda x: None)
    finally:
        srv.shutdown()


# ------------------------------------------------------------------ URL base
@pytest.mark.parametrize("url", ["http://example.com/v2", "ftp://x/v2", "https://", "https://u:p@example.com/v2", "https://example.com/v2?x=1",
                                 "https://example.com/v2#f", "file:///etc", "", "example.com"])
def test_url_base_invalida(url):
    with pytest.raises(sd.DownloadError):
        sd.download(url, "/nonexistent/x", sleep=lambda x: None)


# ------------------------------------------------------------------ CLI y GITHUB_ENV
def test_cli_escribe_expect_prev(served, tmp_path, monkeypatch, capsys):
    s, _ = served
    env = tmp_path / "env"
    monkeypatch.setenv("GITHUB_ENV", str(env))
    assert sd.main(["--base-url", s.url, "--dest", str(tmp_path / "d"), "--mode", "validate", "--github-env"]) == 0
    assert env.read_text() == "EXPECT_PREV=true\n"
    empty = Site(str(tmp_path / "nada"))
    try:
        assert sd.main(["--base-url", empty.url, "--dest", str(tmp_path / "d2"), "--github-env"]) == 0
    finally:
        empty.close()
    assert env.read_text() == "EXPECT_PREV=true\nEXPECT_PREV=false\n"
    assert "primer run" in capsys.readouterr().out


def test_cli_error_sale_con_1_y_no_escribe_expect_prev(served, tmp_path, monkeypatch, capsys):
    s, _ = served
    s.rules["manifest.json"] = lambda n: (500, b"")
    env = tmp_path / "env"
    monkeypatch.setenv("GITHUB_ENV", str(env))
    assert sd.main(["--base-url", s.url, "--dest", str(tmp_path / "d"), "--github-env"]) == 1
    out = capsys.readouterr().out
    assert "::error::" in out and "no se publica a ciegas" in out.lower() and not env.exists()


def test_el_manifest_descargado_alimenta_al_validador(served, tmp_path):
    """Integración: lo que baja el modo validate es suficiente para que sanity_check_v2 compare."""
    import sanity_check_v2 as sc
    s, pub = served
    dl(s, tmp_path, "validate")
    from v2_world import REG
    errs, _ = sc.check_site(str(pub), REG, str(tmp_path / "dest"), today=date(2026, 10, 26), expect_prev=True)
    assert errs == []
    assert hashlib.sha256((tmp_path / "dest" / "manifest.json").read_bytes()).hexdigest() == hashlib.sha256((pub / "manifest.json").read_bytes()).hexdigest()
    json.loads((tmp_path / "dest" / "manifest.json").read_text())
