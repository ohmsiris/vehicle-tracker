import io
import re
import sys
from datetime import date
from pathlib import Path

import pytest
from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app import batch, files
from app.extraction import CATEGORIES, EXTRACTION_SCHEMA

ROOT = Path(__file__).resolve().parent.parent
TODAY = date(2026, 10, 19)


# ---------------------------------------------------------------- categories
def test_categories_match_schema_sql():
    sql = (ROOT / "schema.sql").read_text(encoding="utf-8")
    block = sql.split("insert into categories", 1)[1].split(";", 1)[0]
    in_sql = re.findall(r"\('([^']+)',\s*'[^']+',\s*\d+\)", block)
    assert in_sql == CATEGORIES


def test_schema_has_confidence_fields_required():
    assert "low_confidence_fields" in EXTRACTION_SCHEMA["required"]
    item = EXTRACTION_SCHEMA["properties"]["line_items"]["items"]
    assert "low_confidence_fields" in item["required"]
    assert set(item["required"]) == set(item["properties"])


# --------------------------------------------------------------------- files
def _img_bytes(size=(100, 80), fmt="JPEG", exif_orientation=None):
    img = Image.new("RGB", size, "white")
    out = io.BytesIO()
    kwargs = {}
    if exif_orientation:
        exif = Image.Exif()
        exif[0x0112] = exif_orientation
        kwargs["exif"] = exif
    img.save(out, format=fmt, **kwargs)
    return out.getvalue()


def test_small_jpeg_passes_through_untouched():
    raw = _img_bytes()
    data, mt = files.prepare_file(raw)
    assert data == raw and mt == "image/jpeg"


def test_pdf_passes_through():
    raw = b"%PDF-1.7\n%fake"
    assert files.prepare_file(raw) == (raw, "application/pdf")


def test_rejects_non_image_even_with_image_name():
    with pytest.raises(files.UnsupportedFile):
        files.prepare_file(b"hello, I am a text file")


def test_rejects_empty_and_corrupt():
    with pytest.raises(files.UnsupportedFile):
        files.prepare_file(b"")
    with pytest.raises(files.UnsupportedFile):
        files.prepare_file(b"\xff\xd8\xff" + b"garbage" * 10)


def test_huge_dimension_is_downscaled():
    raw = _img_bytes(size=(5000, 3000))
    data, mt = files.prepare_file(raw)
    assert mt == "image/jpeg"
    assert max(Image.open(io.BytesIO(data)).size) <= files.MAX_LONGEST_SIDE


def test_exif_rotation_is_baked_in():
    raw = _img_bytes(size=(200, 100), exif_orientation=6)  # needs 90deg rotate
    data, _ = files.prepare_file(raw)
    assert Image.open(io.BytesIO(data)).size == (100, 200)


# --------------------------------------------------------------------- batch
def _bill(**over):
    base = {
        "shop_name": "อู่ช่างแดง", "date": "2026-10-10", "branch": "สระบุรี",
        "vehicle_license": "1ฒถ 8966", "vehicle_license_province": "", "vehicle_number": "",
        "mileage": "650316", "next_service_mileage": "660316", "total_cost": 3000,
        "has_total_this_page": True, "continues_next_page": False,
        "low_confidence_fields": [],
        "line_items": [
            {"quantity": 2, "unit": "ชิ้น", "description": "ผ้าเบรก", "cost": 2000, "category": "เบรก", "low_confidence_fields": []},
            {"quantity": 0, "unit": "", "description": "ค่าแรง", "cost": 1000, "category": "ค่าแรง", "low_confidence_fields": []},
        ],
    }
    base.update(over)
    return base


def _file(i, bill=None, ok=True, error=None):
    return {"index": i, "filename": f"f{i}.jpg", "media_type": "image/jpeg", "ok": ok,
            "error": error, "usage": None, "extraction": bill if ok else None}


ROSTER = [{"branch": "สระบุรี", "truck_number": "1", "plate": "1ฒถ 8966", "province": "กรุงเทพมหานคร"}]


def draft(files_, plate=""):
    return batch.build_draft(files_, ROSTER, selected_plate=plate, today=TODAY)


def test_clean_single_bill_has_no_flags_or_warnings():
    d = draft([_file(0, _bill())])
    assert d["warnings"] == []
    assert d["event"]["low_confidence_fields"] == []
    assert d["event"]["total_cost"] == 3000
    assert d["event"]["job_type"] == "เบรก"  # labor excluded from job_type
    assert d["event"]["event_date"] == "2026-10-10"
    assert d["bill_vehicle"]["vehicle_number"] == "1"  # filled from roster
    assert d["bill_vehicle"]["province"] == "กรุงเทพมหานคร"
    li = d["line_items"]
    assert [x["line_number"] for x in li] == [1, 2]
    assert li[0]["unit_cost"] == 1000 and li[0]["quantity"] == 2
    assert li[1]["quantity"] is None and li[1]["unit"] is None and li[1]["unit_cost"] is None


