"""Crawler: feed parsing, funding headlines, matching and change detection (no network, no database)."""
import crawl

FEED = """<?xml version="1.0"?><rss><channel>
<item><title>Acme raises $12M Series A to automate payroll</title><link>https://news.example/acme</link>
<description>&lt;p&gt;Acme, a payroll SaaS, raised a Series A.&lt;/p&gt;</description><pubDate>Tue, 18 Nov 2026 09:00:00 GMT</pubDate></item>
<item><title>Why AI agents are eating SaaS</title><link>https://news.example/essay</link></item>
</channel></rss>"""


def test_feed_items_are_parsed_without_html():
    items = crawl.parse_feed(FEED)
    assert [i["title"] for i in items] == ["Acme raises $12M Series A to automate payroll", "Why AI agents are eating SaaS"]
    assert items[0]["summary"] == "Acme, a payroll SaaS, raised a Series A." and items[0]["published"].year == 2026


def test_funding_headlines():
    assert crawl.funding_of("Acme raises $12M Series A to automate payroll") == {"company": "Acme", "amount": "$12M", "round": "Series A"}
    f = crawl.funding_of("Zenduty secures Rs 40 Cr in pre-Series A round led by X")
    assert f["company"] == "Zenduty" and f["round"].lower() == "pre-series a"
    assert crawl.funding_of("Why AI agents are eating SaaS") is None
    assert crawl.funding_of("Physical AI startup Humyn Labs raises $47M in Series A")["company"] == "Humyn Labs"
    assert crawl.funding_of("YC-backed LATO raises €4.28 million")["company"] == "LATO"
    assert crawl.funding_of("Ghent-based IntelliProve raises €2.7 million")["company"] == "IntelliProve"


def test_changes_between_two_researches():
    old = {"qualification": {"decision": "QUALIFIED_FLAGGED", "score": 40, "certs_held": []}, "profile": {"funding_stage": None}}
    new = {"qualification": {"decision": "DORMANT", "score": None, "certs_held": ["SOC 2"]}, "profile": {"funding_stage": "Series A"}}
    got = {c["field"]: (c["before"], c["after"]) for c in crawl.changes(old, new)}
    assert got["decision"] == ("QUALIFIED_FLAGGED", "DORMANT") and got["certificates"] == ([], ["SOC 2"])
    assert got["funding"] == (None, "Series A") and "score" not in got        # a score going to None is not news
