"""
AI Reasoning Loop — Observe → Analyze → Decide → Act → Verify (OADAV)

Every reasoning_interval_seconds:
  1. Observe  — collect snapshots from all monitors
  2. Analyze  — build prompt and call Ollama
  3. Decide   — parse structured JSON response into ActionProposals
  4. Act      — route each action to the executor (auto / require_approval / deny)
  5. Verify   — re-check metrics to confirm actions had desired effect
"""
from __future__ import annotations

import asyncio
import time
from typing import Any

from guardian.ai.ollama_client import get_ollama_client
from guardian.ai.prompts import (
    SYSTEM_PROMPT,
    build_analysis_prompt,
    build_config_summary,
    build_security_prompt,
)
from guardian.core.config import cfg
from guardian.core.database import (
    get_recent_actions,
    get_recent_events,
    insert_decision,
    insert_event,
)
from guardian.core.logger import get_logger
from guardian.monitors.docker_monitor import collect_docker_metrics
from guardian.monitors.security import collect_security_metrics
from guardian.monitors.system import collect_system_metrics

log = get_logger(__name__)

# Import action executor lazily to avoid circular imports
_executor = None


def _get_executor():
    global _executor
    if _executor is None:
        from guardian.actions.executor import ActionExecutor
        _executor = ActionExecutor()
    return _executor


# ── Phase 1: Observe ──────────────────────────────────────────────────────────

async def observe() -> dict[str, Any]:
    """Gather a full snapshot from all monitors concurrently."""
    system_task = asyncio.create_task(collect_system_metrics())
    docker_task = asyncio.create_task(collect_docker_metrics())
    security_task = asyncio.create_task(collect_security_metrics())
    events_task = asyncio.create_task(get_recent_events(limit=20, unresolved_only=True))

    system_m, docker_m, security_m, recent_events = await asyncio.gather(
        system_task, docker_task, security_task, events_task,
        return_exceptions=True,
    )

    # Replace exceptions with empty dicts so the analysis can still proceed
    if isinstance(system_m, Exception):
        log.error("observe_system_error", exc_info=system_m)
        system_m = {}
    if isinstance(docker_m, Exception):
        log.error("observe_docker_error", exc_info=docker_m)
        docker_m = {}
    if isinstance(security_m, Exception):
        log.error("observe_security_error", exc_info=security_m)
        security_m = {}
    if isinstance(recent_events, Exception):
        log.error("observe_events_error", exc_info=recent_events)
        recent_events = []

    return {
        "system": system_m,
        "docker": docker_m,
        "security": security_m,
        "recent_events": recent_events,
        "observed_at": time.time(),
    }


# ── Phase 2: Analyze ──────────────────────────────────────────────────────────

async def analyze(observation: dict[str, Any]) -> dict[str, Any] | None:
    """Send observation to Ollama, return parsed JSON decision."""
    if not cfg.ai.enabled:
        log.info("ai_disabled_skipping_analysis")
        return None

    ollama = get_ollama_client()
    if not await ollama.is_available():
        log.warning("ollama_unavailable_skipping_analysis", url=cfg.ai.ollama_url)
        await insert_event(
            "warning", "system", "AI Analysis Skipped",
            f"Ollama is not available at {cfg.ai.ollama_url}",
        )
        return None

    # Build context
    config_summary = build_config_summary(cfg.server.project_dir)
    prompt = build_analysis_prompt(
        system_metrics=observation["system"],
        docker_metrics=observation["docker"],
        security_metrics=observation["security"],
        recent_events=observation["recent_events"],
        config_summary=config_summary,
    )

    # Use reasoning model for thorough analysis
    model_preference = [cfg.ai.reasoning_model, cfg.ai.model]
    model = await ollama.best_available_model(model_preference)
    if not model:
        log.warning("no_ollama_models_available")
        return None

    log.info("ai_analysis_started", model=model)
    try:
        result = await ollama.generate_json(
            prompt=prompt,
            model=model,
            system=SYSTEM_PROMPT,
            temperature=0.15,
        )
        if result:
            log.info(
                "ai_analysis_complete",
                model=model,
                health_score=result.get("health_score"),
                issues=len(result.get("issues", [])),
                actions=len(result.get("actions", [])),
            )
        return result
    except Exception as e:
        log.error("ai_analysis_error", exc_info=e)
        return None


# ── Phase 3: Decide ───────────────────────────────────────────────────────────

