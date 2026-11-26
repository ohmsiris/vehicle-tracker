"""The drop box's card-fuel and ice-program readers (app/inbox_r_fuel.py, app/inbox_r_sales.py). Supabase is faked throughout."""
import io
from datetime import date, datetime, timedelta

import pytest

from app import acc_cardfuel, accounting, inbox_r_fuel as fuel, inbox_r_sales as sales, sales_report, supabase
from app.inbox_common import Dropped, sniff_file

VEHICLES = [{"id": 1, "plate_number": "1ฒถ 2090", "vehicle_number": 12, "branch": "สระบุรี"},
            {"id": 2, "plate_number": "84-2051", "vehicle_number": 25, "branch": "สระบุรี"},
            {"id": 3, "plate_number": "2ฒฉ 3733", "vehicle_number": 53, "branch": "แก่งคอย"}]


class FakeDB:
    """select_all / select / delete / insert and the storage calls against in-memory tables and files; every write is recorded."""

    def __init__(self, tables=None):
        self.tables = {"vehicles": VEHICLES, **(tables or {})}
        self.deleted, self.inserted = [], []
        self.files, self.uploads = {}, []

    def select_all(self, table, params=None, page=1000):
        rows = self.tables.get(table, [])
        for col, cond in (params or {}).items():
            if col not in ("source_id", "entry_id", "id") or not isinstance(cond, str):
                continue                                                  # other filters are ignored (the sales tests rely on it)
            if cond.startswith("like."):
                pre = cond[5:].rstrip("*")
                rows = [r for r in rows if str(r.get(col) or "").startswith(pre)]
            elif cond.startswith("in.("):
                want = set(cond[4:-1].split(","))
                rows = [r for r in rows if str(r.get(col)) in want]
            elif cond.startswith("eq."):
                rows = [r for r in rows if str(r.get(col)) == cond[3:]]
        return list(rows)

    def select(self, table, params=None):
        return self.select_all(table, params)

    def delete(self, table, match):
        self.deleted.append((table, dict(match)))
        keep = self.tables.get(table)
        if keep is None:
            return
        gone = {id(r) for r in self.select_all(table, match)}
        self.tables[table] = [r for r in keep if id(r) not in gone]

    def insert(self, table, rows, **kw):
        self.inserted.append((table, list(rows)))
        return rows

    def storage_upload(self, path, data, content_type, upsert=False, bucket=None):
        assert bucket == supabase.ACCOUNTING_BUCKET                       # the private bucket, never the public one
        self.uploads.append(path)
        self.files[path] = data

    def storage_download(self, path, bucket=None):
        if path not in self.files:
            raise supabase.SupabaseError(f"storage download {path}: 404")
        return self.files[path]

    def storage_delete(self, paths, bucket=None):
        for path in paths:
            self.files.pop(path, None)


@pytest.fixture
def db(monkeypatch):
    fake = FakeDB()
    for name in ("select_all", "select", "delete", "insert", "storage_upload", "storage_download", "storage_delete"):
        monkeypatch.setattr(supabase, name, getattr(fake, name))
    monkeypatch.setattr(supabase, "update", lambda *a, **k: pytest.fail("the fuel reader never updates rows"))
    monkeypatch.setattr(accounting, "_require", lambda: None)
    got = []
    monkeypatch.setattr(accounting, "_insert_entries", lambda es: got.extend(es))
    fake.entries = got
    return fake


def _xlsx(sheets: dict) -> bytes:
    from openpyxl import Workbook
    wb = Workbook()
    wb.remove(wb.active)
    for name, rows in sheets.items():
        ws = wb.create_sheet(name)
        for r in rows:
            ws.append(r)
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


