//! Pure translation of a validated problem into SolverForge facts and
//! entities — Planner123's loader without its database. It builds the slot
//! grid, canonical availability, cognitive windows, busy time, applied blocks
//! and task entities; every legality and quality rule stays in `constraints`.

use std::collections::{BTreeMap, HashMap};

use chrono::{DateTime, NaiveTime, TimeZone, Utc};
use chrono_tz::Tz;

use super::constraints::{scale_assignment_penalties, validate_soft_score_range};
use super::input::{DeadlineKind, FocusWindow, Load, Priority, Problem};
use super::scheduling::{local_horizon_end, make_slots, weekday_index};
use super::time::{parse_clock, resolve_utc};
use super::PlannerError;
use crate::planner_domain::{
    SolverAppliedBlock, SolverAvailability, SolverBusy, SolverCognitiveWindow, SolverPlan,
    SolverTask,
};

pub(super) struct BuiltPlan {
    pub plan: SolverPlan,
    pub timezone: Tz,
    pub horizon_end: DateTime<Utc>,
}

pub(super) fn build_plan(problem: &Problem) -> Result<BuiltPlan, PlannerError> {
    problem.validate()?;
    let settings = &problem.settings;
    let timezone = super::time::parse_timezone(&problem.timezone)?;
    let now = match &problem.now {
        Some(value) => resolve_utc(value, timezone)?,
        None => Utc::now(),
    };
    let local_now = now.with_timezone(&timezone);
    let local_start = timezone
        .from_local_datetime(&local_now.date_naive().and_hms_opt(0, 0, 0).unwrap())
        .single()
        .ok_or_else(|| {
            PlannerError::Validation("planner horizon starts at an invalid local time".into())
        })?;
    let horizon_end = local_horizon_end(local_start, settings.horizon_days)?;
    let slots = make_slots(
        local_start.with_timezone(&Utc),
        horizon_end,
        settings.slot_minutes,
    )?;
    let origin = slots
        .first()
        .map(|slot| slot.start)
        .unwrap_or(local_start.with_timezone(&Utc));

    let index_of: HashMap<&str, usize> = problem
        .tasks
        .iter()
        .enumerate()
        .map(|(i, task)| (task.id.as_str(), i))
        .collect();

    let busy = problem
        .busy
        .iter()
        .enumerate()
        .map(|(index, busy)| {
            let start = resolve_utc(&busy.start, timezone)?;
            let end = resolve_utc(&busy.end, timezone)?;
            if end <= start {
                return Err(PlannerError::Validation(format!(
                    "busy block '{}' must end after it starts",
                    busy.id
                )));
            }
            Ok(SolverBusy {
                id: format!("busy:{index}:{}", busy.id),
                event_id: busy.id.clone(),
                event_title: busy.title.clone(),
                calendar_id: busy.source.clone(),
                recurring: false,
                start,
                end,
            })
        })
        .collect::<Result<Vec<_>, _>>()?;

    let mut successors: HashMap<&str, Vec<usize>> = HashMap::new();
    let mut depends_on: HashMap<usize, Vec<usize>> = HashMap::new();
    for (before, after) in &problem.dependencies {
        let Some(&after_index) = index_of.get(after.as_str()) else {
            continue;
        };
        match index_of.get(before.as_str()) {
            Some(&before_index) => depends_on
                .entry(after_index)
                .or_default()
                .push(before_index),
            None => successors
                .entry(before.as_str())
                .or_default()
                .push(after_index),
        }
    }

    let applied = problem
        .applied
        .iter()
        .map(|block| {
            Ok(SolverAppliedBlock {
                id: format!("applied:{}", block.task_id),
                start: resolve_utc(&block.start, timezone)?,
                end: resolve_utc(&block.end, timezone)?,
                high: block.cognitive_load == Load::High,
                successors: successors
                    .get(block.task_id.as_str())
                    .cloned()
                    .unwrap_or_default(),
            })
        })
        .collect::<Result<Vec<_>, PlannerError>>()?;
    let applied_ends: Vec<DateTime<Utc>> = applied
        .iter()
        .filter(|block| block.high)
        .map(|block| block.end)
        .collect();

    let availability = availability_facts(&settings.availability)?;
    let cognitive = cognitive_facts(problem)?;

    let mut tasks = problem
        .tasks
        .iter()
        .enumerate()
        .map(|(index, task)| {
            let deadline = task
                .deadline_at
                .as_deref()
                .map(|value| resolve_utc(value, timezone))
                .transpose()?;
            Ok(SolverTask {
                id: index,
                task_id: task.id.clone(),
                index,
                duration_minutes: task.duration_minutes,
                priority_weight: match task.priority {
                    Priority::Low => settings.priority_low_weight,
                    Priority::Normal => settings.priority_normal_weight,
                    Priority::High => settings.priority_high_weight,
                },
                load: load_key(task.cognitive_load),
                earliest_at: task
                    .earliest_at
                    .as_deref()
                    .map(|value| resolve_utc(value, timezone))
                    .transpose()?,
                hard_deadline: (task.deadline_kind == DeadlineKind::Hard)
                    .then_some(deadline)
                    .flatten(),
                soft_deadline: (task.deadline_kind == DeadlineKind::Soft)
                    .then_some(deadline)
                    .flatten(),
                depends_on: depends_on.get(&index).cloned().unwrap_or_default(),
                applied_predecessor_ends: applied_ends.clone(),
                not_before: now,
                timezone,
                recovery_minutes: settings.recovery_minutes,
                excess_high_penalty: settings.excess_high_penalty,
                high_streak_limit: settings.high_streak_limit,
                grid_origin: origin,
                slot_minutes: settings.slot_minutes,
                start_idx: None,
            })
        })
        .collect::<Result<Vec<_>, PlannerError>>()?;
    scale_assignment_penalties(&mut tasks, horizon_end)?;
    validate_soft_score_range(&tasks, &cognitive)?;

    Ok(BuiltPlan {
        plan: SolverPlan::new(
            slots,
            busy,
            applied,
            availability,
            cognitive,
            tasks,
            settings.solve_seconds as u64,
        ),
        timezone,
        horizon_end,
    })
}

