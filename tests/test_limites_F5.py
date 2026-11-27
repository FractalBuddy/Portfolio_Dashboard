"""F5 punto 4: los topes propios del feed v2 que afectan al crecimiento del registro, fijados a propósito (cambiarlos es una decisión del dueño), y
el aviso al 50 % con el registro real. Números antes/después en auditorias/FIXES_F5_LISTAS_Y_SIMBOLOS.md."""
import pathlib

import feed_v2_common as c

ROOT = pathlib.Path(__file__).resolve().parent.parent


def test_topes_fijados():
    assert c.MAX_REGISTRY_SYMBOLS == 400
    assert c.MAX_BYTES["manifest"] == 600_000 and c.MAX_BYTES["manifest_root"] == 600_000
    assert c.HEADROOM_WARN_FRACTION == 0.5
    assert c.MAX_LISTS == 10 and c.MAX_LIST_SIZE <= c.MAX_REGISTRY_SYMBOLS


def test_el_registro_real_esta_bajo_el_tope_y_por_encima_del_aviso():
    n = len(c.load_registry(ROOT / "registry.json"))
    assert c.HEADROOM_WARN_FRACTION * c.MAX_REGISTRY_SYMBOLS <= n < c.MAX_REGISTRY_SYMBOLS      # 251 de 400 = 63 %: el validador avisa (no bloquea)


def test_el_aviso_del_50_por_ciento_salta_con_el_tamano_real_del_registro():
    import sanity_check_v2 as sc

    class Ctx:
        def __init__(self):
            self.w = []

        def warn(self, m):
            self.w.append(m)
    ctx = Ctx()
    sc._check_headroom({}, len(c.load_registry(ROOT / "registry.json")), ctx)
    assert ctx.w and "MAX_REGISTRY_SYMBOLS 63 % (251 de 400)" in ctx.w[0]
    ctx = Ctx()
    sc._check_headroom({}, 199, ctx)                                                         # control negativo: por debajo del 50 %, sin aviso
    assert ctx.w == []
