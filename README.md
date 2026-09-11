# Market data feed for My Portfolio dashboard

This repo does one job: a GitHub Actions workflow runs a Python script on a
schedule, fetches public market data (indices, stocks, commodities, forex,
country heatmap, and optionally US rates from FRED), and commits the result
to `data.json`. The dashboard reads that file straight from
`raw.githubusercontent.com`, so it stays fresh even when your browser is
closed.

**Nothing about your personal portfolio lives here.** No holdings, no
balances, no account info -- only generic market prices anyone could look up.

## One-time setup

1. **Create the repo.** Public repos get unlimited free GitHub Actions
   minutes, which is why this is public. If you'd rather keep it private,
   that's fine too -- private repos get 2,000 free Actions minutes/month,
   which easily covers a few runs a day.

2. **Add these files** to the repo (same folder structure as this
   download): `scripts/fetch_market_data.py`, `requirements.txt`,
   `.github/workflows/update-data.yml`.

3. **(Optional) Get a free FRED API key** for the rates/bonds section:
   - Go to https://fred.stlouisfed.org/docs/api/api_key.html
   - Create a free account, request a key (instant).

4. **Add it as a repo secret:**
   - Repo → Settings → Secrets and variables → Actions → New repository secret
   - Name: `FRED_API_KEY`
   - Value: the key you got in step 3
   - (If you skip this, the rates section is simply left empty -- everything
     else still works.)

5. **Enable Actions** if prompted (Settings → Actions → allow).

6. **Trigger it once manually** to generate the first `data.json`:
   - Actions tab → "Update market data" workflow → "Run workflow"

7. **Grab your raw data URL** -- it'll look like:
   ```
   https://raw.githubusercontent.com/<your-username>/<your-repo>/main/data.json
   ```
   Send me that URL (or just your username + repo name) and I'll wire it
   into the dashboard.

## Running it locally (optional, for testing)

```bash
pip install -r requirements.txt
export FRED_API_KEY=your_key_here   # optional
python scripts/fetch_market_data.py
cat data.json
```

## Changing what gets tracked

Edit the watchlists at the top of `scripts/fetch_market_data.py`
(`INDICES`, `STOCKS`, `COMMODITIES`, `FOREX`, `COUNTRY_HEATMAP`, `RATES`) --
they're plain Python lists of `(symbol, name)` tuples using Yahoo Finance
tickers (for `yfinance`) or FRED series IDs (for rates).