def decide(analysis: dict[str, Any]) -> list[dict[str, Any]]:
    """
    Filter AI proposals through safety rules.
    Returns a list of approved ActionProposal dicts.
    """
    if not analysis:
        return []

    proposals = []
    for action in analysis.get("actions", []):
        action_type = action.get("action_type", "")
        risk_level = action.get("risk_level", "high")
        confidence = float(action.get("confidence", 0.0))

        # Never act on prohibited action types regardless of AI output
        if action_type in cfg.safety.prohibited_actions:
            log.warning(
                "action_prohibited",
                action_type=action_type,
                reason="In prohibited_actions list",
            )
            continue

        # Emergency stop — queue nothing
        if cfg.safety.emergency_stop:
            log.warning("emergency_stop_active_blocking_actions", action_type=action_type)
            continue

        # Confidence gate
        risk_policy = cfg.ai.risk_policy
        thresholds = cfg.ai.confidence_thresholds

        if risk_level == "low" and confidence < thresholds.auto_execute_low_risk:
            log.info(
                "action_low_confidence_skip",
                action_type=action_type,
                confidence=confidence,
                required=thresholds.auto_execute_low_risk,
            )
            continue
        if risk_level == "medium" and confidence < thresholds.auto_execute_medium_risk:
            log.info(
                "action_medium_confidence_skip",
                action_type=action_type,
                confidence=confidence,
                required=thresholds.auto_execute_medium_risk,
            )
            continue

        proposals.append(action)

    return proposals


# ── Phase 4: Act ──────────────────────────────────────────────────────────────

async def act(
    proposals: list[dict[str, Any]],
    decision_id: int,
) -> list[dict[str, Any]]:
    """Execute approved proposals, route high-risk ones to approval queue."""
    executor = _get_executor()
    results = []

    for proposal in proposals:
        result = await executor.execute(proposal, decision_id=decision_id)
        results.append(result)

    return results


# ── Phase 5: Verify ───────────────────────────────────────────────────────────

async def verify(action_results: list[dict[str, Any]]) -> None:
    """
    After executing actions, do a quick re-check to confirm they had effect.
    Log any discrepancies.
    """
    # For container restarts, wait a moment then check container status
    restart_actions = [
        r for r in action_results
        if r.get("action_type") == "restart_container"
        and r.get("status") == "completed"
    ]

    if not restart_actions:
        return

    await asyncio.sleep(15)  # allow containers to come up

    docker_data = await collect_docker_metrics()
    for action in restart_actions:
        target = action.get("target")
        if not target:
            continue
        container = docker_data.get("containers", {}).get(target, {})
        if container.get("status") != "running":
            await insert_event(
                "warning", "docker",
                f"Restart Verification Failed — {target}",
                f"Container '{target}' was restarted but is still not running "
                f"(status: {container.get('status', 'unknown')})",
            )
            log.warning("restart_verify_failed", target=target, status=container.get("status"))
        else:
            log.info("restart_verified_ok", target=target)


# ── Main Reasoning Loop ───────────────────────────────────────────────────────

async def reasoning_loop(stop_event: asyncio.Event) -> None:
    """
    Main OADAV loop.  Runs every reasoning_interval_seconds.
    """
    interval = cfg.monitoring.reasoning_interval_seconds
    log.info("reasoning_loop_started", interval=interval)

    # Stagger startup by 30 seconds to let monitors collect initial data
    await asyncio.sleep(30)

    while not stop_event.is_set():
        cycle_start = time.time()
        log.info("reasoning_cycle_start")

        try:
            # 1. Observe
            observation = await observe()

            # 2. Analyze
            analysis = await analyze(observation)

            if analysis:
                # Persist the decision
                decision_id = await insert_decision(
                    context={
                        "cpu_pct": observation["system"].get("cpu", {}).get("percent"),
                        "ram_pct": observation["system"].get("memory", {}).get("pct"),
                        "containers": list(observation["docker"].get("containers", {}).keys()),
                    },
                    reasoning=analysis.get("reasoning", ""),
                    summary=analysis.get("summary", ""),
                    confidence=analysis.get("confidence", 0.0),
                    actions=analysis.get("actions", []),
                    model=cfg.ai.reasoning_model,
                )

                # 3. Decide
                proposals = decide(analysis)
                log.info(
                    "reasoning_proposals",
                    total=len(analysis.get("actions", [])),
                    approved=len(proposals),
                )

                # 4. Act
                if proposals:
                    action_results = await act(proposals, decision_id=decision_id)

                    # 5. Verify
                    await verify(action_results)

                # Log any high-severity issues that don't have actions
                for issue in analysis.get("issues", []):
                    if issue.get("severity") in ("critical", "warning"):
                        await insert_event(
                            issue["severity"],
                            issue.get("category", "system"),
                            issue.get("title", "AI Detected Issue"),
                            issue.get("description", ""),
                        )

        except asyncio.CancelledError:
            break
        except Exception as exc:
            log.error("reasoning_cycle_error", exc_info=exc)

        elapsed = time.time() - cycle_start
        sleep_time = max(0, interval - elapsed)
        log.info("reasoning_cycle_complete", elapsed_s=round(elapsed, 1), next_in_s=round(sleep_time))

        try:
            await asyncio.wait_for(
                asyncio.shield(asyncio.ensure_future(stop_event.wait())),
                timeout=sleep_time,
            )
            break
        except asyncio.TimeoutError:
            pass

    log.info("reasoning_loop_stopped")
