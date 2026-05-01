"""Tests for guardian.actions.docker_actions — all operations use a mocked docker client."""
from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest
from docker.errors import NotFound


def _make_actions():
    from guardian.actions.docker_actions import DockerActions

    return DockerActions()


def _mock_client(containers=None, images=None, networks=None, api=None):
    """Return a MagicMock docker client with optional sub-mock configuration."""
    c = MagicMock()
    if containers is not None:
        c.containers = containers
    if images is not None:
        c.images = images
    if networks is not None:
        c.networks = networks
    if api is not None:
        c.api = api
    return c


# ── restart_container ─────────────────────────────────────────────────────────

async def test_restart_container_success():
    actions = _make_actions()
    mock_container = MagicMock()
    mock_client = _mock_client()
    mock_client.containers.get.return_value = mock_container

    with patch("guardian.actions.docker_actions._client", return_value=mock_client):
        result = await actions.restart_container("open-webui")

    mock_container.restart.assert_called_once_with(timeout=30)
    assert result == {"action": "restarted", "container": "open-webui"}


async def test_restart_container_not_found_raises():
    actions = _make_actions()
    mock_client = _mock_client()
    mock_client.containers.get.side_effect = NotFound("open-webui")

    with patch("guardian.actions.docker_actions._client", return_value=mock_client):
        with pytest.raises(RuntimeError, match="not found"):
            await actions.restart_container("open-webui")


# ── stop_container ────────────────────────────────────────────────────────────

async def test_stop_container_success():
    actions = _make_actions()
    mock_container = MagicMock()
    mock_client = _mock_client()
    mock_client.containers.get.return_value = mock_container

    with patch("guardian.actions.docker_actions._client", return_value=mock_client):
        result = await actions.stop_container("caddy", timeout=10)

    mock_container.stop.assert_called_once_with(timeout=10)
    assert result["action"] == "stopped"
    assert result["container"] == "caddy"


async def test_stop_container_not_found_raises():
    actions = _make_actions()
    mock_client = _mock_client()
    mock_client.containers.get.side_effect = NotFound("caddy")

    with patch("guardian.actions.docker_actions._client", return_value=mock_client):
        with pytest.raises(RuntimeError, match="not found"):
            await actions.stop_container("caddy")


# ── pull_image ────────────────────────────────────────────────────────────────

async def test_pull_image_success():
    actions = _make_actions()
    mock_container = MagicMock()
    mock_container.image.tags = ["ghcr.io/open-webui/open-webui:main"]
    mock_client = _mock_client()
    mock_client.containers.get.return_value = mock_container
    mock_client.api.pull.return_value = iter([{"status": "Pull complete"}])

    with patch("guardian.actions.docker_actions._client", return_value=mock_client):
        result = await actions.pull_image("open-webui")

    assert result["action"] == "pulled"
    assert "open-webui" in result["image"]


async def test_pull_image_uses_name_as_fallback_when_container_missing():
    actions = _make_actions()
    mock_client = _mock_client()
    mock_client.containers.get.side_effect = NotFound("myimage")
    mock_client.api.pull.return_value = iter([{"status": "Pull complete"}])

    with patch("guardian.actions.docker_actions._client", return_value=mock_client):
        result = await actions.pull_image("myimage")

    assert result["image"] == "myimage"


# ── prune ─────────────────────────────────────────────────────────────────────

async def test_prune_images_reclaims_space():
    actions = _make_actions()
    mock_client = _mock_client()
    mock_client.images.prune.return_value = {"SpaceReclaimed": 500_000_000}

    with patch("guardian.actions.docker_actions._client", return_value=mock_client):
        result = await actions.prune("images")

    assert result["action"] == "pruned"
    assert result["type"] == "images"
    assert result["space_reclaimed_mb"] == pytest.approx(500.0, abs=1.0)


async def test_prune_containers():
    actions = _make_actions()
    mock_client = _mock_client()
    mock_client.containers.prune.return_value = {"SpaceReclaimed": 0}

    with patch("guardian.actions.docker_actions._client", return_value=mock_client):
        result = await actions.prune("containers")

    assert result["type"] == "containers"


async def test_prune_volumes_raises_value_error():
    actions = _make_actions()
    with pytest.raises(ValueError, match="volumes"):
        await actions.prune("volumes")


async def test_prune_unknown_type_raises():
    actions = _make_actions()
    with pytest.raises(ValueError):
        await actions.prune("secrets")


# ── remove_stopped_containers ─────────────────────────────────────────────────

async def test_remove_stopped_containers():
    actions = _make_actions()
    mock_client = _mock_client()
    mock_client.containers.prune.return_value = {
        "ContainersDeleted": ["abc123", "def456"],
        "SpaceReclaimed": 1_000_000,
    }

    with patch("guardian.actions.docker_actions._client", return_value=mock_client):
        result = await actions.remove_stopped_containers()

    assert result["action"] == "removed_stopped_containers"
    assert len(result["containers_deleted"]) == 2
