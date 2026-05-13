#!/usr/bin/env python3
"""Generate the default Codex turn-ended chime as a small WAV file."""

from __future__ import annotations

import argparse
import math
import struct
import wave
from pathlib import Path


SAMPLE_RATE = 44_100


def envelope(t: float, length: float) -> float:
    attack = 0.012
    release = 0.22
    if t < 0 or t > length:
        return 0.0
    if t < attack:
        return t / attack
    tail = max(length - t, 0.0)
    release_gain = min(1.0, tail / release)
    return math.exp(-4.4 * t) * release_gain


def bell(freq: float, t: float, length: float) -> float:
    gain = envelope(t, length)
    if gain == 0.0:
        return 0.0

    partials = (
        (1.0, 1.0),
        (2.01, 0.38),
        (2.98, 0.18),
        (4.12, 0.08),
    )
    value = 0.0
    for multiple, amount in partials:
        value += amount * math.sin(2.0 * math.pi * freq * multiple * t)
    return gain * value


def render_sample(index: int) -> float:
    t = index / SAMPLE_RATE
    notes = (
        (0.00, 523.251, 0.78, 0.34),
        (0.11, 659.255, 0.84, 0.29),
        (0.23, 783.991, 0.88, 0.24),
    )
    value = 0.0
    for start, freq, length, gain in notes:
        value += gain * bell(freq, t - start, length)

    # A tiny delayed reflection makes the chime less dry without needing effects.
    for delay, amount in ((0.075, 0.18), (0.145, 0.10)):
        delayed_t = t - delay
        if delayed_t <= 0:
            continue
        for start, freq, length, gain in notes:
            value += amount * gain * bell(freq, delayed_t - start, length)

    return math.tanh(value * 1.25) * 0.82


def generate(output: Path) -> None:
    output.parent.mkdir(parents=True, exist_ok=True)
    duration = 1.18
    total_samples = int(SAMPLE_RATE * duration)

    with wave.open(str(output), "wb") as wav:
        wav.setnchannels(2)
        wav.setsampwidth(2)
        wav.setframerate(SAMPLE_RATE)

        frames = bytearray()
        for index in range(total_samples):
            sample = render_sample(index)
            pan = 0.06 * math.sin(2.0 * math.pi * index / total_samples)
            left = max(-1.0, min(1.0, sample * (1.0 - pan)))
            right = max(-1.0, min(1.0, sample * (1.0 + pan)))
            frames.extend(struct.pack("<hh", int(left * 32767), int(right * 32767)))

        wav.writeframes(frames)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "output",
        nargs="?",
        default=str(Path(__file__).resolve().parents[1] / "assets" / "soft-chime.wav"),
    )
    args = parser.parse_args()
    output = Path(args.output).expanduser().resolve()
    generate(output)
    print(output)


if __name__ == "__main__":
    main()
