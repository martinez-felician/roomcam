import time

import numpy as np

from roomcam_laptop.api import MemoryStore
from roomcam_laptop.snapshots import SnapshotStore


def test_snapshots_are_encrypted_and_deletable(tmp_path):
    store = SnapshotStore(tmp_path, MemoryStore())
    frame = np.zeros((120, 160, 3), np.uint8)
    frame[:, :80] = 255
    s1 = store.save(frame, ts=time.time() - 10 * 86400)
    s2 = store.save(frame)
    raw = s2.path.read_bytes()
    assert b"JFIF" not in raw[:64]  # no es un JPEG legible sin la clave
    assert store.load(s2).shape == frame.shape
    assert store.purge_older_than(7) == 1 and not s1.path.exists()
    store.delete(s2)
    assert store.list() == []
    store.save(frame)
    store.save(frame, ts=time.time() + 1)
    assert store.delete_all() == 2
