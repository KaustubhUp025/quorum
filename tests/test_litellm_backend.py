"""Unit tests for the LiteLLM agent loop (all LLM calls mocked)."""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from quorum.agent import QuorumAgent
from quorum.config import Settings
from quorum.gitlab_client import GitLabRESTClient


def _make_settings(llm_backend: str = "ollama/mistral") -> Settings:
    return Settings(
        gitlab_token="fake",
        gemini_api_key="fake",
        llm_backend=llm_backend,
    )


def _make_client() -> GitLabRESTClient:
    client = MagicMock(spec=GitLabRESTClient)
    client.get_merge_request_diffs = AsyncMock(return_value="--- a/src/lock.py\n+++ b/src/lock.py\n+redis.set(key, 'locked', nx=True)")
    client.get_mr_metadata = AsyncMock(return_value={"source_branch": "feat/x", "target_branch": "main"})
    client.semantic_code_search = AsyncMock(return_value="No results found")
    client.get_merge_request = AsyncMock(return_value='{"title": "Test PR"}')
    client.get_file_contents = AsyncMock(return_value="# file content")
    client.create_workitem_note = AsyncMock(return_value='{"id": 1}')
    return client


def _make_openai_response(content: str, tool_calls=None):
    """Fake a litellm ModelResponse."""
    msg = MagicMock()
    msg.content = content
    msg.tool_calls = tool_calls or []
    msg.model_dump.return_value = {"role": "assistant", "content": content, "tool_calls": []}
    choice = MagicMock()
    choice.message = msg
    response = MagicMock()
    response.choices = [choice]
    return response


def _make_tool_call(name: str, args: dict, call_id: str = "call_1"):
    tc = MagicMock()
    tc.id = call_id
    tc.function.name = name
    tc.function.arguments = json.dumps(args)
    return tc


# ---------------------------------------------------------------------------
# _openai_tools — schema validation
# ---------------------------------------------------------------------------


class TestOpenAITools:
    def test_returns_three_tools(self):
        tools = QuorumAgent._openai_tools()
        assert len(tools) == 3

    def test_all_tools_have_function_type(self):
        for t in QuorumAgent._openai_tools():
            assert t["type"] == "function"
            assert "function" in t

    def test_semantic_code_search_has_query_param(self):
        tools = {t["function"]["name"]: t for t in QuorumAgent._openai_tools()}
        params = tools["semantic_code_search"]["function"]["parameters"]
        assert "query" in params["properties"]
        assert "query" in params["required"]

    def test_get_file_contents_has_file_path_param(self):
        tools = {t["function"]["name"]: t for t in QuorumAgent._openai_tools()}
        params = tools["get_file_contents"]["function"]["parameters"]
        assert "file_path" in params["properties"]
        assert "file_path" in params["required"]


# ---------------------------------------------------------------------------
# _agent_loop_litellm — conversation flow
# ---------------------------------------------------------------------------


class TestAgentLoopLiteLLM:
    @pytest.mark.asyncio
    async def test_immediate_text_response(self):
        """If the first response has no tool calls, return its text immediately."""
        settings = _make_settings()
        agent = QuorumAgent(settings)
        client = _make_client()

        final_response = _make_openai_response(
            '```json\n{"findings": []}\n```'
        )

        with patch("litellm.acompletion", new=AsyncMock(return_value=final_response)):
            result = await agent._agent_loop_litellm(
                "Analyse this diff", client, "owner/repo", 1
            )

        assert "findings" in result

    @pytest.mark.asyncio
    async def test_tool_call_then_final_response(self):
        """One tool call round, then final answer."""
        settings = _make_settings()
        agent = QuorumAgent(settings)
        client = _make_client()

        tc = _make_tool_call("semantic_code_search", {"query": "fencing token"})
        tool_call_response = _make_openai_response(None, tool_calls=[tc])
        final_response = _make_openai_response('```json\n{"findings": []}\n```')

        call_count = 0

        async def _fake_completion(**kwargs):
            nonlocal call_count
            call_count += 1
            return tool_call_response if call_count == 1 else final_response

        with patch("litellm.acompletion", new=_fake_completion):
            result = await agent._agent_loop_litellm(
                "Analyse this diff", client, "owner/repo", 1
            )

        assert call_count == 2
        assert "findings" in result
        client.semantic_code_search.assert_called_once_with(
            project_id="owner/repo",
            query="fencing token",
            max_results=settings.max_search_results,
        )

    @pytest.mark.asyncio
    async def test_system_prompt_in_first_message(self):
        """LiteLLM path must include system prompt as first message."""
        settings = _make_settings()
        agent = QuorumAgent(settings)
        client = _make_client()

        final_response = _make_openai_response('{"findings": []}')
        captured_messages = []

        async def _fake_completion(model, messages, **kwargs):
            captured_messages.extend(messages)
            return final_response

        with patch("litellm.acompletion", new=_fake_completion):
            await agent._agent_loop_litellm("prompt text", client, "owner/repo", 1)

        assert captured_messages[0]["role"] == "system"
        assert len(captured_messages[0]["content"]) > 50  # system prompt is non-trivial

    @pytest.mark.asyncio
    async def test_uses_configured_model(self):
        """The LiteLLM loop must pass the configured model name."""
        settings = _make_settings(llm_backend="openai/gpt-4o")
        agent = QuorumAgent(settings)
        client = _make_client()

        final_response = _make_openai_response('{"findings": []}')
        called_with_model = []

        async def _fake_completion(model, **kwargs):
            called_with_model.append(model)
            return final_response

        with patch("litellm.acompletion", new=_fake_completion):
            await agent._agent_loop_litellm("prompt", client, "owner/repo", 1)

        assert called_with_model[0] == "openai/gpt-4o"


