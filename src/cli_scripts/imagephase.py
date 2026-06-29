"""CLI for the imagephase App — phased painting (sibling to imagemutate).

Reuses imagemutate's argparse layer to keep target / radius / children /
seed / workers / save-* flags compatible. Adds the phase-specific flags
on top.
"""
import argparse
import json
import logging
import sys
import os

os.environ['PYGAME_HIDE_SUPPORT_PROMPT'] = "hide"

from apps import imagephase    # noqa: E402
from cli_scripts.imagemutate import get_arg_parser as _mutate_arg_parser    # noqa: E402


def _positive_int(s):
    n = int(s)
    if n < 1:
        raise argparse.ArgumentTypeError(f"must be >= 1, got {n}")
    return n


def _non_negative_float(s):
    f = float(s)
    if f < 0:
        raise argparse.ArgumentTypeError(f"must be >= 0, got {f}")
    return f


def _positive_int_or_inf(s):
    if s.strip().lower() in ("inf", "infinity"):
        return float('inf')
    n = int(s)
    if n < 1:
        raise argparse.ArgumentTypeError(f"must be >= 1 or 'inf', got {n}")
    return n


def get_arg_parser():
    """Build the imagephase argparse — extends imagemutate's parser with
    phase-specific flags. Inheriting from imagemutate's parser keeps the
    shared options (target_path, -r, -c, -j, --seed, etc.) compatible."""
    parser = _mutate_arg_parser()
    parser.prog = 'imagephase'
    parser.description = (
        "Paint a target image in phases — paint with one brush per phase, "
        "advance to next phase on plateau-then-switch. FDM-faithful "
        "composite by default."
    )

    phase_group = parser.add_argument_group(
        'phase mode',
        'Required flags for phase mode. --start-canvas is also required.',
    )
    phase_group.add_argument(
        "--phases", type=_positive_int, required=True,
        help="Number of phases (>=1). Required.",
    )
    phase_group.add_argument(
        "--phase-brushes", nargs='+', default=None,
        help="One brush PNG per phase, space-separated, parallel to --phases. "
             "Mutually exclusive with --phase-brushes-multi.",
    )
    phase_group.add_argument(
        "--phase-brushes-multi", default=None,
        help="EXPERIMENTAL alternative to --phase-brushes. Semicolon-separated "
             "phase groups where each phase has multiple brushes (uniform-random "
             "within a phase). Example: 'p1a.png p1b.png ; p2.png ; p3.png'.",
    )
    phase_group.add_argument(
        "--phase-max-radius", nargs='+', type=int, default=None,
        help="Per-phase starting (max) radius. Parallel list of length --phases. "
             "Default: 40 per phase.",
    )
    phase_group.add_argument(
        "--phase-min-radius", nargs='+', type=int, default=None,
        help="Per-phase adaptive-shrink floor radius. Parallel list. "
             "Default: 5 per phase.",
    )
    phase_group.add_argument(
        "--phase-max-gens", nargs='+', type=_positive_int_or_inf, default=None,
        help="EXPERIMENTAL per-phase safety cap; advance regardless of "
             "plateau when gens-in-phase reaches the cap. Use 'inf' for "
             "no cap. Default: inf per phase.",
    )
    phase_group.add_argument(
        "--plateau-window", type=_positive_int, default=200,
        help="EXPERIMENTAL generations of history to consider for plateau check. "
             "Default 200.",
    )
    phase_group.add_argument(
        "--plateau-delta", type=_non_negative_float, default=0.005,
        help="EXPERIMENTAL min match-%% gain over the window to NOT count as "
             "plateau (pct points). Default 0.005 (= 0.5%% over the window).",
    )
    # Add long-form aliases for the existing -v flag (note: imagemutate's
    # parser already has -v as store_true; we don't redefine it). Add --debug
    # as a separate flag mapped to -vv-level logging.
    phase_group.add_argument(
        "--debug", action="store_true",
        help="DEBUG-level logging (per-gen plateau buffer state, brush-set hash, "
             "mutator_params hash). Equivalent to -vv. Use --verbose / -v for "
             "INFO-level only.",
    )

    return parser


def _setup_logging(options):
    """Configure imagelab.phase logger level based on -v / --verbose / --debug."""
    verbose = options.get('verbose', False)
    debug = options.get('debug', False)
    if debug:
        level = logging.DEBUG
    elif verbose:
        level = logging.INFO
    else:
        level = logging.WARNING

    logger = logging.getLogger("imagelab")
    handler = logging.StreamHandler(sys.stderr)
    handler.setFormatter(logging.Formatter("[%(name)s] %(message)s"))
    logger.addHandler(handler)
    logger.setLevel(level)


def run():
    """Run the imagephase app from the CLI."""
    parser = get_arg_parser()
    args = parser.parse_args()
    options = vars(args)

    if args.config_file is not None:
        with open(args.config_file, 'r') as f:
            config = json.load(f)
            options.update(config)

    if options.get('output_config'):
        options.pop('output_config')
        print(json.dumps(options, default=str, indent=2))
        return 0

    _setup_logging(options)

    app = imagephase.App(options)
    app.run()
    return 0
