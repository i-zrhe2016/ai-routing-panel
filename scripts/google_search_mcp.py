#!/usr/bin/env python3
import sys
from pathlib import Path


ROOT_DIR = Path(__file__).resolve().parent.parent
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from app.xray.google_search_mcp import main


if __name__ == "__main__":
    raise SystemExit(main())