def test_multipage_bill_sums_correctly():
    p1 = _bill(total_cost=0, has_total_this_page=False, continues_next_page=True,
               line_items=[{"quantity": 1, "unit": "ชิ้น", "description": "A", "cost": 500, "category": "เบรก", "low_confidence_fields": []}])
    p2 = _bill(total_cost=1500, has_total_this_page=True,
               line_items=[{"quantity": 1, "unit": "ชิ้น", "description": "B", "cost": 1500, "category": "เบรก", "low_confidence_fields": []}])
    d = draft([_file(0, p1), _file(1, p2)])
    assert d["event"]["total_cost"] == 1500
    assert [x["file_index"] for x in d["line_items"]] == [0, 1]
    assert [x["line_number"] for x in d["line_items"]] == [1, 2]


def test_missing_last_page_warns():
    p1 = _bill(total_cost=0, has_total_this_page=False, continues_next_page=True)
    d = draft([_file(0, p1)])
    assert "total_cost" in d["event"]["low_confidence_fields"]
    assert any("ขาดหน้า" in w for w in d["warnings"])
    assert d["event"]["total_cost"] == 3000  # falls back to the line sum


def test_total_mismatch_flags_total():
    d = draft([_file(0, _bill(total_cost=9999))])
    assert "total_cost" in d["event"]["low_confidence_fields"]
    assert any("ไม่ตรงกับยอดรวม" in w for w in d["warnings"])


def test_buddhist_year_slip_is_corrected():
    d = draft([_file(0, _bill(date="2569-10-10"))])
    assert d["event"]["event_date"] == "2026-10-10"


def test_implausible_date_flagged():
    d = draft([_file(0, _bill(date="2015-01-01"))])
    assert "event_date" in d["event"]["low_confidence_fields"]


def test_unparseable_date_flagged_and_none():
    d = draft([_file(0, _bill(date=""))])
    assert d["event"]["event_date"] is None
    assert "event_date" in d["event"]["low_confidence_fields"]


def test_conflicting_mileage_flagged():
    d = draft([_file(0, _bill(mileage="650316")), _file(1, _bill(mileage="650361", total_cost=0, has_total_this_page=False))])
    assert "mileage" in d["event"]["low_confidence_fields"]


def test_next_service_not_after_current_flagged():
    d = draft([_file(0, _bill(mileage="650316", next_service_mileage="640316"))])
    assert {"mileage", "next_service_mileage"} <= set(d["event"]["low_confidence_fields"])


def test_plate_mismatch_with_selected_vehicle():
    d = draft([_file(0, _bill())], plate="9กก 1111")
    assert "vehicle" in d["event"]["low_confidence_fields"]
    assert any("เลือกรถ" in w for w in d["warnings"])


def test_selected_plate_matches_ignoring_spaces():
    d = draft([_file(0, _bill())], plate="1ฒถ8966")
    assert d["warnings"] == []


def test_model_flags_are_mapped_to_event_names():
    d = draft([_file(0, _bill(low_confidence_fields=["shop_name", "date", "vehicle_number"]))])
    assert {"vendor", "event_date", "vehicle"} <= set(d["event"]["low_confidence_fields"])


def test_line_level_auto_flags():
    bill = _bill(line_items=[
        {"quantity": 1, "unit": "", "description": "สายไฟ [illegible]", "cost": 0, "category": "ระบบไฟฟ้า", "low_confidence_fields": ["unit"]},
    ], total_cost=0, has_total_this_page=False)
    d = draft([_file(0, bill)])
    assert d["line_items"][0]["low_confidence_fields"] == ["cost", "description", "unit"]


def test_failed_file_does_not_sink_batch():
    d = draft([_file(0, _bill()), _file(1, ok=False, error="boom")])
    assert d["event"]["total_cost"] == 3000
    assert any("f1.jpg" in w and "boom" in w for w in d["warnings"])
    assert [b["ok"] for b in d["bills"]] == [True, False]


def test_all_files_failed():
    d = draft([_file(0, ok=False, error="boom")])
    assert d["event"]["total_cost"] is None and d["line_items"] == []


def test_multiple_vendors_warn():
    d = draft([_file(0, _bill()), _file(1, _bill(shop_name="อู่อื่น", total_cost=0, has_total_this_page=False))])
    assert "vendor" in d["event"]["low_confidence_fields"]


