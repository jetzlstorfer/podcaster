"""Hosted Foundry agent — Scriptwriter.

Serves the ``responses`` protocol on :8088. Given a ``ResearchBrief`` JSON
document (with ``language`` and ``length`` fields), it writes a two-host
``PodcastScript`` JSON document. No tools — pure LLM generation.

Runtime-injected env: ``FOUNDRY_PROJECT_ENDPOINT``,
``AZURE_AI_MODEL_DEPLOYMENT_NAME``, ``APPLICATIONINSIGHTS_CONNECTION_STRING``.
"""

from __future__ import annotations

import os

from agent_framework import Agent
from agent_framework.foundry import FoundryChatClient
from agent_framework_foundry_hosting import ResponsesHostServer
from azure.identity import DefaultAzureCredential
from dotenv import load_dotenv

from podcaster.agents.scriptwriter import build_hosted_instructions
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
        name="scriptwriter",
        instructions=build_hosted_instructions(),
        default_options={"store": False},
    )


def main() -> None:
    setup_observability()
    ResponsesHostServer(build_agent()).run()


if __name__ == "__main__":
    main()
