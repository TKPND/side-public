"""Freeze-time fetcher for FOMC + ECB statement bodies (Phase 101 Plan 02 Task 1).

Pipeline (D-62: runtime fetch FORBIDDEN; this runs ONCE pre-SEAL per CONTEXT L109):
    1. Fetch FOMC + ECB index pages for given --years.
    2. Extract candidate statement URLs via regex (no hard-coded URL list).
    3. Download each statement body, strip nav/footer, keep paragraph text.
    4. Write --output CSV with:
         event_ts, central_bank, source_url, statement_text, fetched_at

stdlib only (urllib + re + html). Polite 1-rps throttle, configurable user-agent.

No fabrication: this script ONLY emits rows whose statement_text was downloaded
from federalreserve.gov / ecb.europa.eu in this run. Python-literal statement
bodies are a SEAL-violating regression — Task 1 fabrication_guard grep enforces.

CLI (Plan 101-05 Wave 2):
    Default invocation reproduces the 32-event freeze artifact (2022-2023, ≥30 rows).
    --years 2024 + --min-rows 4 emits the 2024-Q1 inference set used by
    emit_macro_stance_per_event.py. D-59 leak-disjoint is preserved: 2024-Q1 events
    are inference-only (model never trained on them).
"""

from __future__ import annotations

import argparse
import csv
import datetime as dt
import re
import ssl
import sys
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from html import unescape
from pathlib import Path
from typing import Iterable

USER_AGENT = "side-pre-reg-fetch/v412 (research; contact: contact@example.com)"
THROTTLE_SECONDS = 1.0
TIMEOUT_SECONDS = 30
MAX_HTTP_RETRIES = (
    1  # transient retries OK for HTTP (NOT for LLM — that is D-63 one-shot)
)

REPO_ROOT = Path(__file__).resolve().parents[2]
OUTPUT_CSV = REPO_ROOT / "data" / "v4.12" / "labels" / "macro_statements_raw.csv"
DEFAULT_YEARS = (2022, 2023)

# FOMC press-release statement filename pattern: monetary{YYYYMMDD}a.htm
# (a1.htm is the Board implementation note for IORB / primary credit rate — NOT the FOMC statement.
#  Empirical 2026-04-26: a.htm = 82417 bytes w/ policy kw; a1.htm = 79506 bytes w/o policy kw; .htm = 404.)
# Fed only moves years to "historical" archive after ~5 years; 2022/2023 still live on rolling fomccalendars.htm.
FOMC_STATEMENT_RE = re.compile(r"/newsevents/pressreleases/monetary(\d{8})a\.htm")
FOMC_INDEX_URLS = ("https://www.federalreserve.gov/monetarypolicy/fomccalendars.htm",)
# ECB monetary-policy decisions live under /press/pr/date/{YYYY}/html/ecb.mp{YYMMDD}~{hash}.en.html.
# We require BOTH the URL pattern AND the anchor text "Monetary policy decisions" so that
# unrelated press releases (macroprudential / supervisory / general statements) are excluded.
# Anchor block extractor: <a href="...ecb.mpYYMMDD~hash.en.html"...>Monetary policy decisions</a>


def ecb_index_urls(years: tuple[int, ...]) -> tuple[str, ...]:
    """ECB Governing-Council monetary-policy index URLs for the requested years.

    Uses /press/govcdec/mopo/{YYYY}/ which lists exactly the 8 GC monetary-policy
    decisions per year, with anchor text "Monetary policy decisions" (passes
    ECB_MP_TITLE_RE filter). Empirical 2026-04-26: works for 2022, 2023, 2024
    (8/8 hits each); the older /press/pr/date/{yr}/ index_include returns a
    1 KB stub for 2024 (0 mp links), so the date-bucket index is no longer
    a complete source. Statement bodies still live at /press/pr/date/{yr}/...
    so existing 2022-2023 freeze CSV stays bit-identical (only the index page
    changed; statement HTML did not).
    """
    return tuple(
        f"https://www.ecb.europa.eu/press/govcdec/mopo/{yr}/html/index_include.en.html"
        for yr in years
    )


