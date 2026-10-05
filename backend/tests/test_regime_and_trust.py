from app.regime_detector import detect_regime, top_regime
from app.blending.trust import compute_trust

def test_regime_probs_sum_to_one():
    probs = detect_regime({"precipitation": 90, "temperature": 27, "wind_speed": 50}, "sw_monsoon", 10.0)
    assert abs(sum(probs.values()) - 1.0) < 1e-3
    assert top_regime(probs) in probs

def test_low_agreement_and_missing_sources_lowers_trust():
    weights_full = {"GFS": 0.33, "IFS": 0.34, "AIFS": 0.33}
    hist_skill = {"GFS": 0.6, "IFS": 0.6, "AIFS": 0.6}
    trust_high_agree = compute_trust(
        "precipitation", weights_full, hist_skill, disagreement=2.0, lead_hours=24,
        regime_probs={"normal": 0.95, "active_monsoon": 0.05}, n_sources_available=3, n_sources_expected=3,
    )
    trust_low = compute_trust(
        "precipitation", {"GFS": 1.0}, {"GFS": 0.5}, disagreement=90.0, lead_hours=120,
        regime_probs={"normal": 0.4, "active_monsoon": 0.35, "depression": 0.25},
        n_sources_available=1, n_sources_expected=3,
    )
    assert trust_high_agree["trust_score"] > trust_low["trust_score"]
    assert trust_low["abstain"] in (True, False)  # just verifying it doesn't crash / is a bool
