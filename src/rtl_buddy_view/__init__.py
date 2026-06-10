from importlib.metadata import PackageNotFoundError
from importlib.metadata import version as _version

from rtl_buddy_view.cli import app

try:
    __version__ = _version("rtl-buddy-view")
except PackageNotFoundError:  # pragma: no cover - source tree without dist metadata
    __version__ = "0.0.0"


def main() -> None:
    app()
