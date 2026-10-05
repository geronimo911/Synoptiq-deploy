from fastapi import APIRouter
from app.config import PILOT_ZONES, MODELS, VARIABLES, LEAD_HOURS, THRESHOLDS

router = APIRouter(prefix="/regions", tags=["regions"])


@router.get("")
def list_regions():
    return {
        "regions": [
            {"key": k, **v} for k, v in PILOT_ZONES.items()
        ],
        "models": MODELS,
        "variables": VARIABLES,
        "lead_hours_options": LEAD_HOURS,
        "thresholds": THRESHOLDS,
    }
