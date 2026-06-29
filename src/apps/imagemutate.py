"""CLI for imagemutate App.

Produce a target image by randomly mutating a base canvas
"""
import json
import time
import datetime
import pygame
import pygame.freetype  # explicit submodule import — pygame ≥2.6 does not auto-load freetype
import os
import cv2
import multiprocessing
from imagelab import mutation, rng
from imagelab.mutation import _pygame_init
from imagelab.compare import STRATEGIES
from imagelab.geometry import get_random_clip_rect
from imagelab.geometry import resize_with_pad
from imagelab.compare import match_score, get_match_percentage
from imagelab.canvas import Canvas

OUTPUT_MODE_IMAGE = 0
OUTPUT_MODE_INSTRUCTIONS = 1

DEFAULT_DISPLAY_BG_COLOR = (3, 3, 100)
DEFAULT_DISPLAY_HILIGHT_COLOR = (200, 200, 100)
DEFAULT_DISPLAY_HILIGHT_SUB_COLOR = (100, 100, 40)
DEFAULT_DISPLAY_HILIGHT_WIDTH = 3
DEFAULT_DISPLAY_MARGIN_PX = 20
DEFAULT_BIT_DEPTH = 32

DEFAULT_CHILDREN = 100
DEFAULT_SHAPE = 'circle'
DEFAULT_RADIUS = 20
DEFAULT_IMAGE_SAVE_EXT = 'png'
DEFAULT_SAVE_TEMPLATE = "%PREFIX-%FRAME%CHILDREN%GENERATION"

# consider scaling based on display? nt(450*h/768))
STATS_FONT_SIZE = 32
HUD_FONT_SIZE = 16
HUD_PADDING = 4
# STATS_FONT_PATH = "./assets/fonts/RisingSun/RisingSun-Regular.ttf"
STATS_FONT_PATH = os.path.join(os.path.dirname(__file__), "..", "..", "assets", "fonts", "luculent", "luculent.ttf")

MOVIE_EXTENSIONS = ('.mp4', '.mpeg')


def number_length(num):
    return len(str(num))


def cvimage_to_pygame(image):
    """Convert cvimage into a pygame image"""
    return pygame.image.frombuffer(
        image.tostring(),
        image.shape[1::-1],
        # image.shape[:2],
        # "RGB"
        "BGR"
    )


def get_new_surface(size, color=None):
    surface = pygame.Surface(size)
    if color is not None:
        surface.fill(color)

    return surface


def is_movie_file(path):
    return path.endswith(MOVIE_EXTENSIONS)


