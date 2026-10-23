//! Request-specific scalar costs without Python callbacks in traversal loops.

use crate::endpoint_overlay::{Edge, projected_length, segment_points};
use crate::fields::{self, FieldSettings, IntegrationError};
use crate::numeric::*;
use std::collections::{HashMap, HashSet};
use std::time::{Duration, Instant};

#[derive(Clone, Copy)]
pub struct ShapeSettings {
    pub fields: FieldSettings,
    pub centre: f64,
    pub side: f64,
    pub reuse: f64,
    pub penalize_right: bool,
}

pub struct CostSettings {
    pub hill_weight: f64,
    pub shape: Option<ShapeSettings>,
    pub used_segments: HashSet<i64>,
    pub used_spans: HashMap<i64, Vec<(f64, f64)>>,
    pub hill_intervals: HashMap<i64, Vec<[f64; 16]>>,
}

pub struct CostCache {
    pub integrals: HashMap<IntegralKey, fields::Integrals>,
}

type IntegralKey = (usize, usize, u64, u64);

pub fn validate_settings(settings: &CostSettings) -> Result<(), RoutingError> {
    if !settings.hill_weight.is_finite() || settings.hill_weight < 0.0 {
        return Err(RoutingError::Invalid(
            "Hill weight must be finite and nonnegative",
        ));
    }
    if settings
        .used_spans
        .values()
        .flatten()
        .any(|(lo, hi)| !lo.is_finite() || !hi.is_finite() || *lo < 0.0 || hi < lo)
    {
        return Err(RoutingError::Invalid(
            "Invalid previously used physical span",
        ));
    }
    if let Some(shape) = settings.shape {
        let f = shape.fields;
        if ![f.home.0, f.home.1, f.destination.0, f.destination.1]
            .iter()
            .all(|v| v.is_finite())
            || f.home == f.destination
            || ![f.radius, f.transition_width, f.spacing]
                .iter()
                .all(|v| v.is_finite() && *v > 0.0)
            || ![shape.centre, shape.side, shape.reuse]
                .iter()
                .all(|v| v.is_finite() && *v >= 0.0)
        {
            return Err(RoutingError::Invalid("Invalid request shape settings"));
        }
    }
    Ok(())
}

fn matching_chainage(a: f64, b: f64) -> bool {
    (a - b).abs() <= 1e-6_f64.max(1e-9 * a.abs().max(b.abs()))
}

pub fn parse_hill_intervals(payload: &[u8]) -> Result<Vec<[f64; 16]>, RoutingError> {
    if payload.is_empty() || payload.len() % 128 != 0 {
        return Err(RoutingError::Invalid("Invalid hill interval byte count"));
    }
    let mut intervals = Vec::with_capacity(payload.len() / 128);
    let mut previous = 0.0;
    for bytes in payload.chunks_exact(128) {
        let row: [f64; 16] = std::array::from_fn(|i| {
            f64::from_le_bytes(bytes[i * 8..(i + 1) * 8].try_into().unwrap())
        });
        if row.iter().any(|v| !v.is_finite() || *v < 0.0)
            || !matching_chainage(row[0], previous)
            || row[1] <= row[0]
        {
            return Err(RoutingError::Invalid("Invalid prepared hill intervals"));
        }
        previous = row[1];
        intervals.push(row);
    }
    Ok(intervals)
}

fn add_interval(
    values: &mut [f64; 7],
    edge: Edge,
    start: f64,
    end: f64,
    features: [f64; 7],
) -> Result<(), RoutingError> {
    if end <= start {
        return Err(RoutingError::Invalid("Invalid prepared hill chainage"));
    }
    let overlap = edge.high.min(end) - edge.low.max(start);
    if overlap > 0.0 {
        for (total, feature) in values.iter_mut().zip(features) {
            *total += feature * overlap / (end - start);
        }
    }
    Ok(())
}

