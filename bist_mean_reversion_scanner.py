"""
BIST Mean Reversion Scanner
============================
Scans BIST100 or BIST500 stocks for mean-reversion setups and tracks
outcomes over the following 5 trading days.

A signal fires when the closing price has stretched too far from
EMA20 or EMA50 — i.e. the gap% between close and the EMA exceeds
a configurable threshold.

  ABOVE_20 / ABOVE_50  — close is >= threshold% above EMA20/EMA50
                         (price stretched up -> potential pullback)
  BELOW_20 / BELOW_50  — close is >= threshold% below EMA20/EMA50
                         (price stretched down -> potential bounce)

Gap% formula:  (close - EMA) / EMA x 100

Default thresholds: 5% for EMA20, 8% for EMA50.

Scanning:
    python bist_mean_reversion_scanner.py                       # XU100, latest
    python bist_mean_reversion_scanner.py -i xu500              # XU500
    python bist_mean_reversion_scanner.py -g 4 -G 7             # custom thresholds
    python bist_mean_reversion_scanner.py -d 2026-05-16         # specific session
    python bist_mean_reversion_scanner.py -n                    # skip logging

Each run automatically updates d1-d5 outcome data for all incomplete
signals from previous scans before performing the new scan.

Requirements:
    pip install yfinance pandas
"""

import argparse
import csv
import math
import sys
import time
from datetime import datetime
from pathlib import Path

import pandas as pd
import yfinance as yf

HERE = Path(__file__).resolve().parent
STALE_DAYS = 100
TRACK_DAYS = 5

# ---------------------------------------------------------------------------
# Dataset registry
# ---------------------------------------------------------------------------
DATASETS = {
    "xu100": {
        "tickers": HERE / "xu100.csv",
        "signals": HERE / "mr_signals_xu100.csv",
        "outcomes": HERE / "mr_outcomes_xu100.csv",
        "label": "BIST 100",
    },
    "xu500": {
        "tickers": HERE / "xu500.csv",
        "signals": HERE / "mr_signals_xu500.csv",
        "outcomes": HERE / "mr_outcomes_xu500.csv",
        "label": "BIST 500",
    },
}

# Signal log columns (written during scan)
#
# New orthogonal features (raw values only — classification stays in the
# analysis layer, never the scanner):
#   stale         oversold-duration proxy = |gap50| - |gap20|
#                 (fresh dip: ~0/negative; stale/old dip: positive, as EMA20
#                  catches price). Scan-time, from the row's own gaps.
#   mr_score      stock-specific mean-reversion tendency: mean of this ticker's
#                 RESOLVED BELOW outcomes (enter d1 open -> exit d5 close).
#                 Live-safe: only resolved (d5-filled) prior signals feed it, so
#                 today's just-created row never sees its own future.
#   mr_score_exc  same but on each prior signal's excess over that day's
#                 universe-mean BELOW return (removes market drift).
#   mr_n          number of prior resolved signals behind the score (confidence).
SIGNAL_COLUMNS = [
    "scan_date", "signal_date", "ticker",
    "close", "ema20", "ema50", "gap20_pct", "gap50_pct", "stale",
    "atr20_pct", "gap20_atr", "gap50_atr",
    "ema20_slope", "ema50_slope", "pre_momentum_5d",
    "triggers", "position", "vol_ratio", "rsi14",
    "above_count", "below_count",
    "mr_score", "mr_score_exc", "mr_n",
    "source_index",
]

# Outcome columns (written during scan, filled in during --update)
OUTCOME_COLUMNS = [
    "signal_date", "ticker", "trigger", "position", "signal_close",
    "atr20_pct", "gap20_atr", "gap50_atr",
    "ema20_slope", "ema50_slope", "pre_momentum_5d",
    "above_count", "below_count",
    "d1_open", "d1_close", "d1_pct",
    "d2_pct", "d3_pct", "d4_pct", "d5_pct",
    "d1_high_pct", "d1_low_pct",
    "d2_high_pct", "d2_low_pct",
    "d3_high_pct", "d3_low_pct",
    "d4_high_pct", "d4_low_pct",
    "d5_high_pct", "d5_low_pct",
    "d1_vol_ratio", "d2_vol_ratio", "d3_vol_ratio",
    "d4_vol_ratio", "d5_vol_ratio",
    "max_5d_close", "max_5d_pct", "min_5d_close", "min_5d_pct",
    "xu100_open", "xu100_close", "xu100_d1_open", "xu100_d1_close",
    "at_limit",
]

