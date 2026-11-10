//! Compose edge costs from shape, hill, and environment terms during traversal.

use crate::endpoint_overlay::{Edge, projected_length, segment_points};
use crate::environment_cost::{self, EnvironmentIntervals};
use crate::fields::{self, FieldSettings, IntegrationError};
use crate::numeric::*;
use std::collections::{HashMap, HashSet};
use std::sync::Arc;
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
    pub environment_weights: environment_cost::FieldValues,
    pub environment: Option<Arc<EnvironmentIntervals>>,
    pub shape: Option<ShapeSettings>,
    pub used_segments: HashSet<i64>,
    pub used_spans: HashMap<i64, Vec<(f64, f64)>>,
    pub hill_intervals: HashMap<i64, Vec<[f64; 16]>>,
}

pub struct CostCache {
    pub integrals: HashMap<IntegralKey, fields::Integrals>,
}

pub struct EdgeCost {
    /// Stable output order: distance, centre, side, reuse, hills.
    pub components: [f64; 5],
    pub hill_features: [f64; 7],
    pub environment_penalties: environment_cost::FieldValues,
    pub environment_unknown_m: environment_cost::FieldValues,
}

#[derive(Default)]
struct ShapeCosts {
    centre: f64,
    side: f64,
    reuse: f64,
}

type IntegralKey = (usize, usize, u64, u64);

