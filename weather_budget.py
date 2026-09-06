"""A shared deadline across location lookup, forecast and observation requests."""
from contextvars import ContextVar
from functools import wraps
from time import monotonic

_deadline = ContextVar("weather_deadline", default=None)


class WeatherTimeout(TimeoutError):
    pass


def remaining_timeout(default=15):
    deadline = _deadline.get()
    if deadline is None:
        return default
    remaining = deadline - monotonic()
    if remaining <= 0:
        raise WeatherTimeout("天氣服務回應逾時，請稍後重試。")
    return min(default, remaining)


def weather_query(fn):
    @wraps(fn)
    def bounded(*args, **kwargs):
        token = _deadline.set(monotonic() + 10)
        try:
            return fn(*args, **kwargs)
        except WeatherTimeout as exc:
            return {"error": str(exc), "status_code": 504}
        finally:
            _deadline.reset(token)
    return bounded
