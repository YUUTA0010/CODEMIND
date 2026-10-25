"""Background job execution. Long-running work (indexing, verification, experiments) runs in a bounded thread pool."""
from __future__ import annotations

import logging
from concurrent.futures import Future, ThreadPoolExecutor
from typing import Any, Callable

log = logging.getLogger("codemind.jobs")

_executor = ThreadPoolExecutor(max_workers=3, thread_name_prefix="codemind-job")
_inline = False  # tests may set this to run jobs synchronously


def set_inline(value: bool) -> None:
    global _inline
    _inline = value


def submit(fn: Callable[..., Any], *args: Any, **kwargs: Any) -> Future:
    if _inline:
        fut: Future = Future()
        try:
            fut.set_result(fn(*args, **kwargs))
        except Exception as e:  # noqa: BLE001
            fut.set_exception(e)
        return fut

    def runner():
        try:
            return fn(*args, **kwargs)
        except Exception:
            log.exception("background job failed")
            raise

    return _executor.submit(runner)
