import json
from fastapi import APIRouter, HTTPException, Query
from typing import Optional

from backend.persistence import (
    get_remediation_metrics,
    get_remediation_history,
)
from backend.models.schemas import Platform

router = APIRouter(prefix="/reporting", tags=["reporting"])


@router.get("/metrics")
async def get_metrics(
    period: str = Query("24h", regex="^(24h|7d|30d)$"),
    platform: Optional[Platform] = None,
    instance_name: Optional[str] = None,
):
    """
    Get remediation metrics for the specified period.

    Returns:
        {
            "total_attempts": int,
            "success_count": int,
            "failure_count": int,
            "success_rate": float (0-100)
        }
    """
    # Convert period to hours
    period_map = {"24h": 24, "7d": 168, "30d": 720}
    hours = period_map.get(period, 24)

    metrics = get_remediation_metrics(
        period_hours=hours, platform=platform, instance_name=instance_name
    )

    return {
        "period": period,
        "platform": platform,
        "instance_name": instance_name,
        **metrics,
    }


@router.get("/history")
async def get_history(
    platform: Optional[Platform] = None,
    instance_name: Optional[str] = None,
    limit: int = Query(100, ge=1, le=1000),
    offset: int = Query(0, ge=0),
):
    """
    Get audit log of remediations.

    Returns:
        List of remediation records with details
    """
    remediation_list = get_remediation_history(
        platform=platform,
        instance_name=instance_name,
        limit=limit,
        offset=offset,
    )

    # Parse JSON fields for readability
    parsed_list = []
    for r in remediation_list:
        parsed_r = dict(r)
        # Parse JSON fields
        for field in ["classification", "plan", "safety_eval", "execution_result", "email_recipients"]:
            if parsed_r.get(field):
                try:
                    parsed_r[field] = json.loads(parsed_r[field])
                except (json.JSONDecodeError, TypeError):
                    pass
        parsed_list.append(parsed_r)

    return {
        "limit": limit,
        "offset": offset,
        "count": len(parsed_list),
        "remediations": parsed_list,
    }


@router.get("/summary")
async def get_summary(
    platform: Optional[Platform] = None,
    instance_name: Optional[str] = None,
):
    """
    Get summary statistics across all time periods.

    Returns:
        Metrics for 24h, 7d, and 30d periods
    """
    metrics_24h = get_remediation_metrics(24, platform, instance_name)
    metrics_7d = get_remediation_metrics(168, platform, instance_name)
    metrics_30d = get_remediation_metrics(720, platform, instance_name)

    return {
        "platform": platform,
        "instance_name": instance_name,
        "metrics_24h": metrics_24h,
        "metrics_7d": metrics_7d,
        "metrics_30d": metrics_30d,
    }
