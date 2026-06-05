//! `side wfd-rerun` — fresh WFD rerun for macro_event (Phase 70 Plan 03).
//!
//! For each (pair × event) combination, load the per-pair mirror CSV, run the
//! corresponding `run_<EVENT>_event_fee_sweep` from `side_engine::scanner::macro_event`
//! (which already applies `GateConfig::macro_event()` internally, WFD-02), and
//! write a `WfdRerunReport` to `<output_dir>/<pair>/<event>/report.json`.
//!
//! The report embeds two provenance stamps so Plan 04/05 consumers can audit
//! the inputs:
//! - `data_provenance`: `fresh-wfd-rerun-YYYY-MM-DD-<git-short-sha>[-dirty]`
//!   (Pitfall 3: `-dirty` suffix when the working tree is not clean).
//! - `grid_provenance`: snapshot of `WINDOW_OFFSETS`, `HOLD_BARS_VALUES`,
//!   `EXIT_TYPES` const arrays.

use std::path::{Component, Path, PathBuf};

use anyhow::{Context, Result};
use chrono::Local;
use clap::{Args, ValueEnum};
use serde::Serialize;

use side_engine::csv_loader::load_ohlcv_csv;
use side_engine::pair::Pair;
use side_engine::scanner::macro_event::{
    run_ecb_event_fee_sweep, run_fomc_event_fee_sweep, run_nfp_event_fee_sweep, SlotReport,
    EXIT_TYPES, HOLD_BARS_VALUES, WINDOW_OFFSETS,
};
use side_engine::scanner::OhlcvData;

#[derive(Args, Debug)]
pub struct WfdRerunArgs {
    /// Target pair (or `all` to fan out to all 4).
    #[arg(long, value_enum)]
    pub pair: PairSelector,

    /// Target event (or `all` to fan out to all 3).
    #[arg(long, value_enum)]
    pub event: EventSelector,

    /// Root directory for report.json output. Layout: `<output_dir>/<pair>/<event>/report.json`.
    /// Defaults to a staging directory so fresh reruns cannot overwrite protected v4.x archives.
    #[arg(long, default_value = "target/wfd-rerun")]
    pub output_dir: String,

    /// Allow writing under protected v4.6 report archive paths.
    /// Use only for intentional baseline-update evidence with before/after audit material.
    #[arg(long, default_value_t = false)]
    pub allow_protected_output: bool,

    /// Optional override: explicit mirror CSV path (applies to all selected pairs).
    #[arg(long)]
    pub tick_csv_glob: Option<String>,
}

#[derive(Clone, Copy, Debug, ValueEnum)]
pub enum PairSelector {
    Usdjpy,
    Eurusd,
    Audusd,
    Eurjpy,
    All,
}

#[derive(Clone, Copy, Debug, ValueEnum)]
pub enum EventSelector {
    Fomc,
    Ecb,
    Nfp,
    All,
}

#[derive(Debug, Clone, Serialize)]
pub struct WfdRerunReport {
    pub data_provenance: String,
    pub grid_provenance: GridProvenance,
    pub pair: String,
    pub event: String,
    pub slots: Vec<SlotReport>,
}

#[derive(Debug, Clone, Serialize)]
pub struct GridProvenance {
    pub window_offsets: Vec<u32>,
    pub hold_bars_values: Vec<u32>,
    pub exit_types: Vec<String>,
}

fn git_short_sha() -> Result<String> {
    let out = std::process::Command::new("git")
        .args(["rev-parse", "--short", "HEAD"])
        .output()
        .context("git rev-parse failed")?;
    if !out.status.success() {
        anyhow::bail!(
            "git rev-parse --short HEAD exited non-zero: {}",
            String::from_utf8_lossy(&out.stderr)
        );
    }
    Ok(String::from_utf8(out.stdout)?.trim().to_string())
}

fn git_is_dirty() -> bool {
    // Pitfall 3: uncommitted changes silently invalidate the SHA; flag them with `-dirty`.
    std::process::Command::new("git")
        .args(["diff-index", "--quiet", "HEAD", "--"])
        .status()
        .map(|s| !s.success())
        .unwrap_or(false)
}

fn build_provenance() -> Result<String> {
    let date = Local::now().format("%Y-%m-%d").to_string();
    let sha = git_short_sha()?;
    let suffix = if git_is_dirty() { "-dirty" } else { "" };
    Ok(format!("fresh-wfd-rerun-{date}-{sha}{suffix}"))
}

fn grid_snapshot() -> GridProvenance {
    GridProvenance {
        window_offsets: WINDOW_OFFSETS.to_vec(),
        hold_bars_values: HOLD_BARS_VALUES.to_vec(),
        exit_types: EXIT_TYPES.iter().map(|s| s.to_string()).collect(),
    }
}

