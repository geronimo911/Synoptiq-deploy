from pathlib import Path


def test_live_refresh_has_no_fixture_or_synthetic_imports():
    source = Path(__file__).resolve().parents[1] / "realdata" / "live_refresh.py"
    text = source.read_text(encoding="utf-8").lower()
    assert "synthetic_generator" not in text
    assert "mockblend" not in text
    assert "fixtureapi" not in text
