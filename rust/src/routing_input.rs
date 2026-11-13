//! Decode and validate immutable packed hill, environment, and signal inputs.

use crate::chunk_search::check_deadline;
use crate::environment_cost::{self, EnvironmentInterval};
use crate::numeric::RoutingError;
use crate::routing_cost::parse_hill_intervals_until;
use std::collections::{HashMap, HashSet};
use std::sync::Arc;
use std::time::Instant;

type Result<T> = std::result::Result<T, RoutingError>;
pub type HillIntervals = HashMap<i64, Arc<Vec<[f64; 16]>>>;
pub type HillInputs = Vec<Option<HillView>>;

pub struct HillChunk {
    pub ids: Vec<i64>,
    pub lengths: Vec<f64>,
    pub totals: Vec<[f64; 8]>,
    pub general_totals: Vec<[f64; 14]>,
    pub intervals: HashMap<usize, Vec<[f64; 16]>>,
}

pub struct HillView {
    pub owner: Arc<HillChunk>,
    // Empty when packed order already matches topology order.
    pub positions: Vec<usize>,
}

pub struct HillInput {
    pub totals: [f64; 14],
    pub intervals: Vec<[f64; 16]>,
}

pub enum InputRows {
    Environment(Arc<Vec<EnvironmentInterval>>),
    Signals {
        gates: Vec<(f64, bool, u8)>,
        turns: Vec<(i64, i64, bool)>,
    },
}

pub struct InputSegment {
    pub id: i64,
    pub length: f64,
    pub rows: InputRows,
}

pub struct InputChunk {
    pub channel: u32,
    pub segments: Vec<InputSegment>,
    pub resident_bytes: usize,
    pub hills: Option<Arc<HillChunk>>,
}

const ALLOCATION_ALLOWANCE: usize = 64;

const BACKWARD_FEATURES: [usize; 7] = [7, 2, 1, 3, 4, 5, 6];

fn compact_hill_totals(chunk: &mut HillChunk) {
    // Reverse ascent/descent and quality lengths are normally identical. Preserve
    // arbitrary source vectors exactly when that symmetry does not hold.
    if chunk
        .general_totals
        .iter()
        .all(|row| (1..7).all(|i| row[7 + i].to_bits() == row[BACKWARD_FEATURES[i]].to_bits()))
    {
        chunk.totals = chunk
            .general_totals
            .iter()
            .map(|row| std::array::from_fn(|i| row[i]))
            .collect();
        chunk.general_totals = Vec::new();
    }
}

pub fn hill_totals(chunk: &HillChunk, position: usize, backward: bool) -> Option<[f64; 7]> {
    if chunk.general_totals.is_empty() {
        chunk.totals.get(position).map(|row| {
            std::array::from_fn(|i| row[if backward { BACKWARD_FEATURES[i] } else { i }])
        })
    } else {
        chunk
            .general_totals
            .get(position)
            .map(|row| std::array::from_fn(|i| row[usize::from(backward) * 7 + i]))
    }
}

fn vector_bytes<T>(values: &Vec<T>) -> usize {
    values.capacity() * std::mem::size_of::<T>()
        + if values.capacity() == 0 {
            0
        } else {
            ALLOCATION_ALLOWANCE
        }
}

fn input_resident_bytes(segments: &Vec<InputSegment>) -> usize {
    // Arc allowances include the control block and allocator overhead. The input
    // payload is borrowed during decoding and is not retained by the chunk.
    std::mem::size_of::<InputChunk>()
        + ALLOCATION_ALLOWANCE
        + vector_bytes(segments)
        + segments
            .iter()
            .map(|segment| match &segment.rows {
                InputRows::Environment(rows) => {
                    std::mem::size_of::<Vec<EnvironmentInterval>>()
                        + ALLOCATION_ALLOWANCE
                        + vector_bytes(rows)
                }
                InputRows::Signals { gates, turns } => vector_bytes(gates) + vector_bytes(turns),
            })
            .sum::<usize>()
}

struct Reader<'a> {
    bytes: &'a [u8],
    end: Option<Instant>,
}