fn normalized_component_names(path: &Path) -> Vec<String> {
    let mut components = Vec::new();
    for component in path.components() {
        match component {
            Component::Prefix(prefix) => {
                components.push(prefix.as_os_str().to_string_lossy().to_string());
            }
            Component::RootDir | Component::CurDir => {}
            Component::ParentDir => {
                components.pop();
            }
            Component::Normal(part) => components.push(part.to_string_lossy().to_string()),
        }
    }
    components
}

fn resolve_path_for_guard(path: &Path) -> Result<PathBuf> {
    let mut resolved = if path.is_absolute() {
        PathBuf::new()
    } else {
        std::env::current_dir().context("failed to resolve current directory for output guard")?
    };

    for component in path.components() {
        match component {
            Component::Prefix(prefix) => resolved.push(prefix.as_os_str()),
            Component::RootDir => resolved.push(Path::new("/")),
            Component::CurDir => {}
            Component::ParentDir => {
                resolved.pop();
            }
            Component::Normal(part) => {
                let next = resolved.join(part);
                resolved = match std::fs::symlink_metadata(&next) {
                    Ok(metadata) if metadata.file_type().is_symlink() => {
                        let target = std::fs::read_link(&next).with_context(|| {
                            format!("failed to read output path symlink {}", next.display())
                        })?;
                        let target_path = if target.is_absolute() {
                            target
                        } else {
                            next.parent()
                                .expect("joined path has a parent")
                                .join(target)
                        };
                        std::fs::canonicalize(&target_path).unwrap_or(target_path)
                    }
                    Ok(_) => std::fs::canonicalize(&next).unwrap_or(next),
                    Err(_) => next,
                };
            }
        }
    }
    Ok(resolved)
}

fn contains_protected_v46_report_archive(path: &Path) -> bool {
    let components = normalized_component_names(path);

    components.windows(3).any(|window| {
        window[0] == "docs" && window[1] == "reports" && window[2] == "v4.6-verdict-resolution"
    })
}

fn ensure_path_allowed_for_output(path: &Path, allow_protected_output: bool) -> Result<()> {
    let resolved_path = resolve_path_for_guard(path)?;
    let protected = contains_protected_v46_report_archive(path)
        || contains_protected_v46_report_archive(&resolved_path);

    if protected && !allow_protected_output {
        anyhow::bail!(
            "refusing to write wfd-rerun output under protected v4.6 report archive path `{}`; \
             choose a staging output directory or pass --allow-protected-output only for an \
             intentional baseline-update evidence run",
            path.display()
        );
    }
    Ok(())
}

fn report_output_path(output_dir: &str, pair: PairSelector, event: EventSelector) -> PathBuf {
    Path::new(output_dir)
        .join(pair_str(pair))
        .join(event_str(event))
        .join("report.json")
}

fn ensure_output_targets_allowed(
    output_dir: &str,
    pairs: &[PairSelector],
    events: &[EventSelector],
    allow_protected_output: bool,
) -> Result<()> {
    ensure_path_allowed_for_output(Path::new(output_dir), allow_protected_output)?;
    for &pair in pairs {
        for &event in events {
            let out = report_output_path(output_dir, pair, event);
            let parent = out.parent().expect("report.json path has a parent");
            ensure_path_allowed_for_output(parent, allow_protected_output)?;
            ensure_path_allowed_for_output(&out, allow_protected_output)?;
        }
    }
    Ok(())
}

fn pair_str(p: PairSelector) -> &'static str {
    match p {
        PairSelector::Usdjpy => "usdjpy",
        PairSelector::Eurusd => "eurusd",
        PairSelector::Audusd => "audusd",
        PairSelector::Eurjpy => "eurjpy",
        PairSelector::All => unreachable!("all is expanded before pair_str is called"),
    }
}

fn event_str(e: EventSelector) -> &'static str {
    match e {
        EventSelector::Fomc => "fomc",
        EventSelector::Ecb => "ecb",
        EventSelector::Nfp => "nfp",
        EventSelector::All => unreachable!("all is expanded before event_str is called"),
    }
}

fn pair_enum(p: PairSelector) -> Pair {
    match p {
        PairSelector::Usdjpy => Pair::Usdjpy,
        PairSelector::Eurusd => Pair::Eurusd,
        PairSelector::Audusd => Pair::Audusd,
        PairSelector::Eurjpy => Pair::Eurjpy,
        PairSelector::All => unreachable!("all is expanded before pair_enum is called"),
    }
}

fn mirror_csv_path(pair: PairSelector) -> PathBuf {
    let filename = match pair {
        PairSelector::Usdjpy => "USDJPY_1h_2022_2023.csv",
        PairSelector::Eurusd => "EURUSD_1h_2022_2023.csv",
        // AUDUSD / EURJPY: existing 2022-2026 mirror CSVs (Phase 50 fetch).
        // OHLCV is truncated to 2022-2023 at load time so cross-pair sweeps
        // share the same event scope (see `truncate_to_phase_70_window`).
        PairSelector::Audusd => "AUDUSD_1h.csv",
        PairSelector::Eurjpy => "EURJPY_1h.csv",
        PairSelector::All => unreachable!("all is expanded before mirror_csv_path is called"),
    };
    PathBuf::from("rust/data/mirror").join(filename)
}

