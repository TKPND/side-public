"""Drift fixture tests for scripts/v4.11/bootstrap_v411.py (D-45).

Validate fail-close behavior: if any SEAL JSON is mutated, importing bootstrap_v411
must raise RuntimeError with 'signal_commit_v411 drift' and expected/actual sha256
substrings in the message.

Strategy: copy current SEAL JSONs to tmp_path, mutate one, monkeypatch _SEAL_DIR,
importlib.reload — expect RuntimeError.
"""

from __future__ import annotations

import importlib.util
import json
import pathlib
import shutil
import sys

import pytest


_REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]
_MODULE_PATH = _REPO_ROOT / "scripts" / "v4.11" / "bootstrap_v411.py"
_REAL_SEAL_DIR = (
    _REPO_ROOT / ".planning" / "phases" / "92-scope-lock-pre-registration-seal" / "SEAL"
)


def _load_fresh_module():
    """Fresh exec of bootstrap_v411.py so _verify_seal_at_import re-runs."""
    # Remove cached module to force fresh exec
    sys.modules.pop("bootstrap_v411", None)
    spec = importlib.util.spec_from_file_location("bootstrap_v411", _MODULE_PATH)
    mod = importlib.util.module_from_spec(spec)
    sys.modules["bootstrap_v411"] = mod
    spec.loader.exec_module(mod)
    return mod


def _exec_module_with_patched_seal_dir(seal_dir: pathlib.Path):
    """Load the module source and swap _SEAL_DIR literal before exec.

    We cannot monkeypatch before import because _verify_seal_at_import() runs at
    module exec time. Instead we read the source, substitute the SEAL dir literal
    via inject, then exec that modified source in a fresh namespace.
    """
    source = _MODULE_PATH.read_text(encoding="utf-8")
    # Inject override at top of file (after __future__ import)
    injection = (
        f"\nimport pathlib as _pl_override\n"
        f"_SEAL_DIR_OVERRIDE = _pl_override.Path({str(seal_dir)!r})\n"
    )
    # Insert after the first `from __future__` line (or at top if absent)
    marker = "from __future__ import annotations\n"
    if marker in source:
        source = source.replace(marker, marker + injection, 1)
    else:
        source = injection + source

    # Monkey-patch: rewrite the _SEAL_DIR assignment to use override
    # We use a sentinel comment to locate it deterministically.
    # Simpler: append override at end of constants block via runpy-style — but
    # the cleanest approach is to define _SEAL_DIR after the original by appending
    # a reassignment BEFORE _verify_seal_at_import() is called. Since the module
    # invokes _verify_seal_at_import() near the bottom, we need to override BEFORE
    # that call. Easiest: replace the `_verify_seal_at_import()` call line with
    # `_SEAL_DIR = _SEAL_DIR_OVERRIDE; _verify_seal_at_import()`.
    source = source.replace(
        "_verify_seal_at_import()  # import-time fail-close (D-45)",
        "_SEAL_DIR = _SEAL_DIR_OVERRIDE\n_verify_seal_at_import()  # import-time fail-close (D-45)",
        1,
    )

    # Execute in a fresh namespace (do NOT register in sys.modules to avoid
    # polluting the happy-path test module).
    ns: dict = {"__name__": "bootstrap_v411_drift_probe", "__file__": str(_MODULE_PATH)}
    code = compile(source, str(_MODULE_PATH), "exec")
    exec(code, ns)
    return ns


def _clone_seal(tmp_path: pathlib.Path) -> pathlib.Path:
    dst = tmp_path / "SEAL"
    dst.mkdir(parents=True, exist_ok=False)
    for src in _REAL_SEAL_DIR.glob("*.json"):
        shutil.copy2(src, dst / src.name)
    return dst


def test_seal_drift_raises_runtime_error(tmp_path: pathlib.Path) -> None:
    """Mutating a SEAL JSON must trigger RuntimeError('signal_commit_v411 drift')."""
    seal_copy = _clone_seal(tmp_path)
    filter_spec = seal_copy / "filter_spec.json"
    data = json.loads(filter_spec.read_text(encoding="utf-8"))
    data["_drift_marker"] = "injected-by-pytest"
    filter_spec.write_text(
        json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8"
    )

    with pytest.raises(RuntimeError) as exc_info:
        _exec_module_with_patched_seal_dir(seal_copy)

    msg = str(exc_info.value)
    assert "signal_commit_v411 drift" in msg


def test_seal_drift_error_message_format(tmp_path: pathlib.Path) -> None:
    """Error message contains both expected= and got= sha256 substrings."""
    seal_copy = _clone_seal(tmp_path)
    # Subtle mutation: flip a legitimate field value
    vol_cuts = seal_copy / "vol_cuts.json"
    data = json.loads(vol_cuts.read_text(encoding="utf-8"))
    data["lookback_bars"] = 91  # was 90
    vol_cuts.write_text(
        json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8"
    )

    with pytest.raises(RuntimeError) as exc_info:
        _exec_module_with_patched_seal_dir(seal_copy)

    msg = str(exc_info.value)
    assert "expected=" in msg
    assert "got=" in msg
    # Expected sha should still be the literal f8ccc8a8...
    assert "f8ccc8a8" in msg


def test_seal_drift_clean_copy_passes(tmp_path: pathlib.Path) -> None:
    """Sanity: untouched copy of SEAL must NOT raise (sha matches reference)."""
    seal_copy = _clone_seal(tmp_path)
    # Do not mutate — this should pass without RuntimeError.
    ns = _exec_module_with_patched_seal_dir(seal_copy)
    assert ns["M_PRIME"] == 64