# ---------------------------------------------------------------------------
# QuorumAgent.review() — backend selection
# ---------------------------------------------------------------------------


class TestBackendSelection:
    @pytest.mark.asyncio
    async def test_gemini_backend_uses_native_loop(self):
        """llm_backend starting with 'gemini/' must use the Gemini (non-LiteLLM) loop."""
        settings = _make_settings(llm_backend="gemini/gemini-2.5-pro")
        agent = QuorumAgent(settings)
        client = _make_client()

        with patch.object(agent, "_agent_loop", new=AsyncMock(return_value='{"findings": []}')) as mock_gemini, \
             patch.object(agent, "_agent_loop_litellm", new=AsyncMock(return_value='{"findings": []}')) as mock_litellm, \
             patch.object(agent, "_enrich_with_citations", new=AsyncMock(side_effect=lambda f: f)):
            await agent.review(project_id="owner/repo", mr_iid=1, client=client, post_comment=False)

        mock_gemini.assert_called_once()
        mock_litellm.assert_not_called()

    @pytest.mark.asyncio
    async def test_non_gemini_backend_uses_litellm_loop(self):
        """Any llm_backend not starting with 'gemini/' must use the LiteLLM loop."""
        settings = _make_settings(llm_backend="ollama/mistral")
        agent = QuorumAgent(settings)
        client = _make_client()

        with patch.object(agent, "_agent_loop", new=AsyncMock(return_value='{"findings": []}')) as mock_gemini, \
             patch.object(agent, "_agent_loop_litellm", new=AsyncMock(return_value='{"findings": []}')) as mock_litellm, \
             patch.object(agent, "_enrich_with_citations", new=AsyncMock(side_effect=lambda f: f)):
            await agent.review(project_id="owner/repo", mr_iid=1, client=client, post_comment=False)

        mock_litellm.assert_called_once()
        mock_gemini.assert_not_called()


# ---------------------------------------------------------------------------
# Disabled rules filtering
# ---------------------------------------------------------------------------


class TestDisabledRulesFiltering:
    @pytest.mark.asyncio
    async def test_disabled_rule_removed_from_surfaces(self):
        """Triggered rules in disabled_rules must be filtered before agent loop runs."""
        settings = _make_settings(llm_backend="gemini/gemini-2.5-pro")
        settings = settings.model_copy(update={"disabled_rules": ["RULE_01"]})
        agent = QuorumAgent(settings)
        client = _make_client()
        # Diff triggers RULE_01 (setnx)
        client.get_merge_request_diffs = AsyncMock(
            return_value="--- a/lock.py\n+++ b/lock.py\n+redis.set(key, '1', nx=True)"
        )

        with patch.object(agent, "_agent_loop", new=AsyncMock(return_value='{"findings": []}')) as mock_loop, \
             patch.object(agent, "_enrich_with_citations", new=AsyncMock(side_effect=lambda f: f)):
            result = await agent.review(
                project_id="owner/repo", mr_iid=1, client=client, post_comment=False
            )

        # If RULE_01 is disabled, and that's the only surface,
        # the agent loop should not be called (no surfaces left to check).
        mock_loop.assert_not_called()
        assert result.surfaces_detected == 0