// 2024-01-01 00:00:00 UTC in nanoseconds. Bars at-or-after this are dropped
// so AUDUSD/EURJPY (whose mirrors cover 2022-2026) match the USDJPY/EURUSD
// scope (Phase 70 Plan 02 fetched only 2022-2023). Without this, the sweep
// picks up `*_DATES_2024_2026` events and cross-pair trade counts diverge.
const PHASE_70_WINDOW_END_NS: i64 = 1_704_067_200_000_000_000;

fn truncate_to_phase_70_window(ohlcv: &mut OhlcvData) -> usize {
    let cutoff = ohlcv
        .datetimes_ns
        .partition_point(|&ts| ts < PHASE_70_WINDOW_END_NS);
    let trimmed = ohlcv.datetimes_ns.len() - cutoff;
    if trimmed > 0 {
        ohlcv.open.truncate(cutoff);
        ohlcv.high.truncate(cutoff);
        ohlcv.low.truncate(cutoff);
        ohlcv.close.truncate(cutoff);
        ohlcv.volume.truncate(cutoff);
        ohlcv.datetimes_ns.truncate(cutoff);
        if let Some(aux) = ohlcv.aux_close.as_mut() {
            aux.truncate(cutoff);
        }
    }
    trimmed
}

fn expand_pairs(p: PairSelector) -> Vec<PairSelector> {
    match p {
        PairSelector::All => vec![
            PairSelector::Usdjpy,
            PairSelector::Eurusd,
            PairSelector::Audusd,
            PairSelector::Eurjpy,
        ],
        other => vec![other],
    }
}

fn expand_events(e: EventSelector) -> Vec<EventSelector> {
    match e {
        EventSelector::All => vec![EventSelector::Fomc, EventSelector::Ecb, EventSelector::Nfp],
        other => vec![other],
    }
}

pub async fn run(args: WfdRerunArgs) -> Result<()> {
    let pairs = expand_pairs(args.pair);
    let events = expand_events(args.event);
    ensure_output_targets_allowed(
        &args.output_dir,
        &pairs,
        &events,
        args.allow_protected_output,
    )?;

    tracing::info!(
        "wfd-rerun: pair={:?}, event={:?}, output_dir={}",
        args.pair,
        args.event,
        args.output_dir
    );
    let provenance = build_provenance()?;
    let grid = grid_snapshot();

    for p in pairs {
        let csv = args
            .tick_csv_glob
            .as_ref()
            .map(PathBuf::from)
            .unwrap_or_else(|| mirror_csv_path(p));
        tracing::info!("loading mirror CSV: {}", csv.display());
        let mut ohlcv = load_ohlcv_csv(&csv)
            .with_context(|| format!("failed to load mirror CSV: {}", csv.display()))?;
        let trimmed = truncate_to_phase_70_window(&mut ohlcv);
        if trimmed > 0 {
            tracing::info!(
                "truncated {} bars at/after 2024-01-01 UTC (Phase 70 scope; retained {} bars)",
                trimmed,
                ohlcv.datetimes_ns.len()
            );
        }

        for &e in &events {
            let slots = match e {
                EventSelector::Fomc => run_fomc_event_fee_sweep(&ohlcv, pair_enum(p)),
                EventSelector::Ecb => run_ecb_event_fee_sweep(&ohlcv, pair_enum(p)),
                // Deviation from PLAN.md: the NFP sweep signature in macro_event.rs
                // currently takes only `&OhlcvData` (no pair arg).
                EventSelector::Nfp => run_nfp_event_fee_sweep(&ohlcv),
                EventSelector::All => unreachable!("all is expanded before dispatch"),
            };
            let report = WfdRerunReport {
                data_provenance: provenance.clone(),
                grid_provenance: grid.clone(),
                pair: pair_str(p).to_string(),
                event: event_str(e).to_string(),
                slots,
            };
            let out = report_output_path(&args.output_dir, p, e);
            let parent = out.parent().expect("report.json path has a parent");
            std::fs::create_dir_all(parent)
                .with_context(|| format!("failed to create output dir for {}", out.display()))?;
            ensure_path_allowed_for_output(parent, args.allow_protected_output)?;
            ensure_path_allowed_for_output(&out, args.allow_protected_output)?;
            let json = serde_json::to_string_pretty(&report)
                .context("failed to serialize WfdRerunReport")?;
            std::fs::write(&out, &json)
                .with_context(|| format!("failed to write {}", out.display()))?;
            tracing::info!("wrote {}", out.display());
        }
    }
    Ok(())
}
