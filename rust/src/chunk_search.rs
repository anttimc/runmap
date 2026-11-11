//! Request-owned shortest paths and cost budgets across chunk boundaries.

use crate::chunk_graph::{GraphIndex, SegmentRef};
use crate::endpoint_overlay::{Edge, Overlay, node_id};
use crate::numeric::*;
use crate::routing_cost::{
    CostCache, CostSettings, evaluate_cost, evaluate_search_cost, validate_settings,
};
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
    pub signals: Option<crate::signal_cost::SignalSettings>,
    pub profile: usize,
    pub cost_budget: f64,
    pub reverse: bool,
    pub heuristic_distance_scale: f64,
    pub deadline: Option<Instant>,
    pub costs: CostSettings,
}

pub struct SearchResult {
    pub signals: Vec<[i64; 2]>,
    pub distances: Vec<(i64, f64)>,
    pub frontier: Vec<i64>,
    pub expanded: usize,
    pub path: Option<Vec<Edge>>,
    pub cost: Option<f64>,
    pub components: Vec<[f64; 5]>,
    pub hills: Vec<[f64; 7]>,
    pub environment: Vec<crate::environment_cost::FieldValues>,
    pub environment_unknown: Vec<crate::environment_cost::FieldValues>,
}

#[derive(Clone, Copy)]
enum EdgeRef {
    Base { chunk: usize, arc: usize },
    Overlay(usize),
}

struct EdgeVisit<'a> {
    edge: &'a Edge,
    reference: EdgeRef,
}

fn base_graph_edge(
    graph: &GraphIndex,
    chunk: &Chunk<'_>,
    chunk_index: usize,
    arc: usize,
    segment: usize,
) -> Edge {
    let source = graph.chunk_nodes[chunk_index][chunk.columns[ARC_NODES].integer(arc * 2) as usize];
    let target =
        graph.chunk_nodes[chunk_index][chunk.columns[ARC_NODES].integer(arc * 2 + 1) as usize];
    let length = chunk.columns[ARC_LENGTHS].float(arc);
    Edge {
        chunk: chunk_index,
        arc,
        segment,
        source,
        target,
        length,
        low: 0.0,
        high: length,
        backward: chunk.columns[ARC_REVERSE].bytes[arc] != 0,
        geometry_low: None,
        geometry_high: None,
        geometry_endpoints: None,
    }
}

