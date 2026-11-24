"""Revisión nov-2026, hallazgo 5 (F-01): una sola criptomoneda del top 250 con precio < 5·10⁻⁹ USD bloqueaba TODO el feed v2. `normalize_coins`
aceptaba `price > 0` y luego redondeaba a 8 decimales (-> 0.0), y el validador exige `price > 0`: error absoluto, no se publicaba nada.

Arreglo mínimo, sin cambiar el contrato: esa moneda se DESCARTA (como ya se descartaban las de precio <= 0 o id raro) y se avisa en el log del run.
No se publica 0 y el validador no se toca. Si la moneda es una cripto del registro, health.json ya la marca con el canal que existe en el contrato
(`symbols.<SYM>` dormant, `reason: not_listed`). Un aviso propio en `health.json` (p. ej. `problems.crypto_dropped`) cambiaría el esquema cerrado de
health y el contrato: no se hace (ver auditorias/FIXES_REVISION_PUBLICO.md)."""
import sanity_check_v2 as sc
import v2_world as W
import feed_v2 as f2
from feed_v2 import normalize_coins

TODAY = W.NOW.date()


def coin(cid, sym, price, rank=31):
    return {"id": cid, "symbol": sym, "name": cid.title(), "current_price": price, "market_cap": 5e8, "market_cap_rank": rank, "total_volume": 1e6,
            "last_updated": "2026-10-26T11:58:00.000Z"}


def world_with(monkeypatch, extra=(), override=None):
    W.install_world(monkeypatch)
    coins = W.default_coins()
    for i, c in enumerate(coins):
        if override and c["symbol"] in override:
            coins[i] = dict(c, current_price=override[c["symbol"]])
    coins += list(extra)
    monkeypatch.setattr(W.fm, "_cg_markets", lambda params, tries=3: coins)


def test_una_moneda_diminuta_ya_no_bloquea_el_feed_y_no_se_publica_a_cero(tmp_path, monkeypatch, capsys):
    world_with(monkeypatch, [coin("tiny-meme", "tiny", 3e-9)])
    out, _ = W.gen(tmp_path, "a")
    doc = W.jl(out, "crypto/latest.json")
    assert "tiny-meme" not in {x["id"] for x in doc["coins"]} and doc["count"] == len(W.default_coins())
    assert all(x["price"] > 0 for x in doc["coins"])
    errors, _ = sc.check_site(str(out / "v2"), W.REG, today=TODAY)
    assert errors == []                                                       # antes: 'crypto/latest.json: coins: elemento: price: debe ser > 0'
    err = capsys.readouterr().err
    assert "precio < 5e-09 USD" in err and "tiny-meme" in err                 # el aviso sale en el log del run (los ids de CoinGecko son públicos)


def test_una_cripto_del_registro_diminuta_queda_dormant_not_listed_en_health(tmp_path, monkeypatch):
    world_with(monkeypatch, override={"eth": 2e-9})
    out, _ = W.gen(tmp_path, "a")
    assert "ETH" not in {x["symbol"] for x in W.jl(out, "crypto/latest.json")["coins"]}
    assert W.jl(out, "health.json")["symbols"]["ETH"] == {"status": "dormant", "reason": "not_listed", "fail_streak": 0, "tier": "warm"}
    assert sc.check_site(str(out / "v2"), W.REG, today=TODAY)[0] == []


def test_por_encima_del_umbral_se_sigue_publicando_igual_que_antes():
    got = {c["id"]: c["price"] for c in normalize_coins([coin("a", "a", 2e-8), coin("b", "b", 6e-9), coin("c", "c", 0.12345678912)], TODAY)}
    assert got == {"a": 2e-8, "b": 1e-8, "c": 0.12345679}                      # mismo redondeo a 8 decimales de siempre


def test_normalize_coins_descarta_y_cuenta_las_diminutas():
    rows = [coin("a", "a", 4.9e-9), coin("b", "b", 1e-12), coin("c", "c", 1.0)]
    dropped = []
    assert [c["id"] for c in normalize_coins(rows, TODAY, dropped=dropped)] == ["c"] and dropped == ["a", "b"]


# ---- controles negativos ----
def test_control_negativo_el_validador_sigue_rechazando_un_precio_cero(tmp_path, monkeypatch):
    """El arreglo está en el generador; el validador no se ha relajado: un 0 que llegase al sitio seguiría bloqueando."""
    world_with(monkeypatch)
    out, _ = W.gen(tmp_path, "a")
    p = out / "v2" / "crypto" / "latest.json"
    doc = W.jl(out, "crypto/latest.json")
    doc["coins"][5]["price"] = 0.0
    import json
    p.write_text(json.dumps(doc), encoding="utf-8")
    errors, _ = sc.check_site(str(out / "v2"), W.REG, today=TODAY)
    assert any("crypto/latest.json" in e and "price" in e for e in errors)


def test_control_negativo_precio_cero_o_negativo_se_descartaban_ya_y_siguen_igual():
    assert normalize_coins([coin("a", "a", 0), coin("b", "b", -1.0), coin("c", "c", None)], TODAY) == []
    assert f2.CRYPTO_MIN_PRICE == 5e-9
