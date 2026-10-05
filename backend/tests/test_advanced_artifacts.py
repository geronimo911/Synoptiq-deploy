"""Tests for the advanced operational layers.

Covers, with hand-computed expectations wherever the maths is small enough:
  * peak preservation — fusion endpoints, alpha context modulation, the GPD
    method-of-moments fit and the exceedance probability;
  * impact engine — ISI arithmetic, IMD colour bands, CAP 1.2 structure and
    idempotent identifiers;
  * regime router — catalogue coverage, policy-shift materiality;
  * bulletin export — end-to-end products on the real artifact, CSV shape,
    ops log append;
  * API surface — the new endpoints serve the artifacts and 404 honestly.
"""
from __future__ import annotations

import json
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(PROJECT_ROOT / "backend"))
sys.path.insert(0, str(PROJECT_ROOT / "backend" / "scripts"))

from app.blending import peaks  # noqa: E402
from app import impact as impact_mod  # noqa: E402
from app import regimes as regimes_mod  # noqa: E402


# ---------------------------------------------------------------------------
# Peak preservation
# ---------------------------------------------------------------------------

def test_peak_fusion_endpoints_and_clamp():
    assert peaks.peak_preserving_value(30.0, 45.0, 0.0) == 30.0
    assert peaks.peak_preserving_value(30.0, 45.0, 1.0) == 45.0
    assert peaks.peak_preserving_value(30.0, 45.0, 0.5) == pytest.approx(37.5)
    # alpha is clamped to [0, 1]
    assert peaks.peak_preserving_value(30.0, 45.0, 1.7) == 45.0
    assert peaks.peak_preserving_value(30.0, 45.0, -2.0) == 30.0


def test_alpha_context_bounds():
    # no context -> exactly the calibrated alpha
    assert peaks.alpha_for_context(0.3) == pytest.approx(0.3)
    # both signals maxed -> +0.25 cap, never beyond 1.0
    a = peaks.alpha_for_context(0.0, bust_probability=1.0, disagreement=99.0)
    assert a == pytest.approx(0.25)
    assert peaks.alpha_for_context(0.9, bust_probability=1.0, disagreement=99.0) == pytest.approx(1.0)
    # negative inputs cannot reduce alpha below the calibrated value
    assert peaks.alpha_for_context(0.4, bust_probability=-1.0, disagreement=-5.0) == pytest.approx(0.4)


def test_gpd_fit_matches_method_of_moments():
    # for an exponential sample (mean m, var m^2) MoM gives xi -> 0, sigma -> m
    xs = [1.0, 2.0, 3.0, 4.0, 5.0, 6.0, 7.0, 8.0, 9.0, 10.0]
    fit = peaks.fit_gpd(xs)
    mean = sum(xs) / len(xs)
    var = sum((x - mean) ** 2 for x in xs) / len(xs)
    xi = 0.5 * (1 - mean * mean / var)
    sigma = 0.5 * mean * (mean * mean / var + 1)
    assert fit["xi"] == pytest.approx(round(xi, 4))
    assert fit["sigma"] == pytest.approx(round(sigma, 4))
    assert fit["n"] == 10
    # too-small samples are refused, not guessed
    small = peaks.fit_gpd([1.0, 2.0])
    assert small["xi"] is None and "fewer than 10" in small["note"]


def test_gpd_exceedance_probability():
    # xi = 0 -> exponential survival function
    assert peaks.gpd_exceedance_probability(5.0, 0.0, 0.0, 5.0) == pytest.approx(0.367879441, abs=1e-6)
    # y <= 0 is the whole conditional sample
    assert peaks.gpd_exceedance_probability(0.0, 0.0, 0.1, 2.0) == 1.0
    # xi < 0 has a finite upper endpoint; beyond it the answer is honestly None
    assert peaks.gpd_exceedance_probability(100.0, 0.0, -0.5, 10.0) is None
    # xi > 0 heavy tail decays slower than exponential
    heavy = peaks.gpd_exceedance_probability(20.0, 0.0, 0.2, 5.0)
    expo = peaks.gpd_exceedance_probability(20.0, 0.0, 0.0, 5.0)
    assert heavy is not None and expo is not None and heavy > expo


