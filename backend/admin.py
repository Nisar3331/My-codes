import os
from fastapi import APIRouter, HTTPException, Depends
from pydantic import BaseModel, Field
from typing import List, Optional

from backend.persistence import (
    get_polling_config,
    create_polling_config,
    update_polling_config,
    get_all_polling_configs,
    create_notification_config,
    get_notification_config,
    update_notification_config,
)
from backend.models.schemas import Platform

router = APIRouter(prefix="/admin", tags=["admin"])

# Admin API Key for simple auth
ADMIN_API_KEY = os.environ.get("ADMIN_API_KEY", "change-me-in-production")


def verify_admin_key(api_key: str = None) -> bool:
    """Simple API key verification."""
    return api_key == ADMIN_API_KEY


class PollingConfigRequest(BaseModel):
    """Request to create/update polling config."""

    poll_interval_seconds: int = Field(default=300, ge=60, le=3600)
    auto_execute_low_risk: bool = Field(default=False)
    log_filter: Optional[str] = None


class NotificationConfigRequest(BaseModel):
    """Request to create/update notification config."""

    recipients: List[str] = Field(..., min_items=1)


# Polling endpoints


@router.get("/polling", dependencies=[Depends(verify_admin_key)])
async def list_polling_configs(api_key: str = None):
    """List all polling configurations."""
    if not verify_admin_key(api_key):
        raise HTTPException(status_code=401, detail="Invalid API key")

    configs = get_all_polling_configs()
    return {"polling_configs": configs}


@router.get("/polling/{platform}/{instance_name}", dependencies=[Depends(verify_admin_key)])
async def get_polling_config_endpoint(
    platform: Platform, instance_name: str, api_key: str = None
):
    """Get polling config for specific instance."""
    if not verify_admin_key(api_key):
        raise HTTPException(status_code=401, detail="Invalid API key")

    config = get_polling_config(platform, instance_name)
    if not config:
        raise HTTPException(status_code=404, detail="Polling config not found")

    return config


@router.post("/polling/{platform}/{instance_name}", dependencies=[Depends(verify_admin_key)])
async def create_or_update_polling(
    platform: Platform,
    instance_name: str,
    request: PollingConfigRequest,
    api_key: str = None,
):
    """Create or update polling config for instance."""
    if not verify_admin_key(api_key):
        raise HTTPException(status_code=401, detail="Invalid API key")

    # Check if already exists
    existing = get_polling_config(platform, instance_name)

    if existing:
        # Update
        update_polling_config(
            platform=platform,
            instance_name=instance_name,
            poll_interval_seconds=request.poll_interval_seconds,
            auto_execute_low_risk=request.auto_execute_low_risk,
            log_filter=request.log_filter,
        )
        return {
            "status": "updated",
            "platform": platform,
            "instance_name": instance_name,
        }
    else:
        # Create
        config_id = create_polling_config(
            platform=platform,
            instance_name=instance_name,
            poll_interval_seconds=request.poll_interval_seconds,
            auto_execute_low_risk=request.auto_execute_low_risk,
            log_filter=request.log_filter,
        )
        return {
            "status": "created",
            "id": config_id,
            "platform": platform,
            "instance_name": instance_name,
        }


@router.patch("/polling/{platform}/{instance_name}", dependencies=[Depends(verify_admin_key)])
async def update_polling_config_endpoint(
    platform: Platform,
    instance_name: str,
    request: PollingConfigRequest,
    api_key: str = None,
):
    """Update polling config for instance."""
    if not verify_admin_key(api_key):
        raise HTTPException(status_code=401, detail="Invalid API key")

    config = get_polling_config(platform, instance_name)
    if not config:
        raise HTTPException(status_code=404, detail="Polling config not found")

    update_polling_config(
        platform=platform,
        instance_name=instance_name,
        poll_interval_seconds=request.poll_interval_seconds,
        auto_execute_low_risk=request.auto_execute_low_risk,
        log_filter=request.log_filter,
    )

    return {
        "status": "updated",
        "platform": platform,
        "instance_name": instance_name,
    }


# Notification endpoints


@router.get("/notifications/{platform}/{instance_name}", dependencies=[Depends(verify_admin_key)])
async def get_notification_config_endpoint(
    platform: Platform, instance_name: str, api_key: str = None
):
    """Get notification config for specific instance."""
    if not verify_admin_key(api_key):
        raise HTTPException(status_code=401, detail="Invalid API key")

    config = get_notification_config(platform, instance_name)
    if not config:
        raise HTTPException(status_code=404, detail="Notification config not found")

    return config


@router.post("/notifications/{platform}/{instance_name}", dependencies=[Depends(verify_admin_key)])
async def create_or_update_notification(
    platform: Platform,
    instance_name: str,
    request: NotificationConfigRequest,
    api_key: str = None,
):
    """Create or update notification config for instance."""
    if not verify_admin_key(api_key):
        raise HTTPException(status_code=401, detail="Invalid API key")

    existing = get_notification_config(platform, instance_name)

    if existing:
        # Update
        update_notification_config(
            platform=platform,
            instance_name=instance_name,
            recipients=request.recipients,
        )
        return {
            "status": "updated",
            "platform": platform,
            "instance_name": instance_name,
        }
    else:
        # Create
        config_id = create_notification_config(
            platform=platform,
            instance_name=instance_name,
            recipients=request.recipients,
        )
        return {
            "status": "created",
            "id": config_id,
            "platform": platform,
            "instance_name": instance_name,
        }


@router.patch("/notifications/{platform}/{instance_name}", dependencies=[Depends(verify_admin_key)])
async def update_notification_config_endpoint(
    platform: Platform,
    instance_name: str,
    request: NotificationConfigRequest,
    api_key: str = None,
):
    """Update notification config for instance."""
    if not verify_admin_key(api_key):
        raise HTTPException(status_code=401, detail="Invalid API key")

    config = get_notification_config(platform, instance_name)
    if not config:
        raise HTTPException(status_code=404, detail="Notification config not found")

    update_notification_config(
        platform=platform,
        instance_name=instance_name,
        recipients=request.recipients,
    )

    return {
        "status": "updated",
        "platform": platform,
        "instance_name": instance_name,
    }
