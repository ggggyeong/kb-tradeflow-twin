from __future__ import annotations

import os
import tempfile
from pathlib import Path

os.environ["TRADEFLOW_MODE"] = "offline"
os.environ["LANGSMITH_TRACING"] = "false"
test_db = Path(tempfile.gettempdir()) / f"kb_tradeflow_pytest_{os.getpid()}.db"
os.environ["DATABASE_URL"] = f"sqlite:///{test_db}"
