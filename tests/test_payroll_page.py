"""คำนวณเงินเดือน, the links of the unfolded person (owner, 29 Nov 2026): static/payroll.js turns the server's refs into the hash of the
page a value is fixed in, and the pages it opens read their new hash parameters (#loans/emp=, #documents/emp=, #attendance/<month>/emp=,
#employee=<id>/<card>, #accounts-in/check=<file>/page=<n>). Their pure helpers, run in Node. Skipped when Node is not installed."""
import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest

from app import payroll as pw
from tests.test_payroll import MONTH, TODAY, advance_src, factory_pages, helper_src

STATIC = Path(__file__).resolve().parent.parent / "static"
NODE = shutil.which("node")
pytestmark = pytest.mark.skipif(NODE is None, reason="Node is not installed")

RUNNER = r"""
const vm = require("vm"), fs = require("fs");
const [test, data, ...pages] = process.argv.slice(2);
const store = {};
const sessionStorage = { getItem: (k) => (k in store ? store[k] : null), setItem: (k, v) => { store[k] = String(v); }, removeItem: (k) => { delete store[k]; } };
const ctx = vm.createContext({ console, JSON, Date, Promise, Intl, Number, Math, Map, Set, Object, Array, String, RegExp, sessionStorage,
  TH_MONTHS: ["ม.ค.", "ก.พ.", "มี.ค.", "เม.ย.", "พ.ค.", "มิ.ย.", "ก.ค.", "ส.ค.", "ก.ย.", "ต.ค.", "พ.ย.", "ธ.ค."],
  DATA: JSON.parse(fs.readFileSync(data, "utf8")) });
ctx.globalThis = ctx;
for (const p of pages) vm.runInContext(fs.readFileSync(p, "utf8"), ctx, { filename: p });
process.stdout.write(JSON.stringify(vm.runInContext(fs.readFileSync(test, "utf8"), ctx)));
"""
PAGES = ("payroll.js", "loans.js", "documents.js", "attendance.js", "employees.js", "inboxcheck.js")
EMP = "0b6f7c1e-3c3a-4d1e-9b1a-2f4c5d6e7f80"
DRV = "1c7a8d2f-4d4b-4e2f-8c2b-3a5d6e7f8091"
FILE = "2d8b9e30-5e5c-4f30-9d3c-4b6e7f8091a2"
LOAN = "3e9caf41-6f6d-4041-8e4d-5c7f8091a2b3"


def run(test: str, data: dict, tmp_path):
    files = []
    for name, text in (("test.js", test), ("data.json", json.dumps(data, ensure_ascii=False)), ("runner.js", RUNNER)):
        p = tmp_path / name
        p.write_text(text, encoding="utf-8")
        files.append(p)
    out = subprocess.run([NODE, str(files[2]), str(files[0]), str(files[1]), *[str(STATIC / p) for p in PAGES]],
                         capture_output=True, text=True, encoding="utf-8", env={**os.environ, "TZ": "Asia/Bangkok"}, timeout=30)
    assert out.returncode == 0, out.stderr
    return json.loads(out.stdout)


def test_each_ref_becomes_the_hash_of_its_page(tmp_path):
    refs = [{"to": "rules"}, {"to": "employee", "emp": EMP, "part": "rates"}, {"to": "employee", "emp": EMP, "part": "pay"},
            {"to": "driver-days", "emp": EMP}, {"to": "driver-days", "emp": DRV, "say": "ของคนขับ"}, {"to": "attendance", "emp": EMP},
            {"to": "check", "file": FILE, "n": 9, "say": "ใบเบิกกลางเดือน"}, {"to": "loan", "id": LOAN}, {"to": "loans", "emp": EMP},
            {"to": "document", "id": LOAN, "say": "ใบเตือน 04/11/2026"}, {"to": "documents", "emp": EMP}, {"to": "cash-fuel"},
            {"to": "accounts-in", "say": "บัตรน้ำมัน"}, {"to": "accounts-in"}]
    got = run("DATA.refs.map((r) => pwGoHref(r, '2026-11'))", {"refs": refs}, tmp_path)
    assert got == [
        {"edit": True, "label": "แก้ในหน้านี้"},
        {"href": f"#employee={EMP}/rates", "label": "ค่าแรง หน้าพนักงาน"}, {"href": f"#employee={EMP}/pay", "label": "ใบ HQ หน้าพนักงาน"},
        {"href": f"#driver-days/{EMP}/2026-11", "label": "ยอดขายรายวัน"}, {"href": f"#driver-days/{DRV}/2026-11", "label": "ยอดขายรายวัน ของคนขับ"},
        {"href": f"#attendance/2026-11/emp={EMP}", "label": "เวลาทำงาน"},
        {"href": f"#accounts-in/check={FILE}/page=9", "label": "ใบเบิกกลางเดือน หน้า 9"},
        {"href": f"#loan={LOAN}", "label": "เงินกู้"}, {"href": f"#loans/emp={EMP}", "label": "เงินกู้ของคนนี้"},
        {"href": f"#document={LOAN}", "label": "ใบเตือน 04/11/2026"}, {"href": f"#documents/emp={EMP}", "label": "ใบเตือนของคนนี้"},
        {"href": "#cash-fuel/2026-11", "label": "น้ำมันเงินสด"}, {"href": "#accounts-in", "label": "บัตรน้ำมัน (กล่องรับไฟล์)"},
        {"href": "#accounts-in", "label": "กล่องรับไฟล์"}]


