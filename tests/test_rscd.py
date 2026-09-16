from icepredict.common.rscd import parse

def test_parse_full():
    l = parse("2022012523413511-wet-asphalt-smooth.jpg")
    assert (l.friction, l.material, l.uneven, l.cls4) == ("wet", "asphalt", "smooth", "wet")

def test_parse_short_forms():
    assert parse("202202211735577-ice.jpg").cls4 == "black_ice"
    assert parse("2022021522211716-melted_snow.jpg").cls4 == "black_ice"
    assert parse("1-fresh_snow.jpg").cls4 == "wet"
    assert parse("1-dry-mud.jpg").cls4 == "normal"
    assert parse("1-wet-gravel.jpg").material == "gravel"

def test_pothole_priority():
    assert parse("1-wet-concrete-severe.jpg").cls4 == "pothole"
    assert parse("1-dry-asphalt-severe.jpg").cls4_idx == 3

def test_typo_rejected():
    assert parse("2022070913430178wet-dry-asphalt-smooth.jpg") is None
