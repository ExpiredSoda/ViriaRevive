"""Local acoustic voice-profile helpers.

This is a lightweight diagnostic profile, not biometric authentication.
It stores only a small numeric centroid and never needs to keep raw audio.
"""

from __future__ import annotations

import math
import wave
from datetime import datetime, timezone
from pathlib import Path

import numpy as np


VOICE_PROFILE_SCHEMA_VERSION = 1
VOICE_PROFILE_FEATURE_VERSION = 1
VOICE_PROFILE_FEATURE_COUNT = 8
MIN_ACTIVE_SECONDS = 1.5
MIN_VOICE_PROFILE_SAMPLES = 3
MIN_VOICE_PROFILE_TOTAL_ACTIVE_SECONDS = 8.0


def voice_profile_ready(profile: dict | None) -> bool:
    profile = sanitize_voice_profile(profile)
    return bool(
        profile.get("enabled")
        and profile.get("enrolled")
        and int(profile.get("sample_count") or 0) >= MIN_VOICE_PROFILE_SAMPLES
        and float(profile.get("total_active_seconds") or 0.0) >= MIN_VOICE_PROFILE_TOTAL_ACTIVE_SECONDS
    )


def empty_voice_profile(enabled: bool = False) -> dict:
    return {
        "schema_version": VOICE_PROFILE_SCHEMA_VERSION,
        "feature_version": VOICE_PROFILE_FEATURE_VERSION,
        "enabled": bool(enabled),
        "enrolled": False,
        "sample_count": 0,
        "total_active_seconds": 0.0,
        "centroid": [],
        "updated_at": "",
    }


def sanitize_voice_profile(data: dict | None) -> dict:
    if not isinstance(data, dict):
        return empty_voice_profile()
    enabled = bool(data.get("enabled", False))
    profile = empty_voice_profile(enabled=enabled)
    centroid = data.get("centroid")
    sample_count = _safe_int(data.get("sample_count"), 0)
    if (
        isinstance(centroid, list)
        and len(centroid) == VOICE_PROFILE_FEATURE_COUNT
        and sample_count > 0
        and _safe_int(data.get("feature_version"), 0) == VOICE_PROFILE_FEATURE_VERSION
    ):
        try:
            profile["centroid"] = [round(float(v), 6) for v in centroid]
            profile["sample_count"] = sample_count
            profile["total_active_seconds"] = round(max(0.0, float(data.get("total_active_seconds") or 0.0)), 3)
            profile["updated_at"] = str(data.get("updated_at") or "")
            profile["enrolled"] = True
        except (TypeError, ValueError):
            return empty_voice_profile(enabled=enabled)
    return profile


def voice_profile_status(profile: dict | None, *, file_exists: bool = False, size_bytes: int = 0) -> dict:
    profile = sanitize_voice_profile(profile)
    enabled = bool(profile.get("enabled"))
    enrolled = bool(profile.get("enrolled"))
    ready = voice_profile_ready(profile)
    if not enabled:
        readiness = "off"
        status_label = "Off"
        next_action = "enable_voice_profile"
    elif not enrolled:
        readiness = "needs_samples"
        status_label = "Needs samples"
        next_action = "build_from_current_clips"
    elif not ready:
        readiness = "collecting_samples"
        status_label = "Needs more samples"
        next_action = "build_from_current_clips"
    else:
        readiness = "ready"
        status_label = "Ready"
        next_action = "enable_voice_ranking"
    return {
        "schema_version": VOICE_PROFILE_SCHEMA_VERSION,
        "feature_version": VOICE_PROFILE_FEATURE_VERSION,
        "enabled": enabled,
        "enrolled": enrolled,
        "sample_count": int(profile.get("sample_count") or 0),
        "total_active_seconds": round(float(profile.get("total_active_seconds") or 0.0), 3),
        "min_samples_for_ranking": MIN_VOICE_PROFILE_SAMPLES,
        "min_active_seconds_for_ranking": MIN_VOICE_PROFILE_TOTAL_ACTIVE_SECONDS,
        "updated_at": profile.get("updated_at", ""),
        "file_exists": bool(file_exists),
        "size_bytes": int(size_bytes or 0),
        "local_only": True,
        "stores_raw_audio": False,
        "selection_impact": "none",
        "confidence": None,
        "reason": (
            "disabled"
            if not enabled
            else "ready"
            if ready
            else "needs_more_samples"
            if enrolled
            else "not_enrolled"
        ),
        "readiness": readiness,
        "status_label": status_label,
        "next_action": next_action,
        "can_score": ready,
        "can_rank": False,
        "influence_state": "not_influencing",
    }


