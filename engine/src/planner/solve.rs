//! One solve: build the plan, run SolverForge, turn the result into a proposal
//! whose per-task penalties are proven to reconcile with the solver's own
//! score analysis.

use solverforge::{Analyzable, SolverEvent};

use super::build::build_plan;
use super::constraints::{minutes_outside, names, recovery_penalty, TaskInterval};
use super::diagnostics;
use super::input::Problem;
use super::output::{Outcome, Proposal, ProposalItem};
use super::time::local;
use super::PlannerError;
use crate::planner_domain::{SolverPlan, PLANNER_MANAGER};

/// Solves `problem` and returns a proposal. Pure apart from wall-clock time.
pub fn plan(problem: &Problem) -> Result<Proposal, PlannerError> {
    let built = build_plan(problem)?;
    let timezone = built.timezone;
    let horizon_end = built.horizon_end;
    let solved = run(built.plan)?;
    // Every hard rule binds assigned tasks only, so an empty schedule is always
    // feasible and the best solution is never worse. A hard-infeasible result
    // would stack tasks on events or each other: refuse it.
    if solved.score.is_some_and(|score| score.hard() < 0) {
        return Err(PlannerError::Internal(format!(
            "planner returned a hard-infeasible solution ({})",
            solved.score.map(|s| s.to_string()).unwrap_or_default()
        )));
    }
    let penalties = task_penalties(&solved)?;

    let items = solved
        .tasks
        .iter()
        .map(|task| {
            let selected = task.start().zip(task.end());
            let entry = &penalties[task.index];
            let evidence = match selected {
                Some(_) => diagnostics::UnscheduledEvidence::scheduled(),
                None => diagnostics::classify(&solved, horizon_end, task.index),
            };
            ProposalItem {
                task_id: task.task_id.clone(),
                scheduled: selected.is_some(),
                start_at: selected.map(|(start, _)| local(start, timezone)),
                end_at: selected.map(|(_, end)| local(end, timezone)),
                cognitive_penalty: entry.cognitive,
                fatigue_penalty: entry.fatigue,
                explanation: explanation(selected.is_some(), entry, &evidence.outcome),
                outcome: evidence.outcome,
                busy_blockers: evidence.busy_blockers,
                busy_blockers_omitted: evidence.busy_blockers_omitted,
            }
        })
        .collect();

    let horizon_start = solved
        .slots
        .first()
        .map(|slot| local(slot.start, timezone))
        .ok_or_else(|| {
            PlannerError::Validation("the planner horizon produced no candidate slots".into())
        })?;
    Ok(Proposal {
        score: solved
            .score
            .map(|score| score.to_string())
            .unwrap_or_default(),
        hard_feasible: solved.score.is_none_or(|score| score.hard() >= 0),
        timezone: problem.timezone.clone(),
        horizon_start,
        horizon_end: local(horizon_end, timezone),
        items,
    })
}

fn run(plan: SolverPlan) -> Result<SolverPlan, PlannerError> {
    let (job, mut events) = PLANNER_MANAGER
        .solve(plan)
        .map_err(|e| PlannerError::Internal(e.to_string()))?;
    while let Some(event) = events.blocking_recv() {
        match event {
            SolverEvent::Completed { solution, .. } => {
                PLANNER_MANAGER
                    .delete(job)
                    .map_err(|e| PlannerError::Internal(e.to_string()))?;
                return Ok(solution);
            }
            SolverEvent::Failed { error, .. } => {
                let _ = PLANNER_MANAGER.delete(job);
                return Err(PlannerError::Internal(format!("planner failed: {error}")));
            }
            SolverEvent::Cancelled { .. } => {
                let _ = PLANNER_MANAGER.delete(job);
                return Err(PlannerError::Conflict(
                    "planner optimization was cancelled".into(),
                ));
            }
            _ => {}
        }
    }
    let _ = PLANNER_MANAGER.delete(job);
    Err(PlannerError::Internal(
        "planner ended without a terminal result".into(),
    ))
}

/// Soft penalties owned by the task the scoring rule charges.
#[derive(Clone, Default)]
struct TaskPenalties {
    cognitive: i64,
    fatigue: i64,
}

fn task_penalties(plan: &SolverPlan) -> Result<Vec<TaskPenalties>, PlannerError> {
    let intervals: Vec<Option<TaskInterval>> = plan.tasks.iter().map(TaskInterval::of).collect();
    let mut penalties = vec![TaskPenalties::default(); plan.tasks.len()];
    for (index, interval) in intervals.iter().enumerate() {
        let Some(interval) = interval else { continue };
        let cognitive = plan
            .cognitive_windows
            .iter()
            .find(|window| window.load == plan.tasks[index].load && window.outside_penalty > 0)
            .map_or(0, |window| {
                minutes_outside(interval, window.start, window.end) * window.outside_penalty
            });
        let fatigue = recovery_penalty(interval, intervals.iter().flatten());
        penalties[index] = TaskPenalties { cognitive, fatigue };
    }
    reconcile_with_analysis(plan, &penalties)?;
    Ok(penalties)
}

/// Per-item penalties must add up to the solver's aggregate, so a proposal can
/// never disagree with the model that produced it.
fn reconcile_with_analysis(
    plan: &SolverPlan,
    penalties: &[TaskPenalties],
) -> Result<(), PlannerError> {
    let analysis = plan.analyze();
    let soft = |name: &str| {
        analysis
            .constraints
            .iter()
            .find(|constraint| constraint.name == name)
            .map_or(0, |constraint| constraint.score.soft())
    };
    let cognitive: i64 = penalties.iter().map(|p| p.cognitive).sum();
    let fatigue: i64 = penalties.iter().map(|p| p.fatigue).sum();
    let (want_cognitive, want_fatigue) = (
        -soft(names::COGNITIVE_WINDOWS),
        -soft(names::HIGH_LOAD_RECOVERY),
    );
    if cognitive != want_cognitive || fatigue != want_fatigue {
        return Err(PlannerError::Internal(format!(
            "proposal penalties do not reconcile with score analysis: cognitive {cognitive} vs {want_cognitive}, fatigue {fatigue} vs {want_fatigue}"
        )));
    }
    Ok(())
}

fn explanation(scheduled: bool, entry: &TaskPenalties, outcome: &Outcome) -> Option<String> {
    if !scheduled {
        return Some(
            match outcome {
                Outcome::NoHardFeasibleSlot => "No slot in the horizon satisfies every hard rule.",
                Outcome::FeasibleButNotSelected => {
                    "Feasible slots exist, but not alongside the higher-priority tasks."
                }
                Outcome::Scheduled => "Not scheduled within the horizon.",
            }
            .into(),
        );
    }
    if entry.fatigue > 0 {
        return Some("Follows heavy work without the configured recovery gap.".into());
    }
    if entry.cognitive > 0 {
        return Some("Partly outside its focus window.".into());
    }
    None
}
