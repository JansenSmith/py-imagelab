"""CLI for imagemutate App.

See image mutate for examples
"""
import sys
import json
import argparse
import os
os.environ['PYGAME_HIDE_SUPPORT_PROMPT'] = "hide"
from apps import imagemutate    # noqa: E402
from imagelab.constants import (    # noqa: E402
    SHAPE_SETTINGS_NAMES, SHAPE_SETTINGS_ARRAY, SHAPE_MAP
)

DEFAULT_SAVE_TEMPLATE = "%PREFIX-%RUN%FRAME%CHILDREN%GENERATION"


def mapped_shape(shape_name):
    return SHAPE_MAP.get(shape_name)


def get_arg_parser():
    """Parse and return command line options for image mutate"""
    parser = argparse.ArgumentParser(
        description=""" Runs imagelab on a source image or movie.
        Hitting spacebar will save current image.""",
        prog='imagemutate'
    )
    parser.add_argument("target_path",
                        help="""Complete path to the target image or movie"""
                        )
    parser.add_argument("-G", "--gen-start", default=0, type=int,
                        help="start evolution at this [G]eneration")
    parser.add_argument("-g", "--gen-stop", default=1000, type=int,
                        help="""stop evolution at this [g]eneration
                        -1 will allow it to run indefinitely"""
                        )
    parser.add_argument("-T", "--seconds-stop", default=-1, type=int,
                        help="""[T]ime to stop.
                        Stop evolution after this many seconds"""
                        )
    parser.add_argument("-M", "--match-stop", default=101, type=int,
                        help="""[M]atch percentage to stop at.
                        Stop evolution once this match percentage has been
                        reached"""
                        )
    parser.add_argument("-R", "--runs", default=1, type=int,
                        help="""[R]un the evolover multiple times with same
                        inputs but different random output""")
    parser.add_argument("-L", "--lock-controls", default=False,
                        help="disable UI controls that affect the output")
    parser.add_argument("-F", "--frame-start", default=0, type=int,
                        help="start movie evolution at this [F]rame")
    parser.add_argument("-f", "--frame-stop", default=-1, type=int,
                        help="stop movie evolution at this [f]rame")
    parser.add_argument("-c", "--children", default=25, type=int,
                        help="number of [c]hildren to create when cloning")
    parser.add_argument("-S", "--shape", default=0,
                        choices=SHAPE_SETTINGS_ARRAY,
                        type=mapped_shape,
                        metavar=SHAPE_SETTINGS_NAMES,
                        help="""allow other [s]hapes,
                        see imagelab.constants for available shapes"""
                        )
    parser.add_argument("-W", "--words", default="",
                        nargs="+", help="""instead of a shape allow [w]ords from
                        a list. Supports multiple: -W foo -W bar"""
                        )
    parser.add_argument("-r", "--radius", default=40, type=int,
                        help="maximum [r]adius of given shapes")
    parser.add_argument("-n", "--no-alpha", action="store_true",
                        help="do[n]'t use alpha blending")
    parser.add_argument("-i", "--instructions", action="store_true",
                        help="output [i]nstructions instead of image or movie")
    parser.add_argument("-o", "--save-gen", type=int,
                        help="""save [o]utput every N generations.
                        If not set, saves at gen-stop"""
                        )
    parser.add_argument("-b", "--brush-images", nargs="+",
                        help="""A list of images to use as a brush inside of
                            whatever shape is used"""
                        )
    parser.add_argument("--brush-mode",
                        choices=['texture', 'shape'], default='texture',
                        help="""how brush images interact with the polygon
                            shape. 'texture' (default): random sub-rect of
                            brush is sampled and clipped by polygon shape;
                            polygon defines stroke silhouette. 'shape': full
                            brush sampled every stroke; polygon mask blend
                            skipped; brush's own alpha channel defines the
                            stroke silhouette. Requires --brush-images with
                            transparency to be visually meaningful."""
                        )
    parser.add_argument("-s", "--start-canvas",
                        help="""[s]tart with start-canvas instead of a blank
                        canvas. Useful for resuming old runs or just being
                        wacky."""
                        )
    parser.add_argument("-H", "--show-stats", default=False,
                        action='store_true', help="s[H]ow statistics")
    parser.add_argument("-d", "--save-directory", default=".",
                        help="location of output [d]irectory")
    parser.add_argument("-t", "--save-template", default=DEFAULT_SAVE_TEMPLATE,
                        help="""[t]emplate for naming output files.
                        Supports simple template naming schemes.
                        %%PREFIX, %%GENERATION, %%CHILDREN, %%FRAME
                        (for movies)"""
                        )
    parser.add_argument("-p", "--prefix", default="img",
                        help="[p]repend output files with this"
                        )
    parser.add_argument("-I", "--iconify", default=False, action="store_true",
                        help="[i]conify (minimize graphic display)")
    parser.add_argument("-v", "--verbose", default=False, action="store_true",
                        help="""[v]erbose - more output."""
                        )
    parser.add_argument("--seed", default=None, type=int,
                        help="random seed for reproducible runs"
                        )
    parser.add_argument("--compare-strategy", default="euclidean",
                        choices=["euclidean", "lab"],
                        help="comparison strategy: euclidean (default) or lab "
                             "(perceptually weighted)"
                        )
    parser.add_argument("-j", "--workers", default=1, type=int,
                        help="number of parallel worker processes for child "
                             "generation (default: 1, serial)"
                        )
    parser.add_argument("-N", "--no-display", default=False,
                        action="store_true", help="""[N]o display.
                        Don't show results in realtime"""
                        )
    parser.add_argument("--adaptive-cheat-mode", default=False,
                        action="store_true",
                        help="scale radius down and children up as match %% "
                             "improves — better art, less pure simulation"
                        )
    parser.add_argument("--min-radius", default=None, type=int,
                        help="floor for adaptive radius "
                             "(default: max(radius // 8, 5))"
                        )
    parser.add_argument("--close-on-exit", default=False,
                        action="store_true",
                        help="close the display immediately when evolution completes"
                        )
    parser.add_argument("--save-on-exit", default=False,
                        action="store_true",
                        help="automatically save output when evolution completes"
                        )

    parser.add_argument(
        '-C',
        '--config-file',
        type=str,
        default=None,
        help="""A base config file, which provides alternate defaults which can
        be overridden with their corresponding options and exit.""",
    )
    parser.add_argument(
        '-O',
        '--output-config',
        action="store_true",
        help="""Output the resulting configuration (excluding this option) and
            exit the application """
    )
    return parser


def run():
    """Run the image mutate app from the CLI"""
    parser = get_arg_parser()
    args = parser.parse_args()
    options = vars(args)

    # TODO: support correct conversion of shapes to Constants
    # right now the constant value gets imported/exported from/to JSON...
    if args.config_file is not None:
        print("loading config")
        print(args.config_file)
        with open(args.config_file, 'r') as f:
            config = json.load(f)
            options.update(config)

    if options.get('output_config'):
        options.pop('output_config')
        print(json.dumps(options, indent=4))
        sys.exit(0)

    app = imagemutate.App(options)
    app.run()
    sys.exit(0)
