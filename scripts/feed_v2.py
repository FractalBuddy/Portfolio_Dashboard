#!/usr/bin/env python3
"""
Generador del feed GENÉRICO v2 (schema 2): un fichero por símbolo, sin datos del dueño, para publicar en GitHub Pages bajo /v2/.

    python scripts/feed_v2.py --registry registry.json --out site/v2 --prev prev_site/v2

Qué hace (y qué reutiliza de `fetch_market_data.py`, la lógica probada del feed v1: ritmo y 429 de Yahoo, presupuesto de tiempo, FRED, CNN,
alternative.me, CoinGecko, RSS):

  * Lee `registry.json` (lista pública y neutra de símbolos) y el estado del run anterior (`health.json` del sitio publicado).
  * HISTÓRICO INCREMENTAL: de cada símbolo pide solo ~1 mes de barras diarias (más si lleva tiempo sin actualizarse) y las fusiona por fecha con
    lo ya publicado. Un símbolo nuevo baja 10 años una sola vez (máx. BACKFILL_MAX_PER_RUN por run). Los años cerrados no se reescriben.
  * NIVELES: hot en cada run (4 veces al día) y con intradía de 5 min, warm ~1 vez al día, cold ~1 vez a la semana.
  * SÍMBOLOS MUERTOS NO BLOQUEAN: un símbolo que falla conserva sus últimos datos marcados `stale`; tras FAIL_STREAK_INVALID fallos seguidos pasa a
    `invalid` (se reintenta una vez a la semana); uno congelado desde hace >45 días pasa a `delisted`. Si fallan casi todos a la vez (Yahoo caído)
    no se penaliza a nadie.
  * DIVISA/UNIDAD EXPLÍCITAS (BE-01): cada serie lleva `currency` y `unit`; si Yahoo informa otra divisa/escala que la declarada en el registro
    (GBp frente a GBP), el símbolo NO se publica en este run.
  * Además: cripto (CoinGecko, hasta 250 por lote), macro (FX, tipos, IPC, VIX, Fear & Greed, calendario), noticias y salud del run.

Salida (todo bajo --out): index.json, s/<SYM>/{latest.json,d/<AAAA>.json,i.json}, crypto/latest.json, macro.json, news.json, health.json y
manifest.json (ruta -> bytes + sha256 de todo lo anterior). El workflow lo valida con `sanity_check_v2.py` y lo publica con deploy-pages.
"""
import argparse
import hashlib
import json
import math
import os
import re
import shutil
import statistics
import sys
import time
from collections import Counter
from datetime import datetime, timedelta, timezone

import requests

import feed_v2_common as c
import fetch_market_data as fm

# --- Cadencia y cupos -------------------------------------------------------------------------------------------------------------
BACKFILL_MAX_PER_RUN = 80          # descargas largas (5-10 años) por run: reparte el alta de símbolos nuevos y el riesgo de 429
FROZEN_DAYS = 45                   # última barra más vieja que esto (con Yahoo respondiendo) = deslistado o congelado
STALE_BAR_DAYS = fm.STALE_BAR_DAYS # última barra más vieja que esto = el precio se publica marcado `stale`
STALE_CARRY_MAX_DAYS = fm.STALE_CARRY_MAX_DAYS
REBASE_TOLERANCE = 0.02            # el pasado guardado difiere >2 % (mediana) del que da Yahoo ahora: split/ajuste => se baja entero otra vez
MIN_OVERLAP = 3                    # fechas comunes mínimas para juzgar el rebase
GLOBAL_OUTAGE_RATIO = 0.5          # si falla más de la mitad de lo intentado (con ≥10 intentos) es Yahoo, no los símbolos: nadie se penaliza
INTRADAY_MAX_BARS = 800
V2_YAHOO_BUDGET_SECONDS = 13 * 60
V2_HARD_BUDGET_SECONDS = 17 * 60   # BE-12: tope global del run (el job tiene más margen para descargar el sitio anterior y subir el artefacto)
CRYPTO_TOP_N = 250
CRYPTO_BATCH = 250                 # CoinGecko admite hasta 250 por página
CRYPTO_MAX_CARRY_DAYS = 7
NEWS_WATCHLIST_MAX = 25            # símbolos con titulares propios (los primeros hot que sean acciones/ETF)
NEWS_PER_SYMBOL = 2
YAHOO_RSS = "https://feeds.finance.yahoo.com/rss/2.0/headline?s={}&region=US&lang=en-US"
CAL_MAX = 40
CAL_PATTERNS = (                   # (regex sobre el nombre de la publicación en FRED, etiqueta)
    (r"^Consumer Price Index", "US CPI"), (r"^Employment Situation", "US Employment Situation"),
    (r"^Gross Domestic Product", "US GDP"), (r"^Personal Income and Outlays", "US Personal Income and Outlays (PCE)"),
    (r"^FOMC Press Release", "FOMC decision"), (r"Advance Monthly Sales for Retail", "US Retail Sales"),
)
_COIN_ID_RE = re.compile(r"^[a-z0-9][a-z0-9._\-]{0,79}$")
_COIN_SYM_RE = re.compile(r"^[A-Za-z0-9.\-]{1,15}$")
_FX_PAIR_RE = re.compile(r"^([A-Z]{3})([A-Z]{3})=X$")


