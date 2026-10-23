use std::time::Duration;

use pyo3::exceptions::{PyTimeoutError, PyValueError};
use pyo3::prelude::*;
use pyo3::types::{PyBool, PyBytes, PyDict, PyFloat, PyInt, PyString};

use crate::fields::{self, FieldSettings, Integrals, IntegrationError, Point};
use crate::vector_tiles::{LineFeature, Value, encode_lines};

type TileLines = Vec<Vec<(f64, f64)>>;

fn tile_value(value: &Bound<'_, PyAny>) -> PyResult<Value> {
    if value.is_instance_of::<PyBool>() {
        Ok(Value::Boolean(value.extract()?))
    } else if value.is_instance_of::<PyInt>() {
        Ok(Value::Integer(value.extract()?))
    } else if value.is_instance_of::<PyFloat>() {
        let number: f64 = value.extract()?;
        if !number.is_finite() {
            return Err(PyValueError::new_err("Tile properties must be finite"));
        }
        Ok(Value::Double(number.to_bits()))
    } else if value.is_instance_of::<PyString>() {
        Ok(Value::Text(value.extract()?))
    } else {
        Err(PyValueError::new_err("Unsupported tile property"))
    }
}

fn tile_lines(lines: TileLines) -> PyResult<Vec<Vec<(i32, i32)>>> {
    lines
        .into_iter()
        .map(|line| {
            line.into_iter()
                .map(|(x, y)| {
                    if !x.is_finite()
                        || !y.is_finite()
                        || x.abs() > f64::from(i32::MAX / 2)
                        || y.abs() > f64::from(i32::MAX / 2)
                    {
                        return Err(PyValueError::new_err("Invalid tile coordinate"));
                    }
                    Ok((x.round_ties_even() as i32, y.round_ties_even() as i32))
                })
                .collect()
        })
        .collect()
}

#[pyfunction]
fn encode_line_tile(
    py: Python<'_>,
    features: Vec<(TileLines, Py<PyDict>)>,
    extent: u32,
) -> PyResult<Py<PyBytes>> {
    if extent == 0 {
        return Err(PyValueError::new_err("Tile extent must be positive"));
    }
    let features = features
        .into_iter()
        .map(|(lines, properties)| {
            let properties = properties
                .bind(py)
                .iter()
                .map(|(key, value)| Ok((key.extract::<String>()?, tile_value(&value)?)))
                .collect::<PyResult<Vec<_>>>()?;
            Ok(LineFeature {
                lines: tile_lines(lines)?,
                properties,
            })
        })
        .collect::<PyResult<Vec<_>>>()?;
    let bytes = py.detach(|| encode_lines(features, extent));
    Ok(PyBytes::new(py, &bytes).unbind())
}

#[pyfunction]
#[pyo3(signature = (points, home, destination, radius, transition_width, spacing_m=5.0, timeout_seconds=None))]
fn integrate_fields(
    points: Vec<Point>,
    home: Point,
    destination: Point,
    radius: f64,
    transition_width: f64,
    spacing_m: f64,
    timeout_seconds: Option<f64>,
) -> PyResult<Integrals> {
    let budget = timeout_seconds
        .map(Duration::try_from_secs_f64)
        .transpose()
        .map_err(|_| PyValueError::new_err("Timeout must be finite and non-negative"))?;
    let settings = FieldSettings {
        home,
        destination,
        radius,
        transition_width,
        spacing: spacing_m,
    };
    fields::integrate_fields(&points, settings, budget).map_err(|error| match error {
        IntegrationError::InvalidInput(message) => PyValueError::new_err(message),
        IntegrationError::TimedOut => {
            PyTimeoutError::new_err("Field integration exceeded its deadline")
        }
    })
}

#[pymodule]
fn _routing_native(module: &Bound<'_, PyModule>) -> PyResult<()> {
    module.add_function(wrap_pyfunction!(integrate_fields, module)?)?;
    module.add_function(wrap_pyfunction!(encode_line_tile, module)?)?;
    module.add_class::<crate::native_graph::NumericGraph>()
}
