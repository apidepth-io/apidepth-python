"""Single source of truth for the SDK version.

Imported by ``apidepth.__init__`` and included in every batch payload so the
collector can correlate data-quality issues with specific SDK releases without
needing a support ticket.
"""

VERSION = "0.1.0"
