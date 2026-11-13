//! Checked views over legacy numeric chunks and version-3 topology-only chunks.

#[derive(Debug, PartialEq)]
pub enum RoutingError {
    Invalid(&'static str),
    TimedOut,
}

#[derive(Clone, Copy)]
pub struct Counts {
    pub nodes: usize,
    pub segments: usize,
    pub arcs: usize,
    pub profiles: usize,
    pub points: usize,
    pub intervals: usize,
}

#[derive(Clone, Copy)]
pub struct Column<'a> {
    pub bytes: &'a [u8],
}

impl Column<'_> {
    pub fn integer(self, index: usize) -> i64 {
        i64::from_le_bytes(self.bytes[index * 8..index * 8 + 8].try_into().unwrap())
    }

    pub fn float(self, index: usize) -> f64 {
        f64::from_le_bytes(self.bytes[index * 8..index * 8 + 8].try_into().unwrap())
    }
}

pub struct Chunk<'a> {
    pub counts: Counts,
    pub columns: [Column<'a>; 23],
}

pub const NODE_IDS: usize = 0;
pub const NODE_XY: usize = 1;
pub const NODE_DEGREE: usize = 2;
pub const NODE_ALLOWED: usize = 3;
pub const SEGMENT_IDS: usize = 4;
pub const SEGMENT_NODES: usize = 5;
pub const SEGMENT_BOUNDS: usize = 6;
pub const GEOMETRY_OFFSETS: usize = 7;
pub const GEOMETRY_XY: usize = 8;
pub const ARC_IDS: usize = 9;
pub const ARC_NODES: usize = 10;
pub const ARC_SEGMENTS: usize = 11;
pub const ARC_LENGTHS: usize = 12;
pub const ARC_REVERSE: usize = 13;
pub const ARC_ALLOWED: usize = 14;
pub const OUTGOING_OFFSETS: usize = 15;
pub const OUTGOING_ARCS: usize = 16;
pub const INCOMING_OFFSETS: usize = 17;
pub const INCOMING_ARCS: usize = 18;
pub const HILL_OFFSETS: usize = 19;
pub const HILL_CHAINAGE: usize = 20;
pub const HILL_FEATURES: usize = 21;
pub const HILL_TOTALS: usize = 22;

type Result<T> = std::result::Result<T, RoutingError>;

fn checked_product(values: &[usize]) -> Result<usize> {
    values.iter().try_fold(1usize, |size, value| {
        size.checked_mul(*value)
            .ok_or(RoutingError::Invalid("Numeric chunk size overflow"))
    })
}

fn lengths(c: Counts) -> Result<[usize; 23]> {
    let plus_one = |value: usize| {
        value
            .checked_add(1)
            .ok_or(RoutingError::Invalid("Numeric chunk size overflow"))
    };
    Ok([
        c.nodes,
        checked_product(&[c.nodes, 2])?,
        c.nodes,
        checked_product(&[c.nodes, c.profiles])?,
        c.segments,
        checked_product(&[c.segments, 2])?,
        checked_product(&[c.segments, 4])?,
        plus_one(c.segments)?,
        checked_product(&[c.points, 2])?,
        c.arcs,
        checked_product(&[c.arcs, 2])?,
        c.arcs,
        c.arcs,
        c.arcs,
        checked_product(&[c.arcs, c.profiles])?,
        plus_one(c.nodes)?,
        c.arcs,
        plus_one(c.nodes)?,
        c.arcs,
        plus_one(c.segments)?,
        checked_product(&[c.intervals, 2])?,
        checked_product(&[c.intervals, 2, 7])?,
        checked_product(&[c.segments, 2, 7])?,
    ])
}

pub fn parse_chunk(payload: &[u8]) -> Result<Chunk<'_>> {
    if payload.len() < 64 {
        return Err(RoutingError::Invalid("Truncated numeric chunk header"));
    }
    let topology_only = &payload[..8] == b"RUNNUM03" && payload[8..12] == 3u32.to_le_bytes();
    if !topology_only && (&payload[..8] != b"RUNNUM02" || payload[8..12] != 2u32.to_le_bytes()) {
        return Err(RoutingError::Invalid(
            "Unsupported or truncated numeric chunk header",
        ));
    }
    let mut counts = [0usize; 6];
    for (i, count) in counts.iter_mut().enumerate() {
        let value = u64::from_le_bytes(payload[16 + 8 * i..24 + 8 * i].try_into().unwrap());
        *count = usize::try_from(value)
            .map_err(|_| RoutingError::Invalid("Numeric chunk size overflow"))?;
    }
    let c = Counts {
        nodes: counts[0],
        segments: counts[1],
        arcs: counts[2],
        profiles: counts[3],
        points: counts[4],
        intervals: counts[5],
    };
    if topology_only && c.intervals != 0 {
        return Err(RoutingError::Invalid(
            "Topology-only chunks cannot contain hill intervals",
        ));
    }
    let mut columns = [Column { bytes: &[] }; 23];
    let mut position = 64usize;
    for (i, length) in lengths(c)?.into_iter().enumerate() {
        if topology_only && i >= HILL_OFFSETS {
            break;
        }
        position = position
            .checked_add(63)
            .ok_or(RoutingError::Invalid("Numeric chunk size overflow"))?
            / 64
            * 64;
        let size = checked_product(&[
            length,
            if i == ARC_REVERSE || i == ARC_ALLOWED || i == NODE_ALLOWED {
                1
            } else {
                8
            },
        ])?;
        let end = position
            .checked_add(size)
            .ok_or(RoutingError::Invalid("Numeric chunk size overflow"))?;
        columns[i] = Column {
            bytes: payload
                .get(position..end)
                .ok_or(RoutingError::Invalid("Truncated numeric chunk columns"))?,
        };
        position = end;
    }
    if position != payload.len() {
        return Err(RoutingError::Invalid("Numeric chunk length mismatch"));
    }
    Ok(Chunk { counts: c, columns })
}

