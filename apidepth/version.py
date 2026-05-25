"""Single source of truth for the SDK version.

Imported by ``apidepth.__init__`` and included in every batch payload so the
collector can correlate data-quality issues with specific SDK releases without
needing a support ticket.
"""

__version__ = "0.2.0"
VERSION = __version__  # backwards-compatible alias used throughout the SDK
