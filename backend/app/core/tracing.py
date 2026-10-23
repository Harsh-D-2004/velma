"""Phoenix tracing (OpenTelemetry + OpenInference). Off unless PHOENIX_COLLECTOR_ENDPOINT is set."""
import logging
import os
from contextlib import contextmanager

log = logging.getLogger("velma.tracing")
_provider = None
_tracer = None


def setup() -> None:
    global _provider, _tracer
    if not os.getenv("PHOENIX_COLLECTOR_ENDPOINT"):
        return
    try:
        from phoenix.otel import register
        _provider = register(project_name=os.getenv("PHOENIX_PROJECT_NAME", "velma"),
                             auto_instrument=True, batch=True)
        _tracer = _provider.get_tracer("velma")
    except Exception as e:  # tracing must never break a job
        log.warning("tracing disabled: %s", e)
        _provider = _tracer = None
        return
    # exporter errors are logged by the otel SDK; keep the noise to one line per failure
    logging.getLogger("opentelemetry.sdk._shared_internal").setLevel(logging.ERROR)
    logging.getLogger("opentelemetry.exporter").setLevel(logging.ERROR)


def shutdown() -> None:
    if _provider is not None:
        try:
            _provider.shutdown()
        except Exception as e:
            log.warning("tracing shutdown failed: %s", e)


class _Span:
    """Handle yielded by span(); every method is a no-op when tracing is off."""

    def __init__(self, s=None):
        self._s = s

    def output(self, text: str) -> None:
        if self._s is not None:
            self._s.set_attribute("output.value", str(text)[:2000])

    def error(self, msg: str) -> None:
        if self._s is not None:
            from opentelemetry.trace import Status, StatusCode
            self._s.set_status(Status(StatusCode.ERROR, str(msg)[:200]))


@contextmanager
def span(name: str, kind: str = "TOOL", input: str | None = None, **attrs):
    if _tracer is None:
        yield _Span()
        return
    a = {"openinference.span.kind": kind, **attrs}
    if input is not None:
        a["input.value"] = str(input)[:2000]
    with _tracer.start_as_current_span(name, attributes=a) as s:
        yield _Span(s)