# ------------------------------------------------------------------ acc_cardfuel / sales_report additions
def test_cardfuel_sub_key_keeps_old_keys():
    old = acc_cardfuel.build("2026-10", "bangchak", {"84-2051": 107.0}, VEHICLES)
    new = acc_cardfuel.build("2026-10", "bangchak", {"84-2051": 107.0}, VEHICLES, sub="NM")
    assert old["entries"][0]["source_id"] == "fuel:bangchak:2026-10:842051"
    assert new["entries"][0]["source_id"] == "fuel:bangchak:2026-10:NM:842051"
    assert "บางจาก NM" in new["entries"][0]["description"]
    assert [(ln["account"], ln["debit"], ln["credit"]) for ln in new["entries"][0]["lines"]] == [("6120", 100.0, 0.0), ("1400", 7.0, 0.0), ("2100", 0.0, 107.0)]


def test_sales_report_footer_and_print_date():
    assert sales_report.branch_in("x\nบริษัท สระบุรีวารีเทพ จํากัด(สาขาแก่งคอย)\n") == "แก่งคอย"
    assert sales_report.branch_in("รายงานสรุป\nบริษัท สระบุรีวารีเทพ จํากัด\n") == "สระบุรี"
    assert sales_report.branch_in("nothing") is None
    assert sales_report.printed_on("พิมพ์วันทีѷ : 07-11-2026 21:43") == "2026-11-07"


# ------------------------------------------------------------------ PT export
PT_HEAD = ["DATE", "TRANSACTION_ID", "MID", "LICENSE_PLATE_NO", "LITRE", "AMOUNT", "STATUS", "REMARK (VOIDID)"]


def _pt_file(extra_total=0.0):
    fills = [("20/05/2026 08:00:00", "1", "1ฒถ 2090", 1000), ("26/05/2026 06:00:00", "2", "1ฒถ 2090", 2140),
             ("10/06/2026 06:00:00", "3", "1?? 2090", 1070), ("10/06/2026 07:00:00", "3", "1?? 2090", 1070),      # same id twice
             ("11/06/2026 06:00:00", "4", "84-2051", 500), ("12/06/2026 06:00:00", "5", "84-2051", 999, "Void", "V5"),
             ("25/06/2026 18:00:00", "6", "9กก 9999", 300), ("02/07/2026 06:00:00", "7", "84-2051", 700)]
    kept = sum(x[3] for x in fills if len(x) == 4) - 1070
    rows = [["Generated  Now: 2/7/2026"], ["SUMMARY"], ["Product", "QR Code", "Corparate / Fleet Card", "Void", "Transactions", "Litre",
                                                     "Amount Ex-vat", "VAT", "Amount"],
            ["Total", "0", "7", "1", "6", "1", "1", "1", f"{kept + extra_total:,.2f}"], [""], ["LIST"], PT_HEAD]
    for x in fills:
        rows.append([x[0], x[1], "m", x[2], "1", f"{x[3]:.2f}", x[4] if len(x) > 4 else "สำเร็จ", x[5] if len(x) > 5 else ""])
    return _xlsx({"รายงานการเติมน้ำมัน": rows})


def test_pt_books_only_whole_months_and_skips_voids(db):
    f = sniff_file("PT export.xlsx", _pt_file())
    assert fuel.sniff(f) > 0.9 and sales.sniff(f) == 0
    p = fuel.read(f)
    assert [b["key"] for b in p["batches"]] == ["fuel:pt:2026-06"] and p["month"] == "2026-06" and p["months"] == ["2026-06"]
    b = p["batches"][0]
    assert b["branch"] == "สระบุรี" and b["feed"] == "fuel_card" and {r["role"] for r in b["rows"]} == {"cost"}
    assert b["rows_total"] == 2140 + 1070 + 500 + 300
    sids = sorted(e["source_id"] for e in b["entries"])
    assert sids == ["fuel:pt:2026-06:1ฒถ2090", "fuel:pt:2026-06:842051", "fuel:pt:2026-06:9กก9999"]
    assert any("ไม่พบรถ" in (r["flag"] or "") for r in b["rows"])
    text = " | ".join(p["flags"])
    assert "พ.ค. 2569" in text and "ก.ค. 2569" in text and "ไม่ลงบัญชี" in text          # the two partial edge months
    assert "Void" in text and "ซ้ำ 1 แถว" in text
    assert p["file_total"] == p["rows_total"] and "SUMMARY" not in text
    # the owner may book the partial months anyway
    p2 = fuel.read(sniff_file("PT export.xlsx", _pt_file(), {"partial": "book"}))
    assert [b["key"] for b in p2["batches"]] == ["fuel:pt:2026-05", "fuel:pt:2026-06", "fuel:pt:2026-07"]


