"""The app icon is generated at build time, so it is worth a smoke test."""

from __future__ import annotations

import importlib.util
import pathlib
import sys

import pytest

MAKE_ICON = pathlib.Path(__file__).resolve().parent.parent / "packaging" / "make_icon.py"


@pytest.fixture(scope="module")
def make_icon():
    spec = importlib.util.spec_from_file_location("make_icon", MAKE_ICON)
    module = importlib.util.module_from_spec(spec)
    sys.modules["make_icon"] = module
    spec.loader.exec_module(module)
    return module


def alpha(canvas, x: int, y: int) -> float:
    return canvas.px[y * canvas.size + x][3]


def test_corners_are_transparent_and_the_middle_is_not(make_icon):
    cv = make_icon.render(64)
    assert alpha(cv, 0, 0) == pytest.approx(0.0, abs=0.02)
    assert alpha(cv, 63, 0) == pytest.approx(0.0, abs=0.02)
    assert alpha(cv, 32, 32) == pytest.approx(1.0, abs=0.01)


def test_the_body_leaves_room_for_the_shadow(make_icon):
    """Apple's grid: the rounded body is 824 of the 1024pt canvas."""
    cv = make_icon.render(128)
    row = 64
    opaque = [x for x in range(cv.size) if alpha(cv, x, row) > 0.9]
    width = opaque[-1] - opaque[0] + 1
    assert width == pytest.approx(128 * 824 / 1024, abs=2)


def test_edges_are_anti_aliased(make_icon):
    """A hard-edged fill would have no partial coverage anywhere."""
    cv = make_icon.render(64)
    partial = [p for p in cv.px if 0.05 < p[3] < 0.95]
    assert len(partial) > 40


def test_the_prompt_is_green_at_every_size(make_icon):
    for size in (16, 32, 128):
        cv = make_icon.render(size)
        green = [
            p
            for p in cv.px
            if p[3] > 0.6 and p[1] > 150 and p[1] - p[0] > 40 and p[1] - p[2] > 40
        ]
        assert green, f"no prompt visible at {size}px"


def test_small_sizes_drop_the_detail(make_icon):
    """16 and 32px carry one screen, not three, or the panes turn to mush."""

    def gap_brightness(size: int) -> float:
        """Colour at design point (673, 512) - the gutter between the two
        right-hand panes, which the enlarged prompt never reaches."""
        cv = make_icon.render(size)
        ix = int(673 / 1024 * size)
        iy = size // 2
        r, g, b, _ = cv.px[iy * cv.size + ix]
        return r + g + b

    # Detailed: the chassis shows through the gutter. Simplified: one screen
    # covers it, and screens are darker than the chassis.
    assert gap_brightness(128) > gap_brightness(32) + 20
    assert gap_brightness(128) > gap_brightness(16) + 20
