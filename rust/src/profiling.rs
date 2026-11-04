//! Opt-in, thread-local exclusive phase timing and Rust allocation accounting.

#[derive(Clone, Copy)]
#[cfg_attr(not(feature = "python"), allow(dead_code))]
pub enum Phase {
    Views,
    Overlay,
    Initialize,
    Traversal,
    Result,
    Serialize,
    Cost,
    Geometry,
    DecodeGeometry,
    Integration,
    HillDecode,
    Index,
}

impl Phase {
    #[cfg(feature = "profiling")]
    fn detailed(self) -> bool {
        matches!(
            self,
            Self::Cost | Self::Geometry | Self::DecodeGeometry | Self::Integration
        )
    }
}

#[cfg(not(feature = "profiling"))]
pub struct Span;

#[cfg(not(feature = "profiling"))]
#[inline(always)]
pub fn span(_phase: Phase) -> Span {
    Span
}

#[cfg(feature = "profiling")]
#[allow(unused_imports)] // Session control is used by Python bindings and profiling tests.
pub use enabled::{begin, finish, span};

#[cfg(feature = "profiling")]
#[cfg_attr(not(feature = "python"), allow(dead_code))]
mod enabled {
    use super::Phase;
    use std::alloc::{GlobalAlloc, Layout, System};
    use std::cell::{Cell, RefCell};
    use std::time::Instant;

    const NAMES: [&str; 12] = [
        "views",
        "overlay",
        "initialize",
        "traversal",
        "result",
        "serialize",
        "cost",
        "clip_geometry",
        "decode_geometry",
        "integration",
        "hill_decode",
        "index",
    ];

    #[derive(Clone, Copy, Default)]
    struct Measurement {
        nanoseconds: u64,
        calls: u64,
        allocations: u64,
        bytes: u64,
    }

    #[derive(Clone, Copy, Default)]
    struct State {
        active: bool,
        detailed: bool,
        timing: bool,
        depth: usize,
        children: [Measurement; 16],
        rows: [Measurement; 12],
    }

    thread_local! {
        static STATE: RefCell<State> = RefCell::new(State::default());
        static ALLOCATIONS: Cell<(bool, u64, u64)> = const { Cell::new((false, 0, 0)) };
    }

    struct CountingAllocator;

    fn allocated(size: usize) {
        let _ = ALLOCATIONS.try_with(|cell| {
            let (enabled, count, bytes) = cell.get();
            if enabled {
                cell.set((true, count + 1, bytes + size as u64));
            }
        });
    }

    // The wrapper preserves System's allocation/deallocation contracts and only
    // updates allocation-free TLS counters; realloc counts the full new request.
    unsafe impl GlobalAlloc for CountingAllocator {
        unsafe fn alloc(&self, layout: Layout) -> *mut u8 {
            let result = unsafe { System.alloc(layout) };
            if !result.is_null() {
                allocated(layout.size());
            }
            result
        }

        unsafe fn alloc_zeroed(&self, layout: Layout) -> *mut u8 {
            let result = unsafe { System.alloc_zeroed(layout) };
            if !result.is_null() {
                allocated(layout.size());
            }
            result
        }

        unsafe fn dealloc(&self, ptr: *mut u8, layout: Layout) {
            unsafe { System.dealloc(ptr, layout) };
        }

        unsafe fn realloc(&self, ptr: *mut u8, layout: Layout, size: usize) -> *mut u8 {
            let result = unsafe { System.realloc(ptr, layout, size) };
            if !result.is_null() {
                allocated(size);
            }
            result
        }
    }

    #[global_allocator]
    static ALLOCATOR: CountingAllocator = CountingAllocator;

    pub fn begin(detailed: bool, timing: bool, allocations: bool) {
        STATE.with(|cell| {
            *cell.borrow_mut() = State {
                active: true,
                detailed,
                timing,
                ..State::default()
            };
        });
        ALLOCATIONS.with(|cell| cell.set((allocations, 0, 0)));
    }

    pub fn finish() -> Vec<(&'static str, u64, u64, u64, u64)> {
        ALLOCATIONS.with(|cell| cell.set((false, 0, 0)));
        STATE.with(|cell| {
            let state = std::mem::take(&mut *cell.borrow_mut());
            NAMES
                .into_iter()
                .zip(state.rows)
                .map(|(name, row)| (name, row.nanoseconds, row.calls, row.allocations, row.bytes))
                .collect()
        })
    }

    pub struct Span {
        phase: Phase,
        started: Option<Instant>,
        allocations: u64,
        bytes: u64,
        depth: usize,
    }

    pub fn span(phase: Phase) -> Option<Span> {
        STATE.with(|cell| {
            let mut state = cell.borrow_mut();
            if !state.active || (phase.detailed() && !state.detailed) {
                return None;
            }
            let (_, allocations, bytes) = ALLOCATIONS.with(Cell::get);
            let guard = Span {
                phase,
                started: state.timing.then(Instant::now),
                allocations,
                bytes,
                depth: state.depth,
            };
            let depth = state.depth;
            state.children[depth] = Measurement::default();
            state.depth += 1;
            Some(guard)
        })
    }

    impl Drop for Span {
        fn drop(&mut self) {
            let nanoseconds = self
                .started
                .map_or(0, |start| start.elapsed().as_nanos() as u64);
            let (_, allocations, bytes) = ALLOCATIONS.with(Cell::get);
            STATE.with(|cell| {
                let mut state = cell.borrow_mut();
                let child = state.children[self.depth];
                let total_allocations = allocations - self.allocations;
                let total_bytes = bytes - self.bytes;
                let row = &mut state.rows[self.phase as usize];
                row.nanoseconds += nanoseconds.saturating_sub(child.nanoseconds);
                row.calls += 1;
                row.allocations += total_allocations - child.allocations;
                row.bytes += total_bytes - child.bytes;
                state.depth -= 1;
                if self.depth > 0 {
                    let parent = &mut state.children[self.depth - 1];
                    parent.nanoseconds += nanoseconds;
                    parent.allocations += total_allocations;
                    parent.bytes += total_bytes;
                }
            });
        }
    }

    #[cfg(test)]
    mod tests {
        use super::*;

        #[test]
        fn nested_allocations_are_exclusive_and_sessions_reset() {
            begin(true, true, true);
            {
                let _outer = span(Phase::Traversal);
                let buffer = std::hint::black_box(vec![0u8; 100]);
                {
                    let _inner = span(Phase::Cost);
                    std::hint::black_box(vec![0u8; 200]);
                }
                std::hint::black_box(buffer);
            }
            let rows = finish();
            assert_eq!((rows[3].3, rows[3].4), (1, 100));
            assert_eq!((rows[6].3, rows[6].4), (1, 200));
            begin(false, false, false);
            {
                let _guard = span(Phase::Cost);
            }
            assert!(finish().iter().all(|row| row.2 == 0));
        }
    }
}
