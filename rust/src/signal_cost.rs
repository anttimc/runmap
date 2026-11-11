//! Canonical crossing boundaries and arrival-dependent pedestrian movements.

use crate::endpoint_overlay::Edge;
use crate::numeric::{Chunk, SEGMENT_IDS};
use std::collections::HashMap;
use std::sync::Arc;

pub type GateRows = Vec<(i64, f64, bool, u8)>;
pub type TurnRows = Vec<(i64, i64, i64, bool)>;

#[derive(Default)]
pub struct SignalData {
    pub gates: HashMap<i64, Vec<(f64, bool, u8)>>,
    pub turns: HashMap<(i64, i64, i64), bool>,
}

#[derive(Clone, Copy)]
pub struct Arrival {
    pub segment: i64,
    pub start: f64,
    pub end: f64,
}

pub struct SignalSettings {
    pub data: Arc<SignalData>,
    pub weight: f64,
    pub arrival: Option<Arrival>,
}

pub fn arrival(chunks: &[Chunk<'_>], edge: Edge) -> Arrival {
    Arrival {
        segment: chunks[edge.chunk].columns[SEGMENT_IDS].integer(edge.segment),
        start: if edge.backward { edge.high } else { edge.low },
        end: if edge.backward { edge.low } else { edge.high },
    }
}

pub fn counts(
    data: &SignalData,
    previous: Option<Arrival>,
    current: Arrival,
    node: i64,
) -> [i64; 2] {
    let mut result = [0; 2];
    if current.start == current.end {
        return result;
    }
    let forward = current.end > current.start;
    if let Some(gates) = data.gates.get(&current.segment) {
        for &(offset, unresolved, directions) in gates {
            if directions & if forward { 1 } else { 2 } == 0 {
                continue;
            }
            let crossed =
                current.start.min(current.end) < offset && offset < current.start.max(current.end);
            let continued = current.start == offset
                && previous.is_some_and(|prior| {
                    prior.segment == current.segment
                        && prior.end == offset
                        && (prior.end > prior.start) == forward
                });
            if crossed || continued {
                result[usize::from(unresolved)] += 1;
            }
        }
    }
    if let Some(prior) = previous {
        if prior.segment != current.segment {
            if let Some(&unresolved) = data.turns.get(&(node, prior.segment, current.segment)) {
                result[usize::from(unresolved)] += 1;
            }
        }
    }
    result
}
