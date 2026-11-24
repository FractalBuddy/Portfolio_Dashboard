"""Guardia de privacidad (bloque H2): el repo es PÚBLICO y no puede reaparecer ningún ISIN ni id de ficha reales.

Falla si en cualquier fichero del árbol hay un texto con forma de ISIN irlandés (`IE` + 10 caracteres) que no esté en la lista de
sintéticos de tests/privacy_guard.py, un ISIN de CUALQUIER país con dígito de control válido que no sea una excepción (R-06), una forma de secreto
(Supabase, JWT, Healthchecks, token de GitHub) fuera de los tests que las inventan (R-06), o un `/products/<id>` que no sea uno de los inventados o de
los ETF públicos de «Explorar». Los mensajes del fallo dicen fichero y nº de coincidencias (o qué forma), nunca el valor.
"""
import pathlib
import re

import pytest

from privacy_guard import (ISIN_PERMITIDOS, SINTETICOS_VALIDOS, isin_any_leaks, isin_check_digit, isin_leaks, product_id_leaks, secret_shape_leaks,
                           tree_files)

ROOT = pathlib.Path(__file__).resolve().parent.parent
REPO = ROOT                                     # el repo de verdad: la lista genérica de excepciones sale siempre de aquí (ROOT se cambia en los controles negativos)
# ÚNICA excepción: la lista de ISIN PÚBLICOS (fondos conocidos, no de nadie) que usa la sección GENÉRICOS de la sonda de fondos. Se exime solo de la regla de ISIN
# y solo en esa ruta exacta (no por nombre de fichero); los ids de ficha siguen vigilados también ahí. tests/test_fund_probe_generic_list.py lo prueba.
ISIN_EXENTO = "config/fund_probe_isins_generic.json"
# F1 «sombra» NAV Yahoo: en la configuración de la sombra solo puede haber ISIN de esa misma lista genérica pública (el canario). Exención de ISIN SOLO para
# esa ruta y SOLO para los ISIN de la lista genérica: cualquier otro ISIN en ese fichero sigue siendo una fuga.
CANARIO_CFG = "config/yahoo_shadow.json"


def _genericos():
    import json
    return frozenset(e["isin"] for e in json.loads((REPO / ISIN_EXENTO).read_text(encoding="utf-8")))


def _isin_no_permitidos(rel, text, isin=isin_leaks):
    """Regla IE (ventana sin anclar, también sin dígito de control válido) + regla de CUALQUIER país con dígito de control válido (R-06)."""
    if rel == ISIN_EXENTO:
        return 0
    gen = _genericos()
    if rel == CANARIO_CFG:
        return isin(" ".join(w for w in re.findall(r"IE[0-9A-Z]{10}", text) if w not in gen)) + isin_any_leaks(text, gen)
    return isin(text) + isin_any_leaks(text, gen)


def _ficheros():
    return tree_files(ROOT)


def _barrido(isin=isin_leaks, ids=product_id_leaks):
    malos = []
    for p in _ficheros():
        try:
            text = p.read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError):
            continue
        n_isin = _isin_no_permitidos(p.relative_to(ROOT).as_posix(), text, isin)
        n_id = ids(text)
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


# =====================================================================================================================================
# F1 «sombra» NAV Yahoo: el guardia se AMPLÍA a los símbolos Morningstar/Yahoo `0P` + 8 caracteres (un `0P…` identifica un fondo igual que su ISIN).
# Exenciones EXPLÍCITAS (y solo estas):
#   * los 7 `0P…` de la lista FIJA y pública de la sonda de retraso (config/fund_lag_tickers.json; pública por decisión del dueño, ver
#     auditorias/FIXES_LAG_FONDOS.md y D8 de DISENO_NAV_YAHOO). Se congelan por HUELLA (privacy_guard.ZERO_P_FIJOS_SHA256): si alguien añade un `0P…`
#     nuevo a esa lista, el guardia falla y obliga a revisarlo (no basta con meterlo en el fichero);
#   * los `0P…` inventados de los tests (privacy_guard.ZERO_P_SINTETICOS).
#   * El canario de la sombra NO necesita exención de `0P`: se define por ISIN (config/yahoo_shadow.json) y ese ISIN tiene que estar en la lista genérica
#     pública (exención de ISIN SOLO para ese fichero y SOLO para ISIN de esa lista). La lista genérica no contiene `0P…`, así que tampoco se le abre hueco.
# =====================================================================================================================================
def test_no_hay_simbolos_0p_no_permitidos_en_el_arbol():
    from privacy_guard import zero_p_leaks
    malos = []
    for p in _ficheros():
        try:
            text = p.read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError):
            continue
        n = zero_p_leaks(text)
        if n:
            malos.append("%s: %d símbolos 0P no permitidos" % (p.relative_to(ROOT), n))
    assert malos == []


