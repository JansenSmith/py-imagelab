"""Regression test for the numpy-scalar JSON serialization bug.

Pre-fix: `imagemutate -i` (and any other code path that JSON-serializes
canvas action params) would crash with:

    TypeError: Object of type int64 is not JSON serializable

The crash fires when a CanvasAction's params dict contains a numpy scalar
(`pos`, `radius`, `rotation` from `rng.integers`, etc.) and the resulting
JSON is fed to `json.dumps`. That covers every real shape produced by
`mutate_evolve`.

Post-fix: `_jsonsafe(value)` recursively converts numpy scalars / arrays
in `CanvasAction.__json__`'s output before `json.dumps` ever sees them.

This test reproduces the upstream crash and asserts it's gone — does not
exercise `imagephase`. Imagemutate is the canonical surface that triggers
it.
"""
import json
import shutil
import subprocess

import numpy as np
import pytest

from imagelab.canvas import _jsonsafe


# ---------- _jsonsafe helper unit coverage ----------

def test_jsonsafe_passes_through_native_python_types():
    assert _jsonsafe(5) == 5
    assert _jsonsafe(3.14) == pytest.approx(3.14)
    assert _jsonsafe("foo") == "foo"
    assert _jsonsafe(None) is None
    assert _jsonsafe(True) is True


def test_jsonsafe_converts_numpy_integer():
    val = np.int64(42)
    out = _jsonsafe(val)
    assert isinstance(out, int)
    assert out == 42
    # json.dumps should now succeed.
    json.dumps(out)


def test_jsonsafe_converts_numpy_floating():
    val = np.float64(2.5)
    out = _jsonsafe(val)
    assert isinstance(out, float)
    assert out == pytest.approx(2.5)
    json.dumps(out)


def test_jsonsafe_converts_numpy_array():
    val = np.array([1, 2, 3], dtype=np.int64)
    out = _jsonsafe(val)
    assert isinstance(out, list)
    assert out == [1, 2, 3]
    json.dumps(out)


def test_jsonsafe_recurses_into_dict():
    inner = {'a': np.int64(1), 'b': 'plain', 'c': np.float64(2.5)}
    out = _jsonsafe(inner)
    assert isinstance(out['a'], int)
    assert out['b'] == 'plain'
    assert isinstance(out['c'], float)
    json.dumps(out)


def test_jsonsafe_recurses_into_tuple_and_list():
    val = (np.int64(1), [np.int64(2), np.float64(3.5)], 'plain')
    out = _jsonsafe(val)
    assert out == (1, [2, 3.5], 'plain')
    # tuples become tuples in our output; json.dumps treats them as lists.
    json.dumps(list(out))


def test_jsonsafe_nested_mixed():
    """Realistic params dict shape: pos = (int64, int64), color = (int, int, int),
    radius = int64."""
    params = {
        'pos': (np.int64(120), np.int64(45)),
        'color': (255, 0, 0),
        'radius': np.int64(15),
        'alpha': 220,
        'rotation': np.int64(180),
        'shape': 'circle',
    }
    out = _jsonsafe(params)
    s = json.dumps(out)
    reparsed = json.loads(s)
    assert reparsed['pos'] == [120, 45]
    assert reparsed['color'] == [255, 0, 0]
    assert reparsed['radius'] == 15
    assert reparsed['alpha'] == 220
    assert reparsed['rotation'] == 180


# ---------- End-to-end: imagemutate -i no longer crashes ----------

def test_imagemutate_dash_i_produces_valid_json(tmp_path, target_64x64):
    """Pre-fix this crashed with `TypeError: int64 is not JSON serializable`.
    Post-fix: produces a parseable JSON output file."""
    if shutil.which("imagemutate") is None:
        pytest.skip("imagemutate not on PATH; install with `pip install -e .`")

    result = subprocess.run(
        ["imagemutate", str(target_64x64),
         "--seed", "42", "-g", "5", "-c", "3", "-j", "1",
         "-i",  # instructions / JSON mode
         "--save-on-exit", "--close-on-exit", "-p", "json_smoke",
         "-d", str(tmp_path)],
        capture_output=True, text=True, timeout=60,
    )
    assert result.returncode == 0, (
        f"imagemutate exited {result.returncode}\nstderr:\n{result.stderr}"
    )
    assert "int64" not in result.stderr and "JSON serializable" not in result.stderr

    outputs = list(tmp_path.glob("json_smoke-*.json"))
    assert len(outputs) == 1, f"expected one JSON file, got {outputs}"

    data = json.loads(outputs[0].read_text())
    # Sanity-check that key fields are present and well-typed.
    assert isinstance(data['seed'], int)
    assert isinstance(data['history'], list)
    assert len(data['history']) > 0
    # Each shape entry is [opcode, params_dict]; params dict values must be
    # JSON-native types (no numpy leaked through).
    for entry in data['history']:
        assert isinstance(entry, list) and len(entry) >= 2
        params = entry[1]
        if isinstance(params, dict):
            for value in params.values():
                assert _has_no_numpy(value), (
                    f"numpy type leaked into serialized params: {value!r}"
                )


def _has_no_numpy(value):
    """Defense-in-depth: walk the value and assert no numpy types remain."""
    if isinstance(value, (np.integer, np.floating, np.ndarray)):
        return False
    if isinstance(value, (list, tuple)):
        return all(_has_no_numpy(v) for v in value)
    if isinstance(value, dict):
        return all(_has_no_numpy(v) for v in value.values())
    return True
