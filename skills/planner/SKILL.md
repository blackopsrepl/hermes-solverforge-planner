---
name: planner
description: "Use when the user wants tasks scheduled, a week planned, or a plan changed. Drives the planner_* tools: inbox, busy time, solve, approve."
---

# Planning with SolverForge Planner

The planner is a SolverForge constraint solver. You describe the work; it decides where the
work goes. Never place tasks on the calendar yourself and never invent times:
every time you show the user comes from a `planner_optimize` result.

## Flow

0. If no `planner_*` tools are in your tool list, the plugin was installed
   after this Hermes session's tool list was built. Tell the user to start a
   new Hermes session, and stop. Never lay out a schedule yourself instead.

1. `planner_status`. If `ready` is false, fix the listed problems with
   `planner_settings` (ask the user for their timezone and working hours if you
   do not know them). If the engine is missing, tell the user to type
   `/planner install` (or run `hermes solverforge-planner install`) and stop;
   never download it yourself.
   Focus hours: `focus_window` (e.g. `09:00-12:00`) for high-load work and
   `shallow_window` for low-load work, when the user has a preference.
2. Busy time. If you can read the user's calendar with another tool or skill
   (for example the google-workspace skill's `calendar list`), read the planning
   window and pass every event to `planner_busy_set` with a stable `source`
   such as `"google"`. Always pass the full window: the call replaces that
   source. If the user gives an ICS link or file instead, use
   `planner_busy_from_ics`. If neither exists, plan without it and say so.
3. Tasks. Turn what the user says into `planner_task_create` calls:
   - duration: their estimate; ask only if there is no way to guess.
   - `priority`: high for "must", "urgent", "important"; low for "if there is time".
   - `cognitive_load`: high for writing, design, deep thinking; low for email,
     admin, errands; medium otherwise.
   - "by Tuesday" is `deadline_at` with `deadline_kind: hard`; "ideally by" is `soft`.
   - "not before Monday" is `earliest_at`.
   - "after X" or "once X is done" is `planner_depend` with `before` X.
4. `planner_optimize`. Then show the result by writing its `display` value
   (`::planner{proposal="…"}`) alone on its own line; in the Desktop app it
   renders as a plan card with an Apply button, elsewhere it is plain text, so
   also give a short list: each block as day, time, title. Explain every
   `unscheduled` task using its `outcome` and `blockers`, and offer the fix
   (more hours, a later deadline, a shorter task). The planner never splits
   a task: each one is a single block. If a task is longer than a focus
   window and the user wants it inside, offer to replace it with two tasks.
5. Apply only when the user agrees: `planner_apply` with the proposal id.
   Hermes shows them an approval prompt; that is expected. If it is refused
   because tasks, busy time or settings changed, run `planner_optimize` again
   and show the new proposal.
6. After applying, offer the returned `.ics` file by writing `MEDIA:<ics>` on
   its own line, exactly that form, not the bare path; it imports into any calendar app. If you have a tool or skill
   for the user's calendar, offer to add the `written` blocks there too.

## Changing a plan

- "Move X", "I'm out Thursday", "X takes longer": update the task
  (`planner_task_update`), the working hours (`planner_settings` with the full
  `availability` list), or busy time (`planner_busy_set`), then optimize again.
- Applied tasks are fixed and the solver plans around them. To let the solver
  move an applied task, `planner_task_unschedule` it first (the user approves).

## Reading the score

`0hard/…` means every hard rule holds: working hours, busy time, no overlaps,
hard deadlines, earliest starts and task order. Medium counts tasks left
unplaced (weighted by priority) and lateness past soft deadlines. Soft counts
minutes of work outside its focus window and missed recovery after heavy work.
Lower is better. Say "every hard rule is kept" only when `hard_rules_kept` is true.
