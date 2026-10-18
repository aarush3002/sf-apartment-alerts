import pytest
import yaml

from aptbot import configedit as ce
from aptbot.discover import Candidate, fingerprint, render_block, site_name, slug_id

SAMPLE = """# top comment
filters:
  bedrooms: [2, 3]   # 0 = studio
  include_unknown_bedrooms: false

notify:
  to: ["+15550000001"]   # one text per recipient
  channels:
    - imessage

poll:
  interval: 120

buildings:
  alpha:
    name: Alpha Tower
    stories: 20
    source: {type: sightmap, embed: abc123}
  beta:
    name: Beta House
    enabled: false
    stories: 6
    source: {type: jdfp, url: "https://beta.example/floorplans/"}
"""


def test_recipients_roundtrip():
    assert ce.get_recipients(SAMPLE) == ["+15550000001"]
    out = ce.set_recipients(SAMPLE, ["+15550000001", "+15550000002"])
    assert yaml.safe_load(out)["notify"]["to"] == ["+15550000001", "+15550000002"]
    assert "# one text per recipient" in out
    assert yaml.safe_load(out)["buildings"].keys() == {"alpha", "beta"}
    with pytest.raises(ce.ConfigEditError):
        ce.set_recipients(SAMPLE, [])


def test_set_filter_add_change_and_clear():
    out = ce.set_filter(SAMPLE, "bedrooms", [0, 1, 2])
    assert yaml.safe_load(out)["filters"]["bedrooms"] == [0, 1, 2]
    assert "# 0 = studio" in out, "inline comment should survive"
    out = ce.set_filter(SAMPLE, "max_price", 7000.0)
    assert yaml.safe_load(out)["filters"]["max_price"] == 7000
    assert yaml.safe_load(ce.set_filter(out, "max_price", None))["filters"].get("max_price") is None
    # neighbouring sections must be untouched
    assert yaml.safe_load(out)["notify"]["to"] == ["+15550000001"]
    assert yaml.safe_load(out)["poll"]["interval"] == 120


def test_enable_disable():
    assert yaml.safe_load(ce.set_enabled(SAMPLE, "alpha", False))["buildings"]["alpha"]["enabled"] is False
    assert yaml.safe_load(ce.set_enabled(SAMPLE, "beta", True))["buildings"]["beta"]["enabled"] is True
    with pytest.raises(ce.ConfigEditError):
        ce.set_enabled(SAMPLE, "nope", True)


def test_append_building():
    block = render_block("gamma", "Gamma Flats", Candidate("sightmap", {"embed": "xyz789"}, "why"),
                         address="1 Main St", stories=12, listing_url="https://gamma.example/")
    out = ce.append_building(SAMPLE, "gamma", block)
    d = yaml.safe_load(out)
    assert len(d["buildings"]) == 3
    g = d["buildings"]["gamma"]
    assert g["name"] == "Gamma Flats" and g["stories"] == 12 and g["source"] == {"type": "sightmap", "embed": "xyz789"}
    with pytest.raises(ce.ConfigEditError):
        ce.append_building(SAMPLE, "alpha", block)


def test_fingerprint_detects_platforms():
    html = ('<a href="https://foo.securecafe.com/onlineleasing/bar-1/guestlogin.aspx">apply</a>'
            '<iframe src="https://sightmap.com/embed/abc123x"></iframe>'
            '<script src="https://sightmap.com/embed/api.js"></script>')
    found = {c.type: c.params for c in fingerprint(html, "https://example.com/floorplans")}
    assert found["sightmap"] == {"embed": "abc123x"}, "must skip the embed/api.js script"
    assert found["securecafe"] == {"host": "foo.securecafe.com", "slug": "bar-1"}
    assert fingerprint("<html>nothing here</html>", "https://example.com") == []
    assert "jdfp" in {c.type for c in fingerprint('<script id="jd-fp-data-script-app">{}</script>', "https://x.com/fp")}


def test_site_name_prefers_brand_over_marketing_copy():
    assert site_name("<title>Studio, 1, 2, 3 Bedroom Apartment in San Francisco | Chorus</title>", "https://x.com") == "Chorus"
    assert site_name("<title>Floor Plans | 1000 Chestnut</title>", "https://x.com") == "1000 Chestnut"
    assert site_name('<meta property="og:site_name" content="Chorus"><title>Floor Plans</title>', "https://x.com") == "Chorus"
    assert site_name("<html></html>", "https://www.the-gantry.com/x") == "The Gantry"
    assert slug_id("The Brady Apartments!") == "the_brady_apartments"
