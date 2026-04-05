#!/usr/bin/env python3
"""Launch the AI Provocateurs web interface."""

import sys
from pathlib import Path

# Ensure scripts are importable
sys.path.insert(0, str(Path(__file__).resolve().parent / "scripts"))

import uvicorn

if __name__ == "__main__":
    uvicorn.run(
        "web.app:create_app",
        factory=True,
        host="127.0.0.1",
        port=8080,
        reload=True,
    )