def test_conformal_band_shape():
    band = peaks.conformal_band(30.0, 4.0)
    assert band["low"] == 26.0 and band["high"] == 34.0
    assert band["half_width"] == 4.0 and band["level"] == 0.9


# ---------------------------------------------------------------------------
# Impact engine
# ---------------------------------------------------------------------------

def test_isi_arithmetic_and_colour():
    # rain 64.5 mm at threshold, no wind/heat, vulnerability 1.0:
    # contribution = 0.55 * 1.0 * 1.0 = 0.55 -> ISI = 5.5 -> ORANGE
    res = impact_mod.compute_isi("kerala_western_ghats",
                                 {"precipitation": 64.5},
                                 vulnerability_override=1.0)
    assert res["isi"] == pytest.approx(5.5)
    assert res["colour"] == "ORANGE"
    assert res["components"]["precipitation"]["ratio"] == 1.0
    # missing variables contribute nothing but are visibly absent
    assert set(res["components"]) == {"precipitation"}
    # extreme everything saturates at 10
    sat = impact_mod.compute_isi("kerala_western_ghats",
                                 {"precipitation": 300, "wind_speed": 120, "temperature": 50},
                                 vulnerability_override=1.0)
    assert sat["isi"] == 10.0 and sat["colour"] == "RED"


def test_colour_bands_boundaries():
    assert impact_mod.colour_for_isi(7.0)[0] == "RED"
    assert impact_mod.colour_for_isi(6.99)[0] == "ORANGE"
    assert impact_mod.colour_for_isi(4.0)[0] == "ORANGE"
    assert impact_mod.colour_for_isi(2.0)[0] == "YELLOW"
    assert impact_mod.colour_for_isi(0.0)[0] == "GREEN"


def test_vulnerability_factors_documented():
    for zone, entry in impact_mod.VULNERABILITY.items():
        assert 0.1 <= entry["factor"] <= 1.0
        assert len(entry["rationale"]) > 20  # a real, checkable rationale


def test_cap_xml_is_valid_and_idempotent():
    from datetime import datetime, timezone
    res = impact_mod.compute_isi("bay_of_bengal_east_coast",
                                 {"precipitation": 80.0, "wind_speed": 55.0},
                                 vulnerability_override=0.85)
    vfrom = datetime(2025, 5, 30, tzinfo=timezone.utc)
    xml = impact_mod.cap_xml(zone="bay_of_bengal_east_coast", isi_result=res,
                             valid_from=vfrom,
                             valid_to=vfrom.replace(hour=23))
    root = ET.fromstring(xml)
    ns = {"c": "urn:oasis:names:tc:emergency:cap:1.2"}
    assert root.tag.endswith("alert")
    assert root.find("c:identifier", ns).text == "synoptiq-bay_of_bengal_east_coast-20250530"
    assert root.find("c:info/c:eventCode/c:value", ns).text == res["colour"]
    assert root.find("c:info/c:category", ns).text == "Met"
    assert root.find("c:info/c:severity", ns).text in {"Extreme", "Severe", "Moderate", "Minor"}
    assert root.find("c:info/c:headline", ns).text.startswith(res["colour"])
    # same inputs -> same identifier (idempotent re-export)
    xml2 = impact_mod.cap_xml(zone="bay_of_bengal_east_coast", isi_result=res,
                              valid_from=vfrom, valid_to=vfrom.replace(hour=23))
    assert ET.fromstring(xml2).find("c:identifier", ns).text == \
        ET.fromstring(xml).find("c:identifier", ns).text


# ---------------------------------------------------------------------------
# Regime router
# ---------------------------------------------------------------------------

def test_regime_catalogue_covers_config_regimes():
    from app.config import REGIMES
    assert set(REGIMES) <= set(regimes_mod.REGIME_CATALOGUE)
    for regime, entry in regimes_mod.REGIME_CATALOGUE.items():
        assert entry["label"] and entry["synoptic"]


def test_policy_shift_material_and_flat():
    flat = regimes_mod.policy_shift("normal", {"GFS": 0.34, "IFS": 0.33, "AIFS": 0.33},
                                    {"GFS": 0.34, "IFS": 0.33, "AIFS": 0.33})
    assert flat["material"] is False
    assert "no material policy shift" in flat["statement"]
    shifted = regimes_mod.policy_shift("depression", {"GFS": 0.10, "IFS": 0.60, "AIFS": 0.30},
                                       {"GFS": 0.34, "IFS": 0.33, "AIFS": 0.33})
    assert shifted["material"] is True
    assert "IFS" in shifted["statement"] and "up" in shifted["statement"]


