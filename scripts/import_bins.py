"""One-time: the Word sheets' bins text -> one record per bin (app/bins.py). Safe to run again (existing bins are left alone).

    python scripts/import_bins.py [--write]
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app import bins  # noqa: E402

print(bins.import_from_sheets(write="--write" in sys.argv), "(written)" if "--write" in sys.argv else "(dry run)")
