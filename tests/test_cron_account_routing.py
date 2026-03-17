"""Tests for cron callback account_id routing."""

import asyncio
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from nanobot.bus.events import OutboundMessage
from nanobot.cron.types import CronJob, CronPayload, CronSchedule, CronJobState


def _make_job(agent_id: str | None = None) -> CronJob:
    return CronJob(
        id="test_job",
        name="Test Job",
        enabled=True,
        schedule=CronSchedule(kind="cron", expr="0 9 * * *", tz="Asia/Shanghai"),
        payload=CronPayload(
            kind="agent_turn",
            message="do something",
            deliver=True,
            channel="feishu",
            to="oc_abc123",
            agent_id=agent_id,
        ),
        state=CronJobState(),
    )


def _make_agent(agent_id: str) -> MagicMock:
    agent = MagicMock()
    agent._agent_id = agent_id
    agent.workspace = Path("/tmp")
    agent.tools = MagicMock()
    agent.tools.get.return_value = None
    agent.process_direct = AsyncMock(return_value="response text")
    return agent


@pytest.mark.asyncio
async def test_cron_callback_passes_account_id_to_process_direct() -> None:
    """Cron callback should pass agent's account_id as inbound_metadata so
    MessageTool uses the correct Feishu bot account."""
    from nanobot.cli.commands import _make_cron_callback_for_test

    sent: list[OutboundMessage] = []

    async def fake_publish(msg: OutboundMessage) -> None:
        sent.append(msg)

    bus = MagicMock()
    bus.publish_outbound = fake_publish

    agent_pool = MagicMock()
    agent_pool._agents = {}

    operator = _make_agent("Operator")
    job = _make_job()

    callback = _make_cron_callback_for_test(operator, agent_pool, bus)
    await callback(job)

    # process_direct should be called with inbound_metadata containing account_id
    call_kwargs = operator.process_direct.call_args.kwargs
    assert call_kwargs.get("inbound_metadata", {}).get("account_id") == "Operator"


@pytest.mark.asyncio
async def test_cron_fallback_outbound_carries_account_id() -> None:
    """When agent doesn't use MessageTool, the fallback OutboundMessage should
    also carry the correct account_id."""
    from nanobot.cli.commands import _make_cron_callback_for_test

    sent: list[OutboundMessage] = []

    async def fake_publish(msg: OutboundMessage) -> None:
        sent.append(msg)

    bus = MagicMock()
    bus.publish_outbound = fake_publish

    agent_pool = MagicMock()
    agent_pool._agents = {}

    operator = _make_agent("Operator")
    # Simulate agent returning text (not using MessageTool)
    operator.process_direct = AsyncMock(return_value="paper summary")

    job = _make_job()

    callback = _make_cron_callback_for_test(operator, agent_pool, bus)
    await callback(job)

    assert len(sent) == 1
    assert sent[0].metadata.get("account_id") == "Operator"
