"""Entry point: python3 -u main.py (stdin/stdout speak mechfight/1; logs go to stderr)."""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import mechfight as mf  # noqa: E402
from brain import Bot  # noqa: E402

if __name__ == "__main__":
    mf.run(Bot)
