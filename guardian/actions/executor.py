"""
Action executor — the single gateway for all automated actions.

Responsibilities:
  - Enforce dry_run mode (simulate without executing)
  - Enforce rate limits (max actions per hour)
  - Route by risk level: auto-execute low/medium, queue high/critical for approval
  - Write full audit trail to database
  - Call notification service for high-risk pending actions
"""
from __future__ import annotations

import asyncio
import secrets
import time
from collections import deque
from typing import Any

from guardian.core.config import cfg
from guardian.core.database import (
    insert_action,
    update_action,
)
from guardian.core.logger import get_logger

log = get_logger(__name__)


async def _noop_alert() -> dict:
    return {"alerted": True}


# Rolling window of action timestamps for rate limiting
_action_timestamps: deque[float] = deque(maxlen=1000)
_container_restart_timestamps: deque[float] = deque(maxlen=200)

# Pending approvals: token → asyncio.Event that is set when approved/denied
_pending: dict[str, asyncio.Event] = {}
_pending_results: dict[str, bool] = {}   # token → True=approved, False=denied


class ActionExecutor:
    """
    Central dispatcher for all automated actions.
    Import and call .execute(proposal) from the reasoning loop.
    """

    def __init__(self) -> None:
        # Lazy imports to avoid circular dependency at module load time
        self._docker_actions = None
        self._system_actions = None
        self._notify = None

    def _get_docker(self):
        if self._docker_actions is None:
            from guardian.actions.docker_actions import DockerActions
            self._docker_actions = DockerActions()
        return self._docker_actions

    def _get_system(self):
        if self._system_actions is None:
            from guardian.actions.system_actions import SystemActions
            self._system_actions = SystemActions()
        return self._system_actions

    def _get_notify(self):
        if self._notify is None:
            from guardian.notifications.webhook import NotificationService
            self._notify = NotificationService()
        return self._notify

    # ── Rate limiting ─────────────────────────────────────────────────────────

    def _check_rate_limit(self) -> bool:
        now = time.time()
        window = 3600
        # Prune old entries
        while _action_timestamps and now - _action_timestamps[0] > window:
            _action_timestamps.popleft()
        if len(_action_timestamps) >= cfg.safety.max_actions_per_hour:
            return False
        _action_timestamps.append(now)
        return True

    def _check_restart_rate_limit(self) -> bool:
        now = time.time()
        window = 3600
        while _container_restart_timestamps and now - _container_restart_timestamps[0] > window:
            _container_restart_timestamps.popleft()
        if len(_container_restart_timestamps) >= cfg.safety.max_container_restarts_per_hour:
            return False
        _container_restart_timestamps.append(now)
        return True

    # ── Routing ───────────────────────────────────────────────────────────────

    def _get_policy(self, risk_level: str) -> str:
        """Return policy string for the given risk level."""
        policy_map = {
            "low": cfg.ai.risk_policy.low,
            "medium": cfg.ai.risk_policy.medium,
            "high": cfg.ai.risk_policy.high,
            "critical": cfg.ai.risk_policy.critical,
        }
        return policy_map.get(risk_level, "require_approval")

    # ── Main dispatch ─────────────────────────────────────────────────────────

    async def execute(
        self,
        proposal: dict[str, Any],
        decision_id: int | None = None,
    ) -> dict[str, Any]:
        """
        Execute a single action proposal.
        Returns a result dict describing what happened.
        """
        action_type = proposal.get("action_type", "unknown")
        target = proposal.get("target", "")
        risk_level = proposal.get("risk_level", "high")
        confidence = proposal.get("confidence", 0.0)
        reason = proposal.get("reason", "")
        parameters = proposal.get("parameters", {})
        dry_run = cfg.safety.dry_run

        log.info(
            "action_dispatch",
            action_type=action_type,
            target=target,
            risk_level=risk_level,
            confidence=confidence,
            dry_run=dry_run,
        )

        # Hard stop
        if cfg.safety.emergency_stop:
            return {"action_type": action_type, "status": "blocked", "reason": "emergency_stop"}

        # Prohibited action check
        if action_type in cfg.safety.prohibited_actions:
            log.warning("action_prohibited", action_type=action_type)
            return {"action_type": action_type, "status": "prohibited"}

        # Rate limit
        if not self._check_rate_limit():
            log.warning("action_rate_limited", action_type=action_type)
            return {"action_type": action_type, "status": "rate_limited"}

        # Write to DB in pending state
        token = secrets.token_urlsafe(16)
        action_id = await insert_action(
            decision_id=decision_id,
            action_type=action_type,
            parameters={"target": target, **parameters},
            risk_level=risk_level,
            status="dry_run" if dry_run else "pending",
            dry_run=dry_run,
            approval_token=token,
        )

        # Dry-run: simulate and return
        if dry_run:
            log.info("action_dry_run", action_type=action_type, target=target, would_do=reason)
            await update_action(action_id, "dry_run", result={"simulated": True, "reason": reason})
            return {"action_type": action_type, "action_id": action_id, "status": "dry_run"}

        # Route by policy
        policy = self._get_policy(risk_level)

        if policy in ("auto", "auto_with_log"):
            if policy == "auto_with_log":
                log.warning(
                    "action_auto_medium_risk",
                    action_type=action_type,
                    target=target,
                    confidence=confidence,
                    reason=reason,
                )
            return await self._execute_action(action_id, action_type, target, parameters, decision_id)

        elif policy == "require_approval":
            return await self._request_approval(
                action_id, token, action_type, target, risk_level, reason, parameters
            )

        else:
            log.error("unknown_policy", policy=policy)
            return {"action_type": action_type, "status": "error", "reason": "unknown_policy"}

    async def _execute_action(
        self,
        action_id: int,
        action_type: str,
        target: str,
        parameters: dict,
        decision_id: int | None,
    ) -> dict[str, Any]:
        """Route to the correct action implementation and capture outcome."""
        await update_action(action_id, "executing")

        try:
            result = await self._dispatch(action_type, target, parameters)
            await update_action(action_id, "completed", result=result)
            log.info("action_completed", action_type=action_type, target=target, result=result)
            return {"action_type": action_type, "action_id": action_id,
                    "target": target, "status": "completed", **result}

        except Exception as exc:
            err = {"error": str(exc)}
            await update_action(action_id, "failed", result=err)
            log.error("action_failed", action_type=action_type, target=target, exc_info=exc)
            return {"action_type": action_type, "action_id": action_id,
                    "target": target, "status": "failed", "error": str(exc)}

    async def _dispatch(self, action_type: str, target: str, parameters: dict) -> dict:
        """Map action_type string to actual implementation."""
        d = self._get_docker()
        s = self._get_system()

        dispatch_map = {
            "restart_container": lambda: d.restart_container(target),
            "stop_container": lambda: d.stop_container(target),
            "pull_image": lambda: d.pull_image(target),
            "prune_docker": lambda: d.prune(parameters.get("type", "containers")),
            "ban_ip": lambda: s.ban_ip(target, parameters.get("duration_minutes", 60)),
            "run_security_update": lambda: s.run_security_update(),
            "clean_disk_space": lambda: s.clean_disk_space(),
            "restart_service": lambda: s.restart_service(target),
            "alert_only": lambda: _noop_alert(),
        }

        handler = dispatch_map.get(action_type)
        if not handler:
            raise ValueError(f"Unknown action_type: {action_type}")

        return await handler()

    async def _request_approval(
        self,
        action_id: int,
        token: str,
        action_type: str,
        target: str,
        risk_level: str,
        reason: str,
        parameters: dict,
    ) -> dict[str, Any]:
        """
        Notify human via webhook and wait up to 10 minutes for approval.
        Returns result based on approval/denial/timeout.
        """
        await update_action(action_id, "pending")

        # Send notification
        notify = self._get_notify()
        await notify.send_approval_request(
            action_id=action_id,
            token=token,
            action_type=action_type,
            target=target,
            risk_level=risk_level,
            reason=reason,
        )

        # Set up approval event
        event = asyncio.Event()
        _pending[token] = event

        log.info(
            "action_awaiting_approval",
            action_type=action_type,
            target=target,
            token=token,
            timeout_minutes=10,
        )

        try:
            # Wait up to 10 minutes for human response
            await asyncio.wait_for(event.wait(), timeout=600)
            approved = _pending_results.get(token, False)
        except asyncio.TimeoutError:
            approved = False
            log.warning("action_approval_timeout", action_type=action_type, token=token)
        finally:
            _pending.pop(token, None)
            _pending_results.pop(token, None)

        if approved:
            return await self._execute_action(
                action_id, action_type, target, parameters, None
            )
        else:
            await update_action(action_id, "denied")
            return {"action_type": action_type, "action_id": action_id, "status": "denied"}


async def approve_action(token: str, approved_by: str = "webhook") -> bool:
    """Called by the API when a human approves an action via webhook."""
    if token not in _pending:
        return False
    _pending_results[token] = True
    _pending[token].set()
    log.info("action_approved", token=token, by=approved_by)
    return True


async def deny_action(token: str, denied_by: str = "webhook") -> bool:
    """Called by the API when a human denies an action."""
    if token not in _pending:
        return False
    _pending_results[token] = False
    _pending[token].set()
    log.info("action_denied", token=token, by=denied_by)
    return True
