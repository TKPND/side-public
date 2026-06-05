use std::collections::HashSet;
use std::io::Write;
use std::path::{Component, Path, PathBuf};

use anyhow::{anyhow, bail, Context};
use serde::Serialize;
use serde_json::{Map, Value};
use sha2::{Digest, Sha256};

use crate::cmd::backtest_contract::TodEdgeParams;

pub const BACKTEST_CANDIDATE_SCHEMA_VERSION: &str = "risk_contract.v1.backtest_candidate.v1";
pub const BACKTEST_V2_CANDIDATE_SCHEMA_VERSION: &str = "risk_contract.v2.candidate.v1";
pub const BACKTEST_V2_SCHEMA_REF: &str = "risk/contracts/v2/risk_contract_v2.schema.json";
pub const BACKTEST_V2_RESULT_SCHEMA_REF: &str =
    "risk/contracts/v2/risk_contract_validator_result_v2.schema.json";
pub const RISK_GATE_VALIDATOR_REF: &str = "scripts/validate_risk_contract.py";
pub const BACKTEST_REQUESTED_SIZE: f64 = 1.0;
pub const BACKTEST_REQUESTED_SIZE_BASIS: &str = "unit_backtest_run";
pub const BACKTEST_STRATEGY: &str = "tod_edge";

#[derive(Debug, Clone)]
pub struct BacktestCandidateInput<'a> {
    pub asset: &'a str,
    pub timeframe: &'a str,
    pub strategy: &'a str,
    pub params: &'a TodEdgeParams,
    pub fee_bps: f64,
    pub data_ref: &'a str,
    pub data_fingerprint: &'a str,
    pub artifact_root: &'a str,
}

#[derive(Debug, Clone, Serialize)]
pub struct BacktestRiskCandidate {
    pub candidate_schema_version: &'static str,
    pub strategy_id: String,
    pub candidate_id: String,
    pub symbol_or_universe: String,
    pub timeframe: String,
    pub validation_refs: Vec<String>,
    pub requested_size: f64,
    pub requested_size_basis: &'static str,
    pub backtest_params: BacktestCandidateParams,
    pub artifact_root: String,
}

#[derive(Debug, Clone, Serialize)]
pub struct BacktestRiskCandidateV2 {
    pub candidate_schema_version: &'static str,
    pub candidate_id: String,
    pub strategy_id: String,
    pub symbol_or_universe: String,
    pub timeframe: String,
    pub validation_refs: Vec<String>,
    pub surface: BacktestCandidateSurface,
    pub sizing: BacktestCandidateSizing,
    pub surface_payload: BacktestCandidateSurfacePayload,
    pub artifact_root: String,
}

#[derive(Debug, Clone, Serialize)]
pub struct BacktestCandidateSurface {
    pub runtime_surface: &'static str,
    pub surface_status: &'static str,
    pub analysis_scope: &'static str,
    pub analysis_scope_status: &'static str,
}

#[derive(Debug, Clone, Serialize)]
pub struct BacktestCandidateSizing {
    pub requested_size: f64,
    pub requested_size_basis: &'static str,
}

#[derive(Debug, Clone, Serialize)]
pub struct BacktestCandidateSurfacePayload {
    pub backtest_params: BacktestCandidateParams,
}

#[derive(Debug, Clone, Serialize)]
pub struct BacktestCandidateParams {
    pub entry_minute: u16,
    pub direction: String,
    pub hold_h: u8,
    pub strategy: String,
    pub fee_bps: f64,
    pub data_ref: String,
    pub data_fingerprint: String,
}

pub fn canonical_json_bytes(value: &Value) -> anyhow::Result<Vec<u8>> {
    serde_json::to_vec(&canonicalize_json(value)).context("failed to serialize canonical JSON")
}

pub fn backtest_candidate_id(input: &BacktestCandidateInput<'_>) -> anyhow::Result<String> {
    backtest_candidate_id_for_schema(input, BACKTEST_CANDIDATE_SCHEMA_VERSION)
}

fn backtest_candidate_id_for_schema(
    input: &BacktestCandidateInput<'_>,
    candidate_schema_version: &str,
) -> anyhow::Result<String> {
    validate_backtest_candidate_component("asset", input.asset)?;
    validate_backtest_candidate_component("timeframe", input.timeframe)?;
    validate_backtest_candidate_component("strategy", input.strategy)?;
    let fee_bps = serde_json::Number::from_f64(input.fee_bps)
        .ok_or_else(|| anyhow!("fee_bps must be finite"))?;

    let identity = Value::Object(Map::from_iter([
        (
            "candidate_schema_version".to_string(),
            Value::from(candidate_schema_version),
        ),
        ("asset".to_string(), Value::from(input.asset)),
        ("timeframe".to_string(), Value::from(input.timeframe)),
        ("strategy".to_string(), Value::from(input.strategy)),
        (
            "params".to_string(),
            Value::Object(Map::from_iter([
                (
                    "entry_minute".to_string(),
                    Value::from(input.params.entry_minute),
                ),
                (
                    "direction".to_string(),
                    Value::from(input.params.direction.clone()),
                ),
                ("hold_h".to_string(), Value::from(input.params.hold_h)),
            ])),
        ),
        ("fee_bps".to_string(), Value::Number(fee_bps)),
        (
            "data_fingerprint".to_string(),
            Value::from(input.data_fingerprint),
        ),
    ]));
    let digest = Sha256::digest(canonical_json_bytes(&identity)?);
    let hex = hex::encode(digest);
    Ok(format!(
        "backtest.{}.{}.{}.p{}",
        input.asset,
        input.timeframe,
        input.strategy,
        &hex[..12]
    ))
}