def test_control_negativo_detecta_un_0p_no_permitido():
    from privacy_guard import ZERO_P_SINTETICOS, zero_p_leaks
    ajeno = "0P" + "0000" + "ZZZZ"                                            # troceado: este fichero no debe llevar uno ajeno entero
    assert ajeno not in ZERO_P_SINTETICOS
    assert zero_p_leaks("ticker " + ajeno + ".F") == 1
    assert zero_p_leaks("x" + ajeno + "y") == 1                               # pegado a otros caracteres también
    assert zero_p_leaks(ajeno.lower()) == 0                                   # solo mayúsculas (así los da Yahoo)
    assert zero_p_leaks("0PFAKE0001.F 0PFAKE0002") == 0                       # los inventados pasan


def test_los_0p_de_la_lista_fija_son_exactamente_los_congelados_por_huella():
    import hashlib
    import json
    from privacy_guard import ZERO_P_FIJOS_SHA256, zero_p_leaks
    fijos = [e["ticker"] for e in json.loads((ROOT / "config" / "fund_lag_tickers.json").read_text(encoding="utf-8")) if e["ticker"].startswith("0P")]
    assert len(fijos) == 7 and {hashlib.sha256(t[:10].encode()).hexdigest() for t in fijos} == ZERO_P_FIJOS_SHA256
    assert all(zero_p_leaks(t) == 0 for t in fijos)


def test_control_negativo_el_barrido_0p_falla_si_aparece_un_fichero_con_un_0p_ajeno(tmp_path, monkeypatch):
    import test_privacidad_isin as este
    (tmp_path / "fuga.json").write_text('{"s": "' + "0P" + "0000ZZZZ" + '.F"}', encoding="utf-8")
    monkeypatch.setattr(este, "ROOT", tmp_path)
    with pytest.raises(AssertionError):
        test_no_hay_simbolos_0p_no_permitidos_en_el_arbol()


def test_el_canario_de_la_sombra_es_un_isin_de_la_lista_generica_publica():
    import json
    cfg = json.loads((ROOT / CANARIO_CFG).read_text(encoding="utf-8"))
    genericos = {e["isin"] for e in json.loads((ROOT / ISIN_EXENTO).read_text(encoding="utf-8"))}
    assert cfg["canary_isin"] in genericos
    assert _isin_no_permitidos(CANARIO_CFG, "x " + cfg["canary_isin"]) == 0
    assert _isin_no_permitidos(CANARIO_CFG, "IE" + "00ZZZZ" + "0009") == 1          # control negativo: en ese fichero solo pasan ISIN de la lista genérica


# =====================================================================================================================================
# R-06 (revisión nov-2026): ISIN de CUALQUIER país (no solo IE) con dígito de control válido, y formas de secreto en TODO el árbol (no solo en
# .github/workflows). Excepciones de ISIN: la lista genérica pública, los sintéticos de los tests, el país ficticio «ZZ» y los 4 ejemplos públicos de la
# norma ISO 6166 (empresas cotizadas). Excepciones de formas de secreto: SOLO los tests que las inventan, por ruta exacta.
# =====================================================================================================================================
SECRETOS_INVENTADOS = frozenset({"tests/test_personal_nav_only.py", "tests/test_personal_v2.py", "tests/test_workflows.py", "tests/test_workflows_F1.py"})


