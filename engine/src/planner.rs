//! Problem → proposal pipeline around the extracted planning model.

mod build;
pub mod constraints;
mod diagnostics;
pub mod input;
pub mod output;
mod scheduling;
mod solve;
pub mod time;

pub use output::{BusyBlocker, Outcome, Proposal, ProposalItem};
pub use solve::plan;

/// Every way a solve can fail, mapped to a stable machine-readable code.
#[derive(Debug, Clone, PartialEq, Eq)]
pub enum PlannerError {
    Validation(String),
    Conflict(String),
    Internal(String),
}

impl PlannerError {
    pub fn code(&self) -> &'static str {
        match self {
            Self::Validation(_) => "validation_error",
            Self::Conflict(_) => "conflict",
            Self::Internal(_) => "internal_error",
        }
    }
}

impl std::fmt::Display for PlannerError {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        match self {
            Self::Validation(m) | Self::Conflict(m) | Self::Internal(m) => f.write_str(m),
        }
    }
}

impl std::error::Error for PlannerError {}

#[cfg(test)]
mod tests;