def test_pt_summary_mismatch_is_flagged(db):
    p = fuel.read(sniff_file("PT.xlsx", _pt_file(extra_total=50)))
    assert any("SUMMARY" in x and "ตรวจไฟล์" in x for x in p["flags"])


# ------------------------------------------------------------------ Bangchak Reader workbook
LOG_HEAD = ["ลำดับ", "วันที่", "งวด", "รถเบอร์", "ทะเบียนรถประจำเบอร์นี้", "ทะเบียนรถที่เติมจริง", "ชื่อ", "คนขับ", "ชื่อเล่น", "สถานะ",
            "หมายเหตุ", "ลิตร", "ราคา/ลิตร (บาท)", "จำนวนเงิน (บาท)"]


def _bc_file(title="บันทึกการเติมน้ำมัน (ใบวางบิล/ใบกำกับภาษี) บจ.สระบุรีวารีเทพ — สาขา NM — งวด 25/9/69 – 25/10/69", stated=6000):
    def row(n, d, plate, amt):
        return [n, d, "งวด", "25", "84-2051", plate, "x", "x", "x", "ปกติ", None, 10, 40, amt]
    log = [LOG_HEAD, row(1, datetime(2026, 9, 25), "84-2051", 1000), row(2, datetime(2026, 10, 1), "84-2051", 2000),
           row(3, datetime(2026, 10, 25), "2ฒฉ 3733", 3000), [None] * 10 + ["รวมทั้งหมด", None, None, 6000]]
    check = [["รายการ", "อ้างอิงแถว", "ยอดที่คำนวณ", "ยอดที่ระบุในต้นฉบับ", "ตรงกันหรือไม่"],
             ["รวมทั้งงวด: จำนวนบิล", "2-4", None, 3], ["รวมทั้งงวด: ยอดเงินรวม (บาท)", "2-4", None, stated]]
    return _xlsx({"คำอธิบาย": [[title]], "บันทึกเติมน้ำมัน": log, "ตรวจสอบยอดรวม": check})


def test_bangchak_month_from_rows_station_in_key(db):
    db.tables["acc_entries"] = [{"id": "a1", "source_id": "fuel:bangchak:2026-10:842051"},
                                {"id": "a2", "source_id": "fuel:bangchak:2026-10:ห้วยบง:842051"}]
    f = sniff_file("เติมน้ำมัน_NM_25กย-25ตค69.xlsx", _bc_file())
    assert fuel.sniff(f) > 0.9
    p = fuel.read(f)
    assert p["conflicts"] == [] and p["month"] == "2026-10"
    b = p["batches"][0]
    assert b["key"] == "fuel:bangchak:2026-10:NM" and b["file_total"] == 6000 and b["rows_total"] == 6000
    assert sorted(e["source_id"] for e in b["entries"]) == ["fuel:bangchak:2026-10:NM:2ฒฉ3733", "fuel:bangchak:2026-10:NM:842051"]
    assert all(ln["branch"] == "สระบุรี" for e in b["entries"] for ln in e["lines"])        # who paid, not whose truck
    text = " | ".join(p["flags"])
    assert "25/09/2026" in text and "นอกเดือน" in text                                         # the 25 Sep bill stays in October's bill
    assert "แบบเก่า 1 รายการ" in text and p["legacy"] == ["fuel:bangchak:2026-10:842051"]      # the other station's new key is not legacy
    # booking replaces this station's batch and removes the scripts' combined entries of the month, nothing else
    out = fuel.book(p, "file-1")
    assert out["batches"] == ["fuel:bangchak:2026-10:NM"] and out["booked"] > 0
    assert ("acc_entries", {"id": "in.(a1)"}) in db.deleted
    assert ("acc_entries", {"source_id": "eq.fuel:bangchak:2026-10:NM"}) in db.deleted
    assert ("acc_entries", {"source_id": "like.fuel:bangchak:2026-10:NM:*"}) in db.deleted
    assert len(db.entries) == 2
    assert out["legacy_removed"] == 1 and db.uploads == ["inbox/legacy/fuel-bangchak-2026-10.json"]


