//! Small request-owned partial-segment overlays; shared chunks stay unchanged.

use crate::chunk_graph::{GraphIndex, SegmentRef, find_node};
use crate::numeric::*;
use std::collections::HashMap;

#[derive(Clone, Copy)]
pub enum Endpoint {
    Node(i64),
    Segment {
        id: i64,
        offset: f64,
        chainage: Option<f64>,
        point: Option<(f64, f64)>,
    },
}

#[derive(Clone, Copy)]
pub struct Edge {
    pub chunk: usize,
    pub arc: usize,
    pub segment: usize,
    pub source: usize,
    pub target: usize,
    pub length: f64,
    pub low: f64,
    pub high: f64,
    pub backward: bool,
    pub geometry_low: Option<f64>,
    pub geometry_high: Option<f64>,
    pub geometry_endpoints: Option<((f64, f64), (f64, f64))>,
}

pub struct Overlay {
    pub start: usize,
    pub destination: Option<usize>,
    pub points: Vec<(f64, f64)>,
    pub affected: Vec<SegmentRef>,
    pub edges: Vec<Edge>,
    pub outgoing: HashMap<usize, Vec<usize>>,
    pub incoming: HashMap<usize, Vec<usize>>,
}

#[derive(Clone, Copy)]
struct Cut {
    offset: f64,
    chainage: f64,
    node: usize,
    point: (f64, f64),
}

pub fn segment_points(chunk: &Chunk<'_>, segment: usize) -> Vec<(f64, f64)> {
    let lo = chunk.columns[GEOMETRY_OFFSETS].integer(segment) as usize;
    let hi = chunk.columns[GEOMETRY_OFFSETS].integer(segment + 1) as usize;
    (lo..hi)
        .map(|i| {
            (
                chunk.columns[GEOMETRY_XY].float(i * 2),
                chunk.columns[GEOMETRY_XY].float(i * 2 + 1),
            )
        })
        .collect()
}

pub fn projected_length(points: &[(f64, f64)]) -> f64 {
    points
        .windows(2)
        .map(|p| (p[1].0 - p[0].0).hypot(p[1].1 - p[0].1))
        .sum()
}

fn point_at(points: &[(f64, f64)], chainage: f64) -> (f64, f64) {
    let mut remaining = chainage;
    for pair in points.windows(2) {
        let length = (pair[1].0 - pair[0].0).hypot(pair[1].1 - pair[0].1);
        if length > 0.0 && remaining <= length {
            let f = remaining / length;
            return (
                pair[0].0 + f * (pair[1].0 - pair[0].0),
                pair[0].1 + f * (pair[1].1 - pair[0].1),
            );
        }
        remaining -= length;
    }
    *points.last().unwrap()
}

