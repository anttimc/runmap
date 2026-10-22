//! Request-owned shortest paths and distance budgets across chunk boundaries.

use crate::chunk_graph::{GraphIndex, SegmentRef};
use crate::endpoint_overlay::{Edge, Overlay, node_id};
use crate::numeric::*;
use crate::routing_cost::{CostCache, CostSettings, evaluate_cost, validate_settings};
use std::cmp::Ordering;
use std::collections::{BinaryHeap, HashMap};
use std::time::Instant;

#[derive(Clone, Copy)]
struct QueueEntry {
    priority: f64,
    distance: f64,
    key: i64,
    node: usize,
}
impl PartialEq for QueueEntry {
    fn eq(&self, other: &Self) -> bool {
        self.cmp(other) == Ordering::Equal
    }
}
impl Eq for QueueEntry {}
impl PartialOrd for QueueEntry {
    fn partial_cmp(&self, other: &Self) -> Option<Ordering> {
        Some(self.cmp(other))
    }
}
impl Ord for QueueEntry {
    fn cmp(&self, other: &Self) -> Ordering {
        other
            .priority
            .total_cmp(&self.priority)
            .then_with(|| other.distance.total_cmp(&self.distance))
            .then_with(|| other.key.cmp(&self.key))
            .then_with(|| other.node.cmp(&self.node))
    }
}

pub struct SearchSettings {
    pub profile: usize,
    pub maximum: f64,
    pub reverse: bool,
    pub distance_scale: f64,
    pub deadline: Option<Instant>,
    pub costs: CostSettings,
}

pub struct SearchResult {
    pub distances: Vec<(i64, f64)>,
    pub frontier: Vec<i64>,
    pub expanded: usize,
    pub path: Option<Vec<Edge>>,
    pub cost: Option<f64>,
    pub components: Vec<[f64; 5]>,
    pub hills: Vec<[f64; 7]>,
}

pub fn check_deadline(deadline: Option<Instant>) -> Result<(), RoutingError> {
    if deadline.is_some_and(|end| Instant::now() >= end) {
        Err(RoutingError::TimedOut)
    } else {
        Ok(())
    }
}

fn node_point(graph: &GraphIndex, overlay: &Overlay, node: usize) -> (f64, f64) {
    if node < graph.points.len() {
        graph.points[node]
    } else {
        overlay.points[node - graph.points.len()]
    }
}

fn heuristic_scale(graph: &GraphIndex, overlay: &Overlay, mut scale: f64) -> f64 {
    for edge in &overlay.edges {
        let a = node_point(graph, overlay, edge.source);
        let b = node_point(graph, overlay, edge.target);
        let distance = (a.0 - b.0).hypot(a.1 - b.1);
        if distance > 0.0 {
            scale = scale.min(edge.length / distance);
        }
    }
    scale * (1.0 - 1e-12)
}

fn estimate(graph: &GraphIndex, overlay: &Overlay, node: usize, scale: f64) -> f64 {
    overlay.destination.map_or(0.0, |target| {
        let a = node_point(graph, overlay, node);
        let b = node_point(graph, overlay, target);
        scale * (a.0 - b.0).hypot(a.1 - b.1)
    })
}

