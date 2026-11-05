//! End-to-end: drive the built binary through its JSON contract.

use std::io::Write;
use std::process::{Command, Stdio};

use serde_json::{json, Value};

fn solve(problem: &Value) -> (bool, Value) {
    let mut child = Command::new(env!("CARGO_BIN_EXE_solverforge-planner"))
        .arg("solve")
        .stdin(Stdio::piped())
        .stdout(Stdio::piped())
        .stderr(Stdio::piped())
        .spawn()
        .expect("spawn engine");
    child
        .stdin
        .take()
        .unwrap()
        .write_all(problem.to_string().as_bytes())
        .unwrap();
    let out = child.wait_with_output().unwrap();
    let ok = out.status.success();
    let body = if ok { out.stdout } else { out.stderr };
    (
        ok,
        serde_json::from_slice(&body).expect("engine prints one JSON envelope"),
    )
}

fn week() -> Value {
    let day = json!([{"start": "09:00", "end": "18:00"}]);
    json!({
        "timezone": "Europe/Rome",
        "now": "2026-11-06 08:00",
        "settings": {
            "availability": {"mon": day, "tue": day, "wed": day, "thu": day, "fri": day},
            "horizon_days": 10,
            "solve_seconds": 2,
            "high_window": {"start": "09:00", "end": "12:00", "outside_penalty": 10}
        },
        "tasks": [
            {"id": "deck", "duration_minutes": 240, "priority": "high", "cognitive_load": "high",
             "deadline_kind": "hard", "deadline_at": "2026-11-11 18:00"},
            {"id": "pr", "duration_minutes": 60},
            {"id": "accountant", "duration_minutes": 30, "priority": "low", "cognitive_load": "low"}
        ],
        "dependencies": [["deck", "pr"]],
        "busy": [
            {"id": "offsite", "title": "Offsite", "start": "2026-11-09 09:00", "end": "2026-11-09 12:00"},
            {"id": "friday", "title": "Away", "start": "2026-11-06 00:00", "end": "2026-11-07 00:00"}
        ]
    })
}

fn item<'a>(proposal: &'a Value, id: &str) -> &'a Value {
    proposal["items"]
        .as_array()
        .unwrap()
        .iter()
        .find(|i| i["task_id"] == id)
        .unwrap()
}

#[test]
fn a_week_is_planned_inside_every_hard_rule() {
    let (ok, envelope) = solve(&week());
    assert!(ok, "{envelope}");
    let proposal = &envelope["data"];
    assert_eq!(proposal["hard_feasible"], true);
    assert!(
        proposal["score"]
            .as_str()
            .unwrap()
            .starts_with("0hard/0medium"),
        "{proposal}"
    );
    for id in ["deck", "pr", "accountant"] {
        let it = item(proposal, id);
        assert_eq!(it["scheduled"], true, "{it}");
        let start = it["start_at"].as_str().unwrap();
        assert!(
            !start.starts_with("2026-11-06"),
            "{id} placed on the busy Friday"
        );
        assert!(
            !(start.starts_with("2026-11-09") && &start[11..16] < "12:00"),
            "{id} in the offsite"
        );
    }
    assert!(item(proposal, "deck")["end_at"].as_str().unwrap() <= "2026-11-11 18:00:00");
    assert!(item(proposal, "deck")["end_at"].as_str() <= item(proposal, "pr")["start_at"].as_str());
}

#[test]
fn an_impossible_task_is_reported_with_its_blocker() {
    let mut problem = week();
    problem["tasks"] = json!([{"id": "huge", "duration_minutes": 600,
        "deadline_kind": "hard", "deadline_at": "2026-11-09 18:00"}]);
    let (ok, envelope) = solve(&problem);
    assert!(ok, "{envelope}");
    let it = item(&envelope["data"], "huge");
    assert_eq!(it["scheduled"], false);
    assert_eq!(it["outcome"], "no_hard_feasible_slot");
}

#[test]
fn applied_predecessor_orders_inbox_work() {
    let mut problem = week();
    problem["tasks"] = json!([{"id": "pr", "duration_minutes": 60}]);
    problem["applied"] = json!([{"task_id": "deck", "start": "2026-11-10 09:00",
        "end": "2026-11-10 13:00", "cognitive_load": "high"}]);
    let (ok, envelope) = solve(&problem);
    assert!(ok, "{envelope}");
    assert!(item(&envelope["data"], "pr")["start_at"].as_str().unwrap() >= "2026-11-10 13:00:00");
}

#[test]
fn bad_input_is_a_structured_error() {
    let mut problem = week();
    problem["timezone"] = json!("Mars/Olympus");
    let (ok, envelope) = solve(&problem);
    assert!(!ok);
    assert_eq!(envelope["code"], "validation_error");

    let mut problem = week();
    problem["dependencies"] = json!([["ghost", "pr"]]);
    let (ok, envelope) = solve(&problem);
    assert!(!ok);
    assert_eq!(envelope["code"], "conflict");
}
