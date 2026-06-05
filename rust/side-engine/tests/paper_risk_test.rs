use serde_json::json;
use side_engine::paper::risk::{
    build_paper_candidate, build_paper_v2_candidate, paper_apply_action_for_decision,
    paper_candidate_id, validate_paper_runtime_sizing_guard, PaperApplyAction, PaperCandidateInput,
    PaperCostModel, PaperRiskDecision, PaperRiskEvidence, PaperRiskEvidenceInput,
    PaperRiskExecutionState, PaperRiskMode, PaperV2CandidateInput, PAPER_CAP_APPLICATION_STATUS,
    PAPER_CAP_RUNTIME_SIZING_CLAIM_BLOCK_REASON_NOT_APPLIED,
    PAPER_CAP_RUNTIME_SIZING_DEPENDENCY_STATUS_PNL_INDEPENDENT_PROVEN, PAPER_COST_BASIS,
    PAPER_COST_MODEL_SCHEMA_VERSION, PAPER_FEE_MODEL_STATUS_EXPLICIT_NONZERO,
    PAPER_FEE_MODEL_STATUS_MISSING_OR_ZERO, PAPER_REQUESTED_SIZE_BASIS,
    PAPER_RISK_CANDIDATE_SCHEMA_VERSION, PAPER_V2_CANDIDATE_SCHEMA_VERSION,
    PAPER_V2_REQUESTED_SIZE_BASIS,
};
use side_engine::paper::{AuxSource, SlotConfig};
use std::collections::HashMap;

fn slot(aux: &str) -> SlotConfig {
    SlotConfig {
        asset: "USD/JPY".to_string(),
        strategy_name: "keltner".to_string(),
        params: HashMap::from([
            ("ema_period".to_string(), json!(20)),
            ("atr_period".to_string(), json!(14)),
        ]),
        aux_source: Some(AuxSource {
            id: aux.to_string(),
        }),
        timeframe: "1h".to_string(),
        leverage: Some(500.0),
    }
}

fn input<'a>(slot: &'a SlotConfig, data_fingerprint: &'a str) -> PaperCandidateInput<'a> {
    PaperCandidateInput {
        slot_index: 0,
        slot_id: "USD/JPY/keltner/^VIX#1",
        slot,
        config_fingerprint: "cfgabc",
        data_window_fingerprint: data_fingerprint,
        latest_bar_timestamp: "2026-05-13T00:00:00Z",
        requested_size: 2000.0,
        risk_mode: PaperRiskMode::Observe,
        artifact_root: "reports/v6.1/paper_risk",
    }
}

#[test]
fn paper_candidate_id_is_stable_and_prefixed() {
    let s = slot("yf:^VIX");
    let id_a = paper_candidate_id(&input(&s, "dataabc")).unwrap();
    let id_b = paper_candidate_id(&input(&s, "dataabc")).unwrap();
    assert_eq!(id_a, id_b);
    assert!(id_a.starts_with("paper.USDJPY.1h.keltner.p"));
    assert!(!id_a.starts_with("backtest."));
}

#[test]
fn paper_candidate_id_changes_for_aux_and_data_window() {
    let vix = slot("yf:^VIX");
    let gspc = slot("yf:^GSPC");
    let id_vix = paper_candidate_id(&input(&vix, "dataabc")).unwrap();
    let id_gspc = paper_candidate_id(&input(&gspc, "dataabc")).unwrap();
    let id_new_data = paper_candidate_id(&input(&vix, "datadef")).unwrap();
    assert_ne!(id_vix, id_gspc);
    assert_ne!(id_vix, id_new_data);
}

#[test]
fn paper_candidate_keeps_raw_slot_id_out_of_path_identity() {
    let s = slot("yf:^VIX");
    let candidate = build_paper_candidate(input(&s, "dataabc")).unwrap();
    assert_eq!(
        candidate.paper_candidate_schema_version,
        PAPER_RISK_CANDIDATE_SCHEMA_VERSION
    );
    assert_eq!(candidate.requested_size_basis, PAPER_REQUESTED_SIZE_BASIS);
    assert_eq!(candidate.slot_id, "USD/JPY/keltner/^VIX#1");
    assert!(!candidate.slot_key.contains('/'));
    assert!(!candidate.candidate_id.contains('/'));
}