fn base_edges(
    graph: &GraphIndex,
    chunks: &[Chunk<'_>],
    overlay: &Overlay,
    node: usize,
    settings: &SearchSettings,
    mut visit: impl FnMut(Edge) -> Result<(), RoutingError>,
) -> Result<(), RoutingError> {
    if node >= graph.node_ids.len() {
        return Ok(());
    }
    for occurrence in
        &graph.occurrences[graph.occurrence_offsets[node]..graph.occurrence_offsets[node + 1]]
    {
        let c = occurrence.chunk;
        let chunk = &chunks[c];
        let (offsets, entries) = if settings.reverse {
            (INCOMING_OFFSETS, INCOMING_ARCS)
        } else {
            (OUTGOING_OFFSETS, OUTGOING_ARCS)
        };
        let lo = chunk.columns[offsets].integer(occurrence.node) as usize;
        let hi = chunk.columns[offsets].integer(occurrence.node + 1) as usize;
        for i in lo..hi {
            let arc = chunk.columns[entries].integer(i) as usize;
            let s = chunk.columns[ARC_SEGMENTS].integer(arc) as usize;
            if !graph.selected[c][s]
                || overlay.affected.contains(&SegmentRef {
                    chunk: c,
                    segment: s,
                })
                || chunk.columns[ARC_ALLOWED].bytes[arc * chunk.counts.profiles + settings.profile]
                    == 0
            {
                continue;
            }
            let source = graph.chunk_nodes[c][chunk.columns[ARC_NODES].integer(arc * 2) as usize];
            let target =
                graph.chunk_nodes[c][chunk.columns[ARC_NODES].integer(arc * 2 + 1) as usize];
            let length = chunk.columns[ARC_LENGTHS].float(arc);
            visit(Edge {
                chunk: c,
                arc,
                segment: s,
                source,
                target,
                length,
                low: 0.0,
                high: length,
                backward: chunk.columns[ARC_REVERSE].bytes[arc] != 0,
                geometry_low: None,
                geometry_high: None,
                geometry_endpoints: None,
            })?;
        }
    }
    Ok(())
}

fn visit_edges(
    graph: &GraphIndex,
    chunks: &[Chunk<'_>],
    overlay: &Overlay,
    node: usize,
    settings: &SearchSettings,
    mut visit: impl FnMut(Edge) -> Result<(), RoutingError>,
) -> Result<(), RoutingError> {
    base_edges(graph, chunks, overlay, node, settings, &mut visit)?;
    let extra = if settings.reverse {
        overlay.incoming.get(&node)
    } else {
        overlay.outgoing.get(&node)
    };
    for index in extra.into_iter().flatten() {
        let edge = overlay.edges[*index];
        let chunk = &chunks[edge.chunk];
        if chunk.columns[ARC_ALLOWED].bytes[edge.arc * chunk.counts.profiles + settings.profile]
            != 0
        {
            visit(edge)?;
        }
    }
    Ok(())
}

fn reconstruct(overlay: &Overlay, previous: &[Option<Edge>], reverse: bool) -> Option<Vec<Edge>> {
    let mut node = overlay.destination?;
    let mut edges = Vec::new();
    while node != overlay.start {
        let edge = previous[node]?;
        edges.push(edge);
        node = if reverse { edge.target } else { edge.source };
    }
    edges.reverse();
    Some(edges)
}

struct SearchState {
    values: Vec<f64>,
    previous: Vec<Option<Edge>>,
    cache: CostCache,
    queue: BinaryHeap<QueueEntry>,
    expanded: usize,
    frontier: Vec<i64>,
    scale: f64,
}

fn initialize(graph: &GraphIndex, overlay: &Overlay, settings: &SearchSettings) -> SearchState {
    let count = graph.node_ids.len() + overlay.points.len();
    let scale = heuristic_scale(graph, overlay, settings.distance_scale);
    let mut values = vec![f64::INFINITY; count];
    values[overlay.start] = 0.0;
    SearchState {
        values,
        previous: if overlay.destination.is_some() {
            vec![None; count]
        } else {
            Vec::new()
        },
        cache: CostCache {
            integrals: HashMap::new(),
        },
        queue: BinaryHeap::from([QueueEntry {
            priority: estimate(graph, overlay, overlay.start, scale),
            distance: 0.0,
            key: node_id(graph, overlay.start),
            node: overlay.start,
        }]),
        expanded: 0,
        frontier: Vec::new(),
        scale,
    }
}

fn relax(
    graph: &GraphIndex,
    chunks: &[Chunk<'_>],
    overlay: &Overlay,
    settings: &SearchSettings,
    state: &mut SearchState,
    distance: f64,
    edge: Edge,
) -> Result<(), RoutingError> {
    let next = if settings.reverse {
        edge.source
    } else {
        edge.target
    };
    let (components, _) = evaluate_cost(
        chunks,
        edge,
        &settings.costs,
        &mut state.cache,
        settings.deadline,
    )?;
    let candidate = distance + components.iter().sum::<f64>();
    if candidate <= settings.maximum && candidate < state.values[next] {
        state.values[next] = candidate;
        if !state.previous.is_empty() {
            state.previous[next] = Some(edge);
        }
        state.queue.push(QueueEntry {
            priority: candidate + estimate(graph, overlay, next, state.scale),
            distance: candidate,
            key: node_id(graph, next),
            node: next,
        });
    }
    Ok(())
}

fn settle_nodes(
    graph: &GraphIndex,
    chunks: &[Chunk<'_>],
    overlay: &Overlay,
    settings: &SearchSettings,
    state: &mut SearchState,
) -> Result<(), RoutingError> {
    while let Some(entry) = state.queue.pop() {
        check_deadline(settings.deadline)?;
        let node = entry.node;
        if entry.distance != state.values[node] {
            continue;
        }
        if node < graph.frontier.len() && graph.frontier[node] {
            state.frontier.push(node_id(graph, node));
        }
        if overlay.destination == Some(node) {
            break;
        }
        state.expanded += 1;
        visit_edges(graph, chunks, overlay, node, settings, |edge| {
            relax(
                graph,
                chunks,
                overlay,
                settings,
                state,
                entry.distance,
                edge,
            )
        })?;
    }
    Ok(())
}

fn finish(
    graph: &GraphIndex,
    chunks: &[Chunk<'_>],
    overlay: &Overlay,
    settings: &SearchSettings,
    mut state: SearchState,
) -> Result<SearchResult, RoutingError> {
    let path = reconstruct(overlay, &state.previous, settings.reverse);
    let cost = overlay
        .destination
        .and_then(|n| state.values[n].is_finite().then_some(state.values[n]));
    let mut components = Vec::new();
    let mut hills = Vec::new();
    for edge in path.iter().flatten() {
        check_deadline(settings.deadline)?;
        let (c, h) = evaluate_cost(
            chunks,
            *edge,
            &settings.costs,
            &mut state.cache,
            settings.deadline,
        )?;
        components.push(c);
        hills.push(h);
    }
    let distances = if overlay.destination.is_none() {
        state
            .values
            .into_iter()
            .enumerate()
            .filter(|(_, v)| v.is_finite())
            .map(|(n, v)| (node_id(graph, n), v))
            .collect()
    } else {
        Vec::new()
    };
    Ok(SearchResult {
        distances,
        frontier: state.frontier,
        expanded: state.expanded,
        path,
        cost,
        components,
        hills,
    })
}

pub fn search(
    graph: &GraphIndex,
    chunks: &[Chunk<'_>],
    overlay: &Overlay,
    settings: SearchSettings,
) -> Result<SearchResult, RoutingError> {
    check_deadline(settings.deadline)?;
    validate_settings(&settings.costs)?;
    if settings.profile >= graph.profiles
        || settings.maximum.is_nan()
        || settings.maximum < 0.0
        || !settings.distance_scale.is_finite()
        || !(0.0..=1.0).contains(&settings.distance_scale)
    {
        return Err(RoutingError::Invalid(
            "Invalid profile, distance budget, or heuristic scale",
        ));
    }
    let blocked = |node: usize| {
        node < graph.node_ids.len()
            && graph.node_allowed[node * graph.profiles + settings.profile] == 0
    };
    if blocked(overlay.start) || overlay.destination.is_some_and(blocked) {
        return Ok(SearchResult {
            distances: Vec::new(),
            frontier: Vec::new(),
            expanded: 0,
            path: None,
            cost: None,
            components: Vec::new(),
            hills: Vec::new(),
        });
    }
    let mut state = initialize(graph, overlay, &settings);
    settle_nodes(graph, chunks, overlay, &settings, &mut state)?;
    finish(graph, chunks, overlay, &settings, state)
}
