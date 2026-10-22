//! Shared numeric-node lookup and boundary incidence over immutable chunk views.

use crate::numeric::*;
use std::collections::{HashMap, HashSet};

pub type Bounds = (f64, f64, f64, f64);

#[derive(Clone, Copy, Debug, PartialEq, Eq, Hash)]
pub struct SegmentRef {
    pub chunk: usize,
    pub segment: usize,
}

#[derive(Clone, Copy)]
pub struct Occurrence {
    pub chunk: usize,
    pub node: usize,
}

pub struct GraphIndex {
    pub node_ids: Vec<i64>,
    pub points: Vec<(f64, f64)>,
    pub occurrence_offsets: Vec<usize>,
    pub occurrences: Vec<Occurrence>,
    pub chunk_nodes: Vec<Vec<usize>>,
    pub selected: Vec<Vec<bool>>,
    pub frontier: Vec<bool>,
    pub active: Vec<bool>,
    pub segments: HashMap<i64, SegmentRef>,
    pub lengths: Vec<Vec<Option<f64>>>,
    pub profiles: usize,
    pub node_allowed: Vec<u8>,
}

fn selected_segment(chunk: &Chunk<'_>, segment: usize, bounds: Option<Bounds>) -> bool {
    let Some((west, south, east, north)) = bounds else {
        return true;
    };
    let boxes = chunk.columns[SEGMENT_BOUNDS];
    boxes.float(segment * 4) <= east
        && boxes.float(segment * 4 + 2) >= west
        && boxes.float(segment * 4 + 1) <= north
        && boxes.float(segment * 4 + 3) >= south
}