# ------------------------------------------------------------------ the scripts' combined entries come back when the files go
LEGACY = [{"id": "a1", "source_id": "fuel:bangchak:2026-10:842051", "entry_date": "2026-10-25", "branch": "สระบุรี", "source": "card_fuel",
           "party": "บางจาก", "description": "น้ำมันบางจาก ต.ค. 69 84-2051", "doc_type": None, "doc_no": None},
          {"id": "a3", "source_id": "fuel:bangchak:2026-10:est-25", "entry_date": "2026-10-25", "branch": "สระบุรี", "source": "card_fuel",
           "party": "บางจาก", "description": "ประมาณการ", "doc_type": None, "doc_no": None}]
LINES = [{"id": 1, "entry_id": "a1", "account": "6120", "debit": 100.0, "credit": 0.0, "branch": "สระบุรี", "vehicle_id": 2,
          "machine_id": None, "memo": None},
         {"id": 2, "entry_id": "a1", "account": "2100", "debit": 0.0, "credit": 100.0, "branch": "สระบุรี", "vehicle_id": None,
          "machine_id": None, "memo": None},
         {"id": 3, "entry_id": "a3", "account": "6120", "debit": 50.0, "credit": 0.0, "branch": "สระบุรี", "vehicle_id": None,
          "machine_id": None, "memo": "est"}]


def _legacy_db(db):
    db.tables["acc_entries"] = [dict(r) for r in LEGACY]
    db.tables["acc_lines"] = [dict(r) for r in LINES]


def _station(db, station):
    """What a booked station file leaves in the books (its entries' source_ids)."""
    db.tables["acc_entries"].append({"id": f"s-{station}", "source_id": f"fuel:bangchak:2026-10:{station}:842051"})


def test_removing_the_only_station_file_puts_the_old_entries_back(db):
    _legacy_db(db)
    p = fuel.read(sniff_file("เติมน้ำมัน_NM_25กย-25ตค69.xlsx", _bc_file()))
    assert "ห้วยบง" in p["remove_note"] and "กลับมา" in p["remove_note"] and any("เก็บสำเนา" in x for x in p["flags"])
    out = fuel.book(p, "file-1")
    assert out["legacy_removed"] == 2 and not fuel._legacy_bangchak("2026-10")
    db.entries.clear()
    fuel.unbook(p)                                                             # the NM file is removed, ห้วยบง was never booked
    back = {e["source_id"]: e for e in db.entries}
    assert set(back) == {"fuel:bangchak:2026-10:842051", "fuel:bangchak:2026-10:est-25"}
    a1 = back["fuel:bangchak:2026-10:842051"]
    assert (a1["entry_date"], a1["branch"], a1["party"]) == ("2026-10-25", "สระบุรี", "บางจาก")
    assert [(ln["account"], ln["debit"], ln["credit"], ln["vehicle_id"]) for ln in a1["lines"]] == [("6120", 100.0, 0.0, 2), ("2100", 0.0, 100.0, None)]
    assert back["fuel:bangchak:2026-10:est-25"]["lines"][0]["memo"] == "est"
    assert db.files == {}                                                      # the copy is used once


