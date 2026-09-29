# Market data feed for My Portfolio dashboard

This repo does one job: a GitHub Actions workflow runs a Python script on a
schedule, fetches **public** market data (indices, stocks, commodities, forex,
country heatmap, news, Spain CPI and, optionally, US rates from FRED) and
publishes it as `data.json` (+ a tiny `meta.json`) on a dedicated **`data`
branch**. The dashboard reads it through jsDelivr:

```
https://cdn.jsdelivr.net/gh/<user>/<repo>@data/meta.json   (a few bytes, polled)
https://cdn.jsdelivr.net/gh/<user>/<repo>@data/data.json   (~10 MB, ~2 MB gzipped)
```

Only generic market prices anyone could look up live here. The dashboard
itself, holdings, balances and backups are **not** part of this repo (and
`.gitignore` blocks `*.html` and `*backup*.json` to keep it that way).

## Why a separate `data` branch

`data.json` is ~10 MB. Committing it to `main` four times a day would add
~0.4 MB of history per commit -- about half a gigabyte a year -- for data
nobody ever needs the history of. Instead the workflow replaces the `data`
branch with **one fresh commit every run**, so the repository stays a
constant ~2 MB no matter how many years it runs. `main` contains code only.

## One-time setup

1. Create a **public** repo (public repos get unlimited free Actions minutes
   and are required for jsDelivr).
2. Add these files on `main`: `scripts/fetch_market_data.py`,
   `requirements.txt`, `.github/workflows/update-data.yml`,
   `.github/dependabot.yml`, `.gitignore`, this README.
3. *(Optional)* free FRED API key for the rates/CPI sections:
   <https://fred.stlouisfed.org/docs/api/api_key.html>, then
   Settings -> Secrets and variables -> Actions -> **New repository secret**,
   name `FRED_API_KEY`. Without it those sections are just left empty.
4. Settings -> Actions -> General -> Workflow permissions: **Read and write**.
5. Actions tab -> "Update market data" -> **Run workflow**. This creates the
   `data` branch on the first run.
6. Turn on *Settings -> Code security*: Secret scanning + Push protection, and
   Dependabot alerts.

## Maintenance (nothing to do most of the time)

* Dependabot opens a monthly PR to bump the SHA-pinned actions: just merge it.
* The workflow refuses to publish if the output fails its sanity check, so a
  broken Yahoo/FRED day keeps serving the last good data instead of wiping it.
* Runs are serialised (`concurrency`) and time-limited (`timeout-minutes`).

## Running it locally

```bash
pip install -r requirements.txt
export FRED_API_KEY=your_key_here   # optional
python scripts/fetch_market_data.py   # writes data.json + meta.json
```

## Changing what gets tracked

Edit the watchlists at the top of `scripts/fetch_market_data.py` (`INDICES`,
`STOCKS`, `COMMODITIES`, `FOREX`, `COUNTRY_HEATMAP`, `RATES`) -- plain Python
lists of `(symbol, name)` tuples using Yahoo Finance tickers or FRED series
IDs. A stock only gets automatic pricing in the dashboard if its ticker is in
`STOCKS`.
