//! Integrate prepared environment cells over physical graph chainage.

use crate::numeric::RoutingError;
use std::collections::HashMap;

/// Dense indices are internal; Python exchanges field names.
#[derive(Clone, Copy, PartialEq, Eq)]
#[repr(usize)]
pub enum EnvironmentField {
    Greenery = 0,
    Water = 1,
    Traffic = 2,
    Wetland = 3,
}

pub const FIELDS: [EnvironmentField; 4] = [
    EnvironmentField::Greenery,
    EnvironmentField::Water,
    EnvironmentField::Traffic,
    EnvironmentField::Wetland,
];

pub type FieldValues = [f64; FIELDS.len()];

pub struct EnvironmentInterval {
    pub start_m: f64,
    pub end_m: f64,
    pub values: FieldValues,
    pub context_support: f64,
    pub traffic_context_support: f64,
}

pub type EnvironmentIntervals = HashMap<i64, Vec<EnvironmentInterval>>;

pub fn field_name(field: EnvironmentField) -> &'static str {
    match field {
        EnvironmentField::Greenery => "greenery",
        EnvironmentField::Water => "water",
        EnvironmentField::Traffic => "traffic",
        EnvironmentField::Wetland => "wetland",
    }
}

pub fn named_weights(values: HashMap<String, f64>) -> Result<FieldValues, RoutingError> {
    let mut weights = [0.0; FIELDS.len()];
    for (name, weight) in values {
        let field = FIELDS
            .into_iter()
            .find(|field| field_name(*field) == name)
            .ok_or(RoutingError::Invalid("Unknown environment weight field"))?;
        weights[field as usize] = weight;
    }
    Ok(weights)
}

pub fn validate_intervals(values: &EnvironmentIntervals) -> Result<(), RoutingError> {
    for intervals in values.values() {
        if intervals.is_empty() {
            return Err(RoutingError::Invalid("Missing environment intervals"));
        }
        let mut previous = 0.0;
        for interval in intervals {
            if !interval.start_m.is_finite()
                || !interval.end_m.is_finite()
                || (interval.start_m - previous).abs() > 1e-6
                || interval.end_m <= interval.start_m
                || interval
                    .values
                    .iter()
                    .chain([interval.context_support, interval.traffic_context_support].iter())
                    .any(|value| {
                        !value.is_nan() && (!value.is_finite() || !(0.0..=1.0).contains(value))
                    })
            {
                return Err(RoutingError::Invalid(
                    "Invalid prepared environment intervals",
                ));
            }
            previous = interval.end_m;
        }
    }
    Ok(())
}

fn field_supported(interval: &EnvironmentInterval, field: EnvironmentField, value: f64) -> bool {
    !value.is_nan()
        && interval.context_support == 1.0
        && (field != EnvironmentField::Traffic || interval.traffic_context_support == 1.0)
}

fn penalty_density(field: EnvironmentField, value: f64) -> f64 {
    match field {
        EnvironmentField::Greenery | EnvironmentField::Water => 1.0 - value,
        EnvironmentField::Traffic | EnvironmentField::Wetland => value,
    }
}

pub fn integrate(
    intervals: &[EnvironmentInterval],
    low: f64,
    high: f64,
    weights: FieldValues,
) -> Result<(FieldValues, FieldValues), RoutingError> {
    integrate_intervals::<true>(intervals, low, high, weights)
}

pub fn integrate_penalties(
    intervals: &[EnvironmentInterval],
    low: f64,
    high: f64,
    weights: FieldValues,
) -> Result<FieldValues, RoutingError> {
    integrate_intervals::<false>(intervals, low, high, weights).map(|(penalties, _)| penalties)
}

fn integrate_intervals<const DIAGNOSTICS: bool>(
    intervals: &[EnvironmentInterval],
    low: f64,
    high: f64,
    weights: FieldValues,
) -> Result<(FieldValues, FieldValues), RoutingError> {
    if low < 0.0
        || high <= low
        || !low.is_finite()
        || !high.is_finite()
        || intervals
            .last()
            .is_none_or(|interval| high > interval.end_m + 1e-6)
    {
        return Err(RoutingError::Invalid(
            "Environment traversal exceeds prepared chainage",
        ));
    }
    let mut penalties = [0.0; FIELDS.len()];
    let mut unknown = [0.0; FIELDS.len()];
    for interval in intervals {
        let length = (high.min(interval.end_m) - low.max(interval.start_m)).max(0.0);
        if length <= 0.0 {
            continue;
        }
        for (index, field) in FIELDS.into_iter().enumerate() {
            if !DIAGNOSTICS && weights[index] == 0.0 {
                continue;
            }
            let value = interval.values[index];
            let supported = field_supported(interval, field, value);
            if DIAGNOSTICS && !supported {
                unknown[index] += length;
            }
            if weights[index] > 0.0 {
                let density = if supported {
                    penalty_density(field, value)
                } else {
                    1.0
                };
                penalties[index] += weights[index] * length * density;
            }
        }
    }
    Ok((penalties, unknown))
}

#[cfg(test)]
mod tests {
    use super::*;