pub fn hill_features(
    chunk: &Chunk<'_>,
    edge: Edge,
    settings: &CostSettings,
) -> Result<[f64; 7], RoutingError> {
    let direction = usize::from(edge.backward);
    let totals: [f64; 7] = std::array::from_fn(|i| {
        chunk.columns[HILL_TOTALS].float((edge.segment * 2 + direction) * 7 + i)
    });
    if totals[6] <= 0.0 {
        return Err(RoutingError::Invalid("Hill features are unavailable"));
    }
    let length = chunk.columns[ARC_LENGTHS].float(edge.arc);
    if edge.low < 0.0 || edge.high > length + 1e-6 {
        return Err(RoutingError::Invalid(
            "Partial hill traversal is outside physical chainage",
        ));
    }
    if edge.low == 0.0 && edge.high == length {
        return Ok(totals);
    }
    let mut values = [0.0; 7];
    let lo = chunk.columns[HILL_OFFSETS].integer(edge.segment) as usize;
    let hi = chunk.columns[HILL_OFFSETS].integer(edge.segment + 1) as usize;
    if lo == hi {
        let segment_id = chunk.columns[SEGMENT_IDS].integer(edge.segment);
        let intervals = settings
            .hill_intervals
            .get(&segment_id)
            .ok_or(RoutingError::Invalid(
                "Partial hill traversal requires endpoint intervals",
            ))?;
        if intervals
            .last()
            .is_none_or(|last| !matching_chainage(last[1], length))
        {
            return Err(RoutingError::Invalid(
                "Endpoint hill intervals do not cover the segment",
            ));
        }
        for row in intervals {
            add_interval(
                &mut values,
                edge,
                row[0],
                row[1],
                std::array::from_fn(|i| row[2 + direction * 7 + i]),
            )?;
        }
        return Ok(values);
    }
    for interval in lo..hi {
        let start = chunk.columns[HILL_CHAINAGE].float(interval * 2);
        let end = chunk.columns[HILL_CHAINAGE].float(interval * 2 + 1);
        add_interval(
            &mut values,
            edge,
            start,
            end,
            std::array::from_fn(|i| {
                chunk.columns[HILL_FEATURES].float((interval * 2 + direction) * 7 + i)
            }),
        )?;
    }
    Ok(values)
}

fn clipped_geometry(chunk: &Chunk<'_>, edge: Edge) -> Vec<(f64, f64)> {
    let points = segment_points(chunk, edge.segment);
    let total = projected_length(&points);
    let lo = edge.geometry_low.unwrap_or(0.0);
    let hi = edge.geometry_high.unwrap_or(total);
    let mut result = Vec::new();
    let mut position = 0.0;
    for pair in points.windows(2) {
        let length = (pair[1].0 - pair[0].0).hypot(pair[1].1 - pair[0].1);
        let start = lo.max(position);
        let end = hi.min(position + length);
        if length > 0.0 && end > start {
            for point in [start, end] {
                let fraction = (point - position) / length;
                let xy = (
                    pair[0].0 + fraction * (pair[1].0 - pair[0].0),
                    pair[0].1 + fraction * (pair[1].1 - pair[0].1),
                );
                if result.last() != Some(&xy) {
                    result.push(xy);
                }
            }
        }
        position += length;
    }
    if let Some((first, last)) = edge.geometry_endpoints {
        if result.len() >= 2 {
            result[0] = first;
            let end = result.len() - 1;
            result[end] = last;
        }
    }
    result
}

fn shape_integrals(
    chunk: &Chunk<'_>,
    edge: Edge,
    shape: ShapeSettings,
    cache: &mut CostCache,
    deadline: Option<Instant>,
) -> Result<(f64, f64, f64), RoutingError> {
    let key = (
        edge.chunk,
        edge.segment,
        edge.low.to_bits(),
        edge.high.to_bits(),
    );
    if let Some(value) = cache.integrals.get(&key) {
        return Ok(*value);
    }
    let points = clipped_geometry(chunk, edge);
    let remaining = deadline.map(|end| {
        end.checked_duration_since(Instant::now())
            .unwrap_or(Duration::ZERO)
    });
    let value =
        fields::integrate_fields(&points, shape.fields, remaining).map_err(
            |error| match error {
                IntegrationError::TimedOut => RoutingError::TimedOut,
                IntegrationError::InvalidInput(message) => RoutingError::Invalid(message),
            },
        )?;
    cache.integrals.insert(key, value);
    Ok(value)
}

pub fn evaluate_cost(
    chunks: &[Chunk<'_>],
    edge: Edge,
    settings: &CostSettings,
    cache: &mut CostCache,
    deadline: Option<Instant>,
) -> Result<([f64; 5], [f64; 7]), RoutingError> {
    let chunk = &chunks[edge.chunk];
    let mut costs = [edge.length, 0.0, 0.0, 0.0, 0.0];
    if let Some(shape) = settings.shape {
        let (radial, right, length) = shape_integrals(chunk, edge, shape, cache, deadline)?;
        costs[1] = shape.centre * radial;
        costs[2] = shape.side
            * if shape.penalize_right {
                right.max(0.0)
            } else {
                (length - right).max(0.0)
            };
        let segment_id = chunk.columns[SEGMENT_IDS].integer(edge.segment);
        let reused = settings.used_segments.contains(&segment_id)
            || settings.used_spans.get(&segment_id).is_some_and(|spans| {
                spans
                    .iter()
                    .any(|(lo, hi)| edge.low >= lo - 1e-7 && edge.high <= hi + 1e-7)
            });
        if reused {
            costs[3] = shape.reuse * edge.length;
        }
    }
    let hills = if settings.hill_weight > 0.0 {
        hill_features(chunk, edge, settings)?
    } else {
        [0.0; 7]
    };
    costs[4] = settings.hill_weight * hills[0];
    if costs.iter().any(|v| !v.is_finite() || *v < 0.0) || !costs.iter().sum::<f64>().is_finite() {
        return Err(RoutingError::Invalid(
            "Native cost components must be finite and nonnegative",
        ));
    }
    Ok((costs, hills))
}
