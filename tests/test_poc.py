"""Hunter contact lookup: cache per domain and the monthly cap (no network)."""
import config
import db
import poc


def setup(monkeypatch, cached=None, usage=None):
    calls = {"hunter": 0, "saved": None, "usage": None}
    monkeypatch.setattr(config, "HUNTER_API_KEY", "test-key")
    monkeypatch.setattr(poc, "_load_overrides", lambda: {})
    monkeypatch.setattr(db, "get_cached_page", lambda url: cached)
    monkeypatch.setattr(db, "put_cached_page", lambda url, rec: calls.__setitem__("saved", (url, rec)))
    monkeypatch.setattr(db, "get_setting", lambda key: usage)
    monkeypatch.setattr(db, "put_setting", lambda key, val: calls.__setitem__("usage", val))

    class R:
        status_code = 200
        def json(self):
            return {"data": {"emails": [
                {"first_name": "Asha", "last_name": "Rao", "value": "asha@acme.io", "position": "CTO", "confidence": 90},
                {"first_name": "Sam", "last_name": "Lee", "value": "sam@acme.io", "position": "Sales Manager", "confidence": 99}]}}

    def fake_get(*a, **k):
        calls["hunter"] += 1
        return R()
    monkeypatch.setattr(poc.requests, "get", fake_get)
    return calls


def test_hunter_picks_the_decision_maker_and_caches_it(monkeypatch):
    calls = setup(monkeypatch)
    r = poc.find_poc("acme.io", 80)
    assert r["first_name"] == "Asha" and r["source"] == "hunter.io" and calls["hunter"] == 1
    assert calls["saved"][0] == "hunter-dm://acme.io" and calls["usage"]["count"] == 1


def test_cached_result_uses_no_search(monkeypatch):
    calls = setup(monkeypatch, cached={"emails": [{"first_name": "Asha", "value": "asha@acme.io", "position": "CTO", "confidence": 90}]})
    r = poc.find_poc("acme.io", 80)
    assert r["first_name"] == "Asha" and "(cached)" in r["source"] and calls["hunter"] == 0


def test_monthly_cap_stops_searches(monkeypatch):
    import datetime as dt
    calls = setup(monkeypatch, usage={"month": dt.date.today().strftime("%Y-%m"), "count": config.HUNTER_MONTHLY_LIMIT})
    r = poc.find_poc("acme.io", 80)
    assert "monthly limit" in r["error"] and calls["hunter"] == 0


def test_last_months_usage_does_not_count(monkeypatch):
    calls = setup(monkeypatch, usage={"month": "2000-01", "count": 999})
    r = poc.find_poc("acme.io", 80)
    assert r["first_name"] == "Asha" and calls["hunter"] == 1
