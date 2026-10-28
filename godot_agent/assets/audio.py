"""Audio measurement, validation against a style rule, and a seeded procedural SFX synthesizer."""

from __future__ import annotations

import array
import math
import random
import re
import shutil
import subprocess
import wave
from pathlib import Path

SAMPLE_RATE = 44100
# EBU R128 integrated loudness needs at least one 400 ms gating block.
MIN_LUFS_SECONDS = 0.4


# ── Measuring ──

def _ffmpeg_stats(path: Path) -> dict:
    proc = subprocess.run(
        ["ffmpeg", "-hide_banner", "-nostats", "-i", str(path), "-af",
         "silencedetect=n=-50dB:d=0.005,astats=metadata=0:measure_perchannel=none,ebur128=peak=true",
         "-f", "null", "-"], capture_output=True, text=True, timeout=120)
    text = proc.stderr
    metrics: dict = {}
    if m := re.search(r"Duration: (\d+):(\d+):([\d.]+)", text):
        metrics["duration_s"] = round(int(m[1]) * 3600 + int(m[2]) * 60 + float(m[3]), 3)
    if m := re.search(r"Audio: (\w+).*?, (\d+) Hz, (mono|stereo|[\d.]+ channels|\d+\.\d+)", text):
        metrics["codec"] = m[1]
        metrics["sample_rate"] = int(m[2])
        metrics["channels"] = 1 if m[3] == "mono" else 2 if m[3] == "stereo" else m[3]
    if m := re.search(r"RMS level dB: (-?[\d.]+|-inf)", text):
        metrics["rms_db"] = round(float(m[1]), 1) if m[1] != "-inf" else None
    if m := re.search(r"Peak:\s+(-?[\d.]+|-inf) dBFS", text):
        metrics["true_peak_db"] = round(float(m[1]), 1) if m[1] != "-inf" else None
    if (m := re.search(r"I:\s+(-?[\d.]+) LUFS", text)) and metrics.get("duration_s", 0) >= MIN_LUFS_SECONDS:
        lufs = float(m[1])
        metrics["loudness_lufs"] = lufs if lufs > -69.9 else None
    starts = [float(v) for v in re.findall(r"silence_start: (-?[\d.]+)", text)]
    ends = [float(v) for v in re.findall(r"silence_end: ([\d.]+)", text)]
    duration = metrics.get("duration_s", 0)
    metrics["leading_silence_ms"] = round(ends[0] * 1000) if starts and starts[0] <= 0.001 and ends else 0
    # A silence that starts and never ends runs to the end of the file.
    if len(starts) > len(ends):
        metrics["trailing_silence_ms"] = round((duration - starts[-1]) * 1000)
    elif starts and ends and abs(ends[-1] - duration) < 0.01 and starts[-1] > 0.001:
        metrics["trailing_silence_ms"] = round((duration - starts[-1]) * 1000)
    else:
        metrics["trailing_silence_ms"] = 0
    return metrics


