"""Sector Compass daily update.

Pulls end-of-day closes for the enabled sector ETFs, computes 1M / 3M returns,
a momentum score per ETF, and a score per business-cycle phase using
Fidelity's sector-by-phase weights. Writes data.json (read by the web page)
and appends one row per trading day to history.csv.

Data source:
  - Tiingo (dividend-adjusted closes) if the TIINGO_API_KEY env var is set.
  - Otherwise Stooq daily CSV (no key needed).

Run:  python scripts/update.py           # live data
      python scripts/update.py --sample  # sample returns, for testing the logic
"""

import csv
import io
import json
import os
import sys
import urllib.request
from datetime import date, datetime, timedelta, timezone

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CONFIG = os.path.join(ROOT, "config.json")
DATA_OUT = os.path.join(ROOT, "data.json")
HISTORY = os.path.join(ROOT, "history.csv")

# Clearly fake numbers, used only with --sample to test the scoring.
SAMPLE_RETURNS = {
    "XLY": (1.8, 4.5), "XLK": (2.6, 7.9), "XLE": (-1.2, 2.1),
    "XLP": (-0.9, -2.4), "XLU": (0.4, 3.0), "XLF": (1.1, 5.2),
    "XLRE": (-0.5, 1.0), "XLI": (1.4, 4.8), "XLB": (0.2, 1.9),
    "XLV": (-1.6, -3.1), "XLC": (2.0, 6.3),
}


# ---------------------------------------------------------------- data fetch

def _get(url):
    req = urllib.request.Request(url, headers={"User-Agent": "sector-compass/1.0"})
    with urllib.request.urlopen(req, timeout=30) as r:
        return r.read().decode("utf-8")


def fetch_tiingo(symbol, key, start):
    url = (f"https://api.tiingo.com/tiingo/daily/{symbol.lower()}/prices"
           f"?startDate={start.isoformat()}&token={key}")
    rows = json.loads(_get(url))
    return [(datetime.fromisoformat(r["date"][:10]).date(), float(r["adjClose"]))
            for r in rows if r.get("adjClose") is not None]


def fetch_stooq(symbol, start):
    url = f"https://stooq.com/q/d/l/?s={symbol.lower()}.us&i=d"
    text = _get(url)
    if not text.startswith("Date"):
        raise RuntimeError(f"Stooq returned no data for {symbol}: {text[:80]!r}")
    out = []
    for r in csv.DictReader(io.StringIO(text)):
        d = date.fromisoformat(r["Date"])
        if d >= start and r.get("Close"):
            out.append((d, float(r["Close"])))
    return out


def fetch_closes(symbol):
    start = date.today() - timedelta(days=150)
    key = os.environ.get("TIINGO_API_KEY", "").strip()
    if key:
        return fetch_tiingo(symbol, key, start), "Tiingo (dividend-adjusted closes)"
    return fetch_stooq(symbol, start), "Stooq"


# ------------------------------------------------------------------- maths

def months_back(d, n):
    """Same calendar day n months earlier, clamped to month end."""
    y, m = d.year, d.month - n
    while m <= 0:
        m += 12
        y -= 1
    for day in (d.day, 30, 29, 28):
        try:
            return date(y, m, day)
        except ValueError:
            continue


def close_on_or_before(series, target):
    best = None
    for d, c in series:
        if d <= target:
            best = (d, c)
        else:
            break
    if best is None:
        raise RuntimeError(f"Not enough history before {target}")
    return best


def returns_from_series(series):
    series = sorted(series)
    last_d, last_c = series[-1]
    _, c1 = close_on_or_before(series, months_back(last_d, 1))
    _, c3 = close_on_or_before(series, months_back(last_d, 3))
    return last_d, last_c, (last_c / c1 - 1) * 100, (last_c / c3 - 1) * 100


def momentum(r1, r3, w):
    """w on the last month, (1-w) on the two months before it."""
    return w * r1 + (1 - w) * (r3 - r1)


def phase_scores(etfs, phases):
    out = []
    for p in phases:
        num = sum(e["weights"][p] * e["momentum"] for e in etfs)
        den = sum(abs(e["weights"][p]) for e in etfs)
        out.append({
            "phase": p,
            "score": round(num / den, 2) if den else None,
            "signals_used": sum(1 for e in etfs if e["weights"][p] != 0),
        })
    return out


# -------------------------------------------------------------------- main

def main():
    sample = "--sample" in sys.argv
    cfg = json.load(open(CONFIG))
    w = cfg["momentum_weight_1m"]
    phases = cfg["phases"]
    enabled = [e for e in cfg["etfs"] if e["enabled"]]

    etfs, as_of, source, errors = [], None, None, []
    for e in enabled:
        try:
            if sample:
                r1, r3 = SAMPLE_RETURNS[e["symbol"]]
                d, close = date.today(), None
                source = "Sample numbers (not market data)"
            else:
                series, source = fetch_closes(e["symbol"])
                d, close, r1, r3 = returns_from_series(series)
            as_of = max(as_of, d) if as_of else d
            etfs.append({
                "symbol": e["symbol"], "sector": e["sector"],
                "close": round(close, 2) if close is not None else None,
                "r1m": round(r1, 2), "r3m": round(r3, 2),
                "momentum": round(momentum(r1, r3, w), 2),
                "weights": e["weights"],
            })
        except Exception as ex:  # keep going; report on the page
            errors.append(f"{e['symbol']}: {ex}")

    if not etfs:
        print("No data fetched:\n" + "\n".join(errors), file=sys.stderr)
        sys.exit(1)

    for i, e in enumerate(sorted(etfs, key=lambda x: -x["momentum"]), 1):
        e["rank"] = i

    scores = phase_scores(etfs, phases)
    ranked = sorted([s for s in scores if s["score"] is not None], key=lambda s: -s["score"])
    lead = round(ranked[0]["score"] - ranked[1]["score"], 2) if len(ranked) > 1 else None

    data = {
        "sample": sample,
        "as_of": as_of.isoformat(),
        "generated_at": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC"),
        "source": source,
        "momentum_weight_1m": w,
        "call": ranked[0]["phase"],
        "runner_up": ranked[1]["phase"] if len(ranked) > 1 else None,
        "lead": lead,
        "close_call": lead is not None and lead < cfg["close_call_margin"],
        "phases": scores,
        "etfs": etfs,
        "errors": errors,
    }
    with open(DATA_OUT, "w") as f:
        json.dump(data, f, indent=2)

    if not sample:
        append_history(data, phases)

    print(f"{data['as_of']}: call={data['call']} lead={lead} "
          + " ".join(f"{s['phase']}={s['score']}" for s in scores))
    if errors:
        print("Errors:\n" + "\n".join(errors), file=sys.stderr)


def append_history(data, phases):
    new_file = not os.path.exists(HISTORY)
    if not new_file:
        with open(HISTORY) as f:
            if any(line.startswith(data["as_of"]) for line in f):
                return  # already recorded this trading day
    with open(HISTORY, "a", newline="") as f:
        wr = csv.writer(f)
        if new_file:
            wr.writerow(["date"] + phases + ["call"])
        by = {s["phase"]: s["score"] for s in data["phases"]}
        wr.writerow([data["as_of"]] + [by[p] for p in phases] + [data["call"]])


if __name__ == "__main__":
    main()
