#!/usr/bin/env python3
"""
Comprobación de sanidad del feed (data.json + meta.json) ANTES de publicarlo.

Solo usa la biblioteca estándar, a propósito: se ejecuta en el job `publish` (el único con permiso de
escritura), que no instala dependencias de terceros. El job `fetch` sí las instala (yfinance y sus
transitivas), así que su salida se trata como no fiable y se valida aquí, con este script sacado
del propio repositorio (checkout de `scripts/` en `publish`), no del artifact.

Dos tipos de reglas:
  * ABSOLUTAS: el estado del feed por sí solo (suficientes símbolos, histórico, precios, tamaño...).
  * DE REGRESIÓN: comparan con el feed publicado anteriormente (rama `data`). Si no hay feed
    anterior (primer run, rama borrada, descarga fallida) solo se aplican las absolutas.

Uso:
    python3 scripts/sanity_check.py --data data.json --meta meta.json [--prev prev.json]

Variables de entorno opcionales:
    FRED_KEY_SET=true             la clave de FRED está configurada: se exigen tipos e IPC.
    SANITY_ALLOW_REGRESSION=true  las reglas de REGRESIÓN pasan a ser avisos (para cambios
                                  intencionados del universo de símbolos; las absolutas siguen
                                  bloqueando). Se activa con la entrada `allow_regression` del
                                  workflow_dispatch.

Código de salida: 0 = publicable, 1 = no publicar.
"""
import argparse
import datetime as dt
import json
import os
import re
import statistics
import sys

class Alert(str):
    """Aviso que merece que el dueño se entere (el feed se publica, pero con datos degradados que alguien debe corregir):
    el workflow lo manda a Healthchecks como /fail con el texto. Es un `str` normal: el resto del código y los tests no lo notan.
    Los avisos que no son Alert (un tropiezo puntual de Yahoo, información) solo quedan en el log."""


# Listas de filas con `symbol`; solo las cuatro primeras y `funds` llevan histórico.
SYMBOL_LISTS = ("indices", "stocks", "funds", "commodities", "forex", "heatmap", "sector_heatmap")
HISTORY_LISTS = ("indices", "stocks", "funds", "commodities", "forex")

MIN_DATA_BYTES = 3_000_000       # el feed real ronda los 10 MB: por debajo de 3 MB algo se ha perdido
MAX_DATA_BYTES = 18_000_000      # jsDelivr no sirve ficheros de GitHub > 20 MB
MIN_RATIO_VS_PREV = 0.6          # el feed nuevo debe pesar >= 60 % del anterior
MAX_HISTORY_LOSS = 0.10          # tolerancia de acortamiento del histórico de un símbolo
MIN_HIST_BARS_TO_COMPARE = 250   # solo se compara el histórico de símbolos con >250 barras antes
MIN_MEDIAN_STOCK_BARS = 250      # mediana de barras del histórico de las acciones
MAX_HISTORY_AGE_DAYS = 6         # antigüedad máxima de la última barra (cubre fin de semana + festivo)
MIN_FRESH_FRACTION = 0.5         # "mayoría": al menos la mitad de los símbolos con barra reciente
STALE_REUSE_WARN_DAYS = 7        # aviso si tipos/IPC reutilizados llevan tanto sin refrescarse
MAX_STALE_ROWS_FRACTION = 0.3    # más de este % de una lista con precio antiguo (stale) = Yahoo roto, no se publica
PROFILE_WARN_DAYS = 7            # aviso si los fundamentales de las acciones llevan tanto sin refrescarse
STALE_ALERT_DAYS = 2             # una fila con precio antiguo desde hace tanto ya no es un tropiezo puntual de Yahoo: se avisa al dueño
STALE_ALERT_FRACTION = 0.2       # ...o si una lista entera (>= 20 %) lleva precio antiguo hoy
PROFILE_COVERAGE_DROP = 0.6      # aviso si el nº de acciones con perfil cae por debajo de este % del feed anterior
# NAV de iShares (your_funds). Calibrado con un calendario de días hábiles irlandés/UK y los 4 cron diarios (F_nav/evidence/sim_calendar.py):
# con la página 1-2 días hábiles por detrás la edad máxima «normal» del NAV es 5-6 días naturales (Martes de Pascua, 29-dic).
NAV_STALE_DAYS = 6               # edad > 6 días naturales (>= 7): NAV «antiguo» -> aviso en el log (igual que el nav_stale del backend)
NAV_ALERT_DAYS = 8               # >= 8 días en CUALQUIER fondo (o todos «antiguos»): ya no es un festivo, es una página congelada -> Alert (/fail)
NAV_FUTURE_DAYS = 1              # fecha del NAV más allá de «mañana»: no es creíble

