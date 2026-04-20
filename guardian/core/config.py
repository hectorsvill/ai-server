"""
Configuration loader — reads config.yaml, overlays environment variables.
All other modules import `cfg` from here.
"""
from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, Field


# ── Sub-models ────────────────────────────────────────────────────────────────

class ServiceCfg(BaseModel):
    name: str = "ai-guardian"
    host: str = "127.0.0.1"
    port: int = 9900
    log_level: str = "INFO"
    data_dir: str = "/home/hectorsvillai/Desktop/ai-server/guardian/data"
    log_dir: str = "/home/hectorsvillai/Desktop/ai-server/guardian/logs"


class ServerCfg(BaseModel):
    hostname: str = "vailab"
    domain: str = "vailab.us"
    tailscale_ip: str = "100.118.0.92"
    project_dir: str = "/home/hectorsvillai/Desktop/ai-server"
    docker_compose_file: str = "/home/hectorsvillai/Desktop/ai-server/docker-compose.yml"
    caddyfile: str = "/home/hectorsvillai/Desktop/ai-server/Caddyfile"


class MonitoringCfg(BaseModel):
    system_interval_seconds: int = 60
    docker_interval_seconds: int = 60
    security_interval_seconds: int = 30
    reasoning_interval_seconds: int = 300
    metrics_retention_days: int = 30


class ThresholdsCfg(BaseModel):
    cpu_warning_pct: float = 80.0
    cpu_critical_pct: float = 95.0
    ram_warning_pct: float = 80.0
    ram_critical_pct: float = 92.0
    disk_warning_pct: float = 75.0
    disk_critical_pct: float = 90.0
    load_avg_warning: float = 4.0
    load_avg_critical: float = 8.0
    container_restart_warning: int = 3
    container_restart_critical: int = 10
    ssh_failure_warning: int = 5
    ssh_failure_critical: int = 20
    # AMD GPU (RX 7900 GRE)
    gpu_use_warning_pct: float = 90.0
    gpu_use_critical_pct: float = 99.0
    gpu_vram_warning_pct: float = 80.0
    gpu_vram_critical_pct: float = 95.0
    gpu_temp_warning_c: float = 85.0
    gpu_temp_critical_c: float = 100.0


class DockerServiceCfg(BaseModel):
    name: str
    critical: bool = False
    health_check_url: str | None = None
    restart_policy: str = "always"


class AutoRestartCfg(BaseModel):
    enabled: bool = True
    max_restarts_per_hour: int = 3
    backoff_seconds: list[int] = Field(default_factory=lambda: [30, 60, 120])


class DockerCfg(BaseModel):
    compose_project: str = "ai-server"
    socket: str = "unix://var/run/docker.sock"
    services: list[DockerServiceCfg] = Field(default_factory=list)
    auto_restart: AutoRestartCfg = Field(default_factory=AutoRestartCfg)
    prune_images_older_than_days: int = 14
    prune_stopped_containers: bool = True


class NativeServiceCfg(BaseModel):
    name: str
    systemd_unit: str
    health_check_url: str | None = None
    critical: bool = False


class ConfidenceThresholdsCfg(BaseModel):
    auto_execute_low_risk: float = 0.90
    auto_execute_medium_risk: float = 0.97
    require_approval_above: float = 0.0


class RiskPolicyCfg(BaseModel):
    low: str = "auto"
    medium: str = "auto_with_log"
    high: str = "require_approval"
    critical: str = "require_approval"


class AICfg(BaseModel):
    enabled: bool = True
    ollama_url: str = "http://127.0.0.1:11434"
    model: str = "llama3.2:3b"
    reasoning_model: str = "llama3.1:8b"
    timeout_seconds: int = 120
    max_tokens: int = 2048
    confidence_thresholds: ConfidenceThresholdsCfg = Field(
        default_factory=ConfidenceThresholdsCfg
    )
    risk_policy: RiskPolicyCfg = Field(default_factory=RiskPolicyCfg)


class AutoBanSSHCfg(BaseModel):
    enabled: bool = True
    threshold: int = 20
    ban_duration_minutes: int = 60
    whitelist_ips: list[str] = Field(
        default_factory=lambda: ["127.0.0.1", "100.118.0.92", "100.64.0.0/10"]
    )


class SecurityCfg(BaseModel):
    auth_log: str = "/var/log/auth.log"
    syslog: str = "/var/log/syslog"
    ufw_log: str = "/var/log/ufw.log"
    auto_ban_ssh: AutoBanSSHCfg = Field(default_factory=AutoBanSSHCfg)
    suspicious_process_patterns: list[str] = Field(
        default_factory=lambda: ["xmrig", "minerd", "cpuminer", "ethminer",
                                  "nbminer", "t-rex", "teamredminer"]
    )
    private_ports: list[int] = Field(
        default_factory=lambda: [3234, 4389, 5679, 11434, 11457, 40404, 9900]
    )


