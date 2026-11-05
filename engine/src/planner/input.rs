//! The JSON problem contract. Everything is validated here, before a plan is
//! built, so the solver only ever sees well-formed input.

use std::collections::{BTreeMap, HashSet};

use serde::Deserialize;

use super::scheduling::weekday_index;
use super::time::parse_clock;
use super::PlannerError;

#[derive(Debug, Clone, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct Problem {
    /// IANA timezone every wall-clock string in the problem is read in.
    pub timezone: String,
    /// Optional "now" (local wall clock) for reproducible solves; defaults to
    /// the current time. Nothing may be scheduled before it.
    #[serde(default)]
    pub now: Option<String>,
    pub settings: Settings,
    pub tasks: Vec<Task>,
    /// `(before, after)` task-id pairs: `after` starts once `before` ends.
    #[serde(default)]
    pub dependencies: Vec<(String, String)>,
    #[serde(default)]
    pub busy: Vec<Busy>,
    #[serde(default)]
    pub applied: Vec<Applied>,
}

#[derive(Debug, Clone, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct Settings {
    /// Weekday (`mon`..`sun`) → working windows.
    pub availability: BTreeMap<String, Vec<Window>>,
    #[serde(default = "d_horizon")]
    pub horizon_days: i64,
    #[serde(default = "d_slot")]
    pub slot_minutes: i64,
    #[serde(default = "d_solve")]
    pub solve_seconds: i64,
    #[serde(default = "d_low")]
    pub priority_low_weight: i64,
    #[serde(default = "d_normal")]
    pub priority_normal_weight: i64,
    #[serde(default = "d_high")]
    pub priority_high_weight: i64,
    /// Focus windows per cognitive load; absent means no preference.
    #[serde(default)]
    pub low_window: Option<FocusWindow>,
    #[serde(default)]
    pub medium_window: Option<FocusWindow>,
    #[serde(default)]
    pub high_window: Option<FocusWindow>,
    #[serde(default = "d_streak")]
    pub high_streak_limit: i64,
    #[serde(default = "d_recovery")]
    pub recovery_minutes: i64,
    #[serde(default = "d_excess")]
    pub excess_high_penalty: i64,
}

#[derive(Debug, Clone, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct Window {
    pub start: String,
    pub end: String,
}

#[derive(Debug, Clone, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct FocusWindow {
    pub start: String,
    pub end: String,
    /// Soft penalty per minute of work outside the window.
    #[serde(default = "d_outside")]
    pub outside_penalty: i64,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, Deserialize, Default)]
#[serde(rename_all = "lowercase")]
pub enum Priority {
    Low,
    #[default]
    Normal,
    High,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, Deserialize, Default)]
#[serde(rename_all = "lowercase")]
pub enum Load {
    Low,
    #[default]
    Medium,
    High,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, Deserialize, Default)]
#[serde(rename_all = "lowercase")]
pub enum DeadlineKind {
    #[default]
    None,
    Hard,
    Soft,
}

#[derive(Debug, Clone, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct Task {
    pub id: String,
    pub duration_minutes: i64,
    #[serde(default)]
    pub priority: Priority,
    #[serde(default)]
    pub cognitive_load: Load,
    #[serde(default)]
    pub earliest_at: Option<String>,
    #[serde(default)]
    pub deadline_kind: DeadlineKind,
    #[serde(default)]
    pub deadline_at: Option<String>,
}

#[derive(Debug, Clone, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct Busy {
    pub id: String,
    #[serde(default)]
    pub title: String,
    #[serde(default)]
    pub source: String,
    pub start: String,
    pub end: String,
}

/// A block already on the calendar from an earlier applied proposal.
#[derive(Debug, Clone, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct Applied {
    pub task_id: String,
    pub start: String,
    pub end: String,
    #[serde(default)]
    pub cognitive_load: Load,
}

fn d_horizon() -> i64 {
    14
}
fn d_slot() -> i64 {
    15
}
fn d_solve() -> i64 {
    5
}
fn d_low() -> i64 {
    1
}
fn d_normal() -> i64 {
    5
}
fn d_high() -> i64 {
    25
}
fn d_streak() -> i64 {
    1
}
fn d_recovery() -> i64 {
    30
}
fn d_excess() -> i64 {
    60
}
fn d_outside() -> i64 {
    10
}

impl Problem {
    pub fn validate(&self) -> Result<(), PlannerError> {
        let s = &self.settings;
        let range = |name: &str, value: i64, lo: i64, hi: i64| {
            if (lo..=hi).contains(&value) {
                Ok(())
            } else {
                Err(PlannerError::Validation(format!(
                    "{name} must be between {lo} and {hi}"
                )))
            }
        };
        range("horizon_days", s.horizon_days, 1, 90)?;
        range("slot_minutes", s.slot_minutes, 5, 120)?;
        range("solve_seconds", s.solve_seconds, 1, 120)?;
        for (name, value) in [
            ("priority_low_weight", s.priority_low_weight),
            ("priority_normal_weight", s.priority_normal_weight),
            ("priority_high_weight", s.priority_high_weight),
        ] {
            range(name, value, 1, 1_000_000)?;
        }
        range("high_streak_limit", s.high_streak_limit, 1, 100)?;
        range("recovery_minutes", s.recovery_minutes, 0, 24 * 60)?;
        range("excess_high_penalty", s.excess_high_penalty, 0, 1_000_000)?;

        if s.availability.is_empty() {
            return Err(PlannerError::Validation(
                "configure at least one weekly availability window".into(),
            ));
        }
        for (day, windows) in &s.availability {
            if weekday_index(day).is_none() {
                return Err(PlannerError::Validation(format!("invalid weekday '{day}'")));
            }
            for window in windows {
                if parse_clock(&window.end)? <= parse_clock(&window.start)? {
                    return Err(PlannerError::Validation(format!(
                        "availability on '{day}' must end after it starts"
                    )));
                }
            }
        }
        for window in [&s.low_window, &s.medium_window, &s.high_window]
            .into_iter()
            .flatten()
        {
            parse_clock(&window.start)?;
            parse_clock(&window.end)?;
            range("outside_penalty", window.outside_penalty, 0, 1_000_000)?;
        }

        if self.tasks.is_empty() {
            return Err(PlannerError::Validation(
                "there are no tasks to plan".into(),
            ));
        }
        let mut ids = HashSet::new();
        for task in &self.tasks {
            if !ids.insert(task.id.as_str()) {
                return Err(PlannerError::Validation(format!(
                    "duplicate task id '{}'",
                    task.id
                )));
            }
            range("duration_minutes", task.duration_minutes, 5, 24 * 60)?;
            if task.deadline_kind != DeadlineKind::None && task.deadline_at.is_none() {
                return Err(PlannerError::Validation(format!(
                    "task '{}' has a {:?} deadline but no deadline_at",
                    task.id, task.deadline_kind
                )));
            }
        }
        let applied: HashSet<&str> = self.applied.iter().map(|a| a.task_id.as_str()).collect();
        for (before, after) in &self.dependencies {
            if !ids.contains(after.as_str()) {
                continue; // an edge into an already-applied task constrains nothing
            }
            if !ids.contains(before.as_str()) && !applied.contains(before.as_str()) {
                return Err(PlannerError::Conflict(format!(
                    "'{after}' must follow '{before}', which is neither a task to plan nor an applied block"
                )));
            }
        }
        Ok(())
    }
}
