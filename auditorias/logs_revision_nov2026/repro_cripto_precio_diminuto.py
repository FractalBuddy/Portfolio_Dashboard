"""Reproducciones de la revisión nov-2026 (no forman parte del repo). Se ejecutan con el conftest del repo."""
import json, os
import v2_world as W
import sanity_check_v2 as sc
import feed_v2_common as c


def test_cripto_precio_diminuto_bloquea_publicacion(tmp_path, monkeypatch):
    w = W.install_world(monkeypatch)
    coins = W.default_coins()
    coins.append({"id": "tiny-meme", "symbol": "tiny", "name": "Tiny Meme", "current_price": 3e-9, "market_cap": 5e8,
                  "market_cap_rank": 31, "total_volume": 1e6, "last_updated": "2026-10-26T11:58:00.000Z"})
    monkeypatch.setattr(W.fm, "_cg_markets", lambda params, tries=3: coins)
    out, run = W.gen(tmp_path, "a")
    doc = W.jl(out, "crypto/latest.json")
    tiny = [x for x in doc["coins"] if x["id"] == "tiny-meme"][0]
    errors, _ = sc.check_site(str(out / "v2"), W.REG, today=W.NOW.date())
    print("PRECIO_PUBLICADO", tiny["price"], "ERRORES", [e for e in errors if "crypto" in e][:2])
    assert tiny["price"] == 0.0
    assert any("crypto" in e for e in errors)          # una sola moneda diminuta bloquea TODO el sitio
