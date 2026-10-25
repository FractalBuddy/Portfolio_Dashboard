"""Hace importables los módulos de scripts/ (no es un paquete) desde los tests."""
import os
import socket
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))

_LOCAL_HOSTS = {"127.0.0.1", "localhost", "::1", "0.0.0.0", ""}


class RealNetworkAttempt(OSError):
    """Un test ha intentado salir a Internet: debe parchear la función de red que corresponda."""


@pytest.fixture(autouse=True)
def _sin_red_real(monkeypatch):
    intentos = []
    # Un proxy del entorno (en 127.0.0.1) escondería el destino real: sin proxy, el host que se comprueba es el de verdad.
    for var in ("HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "http_proxy", "https_proxy", "all_proxy"):
        monkeypatch.delenv(var, raising=False)
    """Los tests no hacen NINGUNA petición real (Yahoo, iShares, CoinGecko, FRED, CNN, Lido...): solo se permite el bucle local
    (los tests que levantan un servidor propio). Cubre requests/urllib/feedparser (resolución DNS) y curl_cffi (yfinance)."""
    real_getaddrinfo = socket.getaddrinfo

    def getaddrinfo(host, *a, **k):
        if isinstance(host, bytes):
            host = host.decode("ascii", "ignore")
        if host not in _LOCAL_HOSTS:
            intentos.append(host)
            raise RealNetworkAttempt(f"petición real a {host!r}: parchea la función de red en el test")
        return real_getaddrinfo(host, *a, **k)

    def connect(self, address, *a, **k):
        host = address[0] if isinstance(address, tuple) else address
        if self.family != socket.AF_UNIX and host not in _LOCAL_HOSTS:
            intentos.append(host)
            raise RealNetworkAttempt(f"conexión real a {host!r}: parchea la función de red en el test")
        return real_connect(self, address, *a, **k)

    real_connect = socket.socket.connect
    monkeypatch.setattr(socket, "getaddrinfo", getaddrinfo)
    monkeypatch.setattr(socket.socket, "connect", connect)
    try:
        from curl_cffi import requests as cffi_requests

        def blocked(self, method, url, *a, **k):
            host = url.split("://", 1)[-1].split("/", 1)[0].split(":")[0]
            if host not in _LOCAL_HOSTS:
                intentos.append(host)
                raise RealNetworkAttempt(f"petición real (curl_cffi) a {host!r}: parchea yf_history/Ticker en el test")
            return real_request(self, method, url, *a, **k)

        real_request = cffi_requests.Session.request
        monkeypatch.setattr(cffi_requests.Session, "request", blocked)
    except ImportError:
        pass
    yield
    # El código de producción se traga las excepciones de red (degrada a «sin dato»): por eso se comprueba aquí, no solo se lanza.
    assert not intentos, f"el test intentó salir a Internet: {sorted(set(intentos))}"


@pytest.fixture(autouse=True)
def _estado_yahoo_limpio(monkeypatch):
    """El estado de Yahoo (racha de fallos, 429, ritmo) es global del módulo, como en un run real: cada test parte de cero."""
    import fetch_market_data as fm
    for name, value in (("_YF_FAIL_STREAK", 0), ("_YF_429_CONSECUTIVE", 0), ("_YF_429_TOTAL", 0), ("_YF_ABANDONED", False),
                        ("_YF_REQS", 0), ("_YF_LAST_REQ", 0.0), ("_ISHARES_PAGES", {}), ("_BUDGET_HIT", False)):
        monkeypatch.setattr(fm, name, dict(value) if isinstance(value, dict) else value, raising=False)


@pytest.fixture()
def world(monkeypatch):
    """Yahoo, CoinGecko, FRED, RSS y VIX/Fear & Greed simulados para los tests del feed v2 (ver tests/v2_world.py)."""
    from v2_world import install_world
    return install_world(monkeypatch)