XU100_SYMBOL = "XU100.IS"
LIMIT_PCT = 9.5  # BIST daily price limit (approximate)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def load_tickers(ds: dict) -> list[str]:
    """Return list of Yahoo Finance symbols from the dataset's CSV."""
    path = ds["tickers"]
    if not path.exists():
        sys.exit(
            f"Ticker file not found: {path}\n"
            f"Place your CSV there with columns: ticker, yf_symbol"
        )
    age_days = (time.time() - path.stat().st_mtime) / 86400
    if age_days > STALE_DAYS:
        print(
            f"  Warning: {path.name} is {int(age_days)} days old — consider refreshing.",
            file=sys.stderr,
        )
    df = pd.read_csv(path)
    col = "yf_symbol" if "yf_symbol" in df.columns else df.columns[-1]
    symbols = df[col].dropna().str.strip().tolist()
    return [s for s in symbols if s]


def compute_rsi(series: pd.Series, period: int = 14) -> float:
    """Return latest RSI value for a price series."""
    delta = series.diff()
    gain = delta.clip(lower=0)
    loss = -delta.clip(upper=0)
    avg_gain = gain.ewm(alpha=1 / period, min_periods=period).mean()
    avg_loss = loss.ewm(alpha=1 / period, min_periods=period).mean()
    rs = avg_gain / avg_loss
    rsi = 100 - (100 / (1 + rs))
    return round(rsi.iloc[-1], 1) if not rsi.empty else float("nan")


def pct_change(ref: float, val) -> str:
    """Return % change from ref as a rounded string, or '' if val is missing."""
    if val is None or (isinstance(val, float) and math.isnan(val)):
        return ""
    return str(round((val - ref) / ref * 100, 4))


def fetch_xu100_bar(date_str: str) -> dict:
    """Fetch XU100 open/close for a given date. Returns dict with keys or empty."""
    try:
        tk = yf.Ticker(XU100_SYMBOL)
        start = pd.Timestamp(date_str)
        end = start + pd.Timedelta(days=5)
        hist = tk.history(start=start, end=end, auto_adjust=True)
        if hist.empty:
            return {}
        hist.index = hist.index.tz_localize(None)
        row = hist.iloc[0]
        return {"open": round(row["Open"], 4), "close": round(row["Close"], 4)}
    except Exception:
        return {}


# ---------------------------------------------------------------------------
# Scan logic
# ---------------------------------------------------------------------------

