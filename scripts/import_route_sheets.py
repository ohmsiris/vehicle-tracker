"""Load the route trucks' sales sheets (one Word file per driver) as routes + customers for สายรถ · ใบขาย (app/route_sales.py).

    python scripts/import_route_sheets.py "<folder with the .docx files>" [--write] [--replace]

Each file: "<truck no>. <driver>.docx" (the Kaeng Khoi one is "แก่งคอย.docx"). The table rows are read straight from the .docx
(no extra library). A route already in the app keeps its customers unless --replace is given (only safe before drivers use it:
--replace refuses a route that already has sales). Dry run unless --write.
"""
import glob
import os
import re
import sys
import zipfile
from xml.etree import ElementTree as ET

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app import route_sales, supabase  # noqa: E402

W = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"


def _text(el) -> str:
    return "".join(n.text or "" for n in el.iter(W + "t")).strip()


def table_rows(path: str) -> list[list[str]]:
    root = ET.fromstring(zipfile.ZipFile(path).read("word/document.xml"))
    return [[" / ".join(t for t in (_text(p) for p in tc.iter(W + "p")) if t) for tc in tr.findall(W + "tc")] for tr in root.iter(W + "tr")]


def main() -> None:
    folder = sys.argv[1]
    write, replace = "--write" in sys.argv, "--replace" in sys.argv
    vehicles = supabase.select_all("vehicles", {"select": "id,vehicle_number,branch", "order": "id.asc"})
    existing = {(r["branch"], r["truck_no"]): r for r in supabase.select_all("sale_routes", {"select": "id,branch,truck_no", "order": "id.asc"})}
    for path in sorted(glob.glob(os.path.join(folder, "*.docx"))):
        base = os.path.basename(path)
        sheet = route_sales.parse_sheet(table_rows(path))
        branch = "แก่งคอย" if base.startswith("แก่งคอย") else "สระบุรี"
        truck = sheet["truck_no"] or re.match(r"^([\d-]+)", base).group(1)
        if branch == "แก่งคอย":
            truck = f"KK-{truck}"                       # Kaeng Khoi's truck numbers repeat Saraburi's
        if not sheet["driver_name"]:
            m = re.match(r"^[\d-]+\.\s*([^×.]+)", base)
            sheet["driver_name"] = m.group(1).strip() if m else None
        veh = next((v for v in vehicles if v["branch"] == branch and v["vehicle_number"] == truck.removeprefix("KK-")), None)
        n = len(sheet["customers"])
        inv = sum(c["billing"] == "invoice" for c in sheet["customers"])
        print(f"{base:28} {branch} truck {truck:8} {sheet['driver_name'] or '?':24} {sheet['name'] or '?':30} {n:3} customers ({inv} วางบิล)"
              f"{'' if veh else '  [no truck in the app]'}")
        if not write:
            continue
        row = {"branch": branch, "truck_no": truck, "vehicle_id": veh["id"] if veh else None, "name": sheet["name"] or f"สาย {truck}",
               "driver_name": sheet["driver_name"], "driver_phone": sheet["driver_phone"], "source_file": base}
        key = (branch, truck)
        if key in existing:
            rid = existing[key]["id"]
            supabase.update("sale_routes", {"id": f"eq.{rid}"}, row)
            have = supabase.select("sale_route_customers", {"select": "id", "route_id": f"eq.{rid}", "limit": "1"})
            if have and not replace:
                print("      route already has customers -- kept (use --replace before drivers start)")
                continue
            if have:
                if supabase.select("sale_days", {"select": "id", "route_id": f"eq.{rid}", "limit": "1"}):
                    print("      route already has sales -- not replaced")
                    continue
                supabase.delete("sale_route_customers", {"route_id": f"eq.{rid}"})
        else:
            rid = supabase.insert("sale_routes", [row])[0]["id"]
        supabase.insert("sale_route_customers", [{"route_id": rid, **c} for c in sheet["customers"]])
    print("written" if write else "dry run -- nothing written (add --write)")


if __name__ == "__main__":
    main()
