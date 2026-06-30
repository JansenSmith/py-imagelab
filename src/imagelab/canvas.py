"""Canvas utilities for imagelab

A canvas contains a pygame surface and represents the current state and history
of an drawing
"""
from abc import ABC, abstractmethod
import base64
import gzip
import os
import pygame
import numpy as np
from imagelab.constants import SHAPE_CIRCLE
from imagelab.geometry import get_polygon


def _jsonsafe(value):
    """Recursively convert numpy scalar/array types to native Python types
    so json.dumps can serialize them.

    numpy.integer / numpy.floating are returned by numpy random APIs and
    by per-channel arithmetic; without this conversion json.dumps raises
    `TypeError: Object of type int64 is not JSON serializable`. The bug
    is triggered any time a canvas action's params dict contains a numpy
    scalar (e.g. `pos`, `radius`, `rotation` from rng.integers), which is
    every shape produced by mutate_evolve."""
    if isinstance(value, np.integer):
        return int(value)
    if isinstance(value, np.floating):
        return float(value)
    if isinstance(value, np.ndarray):
        return value.tolist()
    if isinstance(value, tuple):
        return tuple(_jsonsafe(v) for v in value)
    if isinstance(value, list):
        return [_jsonsafe(v) for v in value]
    if isinstance(value, dict):
        return {k: _jsonsafe(v) for k, v in value.items()}
    return value

CANVAS_FONT_PATH = os.path.join(os.path.dirname(__file__), "..", "..", "assets", "fonts", "luculent", "luculent.ttf")


class Canvas:
    """A wrapper for a pygame surface.  Holds the surface, its history
      and provides some methods
    """
    surface = None
    current_action = 0
    history = []

    def __init__(self, surface=None):
        self.surface = surface

    @property
    def size(self):
        """ Pass through to surface size """
        if self.surface is None:
            return (0, 0)
        return self.surface.get_size()

    def apply_mutator(self, mutator, args={}):
        # mutation_event = mutator(self.surface, args)
        actions, surface, _ = mutator(self.surface, args)
        # self.surface = mutation_event.surface
        self.surface = surface

        # clear out the surface from the event.  We don't need it in Mem
        # IMPORTANT!
        # mutation_event.surface = None
        # self.history.append(mutation_event)
        self.history.extend(actions)

    def apply_canvas_action(self, canvas_action):
        pass

    def serialize(self):
        # history = [item.serialize() for item in self.history]
        # return json.dumps([self.size, history])
        # return pickle.dumps([self.size,history])
        return self.__json__()

    def clear_surface(self, color=None):
        self.surface = pygame.Surface(self.size)
        if color is not None:
            self.surface.fill(color)
        print("clearing surface")
        self.current_action = 0

    def replay(self, to=-1, color=None):
        self.clear_surface(color)
        num_actions = len(self.history)

        if to == -1:
            to = num_actions - 1
        while self.current_action <= to:
            self.history[self.current_action].run(self.surface)
            self.current_action += 1

    @staticmethod
    def deserialize(serialized_data, autorun=False):
        size = serialized_data["size"]
        hist = serialized_data["history"]
        surface = pygame.Surface(size)
        canvas = Canvas(surface)

        for serialized_action in hist:
            canvas_action = CanvasAction.deserialize(serialized_action)
            if canvas_action is not None:
                if autorun:
                    canvas.current_action += 1
                    canvas_action.run(canvas.surface)
                canvas.history.append(canvas_action)

        return canvas

    def __json__(self):
        history = []
        binary_map = {}
        for item in self.history:
            history.append(item.serialize())
            binary_map.update(item.binary_map)

        return {"size": list(self.size), "history": history, "binaries": binary_map}


class CanvasAction(ABC):
    """Abstract (interface, really) These are single actions.
        They should be kept simple. They must be serializable. This is what the
        Mutators use to record the history of their actions.  Think of them as
        serializable wrappers to function calls.  One rule of thumb for these
        mutators is that given input, output should always be identical. These
        are wrappers for primitive drawing routines. None of the routines
        called should do anything random. If you are tempted to have this do
        something random... use a Mutator
    """
    params = {}
    opcode = '0'
    _binary_params = []

    def __init__(self, params):
        self.params = params

    def serialize(self):
        # return pickle.dumps(self.params)
        # return json.dumps([self.opcode, self.params])
        return self.__json__()

    @staticmethod
    def deserialize(data, cached_binaries=None):
        # self.params = pickle.loads(params)
        [opcode, params] = data

        class_object = {
            cls.opcode: cls for cls in all_canvas_actions
        }.get(opcode, None)

        if class_object is not None:
            return class_object(params)

        return None

    def serialize_binary_param(self, key, data):
        return data

    @property
    def binary_map(self):
        return {id(self.params[key]): self.serialize_binary_param(key, self.params[key]) for key in self.params if self.params[key] is not None and key in self._binary_params}

    def __json__(self):
        filtered_params = {
            key: (
                # Binary-flagged params get replaced by their id() (the
                # actual bytes live in binary_map).
                id(self.params[key])
                if (self.params[key] is not None
                    and key in self._binary_params)
                # Non-binary params get jsonsafe-converted to handle numpy
                # scalars / arrays. Plain Python values pass through.
                else _jsonsafe(self.params[key])
            )
            for key in self.params
        }
        return [self.opcode, filtered_params]

    @abstractmethod
    def run(self, canvas, origin=(0, 0)):
        pass