def test_with_the_other_station_still_booked_the_copy_waits_for_the_last_file(db):
    _legacy_db(db)
    nm = fuel.read(sniff_file("เติมน้ำมัน_NM_25กย-25ตค69.xlsx", _bc_file()))
    fuel.book(nm, "file-nm")
    _station(db, "ห้วยบง")                                                     # the ห้วยบง file is booked too
    hb = fuel.read(sniff_file("เติมน้ำมัน_ห้วยบง_25กย-25ตค69.xlsx", _bc_file(title="สาขาห้วยบง — งวด 25/9/69 – 25/10/69")))
    assert "remove_note" in hb and any("เก็บสำเนา" in x for x in hb["flags"])  # the page can warn on this file too
    db.entries.clear()
    fuel.unbook(nm)                                                            # NM removed while ห้วยบง is booked: nothing comes back
    assert db.entries == [] and "inbox/legacy/fuel-bangchak-2026-10.json" in db.files
    db.tables["acc_entries"] = [r for r in db.tables["acc_entries"] if ":ห้วยบง:" not in r["source_id"]]
    fuel.unbook(hb)                                                            # the last station file goes: the month comes back
    assert sorted(e["source_id"] for e in db.entries) == ["fuel:bangchak:2026-10:842051", "fuel:bangchak:2026-10:est-25"]


def test_booking_a_second_file_of_the_month_keeps_the_first_copy(db):
    _legacy_db(db)
    p = fuel.read(sniff_file("เติมน้ำมัน_NM_25กย-25ตค69.xlsx", _bc_file()))
    fuel.book(p, "file-1")
    first = db.files["inbox/legacy/fuel-bangchak-2026-10.json"]
    out = fuel.book(p, "file-2")                                               # a newer NM file (or the same one re-booked)
    assert out["legacy_removed"] == 0 and db.files["inbox/legacy/fuel-bangchak-2026-10.json"] == first and len(db.uploads) == 1


def test_no_copy_no_restore_and_the_copy_is_written_before_anything_is_deleted(db, monkeypatch):
    p = fuel.read(sniff_file("เติมน้ำมัน_NM_25กย-25ตค69.xlsx", _bc_file()))
    assert "remove_note" not in p
    fuel.unbook(p)                                                             # never booked over old entries: nothing to put back
    assert db.entries == []
    _legacy_db(db)

    def broken(*a, **k):
        raise supabase.SupabaseError("storage down")
    monkeypatch.setattr(supabase, "storage_upload", broken)
    with pytest.raises(supabase.SupabaseError):
        fuel.book(p, "file-1")
    assert len(fuel._legacy_bangchak("2026-10")) == 2 and not any(str(m.get("id", "")).startswith("in.") for t, m in db.deleted)


def test_pt_files_never_touch_the_bangchak_copy(db):
    p = fuel.read(sniff_file("PT export.xlsx", _pt_file()))
    fuel.book(p, "file-1")
    fuel.unbook(p)
    assert db.uploads == [] and db.files == {}


def test_bangchak_disagreements_are_conflicts(db):
    p = fuel.read(sniff_file("เติมน้ำมัน_ห้วยบง_25กย-25ตค69.xlsx", _bc_file()))
    assert [c["key"] for c in p["conflicts"]] == ["station"] and p["conflicts"][0]["default"] == "NM"
    p = fuel.read(sniff_file("เติมน้ำมัน_NM_25กย-25ตค69.xlsx", _bc_file(title="สาขา NM — งวด 26/10/69 – 25/11/69")))
    assert [c["key"] for c in p["conflicts"]] == ["month"]
    p = fuel.read(sniff_file("เติมน้ำมัน_NM_25กย-25ตค69.xlsx", _bc_file(title="สาขา NM — งวด 26/10/69 – 25/11/69"), {"month": "2026-10"}))
    assert p["conflicts"] == [] and p["batches"][0]["key"] == "fuel:bangchak:2026-10:NM"


def test_bangchak_total_mismatch_and_old_file_without_station(db):
    p = fuel.read(sniff_file("เติมน้ำมัน_รถสาย_สระบุรี_25กย-25ตค69.xlsx", _bc_file(title="บันทึกการเติมน้ำมัน รถสาย — สรุปจากสมุด", stated=6500)))
    assert p["batches"][0]["key"] == "fuel:bangchak:2026-10:ห้วยบง" and p["conflicts"] == []
    text = " | ".join(p["flags"])
    assert "รูปแบบไฟล์" in text and "6,500.00" in text


