//! Register Python access to native graph search and scalar field integration.

use std::time::Duration;

use pyo3::exceptions::{PyTimeoutError, PyValueError};
use pyo3::prelude::*;

use crate::fields::{self, FieldSettings, Integrals, IntegrationError, Point};

#[pyfunction]
#[pyo3(signature = (points, home, destination, radius, transition_width, spacing_m=10.0, timeout_seconds=None))]
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
    #[cfg(feature = "profiling")]
    {
        module.add_function(wrap_pyfunction!(profile_begin, module)?)?;
        module.add_function(wrap_pyfunction!(profile_finish, module)?)?;
        module.add_function(wrap_pyfunction!(profile_layout, module)?)?;
    }
    module.add_function(wrap_pyfunction!(integrate_fields, module)?)?;
    module.add_function(wrap_pyfunction!(
        crate::native_graph::environment_unknown_lengths,
        module
    )?)?;
    module.add_class::<crate::native_graph::NumericChunk>()?;
    module.add_class::<crate::native_graph::NumericGraph>()
}

#[cfg(feature = "profiling")]
#[pyfunction]
fn profile_begin(detailed: bool, timing: bool, allocations: bool) {
    crate::profiling::begin(detailed, timing, allocations);
}

#[cfg(feature = "profiling")]
#[pyfunction]
fn profile_finish() -> Vec<(&'static str, u64, u64, u64, u64)> {
    crate::profiling::finish()
}

#[cfg(feature = "profiling")]
#[pyfunction]
fn profile_layout() -> (usize, usize) {
    use crate::endpoint_overlay::Edge;
    (
        std::mem::size_of::<Edge>(),
        std::mem::size_of::<Option<crate::chunk_search::EdgeRef>>(),
    )
}
