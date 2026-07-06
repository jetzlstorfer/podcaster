"""Hosted Foundry agent — Researcher.

Serves the ``responses`` protocol on :8088. Given a JSON request
``{topic, length, language}`` (or a bare topic), it uses the Foundry native
web-search tool to produce a ``ResearchBrief`` JSON document.

Runtime-injected env (by the Foundry hosting platform): ``FOUNDRY_PROJECT_ENDPOINT``,
``AZURE_AI_MODEL_DEPLOYMENT_NAME``, ``APPLICATIONINSIGHTS_CONNECTION_STRING``.

Run locally with ``azd ai agent run`` (or ``python main.py``).
"""

from __future__ import annotations

import os

from agent_framework import Agent
from agent_framework.foundry import FoundryChatClient
from agent_framework_foundry_hosting import ResponsesHostServer
from azure.identity import DefaultAzureCredential
from dotenv import load_dotenv

from podcaster.agents.researcher import build_hosted_instructions
from podcaster.observability import setup_observability

load_dotenv()


def _model() -> str:
    return (
        os.environ.get("AZURE_AI_MODEL_DEPLOYMENT_NAME")
        or os.environ.get("FOUNDRY_MODEL")
        or "gpt-5-mini"
    )


def build_agent() -> Agent:
    client = FoundryChatClient(
        project_endpoint=os.environ["FOUNDRY_PROJECT_ENDPOINT"],
        model=_model(),
        credential=DefaultAzureCredential(),
    )
    return Agent(
        client=client,
        name="researcher",
        instructions=build_hosted_instructions(),
        # WebSearchTool is a MutableMapping; dict() gives the {"type":"web_search"}
        # form the agent framework expects.
        tools=[dict(FoundryChatClient.get_web_search_tool())],
        # The hosting layer manages conversation state; don't persist server-side.
        default_options={"store": False},
    )


def main() -> None:
    setup_observability()
    ResponsesHostServer(build_agent()).run()


if __name__ == "__main__":
    main()
