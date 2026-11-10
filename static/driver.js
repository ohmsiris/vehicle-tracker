"use strict";
/* สายรถ · ใบขาย -- the driver's phone (app/route_sales.py). Plain JS for old phones (no ?. or ??), no libraries.

   The day lives on the phone first: every tap saves to localStorage at once and the whole day is sent to the server shortly after
   (and again when the signal comes back). `rev` counts the phone's saves; the server ignores a save older than what it has.
   Only today can be changed (plus an unsent yesterday, until noon); ส่งใบขาย locks the day, and the office reopens it on request.
   Screens: sign in (name, then a 4-digit PIN) -> the day with three tabs: รับน้ำแข็ง · ลูกค้า · ปิดวัน.
*/
(function () {
  var TOKEN_KEY = "icebox_driver_token";
  var BOOT_KEY = "icebox_driver_boot";
  var S = { token: null, boot: null, day: null, doc: null, rev: 0, savedRev: 0, status: "open", sheetMeta: {}, tab: "customers",
            q: "", owing: false, syncing: false, syncTimer: null, failCount: 0, scrollToNext: true };

  // ------------------------------------------------------------------ small helpers
  function $(id) { return document.getElementById(id); }
  function esc(s) { return String(s == null ? "" : s).replace(/[&<>"']/g, function (c) { return { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]; }); }
  function n(x) { var v = parseFloat(String(x == null ? "" : x).replace(/,/g, "")); return isFinite(v) ? v : 0; }
  function fmt(v, d) { return n(v).toLocaleString("th-TH", { minimumFractionDigits: d || 0, maximumFractionDigits: d || 0 }); }
  function money(v) { var x = n(v); return fmt(x, x % 1 ? 2 : 0); }
  function store(k, v) { try { if (v == null) localStorage.removeItem(k); else localStorage.setItem(k, JSON.stringify(v)); } catch (e) { /* full / blocked */ } }
  function load(k) { try { var v = localStorage.getItem(k); return v ? JSON.parse(v) : null; } catch (e) { return null; } }
  function nowHM() { var d = new Date(); return ("0" + d.getHours()).slice(-2) + ":" + ("0" + d.getMinutes()).slice(-2); }
  function hm(iso) { var d = new Date(iso); return isNaN(d) ? "" : ("0" + d.getHours()).slice(-2) + ":" + ("0" + d.getMinutes()).slice(-2); }
  var TH_DAYS = ["อาทิตย์", "จันทร์", "อังคาร", "พุธ", "พฤหัสฯ", "ศุกร์", "เสาร์"];
  var TH_MON = ["ม.ค.", "ก.พ.", "มี.ค.", "เม.ย.", "พ.ค.", "มิ.ย.", "ก.ค.", "ส.ค.", "ก.ย.", "ต.ค.", "พ.ย.", "ธ.ค."];
  function thDate(iso) { var p = iso.split("-"); var d = new Date(+p[0], +p[1] - 1, +p[2]); return "วัน" + TH_DAYS[d.getDay()] + " " + (+p[2]) + " " + TH_MON[+p[1] - 1]; }
  var toastT = null;
  function toast(msg, bad) { var t = $("toast"); t.textContent = msg; t.className = "toast" + (bad ? " bad" : ""); t.hidden = false;
    clearTimeout(toastT); toastT = setTimeout(function () { t.hidden = true; }, bad ? 4500 : 2000); }

  var ICONS = {
    truck: '<path d="M3 7h11v9H3z"/><path d="M14 10h4l3 3v3h-7"/><circle cx="7" cy="18" r="1.6"/><circle cx="17" cy="18" r="1.6"/>',
    store: '<path d="M4 9l1.6-5h12.8L20 9"/><path d="M4 9h16v1.5a2.7 2.7 0 0 1-5.3 0 2.7 2.7 0 0 1-5.4 0 2.7 2.7 0 0 1-5.3 0z"/><path d="M5.5 13v7h13v-7"/><path d="M10 20v-4h4v4"/>',
    clip: '<rect x="5" y="4" width="14" height="17" rx="2"/><path d="M9 4V3h6v1"/><path d="M9 13l2 2 4-4"/>',
    search: '<circle cx="11" cy="11" r="6"/><path d="M20 20l-4.5-4.5"/>',
    cloud: '<path d="M7 18a4 4 0 0 1-.5-8A6 6 0 0 1 18 9a4 4 0 0 1 0 9z"/><path d="M9.5 13.5l2 2 3.5-3.5"/>',
    cloudwait: '<path d="M7 18a4 4 0 0 1-.5-8A6 6 0 0 1 18 9a4 4 0 0 1 0 9z"/><path d="M12 10.5v3.5M12 16.5v.01"/>',
    lock: '<rect x="5" y="11" width="14" height="10" rx="2"/><path d="M8 11V8a4 4 0 0 1 8 0v3"/>',
    cash: '<rect x="3" y="6" width="18" height="12" rx="2"/><circle cx="12" cy="12" r="2.5"/><path d="M6.5 9.5v.01M17.5 14.5v.01"/>',
    back: '<path d="M15 5l-7 7 7 7"/>',
    plus: '<path d="M12 5v14M5 12h14"/>',
    minus: '<path d="M5 12h14"/>',
    send: '<path d="M4 12l16-8-6 16-2.5-6.5z"/><path d="M11.5 13.5L20 4"/>',
    out: '<path d="M14 8V5a2 2 0 0 0-2-2H5a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h7a2 2 0 0 0 2-2v-3"/><path d="M9 12h12m-3-3l3 3-3 3"/>',
    next: '<path d="M9 6l6 6-6 6"/>'
  };
  function ic(name, cls) { return '<svg class="i' + (cls ? " " + cls : "") + '" viewBox="0 0 24 24" aria-hidden="true">' + ICONS[name] + "</svg>"; }

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

  // ------------------------------------------------------------------ products, prices, customers, money
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
  function soldTo(id) { var s = S.doc.sales[String(id)]; return !!(s && (Object.keys(s.q || {}).some(function (k) { return n(s.q[k]); }) || n(s.old_paid))); }
  function totals() {
    var loaded = {}, sold = {}, cash = 0, credit = 0, inv = 0, old = 0, amt = 0, shops = 0;
    products().forEach(function (p) { loaded[p.key] = 0; sold[p.key] = 0; });
    (S.doc.loads || []).forEach(function (ld) { products().forEach(function (p) { loaded[p.key] += n(ld.q && ld.q[p.key]); }); });
    Object.keys(S.doc.sales || {}).forEach(function (id) {
      var s = S.doc.sales[id], c = custById(id); if (!c) return;
      products().forEach(function (p) { sold[p.key] += n(s.q && s.q[p.key]); });
      var a = amountOf(c, s); amt += a;
      if (c.billing === "invoice") inv += a; else { var pd = paidOf(c, s); cash += pd; credit += a - pd; }
      old += n(s.old_paid);
      if (soldTo(id)) shops += 1;
    });
    return { loaded: loaded, sold: sold, cash: cash, credit: credit, invoiced: inv, old: old, amount: amt, shops: shops };
  }
  function balanceOf(id) { return n((S.boot.balances || {})[String(id)]); }
  function nextCustomer(afterId) {          // the next shop in route order not served yet (after afterId, wrapping round)
    var list = customers(), ids = list.map(function (x) { return String(x.id); });
    var start = afterId != null ? ids.indexOf(String(afterId)) + 1 : 0;
    var rest = list.slice(start).concat(list.slice(0, start));
    return rest.filter(function (x) { return !soldTo(x.id) && String(x.id) !== String(afterId); })[0] || null;
  }
  function lastServed() {
    var best = null;
    customers().forEach(function (c) { var s = S.doc.sales[String(c.id)]; if (soldTo(c.id) && (!best || (s.t || "") >= (best.t || ""))) best = { id: c.id, t: s.t }; });
    return best ? best.id : null;
  }

  // ------------------------------------------------------------------ the day on the phone, and sending it
  function dayKey() { return "icebox_day_" + S.boot.route.id + "_" + S.day; }
  function emptyDoc() { return { loads: [], sales: {}, new_customers: [], returns: null, km_start: S.boot.last_km || null, km_end: null, cash: null, closed: false }; }
  function persist() { store(dayKey(), { doc: S.doc, rev: S.rev, savedRev: S.savedRev, status: S.status }); }
  function locked() { return S.status === "closed" || S.status === "checked" || S.boot.editable === false; }
  function changed() {
    if (locked()) return;
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
      S.savedRev = Math.max(S.savedRev, r.rev || rev);
      if (r.status) S.status = r.status;
      var map = r.id_map || {}, keys = Object.keys(map);
      if (keys.length) {                          // new customers the office now knows: swap the phone's temporary keys
        keys.forEach(function (k) { if (S.doc.sales[k]) { S.doc.sales[map[k]] = S.doc.sales[k]; delete S.doc.sales[k]; } });
        S.doc.new_customers = (S.doc.new_customers || []).filter(function (c) { return !map[c.key]; });
        persist(); reload(false);
        return;
      }
      persist(); paintSync();
      if (S.savedRev < S.rev) sync();
    }).catch(function (e) {
      S.syncing = false;
      if (e.message === "signin") return;
      if (e.status === 422) { toast(e.message, true); reload(true); return; }      // locked on the server: take the server's copy
      S.failCount += 1; paintSync();
      clearTimeout(S.syncTimer); S.syncTimer = setTimeout(sync, Math.min(60000, 3000 * S.failCount));
    });
  }
  window.addEventListener("online", function () { sync(); });
  setInterval(function () { if (S.boot && S.savedRev < S.rev) sync(); }, 30000);

  function paintSync() {
    var el = $("sync"); if (!el) return;
    if (locked()) { el.className = "sync lock"; el.innerHTML = ic("lock", "s") + "ส่งแล้ว"; return; }
    if (S.savedRev >= S.rev) { el.className = "sync"; el.innerHTML = ic("cloud", "s") + "บันทึกแล้ว"; return; }
    if (navigator.onLine === false || S.failCount) { el.className = "sync off"; el.innerHTML = ic("cloudwait", "s") + "รอสัญญาณ"; return; }
    el.className = "sync wait"; el.innerHTML = ic("cloudwait", "s") + "กำลังส่ง";
  }

  function openDay(boot) {
    S.boot = boot; S.day = boot.day;
    var local = load(dayKey()), server = boot.sheet || null;
    S.sheetMeta = server || {};
    if (server && server.status !== "open") local = null;            // sent (locked) on the server: its copy wins
    if (local && (!server || local.rev > server.rev)) { S.doc = local.doc; S.rev = local.rev; S.savedRev = server ? server.rev : (local.savedRev || 0); S.status = local.doc && local.doc.closed ? "closed" : "open"; }
    else if (server) { S.doc = server.doc || emptyDoc(); S.rev = server.rev; S.savedRev = server.rev; S.status = server.status; }
    else { S.doc = emptyDoc(); S.rev = 0; S.savedRev = 0; S.status = "open"; }
    if (!S.doc.sales) S.doc.sales = {};
    if (!S.doc.loads) S.doc.loads = [];
    if (S.doc.km_start == null && boot.last_km) S.doc.km_start = boot.last_km;
    S.scrollToNext = true;
    persist(); render();
    if (S.savedRev < S.rev) sync();
  }
  function reload(discardLocal, day) {
    var d = day || S.day;
    if (discardLocal && S.boot) store("icebox_day_" + S.boot.route.id + "_" + d, null);
    return api("GET", "/api/d/me" + (d && S.boot && d !== S.boot.today ? "?day=" + d : "")).then(function (b) { store(BOOT_KEY, b); openDay(b); })
      .catch(function (e) { if (e.message !== "signin") toast("ไม่มีสัญญาณ ลองใหม่อีกครั้ง", true); });
  }

  // ------------------------------------------------------------------ sign in
  function signedOut() { S.token = null; store(TOKEN_KEY, null); showSignIn(); }
  function showSignIn() {
    closeSheet();
    $("app").innerHTML = '<div class="signin"><h1>ใบขายสายรถ</h1><p>เลือกชื่อของคุณ</p><div class="names" id="names"><div class="empty">กำลังโหลด…</div></div></div>';
    api("GET", "/api/d/drivers").then(function (r) {
      var list = r.drivers || [];
      $("names").innerHTML = list.length ? list.map(function (d) {
        return '<button class="name-btn" data-id="' + d.id + '" data-name="' + esc(d.name) + '"><b>' + esc(d.truck_no) + "</b><span>" + esc(d.name) + "<small>" + esc(d.route) + "</small></span></button>";
      }).join("") : '<div class="empty">ยังไม่มีชื่อ — ให้ออฟฟิศตั้งรหัสก่อน</div>';
    }).catch(function () { $("names").innerHTML = '<div class="empty">ต้องต่ออินเทอร์เน็ตตอนเข้าครั้งแรก</div>'; });
    $("names").onclick = function (e) { var b = e.target.closest(".name-btn"); if (b) showPin(+b.getAttribute("data-id"), b.getAttribute("data-name")); };
  }
  function showPin(id, name) {
    var pin = "";
    $("app").innerHTML = '<div class="signin"><h1>' + esc(name) + '</h1><p>ใส่รหัส 4 หลัก</p><div class="pin"><div class="dots" id="dots"><i></i><i></i><i></i><i></i></div>' +
      '<div class="err" id="perr"></div><div class="pad" id="pad">' + [1, 2, 3, 4, 5, 6, 7, 8, 9].map(function (k) { return '<button data-k="' + k + '">' + k + "</button>"; }).join("") +
      '<button data-k="back" aria-label="กลับ">' + ic("back") + '</button><button data-k="0">0</button><button data-k="del" aria-label="ลบ">⌫</button></div></div></div>';
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
    var b = S.boot;
    $("app").innerHTML =
      '<div class="top"><div class="top-row"><div class="who"><div class="route">สาย ' + esc(b.route.truck_no) + " · " + esc(b.route.name) + " · " + esc(b.driver.name) + "</div>" +
      '<div class="date">' + thDate(S.day) + (S.day !== b.today ? " (เมื่อวาน)" : "") + '</div></div><div class="sync" id="sync"></div></div>' +
      '<div class="cards"><div class="mini"><div class="k">บนรถ</div><div class="v" id="onTruck"></div></div><div class="mini"><div class="k">เงินสด</div><div class="v cash" id="cashNow"></div></div></div>' +
      '<div class="prog"><i id="progBar"></i></div><div class="prog-t" id="progT"></div></div>' +
      '<main id="main"></main>' +
      '<nav class="tabs"><button data-tab="loads" class="' + (S.tab === "loads" ? "on" : "") + '">' + ic("truck", "l") + "รับน้ำแข็ง</button>" +
      '<button data-tab="customers" class="' + (S.tab === "customers" ? "on" : "") + '">' + ic("store", "l") + "ลูกค้า</button>" +
      '<button data-tab="close" class="' + (S.tab === "close" ? "on" : "") + '">' + ic("clip", "l") + "ปิดวัน</button></nav>";
    $("app").querySelector(".tabs").onclick = function (e) { var t = e.target.closest("button"); if (!t) return; S.tab = t.getAttribute("data-tab"); S.scrollToNext = true; render(); window.scrollTo(0, 0); };
    paintTop(); paintSync();
    if (S.tab === "loads") renderLoads(); else if (S.tab === "close") renderClose(); else renderCustomers();
  }
  function paintTop() {
    var t = totals(), parts = [];
    products().forEach(function (p) {
      var left = t.loaded[p.key] - t.sold[p.key];
      if (t.loaded[p.key] || t.sold[p.key]) parts.push(esc(p.label) + " " + (left < 0 ? '<span class="neg">' + fmt(left) + "</span>" : fmt(left)));
    });
    $("onTruck").innerHTML = parts.length ? parts.join(" · ") : '<span style="color:var(--muted);font-weight:400">ยังไม่ได้รับ</span>';
    $("cashNow").textContent = "฿" + money(t.cash + t.old);
    var all = customers().length || 1;
    $("progBar").style.width = Math.min(100, Math.round(100 * t.shops / all)) + "%";
    $("progT").textContent = "ไปแล้ว " + t.shops + " จาก " + customers().length + " ร้าน";
  }
  function banners() {
    var b = S.boot, h = "";
    if (locked()) {
      var when = S.sheetMeta.closed_at ? " " + hm(S.sheetMeta.closed_at) : "";
      var asked = S.sheetMeta.reopen_requested_at;
      h += '<div class="banner lock">' + ic("lock") + '<div class="t"><b>' + (S.status === "checked" ? "ออฟฟิศตรวจแล้ว" : "ส่งใบขายแล้ว" + when) + "</b><small>ล็อกแล้ว ดูได้อย่างเดียว</small></div>" +
        (S.status === "closed" ? '<button class="bb" id="askOpen">' + (asked ? "ขอแล้ว ✓" : "ขอแก้") + "</button>" : "") + "</div>";
    } else if (S.day !== b.today) {
      h += '<div class="banner warn"><div class="t"><b>กำลังแก้ใบขายเมื่อวาน</b><small>ส่งก่อนเที่ยง หลังจากนั้นจะล็อก</small></div><button class="bb" id="goToday">ไปวันนี้</button></div>';
    } else if (b.pending_yesterday) {
      h += '<div class="banner warn"><div class="t"><b>ใบขายเมื่อวานยังไม่ได้ส่ง</b><small>ส่งได้ถึงเที่ยงวันนี้</small></div><button class="bb" id="goYesterday">เปิด</button></div>';
    }
    return h;
  }
  function wireBanners() {
    if ($("askOpen")) $("askOpen").onclick = function () {
      if (S.sheetMeta.reopen_requested_at) { toast("ขอไปแล้ว รอออฟฟิศเปิดให้"); return; }
      api("POST", "/api/d/reopen-request", { day: S.day }).then(function (r) { S.sheetMeta.reopen_requested_at = r.reopen_requested_at; toast("ส่งคำขอแล้ว รอออฟฟิศ"); render(); })
        .catch(function (e) { if (e.message !== "signin") toast(e.message === "Failed to fetch" ? "ไม่มีสัญญาณ" : e.message, true); });
    };
    if ($("goToday")) $("goToday").onclick = function () { reload(false, S.boot.today); };
    if ($("goYesterday")) $("goYesterday").onclick = function () { reload(false, S.boot.pending_yesterday); };
  }

  // ---- customers
  function renderCustomers() {
    var m = $("main"), owing = Object.keys(S.boot.balances || {}).filter(function (k) { return n(S.boot.balances[k]) > 0; }).length;
    m.innerHTML = banners() + '<div class="tools"><label class="search">' + ic("search", "s") + '<input id="q" type="search" placeholder="ค้นหาร้าน หรือเลขที่" value="' + esc(S.q) + '" autocomplete="off"></label>' +
      (owing ? '<button class="filter' + (S.owing ? " on" : "") + '" id="owing">ค้าง ' + owing + "</button>" : "") + "</div>" +
      '<div class="list" id="list"></div>' + (locked() ? "" : '<button class="add" id="addc">' + ic("plus", "s") + "เพิ่มลูกค้าใหม่</button>");
    $("q").oninput = function () { S.q = this.value; paintList(); };
    if ($("owing")) $("owing").onclick = function () { S.owing = !S.owing; renderCustomers(); };
    $("list").onclick = function (e) { var r = e.target.closest(".cust"); if (r) openSale(r.getAttribute("data-id")); };
    if ($("addc")) $("addc").onclick = openNewCustomer;
    wireBanners(); paintList();
  }
  function paintList() {
    var q = S.q.trim().toLowerCase();
    var nx = locked() ? null : nextCustomer(lastServed());
    var rows = customers().filter(function (c) {
      if (S.owing && !(balanceOf(c.id) > 0)) return false;
      return !q || (c.name + " " + c.seq + " " + (c.bins || "")).toLowerCase().indexOf(q) >= 0;
    });
    $("list").innerHTML = rows.length ? rows.map(function (c) {
      var s = S.doc.sales[String(c.id)], sold = soldTo(c.id), isNext = nx && String(nx.id) === String(c.id);
      var meta = [];
      if (isNext) meta.push('<span class="chip next">ร้านถัดไป</span>');
      if (c.billing === "invoice") meta.push('<span class="chip">วางบิล</span>');
      if (c.fresh || c.approved === false) meta.push('<span class="chip warn">ลูกค้าใหม่</span>');
      var bal = balanceOf(c.id); if (bal > 0) meta.push('<span class="chip warn">ค้าง ฿' + money(bal) + "</span>");
      if (c.bins) meta.push(esc(c.bins));
      var right;
      if (sold) {
        var what = products().filter(function (p) { return n(s.q && s.q[p.key]); }).map(function (p) { return p.label + " " + fmt(s.q[p.key]); });
        if (n(s.old_paid)) what.push("เก่า ฿" + money(s.old_paid));
        right = '<span class="right"><b>฿' + money(amountOf(c, s) + n(s.old_paid)) + "</b>" + esc(what.join(" · ")) + (s.pay === "credit" ? " · ค้าง" : "") + "</span>";
      } else {
        var pr = c.prices || {}, first = ["lod", "lek", "mo", "pack", "zong", "kak"].filter(function (k) { return pr[k] != null; })[0];
        right = '<span class="right">' + (first ? money(pr[first]) + "฿" + (first === "pack" || first === "zong" || first === "kak" ? "<br>" + esc(label(first)) : "") : "") + "</span>";
      }
      return '<button class="cust' + (sold ? " done" : "") + (isNext ? " next" : "") + '" data-id="' + esc(c.id) + '"><span class="no">' + (sold ? "✓" : c.fresh ? "+" : c.seq) +
        '</span><span class="mid"><span class="nm">' + esc(c.name) + '</span><span class="meta">' + meta.join(" ") + "</span></span>" + right + "</button>";
    }).join("") : '<div class="empty">' + (S.owing ? "ไม่มีร้านค้าง" : "ไม่พบ") + "</div>";
    if (nx && !q && !S.owing && S.scrollToNext && lastServed() != null) {
      S.scrollToNext = false;
      var el = document.querySelector('.cust[data-id="' + nx.id + '"]');
      if (el) el.scrollIntoView({ block: "center" });
    }
  }

  // ---- one customer's sale (full-screen sheet)
  function openSheet(html) { var sh = $("sheet"); sh.innerHTML = html; sh.hidden = false; sh.scrollTop = 0; document.body.style.overflow = "hidden"; }
  function closeSheet() { var sh = $("sheet"); if (!sh) return; sh.hidden = true; sh.innerHTML = ""; document.body.style.overflow = ""; }
  function stepper(id, val, ro) {
    var dis = ro ? " disabled" : "";
    return '<div class="step"><button data-step="' + id + '" data-d="-1" aria-label="ลด"' + dis + ">" + ic("minus") + '</button><input class="num" id="' + id +
      '" inputmode="numeric" pattern="[0-9]*" value="' + (val ? val : "") + '" placeholder="0"' + dis + '><button data-step="' + id + '" data-d="1" aria-label="เพิ่ม"' + dis + ">" + ic("plus") + "</button></div>";
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
    var showAll = false, ro = locked();
    function draw() {
      var pr = c.prices || {};
      var main = products().filter(function (p) { return pr[p.key] != null || n(s.q[p.key]); });
      if (!main.length) main = products().filter(function (p) { return p.key === "lod"; });
      var rest = products().filter(function (p) { return main.indexOf(p) < 0; });
      var list = showAll ? main.concat(rest) : main;
      var bal = balanceOf(c.id), invoice = c.billing === "invoice", dis = ro ? " disabled" : "";
      var html = '<div class="sheet-head"><button class="back" id="sx" aria-label="กลับ">' + ic("back", "l") + "</button><h2>" + (c.fresh ? "" : c.seq + ". ") + esc(c.name) + "<small>" + esc(c.bins || "") + "</small></h2></div>" +
        '<div class="sheet-body" id="sb">';
      if (ro) html += '<div class="banner lock">' + ic("lock") + '<div class="t"><b>ล็อกแล้ว</b><small>ดูได้อย่างเดียว</small></div></div>';
      if (invoice) html += '<div class="banner warn"><div class="t"><b>วางบิล</b><small>ให้ลูกค้าเซ็นบิล ไม่ต้องเก็บเงิน</small></div></div>';
      html += '<div class="box"><h3>ขายวันนี้</h3>' + list.map(function (p) {
        var price = priceOf(c, s, p.key);
        return '<div class="prow"><div><div class="pl">' + esc(p.label) + '</div><div class="pp' + (price == null && n(s.q[p.key]) ? " need" : "") + '"><button data-price="' + p.key + '"' + dis + ">" +
          (price == null ? "ใส่ราคา" : "฿" + money(price) + " / หน่วย") + "</button></div></div>" + stepper("q_" + p.key, n(s.q[p.key]), ro) + "</div>";
      }).join("") + (!showAll && rest.length && !ro ? '<button class="more" id="more">+ สินค้าอื่น (' + rest.map(function (p) { return p.label; }).join(" ") + ")</button>" : "");
      html += '<div class="total"><span>รวม</span><b id="amt">฿' + money(amountOf(c, s)) + "</b></div>";
      if (!invoice) {
        html += '<div class="seg" id="pay"><button data-pay="cash" class="' + (s.pay === "cash" ? "on" : "") + '"' + dis + '>จ่ายสด</button><button data-pay="credit" class="' + (s.pay === "credit" ? "on credit" : "") + '"' + dis +
          '>ค้าง</button><button data-pay="part" class="' + (s.pay === "part" ? "on" : "") + '"' + dis + ">จ่ายบางส่วน</button></div>";
        if (s.pay === "part") html += '<div class="line"><span class="lbl">รับเงินมา</span><input class="num wide" id="paid" inputmode="decimal" value="' + (s.paid ? s.paid : "") + '" placeholder="0"' + dis + "></div>";
      }
      html += "</div>";
      if (!invoice) {
        html += '<div class="box debt"><h3>' + ic("cash", "s") + "เก็บเงินค่าน้ำแข็งวันก่อน (จ่ายเก่า)</h3>" +
          (bal > 0 ? '<p class="note" style="color:var(--warn)">ค้างในระบบ ฿' + money(bal) + "</p>" : '<p class="note">ลูกค้าจ่ายค่าน้ำแข็งที่ค้างไว้ ใส่ตรงนี้ ไม่ต้องขายวันนี้ก็ได้</p>') +
          (ro || !(bal > 0) ? "" : '<div class="quick"><button id="payall">ทั้งหมด ฿' + money(bal) + "</button></div>") +
          '<div class="line"><span class="lbl">รับมา</span><input class="num wide" id="oldp" inputmode="decimal" value="' + (s.old_paid ? s.old_paid : "") + '" placeholder="0"' + dis + "></div></div>";
      }
      html += "</div>";
      html += ro ? '<div class="actions one"><button class="btn" id="sclose">' + ic("back", "s") + "กลับ</button></div>"
        : '<div class="actions"><button class="btn" id="sclear">ล้าง</button><button class="btn primary" id="ssave">บันทึก · ร้านถัดไป' + ic("next", "s") + "</button></div>";
      openSheet(html);
      $("sx").onclick = closeSheet;
      if (ro) { $("sclose").onclick = closeSheet; return; }
      if ($("more")) $("more").onclick = function () { readInputs(); showAll = true; draw(); };
      bindSteppers($("sb"), function () { readInputs(); $("amt").textContent = "฿" + money(amountOf(c, s)); });
      $("sb").addEventListener("click", function (e) {
        var pb = e.target.closest("button[data-price]");
        if (pb) { readInputs(); askPrice(pb.getAttribute("data-price")); return; }
        var pay = e.target.closest("button[data-pay]");
        if (pay) { readInputs(); s.pay = pay.getAttribute("data-pay"); draw(); }
      });
      if ($("payall")) $("payall").onclick = function () { $("oldp").value = balanceOf(c.id); };
      $("sclear").onclick = function () { delete S.doc.sales[String(c.id)]; changed(); closeSheet(); afterSale(); };
      $("ssave").onclick = function () { readInputs(); save(); };
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
    function save() {
      var any = Object.keys(s.q).some(function (k) { return n(s.q[k]); }) || n(s.old_paid);
      var missing = products().filter(function (p) { return n(s.q[p.key]) && priceOf(c, s, p.key) == null; });
      if (missing.length) { toast("ใส่ราคา " + missing.map(function (p) { return p.label; }).join(" "), true); return; }
      if (any) { s.t = s.t || nowHM(); s.paid = paidOf(c, s); S.doc.sales[String(c.id)] = s; } else delete S.doc.sales[String(c.id)];
      changed(); afterSale();
      var nx = nextCustomer(c.id);                  // straight to the next shop on the route
      if (nx) { openSale(nx.id); toast("บันทึกแล้ว ✓"); }
      else { closeSheet(); toast("ครบทุกร้านแล้ว ✓"); }
    }
    draw();
  }
  function afterSale() { paintTop(); if (S.tab === "customers") { S.scrollToNext = true; paintList(); } }

  function openNewCustomer() {
    openSheet('<div class="sheet-head"><button class="back" id="sx" aria-label="กลับ">' + ic("back", "l") + '</button><h2>ลูกค้าใหม่</h2></div><div class="sheet-body"><div class="box">' +
      '<label class="lbl" for="ncn">ชื่อร้าน</label><input class="txt" id="ncn" autocomplete="off"><label class="lbl" for="ncp">ราคาหลอด / เล็ก / โม่ (บาท)</label>' +
      '<input class="num wide" id="ncp" inputmode="decimal" placeholder="40"><p class="note">ออฟฟิศจะตรวจและเพิ่มเข้าใบขายถาวร</p></div></div>' +
      '<div class="actions one"><button class="btn primary" id="ncs">' + ic("plus", "s") + "เพิ่ม</button></div>");
    $("sx").onclick = closeSheet;
    $("ncs").onclick = function () {
      var name = $("ncn").value.trim(), price = n($("ncp").value);
      if (!name) { toast("ใส่ชื่อร้าน", true); return; }
      var key = "new:" + Date.now();
      S.doc.new_customers = (S.doc.new_customers || []).concat([{ key: key, name: name, prices: price ? { lod: price, lek: price, mo: price } : {} }]);
      changed(); closeSheet(); openSale(key);
    };
  }

  // ---- loads
  function renderLoads() {
    var m = $("main"), loads = S.doc.loads || [];
    m.innerHTML = banners() + (loads.length ? '<div class="list" id="loads">' + loads.map(function (ld, i) {
      var what = products().filter(function (p) { return n(ld.q && ld.q[p.key]); }).map(function (p) { return p.label + " " + fmt(ld.q[p.key]); }).join(" · ");
      return '<button class="cust" data-i="' + i + '"><span class="no">' + (i + 1) + '</span><span class="mid"><span class="nm">เที่ยว ' + (i + 1) + " · " + esc(ld.t || "") +
        '</span><span class="trip-what">' + esc(what || "—") + "</span></span></button>";
    }).join("") + "</div>" : '<div class="empty">ยังไม่ได้รับน้ำแข็งวันนี้</div>') +
      (locked() ? "" : '<button class="add" id="addl">' + ic("plus", "s") + "รับน้ำแข็ง" + (loads.length ? "เที่ยวใหม่" : "") + "</button>");
    if ($("loads")) $("loads").onclick = function (e) { var b = e.target.closest(".cust"); if (b) openLoad(+b.getAttribute("data-i")); };
    if ($("addl")) $("addl").onclick = function () { openLoad(-1); };
    wireBanners();
  }
  function openLoad(i) {
    var ld = i >= 0 ? JSON.parse(JSON.stringify(S.doc.loads[i])) : { q: {}, t: nowHM() };
    var ro = locked();
    openSheet('<div class="sheet-head"><button class="back" id="sx" aria-label="กลับ">' + ic("back", "l") + "</button><h2>รับน้ำแข็ง เที่ยว " + (i >= 0 ? i + 1 : (S.doc.loads.length + 1)) + "<small>" + esc(ld.t) + "</small></h2></div>" +
      '<div class="sheet-body" id="lb"><div class="box">' + products().map(function (p) {
        return '<div class="prow"><div class="pl">' + esc(p.label) + "</div>" + stepper("l_" + p.key, n(ld.q[p.key]), ro) + "</div>";
      }).join("") + "</div></div>" +
      (ro ? '<div class="actions one"><button class="btn" id="lx">' + ic("back", "s") + "กลับ</button></div>"
        : '<div class="actions"><button class="btn" id="ld">' + (i >= 0 ? "ลบเที่ยวนี้" : "ยกเลิก") + '</button><button class="btn primary" id="ls">บันทึก</button></div>'));
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
    var t = totals(), m = $("main"), ro = locked(), dis = ro ? " disabled" : "";
    var used = products().filter(function (p) { return t.loaded[p.key] || t.sold[p.key]; });
    var ret = S.doc.returns || {};
    var html = banners();
    html += '<div class="box"><h3>น้ำแข็งที่เหลือบนรถ (คืน)</h3>';
    if (used.length) {
      html += '<div class="grid4"><span class="h"></span><span class="h">รับ</span><span class="h">ขาย</span><span class="h">ควรเหลือ</span><span class="h">นับได้</span>' +
        used.map(function (p) {
          var left = t.loaded[p.key] - t.sold[p.key], has = S.doc.returns && S.doc.returns[p.key] != null && S.doc.returns[p.key] !== "";
          var diff = has ? n(ret[p.key]) - left : 0;
          return "<b>" + esc(p.label) + '</b><span class="c">' + fmt(t.loaded[p.key]) + '</span><span class="c">' + fmt(t.sold[p.key]) + '</span><span class="c">' + fmt(left) +
            (has && diff ? '<br><span class="' + (diff < 0 ? "bad-t" : "warn-t") + '">' + (diff < 0 ? "ขาด " : "เกิน ") + fmt(Math.abs(diff)) + "</span>" : "") + "</span>" +
            '<input class="num" data-ret="' + p.key + '" inputmode="numeric" pattern="[0-9]*" value="' + (has ? n(ret[p.key]) : "") + '" placeholder="' + fmt(Math.max(left, 0)) + '"' + dis + ">";
        }).join("") + "</div>";
    } else html += '<p class="note">ยังไม่มีการรับ / ขาย</p>';
    html += "</div>";
    var km = S.doc.km_start && S.doc.km_end ? n(S.doc.km_end) - n(S.doc.km_start) : null;
    html += '<div class="box"><h3>' + ic("truck", "s") + 'ไมล์รถ</h3><div class="line"><span class="lbl">ไมล์เก่า</span><input class="num wide" id="kms" inputmode="numeric" value="' + (S.doc.km_start || "") + '"' + dis + "></div>" +
      '<div class="line"><span class="lbl">ไมล์ใหม่</span><input class="num wide" id="kme" inputmode="numeric" value="' + (S.doc.km_end || "") + '"' + dis + "></div>" +
      '<div class="line"><span class="lbl">รถวิ่ง</span><b>' + (km != null ? fmt(km) + " กม." : "—") + "</b></div></div>";
    var cash = S.doc.cash || {}, counted = 0;
    html += '<div class="box"><h3>' + ic("cash", "s") + "นับเงิน</h3>" + (S.boot.notes || []).map(function (note) {
      var c = n(cash[String(note)]); counted += c * note;
      return '<div class="cashrow"><b>' + fmt(note) + ' ×</b><input class="num" data-note="' + note + '" inputmode="numeric" pattern="[0-9]*" value="' + (c || "") + '" placeholder="0"' + dis +
        '><span class="sub">' + (c ? "฿" + fmt(c * note) : "") + "</span></div>";
    }).join("");
    counted += n(cash.coins);
    html += '<div class="cashrow"><b>เหรียญ</b><input class="num wide" data-note="coins" inputmode="decimal" value="' + (n(cash.coins) || "") + '" placeholder="฿"' + dis + '><span class="sub"></span></div>';
    var expect = t.cash + t.old, cdiff = counted - expect;
    html += '<div class="sumgrid"><span>ต้องมีเงินสด</span><b>฿' + money(expect) + "</b><span>นับได้</span><b>฿" + money(counted) + "</b>" +
      (S.doc.cash ? '<span>ต่าง</span><b class="' + (Math.abs(cdiff) < 1 ? "ok-t" : cdiff < 0 ? "bad-t" : "warn-t") + '">' + (Math.abs(cdiff) < 1 ? "ตรง ✓" : (cdiff < 0 ? "ขาด ฿" : "เกิน ฿") + money(Math.abs(cdiff))) + "</b>" : "") + "</div></div>";
    html += '<div class="box"><h3>สรุปวันนี้</h3><div class="sumgrid"><span>ขายทั้งหมด</span><b>฿' + money(t.amount) + "</b><span>เงินสด (ขายวันนี้)</span><b>฿" + money(t.cash) +
      "</b><span>เก็บเงินวันก่อน</span><b>฿" + money(t.old) + "</b><span>ค้าง</span><b>฿" + money(t.credit) + "</b><span>วางบิล</span><b>฿" + money(t.invoiced) + "</b></div></div>";
    if (!ro) html += '<button class="btn primary big" id="closeday">' + ic("send", "s") + 'ส่งใบขาย · ปิดวัน</button><p class="note" style="text-align:center;margin-top:6px">ส่งแล้วจะล็อก ถ้าต้องแก้ต้องขอออฟฟิศ</p>';
    html += '<div class="foot"><button class="link" id="logout">' + ic("out", "s") + "ออกจากระบบ</button></div>";
    m.innerHTML = html;
    wireBanners();
    $("logout").onclick = logout;
    if (ro) { m.onchange = null; return; }
    m.onchange = function (e) {
      var el = e.target;
      if (el.hasAttribute("data-ret")) { if (!S.doc.returns) S.doc.returns = {}; if (el.value === "") delete S.doc.returns[el.getAttribute("data-ret")]; else S.doc.returns[el.getAttribute("data-ret")] = n(el.value); }
      else if (el.hasAttribute("data-note")) { if (!S.doc.cash) S.doc.cash = {}; S.doc.cash[el.getAttribute("data-note")] = n(el.value); }
      else if (el.id === "kms") S.doc.km_start = n(el.value) || null;
      else if (el.id === "kme") S.doc.km_end = n(el.value) || null;
      else return;
      changed(); var y = window.scrollY; renderClose(); window.scrollTo(0, y); paintTop();
    };
    $("closeday").onclick = function () {
      var missing = [];
      if (!S.doc.km_end) missing.push("ไมล์ใหม่");
      if (!S.doc.cash) missing.push("นับเงิน");
      if (used.length && !S.doc.returns) missing.push("น้ำแข็งที่เหลือ");
      var msg = (missing.length ? "ยังไม่ได้ใส่: " + missing.join(", ") + "\n\n" : "") + "ส่งแล้วจะล็อก แก้ไม่ได้ ส่งเลยไหม?";
      if (!window.confirm(msg)) return;
      S.doc.closed = true; S.rev += 1; persist();
      clearTimeout(S.syncTimer); sync();
      S.status = "closed"; S.sheetMeta.closed_at = new Date().toISOString(); persist();
      toast("ส่งใบขายแล้ว ✓"); render();
    };
  }
  function logout() {
    if (S.savedRev < S.rev && !window.confirm("ยังมีข้อมูลที่ยังไม่ได้ส่ง ออกจากระบบแล้วข้อมูลจะยังอยู่ในเครื่อง ออกเลยไหม?")) return;
    api("POST", "/api/d/logout").catch(function () {}).then(function () { signedOut(); });
  }

  // ------------------------------------------------------------------ start
  function start() {
    S.token = load(TOKEN_KEY);
    if (!S.token) { showSignIn(); return; }
    var cached = load(BOOT_KEY);
    api("GET", "/api/d/me").then(function (b) { store(BOOT_KEY, b); openDay(b); })
      .catch(function (e) {
        if (e.message === "signin") return;
        if (cached) {
          var d = new Date(), day = d.getFullYear() + "-" + ("0" + (d.getMonth() + 1)).slice(-2) + "-" + ("0" + d.getDate()).slice(-2);   // the phone's own date
          var b = JSON.parse(JSON.stringify(cached));
          if (b.day !== day) { b.sheet = null; b.pending_yesterday = null; b.editable = true; }
          b.today = day; b.day = day;
          openDay(b); toast("ไม่มีสัญญาณ — ใช้ข้อมูลในเครื่อง", true);
        } else $("app").innerHTML = '<div class="boot">ต่ออินเทอร์เน็ตไม่ได้ ลองใหม่อีกครั้ง</div>';
      });
  }
  if ("serviceWorker" in navigator) { try { navigator.serviceWorker.register("/d/sw.js", { scope: "/d/" }); } catch (e) { /* not supported */ } }
  start();
})();
