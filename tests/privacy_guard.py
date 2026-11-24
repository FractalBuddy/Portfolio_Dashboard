"""Guardia de privacidad compartida por los tests (el repo es PÚBLICO).

Lista privada del dueño (proxies de sus fondos y su lista cripto idiosincrática): NO está en el repo, ni en claro ni como huella. Hasta la revisión de
nov-2026 (R-01) aquí había huellas sha256 SIN sal de esos textos; al ser textos muy cortos se revertían por diccionario, así que se retiraron. Ahora la lista
se lee en ejecución de la variable de entorno PRIVATE_DENYLIST (en CI, el secreto del mismo nombre; en local, una variable de entorno) y, si no está, la
comprobación sale NO EJECUTADO (tests/test_privacidad_lista_privada.py), nunca aprobada.
"""
import hashlib
import os
import pathlib
import re

DENYLIST_ENV = "PRIVATE_DENYLIST"
_TOKEN = re.compile(r"[A-Za-z0-9.^=\-]+")
_SKIP_DIRS = frozenset({".git", "__pycache__", ".pytest_cache", ".ruff_cache", "node_modules", ".venv"})
_SKIP_SUFFIXES = frozenset({".pyc", ".png", ".ico", ".zip", ".gz"})


def load_denylist(env=None):
    """frozenset de entradas normalizadas (minúsculas, espacios simples) de PRIVATE_DENYLIST, o None si no hay lista. Nunca imprime nada."""
    env = os.environ if env is None else env
    raw = env.get(DENYLIST_ENV) or ""
    items = frozenset(" ".join(x.lower().split()) for x in re.split(r"[,;\n]", raw)) - {""}
    return items or None


def _candidates(text):
    toks = [t.strip(".-").lower() for t in _TOKEN.findall(text)]
    return set(toks) | {a + " " + b for a, b in zip(toks, toks[1:])}


def denylist_hits(text, denylist):
    """Cuántas palabras (o pares de palabras seguidas) distintas de `text` están en la lista. Devuelve un número, nunca el texto."""
    return len(_candidates(text) & denylist) if denylist else 0


def tree_files(root):
    """Ficheros del árbol de trabajo (lo versionado y lo que haya sin versionar), sin cachés ni binarios."""
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d not in _SKIP_DIRS]
        for fn in filenames:
            p = pathlib.Path(dirpath) / fn
            if p.suffix not in _SKIP_SUFFIXES:
                yield p


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
