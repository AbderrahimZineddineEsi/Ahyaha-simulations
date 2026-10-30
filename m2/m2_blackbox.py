"""
M2 · Blackbox crash detection: signal code shared by the training notebook and the backend.

A blackbox event is a 16 s window recorded around a trigger, like the VZCrash devices:
    acc    (N, T, 3)  accelerometer in g, 100 Hz. x forward, y LEFT, z up (reads +1 g at rest)
    gyro   (N, T, 3)  gyroscope in deg/s, 100 Hz, same axes
    speed  (N, S)     GPS speed in km/h, 1 Hz (S = 16)

What is in here:
    rule_peak_g        the paper's physical baseline: peak of the x-y acceleration norm
    rule_edr_dv        the event-recorder rule: largest speed change in any 150 ms (49 CFR 563 triggers at 8 km/h)
    window_features    one row of numbers per event, for LightGBM
    to_channels        the 7-channel array the CNN-RNN reads
    crash_summary      ΔV, impact direction, rollover, multiple impacts: the blackbox part of M3's inputs
"""
import numpy as np

HZ = 100
G = 9.81
MS_TO_KMH = 3.6
EDR_WINDOW = 15  # 150 ms at 100 Hz
# Physical limits: anything beyond is a sensor glitch (and would overflow float16)
ACC_LIMIT_G, GYRO_LIMIT_DPS, SPEED_LIMIT_KMH = 50.0, 2000.0, 300.0


def _nan_to_num(x, limit=None):
    x = np.nan_to_num(np.asarray(x, dtype=np.float32), nan=0.0, posinf=0.0, neginf=0.0)
    return np.clip(x, -limit, limit) if limit is not None else x


