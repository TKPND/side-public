use anyhow::{anyhow, bail, Context};
use serde::Serialize;
use side_engine::edges::Edge;
use std::collections::HashSet;
use std::path::{Path, PathBuf};

const STRATEGY_NAME: &str = "tod_edge";
const EXIT_TYPE: &str = "time_hold";
const REQUESTED_SIZE: f64 = 1.0;
const REQUESTED_SIZE_BASIS: &str = "unit_scan_slot";
const SCAN_V2_CANDIDATE_SCHEMA_VERSION: &str = "risk_contract.v2.candidate.v1";
const SCAN_V2_SCHEMA_REF: &str = "risk/contracts/v2/risk_contract_v2.schema.json";
const SCAN_V2_RESULT_SCHEMA_REF: &str =
    "risk/contracts/v2/risk_contract_validator_result_v2.schema.json";
const RISK_GATE_VALIDATOR_REF: &str = "scripts/validate_risk_contract.py";
const ARTIFACT_PATH_PREFIX: &str = "reports/v5.7/risk_gate";
const VALIDATION_REFS: [&str; 3] = [
    "risk/contracts/v1/risk_contract_v1.schema.json",
    "scripts/validate_risk_contract.py",
    "rust/side-engine/tests/fixtures/edges_sample.json",
];

#[derive(Debug, Clone, Serialize)]
pub struct RiskCandidate {
    pub strategy_id: String,
    pub candidate_id: String,
    pub symbol_or_universe: String,
    pub timeframe: String,
    pub validation_refs: Vec<String>,
    pub requested_size: f64,
    pub requested_size_basis: String,
    pub scan_params: ScanParams,
    pub source_edge: SourceEdgeSnapshot,
    pub fee_refs: FeeRefs,
    pub data_refs: DataRefs,
    pub artifact_path: String,
}

#[derive(Debug, Clone, Serialize)]
pub struct ScanRiskCandidateV2 {
    pub candidate_schema_version: &'static str,
    pub candidate_id: String,
    pub strategy_id: String,
    pub symbol_or_universe: String,
    pub timeframe: String,
    pub validation_refs: Vec<String>,
    pub surface: ScanCandidateSurface,
    pub sizing: ScanCandidateSizing,
    pub surface_payload: ScanCandidateSurfacePayload,
    pub artifact_root: String,
}

#[derive(Debug, Clone, Serialize)]
pub struct ScanCandidateSurface {
    pub runtime_surface: &'static str,
    pub surface_status: &'static str,
    pub analysis_scope: &'static str,
    pub analysis_scope_status: &'static str,
}

#[derive(Debug, Clone, Serialize)]
pub struct ScanCandidateSizing {
    pub requested_size: f64,
    pub requested_size_basis: &'static str,
}

#[derive(Debug, Clone, Serialize)]
pub struct ScanCandidateSurfacePayload {
    pub scan_params: ScanParams,
    pub source_edge: SourceEdgeSnapshot,
    pub fee_refs: FeeRefs,
    pub data_refs: DataRefs,
}

#[derive(Debug, Clone, Serialize)]
pub struct ScanParams {
    pub asset: String,
    pub timeframe: String,
    pub strategy_name: String,
    pub params: ScanParamsValues,
    pub source_edge_index: usize,
}

#[derive(Debug, Clone, Serialize)]
pub struct ScanParamsValues {
    pub entry_minute: u16,
    pub direction: String,
    pub hold_h: u8,
    pub exit_type: String,
}

#[derive(Debug, Clone, Serialize)]
pub struct SourceEdgeSnapshot {
    pub entry_minute: u16,
    pub direction: String,
    pub hold_h_candidates: Vec<u8>,
    pub t_stat: f64,
    pub bh_q: f64,
    pub dsr_p: Option<f64>,
    pub source_query: String,
    pub asset: String,
    pub timeframe: String,
}

#[derive(Debug, Clone, Serialize)]
pub struct FeeRefs {
    pub spread_bps_rt: f64,
    pub commission_bps_rt: f64,
    pub fee_sweep_bps_rt: Vec<f64>,
    pub tod_spread_curve: bool,
}

#[derive(Debug, Clone, Serialize)]
pub struct DataRefs {
    pub edges_path: String,
    pub fixture_parquet: Option<String>,
    pub source_query: String,
    pub asset: String,
    pub timeframe: String,
}