#[test]
fn paper_v2_candidate_uses_slot_allocation_unit_and_versioned_identity() {
    let s = slot("yf:^VIX");
    let mut v1_input = input(&s, "dataabc");
    v1_input.risk_mode = PaperRiskMode::Apply;
    let v1_id = paper_candidate_id(&v1_input).unwrap();
    let candidate = build_paper_v2_candidate(PaperV2CandidateInput {
        base: v1_input,
        initial_capital: 10_000.0,
        slot_count: 1,
        effective_leverage: 500.0,
        runtime_accounting_mode: "legacy_gross",
    })
    .unwrap();

    assert_eq!(
        candidate.candidate_schema_version,
        PAPER_V2_CANDIDATE_SCHEMA_VERSION
    );
    assert_ne!(candidate.candidate_id, v1_id);
    assert!(candidate
        .candidate_id
        .starts_with("paper.USDJPY.1h.keltner.p"));
    assert_eq!(candidate.strategy_id, candidate.candidate_id);
    assert_eq!(candidate.surface.runtime_surface, "paper");
    assert_eq!(candidate.surface.surface_status, "implemented");
    assert_eq!(candidate.surface.analysis_scope, "none");
    assert_eq!(candidate.surface.analysis_scope_status, "not_applicable");
    assert_eq!(candidate.sizing.requested_size, 2000.0);
    assert_eq!(
        candidate.sizing.requested_size_basis,
        PAPER_V2_REQUESTED_SIZE_BASIS
    );
    assert_eq!(candidate.surface_payload.slot_id, "USD/JPY/keltner/^VIX#1");
    assert_eq!(candidate.surface_payload.slot_index, 0);
    assert_eq!(candidate.surface_payload.slot_key, "USD_JPY_keltner__VIX_1");
    assert_eq!(
        candidate.surface_payload.allocation_source,
        "PaperConfig::allocations"
    );
    assert_eq!(
        candidate.surface_payload.allocation_method,
        "initial_capital_divided_by_slot_count"
    );
    assert_eq!(candidate.surface_payload.initial_capital, 10_000.0);
    assert_eq!(candidate.surface_payload.slot_count, 1);
    assert_eq!(candidate.surface_payload.effective_leverage, 500.0);
    assert_eq!(
        candidate.surface_payload.runtime_accounting_mode,
        "legacy_gross"
    );
    assert_eq!(candidate.surface_payload.paper_risk_mode, "apply");
    assert!(candidate
        .validation_refs
        .contains(&"risk/contracts/v2/risk_contract_v2.schema.json".to_string()));
    assert!(candidate
        .validation_refs
        .contains(&"risk/contracts/v2/risk_contract_validator_result_v2.schema.json".to_string()));
}

#[test]
fn paper_cost_model_missing_or_zero_blocks_claims() {
    let model = PaperCostModel::new(0.0, 0.0, "cli").unwrap();
    let estimate = model.estimate(2000.0, 500.0, 0.0).unwrap();

    assert_eq!(model.schema_version, PAPER_COST_MODEL_SCHEMA_VERSION);
    assert_eq!(model.status, PAPER_FEE_MODEL_STATUS_MISSING_OR_ZERO);
    assert_eq!(model.cost_basis, PAPER_COST_BASIS);
    assert_eq!(estimate.gross_pnl, 0.0);
    assert_eq!(estimate.estimated_cost, 0.0);
    assert_eq!(estimate.estimated_net_pnl, 0.0);
    assert!(!model.estimated_net_pnl_claim_allowed);
    assert!(!model.parity_claim_allowed);
    assert!(!model.alpha_claim_allowed);
    assert_eq!(
        model.claim_block_reason.as_deref(),
        Some("missing_or_zero_cost_model")
    );
}

#[test]
fn paper_cost_model_explicit_nonzero_estimates_net_pnl() {
    let model = PaperCostModel::new(1.5, 0.5, "cli").unwrap();
    let estimate = model.estimate(2000.0, 500.0, 0.0).unwrap();

    assert_eq!(model.status, PAPER_FEE_MODEL_STATUS_EXPLICIT_NONZERO);
    assert_eq!(estimate.gross_pnl, 0.0);
    assert_eq!(estimate.cost_notional, 1_000_000.0);
    assert_eq!(estimate.estimated_cost, 200.0);
    assert_eq!(estimate.estimated_net_pnl, -200.0);
    assert!(model.estimated_net_pnl_claim_allowed);
    assert!(!model.parity_claim_allowed);
    assert!(!model.alpha_claim_allowed);
    assert_eq!(
        model.claim_block_reason.as_deref(),
        Some("runtime_net_pnl_not_integrated")
    );
}

