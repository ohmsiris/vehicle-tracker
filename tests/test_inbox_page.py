"""static/inbox.js (the drop-box page): its pure helpers, run in Node with the page's globals stubbed.

Skipped when Node is not installed. Each test sends a small script that runs after inbox.js in the same context and prints JSON.
"""
import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest

STATIC = Path(__file__).resolve().parent.parent / "static"
NODE = shutil.which("node")
pytestmark = pytest.mark.skipif(NODE is None, reason="Node is not installed")

# the globals inbox.js uses from app.js / board.js / elec.js / accounts.js (only what the tested helpers touch)
PRELUDE = r"""
const TH_MONTHS = ["ม.ค.", "ก.พ.", "มี.ค.", "เม.ย.", "พ.ค.", "มิ.ย.", "ก.ค.", "ส.ค.", "ก.ย.", "ต.ค.", "พ.ย.", "ธ.ค."];
function beDate(iso) { const m = /^(\d{4})-(\d{2})-(\d{2})/.exec(iso || ""); return m ? `${+m[3]} ${TH_MONTHS[+m[2] - 1]} ${+m[1] + 543}` : ""; }
function beDateOf(d) { return `${d.getDate()} ${TH_MONTHS[d.getMonth()]} ${d.getFullYear() + 543}`; }
const beMonth = (ym) => `${TH_MONTHS[+ym.slice(5, 7) - 1]} ${+ym.slice(0, 4) + 543}`;
const fmtMoney = (n) => Number(n || 0).toLocaleString("th-TH", { minimumFractionDigits: 2, maximumFractionDigits: 2 });
const baht = (n) => Number(n || 0).toLocaleString("th-TH", { maximumFractionDigits: 0 });
"""
STUBS = r"""
function el() { return {}; }
function fill() {}
function $() { return null; }
"""

RUNNER = r"""
const vm = require("vm"), fs = require("fs");
const [prelude, page, test] = process.argv.slice(2).map((p) => fs.readFileSync(p, "utf8"));
const ctx = vm.createContext({ console, JSON, Date, Promise, setTimeout, Intl, Number, Math, Map, Set, Object, Array, String, RegExp });
vm.runInContext(prelude, ctx);
vm.runInContext(page, ctx, { filename: "inbox.js" });
Promise.resolve(vm.runInContext(test, ctx)).then((v) => process.stdout.write(JSON.stringify(v)));
"""


def run(test: str, tmp_path, tz: str = "Asia/Bangkok", dom: bool = False):
    files = []
    for name, text in (("prelude.js", PRELUDE + (FAKE_DOM if dom else STUBS)), ("test.js", test), ("runner.js", RUNNER)):
        p = tmp_path / name
        p.write_text(text, encoding="utf-8")
        files.append(p)
    env = {**os.environ, "TZ": tz}
    out = subprocess.run([NODE, str(files[2]), str(files[0]), str(STATIC / "inbox.js"), str(files[1])],
                         capture_output=True, text=True, encoding="utf-8", env=env, timeout=30)
    assert out.returncode == 0, out.stderr
    return json.loads(out.stdout)


def test_conflict_labels_follow_the_key_on_badge_and_button(tmp_path):
    got = run("""(() => {
      const f = (keys, status = "needs_choice") => ({ status, conflicts: keys.map((key) => ({ key })) });
      return [ibxStatusText(f(["month"])), ibxStatusText(f(["branch"])), ibxStatusText(f(["station"])), ibxStatusText(f(["group"])),
              ibxStatusText(f(["half"])), ibxStatusText(f(["duplicate"])), ibxStatusText(f(["replace_old"])), ibxStatusText(f(["other"])),
              ibxStatusText(f([])), ibxChoiceLabel(f(["month", "branch"])), ibxChoiceLabel(f(["x", "y"]))];
    })()""", tmp_path)
    assert got == ["เลือกเดือน", "เลือกสาขา", "เลือกสถานี", "เลือกกลุ่มเงินเดือน", "เลือกครึ่งเดือน", "ยอดซ้ำ — เลือก", "แทนของเดิมไหม",
                   "ต้องเลือก", "ต้องเลือก", "เลือกเดือน · เลือกสาขา", "ต้องเลือก"]