#[derive(Debug, Clone, Copy)]
pub struct RiskCandidateInput<'a> {
    pub edge: &'a Edge,
    pub source_edge_index: usize,
    pub hold_h: u8,
    pub edges_path: &'a Path,
    pub fixture_parquet: Option<&'a Path>,
    pub spread_bps_rt: f64,
    pub commission_bps_rt: f64,
    pub fee_sweep_bps_rt: &'a [f64],
    pub tod_spread_curve: bool,
}

pub fn candidate_id(edge: &Edge, source_edge_index: usize, hold_h: u8) -> anyhow::Result<String> {
    validate_candidate_component("asset", &edge.asset)?;
    validate_candidate_component("timeframe", &edge.timeframe)?;
    validate_candidate_component("direction", &edge.direction)?;

    Ok(format!(
        "scan_edges.{}.{}.edge{}.m{}.{}.h{}",
        edge.asset, edge.timeframe, source_edge_index, edge.entry_minute, edge.direction, hold_h
    ))
}

pub fn artifact_path_for(candidate_id: &str) -> anyhow::Result<String> {
    validate_candidate_component("candidate_id", candidate_id)?;
    Ok(format!("{ARTIFACT_PATH_PREFIX}/{candidate_id}.json"))
}

pub fn artifact_path_for_root(root: &Path, candidate_id: &str) -> anyhow::Result<PathBuf> {
    validate_candidate_component("candidate_id", candidate_id)?;
    Ok(root.join(format!("{candidate_id}.json")))
}

pub fn build_candidate(input: RiskCandidateInput<'_>) -> anyhow::Result<RiskCandidate> {
    input.edge.validate()?;
    if !input.edge.hold_h_candidates.contains(&input.hold_h) {
        bail!(
            "hold_h {} not present in source_edge.hold_h_candidates",
            input.hold_h
        );
    }

    let candidate_id = candidate_id(input.edge, input.source_edge_index, input.hold_h)?;
    let artifact_path = artifact_path_for(&candidate_id)?;

    Ok(RiskCandidate {
        strategy_id: candidate_id.clone(),
        candidate_id,
        symbol_or_universe: input.edge.asset.clone(),
        timeframe: input.edge.timeframe.clone(),
        validation_refs: VALIDATION_REFS
            .iter()
            .map(|ref_path| ref_path.to_string())
            .collect(),
        requested_size: REQUESTED_SIZE,
        requested_size_basis: REQUESTED_SIZE_BASIS.to_string(),
        scan_params: ScanParams {
            asset: input.edge.asset.clone(),
            timeframe: input.edge.timeframe.clone(),
            strategy_name: STRATEGY_NAME.to_string(),
            params: ScanParamsValues {
                entry_minute: input.edge.entry_minute,
                direction: input.edge.direction.clone(),
                hold_h: input.hold_h,
                exit_type: EXIT_TYPE.to_string(),
            },
            source_edge_index: input.source_edge_index,
        },
        source_edge: SourceEdgeSnapshot {
            entry_minute: input.edge.entry_minute,
            direction: input.edge.direction.clone(),
            hold_h_candidates: input.edge.hold_h_candidates.clone(),
            t_stat: input.edge.t_stat,
            bh_q: input.edge.bh_q,
            dsr_p: input.edge.dsr_p,
            source_query: input.edge.source_query.clone(),
            asset: input.edge.asset.clone(),
            timeframe: input.edge.timeframe.clone(),
        },
        fee_refs: FeeRefs {
            spread_bps_rt: input.spread_bps_rt,
            commission_bps_rt: input.commission_bps_rt,
            fee_sweep_bps_rt: input.fee_sweep_bps_rt.to_vec(),
            tod_spread_curve: input.tod_spread_curve,
        },
        data_refs: DataRefs {
            edges_path: input.edges_path.display().to_string(),
            fixture_parquet: input.fixture_parquet.map(|path| path.display().to_string()),
            source_query: input.edge.source_query.clone(),
            asset: input.edge.asset.clone(),
            timeframe: input.edge.timeframe.clone(),
        },
        artifact_path,
    })
}

