//! Integrate prepared environment cells over physical graph chainage.

use crate::numeric::RoutingError;
use std::collections::HashMap;

pub type EnvironmentIntervals = HashMap<i64, Vec<[f64; 8]>>;

pub fn validate_intervals(values: &EnvironmentIntervals) -> Result<(), RoutingError> {
    for intervals in values.values() {
        if intervals.is_empty() {
            return Err(RoutingError::Invalid("Missing environment intervals"));
        }
        let mut previous = 0.0;
        for row in intervals {
            let [
                start,
                end,
                greenery,
                water,
                traffic,
                wetland,
                support,
                traffic_support,
            ] = *row;
            if !start.is_finite()
                || !end.is_finite()
                || (start - previous).abs() > 1e-6
                || end <= start
                || [greenery, water, traffic, wetland, support, traffic_support]
                    .iter()
                    .any(|value| {
                        !value.is_nan() && (!value.is_finite() || !(0.0..=1.0).contains(value))
                    })
            {
                return Err(RoutingError::Invalid(
                    "Invalid prepared environment intervals",
                ));
            }
            previous = end;
        }
    }
    Ok(())
}

pub fn integrate(
    intervals: &[[f64; 8]],
    low: f64,
    high: f64,
    weights: [f64; 4],
) -> Result<([f64; 4], [f64; 4]), RoutingError> {
    if low < 0.0
        || high <= low
        || !low.is_finite()
        || !high.is_finite()
        || intervals.last().is_none_or(|row| high > row[1] + 1e-6)
    {
        return Err(RoutingError::Invalid(
            "Environment traversal exceeds prepared chainage",
        ));
    }
    let mut penalties = [0.0; 4];
    let mut unknown = [0.0; 4];
    for row in intervals {
        let length = (high.min(row[1]) - low.max(row[0])).max(0.0);
        if length <= 0.0 {
            continue;
        }
        for field in 0..4 {
            let value = row[field + 2];
            let supported = !value.is_nan() && row[6] == 1.0 && (field != 2 || row[7] == 1.0);
            if !supported {
                unknown[field] += length;
            }
            if weights[field] > 0.0 {
                let density = if supported {
                    if field < 2 { 1.0 - value } else { value }
                } else {
                    1.0
                };
                penalties[field] += weights[field] * length * density;
            }
        }
    }
    Ok((penalties, unknown))
}