def test_a_ref_that_is_not_a_page_of_this_site_gives_no_link(tmp_path):
    bad = [None, "x", {}, {"to": "nowhere"}, {"to": "employee", "emp": EMP, "part": "bank"}, {"to": "employee", "emp": "javascript:alert(1)", "part": "pay"},
           {"to": "loan", "id": "1 OR 1=1"}, {"to": "check", "file": FILE, "n": 0}, {"to": "check", "file": FILE, "n": "3"}, {"to": "check", "file": "f1", "n": 3},
           {"to": "loans", "emp": f"{EMP}/../x"}]
    assert run("DATA.bad.map((r) => pwGoHref(r, '2026-11'))", {"bad": bad}, tmp_path) == [None] * len(bad)
    # no month: the pages that are opened on a month give no link
    got = run("[pwGoHref({ to: 'attendance', emp: DATA.e }, ''), pwGoHref({ to: 'driver-days', emp: DATA.e }, 'x'), pwGoHref({ to: 'cash-fuel' }, '')]", {"e": EMP}, tmp_path)
    assert got == [None, None, {"href": "#cash-fuel", "label": "น้ำมันเงินสด"}]


def test_the_servers_refs_round_trip_to_links(tmp_path):
    out = pw.build(advance_src(factory_pages()), MONTH, TODAY)
    p = next(x for x in out["people"] if x["code"] == "SB-FR001")
    person = json.loads(json.dumps(p, default=str).replace('"f1"', f'"{FILE}"').replace('"SB-FR001"', f'"{EMP}"'))
    helper = next(x for x in pw.build(helper_src([]), MONTH, TODAY)["people"] if x["code"] == "H1")
    helper = json.loads(json.dumps(helper, default=str).replace('"H1"', f'"{EMP}"').replace('"D1"', f'"{DRV}"'))
    got = run("""({ advance: pwGoLinks(DATA.p, "advance", "2026-11"), loan: pwGoLinks(DATA.p, "loan", "2026-11"), none: pwGoLinks(DATA.p, "nobonus", "2026-11"),
                    days: pwGoLinks(DATA.h, "days", "2026-11") })""", {"p": person, "h": helper}, tmp_path)
    assert got["advance"] == [{"href": f"#accounts-in/check={FILE}/page=9", "label": "ใบเบิกกลางเดือน หน้า 9"}]
    assert got["loan"] == [{"href": f"#loans/emp={EMP}", "label": "เงินกู้ของคนนี้"}] and got["none"] == []
    assert got["days"] == [{"href": f"#attendance/2026-11/emp={EMP}", "label": "เวลาทำงาน"},
                           {"href": f"#driver-days/{DRV}/2026-11", "label": "ยอดขายรายวัน ของคนขับ"}, {"edit": True, "label": "แก้ในหน้านี้"}]


def test_the_pay_page_hash_and_the_way_back(tmp_path):
    got = run("""(() => {
      const before = pwBackMarker();
      pwRemember("2026-11", { id: DATA.e, code: "SB-FR001", name: "คน ทดสอบ", nickname: "" });
      const back = pwBackMarker();
      const later = pwBackMarker(Date.now() + 9 * 3600e3);
      pwForget();
      return { parse: [pwParseSub(""), pwParseSub("2026-11"), pwParseSub(`2026-11/emp=${DATA.e}`), pwParseSub("2026-13/emp=x"), pwParseSub("2026-11/emp=../x")],
               hash: [pwBackHash("2026-11", DATA.e), pwBackHash("2026-11", null), pwBackHash("", DATA.e), pwBackHash("2026-11", "x")],
               before, back, later, gone: pwBackMarker(), say: pwMonthShort("2026-11") };
    })()""", {"e": EMP}, tmp_path)
    assert got["parse"] == [{"month": "", "emp": None}, {"month": "2026-11", "emp": None}, {"month": "2026-11", "emp": EMP},
                            {"month": "", "emp": None}, {"month": "2026-11", "emp": None}]
    assert got["hash"] == [f"#payroll/2026-11/emp={EMP}", "#payroll/2026-11", "#payroll", "#payroll/2026-11"]
    assert got["before"] is None and got["back"] == {"month": "2026-11", "emp": EMP, "who": "SB-FR001 คน ทดสอบ"}
    assert got["later"] is None and got["gone"] is None and got["say"] == "พ.ย. 2026"


