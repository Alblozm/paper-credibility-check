"""Lets a running check stop waiting for a provider call that is still in flight.

A caller registers a stop test for its context (``watch``). A blocking call made under it (``call``) runs on a
helper thread while the caller re-runs the test every half second, so a stop takes effect within a second instead of
after a model call that can take minutes. The abandoned request finishes in the background and its answer is
dropped; nothing from it is recorded. Without a registered test the call simply runs in place.
"""
from __future__ import annotations

import contextvars
import threading
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from typing import TypeVar

T = TypeVar("T")

_stop_test: contextvars.ContextVar[Callable[[], None] | None] = contextvars.ContextVar("llm_stop_test", default=None)
_POLL_SECONDS = 0.5


@contextmanager
def watch(test: Callable[[], None]) -> Iterator[None]:
    """``test`` raises to stop; it runs before each provider call and while the call is waited on."""
    token = _stop_test.set(test)
    try:
        yield
    finally:
        _stop_test.reset(token)


def call(send: Callable[[], T]) -> T:
    test = _stop_test.get()
    if test is None:
        return send()
    test()
    outcome: dict[str, object] = {}
    context = contextvars.copy_context()

    def run() -> None:
        try:
            outcome["value"] = context.run(send)
        except BaseException as exc:  # re-raised in the caller, unchanged
            outcome["error"] = exc

    worker = threading.Thread(target=run, name="llm-call", daemon=True)
    worker.start()
    while worker.is_alive():
        worker.join(_POLL_SECONDS)
        if worker.is_alive():
            test()
    if "error" in outcome:
        raise outcome["error"]  # type: ignore[misc]
    return outcome["value"]  # type: ignore[return-value]
