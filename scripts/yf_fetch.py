"""Fetch daily OHLCV from Yahoo Finance via yfinance.

Usage: uv run --with yfinance python scripts/yf_fetch.py TICKER DAYS
Output: CSV lines to stdout (date,open,high,low,close,volume)
"""

import sys

import yfinance as yf
import datetime as dt


def main() -> None:
    if len(sys.argv) != 3:
        print(f"Usage: {sys.argv[0]} TICKER DAYS", file=sys.stderr)
        sys.exit(1)

    ticker = sys.argv[1]
    days = int(sys.argv[2])

    end = dt.datetime.now()
    start = end - dt.timedelta(days=days)
    df = yf.download(ticker, start=start, end=end, progress=False, auto_adjust=False)

    if df is None or len(df) == 0:
        return

    for idx, row in df.iterrows():
        ts = idx if not hasattr(idx, "item") else idx.item()
        d = ts.strftime("%Y-%m-%d") if hasattr(ts, "strftime") else str(ts)[:10]
        o = row["Open"].iloc[0] if hasattr(row["Open"], "iloc") else row["Open"]
        h = row["High"].iloc[0] if hasattr(row["High"], "iloc") else row["High"]
        lo = row["Low"].iloc[0] if hasattr(row["Low"], "iloc") else row["Low"]
        c = row["Close"].iloc[0] if hasattr(row["Close"], "iloc") else row["Close"]
        v = row["Volume"].iloc[0] if hasattr(row["Volume"], "iloc") else row["Volume"]
        print(f"{d},{o},{h},{lo},{c},{v}")


if __name__ == "__main__":
    main()
