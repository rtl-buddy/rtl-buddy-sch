from rtl_buddy_view._dist import dist_version
from rtl_buddy_view.cli import app

#: Resolved once, from the one lookup every payload stamp shares
#: (:mod:`rtl_buddy_view._dist`).
__version__ = dist_version()


def main() -> None:
    app()
