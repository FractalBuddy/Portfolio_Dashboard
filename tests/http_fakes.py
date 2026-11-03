"""Respuesta HTTP falsa con el mínimo que usa fetch_market_data._ishares_get_text (streaming, cabeceras, close, raise_for_status)."""
import requests


class FakeResp:
    def __init__(self, text="", status=200, headers=None):
        self.text, self.status_code, self.headers = text, status, dict(headers or {})
        self.encoding = "utf-8"

    def iter_content(self, chunk_size=65536):
        data = self.text.encode("utf-8")
        for i in range(0, len(data), chunk_size):
            yield data[i:i + chunk_size]

    def raise_for_status(self):
        if self.status_code >= 400:
            e = requests.HTTPError("HTTP %d" % self.status_code)
            e.response = self
            raise e

    def close(self):
        pass
