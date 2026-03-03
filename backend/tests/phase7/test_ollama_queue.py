"""Tests for ollama queue semantics across provider modes."""

from __future__ import annotations

import asyncio

import pytest

from agent import ollama_queue


@pytest.mark.asyncio
async def test_ollama_mode_analysis_waits_behind_chat_and_reports_queued():
    ollama_queue.init_queue("ollama")

    chat_entered = asyncio.Event()
    release_chat = asyncio.Event()
    analysis_started = asyncio.Event()
    result: dict[str, str] = {}

    async def _chat_task():
        async with ollama_queue.ollama_chat_slot():
            chat_entered.set()
            await release_chat.wait()

    async def _analysis_task():
        await chat_entered.wait()
        async with ollama_queue.ollama_analysis_slot(timeout=0.5) as status:
            result["status"] = status
            analysis_started.set()

    chat_task = asyncio.create_task(_chat_task())
    analysis_task = asyncio.create_task(_analysis_task())

    await chat_entered.wait()
    await asyncio.sleep(0.02)
    assert not analysis_started.is_set()

    release_chat.set()
    await asyncio.gather(chat_task, analysis_task)

    assert result["status"] == "queued"


@pytest.mark.asyncio
async def test_ollama_mode_analysis_slot_times_out_when_chat_stays_active():
    ollama_queue.init_queue("ollama")

    async with ollama_queue.ollama_chat_slot():
        with pytest.raises(asyncio.TimeoutError):
            async with ollama_queue.ollama_analysis_slot(timeout=0.05):
                pass


@pytest.mark.asyncio
async def test_cloud_mode_analysis_is_immediate_even_with_chat_context_open():
    ollama_queue.init_queue("cloud")

    async with ollama_queue.ollama_chat_slot():
        async with ollama_queue.ollama_analysis_slot(timeout=0.05) as status:
            assert status == "immediate"

    assert ollama_queue.is_chat_active() is False