pub fn build_scan_v2_candidate_for_root(
    input: RiskCandidateInput<'_>,
    artifact_root: &Path,
) -> anyhow::Result<ScanRiskCandidateV2> {
    let candidate = build_candidate(input)?;
    let validation_refs = vec![
        SCAN_V2_SCHEMA_REF.to_string(),
        SCAN_V2_RESULT_SCHEMA_REF.to_string(),
        RISK_GATE_VALIDATOR_REF.to_string(),
        candidate.data_refs.edges_path.clone(),
    ];

    Ok(ScanRiskCandidateV2 {
        candidate_schema_version: SCAN_V2_CANDIDATE_SCHEMA_VERSION,
        candidate_id: candidate.candidate_id.clone(),
        strategy_id: candidate.candidate_id,
        symbol_or_universe: candidate.symbol_or_universe,
        timeframe: candidate.timeframe,
        validation_refs,
        surface: ScanCandidateSurface {
            runtime_surface: "scan",
            surface_status: "implemented",
            analysis_scope: "none",
            analysis_scope_status: "not_applicable",
        },
        sizing: ScanCandidateSizing {
            requested_size: REQUESTED_SIZE,
            requested_size_basis: REQUESTED_SIZE_BASIS,
        },
        surface_payload: ScanCandidateSurfacePayload {
            scan_params: candidate.scan_params,
            source_edge: candidate.source_edge,
            fee_refs: candidate.fee_refs,
            data_refs: candidate.data_refs,
        },
        artifact_root: artifact_root.display().to_string(),
    })
}

pub fn build_candidate_batch<'a>(
    inputs: impl IntoIterator<Item = RiskCandidateInput<'a>>,
) -> anyhow::Result<Vec<RiskCandidate>> {
    let candidates: Vec<RiskCandidate> = inputs
        .into_iter()
        .map(build_candidate)
        .collect::<anyhow::Result<Vec<_>>>()?;
    ensure_unique_candidate_outputs(&candidates)?;
    Ok(candidates)
}

pub fn write_candidate_json<T: Serialize>(candidate: &T, path: &Path) -> anyhow::Result<()> {
    if let Some(parent) = path
        .parent()
        .filter(|parent| !parent.as_os_str().is_empty())
    {
        std::fs::create_dir_all(parent)
            .with_context(|| format!("failed to create {}", parent.display()))?;
    }
    let json = serde_json::to_string_pretty(candidate)
        .context("failed to serialize risk candidate JSON")?;
    std::fs::write(path, format!("{json}\n"))
        .with_context(|| format!("failed to write {}", path.display()))?;
    Ok(())
}

fn validate_candidate_component(name: &str, value: &str) -> anyhow::Result<()> {
    if value.is_empty()
        || value == "."
        || value == ".."
        || value.contains("..")
        || value.contains('/')
        || value.contains('\\')
        || value.contains('\0')
    {
        bail!("unsafe candidate component {name}: {value:?}");
    }
    Ok(())
}

