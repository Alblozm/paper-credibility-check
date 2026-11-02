from __future__ import annotations

import threading
import time

import pytest

from app.llm import interrupt


class Stop(BaseException):
    pass


def test_without_a_watch_the_call_runs_in_place():
    assert interrupt.call(threading.current_thread) is threading.current_thread()


def test_a_stop_abandons_a_call_that_is_still_in_flight():
    release = threading.Event()
    stopped = threading.Event()

    def test():
        if stopped.is_set():
            raise Stop

    threading.Timer(0.2, stopped.set).start()
    started = time.monotonic()
    with interrupt.watch(test), pytest.raises(Stop):
        interrupt.call(lambda: release.wait(10))
    assert time.monotonic() - started < 2
    release.set()


def test_errors_and_results_of_the_call_pass_through_unchanged():
    with interrupt.watch(lambda: None):
        assert interrupt.call(lambda: 42) == 42
        with pytest.raises(ValueError, match="provider"):
            interrupt.call(lambda: (_ for _ in ()).throw(ValueError("provider")))
