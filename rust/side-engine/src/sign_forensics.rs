//! Phase 61 sign_forensics module — Wave 0 scaffold.
//!
//! Phase 62/63 plans will populate audit primitives here.
//! Phase 61 itself keeps the heavy logic in `scripts/v4.4/` (Python),
//! invoked by `side-cli/src/cmd/sign_forensics.rs::run` via `uv run python`.

use serde::{Deserialize, Serialize};

/// Audit verdict emitted by the Phase 61 pipeline.
/// PASS = no config drift detected; FAIL = drift count >= 1, Phase 62 blocked.
#[derive(Debug, Clone, Copy, Serialize, Deserialize, PartialEq, Eq)]
pub enum AuditVerdict {
    Pass,
    Fail,
}
