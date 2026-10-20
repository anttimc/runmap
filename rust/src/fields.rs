use std::time::{Duration, Instant};

pub type Point = (f64, f64);
pub type Integrals = (f64, f64, f64);

#[derive(Clone, Copy)]
pub struct FieldSettings {
    pub home: Point,
    pub destination: Point,
    pub radius: f64,
    pub transition_width: f64,
    pub spacing: f64,
}

#[derive(Debug, PartialEq)]
pub enum IntegrationError {
    InvalidInput(&'static str),
    TimedOut,
}

struct FieldFrame {
    home: Point,
    centre: Point,
    direction: Point,
    separation: f64,
    radius_denominator: f64,
    transition_width: f64,
    spacing: f64,
}

fn prepare_frame(settings: FieldSettings) -> FieldFrame {
    let direction = (
        settings.destination.0 - settings.home.0,
        settings.destination.1 - settings.home.1,
    );
    FieldFrame {
        home: settings.home,
        centre: (
            (settings.home.0 + settings.destination.0) / 2.0,
            (settings.home.1 + settings.destination.1) / 2.0,
        ),
        direction,
        separation: direction.0.hypot(direction.1),
        radius_denominator: 2.0 * settings.radius.powi(2),
        transition_width: settings.transition_width,
        spacing: settings.spacing,
    }
}

fn validate(points: &[Point], settings: FieldSettings) -> Result<(), IntegrationError> {
    let scales = [settings.radius, settings.transition_width, settings.spacing];
    if scales.iter().any(|v| !v.is_finite() || *v <= 0.0) {
        return Err(IntegrationError::InvalidInput(
            "Field scales must be finite and positive",
        ));
    }
    if points
        .iter()
        .chain([&settings.home, &settings.destination])
        .any(|p| !p.0.is_finite() || !p.1.is_finite())
    {
        return Err(IntegrationError::InvalidInput("Coordinates must be finite"));
    }
    let separation =
        (settings.destination.0 - settings.home.0).hypot(settings.destination.1 - settings.home.1);
    if !separation.is_finite() || separation == 0.0 {
        return Err(IntegrationError::InvalidInput(
            "Home and destination must be spatially distinct",
        ));
    }
    Ok(())
}

fn check_timeout(started: Instant, budget: Option<Duration>) -> Result<(), IntegrationError> {
    if budget.is_some_and(|limit| started.elapsed() >= limit) {
        return Err(IntegrationError::TimedOut);
    }
    Ok(())
}

fn integrate_segment(
    first: Point,
    second: Point,
    frame: &FieldFrame,
    started: Instant,
    budget: Option<Duration>,
) -> Result<Integrals, IntegrationError> {
    let distance = (second.0 - first.0).hypot(second.1 - first.1);
    let samples = (distance / frame.spacing).ceil().max(4.0);
    if !samples.is_finite() || samples >= usize::MAX as f64 {
        return Err(IntegrationError::InvalidInput(
            "Sample count exceeds supported range",
        ));
    }
    let count = samples as usize;
    let mut radial = 0.0;
    let mut right = 0.0;
    for sample in 0..count {
        if sample % 256 == 0 {
            check_timeout(started, budget)?;
        }
        let fraction = (sample as f64 + 0.5) / count as f64;
        let x = first.0 + fraction * (second.0 - first.0);
        let y = first.1 + fraction * (second.1 - first.1);
        let signed = (frame.direction.0 * (y - frame.home.1)
            - frame.direction.1 * (x - frame.home.0))
            / frame.separation;
        let squared_distance = (x - frame.centre.0).powi(2) + (y - frame.centre.1).powi(2);
        radial += (-squared_distance / frame.radius_denominator).exp() * distance / count as f64;
        right += (1.0 - (signed / frame.transition_width).tanh()) / 2.0 * distance / count as f64;
    }
    Ok((radial, right, distance))
}

/// Preserve midpoint integration while keeping the sample loop outside Python.
pub fn integrate_fields(
    points: &[Point],
    settings: FieldSettings,
    budget: Option<Duration>,
) -> Result<Integrals, IntegrationError> {
    let started = Instant::now();
    check_timeout(started, budget)?;
    validate(points, settings)?;
    let frame = prepare_frame(settings);
    let mut totals = (0.0, 0.0, 0.0);
    for pair in points.windows(2) {
        let (radial, right, length) = integrate_segment(pair[0], pair[1], &frame, started, budget)?;
        totals.0 += radial;
        totals.1 += right;
        totals.2 += length;
    }
    check_timeout(started, budget)?;
    Ok(totals)
}

#[cfg(test)]
mod tests {
    use super::*;

    fn settings() -> FieldSettings {
        FieldSettings {
            home: (0.0, 0.0),
            destination: (100.0, 0.0),
            radius: 15.0,
            transition_width: 5.0,
            spacing: 5.0,
        }
    }

    #[test]
    fn centreline_has_half_length_on_each_side() {
        let (_, right, length) =
            integrate_fields(&[(0.0, 0.0), (100.0, 0.0)], settings(), None).unwrap();
        assert_eq!(length, 100.0);
        assert!((right - length / 2.0).abs() < 1e-12);
    }

    #[test]
    fn empty_and_repeated_points_have_zero_integrals() {
        for points in [vec![], vec![(1.0, 2.0)], vec![(1.0, 2.0); 2]] {
            assert_eq!(
                integrate_fields(&points, settings(), None),
                Ok((0.0, 0.0, 0.0))
            );
        }
    }

    #[test]
    fn expired_budget_is_reported() {
        assert_eq!(
            integrate_fields(
                &[(0.0, 0.0), (100.0, 0.0)],
                settings(),
                Some(Duration::ZERO)
            ),
            Err(IntegrationError::TimedOut)
        );
    }

    #[test]
    fn invalid_scales_are_rejected() {
        let mut invalid = settings();
        invalid.spacing = 0.0;
        assert!(matches!(
            integrate_fields(&[], invalid, None),
            Err(IntegrationError::InvalidInput(_))
        ));
    }
}
