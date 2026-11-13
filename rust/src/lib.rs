//! Numerical routing and field-integration kernels with optional Python bindings.

pub mod chunk_graph;
pub mod chunk_search;
pub mod endpoint_overlay;
pub mod environment_cost;
pub mod fields;
pub mod numeric;
mod profiling;
pub mod routing_cost;
pub mod routing_input;
pub mod signal_cost;

#[cfg(feature = "python")]
mod native_graph;

#[cfg(feature = "python")]
mod bindings;
