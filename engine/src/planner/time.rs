//! Wall-clock parsing. All times crossing the JSON boundary are naive local
//! `YYYY-MM-DD HH:MM[:SS]` in the problem's IANA timezone, as in Planner123.

use chrono::{DateTime, LocalResult, NaiveDateTime, NaiveTime, TimeZone, Utc};
use chrono_tz::Tz;

use super::PlannerError;

pub const STORAGE_FORMAT: &str = "%Y-%m-%d %H:%M:%S";

pub fn parse_timezone(value: &str) -> Result<Tz, PlannerError> {
    value.parse::<Tz>().map_err(|_| {
        PlannerError::Validation(format!(
            "timezone '{value}' is not an IANA name such as Europe/Rome or UTC"
        ))
    })
}

fn parse_timestamp(value: &str) -> Result<NaiveDateTime, PlannerError> {
    NaiveDateTime::parse_from_str(value, STORAGE_FORMAT)
        .or_else(|_| NaiveDateTime::parse_from_str(value, "%Y-%m-%d %H:%M"))
        .map_err(|_| {
            PlannerError::Validation(format!(
                "invalid timestamp '{value}'; expected YYYY-MM-DD HH:MM[:SS]"
            ))
        })
}

/// Resolves a local wall-clock time to UTC, refusing DST gaps and overlaps
/// instead of guessing.
pub fn resolve_utc(value: &str, timezone: Tz) -> Result<DateTime<Utc>, PlannerError> {
    let timestamp = parse_timestamp(value)?;
    match timezone.from_local_datetime(&timestamp) {
        LocalResult::Single(dt) => Ok(dt.with_timezone(&Utc)),
        LocalResult::Ambiguous(_, _) => Err(PlannerError::Validation(format!(
            "timestamp '{value}' is ambiguous in timezone '{}'",
            timezone.name()
        ))),
        LocalResult::None => Err(PlannerError::Validation(format!(
            "timestamp '{value}' does not exist in timezone '{}'",
            timezone.name()
        ))),
    }
}

pub fn local(at: DateTime<Utc>, timezone: Tz) -> String {
    at.with_timezone(&timezone)
        .format(STORAGE_FORMAT)
        .to_string()
}

pub fn parse_clock(value: &str) -> Result<NaiveTime, PlannerError> {
    NaiveTime::parse_from_str(value, "%H:%M")
        .map_err(|_| PlannerError::Validation(format!("invalid time '{value}'; expected HH:MM")))
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn local_wall_clock_round_trips_through_utc() {
        let rome = parse_timezone("Europe/Rome").unwrap();
        let utc = resolve_utc("2026-04-12 09:30", rome).unwrap();
        assert_eq!(
            utc.format(STORAGE_FORMAT).to_string(),
            "2026-04-12 07:30:00"
        );
        assert_eq!(local(utc, rome), "2026-04-12 09:30:00");
    }

    #[test]
    fn dst_gap_is_refused() {
        let rome = parse_timezone("Europe/Rome").unwrap();
        assert!(resolve_utc("2026-03-29 02:30", rome).is_err());
    }
}