def test_bangchak_pdf_is_store_only():
    f = Dropped(name="เติมน้ำมัน_NM_25กย-25ตค69_สรุปรายคนขับ_แนวนอน.pdf", data=b"%PDF-", ext=".pdf", pages=1,
                text="สรุปรายคนขับ — สาขา NM — 25/9/69 – 25/10/69\nชื่อจริง\nชื่อเล่น\nจำนวนครั้งที่เติม\nรวมลิตร\nรวมจำนวนเงิน (บาท)\n"
                     "เอก ดีศรี\nเอก\n21\n983.589\n42,030.00\nรวมทั้งหมด\n113\n6089.613\n260,220.00\n")
    assert fuel.sniff(f) >= 0.9
    p = fuel.read(f)
    assert p["store_only"] and p["batches"] == [] and p["month"] == "2026-10" and p["file_total"] == 260220.0
    assert "ไม่ลงบัญชี" in p["note"] and "สองครั้ง" in p["note"]
    old = Dropped(name="NM_กันยา69.pdf", data=b"%PDF-", ext=".pdf", pages=1,
                  text="ชืÉอจริง\nชืÉอเล่น\nจํานวนครัÊงทีÉเติม\nรวมลิตร\nรวมจํานวนเงิน (บาท)\n104\n5667.090\n225,548.30\nรวมทัÊงหมด\n")
    p = fuel.read(old)
    assert fuel.sniff(old) >= 0.9 and p["store_only"] and p["month"] == "2026-09"


# ------------------------------------------------------------------ the ice program report
def _day(d: date, sales_: list) -> str:
    out = [d.strftime("%d/%m/%Y")]
    for cust, amt in sales_:
        out += [f"{amt:,.2f}", cust, "1.00 ซอง", "0.00", f"{amt:g}"]
    out += [f" {sum(a for _, a in sales_):,.2f}", "ผลรวม :", " 0.00", f" {len(sales_):.2f}"]
    return "\n".join(out)


def _report(footer="บริษัท สระบุรีวารีเทพ จํากัด(สาขาแก่งคอย)", last=date(2026, 10, 27)):
    days, d, total, qty = [], date(2026, 9, 26), 0.0, 0
    while d <= last:
        day = [("ร้านเฒ่า", 100.0), ("CPF ซิลเลอร์", 1000.0)] + ([("สระบุรี", 50.0)] if d.day == 1 else [])
        days.append(_day(d, day))
        total += sum(a for _, a in day)
        qty += len(day)
        d += timedelta(days=1)
    return ("พิมพ์วันทีѷ : 28-10-2026 09:00\nหน้าทีѷ : 1 / 1\nสินค้า / ลูกค้า\nราคา/หน่วย\nราคารวม\nซอง\n" + "\n".join(days)
            + f"\n {total:,.2f}\n {qty:.2f}\nผลรวมของ [ ซอง ] :\n 0.00\nรายงานสรุปยอดสินค้าต่อลูกค้ารายวัน\n{footer}\n")


@pytest.fixture
def report(monkeypatch, db):
    db.tables["sales_lines"] = [{"branch": "แก่งคอย", "customer": c, "sale_date": "2026-09-20", "amount": 10.0}
                                for c in ("ร้านเฒ่า", "เฮียเหลียว", "OK กุ้งกระทะ")]
    db.tables["tax_invoices"] = [{"month": "2026-10", "kind": "invoice", "customer_name": "CPF ซิลเลอร์", "amount": 31000.0},
                                 {"month": "2026-09", "kind": "invoice", "customer_name": "CPF ซิลเลอร์", "amount": 30000.0}]
    state = {"text": _report()}
    monkeypatch.setattr(sales_report, "text_of", lambda data: state["text"])
    monkeypatch.setattr(sales, "_file_bytes", lambda file_id: b"%PDF-")
    return state