fn index(value: i64, upper: usize) -> Result<usize> {
    usize::try_from(value)
        .ok()
        .filter(|n| *n < upper)
        .ok_or(RoutingError::Invalid(
            "Numeric chunk reference outside its column",
        ))
}

fn validate_offsets(column: Column<'_>, count: usize, end: usize) -> Result<()> {
    let mut previous = 0;
    for i in 0..=count {
        let value = column.integer(i);
        if value < previous
            || usize::try_from(value).map_or(true, |n| n > end)
            || (i == 0 && value != 0)
        {
            return Err(RoutingError::Invalid("Invalid numeric chunk offsets"));
        }
        previous = value;
    }
    if usize::try_from(previous).ok() != Some(end) {
        return Err(RoutingError::Invalid("Invalid numeric chunk offset end"));
    }
    Ok(())
}

fn validate_adjacency(
    chunk: &Chunk<'_>,
    offsets: usize,
    entries: usize,
    direction: usize,
) -> Result<()> {
    let c = chunk.counts;
    validate_offsets(chunk.columns[offsets], c.nodes, c.arcs)?;
    let mut seen = vec![false; c.arcs];
    for node in 0..c.nodes {
        let start = chunk.columns[offsets].integer(node) as usize;
        let end = chunk.columns[offsets].integer(node + 1) as usize;
        for i in start..end {
            let arc = index(chunk.columns[entries].integer(i), c.arcs)?;
            if seen[arc] || chunk.columns[ARC_NODES].integer(arc * 2 + direction) != node as i64 {
                return Err(RoutingError::Invalid(
                    "Adjacency does not match arc endpoints",
                ));
            }
            seen[arc] = true;
        }
    }
    Ok(())
}

pub fn validate_chunk(chunk: &Chunk<'_>) -> Result<()> {
    let c = chunk.counts;
    if c.profiles == 0 {
        return Err(RoutingError::Invalid(
            "Numeric chunks require routing profiles",
        ));
    }
    for column in [
        NODE_XY,
        SEGMENT_BOUNDS,
        GEOMETRY_XY,
        ARC_LENGTHS,
        HILL_CHAINAGE,
        HILL_FEATURES,
        HILL_TOTALS,
    ] {
        for i in 0..chunk.columns[column].bytes.len() / 8 {
            if !chunk.columns[column].float(i).is_finite() {
                return Err(RoutingError::Invalid("Numeric chunk values must be finite"));
            }
        }
    }
    for i in 0..c.segments * 2 {
        index(chunk.columns[SEGMENT_NODES].integer(i), c.nodes)?;
    }
    for i in 0..c.arcs * 2 {
        index(chunk.columns[ARC_NODES].integer(i), c.nodes)?;
    }
    for i in 0..c.arcs {
        index(chunk.columns[ARC_SEGMENTS].integer(i), c.segments)?;
        if chunk.columns[ARC_LENGTHS].float(i) < 0.0 {
            return Err(RoutingError::Invalid("Negative arc length"));
        }
    }
    if chunk.columns[ARC_ALLOWED]
        .bytes
        .iter()
        .chain(chunk.columns[ARC_REVERSE].bytes)
        .chain(chunk.columns[NODE_ALLOWED].bytes)
        .any(|v| *v > 1)
    {
        return Err(RoutingError::Invalid("Invalid numeric policy flags"));
    }
    validate_offsets(chunk.columns[GEOMETRY_OFFSETS], c.segments, c.points)?;
    if !chunk.columns[HILL_OFFSETS].bytes.is_empty() {
        validate_offsets(chunk.columns[HILL_OFFSETS], c.segments, c.intervals)?;
    }
    validate_adjacency(chunk, OUTGOING_OFFSETS, OUTGOING_ARCS, 0)?;
    validate_adjacency(chunk, INCOMING_OFFSETS, INCOMING_ARCS, 1)
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn malformed_and_overflowing_headers_fail_without_panicking() {
        for payload in [&[][..], &[0u8; 64][..]] {
            assert!(parse_chunk(payload).is_err());
        }
        let mut payload = [0u8; 64];
        payload[..8].copy_from_slice(b"RUNNUM02");
        payload[8..12].copy_from_slice(&2u32.to_le_bytes());
        payload[16..24].copy_from_slice(&u64::MAX.to_le_bytes());
        assert!(parse_chunk(&payload).is_err());
    }
}
