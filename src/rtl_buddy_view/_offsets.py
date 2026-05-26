"""Deprecated alias for :mod:`rtl_buddy_view.offsets`.

The module was promoted to public API in view#109 — see
``rtl_buddy_view.offsets``. This shim re-exports :class:`OffsetIndex`
from the new location and emits a ``DeprecationWarning`` on import.
It will be removed in the next minor release.
"""

from __future__ import annotations

import warnings as _warnings

from rtl_buddy_view.offsets import OffsetIndex  # noqa: F401  (re-export)

__all__ = ["OffsetIndex"]

_warnings.warn(
    "rtl_buddy_view._offsets is deprecated; import from "
    "rtl_buddy_view.offsets instead. The underscore-prefixed module "
    "will be removed in the next minor release.",
    DeprecationWarning,
    stacklevel=2,
)
