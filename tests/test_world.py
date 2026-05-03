from __future__ import annotations

import threading

import pytest

from freemotion.world import WorldState, WorldStateSnapshot


def test_default_snapshot() -> None:
    snap = WorldStateSnapshot()
    assert snap.target is None
    assert snap.current_state == "idle"
    assert snap.confidence == 0.0
    assert snap.last_seen == {}
    assert snap.next_action is None
    assert snap.ts


def test_snapshot_is_frozen() -> None:
    snap = WorldStateSnapshot()
    with pytest.raises(Exception):
        snap.target = "person"  # type: ignore[misc]


def test_world_state_starts_with_default_snapshot() -> None:
    ws = WorldState()
    snap = ws.snapshot()
    assert snap.target is None
    assert snap.current_state == "idle"


def test_world_state_accepts_initial_snapshot() -> None:
    seed = WorldStateSnapshot(
        target="person", current_state="moving", confidence=0.9
    )
    ws = WorldState(initial=seed)
    snap = ws.snapshot()
    assert snap.target == "person"
    assert snap.current_state == "moving"


def test_update_replaces_named_fields_only() -> None:
    ws = WorldState()
    ws.update(current_state="armed")
    snap = ws.snapshot()
    assert snap.current_state == "armed"
    assert snap.target is None  # unchanged
    assert snap.confidence == 0.0  # unchanged


def test_update_returns_new_snapshot() -> None:
    ws = WorldState()
    snap = ws.update(current_state="moving", next_action="move")
    assert snap.current_state == "moving"
    assert snap.next_action == "move"
    assert snap is ws.snapshot()  # latest


def test_update_unknown_field_raises() -> None:
    ws = WorldState()
    with pytest.raises(TypeError):
        ws.update(does_not_exist="value")


def test_see_updates_target_and_last_seen() -> None:
    ws = WorldState()
    snap = ws.see("person", confidence=0.8)
    assert snap.target == "person"
    assert snap.confidence == 0.8
    assert "person" in snap.last_seen
    assert snap.last_seen["person"]


def test_see_does_not_mutate_prior_snapshots() -> None:
    """Holding an old snapshot must never observe a later update."""
    ws = WorldState()
    first = ws.snapshot()
    ws.see("person")
    assert first.target is None
    assert first.last_seen == {}


def test_concurrent_updates_do_not_corrupt_state() -> None:
    ws = WorldState()
    n_threads = 8
    iterations = 200
    barrier = threading.Barrier(n_threads)

    def worker(idx: int) -> None:
        barrier.wait()
        for i in range(iterations):
            ws.update(confidence=(idx * iterations + i) / 10000.0)
            ws.see(f"label-{idx}")

    threads = [
        threading.Thread(target=worker, args=(i,)) for i in range(n_threads)
    ]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    snap = ws.snapshot()
    assert snap.target.startswith("label-")  # type: ignore[union-attr]
    assert isinstance(snap.confidence, float)
    assert len(snap.last_seen) == n_threads
    for i in range(n_threads):
        assert f"label-{i}" in snap.last_seen
