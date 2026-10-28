import threading
import time

import pytest

from app import supabase


class FakeResp:
    status_code = 200
    text = ""

    def __init__(self, rows):
        self.rows = rows

    def json(self):
        return self.rows


class FakeClient:
    def __init__(self):
        self.gets = 0
        self.rows = [{"id": 1, "name": "a"}]

    def get(self, url, params=None):
        self.gets += 1
        return FakeResp([dict(r) for r in self.rows])

    def post(self, url, params=None, json=None, headers=None):
        return FakeResp(json)

    def patch(self, url, params=None, json=None, headers=None):
        return FakeResp([json])

    def delete(self, url, params=None):
        return FakeResp([])


@pytest.fixture
def fake(monkeypatch):
    c = FakeClient()
    monkeypatch.setattr(supabase, "_client", lambda: c)
    supabase._forget()
    yield c
    supabase._forget()


def test_a_reference_table_is_read_once_for_a_few_seconds_and_a_write_drops_it(fake):
    assert supabase.select("vehicles", {"select": "id"}) == [{"id": 1, "name": "a"}]
    supabase.select("vehicles", {"select": "id"})
    assert fake.gets == 1                                                      # the second ask was answered from memory
    supabase.select("vehicles", {"select": "id,name"})
    assert fake.gets == 2                                                      # another question is another read
    supabase.update("vehicles", {"id": "eq.1"}, {"name": "b"})
    supabase.select("vehicles", {"select": "id"})
    assert fake.gets == 3                                                      # the write dropped the old answer


def test_what_a_caller_gets_is_a_copy_so_editing_it_does_not_change_the_next_answer(fake):
    first = supabase.select("shops", {"select": "*"})
    first[0]["name"] = "changed"
    first.append({"id": 2})
    assert supabase.select("shops", {"select": "*"}) == [{"id": 1, "name": "a"}]


def test_big_changing_tables_are_never_cached_and_the_cache_expires(fake, monkeypatch):
    supabase.select("maintenance_events", {"select": "id"})
    supabase.select("maintenance_events", {"select": "id"})
    assert fake.gets == 2
    supabase.select("machines", {"select": "id"})
    monkeypatch.setattr(supabase, "CACHE_SECONDS", 0.0)
    supabase.select("machines", {"select": "id"})
    assert fake.gets == 4


def test_parallel_runs_the_reads_together_and_keeps_their_order():
    started = []

    def slow(n):
        def run():
            started.append(n)
            time.sleep(0.2)
            return n
        return run
    t0 = time.time()
    assert supabase.parallel(slow(1), slow(2), slow(3), slow(4)) == [1, 2, 3, 4]
    assert time.time() - t0 < 0.6                                              # 4 x 0.2 s one after another would be 0.8 s
    with pytest.raises(ValueError):
        supabase.parallel(lambda: 1, lambda: (_ for _ in ()).throw(ValueError("x")))


def test_a_big_table_is_read_in_pages_at_once_and_the_rows_keep_their_order(monkeypatch):
    data = [{"n": i} for i in range(2350)]

    def fake_select(table, params=None):
        off, lim = int(params["offset"]), int(params["limit"])
        return data[off:off + lim]
    monkeypatch.setattr(supabase, "select", fake_select)
    assert supabase.select_all_parallel("t", {"order": "n.asc"}) == data
    big = [{"n": i} for i in range(5300)]                                      # more than the 5 pages asked for at once: the rest follows
    monkeypatch.setattr(supabase, "select", lambda table, params=None: big[int(params["offset"]):int(params["offset"]) + int(params["limit"])])
    assert supabase.select_all_parallel("t", {"order": "n.asc"}) == big
    with pytest.raises(supabase.SupabaseError):
        supabase.select_all_parallel("t", {})
