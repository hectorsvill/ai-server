"""Tests for guardian.actions.system_actions — UFW ban, service restart, disk cleanup."""
from __future__ import annotations

import types
from unittest.mock import AsyncMock, patch

import pytest


def _make_actions():
    from guardian.actions.system_actions import SystemActions

    return SystemActions()


# ── ban_ip ────────────────────────────────────────────────────────────────────

async def test_ban_ip_valid_address():
    actions = _make_actions()

    with patch(
        "guardian.actions.system_actions._run_cmd",
        new_callable=AsyncMock,
        return_value=(0, "Rule added", ""),
    ):
        # Suppress the background unban task
        with patch("guardian.actions.system_actions.asyncio") as mock_asyncio:
            mock_asyncio.create_task = lambda coro: None
            result = await actions.ban_ip("1.2.3.4", duration_minutes=30)

    assert result["action"] == "ip_banned"
    assert result["ip"] == "1.2.3.4"
    assert result["duration_minutes"] == 30


async def test_ban_ip_invalid_address_raises():
    actions = _make_actions()

    with pytest.raises(ValueError, match="Invalid IP"):
        await actions.ban_ip("not.an.ip.address")


async def test_ban_ip_ufw_failure_raises():
    actions = _make_actions()

    with patch(
        "guardian.actions.system_actions._run_cmd",
        new_callable=AsyncMock,
        return_value=(1, "", "ERROR: could not find a profile"),
    ):
        with pytest.raises(RuntimeError, match="UFW deny failed"):
            await actions.ban_ip("5.5.5.5")


# ── unban_ip ──────────────────────────────────────────────────────────────────

async def test_unban_ip_success():
    actions = _make_actions()

    with patch(
        "guardian.actions.system_actions._run_cmd",
        new_callable=AsyncMock,
        return_value=(0, "Rule deleted", ""),
    ):
        result = await actions.unban_ip("1.2.3.4")

    assert result["action"] == "ip_unbanned"
    assert result["ip"] == "1.2.3.4"
    assert result["success"] is True


async def test_unban_ip_invalid_address_raises():
    actions = _make_actions()

    with pytest.raises(ValueError, match="Invalid IP"):
        await actions.unban_ip("notanip")


# ── restart_service ───────────────────────────────────────────────────────────

async def test_restart_service_allowed(monkeypatch):
    from guardian.core.config import cfg

    ollama_svc = types.SimpleNamespace(name="ollama", systemd_unit="ollama.service", critical=True)
    monkeypatch.setattr(cfg, "native_services", [ollama_svc])

    actions = _make_actions()
    with patch(
        "guardian.actions.system_actions._run_cmd",
        new_callable=AsyncMock,
        return_value=(0, "", ""),
    ):
        result = await actions.restart_service("ollama.service")

    assert result["action"] == "service_restarted"
    assert result["unit"] == "ollama.service"


async def test_restart_service_not_in_whitelist_raises(monkeypatch):
    from guardian.core.config import cfg

    monkeypatch.setattr(cfg, "native_services", [])

    actions = _make_actions()
    with pytest.raises(ValueError, match="not in the managed services list"):
        await actions.restart_service("sshd.service")


async def test_restart_service_systemctl_failure_raises(monkeypatch):
    from guardian.core.config import cfg

    svc = types.SimpleNamespace(name="ollama", systemd_unit="ollama.service", critical=True)
    monkeypatch.setattr(cfg, "native_services", [svc])

    actions = _make_actions()
    with patch(
        "guardian.actions.system_actions._run_cmd",
        new_callable=AsyncMock,
        return_value=(1, "", "Failed to restart ollama.service"),
    ):
        with pytest.raises(RuntimeError, match="failed"):
            await actions.restart_service("ollama.service")


# ── clean_disk_space ──────────────────────────────────────────────────────────

async def test_clean_disk_space_returns_summary(tmp_path, monkeypatch):
    from guardian.core.config import cfg

    # Maintenance config stub
    disk_cfg = types.SimpleNamespace(clean_journal_days=7)
    maint_cfg = types.SimpleNamespace(disk_cleanup=disk_cfg)
    monkeypatch.setattr(cfg, "maintenance", maint_cfg)
    monkeypatch.setattr(cfg.service, "log_dir", str(tmp_path))

    journal_output = "Vacuuming done, freed 10.5M of archived journals from 1 files.\n"

    def run_cmd_side_effect(cmd, **kwargs):
        if "journalctl" in cmd:
            return (0, journal_output, "")
        return (0, "", "")

    with patch(
        "guardian.actions.system_actions._run_cmd",
        new_callable=AsyncMock,
        side_effect=run_cmd_side_effect,
    ):
        actions = _make_actions()
        result = await actions.clean_disk_space()

    assert result["action"] == "disk_cleanup"
    assert isinstance(result["freed_mb"], float)
    assert isinstance(result["steps"], list)


# ── get_service_status ────────────────────────────────────────────────────────

async def test_get_service_status_active():
    actions = _make_actions()

    with patch(
        "guardian.actions.system_actions._run_cmd",
        new_callable=AsyncMock,
        return_value=(0, "active\n", ""),
    ):
        result = await actions.get_service_status("ollama.service")

    assert result["active"] is True
    assert result["unit"] == "ollama.service"


async def test_get_service_status_inactive():
    actions = _make_actions()

    with patch(
        "guardian.actions.system_actions._run_cmd",
        new_callable=AsyncMock,
        return_value=(3, "inactive\n", ""),
    ):
        result = await actions.get_service_status("missing.service")

    assert result["active"] is False
