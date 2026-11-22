"""Sombra NAV Yahoo (F1), punto 3: comparación SOLO con la barra de la fecha IDÉNTICA a la del NAV oficial (nunca «la más cercana»), puntos básicos
bien calculados tras redondear Yahoo a los decimales del NAV oficial, y bandas de sanidad. Precios inventados."""
from datetime import date
from decimal import Decimal

import pytest

import yahoo_shadow as ys
from yahoo_shadow_fakes import NAV_DAY, NOW, bars, hist

CFG = {"equal_bp": 1, "min_official_decimals": 2}
TODAY = NOW.date()                                   # martes 24-11-2026
THU, FRI, MON = date(2026, 11, 19), NAV_DAY, date(2026, 11, 23)


def cmp(official, points, currency="EUR", official_date=FRI):
    return ys.compare(official, official_date, hist(points, currency), CFG, TODAY)


# ------------------------------------------------------------------ fecha idéntica
def test_misma_fecha_e_igual_tras_redondear():
    out = cmp(87.65, [(THU, 87.40), (FRI, 87.6512)])
    assert out["kind"] == "comparable" and out["equal"] is True and out["bp"] == 0.0
    assert out["raw_bp"] == pytest.approx(0.0012 / 87.65 * 1e4, abs=1e-4)          # 0,137 pb sin redondear
    assert out["lag_bd"] == 0


def test_misma_fecha_y_distinta_en_un_centimo():
    out = cmp(87.65, [(FRI, 87.6551)])                                              # se redondea a 87,66
    assert out["kind"] == "comparable" and out["equal"] is False
    assert out["bp"] == pytest.approx(0.01 / 87.65 * 1e4, abs=1e-4)                 # 1,1409 pb > 1
    assert out["raw_bp"] == pytest.approx(0.0051 / 87.65 * 1e4, abs=1e-4)


def test_nunca_la_mas_cercana_yahoo_por_detras():
    """Yahoo aún no tiene la barra del viernes: el jueves vale EXACTAMENTE lo mismo, y aun así NO se compara."""
    out = cmp(87.65, [(date(2026, 11, 18), 87.10), (THU, 87.65)])
    assert out["kind"] == "yahoo_por_detras" and "bp" not in out and out["lag_bd"] == 1


def test_nunca_la_mas_cercana_hueco_en_la_fecha_oficial():
    """Yahoo tiene el jueves y el lunes, pero no el viernes: sin fecha común, no se compara con ninguna de las dos."""
    out = cmp(87.65, [(THU, 87.65), (MON, 87.65)])
    assert out["kind"] == "sin_fecha_comun" and "bp" not in out and out["lag_bd"] == -1


def test_retraso_en_dias_habiles_con_signo():
    assert cmp(87.65, bars(date(2026, 11, 18), 3, 87.0))["lag_bd"] == 2            # Yahoo dos días hábiles por detrás
    assert cmp(87.65, bars(MON, 3, 87.0), official_date=date(2026, 11, 17))["lag_bd"] == -4


def test_la_barra_de_hoy_o_futura_se_ignora():
    out = cmp(87.65, [(FRI, 87.65), (TODAY, 99.0), (date(2026, 11, 30), 99.0)])
    assert out["kind"] == "comparable" and out["equal"] is True and out["lag_bd"] == 0


# ------------------------------------------------------------------ redondeo y puntos básicos
def test_redondeo_mitad_hacia_arriba_a_los_decimales_del_oficial():
    assert cmp(9.87, [(FRI, 9.865)])["equal"] is True                                # HALF_EVEN daría 9,86 (10 pb): se usa HALF_UP
    assert cmp(9.87, [(FRI, 9.8649)])["equal"] is False


def test_oficial_con_mas_decimales_se_compara_a_esos_decimales():
    out = cmp(87.6543, [(FRI, 87.65434)])
    assert out["equal"] is True and out["bp"] == 0.0
    out = cmp(87.6543, [(FRI, 87.6549)])                                             # a 4 decimales: 6 diezmilésimas = 0,0685 pb (igual a ≤ 1 pb)
    assert out["equal"] is True and out["bp"] == pytest.approx(0.0006 / 87.6543 * 1e4, abs=1e-4)


def test_minimo_de_decimales_del_oficial():
    assert ys.official_decimals(87.6, 2) == 2 and ys.official_decimals(87.65, 2) == 2 and ys.official_decimals(87.6543, 2) == 4
    assert ys.official_decimals(88.0, 2) == 2 and ys.official_decimals(88.0, 0) == 0


def test_bp_exactos_con_decimal_sin_errores_de_coma_flotante():
    rounded, raw = ys.diff_bp(100.0, 100.01, 2)
    assert rounded == Decimal("1") and raw == Decimal("1")                            # 0,01 / 100 = 1 pb exacto: es IGUAL (≤ 1 pb)
    assert cmp(100.0, [(FRI, 100.01)])["equal"] is True
    assert cmp(100.0, [(FRI, 100.02)])["equal"] is False


# ------------------------------------------------------------------ bandas de sanidad
@pytest.mark.parametrize("malo", [0.0, -5.0, float("nan"), float("inf"), 0.05, 20000.0, True, "87.65", None])
def test_precio_fuera_de_rango_o_no_numerico_no_se_compara(malo):
    out = cmp(87.65, [(THU, 87.0), (FRI, malo)])
    assert out["kind"] == "dato_invalido" and "bp" not in out


def test_salto_fuera_de_la_banda_0_5_2_es_comparable_distinto_y_marcado():
    """Un ×2 con divisa EUR y nombre validado es una DISCREPANCIA real (p. ej. otra clase o un ×100 mal etiquetado): entra en la medida y la tumba."""
    out = cmp(87.65, [(FRI, 175.30)])
    assert out["kind"] == "comparable" and out["out_of_band"] is True and out["equal"] is False and out["bp"] == pytest.approx(10000.0)


def test_historico_vacio_o_basura():
    assert cmp(87.65, [])["kind"] == "vacio"
    assert ys.compare(87.65, FRI, {"rows": None, "currency": "EUR"}, CFG, TODAY)["kind"] == "vacio"
    assert ys.compare(87.65, FRI, None, CFG, TODAY)["kind"] == "vacio"
    assert cmp(87.65, [("2026-11-20", 87.65), (None, 1.0)])["kind"] == "dato_invalido"