def test_a_superseded_file_says_which_file_replaced_it(tmp_path):
    got = run("""(() => [
      ibxStatusText({ status: "stored", superseded: true, superseded_by: "เช็ค ต.ค. ใหม่.xlsx" }),
      ibxStatusText({ status: "stored", superseded: true }),
      ibxStatusText({ status: "read", error: "ถูกแทนที่ด้วย a.xlsx" }),
      ibxSuperseded({ status: "booked", superseded: false, error: null }),
      ibxSuperseded({ status: "read", error: null }),
    ])()""", tmp_path)
    assert got[0] == "ถูกแทนที่ด้วย เช็ค ต.ค. ใหม่.xlsx"
    assert got[1] == "ถูกแทนที่ด้วยไฟล์ใหม่"
    assert got[2] == "ถูกแทนที่ด้วย a.xlsx"          # an older server's row: only the error text
    assert got[3] is None and got[4] is None


def test_upload_dates_are_the_viewers_local_day(tmp_path):
    got = run("""[ibxWhen("2026-11-25T18:30:00.123456+00:00"), ibxWhen("2026-11-25T16:59:00+00:00"), ibxWhen(""), ibxWhen(null)]""",
              tmp_path, tz="Asia/Bangkok")
    assert got == ["26 พ.ย. 2569", "25 พ.ย. 2569", "", ""]


def test_a_file_over_50_mb_is_skipped_and_the_rest_are_sent(tmp_path):
    got = run("""(() => {
      const MB = 1024 * 1024;
      const list = [{ name: "a.pdf", size: 1 * MB }, { name: "big.pdf", size: 60 * MB }, { name: "b.pdf", size: 2 * MB },
                    { name: "c.pdf", size: 40 * MB }, { name: "d.pdf", size: 1 }, { name: "e.pdf", size: 1 }, { name: "f.pdf", size: 1 },
                    { name: "g.pdf", size: 1 }, { name: "h.pdf", size: 50 * MB }];
      const r = ibxPlanUpload(list);
      return { chunks: r.chunks.map((c) => c.map((x) => x.name)), skipped: r.skipped.map((x) => x.name), msg: ibxTooBig(list[1]) };
    })()""", tmp_path)
    assert got["skipped"] == ["big.pdf"]
    # four per request at most, and never past 50 MB in one request (h is exactly 50 MB: it goes on its own)
    assert got["chunks"] == [["a.pdf", "b.pdf", "c.pdf", "d.pdf"], ["e.pdf", "f.pdf", "g.pdf"], ["h.pdf"]]
    assert "big.pdf" in got["msg"] and "60.0 MB" in got["msg"] and "50.0 MB" in got["msg"] and "ไม่ได้ส่ง" in got["msg"]


def test_poll_end_reports_each_file_by_its_new_status(tmp_path):
    got = run("""(() => {
      const running = new Map([["1", "a.pdf"], ["2", "b.pdf"], ["3", "c.pdf"], ["4", "d.pdf"], ["5", "e.pdf"]]);
      const files = [{ id: "1", file_name: "a.pdf", status: "failed", error: "AI ตอบไม่ได้" },
                     { id: "2", file_name: "b.pdf", status: "read" },
                     { id: "3", file_name: "c.pdf", status: "needs_choice", conflicts: [{ key: "branch" }] },
                     { id: "4", file_name: "d.pdf", status: "stored" }];
      return [ibxPollReport(running, files), ibxPollReport(new Map(), [])];
    })()""", tmp_path)
    msgs, empty = got
    assert msgs[0] == {"text": "a.pdf: AI อ่านไม่สำเร็จ — AI ตอบไม่ได้", "bad": True}
    assert msgs[1]["text"] == "b.pdf: AI อ่านเสร็จแล้ว — เปิดดูก่อนลงบัญชี" and not msgs[1].get("bad")
    assert "เลือกสาขา" in msgs[2]["text"]
    assert "เก็บไว้" in msgs[3]["text"]
    assert msgs[4]["text"].startswith("e.pdf:") and "ไม่อยู่ในรายการ" in msgs[4]["text"]
    assert not any("AI อ่านเสร็จแล้ว — เปิดดูก่อนลงบัญชี" == m["text"] for m in msgs)
    assert len(empty) == 1 and "AI อ่านจบแล้ว" in empty[0]["text"]