fn resolve_edge(
    graph: &GraphIndex,
    chunks: &[Chunk<'_>],
    overlay: &Overlay,
    reference: EdgeRef,
) -> Edge {
    match reference {
        EdgeRef::Base { chunk, arc } => base_graph_edge(
            graph,
            &chunks[chunk],
            chunk,
            arc,
            chunks[chunk].columns[ARC_SEGMENTS].integer(arc) as usize,
        ),
        EdgeRef::Overlay(index) => overlay.edges[index],
    }
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
    mut visit: impl FnMut(EdgeVisit<'_>) -> Result<(), RoutingError>,
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
            visit(EdgeVisit {
                edge: &base_graph_edge(graph, chunk, c, arc, s),
                reference: EdgeRef::Base { chunk: c, arc },
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
    mut visit: impl FnMut(EdgeVisit<'_>) -> Result<(), RoutingError>,
) -> Result<(), RoutingError> {
    visit_base_graph_edges(graph, chunks, overlay, node, settings, &mut visit)?;
    let extra = if settings.reverse {
        overlay.incoming.get(&node)
    } else {
        overlay.outgoing.get(&node)
    };
    for index in extra.into_iter().flatten() {
        let edge = &overlay.edges[*index];
        let chunk = &chunks[edge.chunk];
        if chunk.columns[ARC_ALLOWED].bytes[edge.arc * chunk.counts.profiles + settings.profile]
            != 0
        {
            visit(EdgeVisit {
                edge,
                reference: EdgeRef::Overlay(*index),
            })?;
        }
    }
    Ok(())
}

fn reconstruct_path(
    graph: &GraphIndex,
    chunks: &[Chunk<'_>],
    overlay: &Overlay,
    predecessor_edges: &[Option<EdgeRef>],
    reverse: bool,
) -> Option<Vec<Edge>> {
    let mut node = overlay.destination?;
    let mut edges = Vec::new();
    while node != overlay.start {
        let edge = resolve_edge(graph, chunks, overlay, predecessor_edges[node]?);
        edges.push(edge);
        node = if reverse { edge.target } else { edge.source };
    }
    edges.reverse();
    Some(edges)
}

struct SearchState {
    best_costs: Vec<f64>,
    predecessor_edges: Vec<Option<EdgeRef>>,
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
    traversal: EdgeVisit<'_>,
) -> Result<(), RoutingError> {
    let edge = traversal.edge;
    let next = if settings.reverse {
        edge.source
    } else {
        edge.target
    };
    // Every cost term is nonnegative, so this edge cannot improve the label.
    if cost_so_far >= state.best_costs[next] {
        return Ok(());
    }
    let cost = evaluate_search_cost(
        chunks,
        *edge,
        &settings.costs,
        &mut state.cache,
        settings.deadline,
    )?;
    let candidate = cost_so_far + cost;
    if candidate <= settings.cost_budget && candidate < state.best_costs[next] {
        state.best_costs[next] = candidate;
        if !state.predecessor_edges.is_empty() {
            state.predecessor_edges[next] = Some(traversal.reference);
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
    let path = reconstruct_path(
        graph,
        chunks,
        overlay,
        &state.predecessor_edges,
        settings.reverse,
    );
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
        let cost = evaluate_cost(
            chunks,
            *edge,
            &settings.costs,
            &mut state.cache,
            settings.deadline,
        )?;
        components.push(cost.components);
        hills.push(cost.hill_features);
        environment.push(cost.environment_penalties);
        environment_unknown.push(cost.environment_unknown_m);
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
        signals: vec![[0; 2]; components.len()],
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
    if let Some(signals) = &settings.signals {
        if !signals.weight.is_finite() || signals.weight < 0.0 {
            return Err(RoutingError::Invalid("Invalid signal weight"));
        }
    }
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
            signals: Vec::new(),
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
    if settings.signals.is_some() {
        return search_signal_routes(graph, chunks, overlay, &settings);
    }
    let mut state = initialize_search_state(graph, overlay, &settings);
    expand_search_frontier(graph, chunks, overlay, &settings, &mut state)?;
    build_search_result(graph, chunks, overlay, &settings, state)
}

#[derive(Clone, Copy, PartialEq, Eq, Hash)]
struct MovementState {
    node: usize,
    segment: i64,
    backward: bool,
}

struct MovementLabel {
    state: MovementState,
    arrival: Option<crate::signal_cost::Arrival>,
    cost: f64,
    predecessor: Option<(usize, EdgeRef)>,
}

struct MovementSearch {
    labels: Vec<MovementLabel>,
    indices: HashMap<MovementState, usize>,
    queue: BinaryHeap<QueueEntry>,
    cache: CostCache,
    frontier: Vec<i64>,
    expanded: usize,
    destination: Option<usize>,
    scale: f64,
}

fn initialize_movement_search(
    graph: &GraphIndex,
    overlay: &Overlay,
    settings: &SearchSettings,
) -> MovementSearch {
    let signals = settings.signals.as_ref().unwrap();
    let scale = compute_safe_heuristic_scale(graph, overlay, settings.heuristic_distance_scale);
    let start = MovementState {
        node: overlay.start,
        segment: signals.arrival.map_or(-1, |a| a.segment),
        backward: signals.arrival.is_some_and(|a| a.end < a.start),
    };
    let labels = vec![MovementLabel {
        state: start,
        arrival: signals.arrival,
        cost: 0.0,
        predecessor: None,
    }];
    let indices = HashMap::from([(start, 0_usize)]);
    let queue = BinaryHeap::from([QueueEntry {
        estimated_total_cost: estimate_remaining_cost(graph, overlay, start.node, scale),
        cost_so_far: 0.0,
        node_id: node_id(graph, start.node),
        node: 0,
    }]);
    let cache = CostCache {
        integrals: HashMap::new(),
    };
    MovementSearch {
        labels,
        indices,
        queue,
        cache,
        frontier: Vec::new(),
        expanded: 0,
        destination: None,
        scale,
    }
}

fn relax_movement_edge(
    graph: &GraphIndex,
    chunks: &[Chunk<'_>],
    overlay: &Overlay,
    settings: &SearchSettings,
    search: &mut MovementSearch,
    entry: QueueEntry,
    visit: EdgeVisit<'_>,
) -> Result<(), RoutingError> {
    use crate::signal_cost::{arrival, counts};
    let signals = settings.signals.as_ref().unwrap();
    let edge = *visit.edge;
    let current = arrival(chunks, edge);
    let label = &search.labels[entry.node];
    let previous = label.arrival;
    // Retracing a spur cannot change which side of a crossing we approached from.
    // A requested turnaround starts a new leg and may reverse its initial arrival.
    if label.predecessor.is_some()
        && previous.is_some_and(|prior| {
            prior.segment == current.segment
                && (prior.end > prior.start) != (current.end > current.start)
        })
    {
        return Ok(());
    }
    let state = MovementState {
        node: edge.target,
        segment: current.segment,
        backward: edge.backward,
    };
    let number = *search.indices.entry(state).or_insert_with(|| {
        let number = search.labels.len();
        search.labels.push(MovementLabel {
            state,
            arrival: Some(current),
            cost: f64::INFINITY,
            predecessor: None,
        });
        number
    });
    if entry.cost_so_far >= search.labels[number].cost {
        return Ok(());
    }
    let passages = counts(
        &signals.data,
        previous,
        current,
        node_id(graph, edge.source),
    );
    let cost = entry.cost_so_far
        + evaluate_search_cost(
            chunks,
            edge,
            &settings.costs,
            &mut search.cache,
            settings.deadline,
        )?
        + signals.weight * (passages[0] + passages[1]) as f64;
    if !cost.is_finite() {
        return Err(RoutingError::Invalid("Signal cost overflow"));
    }
    if cost < search.labels[number].cost && cost <= settings.cost_budget {
        search.labels[number].cost = cost;
        search.labels[number].arrival = Some(current);
        search.labels[number].predecessor = Some((entry.node, visit.reference));
        search.queue.push(QueueEntry {
            estimated_total_cost: cost
                + estimate_remaining_cost(graph, overlay, edge.target, search.scale),
            cost_so_far: cost,
            node_id: node_id(graph, edge.target),
            node: number,
        });
    }
    Ok(())
}

fn expand_movement_frontier(
    graph: &GraphIndex,
    chunks: &[Chunk<'_>],
    overlay: &Overlay,
    settings: &SearchSettings,
    search: &mut MovementSearch,
) -> Result<(), RoutingError> {
    while let Some(entry) = search.queue.pop() {
        check_deadline(settings.deadline)?;
        let label = &search.labels[entry.node];
        if entry.cost_so_far != label.cost {
            continue;
        }
        let node = label.state.node;
        if node < graph.frontier.len() && graph.frontier[node] {
            search.frontier.push(node_id(graph, node));
        }
        if overlay.destination == Some(node) {
            search.destination = Some(entry.node);
            break;
        }
        search.expanded += 1;
        visit_traversable_edges(graph, chunks, overlay, node, settings, |visit| {
            relax_movement_edge(graph, chunks, overlay, settings, search, entry, visit)
        })?;
    }
    Ok(())
}

fn build_movement_result(
    graph: &GraphIndex,
    chunks: &[Chunk<'_>],
    overlay: &Overlay,
    settings: &SearchSettings,
    mut search: MovementSearch,
) -> Result<SearchResult, RoutingError> {
    use crate::signal_cost::{arrival, counts};
    let signals = settings.signals.as_ref().unwrap();
    let cost = search.destination.map(|n| search.labels[n].cost);
    let path = search.destination.map(|mut n| {
        let mut path = Vec::new();
        while let Some((previous, reference)) = search.labels[n].predecessor {
            path.push(resolve_edge(graph, chunks, overlay, reference));
            n = previous;
        }
        path.reverse();
        path
    });
    let mut result = SearchResult {
        distances: Vec::new(),
        frontier: search.frontier,
        expanded: search.expanded,
        path,
        cost,
        components: Vec::new(),
        hills: Vec::new(),
        environment: Vec::new(),
        environment_unknown: Vec::new(),
        signals: Vec::new(),
    };
    let mut previous = signals.arrival;
    for edge in result.path.iter().flatten() {
        check_deadline(settings.deadline)?;
        let current = arrival(chunks, *edge);
        result.signals.push(counts(
            &signals.data,
            previous,
            current,
            node_id(graph, edge.source),
        ));
        previous = Some(current);
        let cost = evaluate_cost(
            chunks,
            *edge,
            &settings.costs,
            &mut search.cache,
            settings.deadline,
        )?;
        result.components.push(cost.components);
        result.hills.push(cost.hill_features);
        result.environment.push(cost.environment_penalties);
        result.environment_unknown.push(cost.environment_unknown_m);
    }
    Ok(result)
}

fn search_signal_routes(
    graph: &GraphIndex,
    chunks: &[Chunk<'_>],
    overlay: &Overlay,
    settings: &SearchSettings,
) -> Result<SearchResult, RoutingError> {
    if settings.reverse || overlay.destination.is_none() {
        return Err(RoutingError::Invalid(
            "Signal search requires a forward destination",
        ));
    }
    let mut search = initialize_movement_search(graph, overlay, settings);
    expand_movement_frontier(graph, chunks, overlay, settings, &mut search)?;
    build_movement_result(graph, chunks, overlay, settings, search)
}
