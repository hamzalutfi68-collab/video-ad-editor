# -*- coding: utf-8 -*-
"""🩺 دكتور الصوت — يفحص تسجيلك (مروحة · ضجيج · صدى) ويختار العلاج المناسب، ويعطيك تقرير قبل/بعد.

  python3 02d_audio_doctor.py <work> [--strength 0.85] [--dereverb auto|0..1] [--analyze-only] [--no-dfn]
                                      [--keep-wav out.wav]

المراحل (كل مرحلة تشتغل بس لو الفحص لقى لها سبب):
  1) فحص: أرضية الضجيج · نغمات المروحة/الطنين (قمم ضيقة بالطيف) · زمن الصدى التقريبي RT60.
  2) نوتش ضيق على نغمات المروحة (بدل ما نخنق الصوت كله).
  3) ضجيج عريض: DeepFilterNet3 لو منصّب (00_setup.sh --extras) وإلا afftdn بأرضية مقيسة من تسجيلك.
  4) صدى: طرح الذيل المتأخر طيفياً (طريقة Lebart) — تخفيف مو إلغاء، وكل ما قوّيته نزل وضوح الكلام قليلاً (قارن بأذنك).
  5) دمج الصوت بالفيديو بنفس سلسلة 02c (قص المنخفض + ضغط + معايرة).

الأصل يُحفظ src_rawaudio.mov — سوّها قبل التفريغ (الخطوة 4) حتى يسمع وِسبر صوتاً نظيفاً.
التقرير يُكتب بـ <work>/audio_report.json و audio_report.txt.
"""
import importlib.util
import json
import os
import shutil
import subprocess
import sys
import tempfile

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
SR = 48000


def _load_02c():
    spec = importlib.util.spec_from_file_location("denoise02c", os.path.join(HERE, "02c_denoise.py"))
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


D = _load_02c()


def die(m):
    print("❌ " + m)
    sys.exit(2)


# ───────────────────────── الفحص ─────────────────────────
def frame_db(x, n):
    k = len(x) // n
    if k < 4:
        return np.full(4, -90.0)
    r = np.sqrt(np.mean(x[:k * n].reshape(k, n) ** 2, axis=1))
    return 20 * np.log10(r + 1e-9)


