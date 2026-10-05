from datetime import datetime, timedelta
from app.data_sim.synthetic_generator import model_forecast_value, ground_truth_value

def test_time_alignment():
    run_time = datetime(2026, 1, 1, 0, 0)
    lead_hours = 72
    valid_time = run_time + timedelta(hours=lead_hours)
    assert (valid_time - run_time).total_seconds() / 3600 == lead_hours

def test_forecast_values_are_non_negative_for_rain_and_wind():
    truth = ground_truth_value("precipitation", "active_monsoon")
    for model in ("GFS", "IFS", "AIFS"):
        val = model_forecast_value(model, truth, "precipitation", "active_monsoon", 72)
        assert val >= 0
        val = model_forecast_value(model, 20.0, "wind_speed", "normal", 48)
        assert val >= 0