fn resolve_endpoint(
    graph: &GraphIndex,
    chunks: &[Chunk<'_>],
    endpoint: Endpoint,
    profile: usize,
    overlay: &mut Overlay,
    cuts: &mut HashMap<SegmentRef, Vec<Cut>>,
) -> Result<usize, RoutingError> {
    let Endpoint::Segment {
        id,
        offset,
        chainage,
        point,
    } = endpoint
    else {
        if let Endpoint::Node(id) = endpoint {
            return find_node(graph, id);
        }
        unreachable!();
    };
    let segment = *graph
        .segments
        .get(&id)
        .ok_or(RoutingError::Invalid("Endpoint segment is not loaded"))?;
    let chunk = &chunks[segment.chunk];
    let length = graph.lengths[segment.chunk][segment.segment]
        .ok_or(RoutingError::Invalid("Endpoint segment has no arcs"))?;
    if !graph.selected[segment.chunk][segment.segment]
        || !offset.is_finite()
        || offset < 0.0
        || offset > length
    {
        return Err(RoutingError::Invalid(
            "Endpoint offset is outside the selected segment",
        ));
    }
    let eligible = (0..chunk.counts.arcs).any(|a| {
        chunk.columns[ARC_SEGMENTS].integer(a) == segment.segment as i64
            && chunk.columns[ARC_ALLOWED].bytes[a * chunk.counts.profiles + profile] != 0
    });
    if !eligible {
        return Err(RoutingError::Invalid(
            "Endpoint segment is ineligible for this profile",
        ));
    }
    let points = segment_points(chunk, segment.segment);
    if points.len() < 2 {
        return Err(RoutingError::Invalid("Endpoint segment has no geometry"));
    }
    let total = projected_length(&points);
    let position = chainage.unwrap_or(if length > 0.0 {
        total * offset / length
    } else {
        0.0
    });
    if !position.is_finite() || position < 0.0 || position > total + 0.01 {
        return Err(RoutingError::Invalid(
            "Endpoint chainage is outside the projected geometry",
        ));
    }
    if point.is_some_and(|p| !p.0.is_finite() || !p.1.is_finite()) {
        return Err(RoutingError::Invalid("Endpoint point must be finite"));
    }
    let entries = cuts.entry(segment).or_insert_with(|| {
        vec![
            Cut {
                offset: 0.0,
                chainage: 0.0,
                point: points[0],
                node: graph.chunk_nodes[segment.chunk]
                    [chunk.columns[SEGMENT_NODES].integer(segment.segment * 2) as usize],
            },
            Cut {
                offset: length,
                chainage: total,
                point: *points.last().unwrap(),
                node: graph.chunk_nodes[segment.chunk]
                    [chunk.columns[SEGMENT_NODES].integer(segment.segment * 2 + 1) as usize],
            },
        ]
    });
    if let Some(cut) = entries
        .iter()
        .find(|cut| (cut.offset - offset).abs() < 1e-7)
    {
        return Ok(cut.node);
    }
    let node = graph.node_ids.len() + overlay.points.len();
    let resolved_point = point.unwrap_or_else(|| point_at(&points, position.min(total)));
    overlay.points.push(resolved_point);
    entries.push(Cut {
        offset,
        chainage: position.min(total),
        point: resolved_point,
        node,
    });
    Ok(node)
}

fn split_edges(chunks: &[Chunk<'_>], cuts: HashMap<SegmentRef, Vec<Cut>>, overlay: &mut Overlay) {
    let mut ordered: Vec<_> = cuts.into_iter().collect();
    ordered.sort_by_key(|(s, _)| (s.chunk, s.segment));
    for (segment, mut entries) in ordered {
        entries.sort_by(|a, b| a.offset.total_cmp(&b.offset));
        overlay.affected.push(segment);
        let chunk = &chunks[segment.chunk];
        for a in 0..chunk.counts.arcs {
            if chunk.columns[ARC_SEGMENTS].integer(a) != segment.segment as i64 {
                continue;
            }
            let backward = chunk.columns[ARC_REVERSE].bytes[a] != 0;
            for pair in entries.windows(2) {
                let (source, target) = if backward {
                    (pair[1].node, pair[0].node)
                } else {
                    (pair[0].node, pair[1].node)
                };
                let edge = Edge {
                    chunk: segment.chunk,
                    arc: a,
                    segment: segment.segment,
                    source,
                    target,
                    length: pair[1].offset - pair[0].offset,
                    low: pair[0].offset,
                    high: pair[1].offset,
                    backward,
                    geometry_low: Some(pair[0].chainage),
                    geometry_high: Some(pair[1].chainage),
                    geometry_endpoints: Some((pair[0].point, pair[1].point)),
                };
                overlay
                    .outgoing
                    .entry(source)
                    .or_default()
                    .push(overlay.edges.len());
                overlay
                    .incoming
                    .entry(target)
                    .or_default()
                    .push(overlay.edges.len());
                overlay.edges.push(edge);
            }
        }
    }
}

pub fn prepare_overlay(
    graph: &GraphIndex,
    chunks: &[Chunk<'_>],
    start: Endpoint,
    destination: Option<Endpoint>,
    profile: usize,
) -> Result<Overlay, RoutingError> {
    if profile >= graph.profiles {
        return Err(RoutingError::Invalid(
            "Profile index outside the loaded graph",
        ));
    }
    let mut overlay = Overlay {
        start: 0,
        destination: None,
        points: Vec::new(),
        affected: Vec::new(),
        edges: Vec::new(),
        outgoing: HashMap::new(),
        incoming: HashMap::new(),
    };
    let mut cuts = HashMap::new();
    overlay.start = resolve_endpoint(graph, chunks, start, profile, &mut overlay, &mut cuts)?;
    if let Some(endpoint) = destination {
        overlay.destination = Some(resolve_endpoint(
            graph,
            chunks,
            endpoint,
            profile,
            &mut overlay,
            &mut cuts,
        )?);
    }
    split_edges(chunks, cuts, &mut overlay);
    Ok(overlay)
}

pub fn node_id(graph: &GraphIndex, node: usize) -> i64 {
    if node < graph.node_ids.len() {
        graph.node_ids[node]
    } else {
        -(node as i64 - graph.node_ids.len() as i64) - 1
    }
}
