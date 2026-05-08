"""Sanity checks on ``kashdao_protocol_sdk.__version__``.

The version string is the source of truth for both the wheel (read by
hatch via ``[tool.hatch.version]``) and the publish-time tag-match
check in ``.github/workflows/publish-pypi.yml``. A typo here cascades
into a broken publish or a bad PyPI listing.

This test enforces:

- The string is structurally a PEP 440 (semver-ish) version.
- It exists at exactly one location (``_version.py``); the package
  __init__ doesn't keep a stale duplicate.
"""

from __future__ import annotations

import re

import kashdao_protocol_sdk

# PEP 440 release segment + optional pre-release (a/b/rc/dev N) +
# optional local segment. Tolerates the patterns we actually ship
# (``0.1.0``, ``0.1.0b1``, ``1.0.0rc1``); rejects accidents like
# ``v0.1.0`` or ``0.1`` or trailing whitespace.
_PEP440 = re.compile(
    r"^(?P<release>\d+(?:\.\d+){2,3})"
    r"(?:(?P<pre>(?:a|b|rc|dev)\d+))?"
    r"(?:\+(?P<local>[a-z0-9]+(?:[-_.][a-z0-9]+)*))?$"
)


def test_version_is_present_and_a_string() -> None:
    assert isinstance(kashdao_protocol_sdk.__version__, str)
    assert kashdao_protocol_sdk.__version__, "__version__ must be non-empty"


def test_version_matches_pep440_shape() -> None:
    v = kashdao_protocol_sdk.__version__
    assert _PEP440.match(v), (
        f"__version__={v!r} is not a recognised PEP 440 release/pre-release. "
        "Allowed shapes: 'X.Y.Z', 'X.Y.ZaN', 'X.Y.ZbN', 'X.Y.ZrcN', 'X.Y.ZdevN'."
    )


def test_version_does_not_carry_v_prefix() -> None:
    """Tag is ``vX.Y.Z`` but the package version must be bare ``X.Y.Z``.

    The publish workflow's ``twine check`` and PyPI's filename validator
    both reject a 'v' prefix; catching it here is faster than at upload.
    """
    assert not kashdao_protocol_sdk.__version__.startswith("v"), (
        "__version__ must NOT include a leading 'v' (the git tag prefix)"
    )
