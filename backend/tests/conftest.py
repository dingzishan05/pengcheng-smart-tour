import os
import sys
from pathlib import Path

os.environ.setdefault("JWT_SECRET", "test-only-secret-key-0123456789abcdef0123456789")

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))