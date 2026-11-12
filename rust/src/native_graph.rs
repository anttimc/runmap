//! Expose immutable numeric graphs and native routing searches through PyO3.

use crate::chunk_graph::{self, Bounds, GraphIndex};
use crate::chunk_search::{self, SearchSettings, check_deadline};
use crate::endpoint_overlay::{self, Endpoint, node_id};
use crate::environment_cost::{self, EnvironmentIntervals};
use crate::fields::FieldSettings;
use crate::numeric::{self, ARC_IDS, ARC_LENGTHS, ARC_SEGMENTS, Chunk, RoutingError, SEGMENT_IDS};
use crate::routing_cost::{CostSettings, ShapeSettings, parse_hill_intervals};
use crate::signal_cost::{Arrival, GateRows, SignalData, SignalSettings, TurnRows};
use pyo3::exceptions::{PyTimeoutError, PyValueError};
use pyo3::prelude::*;
use pyo3::types::PyBytes;
use std::collections::{HashMap, HashSet};
use std::sync::Arc;
use std::time::{Duration, Instant};

pub type SegmentEndpoint = (i64, f64, Option<f64>);
pub type ShapeArguments = ((f64, f64), (f64, f64), f64, f64, f64, f64, f64, f64, bool);
type DistanceOutput = (Py<PyBytes>, Py<PyBytes>, Py<PyBytes>, usize, i64);
type PathOutput = (
    Option<f64>,
    usize,
    Py<PyBytes>,
    Py<PyBytes>,
    Py<PyBytes>,
    Py<PyBytes>,
    Py<PyBytes>,
    Py<PyBytes>,
    HashMap<&'static str, (Py<PyBytes>, Py<PyBytes>)>,
    Py<PyBytes>,
    i64,
    i64,
);

/// Extract named Python records once; the search retains only dense native data.
fn environment_interval(
    input: &Bound<'_, PyAny>,
) -> PyResult<environment_cost::EnvironmentInterval> {
    let values = input.getattr("values")?;
    let support = input.getattr("support")?;
    let mut fields = [0.0; environment_cost::FIELDS.len()];
    for field in environment_cost::FIELDS {
        fields[field as usize] = values
            .getattr(environment_cost::field_name(field))?
            .extract::<Option<f64>>()?
            .unwrap_or(f64::NAN);
    }
    Ok(environment_cost::EnvironmentInterval {
        start_m: input.getattr("graph_start_m")?.extract()?,
        end_m: input.getattr("graph_end_m")?.extract()?,
        values: fields,
        context_support: support
            .getattr("context_support")?
            .extract::<Option<f64>>()?
            .unwrap_or(f64::NAN),
        traffic_context_support: support
            .getattr("traffic_context_support")?
            .extract::<Option<f64>>()?
            .unwrap_or(f64::NAN),
    })
}

fn environment_intervals(
    py: Python<'_>,
    values: Vec<(i64, Vec<Py<PyAny>>)>,
) -> PyResult<EnvironmentIntervals> {
    let count = values.len();
    let intervals: EnvironmentIntervals = values
        .into_iter()
        .map(|(segment, rows)| {
            Ok((
                segment,
                rows.into_iter()
                    .map(|row| environment_interval(row.bind(py)))
                    .collect::<PyResult<Vec<_>>>()?,
            ))
        })
        .collect::<PyResult<_>>()?;
    if intervals.len() != count {
        return Err(PyValueError::new_err("Duplicate environment segment"));
    }
    environment_cost::validate_intervals(&intervals).map_err(routing_error)?;
    Ok(intervals)
}