def extract_voice_features(wav_path: str | Path) -> dict:
    path = Path(wav_path)
    if not path.exists():
        return _feature_error("missing_wav")
    try:
        with wave.open(str(path), "rb") as wav:
            channels = max(1, int(wav.getnchannels()))
            rate = int(wav.getframerate())
            sample_width = int(wav.getsampwidth())
            frames = int(wav.getnframes())
            raw = wav.readframes(frames)
    except (wave.Error, OSError) as exc:
        return _feature_error("wav_read_failed", str(exc))

    if sample_width != 2 or rate <= 0 or not raw:
        return _feature_error("unsupported_wav_format")
    samples = np.frombuffer(raw, dtype=np.int16).astype(np.float32)
    if samples.size == 0:
        return _feature_error("empty_wav")
    if channels > 1:
        samples = samples[: samples.size - (samples.size % channels)]
        samples = samples.reshape(-1, channels).mean(axis=1)
    samples = samples / 32768.0
    duration = float(samples.size) / float(rate)
    if duration < 0.5:
        return _feature_error("too_short", duration=duration)

    frame_size = max(128, int(rate * 0.025))
    hop = max(64, int(rate * 0.010))
    if samples.size < frame_size:
        return _feature_error("too_short", duration=duration)

    starts = range(0, samples.size - frame_size + 1, hop)
    frames_np = np.stack([samples[start:start + frame_size] for start in starts])
    rms = np.sqrt(np.mean(np.square(frames_np), axis=1))
    active_threshold = max(0.008, float(np.percentile(rms, 60)) * 0.45)
    active = frames_np[rms >= active_threshold]
    active_rms = rms[rms >= active_threshold]
    active_seconds = float(active.shape[0] * hop) / float(rate)
    if active.shape[0] < 3 or active_seconds < MIN_ACTIVE_SECONDS:
        return _feature_error("not_enough_active_voice", duration=duration, active_seconds=active_seconds)

    window = np.hanning(frame_size).astype(np.float32)
    spectra = np.abs(np.fft.rfft(active * window, axis=1)) + 1e-8
    freqs = np.fft.rfftfreq(frame_size, d=1.0 / float(rate))
    energy = np.sum(spectra, axis=1)
    centroid = np.sum(spectra * freqs, axis=1) / energy
    bandwidth = np.sqrt(np.sum(spectra * np.square(freqs - centroid[:, None]), axis=1) / energy)
    cumulative = np.cumsum(spectra, axis=1)
    rolloff_idx = np.argmax(cumulative >= (energy[:, None] * 0.85), axis=1)
    rolloff = freqs[rolloff_idx]
    flatness = np.exp(np.mean(np.log(spectra), axis=1)) / np.mean(spectra, axis=1)
    zcr = np.mean(np.diff(np.signbit(active), axis=1), axis=1)

    rms_db = 20.0 * np.log10(active_rms + 1e-8)
    features = [
        _clip01((float(np.mean(rms_db)) + 60.0) / 60.0),
        _clip01(float(np.std(active_rms)) * 10.0),
        _clip01(float(np.mean(zcr)) * 2.0),
        _clip01(float(np.mean(centroid)) / 8000.0),
        _clip01(float(np.std(centroid)) / 4000.0),
        _clip01(float(np.mean(bandwidth)) / 8000.0),
        _clip01(float(np.mean(rolloff)) / 8000.0),
        _clip01(float(np.mean(flatness))),
    ]
    return {
        "ok": True,
        "reason": "ok",
        "features": [round(v, 6) for v in features],
        "duration": round(duration, 3),
        "active_seconds": round(active_seconds, 3),
        "feature_version": VOICE_PROFILE_FEATURE_VERSION,
    }


