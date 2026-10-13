"""Regression tests for the shared temp-dir base used by the trusted evaluator.

The checker and benchmark created cluster/data/dump temp dirs with a hardcoded
``dir="/private/tmp"`` (the macOS shared temp). On Linux that path does not
exist, so ``tempfile.mkdtemp`` raised ``FileNotFoundError`` before any TPC-H
work ran, failing the official gates. ``shared_tmp_base()`` picks a base that
exists on the running platform; these tests pin that contract.
"""

from __future__ import annotations

import os
import shutil
import sys
import tempfile

sys.path.insert(0, os.path.dirname(__file__))  # import the sibling trusted harness

import harness  # noqa: E402


def test_base_is_an_existing_directory_mkdtemp_can_use() -> None:
    """The regression: the base must be a real dir where mkdtemp succeeds.

    At the merge base this failed on Linux — the hardcoded /private/tmp did not
    exist, so mkdtemp(dir=...) raised FileNotFoundError at gate setup.
    """
    base = harness.shared_tmp_base()
    assert isinstance(base, str) and base
    assert os.path.isdir(base), f"shared_tmp_base() returned a non-directory: {base!r}"
    created = tempfile.mkdtemp(prefix="vibesys-tmpbase-test-", dir=base)
    try:
        assert os.path.isdir(created)
    finally:
        shutil.rmtree(created, ignore_errors=True)


def test_base_matches_platform_policy() -> None:
    """macOS keeps the sandbox-visible /private/tmp; every other platform uses
    the ordinary temp dir (``$TMPDIR`` or /tmp)."""
    base = harness.shared_tmp_base()
    if sys.platform == "darwin":
        assert base == "/private/tmp"
    else:
        assert base == tempfile.gettempdir()


if __name__ == "__main__":
    test_base_is_an_existing_directory_mkdtemp_can_use()
    test_base_matches_platform_policy()
    print("shared_tmp_base regression tests: PASS")
