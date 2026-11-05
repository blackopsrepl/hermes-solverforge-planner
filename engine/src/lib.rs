//! SolverForge Planner engine.
//!
//! A stateless scheduling engine: a JSON problem (settings, inbox tasks,
//! dependencies, busy time, already-applied blocks) goes in, a proposal
//! (placements, score, per-task diagnostics) comes out. It owns no storage;
//! the Hermes plugin keeps state and calls this once per solve.
//!
//! The planning model (`planner_domain`) and every scoring rule
//! (`planner::constraints`) are Planner123's, extracted without changes to the
//! rules, so both products score a plan identically.

pub mod planner;
pub mod planner_domain;