pub fn build_backtest_candidate(
    input: BacktestCandidateInput<'_>,
) -> anyhow::Result<BacktestRiskCandidate> {
    validate_artifact_root(input.artifact_root)?;
    let candidate_id = backtest_candidate_id(&input)?;

    Ok(BacktestRiskCandidate {
        candidate_schema_version: BACKTEST_CANDIDATE_SCHEMA_VERSION,
        strategy_id: candidate_id.clone(),
        candidate_id,
        symbol_or_universe: input.asset.to_string(),
        timeframe: input.timeframe.to_string(),
        validation_refs: vec![
            "risk/contracts/v1/risk_contract_v1.schema.json".to_string(),
            "scripts/validate_risk_contract.py".to_string(),
            input.data_ref.to_string(),
            input.data_fingerprint.to_string(),
        ],
        requested_size: BACKTEST_REQUESTED_SIZE,
        requested_size_basis: BACKTEST_REQUESTED_SIZE_BASIS,
        backtest_params: BacktestCandidateParams {
            entry_minute: input.params.entry_minute,
            direction: input.params.direction.clone(),
            hold_h: input.params.hold_h,
            strategy: input.strategy.to_string(),
            fee_bps: input.fee_bps,
            data_ref: input.data_ref.to_string(),
            data_fingerprint: input.data_fingerprint.to_string(),
        },
        artifact_root: input.artifact_root.to_string(),
    })
}

pub fn build_backtest_v2_candidate(
    input: BacktestCandidateInput<'_>,
) -> anyhow::Result<BacktestRiskCandidateV2> {
    validate_backtest_v2_artifact_root(input.artifact_root)?;
    let candidate_id =
        backtest_candidate_id_for_schema(&input, BACKTEST_V2_CANDIDATE_SCHEMA_VERSION)?;

    Ok(BacktestRiskCandidateV2 {
        candidate_schema_version: BACKTEST_V2_CANDIDATE_SCHEMA_VERSION,
        candidate_id: candidate_id.clone(),
        strategy_id: candidate_id,
        symbol_or_universe: input.asset.to_string(),
        timeframe: input.timeframe.to_string(),
        validation_refs: vec![
            BACKTEST_V2_SCHEMA_REF.to_string(),
            BACKTEST_V2_RESULT_SCHEMA_REF.to_string(),
            RISK_GATE_VALIDATOR_REF.to_string(),
            input.data_ref.to_string(),
            input.data_fingerprint.to_string(),
        ],
        surface: BacktestCandidateSurface {
            runtime_surface: "backtest",
            surface_status: "implemented",
            analysis_scope: "none",
            analysis_scope_status: "not_applicable",
        },
        sizing: BacktestCandidateSizing {
            requested_size: BACKTEST_REQUESTED_SIZE,
            requested_size_basis: BACKTEST_REQUESTED_SIZE_BASIS,
        },
        surface_payload: BacktestCandidateSurfacePayload {
            backtest_params: BacktestCandidateParams {
                entry_minute: input.params.entry_minute,
                direction: input.params.direction.clone(),
                hold_h: input.params.hold_h,
                strategy: input.strategy.to_string(),
                fee_bps: input.fee_bps,
                data_ref: input.data_ref.to_string(),
                data_fingerprint: input.data_fingerprint.to_string(),
            },
        },
        artifact_root: input.artifact_root.to_string(),
    })
}

pub fn write_backtest_candidate_json<T: Serialize>(
    candidate: &T,
    path: &Path,
) -> anyhow::Result<()> {
    if let Some(parent) = path
        .parent()
        .filter(|parent| !parent.as_os_str().is_empty())
    {
        std::fs::create_dir_all(parent)
            .with_context(|| format!("failed to create {}", parent.display()))?;
    }
    let json = serde_json::to_string_pretty(candidate)
        .context("failed to serialize backtest risk candidate JSON")?;
    let payload = format!("{json}\n");
    let mut file = std::fs::OpenOptions::new()
        .write(true)
        .create_new(true)
        .open(path)
        .map_err(|err| {
            if err.kind() == std::io::ErrorKind::AlreadyExists {
                anyhow!(
                    "backtest risk candidate artifact already exists: {}",
                    path.display()
                )
            } else {
                anyhow!(err).context(format!("failed to write {}", path.display()))
            }
        })?;
    file.write_all(payload.as_bytes())
        .with_context(|| format!("failed to write {}", path.display()))?;
    Ok(())
}