impl<'a> Reader<'a> {
    fn take(&mut self, count: usize) -> Result<&'a [u8]> {
        check_deadline(self.end)?;
        if count > self.bytes.len() {
            return Err(RoutingError::Invalid("Truncated routing input chunk"));
        }
        let (result, rest) = self.bytes.split_at(count);
        self.bytes = rest;
        Ok(result)
    }

    fn word(&mut self) -> Result<u64> {
        Ok(u64::from_le_bytes(self.take(8)?.try_into().unwrap()))
    }

    fn id(&mut self) -> Result<i64> {
        let id = self.word()? as i64;
        if id < 0 {
            return Err(RoutingError::Invalid("Negative routing input identity"));
        }
        Ok(id)
    }

    fn float(&mut self) -> Result<f64> {
        Ok(f64::from_bits(self.word()?))
    }

    fn count(&mut self) -> Result<usize> {
        usize::try_from(self.word()?)
            .map_err(|_| RoutingError::Invalid("Routing input count overflow"))
    }

    fn rows(&mut self, count: usize, width: usize) -> Result<&'a [u8]> {
        let size = count
            .checked_mul(width)
            .ok_or(RoutingError::Invalid("Routing input size overflow"))?;
        self.take(size)
    }

    fn boolean(&mut self) -> Result<bool> {
        match self.word()? {
            0 => Ok(false),
            1 => Ok(true),
            _ => Err(RoutingError::Invalid("Invalid routing input boolean")),
        }
    }
}

pub fn matching_length(a: f64, b: f64) -> bool {
    (a - b).abs() <= 1e-6_f64.max(1e-9 * a.abs().max(b.abs()))
}

fn hills(reader: &mut Reader<'_>, count: usize, length: f64) -> Result<HillInput> {
    let bytes = reader.rows(1, 112)?;
    let totals =
        std::array::from_fn(|i| f64::from_le_bytes(bytes[i * 8..i * 8 + 8].try_into().unwrap()));
    if totals.iter().any(|v| !v.is_finite() || *v < 0.0) {
        return Err(RoutingError::Invalid("Invalid packed hill totals"));
    }
    let intervals = if count == 0 {
        Vec::new()
    } else {
        parse_hill_intervals_until(reader.rows(count, 128)?, reader.end)?
    };
    for row in &intervals {
        check_deadline(reader.end)?;
        validate_features(&row[2..9])?;
        validate_features(&row[9..16])?;
    }
    validate_features(&totals[..7])?;
    validate_features(&totals[7..])?;
    if intervals
        .last()
        .is_some_and(|row| !matching_length(row[1], length))
    {
        return Err(RoutingError::Invalid("Packed hills do not cover segment"));
    }
    Ok(HillInput { totals, intervals })
}

fn validate_features(values: &[f64]) -> Result<()> {
    if values.iter().any(|v| !v.is_finite() || *v < 0.0)
        || values[3] > values[6] + 1e-6
        || values[4] > values[3] + 1e-6
        || values[5] > values[6] + 1e-6
    {
        return Err(RoutingError::Invalid("Invalid packed hill feature vector"));
    }
    Ok(())
}

fn environment(reader: &mut Reader<'_>, count: usize, length: f64) -> Result<InputRows> {
    let bytes = reader.rows(count, 64)?;
    let mut rows = Vec::with_capacity(count);
    for bytes in bytes.chunks_exact(64) {
        check_deadline(reader.end)?;
        let row: [f64; 8] = std::array::from_fn(|i| {
            f64::from_le_bytes(bytes[i * 8..i * 8 + 8].try_into().unwrap())
        });
        rows.push(EnvironmentInterval {
            start_m: row[0],
            end_m: row[1],
            values: [row[2], row[3], row[4], row[5]],
            context_support: row[6],
            traffic_context_support: row[7],
        });
    }
    let rows = Arc::new(rows);
    environment_cost::validate_intervals(&HashMap::from([(0, Arc::clone(&rows))]))?;
    if !matching_length(rows.last().unwrap().end_m, length) {
        return Err(RoutingError::Invalid(
            "Packed environment does not cover segment",
        ));
    }
    Ok(InputRows::Environment(rows))
}

