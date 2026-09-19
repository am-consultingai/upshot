"""The mark's geometry, which is a claim and therefore has invariants.

The logo asserts that the wave and the rule are the same quantity. That is only
true if they are drawn the same length and the same weight, so both are checked
here rather than left to the eye — the first cut of this mark was 1.4 units wider
at each end than the rule, and it looked fine.
"""

from __future__ import annotations

from app import brand


def test_both_strokes_span_exactly_the_same_width() -> None:
    """The equality is the whole idea; a wave wider than its rule is a lie."""
    xs = [x for x, _ in brand.wave_outline()]
    x0, _, x1, _ = brand.rule_box()

    assert min(xs) == brand.X0 == x0
    assert max(xs) == brand.X1 == x1


def test_both_strokes_carry_the_same_weight() -> None:
    _, y0, _, y1 = brand.rule_box()
    assert y1 - y0 == brand.WEIGHT


def test_wave_is_not_too_tight_for_its_own_stroke() -> None:
    """Below this the inner edge folds through itself and Pillow punches holes."""
    assert brand.peak_curvature_radius() > brand.HALF


def test_mark_is_vertically_centred() -> None:
    ys = [y for _, y in brand.wave_outline()]
    _, _, _, bottom = brand.rule_box()
    assert (min(ys) + bottom) / 2 == brand.GRID / 2


def test_wave_begins_and_ends_on_a_peak() -> None:
    """Horizontal tangents at the ends are what make the terminals cut vertically."""
    for t in (0.0, 1.0):
        _, dy = brand._spine_derivative(t)
        assert abs(dy) < 1e-9


def test_render_is_square_rgba_at_any_size() -> None:
    for size in (16, 32, 64):
        image = brand.render(size, (15, 111, 104))
        assert image.size == (size, size)
        assert image.mode == "RGBA"


def test_badge_does_not_erase_the_whole_mark() -> None:
    """The badge knocks a transparent ring out of the artwork; it must be local."""
    plain = brand.render(64, (15, 111, 104))
    badged = brand.render(64, (15, 111, 104), badge=True)

    opaque = lambda im: sum(1 for px in im.getdata() if px[3] > 128)  # noqa: E731
    assert opaque(badged) > opaque(plain) * 0.6
