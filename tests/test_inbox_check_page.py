"""static/inboxcheck.js (ตรวจเอกสารก่อนลงบัญชี, 28 Nov 2026): its pure helpers, run in Node on a preview the real app/inbox_ai builds
from made-up page reads. Skipped when Node is not installed; never the live database."""
import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest

from app import inbox_ai, supabase
from tests.test_inbox_ai import MID_PAGES, VEH

STATIC = Path(__file__).resolve().parent.parent / "static"
NODE = shutil.which("node")
pytestmark = pytest.mark.skipif(NODE is None, reason="Node is not installed")

RUNNER = r"""
const vm = require("vm"), fs = require("fs");
const [page, test, data] = process.argv.slice(2).map((p) => fs.readFileSync(p, "utf8"));
const ctx = vm.createContext({ console, JSON, Date, Promise, Intl, Number, Math, Map, Set, Object, Array, String, RegExp, DATA: JSON.parse(data) });
ctx.globalThis = ctx;
vm.runInContext(page, ctx, { filename: "inboxcheck.js" });
process.stdout.write(JSON.stringify(vm.runInContext(test, ctx)));
"""


@pytest.fixture
def preview(monkeypatch):
    monkeypatch.setattr(supabase, "configured", lambda: False)
    monkeypatch.setattr(inbox_ai, "_vehicles", lambda: VEH)
    monkeypatch.setattr(inbox_ai, "_cash_fuel_lines", lambda m, b, h: [{"day": "2026-10-27", "amount": 1500, "vehicle_id": "v5"}])
    reads = [{**p, "n": i} for i, p in enumerate(json.loads(json.dumps(MID_PAGES)), 1)]
    pv = inbox_ai.from_pages(reads, "x.pdf", {}, images={1: "accdocs/inbox/a/p1.jpg", 2: "accdocs/inbox/a/p2.jpg"},
                             line_edits={"edits": {"p2:l2": {"delete": True}}, "added": []})
    return {"preview": json.loads(json.dumps(pv)), "accounts": inbox_ai.chart_list(), "vehicles": VEH}


def run(test: str, data: dict, tmp_path):
    files = []
    for name, text in (("test.js", test), ("data.json", json.dumps(data, ensure_ascii=False)), ("runner.js", RUNNER)):
        p = tmp_path / name
        p.write_text(text, encoding="utf-8")
        files.append(p)
    out = subprocess.run([NODE, str(files[2]), str(STATIC / "inboxcheck.js"), str(files[0]), str(files[1])],
                         capture_output=True, text=True, encoding="utf-8", env={**os.environ, "TZ": "Asia/Bangkok"}, timeout=30)
    assert out.returncode == 0, out.stderr
    return json.loads(out.stdout)


def test_pages_say_what_they_are_and_lines_put_the_unbooked_last(preview, tmp_path):
    got = run("""(() => {
      const pages = ickPages(DATA);
      const p2 = pages.find((p) => p.n === 2), p3 = pages.find((p) => p.n === 3);
      const saved = ickCopy(DATA.preview.line_edits);
      return { kinds: pages.map((p) => [p.n, p.kind, p.money, p.canAdd, p.image, p.warn]),
               p2: ickLinesOf(p2, saved).map((l) => [l.key, l.state, l.v.text, l.v.amount]),
               p3: ickLinesOf(p3, saved).map((l) => [l.key, l.state, l.v.amount, l.v.vehicle_id]),
               sum2: ickPageSum(ickLinesOf(p2, saved)), sum3: ickPageSum(ickLinesOf(p3, saved)) };
    })()""", preview, tmp_path)
    assert got["kinds"] == [[1, "cover", False, False, True, False], [2, "expense", True, True, True, True],
                            [3, "fuel", True, True, False, False], [4, "memo", False, False, False, False]]
    assert got["p2"] == [["p2:l1", "book", "ถุงมือ", 500], ["p2:l3", "book", "ข้าวพนักงาน", 880], ["p2:l2", "deleted", "โอที มะเฟือง 3 ชม.", 120]]
    assert got["p3"] == [["p3:l2", "book", 1500, "v31"], ["p3:l1", "cashfuel", 1500, "v5"]]          # already on the cash-fuel page: last
    assert got["sum2"] == 1380 and got["sum3"] == 3000                                                  # cash fuel counts against the paper


