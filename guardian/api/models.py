"""
Pydantic response models for the FastAPI layer.
"""
from __future__ import annotations

from typing import Any

from pydantic import BaseModel


class HealthResponse(BaseModel):
    status: str
    service: str
    version: str = "1.0.0"
    uptime_seconds: float
    emergency_stop: bool
    dry_run: bool


class SystemSnapshot(BaseModel):
    cpu_pct: float | None
    ram_pct: float | None
    load_avg_1m: float | None
    disk_usage: dict[str, Any]
    top_processes: list[dict]


class ContainerSummary(BaseModel):
    name: str
    status: str
    health: str
    restart_count: int
    cpu_pct: float
    mem_mb: float


class DockerSnapshot(BaseModel):
    containers: list[ContainerSummary]
    disk_usage: dict[str, Any]


class StatusResponse(BaseModel):
    hostname: str
    domain: str
    system: SystemSnapshot | None
    docker: DockerSnapshot | None
    unresolved_events: int
    pending_approvals: int
    emergency_stop: bool
    dry_run: bool


class EventRecord(BaseModel):
    id: int
    timestamp: float
    severity: str
    category: str
    title: str
    description: str
    resolved: bool


class DecisionRecord(BaseModel):
    id: int
    timestamp: float
    summary: str
    confidence: float
    model: str


class ActionRecord(BaseModel):
    id: int
    timestamp: float
    action_type: str
    target: str | None
    risk_level: str
    status: str
    dry_run: bool
    result: dict | None


class ApproveRequest(BaseModel):
    approved_by: str = "api"


class TriggerScanResponse(BaseModel):
    status: str
    message: str


class EmergencyStopRequest(BaseModel):
    stop: bool
    reason: str = ""


class ConfigView(BaseModel):
    service_name: str
    domain: str
    dry_run: bool
    emergency_stop: bool
    ai_enabled: bool
    ai_model: str
    monitoring_intervals: dict[str, int]
    managed_containers: list[str]
    native_services: list[str]