def test_checks_numbers_render_the_list_and_tolerate_the_old_dict(tmp_path):
    got = run("""(() => [
      ibxCheckNumbers([{ label: "เข้า", value: 1234567.5, kind: "money" }, { label: "จำนวนคู่", value: 1200, kind: "count" },
                       { label: "ต่าง", value: 1.256, kind: "pct" }, { label: "ผิด", value: { a: 1 }, kind: "money" }, null]),
      ibxCheckNumbers({ account: "1100", in: 1000, out: 250.5, pct: 3.5, pairs: 2, in_by_source: { pea: 1 }, accounts: [1, 2], mystery: 5 }),
      ibxCheckNumbers(null),
      ibxCheckCells({ date: "2026-10-05", text: "ค่าไฟ", amount: 1500, source: "บิลค่าไฟ", source_id: "pea:x", entry_id: 7 }),
      ibxCheckCells({ date: null, text: "x", amount: null, source: "เช็ค" }),
    ])()""", tmp_path)
    new, old, none, cells, cells2 = got
    assert new == [{"label": "เข้า", "text": "1,234,567.50 ฿"}, {"label": "จำนวนคู่", "text": "1,200"}, {"label": "ต่าง", "text": "1.26%"}]
    assert old == [{"label": "เข้า", "text": "1,000.00 ฿"}, {"label": "ออก", "text": "250.50 ฿"}, {"label": "ต่าง", "text": "3.5%"},
                   {"label": "คู่ที่ดูซ้ำ", "text": "2"}]
    assert none == []
    assert [c["text"] for c in cells] == ["5 ต.ค. 2569", "ค่าไฟ", "บิลค่าไฟ", "1,500.00"]
    assert [c["text"] for c in cells2] == ["", "x", "เช็ค", ""]
    assert "[object Object]" not in json.dumps(got, ensure_ascii=False)


def test_month_picker_offers_the_last_18_company_months(tmp_path):
    got = run("""[ibxMonthChoices(new Date(2026, 10, 26)), ibxMonthChoices(new Date(2026, 10, 25)), ibxMonthChoices(new Date(2026, 11, 27))]""", tmp_path)
    after, before, december = got
    assert len(after) == 18 and after[0] == "2026-12" and after[-1] == "2025-07"
    assert before[0] == "2026-11" and before[-1] == "2025-06"
    assert december[0] == "2027-01"


def test_delete_and_reread_confirmations_say_what_happens(tmp_path):
    got = run("""(() => [
      ibxDeleteWarning({ status: "booked", kind: "pea_bill", month: "2026-12" }),
      ibxDeleteWarning({ status: "read", kind: "pea_bill", month: "2026-10" }),
      ibxDeleteWarning({ status: "booked", kind: "card_fuel", label: "น้ำมันบัตรบางจาก (ไฟล์ Bangchak Reader)", month: "2026-10",
                         remove_note: "ถ้าลบไฟล์นี้ น้ำมันบางจากแบบเดิมของ ต.ค. 2569 จะกลับมา" }),
      ibxDeleteWarning({ status: "booked", kind: "card_fuel", label: "น้ำมันบัตร PT (ไฟล์จาก PT)", month: "2026-10" }),
      ibxRereadWarning({ status: "booked" }), ibxRereadWarning({ status: "read" }),
    ])()""", tmp_path)
    pea, pea_unbooked, bangchak, pt, reread_booked, reread_read = got
    assert len(pea) == 2 and "ชุดเช็ค" in pea[1] and "ม.ค. 2570" in pea[1] and "อ่านชุดเช็คนั้นใหม่" in pea[1]
    assert pea_unbooked == []
    assert len(bangchak) == 2 and bangchak[1].startswith("ถ้าลบไฟล์นี้")          # the reader's own note (it knows what comes back)
    assert len(pt) == 1
    assert "ลงบัญชีแล้ว" in reread_booked and "ของเดิมยังอยู่ในบัญชี" in reread_booked          # the server keeps the booking until the new read can book
    assert reread_read == ""