pub fn candidate_staging_path(root: &Path, candidate_id: &str) -> anyhow::Result<PathBuf> {
    let root_str = root
        .to_str()
        .ok_or_else(|| anyhow!("unsafe artifact_root: non-UTF-8 path"))?;
    validate_artifact_root(root_str)?;
    validate_backtest_candidate_component("candidate_id", candidate_id)?;
    Ok(root.join("candidates").join(format!("{candidate_id}.json")))
}

pub fn validate_backtest_candidate_component(name: &str, value: &str) -> anyhow::Result<()> {
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

pub fn validate_artifact_root(root: &str) -> anyhow::Result<()> {
    if root.is_empty() || root == "." || root.contains('\\') || root.contains('\0') {
        bail!("unsafe artifact_root: {root:?}");
    }
    let path = Path::new(root);
    if path.is_absolute() {
        bail!("unsafe artifact_root: {root:?}");
    }
    let mut saw_component = false;
    for component in path.components() {
        match component {
            Component::Normal(_) => saw_component = true,
            _ => bail!("unsafe artifact_root: {root:?}"),
        }
    }
    if !saw_component {
        bail!("unsafe artifact_root: {root:?}");
    }
    Ok(())
}

pub fn validate_backtest_v2_artifact_root(root: &str) -> anyhow::Result<()> {
    validate_artifact_root(root)?;
    let path = Path::new(root);
    let parts = path
        .components()
        .filter_map(|component| match component {
            Component::Normal(value) => value.to_str(),
            _ => None,
        })
        .collect::<Vec<_>>();

    if parts.first() == Some(&"reports")
        && parts.get(1).is_some_and(|version| {
            version.starts_with("v4.")
                || matches!(*version, "v5.7" | "v5.8")
                || version.starts_with("v8.")
        })
    {
        bail!("unsafe v2 artifact_root: protected report root {root:?}");
    }
    if parts.first() == Some(&".planning") {
        bail!("unsafe v2 artifact_root: protected planning root {root:?}");
    }
    if parts.first() == Some(&"docs")
        && parts.get(1) == Some(&"reports")
        && parts.get(2) == Some(&"v4")
    {
        bail!("unsafe v2 artifact_root: protected v4 docs root {root:?}");
    }
    if parts.first() == Some(&"data") && parts.get(1) == Some(&"v4") {
        bail!("unsafe v2 artifact_root: protected v4 data root {root:?}");
    }
    if parts.first() == Some(&"risk") && parts.get(1) == Some(&"contracts") {
        bail!("unsafe v2 artifact_root: protected contract root {root:?}");
    }
    Ok(())
}

pub fn build_backtest_candidate_batch<'a>(
    inputs: impl IntoIterator<Item = BacktestCandidateInput<'a>>,
) -> anyhow::Result<Vec<BacktestRiskCandidate>> {
    let candidates = inputs
        .into_iter()
        .map(build_backtest_candidate)
        .collect::<anyhow::Result<Vec<_>>>()?;
    ensure_unique_staging_outputs(&candidates)?;
    ensure_unique_candidate_ids(&candidates)?;
    Ok(candidates)
}

fn ensure_unique_staging_outputs(candidates: &[BacktestRiskCandidate]) -> anyhow::Result<()> {
    let mut staging_paths = HashSet::new();
    for candidate in candidates {
        let path =
            candidate_staging_path(Path::new(&candidate.artifact_root), &candidate.candidate_id)?;
        if !staging_paths.insert(path) {
            return Err(anyhow!(
                "duplicate candidate staging output: {}/{}",
                candidate.artifact_root,
                candidate.candidate_id
            ));
        }
    }
    Ok(())
}

fn ensure_unique_candidate_ids(candidates: &[BacktestRiskCandidate]) -> anyhow::Result<()> {
    let mut candidate_ids = HashSet::new();
    for candidate in candidates {
        if !candidate_ids.insert(candidate.candidate_id.as_str()) {
            return Err(anyhow!(
                "duplicate candidate_id: {}",
                candidate.candidate_id
            ));
        }
    }
    Ok(())
}

fn canonicalize_json(value: &Value) -> Value {
    match value {
        Value::Object(map) => {
            let mut pairs = map.iter().collect::<Vec<_>>();
            pairs.sort_by(|(left, _), (right, _)| left.cmp(right));
            Value::Object(Map::from_iter(
                pairs
                    .into_iter()
                    .map(|(key, value)| (key.clone(), canonicalize_json(value))),
            ))
        }
        Value::Array(items) => Value::Array(items.iter().map(canonicalize_json).collect()),
        _ => value.clone(),
    }
}
