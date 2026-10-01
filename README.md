# Sector Compass

Reads the market-implied business-cycle phase from US sector ETF momentum,
using Fidelity's sector-by-phase patterns.

- `index.html` – the web page (reads `data.json` and `history.csv`)
- `config.json` – ETFs, on/off switches, Fidelity weights, momentum weighting
- `scripts/update.py` – fetches prices, computes scores, writes `data.json`
- `.github/workflows/update.yml` – runs the update at 23:00 UTC on weekdays and publishes the site

Manual update: Actions tab → "Daily update and publish" → Run workflow.
Data source: Yahoo Finance (adjusted closes), falling back to Stooq. `last_run.txt` records the last run and any errors. Add a `TIINGO_API_KEY` repository secret to switch to Tiingo.