def test_a_second_action_on_the_same_file_waits_for_the_first(tmp_path):
    got = run("""(async () => {
      const calls = [];
      let release;
      const gate = new Promise((r) => { release = r; });
      const first = ibxRun("f1", async () => { calls.push("first"); await gate; });
      const second = await ibxRun("f1", async () => { calls.push("second"); });
      const other = await ibxRun("f2", async () => { calls.push("other file"); });
      const busyWhile = ibx.busy.has("f1");
      release();
      const firstRan = await first;
      const again = await ibxRun("f1", async () => { calls.push("after"); });
      return { calls, second, other, busyWhile, firstRan, again, busyAfter: ibx.busy.has("f1"), msg: ibx.msgs[0] };
    })()""", tmp_path)
    assert got["calls"] == ["first", "other file", "after"]
    assert got["second"] is False and got["other"] is True and got["firstRan"] is True and got["again"] is True
    assert got["busyWhile"] is True and got["busyAfter"] is False
    assert got["msg"] == {"text": "ไฟล์นี้กำลังทำรายการอยู่ รอสักครู่", "bad": True}


def test_a_failed_action_still_frees_the_file(tmp_path):
    got = run("""(async () => {
      try { await ibxRun("f9", async () => { throw new Error("x"); }); } catch (e) { /* the caller's own catch */ }
      return ibx.busy.has("f9");
    })()""", tmp_path)
    assert got is False


# a very small DOM: enough for el()/fill() and the buttons' disabled / dataset, to draw rows, checks and the dialog
FAKE_DOM = r"""
class Node {
  constructor(tag) { this.tag = tag; this.children = []; this.attrs = {}; this.dataset = {}; this.className = ""; this._text = "";
    this.disabled = false; this.hidden = false; this.selected = false; this.checked = false; this.value = ""; this.open = false; this.on = {}; }
  set textContent(v) { this._text = String(v); this.children = []; }
  get textContent() { return this._text + this.children.map((c) => (typeof c === "string" ? c : c.textContent)).join(""); }
  setAttribute(k, v) { this.attrs[k] = v; if (k.startsWith("data-")) this.dataset[k.slice(5).replace(/-(\w)/g, (_, c) => c.toUpperCase())] = v;
    if (k === "value") this.value = v; if (k === "id") this.id = v; }
  addEventListener(k, fn) { this.on[k] = fn; }
  append(...c) { this.children.push(...c); }
  replaceChildren(...c) { this.children = c; }
  replaceWith() {}
  showModal() { this.open = true; }
  close() { this.open = false; }
  all() { return this.children.flatMap((c) => (typeof c === "string" ? [] : [c, ...c.all()])); }
  querySelectorAll(sel) { return this.all().filter((n) => (sel === "[data-ibx-file]" ? "ibxFile" in n.dataset : n.tag === sel)); }
  querySelector(sel) { return sel === "input:checked" ? this.all().find((n) => n.tag === "input" && n.checked) || null : this.querySelectorAll(sel)[0] || null; }
}
const body = new Node("body"), head = new Node("head");
const document = { body, head, createElement: (t) => new Node(t), getElementById: (id) => [head, body].flatMap((n) => [n, ...n.all()]).find((n) => n.id === id) || null,
                   querySelectorAll: (sel) => body.querySelectorAll(sel) };
function el(tag, props = {}, ...children) {
  const node = document.createElement(tag);
  for (const [k, v] of Object.entries(props)) {
    if (k === "class") node.className = v; else if (k === "text") node.textContent = v; else if (k.startsWith("on")) node.addEventListener(k.slice(2), v);
    else if (v === true) node.setAttribute(k, ""); else if (v !== false && v != null) node.setAttribute(k, v);
  }
  for (const c of children) if (c != null) node.append(c);
  return node;
}
function fill(n, ...c) { n.replaceChildren(...c.filter((x) => x != null)); }
function $(id) { return document.getElementById(id); }
const list = el("div", { id: "ibxList" }), checks = el("div", { id: "ibxChecks" }), msg = el("div", { id: "ibxMsg" });
body.append(list, checks, msg, el("select", { id: "acxMonth", value: "2026-11" }), el("select", { id: "acxBranch", value: "" }));
const jsonOpts = (m, b) => ({ method: m, body: JSON.stringify(b) });
const state = { token: "t" };
const calls = [];
let api = async (path) => { calls.push(path); return {}; };
let loadAccounts = async () => { ibxPaintList(); };
let confirm = () => true;
const find = (root, pred) => root.all().filter(pred);
const btns = (root) => find(root, (n) => n.tag === "button");
"""