# ------------------------------------------------------------------ endpoint
def test_endpoint_auth_and_flow(monkeypatch):
    from fastapi.testclient import TestClient

    from app import main
    object.__setattr__(main.settings, "app_token", "secret")
    object.__setattr__(main.settings, "anthropic_api_key", "test-key")

    calls = []

    def fake_extract(data, media_type, roster_text=""):
        calls.append(media_type)
        return _bill(), {"input_tokens": 1, "output_tokens": 1}

    monkeypatch.setattr(main, "extract_file", fake_extract)
    client = TestClient(main.app)
    img = _img_bytes()

    assert client.post("/api/extract", files=[("files", ("a.jpg", img, "image/jpeg"))]).status_code == 403
    r = client.post("/api/extract", headers={"X-App-Token": "wrong"}, files=[("files", ("a.jpg", img, "image/jpeg"))])
    assert r.status_code == 403

    r = client.post(
        "/api/extract?token=secret",
        files=[("files", ("a.jpg", img, "image/jpeg")), ("files", ("b.txt", b"not an image", "image/jpeg"))],
    )
    assert r.status_code == 200
    body = r.json()
    assert calls == ["image/jpeg"]  # the text file never reached the model
    assert [b["ok"] for b in body["bills"]] == [True, False]
    assert body["event"]["total_cost"] == 3000


def test_endpoint_refuses_when_token_unconfigured():
    from fastapi.testclient import TestClient

    from app import main
    object.__setattr__(main.settings, "app_token", "")
    r = TestClient(main.app).post("/api/extract", files=[("files", ("a.jpg", _img_bytes(), "image/jpeg"))])
    assert r.status_code == 503


def test_file_with_no_money_warns_not_a_bill():
    junk = _bill(total_cost=0, has_total_this_page=False,
                 line_items=[{"quantity": 0, "unit": "", "description": "เช็คเบรค", "cost": 0, "category": "เบรก", "low_confidence_fields": []}])
    d = draft([_file(0, _bill()), _file(1, junk)])
    msgs = [w for w in d["warnings"] if "ดูไม่เหมือนบิล" in w]
    assert len(msgs) == 1 and "f1.jpg" in msgs[0]


# ---------------------------------------------------------------- categorize
def test_english_category_names_match_schema_sql():
    from app.categorize import CATEGORY_EN
    sql = (ROOT / "schema.sql").read_text(encoding="utf-8")
    block = sql.split("insert into categories", 1)[1].split(";", 1)[0]
    in_sql = re.findall(r"\('[^']+',\s*'([^']+)',\s*\d+\)", block)
    assert in_sql == CATEGORY_EN


class _FakeMessages:
    def __init__(self, payloads):
        self.payloads = list(payloads)
        self.calls = 0

    def create(self, **kwargs):
        from types import SimpleNamespace
        self.calls += 1
        payload = self.payloads.pop(0)
        if isinstance(payload, Exception):
            raise payload
        return SimpleNamespace(content=[SimpleNamespace(type="text", text=payload)])


def _fake_client(monkeypatch, payloads):
    from types import SimpleNamespace

    from app import categorize as cat
    msgs = _FakeMessages(payloads)
    monkeypatch.setattr(cat, "get_client", lambda: SimpleNamespace(messages=msgs))
    return msgs


def test_categorize_maps_english_back_to_thai_by_line_number(monkeypatch):
    import json

    from app.categorize import categorize
    # out of order on purpose -- matching is by line number, not position
    payload = json.dumps({"lines": [
        {"line": 2, "is": "brake pads", "category": "Brakes"},
        {"line": 1, "is": "battery", "category": "Electrical"},
    ]})
    _fake_client(monkeypatch, [payload])
    assert categorize([{"description": "แบตเตอรี่"}, {"description": "ผ้าเบรก"}]) == ["ระบบไฟฟ้า", "เบรก"]


def test_categorize_retries_on_wrong_line_count_then_succeeds(monkeypatch):
    import json

    from app.categorize import categorize
    bad = json.dumps({"lines": [{"line": 1, "is": "x", "category": "Engine"}]})  # 1 of 2
    good = json.dumps({"lines": [{"line": 1, "is": "x", "category": "Engine"}, {"line": 2, "is": "y", "category": "Labor"}]})
    msgs = _fake_client(monkeypatch, [bad, good])
    assert categorize([{"description": "a"}, {"description": "b"}]) == ["เครื่องยนต์", "ค่าแรง"]
    assert msgs.calls == 2


def test_categorize_raises_after_exhausting_attempts(monkeypatch):
    from app.categorize import MAX_ATTEMPTS, categorize
    msgs = _fake_client(monkeypatch, [RuntimeError("api down")] * MAX_ATTEMPTS)
    with pytest.raises(RuntimeError):
        categorize([{"description": "a"}])
    assert msgs.calls == MAX_ATTEMPTS