fn signals(
    reader: &mut Reader<'_>,
    gates: usize,
    turns: usize,
    length: f64,
    outgoing: i64,
) -> Result<InputRows> {
    // Bound allocations before trusting externally supplied counts.
    let size = gates
        .checked_add(turns)
        .and_then(|n| n.checked_mul(24))
        .ok_or(RoutingError::Invalid("Routing input size overflow"))?;
    if size > reader.bytes.len() {
        return Err(RoutingError::Invalid("Truncated signal input"));
    }
    let mut gate_rows = Vec::with_capacity(gates);
    let mut offsets = HashSet::new();
    for _ in 0..gates {
        let offset = reader.float()?;
        let unresolved = reader.boolean()?;
        let directions = reader.word()?;
        if !offset.is_finite()
            || offset <= 0.0
            || offset >= length
            || !(1..=3).contains(&directions)
            || !offsets.insert(offset.to_bits())
        {
            return Err(RoutingError::Invalid(
                "Invalid or duplicate packed signal gate",
            ));
        }
        gate_rows.push((offset, unresolved, directions as u8));
    }
    let mut turn_rows = Vec::with_capacity(turns);
    let mut movements = HashSet::new();
    for _ in 0..turns {
        let node = reader.id()?;
        let incoming = reader.id()?;
        let unresolved = reader.boolean()?;
        if incoming == outgoing || !movements.insert((node, incoming)) {
            return Err(RoutingError::Invalid(
                "Invalid or duplicate packed signal turn",
            ));
        }
        turn_rows.push((node, incoming, unresolved));
    }
    Ok(InputRows::Signals {
        gates: gate_rows,
        turns: turn_rows,
    })
}

pub fn parse(payload: &[u8], end: Option<Instant>) -> Result<InputChunk> {
    let mut reader = Reader {
        bytes: payload,
        end,
    };
    if reader.take(8)? != b"RUNINP01" || reader.take(4)? != 1_u32.to_le_bytes() {
        return Err(RoutingError::Invalid("Invalid routing input header"));
    }
    let channel = u32::from_le_bytes(reader.take(4)?.try_into().unwrap());
    if !(1..=3).contains(&channel) {
        return Err(RoutingError::Invalid("Unknown routing input channel"));
    }
    let count = reader.count()?;
    if count == 0 || count > reader.bytes.len() / 32 {
        return Err(RoutingError::Invalid("Invalid routing input segment count"));
    }
    let mut segments = Vec::with_capacity(if channel == 1 { 0 } else { count });
    let mut hill_chunk = HillChunk {
        ids: Vec::new(),
        lengths: Vec::new(),
        totals: Vec::new(),
        general_totals: Vec::new(),
        intervals: HashMap::new(),
    };
    if channel == 1 {
        hill_chunk.ids.reserve_exact(count);
        hill_chunk.lengths.reserve_exact(count);
        hill_chunk.general_totals.reserve_exact(count);
    }
    let mut previous = None;
    for _ in 0..count {
        let id = reader.id()?;
        let length = reader.float()?;
        let count1 = reader.count()?;
        let count2 = reader.count()?;
        if previous.is_some_and(|p| id <= p)
            || !length.is_finite()
            || length <= 0.0
            || (channel != 3 && count2 != 0)
        {
            return Err(RoutingError::Invalid(
                "Invalid routing input segment record",
            ));
        }
        previous = Some(id);
        if channel == 1 {
            let rows = hills(&mut reader, count1, length)?;
            let position = hill_chunk.ids.len();
            hill_chunk.ids.push(id);
            hill_chunk.lengths.push(length);
            hill_chunk.general_totals.push(rows.totals);
            if !rows.intervals.is_empty() {
                hill_chunk.intervals.insert(position, rows.intervals);
            }
            continue;
        }
        let rows = match channel {
            2 => environment(&mut reader, count1, length)?,
            _ => signals(&mut reader, count1, count2, length, id)?,
        };
        segments.push(InputSegment { id, length, rows });
    }
    if !reader.bytes.is_empty() {
        return Err(RoutingError::Invalid("Trailing routing input bytes"));
    }
    check_deadline(end)?;
    if channel == 1 {
        compact_hill_totals(&mut hill_chunk);
    }
    let resident_bytes = input_resident_bytes(&segments)
        + if channel == 1 {
            std::mem::size_of::<HillChunk>()
                + ALLOCATION_ALLOWANCE
                + vector_bytes(&hill_chunk.ids)
                + vector_bytes(&hill_chunk.lengths)
                + vector_bytes(&hill_chunk.totals)
                + vector_bytes(&hill_chunk.general_totals)
                + hill_chunk.intervals.capacity() * 64
                + hill_chunk
                    .intervals
                    .values()
                    .map(vector_bytes)
                    .sum::<usize>()
        } else {
            0
        };
    check_deadline(end)?;
    Ok(InputChunk {
        channel,
        segments,
        resident_bytes,
        hills: (channel == 1).then(|| Arc::new(hill_chunk)),
    })
}

