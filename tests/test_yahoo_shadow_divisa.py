"""Sombra NAV Yahoo (F1), punto 4: divisa. D5 B: las clases en otra divisa se convertirán a EUR con el cambio del BCE más adelante; en la SOMBRA
solo se mide: si el `0P…` no está en la divisa del NAV oficial (EUR), se cuenta aparte como «distinta divisa» y NO entra en el porcentaje de iguales.
Una cotización en subunidad (GBp, peniques) es otra divisa: nunca se divide ni se multiplica por 100 en silencio."""
import pytest

import yahoo_shadow as ys
from yahoo_shadow_fakes import NAV_DAY, NOW, hist

CFG = {"equal_bp": 1, "min_official_decimals": 2}


def cmp(points, currency):
    return ys.compare(87.65, NAV_DAY, hist(points, currency), CFG, NOW.date())


@pytest.mark.parametrize("cur", ["USD", "GBP", "CHF", "eur", "Eur"])
def test_otra_divisa_no_se_compara(cur):
    out = cmp([(NAV_DAY, 87.65)], cur)                       # incluso con el mismo número: no es comparable
    assert out["kind"] == "distinta_divisa" and out.get("subunit") is False and "bp" not in out and "equal" not in out


def test_gbp_peniques_es_distinta_divisa_sin_x100_silencioso():
    """8765 GBp = 87,65 GBP: si el código dividiera por 100 sin decirlo, saldría «igual». Tiene que salir «distinta divisa» (subunidad)."""
    out = cmp([(NAV_DAY, 8765.0)], "GBp")
    assert out["kind"] == "distinta_divisa" and out["subunit"] is True and "bp" not in out
    out = cmp([(NAV_DAY, 87.65)], "GBp")                     # tampoco «igual» por casualidad de número
    assert out["kind"] == "distinta_divisa" and out["subunit"] is True


@pytest.mark.parametrize("cur", [None, "", 3, "EURO", "<b>"])
def test_sin_divisa_conocida_no_se_compara(cur):
    out = cmp([(NAV_DAY, 87.65)], cur)
    assert out["kind"] == "sin_divisa" and "bp" not in out


def test_eur_si_se_compara_y_la_divisa_se_mira_antes_que_las_barras():
    assert cmp([(NAV_DAY, 87.65)], "EUR")["kind"] == "comparable"
    assert cmp([], "USD")["kind"] == "vacio"                  # sin barras no hay nada que medir, sea cual sea la divisa
    assert cmp([(NAV_DAY, 1e9)], "USD")["kind"] == "distinta_divisa"