ECB_ANCHOR_RE = re.compile(
    r'<a[^>]+href="(/press/pr/date/(\d{4})/html/ecb\.mp(\d{6})[~_][a-f0-9]+\.en\.html)"[^>]*>([^<]{0,200})</a>',
    re.IGNORECASE,
)
ECB_MP_TITLE_RE = re.compile(r"monetary policy decision", re.IGNORECASE)


@dataclass(frozen=True)
class Statement:
    event_ts: str  # ISO-8601 UTC
    central_bank: str  # FOMC | ECB
    source_url: str
    statement_text: str
    fetched_at: str


class FetchError(RuntimeError):
    pass


def http_get(url: str) -> str:
    """GET with polite UA, 1-rps throttle, single transient retry."""
    last_err: Exception | None = None
    for attempt in range(MAX_HTTP_RETRIES + 1):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
            ctx = ssl.create_default_context()
            with urllib.request.urlopen(
                req, timeout=TIMEOUT_SECONDS, context=ctx
            ) as resp:
                if resp.status != 200:
                    raise FetchError(f"HTTP {resp.status} for {url}")
                body = resp.read().decode("utf-8", errors="replace")
            time.sleep(THROTTLE_SECONDS)
            return body
        except (urllib.error.URLError, urllib.error.HTTPError, FetchError) as e:
            last_err = e
            if attempt < MAX_HTTP_RETRIES:
                time.sleep(THROTTLE_SECONDS * 2)
                continue
            raise FetchError(
                f"GET failed after {attempt + 1} attempts: {url} -> {e}"
            ) from e
    assert last_err is not None
    raise FetchError(f"unreachable: {last_err}")


def strip_html_to_paragraphs(html: str) -> str:
    """Extract <p>..</p> bodies, drop tags, decode entities, join with \\n\\n.

    Defensive: also drop common nav/footer noise by filtering out paragraphs
    shorter than 30 chars (typical nav links / breadcrumbs).
    """
    paragraphs = re.findall(r"<p[^>]*>(.*?)</p>", html, flags=re.DOTALL | re.IGNORECASE)
    cleaned: list[str] = []
    for raw in paragraphs:
        text = re.sub(r"<[^>]+>", "", raw)
        text = unescape(text).strip()
        text = re.sub(r"\s+", " ", text)
        if len(text) >= 30:
            cleaned.append(text)
    return "\n\n".join(cleaned)


def extract_fomc_urls(index_html: str, years: tuple[int, ...]) -> list[tuple[str, str]]:
    """Return (event_ts_iso, statement_url) tuples from fomccalendars.htm.

    fomccalendars.htm covers multiple recent years; we filter to the requested
    `years` window (D-59 leak-disjoint: only emit events the caller asked for).
    """
    out: list[tuple[str, str]] = []
    for match in FOMC_STATEMENT_RE.finditer(index_html):
        yyyymmdd = match.group(1)
        try:
            d = dt.datetime.strptime(yyyymmdd, "%Y%m%d").replace(tzinfo=dt.timezone.utc)
        except ValueError:
            continue
        if d.year not in years:
            continue  # D-59 leak-disjoint: only emit events in caller's window
        url = f"https://www.federalreserve.gov{match.group(0)}"
        out.append((d.isoformat(), url))
    return list(dict.fromkeys(out))  # dedupe preserve order


def extract_ecb_urls(index_html: str, years: tuple[int, ...]) -> list[tuple[str, str]]:
    """Return (event_ts_iso, statement_url) tuples from ECB year index_include.en.html.

    Two-stage filter:
      1. URL must match ecb.mp{YYMMDD}~{hash}.en.html (mp prefix only)
      2. Anchor text must contain "Monetary policy decision" (excludes unrelated PRs)
    """
    out: list[tuple[str, str]] = []
    for match in ECB_ANCHOR_RE.finditer(index_html):
        rel = match.group(1)
        yymmdd = match.group(3)
        anchor_text = match.group(4)
        if not ECB_MP_TITLE_RE.search(anchor_text):
            continue
        try:
            d = dt.datetime.strptime(yymmdd, "%y%m%d").replace(tzinfo=dt.timezone.utc)
        except ValueError:
            continue
        if d.year not in years:
            continue
        url = f"https://www.ecb.europa.eu{rel}"
        out.append((d.isoformat(), url))
    return list(dict.fromkeys(out))


