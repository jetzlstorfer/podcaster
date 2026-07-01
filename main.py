"""
Podcaster – entrypoint.

Modes:
  --server   Run as devui HTTP server (Agent Inspector, port 8088)
  --cli      Run the pipeline once and print results
             Requires --question "...", or --script <path> to narrate only
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Multi-agent podcast generator")
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--server", action="store_true", help="Start devui HTTP server")
    mode.add_argument("--cli", action="store_true", help="Run pipeline once (CLI)")
    parser.add_argument(
        "--question",
        type=str,
        default=None,
        help="Research question (required for --cli)",
    )
    parser.add_argument(
        "--script",
        type=str,
        default=None,
        help=(
            "Path to a saved script JSON (from a previous run). Skips research "
            "and scriptwriting and only runs the narrator."
        ),
    )
    parser.add_argument(
        "--port",
        type=int,
        default=8088,
        help="HTTP server port (default: 8088)",
    )
    return parser.parse_args()


def _run_server(port: int) -> None:
    # devui.serve() calls uvicorn.run() which manages its own event loop.
    # Must NOT be called inside asyncio.run() to avoid a nested loop conflict.
    from agent_framework.devui import serve

    from src.podcaster.workflow import make_workflow

    workflow = make_workflow()
    print(f"Starting Podcaster devui server on port {port}…")
    serve(
        entities=[workflow],
        port=port,
        host="127.0.0.1",
        auth_enabled=False,
        auto_open=False,
    )


async def _run_cli(question: str) -> None:
    from src.podcaster.workflow import make_workflow

    print(f"\n[Podcaster] Research question: {question}\n")
    workflow = make_workflow()
    run_result = await workflow.run(question)
    outputs = run_result.get_outputs()
    result = outputs[0] if outputs else {}
    print("\n" + "=" * 60)
    print(f"Title : {result.get('title', '')}")
    print(f"Turns : {result.get('turns', 0)}")
    print(f"Audio : {result.get('audio', '')}")
    print(f"Image : {result.get('image', '')}")
    print("=" * 60)
    print("\nScript:\n")
    for turn in result.get("script", []):
        print(f"  {turn['speaker']:6s}  {turn['text']}")
    print()


async def _run_narrate_only(script_path: str) -> None:
    from src.podcaster.agents.image_designer import run_image_designer
    from src.podcaster.agents.narrator import run_narrator
    from src.podcaster.models import PodcastScript

    path = Path(script_path)
    if not path.is_file():
        print(f"Error: script file not found: {path}", file=sys.stderr)
        sys.exit(1)

    print(f"\n[Podcaster] Narrating saved script: {path}\n")
    script = PodcastScript.model_validate_json(path.read_text(encoding="utf-8"))

    async def _narrate() -> str:
        return str(await run_narrator(script))

    async def _image() -> str:
        try:
            return str(await run_image_designer(script))
        except Exception as exc:  # noqa: BLE001 - image is optional, never fatal
            return f"[Image skipped: {exc}]"

    audio_path, image_path = await asyncio.gather(_narrate(), _image())
    print("\n" + "=" * 60)
    print(f"Title : {script.title}")
    print(f"Turns : {len(script.turns)}")
    print(f"Audio : {audio_path}")
    print(f"Image : {image_path}")
    print("=" * 60)


def main() -> None:
    args = _parse_args()

    from src.podcaster.observability import setup_observability

    setup_observability()

    if args.server:
        _run_server(args.port)
    elif args.script:
        asyncio.run(_run_narrate_only(args.script))
    else:
        if not args.question:
            print(
                "Error: --question (or --script) is required when using --cli",
                file=sys.stderr,
            )
            sys.exit(1)
        asyncio.run(_run_cli(args.question))


if __name__ == "__main__":
    main()