#[test]
fn paper_cost_model_fingerprint_changes_when_cost_fields_change() {
    let a = PaperCostModel::new(1.5, 0.5, "cli").unwrap();
    let b = PaperCostModel::new(1.5, 0.5, "cli").unwrap();
    let c = PaperCostModel::new(2.0, 0.5, "cli").unwrap();

    assert_eq!(a.cost_model_fingerprint, b.cost_model_fingerprint);
    assert_ne!(a.cost_model_fingerprint, c.cost_model_fingerprint);
    assert!(a.cost_model_fingerprint.starts_with("sha256:"));
}

#[test]
fn paper_cost_model_fingerprint_preserves_sub_micro_bps_changes() {
    let a = PaperCostModel::new(1.0000001, 0.5, "cli").unwrap();
    let b = PaperCostModel::new(1.0000002, 0.5, "cli").unwrap();

    assert_ne!(a.cost_model_fingerprint, b.cost_model_fingerprint);
    assert!(a.cost_model_fingerprint.starts_with("sha256:"));
    assert!(b.cost_model_fingerprint.starts_with("sha256:"));
}

#[test]
fn paper_cost_model_rejects_non_finite_estimate_outputs() {
    let zero_model = PaperCostModel::new(0.0, 0.0, "cli").unwrap();
    assert!(zero_model.estimate(f64::MAX, 2.0, 0.0).is_err());

    let max_cost_model = PaperCostModel::new(10_000.0, 10_000.0, "cli").unwrap();
    assert!(max_cost_model.estimate(f64::MAX, 1.0, 0.0).is_err());

    let finite_cost_model = PaperCostModel::new(5_000.0, 0.0, "cli").unwrap();
    assert!(finite_cost_model
        .estimate(f64::MAX, 1.0, -f64::MAX)
        .is_err());
}

#[test]
fn paper_cost_model_rejects_invalid_values() {
    assert!(PaperCostModel::new(-0.1, 0.0, "cli").is_err());
    assert!(PaperCostModel::new(f64::NAN, 0.0, "cli").is_err());
    assert!(PaperCostModel::new(0.0, f64::INFINITY, "cli").is_err());
}

