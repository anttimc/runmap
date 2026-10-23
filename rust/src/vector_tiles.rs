//! Encode already clipped and quantized linework without Python feature loops.

use std::collections::HashMap;

#[derive(Clone, PartialEq, Eq, Hash)]
pub enum Value {
    Text(String),
    Boolean(bool),
    Integer(i64),
    Double(u64),
}

pub struct LineFeature {
    pub lines: Vec<Vec<(i32, i32)>>,
    pub properties: Vec<(String, Value)>,
}

#[derive(Default)]
struct Dictionary {
    keys: HashMap<String, usize>,
    values: HashMap<Value, usize>,
    fields: Vec<u8>,
}

fn varint(mut value: u64, bytes: &mut Vec<u8>) {
    while value >= 128 {
        bytes.push((value as u8 & 127) | 128);
        value >>= 7;
    }
    bytes.push(value as u8);
}

fn integer(field: u64, value: u64, bytes: &mut Vec<u8>) {
    varint(field << 3, bytes);
    varint(value, bytes);
}

fn message(field: u64, payload: &[u8], bytes: &mut Vec<u8>) {
    varint((field << 3) | 2, bytes);
    varint(payload.len() as u64, bytes);
    bytes.extend_from_slice(payload);
}

fn encode_value(value: &Value) -> Vec<u8> {
    let mut bytes = Vec::new();
    match value {
        Value::Text(text) => message(1, text.as_bytes(), &mut bytes),
        Value::Boolean(value) => integer(7, u64::from(*value), &mut bytes),
        Value::Integer(value) => integer(4, *value as u64, &mut bytes),
        Value::Double(bits) => {
            varint((3 << 3) | 1, &mut bytes);
            bytes.extend_from_slice(&bits.to_le_bytes());
        }
    }
    bytes
}

fn property_tags(properties: Vec<(String, Value)>, dictionary: &mut Dictionary) -> Vec<u8> {
    let mut tags = Vec::new();
    for (key, value) in properties {
        let key_index = if let Some(index) = dictionary.keys.get(&key) {
            *index
        } else {
            let index = dictionary.keys.len();
            message(3, key.as_bytes(), &mut dictionary.fields);
            dictionary.keys.insert(key, index);
            index
        };
        let value_index = if let Some(index) = dictionary.values.get(&value) {
            *index
        } else {
            let index = dictionary.values.len();
            message(4, &encode_value(&value), &mut dictionary.fields);
            dictionary.values.insert(value, index);
            index
        };
        varint(key_index as u64, &mut tags);
        varint(value_index as u64, &mut tags);
    }
    tags
}

fn delta(point: (i32, i32), previous: &mut (i32, i32), bytes: &mut Vec<u8>) {
    for value in [
        i64::from(point.0) - i64::from(previous.0),
        i64::from(point.1) - i64::from(previous.1),
    ] {
        varint(((value << 1) ^ (value >> 63)) as u64, bytes);
    }
    *previous = point;
}

fn line_geometry(lines: Vec<Vec<(i32, i32)>>) -> Vec<u8> {
    let mut bytes = Vec::new();
    let mut previous = (0, 0);
    for mut points in lines {
        points.dedup();
        if points.len() < 2 {
            continue;
        }
        varint(9, &mut bytes); // MoveTo, one point.
        delta(points[0], &mut previous, &mut bytes);
        varint((((points.len() - 1) as u64) << 3) | 2, &mut bytes);
        for point in points.into_iter().skip(1) {
            delta(point, &mut previous, &mut bytes);
        }
    }
    bytes
}

pub fn encode_lines(features: Vec<LineFeature>, extent: u32) -> Vec<u8> {
    let mut layer = Vec::new();
    message(1, b"paths", &mut layer);
    let mut dictionary = Dictionary::default();
    for feature in features {
        let geometry = line_geometry(feature.lines);
        if geometry.is_empty() {
            continue;
        }
        let tags = property_tags(feature.properties, &mut dictionary);
        let mut encoded = Vec::new();
        if !tags.is_empty() {
            message(2, &tags, &mut encoded);
        }
        integer(3, 2, &mut encoded); // LINESTRING, including multipart lines.
        message(4, &geometry, &mut encoded);
        message(2, &encoded, &mut layer);
    }
    layer.extend_from_slice(&dictionary.fields);
    integer(5, u64::from(extent), &mut layer);
    integer(15, 2, &mut layer);
    let mut tile = Vec::new();
    message(3, &layer, &mut tile);
    tile
}
