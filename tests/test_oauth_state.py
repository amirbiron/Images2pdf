"""ה-state של OAuth: נצרך פעם אחת בלבד, גם תחת מקביליות."""

from __future__ import annotations

import os
import threading
import time

import pytest

from oauth_state import OAuthStateStore


def test_state_is_single_use(data_dir):
    store = OAuthStateStore(data_dir)
    state = store.create()
    assert store.consume(state) is True
    assert store.consume(state) is False


def test_unknown_and_malformed_states_are_rejected(data_dir):
    store = OAuthStateStore(data_dir)
    for value in (None, 123, ["x"], "", "short", "../../etc/passwd", "x" * 500):
        assert store.consume(value) is False


def test_expired_state_is_rejected(data_dir, monkeypatch):
    store = OAuthStateStore(data_dir, ttl_seconds=600)
    state = store.create()
    real_time = time.time
    monkeypatch.setattr("oauth_state.time.time", lambda: real_time() + 601)
    assert store.consume(state) is False


def test_state_file_name_is_a_hash_not_the_state(data_dir):
    store = OAuthStateStore(data_dir)
    state = store.create()
    names = os.listdir(data_dir / "oauth_states")
    assert len(names) == 1
    assert state not in names[0]


def test_parallel_consumers_only_one_wins(data_dir):
    """20 threads צורכים את אותו state באותו רגע — בדיוק אחד מצליח."""
    for _ in range(20):
        store = OAuthStateStore(data_dir)
        state = store.create()
        barrier = threading.Barrier(20)
        wins: list[bool] = []
        lock = threading.Lock()

        def consume():
            barrier.wait()
            result = store.consume(state)
            with lock:
                wins.append(result)

        threads = [threading.Thread(target=consume) for _ in range(20)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        assert wins.count(True) == 1, wins


@pytest.mark.parametrize("age", [0, 300])
def test_fresh_state_within_ttl_is_accepted(data_dir, monkeypatch, age):
    store = OAuthStateStore(data_dir, ttl_seconds=600)
    state = store.create()
    real_time = time.time
    monkeypatch.setattr("oauth_state.time.time", lambda: real_time() + age)
    assert store.consume(state) is True
