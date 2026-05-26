"""Deprecated alias for :mod:`rtl_buddy_view.cst_cache`.

The module was promoted to public API in view#109 — see
``rtl_buddy_view.cst_cache``. This shim re-exports every public name
from the new location and emits a ``DeprecationWarning`` on import.
It will be removed in the next minor release.
"""

from __future__ import annotations

import warnings as _warnings

from rtl_buddy_view.cst_cache import (  # noqa: F401  (re-exports)
    _VERIBLE_VERSION_BY_BINARY,
    _atomic_write_json,
    _cache_path_for,
    _query_version,
    cache_root,
    content_hash,
    get_or_compute,
    is_disabled,
    verible_version,
)

__all__ = [
    "_VERIBLE_VERSION_BY_BINARY",
    "_atomic_write_json",
    "_cache_path_for",
    "_query_version",
    "cache_root",
    "content_hash",
    "get_or_compute",
    "is_disabled",
    "verible_version",
]

_warnings.warn(
    "rtl_buddy_view._cst_cache is deprecated; import from "
    "rtl_buddy_view.cst_cache instead. The underscore-prefixed module "
    "will be removed in the next minor release.",
    DeprecationWarning,
    stacklevel=2,
)