/// Canonicalizes weekly windows into disjoint per-weekday intervals, so a
/// minute is covered by at most one fact and overlaps never double-count.
fn availability_facts(
    availability: &BTreeMap<String, Vec<super::input::Window>>,
) -> Result<Vec<SolverAvailability>, PlannerError> {
    let mut by_weekday: BTreeMap<u32, Vec<(NaiveTime, NaiveTime)>> = BTreeMap::new();
    for (day, windows) in availability {
        let weekday = weekday_index(day)
            .ok_or_else(|| PlannerError::Validation(format!("invalid weekday '{day}'")))?;
        for window in windows {
            by_weekday
                .entry(weekday)
                .or_default()
                .push((parse_clock(&window.start)?, parse_clock(&window.end)?));
        }
    }
    let mut facts = Vec::new();
    for (weekday, mut windows) in by_weekday {
        windows.sort();
        let mut merged: Vec<(NaiveTime, NaiveTime)> = Vec::new();
        for (start, end) in windows {
            match merged.last_mut() {
                Some(last) if start <= last.1 => last.1 = last.1.max(end),
                _ => merged.push((start, end)),
            }
        }
        for (start, end) in merged {
            facts.push(SolverAvailability {
                id: format!("availability:{weekday}:{}", facts.len()),
                weekday,
                start,
                end,
            });
        }
    }
    Ok(facts)
}

fn cognitive_facts(problem: &Problem) -> Result<Vec<SolverCognitiveWindow>, PlannerError> {
    let s = &problem.settings;
    [
        (0usize, &s.low_window),
        (1, &s.medium_window),
        (2, &s.high_window),
    ]
    .into_iter()
    .map(|(load, window): (usize, &Option<FocusWindow>)| {
        let midnight = NaiveTime::MIN;
        Ok(match window {
            Some(w) => SolverCognitiveWindow {
                id: format!("cognitive:{load}"),
                load,
                start: parse_clock(&w.start)?,
                end: parse_clock(&w.end)?,
                outside_penalty: w.outside_penalty,
            },
            None => SolverCognitiveWindow {
                id: format!("cognitive:{load}"),
                load,
                start: midnight,
                end: midnight,
                outside_penalty: 0,
            },
        })
    })
    .collect()
}

fn load_key(load: Load) -> usize {
    match load {
        Load::Low => 0,
        Load::Medium => 1,
        Load::High => 2,
    }
}