def test_dominant_regime():
    assert regimes_mod.dominant_regime({"normal": 0.2, "depression": 0.6}) == "depression"
    assert regimes_mod.dominant_regime({}) == "normal"


# ---------------------------------------------------------------------------
# Bulletin export (integration on the real artifact)
# ---------------------------------------------------------------------------

ARTIFACTS = PROJECT_ROOT / "artifacts" / "real_12m_aifs_corrected"


@pytest.mark.skipif(not (ARTIFACTS / "research" / "impact_engine.json").is_file(),
                    reason="operational artifacts not generated in this checkout")
def test_bulletin_export_products():
    import export_bulletin

    bulletin = export_bulletin.build_bulletin(None)
    assert bulletin["zones"], "bulletin must contain at least one zone"
    entry = export_bulletin.write_outputs(bulletin)
    day = bulletin["valid_date"]
    outdir = ARTIFACTS / "bulletins" / day
    for name in ("bulletin.json", "bulletin.csv", "bulletin.txt"):
        assert (outdir / name).is_file()
    caps = list((outdir / "cap").glob("*.xml"))
    assert len(caps) == len(bulletin["zones"])
    for cap in caps:
        ET.parse(cap)  # every CAP file must be valid XML
    # CSV has one header + one row per zone
    rows = (outdir / "bulletin.csv").read_text(encoding="utf-8").strip().splitlines()
    assert len(rows) == len(bulletin["zones"]) + 1
    # ops log gained exactly the run we just did
    log = (PROJECT_ROOT / "ops" / "run_log.jsonl").read_text(encoding="utf-8").strip().splitlines()
    assert json.loads(log[-1])["valid_date"] == entry["valid_date"]


@pytest.mark.skipif(not (ARTIFACTS / "research" / "impact_engine.json").is_file(),
                    reason="operational artifacts not generated in this checkout")
def test_bulletin_for_specific_event_day():
    import export_bulletin
    engine = json.loads((ARTIFACTS / "research" / "impact_engine.json").read_text())
    day = engine["strongest_archived_event"]["date"]
    bulletin = export_bulletin.build_bulletin(day)
    assert bulletin["valid_date"] == day
    assert any(z["colour"] != "GREEN" for z in bulletin["zones"]), \
        "the strongest archived event day should trip at least one non-green zone"


# ---------------------------------------------------------------------------
# API surface
# ---------------------------------------------------------------------------

def test_advanced_endpoints_serve_artifacts(monkeypatch):
    from fastapi import HTTPException
    from app import research as research_mod

    if not (ARTIFACTS / "research" / "trust_atlas.json").is_file():
        pytest.skip("advanced artifacts not generated")
    research_mod._load_named.cache_clear()
    atlas = research_mod.trust_atlas()
    assert atlas["cells"] and atlas["region_lead_matrix"]
    router_payload = research_mod.regime_router()
    assert router_payload["profiles"] and router_payload["current"]["regime"]
    pp = research_mod.peak_preservation()
    assert set(pp["variables"]) == {"precipitation", "temperature", "wind_speed"}
    impact = research_mod.impact_for_date(None)
    assert impact["zones"] and impact["date"]
    # CAP XML for a real zone
    xml = research_mod.cap_alert(impact["zones"][0]["zone"], None)
    ET.fromstring(xml)
    # ops status reflects generated bulletins
    status = research_mod.ops_status()
    assert "artifacts_present" in status and "commands" in status


def test_missing_advanced_artifact_404s(monkeypatch, tmp_path):
    from fastapi import HTTPException
    from app import research as research_mod

    monkeypatch.setattr(research_mod, "RESEARCH_DIR", tmp_path)
    research_mod._load_named.cache_clear()
    with pytest.raises(HTTPException) as exc:
        research_mod.trust_atlas()
    assert exc.value.status_code == 404
    assert "build_advanced_artifacts.py" in exc.value.detail
    research_mod._load_named.cache_clear()