fn ensure_unique_candidate_outputs(candidates: &[RiskCandidate]) -> anyhow::Result<()> {
    let mut candidate_ids = HashSet::new();
    for candidate in candidates {
        if !candidate_ids.insert(candidate.candidate_id.as_str()) {
            return Err(anyhow!(
                "duplicate candidate_id: {}",
                candidate.candidate_id
            ));
        }
    }

    let mut artifact_paths = HashSet::new();
    for candidate in candidates {
        if !artifact_paths.insert(candidate.artifact_path.as_str()) {
            return Err(anyhow!(
                "duplicate artifact_path: {}",
                candidate.artifact_path
            ));
        }
    }

    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;
    use serde_json::Value;
    use side_engine::edges::Edge;
    use std::path::Path;

    fn sample_edge() -> Edge {
        Edge {
            entry_minute: 0,
            direction: "long".to_string(),
            hold_h_candidates: vec![1, 3],
            t_stat: 4.52,
            bh_q: 0.018,
            dsr_p: None,
            source_query: "bq_usdjpy_directional_windows.sql".to_string(),
            asset: "USDJPY".to_string(),
            timeframe: "1h".to_string(),
        }
    }

    fn sample_input(edge: &Edge, source_edge_index: usize, hold_h: u8) -> RiskCandidateInput<'_> {
        RiskCandidateInput {
            edge,
            source_edge_index,
            hold_h,
            edges_path: Path::new("rust/side-engine/tests/fixtures/edges_sample.json"),
            fixture_parquet: Some(Path::new(
                "rust/side-engine/tests/fixtures/usdjpy_1h_sample.parquet",
            )),
            spread_bps_rt: 1.5,
            commission_bps_rt: 0.5,
            fee_sweep_bps_rt: &[2.0],
            tod_spread_curve: false,
        }
    }

    #[test]
    fn risk_adapter_candidate_contract() {
        let edge = sample_edge();
        let candidate = build_candidate(sample_input(&edge, 0, 1)).unwrap();
        let value = serde_json::to_value(&candidate).unwrap();

        assert_eq!(
            candidate.candidate_id,
            "scan_edges.USDJPY.1h.edge0.m0.long.h1"
        );
        assert_eq!(candidate.strategy_id, candidate.candidate_id);
        assert_eq!(
            candidate.artifact_path,
            "reports/v5.7/risk_gate/scan_edges.USDJPY.1h.edge0.m0.long.h1.json"
        );
        assert_eq!(
            artifact_path_for("x").unwrap(),
            "reports/v5.7/risk_gate/x.json"
        );
        assert_eq!(candidate.requested_size, 1.0);
        assert_eq!(candidate.requested_size_basis, "unit_scan_slot");
        assert_eq!(candidate.scan_params.strategy_name, "tod_edge");
        assert_eq!(candidate.scan_params.params.exit_type, "time_hold");
        assert!(candidate
            .validation_refs
            .contains(&"risk/contracts/v1/risk_contract_v1.schema.json".to_string()));
        assert!(candidate
            .validation_refs
            .contains(&"scripts/validate_risk_contract.py".to_string()));
        assert!(candidate
            .validation_refs
            .contains(&"rust/side-engine/tests/fixtures/edges_sample.json".to_string()));
        assert_absent(&value, "fee_curve");
        assert_absent(&value, "verdict");
        assert_absent(&value, "relaxed_pass");
        assert_absent(&value, "verdicts_per_fee");
    }

    #[test]
    fn risk_adapter_rejects_hold_not_in_source_edge_candidates() {
        let edge = sample_edge();
        let err = build_candidate(sample_input(&edge, 0, 2)).unwrap_err();
        assert!(format!("{err:#}").contains("hold_h"));
    }

    #[test]
    fn risk_adapter_rejects_unsafe_candidate_components() {
        let mut edge = sample_edge();
        edge.asset = "../USDJPY".to_string();
        let err = build_candidate(sample_input(&edge, 0, 1)).unwrap_err();
        assert!(format!("{err:#}").contains("unsafe candidate component"));
    }

    #[test]
    fn risk_adapter_duplicate_ids_fail_closed() {
        let edge = sample_edge();
        let err = build_candidate_batch([sample_input(&edge, 0, 1), sample_input(&edge, 0, 1)])
            .unwrap_err();
        assert!(format!("{err:#}").contains("duplicate candidate_id"));
    }

    #[test]
    fn risk_adapter_duplicate_paths_fail_closed() {
        let edge = sample_edge();
        let mut candidates =
            build_candidate_batch([sample_input(&edge, 0, 1), sample_input(&edge, 0, 3)]).unwrap();
        candidates[1].artifact_path = candidates[0].artifact_path.clone();

        let err = ensure_unique_candidate_outputs(&candidates).unwrap_err();
        assert!(format!("{err:#}").contains("duplicate artifact_path"));
    }

    #[test]
    fn risk_gate_artifact_path_for_root_safe_join() {
        let path = artifact_path_for_root(
            Path::new("reports/v5.7/risk_gate"),
            "scan_edges.USDJPY.1h.edge0.m0.long.h1",
        )
        .unwrap();

        assert_eq!(
            path,
            Path::new("reports/v5.7/risk_gate").join("scan_edges.USDJPY.1h.edge0.m0.long.h1.json")
        );
    }

    #[test]
    fn risk_gate_artifact_path_for_root_rejects_unsafe_candidate_id() {
        let err =
            artifact_path_for_root(Path::new("reports/v5.7/risk_gate"), "../evil").unwrap_err();

        assert!(format!("{err:#}").contains("unsafe candidate component"));
    }

    fn assert_absent(value: &Value, key: &str) {
        match value {
            Value::Object(map) => {
                assert!(!map.contains_key(key), "unexpected key {key}");
                for nested in map.values() {
                    assert_absent(nested, key);
                }
            }
            Value::Array(items) => {
                for nested in items {
                    assert_absent(nested, key);
                }
            }
            _ => {}
        }
    }
}
