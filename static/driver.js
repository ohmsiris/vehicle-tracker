"use strict";
/* สายรถ · ใบขาย -- the driver's phone (app/route_sales.py). Plain JS for old phones (no ?. or ??), no libraries.

   The day lives on the phone first: every tap saves to localStorage at once and the whole day is sent to the server shortly after
   (and again when the signal comes back). `rev` counts the phone's saves; the server ignores a save older than what it has.
   Screens: sign in (name, then a 4-digit PIN) -> the day with three tabs: รับน้ำแข็ง (loads) · ลูกค้า (customers) · ปิดวัน (close).
*/
(function () {
  var TOKEN_KEY = "icebox_driver_token";
  var BOOT_KEY = "icebox_driver_boot";
  var S = { token: null, boot: null, day: null, doc: null, rev: 0, savedRev: 0, status: "open", tab: "customers", q: "",
            syncing: false, syncTimer: null, failCount: 0 };

  // ------------------------------------------------------------------ small helpers
  function $(id) { return document.getElementById(id); }
  function esc(s) { return String(s == null ? "" : s).replace(/[&<>"']/g, function (c) { return { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]; }); }
  function n(x) { var v = parseFloat(String(x == null ? "" : x).replace(/,/g, "")); return isFinite(v) ? v : 0; }
  function fmt(v, d) { return n(v).toLocaleString("th-TH", { minimumFractionDigits: d || 0, maximumFractionDigits: d || 0 }); }
  function store(k, v) { try { if (v == null) localStorage.removeItem(k); else localStorage.setItem(k, JSON.stringify(v)); } catch (e) { /* full / blocked */ } }
  function load(k) { try { var v = localStorage.getItem(k); return v ? JSON.parse(v) : null; } catch (e) { return null; } }
  function nowHM() { var d = new Date(); return ("0" + d.getHours()).slice(-2) + ":" + ("0" + d.getMinutes()).slice(-2); }
  var TH_DAYS = ["อาทิตย์", "จันทร์", "อังคาร", "พุธ", "พฤหัสฯ", "ศุกร์", "เสาร์"];
  var TH_MON = ["ม.ค.", "ก.พ.", "มี.ค.", "เม.ย.", "พ.ค.", "มิ.ย.", "ก.ค.", "ส.ค.", "ก.ย.", "ต.ค.", "พ.ย.", "ธ.ค."];
  function thDate(iso) { var p = iso.split("-"); var d = new Date(+p[0], +p[1] - 1, +p[2]); return "วัน" + TH_DAYS[d.getDay()] + " " + (+p[2]) + " " + TH_MON[+p[1] - 1] + " " + (+p[0] + 543); }
  function addDays(iso, k) { var p = iso.split("-"); var d = new Date(Date.UTC(+p[0], +p[1] - 1, +p[2] + k)); return d.toISOString().slice(0, 10); }
  var toastT = null;
  function toast(msg, bad) { var t = $("toast"); t.textContent = msg; t.className = "toast" + (bad ? " bad" : ""); t.hidden = false;
    clearTimeout(toastT); toastT = setTimeout(function () { t.hidden = true; }, bad ? 4500 : 2200); }

  function api(method, path, body) {
    var h = { "Content-Type": "application/json" };
    if (S.token) h["X-Driver-Token"] = S.token;
    return fetch(path, { method: method, headers: h, body: body ? JSON.stringify(body) : undefined, cache: "no-store" }).then(function (r) {
      return r.json().catch(function () { return {}; }).then(function (j) {
        if (r.status === 401) { signedOut(); throw new Error("signin"); }
        if (!r.ok) { var e = new Error(j.detail || ("ผิดพลาด " + r.status)); e.status = r.status; throw e; }
        return j;
      });
    });
  }

  // ------------------------------------------------------------------ products, prices, customers
  function products() { return (S.boot && S.boot.products) || []; }
  function label(k) { var p = products().filter(function (x) { return x.key === k; })[0]; return p ? p.label : k; }
  function customers() {
    var list = (S.boot.customers || []).slice();
    (S.doc.new_customers || []).forEach(function (c) { list.push({ id: c.key, seq: 900 + list.length, name: c.name, prices: c.prices || {}, billing: "cash", fresh: true }); });
    return list;
  }
  function custById(id) { return customers().filter(function (c) { return String(c.id) === String(id); })[0]; }
  function priceOf(c, sale, k) {
    if (sale && sale.price && sale.price[k] != null && sale.price[k] !== "") return n(sale.price[k]);
    var p = (c && c.prices) || {}; return p[k] != null ? n(p[k]) : null;
  }
  function amountOf(c, sale) {
    var t = 0; products().forEach(function (p) { var q = n(sale && sale.q && sale.q[p.key]); if (q) t += q * (priceOf(c, sale, p.key) || 0); });
    return Math.round(t * 100) / 100;
  }
  function paidOf(c, sale) {
    if (c.billing === "invoice") return 0;
    var a = amountOf(c, sale);
    if (sale.pay === "credit") return 0;
    if (sale.pay === "part") return Math.min(n(sale.paid), a);
    return a;
  }
  function totals() {
    var loaded = {}, sold = {}, cash = 0, credit = 0, inv = 0, old = 0, amt = 0;
    products().forEach(function (p) { loaded[p.key] = 0; sold[p.key] = 0; });
    (S.doc.loads || []).forEach(function (ld) { products().forEach(function (p) { loaded[p.key] += n(ld.q && ld.q[p.key]); }); });
    Object.keys(S.doc.sales || {}).forEach(function (id) {
      var s = S.doc.sales[id], c = custById(id); if (!c) return;
      products().forEach(function (p) { sold[p.key] += n(s.q && s.q[p.key]); });
      var a = amountOf(c, s); amt += a;
      if (c.billing === "invoice") inv += a; else { var pd = paidOf(c, s); cash += pd; credit += a - pd; }
      old += n(s.old_paid);
    });
    return { loaded: loaded, sold: sold, cash: cash, credit: credit, invoiced: inv, old: old, amount: amt };
  }
  function balanceOf(id) { return n((S.boot.balances || {})[String(id)]); }

  // ------------------------------------------------------------------ the day on the phone, and sending it
  function dayKey() { return "icebox_day_" + S.boot.route.id + "_" + S.day; }
  function emptyDoc() { return { loads: [], sales: {}, new_customers: [], returns: null, km_start: S.boot.last_km || null, km_end: null, cash: null, closed: false }; }
  function persist() { store(dayKey(), { doc: S.doc, rev: S.rev, savedRev: S.savedRev, status: S.status }); }
  function changed() {
    if (S.status === "checked") return;
    S.rev += 1; persist(); paintSync();
    clearTimeout(S.syncTimer); S.syncTimer = setTimeout(sync, 700);
  }
  function sync() {
    if (S.syncing || S.savedRev >= S.rev) { paintSync(); return; }
    if (navigator.onLine === false) { paintSync(); return; }
    S.syncing = true; paintSync();
    var rev = S.rev, day = S.day;
    api("PUT", "/api/d/day", { day: day, rev: rev, doc: S.doc }).then(function (r) {
      S.syncing = false; S.failCount = 0;
      if (day !== S.day) return;
      S.savedRev = Math.max(S.savedRev, r.rev || rev); S.status = r.status || S.status;
      var map = r.id_map || {}, keys = Object.keys(map);
      if (keys.length) {                          // new customers the office now knows: swap the phone's temporary keys
        keys.forEach(function (k) { if (S.doc.sales[k]) { S.doc.sales[map[k]] = S.doc.sales[k]; delete S.doc.sales[k]; } });
        S.doc.new_customers = (S.doc.new_customers || []).filter(function (c) { return !map[c.key]; });
        refreshBoot();
      }
      persist(); paintSync();
      if (S.savedRev < S.rev) sync();
    }).catch(function (e) {
      S.syncing = false;
      if (e.message === "signin") return;
      if (e.status === 422) { toast(e.message, true); S.savedRev = S.rev; persist(); paintSync(); return; }
      S.failCount += 1; paintSync();
      clearTimeout(S.syncTimer); S.syncTimer = setTimeout(sync, Math.min(60000, 3000 * S.failCount));
    });
  }
  window.addEventListener("online", function () { sync(); });
  setInterval(function () { if (S.boot && S.savedRev < S.rev) sync(); }, 30000);

  function paintSync() {
    var el = $("sync"); if (!el) return;
    if (S.status === "checked") { el.className = "sync"; el.textContent = "ออฟฟิศตรวจแล้ว"; return; }
    if (S.savedRev >= S.rev) { el.className = "sync"; el.textContent = "✓ ส่งแล้ว"; return; }
    if (navigator.onLine === false || S.failCount) { el.className = "sync off"; el.textContent = "• เก็บในเครื่อง รอสัญญาณ"; return; }
    el.className = "sync wait"; el.textContent = "กำลังส่ง…";
  }

  function openDay(day, sheet) {
    S.day = day;
    var local = load(dayKey());
    var server = sheet || null;
    if (local && (!server || local.rev > server.rev)) { S.doc = local.doc; S.rev = local.rev; S.savedRev = server ? server.rev : (local.savedRev || 0); S.status = local.status || "open"; }
    else if (server) { S.doc = server.doc || emptyDoc(); S.rev = server.rev; S.savedRev = server.rev; S.status = server.status; }
    else { S.doc = emptyDoc(); S.rev = 0; S.savedRev = 0; S.status = "open"; }
    if (!S.doc.sales) S.doc.sales = {};
    if (!S.doc.loads) S.doc.loads = [];
    if (S.doc.km_start == null && S.boot.last_km) S.doc.km_start = S.boot.last_km;
    persist(); render();
    if (S.savedRev < S.rev) sync();
  }
  function goDay(day) {
    if (S.savedRev < S.rev) sync();
    if (day === S.boot.day) { openDay(day, S.boot.sheet); return; }
    $("app").querySelector("main").innerHTML = '<div class="empty">กำลังเปิด…</div>';
    api("GET", "/api/d/me?day=" + day).then(function (b) { S.boot = b; store(BOOT_KEY, b); openDay(day, b.sheet); })
      .catch(function (e) { if (e.message !== "signin") { toast("ไม่มีสัญญาณ — เปิดวันอื่นไม่ได้ตอนนี้", true); openDay(S.day, null); } });
  }
  function refreshBoot() {
    api("GET", "/api/d/me?day=" + S.day).then(function (b) { var keep = S.doc; S.boot = b; store(BOOT_KEY, b); S.doc = keep; render(); }).catch(function () {});
  }

  // ------------------------------------------------------------------ sign in
  function signedOut() { S.token = null; store(TOKEN_KEY, null); showSignIn(); }
  function showSignIn() {
    closeSheet();
    var app = $("app");
    app.innerHTML = '<div class="signin"><h1>ใบขายสายรถ</h1><p>เลือกชื่อของคุณ</p><div class="names" id="names"><div class="empty">กำลังโหลด…</div></div></div>';
    api("GET", "/api/d/drivers").then(function (r) {
      var list = r.drivers || [];
      $("names").innerHTML = list.length ? list.map(function (d) {
        return '<button class="name-btn" data-id="' + d.id + '" data-name="' + esc(d.name) + '"><b>' + esc(d.truck_no) + '</b><span>' + esc(d.name) + '<small>' + esc(d.route) + '</small></span></button>';
      }).join("") : '<div class="empty">ยังไม่มีชื่อ — ให้ออฟฟิศตั้งรหัสก่อน</div>';
    }).catch(function () { $("names").innerHTML = '<div class="empty">ต้องต่ออินเทอร์เน็ตตอนเข้าครั้งแรก</div>'; });
    $("names").onclick = function (e) { var b = e.target.closest(".name-btn"); if (b) showPin(+b.getAttribute("data-id"), b.getAttribute("data-name")); };
  }
  function showPin(id, name) {
    var pin = "";
    var app = $("app");
    app.innerHTML = '<div class="signin"><h1>' + esc(name) + '</h1><p>ใส่รหัส 4 หลัก</p><div class="pin"><div class="dots" id="dots"><i></i><i></i><i></i><i></i></div>' +
      '<div class="err" id="perr"></div><div class="pad" id="pad">' + [1, 2, 3, 4, 5, 6, 7, 8, 9].map(function (k) { return '<button data-k="' + k + '">' + k + '</button>'; }).join("") +
      '<button data-k="back">←</button><button data-k="0">0</button><button data-k="del">⌫</button></div></div></div>';
    function paint() { var dots = $("dots").children; for (var i = 0; i < 4; i++) dots[i].className = i < pin.length ? "on" : ""; }
    $("pad").onclick = function (e) {
      var b = e.target.closest("button"); if (!b) return;
      var k = b.getAttribute("data-k");
      if (k === "back") { showSignIn(); return; }
      if (k === "del") { pin = pin.slice(0, -1); paint(); return; }
      if (pin.length >= 4) return;
      pin += k; paint();
      if (pin.length === 4) {
        $("perr").textContent = "กำลังตรวจ…";
        api("POST", "/api/d/login", { driver_id: id, pin: pin }).then(function (r) { S.token = r.token; store(TOKEN_KEY, r.token); start(); })
          .catch(function (err) { pin = ""; paint(); $("perr").textContent = err.message === "Failed to fetch" ? "ไม่มีสัญญาณ ลองใหม่" : err.message; });
      }
    };
  }

  // ------------------------------------------------------------------ the day screen
  function render() {
    var b = S.boot, app = $("app");
    var isToday = S.day === b.today;
    app.innerHTML =
      '<div class="top" id="top"><div class="top-row"><div class="route">สาย ' + esc(b.route.truck_no) + ' · ' + esc(b.driver.name) + '</div><div class="sync" id="sync"></div></div>' +
      '<div class="date-row"><button id="prev" aria-label="วันก่อน">‹</button><div class="d">' + thDate(S.day) + (isToday ? "" : " (ย้อนหลัง)") + '</div>' +
      '<button id="next" aria-label="วันถัดไป"' + (S.day >= b.today ? " disabled" : "") + '>›</button></div></div>' +
      '<div class="stock" id="stock"></div><main id="main"></main>' +
      '<nav class="tabs"><button data-tab="loads" class="' + (S.tab === "loads" ? "on" : "") + '"><span class="ic">🧊</span>รับน้ำแข็ง</button>' +
      '<button data-tab="customers" class="' + (S.tab === "customers" ? "on" : "") + '"><span class="ic">🏪</span>ลูกค้า</button>' +
      '<button data-tab="close" class="' + (S.tab === "close" ? "on" : "") + '"><span class="ic">✅</span>ปิดวัน</button></nav>';
    $("prev").onclick = function () { if (S.day > addDays(b.today, -7)) goDay(addDays(S.day, -1)); };
    $("next").onclick = function () { if (S.day < b.today) goDay(addDays(S.day, 1)); };
    app.querySelector(".tabs").onclick = function (e) { var t = e.target.closest("button"); if (!t) return; S.tab = t.getAttribute("data-tab"); render(); window.scrollTo(0, 0); };
    document.documentElement.style.setProperty("--top-h", $("top").offsetHeight + "px");
    paintStock(); paintSync();
    if (S.tab === "loads") renderLoads(); else if (S.tab === "close") renderClose(); else renderCustomers();
  }
  function paintStock() {
    var t = totals(), parts = [];
    products().forEach(function (p) {
      var left = t.loaded[p.key] - t.sold[p.key];
      if (t.loaded[p.key] || t.sold[p.key]) parts.push(esc(p.label) + ' <b class="' + (left < 0 ? "neg" : "") + '">' + fmt(left) + "</b>");
    });
    $("stock").innerHTML = '<span class="lbl">บนรถ</span> ' + (parts.length ? parts.join(" · ") : '<span class="lbl">ยังไม่ได้รับน้ำแข็ง</span>') +
      '<span class="cash">เงินสด ฿' + fmt(t.cash + t.old) + "</span>";
  }
  function readOnly() { return S.status === "checked"; }
  function statusBanner() {
    if (S.status === "checked") return '<div class="banner ok">ออฟฟิศตรวจวันนี้แล้ว — แก้ไม่ได้</div>';
    if (S.status === "closed" || S.doc.closed) return '<div class="banner ok">ปิดวันแล้ว · ยังแก้ได้จนกว่าออฟฟิศตรวจ</div>';
    return "";
  }

  // ---- customers
  function renderCustomers() {
    var m = $("main");
    m.innerHTML = statusBanner() + '<input class="search" id="q" type="search" placeholder="ค้นหาร้าน หรือเลขที่" value="' + esc(S.q) + '" autocomplete="off">' +
      '<div class="list" id="list"></div>' + (readOnly() ? "" : '<button class="add" id="addc">+ เพิ่มลูกค้าใหม่</button>');
    $("q").oninput = function () { S.q = this.value; paintList(); };
    $("list").onclick = function (e) { var r = e.target.closest(".cust"); if (r) openSale(r.getAttribute("data-id")); };
    if ($("addc")) $("addc").onclick = openNewCustomer;
    paintList();
  }
  function paintList() {
    var q = S.q.trim().toLowerCase();
    var rows = customers().filter(function (c) { return !q || (c.name + " " + c.seq + " " + (c.bins || "")).toLowerCase().indexOf(q) >= 0; });
    $("list").innerHTML = rows.length ? rows.map(function (c) {
      var s = S.doc.sales[String(c.id)], sold = s && (Object.keys(s.q || {}).some(function (k) { return n(s.q[k]); }) || n(s.old_paid));
      var meta = [];
      if (c.billing === "invoice") meta.push('<span class="chip">วางบิล</span>');
      if (c.fresh || c.approved === false) meta.push('<span class="chip warn">ลูกค้าใหม่</span>');
      var bal = balanceOf(c.id); if (bal > 0) meta.push('<span class="chip warn">ค้าง ฿' + fmt(bal) + "</span>");
      if (c.bins) meta.push(esc(c.bins));
      var right;
      if (sold) {
        var what = products().filter(function (p) { return n(s.q && s.q[p.key]); }).map(function (p) { return p.label + " " + fmt(s.q[p.key]); }).join(" · ");
        right = '<span class="right"><b>฿' + fmt(amountOf(c, s)) + "</b>" + esc(what) + (s.pay === "credit" ? " · ค้าง" : "") + "</span>";
      } else {
        var pr = c.prices || {}, first = ["lod", "lek", "mo", "pack", "zong", "kak"].filter(function (k) { return pr[k] != null; })[0];   // the bag price first
        right = '<span class="right">' + (first ? fmt(pr[first]) + "฿" + (first === "pack" || first === "zong" || first === "kak" ? '<br><span class="note">' + esc(label(first)) + "</span>" : "") : "") + "</span>";
      }
      return '<button class="cust' + (sold ? " done" : "") + '" data-id="' + esc(c.id) + '"><span class="no">' + (c.fresh ? "+" : c.seq) + '</span><span class="mid"><span class="nm">' +
        esc(c.name) + '</span><span class="meta">' + meta.join(" ") + "</span></span>" + right + "</button>";
    }).join("") : '<div class="empty">ไม่พบ</div>';
  }

  // ---- one customer's sale (full-screen sheet)
  function openSheet(html) { var sh = $("sheet"); sh.innerHTML = html; sh.hidden = false; sh.scrollTop = 0; document.body.style.overflow = "hidden"; }
  function closeSheet() { var sh = $("sheet"); if (!sh) return; sh.hidden = true; sh.innerHTML = ""; document.body.style.overflow = ""; }
  function stepper(id, val) {
    return '<div class="step"><button data-step="' + id + '" data-d="-1" aria-label="ลด">−</button><input class="num" id="' + id + '" inputmode="numeric" pattern="[0-9]*" value="' +
      (val ? val : "") + '" placeholder="0"><button data-step="' + id + '" data-d="1" aria-label="เพิ่ม">+</button></div>';
  }
  function bindSteppers(root, onChange) {
    root.addEventListener("click", function (e) {
      var b = e.target.closest("button[data-step]"); if (!b) return;
      var inp = $(b.getAttribute("data-step")); var v = Math.max(0, n(inp.value) + n(b.getAttribute("data-d")));
      inp.value = v || ""; onChange();
    });
    root.addEventListener("input", function (e) { if (e.target.classList.contains("num")) onChange(); });
    root.addEventListener("focusin", function (e) { if (e.target.classList.contains("num")) setTimeout(function () { try { e.target.select(); } catch (x) {} }, 0); });
  }

  function openSale(id) {
    var c = custById(id); if (!c) return;
    var s = JSON.parse(JSON.stringify(S.doc.sales[String(id)] || { q: {}, pay: "cash" }));
    if (!s.q) s.q = {};
    if (!s.pay) s.pay = "cash";
    var showAll = false;
    var ro = readOnly();
    function draw() {
      var pr = c.prices || {};
      var main = products().filter(function (p) { return pr[p.key] != null || n(s.q[p.key]); });
      if (!main.length) main = products().filter(function (p) { return p.key === "lod"; });
      var rest = products().filter(function (p) { return main.indexOf(p) < 0; });
      var list = showAll ? main.concat(rest) : main;
      var bal = balanceOf(c.id);
      var invoice = c.billing === "invoice";
      var html = '<div class="sheet-head"><h2>' + (c.fresh ? "" : c.seq + ". ") + esc(c.name) + "<small>" + esc(c.bins || "") + '</small></h2><button class="x" id="sx" aria-label="ปิด">×</button></div><div class="sheet-body" id="sb">';
      if (invoice) html += '<div class="banner warn">วางบิล — ให้ลูกค้าเซ็นบิล ไม่ต้องเก็บเงิน</div>';
      html += '<div class="box">' + list.map(function (p) {
        var price = priceOf(c, s, p.key);
        return '<div class="prow"><div><div class="pl">' + esc(p.label) + '</div><div class="pp' + (price == null && n(s.q[p.key]) ? " need" : "") + '"><button data-price="' + p.key + '">' +
          (price == null ? "ใส่ราคา" : "฿" + fmt(price, price % 1 ? 2 : 0) + " / หน่วย") + "</button></div></div>" + stepper("q_" + p.key, n(s.q[p.key])) + "</div>";
      }).join("") + (!showAll && rest.length ? '<button class="more" id="more">+ สินค้าอื่น (' + rest.map(function (p) { return p.label; }).join(" ") + ")</button>" : "") + "</div>";
      var amount = amountOf(c, s);
      html += '<div class="box"><div class="total"><span>รวม</span><b id="amt">฿' + fmt(amount, amount % 1 ? 2 : 0) + "</b></div>";
      if (!invoice) {
        html += '<div class="seg" id="pay"><button data-pay="cash" class="' + (s.pay === "cash" ? "on" : "") + '">จ่ายสด</button><button data-pay="credit" class="credit ' + (s.pay === "credit" ? "on credit" : "") +
          '">ค้าง</button><button data-pay="part" class="' + (s.pay === "part" ? "on" : "") + '">จ่ายบางส่วน</button></div>';
        if (s.pay === "part") html += '<div class="line"><span class="lbl">รับเงินมา</span><input class="num wide" id="paid" inputmode="decimal" value="' + (s.paid ? s.paid : "") + '" placeholder="0"></div>';
      }
      html += "</div>";
      if (!invoice && (bal > 0 || n(s.old_paid))) {
        html += '<div class="box"><div class="line"><span>หนี้เก่า</span><b>฿' + fmt(bal) + '</b></div><div class="line"><span class="lbl">จ่ายหนี้เก่าวันนี้</span><input class="num wide" id="oldp" inputmode="decimal" value="' +
          (s.old_paid ? s.old_paid : "") + '" placeholder="0"></div>' + (bal > 0 ? '<button class="more" id="payall">จ่ายหมด ฿' + fmt(bal) + "</button>" : "") + "</div>";
      }
      html += "</div>";
      html += ro ? '<div class="actions one"><button class="btn" id="sclose">ปิด</button></div>'
        : '<div class="actions"><button class="btn danger" id="sclear">ล้าง</button><button class="btn primary" id="ssave">บันทึก › ร้านถัดไป</button></div>';
      openSheet(html);
      var root = $("sheet");
      $("sx").onclick = function () { closeSheet(); };
      if (ro) { $("sclose").onclick = closeSheet; return; }
      if ($("more")) $("more").onclick = function () { readInputs(); showAll = true; draw(); };
      bindSteppers($("sb"), function () { readInputs(); var a = amountOf(c, s); $("amt").textContent = "฿" + fmt(a, a % 1 ? 2 : 0); });
      $("sb").addEventListener("click", function (e) {
        var pb = e.target.closest("button[data-price]");
        if (pb) { readInputs(); askPrice(pb.getAttribute("data-price")); return; }
        var pay = e.target.closest("button[data-pay]");
        if (pay) { readInputs(); s.pay = pay.getAttribute("data-pay"); draw(); }
      });
      if ($("payall")) $("payall").onclick = function () { $("oldp").value = balanceOf(c.id); };
      $("sclear").onclick = function () { delete S.doc.sales[String(c.id)]; changed(); closeSheet(); afterSale(); };
      $("ssave").onclick = function () { readInputs(); save(true); };
      root.onkeydown = null;
    }
    function readInputs() {
      products().forEach(function (p) { var el = $("q_" + p.key); if (el) { var v = n(el.value); if (v) s.q[p.key] = v; else delete s.q[p.key]; } });
      if ($("paid")) s.paid = n($("paid").value);
      if ($("oldp")) s.old_paid = n($("oldp").value);
    }
    function askPrice(k) {
      var cur = priceOf(c, s, k);
      var v = window.prompt("ราคา " + label(k) + " ต่อหน่วย (บาท)", cur == null ? "" : cur);
      if (v == null) return;
      if (!s.price) s.price = {};
      if (String(v).trim() === "") delete s.price[k]; else s.price[k] = n(v);
      draw();
    }
    function save(next) {
      var any = Object.keys(s.q).some(function (k) { return n(s.q[k]); }) || n(s.old_paid);
      var missing = products().filter(function (p) { return n(s.q[p.key]) && priceOf(c, s, p.key) == null; });
      if (missing.length) { toast("ใส่ราคา " + missing.map(function (p) { return p.label; }).join(" "), true); return; }
      if (any) { s.t = s.t || nowHM(); s.paid = paidOf(c, s); S.doc.sales[String(c.id)] = s; } else delete S.doc.sales[String(c.id)];
      changed(); closeSheet(); afterSale();
      if (next && any) {
        var list = customers(), i = list.map(function (x) { return String(x.id); }).indexOf(String(c.id));
        var nx = list.slice(i + 1).filter(function (x) { return !S.doc.sales[String(x.id)]; })[0];
        if (nx && !S.q) { setTimeout(function () { var el = document.querySelector('.cust[data-id="' + nx.id + '"]'); if (el) el.scrollIntoView({ block: "center" }); }, 30); }
      }
    }
    draw();
  }
  function afterSale() { paintStock(); if (S.tab === "customers") paintList(); }

  function openNewCustomer() {
    openSheet('<div class="sheet-head"><h2>ลูกค้าใหม่</h2><button class="x" id="sx">×</button></div><div class="sheet-body"><div class="box">' +
      '<label class="lbl">ชื่อร้าน</label><input class="txt" id="ncn" autocomplete="off"><label class="lbl">ราคาหลอด / เล็ก / โม่ (บาท)</label>' +
      '<input class="num wide" id="ncp" inputmode="decimal" placeholder="40"><p class="note">ออฟฟิศจะตรวจและเพิ่มเข้าใบขายถาวร</p></div></div>' +
      '<div class="actions one"><button class="btn primary" id="ncs">เพิ่ม</button></div>');
    $("sx").onclick = closeSheet;
    $("ncs").onclick = function () {
      var name = $("ncn").value.trim(), price = n($("ncp").value);
      if (!name) { toast("ใส่ชื่อร้าน", true); return; }
      var key = "new:" + Date.now();
      var prices = price ? { lod: price, lek: price, mo: price } : {};
      S.doc.new_customers = (S.doc.new_customers || []).concat([{ key: key, name: name, prices: prices }]);
      changed(); closeSheet(); openSale(key);
    };
  }

  // ---- loads
  function renderLoads() {
    var m = $("main"), loads = S.doc.loads || [];
    m.innerHTML = statusBanner() + (loads.length ? '<div class="list">' + loads.map(function (ld, i) {
      var what = products().filter(function (p) { return n(ld.q && ld.q[p.key]); }).map(function (p) { return p.label + " " + fmt(ld.q[p.key]); }).join(" · ");
      return '<button class="cust" data-i="' + i + '"><span class="trip"><span><b>เที่ยว ' + (i + 1) + "</b> · " + esc(ld.t || "") + '<br><span class="what">' + esc(what || "—") + "</span></span></span></button>";
    }).join("") + "</div>" : '<div class="empty">ยังไม่ได้รับน้ำแข็งวันนี้</div>') +
      (readOnly() ? "" : '<button class="add" id="addl">+ รับน้ำแข็ง' + (loads.length ? "เที่ยวใหม่" : "") + "</button>");
    m.querySelector(".list") && (m.querySelector(".list").onclick = function (e) { var b = e.target.closest(".cust"); if (b) openLoad(+b.getAttribute("data-i")); });
    if ($("addl")) $("addl").onclick = function () { openLoad(-1); };
  }
  function openLoad(i) {
    var ld = i >= 0 ? JSON.parse(JSON.stringify(S.doc.loads[i])) : { q: {}, t: nowHM() };
    var ro = readOnly();
    openSheet('<div class="sheet-head"><h2>รับน้ำแข็ง เที่ยว ' + (i >= 0 ? i + 1 : (S.doc.loads.length + 1)) + "<small>" + esc(ld.t) + '</small></h2><button class="x" id="sx">×</button></div>' +
      '<div class="sheet-body" id="lb"><div class="box">' + products().map(function (p) {
        return '<div class="prow"><div class="pl">' + esc(p.label) + "</div>" + stepper("l_" + p.key, n(ld.q[p.key])) + "</div>";
      }).join("") + "</div></div>" +
      (ro ? '<div class="actions one"><button class="btn" id="lx">ปิด</button></div>'
        : '<div class="actions"><button class="btn danger" id="ld">' + (i >= 0 ? "ลบเที่ยวนี้" : "ยกเลิก") + '</button><button class="btn primary" id="ls">บันทึก</button></div>'));
    $("sx").onclick = closeSheet;
    if (ro) { $("lx").onclick = closeSheet; return; }
    bindSteppers($("lb"), function () {});
    $("ld").onclick = function () { if (i >= 0) { S.doc.loads.splice(i, 1); changed(); } closeSheet(); render(); };
    $("ls").onclick = function () {
      ld.q = {};
      products().forEach(function (p) { var v = n($("l_" + p.key).value); if (v) ld.q[p.key] = v; });
      if (!Object.keys(ld.q).length) { toast("ใส่จำนวนอย่างน้อย 1 อย่าง", true); return; }
      if (i >= 0) S.doc.loads[i] = ld; else S.doc.loads.push(ld);
      changed(); closeSheet(); render();
    };
  }

  // ---- close the day
  function renderClose() {
    var t = totals(), m = $("main"), ro = readOnly();
    var used = products().filter(function (p) { return t.loaded[p.key] || t.sold[p.key]; });
    var ret = S.doc.returns || {};
    var html = statusBanner();
    html += '<div class="box"><h3>น้ำแข็ง: คืน (นับที่เหลือบนรถ)</h3>';
    if (used.length) {
      html += '<div class="grid4"><span class="h"></span><span class="h">รับ</span><span class="h">ขาย</span><span class="h">ควรเหลือ</span><span class="h">คืนจริง</span>' +
        used.map(function (p) {
          var left = t.loaded[p.key] - t.sold[p.key], has = S.doc.returns && S.doc.returns[p.key] != null && S.doc.returns[p.key] !== "";
          var diff = has ? n(ret[p.key]) - left : 0;
          return '<b>' + esc(p.label) + '</b><span class="c">' + fmt(t.loaded[p.key]) + '</span><span class="c">' + fmt(t.sold[p.key]) + '</span><span class="c">' + fmt(left) +
            (has && diff ? '<br><span class="' + (diff < 0 ? "bad-t" : "warn-t") + '">' + (diff < 0 ? "ขาด " : "เกิน ") + fmt(Math.abs(diff)) + "</span>" : "") + "</span>" +
            '<input class="num" data-ret="' + p.key + '" inputmode="numeric" pattern="[0-9]*" value="' + (has ? n(ret[p.key]) : "") + '" placeholder="' + fmt(Math.max(left, 0)) + '"' + (ro ? " disabled" : "") + ">";
        }).join("") + "</div>";
    } else html += '<div class="note">ยังไม่มีการรับ / ขาย</div>';
    html += "</div>";
    var km = S.doc.km_start && S.doc.km_end ? n(S.doc.km_end) - n(S.doc.km_start) : null;
    html += '<div class="box"><h3>ไมล์รถ</h3><div class="line"><span class="lbl">ไมล์เก่า</span><input class="num wide" id="kms" inputmode="numeric" value="' + (S.doc.km_start || "") + '"' + (ro ? " disabled" : "") + '></div>' +
      '<div class="line"><span class="lbl">ไมล์ใหม่</span><input class="num wide" id="kme" inputmode="numeric" value="' + (S.doc.km_end || "") + '"' + (ro ? " disabled" : "") + "></div>" +
      '<div class="line"><span class="lbl">รถวิ่ง</span><b>' + (km != null ? fmt(km) + " กม." : "—") + "</b></div></div>";
    var cash = S.doc.cash || {}, counted = 0;
    html += '<div class="box"><h3>นับเงิน</h3>' + (S.boot.notes || []).map(function (note) {
      var c = n(cash[String(note)]); counted += c * note;
      return '<div class="cashrow"><b>' + fmt(note) + ' ×</b><input class="num" data-note="' + note + '" inputmode="numeric" pattern="[0-9]*" value="' + (c || "") + '" placeholder="0"' + (ro ? " disabled" : "") +
        '><span class="sub">' + (c ? "฿" + fmt(c * note) : "") + "</span></div>";
    }).join("");
    counted += n(cash.coins);
    html += '<div class="cashrow"><b>เหรียญ</b><input class="num wide" data-note="coins" inputmode="decimal" value="' + (n(cash.coins) || "") + '" placeholder="฿"' + (ro ? " disabled" : "") + '><span class="sub"></span></div>';
    var expect = t.cash + t.old, cdiff = counted - expect;
    html += '<div class="sumgrid"><span>ต้องมีเงินสด</span><b>฿' + fmt(expect) + "</b><span>นับได้</span><b>฿" + fmt(counted) + "</b>" +
      (S.doc.cash ? "<span>ต่าง</span><b class=\"" + (Math.abs(cdiff) < 1 ? "ok-t" : cdiff < 0 ? "bad-t" : "warn-t") + "\">" + (Math.abs(cdiff) < 1 ? "ตรง ✓" : (cdiff < 0 ? "ขาด ฿" : "เกิน ฿") + fmt(Math.abs(cdiff))) + "</b>" : "") + "</div></div>";
    html += '<div class="box"><h3>สรุปวันนี้</h3><div class="sumgrid"><span>ขายทั้งหมด</span><b>฿' + fmt(t.amount) + "</b><span>เงินสด (ขายวันนี้)</span><b>฿" + fmt(t.cash) +
      "</b><span>รับหนี้เก่า</span><b>฿" + fmt(t.old) + "</b><span>ค้าง</span><b>฿" + fmt(t.credit) + "</b><span>วางบิล</span><b>฿" + fmt(t.invoiced) + "</b></div></div>";
    if (!ro) html += '<button class="btn primary" id="closeday" style="width:100%">' + (S.doc.closed ? "ส่งใบขายอีกครั้ง" : "ส่งใบขาย · ปิดวัน") + "</button>";
    html += '<div class="foot"><button class="link" id="logout">ออกจากระบบ</button></div>';
    m.innerHTML = html;
    if (ro) { $("logout").onclick = logout; return; }
    m.onchange = function (e) {
      var el = e.target;
      if (el.hasAttribute("data-ret")) { if (!S.doc.returns) S.doc.returns = {}; if (el.value === "") delete S.doc.returns[el.getAttribute("data-ret")]; else S.doc.returns[el.getAttribute("data-ret")] = n(el.value); }
      else if (el.hasAttribute("data-note")) { if (!S.doc.cash) S.doc.cash = {}; S.doc.cash[el.getAttribute("data-note")] = n(el.value); }
      else if (el.id === "kms") S.doc.km_start = n(el.value) || null;
      else if (el.id === "kme") S.doc.km_end = n(el.value) || null;
      else return;
      changed(); var y = window.scrollY; renderClose(); window.scrollTo(0, y); paintStock();
    };
    $("closeday").onclick = function () {
      var missing = [];
      if (!S.doc.km_end) missing.push("ไมล์ใหม่");
      if (!S.doc.cash) missing.push("นับเงิน");
      if (used.length && !S.doc.returns) missing.push("น้ำแข็งที่คืน");
      if (missing.length && !window.confirm("ยังไม่ได้ใส่: " + missing.join(", ") + "\nส่งเลยไหม?")) return;
      S.doc.closed = true; changed(); toast("ส่งใบขายแล้ว ✓"); renderClose();
    };
    $("logout").onclick = logout;
  }
  function logout() {
    if (S.savedRev < S.rev && !window.confirm("ยังมีข้อมูลที่ยังไม่ได้ส่ง ออกจากระบบจะเก็บไว้ในเครื่อง ออกเลยไหม?")) return;
    api("POST", "/api/d/logout").catch(function () {}).then(function () { signedOut(); });
  }

  // ------------------------------------------------------------------ start
  function start() {
    S.token = load(TOKEN_KEY);
    if (!S.token) { showSignIn(); return; }
    var cached = load(BOOT_KEY);
    api("GET", "/api/d/me").then(function (b) { S.boot = b; store(BOOT_KEY, b); openDay(b.day, b.sheet); })
      .catch(function (e) {
        if (e.message === "signin") return;
        if (cached) {
          var d = new Date(), day = d.getFullYear() + "-" + ("0" + (d.getMonth() + 1)).slice(-2) + "-" + ("0" + d.getDate()).slice(-2);   // the phone's own date
          S.boot = cached; S.boot.today = day; openDay(day, day === cached.day ? cached.sheet : null); toast("ไม่มีสัญญาณ — ใช้ข้อมูลในเครื่อง", true);
        }
        else $("app").innerHTML = '<div class="boot">ต่ออินเทอร์เน็ตไม่ได้ ลองใหม่อีกครั้ง</div>';
      });
  }
  if ("serviceWorker" in navigator) { try { navigator.serviceWorker.register("/d/sw.js", { scope: "/d/" }); } catch (e) { /* not supported */ } }
  start();
})();
