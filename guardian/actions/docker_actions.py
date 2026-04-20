"""
Docker action implementations.
All operations use docker-py (sync) run inside asyncio.run_in_executor.
"""
from __future__ import annotations

import asyncio
import time
from typing import Any

import docker
from docker.errors import DockerException, NotFound

from guardian.core.config import cfg
from guardian.core.logger import get_logger

log = get_logger(__name__)


def _client() -> docker.DockerClient:
    return docker.DockerClient(base_url=cfg.docker.socket)


class DockerActions:

    async def _run(self, fn, *args, **kwargs) -> Any:
        """Run a synchronous docker-py call in the thread executor."""
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(None, lambda: fn(*args, **kwargs))

    # ── Container lifecycle ────────────────────────────────────────────────────

    async def restart_container(self, name: str) -> dict:
        """Gracefully restart a container (SIGTERM → wait → SIGKILL)."""
        def _restart():
            c = _client()
            container = c.containers.get(name)
            container.restart(timeout=30)
            c.close()
            return {"action": "restarted", "container": name}

        log.info("docker_restart_container", container=name)
        try:
            result = await self._run(_restart)
            log.info("docker_restart_complete", container=name)
            return result
        except NotFound:
            raise RuntimeError(f"Container '{name}' not found")
        except DockerException as e:
            raise RuntimeError(f"Docker error restarting {name}: {e}")

    async def stop_container(self, name: str, timeout: int = 30) -> dict:
        """Gracefully stop a container."""
        def _stop():
            c = _client()
            container = c.containers.get(name)
            container.stop(timeout=timeout)
            c.close()
            return {"action": "stopped", "container": name}

        log.warning("docker_stop_container", container=name)
        try:
            return await self._run(_stop)
        except NotFound:
            raise RuntimeError(f"Container '{name}' not found")

    async def start_container(self, name: str) -> dict:
        def _start():
            c = _client()
            container = c.containers.get(name)
            container.start()
            c.close()
            return {"action": "started", "container": name}

        log.info("docker_start_container", container=name)
        return await self._run(_start)

    async def pull_image(self, name: str) -> dict:
        """
        Pull the latest image for a container.
        Looks up the image tag from the running container.
        Does NOT restart the container — a separate restart_container is needed.
        """
        def _pull():
            c = _client()
            try:
                container = c.containers.get(name)
                image_tag = container.image.tags[0] if container.image.tags else None
            except NotFound:
                image_tag = name  # treat name as direct image reference
            if not image_tag:
                raise RuntimeError(f"Cannot determine image tag for container '{name}'")
            log.info("docker_pull_image_start", image=image_tag)
            for line in c.api.pull(image_tag, stream=True, decode=True):
                if "error" in line:
                    raise RuntimeError(f"Pull error: {line['error']}")
            c.close()
            return {"action": "pulled", "image": image_tag}

        return await self._run(_pull)

    async def get_container_logs(
        self, name: str, tail: int = 100, since_minutes: int = 60
    ) -> list[str]:
        """Fetch recent container log lines."""
        def _logs():
            c = _client()
            container = c.containers.get(name)
            since = int(time.time()) - since_minutes * 60
            raw = container.logs(tail=tail, since=since, timestamps=True)
            c.close()
            return raw.decode("utf-8", errors="replace").splitlines()

        return await self._run(_logs)

    # ── Cleanup ────────────────────────────────────────────────────────────────

    async def prune(self, prune_type: str) -> dict:
        """
        Prune unused Docker objects.
        prune_type: 'images' | 'containers' | 'networks' | 'system'
        Note: 'volumes' is intentionally NOT in this map (prohibited action).
        """
        allowed = {"images", "containers", "networks", "system"}
        if prune_type not in allowed:
            raise ValueError(
                f"Prune type '{prune_type}' not allowed. "
                f"Allowed: {allowed}. Volumes require manual action."
            )

        def _prune():
            c = _client()
            if prune_type == "images":
                result = c.images.prune(filters={"dangling": True})
                reclaimed = result.get("SpaceReclaimed", 0)
            elif prune_type == "containers":
                result = c.containers.prune()
                reclaimed = result.get("SpaceReclaimed", 0)
            elif prune_type == "networks":
                result = c.networks.prune()
                reclaimed = 0
            elif prune_type == "system":
                # Safe system prune: stopped containers + dangling images + unused networks
                result = c.api.prune_builds()  # doesn't exist, use individual
                reclaimed = 0
                for fn in (c.containers.prune, c.images.prune, c.networks.prune):
                    r = fn()
                    reclaimed += r.get("SpaceReclaimed", 0)
            c.close()
            return {
                "action": "pruned",
                "type": prune_type,
                "space_reclaimed_mb": round(reclaimed / 1e6, 1),
            }

        log.info("docker_prune", type=prune_type)
        return await self._run(_prune)

    async def remove_stopped_containers(self) -> dict:
        """Remove all stopped containers (safe, data is in volumes)."""
        def _remove():
            c = _client()
            result = c.containers.prune()
            c.close()
            return {
                "action": "removed_stopped_containers",
                "containers_deleted": result.get("ContainersDeleted") or [],
                "space_reclaimed_mb": round(result.get("SpaceReclaimed", 0) / 1e6, 1),
            }
        return await self._run(_remove)

    async def restart_compose_stack(self, project_dir: str) -> dict:
        """
        Restart the entire docker-compose stack via subprocess.
        Used as a last-resort self-healing action.
        """
        import subprocess
        log.warning("docker_compose_restart", project_dir=project_dir)
        proc = await asyncio.create_subprocess_exec(
            "docker", "compose", "up", "-d", "--remove-orphans",
            cwd=project_dir,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await proc.communicate()
        if proc.returncode != 0:
            raise RuntimeError(f"docker compose up failed: {stderr.decode()[:500]}")
        return {
            "action": "compose_restarted",
            "project_dir": project_dir,
            "output": stdout.decode()[-500:],
        }

    async def inspect_container(self, name: str) -> dict:
        """Return full container inspect data."""
        def _inspect():
            c = _client()
            container = c.containers.get(name)
            attrs = container.attrs
            c.close()
            return attrs
        return await self._run(_inspect)
