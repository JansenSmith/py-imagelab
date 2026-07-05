"""Mutation utilities for imagelab

   Various utilities for altering a canvas based on different algorithms, etc

   class MutationEvent:

    An event that can be applied to any canvas.
    It keeps track of the Mutator who should run this event.

    a) We tell the Mutator to draw a random shape on our canvas.
       These are the mutatorInstructions
    b) Mutator draws a red circle of radius 3 at x:20, y:20 and then a 2px
       blue line from x:1 - y:33 .  These are mutatorActions

    Storing both gives us the ability to send the same instructions,
    or replay the events exactly.

    This class should remain light weight

    #surface = None
    name = None

    # Input to the Mutator. simple dictionary of instrctions.
    # Might be slimmer to use op-codes, though.
    # When saving, we may opt to only save the actions, actually,
    # since we may not care how
    #They were achieved
    #mutatorInstructions = []

    #Output from the Mutator.  A sequence of actions taken against the canvas
    actions = []

    def __init__(self, name, actions):
        self.name = name
        self.actions = actions

    #def serialize(self):
    #    ret = self.mutatorName, self.mutatorActions
    #    return pickle.dumps(ret)
"""
import os
import numpy as np
from multiprocessing.shared_memory import SharedMemory
from imagelab import rng
from imagelab.constants import (
    SHAPE_CIRCLE,
    SHAPE_POLYGON,
    SHAPE_SETTINGS_ARRAY,
    POLYGON_NUM_SIDES,
)
from imagelab.drawing import draw_random_circle, draw_random_polygon, draw_random_word
from imagelab.compare import best_match, euclidean_score, lab_score


def _pygame_init():
    """Worker process initializer: configure headless SDL and init pygame."""
    os.environ.setdefault('SDL_VIDEODRIVER', 'dummy')
    os.environ.setdefault('SDL_AUDIODRIVER', 'dummy')
    import pygame
    pygame.init()
    # smoothscale and convert_alpha require a display surface to exist even on
    # the dummy driver; set a minimal mode to satisfy that requirement.
    pygame.display.set_mode((1, 1))


def _child_worker(args):
    """Generate a batch of children in one worker, return the best (score, opcode, params).
    Reads clip_base and target from shared memory — no large array transfers.
    """
    (seed_chunk,
     clip_shm_name, clip_shape, clip_dtype,
     target_shm_name, target_shape, target_dtype,
     shape, words, max_radius, min_radius, radius, alpha, color, color_key,
     pos, max_edges, brush_arrays, clip_rect_tuple, surface_origin,
     score_fn_name) = args

    # Imports are deferred to function scope because spawn workers start with a
    # clean interpreter — top-level imports in the parent are not inherited.
    import time
    import pygame
    import numpy as np
    from multiprocessing.shared_memory import SharedMemory

    clip_shm = SharedMemory(name=clip_shm_name)
    clip_base_array = np.ndarray(clip_shape, dtype=clip_dtype, buffer=clip_shm.buf)

    target_shm = SharedMemory(name=target_shm_name)
    target_array = np.ndarray(target_shape, dtype=target_dtype, buffer=target_shm.buf)

    brush_images = [pygame.surfarray.make_surface(a) for a in brush_arrays] if brush_arrays else None
    clip_rect = pygame.Rect(*clip_rect_tuple) if clip_rect_tuple else None

    # Pre-process target once for all children in this batch
    if score_fn_name == 'lab':
        import cv2
        def to_lab(arr):
            return cv2.cvtColor(
                np.ascontiguousarray(arr.astype(np.uint8).transpose(1, 0, 2)),
                cv2.COLOR_RGB2LAB
            ).astype(float)
        target_pre = to_lab(target_array)
        def _score(result_array):
            diff = target_pre - to_lab(result_array)
            return float(np.average(np.sqrt(np.sum(diff * diff, axis=2))))
    else:
        target_pre = target_array.astype(np.int32)
        def _score(result_array):
            diff = result_array.astype(np.int32) - target_pre
            return float(np.average(np.sqrt(np.sum(diff * diff, axis=2))))

    best_score = float('inf')
    best_opcode = None
    best_params = None
    start_time = time.perf_counter()

    for seed in seed_chunk:
        rng.seed(seed)
        img = pygame.surfarray.make_surface(clip_base_array).copy()

        if words:
            result = draw_random_word(
                img, words, None, clip_rect, max_radius, radius,
                alpha, color, color_key, pos, brush_images,
                surface_origin=surface_origin,
                min_radius=min_radius,
            )
        elif shape in POLYGON_NUM_SIDES or shape == SHAPE_POLYGON:
            num_sides = None if shape == SHAPE_POLYGON else POLYGON_NUM_SIDES[shape]
            result = draw_random_polygon(
                img, num_sides, None, clip_rect, max_radius, radius,
                alpha, color, color_key, pos, max_edges, brush_images,
                surface_origin=surface_origin,
                min_radius=min_radius,
            )
        else:
            result = draw_random_circle(
                img, clip_rect, max_radius, radius, alpha, color,
                color_key, pos, brush_images,
                surface_origin=surface_origin,
                min_radius=min_radius,
            )

        score = _score(pygame.surfarray.array3d(img))
        if score < best_score:
            best_score = score
            p = dict(result.params)
            if p.get('brush_image') is not None:
                p['brush_image'] = pygame.surfarray.array3d(p['brush_image'])
            best_opcode = result.opcode
            # pygame.Surface can't be pickled across process boundaries;
            # convert to numpy array. brush_sample_rect is already a plain tuple.
            best_params = p

    elapsed = time.perf_counter() - start_time
    clip_shm.close()
    target_shm.close()

    return best_score, best_opcode, best_params, elapsed


