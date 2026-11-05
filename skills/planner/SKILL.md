---
name: planner
description: "Use when the user wants tasks scheduled, a week planned, or a plan changed. Drives the planner_* tools with the user's calendar tool."
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
2. Calendars. The planner reads no calendar itself: you read the user's
   calendars with the calendar tool they have, and you write planned blocks
   there with the same tool. For Google Calendar that is the google-workspace
   skill; for Outlook and Microsoft 365, the `microsoft365` plugin. If the user
   has a calendar but no tool for it, name the one to install, and meanwhile
   plan with the `.ics` file (step 6). If they say they have no calendar, skip
   to step 3: the planner only avoids what they tell you.
   - Read every calendar the user plans around, for the whole window
     `planner_optimize` will plan (today 00:00 to `horizon_days` + 1 days
     ahead), with recurring events as single occurrences. Pass each calendar
     to `planner_busy_set` with `kind: calendar`, a stable `source` such as
     `"google: work"`, the `window_start`/`window_end` you read, and every event
     with its `id` and `description`. Pass events marked free too only if the
     tool cannot tell them apart; otherwise leave them out.
   - Once a calendar has been passed, the planner refuses (`calendar_stale`)
     to plan on a read older than 15 minutes, and to apply on a read from
     before the plan. Read again and pass it again; never work around it.
     If the user stops using a calendar, `planner_busy_set` it with
     `remove: true`.
   - Time the user mentions ("I'm out Thursday afternoon") goes to
     `planner_busy_set` with `kind: other`. An .ics file or link they hand you
     goes to `planner_busy_from_ics`.
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
5. Apply only when the user agrees. First read the calendars again and pass
   them (the plan is older than the read the apply needs), then
   `planner_apply` with the proposal id. Hermes shows them an approval prompt;
   that is expected. If it is refused with `stale` or `calendar_conflict`
   (something new is on the calendar where a block was going), run
   `planner_optimize` again and show the new proposal.
6. Put the plan on the calendar. `create_on_calendar` lists the events to
   create, in the calendar named by `publish_to`; if that is empty, ask the
   user which calendar once and save it with `planner_settings`
   `publish_calendar`. Create each event with their calendar tool, with the
   given `title`, `start`, `end` and `description` exactly, and with
   `event_id` as the event's id when the tool takes one (Google Calendar's
   insert does; if it answers that the id already exists, that event is
   already there: skip it). Then read that calendar again and pass it with
   `planner_busy_set`. If `planner_status` → `not_on_calendar_yet` still lists
   events, `planner_apply` the same proposal again: it returns only those,
   never one already on the calendar. Never create a planner event that is
   not in `create_on_calendar`.
   With no calendar tool, offer the returned `.ics` file by writing
   `MEDIA:<ics>` on its own line, exactly that form, not the bare path; it
   imports into any calendar app, and importing it again updates the same
   events instead of adding copies.

## Google Calendar through the google-workspace skill

- Read: `calendar list --calendar <id> --start <window_start> --end <window_end>
  --max 2500`. Without `--max` it stops at 25 events and the read is
  incomplete. Map each event to `{title: summary, start, end, id,
  description}`. Read every calendar the user plans around, `primary` included.
- Create: `calendar create --calendar <publish_calendar> --summary <title>
  --start <start> --end <end> --description <description>`. The description
  carries the marker the planner recognizes; never shorten it.
- Delete: `calendar delete <id> --calendar <id>`, with the id from your last
  read for the event whose description holds the `event_id`.
- A calendar other than `primary` is named by its calendar ID (Google Calendar
  settings → the calendar → Integrate calendar → Calendar ID). Save that as
  `publish_calendar`.

## Changing a plan

- "Move X", "I'm out Thursday", "X takes longer": update the task
  (`planner_task_update`), the working hours (`planner_settings` with the full
  `availability` list), or busy time (`planner_busy_set`), then optimize again.
- Applied tasks are pinned: new plans go around them and applying a new plan
  never moves or removes them. If the user moved one of the planner's events
  in their calendar app, the next calendar read moves the block with it
  (`moved_on_calendar`). Only if the user asks the planner to move one,
  `planner_task_unschedule` it (they approve), delete the events it returns in
  `delete_from_calendar` with their calendar tool, then optimize again.
- Never delete or change an event the planner did not create.

## Reading the score

`0hard/…` means every hard rule holds: working hours, busy time, no overlaps,
hard deadlines, earliest starts and task order. Medium counts tasks left
unplaced (weighted by priority) and lateness past soft deadlines. Soft counts
minutes of work outside its focus window and missed recovery after heavy work.
Lower is better. Say "every hard rule is kept" only when `hard_rules_kept` is true.
