"""R-01 (revisión nov-2026): la lista privada del dueño (proxies de sus fondos y su lista cripto idiosincrática) NO está en el repo, ni en claro ni
como huella sha256 sin sal (las huellas de textos cortos se revierten por diccionario).

La comprobación lee la lista en ejecución de la variable de entorno PRIVATE_DENYLIST: en CI la rellena el secreto del mismo nombre (lo crea el dueño a mano);
en local, una variable de entorno. Formato: entradas separadas por comas, punto y coma o saltos de línea; una o dos palabras por entrada; da igual
mayúsculas o minúsculas.

Sin lista (PR de un fork, Dependabot, local sin la variable) la comprobación NO SE EJECUTA y lo dice: sale como «skipped» con el motivo
«NO EJECUTADO», nunca como aprobada. Los mensajes de fallo dicen fichero y nº de coincidencias, nunca el valor.
"""
import pathlib

import pytest

import privacy_guard as pg

ROOT = pathlib.Path(__file__).resolve().parent.parent
NO_EJECUTADO = ("NO EJECUTADO: falta la lista privada (secreto de CI o variable de entorno local %s); la comprobación de la lista del dueño "
                "NO se ha hecho en este run" % pg.DENYLIST_ENV)


def _barrido(denylist, root=None):
    malos = []
    for p in pg.tree_files(root or ROOT):
        try:
            text = p.read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError):
            continue
        n = pg.denylist_hits(text, denylist)
        if n:
            malos.append("%s: %d coincidencias con la lista privada" % (p.relative_to(root or ROOT).as_posix(), n))
    return malos


def test_la_lista_privada_del_dueno_no_aparece_en_el_arbol():
    denylist = pg.load_denylist()
    if denylist is None:
        pytest.skip(NO_EJECUTADO)
    malos = _barrido(denylist)
    if malos:
        pytest.fail("lista privada del dueño en el árbol (sin valores): " + "; ".join(malos[:20]), pytrace=False)


def test_el_guardia_ya_no_publica_huellas_de_la_lista_del_dueno():
    """Las huellas sin sal eran la fuga: no pueden volver al módulo ni a ningún fichero de tests."""
    nombres = ("PROXY" + "_HASHES", "CRYPTO" + "_HASHES")                        # troceados: este fichero tampoco los lleva enteros
    assert not any(hasattr(pg, n) for n in nombres)
    for p in (ROOT / "tests").glob("*.py"):
        text = p.read_text(encoding="utf-8")
        assert not any(n in text for n in nombres), p.name


# ---- controles negativos (los valores se inventan aquí; no son de nadie) ----
CANARIO = "qz" + "listaprivada" + "canario"                           # troceado: este fichero no lleva el canario entero


def test_control_negativo_sin_variable_o_vacia_no_hay_lista():
    assert pg.load_denylist({}) is None
    assert pg.load_denylist({pg.DENYLIST_ENV: ""}) is None
    assert pg.load_denylist({pg.DENYLIST_ENV: " ,\n ; "}) is None
    assert pg.load_denylist({pg.DENYLIST_ENV: "Alfa, beta\nGamma  Delta;epsilon.x"}) == frozenset({"alfa", "beta", "gamma delta", "epsilon.x"})


def test_control_negativo_sin_lista_sale_no_ejecutado_y_no_aprobado(monkeypatch):
    monkeypatch.delenv(pg.DENYLIST_ENV, raising=False)
    with pytest.raises(pytest.skip.Exception) as e:
        test_la_lista_privada_del_dueno_no_aparece_en_el_arbol()
    assert "NO EJECUTADO" in str(e.value) and pg.DENYLIST_ENV in str(e.value)


def test_control_negativo_detecta_palabra_y_par_sin_importar_mayusculas():
    dl = pg.load_denylist({pg.DENYLIST_ENV: CANARIO + ", fondo " + CANARIO})
    assert pg.denylist_hits("precio de " + CANARIO.upper() + ".", dl) == 1
    assert pg.denylist_hits("el fondo " + CANARIO + " sube", dl) == 2           # la palabra y el par
    assert pg.denylist_hits("nada que ver", dl) == 0
    assert pg.denylist_hits(CANARIO + "x", dl) == 0                             # palabra completa, no subcadena


def test_control_negativo_el_barrido_falla_sin_decir_el_valor(tmp_path, monkeypatch):
    import test_privacidad_lista_privada as este
    (tmp_path / "sub").mkdir()
    (tmp_path / "sub" / "fuga.md").write_text("cartera: " + CANARIO.upper() + "\n", encoding="utf-8")
    (tmp_path / "limpio.md").write_text("nada\n", encoding="utf-8")
    monkeypatch.setattr(este, "ROOT", tmp_path)
    monkeypatch.setenv(pg.DENYLIST_ENV, CANARIO)
    with pytest.raises(pytest.fail.Exception) as e:
        este.test_la_lista_privada_del_dueno_no_aparece_en_el_arbol()
    msg = str(e.value)
    assert "sub/fuga.md: 1 coincidencias" in msg and "limpio.md" not in msg
    assert CANARIO not in msg.lower()


def test_ci_pasa_el_secreto_solo_al_paso_de_tests_y_avisa_si_falta():
    yaml = pytest.importorskip("yaml")
    wf = yaml.safe_load((ROOT / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8"))
    steps = wf["jobs"]["checks"]["steps"]
    con = [s for s in steps if "PRIVATE_DENYLIST" in str(s)]
    assert len(con) == 1 and con[0]["env"] == {"PRIVATE_DENYLIST": "${{ secrets.PRIVATE_DENYLIST }}"}
    run = con[0]["run"]
    assert "python -m pytest" in run and "-rs" in run                          # el motivo de los «skipped» (NO EJECUTADO) sale en el log
    assert "::warning::" in run and "NO SE HA EJECUTADO" in run
    assert "echo \"$PRIVATE_DENYLIST" not in run and "set -x" not in run