FILES = """[
  { id: "r1", file_name: "read.xlsx", status: "read", label: "เช็ค", month: "2026-11", uploaded_at: "2026-11-25T18:30:00+00:00", batches: [{}] },
  { id: "s1", file_name: "old.xlsx", status: "stored", superseded: true, superseded_by: "new.xlsx", label: "เช็ค", month: "2026-11", batches: [{}] },
  { id: "c1", file_name: "cheq.xlsx", status: "needs_choice", conflicts: [{ key: "month" }, { key: "branch" }], label: "เช็ค" },
  { id: "b1", file_name: "pea.pdf", kind: "pea_bill", status: "booked", booked_total: 12345, booked_at: "2026-11-25T20:00:00Z", month: "2026-11", batches: [{}] },
]"""


def test_rows_draw_superseded_choices_and_local_dates(tmp_path):
    got = run("""(() => {
      ibx.files = %s; ibxPaintList();
      const rows = list.children;
      const names = (r) => btns(r).filter((b) => b.dataset.ibxFile).map((b) => b.textContent);
      const badge = (r) => find(r, (n) => n.tag === "span" && /badge/.test(n.className))[0].textContent;
      return { read: names(rows[0]), sup: names(rows[1]), supBadge: badge(rows[1]), choice: names(rows[2]), choiceBadge: badge(rows[2]),
               readText: rows[0].textContent, bookedText: rows[3].textContent };
    })()""" % FILES, tmp_path, dom=True)
    assert "ลงบัญชี" in got["read"]
    assert "ลงบัญชี" not in got["sup"] and got["supBadge"] == "ถูกแทนที่ด้วย new.xlsx"
    assert got["choice"][0] == "เลือกเดือน · เลือกสาขา" and got["choiceBadge"] == "เลือกเดือน · เลือกสาขา"
    assert "ส่งเมื่อ 26 พ.ย. 2569" in got["readText"]
    assert "ลงบัญชี 12,345 ฿ · 26 พ.ย. 2569" in got["bookedText"]


def test_double_click_on_book_sends_one_request_and_disables_the_files_buttons(tmp_path):
    got = run("""(async () => {
      ibx.files = %s; ibxPaintList();
      let release;
      api = async (path) => { calls.push(path); if (path.endsWith("/book")) return new Promise((r) => { release = () => r({ booked_total: 1000 }); }); return {}; };
      const book = btns(list.children[0]).find((b) => b.textContent === "ลงบัญชี");
      const first = book.on.click();
      const second = book.on.click();
      await second;
      const during = btns(list.children[0]).filter((b) => b.dataset.ibxFile === "r1").map((b) => b.disabled);
      const others = btns(list.children[3]).filter((b) => b.dataset.ibxFile === "b1").map((b) => b.disabled);
      release(); await first;
      return { calls, during, others, busy: ibx.busy.size, msg: ibx.msgs[0].text };
    })()""" % FILES, tmp_path, dom=True)
    assert got["calls"] == ["/api/accounts/inbox/r1/book"]
    assert got["during"] and all(got["during"]) and not any(got["others"])
    assert got["busy"] == 0 and got["msg"] == "read.xlsx: ลงบัญชีแล้ว 1,000 ฿"


