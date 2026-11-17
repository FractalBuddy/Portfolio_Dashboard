"""Sonda de fondos, GENÉRICOS 1/5: lista pública de ISIN (formato, dígito de control ISO 6166, sin duplicados) y su excepción acotada en el guardia de privacidad."""
import json
import pathlib

import pytest

import fund_probe as fp
import test_privacidad_isin as guard
from privacy_guard import isin_check_digit as guard_check_digit

ROOT = pathlib.Path(__file__).resolve().parent.parent
GENERIC = ROOT / "config" / "fund_probe_isins_generic.json"


def _entries():
    return json.loads(GENERIC.read_text(encoding="utf-8"))


def _failures(entries):
    """ISIN que NO pasan formato o dígito de control: se LISTAN (nunca se descartan en silencio)."""
    bad = []
    for e in entries:
        isin = e["isin"]
        if not fp._ISIN_STRICT.match(isin):
            bad.append((isin, "formato"))
        elif fp.isin_check_digit(isin[:11]) != int(isin[11]):
            bad.append((isin, "digito_de_control"))
    return bad


# ------------------------------------------------------------------ el algoritmo del dígito de control
@pytest.mark.parametrize("isin", ["US0378331005", "AU0000XVGZA3", "GB0002634946", "DE000BAY0017"])
def test_digito_de_control_de_ejemplos_publicos_de_la_norma(isin):
    assert fp.isin_check_digit(isin[:11]) == int(isin[11])


@pytest.mark.parametrize("isin", ["US0378331005", "AU0000XVGZA3", "GB0002634946", "DE000BAY0017"])
def test_control_negativo_un_digito_alterado_no_pasa(isin):
    for wrong in range(10):
        if wrong != int(isin[11]):
            assert fp.isin_check_digit(isin[:11]) != wrong


def test_coincide_con_la_implementacion_independiente_del_guardia_en_toda_la_lista():
    for e in _entries():
        assert fp.isin_check_digit(e["isin"][:11]) == guard_check_digit(e["isin"][:11])


def test_un_error_de_transcripcion_se_detecta():
    """Control negativo del propio detector de la lista: cambiar UN carácter de un ISIN válido lo hace fallar (los intercambios de dígitos adyacentes
    iguales no, pero un cambio de carácter sí)."""
    e = _entries()[0]["isin"]
    mutated = e[:4] + ("A" if e[4] != "A" else "B") + e[5:]
    assert _failures([{"isin": mutated}]) == [(mutated, "digito_de_control")]
    assert _failures([{"isin": e[:-1]}]) == [(e[:-1], "formato")]
    assert _failures([{"isin": e.lower()}]) == [(e.lower(), "formato")]


# ------------------------------------------------------------------ el fichero del repo
def test_la_lista_tiene_la_estructura_pedida_y_no_hay_duplicados():
    data = _entries()
    assert isinstance(data, list) and len(data) == 43
    assert all(set(e) == {"isin", "name"} and all(isinstance(v, str) and v.strip() for v in e.values()) for e in data)
    isins = [e["isin"] for e in data]
    assert len(set(isins)) == len(isins)


def test_todos_los_isin_de_la_lista_pasan_formato_y_digito_de_control():
    bad = _failures(_entries())
    assert bad == [], f"ISIN que no pasan (se listan, no se descartan): {bad}"


def test_la_lista_cubre_las_gestoras_pedidas_y_no_contiene_nada_personal():
    names = " ".join(e["name"] for e in _entries())
    for gestora in ("iShares", "Vanguard", "Xtrackers", "Amundi", "Fidelity", "SPDR", "Invesco", "HSBC", "Cobas", "Azvalor", "Horos", "MyInvestor", "Numantia"):
        assert gestora in names, gestora
    blob = GENERIC.read_text(encoding="utf-8")
    assert "@" not in blob and "http" not in blob


# ------------------------------------------------------------------ el cargador de la sonda
def test_load_generic_devuelve_todos_con_su_bandera_de_digito():
    got = fp.load_generic(str(GENERIC))
    assert len(got) == 43 and all(set(g) == {"isin", "name", "check_ok"} and g["check_ok"] is True for g in got)


def test_load_generic_no_descarta_un_digito_malo_lo_marca(tmp_path):
    e = _entries()[0]
    wrong = e["isin"][:11] + str((int(e["isin"][11]) + 1) % 10)
    p = tmp_path / "g.json"
    p.write_text(json.dumps([{"isin": wrong, "name": "x"}, _entries()[1]]), encoding="utf-8")
    got = fp.load_generic(str(p))
    assert [(g["isin"], g["check_ok"]) for g in got] == [(wrong, False), (_entries()[1]["isin"], True)]


_OK = "IE" + "00B4L5Y98" + "3"                      # construido en ejecución: ningún fichero de tests lleva un ISIN irlandés literal (guardia del repo)


@pytest.mark.parametrize("content", ["no json", "[]", "{}", '[{"isin": "X"}]', '[{"isin": "%s", "name": ""}]' % _OK, '[{"isin": 5, "name": "x"}]',
                                     '[{"isin": "%s", "name": "x", "extra": 1}]' % _OK])
def test_load_generic_rechaza_estructuras_invalidas(tmp_path, content):
    p = tmp_path / "g.json"
    p.write_text(content, encoding="utf-8")
    with pytest.raises(ValueError):
        fp.load_generic(str(p))


def test_load_generic_lista_los_de_formato_invalido_y_los_duplicados(tmp_path):
    ok = _entries()[0]
    p = tmp_path / "g.json"
    p.write_text(json.dumps([ok, ok, {"isin": "NOPE", "name": "x"}]), encoding="utf-8")
    with pytest.raises(ValueError) as ei:
        fp.load_generic(str(p))
    msg = str(ei.value)
    assert "NOPE (formato)" in msg and f"{ok['isin']} (duplicado)" in msg


# ------------------------------------------------------------------ excepción ACOTADA del guardia de privacidad
def test_el_guardia_sigue_activo_en_todo_el_arbol_y_la_excepcion_es_solo_para_este_fichero(tmp_path, monkeypatch):
    ajeno = "IE" + "00ZZZZ" + "0009"
    for nombre in ("fund_probe_isins_generic.json", "config/otro.json"):
        d = tmp_path / pathlib.Path(nombre).parent
        d.mkdir(parents=True, exist_ok=True)
    (tmp_path / "config" / "fund_probe_isins_generic.json").write_text(ajeno, encoding="utf-8")        # el fichero permitido, en su sitio
    (tmp_path / "fund_probe_isins_generic.json").write_text(ajeno, encoding="utf-8")                   # mismo nombre, otro sitio: NO permitido
    (tmp_path / "config" / "otro.json").write_text(ajeno, encoding="utf-8")                            # otro fichero de config: NO permitido
    monkeypatch.setattr(guard, "ROOT", tmp_path)
    assert sorted(guard._barrido()) == ["config/otro.json: 1 ISIN y 0 ids de ficha no permitidos", "fund_probe_isins_generic.json: 1 ISIN y 0 ids de ficha no permitidos"]


def test_la_excepcion_no_exime_de_los_ids_de_ficha(tmp_path, monkeypatch):
    (tmp_path / "config").mkdir()
    (tmp_path / "config" / "fund_probe_isins_generic.json").write_text("/products" + "/987654/", encoding="utf-8")
    monkeypatch.setattr(guard, "ROOT", tmp_path)
    assert guard._barrido() == ["config/fund_probe_isins_generic.json: 0 ISIN y 1 ids de ficha no permitidos"]
