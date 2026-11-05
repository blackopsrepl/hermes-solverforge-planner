# SolverForge Planner for Hermes

Constraint-solved planning for [Hermes Agent](https://github.com/NousResearch/hermes-agent).

Ask Hermes to fit your work into your week. A [SolverForge](https://solverforge.org) engine places
every task into free working time around your meetings, deadlines, task order and focus hours, and
reports a score that says whether every hard rule holds. Hermes shows you the plan, you approve it,
and only then does it go on your calendar, as blocks you can see in Hermes and as an `.ics` file any
calendar app imports.

An LLM asked to schedule a week produces something plausible. A solver produces something correct,
and says what it could not fit and why.

## What you get

- **Tools for the agent**: inbox, tasks, ordering rules, working hours and focus windows, busy time,
  solve, review and apply.
- **Approval before every calendar write**: `planner_apply` and `planner_task_unschedule` go through
  Hermes's approval prompt, scoped to the exact proposal or task, so "always allow" never covers a
  later write. Where nobody can answer (cron, `hermes chat -q`) the write is refused. Your own
  `approvals.mode` setting still applies: with approvals off, Hermes does not ask.
- **Plans that cannot go stale**: a proposal records what it was solved from. If a task, the busy
  time or the settings change afterwards, applying it is refused and Hermes solves again.
- **Busy time from any calendar**: the agent passes events read with whatever calendar tool or skill
  it has (for example the bundled `google-workspace` skill), or loads an ICS feed or file. Each source
  is replaced as a whole, so it never drifts.
- **Desktop app surfaces**:
  - a plan card inline in the chat (`::planner{proposal="…"}`) with busy time, the proposed blocks,
    the score, what did not fit and an Apply button behind a confirmation;
  - a **Planner** page in the sidebar with the latest proposal, your tasks, **Solve now** and, on first
    run, **Install engine**;
  - a status-bar chip for a proposal waiting for approval or your next block;
  - palette commands: *Planner: open*, *Planner: solve now*.
- **`/planner`** in any chat or messaging app: `status`, `install`, `inbox`, `plan`, `show [id]`,
  `apply <id>`.
- **Morning plan blueprint**: `/blueprint solverforge-planner:morning` schedules a workday run that
  refreshes busy time, solves and sends you the proposal to approve.

## Install

```sh
hermes plugins install blackopsrepl/hermes-solverforge-planner --enable
hermes solverforge-planner install     # or /planner install, or Install engine in the Desktop app
```

The second step downloads one engine binary for your platform (Linux x86_64/arm64, macOS
Apple silicon/Intel, Windows x86_64) from this repository's GitHub release. No Rust toolchain needed.

In the Desktop app, turn on the package's Desktop half in **Capabilities → Plugins**, then restart
the backend once so the plugin's API routes mount.

Tell Hermes your timezone and working hours ("I work 9 to 6, Monday to Friday, Rome time"), or use
`/planner status` to see what is missing.

### Settings

Under **Settings → Plugins → SolverForge Planner** or `plugins.entries.solverforge-planner.settings`:

| Key | Meaning |
| --- | --- |
| `binary` | Path to an engine binary. Empty uses the one `install` put in the plugin data directory. |
| `solve_timeout` | Seconds before a solve is stopped. Default 120. |

Working hours, focus windows, horizon and solve time are planner settings the agent manages with
`planner_settings`.

## How it works

```
you ──► Hermes ──► planner_* tools ──► plugin store (SQLite) ──► problem JSON
                     │                                              │
                     │                          solverforge-planner solve (Rust, SolverForge)
                     │                                              │
                     └── pre_tool_call ──► approval ──► apply ◄── proposal JSON
Desktop ──► /api/plugins/solverforge-planner (dashboard/plugin_api.py) ──► same store and engine
```

- `engine/` is a Rust crate: a stateless SolverForge model. A JSON problem (settings, tasks, ordering
  rules, busy time, applied blocks) goes in on stdin; a proposal (placements, score, per-task
  diagnostics) comes out on stdout. Its planning model and all fourteen scoring rules are
  [Planner123](https://github.com/blackopsrepl/Planner123)'s, extracted without changes; on the
  same week both produce identical placements and the same score.
- The Python package keeps state in its own SQLite file, translates between the model's vocabulary and
  the engine contract, gates writes and serves the Desktop half. It makes no scheduling decisions.
- Before a proposal is returned, its per-task penalties are reconciled against SolverForge's own score
  analysis, and a hard-infeasible result is refused.

Score format `Nhard/Nmedium/Nsoft`, lower is better:

- **hard**: working hours, busy time, no overlaps, hard deadlines, earliest starts, task order;
- **medium**: tasks left unplaced (weighted by priority) and lateness past soft deadlines;
- **soft**: minutes of work outside its focus window and missed recovery after heavy work.

## Disclosure

- **Binary download**: `hermes solverforge-planner install`, `/planner install` and the Desktop
  **Install engine** button download `solverforge-planner-<platform>` from
  `github.com/blackopsrepl/hermes-solverforge-planner/releases` at the version pinned in
  `engine_bin.py`, and refuse it unless its SHA-256 matches the checksum pinned next to it. Each
  binary is built from `engine/` by this repository's release workflow and carries a GitHub build
  attestation (`gh attestation verify <file> --repo blackopsrepl/hermes-solverforge-planner`). The
  agent cannot trigger the download; only you can.
- **Shell commands**: runs that engine binary once per solve (`solverforge-planner solve`, JSON over
  stdin/stdout, stopped at `solve_timeout`). Nothing else is executed.
- **Network**: the engine download above, and `planner_busy_from_ics` fetching an ICS URL you give it
  (max 10 MB). The engine itself makes no network calls.
- **Files**: everything lives in `<hermes home>/plugin-data/solverforge-planner/`: `planner.db`, the
  engine binary and `exports/*.ics`. It reads ICS files you point it at. No credentials are stored or
  read.
- **No telemetry, no background processes.**

## Development

```sh
cd engine && cargo test --release && cd ..        # engine unit, incremental-score and CLI tests
uv run --no-project --with pytest python -m pytest # plugin end to end, against the real engine
hermes plugins validate . && hermes plugins doctor . --ci
scripts/build-engine.sh aarch64-apple-darwin       # one release target, cross-built with cargo-zigbuild
```

Nothing in the tests is mocked except the network in the install test.

## License

MIT
