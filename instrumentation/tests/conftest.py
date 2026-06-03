"""Shared pytest setup for the instrumentation test suite.

Adds the parent `instrumentation/` directory to `sys.path` so test modules
can `import detect_ping_pong` etc. without an editable install.
"""

import sys
from pathlib import Path

INSTRUMENTATION_DIR = Path(__file__).resolve().parent.parent
if str(INSTRUMENTATION_DIR) not in sys.path:
    sys.path.insert(0, str(INSTRUMENTATION_DIR))