def _speed_at_100hz(speed, T):
    """Repeat each 1 Hz GPS sample so the speed lines up with the 100 Hz signals."""
    speed = np.asarray(speed, dtype=np.float32)
    S = speed.shape[1]
    idx = np.minimum((np.arange(T) * S) // T, S - 1)
    return speed[:, idx]


def _window_sums(x, w):
    """Sum of x over every run of w consecutive samples, along axis 1."""
    c = np.cumsum(x, axis=1, dtype=np.float64)
    c = np.concatenate([np.zeros_like(c[:, :1]), c], axis=1)
    return c[:, w:] - c[:, :-w]


# ---------------------------------------------------------------- rules

def rule_peak_g(acc):
    """Physical baseline from the VZCrash paper: the peak of sqrt(ax² + ay²), in g."""
    acc = _nan_to_num(acc)
    return np.sqrt(acc[..., 0] ** 2 + acc[..., 1] ** 2).max(axis=1)


def edr_dv_kmh(acc, w=EDR_WINDOW):
    """Largest speed change (km/h) in any w-sample window, from the x-y acceleration."""
    acc = _nan_to_num(acc)
    dvx = _window_sums(acc[..., 0], w) / HZ * G * MS_TO_KMH
    dvy = _window_sums(acc[..., 1], w) / HZ * G * MS_TO_KMH
    return np.sqrt(dvx ** 2 + dvy ** 2).max(axis=1)


rule_edr_dv = edr_dv_kmh


# ---------------------------------------------------------------- features for LightGBM

def window_features(acc, gyro, speed):
    """One row of features per event. Works on a batch; returns (names, float32 matrix)."""
    acc, gyro = _nan_to_num(acc, ACC_LIMIT_G), _nan_to_num(gyro, GYRO_LIMIT_DPS)
    gyro = np.round(gyro)  # half the devices record 1 deg/s, half 0.001 deg/s: put everyone on 1 deg/s
    speed = np.asarray(speed, dtype=np.float32)
    N, T, _ = acc.shape
    ax, ay, az = acc[..., 0], acc[..., 1], acc[..., 2] - 1.0
    xy = np.sqrt(ax ** 2 + ay ** 2)
    a3 = np.sqrt(ax ** 2 + ay ** 2 + az ** 2)
    gnorm = np.sqrt((gyro ** 2).sum(-1))
    peak_t = xy.argmax(axis=1)
    rows = np.arange(N)
    f = {}

    # Shock: how hard, how long, how sharp
    f["xy_peak_g"] = xy.max(1)
    f["xy_p99_g"] = np.percentile(xy, 99, axis=1)
    f["xy_mean_g"] = xy.mean(1)
    f["a3_peak_g"] = a3.max(1)
    f["ax_min_g"] = ax.min(1)              # strongest braking or frontal hit
    f["ax_max_g"] = ax.max(1)              # rear hit pushes the car forward
    f["ay_absmax_g"] = np.abs(ay).max(1)   # side hit
    f["az_min_g"] = az.min(1)
    f["az_max_g"] = az.max(1)              # potholes and kerbs are mostly vertical
    f["az_std_g"] = az.std(1)
    f["xy_over_1g_ms"] = (xy > 1.0).sum(1) * 10.0
    f["xy_over_05g_ms"] = (xy > 0.5).sum(1) * 10.0
    f["jerk_xy_max"] = np.abs(np.diff(xy, axis=1)).max(1) * HZ
    f["vert_ratio"] = f["az_max_g"] / (f["xy_peak_g"] + 1e-3)

    # Speed change during the impact (event-recorder style) and the direction of the peak
    f["dv150_kmh"] = edr_dv_kmh(acc, EDR_WINDOW)
    f["dv500_kmh"] = edr_dv_kmh(acc, 50)
    f["peak_dir_sin"] = ay[rows, peak_t] / (xy[rows, peak_t] + 1e-6)
    f["peak_dir_cos"] = ax[rows, peak_t] / (xy[rows, peak_t] + 1e-6)
    f["peak_time_s"] = peak_t / HZ

    # Rotation: spins, swerves, rollovers
    f["gyro_peak"] = gnorm.max(1)
    f["gyro_mean"] = gnorm.mean(1)
    for i, axis in enumerate("xyz"):
        f[f"g{axis}_absmax"] = np.abs(gyro[..., i]).max(1)
    f["roll_range_deg"] = np.ptp(np.cumsum(gyro[..., 0], 1) / HZ, axis=1)
    f["yaw_change_deg"] = np.abs(gyro[..., 2].sum(1) / HZ)
    f["upside_down_s"] = (acc[..., 2] < 0).sum(1) / HZ

    # Around the peak: calm before, chaos at, stillness after
    def around(sig, lo, hi):
        t = np.clip(peak_t[:, None] + np.arange(lo, hi)[None, :], 0, T - 1)
        return sig[rows[:, None], t]
    f["xy_std_pre2s"] = around(xy, -200, -20).std(1)
    f["xy_std_post2s"] = around(xy, 50, 250).std(1)
    f["gyro_mean_post2s"] = around(gnorm, 50, 250).mean(1)
    f["xy_std_last3s"] = xy[:, -300:].std(1)
    f["gyro_mean_last3s"] = gnorm[:, -300:].mean(1)

    # Second impact: another x-y peak over 1.5 g at least 0.5 s away from the main one
    far = np.abs(np.arange(T)[None, :] - peak_t[:, None]) > 50
    f["second_peak_g"] = np.where(far, xy, 0).max(1)

    # GPS speed: moving before, stopped after?
    sp = np.where(np.isnan(speed), np.nan, speed)
    S = sp.shape[1]
    peak_s = np.minimum(peak_t // HZ, S - 1)
    sec = np.arange(S)[None, :]
    before = np.where(sec <= peak_s[:, None], sp, np.nan)
    after = np.where(sec > peak_s[:, None], sp, np.nan)
    with np.errstate(all="ignore"), _quiet():
        f["speed_first"] = sp[:, 0]
        f["speed_max"] = np.nanmax(sp, 1)
        f["speed_before_peak"] = np.nanmax(before, 1)
        f["speed_after_min"] = np.nanmin(after, 1)
        f["speed_last"] = sp[:, -1]
        f["speed_last3_mean"] = np.nanmean(sp[:, -3:], 1)
        f["speed_drop"] = f["speed_before_peak"] - f["speed_after_min"]
        f["speed_max_decel"] = -np.nanmin(np.diff(sp, axis=1), 1)
        f["stopped_after_s"] = (after < 3).sum(1).astype(np.float32)
        f["speed_missing"] = np.isnan(sp).mean(1)

    names = list(f)
    X = np.stack([np.asarray(f[n], dtype=np.float32) for n in names], axis=1)
    return names, X


class _quiet:
    """Silence numpy's 'all-NaN slice' warnings inside feature extraction."""
    def __enter__(self):
        import warnings
        self._w = warnings.catch_warnings()
        self._w.__enter__()
        warnings.simplefilter("ignore", RuntimeWarning)

    def __exit__(self, *a):
        self._w.__exit__(*a)


# ---------------------------------------------------------------- input for the CNN-RNN

def to_channels(acc, gyro, speed):
    """(N, 7, T) float32: ax, ay, az - 1, gx, gy, gz (deg/s, rounded), speed. Not yet standardized."""
    acc, gyro = _nan_to_num(acc, ACC_LIMIT_G), _nan_to_num(gyro, GYRO_LIMIT_DPS)
    T = acc.shape[1]
    sp = _nan_to_num(_speed_at_100hz(speed, T), SPEED_LIMIT_KMH)
    x = np.concatenate([acc - np.array([0, 0, 1], np.float32), np.round(gyro), sp[..., None]], axis=-1)
    return np.ascontiguousarray(x.transpose(0, 2, 1), dtype=np.float32)


# ---------------------------------------------------------------- from a detected crash to M3's inputs

def crash_summary(acc, gyro, speed=None, pulse_ms=300, multi_g=1.5):
    """
    The blackbox part of M3's inputs, for one event (acc, gyro: (T, 3)).

    dv_kmh        speed change over the crash pulse, from the x-y acceleration (km/h)
    pdof_deg      direction the force comes FROM, clockwise from the front:
                  0 front, 90 right, 180 rear, 270 left (the driver's door in Algeria)
    rollover      1 if the car rolled past 90° or ended upside down
    multi_impact  1 if a second x-y peak over `multi_g` comes at least 0.5 s after or before the main one
    """
    acc = _nan_to_num(acc, ACC_LIMIT_G)[None]
    gyro = _nan_to_num(gyro, GYRO_LIMIT_DPS)[None]
    T = acc.shape[1]
    ax, ay = acc[0, :, 0], acc[0, :, 1]
    xy = np.sqrt(ax ** 2 + ay ** 2)
    peak = int(xy.argmax())

    # Remove the level before the crash (braking, tilt) and integrate around the peak
    pre = slice(max(0, peak - 150), max(1, peak - 50))
    w = pulse_ms // 10
    lo, hi = max(0, peak - w // 3), min(T, peak + 2 * w // 3)
    dvx = (ax[lo:hi] - ax[pre].mean()).sum() / HZ * G * MS_TO_KMH
    dvy = (ay[lo:hi] - ay[pre].mean()).sum() / HZ * G * MS_TO_KMH
    dv = float(np.hypot(dvx, dvy))

    # The car's speed change points away from the force, so the force comes from -ΔV.
    # Forward/right frame: from_fwd = -dvx, from_right = -(-dvy) = dvy  (y points left)
    pdof = float(np.degrees(np.arctan2(dvy, -dvx)) % 360)

    roll = np.cumsum(np.round(gyro[0, :, 0])) / HZ
    upside_down = (acc[0, -200:, 2] < -0.3).mean() > 0.5
    rollover = int(np.ptp(roll) > 90 or upside_down)

    far = np.abs(np.arange(T) - peak) > 50
    multi = int((xy[far] > multi_g).any())

    out = {"dv_kmh": round(dv, 1), "pdof_deg": round(pdof), "rollover": rollover, "multi_impact": multi,
           "peak_g": round(float(xy[peak]), 2)}
    if speed is not None:
        sp = np.asarray(speed, dtype=np.float32)
        if np.isfinite(sp).any():
            s_peak = min(peak // HZ, len(sp) - 1)
            with _quiet():
                out["speed_before_kmh"] = round(float(np.nanmax(sp[: s_peak + 1])), 1)
    return out