def scan_ticker(symbol: str, target_date: str | None, gap20: float, gap50: float):
    """Scan one ticker for mean-reversion signals."""
    try:
        tk = yf.Ticker(symbol)
        hist = tk.history(period="6mo", auto_adjust=True)
    except Exception:
        return None

    if hist.empty or len(hist) < 50:
        return None

    hist.index = hist.index.tz_localize(None)

    if target_date:
        td = pd.Timestamp(target_date)
        hist = hist[hist.index <= td]
        if hist.empty or len(hist) < 50:
            return None

    hist["EMA20"] = hist["Close"].ewm(span=20, adjust=False).mean()
    hist["EMA50"] = hist["Close"].ewm(span=50, adjust=False).mean()

    today = hist.iloc[-1]
    close = today["Close"]
    ema20 = today["EMA20"]
    ema50 = today["EMA50"]
    volume = today["Volume"]

    gap20_pct = (close - ema20) / ema20 * 100
    gap50_pct = (close - ema50) / ema50 * 100

    # ATR20: Average True Range as % of close
    prev_close = hist["Close"].shift(1)
    tr = pd.concat([
        hist["High"] - hist["Low"],
        (hist["High"] - prev_close).abs(),
        (hist["Low"] - prev_close).abs(),
    ], axis=1).max(axis=1)
    atr20 = tr.tail(20).mean()
    atr20_pct = atr20 / close * 100 if close > 0 else float("nan")

    # Gap normalized by ATR (how many daily ATRs the gap represents)
    gap20_atr = gap20_pct / atr20_pct if atr20_pct > 0 else float("nan")
    gap50_atr = gap50_pct / atr20_pct if atr20_pct > 0 else float("nan")

    # EMA slopes: 5-day % change of the EMA itself
    ema20_5ago = hist["EMA20"].iloc[-6] if len(hist) >= 6 else hist["EMA20"].iloc[0]
    ema50_5ago = hist["EMA50"].iloc[-6] if len(hist) >= 6 else hist["EMA50"].iloc[0]
    ema20_slope = (ema20 - ema20_5ago) / ema20_5ago * 100
    ema50_slope = (ema50 - ema50_5ago) / ema50_5ago * 100

    # Pre-signal momentum: 5-day price change ending on signal day
    close_5ago = hist["Close"].iloc[-6] if len(hist) >= 6 else hist["Close"].iloc[0]
    pre_momentum_5d = (close - close_5ago) / close_5ago * 100

    if close > ema20 and close > ema50:
        position = "Above both"
    elif close < ema20 and close < ema50:
        position = "Below both"
    elif ema20 >= ema50:
        position = "Between (20>50)"
    else:
        position = "Between (50>20)"

    triggers = []
    if gap20_pct >= gap20:
        triggers.append("ABOVE_20")
    if gap20_pct <= -gap20:
        triggers.append("BELOW_20")
    if gap50_pct >= gap50:
        triggers.append("ABOVE_50")
    if gap50_pct <= -gap50:
        triggers.append("BELOW_50")

    if not triggers:
        return None

    avg_vol = hist["Volume"].tail(20).mean()
    vol_ratio = volume / avg_vol if avg_vol > 0 else float("nan")
    rsi14 = compute_rsi(hist["Close"])
    signal_date = hist.index[-1].strftime("%Y-%m-%d")

    # Oversold-duration proxy: |gap50| - |gap20|.
    # Fresh drop -> the two gaps are similar (~0 / negative); prolonged drop ->
    # the fast EMA20 has caught up to price, so |gap50| >> |gap20| (positive).
    stale = abs(gap50_pct) - abs(gap20_pct)

    return {
        "signal_date": signal_date,
        "ticker": symbol.replace(".IS", ""),
        "close": round(close, 2),
        "ema20": round(ema20, 2),
        "ema50": round(ema50, 2),
        "gap20_pct": round(gap20_pct, 2),
        "gap50_pct": round(gap50_pct, 2),
        "stale": round(stale, 2),
        "atr20_pct": round(atr20_pct, 2),
        "gap20_atr": round(gap20_atr, 2),
        "gap50_atr": round(gap50_atr, 2),
        "ema20_slope": round(ema20_slope, 2),
        "ema50_slope": round(ema50_slope, 2),
        "pre_momentum_5d": round(pre_momentum_5d, 2),
        "triggers": "+".join(triggers),
        "position": position,
        "vol_ratio": round(vol_ratio, 2),
        "rsi14": rsi14,
    }


# ---------------------------------------------------------------------------
# Logging (signal log + outcomes skeleton)
# ---------------------------------------------------------------------------

def _existing_keys(path: Path, key_cols: list[str]) -> set[tuple]:
    """Read a CSV and return a set of (col1, col2, ...) tuples for dedup."""
    if not path.exists():
        return set()
    try:
        df = pd.read_csv(path, usecols=key_cols, dtype=str, keep_default_na=False)
        return set(df.itertuples(index=False, name=None))
    except Exception:
        return set()


def _migrate_log_schema(path: Path, expected_columns: list[str]):
    """Ensure a CSV file has all expected columns, adding missing ones."""
    if not path.exists():
        return
    df = pd.read_csv(path, dtype=str, keep_default_na=False)
    changed = False
    for col in expected_columns:
        if col not in df.columns:
            df[col] = ""
            changed = True
    # Remove columns not in expected set
    extra = [c for c in df.columns if c not in expected_columns]
    if extra:
        df = df.drop(columns=extra)
        changed = True
    if changed:
        df = df[expected_columns]  # enforce column order
        df.to_csv(path, index=False)
        added = [c for c in expected_columns if c not in pd.read_csv(path, nrows=0).columns] if not changed else []
        print(f"  Schema migrated: {path.name}")