def test_categorize_empty_makes_no_call(monkeypatch):
    from app.categorize import categorize
    msgs = _fake_client(monkeypatch, [])
    assert categorize([]) == [] and msgs.calls == 0


def test_categorize_endpoint(monkeypatch):
    from fastapi.testclient import TestClient

    from app import main
    object.__setattr__(main.settings, "app_token", "secret")
    object.__setattr__(main.settings, "anthropic_api_key", "test-key")
    monkeypatch.setattr(main, "categorize", lambda items: ["เบรก"] * len(items))
    client = TestClient(main.app)
    body = {"items": [{"description": "ผ้าเบรก", "unit": "ชุด"}]}
    assert client.post("/api/categorize", json=body).status_code == 403
    r = client.post("/api/categorize?token=secret", json=body)
    assert r.status_code == 200 and r.json() == {"categories": ["เบรก"]}
    too_many = {"items": [{"description": "x"}] * 81}
    assert client.post("/api/categorize?token=secret", json=too_many).status_code == 422

    def boom(items):
        raise RuntimeError("down")
    monkeypatch.setattr(main, "categorize", boom)
    assert client.post("/api/categorize?token=secret", json=body).status_code == 502


def test_app_files_are_always_revalidated_but_fonts_are_cached():
    from fastapi.testclient import TestClient

    from app import main
    c = TestClient(main.app)
    for path in ("/", "/static/app.js", "/static/board.js", "/static/dashboard.js", "/static/app.css", "/static/index.html"):
        r = c.get(path)
        assert r.status_code == 200 and r.headers["cache-control"] == "no-cache", path
    font = c.get("/static/fonts/fonts.css")
    assert font.headers["cache-control"] == "public, max-age=604800"
    assert "cache-control" not in c.get("/health").headers          # API responses are untouched


def test_production_hardening_headers_and_no_public_docs():
    from fastapi.testclient import TestClient

    from app import main
    c = TestClient(main.app)
    for path in ("/", "/static/app.js", "/health"):
        h = c.get(path).headers
        assert h["referrer-policy"] == "no-referrer" and h["x-content-type-options"] == "nosniff" and h["x-frame-options"] == "DENY", path
    for path in ("/docs", "/redoc", "/openapi.json"):
        assert c.get(path).status_code == 404, path


def test_railway_config_is_valid_and_points_at_the_health_check():
    import json
    cfg = json.loads((ROOT / "railway.json").read_text(encoding="utf-8"))
    assert cfg["deploy"]["startCommand"] == "uvicorn app.main:app --host 0.0.0.0 --port $PORT"
    assert cfg["deploy"]["healthcheckPath"] == "/health"
    assert (ROOT / ".python-version").read_text().strip() == "3.12"


def test_hidden_attribute_always_wins_over_a_components_own_display():
    # `.card.note { display: flex }` used to override [hidden], so a banner that should have been
    # hidden showed on every visit. One global rule makes `hidden` mean hidden everywhere.
    css = (ROOT / "static" / "app.css").read_text(encoding="utf-8")
    assert "[hidden] { display: none !important; }" in css


def test_the_page_has_the_manual_entry_pieces_and_no_top_bar_step_text():
    html = (ROOT / "static" / "index.html").read_text(encoding="utf-8")
    for needle in ('id="modeBill"', 'id="modeManual"', 'id="manualMode"', 'id="manualSaveBtn"', 'id="fixSelf"', "/static/manual.js"):
        assert needle in html, needle
    assert 'id="stepHint"' not in html
    # every id the scripts look up with $("...") exists (a typo here is a silent crash in the browser)
    import re
    ids = set(re.findall(r'id="([^"]+)"', html))
    for js in ("manual.js", "board.js", "app.js", "fleet.js", "dashboard.js"):
        src = (ROOT / "static" / js).read_text(encoding="utf-8")
        missing = {i for i in re.findall(r'\$\("([A-Za-z][A-Za-z0-9_]*)"\)', src) if i not in ids}
        # ids that are created by the scripts themselves (edit form, review form) are not in the static HTML
        created = set(re.findall(r'id: "([^"]+)"', src)) | set(re.findall(r'input\("([A-Za-z0-9_]+)"', src)) | set(re.findall(r'field\([^)]*?\bid: "([^"]+)"', src))
        dynamic = {"evVehicle", "evDate", "evDateCap", "evVendor", "evBranch", "evMileage", "evNext", "evTotal", "evNotes", "evJobType",
                   "edVehicle", "edDate", "edDateCap", "edVendor", "edPerformedBy", "edBranch", "edMileage", "edNext", "edTotal", "edSummary", "edNotes",
                   "edTotals", "edSave", "edAiSummary", "editLines"}
        assert not (missing - created - dynamic), (js, sorted(missing - created - dynamic))
