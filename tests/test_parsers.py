import json

from aptbot.sources import avalon, equity, greystar, jdfp, nema, related, securecafe, sightmap, udr
from aptbot.sources.util import building_label, fmt_date, parse_beds, parse_money_range


def test_util_helpers():
    assert parse_beds("Studio") == 0 and parse_beds("2 Bed") == 2 and parse_beds(0.5) == 0 and parse_beds("Jr. 1 Bed") == 1
    assert parse_money_range("$5,434 - $7,000") == (5434.0, 7000.0)
    assert parse_money_range("$7,830") == (7830.0, 7830.0)
    assert fmt_date("Available Now") == "Now" and fmt_date("Available") == "Now" and fmt_date("10/14/2026") == "2026-10-14"
    assert fmt_date("/Date(1790899200000)/") == "2026-10-02" and fmt_date("2026-10-23T04:00:00+00:00") == "2026-10-23"
    assert building_label("Mission Rock", "Canyon B9", {"Canyon": "The Canyon", "Verde": "Verde"}) == "The Canyon"
    assert building_label("X", "A1", {"Canyon": "C"}) == "X"


def test_securecafe_spera(fixture):
    rows = securecafe.parse_available_units(fixture("securecafe_spera.html"))
    assert len(rows) == 13
    two = [r for r in rows if r["bedrooms"] == 2]
    assert len(two) == 5
    r = rows[0]
    assert r["unit_number"] == "07I" and r["sqft"] == 480 and r["price"] == 5009.0 and r["available"] == "2026-10-04"
    assert r["unit_id"] == "42497005" and r["floorplan_id"] == "5417804"
    cfg = {"host": "sentral.securecafe.com", "slug": "spera", "property_id": "1857892"}
    assert securecafe.apply_url(cfg, r["unit_id"], r["floorplan_id"]).endswith("FloorPlanID=5417804&UnitID=42497005&header=1")


def test_securecafe_nema_and_missionrock(fixture):
    rows = securecafe.parse_available_units(fixture("securecafe_nema.html"))
    assert len(rows) == 49 and sum(1 for r in rows if r["bedrooms"] == 2) == 18
    assert rows[0]["unit_number"] == "1901" and rows[0]["available"] == "2026-09-17"
    rows = securecafe.parse_available_units(fixture("securecafe_missionrock.html"))
    assert len(rows) == 20 and rows[0]["floorplan"].startswith("Canyon")
    assert any(r["bedrooms"] == 3 for r in rows)
    assert securecafe.resolve_property_id_from_html(fixture("securecafe_spera.html")) == "1857892"


def test_sightmap(fixture):
    rows = sightmap.parse_sightmap(json.loads(fixture("sightmap_100vn.json")))
    assert len(rows) == 27
    r = rows[0]
    assert r["unit_number"] == "1103" and r["bedrooms"] == 2 and r["price"] == 7595.0 and r["sqft"] == 1101 and r["floor"] == "11"
    assert sightmap.find_api_url(fixture("sightmap_embed.html")) == "https://sightmap.com/app/api/v1/8ywkl0d2vlx/sightmaps/102068"


def test_jdfp(fixture):
    rows = jdfp.parse_jdfp(fixture("jdfp_1177market.html"))
    assert len(rows) == 10 and rows[0]["unit_number"] == "0219" and rows[0]["bedrooms"] == 2 and rows[0]["available"] == "Now"
    assert rows[0]["permalink"].startswith("/floorplans/unit-")
    rows = jdfp.parse_jdfp(fixture("jdfp_100vanness.html"))
    assert len(rows) == 29 and sum(1 for r in rows if r["bedrooms"] == 2) == 12


def test_greystar(fixture):
    d = greystar.parse_greystar(fixture("greystar_solaire.html"))
    assert len(d["units"]) == 11 and d["lease_url"].startswith("https://solairesf.securecafe.com/")
    u = d["units"][0]
    assert u["unit_number"] == "1006" and u["bedrooms"] == 2 and u["price"] == 7517 and u["floor"] == "10"


def test_equity(fixture):
    d = equity.parse_equity(fixture("equity_340fremont.html"))
    assert len(d["units"]) == 7 and d["prop_id"] == "29921"
    two = [u for u in d["units"] if u["bedrooms"] == 2]
    assert len(two) == 4
    assert equity.apply_link(d, d["units"][0]) == (
        "https://app.meetelise.com/eliseCrmApi/application/external/equity/redirect?external_property_id=29921&unit_prefix=1&unit_number=217")


def test_related(fixture):
    rows = related.parse_related(fixture("related_paramount.html"))
    assert len(rows) == 4
    assert rows[0]["bedrooms"] == 0 and rows[0]["price"] == 4215.0 and rows[0]["available"] == "Now"
    assert rows[0]["url"].startswith("https://www.relatedrentals.com/apartment-rentals/san-francisco/soma/the-paramount/")
    assert sum(1 for r in rows if r["bedrooms"] == 2) == 2


def test_avalon(fixture):
    rows = avalon.parse_avalon(fixture("avalon_ava55.html"))
    assert len(rows) == 12
    assert rows[0]["unit_number"] == "316" and rows[0]["price"] == 3235.0 and rows[0]["url"].endswith("CA100-CA100-001-316")


