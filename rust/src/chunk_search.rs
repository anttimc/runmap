//! Request-owned shortest paths and cost budgets across chunk boundaries.

use crate::chunk_graph::{GraphIndex, SegmentRef};
use crate::endpoint_overlay::{Edge, Overlay, node_id};
use crate::numeric::*;
use crate::routing_cost::{CostCache, CostSettings, evaluate_cost, validate_settings};
use std::cmp::Ordering;
use std::collections::{BinaryHeap, HashMap};
use std::time::Instant;

#[derive(Clone, Copy)]
struct QueueEntry {
    estimated_total_cost: f64,
    cost_so_far: f64,
    node_id: i64,
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
            .estimated_total_cost
            .total_cmp(&self.estimated_total_cost)
            .then_with(|| other.cost_so_far.total_cmp(&self.cost_so_far))
            .then_with(|| other.node_id.cmp(&self.node_id))
            .then_with(|| other.node.cmp(&self.node))
    }
}

pub struct SearchSettings {
    pub profile: usize,
    pub cost_budget: f64,
    pub reverse: bool,
    pub heuristic_distance_scale: f64,
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
    pub environment: Vec<[f64; 4]>,
    pub environment_unknown: Vec<[f64; 4]>,
}

pub fn check_deadline(deadline: Option<Instant>) -> Result<(), RoutingError> {
    if deadline.is_some_and(|end| Instant::now() >= end) {
        Err(RoutingError::TimedOut)
    } else {
        Ok(())
    }
}

fn node_coordinates(graph: &GraphIndex, overlay: &Overlay, node: usize) -> (f64, f64) {
    if node < graph.points.len() {
        graph.points[node]
    } else {
        overlay.points[node - graph.points.len()]
    }
}

fn compute_safe_heuristic_scale(graph: &GraphIndex, overlay: &Overlay, mut scale: f64) -> f64 {
    for edge in &overlay.edges {
        let a = node_coordinates(graph, overlay, edge.source);
        let b = node_coordinates(graph, overlay, edge.target);
        let straight_line_distance = (a.0 - b.0).hypot(a.1 - b.1);
        if straight_line_distance > 0.0 {
            scale = scale.min(edge.length / straight_line_distance);
        }
    }
    scale * (1.0 - 1e-12)
}

fn estimate_remaining_cost(graph: &GraphIndex, overlay: &Overlay, node: usize, scale: f64) -> f64 {
    overlay.destination.map_or(0.0, |target| {
        let a = node_coordinates(graph, overlay, node);
        let b = node_coordinates(graph, overlay, target);
        scale * (a.0 - b.0).hypot(a.1 - b.1)
    })
}

