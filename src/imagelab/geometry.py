"""Math and geometry utilities for imagelab
"""
import math
from imagelab import rng
from imagelab.color import get_random_color

def get_random_clip_rect(rect, clip_width, clip_height, constrain=False):
    """ Take an input rectangle and return a random sub rectange if constrain
        is true, do not allow the output rect to extend beyond
        input rect bounds
    """
    if constrain:
        # When the clip exactly fills (or exceeds) the rect dimension on an
        # axis, the only valid position on that axis is 0. Without this guard
        # numpy's rng.integers raises `high <= 0` since rect.dim - clip_dim
        # <= 0.
        x_pos = (
            rng.integers(0, rect.width - clip_width)
            if rect.width > clip_width else 0
        )
        y_pos = (
            rng.integers(0, rect.height - clip_height)
            if rect.height > clip_height else 0
        )
    else:
        x_pos = rng.integers(int(-(clip_width/2)), int(rect.width - (clip_width/2)))
        y_pos = rng.integers(int(-(clip_height/2)), int(rect.height - (clip_height/2)))

    return (x_pos, y_pos, clip_width, clip_height)


def resize_with_pad(source_size, target_size):
    (source_width, source_height) = source_size
    (target_width, target_height) = target_size
    target_ratio = target_height / target_width
    source_ratio = source_height / source_width
    if target_ratio > source_ratio:
        # It must be fixed by width
        resize_width = target_width
        resize_height = round(resize_width * source_ratio)
    else:
        # Fixed by height
        resize_height = target_height
        resize_width = round(resize_height / source_ratio)

    return (resize_width, resize_height)


def get_random_circle(canvas, clip_rect=None, max_radius=20, radius=None,
                      color=None, color_key=(0, 0, 0), pos=None):
    """Create a random circle, defined by color position and radius"""
    if not radius:
        radius = rng.integers(1, max_radius)

    if not color:
        color = get_random_color()

    if not pos:
        if clip_rect:
            pos = (rng.integers(clip_rect.left + radius, clip_rect.right - radius),
                   rng.integers(clip_rect.top + radius, clip_rect.bottom - radius))
        else:
            pos = (rng.integers(0, max(canvas.get_rect().size)),
                   rng.integers(0, max(canvas.get_rect().size)))

    return (color, pos, radius)


# todo: allow for random irregular polygons:
# https://observablehq.com/@tarte0/generate-random-simple-polygon
# todo: look into getting rid of max_(edges, radius) and simply letters
# edge/radius,etc be a tuple: (min,max) or a single value
def get_random_polygon(canvas, edges=None, rotation=None, clip_rect=None,
                       max_radius=20, radius=None, color=None, pos=None,
                       max_edges=8):
    """Create a random polygon defined by color position
       edges rotation and radius"""
    if not radius:
        radius = rng.integers(1, max_radius + 1)

    if not color:
        color = get_random_color()

    if not edges:
        edges = rng.integers(3, max_edges)

    if rotation is None:
        rotation = rng.integers(0, 361)

    if not pos:
        if clip_rect:
            pos = (rng.integers(clip_rect.left + max_radius,
                                clip_rect.right - max_radius + 1),
                   rng.integers(clip_rect.top + max_radius,
                                clip_rect.bottom - max_radius + 1))
        else:
            pos = (rng.integers(0, max(canvas.get_rect().size)),
                   rng.integers(0, max(canvas.get_rect().size)))

    return (color, pos, radius, edges, rotation)


def get_random_word(canvas, words, rotation=None, clip_rect=None,
                    max_radius=20, radius=None, color=None, pos=None):
    word = rng.choice(words, size=None)
    if not radius:
        radius = rng.integers(1, max_radius + 1)

    if not color:
        color = get_random_color()

    if rotation is None:
        rotation = rng.integers(0, 361)

    if not pos:
        if clip_rect:
            pos = (rng.integers(clip_rect.left + max_radius,
                                clip_rect.right - max_radius + 1),
                   rng.integers(clip_rect.top + max_radius,
                                clip_rect.bottom - max_radius + 1))
        else:
            pos = (rng.integers(0, max(canvas.get_rect().size)),
                   rng.integers(0, max(canvas.get_rect().size)))

    return (color, pos, radius, word, rotation)


def get_polygon(edges=3, radius=10, pos=(0, 0), rotation=0):
    d_angle = 2*math.pi / edges

    rad = rotation * (180/math.pi)

    ret = []

    for i in range(edges):
        point = (pos[0] + radius*math.cos(rad), pos[1] + radius*math.sin(rad))

        ret.append(point)

        rad += d_angle

    return ret