# Límites de plausibilidad de los campos de perfil de las acciones (más holgados que los del parser: aquí solo se
# atrapa un fetch roto o manipulado, no un dato raro de Yahoo). El job `publish` no se fía de la salida de `fetch`.
PROFILE_NUM_BOUNDS = {
    "market_cap": (0, 1e15), "enterprise_value": (-1e15, 1e15), "employees": (0, 5e6),
    "pe_trailing": (0, 1e4), "pe_forward": (0, 1e4), "ev_ebitda": (0, 1e4),
    "profit_margin": (-5000, 500), "operating_margin": (-5000, 500), "beta": (-20, 20), "insiders_pct": (0, 100),
    "dividend_yield": (0, 30), "dividend_rate": (0, 1e5), "dividend_5y_avg": (0, 30),
    "ebitda": (-1e15, 1e15), "quarterly_earnings_growth": (-1000, 10000), "total_cash": (0, 1e15), "total_debt": (0, 1e15),
    # campos del veredicto (todos opcionales)
    "market_cap_usd": (0, 1e15), "peg": (0, 1e4), "ps": (0, 1e4), "pb": (0, 1e4), "ev_revenue": (0, 1e4),
    "gross_margin": (-5000, 500), "revenue_growth": (-100, 10000), "roe": (-3000, 3000), "free_cashflow": (-1e15, 1e15),
    "target_mean": (0, 1e8), "analyst_count": (0, 500), "recommendation_mean": (1, 5), "fcf_yield": (-100, 100),
}


def _rows(d, key):
    """Filas (dict) de una lista del feed; ignora basura de otro tipo."""
    v = d.get(key, []) if isinstance(d, dict) else []
    return [x for x in v if isinstance(x, dict)] if isinstance(v, list) else []


def _hist(x):
    h = x.get("history")
    return [b for b in h if isinstance(b, dict)] if isinstance(h, list) else []


def _with_hist(d, key):
    return sum(1 for x in _rows(d, key) if _hist(x))


def _last_date(x):
    """Fecha (date) de la barra más reciente de un símbolo, o None."""
    best = None
    for b in _hist(x):
        try:
            v = dt.date.fromisoformat(str(b.get("date", ""))[:10])
        except ValueError:
            continue
        if best is None or v > best:
            best = v
    return best


def _num(v):
    return isinstance(v, (int, float)) and not isinstance(v, bool)


# Mismos meses y mismas reglas que fetch_market_data._parse_nav_date (copia a propósito: este script es solo-stdlib y no importa el otro;
# un test comprueba que ambos parsers coinciden). Prefijo >= 3 letras de un mes en inglés o español, sin ambigüedad.
_MES_NOMBRES = {
    1: ("january", "enero"), 2: ("february", "febrero"), 3: ("march", "marzo"), 4: ("april", "abril"),
    5: ("may", "mayo"), 6: ("june", "junio"), 7: ("july", "julio"), 8: ("august", "agosto"),
    9: ("september", "septiembre", "setiembre"), 10: ("october", "octubre"), 11: ("november", "noviembre"),
    12: ("december", "diciembre"),
}