def append_to_signal_log(signals: list[dict], log_path: Path, index_label: str):
    """Append signal rows to the signal log CSV (skips duplicates)."""
    existing = _existing_keys(log_path, ["signal_date", "ticker"])
    write_header = not log_path.exists()
    scan_ts = datetime.now().strftime("%Y-%m-%d %H:%M")

    added = 0
    with open(log_path, "a", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=SIGNAL_COLUMNS)
        if write_header:
            writer.writeheader()
        for s in signals:
            key = (s["signal_date"], s["ticker"])
            if key in existing:
                continue
            existing.add(key)
            row = {"scan_date": scan_ts, "source_index": index_label}
            row.update(s)
            writer.writerow(row)
            added += 1
    return added


def append_to_outcomes(signals: list[dict], outcomes_path: Path, xu100_bar: dict):
    """Append skeleton outcome rows for new signals (skips duplicates)."""
    existing = _existing_keys(outcomes_path, ["signal_date", "ticker"])
    write_header = not outcomes_path.exists()

    added = 0
    with open(outcomes_path, "a", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=OUTCOME_COLUMNS)
        if write_header:
            writer.writeheader()
        for s in signals:
            ticker_is = s["ticker"] if s["ticker"].endswith(".IS") else s["ticker"] + ".IS"
            key = (s["signal_date"], ticker_is)
            if key in existing:
                continue
            existing.add(key)
            row = {c: "" for c in OUTCOME_COLUMNS}
            row["signal_date"] = s["signal_date"]
            row["ticker"] = ticker_is
            row["trigger"] = s["triggers"]
            row["position"] = s["position"]
            row["signal_close"] = s["close"]
            row["atr20_pct"] = s["atr20_pct"]
            row["gap20_atr"] = s["gap20_atr"]
            row["gap50_atr"] = s["gap50_atr"]
            row["ema20_slope"] = s["ema20_slope"]
            row["ema50_slope"] = s["ema50_slope"]
            row["pre_momentum_5d"] = s["pre_momentum_5d"]
            row["above_count"] = s["above_count"]
            row["below_count"] = s["below_count"]
            row["xu100_open"] = xu100_bar.get("open", "")
            row["xu100_close"] = xu100_bar.get("close", "")
            writer.writerow(row)
            added += 1
    return added


def current_mr_scores(datasets: dict) -> dict:
    """Per-ticker stock-specific mean-reversion score from RESOLVED outcomes.

    Pools every outcomes file present (a stock's bounce tendency is index-
    independent, so XU100 names also get their XU500-scan history), keeps only
    BELOW signals whose d5 is filled (resolved), and returns:

        { bare_ticker: {"mr_score": .., "mr_score_exc": .., "mr_n": ..} }

    A new signal scanned today is scored purely from prior resolved history —
    today's just-created row has no d5 yet, so it is excluded automatically and
    there is no look-ahead. Tickers with no resolved history simply won't appear
    in the dict (caller assigns blank / mr_n=0).
    """
    frames = []
    for ds in datasets.values():
        p = ds["outcomes"]
        if p.exists():
            try:
                frames.append(pd.read_csv(p, dtype=str, keep_default_na=False))
            except Exception:
                continue
    if not frames:
        return {}

    df = pd.concat(frames, ignore_index=True)
    df = df.drop_duplicates(["signal_date", "ticker"], keep="first")
    # BELOW signals that have resolved (d5 filled)
    df = df[df["trigger"].str.contains("BELOW") & (df["d5_pct"] != "")]
    if df.empty:
        return {}

    def _ret_d5(r):
        try:
            sc = float(r["signal_close"]); op = float(r["d1_open"]); d5 = float(r["d5_pct"])
            if op <= 0:
                return None
            return (sc * (1.0 + d5 / 100.0) - op) / op * 100.0
        except (ValueError, ZeroDivisionError):
            return None

    df["ret_d5"] = df.apply(_ret_d5, axis=1)
    df = df[df["ret_d5"].notna()]
    if df.empty:
        return {}

    df["tkr"] = df["ticker"].str.replace(".IS", "", regex=False)
    df["day_mean"] = df.groupby("signal_date")["ret_d5"].transform("mean")
    df["excess"] = df["ret_d5"] - df["day_mean"]

    scores = {}
    for tkr, g in df.groupby("tkr"):
        scores[tkr] = {
            "mr_score": round(g["ret_d5"].mean(), 2),
            "mr_score_exc": round(g["excess"].mean(), 2),
            "mr_n": int(len(g)),
        }
    return scores


