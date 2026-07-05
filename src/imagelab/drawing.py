"""Drawing Routines to be applied as CanvasActions
"""
from imagelab import rng
from imagelab.constants import SHAPE_POLYGON, SHAPE_CIRCLE
from imagelab.geometry import (get_random_circle, get_random_polygon,
                               get_random_clip_rect, get_random_word)
from imagelab.canvas import CanvasActionDrawShape
from imagelab.canvas import CanvasActionDrawText


def draw_random_circle(canvas, clip_rect=None, max_radius=20, radius=None,
                       alpha=None, color=None, color_key=(0, 0, 0), pos=None,
                       brush_images=None, surface_origin=(0, 0),
                       brush_mode='texture'):
    """Apply paint to the canvas, return details of the circle."""

    (color, pos, radius) = get_random_circle(canvas, clip_rect, max_radius,
                                             radius, color, pos)

    brush_image = rng.choice(brush_images, size=None) if brush_images else None

    params = {'color': color, 'brush_image': brush_image, 'pos': pos,
              'radius': radius, 'alpha': alpha, 'shape': SHAPE_CIRCLE,
              'brush_mode': brush_mode}

    if brush_image:
        brush_size = brush_image.get_size()
        if brush_mode == 'shape':
            sample_size = min(brush_size)
        else:
            max_sample_size = max(radius*2, min(brush_size))
            sample_size = rng.integers(radius*2, max_sample_size)
        params['brush_sample_rect'] = get_random_clip_rect(
            brush_image.get_rect(),
            sample_size,
            sample_size,
            True
        )
        params['brush_rotation'] = rng.integers(1, 361)

    ca = CanvasActionDrawShape(params)
    ca.run(canvas, origin=surface_origin)

    return ca


def draw_random_polygon(canvas, edges=None, rotation=None, clip_rect=None,
                        max_radius=20, radius=None, alpha=None, color=None,
                        color_key=(0, 0, 0), pos=None, max_edges=8,
                        brush_images=None, surface_origin=(0, 0),
                        brush_mode='texture'):
    """Apply paint to the canvas, return details of the polygon."""

    (color, pos, radius, edges, rotation) = get_random_polygon(
        canvas, edges, rotation, clip_rect, max_radius, radius, color,
        pos, max_edges)

    brush_image = rng.choice(brush_images, size=None) if brush_images else None

    params = {'color': color, 'brush_image': brush_image, 'pos': pos,
              'radius': radius, 'edges': edges, 'rotation': rotation,
              'alpha': alpha, 'shape': SHAPE_POLYGON,
              'brush_mode': brush_mode}

    if brush_image:
        brush_size = brush_image.get_size()
        if brush_mode == 'shape':
            sample_size = min(brush_size)
        else:
            max_sample_size = max(radius*2, min(brush_size))
            sample_size = rng.integers(radius*2, max_sample_size)
        params['brush_sample_rect'] = get_random_clip_rect(
            brush_image.get_rect(),
            sample_size,
            sample_size,
            True
        )
        params['brush_rotation'] = rng.integers(1, 361)

    ca = CanvasActionDrawShape(params)
    ca.run(canvas, origin=surface_origin)

    return ca


def draw_random_word(canvas, words, rotation=None, clip_rect=None,
                     max_radius=20, radius=None, alpha=None, color=None,
                     color_key=(0, 0, 0), pos=None,
                     brush_images=None, surface_origin=(0, 0),
                     brush_mode='texture'):
    """ Apply paint to the canvas, return details of a random word from
        candidate list. brush_mode accepted for API symmetry; text rendering
        doesn't consult brush alpha for a shape mask, so texture/shape both
        behave the same here. """

    (color, pos, radius, word, rotation) = get_random_word(
        canvas, words, rotation, clip_rect, max_radius, radius, color, pos)

    brush_image = rng.choice(brush_images, size=None) if brush_images else None

    params = {'color': color, 'brush_image': brush_image, 'pos': pos,
              'radius': radius, 'rotation': rotation, 'alpha': alpha,
              'text': word}

    ca = CanvasActionDrawText(params)
    ca.run(canvas, origin=surface_origin)

    return ca