def test_sales_branch_months_roles(report, db):
    f = Dropped(name="whatever.pdf", data=b"%PDF-", ext=".pdf", pages=1, text=report["text"])
    assert sales.sniff(f) > 0.9 and fuel.sniff(f) == 0
    p = sales.read(f)
    assert p["conflicts"] == [] and p["month"] == "2026-10" and p["months"] == ["2026-10"]
    assert any("พ.ย. 2569" in x and "ไม่ครบเดือน" in x for x in p["flags"])                 # 26-27 Oct = November, not booked
    b = p["batches"][0]
    assert b["key"] == "sales:แก่งคอย:2026-10" and b["branch"] == "แก่งคอย"
    roles = {(r["text"] if r.get("source") != "invoice" else "invoice", r["role"]) for r in b["rows"]}
    assert roles == {("ร้านเฒ่า", "revenue"), ("CPF ซิลเลอร์", "info"), ("สระบุรี", "transfer"), ("invoice", "revenue")}
    assert b["file_total"] == b["rows_total"] == 30 * 1100 + 50                                  # 26 Sep - 25 Oct, the day totals
    sids = {e["source_id"] for e in b["entries"]}
    assert {"sales:แก่งคอย:2026-10:ร้านเฒ่า", "sales:แก่งคอย:2026-10:สระบุรี", "sales:แก่งคอย:2026-10:สระบุรี:other"} <= sids
    assert "sales:แก่งคอย:2026-10:CPF ซิลเลอร์" not in sids                                   # CP is booked from the invoices
    assert any(s.startswith("sales:แก่งคอย:2026-10:invoice:cp") for s in sids) and b["extra_total"] > 0
    assert b["line_count"] == 30 * 2 + 1


def test_sales_book_replaces_only_its_months(report, db):
    p = sales.read(Dropped(name="r.pdf", data=b"%PDF-", ext=".pdf", pages=1, text=report["text"]))
    sales.book(p, "file-1")
    assert ("sales_lines", {"branch": "eq.แก่งคอย", "month": "eq.2026-10", "source": "eq.program"}) in db.deleted
    assert not any(t == "sales_lines" and m.get("month") != "eq.2026-10" for t, m in db.deleted)   # never the whole branch
    assert ("acc_entries", {"source_id": "like.sales:แก่งคอย:2026-10:*"}) in db.deleted
    ins = [r for t, rows in db.inserted if t == "sales_lines" for r in rows]
    assert len(ins) == 61 and {r["month"] for r in ins} == {"2026-10"}
    db.deleted.clear()
    sales.unbook(p)
    assert ("sales_lines", {"branch": "eq.แก่งคอย", "month": "eq.2026-10", "source": "eq.program"}) in db.deleted
    # a file that changed since the preview is refused
    report["text"] = _report(last=date(2026, 10, 20))
    with pytest.raises(accounting.InvalidEntry):
        sales.book(p, "file-1")


def test_sales_branch_conflict_and_saraburi_footer(report, db):
    db.tables["sales_lines"] = [{"branch": "สระบุรี", "customer": c, "sale_date": "2026-09-20", "amount": 1.0}
                                for c in ("ร้านเฒ่า", "CPF ซิลเลอร์", "สระบุรี", "x")]
    p = sales.read(Dropped(name="r.pdf", data=b"%PDF-", ext=".pdf", pages=1, text=report["text"]))
    assert [c["key"] for c in p["conflicts"]] == ["branch"] and p["conflicts"][0]["default"] == "แก่งคอย"
    report["text"] = _report(footer="บริษัท สระบุรีวารีเทพ จํากัด")
    p = sales.read(Dropped(name="r.pdf", data=b"%PDF-", ext=".pdf", pages=1, text=report["text"]))
    assert p["conflicts"] == [] and p["batches"][0]["key"] == "sales:สระบุรี:2026-10"
    roles = {r["text"]: r["role"] for r in p["batches"][0]["rows"]}
    assert roles["CPF ซิลเลอร์"] == "revenue" and roles["สระบุรี"] == "revenue"               # Saraburi: only "...แก่งคอย" is internal
