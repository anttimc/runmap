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
pub type HillInputs = HashMap<i64, Arc<HillInput>>;

pub struct HillInput {
    pub totals: [f64; 14],
    pub intervals: Vec<[f64; 16]>,
}

pub enum InputRows {
    Hills(Arc<HillInput>),
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
}

const ALLOCATION_ALLOWANCE: usize = 64;

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
                InputRows::Hills(rows) => {
                    std::mem::size_of::<HillInput>()
                        + ALLOCATION_ALLOWANCE
                        + vector_bytes(&rows.intervals)
                }
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

fn hills(reader: &mut Reader<'_>, count: usize, length: f64) -> Result<InputRows> {
    let mut totals = [0.0; 14];
    for value in &mut totals {
        *value = reader.float()?;
    }
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
    Ok(InputRows::Hills(Arc::new(HillInput { totals, intervals })))
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
    let mut segments = Vec::with_capacity(count);
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
        let rows = match channel {
            1 => hills(&mut reader, count1, length)?,
            2 => environment(&mut reader, count1, length)?,
            _ => signals(&mut reader, count1, count2, length, id)?,
        };
        segments.push(InputSegment { id, length, rows });
    }
    if !reader.bytes.is_empty() {
        return Err(RoutingError::Invalid("Trailing routing input bytes"));
    }
    check_deadline(end)?;
    let resident_bytes = input_resident_bytes(&segments);
    check_deadline(end)?;
    Ok(InputChunk {
        channel,
        segments,
        resident_bytes,
    })
}

pub struct AttachedInputs {
    pub hills: Arc<HillInputs>,
    pub environment: Option<Arc<environment_cost::EnvironmentIntervals>>,
    pub signals: Option<Arc<crate::signal_cost::SignalData>>,
}

/// Join complete channels against a graph selection without copying interval vectors.
pub fn attach(
    owners: &[Arc<InputChunk>],
    lengths: &HashMap<i64, f64>,
    end: Option<Instant>,
) -> Result<AttachedInputs> {
    let mut hills = HashMap::new();
    let mut environment = HashMap::new();
    let mut signals = crate::signal_cost::SignalData::default();
    let mut seen: [HashSet<i64>; 3] = std::array::from_fn(|_| HashSet::new());
    let mut channels = [false; 3];
    for owner in owners {
        check_deadline(end)?;
        let channel = owner.channel as usize - 1;
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
                InputRows::Hills(rows) => {
                    hills.insert(segment.id, Arc::clone(rows));
                }
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
        hills: Arc::new(hills),
        environment: channels[1].then(|| Arc::new(environment)),
        signals: channels[2].then(|| Arc::new(signals)),
    })
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
    fn resident_bytes_cover_owned_storage_without_charging_payload() {
        for channel in 1..=3 {
            let bytes = if channel == 3 {
                signal_payload()
            } else {
                payload(channel, &[7])
            };
            let chunk = parse(&bytes, None).unwrap();
            let mut logical = std::mem::size_of::<InputChunk>()
                + std::mem::size_of_val(chunk.segments.as_slice());
            let mut allocations = 2; // Outer Arc and segment vector.
            for segment in &chunk.segments {
                match &segment.rows {
                    InputRows::Hills(rows) => {
                        logical += std::mem::size_of::<HillInput>()
                            + std::mem::size_of_val(rows.intervals.as_slice());
                        allocations += 1;
                    }
                    InputRows::Environment(rows) => {
                        logical += std::mem::size_of::<Vec<EnvironmentInterval>>()
                            + std::mem::size_of_val(rows.as_slice());
                        allocations += 2;
                    }
                    InputRows::Signals { gates, turns } => {
                        logical += std::mem::size_of_val(gates.as_slice())
                            + std::mem::size_of_val(turns.as_slice());
                        allocations += 2;
                    }
                }
            }
            assert!(chunk.resident_bytes >= logical);
            assert_eq!(
                chunk.resident_bytes,
                logical + allocations * ALLOCATION_ALLOWANCE
            );
        }
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
        assert_eq!(
            detailed.resident_bytes - totals_only.resident_bytes,
            std::mem::size_of::<[f64; 16]>() + ALLOCATION_ALLOWANCE
        );

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
        assert_eq!(attached.hills.len(), 1);
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
        assert!(attach(&[Arc::clone(&hills), Arc::clone(&hills)], &lengths, None).is_err());
        assert!(attach(&[Arc::clone(&hills)], &HashMap::from([(8, 20.0)]), None).is_err());
        assert!(attach(&[Arc::clone(&hills)], &HashMap::from([(7, 21.0)]), None).is_err());
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
