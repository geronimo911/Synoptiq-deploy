from __future__ import annotations
import json
from pathlib import Path
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.database import get_db
from app.models_db import SkillRow
from app.config import ACTIVE_METRICS_DIR, PILOT_ZONES, TARGET_RELATIVE_CSI_IMPROVEMENT
from app.schemas import VerificationResponse, ScorecardRow

router = APIRouter(prefix="/verification", tags=["verification"])


@router.get("/scorecard", response_model=VerificationResponse)
def scorecard(region: str, variable: str = "precipitation", db: Session = Depends(get_db)):
    """
    Two genuinely different things are shown here, and they are labeled
    separately on purpose:

    1. `rows` — the per-model/lead_hours diagnostic skill table (RMSE/MAE/
       Bias/CSI/POD/FAR/FSS-proxy), built ONLY from train+validation data,
       frozen before any test-period evaluation ran. This explains *why*
       the meta-model weights the way it does; it is not the pitch number.

    2. `synoptiq_csi` / `best_single_model_csi` / `relative_csi_improvement_pct`
       — the headline KPI, read from scripts/evaluate_blend.py's output,
       computed ENTIRELY on the held-out TEST split (both numbers, so the
       comparison is apples-to-apples). If that script hasn't been run yet,
       these come back as 0 / not-met rather than a fabricated number.
    """
    if region not in PILOT_ZONES:
        raise HTTPException(404, f"Unknown region '{region}'.")
    rows = db.query(SkillRow).filter(SkillRow.region == region, SkillRow.variable == variable).all()
    if not rows:
        raise HTTPException(404, "Skill table is empty for that region/variable. Run training first.")

    scorecard_rows = [
        ScorecardRow(model=r.model, region=r.region, lead_hours=r.lead_hours, rmse=round(r.rmse, 2),
                     mae=round(r.mae, 2), bias=round(r.bias, 2), csi_50mm=r.csi_50mm,
                     pod_50mm=r.pod_50mm, far_50mm=r.far_50mm, fss_50mm=r.fss_50mm)
        for r in rows
    ]

    eval_path = ACTIVE_METRICS_DIR / f"blend_eval_{region}_{variable}.json"
    if eval_path.exists():
        held_out = json.loads(eval_path.read_text())
        synoptiq_csi = held_out.get("synoptiq_csi", 0.0)
        best_single = held_out.get("best_single_model_csi", 0.0)
        rel_improve = held_out.get("relative_csi_improvement_pct", 0.0)
    else:
        # Never fabricate: no held-out run yet -> report zero / not-met, and
        # say so, rather than substituting a train+val diagnostic number.
        synoptiq_csi = best_single = rel_improve = 0.0

    return VerificationResponse(
        region=region, variable=variable, threshold_label="20mm/24h",
        rows=scorecard_rows, synoptiq_csi=round(synoptiq_csi, 4),
        best_single_model_csi=round(best_single, 4),
        relative_csi_improvement_pct=round(rel_improve, 2),
        target_relative_csi_improvement_pct=round(TARGET_RELATIVE_CSI_IMPROVEMENT * 100, 2),
        meets_target=rel_improve >= TARGET_RELATIVE_CSI_IMPROVEMENT * 100,
    )