def test_superseded_and_booked_files_ask_before_booking_or_rereading(tmp_path):
    got = run("""(async () => {
      ibx.files = %s;
      const asked = [];
      confirm = (t) => { asked.push(t); return false; };
      await ibxBook(ibx.files[1]);
      await ibxReread(ibx.files[3]);
      await ibxReread(ibx.files[0]);
      await ibxDelete(ibx.files[3]);
      return { calls, asked };
    })()""" % FILES, tmp_path, dom=True)
    assert got["calls"] == ["/api/accounts/inbox/r1/reread"]          # only the unbooked reread went through without asking
    sup, reread, delete = got["asked"]
    assert "ถูกแทนที่ด้วย new.xlsx" in sup and "อีกครั้ง" in sup
    assert "ลงบัญชีแล้ว" in reread
    assert "ชุดเช็ค" in delete and "ธ.ค. 2569" in delete


def test_a_month_conflict_with_no_options_offers_a_month_list(tmp_path):
    got = run("""(async () => {
      const sent = [];
      api = async (path, opts) => {
        calls.push(path);
        if (path.endsWith("/choose")) { sent.push(JSON.parse(opts.body)); return {}; }
        return { id: "c1", file_name: "cheq.xlsx", status: "needs_choice",
                 preview: { conflicts: [{ key: "month", message: "ไม่พบเดือนในไฟล์", options: [] }], batches: [] } };
      };
      await ibxPreview("c1");
      const dlg = $("ibxDialog");
      const sel = find(dlg, (n) => n.tag === "select")[0];
      const choose = btns(dlg).find((b) => /ใช้ตัวเลือกนี้/.test(b.textContent));
      await choose.on.click();                       // nothing picked yet: says so, sends nothing
      const before = sent.length, warn = find(dlg, (n) => n.className === "error")[0];
      sel.value = sel.children[3].value;
      await choose.on.click();
      return { options: sel.children.length, first: sel.children[1].value, before, warn: warn.textContent, sent, label: choose.textContent };
    })()""", tmp_path, dom=True)
    assert got["options"] == 19 and got["before"] == 0 and "ยังไม่ได้เลือก" in got["warn"]
    assert len(got["sent"]) == 1 and list(got["sent"][0]["choices"]) == ["month"]
    assert "เลือกเดือน" in got["label"]


def test_the_checks_card_shows_thai_numbers_only(tmp_path):
    got = run("""(() => {
      ibx.checks = { available: true, checks: [
        { key: "a", title: "บัญชีพักเงินสด", state: "warn", numbers: [{ label: "เข้า", value: 1234567.5, kind: "money" }, { label: "คู่", value: 1200, kind: "count" }],
          rows: [{ date: "2026-10-05", text: "ค่าไฟ", amount: 1500, source: "บิลค่าไฟ" }] },
        { key: "b", title: "แบบเก่า", state: "ok", numbers: { account: "1100", in: 1000, in_by_source: { pea: 1 }, out_by_source: [1] },
          rows: [{ date: "2026-10-05", text: "x", amount: 2, source_id: "pea:1", entry_id: 9 }] }] };
      ibxPaintChecks();
      return checks.textContent;
    })()""", tmp_path, dom=True)
    assert "เข้า 1,234,567.50 ฿" in got and "คู่ 1,200" in got and "เข้า 1,000.00 ฿" in got
    for bad in ("[object Object]", "in_by_source", "account", "source_id", "entry_id", "1100", "pea:1"):
        assert bad not in got, bad
    assert "วันที่" in got and "ที่มา" in got and "5 ต.ค. 2569" in got