#[test]
fn observe_evidence_records_cap_deferred_and_no_runtime_sizing() {
    let s = slot("yf:^VIX");
    let candidate = build_paper_candidate(input(&s, "dataabc")).unwrap();
    let evidence = PaperRiskEvidence::from_input(PaperRiskEvidenceInput {
        run_id: "run1",
        tick_id: "tick1",
        candidate: &candidate,
        decision: PaperRiskDecision {
            decision_class: "cap".to_string(),
            allowed_size: 0.25,
            binding_rule: "risk-policy.v1.cap".to_string(),
            fail_close_reason: "cap_rule".to_string(),
            policy_version: "risk-policy.v1.test".to_string(),
            decision_artifact_path: "reports/v6.1/paper_risk/decision.json".to_string(),
            decision_artifact_sha256: "abc123".to_string(),
            policy_path: "policy.json".to_string(),
            policy_sha256: "def456".to_string(),
            validator_valid: true,
            validator_errors: vec![],
        },
        candidate_artifact_path: "reports/v6.1/paper_risk/candidate.json",
        candidate_artifact_sha256: "789abc",
        execution_state: PaperRiskExecutionState::Observed,
        position_mutation: false,
        db_before: "not_captured",
        db_after: "not_captured",
        db_before_artifact_path: "reports/v6.5/explicit_nonzero_cost/db_before.json",
        db_before_artifact_sha256: "sha256:before",
        db_after_artifact_path: "reports/v6.5/explicit_nonzero_cost/db_after.json",
        db_after_artifact_sha256: "sha256:after",
        health_artifact_path: "reports/v6.5/explicit_nonzero_cost/health.json",
        health_artifact_sha256: "sha256:health",
        parity_status: "observed",
        cost_model: PaperCostModel::new(1.5, 0.5, "cli").unwrap(),
        effective_leverage: 500.0,
        gross_pnl: 0.0,
        runtime_sizing_applied: false,
        actual_effective_size: candidate.requested_size,
    });
    assert!(!evidence.runtime_sizing_applied);
    assert_eq!(
        evidence.cap_application_status.as_deref(),
        Some(PAPER_CAP_APPLICATION_STATUS)
    );
    assert_eq!(
        evidence.cost_model_schema_version,
        PAPER_COST_MODEL_SCHEMA_VERSION
    );
    assert_eq!(
        evidence.paper_fee_model_status,
        PAPER_FEE_MODEL_STATUS_EXPLICIT_NONZERO
    );
    assert_eq!(evidence.fee_bps, 1.5);
    assert_eq!(evidence.spread_bps, 0.5);
    assert_eq!(evidence.cost_basis, PAPER_COST_BASIS);
    assert_eq!(evidence.cost_model_source, "cli");
    assert_eq!(evidence.cost_notional, 1_000_000.0);
    assert_eq!(evidence.gross_pnl, 0.0);
    assert_eq!(evidence.estimated_cost, 200.0);
    assert_eq!(evidence.estimated_net_pnl, -200.0);
    assert!(evidence.estimated_net_pnl_claim_allowed);
    assert!(!evidence.parity_claim_allowed);
    assert!(!evidence.alpha_claim_allowed);
    assert_eq!(
        evidence.claim_block_reason.as_deref(),
        Some("runtime_net_pnl_not_integrated")
    );
    assert_eq!(
        evidence.db_before_artifact_path,
        "reports/v6.5/explicit_nonzero_cost/db_before.json"
    );
    assert_eq!(evidence.db_before_artifact_sha256, "sha256:before");
    assert_eq!(
        evidence.health_artifact_path,
        "reports/v6.5/explicit_nonzero_cost/health.json"
    );
    assert_eq!(evidence.actual_effective_size, candidate.requested_size);
    assert_eq!(evidence.would_effective_size, 0.25);
    assert_eq!(
        evidence.cap_runtime_sizing_dependency_status,
        PAPER_CAP_RUNTIME_SIZING_DEPENDENCY_STATUS_PNL_INDEPENDENT_PROVEN
    );
    assert!(!evidence.cap_depends_on_runtime_pnl);
    assert_eq!(
        evidence.cap_dependency_proof,
        "cap sizing uses requested_size and validator allowed_size only; runtime equity, realized PnL, trades.pnl, and runtime_pnl_ledger are not read by the current paper cap apply boundary"
    );
    assert!(!evidence.cap_runtime_sizing_claim_allowed);
    assert_eq!(
        evidence.cap_runtime_sizing_claim_block_reason.as_deref(),
        Some(PAPER_CAP_RUNTIME_SIZING_CLAIM_BLOCK_REASON_NOT_APPLIED)
    );
}

#[test]
fn zero_cost_cap_dependency_proof_still_blocks_cap_claim() {
    let s = slot("yf:^VIX");
    let candidate = build_paper_candidate(input(&s, "dataabc")).unwrap();
    let evidence = PaperRiskEvidence::from_input(PaperRiskEvidenceInput {
        run_id: "run1",
        tick_id: "tick1",
        candidate: &candidate,
        decision: PaperRiskDecision {
            decision_class: "cap".to_string(),
            allowed_size: 0.25,
            binding_rule: "risk-policy.v1.cap".to_string(),
            fail_close_reason: "cap_rule".to_string(),
            policy_version: "risk-policy.v1.test".to_string(),
            decision_artifact_path: "reports/v6.8/decision.json".to_string(),
            decision_artifact_sha256: "sha256:decision".to_string(),
            policy_path: "policy.json".to_string(),
            policy_sha256: "sha256:policy".to_string(),
            validator_valid: true,
            validator_errors: vec![],
        },
        candidate_artifact_path: "reports/v6.8/candidate.json",
        candidate_artifact_sha256: "sha256:candidate",
        execution_state: PaperRiskExecutionState::Observed,
        position_mutation: false,
        db_before: "not_captured",
        db_after: "not_captured",
        db_before_artifact_path: "reports/v6.8/missing_or_zero_cap_dependency/db_before.json",
        db_before_artifact_sha256: "sha256:before",
        db_after_artifact_path: "reports/v6.8/missing_or_zero_cap_dependency/db_after.json",
        db_after_artifact_sha256: "sha256:after",
        health_artifact_path: "reports/v6.8/missing_or_zero_cap_dependency/health.json",
        health_artifact_sha256: "sha256:health",
        parity_status: "observed",
        cost_model: PaperCostModel::new(0.0, 0.0, "cli").unwrap(),
        effective_leverage: 500.0,
        gross_pnl: 0.0,
        runtime_sizing_applied: false,
        actual_effective_size: candidate.requested_size,
    });

    assert_eq!(
        evidence.cap_runtime_sizing_dependency_status,
        PAPER_CAP_RUNTIME_SIZING_DEPENDENCY_STATUS_PNL_INDEPENDENT_PROVEN
    );
    assert!(!evidence.cap_depends_on_runtime_pnl);
    assert!(!evidence.cap_runtime_sizing_claim_allowed);
    assert_eq!(
        evidence.cap_runtime_sizing_claim_block_reason.as_deref(),
        Some("missing_or_zero_cost_model")
    );
    assert!(!evidence.runtime_sizing_applied);
    assert_eq!(evidence.actual_effective_size, candidate.requested_size);
    assert_eq!(evidence.would_effective_size, 0.25);
}

