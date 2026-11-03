"""Guardia de privacidad (bloque H2): el repo es PÚBLICO y no puede reaparecer ningún ISIN ni id de ficha reales.

Falla si en cualquier fichero versionado hay un texto con forma de ISIN irlandés (`IE` + 10 caracteres) que no esté en la lista de
sintéticos de tests/privacy_guard.py, o un `/products/<id>` que no sea uno de los inventados o de los ETF públicos de «Explorar».
Los mensajes del fallo dicen fichero y nº de coincidencias, nunca el valor.
"""
import os
import pathlib

import pytest

from privacy_guard import (ISIN_PERMITIDOS, SINTETICOS_VALIDOS, isin_check_digit, isin_leaks, product_id_leaks)

ROOT = pathlib.Path(__file__).resolve().parent.parent
_SKIP_DIRS = {".git", "__pycache__", ".pytest_cache", ".ruff_cache", "node_modules", ".venv"}


def _ficheros():
    for dirpath, dirnames, filenames in os.walk(ROOT):
        dirnames[:] = [d for d in dirnames if d not in _SKIP_DIRS]
        for fn in filenames:
            p = pathlib.Path(dirpath) / fn
            if p.suffix in (".pyc", ".png", ".ico", ".zip", ".gz"):
                continue
            yield p


def _barrido(isin=isin_leaks, ids=product_id_leaks):
    malos = []
    for p in _ficheros():
        try:
            text = p.read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError):
            continue
        n_isin, n_id = isin(text), ids(text)
        if n_isin or n_id:
            malos.append("%s: %d ISIN y %d ids de ficha no permitidos" % (p.relative_to(ROOT), n_isin, n_id))
    return malos


def test_no_hay_isin_ni_ids_de_ficha_reales_en_el_arbol():
    assert _barrido() == []


# ---- control negativo: el barrido SÍ detecta lo que debe (los valores se fabrican aquí, no existen como instrumento) ----
def test_control_negativo_detecta_un_isin_no_permitido():
    ajeno = "IE" + "00ZZZZ" + "0009"
    assert ajeno not in ISIN_PERMITIDOS
    assert isin_leaks("ficha " + ajeno + " NAV") == 1
    assert isin_leaks("X" + ajeno + "Y") == 1                                   # pegado a otros caracteres también
    assert isin_leaks("minusculas " + ajeno.lower()) == 0                       # solo mayúsculas: así se escribe un ISIN
    assert isin_leaks("IE000SYNA013 IE0000000001 IE000TEST0001") == 0           # los permitidos pasan (incluido con sufijo)


def test_control_negativo_detecta_un_id_de_ficha_no_permitido():
    base = "https://www.ishares.com/ch/individual/en/products" + "/"       # troceado: este fichero tampoco debe llevar un id ajeno
    assert product_id_leaks(base + "987654/x") == 1
    assert product_id_leaks(base + "100001/x") == 0


def test_control_negativo_el_barrido_falla_si_aparece_un_fichero_con_un_isin_ajeno(tmp_path, monkeypatch):
    import test_privacidad_isin as este
    (tmp_path / "fuga.txt").write_text("IE" + "00ZZZZ" + "0009 y /products" + "/987654/", encoding="utf-8")
    (tmp_path / "limpio.txt").write_text("IE000SYNA013 /products" + "/100001/", encoding="utf-8")
    monkeypatch.setattr(este, "ROOT", tmp_path)
    assert este._barrido() == ["fuga.txt: 1 ISIN y 1 ids de ficha no permitidos"]


@pytest.mark.parametrize("isin", sorted(SINTETICOS_VALIDOS))
def test_los_sinteticos_validos_tienen_digito_de_control_correcto(isin):
    assert len(isin) == 12 and isin_check_digit(isin[:11]) == int(isin[11])


def test_el_calculo_del_digito_de_control_es_correcto():
    assert isin_check_digit("US037833100") == 5                                  # Apple (ejemplo público de la norma ISO 6166)