pub struct AttachedInputs {
    pub environment: Option<Arc<environment_cost::EnvironmentIntervals>>,
    pub signals: Option<Arc<crate::signal_cost::SignalData>>,
}

/// Join complete channels against a graph selection without copying interval vectors.
pub fn attach(
    owners: &[Arc<InputChunk>],
    lengths: &HashMap<i64, f64>,
    end: Option<Instant>,
) -> Result<AttachedInputs> {
    let mut environment = HashMap::new();
    let mut signals = crate::signal_cost::SignalData::default();
    let mut seen: [HashSet<i64>; 3] = std::array::from_fn(|_| HashSet::new());
    let mut channels = [false; 3];
    for owner in owners {
        check_deadline(end)?;
        let channel = owner.channel as usize - 1;
        if channel == 0 {
            continue;
        }
        channels[channel] = true;
        for segment in &owner.segments {
            check_deadline(end)?;
            if !seen[channel].insert(segment.id) {
                return Err(RoutingError::Invalid(
                    "Duplicate routing input segment across chunks",
                ));
            }
            let Some(&length) = lengths.get(&segment.id) else {
                continue;
            };
            if !matching_length(segment.length, length) {
                return Err(RoutingError::Invalid(
                    "Routing input chainage does not match graph",
                ));
            }
            match &segment.rows {
                InputRows::Environment(rows) => {
                    environment.insert(segment.id, Arc::clone(rows));
                }
                InputRows::Signals { gates, turns } => {
                    if !gates.is_empty() {
                        signals.gates.insert(segment.id, gates.clone());
                    }
                    for &(node, incoming, unresolved) in turns {
                        check_deadline(end)?;
                        signals
                            .turns
                            .insert((node, incoming, segment.id), unresolved);
                    }
                }
            }
        }
    }
    for (channel, present) in channels.into_iter().enumerate() {
        if present && lengths.keys().any(|id| !seen[channel].contains(id)) {
            return Err(RoutingError::Invalid(
                "Routing inputs lack a selected segment",
            ));
        }
    }
    check_deadline(end)?;
    Ok(AttachedInputs {
        environment: channels[1].then(|| Arc::new(environment)),
        signals: channels[2].then(|| Arc::new(signals)),
    })
}

