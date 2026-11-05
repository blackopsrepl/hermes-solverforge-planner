//! The JSON proposal contract.

use serde::Serialize;

#[derive(Debug, Clone, Serialize)]
pub struct Proposal {
    /// `NhardNmediumNsoft`-style score string from SolverForge.
    pub score: String,
    pub hard_feasible: bool,
    pub timezone: String,
    pub horizon_start: String,
    pub horizon_end: String,
    pub items: Vec<ProposalItem>,
}

#[derive(Debug, Clone, Serialize)]
pub struct ProposalItem {
    pub task_id: String,
    pub scheduled: bool,
    pub start_at: Option<String>,
    pub end_at: Option<String>,
    pub cognitive_penalty: i64,
    pub fatigue_penalty: i64,
    pub explanation: Option<String>,
    pub outcome: Outcome,
    pub busy_blockers: Vec<BusyBlocker>,
    pub busy_blockers_omitted: usize,
}

#[derive(Debug, Clone, Default, PartialEq, Eq, Serialize)]
#[serde(rename_all = "snake_case")]
pub enum Outcome {
    #[default]
    Scheduled,
    NoHardFeasibleSlot,
    FeasibleButNotSelected,
}

/// A busy block that removed an otherwise hard-feasible slot.
#[derive(Debug, Clone, Serialize)]
pub struct BusyBlocker {
    pub id: String,
    pub title: String,
    pub source: String,
    pub start_at: String,
    pub end_at: String,
}