def _nav_date(f):
    """Fecha (date) del NAV de una fila de your_funds: `nav_date_iso` si viene; si no, `nav_date` en 'dd/Mon/yyyy' y variantes. None si ilegible."""
    iso = f.get("nav_date_iso")
    if isinstance(iso, str):
        try:
            return dt.date.fromisoformat(iso[:10])
        except ValueError:
            pass
    raw = f.get("nav_date")
    if not isinstance(raw, str):
        return None
    t = raw.strip()
    m = re.fullmatch(r"(\d{1,2})[/\-. ]+([A-Za-z]{3,10})\.?[/\-. ]+(\d{4})", t)
    try:
        if m:
            tok = m.group(2).lower()
            hits = {k for k, names in _MES_NOMBRES.items() for n in names if n.startswith(tok)}
            return dt.date(int(m.group(3)), hits.pop(), int(m.group(1))) if len(hits) == 1 else None
        m = re.fullmatch(r"(\d{4})-(\d{2})-(\d{2})(?:[T ].*)?", t)
        if m:
            return dt.date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
        m = re.fullmatch(r"(\d{1,2})[/.](\d{1,2})[/.](\d{4})", t)
        if m:
            a, b, y = int(m.group(1)), int(m.group(2)), int(m.group(3))
            if a > 12 >= b:
                return dt.date(y, b, a)
            if b > 12 >= a:
                return dt.date(y, a, b)
    except ValueError:
        pass
    return None