def test_fixes_typed_back_are_no_fix_and_the_payload_is_clean(preview, tmp_path):
    got = run("""(() => {
      const saved = ickCopy(DATA.preview.line_edits);
      const d = ickCopy(saved);
      const out = { start: ickCount(saved, d) };
      ick.saved = saved; ick.draft = d;
      const l1 = () => ickLinesOf(ickPages(DATA).find((p) => p.n === 2), d)[0];
      ickSet(l1(), "amount", "1,450");
      ickSet(l1(), "amount", "500.00");                            // typed back to what was read: nothing to save
      out.same = ickCount(saved, d);
      ickSet(l1(), "amount", "1,450");
      d.edits["p2:l2"] = {};                                       // the deleted line brought back
      d.added.push({ id: "x1", page: 2, date: "2026-11-01", text: "ค่าส่ง", amount: "40", account: "7300", vehicle_id: "" });
      d.added.push({ id: "x2", page: 2, date: "2026-11-01", text: "ลบแล้ว", amount: "5", account: "7900", _gone: true });
      out.n = ickCount(saved, d);
      out.payload = ickPayload(d);
      const p2 = ickPages(DATA).find((p) => p.n === 2);
      out.lines = ickLinesOf(p2, d).map((l) => [l.key, l.state, [...ickChanged(l)]]);
      return out;
    })()""", preview, tmp_path)
    assert got["start"] == 0 and got["same"] == 0 and got["n"] == 3
    assert got["payload"] == {"edits": {"p2:l1": {"amount": "1,450"}},
                              "added": [{"id": "x1", "page": 2, "date": "2026-11-01", "text": "ค่าส่ง", "amount": "40", "account": "7300", "vehicle_id": None}]}
    assert got["lines"] == [["p2:l1", "book", ["amount"]], ["p2:l3", "book", []], ["p2:l2", "book", []],
                            ["p2:x1", "new", ["date", "text", "amount", "account", "vehicle_id"]]]


def test_where_a_line_goes_is_worked_out_as_the_owner_types(preview, tmp_path):
    got = run("""(() => {
      const d = ickCopy(DATA.preview.line_edits);
      const p3 = ickPages(DATA).find((p) => p.n === 3);
      const [booked, cf] = ickLinesOf(p3, d);
      d.edits["p3:l2"] = { account: "6100", vehicle_id: "v5" };
      const fixed = ickLinesOf(p3, d)[0];
      return [ickDest(booked, DATA.accounts, DATA.vehicles), ickDest(cf, DATA.accounts, DATA.vehicles), ickDest(fixed, DATA.accounts, DATA.vehicles),
              ickDestText("1510", "", DATA.accounts, DATA.vehicles), ickDestText("1500", "", DATA.accounts, DATA.vehicles),
              ickHalfStart("2026-01", "mid"), ickHalfStart("2026-11", "end"), /^x[0-9a-z]{1,12}$/.test(ickNewId())];
    })()""", preview, tmp_path)
    assert got[0] == ["book", "บัญชี 6130 น้ำมันเงินสด · รถ #31 (84-4607)"]
    assert got[1] == ["cashfuel", "มีแล้วในน้ำมันเงินสด"]
    assert got[2] == ["book", "บัญชี 6100 ซ่อมรถ · รถ #5 (1ฒถ 2085)"]
    assert got[3:5] == ["บัญชี 1510 เงินกู้พนักงาน", "บัญชี 1500 เงินเบิกล่วงหน้า"]
    assert got[5:] == ["2025-12-26", "2026-11-11", True]
