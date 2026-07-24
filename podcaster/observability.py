"""Central logging + OpenTelemetry setup for the podcaster app.

Call :func:`setup_observability` once at process startup (``main.py`` for the
CLI/devui server, ``server.py`` for the FastAPI/AG-UI server). It is idempotent,
so it is safe if both paths run in the same process.

Two layers are configured:

1. **Standard Python logging** — a readable console format at ``LOG_LEVEL``
   (default ``INFO``). Every agent/executor logs start/finish and timing so a
   run's progress (and any retry backoff) is visible in the terminal.
2. **OpenTelemetry** (optional, ``ENABLE_OTEL=true``) — the Microsoft Agent
   Framework emits traces, metrics and logs for every agent, chat client and
   workflow step. Exporters are picked up from the standard ``OTEL_EXPORTER_*``
   environment variables, an optional console exporter, an optional VS Code
   extension port, and an optional Azure Monitor connection string.
"""

from __future__ import annotations

import logging

from podcaster import config

_configured = False

# Third-party loggers that are noisy at INFO/DEBUG and drown out our own logs.
_NOISY_LOGGERS = (
    "azure",
    "azure.identity",
    "azure.core.pipeline.policies.http_logging_policy",
    "httpx",
    "httpcore",
    "urllib3",
    "openai",
)


def setup_observability() -> None:
    """Configure logging and (optionally) OpenTelemetry. Idempotent."""
    global _configured
    if _configured:
        return
    _configured = True

    level = getattr(logging, config.LOG_LEVEL, logging.INFO)
    logging.basicConfig(
        level=level,
        format="%(asctime)s %(levelname)-8s %(name)s: %(message)s",
        datefmt="%H:%M:%S",
    )
    for name in _NOISY_LOGGERS:
        logging.getLogger(name).setLevel(logging.WARNING)

    log = logging.getLogger("podcaster")
    log.debug("Logging configured at level %s", config.LOG_LEVEL)

    if not config.ENABLE_OTEL:
        log.info("OpenTelemetry disabled (set ENABLE_OTEL=true to enable tracing).")
        return

    _configure_otel(log)


def _configure_otel(log: logging.Logger) -> None:
    """Enable Microsoft Agent Framework OpenTelemetry instrumentation."""
    # Optional Azure Monitor / Application Insights export. Configured first so
    # its provider is registered before the agent framework wires in its own.
    if config.APPLICATIONINSIGHTS_CONNECTION_STRING:
        try:
            from azure.monitor.opentelemetry import configure_azure_monitor

            configure_azure_monitor(
                connection_string=config.APPLICATIONINSIGHTS_CONNECTION_STRING
            )
            log.info("Azure Monitor (Application Insights) exporter enabled.")
        except ImportError as exc:  # pragma: no cover - optional dependency
            log.warning(
                "APPLICATIONINSIGHTS_CONNECTION_STRING is set but Azure Monitor "
                "could not be configured (%s). Install 'azure-monitor-opentelemetry'.",
                exc,
            )

    try:
        from agent_framework.observability import configure_otel_providers
    except ImportError as exc:  # pragma: no cover - import guard
        log.warning("Agent Framework observability unavailable: %s", exc)
        return

    kwargs: dict[str, object] = {}
    if config.OTEL_CONSOLE:
        kwargs["enable_console_exporters"] = True
    if config.VS_CODE_EXTENSION_PORT is not None:
        kwargs["vs_code_extension_port"] = config.VS_CODE_EXTENSION_PORT

    try:
        configure_otel_providers(**kwargs)
        log.info("OpenTelemetry instrumentation enabled.")
    except (AttributeError, RuntimeError, TypeError, ValueError) as exc:  # pragma: no cover - defensive
        log.warning("Failed to configure OpenTelemetry providers: %s", exc)