def check(d, m, prev=None, *, data_bytes, prev_bytes=0, fred_key_set=False, today=None,
          allow_regression=False):
    """Devuelve (errores, avisos): listas de textos de una línea. Sin errores = publicable."""
    errors, warnings = [], []
    today = today or dt.datetime.now(dt.timezone.utc).date()
    # Los fallos de regresión van a `reg` y, según allow_regression, se tratan como error o aviso.
    reg = []

    # ------------------------------------------------------------------ reglas ABSOLUTAS
    def need(cond, msg):
        if not cond:
            errors.append(msg)

    need(len(_rows(d, "indices")) >= 5, "pocos índices")
    need(len(_rows(d, "stocks")) >= 10, "pocas acciones")
    for k in ("indices", "stocks"):
        need(_with_hist(d, k) >= 0.7 * len(_rows(d, k)),
             "la mayoría de %s no tiene histórico (¿Yahoo limitando?) (%d/%d)" % (k, _with_hist(d, k), len(_rows(d, k))))
    # funds/commodities/forex también: antes nadie miraba si venían sin histórico.
    for k in ("funds", "commodities", "forex"):
        need(_with_hist(d, k) >= 0.7 * len(_rows(d, k)),
             "la mayoría de %s no tiene histórico (%d/%d)" % (k, _with_hist(d, k), len(_rows(d, k))))

    stocks = _rows(d, "stocks")
    priced = sum(1 for x in stocks if x.get("price") is not None)
    need(priced >= 0.7 * len(stocks), "la mayoría de las acciones no tiene precio (%d/%d)" % (priced, len(stocks)))
    bm = d.get("benchmarks", {}) if isinstance(d.get("benchmarks", {}), dict) else {}
    need(len(bm.get("sp500", []) or []) >= 30, "benchmark S&P 500 demasiado corto")
    need(len(_rows(d, "commodities")) >= 3 and len(_rows(d, "forex")) >= 3, "materias primas/divisas demasiado pequeñas")

    # market_mood: antes bastaba 1 clave (p. ej. solo el Fear & Greed cripto con VIX y CNN caídos).
    mood = d.get("market_mood", {}) if isinstance(d.get("market_mood", {}), dict) else {}
    need(len(mood) >= 2 and ("vix" in mood or "stock_fear_greed" in mood),
         "market_mood sin VIX ni Fear & Greed de bolsa (claves: %s)" % sorted(mood)[:6])

    news = d.get("news", {}) if isinstance(d.get("news", {}), dict) else {}
    need(any(news.get(k) for k in ("general", "positions")), "no hay noticias")

    # your_funds: debe haber al menos un fondo con alguna señal utilizable (NAV o variación del proxy).
    yf_rows = _rows(d, "your_funds")
    need(len(yf_rows) >= 1, "your_funds vacío")
    usable = [f for f in yf_rows if f.get("price") is not None or f.get("change_pct") is not None]
    if yf_rows:
        if len(usable) < 1:  # solo aviso: el dashboard conserva el precio manual; no se frena el resto de precios
            warnings.append("your_funds sin NAV ni variación en ningún fondo (¿scrape y proxy caídos?)")
        for f in yf_rows:
            if f not in usable:
                warnings.append("your_funds/%s sin NAV ni variación" % f.get("isin"))
    for f in yf_rows:
        if f.get("source") != "ishares_nav_scrape":
            warnings.append("your_funds/%s no viene del scrape de iShares (source=%s): NAV aproximado o ausente"
                            % (f.get("isin"), f.get("source")))
    # Frescura del NAV scrapeado. NO se fía de `nav_stale` del feed (lo calcula el job fetch): se recalcula aquí con la fecha del NAV.
    # Hasta ahora nav_stale era un simple aviso de log: iShares podía llevar semanas congelado sin que Healthchecks se enterara.
    nav_ages = []
    for f in yf_rows:
        if f.get("source") != "ishares_nav_scrape" or f.get("price") is None:
            continue
        isin_f, nd = f.get("isin"), _nav_date(f)
        if nd is None:
            warnings.append(Alert("your_funds/%s: la fecha del NAV (%r) es ilegible: no se puede comprobar que no esté congelado (¿cambió el formato de iShares?)"
                                  % (isin_f, f.get("nav_date"))))
            continue
        age = (today - nd).days
        if age < -NAV_FUTURE_DAYS:
            warnings.append(Alert("your_funds/%s: la fecha del NAV (%s) está en el futuro: lectura errónea de la página de iShares" % (isin_f, nd.isoformat())))
            continue
        nav_ages.append((isin_f, nd, age))
        if age >= NAV_ALERT_DAYS:
            warnings.append(Alert("your_funds/%s: el NAV de iShares lleva congelado desde %s (%d días): el dashboard muestra un precio desfasado"
                                  % (isin_f, nd.isoformat(), age)))
        elif age > NAV_STALE_DAYS or f.get("nav_stale"):
            warnings.append("your_funds/%s: el NAV es de %s (antiguo, %d días)" % (isin_f, nd.isoformat(), age))
    if nav_ages and all(a > NAV_STALE_DAYS for _, _, a in nav_ages) and not any(a >= NAV_ALERT_DAYS for _, _, a in nav_ages):
        # Todos los fondos con NAV antiguo a la vez (no es un festivo de un solo fondo): la página no se está actualizando para ninguno.
        warnings.append(Alert("your_funds: TODOS los NAV de iShares son antiguos (el más reciente, del %s): la página parece congelada"
                              % max(nd for _, nd, _ in nav_ages).isoformat()))

    need(len(_rows(d, "heatmap")) >= 10, "mapa de países demasiado pequeño")
    need(len(_rows(d, "sector_heatmap")) >= 8, "mapa de sectores demasiado pequeño")

    # FRED: con clave configurada se exigen tipos e IPC (el script reutiliza los del feed anterior si FRED cae).
    if fred_key_set:
        need(len(_rows(d, "rates")) >= 1, "clave de FRED definida pero sin tipos")
        cpi = d.get("cpi", []) if isinstance(d.get("cpi", []), list) else []
        need(len(cpi) >= 12, "clave de FRED definida pero la serie de IPC está vacía")
        if cpi and isinstance(cpi[-1], dict):
            try:
                last = dt.date.fromisoformat(str(cpi[-1].get("date")))
                if (today - last).days > 120:
                    warnings.append("La serie de IPC termina en %s: el modo 'real' del dashboard usa ese último valor para fechas posteriores" % last)
            except ValueError:
                warnings.append("fecha de IPC ilegible")
    # Datos reutilizados del feed anterior (FRED caído): se publican, pero se avisa.
    for r in _rows(d, "rates"):
        if r.get("stale"):
            warnings.append("tipos de FRED reutilizados del feed anterior (%s, stale_since=%s)" % (r.get("series"), r.get("stale_since")))
            break
    cm = d.get("cpi_meta", {}) if isinstance(d.get("cpi_meta", {}), dict) else {}
    if cm.get("reused_from_previous"):
        warnings.append("IPC reutilizado del feed anterior (FRED caído; stale_since=%s)" % cm.get("stale_since"))
    for since in [r.get("stale_since") for r in _rows(d, "rates")] + [cm.get("stale_since")]:
        try:
            if since and (today - dt.date.fromisoformat(str(since)[:10])).days > STALE_REUSE_WARN_DAYS:
                warnings.append(Alert("FRED lleva más de %d días sin refrescarse (desde %s): revisa la clave o las series" % (STALE_REUSE_WARN_DAYS, since)))
                break
        except ValueError:
            pass

    # Plausibilidad: Yahoo degradado/congelado no debe pisar el último feed bueno.
    sp = next((x for x in _rows(d, "indices") if x.get("symbol") == "^GSPC"), None)
    need(sp is not None and bool(_hist(sp)), "falta ^GSPC o no tiene histórico")
    if sp is not None and _last_date(sp):
        need((today - _last_date(sp)).days <= MAX_HISTORY_AGE_DAYS, "histórico de ^GSPC antiguo (¿Yahoo congelado?)")
    bad = [x.get("symbol") for k in ("indices", "stocks", "commodities", "forex") for x in _rows(d, k)
           if not (x.get("price") or 0) > 0 or abs(x.get("change_pct") or 0) > 60]
    need(len(bad) <= 2, "precio/variación inverosímil: %s" % bad[:5])
    need(len(stocks) >= 25 and len(_rows(d, "funds")) >= 3, "el universo se ha encogido")

    # ---- Fundamentales de las acciones (Yahoo Ticker.info) y filas con precio antiguo
    bad_prof = []
    for x in stocks:
        for k, (lo, hi) in PROFILE_NUM_BOUNDS.items():
            if k in x and not (_num(x[k]) and lo <= x[k] <= hi):
                bad_prof.append("%s.%s" % (x.get("symbol"), k))
        for k in ("sector", "industry"):
            if k in x and not (isinstance(x[k], str) and len(x[k]) <= 80):
                bad_prof.append("%s.%s" % (x.get("symbol"), k))
        for k in ("cap_currency", "financial_currency"):
            if k in x and not (isinstance(x[k], str) and len(x[k]) == 3 and x[k].isalpha() and x[k].isupper()):
                bad_prof.append("%s.%s" % (x.get("symbol"), k))
        if "profile_as_of" in x:
            try:
                dt.date.fromisoformat(str(x["profile_as_of"])[:10])
            except ValueError:
                bad_prof.append("%s.profile_as_of" % x.get("symbol"))
    need(not bad_prof, "perfil de acción con campos inverosímiles o de tipo erróneo: %s" % bad_prof[:6])
    # Tipos de cambio a USD (backend: comparar capitalizaciones y flujos de caja de acciones en distintas divisas).
    fx = d.get("fx_usd")
    if fx is not None:
        need(isinstance(fx, dict) and all(isinstance(k, str) and len(k) == 3 and _num(v) and 1e-4 < v < 1e4 for k, v in fx.items()),
             "fx_usd malformado (divisa o tipo fuera de rango)")
        if isinstance(fx, dict):
            no_usd = [x.get("symbol") for x in stocks if _num(x.get("market_cap")) and x.get("market_cap") > 0 and "market_cap_usd" not in x]
            if no_usd:
                warnings.append(Alert("sin capitalización en USD (falta el tipo de cambio de su divisa): %s" % no_usd[:8]))
            h_fx = (d.get("health") or {}).get("fx_stale") if isinstance(d.get("health"), dict) else None
            if isinstance(h_fx, list) and h_fx:
                warnings.append("tipos de cambio a USD reutilizados del feed anterior (Yahoo no los dio hoy): %s" % h_fx[:8])
    ages_p = []
    for x in stocks:
        try:
            ages_p.append((today - dt.date.fromisoformat(str(x.get("profile_as_of"))[:10])).days)
        except ValueError:
            pass
    if ages_p and sum(1 for a in ages_p if a > PROFILE_WARN_DAYS) >= 0.5 * len(ages_p):
        warnings.append(Alert("fundamentales de acciones desactualizados: %d de %d perfiles con más de %d días (Yahoo .info falla; el más antiguo, %d días)"
                        % (sum(1 for a in ages_p if a > PROFILE_WARN_DAYS), len(ages_p), PROFILE_WARN_DAYS, max(ages_p))))
    # Market cap / EV / P/E / EV/EBITDA come from Yahoo's Valuation Measures table; `info` (the fallback) has a wrong EV for some ADRs.
    h_prof = (d.get("health") or {}).get("profiles") if isinstance(d.get("health"), dict) else None
    if isinstance(h_prof, dict) and isinstance(h_prof.get("valuation_table"), int) and h_prof.get("with_data"):
        fresh_with = h_prof["with_data"] - h_prof.get("older_than_7d", 0)
        if fresh_with >= 5 and h_prof["valuation_table"] < 0.6 * fresh_with:
            warnings.append(Alert("tabla de valoración de Yahoo (Ticker.valuation) en solo %d de %d perfiles: capitalización/EV/P/E vienen de `info`, menos fiable (¿cambió yfinance o Yahoo?)"
                            % (h_prof["valuation_table"], fresh_with)))
    odd_ev = []
    for x in stocks:
        mc, ev = x.get("market_cap"), x.get("enterprise_value")
        if isinstance(mc, (int, float)) and isinstance(ev, (int, float)) and not isinstance(mc, bool) and not isinstance(ev, bool) and mc > 0 and ev > 0 \
                and not (0.25 < ev / mc < 4):
            odd_ev.append("%s (%.1fx)" % (x.get("symbol"), ev / mc))
    if odd_ev:
        warnings.append(Alert("EV incoherente con la capitalización (el dashboard lo oculta): %s" % odd_ev[:8]))
    for k in ("indices", "stocks", "funds", "commodities", "forex"):
        rows_k = _rows(d, k)
        stale_k = [x.get("symbol") for x in rows_k if x.get("stale")]
        if stale_k:
            old_k = []
            for x in rows_k:
                if x.get("stale"):
                    try:
                        if (today - dt.date.fromisoformat(str(x.get("stale_since"))[:10])).days >= STALE_ALERT_DAYS:
                            old_k.append(x.get("symbol"))
                    except ValueError:
                        pass
            msg = "%s con precio antiguo (Yahoo no devolvió datos nuevos): %s" % (k, stale_k[:8])
            if old_k:
                msg += " — desde hace %d+ días: %s" % (STALE_ALERT_DAYS, old_k[:8])
            persistent = bool(old_k) or (len(rows_k) >= 3 and len(stale_k) >= STALE_ALERT_FRACTION * len(rows_k))
            warnings.append(Alert(msg) if persistent else msg)
            need(len(rows_k) < 3 or len(stale_k) <= MAX_STALE_ROWS_FRACTION * len(rows_k),
                 "%s: %d de %d filas con precio antiguo (¿Yahoo caído?)" % (k, len(stale_k), len(rows_k)))
    hist_reused = [x.get("symbol") for k in HISTORY_LISTS for x in _rows(d, k) if x.get("history_stale")]
    if hist_reused:
        warnings.append("histórico reutilizado del feed anterior: %s" % hist_reused[:8])

    links_ok = all(str(n.get("link", "")).startswith(("http://", "https://"))
                   for k in news for n in news[k] if isinstance(n, dict)) if news else True
    need(links_ok, "enlace de noticia no http(s)")
    need(m.get("generated_at") == d.get("generated_at"), "meta.json y data.json no coinciden (generated_at)")
    need(m.get("data_bytes") == data_bytes, "meta.data_bytes (%s) no coincide con el tamaño real (%s)" % (m.get("data_bytes"), data_bytes))
    need(data_bytes < MAX_DATA_BYTES, "data.json demasiado grande para jsDelivr (%s bytes)" % data_bytes)
    need(data_bytes >= MIN_DATA_BYTES, "data.json sospechosamente pequeño (%s bytes < %s)" % (data_bytes, MIN_DATA_BYTES))

    # Histórico congelado: la última barra de la MAYORÍA de símbolos no puede tener > 6 días.
    # Tolerante con fines de semana y festivos (por eso 6 días y "mayoría", no todos).
    ages = []
    for k in HISTORY_LISTS:
        for x in _rows(d, k):
            ld = _last_date(x)
            if ld is not None:
                ages.append((today - ld).days)
    if ages:
        fresh = sum(1 for a in ages if a <= MAX_HISTORY_AGE_DAYS)
        need(fresh >= MIN_FRESH_FRACTION * len(ages),
             "histórico congelado: solo %d de %d símbolos tienen una barra de los últimos %d días" % (fresh, len(ages), MAX_HISTORY_AGE_DAYS))
    # Mediana del histórico de acciones (con 1 sola barra por símbolo antes "pasaba").
    if stocks:
        med = statistics.median(len(_hist(x)) for x in stocks)
        need(med >= MIN_MEDIAN_STOCK_BARS, "histórico mediano de las acciones demasiado corto (%s barras < %d)" % (med, MIN_MEDIAN_STOCK_BARS))
    # Índices: misma idea, con umbral más laxo (hay índices recientes en la lista).
    idx_rows = _rows(d, "indices")
    if idx_rows:
        med_i = statistics.median(len(_hist(x)) for x in idx_rows)
        need(med_i >= MIN_MEDIAN_STOCK_BARS, "histórico mediano de los índices demasiado corto (%s barras)" % med_i)

    # ------------------------------------------------------------------ reglas de REGRESIÓN
    has_prev = isinstance(prev, dict) and any(_rows(prev, k) for k in ("indices", "stocks"))
    if has_prev:
        for k in SYMBOL_LISTS:
            ps = {x.get("symbol"): x for x in _rows(prev, k) if x.get("symbol")}
            ns = {x.get("symbol"): x for x in _rows(d, k) if x.get("symbol")}
            if not ps:
                continue
            tol = max(1, int(0.05 * len(ps)))
            gone = sorted(set(ps) - set(ns))
            if len(gone) > tol:
                reg.append("%s: han desaparecido %d símbolos respecto al feed anterior (tolerancia %d): %s" % (k, len(gone), tol, gone[:6]))
            if k in HISTORY_LISTS:
                short = sorted(s for s, x in ps.items()
                               if s in ns and len(_hist(x)) > MIN_HIST_BARS_TO_COMPARE
                               and len(_hist(ns[s])) < (1 - MAX_HISTORY_LOSS) * len(_hist(x)))
                if len(short) > tol:
                    reg.append("%s: histórico acortado >%d %% en %d símbolos (tolerancia %d): %s" % (k, int(MAX_HISTORY_LOSS * 100), len(short), tol, short[:6]))
        # Perfiles: si de golpe muchas menos acciones traen fundamentales, Yahoo .info está fallando (solo aviso: los
        # precios y gráficos no dependen de ello y el dashboard conserva el último perfil bueno).
        n_prev = sum(1 for x in _rows(prev, "stocks") if "market_cap" in x)
        n_new = sum(1 for x in stocks if "market_cap" in x)
        if n_prev >= 10 and n_new < PROFILE_COVERAGE_DROP * n_prev:
            warnings.append(Alert("perfiles de acciones: solo %d con capitalización frente a %d en el feed anterior (¿Yahoo .info limitando?)" % (n_new, n_prev)))
        # your_funds: ningún fondo con NAV exacto antes puede perderlo (ni desaparecer).
        new_f = {f.get("isin"): f for f in yf_rows}
        lost = [f.get("isin") for f in _rows(prev, "your_funds")
                if f.get("price") is not None and (f.get("isin") not in new_f or new_f[f.get("isin")].get("price") is None)]
        # Sin scrape dos runs seguidos: el aviso de arriba solo salta UNA vez (en el siguiente run el feed anterior ya no tenía NAV exacto
        # y `lost` queda vacío), así que un scrape roto de forma permanente se quedaba en un aviso de log que nadie lee.
        prev_f = {f.get("isin"): f for f in _rows(prev, "your_funds")}
        still = [f.get("isin") for f in yf_rows
                 if f.get("source") != "ishares_nav_scrape" and f.get("isin") in prev_f and prev_f[f.get("isin")].get("source") != "ishares_nav_scrape"]
        if still:
            warnings.append(Alert("your_funds: %s sigue sin NAV exacto de iShares (2+ runs seguidos con el proxy): el scrape está roto o cambió la página" % still))
        if lost:
            # Solo aviso: el NAV sale de un scrape secundario y no debe frenar la publicación de los precios de bolsa/índices.
            warnings.append(Alert("your_funds: se ha perdido el NAV exacto de %s (el scrape de iShares falla o cambió la página; se usa el proxy)" % lost))
        if prev_bytes:
            if data_bytes < MIN_RATIO_VS_PREV * prev_bytes:
                reg.append("el feed nuevo pesa %s bytes, menos del %d %% del anterior (%s)" % (data_bytes, int(MIN_RATIO_VS_PREV * 100), prev_bytes))
    else:
        warnings.append("sin feed anterior utilizable: solo se aplican las reglas absolutas")

    (warnings if allow_regression else errors).extend(
        ("[regresión permitida] " if allow_regression else "") + r for r in reg)
    return errors, warnings


