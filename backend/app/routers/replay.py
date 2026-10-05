from __future__ import annotations
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.database import get_db
from app.models_db import ReplayEvent
from app.schemas import ReplayEventSummary

router = APIRouter(prefix="/replay", tags=["replay (offline demo mode)"])


@router.get("/events", response_model=list[ReplayEventSummary])
def list_events(region: str | None = None, db: Session = Depends(get_db)):
    q = db.query(ReplayEvent)
    if region:
        q = q.filter(ReplayEvent.region == region)
    events = q.order_by(ReplayEvent.severity.desc()).all()
    return [
        ReplayEventSummary(event_id=e.event_id, region=e.region, label=e.label,
                            valid_time=e.valid_time, variable=e.variable, severity=e.severity)
        for e in events
    ]


@router.get("/events/{event_id}")
def get_event(event_id: str, db: Session = Depends(get_db)):
    """Fully precomputed payload — zero live model inference, demo-day safe
    (Section 18 guardrail: 'Live API fragility -> Pre-cache representative
    cases and provide Historical Replay Mode')."""
    e = db.get(ReplayEvent, event_id)
    if not e:
        raise HTTPException(404, f"No replay event '{event_id}'. See /replay/events.")
    return e.payload