def test_udr(fixture):
    d = udr.parse_udr(fixture("udr_399fremont.html"))
    assert len(d["units"]) == 33 and d["prop_id"] == "51589"
    u = d["units"][0]
    assert u["unit_number"] == "2909" and u["bedrooms"] == 1 and u["price"] == 6013.0 and u["available"] == "2026-10-02"
    assert udr.apply_link(d, u) == "https://www.udr.com/leaseoll/floorplan/?pid=51589&realpagesiteid=3803916&unitid=348&maxDaysToShow=49"
    assert sum(1 for x in d["units"] if x["bedrooms"] == 2) == 17


def test_nema(fixture):
    rows = nema.parse_nema(fixture("nema_availabilities.html"))
    assert len(rows) == 49 and rows[0]["unit_number"] == "1831" and rows[0]["apply"].startswith("https://rentnema.securecafe.com/onlineleasing/nema/oleapplication.aspx?")
    assert "&amp;" not in rows[0]["apply"]


def test_rentcafe_site(fixture):
    from aptbot.sources import rentcafe_site as rs
    fps = rs.parse_floorplans(fixture("rentcafe_site_towers_floorplans.html"), "https://www.thetowersatrincon.com/floorplans")
    assert len(fps) == 7 and fps[0]["floorplan"] == "Embarcadero" and fps[0]["bedrooms"] == 0
    assert fps[0]["price"] == 4548.0 and fps[0]["price_max"] == 4800.0 and fps[0]["available"] == "2026-10-04"
    assert fps[0]["detail_url"] == "https://www.thetowersatrincon.com/floorplans/embarcadero"
    units = rs.parse_detail(fixture("rentcafe_site_towers_detail.html"), "https://www.thetowersatrincon.com/floorplans/embarcadero-2")
    assert len(units) == 12 and units[0]["unit_number"] == "1221" and units[0]["available"] == "2026-09-21"
    assert units[0]["apply_url"].startswith("https://thetowersatrincon.securecafe.com/onlineleasing/") and units[0]["unit_id"] == "48280833"
    assert rs.label_for("https://www.presidio-residences.com/apartments/ca/san-francisco/wyman-avenue-neighborhood/floorplans", {}, "Presidio Residences") == "Presidio Residences – Wyman Avenue"


def test_rentcafe_site_units_list(fixture):
    from aptbot.sources import rentcafe_site as rs
    units = rs.parse_units_list(fixture("rentcafe_site_towers_floorplans.html"))
    assert len(units) == 12
    u = units[0]
    assert u["unit_number"] == "1221" and u["bedrooms"] == 1 and u["sqft"] == 539 and u["price"] == 5636.0 and u["available"] == "2026-09-21"
    assert u["apply_url"] == ("https://thetowersatrincon.securecafe.com/onlineleasing/towers-at-rincon0/oleapplication.aspx"
                              "?stepname=RentalOptions&myOlePropertyId=2371368&FloorPlanID=6417327&UnitID=48280833")
    assert sum(1 for x in units if x["bedrooms"] == 2) == 2


def test_nema_placeholder_rent():
    from aptbot.sources import nema
    html = ('<div class="availabilities-list__item" data-unit="1801" data-unit-raw="1801" data-unit-beds="2" data-unit-bath="2" '
            'data-rent="-1" data-sqft="1442" data-date="10/25/2026" data-availability="10/25/2026" data-floorplan="2A" data-apply="https://x?a=1&amp;b=2"></div>')
    rows = nema.parse_nema(html)
    assert rows[0]["price"] is None and rows[0]["available"] == "2026-10-25" and rows[0]["apply"] == "https://x?a=1&b=2"


def test_elise(fixture):
    from aptbot.models import norm_unit
    from aptbot.sources import elise
    rows = elise.parse_search_units(json.loads(fixture("elise_340fremont.json")))
    assert len(rows) == 5 and sum(1 for r in rows if r["bedrooms"] == 2) == 4
    r = [x for x in rows if x["unit_number"] == "407"][0]
    assert r["sub_building"] == "1" and r["sqft"] == 977 and r["floor"] == "4" and r["price"] == 6880.0 and r["price_max"] == 7200.0
    assert r["available"] == "2026-10-07" and r["term"] == 12 and r["market_rate"]
    assert norm_unit(r["unit_number"]) == norm_unit("407")  # same key as the equityapartments.com source
    lettered = elise.parse_search_units([{"id": "u1", "unit_number": "N-314", "number_of_bedrooms": 2}])[0]
    assert lettered["unit_number"] == "314" and lettered["sub_building"] == "N" and lettered["raw_unit"] == "N-314"
    assert elise.apply_link("eqr-applications.com", "340-fremont-2", r) == (
        "https://eqr-applications.com/building/340-fremont-2/units?unit_id=u1903084172&move_in_date=2026-10-07&lease_term=12")


def test_entrata(fixture):
    from aptbot.sources import entrata
    d = entrata.parse_entrata(fixture("entrata_hanover.html"))
    assert len(d["units"]) == 33
    u = d["units"][0]
    assert u["unit_number"] == "161" and u["bedrooms"] == 0 and u["sqft"] == 437 and u["floor"] == "1"
    assert u["available"] == "2026-10-22" and u["price"] == 3536.0 and u["floorplan"] == "S4"
    assert all(x["available"] and "/" not in x["available"] for x in d["units"])


def test_avalon_empty_and_broken():
    import pytest
    from aptbot.sources import avalon
    assert avalon.parse_avalon('{"communityId":"AVB-CA104","units":[]}') == []
    with pytest.raises(ValueError):
        avalon.parse_avalon("<html>maintenance page</html>")