fn visit_base_graph_edges(
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

fn visit_traversable_edges(
    graph: &GraphIndex,
    chunks: &[Chunk<'_>],
    overlay: &Overlay,
    node: usize,
    settings: &SearchSettings,
    mut visit: impl FnMut(Edge) -> Result<(), RoutingError>,
) -> Result<(), RoutingError> {
    visit_base_graph_edges(graph, chunks, overlay, node, settings, &mut visit)?;
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

fn reconstruct_path(
    overlay: &Overlay,
    predecessor_edges: &[Option<Edge>],
    reverse: bool,
) -> Option<Vec<Edge>> {
    let mut node = overlay.destination?;
    let mut edges = Vec::new();
    while node != overlay.start {
        let edge = predecessor_edges[node]?;
        edges.push(edge);
        node = if reverse { edge.target } else { edge.source };
    }
    edges.reverse();
    Some(edges)
}

struct SearchState {
    best_costs: Vec<f64>,
    predecessor_edges: Vec<Option<Edge>>,
    cache: CostCache,
    open_queue: BinaryHeap<QueueEntry>,
    expanded: usize,
    frontier: Vec<i64>,
    scale: f64,
}

fn initialize_search_state(
    graph: &GraphIndex,
    overlay: &Overlay,
    settings: &SearchSettings,
) -> SearchState {
    let _profile = crate::profiling::span(crate::profiling::Phase::Initialize);
    let count = graph.node_ids.len() + overlay.points.len();
    let scale = compute_safe_heuristic_scale(graph, overlay, settings.heuristic_distance_scale);
    let mut best_costs = vec![f64::INFINITY; count];
    best_costs[overlay.start] = 0.0;
    SearchState {
        best_costs,
        predecessor_edges: if overlay.destination.is_some() {
            vec![None; count]
        } else {
            Vec::new()
        },
        cache: CostCache {
            integrals: HashMap::new(),
        },
        open_queue: BinaryHeap::from([QueueEntry {
            estimated_total_cost: estimate_remaining_cost(graph, overlay, overlay.start, scale),
            cost_so_far: 0.0,
            node_id: node_id(graph, overlay.start),
            node: overlay.start,
        }]),
        expanded: 0,
        frontier: Vec::new(),
        scale,
    }
}

fn relax_edge(
    graph: &GraphIndex,
    chunks: &[Chunk<'_>],
    overlay: &Overlay,
    settings: &SearchSettings,
    state: &mut SearchState,
    cost_so_far: f64,
    edge: Edge,
) -> Result<(), RoutingError> {
    let next = if settings.reverse {
        edge.source
    } else {
        edge.target
    };
    let (components, _, environment, _) = evaluate_cost(
        chunks,
        edge,
        &settings.costs,
        &mut state.cache,
        settings.deadline,
    )?;
    let candidate = cost_so_far + components.iter().chain(environment.iter()).sum::<f64>();
    if candidate <= settings.cost_budget && candidate < state.best_costs[next] {
        state.best_costs[next] = candidate;
        if !state.predecessor_edges.is_empty() {
            state.predecessor_edges[next] = Some(edge);
        }
        state.open_queue.push(QueueEntry {
            estimated_total_cost: candidate
                + estimate_remaining_cost(graph, overlay, next, state.scale),
            cost_so_far: candidate,
            node_id: node_id(graph, next),
            node: next,
        });
    }
    Ok(())
}

fn expand_search_frontier(
    graph: &GraphIndex,
    chunks: &[Chunk<'_>],
    overlay: &Overlay,
    settings: &SearchSettings,
    state: &mut SearchState,
) -> Result<(), RoutingError> {
    let _profile = crate::profiling::span(crate::profiling::Phase::Traversal);
    while let Some(entry) = state.open_queue.pop() {
        check_deadline(settings.deadline)?;
        let node = entry.node;
        if entry.cost_so_far != state.best_costs[node] {
            continue;
        }
        if node < graph.frontier.len() && graph.frontier[node] {
            state.frontier.push(node_id(graph, node));
        }
        if overlay.destination == Some(node) {
            break;
        }
        state.expanded += 1;
        visit_traversable_edges(graph, chunks, overlay, node, settings, |edge| {
            relax_edge(
                graph,
                chunks,
                overlay,
                settings,
                state,
                entry.cost_so_far,
                edge,
            )
        })?;
    }
    Ok(())
}

fn build_search_result(
    graph: &GraphIndex,
    chunks: &[Chunk<'_>],
    overlay: &Overlay,
    settings: &SearchSettings,
    mut state: SearchState,
) -> Result<SearchResult, RoutingError> {
    let _profile = crate::profiling::span(crate::profiling::Phase::Result);
    let path = reconstruct_path(overlay, &state.predecessor_edges, settings.reverse);
    let cost = overlay.destination.and_then(|n| {
        state.best_costs[n]
            .is_finite()
            .then_some(state.best_costs[n])
    });
    let mut components = Vec::new();
    let mut hills = Vec::new();
    let mut environment = Vec::new();
    let mut environment_unknown = Vec::new();
    for edge in path.iter().flatten() {
        check_deadline(settings.deadline)?;
        let (c, h, e, u) = evaluate_cost(
            chunks,
            *edge,
            &settings.costs,
            &mut state.cache,
            settings.deadline,
        )?;
        components.push(c);
        hills.push(h);
        environment.push(e);
        environment_unknown.push(u);
    }
    let distances = if overlay.destination.is_none() {
        state
            .best_costs
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
        environment,
        environment_unknown,
    })
}

pub fn search_routes(
    graph: &GraphIndex,
    chunks: &[Chunk<'_>],
    overlay: &Overlay,
    settings: SearchSettings,
) -> Result<SearchResult, RoutingError> {
    check_deadline(settings.deadline)?;
    validate_settings(&settings.costs)?;
    if settings.profile >= graph.profiles
        || settings.cost_budget.is_nan()
        || settings.cost_budget < 0.0
        || !settings.heuristic_distance_scale.is_finite()
        || !(0.0..=1.0).contains(&settings.heuristic_distance_scale)
    {
        return Err(RoutingError::Invalid(
            "Invalid profile, cost budget, or heuristic scale",
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
            environment: Vec::new(),
            environment_unknown: Vec::new(),
        });
    }
    let mut state = initialize_search_state(graph, overlay, &settings);
    expand_search_frontier(graph, chunks, overlay, &settings, &mut state)?;
    build_search_result(graph, chunks, overlay, &settings, state)
}