def mutator(mutator_fn):
    """Take some arguments and return a surface and actions that were applied
    to it"""

    def wrap(*__args, **__kw):
        actions, surface = mutator_fn(*__args, **__kw)
        # return MutationEvent(mutator_fn.__name__, actions), surface
        return actions, surface, mutator_fn.__name__

    return wrap


@mutator
def mutate_null(surface):
    """Do nothing. Used to test pipeline"""
    return [], surface


@mutator
def mutate_evolve(surface, params):
    """Spawn child surfaces and return the one closes to the target"""
    clip_rect = params.get("clip_rect")
    shape = params.get("shape", SHAPE_CIRCLE)
    max_radius = params.get("max_radius", (clip_rect.width / 2))
    min_radius = params.get("min_radius", 1)
    radius = params.get("radius")
    max_edges = params.get("max_edges", 8)
    alpha = params.get("alpha", 180)
    color = params.get("color")
    color_key = params.get("color_key", (0, 0, 0))
    children = params.get("children", 80)
    pos = params.get("pos")
    target = params.get("target")
    child_callback = params.get("child_callback", None)
    words = params.get("words", None)
    brush_images = params.get("brush_images", None)
    score_fn = params.get("score_fn", None)
    pool = params.get("pool", None)

    if pool is not None:
        import pygame
        from imagelab.canvas import CanvasAction

        resolved_shape = shape if shape is not None else SHAPE_SETTINGS_ARRAY[int(rng.integers(0, 6))]
        score_fn_name = 'lab' if score_fn is lab_score else 'euclidean'

        if clip_rect is not None:
            clipped_rect = surface.get_rect().clip(clip_rect)
            clip_base = surface.subsurface(clipped_rect).copy()
            surface_origin = clipped_rect.topleft
            target_arr = pygame.surfarray.array3d(target)
            target_clipped = np.ascontiguousarray(
                target_arr[clipped_rect.left:clipped_rect.right,
                           clipped_rect.top:clipped_rect.bottom]
            )
        else:
            clip_base = surface
            surface_origin = (0, 0)
            target_clipped = np.ascontiguousarray(pygame.surfarray.array3d(target))

        clip_base_array = np.ascontiguousarray(pygame.surfarray.array3d(clip_base))

        # Write shared arrays once; workers read without copying
        clip_shm = SharedMemory(create=True, size=clip_base_array.nbytes)
        np.ndarray(clip_base_array.shape, dtype=clip_base_array.dtype,
                   buffer=clip_shm.buf)[:] = clip_base_array

        target_shm = SharedMemory(create=True, size=target_clipped.nbytes)
        np.ndarray(target_clipped.shape, dtype=target_clipped.dtype,
                   buffer=target_shm.buf)[:] = target_clipped

        brush_arrays = [pygame.surfarray.array3d(b) for b in brush_images] if brush_images else []
        clip_rect_tuple = (clip_rect.x, clip_rect.y, clip_rect.width, clip_rect.height) if clip_rect else None
        all_seeds = [int(rng.integers(0, 2**31)) for _ in range(children)]

        # One task per worker, each handles a chunk of children internally
        n_workers = min(params.get("workers", 1), children)
        chunk_size = (children + n_workers - 1) // n_workers
        seed_chunks = [all_seeds[i:i + chunk_size] for i in range(0, children, chunk_size)]

        args_list = [
            (chunk,
             clip_shm.name, clip_base_array.shape, str(clip_base_array.dtype),
             target_shm.name, target_clipped.shape, str(target_clipped.dtype),
             resolved_shape, words, max_radius, min_radius, radius, alpha, color, color_key,
             pos, max_edges, brush_arrays, clip_rect_tuple, surface_origin, score_fn_name)
            for chunk in seed_chunks
        ]

        try:
            # map_async + pump loop keeps the pygame event queue alive while
            # workers compute. pool.map() blocks the main thread entirely,
            # causing the OS to mark the window as not responding on long gens.
            async_result = pool.map_async(_child_worker, args_list)
            while not async_result.ready():
                pygame.event.pump()
                pygame.time.wait(50)
            raw_results = async_result.get()
        finally:
            clip_shm.close()
            clip_shm.unlink()
            target_shm.close()
            target_shm.unlink()

        # Side-channel: the @mutator decorator fixes the return shape of this
        # function to (actions, surface, name), leaving no slot for extra data.
        # Writing into the caller-supplied params dict is the escape hatch.
        params['_parallel_stats'] = {
            'worker_times': [r[3] for r in raw_results],
        }

        # Contender baseline: current surface unchanged
        contender_score = (score_fn if score_fn else euclidean_score)(target_clipped, clip_base_array)

        best_score = contender_score
        best_result = None
        for worker_score, opcode, result_params, _ in raw_results:
            if worker_score < best_score:
                best_score = worker_score
                best_result = (opcode, result_params)

        shape_actions = []
        if best_result is not None:
            opcode, result_params = best_result
            p = dict(result_params)
            # Reconstruct brush_image from numpy array (was serialized for IPC).
            if p.get('brush_image') is not None:
                p['brush_image'] = pygame.surfarray.make_surface(p['brush_image'])
            action = CanvasAction.deserialize([opcode, p])
            shape_actions = [action]

            # Regenerate winning surface by replaying the action — no array transfer
            winner_surf = clip_base.copy()
            action.run(winner_surf, origin=surface_origin)

            if clip_rect is not None:
                clipped_rect = surface.get_rect().clip(clip_rect)
                surface = surface.copy()
                surface.blit(winner_surf, clipped_rect.topleft)
            else:
                surface = winner_surf

        return shape_actions, surface

    # TODO: move above PARAMS into **kwargs and set default vaules via
    # new_params = defaults.copy()
    # new_params.update(params)
    # and maybe filter:
    # whitelist = ['color', clip_rect', ...]
    # cleaned_params = {key: value for key, value in dict.items() if key
    #   in whitelist}
    morph_results = morph_surface(
        surface,
        children,
        clip_rect,
        shape,
        words,
        max_radius,
        radius,
        alpha,
        color,
        color_key,
        pos,
        max_edges,
        child_callback=child_callback,
        brush_images=brush_images,
        min_radius=min_radius,
    )

    # get the list of surfaces
    # surface_candidates = morph_results[2]
    shape_actions, winning_surface, score = best_match(
        target, surface, morph_results, clip_rect,
        score_fn=score_fn,
        candidates_are_clipped=(clip_rect is not None),
    )

    # When clip_rect is active, candidates are clip-sized surfaces.
    # Composite the winner back onto a full canvas copy.
    if clip_rect is not None:
        if shape_actions:
            clipped_rect = surface.get_rect().clip(clip_rect)
            surface = surface.copy()
            surface.blit(winning_surface, clipped_rect.topleft)
        # else: no improvement, surface unchanged
    else:
        surface = winning_surface

    try:
        # get the shape instructions that made the best match
        # shape_actions.append(morph_results[1][id(surface)])
        pass
    except KeyError:
        # no shape showed an improvement, let's note that
        #        shape_actions.append(None)
        pass
    return shape_actions, surface


