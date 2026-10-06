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
import statistics
import sys

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
        if f.get("nav_stale"):
            warnings.append("your_funds/%s: el NAV es de %s (antiguo)" % (f.get("isin"), f.get("nav_date")))

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
                warnings.append("FRED lleva más de %d días sin refrescarse (desde %s): revisa la clave o las series" % (STALE_REUSE_WARN_DAYS, since))
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
        # your_funds: ningún fondo con NAV exacto antes puede perderlo (ni desaparecer).
        new_f = {f.get("isin"): f for f in yf_rows}
        lost = [f.get("isin") for f in _rows(prev, "your_funds")
                if f.get("price") is not None and (f.get("isin") not in new_f or new_f[f.get("isin")].get("price") is None)]
        if lost:
            # Solo aviso: el NAV sale de un scrape secundario y no debe frenar la publicación de los precios de bolsa/índices.
            warnings.append("your_funds: se ha perdido el NAV exacto de %s (el scrape de iShares falla o cambió la página; se usa el proxy)" % lost)
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


def _load_json(path):
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[1])
    ap.add_argument("--data", default="data.json")
    ap.add_argument("--meta", default="meta.json")
    ap.add_argument("--prev", default=None, help="feed publicado anteriormente (opcional)")
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

    for w in warnings:
        print("::warning::" + _line(w))
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