def log(msg):
    print(msg, file=sys.stderr)


def iso_z(dt):
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def parse_z(s):
    try:
        return datetime.strptime(str(s), "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
    except ValueError:
        return None


def parse_day(s):
    try:
        return datetime.strptime(str(s)[:10], "%Y-%m-%d").date()
    except ValueError:
        return None


class Counting:
    """Cuenta las peticiones HTTP de `requests.get` (CoinGecko, FRED, RSS, CNN, alternative.me...) por familia de host durante el run.
    Yahoo va por curl_cffi (yfinance) y se cuenta aparte, con los contadores del propio fetch_market_data."""
    def __init__(self):
        self.by = Counter()
        self._orig = None

    def __enter__(self):
        self._orig = requests.get

        def counted(url, *a, **k):
            host = re.sub(r"[^a-z0-9.\-:]", "", str(url).split("://", 1)[-1].split("/", 1)[0].lower())[:60] or "otro"
            self.by[host] += 1
            return self._orig(url, *a, **k)
        requests.get = counted
        return self

    def __exit__(self, *exc):
        requests.get = self._orig
        return False


# ------------------------------------------------------------------------------------------------------------------------------
# Barras
# ------------------------------------------------------------------------------------------------------------------------------
def _num(v, default):
    try:
        f = float(v)
        return f if math.isfinite(f) else default
    except (TypeError, ValueError):
        return default


def bars_from_df(df, today):
    """DataFrame de Yahoo -> lista de barras {d,o,h,l,c,v} ordenada, sin fechas repetidas ni futuras. Descarta las barras sin cierre finito > 0
    y repara las incoherentes (máximo/mínimo que no contienen la apertura o el cierre) para que se cumpla low <= close <= high. Redondeo a 6 decimales."""
    limit = today + timedelta(days=1)

    def col(name):
        return df[name].tolist() if name in df.columns else [None] * len(df)

    days = [i.strftime("%Y-%m-%d") for i in df.index]
    out = {}
    for day, op, hi_, lo_, cl_, vol_ in zip(days, col("Open"), col("High"), col("Low"), col("Close"), col("Volume")):
        d = parse_day(day)
        cl = _num(cl_, None)
        if d is None or d > limit or cl is None or cl <= 0:
            continue
        o, h, lo = _num(op, cl), _num(hi_, cl), _num(lo_, cl)
        hi, lo = max(h, o, cl, lo), min(h, o, cl, lo)
        vol = _num(vol_, 0.0)
        out[day] = {"d": day, "o": round(o, 6), "h": round(hi, 6), "l": round(lo, 6), "c": round(cl, 6), "v": int(vol) if vol > 0 else 0}
    return [out[k] for k in sorted(out)]


def merge_bars(old, new):
    """Fusiona por fecha: la barra nueva sustituye a la antigua de la misma fecha."""
    by = {b["d"]: b for b in old}
    by.update({b["d"]: b for b in new})
    return [by[k] for k in sorted(by)]


def rebase_ratio(old, new):
    """Mediana de cierre_guardado / cierre_nuevo en las fechas comunes. ~1 si el pasado guardado sigue valiendo; ≠1 tras un split o un ajuste.
    None si hay menos de MIN_OVERLAP fechas comunes."""
    nb = {b["d"]: b["c"] for b in new}
    ratios = [b["c"] / nb[b["d"]] for b in old if b["d"] in nb and nb[b["d"]] > 0]
    return statistics.median(ratios) if len(ratios) >= MIN_OVERLAP else None


def year_doc(sym, entry, year, bars):
    return {"schema": c.SCHEMA, "s": sym, "y": year, "currency": entry["currency"], "unit": entry["unit"],
            "d": [b["d"] for b in bars], "o": [b["o"] for b in bars], "h": [b["h"] for b in bars],
            "l": [b["l"] for b in bars], "c": [b["c"] for b in bars], "v": [b["v"] for b in bars]}


def bars_from_year_doc(doc):
    try:
        cols = [doc[k] for k in "dohlcv"]
        if not all(isinstance(x, list) and len(x) == len(cols[0]) for x in cols):
            return []
        return [{"d": d, "o": o, "h": h, "l": lo, "c": cl, "v": v} for d, o, h, lo, cl, v in zip(*cols)]
    except (KeyError, TypeError):
        return []


def period_for_gap(gap_days):
    """Periodo de Yahoo que cubre el hueco con solape suficiente para fusionar y comprobar el rebase."""
    for limit, period in ((20, "1mo"), (80, "3mo"), (170, "6mo"), (350, "1y"), (1700, "5y")):
        if gap_days <= limit:
            return period
    return "10y"


def check_unit(entry, observed):
    """None si la divisa/escala observada en Yahoo concuerda con la del registro; si no, el motivo (el símbolo NO se publica).
    Índices y pares de divisas (unit points/rate) no se comprueban: Yahoo no da ahí una divisa fiable. En `subunit` (GBp...) es OBLIGATORIO que
    Yahoo informe la divisa: no verificar la escala es justo el riesgo x100."""
    if entry["unit"] in ("points", "rate"):
        return None
    if observed is None:
        return "Yahoo no informa la divisa y la unidad es subunit: no se puede verificar la escala" if entry["unit"] == "subunit" else None
    if not c.same_unit(observed, entry["currency"]):
        return "divisa observada %s != declarada %s" % (observed, entry["currency"])
    return None


# ------------------------------------------------------------------------------------------------------------------------------
# Un run
# ------------------------------------------------------------------------------------------------------------------------------
class Run:
    def __init__(self, registry, out, prev, now, force_all=False):
        self.registry = registry
        self.out, self.prev = out, prev
        self.now = now
        self.today = now.date()
        self.force_all = force_all
        self.generated_at = iso_z(now)
        self.stats = Counter()
        self.problems = {"unit_mismatch": [], "failed": [], "rebased": [], "deferred": [], "frozen": []}
        self.state = {}                # símbolo -> estado nuevo
        self.latest = {}               # símbolo -> latest.json publicado (para índice y macro)
        self.written = {}              # ruta -> bytes
        self.yahoo_calls = 0
        self.stop = False
        self.backfills = 0
        self.prev_state = self._load_prev_state()

    # -- E/S -----------------------------------------------------------------------------------------------------------------
    def prev_path(self, rel):
        return os.path.join(self.prev, *rel.split("/")) if self.prev else None

    def read_prev(self, rel):
        p = self.prev_path(rel)
        if not p or not os.path.isfile(p):
            return None
        try:
            with open(p, encoding="utf-8") as f:
                return json.load(f)
        except (OSError, ValueError):
            log("  ! %s del sitio anterior ilegible: se ignora" % rel)
            return None

    def emit(self, rel, obj):
        self.written[rel] = c.write_json(os.path.join(self.out, *rel.split("/")), obj)

    def copy_prev(self, rel):
        src = self.prev_path(rel)
        if not src or not os.path.isfile(src):
            return False
        dst = os.path.join(self.out, *rel.split("/"))
        os.makedirs(os.path.dirname(dst), exist_ok=True)
        shutil.copyfile(src, dst)
        self.written[rel] = os.path.getsize(dst)
        return True

    def _load_prev_state(self):
        h = self.read_prev("health.json")
        st = h.get("symbols") if isinstance(h, dict) else None
        return st if isinstance(st, dict) else {}

    def prev_year_files(self, sym):
        d = self.prev_path("s/%s/d" % sym)
        if not d or not os.path.isdir(d):
            return []
        years = []
        for name in os.listdir(d):
            m = re.fullmatch(r"(\d{4})\.json", name)
            if m:
                years.append(int(m.group(1)))
        return sorted(years)

    # -- Yahoo ---------------------------------------------------------------------------------------------------------------
    def yf(self, sym, period):
        self.yahoo_calls += 1
        return fm.yf_history(sym, period=period, interval="1d", auto_adjust=False)

    def yahoo_dead(self):
        """¿Hay que dejar de pedir a Yahoo en este run? (429 sostenido, presupuesto de tiempo)"""
        return self.stop or fm._YF_ABANDONED or fm._budget_exhausted(fm._DEADLINE)

    # -- Estado y cadencia ---------------------------------------------------------------------------------------------------
    def status_of(self, entry):
        if entry["status"] != "active":
            return entry["status"]               # lo que el dueño fija en el registro manda sobre el estado automático
        s = (self.prev_state.get(entry["symbol"]) or {}).get("status")
        return s if s in ("active", "invalid", "delisted") else "active"

    def final_status(self, entry):
        """Estado con el que acaba este run (el índice lo publica): lo fijado en el registro, o el que han dejado los fallos/éxitos de hoy."""
        if entry["status"] != "active" or entry["kind"] != "yahoo":
            return entry["status"]
        return (self.state.get(entry["symbol"]) or {}).get("status", "active")

    def is_due(self, entry, status):
        if status == "dormant":
            return False
        st = self.prev_state.get(entry["symbol"]) or {}
        last_ok, last_try = parse_z(st.get("last_ok")), parse_z(st.get("last_try"))
        if status in ("invalid", "delisted"):
            return self.force_all or last_try is None or (self.now - last_try) >= timedelta(hours=c.RETRY_INVALID_HOURS)
        if self.force_all or last_ok is None:
            return True
        return (self.now - last_ok) >= timedelta(hours=c.TIER_MIN_INTERVAL_HOURS[entry["tier"]])

    # -- Un símbolo ----------------------------------------------------------------------------------------------------------
    def update_symbol(self, entry):
        """Descarga, valida y escribe un símbolo. Devuelve ('ok'|'fail'|'deferred', motivo)."""
        sym = entry["symbol"]
        years = self.prev_year_files(sym)
        recent = []
        for y in years:
            if y >= self.today.year - 1:
                recent += bars_from_year_doc(self.read_prev(c.year_path(sym, y)) or {})
        recent.sort(key=lambda b: b["d"])
        if recent:
            last_old = parse_day(recent[-1]["d"])
            gap = (self.today - last_old).days if last_old else 9999
        else:
            gap = 9999
        period = period_for_gap(gap)
        heavy = period in ("5y", "10y")
        if heavy and self.backfills >= BACKFILL_MAX_PER_RUN:
            return "deferred", "cupo de descargas largas del run agotado"
        try:
            df = self.yf(sym, period)
        except (fm.YahooStop, TimeoutError) as e:
            self.stop = True
            return "deferred", "%s" % type(e).__name__
        except Exception as e:                                           # noqa: BLE001 - cualquier fallo de Yahoo = fallo de este símbolo
            return "fail", "yahoo: %s" % type(e).__name__
        if heavy:
            self.backfills += 1
        why = check_unit(entry, df.attrs.get("currency") if df is not None else None)
        if why:
            return "fail", "unit_mismatch: " + why
        new = bars_from_df(df, self.today)
        if len(new) < 2:
            return "fail", "sin datos suficientes"
        last_day = parse_day(new[-1]["d"])
        if (self.today - last_day).days > FROZEN_DAYS:
            return "fail", "frozen: última barra %s" % new[-1]["d"]
        rewrite_all = heavy or not years
        if recent and not heavy:
            r = rebase_ratio(recent, new)
            if r is not None and abs(r - 1) > REBASE_TOLERANCE:
                # El pasado guardado ya no casa con el que da Yahoo (split, ajuste de dividendos...): se baja la serie entera otra vez, una sola vez.
                if self.backfills >= BACKFILL_MAX_PER_RUN:
                    return "deferred", "rebase pendiente (cupo agotado)"
                try:
                    df2 = self.yf(sym, "10y")
                except (fm.YahooStop, TimeoutError) as e:
                    self.stop = True
                    return "deferred", "%s" % type(e).__name__
                except Exception as e:                                   # noqa: BLE001
                    return "fail", "yahoo: %s" % type(e).__name__
                self.backfills += 1
                why = check_unit(entry, df2.attrs.get("currency") if df2 is not None else None)
                new2 = bars_from_df(df2, self.today) if not why else []
                if why or len(new2) < 2:
                    return "fail", "unit_mismatch: " + why if why else "sin datos suficientes"
                new, recent, rewrite_all = new2, [], True
                self.problems["rebased"].append(sym)
        merged = merge_bars(recent, new)
        cutoff_year = self.today.year - c.HISTORY_YEARS + 1
        by_year = {}
        for b in merged:
            y = int(b["d"][:4])
            if y >= cutoff_year:
                by_year.setdefault(y, []).append(b)
        new_years = {int(b["d"][:4]) for b in new}
        grace = self.today.month <= 2
        rewrite = set()
        for y in by_year:
            if rewrite_all or y >= self.today.year or (y == self.today.year - 1 and grace) or (y in new_years and y not in years):
                rewrite.add(y)
        for y in sorted(by_year):
            if y in rewrite:
                self.emit(c.year_path(sym, y), year_doc(sym, entry, y, by_year[y]))
            elif not self.copy_prev(c.year_path(sym, y)):
                self.emit(c.year_path(sym, y), year_doc(sym, entry, y, by_year[y]))
        if not rewrite_all:
            for y in years:                                                # años que este run no ha tocado
                if y >= cutoff_year and y not in by_year:
                    self.copy_prev(c.year_path(sym, y))
        latest = self.make_latest(entry, merged)
        self.emit(c.latest_path(sym), latest)
        self.latest[sym] = latest
        if entry["tier"] == "hot":
            self.write_intraday(entry)
        return "ok", ""

    def make_latest(self, entry, bars):
        last, prev = bars[-1], bars[-2]
        week_ref = None
        cut = (parse_day(last["d"]) - timedelta(days=7)).isoformat()
        for b in bars:
            if b["d"] <= cut:
                week_ref = b
        vols = [b["v"] for b in bars[-8:] if b["v"] > 0]
        vol_ratio = (last["v"] / (sum(vols) / len(vols))) if last["v"] > 0 and vols else None
        out = {
            "schema": c.SCHEMA, "s": entry["symbol"], "kind": entry["kind"], "name": entry["name"],
            "currency": entry["currency"], "unit": entry["unit"], "price_unit_divisor": c.price_unit_divisor(entry["currency"], entry["unit"]),
            "price": last["c"],
            "change_pct": round((last["c"] / prev["c"] - 1) * 100, 2),
            "change_pct_7d": round((last["c"] / week_ref["c"] - 1) * 100, 2) if week_ref else None,
            "volume_ratio": round(vol_ratio, 2) if vol_ratio else None,
            "sparkline": [b["c"] for b in bars[-c.LATEST_SPARKLINE_BARS:]],
            "as_of": last["d"], "generated_at": self.generated_at,
        }
        age = (self.today - parse_day(last["d"])).days
        if age > STALE_BAR_DAYS:
            out["stale"], out["stale_since"] = True, last["d"]
        return out

    def write_intraday(self, entry):
        sym = entry["symbol"]
        try:
            bars = fm.fetch_intraday_history(sym)
        except Exception:                                                # noqa: BLE001
            bars = []
        self.yahoo_calls += 1
        if not bars:
            return
        tz = fm._INTRADAY_TZ.get(sym)
        bars = [b for b in bars if isinstance(b.get("close"), (int, float)) and b["close"] > 0][-INTRADAY_MAX_BARS:]
        if len(bars) < 2:
            return
        doc = {"schema": c.SCHEMA, "s": sym, "currency": entry["currency"], "unit": entry["unit"],
               "t": [b["time"] for b in bars], "c": [round(b["close"], 6) for b in bars]}
        if tz:
            doc["tz"] = tz
        self.emit(c.intraday_path(sym), doc)

    # -- Símbolo que no se ha actualizado en este run ------------------------------------------------------------------------
    def carry(self, entry, failed):
        """Conserva los datos publicados del símbolo (los años por byte). Si falló hoy, su latest.json se marca `stale`; si lleva más de
        STALE_CARRY_MAX_DAYS así, se retira (los años de histórico se conservan). Devuelve True si queda un latest."""
        sym = entry["symbol"]
        cutoff_year = self.today.year - c.HISTORY_YEARS + 1
        for y in self.prev_year_files(sym):
            if y >= cutoff_year:
                self.copy_prev(c.year_path(sym, y))
        old = self.read_prev(c.latest_path(sym))
        if not isinstance(old, dict) or not isinstance(old.get("price"), (int, float)):
            return False
        as_of = parse_day(old.get("as_of"))
        if as_of is None or (failed and (self.today - as_of).days > STALE_CARRY_MAX_DAYS):
            return False
        if failed:
            old = dict(old)
            old["stale"] = True
            old["stale_since"] = str(old.get("stale_since") or as_of.isoformat())[:10]
            self.emit(c.latest_path(sym), old)
        else:
            self.copy_prev(c.latest_path(sym))
        self.latest[sym] = old
        return True

    # -- Todos los símbolos de Yahoo -----------------------------------------------------------------------------------------
    def run_symbols(self):
        entries = [e for e in self.registry if e["kind"] == "yahoo"]
        order = {t: i for i, t in enumerate(c.TIERS)}
        due = []
        for e in entries:
            if self.is_due(e, self.status_of(e)):
                due.append(e)
        pos = {e["symbol"]: i for i, e in enumerate(entries)}
        due.sort(key=lambda e: (order[e["tier"]], pos[e["symbol"]]))
        self.stats["due"] = len(due)
        results = {}
        for e in due:
            sym = e["symbol"]
            if self.yahoo_dead():
                results[sym] = ("deferred", "Yahoo abandonado o presupuesto de tiempo agotado")
                continue
            try:
                results[sym] = self.update_symbol(e)
            except Exception as exc:                                       # noqa: BLE001 - un símbolo con un fallo inesperado no tumba el run
                results[sym] = ("fail", "interno: %s" % type(exc).__name__)
            kind, why = results[sym]
            if kind != "ok":
                log("  ! %s: %s (%s)" % (sym, kind, why))
        attempted = [s for s, (k, _) in results.items() if k != "deferred"]
        failed = [s for s in attempted if results[s][0] == "fail"]
        outage = len(attempted) >= 10 and len(failed) / len(attempted) > GLOBAL_OUTAGE_RATIO
        if outage:
            log("  ! fallan %d de %d símbolos a la vez: se trata como caída de Yahoo, no se penaliza a ninguno" % (len(failed), len(attempted)))
            self.stats["outage"] = 1
        for e in entries:
            sym = e["symbol"]
            status = self.status_of(e)
            st = dict(self.prev_state.get(sym) or {})
            st.setdefault("fail_streak", e["fail_streak"])
            res = results.get(sym)
            if res is None:
                kept = self.carry(e, failed=False)
            elif res[0] == "ok":
                st.update({"status": "active", "fail_streak": 0, "last_ok": self.generated_at, "last_try": self.generated_at})
                self.stats["updated"] += 1
                kept = True
            elif res[0] == "deferred":
                self.problems["deferred"].append(sym)
                self.stats["deferred"] += 1
                kept = self.carry(e, failed=False)
            else:
                kept = self.carry(e, failed=True)
                self.stats["failed"] += 1
                self.problems["failed"].append(sym)
                reason = res[1]
                if reason.startswith("unit_mismatch"):
                    self.problems["unit_mismatch"].append(sym)
                    self.stats["unit_mismatch"] += 1
                if reason.startswith("frozen"):
                    self.problems["frozen"].append(sym)
                st["last_try"] = self.generated_at
                if not outage or reason.startswith(("unit_mismatch", "frozen")):
                    st["fail_streak"] = int(st.get("fail_streak", 0)) + 1
                    if reason.startswith("frozen"):
                        st["status"] = "delisted"
                    elif st["fail_streak"] >= c.FAIL_STREAK_INVALID:
                        st["status"] = "invalid"
            if kept and res is not None and res[0] != "ok":
                self.stats["carried_stale"] += 1
            if res is None and status == "dormant":
                self.stats["dormant"] += 1
            st["tier"] = e["tier"]
            st["status"] = st.get("status") if st.get("status") in ("active", "invalid", "delisted") else "active"
            self.state[sym] = {k: v for k, v in st.items() if k in ("status", "fail_streak", "last_ok", "last_try", "tier") and v is not None}
        self.stats["backfilled"] = self.backfills

    # -- Criptomonedas -------------------------------------------------------------------------------------------------------
    def build_crypto(self):
        rel = "crypto/latest.json"
        try:
            rows = []
            pages = -(-CRYPTO_TOP_N // CRYPTO_BATCH)
            for page in range(1, pages + 1):
                rows += fm._cg_markets({"vs_currency": "usd", "order": "market_cap_desc", "per_page": CRYPTO_BATCH, "page": page,
                                        "sparkline": "false", "price_change_percentage": "24h,7d"})
                if page < pages:
                    fm._nap(2.5)
            coins = normalize_coins(rows[:CRYPTO_TOP_N], self.today)
            if len(coins) < 20:
                raise ValueError("CoinGecko devolvió muy pocas monedas útiles (%d)" % len(coins))
            self.emit(rel, {"schema": c.SCHEMA, "generated_at": self.generated_at, "currency": "USD", "unit": "currency",
                            "count": len(coins), "coins": coins})
            have = {co["symbol"] for co in coins}
            miss = [e["symbol"] for e in self.registry if e["kind"] == "crypto" and e["symbol"].upper() not in have]
            if miss:
                log("  ! cripto del registro que no aparece en el top de CoinGecko: %s" % miss[:10])
            self.stats["crypto"] = len(coins)
            self.crypto_coins = coins
            return
        except Exception as e:                                               # noqa: BLE001
            log("  ! cripto: %s" % type(e).__name__)
        old = self.read_prev(rel)
        ga = parse_z(old.get("generated_at")) if isinstance(old, dict) else None
        self.crypto_coins = []
        if ga and (self.now - ga).days <= CRYPTO_MAX_CARRY_DAYS and isinstance(old.get("coins"), list):
            old = dict(old)
            old["stale"], old["stale_since"] = True, old.get("stale_since") or ga.date().isoformat()
            self.emit(rel, old)
            self.crypto_coins = old["coins"]
            self.stats["crypto_stale"] = 1

    # -- Macro ---------------------------------------------------------------------------------------------------------------
    def fx_usd(self):
        out = {"USD": 1.0}
        for e in self.registry:
            m = _FX_PAIR_RE.match(e["symbol"])
            lat = self.latest.get(e["symbol"])
            if not (m and e["unit"] == "rate" and lat and not lat.get("stale")):
                continue
            base, quote_ = m.groups()
            rate = lat["price"]
            if quote_ == "USD" and 1e-4 < rate < 1e4:
                out[base] = round(rate, 8)
            elif base == "USD" and 1e-4 < rate < 1e4:
                out[quote_] = round(1.0 / rate, 8)
        return out

    def build_macro(self):
        prev = self.read_prev("macro.json") or {}
        prev = prev if isinstance(prev, dict) else {}
        rates = fm.build_rates()
        cpi, cpi_meta = fm.fetch_cpi_history()
        if fm.FRED_API_KEY:
            rates, cpi, cpi_meta = fm.reuse_previous_fred(rates, cpi, cpi_meta, prev)
        mood = clean_mood(fm.build_market_mood(prev_feed=prev, include_altcoin=False))
        doc = {"schema": c.SCHEMA, "generated_at": self.generated_at, "fx_usd": self.fx_usd(), "rates": rates, "cpi": cpi,
               "cpi_meta": cpi_meta, "market_mood": mood, "calendar": build_calendar(self.today)}
        self.emit("macro.json", doc)

    # -- Noticias ------------------------------------------------------------------------------------------------------------
    def build_news(self):
        fm._NEWS_DEADLINE = min(fm._DEADLINE, time.monotonic() + fm.NEWS_BUDGET_SECONDS)
        general = []
        for url in fm.GENERAL_NEWS_FEEDS:
            general.extend(fm.parse_feed(url, limit=8))
        watch = []
        picks = [e for e in self.registry if e["kind"] == "yahoo" and e["tier"] == "hot" and e["unit"] in ("currency", "subunit")]
        for e in picks[:NEWS_WATCHLIST_MAX]:
            for it in fm.parse_feed(YAHOO_RSS.format(c.quote_symbol(e["symbol"])), limit=NEWS_PER_SYMBOL):
                it["symbol"], it["asset_name"] = e["symbol"], e["name"]
                watch.append(it)
        # Cripto: se filtran los feeds generales por el nombre/ticker de las mayores monedas del registro (neutral, no una lista personal).
        for url in fm.CRYPTO_NEWS_FEEDS:
            for it in fm.parse_feed(url, limit=15):
                title = it["title"].lower()
                hit = next((e for e in self.registry if e["kind"] == "crypto"
                            and any(re.search(r"\b" + re.escape(k) + r"\b", title) for k in (e["name"].lower(), e["symbol"].lower()))), None)
                if hit:
                    it["symbol"], it["asset_name"] = hit["symbol"], hit["name"]
                    watch.append(it)
        self.emit("news.json", {"schema": c.SCHEMA, "generated_at": self.generated_at, "general": dedupe(clean_items(general))[:20],
                                "watchlist": dedupe(clean_items(watch))[:40]})

    # -- Índice, salud, manifiesto -------------------------------------------------------------------------------------------
    def build_index(self):
        rows = []
        crypto = {co["symbol"] for co in getattr(self, "crypto_coins", [])}
        for e in self.registry:
            lat = self.latest.get(e["symbol"]) if e["kind"] == "yahoo" else None
            as_of = lat["as_of"] if lat else (self.today.isoformat() if e["kind"] == "crypto" and e["symbol"].upper() in crypto else None)
            rows.append({"s": e["symbol"], "kind": e["kind"], "name": e["name"], "currency": e["currency"], "unit": e["unit"],
                         "tier": e["tier"], "as_of": as_of, "status": self.final_status(e)})
        self.emit("index.json", {"schema": c.SCHEMA, "generated_at": self.generated_at, "symbols": rows})

    def build_health(self, duration, http):
        cap = lambda l: sorted(set(l))[:40]                                  # noqa: E731
        published = sum(1 for e in self.registry if e["kind"] == "yahoo" and e["symbol"] in self.latest)
        counts = {k: int(self.stats.get(k, 0)) for k in ("due", "updated", "failed", "carried_stale", "deferred", "backfilled",
                                                         "unit_mismatch", "crypto", "outage")}
        counts.update({"registry": len(self.registry), "published": published, "rebased": len(self.problems["rebased"])})
        doc = {
            "schema": c.SCHEMA, "generated_at": self.generated_at,
            "run": {"duration_s": round(duration, 1), "yahoo_calls": self.yahoo_calls, "yahoo_requests": int(fm._YF_REQS),
                    "http_requests": dict(sorted(http.items())), "yahoo_rate_limited": int(fm._YF_429_TOTAL),
                    "yahoo_abandoned": bool(fm._YF_ABANDONED), "budget_exhausted": bool(fm._BUDGET_HIT)},
            "counts": counts,
            "problems": {k: cap(v) for k, v in self.problems.items()},
            "symbols": self.state,
        }
        self.emit("health.json", doc)

    def build_manifest(self):
        files = {}
        total = 0
        for rel in sorted(self.written):
            p = os.path.join(self.out, *rel.split("/"))
            with open(p, "rb") as f:
                data = f.read()
            files[rel] = {"bytes": len(data), "sha256": hashlib.sha256(data).hexdigest()}
            total += len(data)
        self.emit("manifest.json", {"schema": c.SCHEMA, "generated_at": self.generated_at, "total_bytes": total, "files": files})
        return total


_LINK_OK_RE = re.compile(r"^https?://[^\s<>\"'\x00-\x1f\x7f]{1,990}$", re.I)


def clean_items(items):
    """Titulares de RSS (no fiables): se descartan los que llevan un enlace con espacios, comillas, '<', '>' o control. Un solo enlace raro NO debe
    bloquear la publicación entera (el validador exige textos sin marcado)."""
    out = []
    for it in items:
        if isinstance(it, dict) and isinstance(it.get("link"), str) and _LINK_OK_RE.match(it["link"]) and it.get("title"):
            out.append(it)
    return out


def clean_mood(mood):
    """Textos de market_mood (CNN, alternative.me: externos) reducidos a texto plano; una cadena que queda vacía se omite."""
    def walk(v):
        if isinstance(v, dict):
            out = {}
            for k, x in v.items():
                y = walk(x)
                if y is not None or not isinstance(x, str):
                    out[k] = y
            return out
        if isinstance(v, str):
            return fm._plain_text(v, 60) or None
        return v
    return walk(mood)


def dedupe(items):
    seen, out = set(), []
    for it in items:
        k = it["title"].lower()
        if k and k not in seen:
            seen.add(k)
            out.append(it)
    return out


def _pct(v):
    f = _num(v, None)
    return round(f, 2) if f is not None and abs(f) <= 1000 else None


def normalize_coins(rows, today):
    """Filas de CoinGecko /coins/markets (no fiables) -> monedas con campos validados. Descarta las que no pasan (id/símbolo/precio raros)."""
    out = []
    for r in rows:
        if not isinstance(r, dict):
            continue
        cid, sym = r.get("id"), r.get("symbol")
        price = _num(r.get("current_price"), None)
        if not (isinstance(cid, str) and _COIN_ID_RE.match(cid) and isinstance(sym, str) and _COIN_SYM_RE.match(sym)
                and price is not None and price > 0):
            continue
        name = fm._plain_text(r.get("name"), 60)
        if not name:
            continue
        rank = r.get("market_cap_rank")
        cap, vol = _num(r.get("market_cap"), None), _num(r.get("total_volume"), None)
        as_of = (parse_day(str(r.get("last_updated") or "")[:10]) or today).isoformat()
        out.append({"id": cid, "symbol": sym.upper(), "name": name, "rank": rank if type(rank) is int and 0 < rank < 100000 else None,
                    "price": round(price, 8), "change_24h_pct": _pct(r.get("price_change_percentage_24h_in_currency", r.get("price_change_percentage_24h"))),
                    "change_7d_pct": _pct(r.get("price_change_percentage_7d_in_currency")),
                    "market_cap": cap if cap is not None and cap >= 0 else None, "volume_24h": vol if vol is not None and vol >= 0 else None,
                    "as_of": as_of})
    return out


def build_calendar(today):
    """Próximas publicaciones macro de EE. UU. (FRED `releases/dates`, requiere FRED_API_KEY). Best-effort: sin clave o si falla, lista vacía."""
    if not fm.FRED_API_KEY or fm._budget_exhausted():
        return []
    try:
        res = requests.get("https://api.stlouisfed.org/fred/releases/dates", timeout=fm._net_timeout(15), params={
            "api_key": fm.FRED_API_KEY, "file_type": "json", "realtime_start": today.isoformat(),
            "realtime_end": (today + timedelta(days=60)).isoformat(), "include_release_dates_with_no_data": "true",
            "order_by": "release_date", "sort_order": "asc", "limit": 1000})
        res.raise_for_status()
        out = []
        for r in res.json().get("release_dates", []):
            day, name = parse_day(r.get("date")), str(r.get("release_name") or "")
            if day is None or day < today:
                continue
            label = next((lab for rx, lab in CAL_PATTERNS if re.search(rx, name)), None)
            if label:
                out.append({"date": day.isoformat(), "event": label})
        return out[:CAL_MAX]
    except Exception as e:                                                   # noqa: BLE001 - sin {e}: la URL de la excepción lleva la api_key
        log("  ! calendario macro: %s" % type(e).__name__)
        return []


# ------------------------------------------------------------------------------------------------------------------------------
def generate(registry, out, prev=None, now=None, force_all=False):
    """Genera el sitio v2 en `out`. Devuelve el Run (para los tests y el resumen)."""
    now = now or datetime.now(timezone.utc)
    if os.path.exists(out) and os.listdir(out):
        raise FileExistsError("--out debe estar vacío o no existir: no se borra nada")
    os.makedirs(out, exist_ok=True)
    run = Run(registry, out, prev, now, force_all=force_all)
    t0 = time.monotonic()
    yf0 = fm._YF_REQS
    with Counting() as http:
        run.run_symbols()
        run.build_crypto()
        run.build_macro()
        run.build_news()
    run.build_index()
    run.build_health(time.monotonic() - t0, http.by)
    run.yahoo_paced = fm._YF_REQS - yf0
    run.total_bytes = run.build_manifest()
    return run


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[1])
    ap.add_argument("--registry", default="registry.json")
    ap.add_argument("--out", default="site/v2")
    ap.add_argument("--prev", default=None, help="carpeta v2 del sitio publicado anteriormente (vacía o ausente = primer run)")
    ap.add_argument("--force-all", action="store_true", help="refrescar todos los símbolos, sea cual sea su nivel")
    a = ap.parse_args(argv)
    try:
        registry = c.load_registry(a.registry)
    except c.RegistryError as e:
        print("::error::registro inválido: %s" % str(e).replace("\n", " ")[:400])
        return 1
    fm._DEADLINE = time.monotonic() + V2_YAHOO_BUDGET_SECONDS
    fm._HARD_DEADLINE = time.monotonic() + V2_HARD_BUDGET_SECONDS
    force = a.force_all or os.environ.get("FEED_V2_FORCE_ALL", "").lower() in ("1", "true", "yes")
    run = generate(registry, a.out, a.prev if a.prev and os.path.isdir(a.prev) else None, force_all=force)
    s = run.stats
    print("v2: %d en el registro, %d tocaban, %d actualizados, %d fallidos, %d aplazados, %d con descarga larga; %d peticiones a Yahoo (%d llamadas); "
          "%d bytes en total" % (len(registry), s["due"], s["updated"], s["failed"], s["deferred"], run.backfills, run.yahoo_paced,
                                 run.yahoo_calls, run.total_bytes))
    return 0


if __name__ == "__main__":
    sys.exit(main())