class CanvasActionDrawShape(CanvasAction):
    """Draw a shape on the canvas"""
    opcode = "s"
    _binary_params = ['brush_image']

    def deserialize_binary_param(self, key, data):
        if key == 'brush_image':
            # not tested, probably won't work because of the utf-8 bit?
            # also missing size dimensions
            return pygame.image.fromstring(gzip.decompress(base64.b64decode(data)))
        return super().deserialize_binary_param(key, data)

    def serialize_binary_param(self, key, data):
        if key == 'brush_image':
            return None if not self.params.get('brush_image') else \
                base64.b64encode(
                    gzip.compress(pygame.image.tostring(
                        self.params.get('brush_image'), 'RGBA'
                    ))
                ).decode('utf-8')
        return super().serialize_binary_param(key, data)

    def run(self, canvas, origin=(0, 0)):
        alpha = self.params.get('alpha', 220)
        color = self.params.get('color')
        pos = self.params.get('pos')
        radius = self.params.get('radius')
        color_key = self.params.get('color_key', (0, 0, 0))
        shape = self.params.get('shape')
        rotation = self.params.get('rotation')
        edges = self.params.get('edges')
        brush_image = self.params.get('brush_image')
        brush_sample_rect = self.params.get('brush_sample_rect')
        brush_rotation = self.params.get('brush_rotation', 0)

        ox, oy = origin
        draw_pos = (pos[0] - ox, pos[1] - oy)

        if not brush_image and not alpha:
            if SHAPE_CIRCLE == shape:
                pygame.draw.circle(canvas, color, draw_pos, radius, 0)
            else:
                pygame.draw.polygon(canvas, color,
                                    get_polygon(edges, radius, draw_pos, rotation))
            return

        # TODO: all of this should live outside of the canvas action
        # since it includes random calculations
        if brush_image:
            scaled_size = (radius*2, radius*2)
            brush_image = brush_image.subsurface(
                brush_image.get_rect().clip(brush_sample_rect)
            )
            brush_image = pygame.transform.smoothscale(
                brush_image,
                scaled_size
            ).convert_alpha()

            if brush_rotation:
                # brush_image = pygame.transform.rotozoom(
                # brush_image, brush_rotation, 1)
                brush_image = pygame.transform.rotate(
                    brush_image, brush_rotation)

        if not brush_image:
            # No-brush path: unchanged from upstream original. Colorkey-based
            # transparency, polygon drawn directly in `color`, blitted onto
            # canvas without blend flags. This path doesn't exhibit the
            # white-stencil leak because there's no brush to gap.
            shape_surface = pygame.Surface((radius*2, radius*2))
            shape_surface.set_colorkey(color_key)
            shape_surface.fill(color_key)
            shape_surface.set_alpha(alpha)

            if SHAPE_CIRCLE == shape:
                pygame.draw.circle(shape_surface, color,
                                   (radius, radius), radius, 0)
            else:
                poly = get_polygon(edges, radius, (radius, radius), rotation)
                pygame.draw.polygon(shape_surface, color, poly)

            canvas.blit(shape_surface,
                        (draw_pos[0] - radius, draw_pos[1] - radius))
            return

        # Brush path: SRCALPHA shape_surface; polygon defines an alpha mask
        # ONLY (the polygon never contributes color to the canvas); brush
        # supplies all visible color. No leak possible because there's no
        # stencil color present to leak through.
        #
        # NB: this also fixes a previously-masked rendering bug — in the
        # legacy implementation, the original `pygame.BLEND_MIN` was
        # functioning as the polygon-shape CLIPPING mechanism (min(brush,
        # white_stencil)=brush inside polygon; min(brush, black_bg)=black
        # outside, which got colorkey-masked). When F1's `--brush-blend-mode
        # opaque` skipped BLEND_MIN, the clipping mechanism disappeared and
        # strokes rendered as brush rectangles rather than polygons. The
        # explicit BLEND_RGBA_MULT mask below clips to polygon shape
        # regardless of blend_mode.
        shape_surface = pygame.Surface((radius*2, radius*2), pygame.SRCALPHA)
        shape_surface.fill((0, 0, 0, 0))

        mask_surface = pygame.Surface((radius*2, radius*2), pygame.SRCALPHA)
        mask_surface.fill((0, 0, 0, 0))
        if SHAPE_CIRCLE == shape:
            pygame.draw.circle(mask_surface, (255, 255, 255, 255),
                               (radius, radius), radius, 0)
        else:
            poly = get_polygon(edges, radius, (radius, radius), rotation)
            pygame.draw.polygon(mask_surface, (255, 255, 255, 255), poly)

        # rotation may have embiggened the brush image, so we want to
        # center it
        brush_image_size = brush_image.get_size()
        center_offset = (
                            (radius*2-brush_image_size[0])/2,
                            (radius*2-brush_image_size[1])/2
                        )
        shape_surface.blit(brush_image, center_offset)

        # BLEND_RGBA_MULT multiplies channel-wise: pixels inside the
        # polygon (mask alpha=255) preserve brush color & alpha;
        # pixels outside (mask alpha=0) become fully transparent.
        shape_surface.blit(
            mask_surface, (0, 0),
            special_flags=pygame.BLEND_RGBA_MULT,
        )

        # blend_mode controls how this stroke composites onto the canvas
        # at the existing pixels. 'min' = channel-wise minimum (darkens
        # overlapping strokes); 'opaque' / 'alpha' / None = normal alpha
        # blend (top stroke wins outright). See --brush-blend-mode CLI
        # flag and F1 PR for the original semantic intent.
        #
        # NB: pygame.BLEND_MIN does not respect SRCALPHA — transparent
        # source pixels (alpha=0) are treated as opaque black and would
        # min canvas pixels to (0,0,0). To prevent that, we clip the
        # BLEND_MIN blit to the polygon's bounding sub-region of
        # shape_surface AND additionally use the mask to short-circuit
        # outside the polygon. The simplest pygame-safe path: in 'min'
        # mode, do per-pixel BLEND_MIN via surfarray under the mask.
        blend_mode = self.params.get('blend_mode') or 'min'
        if blend_mode == 'min':
            # numpy-side per-pixel min, masked by the polygon's alpha.
            # Avoids pygame.BLEND_MIN's alpha-ignorance pitfall.
            sx, sy = (draw_pos[0] - radius, draw_pos[1] - radius)
            cw, ch = canvas.get_size()
            x0, y0 = max(sx, 0), max(sy, 0)
            x1, y1 = min(sx + radius*2, cw), min(sy + radius*2, ch)
            if x0 < x1 and y0 < y1:
                # Source sub-rect (in shape_surface coords)
                srcx0, srcy0 = x0 - sx, y0 - sy
                srcx1, srcy1 = x1 - sx, y1 - sy
                canvas_arr = pygame.surfarray.pixels3d(canvas)
                mask_arr = pygame.surfarray.pixels_alpha(mask_surface)
                shape_arr = pygame.surfarray.pixels3d(shape_surface)
                # Apply per-stroke alpha by scaling mask intensity.
                stroke_alpha = alpha / 255.0 if alpha is not None else 1.0
                m = (mask_arr[srcx0:srcx1, srcy0:srcy1] / 255.0
                     * stroke_alpha)
                dst_slice = canvas_arr[x0:x1, y0:y1]
                src_slice = shape_arr[srcx0:srcx1, srcy0:srcy1]
                # Where mask is full: min(canvas, brush); where mask is
                # zero: keep canvas unchanged. Smooth blend across edges.
                import numpy as np
                m3 = m[..., None]
                mn = np.minimum(dst_slice, src_slice)
                dst_slice[...] = (mn * m3 + dst_slice * (1.0 - m3)).astype(
                    dst_slice.dtype
                )
                # Release surfarray locks
                del canvas_arr, mask_arr, shape_arr
        else:
            # opaque / alpha / None: per-pixel alpha blend respects the
            # mask naturally (transparent SRCALPHA pixels don't draw).
            shape_surface.set_alpha(alpha)
            canvas.blit(shape_surface,
                        (draw_pos[0] - radius, draw_pos[1] - radius))