pub fn attach_hills(
    owners: &[Arc<InputChunk>],
    chunks: &[crate::numeric::Chunk<'_>],
    lengths: &[Vec<Option<f64>>],
    selected: &[Vec<bool>],
    end: Option<Instant>,
) -> Result<HillInputs> {
    use crate::numeric::SEGMENT_IDS;
    let hills: Vec<_> = owners
        .iter()
        .filter_map(|owner| owner.hills.as_ref())
        .collect();
    if hills.is_empty() {
        return Ok(Vec::new());
    }
    if hills.len() != chunks.len() {
        return Err(RoutingError::Invalid(
            "Hill inputs lack a selected segment or contain duplicate chunks",
        ));
    }
    let mut by_first = HashMap::with_capacity(hills.len());
    for (index, owner) in hills.iter().enumerate() {
        if by_first.insert(owner.ids[0], index).is_some() {
            return Err(RoutingError::Invalid("Duplicate hill input chunk"));
        }
    }
    let mut result = Vec::with_capacity(chunks.len());
    for (index, topology) in chunks.iter().enumerate() {
        check_deadline(end)?;
        let first = (0..topology.counts.segments)
            .map(|segment| topology.columns[SEGMENT_IDS].integer(segment))
            .min()
            .ok_or(RoutingError::Invalid(
                "Hill inputs require nonempty topology chunks",
            ))?;
        let owner = by_first
            .remove(&first)
            .map(|owner| hills[owner])
            .ok_or(RoutingError::Invalid("Hill inputs lack a selected segment"))?;
        if owner.ids.len() != topology.counts.segments {
            return Err(RoutingError::Invalid(
                "Hill inputs do not follow topology chunks",
            ));
        }
        let mut positions = Vec::with_capacity(topology.counts.segments);
        for segment in 0..topology.counts.segments {
            check_deadline(end)?;
            let id = topology.columns[SEGMENT_IDS].integer(segment);
            let position = if owner.ids[segment] == id {
                segment
            } else {
                owner
                    .ids
                    .binary_search(&id)
                    .map_err(|_| RoutingError::Invalid("Hill inputs lack a selected segment"))?
            };
            if selected[index][segment]
                && lengths[index][segment]
                    .is_some_and(|length| !matching_length(length, owner.lengths[position]))
            {
                return Err(RoutingError::Invalid(
                    "Routing input chainage does not match graph",
                ));
            }
            positions.push(position);
        }
        if positions
            .iter()
            .enumerate()
            .all(|(i, position)| i == *position)
        {
            positions.clear();
            positions.shrink_to_fit();
        }
        result.push(Some(HillView {
            owner: Arc::clone(owner),
            positions,
        }));
    }
    Ok(result)
}

#[cfg(test)]
mod tests {
    use super::*;

    fn payload(channel: u32, ids: &[i64]) -> Vec<u8> {
        let mut bytes = b"RUNINP01".to_vec();
        bytes.extend(1_u32.to_le_bytes());
        bytes.extend(channel.to_le_bytes());
        bytes.extend((ids.len() as u64).to_le_bytes());
        for &id in ids {
            bytes.extend(id.to_le_bytes());
            bytes.extend(20_f64.to_le_bytes());
            bytes.extend(u64::from(channel == 2).to_le_bytes());
            bytes.extend(0_u64.to_le_bytes());
            match channel {
                1 => {
                    for value in [2.0_f64, 1.0, 0.0, 25.0, 20.0, 5.0, 30.0].repeat(2) {
                        bytes.extend(value.to_le_bytes());
                    }
                }
                2 => {
                    for value in [0.0_f64, 20.0, 0.5, f64::NAN, 0.25, 0.0, 1.0, 0.0] {
                        bytes.extend(value.to_le_bytes());
                    }
                }
                _ => {}
            }
        }
        bytes
    }

    fn replace_word(bytes: &mut [u8], offset: usize, word: u64) {
        bytes[offset..offset + 8].copy_from_slice(&word.to_le_bytes());
    }

    #[test]
    fn hills_use_contiguous_storage_without_segment_allocations() {
        let chunk = parse(&payload(1, &[7, 8]), None).unwrap();
        assert!(chunk.segments.is_empty());
        let hills = chunk.hills.as_ref().unwrap();
        assert_eq!(hills.general_totals.len(), 2); // This fixture has asymmetric ascent/descent.
        assert!(hills.intervals.is_empty());
        let large = parse(&payload(1, &(0..1000).collect::<Vec<_>>()), None).unwrap();
        assert!(large.resident_bytes < 129 * 1000);
    }

    #[test]
    fn symmetric_totals_are_compact_and_asymmetric_totals_remain_exact() {
        let mut bytes = payload(1, &[7]);
        replace_word(&mut bytes, 120, 0_f64.to_bits());
        replace_word(&mut bytes, 128, 1_f64.to_bits());
        let chunk = parse(&bytes, None).unwrap();
        let hills = chunk.hills.as_ref().unwrap();
        assert_eq!(hills.totals.len(), 1);
        assert!(hills.general_totals.is_empty());
        assert_eq!(
            hill_totals(hills, 0, false).unwrap(),
            [2.0, 1.0, 0.0, 25.0, 20.0, 5.0, 30.0]
        );
        assert_eq!(
            hill_totals(hills, 0, true).unwrap(),
            [2.0, 0.0, 1.0, 25.0, 20.0, 5.0, 30.0]
        );
        replace_word(&mut bytes, 136, 24_f64.to_bits());
        let chunk = parse(&bytes, None).unwrap();
        let hills = chunk.hills.as_ref().unwrap();
        assert!(hills.totals.is_empty());
        assert_eq!(hills.general_totals.len(), 1);
        assert_eq!(hill_totals(hills, 0, true).unwrap()[3], 24.0);
    }

    #[test]
    fn hill_views_share_chunks_and_map_topology_order() {
        use crate::numeric::{Chunk, Column, Counts, SEGMENT_IDS};
        let owner = Arc::new(parse(&payload(1, &[7, 8]), None).unwrap());
        let ids: Vec<u8> = [8_i64, 7].into_iter().flat_map(i64::to_le_bytes).collect();
        let mut columns = [Column { bytes: &[] }; 23];
        columns[SEGMENT_IDS] = Column { bytes: &ids };
        let chunks = [Chunk {
            counts: Counts {
                nodes: 0,
                segments: 2,
                arcs: 0,
                profiles: 1,
                points: 0,
                intervals: 0,
            },
            columns,
        }];
        let lengths = vec![vec![Some(20.0), Some(20.0)]];
        let selected = vec![vec![true, true]];
        let views =
            attach_hills(&[Arc::clone(&owner)], &chunks, &lengths, &selected, None).unwrap();
        let view = views[0].as_ref().unwrap();
        assert!(Arc::ptr_eq(&view.owner, owner.hills.as_ref().unwrap()));
        assert_eq!(view.positions, [1, 0]);
        assert!(
            attach_hills(&[owner], &chunks, &lengths, &selected, Some(Instant::now())).is_err()
        );
    }

    #[test]
    fn resident_bytes_scale_with_interval_capacity() {
        let totals_only = parse(&payload(1, &[7]), None).unwrap();
        let mut hills = payload(1, &[7]);
        replace_word(&mut hills, 40, 1);
        hills.extend(0_f64.to_le_bytes());
        hills.extend(20_f64.to_le_bytes());
        hills.extend_from_within(56..168);
        let detailed = parse(&hills, None).unwrap();
        assert!(detailed.resident_bytes > totals_only.resident_bytes + 128);

        let mut env = payload(2, &[7]);
        let single = parse(&env, None).unwrap();
        replace_word(&mut env, 40, 2);
        replace_word(&mut env, 64, 10_f64.to_bits());
        env.extend_from_within(56..120);
        replace_word(&mut env, 120, 10_f64.to_bits());
        replace_word(&mut env, 128, 20_f64.to_bits());
        let double = parse(&env, None).unwrap();
        assert_eq!(
            double.resident_bytes - single.resident_bytes,
            std::mem::size_of::<EnvironmentInterval>()
        );

        let mut segments = vec![InputSegment {
            id: 7,
            length: 20.0,
            rows: InputRows::Environment(Arc::new(Vec::new())),
        }];
        let before = input_resident_bytes(&segments);
        let InputRows::Environment(rows) = &mut segments[0].rows else {
            panic!()
        };
        // Grow an empty owned vector to check spare capacity, not just row count.
        let rows = Arc::get_mut(rows).unwrap();
        rows.reserve_exact(8);
        let extra_capacity = rows.capacity();
        assert_eq!(
            input_resident_bytes(&segments) - before,
            extra_capacity * std::mem::size_of::<EnvironmentInterval>() + ALLOCATION_ALLOWANCE
        );
    }

    #[test]
    fn rejects_truncation_trailing_bytes_and_malformed_metadata() {
        for channel in 1..=3 {
            let valid = payload(channel, &[7]);
            assert!(parse(&valid, None).is_ok());
            for length in 0..valid.len() {
                assert!(parse(&valid[..length], None).is_err());
            }
            let mut trailing = valid.clone();
            trailing.push(0);
            assert!(parse(&trailing, None).is_err());
            for (offset, word) in [
                (16, 0),
                (16, u64::MAX),
                (24, u64::MAX),
                (32, f64::NAN.to_bits()),
                (32, 0),
                (32, (-1_f64).to_bits()),
                (40, u64::MAX),
                (48, u64::MAX),
            ] {
                let mut invalid = valid.clone();
                replace_word(&mut invalid, offset, word);
                assert!(
                    parse(&invalid, None).is_err(),
                    "channel {channel} offset {offset}"
                );
            }
            for offset in [0, 8, 12] {
                let mut invalid = valid.clone();
                invalid[offset] = 255;
                assert!(parse(&invalid, None).is_err());
            }
            for ids in [&[7, 7][..], &[8, 7][..], &[-1][..]] {
                assert!(parse(&payload(channel, ids), None).is_err());
            }
        }
    }

    #[test]
    fn validates_environment_support_and_chainage() {
        let valid = payload(2, &[7]);
        for (offset, value) in [
            (56, 1.0),
            (64, 19.0),
            (64, 0.0),
            (72, 1.5),
            (104, f64::INFINITY),
            (112, -1.0),
        ] {
            let mut invalid = valid.clone();
            replace_word(&mut invalid, offset, value.to_bits());
            assert!(parse(&invalid, None).is_err());
        }
        let chunk = parse(&valid, None).unwrap();
        let InputRows::Environment(rows) = &chunk.segments[0].rows else {
            panic!()
        };
        let (penalty, unknown) = environment_cost::integrate(rows, 5.0, 15.0, [1.0; 4]).unwrap();
        assert_eq!(penalty, [5.0, 10.0, 10.0, 0.0]);
        assert_eq!(unknown, [0.0, 10.0, 10.0, 0.0]);
    }

    fn signal_payload() -> Vec<u8> {
        let mut bytes = payload(3, &[7]);
        replace_word(&mut bytes, 40, 1);
        replace_word(&mut bytes, 48, 1);
        bytes.extend(10_f64.to_le_bytes());
        bytes.extend(1_u64.to_le_bytes());
        bytes.extend(3_u64.to_le_bytes());
        bytes.extend(9_i64.to_le_bytes());
        bytes.extend(99_i64.to_le_bytes());
        bytes.extend(0_u64.to_le_bytes());
        bytes
    }

    #[test]
    fn validates_signal_flags_identities_and_duplicate_boundaries() {
        let valid = signal_payload();
        assert!(parse(&valid, None).is_ok());
        for (offset, word) in [
            (56, 0),
            (56, 20_f64.to_bits()),
            (56, f64::NAN.to_bits()),
            (64, 2),
            (72, 0),
            (72, 4),
            (80, u64::MAX),
            (88, u64::MAX),
            (88, 7),
            (96, 2),
        ] {
            let mut invalid = valid.clone();
            replace_word(&mut invalid, offset, word);
            assert!(parse(&invalid, None).is_err(), "offset {offset}");
        }
        for gate in [true, false] {
            let mut invalid = valid.clone();
            if gate {
                replace_word(&mut invalid, 40, 2);
                invalid.splice(80..80, valid[56..80].iter().copied());
            } else {
                replace_word(&mut invalid, 48, 2);
                invalid.extend_from_slice(&valid[80..104]);
            }
            assert!(parse(&invalid, None).is_err());
        }
    }

    #[test]
    fn validates_hill_vectors_and_intervals_without_equating_sampled_length() {
        let valid = payload(1, &[7]);
        assert!(parse(&valid, None).is_ok()); // Sampled length 30 exceeds chainage 20.
        for value in [-1_f64, f64::NAN, f64::INFINITY] {
            let mut invalid = valid.clone();
            replace_word(&mut invalid, 56, value.to_bits());
            assert!(parse(&invalid, None).is_err());
        }
        let mut detailed = valid.clone();
        replace_word(&mut detailed, 40, 1);
        detailed.extend(0_f64.to_le_bytes());
        detailed.extend(20_f64.to_le_bytes());
        detailed.extend_from_slice(&valid[56..168]);
        assert!(parse(&detailed, None).is_ok());
        for (offset, value) in [(168, 1_f64), (176, 19_f64), (184, -1_f64)] {
            let mut invalid = detailed.clone();
            replace_word(&mut invalid, offset, value.to_bits());
            assert!(parse(&invalid, None).is_err());
        }
    }

    #[test]
    fn attachment_filters_shares_validates_coverage_and_keeps_cross_window_turns() {
        let env = Arc::new(parse(&payload(2, &[7, 8]), None).unwrap());
        let signal = Arc::new(parse(&signal_payload(), None).unwrap());
        let hill = Arc::new(parse(&payload(1, &[7, 8]), None).unwrap());
        let lengths = HashMap::from([(7, 20.0)]);
        let attached = attach(&[Arc::clone(&env), signal, hill], &lengths, None).unwrap();
        let rows = &attached.environment.as_ref().unwrap()[&7];
        let InputRows::Environment(original) = &env.segments[0].rows else {
            panic!()
        };
        assert!(Arc::ptr_eq(rows, original));
        assert!(
            attached
                .signals
                .as_ref()
                .unwrap()
                .turns
                .contains_key(&(9, 99, 7))
        );
        drop(env);
        assert_eq!(rows.len(), 1);
        let hills = Arc::new(parse(&payload(1, &[7]), None).unwrap());
        assert!(
            attach(&[Arc::clone(&hills)], &lengths, None)
                .unwrap()
                .environment
                .is_none()
        );
        let expired = Some(Instant::now());
        assert!(matches!(
            parse(&payload(1, &[7]), expired),
            Err(RoutingError::TimedOut)
        ));
        assert!(matches!(
            attach(&[hills], &lengths, expired),
            Err(RoutingError::TimedOut)
        ));
    }
}
