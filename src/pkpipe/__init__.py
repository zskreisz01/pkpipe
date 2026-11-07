"""pkpipe – Personal Knowledge Pipeline."""

from importlib.metadata import PackageNotFoundError, version

try:  # one source of truth: ``version`` in pyproject.toml (the installed distribution's metadata)
    __version__ = version("pkpipe")
except PackageNotFoundError:  # running from a source tree that was never installed
    __version__ = "0+unknown"