#[test]
fn zero_cost_evidence_blocks_parity_and_alpha_claims() {
    let s = slot("yf:^VIX");
    let candidate = build_paper_candidate(input(&s, "dataabc")).unwrap();
    let evidence = PaperRiskEvidence::from_input(PaperRiskEvidenceInput {
        run_id: "run1",
        tick_id: "tick1",
        candidate: &candidate,
        decision: PaperRiskDecision {
            decision_class: "size".to_string(),
            allowed_size: 2000.0,
            binding_rule: "risk-policy.v1.size".to_string(),
            fail_close_reason: "size_rule".to_string(),
            policy_version: "risk-policy.v1.test".to_string(),
            decision_artifact_path: "reports/v6.5/decision.json".to_string(),
            decision_artifact_sha256: "sha256:decision".to_string(),
            policy_path: "policy.json".to_string(),
            policy_sha256: "sha256:policy".to_string(),
            validator_valid: true,
            validator_errors: vec![],
        },
        candidate_artifact_path: "reports/v6.5/candidate.json",
        candidate_artifact_sha256: "sha256:candidate",
        execution_state: PaperRiskExecutionState::Observed,
        position_mutation: false,
        db_before: "not_captured",
        db_after: "not_captured",
        db_before_artifact_path: "reports/v6.5/missing_or_zero_cost/db_before.json",
        db_before_artifact_sha256: "sha256:before",
        db_after_artifact_path: "reports/v6.5/missing_or_zero_cost/db_after.json",
        db_after_artifact_sha256: "sha256:after",
        health_artifact_path: "reports/v6.5/missing_or_zero_cost/health.json",
        health_artifact_sha256: "sha256:health",
        parity_status: "observed",
        cost_model: PaperCostModel::missing_or_zero(),
        effective_leverage: 500.0,
        gross_pnl: 0.0,
        runtime_sizing_applied: false,
        actual_effective_size: candidate.requested_size,
    });
    assert_eq!(
        evidence.paper_fee_model_status,
        PAPER_FEE_MODEL_STATUS_MISSING_OR_ZERO
    );
    assert!(!evidence.parity_claim_allowed);
    assert!(!evidence.alpha_claim_allowed);
    assert_eq!(
        evidence.claim_block_reason.as_deref(),
        Some("missing_or_zero_cost_model")
    );
    assert_eq!(evidence.estimated_cost, 0.0);
    assert_eq!(evidence.estimated_net_pnl, 0.0);
    assert!(!evidence.runtime_sizing_applied);
    assert_eq!(evidence.actual_effective_size, candidate.requested_size);
}