def hum_peaks(x, sr=SR, win=8192):
    """نغمات ثابتة بطيف الأجزاء الهادية (مروحة/طنين كهرباء). يرجّع [(Hz, بروز dB, مستوى dBFS)] الأقوى أولاً.
    النغمة لازم تظهر بـ80% من النوافذ الهادية — هيك ذيل صوتك (يتغيّر كل لحظة) ما يتحسب مروحة."""
    from scipy.ndimage import median_filter
    from scipy.signal import find_peaks
    k = len(x) // win
    if k < 4:
        return []
    seg = x[:k * win].reshape(k, win)
    rms = np.sqrt(np.mean(seg ** 2, axis=1))
    take = max(3, k // 4)
    quiet = seg[np.argsort(rms)[:take]]
    w = np.hanning(win)
    P = np.abs(np.fft.rfft(quiet * w, axis=1)) ** 2
    spec = P.mean(axis=0)
    db = 10 * np.log10(spec + 1e-18)
    f = np.fft.rfftfreq(win, 1 / sr)
    excess = db - median_filter(db, size=201, mode="nearest")
    lo, hi = np.searchsorted(f, 40), np.searchsorted(f, 1500)
    idx, _ = find_peaks(excess[lo:hi], height=12, distance=4)
    pw = 10 * np.log10(P + 1e-18)
    per_win = [pw[i] - median_filter(pw[i], size=201, mode="nearest") for i in range(len(pw))]
    out = []
    for i in sorted(idx + lo, key=lambda j: -excess[j]):
        fr = float(f[i])
        stable = np.mean([pw_ex[max(0, i - 1):i + 2].max() >= 8 for pw_ex in per_win])
        if stable < 0.8 or any(abs(fr - o[0]) <= 4 for o in out):
            continue
        amp = np.sqrt(spec[i]) * 2 / np.sum(w)
        out.append((round(fr, 1), round(float(excess[i]), 1), round(float(20 * np.log10(amp + 1e-9)), 1)))
        if len(out) >= 6:
            break
    return out


def rt60_estimate(x, sr=SR):
    """زمن الصدى RT60 تقريبي من ذيول نهايات الجُمل (ميل الهبوط بالديسيبل). None لو ما كفت الأحداث."""
    n = int(sr * 0.01)
    db = frame_db(x, n)
    floor, peak = np.percentile(db, 10), np.percentile(db, 95)
    if peak - floor < 18:
        return None
    slopes, i = [], 1
    while i < len(db) - 12:
        if db[i - 1] > peak - 12 and db[i] < db[i - 1] - 1.0:
            j = i
            while j < len(db) - 1 and j - i < 45 and db[j] > floor + 6 and db[j + 1] <= db[j] + 1.5:
                j += 1
            if j - i >= 8 and db[i] - db[j] >= 10:
                t = np.arange(i, j + 1) * 0.01
                s = np.polyfit(t, db[i:j + 1], 1)[0]
                if s < -20:
                    slopes.append(s)
            i = j + 1
        else:
            i += 1
    if len(slopes) < 2:
        return None
    return round(float(min(2.5, max(0.1, -60.0 / np.median(slopes)))), 2)


def rt60_calibrated(est):
    """المقدّر الخام يضغط القيم العالية (كلام نظيف ≈0.25 · RT60 حقيقي 0.3→0.33 · 0.6→0.51 · 0.9→0.65 على صدى اصطناعي) —
    نمدّه بنقاط المعايرة. تقريبي: معايَر على صدى ضجيجي اصطناعي، مو على غرف حقيقية."""
    if est is None:
        return None
    return round(float(np.interp(est, [0.25, 0.33, 0.51, 0.65], [0.1, 0.3, 0.6, 0.9])), 2)


def analyze(x, sr=SR):
    db = frame_db(x, int(sr * 0.02))
    floor, speech = float(np.percentile(db, 10)), float(np.percentile(db, 90))
    hp = hum_peaks(x, sr)
    return {
        "floor_db": round(floor, 1),
        "speech_db": round(speech, 1),
        "snr_db": round(speech - floor, 1),
        "hum": hp,
        "hum_max_db": max((h[2] for h in hp), default=-90.0),
        "rt60": rt60_calibrated(rt60_estimate(x, sr)),
    }


# ───────────────────────── العلاج ─────────────────────────
def ffmpeg_wav(src, dst, af=None):
    cmd = ["ffmpeg", "-v", "error", "-y", "-i", src, "-vn", "-ac", "1", "-ar", str(SR)]
    if af:
        cmd += ["-af", af]
    r = subprocess.run(cmd + ["-c:a", "pcm_s16le", dst], capture_output=True, text=True)
    if r.returncode:
        die("ffmpeg: " + r.stderr[-300:])


def notch_chain(peaks):
    parts = []
    for fr, ex in peaks:
        parts.append(f"equalizer=f={fr}:width_type=q:w=40:g=-{min(24.0, ex * 0.9):.1f}")
    return ",".join(parts)


def dereverb(x, rt60, strength, sr=SR):
    """طرح الذيل المتأخر (Lebart 2001): طاقة الصدى المتأخر ≈ طاقة ما قبل 50ms مضروبة بعامل اضمحلال RT60."""
    from scipy.ndimage import uniform_filter1d
    from scipy.signal import istft, stft
    nper, hop = 1024, 256
    _, _, Y = stft(x, sr, nperseg=nper, noverlap=nper - hop, window="hann")
    Py = np.abs(Y) ** 2
    d = max(1, int(round(0.05 * sr / hop)))
    delta = 3 * np.log(10) / rt60
    fac = np.exp(-2 * delta * 0.05)
    Pr = np.zeros_like(Py)
    Pr[:, d:] = fac * Py[:, :-d]
    alpha = 0.8 + 0.7 * strength
    gmin = 10 ** ((-3 - 6 * strength) / 20)
    G = np.sqrt(np.clip(1 - alpha * Pr / (Py + 1e-12), gmin ** 2, 1.0))
    G = uniform_filter1d(G, size=3, axis=1, mode="nearest")
    _, y = istft(Y * G, sr, nperseg=nper, noverlap=nper - hop, window="hann")
    y = y[:len(x)]
    return np.pad(y, (0, max(0, len(x) - len(y)))).astype(np.float32)


def write_wav(p, x):
    import wave
    with wave.open(p, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(SR)
        w.writeframes((np.clip(x, -1, 1) * 32767).astype(np.int16).tobytes())


def plan_of(a, dereverb_opt):
    p = {"notch": [(pk[0], pk[1]) for pk in a["hum"] if pk[1] >= 12], "denoise": a["snr_db"] < 45, "dereverb": 0.0}
    if dereverb_opt == "auto":
        # تلقائي = خفيف عمداً (سقف 0.4): بالاختبار كل زيادة بقوة الصدى تنزّل وضوح الكلام (STOI) — قوّيه يدوياً وقارن بأذنك
        if a["rt60"] and a["rt60"] >= 0.4:
            p["dereverb"] = round(min(0.4, 0.15 + (a["rt60"] - 0.35) * 0.8), 2)
    else:
        p["dereverb"] = float(dereverb_opt) if a["rt60"] else 0.0
    return p


def run(W, strength, dereverb_opt, analyze_only, use_dfn, keep_wav):
    src, raw = os.path.join(W, "src.mov"), os.path.join(W, "src_rawaudio.mov")
    if not os.path.exists(src):
        die("ما لقيت " + src)
    if not os.path.exists(raw):
        shutil.copy2(src, raw)
    tmp = tempfile.mkdtemp(prefix="doc_")
    try:
        dry_p = os.path.join(tmp, "dry.wav")
        ffmpeg_wav(raw, dry_p)
        dry = D.read_wav(dry_p)
        before = analyze(dry)
        if before["rt60"] is None:      # الضجيج غطّى الذيول — نقيس على نسخة مخففة (نوتش + afftdn) بس للقياس
            pre = [notch_chain([(f, e) for f, e, _ in before["hum"] if e >= 12])] if before["hum"] else []
            nf = int(np.clip(before["floor_db"] - 3, -80, -20))
            pre_p = os.path.join(tmp, "pre.wav")
            ffmpeg_wav(dry_p, pre_p, ",".join([c for c in pre if c] + [f"highpass=f=80,afftdn=nf={nf}:nt=w"]))
            before["rt60"] = rt60_calibrated(rt60_estimate(D.read_wav(pre_p)))
        plan = plan_of(before, dereverb_opt)
        print("🩺 الفحص:", json.dumps(before, ensure_ascii=False), flush=True)
        if analyze_only:
            print("   الخطة:", json.dumps(plan, ensure_ascii=False))
            return before, None, plan

        cur_p = dry_p
        if plan["notch"]:
            cur_p = os.path.join(tmp, "notch.wav")
            ffmpeg_wav(dry_p, cur_p, notch_chain(plan["notch"]))
            print(f"   🔕 نوتش على {len(plan['notch'])} نغمة: " + "، ".join(f"{f:.0f}Hz" for f, _ in plan["notch"]))

        engine = "none"
        if plan["denoise"]:
            binp = D.find_dfn() if use_dfn else None
            wet_p = D.dfn_clean(binp, cur_p, tmp, strength) if binp else None
            if wet_p:
                cur, wet = D.read_wav(cur_p), D.read_wav(wet_p)
                lag = D.lag_of(cur, wet)
                wet = wet[lag:] if lag > 0 else np.concatenate([np.zeros(-lag, np.float32), wet]) if lag < 0 else wet
                wet = np.pad(wet, (0, max(0, len(cur) - len(wet))))[:len(cur)]
                x, engine = wet, "DeepFilterNet3"
            else:
                nf = int(np.clip(before["floor_db"] - 3, -80, -20))
                p2 = os.path.join(tmp, "afft.wav")
                ffmpeg_wav(cur_p, p2, f"highpass=f=80,afftdn=nf={nf}:nt=w")
                x, engine = D.read_wav(p2), f"afftdn(nf={nf})"
        else:
            x = D.read_wav(cur_p)
        print(f"   🎙️ ضجيج: {engine}")

        if plan["dereverb"] > 0:
            x = dereverb(x, before["rt60"], plan["dereverb"])
            print(f"   🏛️ صدى: طرح الذيل المتأخر (RT60≈{before['rt60']}s · قوة {plan['dereverb']})")

        mix_p = os.path.join(tmp, "mix.wav")
        write_wav(mix_p, x)
        after = analyze(D.read_wav(mix_p))
        if keep_wav:
            shutil.copy2(mix_p, keep_wav)
        subprocess.run(["ffmpeg", "-v", "error", "-y", "-i", raw, "-i", mix_p, "-map", "0:v:0", "-map", "1:a:0",
                        "-c:v", "copy", "-af", D.CHAIN, "-ac", "2", "-ar", str(SR), "-c:a", "aac", "-b:a", "192k",
                        "-shortest", "-movflags", "+faststart", src], check=True)
        return before, after, dict(plan, engine=engine)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def report(W, before, after, plan):
    def row(name, k, unit="", better="down"):
        b, a = before.get(k), after.get(k)
        return f"{name:<22}{b if b is not None else '—':>8}{unit}  →  {a if a is not None else '—':>8}{unit}"
    lines = [
        "تقرير الصوت (قبل → بعد)",
        row("أرضية الضجيج", "floor_db", " dB"),
        row("مستوى الكلام", "speech_db", " dB"),
        row("فرق الكلام عن الضجيج", "snr_db", " dB"),
        row("أقوى نغمة مروحة (مطلق)", "hum_max_db", " dBFS"),
        row("الصدى RT60 (تقريبي)", "rt60", " s"),
        "العلاج: " + json.dumps(plan, ensure_ascii=False),
        "ملاحظة: الصدى الثقيل ما ينلغي بالكامل بالبرمجيات — الأفضل مصدر قريب من المايك وغرفة فيها أقمشة.",
    ]
    txt = "\n".join(lines)
    with open(os.path.join(W, "audio_report.txt"), "w", encoding="utf-8") as f:
        f.write(txt + "\n")
    with open(os.path.join(W, "audio_report.json"), "w", encoding="utf-8") as f:
        json.dump({"before": before, "after": after, "plan": plan}, f, ensure_ascii=False, indent=2)
    print(txt)


def main(a):
    if not a or a[0] in ("-h", "--help"):
        print(__doc__)
        sys.exit(0)
    W = os.path.abspath(a[0])
    s = max(0.0, min(1.0, D.flag(a, "--strength", 0.85, float)))
    dv = D.flag(a, "--dereverb", "auto")
    before, after, plan = run(W, s, dv, "--analyze-only" in a, "--no-dfn" not in a, D.flag(a, "--keep-wav", None))
    if after:
        report(W, before, after, plan)
        print("✅ تم — الأصل بـ src_rawaudio.mov")


if __name__ == "__main__":
    main(sys.argv[1:])