# ---------------------------------------------------------------------------
# Outcome updater
# ---------------------------------------------------------------------------

def update_outcomes(outcomes_path: Path):
    """Fill in d1-d5 data for incomplete outcome rows."""
    if not outcomes_path.exists():
        print(f"  No outcomes file found at {outcomes_path.name}")
        return

    df = pd.read_csv(outcomes_path, dtype=str, keep_default_na=False)
    if df.empty:
        print("  Outcomes file is empty.")
        return

    updated = 0
    total = len(df)

    # Cache fetched histories to avoid duplicate downloads
    hist_cache: dict[str, pd.DataFrame] = {}
    xu100_cache: dict[str, dict] = {}

    for idx, row in df.iterrows():
        signal_date = row["signal_date"]
        ticker = row["ticker"]
        signal_close_str = row["signal_close"]

        if not signal_close_str:
            continue
        signal_close = float(signal_close_str)

        # Check how many days are already filled
        filled = 0
        for d in range(1, TRACK_DAYS + 1):
            if row.get(f"d{d}_pct", ""):
                filled = d
            else:
                break

        if filled >= TRACK_DAYS:
            continue  # fully tracked already

        # Fetch history if not cached
        if ticker not in hist_cache:
            print(f"\r  Fetching {ticker}...{' '*20}", end="", flush=True)
            try:
                tk = yf.Ticker(ticker)
                hist = tk.history(period="3mo", auto_adjust=True)
                if not hist.empty:
                    hist.index = hist.index.tz_localize(None)
                hist_cache[ticker] = hist
            except Exception:
                hist_cache[ticker] = pd.DataFrame()

        hist = hist_cache[ticker]
        if hist.empty:
            continue

        sig_ts = pd.Timestamp(signal_date)
        # Get bars AFTER signal date
        future = hist[hist.index > sig_ts]
        if future.empty:
            continue

        available_days = len(future)
        if available_days <= filled:
            continue  # no new data since last update

        # 20-day avg volume ending on signal date
        prior = hist[hist.index <= sig_ts]
        avg_vol = prior["Volume"].tail(20).mean() if len(prior) >= 1 else 1.0

        # Fill day-by-day
        for d in range(1, TRACK_DAYS + 1):
            if d > available_days:
                break
            if row.get(f"d{d}_pct", "") and d > 1:
                continue  # already filled

            bar = future.iloc[d - 1]
            close_d = bar["Close"]
            high_d = bar["High"]
            low_d = bar["Low"]
            vol_d = bar["Volume"]

            df.at[idx, f"d{d}_pct"] = pct_change(signal_close, close_d)
            df.at[idx, f"d{d}_high_pct"] = pct_change(signal_close, high_d)
            df.at[idx, f"d{d}_low_pct"] = pct_change(signal_close, low_d)
            df.at[idx, f"d{d}_vol_ratio"] = str(round(vol_d / avg_vol, 4)) if avg_vol > 0 else ""

            if d == 1:
                df.at[idx, "d1_open"] = str(round(bar["Open"], 4))
                df.at[idx, "d1_close"] = str(round(close_d, 4))

        # Max / min 5d close
        n_days = min(TRACK_DAYS, available_days)
        closes_5d = [future.iloc[d]["Close"] for d in range(n_days)]
        if closes_5d:
            max_c = max(closes_5d)
            min_c = min(closes_5d)
            df.at[idx, "max_5d_close"] = str(round(max_c, 4))
            df.at[idx, "max_5d_pct"] = pct_change(signal_close, max_c)
            df.at[idx, "min_5d_close"] = str(round(min_c, 4))
            df.at[idx, "min_5d_pct"] = pct_change(signal_close, min_c)

        # XU100 d1 bar
        if not row.get("xu100_d1_open", "") and available_days >= 1:
            d1_date = future.index[0].strftime("%Y-%m-%d")
            if d1_date not in xu100_cache:
                xu100_cache[d1_date] = fetch_xu100_bar(d1_date)
            xu_bar = xu100_cache[d1_date]
            if xu_bar:
                df.at[idx, "xu100_d1_open"] = str(xu_bar.get("open", ""))
                df.at[idx, "xu100_d1_close"] = str(xu_bar.get("close", ""))

        # at_limit: did signal-day close hit approx. ±10% from previous close?
        if not row.get("at_limit", ""):
            prior_closes = hist[hist.index < sig_ts]["Close"]
            if len(prior_closes) >= 1:
                prev_close = prior_closes.iloc[-1]
                day_pct = abs((signal_close - prev_close) / prev_close * 100)
                df.at[idx, "at_limit"] = "T" if day_pct >= LIMIT_PCT else "F"

        updated += 1

    # Write back
    df.to_csv(outcomes_path, index=False)
    print(f"\r  Updated {updated}/{total} outcome rows in {outcomes_path.name}{' '*20}")


