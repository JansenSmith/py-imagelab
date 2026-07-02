"""Regression sentinel for `imagelab.geometry.get_polygon`'s rotation conversion.

Pre-fix history: `geometry.py` had `rad = rotation * (180/math.pi)` which
inverted the degrees→radians conversion. The bug was invisible in random-
rotation use because rotation is uniformly sampled from [0, 360], so the
buggy mapping produced a different-but-still-uniform distribution of
visible rotations. But any code path that PASSES A SPECIFIC ROTATION
expecting it to mean degrees got the wrong angle.

This test pins the fix mechanically: passing rotation=90 (degrees) should
produce a polygon whose first vertex is rotated 90 degrees from the
unrotated (rotation=0) position.

The baseline regression test doesn't exercise this path because it runs
under `-S circle` (default), and circles use `pygame.draw.circle` which
doesn't call `get_polygon` at all.
"""
import math

from imagelab.geometry import get_polygon


def test_get_polygon_rotation_zero_first_vertex_on_positive_x_axis():
    """With rotation=0, first vertex is at angle 0 from center (positive
    x-axis, i.e. (radius, 0) relative to pos)."""
    pos = (100, 100)
    radius = 10
    vertices = get_polygon(edges=4, radius=radius, pos=pos, rotation=0)
    fx, fy = vertices[0]
    # First vertex relative to pos should be (radius * cos(0), radius * sin(0))
    # = (radius, 0)
    assert math.isclose(fx - pos[0], radius, abs_tol=1e-9)
    assert math.isclose(fy - pos[1], 0, abs_tol=1e-9)


def test_get_polygon_rotation_90_first_vertex_on_positive_y_axis():
    """With rotation=90 (degrees), first vertex is at angle 90 from
    positive x-axis = positive y-axis, i.e. (0, radius) relative to pos.
    Pre-fix this would compute 90*(180/π) ≈ 5157 radians, modulo 2π
    ≈ 4.18 rad ≈ 240° — wrong direction, wrong magnitude."""
    pos = (100, 100)
    radius = 10
    vertices = get_polygon(edges=4, radius=radius, pos=pos, rotation=90)
    fx, fy = vertices[0]
    # cos(90°) = 0, sin(90°) = 1 → first vertex at (pos_x, pos_y + radius)
    assert math.isclose(fx - pos[0], 0, abs_tol=1e-9)
    assert math.isclose(fy - pos[1], radius, abs_tol=1e-9)


def test_get_polygon_rotation_180_first_vertex_on_negative_x_axis():
    """With rotation=180, first vertex at angle 180° = (−radius, 0)
    relative to pos."""
    pos = (50, 50)
    radius = 5
    vertices = get_polygon(edges=3, radius=radius, pos=pos, rotation=180)
    fx, fy = vertices[0]
    assert math.isclose(fx - pos[0], -radius, abs_tol=1e-9)
    assert math.isclose(fy - pos[1], 0, abs_tol=1e-9)


def test_get_polygon_triangle_has_three_vertices():
    """Sanity: edges=3 returns exactly 3 vertices."""
    vertices = get_polygon(edges=3, radius=10, pos=(0, 0), rotation=45)
    assert len(vertices) == 3


def test_get_polygon_vertices_evenly_spaced():
    """For an unrotated hexagon, consecutive vertices should be spaced
    by exactly 60° = π/3 radians."""
    radius = 100
    pos = (0, 0)
    vertices = get_polygon(edges=6, radius=radius, pos=pos, rotation=0)
    # First and second vertex angles
    a0 = math.atan2(vertices[0][1], vertices[0][0])
    a1 = math.atan2(vertices[1][1], vertices[1][0])
    # Difference should be 2π/6 = π/3 (allowing for atan2 branch wrap)
    diff = (a1 - a0) % (2 * math.pi)
    assert math.isclose(diff, math.pi / 3, abs_tol=1e-9)
