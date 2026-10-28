"""
Take the stray type word ("อะไหล่" / "ค่าแรง" / "วัสดุสิ้นเปลือง") off the front of saved bill lines (app/linetext.py).

    .venv/Scripts/python.exe scripts/clean_line_type_words.py [--apply]

Only the description is changed ("อะไหล่ ไส้กรองอากาศ" -> "ไส้กรองอากาศ"); a line left with no category that began with ค่าแรง gets ค่าแรง. Dry run unless --apply.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app import supabase                                         # noqa: E402
from app.linetext import strip_type_word                          # noqa: E402


def main(apply: bool) -> None:
    rows = supabase.select_all("bill_line_items", {"select": "id,event_id,description,category", "order": "id.asc"})
    todo = []
    for r in rows:
        clean, word = strip_type_word(r["description"])
        if word and clean and clean != r["description"]:
            values = {"description": clean}
            if word == "ค่าแรง" and not r.get("category"):
                values["category"] = "ค่าแรง"
            todo.append((r, values))
    for r, v in todo[:15]:
        print(f"  {r['description'][:50]!r} -> {v['description'][:50]!r}")
    print(f"{len(todo)} of {len(rows)} lines to clean")
    if apply:
        for r, v in todo:
            supabase.update("bill_line_items", {"id": f"eq.{r['id']}"}, v)
        print("written")
    else:
        print("dry run -- nothing written (add --apply)")


if __name__ == "__main__":
    main("--apply" in sys.argv)
