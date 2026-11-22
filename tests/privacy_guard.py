"""Guardia de privacidad compartida por los tests (el repo es PÚBLICO).

Tras el cierre del corte ya no hay listas del dueño en el código, así que los tests que antes las leían de `fetch_market_data`
(`YOUR_FUNDS`, `CRYPTO_WATCHLIST`) comprueban ahora contra HUELLAS (sha256 del texto en minúsculas): el valor no se repite aquí.
"""
import hashlib
import re

# sha256 de: proxies EUR de los fondos del dueño y palabras de la lista cripto idiosincrática (las grandes y genéricas no cuentan).
PROXY_HASHES = frozenset({
    "611ae0ee18d4697d54281d404aa31e668a57c613960a88c494cbd834e7967410",
    "a0836648fe6466613a186d668943a60750e73d4d43f5d1b01bb6b53c4fe5965e",
})
CRYPTO_HASHES = frozenset({
    "9bb840df7f699d6547f49fd6f9ed17b2d9dd34148f0b01e798d7c51da897ea1a",
    "c13aa4e200ae5d168af4f7469b26e164963e0ffb9d3c0a3698c304f0693ae254",
    "a0c7f3081f6a44603fa63d0ebb5e3d979aea85c240d649832539c4056d503b15",
    "afefb91b38561ce4c5ed9f03d31ba2e93d8d878f6d2817a2851641fbdd290343",
    "2b81a0696f5bae30162c46892aee6dbec43a5c71d887cd9518a2a2ba015c7073",
    "0b391db142bb2de896d9e4ba3a7e8a720ef02136d275ef46fb5b157c3fe96a66",
})

_TOKEN = re.compile(r"[A-Za-z0-9.^=\-]+")


def _h(s):
    return hashlib.sha256(s.lower().encode("utf-8")).hexdigest()


def hits(text, hashes):
    """Palabras (o pares de palabras) de `text` cuya huella está en `hashes`. Solo devuelve la huella, nunca el texto."""
    toks = [t.strip(".-").lower() for t in _TOKEN.findall(text)]
    cand = set(toks) | {a + " " + b for a, b in zip(toks, toks[1:])}
    return sorted(h for h in map(_h, cand) if h in hashes)


# ---------------------------------------------------------------------------------------------------------------------------------
# ISIN e ids de ficha: solo datos SINTÉTICOS en el árbol (bloque H2). Un ISIN de fondo irlandés es «IE» + 10 caracteres.
# ---------------------------------------------------------------------------------------------------------------------------------
ISIN_WINDOW = re.compile(r"IE[0-9A-Z]{10}")                  # ventana de 12 caracteres, SIN anclar: también caza un ISIN pegado a otros caracteres
PRODUCT_ID = re.compile(r"/products/(\d+)")

# Con dígito de control válido (ISO 6166), inventados: no existe ningún instrumento con ellos.
SINTETICOS_VALIDOS = frozenset({"IE000SYNA013", "IE000SYNB011", "IE000SYNC019", "IE000SYND017"})
# Sin dígito de control válido, de los tests de formato y de «ISIN dentro de otro texto» (los tests añaden sufijos: de ahí que la ventana
# de 12 caracteres de «IE000TEST0001» sea «IE000TEST000»).
SINTETICOS_FORMATO = frozenset({"IE0000000001", "IE0000000002", "IE000TEST000", "IE0FAKE00001", "IE0FAKE00002", "IE0FAKE00003"})
ISIN_PERMITIDOS = SINTETICOS_VALIDOS | SINTETICOS_FORMATO
# Ids de ficha de iShares permitidos: los inventados de los tests y los de los 4 ETF públicos de «Explorar» (FUND_FACT_URLS; no son del dueño).
IDS_FICHA_PERMITIDOS = frozenset({"000001", "000002", "1", "100001", "100002", "251860", "251882", "258441", "264659"})


def isin_check_digit(base11):
    """Dígito de control ISO 6166 (Luhn sobre las letras convertidas a números) de los 11 primeros caracteres."""
    digits = "".join(str(int(c, 36)) for c in base11)
    total = 0
    for i, ch in enumerate(reversed(digits)):
        n = int(ch) * (2 if i % 2 == 0 else 1)
        total += n - 9 if n > 9 else n
    return (10 - total % 10) % 10


def isin_leaks(text):
    """Ventanas con forma de ISIN irlandés que no están en la lista de sintéticos. Devuelve cuántas son, nunca su valor."""
    return sum(1 for m in ISIN_WINDOW.finditer(text) if m.group(0) not in ISIN_PERMITIDOS)


def product_id_leaks(text):
    return sum(1 for m in PRODUCT_ID.finditer(text) if m.group(1) not in IDS_FICHA_PERMITIDOS)


# ---------------------------------------------------------------------------------------------------------------------------------
# Símbolos Morningstar/Yahoo `0P` + 8 caracteres (F1 «sombra» NAV Yahoo): identifican un fondo igual que su ISIN, así que el árbol solo puede llevar:
#   * los 7 de la lista FIJA y pública de la sonda de retraso (config/fund_lag_tickers.json), congelados por HUELLA (sha256 de los 10 primeros caracteres):
#     no se repiten aquí y uno nuevo en esa lista hace fallar el guardia hasta que alguien lo revise a propósito;
#   * los inventados de los tests (no existen como instrumento).
# ---------------------------------------------------------------------------------------------------------------------------------
ZERO_P_WINDOW = re.compile(r"0P[0-9A-Z]{8}")                  # sin anclar: también caza un símbolo pegado a otros caracteres (o con su sufijo .F)
ZERO_P_FIJOS_SHA256 = frozenset({
    "83549c3152eee82401415503236558999485d7bdb97fb5a8f3b16e9faae02ff3",
    "1a05463acfb343a76bc5764eff5c6a685e3a90fd1d7eebf43853389d3229b14a",
    "eca9d83ddc24115a4cadc2499f26ee7b498c1d0ba92a04942108854de92dd95c",
    "d5127657398725ec85c07bb515552bea46008bed9fa745eea495f2f1a1fc1b4b",
    "ae11f18baeeafba79ed6d54b305df997d08fc0c03216ecfc44384a80f88d5255",
    "d74af3d6fdffcf5bd760c7390ba3dba276d442fa3bc6a0ea89d7abec845bf307",
    "a228bffc11e10ac7e3217cece1d8865891803c1d1a020605a4f011fd43fc433b",
})
ZERO_P_SINTETICOS = frozenset({
    "0P00000AAA", "0P0ALPHA01", "0P0ALPHA02", "0P0BETA001", "0P0GAMMA01", "0P0SECRET9", "0P0PRIVATE",        # tests de la sonda de fondos
    "0PFAKE0001", "0PFAKE0002", "0PFAKE0003", "0PFAKE0004", "0PFAKE0005", "0PCANARY01",                       # tests de la sombra NAV Yahoo
})


def zero_p_leaks(text):
    """Ventanas `0P` + 8 que no son ni de la lista fija (por huella) ni inventadas. Devuelve cuántas son, nunca su valor."""
    return sum(1 for m in ZERO_P_WINDOW.finditer(text)
               if m.group(0) not in ZERO_P_SINTETICOS and hashlib.sha256(m.group(0).encode("utf-8")).hexdigest() not in ZERO_P_FIJOS_SHA256)
