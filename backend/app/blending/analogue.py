"""
CREATIVE ADDITION (beyond the literal PS ask, but listed as a "High priority"
differentiator in Section 11 of the blueprint: "Historical analogue memory").

For the current context (region, regime, season, lead_hours) this finds the
closest past cases in the synthetic archive and reports what actually
happened then plus how much the models disagreed — a lightweight, fully
explainable alternative/complement to a learned uncertainty model: instead of
only saying "trust is 59%", the dashboard can also say "in 4 similar past
situations, observed rainfall averaged 132mm and model spread was large."
"""
from __future__ import annotations
import pandas as pd
from sqlalchemy.orm import Session
from app.models_db import ForecastRow, GroundTruthRow


def find_analogues(db: Session, region: str, variable: str, regime: str, season: str,
                    lead_hours: int, top_n: int = 3, before=None) -> list[dict]:
    fdf = pd.read_sql(
        db.query(ForecastRow)
          .filter(ForecastRow.region == region, ForecastRow.variable == variable,
                   ForecastRow.regime == regime, ForecastRow.season == season)
          .statement,
        db.bind,
    )
    if fdf.empty:
        return []
    if before is not None:
        fdf = fdf[pd.to_datetime(fdf["valid_time"]) < pd.Timestamp(before)]
        if fdf.empty:
            return []
    gdf = pd.read_sql(
        db.query(GroundTruthRow)
          .filter(GroundTruthRow.region == region, GroundTruthRow.variable == variable)
          .statement,
        db.bind,
    )
    merged = fdf.merge(gdf[["region", "valid_time", "variable", "observed_value"]],
                        on=["region", "valid_time", "variable"], how="inner")
    if merged.empty:
        return []
    merged["lead_diff"] = (merged["lead_hours"] - lead_hours).abs()

    agg = (merged.groupby("valid_time")
                  .agg(observed_value=("observed_value", "first"),
                       spread=("forecast_value", "std"),
                       mean_lead_diff=("lead_diff", "mean"))
                  .reset_index()
                  .sort_values("mean_lead_diff")
                  .head(top_n))

    out = []
    for _, row in agg.iterrows():
        out.append({
            "valid_time": row["valid_time"].isoformat() if hasattr(row["valid_time"], "isoformat") else str(row["valid_time"]),
            "observed_value": round(float(row["observed_value"]), 1),
            "model_spread": round(float(row["spread"]) if pd.notna(row["spread"]) else 0.0, 1),
        })
    return out
