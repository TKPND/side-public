"""Generate per_trade_log.parquet by invoking the Rust dump_per_trade_log bin.

Phase 86 EXIT-01 D-21 / Phase 87 Task 0-1: Rust CLI path wired (additive only).
Output: data/v4.9/per_trade_log.parquet (D-13).

Schema (polars):
    cell_id      : Utf8      # w{window_offset}_h{hold_bars}_{exit_type}  (Phase 87)
    fold         : Int64     # OOS fold id
    trade_id     : UInt64
    entry_bar    : Int64
    entry_price  : Float64
    direction    : Int8      # +1 long, -1 short
    atr_at_entry : Float64
    bars         : List[Struct{high: Float64, low: Float64, close: Float64, atr: Float64}]

CLI::

    uv run python scripts/v4.9/gen_per_trade_log.py \\
        --data-path <csv or parquet of OHLCV> \\
        --output-path data/v4.9/per_trade_log.parquet \\
        [--pair USDJPY] [--smoke]
"""

from __future__ import annotations

import argparse
import json
import pathlib
import subprocess
import sys

import polars as pl

BARS_STRUCT_SCHEMA = pl.Struct(
    {
        "high": pl.Float64,
        "low": pl.Float64,
        "close": pl.Float64,
        "atr": pl.Float64,
    }
)

PER_TRADE_LOG_SCHEMA = {
    "cell_id": pl.Utf8,
    "fold": pl.Int64,
    "trade_id": pl.UInt64,
    "entry_bar": pl.Int64,
    "entry_price": pl.Float64,
    "direction": pl.Int8,
    "atr_at_entry": pl.Float64,
    "bars": pl.List(BARS_STRUCT_SCHEMA),
}


def jsonl_to_rows(jsonl_path: pathlib.Path) -> list[dict]:
    """Parse JSON Lines from dump_per_trade_log Rust bin into per-trade rows."""
    rows: list[dict] = []
    with open(jsonl_path) as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            t = json.loads(line)
            rows.append(
                {
                    "cell_id": str(t["cell_id"]),
                    "fold": int(t["fold"]),
                    "trade_id": int(t["trade_id"]),
                    "entry_bar": int(t["entry_bar"]),
                    "entry_price": float(t["entry_price"]),
                    "direction": int(t["direction"]),
                    "atr_at_entry": float(t["atr_at_entry"]),
                    "bars": [
                        {
                            "high": float(b["high"]),
                            "low": float(b["low"]),
                            "close": float(b["close"]),
                            "atr": float(b["atr"]),
                        }
                        for b in t["bars"]
                    ],
                }
            )
    return rows


def _smoke_rows() -> list[dict]:
    """Deterministic smoke fixture (1 fold, 1 trade, 2 bars).

    Matches long_trade_atr_stop_trigger fixture in test_exit_replay.py.
    cell_id uses the canonical format for slot w0_h1_none.
    """
    return [
        {
            "cell_id": "w0_h1_none",
            "fold": 0,
            "trade_id": 1,
            "entry_bar": 10,
            "entry_price": 100.0,
            "direction": 1,
            "atr_at_entry": 0.5,
            "bars": [
                {"high": 100.2, "low": 98.9, "close": 99.5, "atr": 0.5},
                {"high": 100.0, "low": 99.0, "close": 99.5, "atr": 0.5},
            ],
        }
    ]


def write_per_trade_log_parquet(rows: list[dict], output_path: pathlib.Path) -> None:
    df = pl.DataFrame(rows, schema=PER_TRADE_LOG_SCHEMA)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    df.write_parquet(
        output_path,
        metadata={
            "sizing_exit_commit": "8a4e49d2000b08e9e1b93b5f9f0de661d5dff7613d8dfc8339313452a3b81fab",
            "atr_window_bars": "14",
        },
    )


def _find_rust_bin(manifest_dir: pathlib.Path | None = None) -> pathlib.Path:
    """Locate the dump_per_trade_log Rust binary."""
    if manifest_dir is None:
        # Assume script is at scripts/v4.9/; project root is 2 levels up.
        manifest_dir = pathlib.Path(__file__).resolve().parent.parent.parent
    candidate = manifest_dir / "rust" / "target" / "debug" / "dump_per_trade_log"
    if candidate.exists():
        return candidate
    candidate_release = (
        manifest_dir / "rust" / "target" / "release" / "dump_per_trade_log"
    )
    if candidate_release.exists():
        return candidate_release
    raise FileNotFoundError(
        f"dump_per_trade_log binary not found. Run: "
        f"cargo build --bin dump_per_trade_log --manifest-path "
        f"{manifest_dir}/rust/side-engine/Cargo.toml"
    )


def generate_per_trade_log(
    data_path: pathlib.Path,
    output_path: pathlib.Path,
    pair: str = "USDJPY",
    smoke: bool = False,
) -> None:
    """Call Rust dump_per_trade_log bin and write per_trade_log.parquet."""
    if smoke:
        rows = _smoke_rows()
        write_per_trade_log_parquet(rows, output_path)
        return

    # Invoke Rust bin → JSON Lines → parquet
    rust_bin = _find_rust_bin()
    jsonl_path = output_path.with_suffix(".jsonl")

    cmd = [
        str(rust_bin),
        "--data",
        str(data_path),
        "--output",
        str(jsonl_path),
    ]
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        raise RuntimeError(
            f"dump_per_trade_log failed (exit {result.returncode}):\n{result.stderr}"
        )

    rows = jsonl_to_rows(jsonl_path)
    write_per_trade_log_parquet(rows, output_path)

    # Clean up intermediate JSONL
    jsonl_path.unlink(missing_ok=True)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Generate per_trade_log.parquet from Rust engine"
    )
    parser.add_argument(
        "--data-path",
        type=pathlib.Path,
        default=None,
        help="Path to OHLCV CSV/parquet (required for real run)",
    )
    parser.add_argument(
        "--output-path",
        required=True,
        type=pathlib.Path,
        help="Output parquet path (data/v4.9/per_trade_log.parquet)",
    )
    parser.add_argument("--pair", default="USDJPY")
    parser.add_argument(
        "--smoke",
        action="store_true",
        help="Use deterministic fixture instead of calling the engine",
    )
    args = parser.parse_args(argv)

    try:
        generate_per_trade_log(
            data_path=args.data_path or pathlib.Path("/dev/null"),
            output_path=args.output_path,
            pair=args.pair,
            smoke=args.smoke,
        )
    except NotImplementedError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2
    except Exception as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1

    print(f"Wrote per_trade_log to {args.output_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
