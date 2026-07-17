from __future__ import annotations

import os
import tempfile

# Keep imports deterministic and prevent GUI prompts or writes to a developer's
# real application data directory during test collection.
os.environ.setdefault("MCPCLIENT_HEADLESS", "1")
os.environ.setdefault("MCPCLIENT_DATA_DIR", tempfile.mkdtemp(prefix="mcpclient-tests-"))
