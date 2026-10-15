"""Health check schemas."""

from datetime import datetime
from pydantic import BaseModel, Field


class HealthResponse(BaseModel):
    """Liveness and health check response schema."""

    status: str = Field(default="healthy", description="Current service health status")
    service: str = Field(default="trace-backend", description="Service identifier")
    version: str = Field(description="Service semantic version")
    timestamp: datetime = Field(description="Current server timestamp with timezone")
    environment: str = Field(description="Active runtime environment")