def fetch_statements(
    bank: str, index_urls: Iterable[str], extractor
) -> list[Statement]:
    rows: list[Statement] = []
    candidates: list[tuple[str, str]] = []
    for idx_url in index_urls:
        sys.stderr.write(f"[fetch] index {bank}: {idx_url}\n")
        try:
            idx_html = http_get(idx_url)
        except FetchError as e:
            sys.stderr.write(f"[warn] index fetch failed: {e}\n")
            continue
        candidates.extend(extractor(idx_html))
    candidates = list(dict.fromkeys(candidates))
    sys.stderr.write(f"[fetch] {bank}: {len(candidates)} candidate URLs\n")
    fetched_at = dt.datetime.now(dt.timezone.utc).isoformat()
    for event_ts, url in candidates:
        try:
            body = http_get(url)
        except FetchError as e:
            sys.stderr.write(f"[warn] body fetch failed: {url} -> {e}\n")
            continue
        text = strip_html_to_paragraphs(body)
        if len(text) < 200:
            sys.stderr.write(f"[warn] body too short ({len(text)} chars): {url}\n")
            continue
        rows.append(
            Statement(
                event_ts=event_ts,
                central_bank=bank,
                source_url=url,
                statement_text=text,
                fetched_at=fetched_at,
            )
        )
    return rows


def write_csv(rows: list[Statement], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as f:
        writer = csv.writer(f, quoting=csv.QUOTE_ALL)
        writer.writerow(
            ["event_ts", "central_bank", "source_url", "statement_text", "fetched_at"]
        )
        for r in rows:
            writer.writerow(
                [
                    r.event_ts,
                    r.central_bank,
                    r.source_url,
                    r.statement_text,
                    r.fetched_at,
                ]
            )


def _parse_years(spec: str) -> tuple[int, ...]:
    """Parse comma-separated YYYY list (e.g., '2022,2023' or '2024')."""
    out: list[int] = []
    for tok in spec.split(","):
        tok = tok.strip()
        if not tok:
            continue
        out.append(int(tok))
    if not out:
        raise ValueError(f"--years got no usable years from {spec!r}")
    return tuple(out)


def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        description=(
            "Freeze-time fetcher for FOMC + ECB monetary-policy statements. "
            "Default reproduces the 32-event 2022-2023 freeze artifact; pass "
            "--years 2024 --min-rows 4 for the 2024-Q1 inference set."
        )
    )
    p.add_argument(
        "--years",
        type=str,
        default=",".join(str(y) for y in DEFAULT_YEARS),
        help="Comma-separated YYYY list (default: 2022,2023)",
    )
    p.add_argument(
        "--output",
        type=Path,
        default=OUTPUT_CSV,
        help=f"Output CSV path (default: {OUTPUT_CSV})",
    )
    p.add_argument(
        "--min-rows",
        type=int,
        default=30,
        help="Minimum rows required to exit 0 (default: 30 for 32-event freeze)",
    )
    return p


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    years = _parse_years(args.years)
    output: Path = args.output
    min_rows: int = args.min_rows

    if output.exists():
        sys.stderr.write(f"[skip] {output} already exists. Delete to re-fetch.\n")
        return 0

    fomc = fetch_statements(
        "FOMC", FOMC_INDEX_URLS, lambda h: extract_fomc_urls(h, years)
    )
    ecb = fetch_statements(
        "ECB", ecb_index_urls(years), lambda h: extract_ecb_urls(h, years)
    )
    rows = fomc + ecb
    rows.sort(key=lambda r: (r.event_ts, r.central_bank))
    write_csv(rows, output)
    sys.stderr.write(
        f"[done] wrote {len(rows)} rows ({len(fomc)} FOMC + {len(ecb)} ECB) -> {output}\n"
    )
    if len(rows) < min_rows:
        sys.stderr.write(
            f"[error] only {len(rows)} rows fetched; ≥{min_rows} required "
            f"(years={years}; default 2022-2023 expects 32 = FOMC 8/yr × 2yr + ECB 8/yr × 2yr).\n"
        )
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