class DiskCleanupCfg(BaseModel):
    enabled: bool = True
    trigger_at_pct: float = 80.0
    clean_docker_on_trigger: bool = True
    clean_journal_days: int = 7


class SecurityUpdatesCfg(BaseModel):
    enabled: bool = True
    schedule_cron: str = "0 3 * * 0"
    docker_image_updates: bool = False
    require_approval: bool = True


class LogRotationCfg(BaseModel):
    max_size_mb: int = 100
    backup_count: int = 5


class MaintenanceCfg(BaseModel):
    disk_cleanup: DiskCleanupCfg = Field(default_factory=DiskCleanupCfg)
    security_updates: SecurityUpdatesCfg = Field(default_factory=SecurityUpdatesCfg)
    log_rotation: LogRotationCfg = Field(default_factory=LogRotationCfg)


class TelegramCfg(BaseModel):
    enabled: bool = False
    bot_token: str = ""
    chat_id: str = ""


class DiscordCfg(BaseModel):
    enabled: bool = False
    webhook_url: str = ""


class SlackCfg(BaseModel):
    enabled: bool = False
    webhook_url: str = ""


class NotificationsCfg(BaseModel):
    enabled: bool = False
    telegram: TelegramCfg = Field(default_factory=TelegramCfg)
    discord: DiscordCfg = Field(default_factory=DiscordCfg)
    slack: SlackCfg = Field(default_factory=SlackCfg)
    notify_on: list[str] = Field(
        default_factory=lambda: [
            "critical_event", "security_threat", "container_down",
            "action_requires_approval", "disk_critical", "service_recovered",
        ]
    )


class SafetyCfg(BaseModel):
    dry_run: bool = False
    emergency_stop: bool = False
    max_actions_per_hour: int = 20
    max_container_restarts_per_hour: int = 5
    prohibited_actions: list[str] = Field(
        default_factory=lambda: ["delete_volume", "drop_database", "remove_tailscale"]
    )


# ── Root config ───────────────────────────────────────────────────────────────

class GuardianConfig(BaseModel):
    service: ServiceCfg = Field(default_factory=ServiceCfg)
    server: ServerCfg = Field(default_factory=ServerCfg)
    monitoring: MonitoringCfg = Field(default_factory=MonitoringCfg)
    thresholds: ThresholdsCfg = Field(default_factory=ThresholdsCfg)
    docker: DockerCfg = Field(default_factory=DockerCfg)
    native_services: list[NativeServiceCfg] = Field(default_factory=list)
    ai: AICfg = Field(default_factory=AICfg)
    security: SecurityCfg = Field(default_factory=SecurityCfg)
    maintenance: MaintenanceCfg = Field(default_factory=MaintenanceCfg)
    notifications: NotificationsCfg = Field(default_factory=NotificationsCfg)
    safety: SafetyCfg = Field(default_factory=SafetyCfg)


def _overlay_env(data: dict[str, Any]) -> None:
    """
    Overlay select environment variables onto the parsed YAML dict.
    Env vars take precedence over config.yaml values.
    """
    env_map = {
        "GUARDIAN_TELEGRAM_TOKEN": ("notifications", "telegram", "bot_token"),
        "GUARDIAN_TELEGRAM_CHAT_ID": ("notifications", "telegram", "chat_id"),
        "GUARDIAN_DISCORD_WEBHOOK": ("notifications", "discord", "webhook_url"),
        "GUARDIAN_SLACK_WEBHOOK": ("notifications", "slack", "webhook_url"),
        "GUARDIAN_DRY_RUN": ("safety", "dry_run"),
        "GUARDIAN_EMERGENCY_STOP": ("safety", "emergency_stop"),
        "GUARDIAN_OLLAMA_URL": ("ai", "ollama_url"),
        "GUARDIAN_AI_MODEL": ("ai", "model"),
        "GUARDIAN_PORT": ("service", "port"),
    }
    for env_key, path in env_map.items():
        val = os.environ.get(env_key)
        if val is None:
            continue
        node = data
        for part in path[:-1]:
            node = node.setdefault(part, {})
        # Coerce booleans
        if val.lower() in ("true", "1", "yes"):
            val = True
        elif val.lower() in ("false", "0", "no"):
            val = False
        node[path[-1]] = val


def load_config(path: str | Path | None = None) -> GuardianConfig:
    """Load config.yaml, apply env overrides, return validated config object."""
    if path is None:
        path = Path(__file__).parent.parent / "config.yaml"
    path = Path(path)

    raw: dict[str, Any] = {}
    if path.exists():
        with open(path) as f:
            raw = yaml.safe_load(f) or {}

    _overlay_env(raw)

    # Ensure data/log dirs exist
    cfg = GuardianConfig(**raw)
    Path(cfg.service.data_dir).mkdir(parents=True, exist_ok=True)
    Path(cfg.service.log_dir).mkdir(parents=True, exist_ok=True)
    return cfg


# Module-level singleton — import this everywhere
cfg: GuardianConfig = load_config()