fn build_node_lookup(chunks: &[Chunk<'_>]) -> Result<GraphIndex, RoutingError> {
    let mut order: Vec<(i64, usize, usize)> = chunks
        .iter()
        .enumerate()
        .flat_map(|(c, chunk)| {
            (0..chunk.counts.nodes).map(move |n| (chunk.columns[NODE_IDS].integer(n), c, n))
        })
        .collect();
    order.sort_unstable();
    let mut graph = GraphIndex {
        node_ids: Vec::new(),
        points: Vec::new(),
        occurrence_offsets: Vec::new(),
        occurrences: Vec::with_capacity(order.len()),
        chunk_nodes: chunks.iter().map(|c| vec![0; c.counts.nodes]).collect(),
        selected: Vec::new(),
        frontier: Vec::new(),
        active: Vec::new(),
        segments: HashMap::new(),
        lengths: Vec::new(),
        profiles: chunks.first().map_or(0, |c| c.counts.profiles),
        node_allowed: Vec::new(),
    };
    let mut last = None;
    let mut degree = 0;
    for (id, c, n) in order {
        if id < 0 || chunks[c].columns[NODE_DEGREE].integer(n) < 0 {
            return Err(RoutingError::Invalid(
                "Invalid global node identity or degree",
            ));
        }
        let point = (
            chunks[c].columns[NODE_XY].float(n * 2),
            chunks[c].columns[NODE_XY].float(n * 2 + 1),
        );
        if last != Some(id) {
            graph.node_ids.push(id);
            graph.points.push(point);
            graph.occurrence_offsets.push(graph.occurrences.len());
            degree = chunks[c].columns[NODE_DEGREE].integer(n);
            let profiles = graph.profiles;
            graph.node_allowed.extend_from_slice(
                &chunks[c].columns[NODE_ALLOWED].bytes[n * profiles..(n + 1) * profiles],
            );
        } else if graph.points.last() != Some(&point)
            || degree != chunks[c].columns[NODE_DEGREE].integer(n)
            || graph.occurrences.last().is_some_and(|o| o.chunk == c)
        {
            return Err(RoutingError::Invalid(
                "Inconsistent duplicate node records across chunks",
            ));
        }
        let profiles = graph.profiles;
        let global = graph.node_ids.len() - 1;
        if graph.node_allowed[global * profiles..(global + 1) * profiles]
            != chunks[c].columns[NODE_ALLOWED].bytes[n * profiles..(n + 1) * profiles]
        {
            return Err(RoutingError::Invalid(
                "Node access masks disagree across chunks",
            ));
        }
        graph.chunk_nodes[c][n] = graph.node_ids.len() - 1;
        graph.occurrences.push(Occurrence { chunk: c, node: n });
        last = Some(id);
    }
    graph.occurrence_offsets.push(graph.occurrences.len());
    Ok(graph)
}

fn register_segments(
    graph: &mut GraphIndex,
    chunks: &[Chunk<'_>],
    bounds: Option<Bounds>,
) -> Result<(), RoutingError> {
    let mut incidence = vec![0i64; graph.node_ids.len()];
    for (c, chunk) in chunks.iter().enumerate() {
        let mut selected = Vec::with_capacity(chunk.counts.segments);
        for s in 0..chunk.counts.segments {
            let id = chunk.columns[SEGMENT_IDS].integer(s);
            if id < 0
                || graph
                    .segments
                    .insert(
                        id,
                        SegmentRef {
                            chunk: c,
                            segment: s,
                        },
                    )
                    .is_some()
            {
                return Err(RoutingError::Invalid(
                    "Duplicate or invalid global segment identity",
                ));
            }
            let active = selected_segment(chunk, s, bounds);
            selected.push(active);
            for direction in 0..2 {
                if active {
                    let node = chunk.columns[SEGMENT_NODES].integer(s * 2 + direction) as usize;
                    incidence[graph.chunk_nodes[c][node]] += 1;
                }
            }
        }
        graph.selected.push(selected);
        graph.lengths.push(vec![None; chunk.counts.segments]);
    }
    graph.active = incidence.iter().map(|n| *n > 0).collect();
    for (node, loaded) in incidence.into_iter().enumerate() {
        let occurrence = graph.occurrences[graph.occurrence_offsets[node]];
        let total = chunks[occurrence.chunk].columns[NODE_DEGREE].integer(occurrence.node);
        if loaded > total {
            return Err(RoutingError::Invalid(
                "Loaded incidence exceeds global node degree",
            ));
        }
        graph.frontier.push(loaded > 0 && loaded < total);
    }
    Ok(())
}

fn register_arcs(graph: &mut GraphIndex, chunks: &[Chunk<'_>]) -> Result<(), RoutingError> {
    let mut ids = HashSet::new();
    for (c, chunk) in chunks.iter().enumerate() {
        if chunk.counts.profiles != graph.profiles {
            return Err(RoutingError::Invalid(
                "Numeric chunks disagree on profile count",
            ));
        }
        for a in 0..chunk.counts.arcs {
            let id = chunk.columns[ARC_IDS].integer(a);
            if id < 0 || !ids.insert(id) {
                return Err(RoutingError::Invalid(
                    "Duplicate or invalid global arc identity",
                ));
            }
            let s = chunk.columns[ARC_SEGMENTS].integer(a) as usize;
            let backward = chunk.columns[ARC_REVERSE].bytes[a] as usize;
            for direction in 0..2 {
                if chunk.columns[ARC_NODES].integer(a * 2 + direction)
                    != chunk.columns[SEGMENT_NODES].integer(s * 2 + (direction ^ backward))
                {
                    return Err(RoutingError::Invalid(
                        "Arc does not follow its physical segment",
                    ));
                }
            }
            for profile in 0..chunk.counts.profiles {
                if chunk.columns[ARC_ALLOWED].bytes[a * chunk.counts.profiles + profile] != 0 {
                    for direction in 0..2 {
                        let node = chunk.columns[ARC_NODES].integer(a * 2 + direction) as usize;
                        if chunk.columns[NODE_ALLOWED].bytes[node * chunk.counts.profiles + profile]
                            == 0
                        {
                            return Err(RoutingError::Invalid(
                                "Eligible arc has a blocked endpoint",
                            ));
                        }
                    }
                }
            }
            let length = chunk.columns[ARC_LENGTHS].float(a);
            if graph.lengths[c][s].is_some_and(|old| (old - length).abs() > 1e-6) {
                return Err(RoutingError::Invalid(
                    "Directed arc lengths disagree on physical segment length",
                ));
            }
            graph.lengths[c][s] = Some(length);
        }
    }
    Ok(())
}

pub fn build_graph(
    chunks: &[Chunk<'_>],
    bounds: Option<Bounds>,
) -> Result<GraphIndex, RoutingError> {
    if let Some((w, s, e, n)) = bounds {
        if ![w, s, e, n].iter().all(|v| v.is_finite()) || w > e || s > n {
            return Err(RoutingError::Invalid(
                "Expected finite ordered projected bounds",
            ));
        }
    }
    let profiles = chunks.first().map_or(0, |c| c.counts.profiles);
    for chunk in chunks {
        if chunk.counts.profiles != profiles {
            return Err(RoutingError::Invalid(
                "Numeric chunks disagree on profile count",
            ));
        }
        validate_chunk(chunk)?;
    }
    let mut graph = build_node_lookup(chunks)?;
    register_segments(&mut graph, chunks, bounds)?;
    register_arcs(&mut graph, chunks)?;
    Ok(graph)
}

pub fn find_node(graph: &GraphIndex, id: i64) -> Result<usize, RoutingError> {
    graph
        .node_ids
        .binary_search(&id)
        .ok()
        .filter(|n| graph.active[*n])
        .ok_or(RoutingError::Invalid(
            "Route endpoint is not in the selected graph",
        ))
}
