//! `solverforge-planner solve`: read a JSON problem on stdin, write a JSON
//! proposal on stdout. Failures go to stderr as `{"status":"error",...}` with a
//! non-zero exit, mirroring the planner123-cli envelope.

use std::io::{Read, Write};

use serde_json::json;
use solverforge_planner::planner::{input::Problem, plan, PlannerError};

const USAGE: &str =
    "usage: solverforge-planner solve < problem.json\n       solverforge-planner --version";

fn fail(code: &str, message: &str) -> ! {
    let body = json!({"status": "error", "code": code, "message": message});
    let _ = writeln!(std::io::stderr(), "{body}");
    std::process::exit(1)
}

fn main() {
    let args: Vec<String> = std::env::args().skip(1).collect();
    match args.first().map(String::as_str) {
        Some("--version") | Some("-V") => {
            println!(
                "{}",
                json!({"status": "ok", "data": {"version": env!("CARGO_PKG_VERSION")}})
            );
        }
        Some("solve") => {
            let mut raw = String::new();
            if let Err(error) = std::io::stdin().read_to_string(&mut raw) {
                fail("io_error", &error.to_string());
            }
            let problem: Problem = match serde_json::from_str(&raw) {
                Ok(problem) => problem,
                Err(error) => fail("invalid_problem", &error.to_string()),
            };
            match plan(&problem) {
                Ok(proposal) => println!("{}", json!({"status": "ok", "data": proposal})),
                Err(error @ PlannerError::Validation(_))
                | Err(error @ PlannerError::Conflict(_))
                | Err(error @ PlannerError::Internal(_)) => fail(error.code(), &error.to_string()),
            }
        }
        _ => fail("invalid_arguments", USAGE),
    }
}
