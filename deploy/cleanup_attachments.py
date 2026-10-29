#!/usr/bin/env python3
"""Delete only expired Bot attachment directories; invoked by the hourly timer."""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from fa_bot.attachments import cleanup

if __name__ == '__main__':
    print(f'Removed {cleanup()} expired attachment batches.', flush=True)