def update_voice_profile(profile: dict | None, features: list[float], *, active_seconds: float = 0.0) -> dict:
    profile = sanitize_voice_profile(profile)
    vector = _clean_vector(features)
    if vector is None:
        return profile
    old_count = int(profile.get("sample_count") or 0)
    if old_count > 0:
        old = np.asarray(profile.get("centroid"), dtype=np.float32)
        new = ((old * old_count) + np.asarray(vector, dtype=np.float32)) / float(old_count + 1)
        centroid = [round(float(v), 6) for v in new.tolist()]
    else:
        centroid = vector
    profile.update(
        {
            "enabled": True,
            "enrolled": True,
            "sample_count": old_count + 1,
            "total_active_seconds": round(float(profile.get("total_active_seconds") or 0.0) + max(0.0, float(active_seconds or 0.0)), 3),
            "centroid": centroid,
            "updated_at": datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z"),
            "feature_version": VOICE_PROFILE_FEATURE_VERSION,
            "schema_version": VOICE_PROFILE_SCHEMA_VERSION,
        }
    )
    return profile


def score_voice_profile(profile: dict | None, features: list[float] | None) -> dict:
    profile = sanitize_voice_profile(profile)
    base = {
        "schema_version": VOICE_PROFILE_SCHEMA_VERSION,
        "feature_version": VOICE_PROFILE_FEATURE_VERSION,
        "enabled": bool(profile.get("enabled")),
        "enrolled": bool(profile.get("enrolled")),
        "sample_count": int(profile.get("sample_count") or 0),
        "confidence": None,
        "distance": None,
        "reason": "disabled",
        "selection_impact": "none",
        "diagnostic_only": True,
    }
    if not base["enabled"]:
        return base
    if not base["enrolled"]:
        base["reason"] = "not_enrolled"
        return base
    if not voice_profile_ready(profile):
        base["reason"] = "needs_more_samples"
        return base
    vector = _clean_vector(features)
    centroid = _clean_vector(profile.get("centroid"))
    if vector is None or centroid is None:
        base["reason"] = "no_features"
        return base
    distance = math.sqrt(sum((a - b) ** 2 for a, b in zip(vector, centroid)) / VOICE_PROFILE_FEATURE_COUNT)
    confidence = _clip01(1.0 - (distance / 0.42))
    base.update(
        {
            "confidence": round(float(confidence), 4),
            "distance": round(float(distance), 4),
            "reason": "scored",
        }
    )
    return base


def _clean_vector(values) -> list[float] | None:
    if not isinstance(values, list) or len(values) != VOICE_PROFILE_FEATURE_COUNT:
        return None
    try:
        vector = [float(v) for v in values]
    except (TypeError, ValueError):
        return None
    if not all(math.isfinite(v) for v in vector):
        return None
    return [round(_clip01(v), 6) for v in vector]


def _clip01(value: float) -> float:
    return max(0.0, min(1.0, float(value)))


def _safe_int(value, default: int) -> int:
    try:
        return max(0, int(value))
    except (TypeError, ValueError):
        return default


def _feature_error(reason: str, detail: str = "", *, duration: float = 0.0, active_seconds: float = 0.0) -> dict:
    payload = {
        "ok": False,
        "reason": reason,
        "features": [],
        "duration": round(float(duration or 0.0), 3),
        "active_seconds": round(float(active_seconds or 0.0), 3),
        "feature_version": VOICE_PROFILE_FEATURE_VERSION,
    }
    if detail:
        payload["detail"] = detail[:240]
    return payload