def _barrido_secretos():
    malos = []
    for p in _ficheros():
        rel = p.relative_to(ROOT).as_posix()
        if rel in SECRETOS_INVENTADOS:
            continue
        try:
            text = p.read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError):
            continue
        found = secret_shape_leaks(text)
        if found:
            malos.append("%s: %s" % (rel, ", ".join(found)))                 # qué forma, nunca el valor
    return malos


def test_no_hay_formas_de_secreto_en_el_arbol():
    assert _barrido_secretos() == []


def _valido(base11):
    return base11 + str(isin_check_digit(base11))


def test_control_negativo_detecta_un_isin_valido_de_otro_pais():
    for base in ("LU" + "00ZZZZ001", "DE" + "000ZZZZ00", "FR" + "00ZZZZ002", "US" + "00ZZZZ003"):
        ajeno = _valido(base)
        assert isin_any_leaks("fondo " + ajeno + " NAV") == 1, ajeno
        assert isin_any_leaks('{"isin": "' + ajeno + '"}') == 1
        malo = ajeno[:11] + str((int(ajeno[11]) + 1) % 10)
        assert isin_any_leaks("fondo " + malo) == 0                              # sin dígito de control válido: no es un ISIN (evita falsos positivos)
        assert isin_any_leaks(ajeno.lower()) == 0
    assert isin_any_leaks("ZZ" + "00SYNTH007") == 0                              # país ficticio de los tests
    assert isin_any_leaks("US0378331005 IE000SYNA013") == 0                      # ejemplo de la norma y sintético
    gen = sorted(_genericos())[0]
    assert isin_any_leaks(gen) == 1 and isin_any_leaks(gen, _genericos()) == 0   # la lista genérica solo pasa si se da como excepción


def test_control_negativo_el_barrido_falla_con_un_isin_valido_de_otro_pais(tmp_path, monkeypatch):
    import test_privacidad_isin as este
    (tmp_path / "doc.md").write_text("mi fondo: " + _valido("LU" + "00ZZZZ001") + "\n", encoding="utf-8")
    monkeypatch.setattr(este, "ROOT", tmp_path)
    assert este._barrido() == ["doc.md: 1 ISIN y 0 ids de ficha no permitidos"]


@pytest.mark.parametrize("muestra,forma", [
    ("https://" + "abcdefghijklmnopqrst" + ".supabase.co/rest/v1", "URL de proyecto de Supabase"),
    ("https://hc-ping.com/" + "0123abcd-45ef-6789-abcd-0123456789ab", "URL de Healthchecks con clave"),
    ("https://hc-ping.com/" + "abcdefghijklmnopqrstuv/check", "URL de Healthchecks con clave"),
    ("sb_" + "secret_" + "ABCDEFGHIJKL", "clave de Supabase"),
    ("eyJ" + "hbGciOiJIUzI1" + "." + "eyJyb2xlIjoiYW5vbiJ9", "JWT"),
    ("ghp_" + "a" * 30, "token de GitHub"),
], ids=["supabase", "hc-uuid", "hc-slug", "sb-key", "jwt", "gh-token"])                  # ids propios: la salida de pytest no repite las muestras
def test_control_negativo_el_barrido_de_secretos_encuentra_cada_forma_en_cualquier_fichero(tmp_path, monkeypatch, muestra, forma):
    import test_privacidad_isin as este
    (tmp_path / "docs").mkdir()
    (tmp_path / "docs" / "nota.md").write_text("valor: " + muestra + "\n", encoding="utf-8")
    (tmp_path / "tests").mkdir()
    (tmp_path / "tests" / "test_workflows.py").write_text(muestra, encoding="utf-8")       # exento por ruta exacta
    monkeypatch.setattr(este, "ROOT", tmp_path)
    malos = este._barrido_secretos()
    assert malos == ["docs/nota.md: " + forma] and muestra not in " ".join(malos)


def test_las_excepciones_de_secretos_existen_y_de_verdad_llevan_formas_inventadas():
    """Si un test exento deja de inventar secretos, sobra su excepción (se retira para no dejar un hueco sin motivo)."""
    for rel in SECRETOS_INVENTADOS:
        assert secret_shape_leaks((ROOT / rel).read_text(encoding="utf-8")), rel
