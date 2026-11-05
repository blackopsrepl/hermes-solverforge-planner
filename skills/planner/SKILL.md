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
2. Calendars. The planner keeps its own calendar on this computer (status →
   `local_calendar`, a `planner.ics` any calendar app can subscribe to). The
   user's cloud calendars are optional, and reached only with the calendar
   tool they have: the google-workspace skill for Google Calendar, the
   `microsoft365` plugin for Outlook. If they have a cloud calendar but no
   tool, name the one to install. Without one, the planner plans around what
   the user tells you.
   - **Pull** (also before every plan and apply, when a calendar was pulled
     before): read the calendar with their tool for today 00:00 to
     `horizon_days` + 1 days ahead, recurring events as single occurrences,
     and pass it to `planner_busy_set` with `kind: calendar`, a stable
     `source` (`"google: primary"`), the `window_start`/`window_end` you read,
     and every event with its `id` and `description`. Report
     `removed_on_calendar` (tasks back in the inbox: their events are gone
     from the pulled days, deleted or moved further out), `back_on_calendar`
     (such an event showed up again and its task is pinned there) and
     `moved_on_calendar`.
   - Once a calendar has been pulled, the planner refuses to plan on a pull
     older than 15 minutes, and to push on one older than that
     (`calendar_stale`): pull again; never work around it. A calendar the user
     stops using: `planner_busy_set` with `remove: true`.
   - Time the user mentions ("I'm out Thursday afternoon") goes to
     `planner_busy_set` with `kind: other`. An .ics file or link they hand you
     goes to `planner_busy_from_ics`.
   - `planner_status` → `clashes` lists blocks already on the plan with a
     calendar event now on top of them. Tell the user and offer to re-plan
     them (see Changing a plan).
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
5. Apply only when the user agrees: pull first if a calendar was pulled
   before (apply checks the latest pull for new overlaps), then
   `planner_apply` with the proposal id. Hermes shows them an
   approval prompt; that is expected. If it is refused with `stale` or
   `calendar_conflict` (something new is on the calendar where a block was
   going), run `planner_optimize` again and show the new proposal. Apply puts
   the blocks on the planner's own calendar only.
6. **Push** when the user wants the plan on their cloud calendar ("update my
   calendar"; offer it after an apply when `push_to` is set): pull it, then
   `planner_push`. Carry out exactly what it returns with their calendar
   tool: create each event in `create` with its `title`, `start`, `end` and
   `description` unchanged (and `event_id` as the event's id when the tool
   takes one; "already exists" means it is there), delete each event in
   `delete` by its `id`. Then pull again so the planner confirms. Never create,
   change or delete any other event. If `planner_push` says to pull first, do
   that: it is what keeps an event from being created twice.
   With no calendar tool, offer the `.ics` from `planner_apply` by writing
   `MEDIA:<ics>` on its own line, exactly that form; importing it again
   updates the same events instead of adding copies.

## Google Calendar through the google-workspace skill

- Read: `calendar list --calendar <id> --start <window_start> --end <window_end>
  --max 2500`. Without `--max` it stops at 25 events and the read is
  incomplete. Map each event to `{title: summary, start, end, id,
  description}`. Read every calendar the user plans around, `primary` included.
- Create: `calendar create --calendar <publish_calendar> --summary <title>
  --start <start> --end <end> --description <description>`. The description
  carries the marker the planner recognizes; never shorten it.
- Delete: `calendar delete <id> --calendar <calendar id>` with the `id` from
  `planner_push` → `delete`.
- A calendar other than `primary` is named by its calendar ID (Google Calendar
  settings → the calendar → Integrate calendar → Calendar ID). Use it in the
  source name (`"google: <calendar id>"`) so pushes go to the right one.

## Changing a plan

- "Move X", "I'm out Thursday", "X takes longer": update the task
  (`planner_task_update`), the working hours (`planner_settings` with the full
  `availability` list), or busy time (`planner_busy_set`), then optimize again.
- Applied tasks are pinned: new plans go around them and applying a new plan
  never moves or removes them. If the user moved one of the planner's events
  in their calendar app, the next calendar read moves the block with it
  (`moved_on_calendar`). Only if the user asks the planner to move one,
  `planner_task_unschedule` it (they approve), optimize again, and push: the
  push deletes its old event and creates the new one.
- Never delete or change an event the planner did not create.

## Reading the score

`0hard/…` means every hard rule holds: working hours, busy time, no overlaps,
hard deadlines, earliest starts and task order. Medium counts tasks left
unplaced (weighted by priority) and lateness past soft deadlines. Soft counts
minutes of work outside its focus window and missed recovery after heavy work.
Lower is better. Say "every hard rule is kept" only when `hard_rules_kept` is true.
