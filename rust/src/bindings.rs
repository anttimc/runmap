use std::time::Duration;

use pyo3::exceptions::{PyTimeoutError, PyValueError};
use pyo3::prelude::*;

use crate::fields::{self, FieldSettings, Integrals, IntegrationError, Point};

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
    module.add_class::<crate::native_graph::NumericGraph>()
}
