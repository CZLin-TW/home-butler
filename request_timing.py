"""Request-local, content-free timing for voice routes; no external I/O or retries."""

from contextlib import contextmanager
from contextvars import ContextVar
import json
from time import perf_counter
from uuid import uuid4


_request = ContextVar("voice_request_timing", default=None)
_span = ContextVar("voice_timing_span", default=None)


def _emit(trace, event, **fields):
    # Logging must not turn an accepted command into an HTTP error or a retry.
    try:
        print("[TIMING] " + json.dumps({**trace, "event": event, **fields},
                                      ensure_ascii=False), flush=True)
    except Exception:
        pass


@contextmanager
def request_timing(source):
    trace = {"request_id": uuid4().hex, "source": source}
    token = _request.set(trace)
    span_token = _span.set(None)
    start = perf_counter()
    status = "completed"
    _emit(trace, "request_start")
    try:
        yield
    except BaseException:
        status = "error"
        raise
    finally:
        _emit(trace, "request_end", status=status,
              duration_ms=round((perf_counter() - start) * 1000, 2))
        _request.reset(token)
        _span.reset(span_token)


@contextmanager
def timing_stage(stage):
    """Use code-owned stage names only, never text, identity, URLs or parameters."""
    trace = _request.get()
    if trace is None:
        yield
        return
    span = uuid4().hex[:12]
    parent_span = _span.get()
    token = _span.set(span)
    start = perf_counter()
    status = "completed"
    fields = {}
    _emit(trace, "stage_start", stage=stage, span_id=span, parent_span_id=parent_span)
    try:
        yield
    except BaseException as exc:
        status = "error"
        fields["error_type"] = type(exc).__name__
        raise
    finally:
        _emit(trace, "stage_end", stage=stage, span_id=span, status=status,
              parent_span_id=parent_span,
              duration_ms=round((perf_counter() - start) * 1000, 2), **fields)
        _span.reset(token)


def timed_call(stage, call, *args, **kwargs):
    with timing_stage(stage):
        return call(*args, **kwargs)


def timed_model_call(stage, create, **kwargs):
    """Time the existing SDK call including its retries; never add a model call."""
    with timing_stage(stage):
        response = create(**kwargs)
    trace = _request.get()
    if trace is not None:
        # No prompts, response text, thinking content or exception messages.
        try:
            usage = getattr(response, "usage", None)
            fields = {key: getattr(usage, key, None) for key in (
                "input_tokens", "output_tokens", "cache_read_input_tokens",
                "cache_creation_input_tokens")}
            _emit(trace, "model_usage", stage=stage,
                  stop_reason=getattr(response, "stop_reason", None), **fields)
        except Exception:
            pass
    return response