/// Compute the existing zero-weight observations after route selection.
#[pyfunction]
#[pyo3(signature = (values, lengths, spans, *, timeout_seconds=None))]
pub fn environment_unknown_lengths(
    py: Python<'_>,
    values: Vec<(i64, Vec<Py<PyAny>>)>,
    lengths: Vec<(i64, f64)>,
    spans: Vec<(i64, f64, f64)>,
    timeout_seconds: Option<f64>,
) -> PyResult<HashMap<&'static str, Py<PyBytes>>> {
    let end = deadline(timeout_seconds)?;
    check_deadline(end).map_err(routing_error)?;
    let intervals = environment_intervals(py, values)?;
    validate_environment_lengths(&intervals, &lengths)?;
    let rows = py
        .detach(|| {
            let mut rows = Vec::with_capacity(spans.len());
            for (segment, a, b) in spans {
                check_deadline(end)?;
                if !a.is_finite() || !b.is_finite() {
                    return Err(RoutingError::Invalid("Invalid environment traversal span"));
                }
                let values = intervals
                    .get(&segment)
                    .ok_or(RoutingError::Invalid("Missing environment segment"))?;
                let (_, unknown) =
                    environment_cost::integrate(values, a.min(b), a.max(b), [0.0; 4])?;
                rows.push(unknown);
            }
            check_deadline(end)?;
            Ok::<_, RoutingError>(rows)
        })
        .map_err(routing_error)?;
    Ok(environment_cost::FIELDS
        .into_iter()
        .enumerate()
        .map(|(index, field)| {
            (
                environment_cost::field_name(field),
                floats(py, rows.iter().map(|row| row[index])),
            )
        })
        .collect())
}

fn validate_environment_lengths(
    intervals: &EnvironmentIntervals,
    lengths: &[(i64, f64)],
) -> PyResult<()> {
    let mut seen = HashSet::new();
    for &(segment, length) in lengths {
        let rows = intervals
            .get(&segment)
            .ok_or_else(|| PyValueError::new_err("Missing environment segment"))?;
        if segment < 0
            || !seen.insert(segment)
            || !length.is_finite()
            || length <= 0.0
            || (rows.last().unwrap().end_m - length).abs() > 1e-6
        {
            return Err(PyValueError::new_err(
                "Environment chainage does not match graph",
            ));
        }
    }
    if seen.len() != intervals.len() {
        return Err(PyValueError::new_err(
            "Environment segments do not match route",
        ));
    }
    Ok(())
}

pub fn routing_error(error: RoutingError) -> PyErr {
    match error {
        RoutingError::Invalid(message) => PyValueError::new_err(message),
        RoutingError::TimedOut => {
            PyTimeoutError::new_err("Native route search exceeded its deadline")
        }
    }
}

fn deadline(timeout: Option<f64>) -> PyResult<Option<Instant>> {
    timeout
        .map(|seconds| {
            let duration = Duration::try_from_secs_f64(seconds)
                .map_err(|_| PyValueError::new_err("Timeout must be finite and nonnegative"))?;
            Instant::now()
                .checked_add(duration)
                .ok_or_else(|| PyValueError::new_err("Timeout is too large"))
        })
        .transpose()
}

fn views<'a>(py: Python<'a>, owners: &'a [Arc<PreparedChunk>]) -> PyResult<Vec<Chunk<'a>>> {
    let _profile = crate::profiling::span(crate::profiling::Phase::Views);
    owners
        .iter()
        .map(|owner| numeric::parse_chunk(owner.payload.bind(py).as_bytes()).map_err(routing_error))
        .collect()
}

fn request_hill_intervals(
    py: Python<'_>,
    values: Vec<(i64, Py<PyBytes>)>,
    end: Option<Instant>,
) -> PyResult<HashMap<i64, Vec<[f64; 16]>>> {
    let mut intervals = HashMap::new();
    for (id, payload) in values {
        check_deadline(end).map_err(routing_error)?;
        let parsed = parse_hill_intervals(payload.bind(py).as_bytes()).map_err(routing_error)?;
        if id < 0 || intervals.insert(id, parsed).is_some() {
            return Err(PyValueError::new_err(
                "Invalid or duplicate endpoint hill segment",
            ));
        }
    }
    Ok(intervals)
}

fn endpoint(
    node: Option<i64>,
    segment: Option<SegmentEndpoint>,
    point: Option<(f64, f64)>,
) -> PyResult<Endpoint> {
    match (node, segment) {
        (Some(id), None) => Ok(Endpoint::Node(id)),
        (None, Some((id, offset, chainage))) => Ok(Endpoint::Segment {
            id,
            offset,
            chainage,
            point,
        }),
        _ => Err(PyValueError::new_err(
            "Choose exactly one node or segment endpoint",
        )),
    }
}

fn shape_settings(value: Option<ShapeArguments>) -> Option<ShapeSettings> {
    value.map(
        |(
            home,
            destination,
            radius,
            transition_width,
            spacing,
            centre,
            side,
            reuse,
            penalize_right,
        )| ShapeSettings {
            fields: FieldSettings {
                home,
                destination,
                radius,
                transition_width,
                spacing,
            },
            centre,
            side,
            reuse,
            penalize_right,
        },
    )
}

