"""cagent — concurrent agent workflow dispatcher."""

import sys


def _check_version():
    if sys.version_info < (3, 11):
        sys.exit(
            f"cagent requires Python >= 3.11 (found {sys.version}). "
            "Please upgrade."
        )


_check_version()

from cagent.cli import main  # noqa: E402

main()