def _decode(path: Path, rate: int = 22050) -> array.array | None:
    """Mono 16-bit samples, via ffmpeg (any format) or the wave module (WAV only)."""
    if shutil.which("ffmpeg"):
        proc = subprocess.run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-i", str(path), "-ac", "1",
                               "-ar", str(rate), "-f", "s16le", "-"], capture_output=True, timeout=120)
        samples = array.array("h")
        samples.frombytes(proc.stdout[: len(proc.stdout) // 2 * 2])
        return samples
    if path.suffix.lower() != ".wav":
        return None
    with wave.open(str(path)) as wav:
        if wav.getsampwidth() != 2:
            return None
        samples = array.array("h")
        samples.frombytes(wav.readframes(wav.getnframes()))
        channels = wav.getnchannels()
        return samples if channels == 1 else array.array("h", samples[::channels])


def _db(value: float) -> float | None:
    return round(20 * math.log10(value), 1) if value > 0 else None


def _wave_stats(path: Path) -> dict:
    samples = _decode(path, SAMPLE_RATE)
    if samples is None:
        return {"note": "install ffmpeg to analyse non-WAV audio"}
    with wave.open(str(path)) as wav:
        rate, channels = wav.getframerate(), wav.getnchannels()
    peak = max((abs(s) for s in samples), default=0) / 32768
    rms = math.sqrt(sum(s * s for s in samples) / len(samples)) / 32768 if samples else 0
    threshold = 32768 * 10 ** (-50 / 20)
    lead = next((i for i, s in enumerate(samples) if abs(s) > threshold), len(samples))
    trail = next((i for i, s in enumerate(reversed(samples)) if abs(s) > threshold), len(samples))
    return {"duration_s": round(len(samples) / SAMPLE_RATE, 3), "sample_rate": rate, "channels": channels,
            "true_peak_db": _db(peak), "rms_db": _db(rms),
            "leading_silence_ms": round(lead / SAMPLE_RATE * 1000),
            "trailing_silence_ms": round(trail / SAMPLE_RATE * 1000),
            "note": "sample peak and no LUFS (ffmpeg not installed)"}


def _loop_seam(path: Path) -> dict:
    """How well the end flows into the start when the clip loops."""
    samples = _decode(path)
    if not samples or len(samples) < 4410:
        return {}
    window = 1102  # 50 ms at 22050 Hz

    def rms(chunk) -> float:
        return math.sqrt(sum(s * s for s in chunk) / len(chunk)) / 32768

    head, tail = rms(samples[:window]), rms(samples[-window:])
    level_jump = abs((_db(head) or -90) - (_db(tail) or -90))
    sample_jump = abs(samples[0] - samples[-1]) / 32768
    return {"loop_level_jump_db": round(level_jump, 1), "loop_sample_jump": round(sample_jump, 3)}


def analyze_audio(path: Path, loop: bool = False) -> dict:
    metrics = _ffmpeg_stats(path) if shutil.which("ffmpeg") else _wave_stats(path)
    if loop:
        metrics.update(_loop_seam(path))
    return metrics


def validate_audio(project: Path, rel: str, rule: dict) -> dict:
    metrics = analyze_audio(project / rel, loop=bool(rule.get("loop")))
    problems: list[str] = []
    notes: list[str] = [metrics.pop("note")] if "note" in metrics else []

    def outside(value, bounds) -> bool:
        return value is not None and not bounds[0] <= value <= bounds[1]

    if "formats" in rule and Path(rel).suffix.lower().lstrip(".") not in rule["formats"]:
        problems.append(f"format {Path(rel).suffix}, expected one of {rule['formats']}")
    if "sample_rates" in rule and metrics.get("sample_rate") not in rule["sample_rates"]:
        problems.append(f"sample rate {metrics.get('sample_rate')} Hz, expected {rule['sample_rates']}")
    if "channels" in rule and metrics.get("channels") != rule["channels"]:
        problems.append(f"{metrics.get('channels')} channels, expected {rule['channels']}")
    peak = metrics.get("true_peak_db")
    if "max_peak_db" in rule and peak is not None and peak > rule["max_peak_db"]:
        problems.append(f"peak {peak} dBFS, max {rule['max_peak_db']} (clipping risk)")
    if "loudness_lufs" in rule:
        lufs = metrics.get("loudness_lufs")
        if lufs is None:
            notes.append("too short for LUFS; checked rms_db instead" if "rms_db" in rule else "too short for LUFS")
        elif outside(lufs, rule["loudness_lufs"]):
            problems.append(f"loudness {lufs} LUFS, expected {rule['loudness_lufs'][0]} to {rule['loudness_lufs'][1]}")
    if "rms_db" in rule and metrics.get("loudness_lufs") is None and outside(metrics.get("rms_db"), rule["rms_db"]):
        problems.append(f"RMS {metrics.get('rms_db')} dB, expected {rule['rms_db'][0]} to {rule['rms_db'][1]}")
    duration = metrics.get("duration_s", 0)
    if "max_duration_s" in rule and duration > rule["max_duration_s"]:
        problems.append(f"{duration}s long, max {rule['max_duration_s']}s")
    if "min_duration_s" in rule and duration < rule["min_duration_s"]:
        problems.append(f"{duration}s long, min {rule['min_duration_s']}s")
    if "max_leading_silence_ms" in rule and metrics.get("leading_silence_ms", 0) > rule["max_leading_silence_ms"]:
        problems.append(f"{metrics['leading_silence_ms']} ms silence before the sound starts "
                        f"(max {rule['max_leading_silence_ms']}): it will feel late")
    if "max_trailing_silence_ms" in rule and metrics.get("trailing_silence_ms", 0) > rule["max_trailing_silence_ms"]:
        problems.append(f"{metrics['trailing_silence_ms']} ms trailing silence (max {rule['max_trailing_silence_ms']})")
    if rule.get("loop"):
        if metrics.get("loop_level_jump_db", 0) > rule.get("max_loop_jump_db", 6):
            problems.append(f"loop seam: level jumps {metrics['loop_level_jump_db']} dB from end to start")
        if metrics.get("loop_sample_jump", 0) > 0.2:
            notes.append(f"loop seam: waveform jumps {metrics['loop_sample_jump']} of full scale (possible click)")
    return {"path": rel, "kind": "audio", "ok": not problems, "problems": problems, "notes": notes,
            "metrics": metrics, "rule": rule}


# ── Procedural SFX ──
#
# A small sfxr-style synthesizer: one oscillator with pitch slide, arpeggio and
# vibrato, an attack/sustain/decay envelope with punch, and one-pole filters.
# Every preset is a recipe that draws its parameters from a seeded RNG, so a
# (preset, seed) pair always produces the same sound and can be regenerated.

def _pick(rng: random.Random, low: float, high: float) -> float:
    return round(rng.uniform(low, high), 4)


def _preset_params(preset: str, rng: random.Random) -> dict:
    u = lambda a, b: _pick(rng, a, b)  # noqa: E731 - short local alias keeps the recipes readable
    recipes = {
        "pickup": lambda: dict(wave="square", freq=u(700, 1400), duty=u(0.4, 0.6), sustain=u(0.03, 0.08),
                               decay=u(0.1, 0.25), punch=u(0.3, 0.6), arp_mult=u(1.25, 1.6), arp_time=u(0.04, 0.09)),
        "laser": lambda: dict(wave=rng.choice(["square", "saw"]), freq=u(600, 1500), slide=-u(3, 7),
                              duty=u(0.2, 0.5), duty_slide=u(0, 2), sustain=u(0.05, 0.15), decay=u(0.1, 0.25)),
        "explosion": lambda: dict(wave="noise", freq=u(60, 220), slide=-u(0.5, 2), sustain=u(0.05, 0.2),
                                  decay=u(0.3, 0.7), punch=u(0.4, 0.8), lowpass=u(1500, 4000)),
        "powerup": lambda: dict(wave=rng.choice(["square", "sine"]), freq=u(250, 500), slide=u(1.5, 3),
                                vibrato_depth=u(0, 0.5), vibrato_speed=u(8, 20), repeat=u(0.08, 0.15),
                                sustain=u(0.2, 0.35), decay=u(0.15, 0.3)),
        "hit": lambda: dict(wave=rng.choice(["noise", "saw"]), freq=u(150, 500), slide=-u(4, 8),
                            sustain=u(0.01, 0.05), decay=u(0.08, 0.18), punch=u(0.2, 0.5), highpass=80),
        "jump": lambda: dict(wave="square", freq=u(250, 450), slide=u(2, 4), duty=u(0.3, 0.5),
                             sustain=u(0.05, 0.12), decay=u(0.1, 0.2)),
        "blip": lambda: dict(wave=rng.choice(["square", "sine"]), freq=u(500, 1100), duty=0.5,
                             sustain=u(0.02, 0.06), decay=u(0.02, 0.08)),
        "door": lambda: dict(wave="noise", freq=u(40, 90), slide=-u(0.3, 1), lowpass=u(350, 700),
                             sustain=0.03, decay=u(0.25, 0.45), punch=u(0.6, 0.9)),
        "step": lambda: dict(wave="noise", freq=u(300, 900), lowpass=u(1500, 2500), highpass=200,
                             sustain=0.005, decay=u(0.03, 0.06)),
        "magic": lambda: dict(wave=rng.choice(["sine", "triangle"]), freq=u(400, 900), slide=u(0.5, 1.5),
                              vibrato_depth=u(0.5, 1.5), vibrato_speed=u(10, 18), arp_mult=u(1.2, 1.5),
                              arp_time=u(0.06, 0.12), sustain=u(0.15, 0.3), decay=u(0.2, 0.4)),
        "death": lambda: dict(wave=rng.choice(["square", "saw"]), freq=u(300, 500), slide=-u(1.5, 3),
                              vibrato_depth=u(0.3, 1), vibrato_speed=u(6, 12), sustain=u(0.1, 0.2), decay=u(0.3, 0.5)),
        # A short airy whoosh: a bow or thrown weapon leaving.
        "arrow": lambda: dict(wave="noise", freq=u(1400, 2400), slide=-u(0.8, 1.6), highpass=u(500, 900),
                              lowpass=u(5000, 8000), attack=0.004, sustain=u(0.015, 0.03), decay=u(0.1, 0.16)),
        # A low thump with a noisy tail: cannon, mortar, catapult.
        "cannon": lambda: dict(wave="noise", freq=u(45, 80), slide=-u(0.6, 1.2), lowpass=u(900, 1500),
                               punch=u(0.6, 0.9), sustain=u(0.03, 0.06), decay=u(0.35, 0.5)),
        # A wooden knock: placing, building, hammering.
        "build": lambda: dict(wave="triangle", freq=u(110, 170), slide=-u(1.5, 2.5), punch=u(0.7, 0.9),
                              highpass=60, sustain=u(0.02, 0.04), decay=u(0.12, 0.2)),
        # A swelling brass-like call: wave start, alarm, fanfare.
        "horn": lambda: dict(wave="saw", freq=u(160, 240), slide=u(0.05, 0.15), vibrato_depth=u(0.15, 0.3),
                             vibrato_speed=u(4, 6), lowpass=u(1400, 2200), attack=u(0.04, 0.08),
                             sustain=u(0.35, 0.5), decay=u(0.25, 0.35)),
    }
    if preset not in recipes:
        raise KeyError(preset)
    params = {"attack": 0.0, **recipes[preset]()}
    params["noise_seed"] = rng.randrange(1 << 30)
    return params


PRESETS = ("pickup", "laser", "explosion", "powerup", "hit", "jump", "blip", "door", "step", "magic", "death",
           "arrow", "cannon", "build", "horn")


def preset_params(preset: str, seed: int) -> dict:
    try:
        return _preset_params(preset, random.Random(f"{preset}:{seed}"))
    except KeyError:
        raise ValueError(f"unknown preset '{preset}'; choose from {', '.join(PRESETS)}") from None


def mutate(params: dict, amount: float, seed: int) -> dict:
    """Nudges every numeric parameter by up to ±amount (0.1 = 10%), for variations on a sound."""
    rng = random.Random(seed)
    out = dict(params)
    for key, value in params.items():
        if isinstance(value, float) and key != "noise_seed" and value != 0:
            out[key] = round(value * (1 + rng.uniform(-amount, amount)), 4)
    out["noise_seed"] = rng.randrange(1 << 30)
    return out


def synthesize(p: dict, rate: int = SAMPLE_RATE) -> list[float]:
    attack, sustain, decay = p.get("attack", 0.0), p.get("sustain", 0.1), p.get("decay", 0.2)
    total = attack + sustain + decay
    noise = random.Random(p.get("noise_seed", 0))
    wave_type = p.get("wave", "square")
    lp_alpha = 1 - math.exp(-2 * math.pi * p["lowpass"] / rate) if p.get("lowpass") else None
    hp_rc = 1 / (2 * math.pi * p["highpass"]) if p.get("highpass") else None
    hp_alpha = hp_rc / (hp_rc + 1 / rate) if hp_rc else None
    phase, noise_value, noise_step = 0.0, 0.0, -1
    lp_state, hp_state, hp_prev = 0.0, 0.0, 0.0
    out = []
    for i in range(int(total * rate)):
        t = i / rate
        pitch_t = t % p["repeat"] if p.get("repeat") else t
        octaves = p.get("slide", 0.0) * pitch_t + 0.5 * p.get("slide_accel", 0.0) * pitch_t ** 2
        freq = p["freq"] * 2 ** octaves
        if p.get("arp_time") and pitch_t >= p["arp_time"]:
            freq *= p.get("arp_mult", 1.0)
        if p.get("vibrato_depth"):
            freq *= 2 ** (p["vibrato_depth"] * math.sin(2 * math.pi * p.get("vibrato_speed", 6) * t) / 12)
        freq = min(max(freq, p.get("min_freq", 20.0)), 20000.0)
        phase = (phase + freq / rate) % 1.0

        if wave_type == "square":
            duty = min(max(p.get("duty", 0.5) + p.get("duty_slide", 0.0) * t, 0.05), 0.95)
            sample = 1.0 if phase < duty else -1.0
        elif wave_type == "saw":
            sample = 2 * phase - 1
        elif wave_type == "triangle":
            sample = 4 * abs(phase - 0.5) - 1
        elif wave_type == "sine":
            sample = math.sin(2 * math.pi * phase)
        else:
            # Noise holds a random value for 1/16 of a cycle, so pitch still shapes it.
            step = int(phase * 16)
            if step != noise_step:
                noise_step, noise_value = step, noise.uniform(-1, 1)
            sample = noise_value

        if t < attack:
            envelope = t / attack
        elif t < attack + sustain:
            envelope = 1 + p.get("punch", 0.0) * (1 - (t - attack) / sustain)
        else:
            envelope = max(0.0, 1 - (t - attack - sustain) / decay)
        sample *= envelope

        if lp_alpha:
            lp_state += lp_alpha * (sample - lp_state)
            sample = lp_state
        if hp_alpha:
            hp_state = hp_alpha * (hp_state + sample - hp_prev)
            hp_prev = sample
            sample = hp_state
        out.append(sample)
    return out


def write_wav(samples: list[float], path: Path, peak_db: float = -1.0, rate: int = SAMPLE_RATE) -> None:
    """Writes 16-bit mono with its true peak at or below peak_db.

    Square and saw waves overshoot between samples, so the true peak that a
    resampler or DAC produces sits above the sample peak (by about 2 dB for a
    square wave). The file is written once, measured, and rewritten with the
    gain trimmed when it overshoots.
    """
    _write_pcm(samples, path, peak_db, rate)
    if shutil.which("ffmpeg"):
        true_peak = _ffmpeg_stats(path).get("true_peak_db")
        if true_peak is not None and true_peak > peak_db:
            _write_pcm(samples, path, peak_db - (true_peak - peak_db) - 0.05, rate)


def _write_pcm(samples: list[float], path: Path, peak_db: float, rate: int) -> None:
    """Normalizes the sample peak, fades the edges against clicks, writes 16-bit mono."""
    peak = max((abs(s) for s in samples), default=0) or 1.0
    gain = 10 ** (peak_db / 20) / peak
    fade_in, fade_out = int(0.001 * rate), int(0.004 * rate)
    data = array.array("h")
    n = len(samples)
    for i, s in enumerate(samples):
        edge = min(1.0, (i + 1) / fade_in if i < fade_in else 1.0, (n - i) / fade_out if i >= n - fade_out else 1.0)
        data.append(int(max(-1.0, min(1.0, s * gain * edge)) * 32767))
    path.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(path), "wb") as out:
        out.setnchannels(1)
        out.setsampwidth(2)
        out.setframerate(rate)
        out.writeframes(data.tobytes())


# ── Seeing sound ──

def review_sheet(items: list[dict], project: Path, out: Path, width: int = 720) -> Path:
    """Waveform over spectrogram for each clip, so a model can judge attack, decay and pitch."""
    from PIL import Image, ImageDraw, ImageFont

    if not shutil.which("ffmpeg"):
        raise RuntimeError("ffmpeg is needed to draw audio")
    font = ImageFont.load_default()
    rows = []
    for item in items:
        source = project / item["path"]
        with tempfile_dir() as tmp:
            wave_png, spec_png = Path(tmp) / "w.png", Path(tmp) / "s.png"
            subprocess.run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-i", str(source), "-lavfi",
                            f"showwavespic=s={width}x90:split_channels=0:colors=#9ad0ff", "-frames:v", "1",
                            str(wave_png)], check=True, timeout=60)
            subprocess.run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-i", str(source), "-lavfi",
                            f"showspectrumpic=s={width}x140:legend=0:scale=log:fscale=log", "-frames:v", "1",
                            str(spec_png)], check=True, timeout=60)
            wave_img = Image.open(wave_png).convert("RGB")
            spec_img = Image.open(spec_png).convert("RGB").resize((width, 140))
        row = Image.new("RGB", (width, 20 + 90 + 140), (22, 22, 26))
        draw = ImageDraw.Draw(row)
        status = "OK" if item.get("ok", True) else "FAIL"
        draw.text((4, 4), f"{status}  {item['path']}  {item.get('summary', '')}", font=font,
                  fill=(120, 220, 140) if status == "OK" else (240, 90, 100))
        row.paste(wave_img, (0, 20))
        row.paste(spec_img, (0, 110))
        rows.append(row)
    sheet = Image.new("RGB", (width, sum(r.height + 6 for r in rows)), (10, 10, 12))
    y = 0
    for row in rows:
        sheet.paste(row, (0, y))
        y += row.height + 6
    out.parent.mkdir(parents=True, exist_ok=True)
    sheet.save(out)
    return out


def tempfile_dir():
    import tempfile
    return tempfile.TemporaryDirectory()
