import numpy as np

from roomcam_laptop.motion import MotionDetector, MotionSettings


class FakeClock:
    def __init__(self):
        self.t = 1000.0

    def __call__(self):
        return self.t


def still(value=100):
    return np.full((360, 640, 3), value, np.uint8)


def with_box(x, y, w=160, h=160):
    f = still()
    f[y:y + h, x:x + w] = 250
    return f


def feed(det, frames):
    return [det.process(f) for f in frames]


def test_still_scene_never_alerts():
    det = MotionDetector(MotionSettings(confirm_frames=2), clock=FakeClock())
    results = feed(det, [still()] * 20)
    assert not any(r.alert for r in results)


def test_movement_alerts_after_confirmation():
    det = MotionDetector(MotionSettings(confirm_frames=3), clock=FakeClock())
    feed(det, [still()] * 3)
    results = feed(det, [with_box(100 + i * 20, 100) for i in range(4)])
    assert [r.alert for r in results][:3] == [False, False, True]


def test_short_flicker_is_ignored():
    det = MotionDetector(MotionSettings(confirm_frames=3), clock=FakeClock())
    feed(det, [still()] * 3)
    results = feed(det, [with_box(100, 100), still(), still(), with_box(300, 100), still()])
    assert not any(r.alert for r in results)


def test_cooldown_blocks_repeated_alerts():
    clock = FakeClock()
    det = MotionDetector(MotionSettings(confirm_frames=1, cooldown_seconds=60), clock=clock)
    feed(det, [still()])
    assert det.process(with_box(100, 100)).alert
    clock.t += 30
    assert not det.process(with_box(300, 100)).alert
    clock.t += 31
    assert det.process(with_box(100, 150)).alert


def test_zone_ignores_motion_outside():
    det = MotionDetector(MotionSettings(confirm_frames=1, zone=(0.6, 0.0, 0.4, 1.0)), clock=FakeClock())
    feed(det, [still()])
    # Movimiento en la mitad izquierda: fuera de la zona.
    assert not any(r.alert for r in feed(det, [with_box(20, 100), with_box(60, 100)]))
    # Movimiento dentro de la zona.
    assert det.process(with_box(460, 100)).alert


def test_low_sensitivity_ignores_small_change():
    small = still()
    small[100:130, 100:130] = 140  # cambio pequeño y suave
    low = MotionDetector(MotionSettings(sensitivity=1, confirm_frames=1), clock=FakeClock())
    high = MotionDetector(MotionSettings(sensitivity=100, confirm_frames=1), clock=FakeClock())
    feed(low, [still()])
    feed(high, [still()])
    assert not low.process(small).moving
    assert high.process(small).moving
