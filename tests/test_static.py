import re
from collections import Counter
from pathlib import Path

STATIC = Path(__file__).resolve().parent.parent / "static"


def test_no_two_scripts_define_the_same_top_level_name():
    """The pages are plain scripts sharing one global scope, so a second `function truckRow` silently replaces the
    first (that once made the "where are my trucks" page draw roster cards with 'undefined' in them)."""
    names = Counter()
    for js in sorted(STATIC.glob("*.js")):
        for m in re.finditer(r"^(?:async\s+)?function\s+(\w+)|^(?:const|let|var)\s+(\w+)\s*=", js.read_text(encoding="utf-8"), re.M):
            names[m.group(1) or m.group(2)] += 1
    assert [n for n, c in names.items() if c > 1] == []
