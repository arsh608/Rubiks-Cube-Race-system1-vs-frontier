import os
import sys
import tempfile
from pathlib import Path

# Isolate test results and never use a real key in tests.
os.environ["RESULTS_DIR"] = tempfile.mkdtemp(prefix="cube_race_test_")
os.environ["OPENROUTER_API_KEY"] = "test-key"
os.environ["RETRY_BACKOFF_S"] = "0"
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