    fn interval_from_row(row: [f64; 8]) -> EnvironmentInterval {
        let [
            start_m,
            end_m,
            greenery,
            water,
            traffic,
            wetland,
            context_support,
            traffic_context_support,
        ] = row;
        EnvironmentInterval {
            start_m,
            end_m,
            values: [greenery, water, traffic, wetland],
            context_support,
            traffic_context_support,
        }
    }

    #[test]
    fn named_weights_are_independent_of_mapping_order() {
        let weights = named_weights(HashMap::from([
            ("traffic".into(), 4.0),
            ("greenery".into(), 0.25),
        ]))
        .unwrap();
        assert_eq!(weights[EnvironmentField::Greenery as usize], 0.25);
        assert_eq!(weights[EnvironmentField::Water as usize], 0.0);
        assert_eq!(weights[EnvironmentField::Traffic as usize], 4.0);
        assert!(named_weights(HashMap::from([("typo".into(), 1.0)])).is_err());
    }

    fn assert_close(actual: [f64; 4], expected: [f64; 4]) {
        for (actual, expected) in actual.into_iter().zip(expected) {
            assert!((actual - expected).abs() < 1e-10, "{actual} != {expected}");
        }
    }

    #[test]
    fn field_order_weights_and_partial_chainage() {
        let intervals = [
            interval_from_row([0.0, 10.0, 0.2, 0.4, 0.6, 0.1, 1.0, 1.0]),
            interval_from_row([10.0, 20.0, 0.8, f64::NAN, 0.0, 0.7, 1.0, 0.0]),
        ];
        let weights = [1.0, 2.0, 3.0, 4.0];
        let (full, unknown) = integrate(&intervals, 0.0, 20.0, weights).unwrap();
        assert_close(full, [10.0, 32.0, 48.0, 32.0]);
        assert_eq!(unknown, [0.0, 10.0, 10.0, 0.0]);
        let (partial, unknown) = integrate(&intervals, 5.0, 15.0, weights).unwrap();
        assert_close(partial, [5.0, 16.0, 24.0, 16.0]);
        assert_eq!(
            integrate_penalties(&intervals, 5.0, 15.0, weights).unwrap(),
            partial
        );
        assert_eq!(unknown, [0.0, 5.0, 5.0, 0.0]);
    }

    #[test]
    fn support_and_missing_values_are_independent_of_weights() {
        let cases = [
            ([1.0, 1.0, 0.0, 0.0], 1.0, 1.0, [0.0; 4]),
            ([1.0, 1.0, 0.0, 0.0], 0.0, 1.0, [10.0; 4]),
            ([1.0, 1.0, 0.0, 0.0], 0.5, 1.0, [10.0; 4]),
            ([1.0, 1.0, 0.0, 0.0], f64::NAN, 1.0, [10.0; 4]),
            ([1.0, 1.0, 0.0, 0.0], 1.0, 0.0, [0.0, 0.0, 10.0, 0.0]),
            ([1.0, 1.0, 0.0, 0.0], 1.0, f64::NAN, [0.0, 0.0, 10.0, 0.0]),
            ([1.0, f64::NAN, 0.0, 0.0], 1.0, 1.0, [0.0, 10.0, 0.0, 0.0]),
        ];
        for (values, context_support, traffic_context_support, expected) in cases {
            let intervals = [EnvironmentInterval {
                start_m: 0.0,
                end_m: 10.0,
                values,
                context_support,
                traffic_context_support,
            }];
            let (penalties, unknown) = integrate(&intervals, 0.0, 10.0, [1.0; 4]).unwrap();
            assert_eq!(
                integrate_penalties(&intervals, 0.0, 10.0, [1.0; 4]).unwrap(),
                penalties
            );
            assert_eq!(penalties, expected);
            assert_eq!(unknown, expected);
            let (disabled, unknown) = integrate(&intervals, 0.0, 10.0, [0.0; 4]).unwrap();
            assert_eq!(
                integrate_penalties(&intervals, 0.0, 10.0, [0.0; 4]).unwrap(),
                disabled
            );
            assert_eq!(disabled, [0.0; 4]);
            assert_eq!(unknown, expected);
        }
    }

    #[test]
    fn validates_named_interval_fields_and_traversal_bounds() {
        let valid = [0.0, 10.0, 0.2, 0.4, 0.6, 0.1, 1.0, 1.0];
        let values = HashMap::from([(7, vec![interval_from_row(valid)])]);
        assert!(validate_intervals(&values).is_ok());
        for index in 0..valid.len() {
            let mut invalid = valid;
            invalid[index] = f64::INFINITY;
            let values = HashMap::from([(7, vec![interval_from_row(invalid)])]);
            assert!(validate_intervals(&values).is_err());
        }
        let intervals = &values[&7];
        for (low, high) in [(-1.0, 5.0), (5.0, 5.0), (0.0, 11.0), (f64::NAN, 5.0)] {
            assert!(integrate(intervals, low, high, [1.0; 4]).is_err());
            assert!(integrate_penalties(intervals, low, high, [1.0; 4]).is_err());
        }
    }
}
