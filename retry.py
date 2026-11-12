"""Retry table from the L4 lecture, as code.

Transient failures (timeouts, 429 rate limit, 5xx) are retried a few times with
back-off. Permanent failures (bad key, auth refused, 4xx other than 429) are
raised at once: retrying them changes nothing.
"""
import time


class PermanentError(Exception):
    """Do not retry: retrying this exact call cannot succeed."""


def with_retry(fn, *, attempts: int = 3, base_delay: float = 2.0, transient: tuple = (Exception,)):
    last = None
    for i in range(attempts):
        try:
            return fn()
        except PermanentError:
            raise
        except transient as e:
            last = e
            if i < attempts - 1:
                time.sleep(base_delay * (2 ** i))
    raise last