#[test]
fn explicit_nonzero_cap_apply_records_runtime_sizing_applied() {
    let s = slot("yf:^VIX");
    let candidate = build_paper_candidate(input(&s, "dataabc")).unwrap();
    let evidence = PaperRiskEvidence::from_input(PaperRiskEvidenceInput {
        run_id: "run1",
        tick_id: "tick1",
        candidate: &candidate,
        decision: PaperRiskDecision {
            decision_class: "cap".to_string(),
            allowed_size: 0.25,
            binding_rule: "risk-policy.v1.cap".to_string(),
            fail_close_reason: "cap_rule".to_string(),
            policy_version: "risk-policy.v1.test".to_string(),
            decision_artifact_path: "reports/v6.9/decision.json".to_string(),
            decision_artifact_sha256: "sha256:decision".to_string(),
            policy_path: "policy.json".to_string(),
            policy_sha256: "sha256:policy".to_string(),
            validator_valid: true,
            validator_errors: vec![],
        },
        candidate_artifact_path: "reports/v6.9/candidate.json",
        candidate_artifact_sha256: "sha256:candidate",
        execution_state: PaperRiskExecutionState::Continued,
        position_mutation: false,
        db_before: "not_captured",
        db_after: "not_captured",
        db_before_artifact_path: "reports/v6.9/explicit_nonzero_cap_apply/db_before.json",
        db_before_artifact_sha256: "sha256:before",
        db_after_artifact_path: "reports/v6.9/explicit_nonzero_cap_apply/db_after.json",
        db_after_artifact_sha256: "sha256:after",
        health_artifact_path: "reports/v6.9/explicit_nonzero_cap_apply/health.json",
        health_artifact_sha256: "sha256:health",
        parity_status: "observed",
        cost_model: PaperCostModel::new(1.5, 0.5, "cli").unwrap(),
        effective_leverage: 500.0,
        gross_pnl: 0.0,
        runtime_sizing_applied: true,
        actual_effective_size: 0.25,
    });

    assert!(evidence.runtime_sizing_applied);
    assert_eq!(evidence.actual_effective_size, 0.25);
    assert_eq!(evidence.would_effective_size, 0.25);
    assert_eq!(evidence.cap_application_status.as_deref(), Some("applied"));
    assert_eq!(
        evidence.cap_runtime_sizing_dependency_status,
        PAPER_CAP_RUNTIME_SIZING_DEPENDENCY_STATUS_PNL_INDEPENDENT_PROVEN
    );
    assert!(!evidence.cap_depends_on_runtime_pnl);
    assert!(evidence.cap_runtime_sizing_claim_allowed);
    assert_eq!(evidence.cap_runtime_sizing_claim_block_reason, None);
}

#[test]
fn paper_v2_evidence_adds_public_contract_version_proof() {
    let s = slot("yf:^VIX");
    let mut candidate_input = input(&s, "dataabc");
    candidate_input.risk_mode = PaperRiskMode::Apply;
    let candidate = build_paper_v2_candidate(PaperV2CandidateInput {
        base: candidate_input,
        initial_capital: 10_000.0,
        slot_count: 1,
        effective_leverage: 500.0,
        runtime_accounting_mode: "legacy_gross",
    })
    .unwrap();
    let evidence = PaperRiskEvidence::from_input(PaperRiskEvidenceInput {
        run_id: "run1",
        tick_id: "tick1",
        candidate: &candidate,
        decision: PaperRiskDecision {
            decision_class: "cap".to_string(),
            allowed_size: 0.25,
            binding_rule: "risk-policy.v1.cap".to_string(),
            fail_close_reason: "not_fail_closed".to_string(),
            policy_version: "risk-policy.v1.test".to_string(),
            decision_artifact_path: "reports/risk-contract-v2/paper-runtime-adoption/decision.json"
                .to_string(),
            decision_artifact_sha256: "sha256:decision".to_string(),
            policy_path: "policy.json".to_string(),
            policy_sha256: "sha256:policy".to_string(),
            validator_valid: true,
            validator_errors: vec![],
        },
        candidate_artifact_path: "reports/risk-contract-v2/paper-runtime-adoption/candidate.json",
        candidate_artifact_sha256: "sha256:candidate",
        execution_state: PaperRiskExecutionState::Continued,
        position_mutation: false,
        db_before: "not_captured",
        db_after: "not_captured",
        db_before_artifact_path: "not_captured",
        db_before_artifact_sha256: "not_captured",
        db_after_artifact_path: "not_captured",
        db_after_artifact_sha256: "not_captured",
        health_artifact_path: "not_captured",
        health_artifact_sha256: "not_captured",
        parity_status: "observed",
        cost_model: PaperCostModel::new(1.5, 0.5, "cli").unwrap(),
        effective_leverage: 500.0,
        gross_pnl: 0.0,
        runtime_sizing_applied: true,
        actual_effective_size: 0.25,
    });

    assert_eq!(evidence.requested_size_basis, PAPER_V2_REQUESTED_SIZE_BASIS);
    assert_eq!(
        evidence.risk_contract_schema_version.as_deref(),
        Some("risk_contract.v2")
    );
    assert_eq!(evidence.risk_contract_version.as_deref(), Some("v2"));
    assert_eq!(
        evidence.validator_result_schema_version.as_deref(),
        Some("risk_contract_validator_result.v2")
    );
    assert_eq!(
        evidence.validated_schema_ref.as_deref(),
        Some("risk/contracts/v2/risk_contract_v2.schema.json")
    );
    assert_eq!(
        evidence.validator.as_deref(),
        Some("scripts/validate_risk_contract.py")
    );
}

