"""Shared settings for the Hub 2 refresh manager."""
DEFAULT_POLL_INTERVAL = 60


def poll_interval(value):
    """Accept whole seconds only, including integral values from NumberSelector."""
    if type(value) not in (int, float) or not 60 <= value <= 3600 or int(value) != value:
        raise ValueError("invalid_poll_interval")
    return int(value)