pub fn validate_settings(settings: &CostSettings) -> Result<(), RoutingError> {
    if !settings.hill_weight.is_finite() || settings.hill_weight < 0.0 {
        return Err(RoutingError::Invalid(
            "Hill weight must be finite and nonnegative",
        ));
    }
    if settings
        .environment_weights
        .iter()
        .any(|value| !value.is_finite() || *value < 0.0)
        || (settings
            .environment_weights
            .iter()
            .any(|value| *value > 0.0)
            && settings.environment.is_none())
    {
        return Err(RoutingError::Invalid(
            "Environment weights require prepared intervals",
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
    let _profile = crate::profiling::span(crate::profiling::Phase::HillDecode);
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

fn add_interval<const N: usize>(
    values: &mut [f64; N],
    edge: Edge,
    start: f64,
    end: f64,
    features: [f64; N],
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
    integrate_hills(chunk, edge, settings)
}

fn integrate_hills<const N: usize>(
    chunk: &Chunk<'_>,
    edge: Edge,
    settings: &CostSettings,
) -> Result<[f64; N], RoutingError> {
    let direction = usize::from(edge.backward);
    let totals: [f64; N] = std::array::from_fn(|i| {
        chunk.columns[HILL_TOTALS].float((edge.segment * 2 + direction) * 7 + i)
    });
    if chunk.columns[HILL_TOTALS].float((edge.segment * 2 + direction) * 7 + 6) <= 0.0 {
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
    let mut values = [0.0; N];
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
    let _profile = crate::profiling::span(crate::profiling::Phase::Geometry);
    let mut points = segment_points(chunk, edge.segment);
    if edge.geometry_low.is_none()
        && edge.geometry_high.is_none()
        && edge.geometry_endpoints.is_none()
    {
        points.dedup();
        if points.len() < 2 {
            points.clear();
        }
        return points;
    }
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

fn traversal_reused(chunk: &Chunk<'_>, edge: Edge, settings: &CostSettings) -> bool {
    let segment_id = chunk.columns[SEGMENT_IDS].integer(edge.segment);
    settings.used_segments.contains(&segment_id)
        || settings.used_spans.get(&segment_id).is_some_and(|spans| {
            spans
                .iter()
                .any(|(low, high)| edge.low >= low - 1e-7 && edge.high <= high + 1e-7)
        })
}

fn shape_costs(
    chunk: &Chunk<'_>,
    edge: Edge,
    settings: &CostSettings,
    cache: &mut CostCache,
    deadline: Option<Instant>,
) -> Result<ShapeCosts, RoutingError> {
    let Some(shape) = settings.shape else {
        return Ok(ShapeCosts::default());
    };
    let (radial, right, length) = shape_integrals(chunk, edge, shape, cache, deadline)?;
    let penalized_side_length = if shape.penalize_right {
        right.max(0.0)
    } else {
        (length - right).max(0.0)
    };
    Ok(ShapeCosts {
        centre: shape.centre * radial,
        side: shape.side * penalized_side_length,
        reuse: if traversal_reused(chunk, edge, settings) {
            shape.reuse * edge.length
        } else {
            0.0
        },
    })
}

fn environment_costs(
    chunk: &Chunk<'_>,
    edge: Edge,
    settings: &CostSettings,
) -> Result<(environment_cost::FieldValues, environment_cost::FieldValues), RoutingError> {
    let Some(prepared) = &settings.environment else {
        return Ok((
            [0.0; environment_cost::FIELDS.len()],
            [0.0; environment_cost::FIELDS.len()],
        ));
    };
    let segment_id = chunk.columns[SEGMENT_IDS].integer(edge.segment);
    let intervals = prepared
        .get(&segment_id)
        .ok_or(RoutingError::Invalid("Missing environment segment"))?;
    environment_cost::integrate(intervals, edge.low, edge.high, settings.environment_weights)
}

pub fn total_cost(cost: &EdgeCost) -> f64 {
    cost.components
        .iter()
        .chain(&cost.environment_penalties)
        .sum()
}

fn validate_cost(cost: &EdgeCost) -> Result<(), RoutingError> {
    validated_total(&cost.components, &cost.environment_penalties).map(|_| ())
}

fn validated_total(
    components: &[f64; 5],
    environment: &environment_cost::FieldValues,
) -> Result<f64, RoutingError> {
    let invalid_component = components
        .iter()
        .chain(environment)
        .any(|value| !value.is_finite() || *value < 0.0);
    let total = components.iter().chain(environment).sum::<f64>();
    if invalid_component || !total.is_finite() {
        return Err(RoutingError::Invalid(
            "Native cost components must be finite and nonnegative",
        ));
    }
    Ok(total)
}

/// Traversal needs the objective only; final-path evaluation supplies diagnostics.
pub fn evaluate_search_cost(
    chunks: &[Chunk<'_>],
    edge: Edge,
    settings: &CostSettings,
    cache: &mut CostCache,
    deadline: Option<Instant>,
) -> Result<f64, RoutingError> {
    let _profile = crate::profiling::span(crate::profiling::Phase::Cost);
    let chunk = &chunks[edge.chunk];
    let shape = shape_costs(chunk, edge, settings, cache, deadline)?;
    let hill = if settings.hill_weight > 0.0 {
        integrate_hills::<1>(chunk, edge, settings)?[0]
    } else {
        0.0
    };
    let environment = search_environment_costs(chunk, edge, settings)?;
    validated_total(
        &[
            edge.length,
            shape.centre,
            shape.side,
            shape.reuse,
            settings.hill_weight * hill,
        ],
        &environment,
    )
}

fn search_environment_costs(
    chunk: &Chunk<'_>,
    edge: Edge,
    settings: &CostSettings,
) -> Result<environment_cost::FieldValues, RoutingError> {
    if settings
        .environment_weights
        .iter()
        .all(|weight| *weight == 0.0)
    {
        return Ok([0.0; environment_cost::FIELDS.len()]);
    }
    let prepared = settings.environment.as_ref().ok_or(RoutingError::Invalid(
        "Environment weights require prepared intervals",
    ))?;
    let segment_id = chunk.columns[SEGMENT_IDS].integer(edge.segment);
    let intervals = prepared
        .get(&segment_id)
        .ok_or(RoutingError::Invalid("Missing environment segment"))?;
    environment_cost::integrate_penalties(
        intervals,
        edge.low,
        edge.high,
        settings.environment_weights,
    )
}

pub fn evaluate_cost(
    chunks: &[Chunk<'_>],
    edge: Edge,
    settings: &CostSettings,
    cache: &mut CostCache,
    deadline: Option<Instant>,
) -> Result<EdgeCost, RoutingError> {
    let _profile = crate::profiling::span(crate::profiling::Phase::Cost);
    let chunk = &chunks[edge.chunk];
    let shape = shape_costs(chunk, edge, settings, cache, deadline)?;
    let hill_features = if settings.hill_weight > 0.0 {
        hill_features(chunk, edge, settings)?
    } else {
        [0.0; 7]
    };
    let [hill_penalty_m, ..] = hill_features;
    let (environment_penalties, environment_unknown_m) = environment_costs(chunk, edge, settings)?;
    let cost = EdgeCost {
        components: [
            edge.length,
            shape.centre,
            shape.side,
            shape.reuse,
            settings.hill_weight * hill_penalty_m,
        ],
        hill_features,
        environment_penalties,
        environment_unknown_m,
    };
    validate_cost(&cost)?;
    Ok(cost)
}

#[cfg(test)]
mod tests {
    use super::*;

    fn sample_edge() -> Edge {
        Edge {
            chunk: 0,
            arc: 0,
            segment: 0,
            source: 0,
            target: 1,
            length: 20.0,
            low: 0.0,
            high: 20.0,
            backward: false,
            geometry_low: None,
            geometry_high: None,
            geometry_endpoints: None,
        }
    }

    fn sample_chunk<'a>(geometry: &'a [u8], offsets: &'a [u8]) -> Chunk<'a> {
        const LENGTH: [u8; 8] = 20.0_f64.to_le_bytes();
        const SEGMENT: [u8; 8] = 7_i64.to_le_bytes();
        let mut columns = [Column { bytes: &[] }; 23];
        columns[GEOMETRY_XY] = Column { bytes: geometry };
        columns[GEOMETRY_OFFSETS] = Column { bytes: offsets };
        columns[ARC_LENGTHS] = Column { bytes: &LENGTH };
        columns[SEGMENT_IDS] = Column { bytes: &SEGMENT };
        columns[HILL_OFFSETS] = Column { bytes: &[0; 16] };
        columns[HILL_TOTALS] = Column { bytes: &[0; 112] };
        Chunk {
            counts: Counts {
                nodes: 2,
                segments: 1,
                arcs: 1,
                profiles: 1,
                points: geometry.len() / 16,
                intervals: 0,
            },
            columns,
        }
    }

    #[test]
    fn full_geometry_preserves_pieces_and_partial_endpoint_overrides() {
        let geometry: Vec<u8> = [0.0_f64, 0.0, 0.0, 0.0, 10.0, 0.0, 10.0, 0.0, 20.0, 0.0]
            .into_iter()
            .flat_map(f64::to_le_bytes)
            .collect();
        let offsets: Vec<u8> = [0_i64, 5].into_iter().flat_map(i64::to_le_bytes).collect();
        let chunk = sample_chunk(&geometry, &offsets);
        let mut edge = sample_edge();
        assert_eq!(
            clipped_geometry(&chunk, edge),
            vec![(0.0, 0.0), (10.0, 0.0), (20.0, 0.0)]
        );
        edge.geometry_low = Some(5.0);
        edge.geometry_high = Some(15.0);
        edge.geometry_endpoints = Some(((5.0, 1.0), (15.0, 1.0)));
        assert_eq!(
            clipped_geometry(&chunk, edge),
            vec![(5.0, 1.0), (10.0, 0.0), (15.0, 1.0)]
        );
        // A zero-length polyline must not introduce a sampled field piece.
        let zero_offsets: Vec<u8> = [0_i64, 2].into_iter().flat_map(i64::to_le_bytes).collect();
        let repeated = sample_chunk(&geometry[..32], &zero_offsets);
        assert!(clipped_geometry(&repeated, sample_edge()).is_empty());
    }

    fn sample_settings() -> CostSettings {
        CostSettings {
            hill_weight: 2.0,
            environment_weights: [0.0; 4],
            environment: Some(Arc::new(HashMap::from([(
                7,
                vec![
                    environment_cost::EnvironmentInterval {
                        start_m: 0.0,
                        end_m: 10.0,
                        values: [0.2, 0.4, f64::NAN, 0.8],
                        context_support: 1.0,
                        traffic_context_support: 0.0,
                    },
                    environment_cost::EnvironmentInterval {
                        start_m: 10.0,
                        end_m: 20.0,
                        values: [0.8, 0.6, 0.1, 0.2],
                        context_support: 1.0,
                        traffic_context_support: 1.0,
                    },
                ],
            )]))),
            shape: None,
            used_segments: HashSet::from([7]),
            used_spans: HashMap::new(),
            hill_intervals: HashMap::from([(
                7,
                vec![
                    [
                        0.0, 10.0, 3.0, 0.0, 0.0, 0.0, 0.0, 0.0, 10.0, 5.0, 0.0, 0.0, 0.0, 0.0,
                        0.0, 10.0,
                    ],
                    [
                        10.0, 20.0, 6.0, 0.0, 0.0, 0.0, 0.0, 0.0, 10.0, 7.0, 0.0, 0.0, 0.0, 0.0,
                        0.0, 10.0,
                    ],
                ],
            )]),
        }
    }

    #[test]
    fn scalar_objective_matches_diagnostics_for_full_and_partial_edges() {
        let geometry: Vec<u8> = [0.0_f64, 0.0, 10.0, 0.0, 20.0, 0.0]
            .into_iter()
            .flat_map(f64::to_le_bytes)
            .collect();
        let offsets: Vec<u8> = [0_i64, 3].into_iter().flat_map(i64::to_le_bytes).collect();
        let mut chunk = sample_chunk(&geometry, &offsets);
        let totals: Vec<u8> = [
            9.0_f64, 1.0, 2.0, 3.0, 4.0, 5.0, 20.0, 12.0, 1.0, 2.0, 3.0, 4.0, 5.0, 20.0,
        ]
        .into_iter()
        .flat_map(f64::to_le_bytes)
        .collect();
        chunk.columns[HILL_TOTALS] = Column { bytes: &totals };
        let chunks = [chunk];
        let shape = ShapeSettings {
            fields: FieldSettings {
                home: (0.0, 0.0),
                destination: (20.0, 0.0),
                radius: 5.0,
                transition_width: 2.0,
                spacing: 2.0,
            },
            centre: 1.0,
            side: 2.0,
            reuse: 3.0,
            penalize_right: true,
        };
        let mut settings = sample_settings();
        for shape in [None, Some(shape)] {
            settings.shape = shape;
            for weights in [[0.0; 4], [1.0, 0.0, 4.0, 0.0], [1.0, 2.0, 4.0, 3.0]] {
                settings.environment_weights = weights;
                for backward in [false, true] {
                    for (low, high) in [(0.0, 20.0), (5.0, 15.0)] {
                        let mut edge = sample_edge();
                        edge.low = low;
                        edge.high = high;
                        edge.length = high - low;
                        edge.backward = backward;
                        edge.geometry_low = Some(low);
                        edge.geometry_high = Some(high);
                        let mut cache = CostCache {
                            integrals: HashMap::new(),
                        };
                        let expected =
                            evaluate_cost(&chunks, edge, &settings, &mut cache, None).unwrap();
                        let actual =
                            evaluate_search_cost(&chunks, edge, &settings, &mut cache, None)
                                .unwrap();
                        assert_eq!(actual, total_cost(&expected));
                    }
                }
            }
        }
    }

    fn sample_cost() -> EdgeCost {
        EdgeCost {
            components: [10.0, 1.0, 2.0, 3.0, 4.0],
            hill_features: [1000.0; 7],
            environment_penalties: [0.1, 0.2, 0.3, 0.4],
            environment_unknown_m: [1000.0; environment_cost::FIELDS.len()],
        }
    }

    #[test]
    fn objective_includes_penalties_without_double_counting_diagnostics() {
        let cost = sample_cost();
        assert!(validate_cost(&cost).is_ok());
        assert!((total_cost(&cost) - 21.0).abs() < 1e-12);
    }

    #[test]
    fn rejects_invalid_components_and_overflowing_totals() {
        for invalid in [-1.0, f64::NAN, f64::INFINITY, f64::NEG_INFINITY] {
            let mut cost = sample_cost();
            cost.components[0] = invalid;
            assert!(validate_cost(&cost).is_err());
            let mut cost = sample_cost();
            cost.environment_penalties[environment_cost::EnvironmentField::Traffic as usize] =
                invalid;
            assert!(validate_cost(&cost).is_err());
        }
        let mut cost = sample_cost();
        cost.components = [f64::MAX; 5];
        assert!(validate_cost(&cost).is_err());
    }
}