class CanvasActionDrawText(CanvasAction):
    """Draw text on the canvas"""
    opcode = "t"

    def run(self, canvas, origin=(0, 0)):
        alpha = self.params.get('alpha', 220)
        color = self.params.get('color')
        pos = self.params.get('pos')
        radius = self.params.get('radius')
        rotation = self.params.get('rotation')
        text = self.params.get('text')
        ox, oy = origin

        font_size = estimate_font_size(text, radius)

        # tmp_font = pygame.freetype.SysFont('Helvetica', font_size)
        tmp_font = pygame.freetype.SysFont('Arial Black', font_size)
        if alpha:
            color = color + (alpha,)
            # text_surface.set_alpha(alpha)
            text_surface, _ = tmp_font.render(text, color)

        # print(f"letters: {len(text)} radius: {radius} outputFontSize: "
        #      f"{font_size} surface_width: {text_surface.get_size()[0]}")

        if rotation:
            text_surface = pygame.transform.rotozoom(text_surface, rotation, 1)

        canvas.blit(text_surface, (pos[0] - radius - ox, pos[1] - radius - oy))


def estimate_font_size(text, radius):
    # print(f"text: {text} radius: {radius}")
    text_len = len(text)
    return max(int(radius / max(text_len, 4))*4, 4)


all_canvas_actions = [CanvasActionDrawShape, CanvasActionDrawText]