#[test]
fn zero_cost_cap_apply_applies_sizing_but_blocks_claim() {
    let s = slot("yf:^VIX");
    let candidate = build_paper_candidate(input(&s, "dataabc")).unwrap();
    let evidence = PaperRiskEvidence::from_input(PaperRiskEvidenceInput {
        run_id: "run1",
        tick_id: "tick1",
        candidate: &candidate,
        decision: PaperRiskDecision {
            decision_class: "cap".to_string(),
            allowed_size: 0.25,
            binding_rule: "risk-policy.v1.cap".to_string(),
            fail_close_reason: "cap_rule".to_string(),
            policy_version: "risk-policy.v1.test".to_string(),
            decision_artifact_path: "reports/v6.9/decision.json".to_string(),
            decision_artifact_sha256: "sha256:decision".to_string(),
            policy_path: "policy.json".to_string(),
            policy_sha256: "sha256:policy".to_string(),
            validator_valid: true,
            validator_errors: vec![],
        },
        candidate_artifact_path: "reports/v6.9/candidate.json",
        candidate_artifact_sha256: "sha256:candidate",
        execution_state: PaperRiskExecutionState::Continued,
        position_mutation: false,
        db_before: "not_captured",
        db_after: "not_captured",
        db_before_artifact_path: "reports/v6.9/missing_or_zero_cap_apply/db_before.json",
        db_before_artifact_sha256: "sha256:before",
        db_after_artifact_path: "reports/v6.9/missing_or_zero_cap_apply/db_after.json",
        db_after_artifact_sha256: "sha256:after",
        health_artifact_path: "reports/v6.9/missing_or_zero_cap_apply/health.json",
        health_artifact_sha256: "sha256:health",
        parity_status: "observed",
        cost_model: PaperCostModel::missing_or_zero(),
        effective_leverage: 500.0,
        gross_pnl: 0.0,
        runtime_sizing_applied: true,
        actual_effective_size: 0.25,
    });

    assert!(evidence.runtime_sizing_applied);
    assert_eq!(evidence.actual_effective_size, 0.25);
    assert!(!evidence.cap_runtime_sizing_claim_allowed);
    assert_eq!(
        evidence.cap_runtime_sizing_claim_block_reason.as_deref(),
        Some("missing_or_zero_cost_model")
    );
}

#[test]
fn paper_cap_runtime_sizing_guard_rejects_expansion() {
    let err = validate_paper_runtime_sizing_guard("cap", 100.0, 100.01)
        .expect_err("cap must not expand runtime size beyond requested_size");

    assert!(err
        .to_string()
        .contains("paper cap allowed_size must be <= requested_size"));
}

#[test]
fn paper_cap_runtime_sizing_guard_rejects_negative_allowed_size() {
    let err = validate_paper_runtime_sizing_guard("cap", 100.0, -0.01)
        .expect_err("cap must fail closed on negative allowed_size");

    assert!(err
        .to_string()
        .contains("paper cap allowed_size must be finite and non-negative"));
}

#[test]
fn paper_runtime_sizing_guard_allows_non_expanding_cap_and_non_cap_size() {
    validate_paper_runtime_sizing_guard("cap", 100.0, 25.0).unwrap();
    validate_paper_runtime_sizing_guard("size", 100.0, 150.0).unwrap();
}

#[test]
fn stop_classes_map_to_expected_paper_apply_actions() {
    assert_eq!(
        paper_apply_action_for_decision("reject").unwrap(),
        PaperApplyAction::StopSlot
    );
    assert_eq!(
        paper_apply_action_for_decision("block").unwrap(),
        PaperApplyAction::StopSlot
    );
    assert_eq!(
        paper_apply_action_for_decision("kill").unwrap(),
        PaperApplyAction::StopTick
    );
    assert_eq!(
        paper_apply_action_for_decision("cap").unwrap(),
        PaperApplyAction::ApplySizing
    );
}
