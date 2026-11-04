//! Measure quadrature choices on prepared geometry, excluding Python conversion.

use _routing_native::fields::{FieldSettings, Integrals, Point, experiment_integrate};
use std::hint::black_box;
use std::time::Instant;

struct Case {
    points: Vec<Point>,
    settings: FieldSettings,
}

fn evaluate(cases: &[Case], cap: f64, minimum: usize) -> Vec<Integrals> {
    cases
        .iter()
        .map(|case| {
            let settings = FieldSettings {
                spacing: cap.min(case.settings.radius.min(case.settings.transition_width) / 4.0),
                ..case.settings
            };
            experiment_integrate(&case.points, settings, minimum).unwrap()
        })
        .collect()
}

fn percentile(values: &mut [f64], fraction: f64) -> f64 {
    values.sort_by(f64::total_cmp);
    values[((values.len() as f64 * fraction).ceil() as usize).saturating_sub(1)]
}

fn counts(cases: &[Case], cap: f64, minimum: usize) -> (usize, usize, usize) {
    let mut samples = 0;
    let mut floor = 0;
    let mut pieces = 0;
    for case in cases {
        let spacing = cap.min(case.settings.radius.min(case.settings.transition_width) / 4.0);
        for pair in case.points.windows(2) {
            let distance = (pair[1].0 - pair[0].0).hypot(pair[1].1 - pair[0].1);
            let needed = (distance / spacing).ceil() as usize;
            samples += needed.max(minimum);
            floor += usize::from(needed <= minimum);
            pieces += 1;
        }
    }
    (samples, floor, pieces)
}

fn errors(actual: &[Integrals], reference: &[Integrals]) -> (f64, f64) {
    let mut errors: Vec<f64> = actual
        .iter()
        .zip(reference)
        .map(|(a, r)| {
            if r.2 == 0.0 {
                0.0
            } else {
                (a.0 - r.0).abs().max((a.1 - r.1).abs()) / r.2 * 100.0
            }
        })
        .collect();
    let maximum = errors.iter().copied().fold(0.0, f64::max);
    (percentile(&mut errors, 0.99), maximum)
}

fn read_cases(input: &str) -> Vec<Vec<Case>> {
    let mut values = input.split_whitespace();
    let groups: usize = values.next().unwrap().parse().unwrap();
    (0..groups)
        .map(|_| {
            let count: usize = values.next().unwrap().parse().unwrap();
            (0..count)
                .map(|_| {
                    let mut number = || values.next().unwrap().parse::<f64>().unwrap();
                    let home = (number(), number());
                    let destination = (number(), number());
                    let radius = number();
                    let transition_width = number();
                    let point_count = number() as usize;
                    let points = (0..point_count).map(|_| (number(), number())).collect();
                    Case {
                        points,
                        settings: FieldSettings {
                            home,
                            destination,
                            radius,
                            transition_width,
                            spacing: 5.0,
                        },
                    }
                })
                .collect()
        })
        .collect()
}

fn main() {
    let path = std::env::args().nth(1).expect("input file required");
    let repeats: usize = std::env::args()
        .nth(2)
        .unwrap_or("15".into())
        .parse()
        .unwrap();
    assert!(repeats > 0);
    let groups = read_cases(&std::fs::read_to_string(path).unwrap());
    println!(
        "scenario,cap_m,minimum,median_ms,p95_ms,samples,floor_pieces,pieces,error_p99_pct,error_max_pct,reference_delta_max_pct"
    );
    for (group, cases) in groups.iter().enumerate() {
        let reference = evaluate(cases, 0.05, 4);
        let reference_delta = errors(&evaluate(cases, 0.1, 4), &reference).1;
        let variants: Vec<_> = [4, 2, 1]
            .into_iter()
            .flat_map(|minimum| {
                [5.0, 10.0, 20.0, 40.0]
                    .into_iter()
                    .map(move |cap| (cap, minimum))
            })
            .collect();
        let mut timings = vec![Vec::new(); variants.len()];
        for &(cap, minimum) in &variants {
            black_box(evaluate(cases, cap, minimum));
        }
        for repeat in 0..repeats {
            // Rotate order to spread temporal effects across configurations.
            for offset in 0..variants.len() {
                let index = (offset + repeat) % variants.len();
                let (cap, minimum) = variants[index];
                let started = Instant::now();
                for _ in 0..10 {
                    black_box(evaluate(cases, cap, minimum));
                }
                timings[index].push(started.elapsed().as_secs_f64() * 100.0);
            }
        }
        for (index, &(cap, minimum)) in variants.iter().enumerate() {
            let actual = evaluate(cases, cap, minimum);
            let (p99, maximum) = errors(&actual, &reference);
            let (samples, floor, pieces) = counts(cases, cap, minimum);
            let p95 = percentile(&mut timings[index], 0.95);
            let median = percentile(&mut timings[index], 0.5);
            println!(
                "{group},{cap},{minimum},{median},{p95},{samples},{floor},{pieces},{p99},{maximum},{reference_delta}"
            );
        }
    }
}