def test_the_rule_form_sends_only_what_changed(tmp_path):
    rule = {"pay_group": "DAY", "commission_pct": None, "bag_rate": 3, "bonus": False, "rate2": "344", "rides_with": None, "sso": 750}
    got = run("""[
      pwRuleChanges(DATA.r, { pay_group: "DAY", commission_pct: "", bag_rate: "3", bonus: false, rate2: "344", rides_with: "", sso: "750" }),
      pwRuleChanges(DATA.r, { pay_group: "DRV-A", commission_pct: "5", bag_rate: "3.5", bonus: true, rate2: "", rides_with: DATA.e, sso: "0" }),
      pwRuleChanges(DATA.r, { sso: "abc", rate2: "1,344" }),
      pwRuleChanges(null, { pay_group: "", sso: "0" }),
      pwRuleChanges(null, { pay_group: "DAY", bonus: false, rides_with: "" }),
    ]""", {"r": rule, "e": EMP}, tmp_path)
    assert got == [{}, {"pay_group": "DRV-A", "commission_pct": 5, "bag_rate": 3.5, "bonus": True, "rate2": None, "rides_with": EMP, "sso": 0},
                   {"sso": "abc", "rate2": 1344}, {"sso": 0}, {"pay_group": "DAY"}]


def test_the_pages_it_opens_read_their_new_hash(tmp_path):
    got = run("""({
      loans: [lnParseSub(`emp=${DATA.e}`), lnParseSub(""), lnParseSub("emp=x"), lnParseSub(`emp=${DATA.e}/x`)],
      docs: [dcParseSub(`emp=${DATA.e}`), dcParseSub("emp=")],
      att: [atParseSub(`2026-11/emp=${DATA.e}`), atParseSub("2026-11"), atParseSub(""), atParseSub(`x/emp=${DATA.e}`)],
      emp: [empParseHash(`employee=${DATA.e}`), empParseHash(`employee=${DATA.e}/rates`), empParseHash(`employee=${DATA.e}/pay`),
            empParseHash(`employee=${DATA.e}/bank`), empParseHash("employee=x")],
      check: [ickParseHash(`accounts-in/check=${DATA.f}`), ickParseHash(`accounts-in/check=${DATA.f}/page=9`), ickParseHash(`accounts-in/check=${DATA.f}/page=0`),
              ickParseHash(`accounts-in/check=${DATA.f}/page=x`), ickParseHash("accounts-in/check=f1"), ickParseHash("accounts-in")],
    })""", {"e": EMP, "f": FILE}, tmp_path)
    assert got["loans"] == [EMP, None, None, None] and got["docs"] == [EMP, None]
    assert got["att"] == [{"month": "2026-11", "emp": EMP}, {"month": "2026-11", "emp": None}, {"month": "", "emp": None}, {"month": "", "emp": EMP}]
    assert got["emp"] == [{"id": EMP, "part": ""}, {"id": EMP, "part": "rates"}, {"id": EMP, "part": "pay"}, None, None]
    assert got["check"] == [{"id": FILE, "page": None}, {"id": FILE, "page": 9}, None, None, None, None]


def test_the_router_and_the_bar_are_wired():
    fleet = (STATIC / "fleet.js").read_text(encoding="utf-8")
    for needle in ('h.startsWith("loans/")) return openLoans(h.slice(6))', 'h.startsWith("attendance/")) return openAttendance(h.slice(11))',
                   'h.startsWith("documents/")) return openDocuments(h.slice(10))', "empParseHash(h)", "ickParseHash(h)", "openInboxCheck(ick_.id, ick_.page)"):
        assert needle in fleet, needle
    assert 'id="pwBack"' in (STATIC / "index.html").read_text(encoding="utf-8")
    assert "pwBackBar(name)" in (STATIC / "app.js").read_text(encoding="utf-8")
    js = (STATIC / "payroll.js").read_text(encoding="utf-8")
    assert "/api/payroll/rules/" in js and "bank" not in js
