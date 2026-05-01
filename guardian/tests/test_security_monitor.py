"""Tests for guardian.monitors.security — auth.log parsing, brute-force detection, process scanning."""
from __future__ import annotations

import types
from unittest.mock import MagicMock, patch

import pytest


# ── _is_whitelisted ───────────────────────────────────────────────────────────

def test_is_whitelisted_exact_match(monkeypatch):
    from guardian.core.config import cfg
    from guardian.monitors.security import _is_whitelisted

    ban_cfg = types.SimpleNamespace(whitelist_ips=["192.168.1.1", "10.0.0.5"])
    monkeypatch.setattr(cfg.security, "auto_ban_ssh", ban_cfg)

    assert _is_whitelisted("192.168.1.1") is True
    assert _is_whitelisted("10.0.0.5") is True
    assert _is_whitelisted("8.8.8.8") is False


def test_is_whitelisted_cidr(monkeypatch):
    from guardian.core.config import cfg
    from guardian.monitors.security import _is_whitelisted

    ban_cfg = types.SimpleNamespace(whitelist_ips=["10.0.0.0/8", "192.168.0.0/16"])
    monkeypatch.setattr(cfg.security, "auto_ban_ssh", ban_cfg)

    assert _is_whitelisted("10.5.6.7") is True
    assert _is_whitelisted("192.168.1.100") is True
    assert _is_whitelisted("172.16.0.1") is False


def test_is_whitelisted_tailscale_ip(monkeypatch):
    from guardian.core.config import cfg
    from guardian.monitors.security import _is_whitelisted

    ban_cfg = types.SimpleNamespace(whitelist_ips=["100.64.0.0/10"])
    monkeypatch.setattr(cfg.security, "auto_ban_ssh", ban_cfg)

    assert _is_whitelisted("100.118.0.92") is True  # Tailscale range
    assert _is_whitelisted("1.1.1.1") is False


# ── _parse_auth_log ───────────────────────────────────────────────────────────

async def test_parse_auth_log_detects_password_failures(tmp_path, monkeypatch):
    from guardian.core.config import cfg
    import guardian.monitors.security as sec

    log_file = tmp_path / "auth.log"
    log_file.write_text(
        "Failed password for root from 203.0.113.5 port 22 ssh2\n"
        "Failed password for invalid user bob from 203.0.113.5 port 22 ssh2\n"
        "Failed password for root from 198.51.100.1 port 22 ssh2\n"
    )
    monkeypatch.setattr(cfg.security, "auth_log", str(log_file))

    result = await sec._parse_auth_log()

    assert result["failures_by_ip"]["203.0.113.5"] == 2
    assert result["failures_by_ip"]["198.51.100.1"] == 1
    assert "203.0.113.5" in result["recent_failures"]


async def test_parse_auth_log_detects_invalid_user(tmp_path, monkeypatch):
    from guardian.core.config import cfg
    import guardian.monitors.security as sec

    log_file = tmp_path / "auth.log"
    log_file.write_text(
        "Invalid user admin from 10.10.10.10 port 54321\n"
        "Invalid user ubuntu from 10.10.10.10 port 54321\n"
    )
    monkeypatch.setattr(cfg.security, "auth_log", str(log_file))

    result = await sec._parse_auth_log()

    assert result["failures_by_ip"]["10.10.10.10"] == 2


async def test_parse_auth_log_detects_successful_logins(tmp_path, monkeypatch):
    from guardian.core.config import cfg
    import guardian.monitors.security as sec

    log_file = tmp_path / "auth.log"
    log_file.write_text(
        "Accepted publickey for hectorsvill from 100.64.34.72 port 51234 ssh2\n"
    )
    monkeypatch.setattr(cfg.security, "auth_log", str(log_file))

    result = await sec._parse_auth_log()

    assert any(c["user"] == "hectorsvill" for c in result["connections"])
    assert any(c["ip"] == "100.64.34.72" for c in result["connections"])


async def test_parse_auth_log_missing_file(tmp_path, monkeypatch):
    from guardian.core.config import cfg
    import guardian.monitors.security as sec

    monkeypatch.setattr(cfg.security, "auth_log", str(tmp_path / "nonexistent.log"))

    result = await sec._parse_auth_log()

    assert "error" in result


