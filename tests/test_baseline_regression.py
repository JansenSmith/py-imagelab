"""Regression sentinel: imagemutate's canonical baseline run must produce
a byte-identical PNG every time. Captures the project's current behavior
under fixed (seed, target, params) so any unintentional drift is caught.

If a future change intentionally alters this behavior, update
tests/fixtures/baseline_hash.txt in the SAME PR that introduces the change,
and explain why in the PR body.

The run command uses display-mode (no `-N`); CI wraps the whole pytest
invocation in `xvfb-run`. See tests/README.md.
"""
import hashlib
import subprocess


# Canonical baseline parameters. Do NOT alter without also regenerating
# baseline_hash.txt + updating the run's documentation.
BASELINE_PARAMS = [
    "--seed", "42",
    "-g", "100",
    "-c", "10",
    "-j", "1",
    "--save-on-exit",
    "--close-on-exit",
    "-p", "baseline",
]


def test_baseline_regression(target_64x64, baseline_hash, imagemutate_cmd, tmp_path):
    cmd = imagemutate_cmd + [
        str(target_64x64),
        *BASELINE_PARAMS,
        "-d", str(tmp_path),
    ]
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
    assert result.returncode == 0, (
        f"imagemutate exited {result.returncode}\n"
        f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
    )

    outputs = sorted(tmp_path.glob("baseline-*.png"))
    assert len(outputs) == 1, (
        f"expected exactly one baseline-*.png output, got {len(outputs)}: "
        f"{[p.name for p in outputs]}"
    )

    actual = hashlib.sha256(outputs[0].read_bytes()).hexdigest()
    assert actual == baseline_hash, (
        f"baseline hash drift detected.\n"
        f"  expected: {baseline_hash}\n"
        f"  actual:   {actual}\n"
        f"  output file: {outputs[0]}\n"
        f"If this drift is intentional (e.g. an upstream behavior change "
        f"landed), regenerate the baseline per tests/README.md."
    )