# ---------------------------------------------------------------------------
# Console output
# ---------------------------------------------------------------------------

def print_table(signals: list[dict], direction: str, label: str, threshold_info: str):
    """Pretty-print a table of signals."""
    if not signals:
        return

    print(f"\n{'='*130}")
    print(f"  {label}  |  {direction}")
    print(f"  Thresholds: {threshold_info}")
    above_c = signals[0].get("above_count", "")
    below_c = signals[0].get("below_count", "")
    if above_c != "":
        print(f"  Signal density: ABOVE {above_c} / BELOW {below_c} / Total {above_c + below_c}")
    print(f"{'='*130}")
    print(
        f"  {'Ticker':<10} {'Close':>10} {'Gap20%':>8} {'Gap50%':>8} {'ATR%':>6} {'G20a':>6} {'G50a':>6}"
        f" {'E20sl':>6} {'Pre5d':>7} {'VolR':>6} {'RSI':>6} {'Stale':>6} {'MRsc':>6} {'MRn':>4}  {'Position':<16} Triggers"
    )
    print(
        f"  {'-'*10} {'-'*10} {'-'*8} {'-'*8} {'-'*6} {'-'*6} {'-'*6}"
        f" {'-'*6} {'-'*7} {'-'*6} {'-'*6} {'-'*6} {'-'*6} {'-'*4}  {'-'*16} {'-'*20}"
    )
    for s in signals:
        mr = s.get("mr_score", "")
        mrsc_disp = f"{mr:+.2f}" if isinstance(mr, (int, float)) else "–"
        mrn = s.get("mr_n", 0)
        mrn_disp = str(mrn) if mrn else "–"
        print(
            f"  {s['ticker']:<10} {s['close']:>10.2f} {s['gap20_pct']:>+8.2f} {s['gap50_pct']:>+8.2f}"
            f" {s['atr20_pct']:>6.2f} {s['gap20_atr']:>+6.1f} {s['gap50_atr']:>+6.1f}"
            f" {s['ema20_slope']:>+6.2f} {s['pre_momentum_5d']:>+7.2f} {s['vol_ratio']:>6.2f} {s['rsi14']:>6.1f}"
            f" {s['stale']:>+6.2f} {mrsc_disp:>6} {mrn_disp:>4}"
            f"  {s['position']:<16} {s['triggers']}"
        )
    print()


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    ap = argparse.ArgumentParser(
        description="BIST Mean Reversion Scanner — find EMA-stretched stocks",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    ap.add_argument(
        "-i", "--index", choices=["xu100", "xu500"], default="xu100",
        help="Which index to scan (default: xu100)",
    )
    ap.add_argument(
        "-g", "--gap20", type=float, default=5.0,
        help="Threshold gap%% for EMA20 (default: 5.0)",
    )
    ap.add_argument(
        "-G", "--gap50", type=float, default=8.0,
        help="Threshold gap%% for EMA50 (default: 8.0)",
    )
    ap.add_argument(
        "-d", "--date", type=str, default=None,
        help="Scan a specific session date (YYYY-MM-DD)",
    )
    ap.add_argument(
        "-n", "--no-log", action="store_true",
        help="Don't append results to signal log or outcomes CSV",
    )
    args = ap.parse_args()

    ds_key = args.index
    ds = DATASETS[ds_key]

    # Migrate CSV schemas if columns were added/removed
    _migrate_log_schema(ds["signals"], SIGNAL_COLUMNS)
    _migrate_log_schema(ds["outcomes"], OUTCOME_COLUMNS)

    # Auto-update outcomes from previous scans before doing anything else
    if ds["outcomes"].exists() and not args.no_log:
        print(f"\n  Updating outcomes for prior {ds['label']} signals...")
        update_outcomes(ds["outcomes"])

    # --- Scan ---
    scan_date_str = args.date or "latest session"

    print(f"\n  BIST Mean Reversion Scanner")
    print(f"  {'─'*40}")
    print(f"  Index:           {ds['label']}")
    print(f"  Date:            {scan_date_str}")
    print(f"  EMA20 threshold: +/-{args.gap20}%")
    print(f"  EMA50 threshold: +/-{args.gap50}%")
    print(f"\n{'─'*60}")
    print(f"  Scanning {ds['label']}...")

    symbols = load_tickers(ds)
    print(f"  Loaded {len(symbols)} tickers from {ds['tickers'].name}")

    all_signals = []

    for i, sym in enumerate(symbols, 1):
        pct = i / len(symbols) * 100
        print(
            f"\r  [{i}/{len(symbols)}] {pct:5.1f}%  {sym:<12}",
            end="", flush=True,
        )
        result = scan_ticker(sym, args.date, args.gap20, args.gap50)
        if result:
            all_signals.append(result)

    print(
        f"\r  Scanned {len(symbols)} tickers — "
        f"{len(all_signals)} signal(s) found.{' '*20}"
    )

    if not all_signals:
        print(f"\n  No mean-reversion signals in {ds['label']} today.")
        print(f"  Try lowering: --gap20 3 --gap50 5\n")
        return

    # Split into stretched-above and stretched-below
    above = [
        s for s in all_signals
        if any(t.startswith("ABOVE") for t in s["triggers"].split("+"))
    ]
    below = [
        s for s in all_signals
        if any(t.startswith("BELOW") for t in s["triggers"].split("+"))
    ]

    # Signal clustering: add counts to each signal
    above_count = len(above)
    below_count = len(below)
    for s in all_signals:
        s["above_count"] = above_count
        s["below_count"] = below_count

    # Stock-specific mean-reversion score from prior RESOLVED outcomes.
    # Pools both indices' outcomes; today's unresolved rows are excluded, so the
    # lookup is look-ahead-free. New / rare tickers get blank score, mr_n=0.
    mr_scores = current_mr_scores(DATASETS)
    for s in all_signals:
        sc = mr_scores.get(s["ticker"])
        if sc:
            s["mr_score"] = sc["mr_score"]
            s["mr_score_exc"] = sc["mr_score_exc"]
            s["mr_n"] = sc["mr_n"]
        else:
            s["mr_score"] = ""
            s["mr_score_exc"] = ""
            s["mr_n"] = 0

    above.sort(key=lambda s: -max(abs(s["gap20_pct"]), abs(s["gap50_pct"])))
    below.sort(key=lambda s: -max(abs(s["gap20_pct"]), abs(s["gap50_pct"])))

    threshold_info = f"EMA20 >= {args.gap20}%, EMA50 >= {args.gap50}%"

    print_table(
        above,
        "STRETCHED ABOVE EMAs (overbought / pullback candidates)",
        ds["label"], threshold_info,
    )
    print_table(
        below,
        "STRETCHED BELOW EMAs (oversold / bounce candidates)",
        ds["label"], threshold_info,
    )

    # Log signals + create outcome skeletons
    if not args.no_log:
        added_sig = append_to_signal_log(all_signals, ds["signals"], ds_key)
        print(f"  Logged {added_sig} new signal(s) to {ds['signals'].name}"
              + (f" ({len(all_signals) - added_sig} already existed)" if added_sig < len(all_signals) else ""))

        signal_date = all_signals[0]["signal_date"]
        xu100_bar = fetch_xu100_bar(signal_date)
        added_out = append_to_outcomes(all_signals, ds["outcomes"], xu100_bar)
        print(f"  Created {added_out} new outcome row(s) in {ds['outcomes'].name}"
              + (f" ({len(all_signals) - added_out} already existed)" if added_out < len(all_signals) else ""))

    print(f"\n{'─'*60}")
    print(f"  Done. {len(all_signals)} signal(s) in {ds['label']}.\n")


if __name__ == "__main__":
    main()