async def test_parse_auth_log_tracks_position(tmp_path, monkeypatch):
    """Calling _parse_auth_log twice only returns NEW lines the second time."""
    from guardian.core.config import cfg
    import guardian.monitors.security as sec

    log_file = tmp_path / "auth.log"
    log_file.write_text("Failed password for root from 1.1.1.1 port 22 ssh2\n")
    monkeypatch.setattr(cfg.security, "auth_log", str(log_file))

    await sec._parse_auth_log()  # first read

    # Append a new line
    with open(log_file, "a") as f:
        f.write("Failed password for root from 2.2.2.2 port 22 ssh2\n")

    result2 = await sec._parse_auth_log()  # second read should only see new line
    assert "2.2.2.2" in result2["failures_by_ip"]
    assert "1.1.1.1" not in result2["failures_by_ip"]


# ── _check_brute_force ────────────────────────────────────────────────────────

async def test_brute_force_threshold_triggers_ban(db, monkeypatch):
    from guardian.core.config import cfg
    import guardian.monitors.security as sec

    ban_cfg = types.SimpleNamespace(threshold=5, whitelist_ips=[], duration_minutes=60)
    monkeypatch.setattr(cfg.security, "auto_ban_ssh", ban_cfg)

    # Directly populate _ssh_failures above threshold
    import time
    now = time.time()
    sec._ssh_failures["203.0.113.99"] = [now] * 10  # 10 failures

    auth_data = {
        "recent_failures": {"203.0.113.99": 10},
        "failures_by_ip": {},
    }
    to_ban = await sec._check_brute_force(auth_data)

    assert "203.0.113.99" in to_ban


async def test_brute_force_whitelisted_ip_not_banned(db, monkeypatch):
    from guardian.core.config import cfg
    import guardian.monitors.security as sec

    ban_cfg = types.SimpleNamespace(threshold=5, whitelist_ips=["192.168.1.0/24"])
    monkeypatch.setattr(cfg.security, "auto_ban_ssh", ban_cfg)

    auth_data = {"recent_failures": {"192.168.1.50": 99}}
    to_ban = await sec._check_brute_force(auth_data)

    assert "192.168.1.50" not in to_ban


async def test_brute_force_below_threshold_not_banned(db, monkeypatch):
    from guardian.core.config import cfg
    import guardian.monitors.security as sec

    ban_cfg = types.SimpleNamespace(threshold=10, whitelist_ips=[])
    monkeypatch.setattr(cfg.security, "auto_ban_ssh", ban_cfg)
    thresholds = types.SimpleNamespace(ssh_failure_critical=5)
    monkeypatch.setattr(cfg, "thresholds", thresholds)

    auth_data = {"recent_failures": {"1.2.3.4": 2}}
    to_ban = await sec._check_brute_force(auth_data)

    assert "1.2.3.4" not in to_ban


# ── _scan_processes_sync ──────────────────────────────────────────────────────

def test_scan_processes_detects_cryptominer(monkeypatch):
    from guardian.core.config import cfg
    from guardian.monitors.security import _scan_processes_sync

    monkeypatch.setattr(cfg.security, "suspicious_process_patterns", ["xmrig", "minerd"])

    mock_proc = MagicMock()
    mock_proc.info = {
        "pid": 1234,
        "name": "xmrig",
        "cmdline": ["xmrig", "--pool", "pool.minexmr.com"],
        "username": "nobody",
        "cpu_percent": 99.0,
    }

    with patch("guardian.monitors.security.psutil.process_iter", return_value=[mock_proc]):
        found = _scan_processes_sync()

    assert len(found) == 1
    assert found[0]["name"] == "xmrig"
    assert found[0]["matched_pattern"] == "xmrig"


def test_scan_processes_ignores_normal_processes(monkeypatch):
    from guardian.core.config import cfg
    from guardian.monitors.security import _scan_processes_sync

    monkeypatch.setattr(cfg.security, "suspicious_process_patterns", ["xmrig"])

    mock_proc = MagicMock()
    mock_proc.info = {
        "pid": 500,
        "name": "nginx",
        "cmdline": ["nginx", "-g", "daemon off;"],
        "username": "www-data",
        "cpu_percent": 0.1,
    }

    with patch("guardian.monitors.security.psutil.process_iter", return_value=[mock_proc]):
        found = _scan_processes_sync()

    assert found == []