""" Utility functions
   if your mutator does something special, consider putting it here so all of
   the components can be used by other mutators
"""


def morph_surface(
    canvas,
    count=1,
    clip_rect=None,
    shape=SHAPE_CIRCLE,
    words=None,
    max_radius=0,
    radius=None,
    alpha=None,
    color=None,
    color_key=(0, 0, 0),
    pos=None,
    max_edges=None,
    child_callback=None,
    brush_images=None,
    min_radius=1,
):
    """
    Takes an origin surface (canvas) and creates multiple (count) children.
    Then, randomly plots a 'shape' of random size color and position.
    If return_actions is true, it will return an array containing the
    metadata used to make each child indexed by id(child)
    """
    i = 0

    if shape is None:
        shape = SHAPE_SETTINGS_ARRAY[rng.integers(0, 6)]

    # When clip_rect is active, draw on clip-sized surfaces to avoid copying
    # the full canvas for every child. One full copy is made by mutate_evolve
    # only for the winning child.
    if clip_rect is not None:
        clipped_rect = canvas.get_rect().clip(clip_rect)
        clip_base = canvas.subsurface(clipped_rect).copy()
        surface_origin = clipped_rect.topleft
    else:
        clip_base = None
        surface_origin = (0, 0)

    while i < count:
        i += 1

        img = clip_base.copy() if clip_rect is not None else canvas.copy()

        if words:
            result = draw_random_word(
                img, words, None, clip_rect,
                max_radius, radius, alpha, color, color_key, pos, brush_images,
                surface_origin=surface_origin,
                min_radius=min_radius,
            )
        elif shape in POLYGON_NUM_SIDES or shape == SHAPE_POLYGON:
            numSides = None if shape == SHAPE_POLYGON else POLYGON_NUM_SIDES[shape]
            result = draw_random_polygon(
                img, numSides, None, clip_rect,
                max_radius, radius, alpha, color, color_key, pos, max_edges, brush_images,
                surface_origin=surface_origin,
                min_radius=min_radius,
            )
        else:
            result = draw_random_circle(
                img, clip_rect,
                max_radius, radius, alpha, color, color_key, pos, brush_images,
                surface_origin=surface_origin,
                min_radius=min_radius,
            )

        if child_callback:
            child_callback(i, result, img)

        yield [img, [result]]
