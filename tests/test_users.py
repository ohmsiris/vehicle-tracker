"""Office logins (app/users.py) and the server-side menu gate (main.role_gate)."""
from fastapi.testclient import TestClient

from app import users


def test_menu_paths():
    assert users.allowed(["sales_sheets"], "/api/sales-routes")
    assert users.allowed(["sales_sheets"], "/api/sales-routes/day/5/walkin")
    assert users.allowed(["sales_sheets"], "/api/sales-routes-attention")
    assert users.allowed([], "/api/meta")
    assert not users.allowed(["sales_sheets"], "/api/routes")
    assert not users.allowed(["sales_sheets"], "/api/users")
    assert not users.allowed(["sales_sheets"], "/api/employees")
    assert not users.allowed(["nonsense"], "/api/sales-routes")


def test_me():
    owner = users.me_for(None)
    assert owner["role"] == "owner" and owner["nav"] is None
    me = users.me_for({"name": "บัญชี", "username": "acc1", "menus": ["routes", "bad", "sales_sheets"]})
    assert me["menus"] == ["sales_sheets", "routes"] and me["nav"] == ["navSalesSheets", "navRoutes"]
    assert me["home"] == "#sales-sheets" and "#routes" in me["hashes"]
    assert users.me_for({"name": "x", "menus": []})["home"] is None


def test_names_and_passwords():
    assert users.valid_username("account1") and not users.valid_username("ab") and not users.valid_username("บัญชี")
    assert users.clean_username("  Acc1 ") == "acc1"
    assert users.valid_password("short") and users.valid_password("รหัสผ่านยาวมาก")
    assert users.valid_password("long-enough-1") is None


def test_a_person_only_reaches_their_menus(monkeypatch):
    from app import main
    object.__setattr__(main.settings, "app_token", "secret")
    monkeypatch.setattr(users, "session_user", lambda t: {"id": 1, "name": "บัญชี", "username": "acc1", "menus": ["sales_sheets"]} if t == "u_ok" else None)
    c = TestClient(main.app)
    r = c.get("/api/users", headers={"X-App-Token": "u_ok"})
    assert r.status_code == 403
    r = c.get("/api/employees", headers={"X-App-Token": "u_ok"})
    assert r.status_code == 403 and "ไม่มีสิทธิ์" in r.json()["detail"]
    r = c.get("/api/routes", headers={"X-App-Token": "u_bad"})                 # unknown token: the endpoint's own check refuses
    assert r.status_code == 403
    main._check_token("u_ok", "")                                               # a signed-in person passes the token check
    main._check_token("secret", "")
    try:
        main._check_owner("u_ok", "")
        raise AssertionError("a person is not the owner")
    except main.HTTPException as e:
        assert e.status_code == 403


def test_new_sales_route_endpoints_answer(monkeypatch):
    from app import main, route_sales
    object.__setattr__(main.settings, "app_token", "secret")
    monkeypatch.setattr(main.supabase, "configured", lambda: True)
    monkeypatch.setattr(route_sales, "attention", lambda: {"walkins": 1, "reopen_requests": 0, "unsent": 0})
    monkeypatch.setattr(route_sales, "walkin_to_customer", lambda day_id, key: {"customer_id": 7})
    c = TestClient(main.app)
    assert c.get("/api/sales-routes-attention", headers={"X-App-Token": "secret"}).json()["walkins"] == 1
    assert c.post("/api/sales-routes/day/3/walkin", json={"key": "new:1"}, headers={"X-App-Token": "secret"}).json() == {"customer_id": 7}


def test_an_owner_account_sees_everything(monkeypatch):
    from app import main
    object.__setattr__(main.settings, "app_token", "secret")
    monkeypatch.setattr(users, "session_user", lambda t: {"id": 2, "name": "เจ้าของ", "username": "boss", "menus": [], "role": "owner"} if t == "u_boss" else None)
    me = users.me_for({"name": "เจ้าของ", "username": "boss", "menus": [], "role": "owner"})
    assert me["role"] == "owner" and me["nav"] is None and me["username"] == "boss"
    main._check_owner("u_boss", "")                                             # may manage people
    c = TestClient(main.app)
    r = c.get("/api/employees", headers={"X-App-Token": "u_boss"})
    assert "ไม่มีสิทธิ์" not in r.text                                           # the menu gate lets the owner through


def test_the_register_is_its_own_menu():
    assert not users.allowed(["sales_sheets"], "/api/customers")
    assert not users.allowed(["sales_sheets"], "/api/customers/photo/1")
    assert users.allowed(["customers"], "/api/customers/12")


def test_every_page_can_be_given_and_the_old_menus_stay():
    """Owner, 13 Nov 2026: every menu with its sections; the five old keys keep their order (people who have them keep exactly that)."""
    assert list(users.MENUS)[:5] == ["sales_sheets", "routes", "cash_fuel", "orders", "customers"]
    opts = users.menu_options()
    assert len(opts) == len(users.MENUS) >= 35 and opts[0]["key"] == "home"
    assert all(o["group"] in users.GROUPS for o in opts)
    navs = [n for m in users.MENUS.values() for n in m["nav"]]
    assert len(navs) == len(set(navs))                                              # each sidebar button in one menu
    assert not any(p in ("/api/", "/api/users") or p.startswith("/api/users") for m in users.MENUS.values() for p in m["api"])
    me = users.me_for({"name": "x", "menus": ["truck_roster", "sales_sheets"]})
    assert "#truck=" in me["hashes"] and me["home"] == "#sales-sheets"
    assert users.allowed(["licences"], "/api/licences/3") and not users.allowed(["sales_sheets"], "/api/licences")
