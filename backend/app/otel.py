"""Optional OpenTelemetry export: one trace per failed check, with a child span per timing phase.

Active only when OTEL_EXPORTER_OTLP_ENDPOINT is set and the opentelemetry SDK and OTLP/HTTP
exporter are installed (pip install opentelemetry-sdk opentelemetry-exporter-otlp-proto-http).
"""
import logging
import os
import time

log = logging.getLogger(__name__)

PHASES = ("dns", "connect", "tls", "ttfb", "download")
_tracer = None
_tried = False


def _get_tracer():
    global _tracer, _tried
    if _tried:
        return _tracer
    _tried = True
    if not os.environ.get("OTEL_EXPORTER_OTLP_ENDPOINT"):
        return None
    try:
        from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
        from opentelemetry.sdk.resources import Resource
        from opentelemetry.sdk.trace import TracerProvider
        from opentelemetry.sdk.trace.export import BatchSpanProcessor
    except ImportError:
        log.warning("OTEL_EXPORTER_OTLP_ENDPOINT is set but the opentelemetry SDK is not installed")
        return None
    # The exporter reads the endpoint and headers from the standard OTEL_* variables
    provider = TracerProvider(resource=Resource.create(
        {"service.name": os.environ.get("OTEL_SERVICE_NAME", "myappsuptime")}))
    provider.add_span_processor(BatchSpanProcessor(OTLPSpanExporter()))
    _tracer = provider.get_tracer("myappsuptime.checks")
    return _tracer


def export_failed_check(name: str, target: str | None, status: str, message: str, timings: dict | None) -> None:
    try:
        tracer = _get_tracer()
        if tracer is None:
            return
        from opentelemetry.trace import Status, StatusCode

        timings = timings or {}
        end = time.time_ns()
        start = end - int(timings.get("total", 0) * 1_000_000)
        span = tracer.start_span(f"check {name}", start_time=start, attributes={
            "monitor.name": name, "monitor.target": target or "", "check.status": status,
            "check.message": message[:300]})
        span.set_status(Status(StatusCode.ERROR, message[:300]))
        cursor = start
        for phase in PHASES:
            if phase in timings:
                length = int(timings[phase] * 1_000_000)
                child = tracer.start_span(phase, context=_context(span), start_time=cursor)
                child.end(end_time=cursor + length)
                cursor += length
        span.end(end_time=end)
    except Exception:  # telemetry must never break checks
        log.debug("OpenTelemetry export failed", exc_info=True)


def _context(span):
    from opentelemetry.trace import set_span_in_context
    return set_span_in_context(span)
