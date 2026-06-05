# WFD Nondeterminism RCA — Phase 100 Closure

## 1. Closure Target

**GCP-V412-03(a)** — Phase 99 deferred flag: `wfd-rerun` deterministic sha256.

This document closes flag (a) by **characterising the nondeterminism residue** per D-B1.
The actual fix is deferred to Phase 102 per D-B2.

## 2. Decision Frame (D-B1 / D-B2 from CONTEXT.md)

| Decision | Description |
|----------|-------------|
| **D-B1** | Acknowledge nondeterminism residue rather than block Phase 100 ship on a fix |
| **D-B2** | Actual deterministic fix deferred to Phase 102 wave-0 prerequisite |

Phase 100 closes GCP-V412-03(a) by **characterising** the residue and documenting
the D-A2 mask recipe that cancels the H1+H2 sources. The H3/H4 sources (if present)
are acknowledged and handed off to Phase 102.

## 3. Hypothesis Tree (from RESEARCH.md)

| Hypothesis | Description | Status |
|------------|-------------|--------|
| **H1** | `data_provenance` string drift — wall-clock + git sha embedded per `wfd_rerun.rs:108-112` | **Cancelled by D-A2 mask recipe** (`jq -cS '.data_provenance = "<MASKED>"'`) |
| **H2** | `serde_json` HashMap key ordering — non-deterministic field order in JSON output | **Cancelled by `jq -cS`** (canonical sorted keys) |
| **H3** | `rand::thread_rng()` thread-locality drift — each thread seeds independently | **Residue: characterisation pending real probe** |
| **H4** | Parallel float reduction order — `rayon` `par_iter().sum()` float associativity | **Residue: characterisation pending real probe** |

The D-A2 masked-sha256 formula (`jq -cS '.data_provenance = "<MASKED>"' | sha256sum`)
definitively cancels H1 and H2. Whether H3/H4 contribute residual drift requires a
real probe (see Section 4).

## 4. Probe Results

> **NOTE: [real probe pending — re-run before Phase 101 SEAL]**
>
> Fleet was deferred at CHECKPOINT 1 (developer chose "wave 1 approved, fleet deferred").
> The `investigate_wfd_nondeterminism.sh` script was run in DRY_RUN mode only.
> Real probe results will be populated when Phase 101 SEAL prerequisites are met.

Raw DRY_RUN output from `investigate_wfd_nondeterminism.sh`:

```json
{"h1_h2_masked_sha256_strict_match": "DRY_RUN",
 "h3_h4_singlethread_strict_match": "DRY_RUN",
 "out_dir": "/tmp/rca_dry",
 "rca_branch_outcome": "DRY_RUN_pending_real_probe"}
```

Fields to populate after real probe:
- `h1_h2_masked_sha256_strict_match`: expected `1` (H1+H2 cancelled by mask)
- `h3_h4_singlethread_strict_match`: `1` = H3/H4 isolated to parallel reduction; `0` = deeper residue
- `rca_branch_outcome`: one of `H1+H2_only_residue_resolved` / `H3_or_H4_isolated_to_parallel_reduction` / `H3+H4_unresolved_residue_acknowledged`

## 5. Conclusion

> **Branch pending real probe execution.**

Based on the hypothesis analysis:

- **Branch A (H1+H2 only):** If real probe shows `h1_h2_masked_sha256_strict_match=1`,
  the D-A2 SEAL formula is sufficient — masked-sha256 strict-match achieved.
  Flag (a) FULLY CLOSED.

- **Branch B (H3 or H4 isolated):** If `h1_h2_masked_sha256_strict_match=0` but
  `h3_h4_singlethread_strict_match=1`, the residue is isolated to parallel float reduction.
  D-B2 Phase 102 fix scope: serial reduction OR seeded parallel RNG.
  Flag (a) CHARACTERISED — residue documented + Phase 102 ticket filed.

- **Branch C (unresolved):** If even single-thread (`RAYON_NUM_THREADS=1`) mismatches,
  deeper RCA needed (FFI, time-of-day, OS scheduler).
  Flag (a) RESIDUE ACKNOWLEDGED per D-B1 — Phase 102 ticket includes "RCA continuation" subtask.

**Current status (fleet deferred):** Flag (a) is RESIDUE ACKNOWLEDGED per D-B1.
The D-A2 mask recipe covers H1+H2. H3/H4 characterisation is a Phase 101 prerequisite
before SEAL (run `I_KNOW_THIS_TAKES_TIME=1 bash scripts/v4.12/investigate_wfd_nondeterminism.sh`
then update this document with real branch outcome).

## 6. Phase 102 Hand-Off

Whichever branch applies, Phase 102 must update:

- `config/v4.12/workload_spec_v412.json` → `deterministic_requirements.parallel_float_reduction_policy`
- `config/v4.12/workload_spec_v412.json` → `deterministic_requirements.rng_seed_policy`

**Test that must pass before Phase 102 ship:**
```bash
bash scripts/wfd_rerun_repro_check.sh dev_run vm_run
```
Exit 0 over a full 192-cell run (all 4 pairs × all 3 events × 16 slots per cell).

## 7. Sign-Off

| Role | Identity | Date |
|------|----------|------|
| Author | Phase 100 plan author | 2026-04-26 |
| Verifier | Phase 100 executor subagent (Claude) | 2026-04-26 |
| Reviewer | Phase 101 SEAL agent (pending) | — |

**GCP-V412-03(a) status: RESIDUE ACKNOWLEDGED per D-B1.**
Real probe pending before Phase 101 SEAL. See Section 4 for re-run instructions.