fn ints(py: Python<'_>, values: impl Iterator<Item = i64>) -> Py<PyBytes> {
    let _profile = crate::profiling::span(crate::profiling::Phase::Serialize);
    let bytes: Vec<u8> = values.flat_map(i64::to_le_bytes).collect();
    PyBytes::new(py, &bytes).unbind()
}

fn floats(py: Python<'_>, values: impl Iterator<Item = f64>) -> Py<PyBytes> {
    let _profile = crate::profiling::span(crate::profiling::Phase::Serialize);
    let bytes: Vec<u8> = values.flat_map(f64::to_le_bytes).collect();
    PyBytes::new(py, &bytes).unbind()
}

fn validate_environment_graph(
    graph: &GraphIndex,
    chunks: &[Chunk<'_>],
    intervals: &EnvironmentIntervals,
) -> PyResult<()> {
    for (chunk, selected) in chunks.iter().zip(&graph.selected) {
        for arc in 0..chunk.counts.arcs {
            let segment = chunk.columns[ARC_SEGMENTS].integer(arc) as usize;
            if !selected[segment] {
                continue;
            }
            let id = chunk.columns[SEGMENT_IDS].integer(segment);
            let rows = intervals.get(&id).ok_or_else(|| {
                PyValueError::new_err("Environment join lacks a selected segment")
            })?;
            let length = chunk.columns[ARC_LENGTHS].float(arc);
            if (rows.last().unwrap().end_m - length).abs() > 1e-6 {
                return Err(PyValueError::new_err(
                    "Environment chainage does not match graph",
                ));
            }
        }
    }
    Ok(())
}

struct PreparedChunk {
    payload: Py<PyBytes>,
    index: chunk_graph::ChunkIndex,
    resident_bytes: usize,
}

fn prepare_payload(
    py: Python<'_>,
    payload: Py<PyBytes>,
    end: Option<Instant>,
) -> PyResult<Arc<PreparedChunk>> {
    let chunk = numeric::parse_chunk(payload.bind(py).as_bytes()).map_err(routing_error)?;
    let index = py
        .detach(|| {
            check_deadline(end)?;
            let index = chunk_graph::prepare_chunk(&chunk)?;
            check_deadline(end)?;
            Ok::<_, RoutingError>(index)
        })
        .map_err(routing_error)?;
    let resident_bytes = payload.bind(py).as_bytes().len()
        + chunk_graph::chunk_index_bytes(&index)
        + std::mem::size_of::<PreparedChunk>()
        + 128;
    Ok(Arc::new(PreparedChunk {
        payload,
        index,
        resident_bytes,
    }))
}

/// Reusable validated topology and immutable adjacency payload for one chunk.
#[pyclass(frozen, module = "runmap._routing_native")]
pub struct NumericChunk {
    data: Arc<PreparedChunk>,
}

#[pymethods]
impl NumericChunk {
    #[new]
    #[pyo3(signature = (payload, *, timeout_seconds=None))]
    fn new(py: Python<'_>, payload: Py<PyBytes>, timeout_seconds: Option<f64>) -> PyResult<Self> {
        Ok(Self {
            data: prepare_payload(py, payload, deadline(timeout_seconds)?)?,
        })
    }

    #[getter]
    fn resident_bytes(&self) -> usize {
        self.data.resident_bytes
    }

    #[getter]
    fn payload_bytes(&self, py: Python<'_>) -> usize {
        self.data.payload.bind(py).as_bytes().len()
    }
}

fn graph_from_chunks(
    py: Python<'_>,
    owners: Vec<Arc<PreparedChunk>>,
    bounds: Option<Bounds>,
    distance_scale: f64,
    end: Option<Instant>,
) -> PyResult<NumericGraph> {
    if !distance_scale.is_finite() || !(0.0..=1.0).contains(&distance_scale) {
        return Err(PyValueError::new_err(
            "Distance scale must be between zero and one",
        ));
    }
    let chunks = views(py, &owners)?;
    let indexes: Vec<_> = owners.iter().map(|owner| &owner.index).collect();
    let graph = py
        .detach(|| {
            check_deadline(end)?;
            let graph = chunk_graph::build_prepared_graph(&chunks, &indexes, bounds)?;
            check_deadline(end)?;
            Ok::<_, RoutingError>(graph)
        })
        .map_err(routing_error)?;
    let scale = indexes.iter().fold(distance_scale, |scale, index| {
        scale.min(index.distance_scale)
    });
    Ok(NumericGraph {
        owners,
        graph,
        distance_scale: scale,
        environment: None,
        signals: None,
    })
}

#[pyclass(module = "runmap._routing_native")]
pub struct NumericGraph {
    owners: Vec<Arc<PreparedChunk>>,
    graph: GraphIndex,
    distance_scale: f64,
    environment: Option<Arc<EnvironmentIntervals>>,
    signals: Option<Arc<SignalData>>,
}

#[pymethods]
impl NumericGraph {
    #[new]
    #[pyo3(signature = (payloads, *, bounds=None, distance_scale=0.0, timeout_seconds=None))]
    fn new(
        py: Python<'_>,
        payloads: Vec<Py<PyBytes>>,
        bounds: Option<Bounds>,
        distance_scale: f64,
        timeout_seconds: Option<f64>,
    ) -> PyResult<Self> {
        let end = deadline(timeout_seconds)?;
        let owners = payloads
            .into_iter()
            .map(|payload| prepare_payload(py, payload, end))
            .collect::<PyResult<Vec<_>>>()?;
        graph_from_chunks(py, owners, bounds, distance_scale, end)
    }

    #[staticmethod]
    #[pyo3(signature = (chunks, *, bounds=None, distance_scale=0.0, timeout_seconds=None))]
    fn from_chunks(
        py: Python<'_>,
        chunks: Vec<PyRef<'_, NumericChunk>>,
        bounds: Option<Bounds>,
        distance_scale: f64,
        timeout_seconds: Option<f64>,
    ) -> PyResult<Self> {
        let owners = chunks.iter().map(|chunk| Arc::clone(&chunk.data)).collect();
        graph_from_chunks(
            py,
            owners,
            bounds,
            distance_scale,
            deadline(timeout_seconds)?,
        )
    }

    #[getter]
    fn chunk_bytes(&self) -> usize {
        self.owners.iter().map(|chunk| chunk.resident_bytes).sum()
    }

    #[getter]
    fn index_bytes(&self) -> usize {
        chunk_graph::graph_index_bytes(&self.graph)
            + self.owners.capacity() * std::mem::size_of::<Arc<PreparedChunk>>()
            + std::mem::size_of::<Self>()
            + 128
    }

    #[getter]
    fn node_count(&self) -> usize {
        self.graph.active.iter().filter(|v| **v).count()
    }

    #[getter]
    fn segment_count(&self) -> usize {
        self.graph.selected.iter().flatten().filter(|v| **v).count()
    }

    #[getter]
    fn has_frontier(&self) -> bool {
        self.graph.frontier.iter().any(|value| *value)
    }

    #[getter]
    fn environment_prepared(&self) -> bool {
        self.environment.is_some()
    }

    #[getter]
    fn signals_prepared(&self) -> bool {
        self.signals.is_some()
    }

    fn set_signals(&mut self, py: Python<'_>, gates: GateRows, turns: TurnRows) -> PyResult<()> {
        if self.signals.is_some() {
            return Err(PyValueError::new_err("Signals already attached"));
        }
        let chunks = views(py, &self.owners)?;
        let mut lengths = HashMap::new();
        for (chunk, selected) in chunks.iter().zip(&self.graph.selected) {
            for arc in 0..chunk.counts.arcs {
                let segment = chunk.columns[ARC_SEGMENTS].integer(arc) as usize;
                if selected[segment] {
                    lengths.insert(
                        chunk.columns[SEGMENT_IDS].integer(segment),
                        chunk.columns[ARC_LENGTHS].float(arc),
                    );
                }
            }
        }
        let mut data = SignalData::default();
        for (segment, offset, unresolved, directions) in gates {
            if !(1..=3).contains(&directions)
                || !offset.is_finite()
                || offset <= 0.0
                || lengths.get(&segment).is_none_or(|length| offset >= *length)
            {
                return Err(PyValueError::new_err("Invalid signal charging boundary"));
            }
            let rows = data.gates.entry(segment).or_default();
            if rows.iter().any(|(existing, _, _)| *existing == offset) {
                return Err(PyValueError::new_err("Duplicate signal boundary"));
            }
            rows.push((offset, unresolved, directions));
        }
        for (node, incoming, outgoing, unresolved) in turns {
            if incoming == outgoing
                || !lengths.contains_key(&outgoing)
                || data
                    .turns
                    .insert((node, incoming, outgoing), unresolved)
                    .is_some()
            {
                return Err(PyValueError::new_err(
                    "Invalid or duplicate signal movement",
                ));
            }
        }
        self.signals = Some(Arc::new(data));
        Ok(())
    }

    fn selected_segment_ids(&self, py: Python<'_>) -> Py<PyBytes> {
        let chunks = views(py, &self.owners).expect("Validated graph chunks");
        let mut ids = HashSet::new();
        for (chunk, selected) in chunks.iter().zip(&self.graph.selected) {
            for (segment, enabled) in selected.iter().enumerate() {
                if *enabled {
                    ids.insert(chunk.columns[SEGMENT_IDS].integer(segment));
                }
            }
        }
        ints(py, ids.into_iter())
    }

    fn set_environment_intervals(
        &mut self,
        py: Python<'_>,
        values: Vec<(i64, Vec<Py<PyAny>>)>,
    ) -> PyResult<()> {
        if self.environment.is_some() {
            return Err(PyValueError::new_err(
                "Environment intervals are already attached",
            ));
        }
        let intervals = environment_intervals(py, values)?;
        let chunks = views(py, &self.owners)?;
        validate_environment_graph(&self.graph, &chunks, &intervals)?;
        self.environment = Some(Arc::new(intervals));
        Ok(())
    }

    fn environment_fields(&self) -> Vec<&'static str> {
        environment_cost::FIELDS
            .into_iter()
            .map(environment_cost::field_name)
            .collect()
    }

    #[pyo3(signature = (start_node=None, *, start_segment=None, profile=0, maximum=f64::INFINITY, reverse=false, timeout_seconds=None))]
    #[allow(clippy::too_many_arguments)] // Explicit keyword arguments at the Python boundary.
    fn distances(
        &self,
        py: Python<'_>,
        start_node: Option<i64>,
        start_segment: Option<SegmentEndpoint>,
        profile: usize,
        maximum: f64,
        reverse: bool,
        timeout_seconds: Option<f64>,
    ) -> PyResult<DistanceOutput> {
        let end = deadline(timeout_seconds)?;
        let start = endpoint(start_node, start_segment, None)?;
        let chunks = views(py, &self.owners)?;
        let (result, start_id) = py
            .detach(|| {
                check_deadline(end)?;
                let overlay =
                    endpoint_overlay::prepare_overlay(&self.graph, &chunks, start, None, profile)?;
                let start_id = node_id(&self.graph, overlay.start);
                let settings = SearchSettings {
                    signals: None,
                    profile,
                    cost_budget: maximum,
                    reverse,
                    heuristic_distance_scale: 0.0,
                    deadline: end,
                    costs: CostSettings {
                        hill_weight: 0.0,
                        environment_weights: [0.0; environment_cost::FIELDS.len()],
                        environment: None,
                        shape: None,
                        used_segments: HashSet::new(),
                        used_spans: HashMap::new(),
                        hill_intervals: HashMap::new(),
                    },
                };
                let result = chunk_search::search_routes(&self.graph, &chunks, &overlay, settings)?;
                Ok::<_, RoutingError>((result, start_id))
            })
            .map_err(routing_error)?;
        Ok((
            ints(py, result.distances.iter().map(|(id, _)| *id)),
            floats(py, result.distances.iter().map(|(_, d)| *d)),
            ints(py, result.frontier.into_iter()),
            result.expanded,
            start_id,
        ))
    }

    #[pyo3(signature = (start_node=None, destination_node=None, *, start_segment=None, destination_segment=None,
                       start_point=None, destination_point=None, profile=0, hill_weight=0.0, environment_weights=None, shape=None, used_segments=Vec::new(), used_spans=Vec::new(), hill_intervals=Vec::new(), signal_weight=0.0, signal_arrival=None, timeout_seconds=None))]
    #[allow(clippy::too_many_arguments)] // Core search receives structured settings.
    fn shortest_path(
        &self,
        py: Python<'_>,
        start_node: Option<i64>,
        destination_node: Option<i64>,
        start_segment: Option<SegmentEndpoint>,
        destination_segment: Option<SegmentEndpoint>,
        start_point: Option<(f64, f64)>,
        destination_point: Option<(f64, f64)>,
        profile: usize,
        hill_weight: f64,
        environment_weights: Option<HashMap<String, f64>>,
        shape: Option<ShapeArguments>,
        used_segments: Vec<i64>,
        used_spans: Vec<(i64, f64, f64)>,
        hill_intervals: Vec<(i64, Py<PyBytes>)>,
        signal_weight: f64,
        signal_arrival: Option<(i64, f64, f64)>,
        timeout_seconds: Option<f64>,
    ) -> PyResult<PathOutput> {
        let end = deadline(timeout_seconds)?;
        if !signal_weight.is_finite() || signal_weight < 0.0 {
            return Err(PyValueError::new_err("Invalid signal weight"));
        }
        let signals = if signal_weight > 0.0 {
            let data = self
                .signals
                .clone()
                .ok_or_else(|| PyValueError::new_err("Signal passages are unavailable"))?;
            let arrival = signal_arrival.map(|(segment, start, end)| Arrival {
                segment,
                start,
                end,
            });
            if arrival.is_some_and(|a| {
                a.segment < 0
                    || !a.start.is_finite()
                    || !a.end.is_finite()
                    || a.start < 0.0
                    || a.end < 0.0
                    || a.start == a.end
            }) {
                return Err(PyValueError::new_err("Invalid signal arrival"));
            }
            Some(SignalSettings {
                data,
                weight: signal_weight,
                arrival,
            })
        } else {
            None
        };
        let environment_weights =
            environment_cost::named_weights(environment_weights.unwrap_or_default())
                .map_err(routing_error)?;
        let start = endpoint(start_node, start_segment, start_point)?;
        let destination = endpoint(destination_node, destination_segment, destination_point)?;
        let chunks = views(py, &self.owners)?;
        let hill_intervals = request_hill_intervals(py, hill_intervals, end)?;
        let (result, start_id, destination_id) = py
            .detach(|| {
                check_deadline(end)?;
                let overlay = endpoint_overlay::prepare_overlay(
                    &self.graph,
                    &chunks,
                    start,
                    Some(destination),
                    profile,
                )?;
                let start_id = node_id(&self.graph, overlay.start);
                let destination_id = node_id(&self.graph, overlay.destination.unwrap());
                let settings = SearchSettings {
                    signals,
                    profile,
                    cost_budget: f64::INFINITY,
                    reverse: false,
                    heuristic_distance_scale: self.distance_scale,
                    deadline: end,
                    costs: CostSettings {
                        hill_weight,
                        environment_weights,
                        environment: self.environment.clone(),
                        shape: shape_settings(shape),
                        used_segments: used_segments.into_iter().collect(),
                        used_spans: used_spans.into_iter().fold(
                            HashMap::new(),
                            |mut spans, (id, a, b)| {
                                spans.entry(id).or_insert_with(Vec::new).push((a, b));
                                spans
                            },
                        ),
                        hill_intervals,
                    },
                };
                let result = chunk_search::search_routes(&self.graph, &chunks, &overlay, settings)?;
                Ok::<_, RoutingError>((result, start_id, destination_id))
            })
            .map_err(routing_error)?;
        let edges = result.path.as_deref().unwrap_or(&[]);
        Ok((
            result.cost,
            result.expanded,
            ints(
                py,
                edges
                    .iter()
                    .map(|e| chunks[e.chunk].columns[ARC_IDS].integer(e.arc)),
            ),
            ints(
                py,
                edges
                    .iter()
                    .map(|e| chunks[e.chunk].columns[SEGMENT_IDS].integer(e.segment)),
            ),
            floats(
                py,
                edges
                    .iter()
                    .map(|e| if e.backward { e.high } else { e.low }),
            ),
            floats(
                py,
                edges
                    .iter()
                    .map(|e| if e.backward { e.low } else { e.high }),
            ),
            floats(
                py,
                result
                    .components
                    .iter()
                    .zip(&result.signals)
                    .flat_map(|(c, s)| c.iter().copied().chain(s.iter().map(|n| *n as f64))),
            ),
            floats(py, result.hills.iter().flat_map(|h| h.iter().copied())),
            environment_cost::FIELDS
                .into_iter()
                .map(|field| {
                    let index = field as usize;
                    (
                        environment_cost::field_name(field),
                        (
                            floats(py, result.environment.iter().map(|values| values[index])),
                            floats(
                                py,
                                result
                                    .environment_unknown
                                    .iter()
                                    .map(|values| values[index]),
                            ),
                        ),
                    )
                })
                .collect(),
            ints(py, result.frontier.into_iter()),
            start_id,
            destination_id,
        ))
    }
}
