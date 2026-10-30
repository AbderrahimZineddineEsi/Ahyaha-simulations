"""
Turns one car's live telemetry into the 16 s windows M2 scores.

Each second of data is checked for a trigger. When one fires, the window is the 8 s before it and the 8 s after
it, so the window is finished 8 s later. Nothing here touches the network, so the same logic could run on a real
blackbox. Why these numbers: measured on real VZCrash events, a 1 g trigger fires on 99% of crashes (1.5 g: 96%),
and with the trigger at 8 s M2 catches as many crashes as on the original windows.
"""
from collections import deque
from dataclasses import dataclass
from datetime import datetime

import numpy as np

HZ = 100
TRIGGER_G = 1.0
BEFORE_S, AFTER_S = 8, 8
COOLDOWN_S = 8  # shaking after a crash must not start a second event
MAX_GAP_S = 16  # a longer gap means a new drive, not lost messages

BEFORE, AFTER, COOLDOWN = BEFORE_S * HZ, AFTER_S * HZ, COOLDOWN_S * HZ
MAX_BATCHES = BEFORE_S + AFTER_S + 2  # a window can overlap two partial seconds


def find_trigger(acc):
    """Index of the first sample whose x-y acceleration is over TRIGGER_G, or None. acc: (n, 3) in g."""
    over = np.hypot(acc[:, 0], acc[:, 1]) > TRIGGER_G
    return int(over.argmax()) if over.any() else None


@dataclass
class Second:
    start: int  # sample number since the stream began
    acc: np.ndarray
    gyro: np.ndarray
    speed: float
    filled: bool = False
    seq: int | None = None
    recorded_at: datetime | None = None
    location: dict | None = None


@dataclass
class Window:
    acc: np.ndarray    # (1600, 3)
    gyro: np.ndarray   # (1600, 3)
    speed: np.ndarray  # (16,), NaN where GPS is missing
    trigger: Second    # the second the trigger fired in
    filled_s: float    # seconds of the window that weren't received
    stream_ended: bool  # the stream stopped before the 8 s after the trigger were complete


class DeviceStream:
    def __init__(self):
        self.seconds = deque(maxlen=MAX_BATCHES)
        self.samples = 0
        self.last_seq = None
        self.trigger_at = None
        self.trigger_second = None
        self.armed_from = 0

    @property
    def collecting(self):
        return self.trigger_at is not None

    def progress(self):
        """While collecting: whole seconds held from before the trigger, and seconds still to collect."""
        before = min(BEFORE_S, self.trigger_at // HZ)
        return before, max(0, -(-(self.trigger_at + AFTER - self.samples) // HZ))

    def push(self, seq, acc, gyro, speed, recorded_at=None, location=None):
        """Adds one second of data. Returns the windows it completed: usually none."""
        windows = []
        if self.last_seq is not None:
            gap = seq - self.last_seq - 1
            if gap < 0 or gap > MAX_GAP_S:  # the simulator restarted, or the car was gone for a long time
                windows += self.finish()
                self.__init__()
            else:
                for _ in range(gap):
                    windows += self._add_filler()
        self.last_seq = seq
        second = Second(self.samples, acc, gyro, speed, seq=seq, recorded_at=recorded_at, location=location)
        return windows + self._add(second)

    def finish(self):
        """The stream stopped: completes a window in progress with filler, so a crash is never dropped."""
        windows = []
        while self.collecting:
            windows += self._add_filler(stream_ended=True)
        return windows

    def _add_filler(self, stream_ended=False):
        """A lost second: the car sitting still in its last position, with no GPS."""
        last = self.seconds[-1]
        acc = np.tile(np.median(last.acc, axis=0), (HZ, 1)).astype(np.float32)
        gyro = np.zeros((HZ, 3), np.float32)
        return self._add(Second(self.samples, acc, gyro, np.nan, filled=True), stream_ended)

    def _add(self, second, stream_ended=False):
        self.seconds.append(second)
        self.samples += HZ
        if not self.collecting and not second.filled:
            self._check_trigger(second)
        if self.collecting and self.samples >= self.trigger_at + AFTER:
            return [self._cut(stream_ended)]
        return []

    def _check_trigger(self, second):
        skip = max(0, self.armed_from - second.start)
        found = find_trigger(second.acc[skip:]) if skip < HZ else None
        if found is not None:
            self.trigger_at = second.start + skip + found
            self.trigger_second = second

    def _cut(self, stream_ended):
        start = self.trigger_at - BEFORE
        seconds = list(self.seconds)
        acc = np.concatenate([s.acc for s in seconds])
        gyro = np.concatenate([s.gyro for s in seconds])
        speed = np.repeat([s.speed for s in seconds], HZ)
        filled = np.repeat([s.filled for s in seconds], HZ)

        # A trigger soon after the car connects has less than 8 s behind it: hold the first sample
        missing = max(0, seconds[0].start - start)
        if missing:
            acc = np.concatenate([np.repeat(acc[:1], missing, 0), acc])
            gyro = np.concatenate([np.repeat(gyro[:1], missing, 0), gyro])
            speed = np.concatenate([np.repeat(speed[:1], missing), speed])
            filled = np.concatenate([np.ones(missing, bool), filled])

        i = start - seconds[0].start + missing
        j = i + BEFORE + AFTER
        window = Window(acc[i:j], gyro[i:j], speed[i:j:HZ].astype(np.float32), self.trigger_second,
                        round(float(filled[i:j].mean()) * (BEFORE_S + AFTER_S), 2), stream_ended)
        self.armed_from = self.trigger_at + AFTER + COOLDOWN
        self.trigger_at = self.trigger_second = None
        return window
