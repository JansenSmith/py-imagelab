#!/usr/bin/env python3
"""assemble_stroke_movie — thin ffmpeg wrapper for imagephase F9 frame dumps.

Takes a directory containing `<prefix>-frame-NNNNNNN.png` files (produced
by `imagephase --frames-dir <dir>`) and runs ffmpeg with sensible defaults
to produce a playable mp4.

The plain ffmpeg one-liner works equally well — this script exists for the
convenience of not memorizing the flags. For anything non-trivial (custom
codecs, scale filters, audio overlays), use ffmpeg directly.

Example:
    python tools/assemble_stroke_movie.py \\
        --frames-dir ./run_output/ \\
        --output ./run_output/stroke_movie.mp4
"""
import argparse
import glob
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path


DEFAULT_FRAMERATE = 30
DEFAULT_CODEC = 'libx264'
DEFAULT_PIX_FMT = 'yuv420p'


def find_frame_sequence(frames_dir):
    """Locate the single `<prefix>-frame-NNNNNNN.png` sequence inside
    frames_dir. Returns (prefix, count, first_idx, last_idx).

    Raises ValueError if zero or multiple prefixes are found (multi-run
    output dirs aren't supported — clean up first or pass --pattern)."""
    pngs = sorted(glob.glob(os.path.join(frames_dir, '*-frame-*.png')))
    if not pngs:
        raise ValueError(
            f"no '*-frame-*.png' files found in {frames_dir!r}"
        )
    prefixes = set()
    indices = []
    pattern = re.compile(r'^(?P<prefix>.+)-frame-(?P<idx>\d+)\.png$')
    for p in pngs:
        m = pattern.match(os.path.basename(p))
        if not m:
            continue
        prefixes.add(m.group('prefix'))
        indices.append(int(m.group('idx')))
    if len(prefixes) > 1:
        raise ValueError(
            f"multiple frame-sequence prefixes in {frames_dir!r}: "
            f"{sorted(prefixes)}. Run on a clean output dir or pass "
            f"--pattern to override."
        )
    if not indices:
        raise ValueError(
            f"no parseable '<prefix>-frame-NNNN.png' files in {frames_dir!r}"
        )
    prefix = next(iter(prefixes))
    return prefix, len(indices), min(indices), max(indices)


def build_ffmpeg_cmd(frames_dir, output, prefix, framerate=DEFAULT_FRAMERATE,
                     codec=DEFAULT_CODEC, pix_fmt=DEFAULT_PIX_FMT,
                     start_number=0, pattern=None):
    """Build the ffmpeg argv. Honors `pattern` override when supplied;
    otherwise constructs from prefix."""
    if pattern is None:
        pattern = os.path.join(frames_dir, f'{prefix}-frame-%07d.png')
    return [
        'ffmpeg', '-y',
        '-framerate', str(framerate),
        '-start_number', str(start_number),
        '-i', pattern,
        '-c:v', codec,
        '-pix_fmt', pix_fmt,
        str(output),
    ]


def get_arg_parser():
    parser = argparse.ArgumentParser(
        prog='assemble_stroke_movie',
        description=(
            "Assemble imagephase --frames-dir output into an mp4 via ffmpeg."
        ),
    )
    parser.add_argument(
        '--frames-dir', required=True,
        help="Directory containing <prefix>-frame-NNNNNNN.png files.",
    )
    parser.add_argument(
        '--output', '-o', required=True,
        help="Output mp4 path.",
    )
    parser.add_argument(
        '--framerate', type=int, default=DEFAULT_FRAMERATE,
        help=f"Playback framerate (default: {DEFAULT_FRAMERATE} fps).",
    )
    parser.add_argument(
        '--codec', default=DEFAULT_CODEC,
        help=f"Video codec (default: {DEFAULT_CODEC}).",
    )
    parser.add_argument(
        '--pix-fmt', default=DEFAULT_PIX_FMT,
        help=f"Pixel format (default: {DEFAULT_PIX_FMT}; needed for "
             f"player compatibility — yuv420p is the safe choice).",
    )
    parser.add_argument(
        '--pattern', default=None,
        help="Override the ffmpeg input pattern. Useful if the dir contains "
             "frames from multiple runs or non-standard naming.",
    )
    parser.add_argument(
        '--dry-run', action='store_true',
        help="Print the ffmpeg command without invoking it.",
    )
    return parser


def run():
    parser = get_arg_parser()
    args = parser.parse_args()

    if not shutil.which('ffmpeg'):
        print(
            "error: ffmpeg not found on PATH. Install ffmpeg first "
            "(e.g. `apt install ffmpeg`, `brew install ffmpeg`).",
            file=sys.stderr,
        )
        return 1

    if not os.path.isdir(args.frames_dir):
        print(f"error: --frames-dir {args.frames_dir!r} does not exist",
              file=sys.stderr)
        return 1

    if args.pattern is not None:
        prefix = None
        start_number = 0
        # Trust the user-supplied pattern; don't enumerate.
        print(
            f"using --pattern {args.pattern!r} (frame count not enumerated)",
            file=sys.stderr,
        )
    else:
        try:
            prefix, count, first, last = find_frame_sequence(args.frames_dir)
        except ValueError as exc:
            print(f"error: {exc}", file=sys.stderr)
            return 1
        start_number = first
        print(
            f"found {count} frames ({prefix}-frame-{first:07d}..{last:07d}) "
            f"in {args.frames_dir}",
            file=sys.stderr,
        )

    cmd = build_ffmpeg_cmd(
        args.frames_dir, args.output, prefix,
        framerate=args.framerate, codec=args.codec,
        pix_fmt=args.pix_fmt, start_number=start_number,
        pattern=args.pattern,
    )

    if args.dry_run:
        print(' '.join(repr(a) if ' ' in a else a for a in cmd))
        return 0

    Path(args.output).parent.mkdir(parents=True, exist_ok=True)
    print(f"running: {' '.join(cmd)}", file=sys.stderr)
    result = subprocess.run(cmd)
    return result.returncode


if __name__ == '__main__':
    sys.exit(run())
