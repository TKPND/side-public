# Phase 97 Reference Fixtures (Parquet-only)

Git-tracked reference fixtures for bit-exact regression tests. Phase 96 の D-07 CSV lock は Phase 97 で解除され、fetch path は Parquet 一択 (scripts/lib/data_fetch.py / scripts/fetch_ticks.py)。本ディレクトリには Parquet 版のみを保持し、legacy CSV は Wave 3 で削除済 (commits: Wave 1A 1d492d5, Wave 3 [this commit])。

## Files

| File | Rows | Schema | Description |
|------|------|--------|-------------|
| `usdjpy_ticks_2024-01-08.parquet` | 159,704 | `timestamp:TIMESTAMP(ms,UTC)` + `bidPrice/askPrice/bidVolume/askVolume:FLOAT×4` | USDJPY tick, 2024-01-08 UTC, bit-exact baked Wave 0 (commit 3a63e6e) |
| `usdjpy_1h_sample.parquet` | 16 | `datetime:TIMESTAMP(ms,UTC)` + `open/high/low/close/volume:FLOAT×5` | USDJPY 1h OHLCV, 2024-01-08 UTC (market hours only) |
| `FIXTURE_HASHES.txt` | — | `<filename> <sha256hex>` | SHA-256 digests for bit-exact verification |

## Schema / Writer options (SEAL-locked)

- Writer: pyarrow, `compression=snappy`, `use_dictionary=False`, `data_page_size=1048576`, `version=2.6`
- Schema canonical definition: `.planning/phases/97-.../SEAL/parquet_schema.json` (D-08 pre-reg lite、byte-for-byte pin)
- `datetime_ns:INT64` は Phase 97 D-04 で廃棄 (TIMESTAMP(ms,UTC) 一本化)

## Bit-Exact Verification

`tests/fixtures/fetch/test_fetch_ticks_bit_exact.py` が `hashlib.sha256` で fetcher 出力と baked Parquet を比較。`expected_tick_parquet_sha256` pytest fixture が `FIXTURE_HASHES.txt` を single source of truth として読む (`conftest.py`)。

## D-07 Compliance

- Phase 96: CSV のみ (D-07 CSV lock)
- Phase 97 Wave 1A: Parquet writer 実装 + bit-exact test Parquet 化
- Phase 97 Wave 3 (本 commit): CSV fixtures / Rust csv_reader.rs / 4 BQ shell CSV 分岐 を完全撤去、CLAUDE.md「Parquet 保存のみ」方針 100% 達成

## Regenerating (immutable by policy, manual only)

Fixtures are **immutable by design** — their sha256 is locked in `FIXTURE_HASHES.txt` and their schema/writer options in `SEAL/parquet_schema.json` (D-08 pre-reg lite). Do not regenerate unless pyarrow minor version is upgraded in CI and bit-exact drift is documented in a new phase.

If regeneration is truly needed, source the inputs directly from archived Dukascopy exports and run pyarrow manually (no helper script — the Wave 0 `scripts/bake_fixtures.py` was removed in Wave 3 because its CSV source was deleted by the D-07 lock removal):

```bash
# 1. Source tick: data/bq_ticks/usdjpy_ticks_2024-01.csv (archived Dukascopy monthly)
# 2. Source 1h:   dukascopy-python fetch (INTERVAL_HOUR_1, ASK side, 2024-01-08)
# 3. Write Parquet with deterministic options:
#      compression=snappy, use_dictionary=False, data_page_size=1048576, version=2.6
#      schema per SEAL/parquet_schema.json
# 4. sha256sum the outputs and update FIXTURE_HASHES.txt + bump SEAL version
```

Cross-minor pyarrow upgrades may produce different bytes and require a new SEAL/FIXTURE_HASHES commit pair.