def _line(s):
    """Una sola línea: evita que un texto del feed (no fiable) inyecte comandos de workflow."""
    return str(s).replace("\r", " ").replace("\n", " ")


def _no_constant(name):
    raise ValueError("constante no válida en JSON: %s" % name)


def _load_json(path):
    # json.load acepta NaN/Infinity (no son JSON válido): JSON.parse del navegador los rechaza y rompe TODO el feed.
    with open(path, encoding="utf-8") as f:
        return json.load(f, parse_constant=_no_constant)


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[1])
    ap.add_argument("--data", default="data.json")
    ap.add_argument("--meta", default="meta.json")
    ap.add_argument("--prev", default=None, help="feed publicado anteriormente (opcional)")
    ap.add_argument("--alerts-out", default=None,
                    help="fichero donde escribir, en UNA línea, los avisos que merecen que el dueño se entere (vacío = todo bien)")
    a = ap.parse_args(argv)

    try:
        d, m = _load_json(a.data), _load_json(a.meta)
        if not isinstance(d, dict) or not isinstance(m, dict):
            raise ValueError("raíz no es un objeto")
    except Exception as e:  # noqa: BLE001 - cualquier fallo de lectura = no publicable
        print("::error::feed ilegible: %s" % _line(type(e).__name__))
        return 1

    prev, prev_bytes = None, 0
    if a.prev and os.path.exists(a.prev):
        try:
            prev = _load_json(a.prev)
            prev_bytes = os.path.getsize(a.prev)
        except Exception as e:  # noqa: BLE001
            print("::warning::feed anterior ilegible (%s): se ignora la comparación" % _line(type(e).__name__))
            prev = None

    truthy = ("1", "true", "yes", "on")
    try:
        errors, warnings = check(
            d, m, prev,
            data_bytes=os.path.getsize(a.data), prev_bytes=prev_bytes,
            fred_key_set=os.environ.get("FRED_KEY_SET", "").lower() in truthy,
            allow_regression=os.environ.get("SANITY_ALLOW_REGRESSION", "").lower() in truthy,
        )
    except Exception as e:  # noqa: BLE001 - feed con tipos inesperados = no publicable
        print("::error::feed malformado: %s" % _line(type(e).__name__))
        return 1

    alerts = [w for w in warnings if isinstance(w, Alert)]
    for w in warnings:
        print("::warning::" + _line(w))
    if a.alerts_out:
        try:
            with open(a.alerts_out, "w", encoding="utf-8") as f:
                f.write(" | ".join(_line(w) for w in alerts)[:900])
        except OSError as e:
            print("::warning::no se pudo escribir %s (%s)" % (_line(a.alerts_out), _line(type(e).__name__)))
    for e in errors:
        print("::error::" + _line(e))
    if errors:
        print("Sanity-check FALLIDO (%d errores): no se publica." % len(errors))
        return 1
    print("OK: %d índices, %d acciones, %d bytes%s" % (
        len(_rows(d, "indices")), len(_rows(d, "stocks")), os.path.getsize(a.data),
        "" if prev else " (sin comparación con feed anterior)"))
    return 0


if __name__ == "__main__":
    sys.exit(main())