class App:
    """
        Evolve the target image by finding the best child over a number of
        generations
    """
    options = {}
    stats = {}
    running = False

    # image processing
    # The target image we are trying to match
    target_surface = None
    canvas = None

    brush_surfaces = None

    current_run = 0
    current_generation = 0
    current_child = 0

    # for movie_mode, initialized when rendering a movie
    current_frame = None
    target_movie = None

    # Display Settings
    # the pygame screen display
    screen = None

    # base background image
    background = None

    display_bg_color = None
    display_margin_px = None
    bit_depth = None

    # should we create all children for a given generation in the same zone
    # for faster comparison speed?
    morph_clipped_sections = True
    # how large is the zome in relation to the radius
    # must be at least two to allow for full radius
    clipped_section_scale = 4
    # after how many ticks should we find a new zone?
    # having a value greater than one results in multiple generations "working"
    # in the same area of the canvas
    clip_change_rate = 1

    stats_font = None
    hud_font = None
    _evolution_complete = False
    _final_match_pct = None
    _current_radius = None
    _current_children = None

    # internal house-keeping
    _process_start_time = 0
    _clock_start_time = 0

    _tick_process_start_time = 0
    _tick_clock_start_time = 0

    _frame_process_start_time = 0
    _frame_clock_start_time = 0

    _last_flip_time = 0
    _cached_scaled_size = None
    _cached_screen_size = None

    _movie_mode = False
    _pool = None
    _last_worker_times = []

    # current clip rectangle being used to optimize comparison
    _clip_rect = None
    # current rectangle of laste-evaluated child
    _clip_sub_rect = None

    _show_highlights = False
    _show_info = False

    def __init__(self, options=None):
        if options is not None:
            self.set_options(options)
        self.print("starting app")

    def __del__(self):
        self.print("exiting")
        pygame.quit()

    def set_options(self, options):
        """Set the config options for this instance
        Positional arguments:
        options -- a simple dict of options used by this app
        """
        self.options = options

    def run(self):
        """Run this app """
        seed = self.options.get('seed')
        if seed is None:
            seed = int(rng.integers(0, 1000000))
            self.options['seed'] = seed
        rng.seed(seed)
        self.initialize_display()
        self.initialize_target()
        self.initialize_profiler()
        self.running = True

        workers = self.options.get('workers', 1)
        if workers > 1:
            # 'spawn' is required: pygame initialises SDL in the parent process,
            # and forked children inherit that state in a broken way on Mac/Linux.
            # spawn starts a clean interpreter each time, safe cross-platform.
            ctx = multiprocessing.get_context('spawn')
            self._pool = ctx.Pool(workers, initializer=_pygame_init)

        try:
            runs = self.options.get('runs', 1)
            complete = False
            self.current_run = 0
            while not complete and self.running:
                if(self._movie_mode):
                    self.evolve_movie_mode()
                else:
                    self.evolve_image_mode()
                self.current_run += 1
                if self.current_run >= runs:
                    complete = True
        finally:
            if self._pool is not None:
                self._pool.terminate()
                self._pool.join()
                self._pool = None

    def initialize_display(self):
        """Set up the display"""
        if self.options.get('no_display', False):
            return

        self.print("initializing display")

        self.display_bg_color = DEFAULT_DISPLAY_BG_COLOR
        self.display_highlight_color = DEFAULT_DISPLAY_HILIGHT_COLOR
        self.display_highlight_sub_color = DEFAULT_DISPLAY_HILIGHT_SUB_COLOR
        self.display_highlight_width = DEFAULT_DISPLAY_HILIGHT_WIDTH
        self.display_margin_px = DEFAULT_DISPLAY_MARGIN_PX

        pygame.init()

        self.stats_font = pygame.freetype.Font(
            os.path.normpath(STATS_FONT_PATH),
            STATS_FONT_SIZE
        )
        self.hud_font = pygame.freetype.Font(
            os.path.normpath(STATS_FONT_PATH),
            HUD_FONT_SIZE
        )

        if self.options.get('iconify', False):
            pygame.display.iconify()

    def initialize_target(self):
        """ Look at the target image or movie
            and set up the config accordingly """
        self.bit_depth = DEFAULT_BIT_DEPTH

        if is_movie_file(self.options.get('target_path')):
            self.initialize_movie_settings()
        else:
            self.initialize_image_settings()

        # allow for two side-by-side comparison images
        screen_width = (
            self.target_surface.get_width() * 2
        ) + (self.display_margin_px * 3)

        screen_height = (
            self.target_surface.get_height() + self.display_margin_px * 2
        )

        screenrect = pygame.Rect(0, 0, screen_width, screen_height)

        self.screen = pygame.display.set_mode(
            (600, 450),
            pygame.RESIZABLE, self.bit_depth
        )

        self.print(f"setting display {screenrect.size} {self.bit_depth}bit")
        self.background = pygame.Surface(screenrect.size)

    def initialize_image_settings(self):
        self.print(f"loading target image: {self.options.get('target_path')}")
        self.target_surface = pygame.image.load(
            self.options.get('target_path')
        )
        self.target_surface.convert(self.bit_depth)

        if(self.options.get('brush_images')):
            self.print(
                f"loading brush images {self.options.get('brush_images')}"
            )
            self.brush_surfaces = []
            for surface_path in self.options.get('brush_images', []):
                brush_surface = pygame.image.load(surface_path)
                brush_surface.convert(self.bit_depth)
                self.brush_surfaces.append(brush_surface)

    def initialize_movie_settings(self):
        self._movie_mode = True
        self.target_movie = cv2.VideoCapture(self.options.get('target_path'))
        width = self.target_movie.get(cv2.CAP_PROP_FRAME_WIDTH)
        height = self.target_movie.get(cv2.CAP_PROP_FRAME_HEIGHT)
        self.target_surface = pygame.Surface((width, height))
        self.total_frames = self.target_movie.get(7)
        self.print(
            f"loaded movie {width}x{height} @ {self.total_frames} frames"
        )
        self.target_surface.convert(self.bit_depth)

    def initialize_profiler(self):
        self.stats = {}
        process_time = time.process_time()
        clock_time = time.perf_counter()
        self._process_start_time = process_time
        self._clock_start_time = clock_time
        self.stats["total_process_time"] = 0
        self.stats["total_clock_time"] = 0
        self.stats["average_generation_process_time"] = 0
        self.stats["average_generation_clock_time"] = 0
        self.stats["current_generation_process_time"] = 0
        self.stats["current_generation_clock_time"] = 0
        if self._movie_mode:
            self.stats["average_frame_process_time"] = 0
            self.stats["average_frame_clock_time"] = 0
            self.stats["current_frame_process_time"] = 0
            self.stats["current_frame_clock_time"] = 0

    def evolve_image_mode(self):
        self.print("starting image evolution")
        canvas_surface = get_new_surface(
            self.target_surface.get_rect().size,
            self.display_bg_color
        )
        self.evolve(canvas_surface)

    def evolve_movie_mode(self):
        self.print("starting movie evolution")
        self.current_frame = self.options.get('frame_start', 0)

        complete = False

        while not complete and self.running:
            self.handle_frame_start()
            self.handle_frame()
            self.current_frame += 1
            if self.movie_complete():
                complete = True

    def handle_frame_start(self):
        process_time = time.process_time()
        clock_time = time.perf_counter()
        self._frame_process_start_time = process_time
        self._frame_clock_start_time = clock_time

    def handle_frame(self):
        self.print(
            f"evolving frame {self.current_frame+1}/{self.total_frames}"
        )
        self.target_movie.set(cv2.CAP_PROP_POS_FRAMES, self.current_frame)
        success, frame = self.target_movie.read()
        self.target_surface = cvimage_to_pygame(frame)
        canvas_surface = get_new_surface(
            self.target_surface.get_rect().size,
            self.display_bg_color
        )
        self.evolve(canvas_surface)

    def handle_frame_end(self):
        pass

    def evolve(self, canvas_surface):
        """ Run the main series of mutations """
        self.print("starting evolution")
        complete = False
        tick = 0

        self.canvas = Canvas(canvas_surface)

        # TODO: make a reset-evolution method, to set current_gen, etc to zero
        # otherwise we do not reset things while in movie mode

        self.update_display()

        self.current_generation = 0

        try:
            while not complete and self.running:
                """ Make a random rect.  Allow room to start a circle off screen
                    The purpose of this is so that we only create children inside a
                    common subdivision of the canvas.  This allows quicker
                    comparison, since it's not against the entire canvas.
                """
                # start of tick house-keeping
                self.handle_tick_start()
                # do the actual work for this tick

                try:
                    self.handle_evolution_tick(tick)
                except SystemExit:
                    self.running = False

                # output results
                self.update_display()
                # poll for input
                try:
                    self.handle_events()
                except SystemExit:
                    self.running = False

                # end of tick house-keeping
                self.handle_tick_end()
                if self.evolution_complete():
                    complete = True

                tick += 1
        finally:
            self.print_profiler()

        if complete:
            self._evolution_complete = True
            self.update_display()
            if self.options.get('save_on_exit', False):
                self.save()
            if not self.options.get('close_on_exit', False) and \
                    not self.options.get('no_display', False):
                self._run_done_loop()

    def handle_tick_start(self):
        """ Start a new image evolution cylcle (begin a tick) """
        self.current_generation += 1
        process_time = time.process_time()
        clock_time = time.perf_counter()
        self._tick_process_start_time = process_time
        self._tick_clock_start_time = clock_time

    def update_profiler(self):
        process_time = time.process_time()
        clock_time = time.perf_counter()
        gen_clock_duration = clock_time - self._tick_clock_start_time
        gen_process_duration = process_time - self._tick_process_start_time
        total_clock_duration = clock_time - self._clock_start_time
        total_process_duration = process_time - self._process_start_time

        self.stats["total_process_time"] = total_process_duration
        self.stats["total_clock_time"] = total_clock_duration

        self.stats["average_generation_process_time"] = (
            (
                self.stats["average_generation_process_time"] *
                (self.current_generation - 1) + gen_process_duration
            )/(self.current_generation)
        )

        self.stats["current_generation_process_time"] = gen_process_duration
        self.stats["current_generation_clock_time"] = gen_clock_duration

        self.stats["average_generation_clock_time"] = (
            (
                self.stats["average_generation_clock_time"] *
                (self.current_generation - 1) + gen_clock_duration
            ) / (self.current_generation)
        )
        self.stats["process_time_per_child"] = \
            self.stats["total_process_time"] / (
                self.current_generation *
                self.options.get('children', DEFAULT_CHILDREN)
        )

        self.stats["clock_time_per_child"] = self.stats["total_clock_time"] / \
            (
                self.current_generation *
                self.options.get('children', DEFAULT_CHILDREN)
        )

        if self._last_worker_times:
            n = len(self._last_worker_times)
            utilization = (
                sum(self._last_worker_times) / (gen_clock_duration * n) * 100
            ) if gen_clock_duration > 0 else 0
            self.stats["workers_used"] = n
            self.stats["worker_utilization"] = utilization
            self.stats["avg_worker_utilization"] = (
                (self.stats.get("avg_worker_utilization", 0) *
                 (self.current_generation - 1) + utilization)
                / self.current_generation
            )

    def print_profiler(self):
        parallel = self.options.get('workers', 1) > 1

        print("======================================")
        if self._movie_mode:
            print(f"====== Frame {self.current_frame} / \
            {self.total_frames} ======")

        if self.options.get('runs', 1) != 1:
            print(f"--- run: {self.current_generation}", end='')

        print(f"--- gen: {self.current_generation}", end='')
        if self.options.get('gen_stop', -1) >= 0:
            print(f"/{self.options.get('gen_stop')}", end='')

        if parallel:
            print(f" workers: {self.options.get('workers')} ---")
        else:
            print(
                f" child: {self.current_child}/{self.options.get('children')} ---"
            )

        process_time_label = "(main thread only)" if parallel else ""
        process_time_keys = {
            "total_process_time",
            "average_generation_process_time",
            "current_generation_process_time",
            "process_time_per_child",
        }
        for val in self.stats:
            suffix = f" {process_time_label}" if parallel and val in process_time_keys else ""
            print(f"{val}{suffix}:\t{self.stats[val]}")
        print("-------------------")

        """ we could calculate this every tick, but it would add cost """
        score = match_score(self.target_surface, self.canvas.surface)
        self._final_match_pct = get_match_percentage(score)
        print(f"match percentage: {self._final_match_pct}")
        print("-------------------")

    def _format_elapsed(self, seconds):
        seconds = int(seconds)
        m, s = divmod(seconds, 60)
        h, m = divmod(m, 60)
        if h:
            return f"{h}:{m:02d}:{s:02d}"
        return f"{m}:{s:02d}"

    def render_hud(self, surface):
        # Must render on self.screen, not on scaled_screen. scaled_screen is a
        # temporary surface whose pixel dimensions change with window size — text
        # blitted there would be stretched. self.screen is always 1:1 pixels.
        if self.hud_font is None:
            return
        screen_w, screen_h = surface.get_size()
        line_h = HUD_FONT_SIZE + HUD_PADDING * 2
        bar_h = line_h * 2 + HUD_PADDING

        bar = pygame.Surface((screen_w, bar_h), pygame.SRCALPHA)
        bar.fill((0, 0, 0, 160))

        if self._evolution_complete:
            elapsed = self._format_elapsed(self.stats.get("total_clock_time", 0))
            match_str = f"{self._final_match_pct:.1f}%" if self._final_match_pct is not None else "?"
            status = f"Done — {elapsed}  ·  match: {match_str}  (Enter/S: save  ·  Esc: close)"
        else:
            gen_stop = self.options.get('gen_stop', -1)
            gen_str = f"gen: {self.current_generation}"
            if gen_stop > 0:
                gen_str += f"/{gen_stop}"

            adaptive = self.options.get('adaptive_cheat_mode', False)
            base_r = self.options.get('radius')
            base_c = self.options.get('children', DEFAULT_CHILDREN)
            cur_r = self._current_radius or base_r
            cur_c = self._current_children or base_c

            r_str = f"{cur_r}→{base_r}" if adaptive and cur_r != base_r else str(base_r)
            c_str = f"{cur_c}→{base_c}" if adaptive and cur_c != base_c else str(base_c)

            if self.options.get('workers', 1) > 1:
                status = f"{gen_str}  ·  workers: {self.options.get('workers')}  ·  children: {c_str}  ·  maxR: {r_str}"
            else:
                status = f"{gen_str}  ·  child: {self.current_child}/{c_str}  ·  maxR: {r_str}"

        hotkeys = (
            "Esc: quit  ·  Space: stats dump  ·  Enter/S: snapshot  ·  A: save JSON"
            "  ·  H: highlights  ·  ←→: children  ·  ↑↓: radius"
        )

        status_surf, _ = self.hud_font.render(status, (220, 220, 220))
        hotkey_surf, _ = self.hud_font.render(hotkeys, (150, 150, 150))

        bar.blit(status_surf, (HUD_PADDING, HUD_PADDING))
        bar.blit(hotkey_surf, (HUD_PADDING, line_h + HUD_PADDING))
        surface.blit(bar, (0, screen_h - bar_h))

    def _run_done_loop(self):
        while True:
            for event in pygame.event.get():
                if event.type == pygame.QUIT:
                    return
                elif event.type == pygame.KEYDOWN:
                    if event.key == pygame.K_ESCAPE:
                        return
                    elif event.key in (pygame.K_RETURN, pygame.K_s):
                        print(f"saved: {self.save()}")
                    elif event.key == pygame.K_a:
                        print(f"saved: {self.save(OUTPUT_MODE_INSTRUCTIONS)}")
                elif event.type == pygame.VIDEORESIZE:
                    pass
            self.update_display()
            pygame.time.wait(50)

    def print(self, output_string):
        if self.options.get('verbose', False):
            print(output_string)

    def update_display_minimal(self):
        # update display but in a very light weight way...
        # used for child iterations which need to be as fast as possible

        tmp_bg = self.background.copy()

        if self._show_highlights and self._clip_sub_rect:
            # highlight the working section
            pygame.draw.rect(
                tmp_bg,
                self.display_highlight_sub_color,
                self._clip_sub_rect.move(
                    self.display_margin_px, self.display_margin_px
                ),
                self.display_highlight_width
            )

        if self._show_highlights and self._clip_rect:
            # highlight the working section
            pygame.draw.rect(
                tmp_bg,
                self.display_highlight_color,
                self._clip_rect.move(
                    self.display_margin_px, self.display_margin_px
                ),

                self.display_highlight_width
            )

        screen_size = self.screen.get_size()
        if screen_size != self._cached_screen_size:
            self._cached_screen_size = screen_size
            self._cached_scaled_size = resize_with_pad(tmp_bg.get_size(), screen_size)
        scaled_size = self._cached_scaled_size

        scaled_screen = pygame.transform.smoothscale(tmp_bg, scaled_size)

        if self._show_info:
            self.render_stats(
                scaled_screen,
                (self.display_margin_px*2, self.display_margin_px*2)
            )

        origin = (
            screen_size[0]/2 - scaled_size[0]/2,
            screen_size[1]/2 - scaled_size[1]/2
        )
        self.screen.blit(scaled_screen, origin)
        self.render_hud(self.screen)
        pygame.display.update()

    def render_stats(self, surface, pos):
        # fontSurface = self.font.render(("Children: %03d - Frame: %09d -
        # Generation: %06d" % (self.options.get('children'), useFrame,
        # self.generation)), True, (0,200,100))
        line_1 = ""
        if self._movie_mode:
            line_1 = f"frame: {self.current_frame}"

        if self.options.get('runs', 1) != 1:
            line_1 = f"{line_1}run: {self.current_run}"

        line_1 = f"{line_1}gen: {self.current_generation}"

        if self.options.get('gen_stop', -1) > 0:
            line_1 = f"{line_1}/{self.options.get('gen_stop', -1)}"

        if self.options.get('workers', 1) > 1:
            line_1 = f"{line_1} workers: {self.options.get('workers')}"
        else:
            children = self.options.get('children')
            children_length = number_length(children)
            line_1 = f"{line_1} child: {self.current_child:0{children_length}d}/{children}"

        line_2 = f"maxR: {self.options.get('radius')}"

        line_1_img, _ = self.stats_font.render(
            line_1,
            pygame.Color(255, 255, 255),
            pygame.Color(0, 0, 0, 50)
        )

        line_2_img, _ = self.stats_font.render(
            line_2,
            pygame.Color(255, 255, 255),
            pygame.Color(0, 0, 0, 50)
        )
        _, line_height = line_1_img.get_size()
        surface.blit(line_1_img, pos)
        pos2 = (pos[0], pos[1]+(line_height + (line_height/20)))
        surface.blit(line_2_img, pos2)

    def update_display(self):
        """ Draw current progress to the screen. """
        if self.options.get('no_display', False):
            # required even with display off so we can gather keypresses
            pygame.display.update()
            return

        self.background.fill(self.display_bg_color)
        self.background.blit(
            self.canvas.surface,
            (self.display_margin_px, self.display_margin_px)
        )

        if self.target_surface is not None:
            self.background.blit(
                self.target_surface,
                (self.target_surface.get_rect().width + (
                    self.display_margin_px*2
                ),
                    self.display_margin_px)
            )

        tmp_bg = self.background.copy()

        if self._show_highlights and self._clip_sub_rect:
            pygame.draw.rect(
                tmp_bg,
                self.display_highlight_sub_color,
                self._clip_sub_rect.move(
                    self.display_margin_px, self.display_margin_px
                ),
                self.display_highlight_width
            )

        if self._show_highlights and self._clip_rect:
            pygame.draw.rect(
                tmp_bg,
                self.display_highlight_color,
                self._clip_rect.move(
                    self.display_margin_px, self.display_margin_px
                ),
                self.display_highlight_width
            )

        screen_size = self.screen.get_size()
        scaled_size = resize_with_pad(
            tmp_bg.get_size(), screen_size
        )

        scaled_screen = pygame.transform.smoothscale(
            tmp_bg,
            scaled_size
        )

        if self._show_info:
            self.render_stats(
                scaled_screen,
                (self.display_margin_px*2, self.display_margin_px*2)
            )

        origin = (
            screen_size[0]/2 - scaled_size[0]/2,
            screen_size[1]/2 - scaled_size[1]/2
        )
        self.screen.blit(scaled_screen, origin)
        self.render_hud(self.screen)

        # required even with display off so we can gather keypresses
        pygame.display.update()

    def get_default_save_file_prefix(self):
        return 'imagelab'

    def get_save_file_name(self, ext=None, tpt=None):
        """ Return a save-file name based on template configuration and
            app state """
        if tpt is None:
            tpt = self.options.get('save_template', DEFAULT_SAVE_TEMPLATE)

        tpt = tpt.replace('%PREFIX', self.options.get(
            'prefix', self.get_default_save_file_prefix()
        ))

        if(self.options.get('runs', 1) != 1):
            tpt = tpt.replace('%RUN', 'r%06d' % self.current_run)
        else:
            tpt = tpt.replace('%RUN', '')

        tpt = tpt.replace('%CHILDREN', 'c%06d' % self.options.get('children'))
        tpt = tpt.replace('%WIDTH', 'w%04d' % self.canvas.size[0])
        tpt = tpt.replace('%HEIGHT', 'h%04d' % self.canvas.size[1])
        tpt = tpt.replace('%GENERATION', 'g%06d' % self.current_generation)

        if self._movie_mode:
            tpt = tpt.replace('%FRAME', 'f%09d' % self.current_frame)
        else:
            tpt = tpt.replace('%FRAME', '')

        if ext is not None:
            tpt = tpt + '.' + ext

        path = self.options.get('save_directory', '.')

        return os.path.join(path, tpt)

    def save(self, output_mode=None):
        """ Output current state to file """
        self.print("saving output")

        if output_mode is None:
            output_mode = OUTPUT_MODE_INSTRUCTIONS if \
                self.options.get('instructions') else OUTPUT_MODE_IMAGE

        if output_mode == OUTPUT_MODE_INSTRUCTIONS:
            savefile = self.get_save_file_name('json')
            os.makedirs(os.path.dirname(savefile), exist_ok=True)
            canvas_data = self.canvas.serialize()
            output_data = {
                "version": 1,
                "seed": self.options.get('seed'),
                "timestamp": datetime.datetime.utcnow().isoformat(),
                "gen_stop": self.options.get('gen_stop'),
                "children": self.options.get('children'),
                "radius": self.options.get('radius'),
                "shape": self.options.get('shape'),
                **canvas_data,
            }
            with open(savefile, 'w') as output:
                output.write(json.dumps(output_data))
        else:
            savefile = self.get_save_file_name(DEFAULT_IMAGE_SAVE_EXT)
            self.print(f"saving file {savefile}")
            os.makedirs(os.path.dirname(savefile), exist_ok=True)
            pygame.image.save(self.canvas.surface, savefile)

        return savefile

    def handle_events(self):
        """ handle keyboard inputs """
        for event in pygame.event.get():
            if event.type == pygame.QUIT:
                raise SystemExit("User Quit")
            elif event.type == pygame.KEYDOWN:
                if event.key == pygame.K_ESCAPE:
                    raise SystemExit("User Quit")
                elif event.key == pygame.K_SPACE:
                    self.print_profiler()
                elif event.key == pygame.K_h:
                    self._show_highlights = not self._show_highlights
                elif event.key == pygame.K_i:
                    self._show_info = not self._show_info
                elif event.key == pygame.K_a:
                    self.save(OUTPUT_MODE_INSTRUCTIONS)
                elif event.key == pygame.K_s:
                    self.save(OUTPUT_MODE_IMAGE)
                elif event.key == pygame.K_RETURN:
                    self.save()
            elif event.type == pygame.VIDEORESIZE:
                self.update_display()

        keys = pygame.key.get_pressed()
        if len(keys):
            # if (keys[pygame.K_LALT] | keys[pygame.K_RALT]) and \
            # keys[pygame.K_RETURN]:
            #    pygame.display.toggle_fullscreen()
            if self.options.get('lock_controls'):
                return
            if keys[pygame.K_LSHIFT] or keys[pygame.K_RSHIFT]:
                amount = 10
            else:
                amount = 1

            if keys[pygame.K_LEFT]:
                self.options['children'] = max(
                    1, self.options.get('children') - amount
                )
            if keys[pygame.K_RIGHT]:
                self.options['children'] = min(
                    10000, self.options.get('children') + amount
                )
            if keys[pygame.K_UP]:
                self.options['radius'] = min(
                    1000, self.options.get('radius') + amount
                )
            if keys[pygame.K_DOWN]:
                self.options['radius'] = max(
                    1, self.options.get('radius') - amount
                )

    def handle_tick_end(self):
        save_gen = self.options.get('save_gen')
        if save_gen is not None and self.current_generation % save_gen == 0:
            self.save()

        self.update_profiler()

    def child_callback(self, i, canvas_action, img):
        self.current_child = i
        now_time = time.perf_counter()
        if now_time - self._last_flip_time <= 0.1:
            return

        self._last_flip_time = now_time
        radius = canvas_action.params.get('radius')
        pos = canvas_action.params.get('pos')
        if pos and radius:
            self._clip_sub_rect = pygame.Rect(
                (pos[0]-radius, pos[1]-radius),
                (radius * 2, radius * 2)
            )
        self.update_display_minimal()
        self.handle_events()

    def _adaptive_params(self, match_pct):
        base_radius = self.options.get('radius', DEFAULT_RADIUS)
        base_children = self.options.get('children', DEFAULT_CHILDREN)
        min_radius = self.options.get('min_radius') or max(base_radius // 8, 5)

        t = max(0.0, min(1.0, match_pct / 100.0))
        radius = max(min_radius, int(base_radius * (1.0 - t)))
        # scale children inversely so hit probability per generation stays
        # roughly constant as radius (and thus target area) shrinks
        children = int(base_children * (base_radius / max(radius, 1)))
        return radius, children

    def handle_evolution_tick(self, tick):
        max_radius = self.options.get('radius', DEFAULT_RADIUS)

        if self.morph_clipped_sections and (
            tick % self.clip_change_rate == 0
        ):
            self._clip_rect = pygame.Rect(get_random_clip_rect(
                self.canvas.surface.get_rect(),
                max_radius*self.clipped_section_scale,
                max_radius*self.clipped_section_scale
            ))

        children = self.options.get('children', DEFAULT_CHILDREN)
        if self.options.get('adaptive_cheat_mode', False):
            score = match_score(self.target_surface, self.canvas.surface)
            max_radius, children = self._adaptive_params(get_match_percentage(score))
        self._current_radius = max_radius
        self._current_children = children

        # Resolve brush composite controls. brush_alpha=None means
        # "use the downstream default" — mutate_evolve falls back to 180.
        # brush_blend_mode 'auto' resolves to 'opaque' (alpha>=255) or 'min'
        # (alpha<255). Explicit values pass through unchanged.
        brush_alpha = self.options.get('brush_alpha')
        brush_blend_mode = self.options.get('brush_blend_mode', 'auto')
        if brush_blend_mode == 'auto':
            effective_alpha = brush_alpha if brush_alpha is not None else 180
            brush_blend_mode = 'opaque' if effective_alpha >= 255 else 'min'

        mutator_params = {
            'target': self.target_surface,
            'clip_rect': self._clip_rect,
            'children': children,
            'shape': self.options.get('shape', DEFAULT_SHAPE),
            'words': self.options.get('words', None),
            'brush_images': self.brush_surfaces,
            'max_radius': max_radius,
            'blend_mode': brush_blend_mode,
            'child_callback': self.child_callback,
            'score_fn': STRATEGIES.get(
                self.options.get('compare_strategy', 'euclidean'),
                STRATEGIES['euclidean']
            ),
            'pool': self._pool,
            'workers': self.options.get('workers', 1),
            '_parallel_stats': {},
        }
        # Only override alpha when explicit; absence preserves mutate_evolve's
        # legacy default of 180.
        if brush_alpha is not None:
            mutator_params['alpha'] = brush_alpha
        self.canvas.apply_mutator(mutation.mutate_evolve, mutator_params)
        self._last_worker_times = mutator_params['_parallel_stats'].get('worker_times', [])

    def movie_complete(self):
        """ Boolean: True if frames are done """
        frame_stop = self.options.get("frame_stop", -1)
        seconds_stop = self.options.get("seconds_stop", -1)
        if self.current_frame >= self.total_frames:
            return True
        if frame_stop > -1 and self.current_frame >= frame_stop:
            self.print(f"stopping movie evolution at frame_stop={frame_stop}")
            return True
        if (
            seconds_stop > -1 and
            self.stats.get("total_clock_time") >= seconds_stop
        ):
            self.print(f"stopping evolution at seconds_stop={seconds_stop}")
            return True
        return False

    def evolution_complete(self):
        """ Boolean: True if evolution is complete based on config """
        gen_stop = self.options.get("gen_stop", -1)
        seconds_stop = self.options.get("seconds_stop", -1)
        match_stop = self.options.get("match_stop", 101)
        if gen_stop > -1 and self.current_generation >= gen_stop:
            self.print(f"stopping evolution at gen_stop={gen_stop}")
            return True
        if (
            seconds_stop > -1 and
            self.stats.get("total_clock_time") >= seconds_stop
        ):
            self.print(f"stopping evolution at seconds_stop={seconds_stop}")
            return True
        if(match_stop < 101):
            score = match_score(self.target_surface, self.canvas.surface)
            percentage = get_match_percentage(score)
            if(percentage >= match_stop):
                self.print(f"stopping evolution at match={match_stop}%")
                return True

        return False
