# BIST Mean Reversion Scanner

A Python-based scanner that detects mean-reversion setups in Borsa İstanbul (BIST) stocks by identifying prices stretched beyond configurable thresholds from their EMA20 and EMA50, and tracks outcomes over the following 5 trading days.

## How It Works

The scanner computes EMA20 and EMA50 for each stock in the BIST100 or BIST500 index and calculates the percentage gap between the closing price and each EMA:

```
gap% = (close − EMA) / EMA × 100
```

A signal fires when the gap exceeds a configurable threshold:

| Signal | Meaning |
|---|---|
| `ABOVE_20` | Close is ≥ threshold% above EMA20 |
| `ABOVE_50` | Close is ≥ threshold% above EMA50 |
| `BELOW_20` | Close is ≥ threshold% below EMA20 |
| `BELOW_50` | Close is ≥ threshold% below EMA50 |

A stock can trigger multiple signals simultaneously (e.g. `BELOW_20+BELOW_50`).

Default thresholds: **5%** for EMA20, **8%** for EMA50.

## Features per Signal

Each signal captures a rich set of features for analysis:

| Feature | Description |
|---|---|
| `gap20_pct`, `gap50_pct` | Percentage gap from EMA20 and EMA50 |
| `atr20_pct` | 20-day ATR as % of close (stock's daily volatility) |
| `gap20_atr`, `gap50_atr` | Gap normalized by ATR (gap in volatility units) |
| `ema20_slope`, `ema50_slope` | 5-day % change of the EMA itself (trend direction) |
| `pre_momentum_5d` | 5-day cumulative return before the signal (speed of decline/rise) |
| `vol_ratio` | Signal-day volume / 20-day average volume |
| `rsi14` | 14-period RSI |
| `position` | Price location: Above both, Below both, Between |
| `above_count`, `below_count` | Signal clustering — total ABOVE/BELOW signals on the same day |

## Outcome Tracking

Each run automatically updates outcome data for all prior signals before scanning for new ones. For each signal, the scanner tracks 5 trading days of outcomes:

- **d1–d5**: Daily close % change from signal close
- **d1–d5 high/low %**: Intraday extremes relative to signal close
- **d1–d5 volume ratio**: Daily volume vs 20-day average
- **max/min 5d**: Best and worst close within the 5-day window
- **XU100 context**: Index open/close on signal day and d1
- **at_limit**: Whether the signal-day close hit the BIST ±10% daily price limit

## Installation

```bash
git clone https://github.com/bkadirbeyoglu/bist-mean-reversion-scanner.git
cd bist-mean-reversion-scanner
pip install yfinance pandas
```

## Usage

### Scanning

```bash
# Scan BIST100 with default thresholds (EMA20: 5%, EMA50: 8%)
python bist_mean_reversion_scanner.py

# Scan BIST500
python bist_mean_reversion_scanner.py -i xu500

# Custom thresholds
python bist_mean_reversion_scanner.py -g 4 -G 7

# Scan a specific past session
python bist_mean_reversion_scanner.py -d 2026-06-01

# Scan without logging (console output only)
python bist_mean_reversion_scanner.py -n
```

### Arguments

| Short | Long | Default | Description |
|---|---|---|---|
| `-i` | `--index` | `xu100` | Index to scan: `xu100` or `xu500` |
| `-g` | `--gap20` | `5.0` | Gap threshold % for EMA20 |
| `-G` | `--gap50` | `8.0` | Gap threshold % for EMA50 |
| `-d` | `--date` | latest | Scan a specific session (YYYY-MM-DD) |
| `-n` | `--no-log` | off | Skip logging to CSV files |

### Updating Index Constituents

```bash
# Update BIST100 tickers from KAP
python update_index.py

# Update BIST500 tickers
python update_index.py -i xu500

# Use Midas as fallback source
python update_index.py -i xu100 -s midas
```

## Output Files

| File | Description |
|---|---|
| `mr_signals_xu100.csv` | Signal log with all features |
| `mr_outcomes_xu100.csv` | Outcome tracking with d1–d5 data |
| `mr_signals_xu500.csv` | Same for BIST500 |
| `mr_outcomes_xu500.csv` | Same for BIST500 |

## Recommended Workflow

1. Run the scanner each evening after market close (BIST data is typically available on yfinance ~3–3.5 hours after close, around 21:30 Istanbul time).
2. Review BELOW signals with gap ≥ 8%.
3. Next trading day: observe d1 close. If d1 > +1%, consider entry.
4. Hold for up to 5 trading days.
5. The scanner auto-updates prior outcomes on each run — no manual tracking needed.

## Data Sources

- **yfinance**: Stock price data (OHLCV), ~15-minute intraday delay, end-of-day data available ~3 hours after close
- **KAP** (kap.org.tr): BIST index constituents (primary source for `update_index.py`)
- **Midas** (getmidas.com): Fallback source for index constituents

## Requirements

- Python 3.10+
- `yfinance`
- `pandas`

## License

MIT

## Disclaimer

This tool is for research and educational purposes only. It does not constitute financial advice. Past performance does not guarantee future results. Always do your own research before making investment decisions.
