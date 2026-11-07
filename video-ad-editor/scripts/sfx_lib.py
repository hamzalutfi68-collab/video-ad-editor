# -*- coding: utf-8 -*-
"""🎚️ مكتبة المؤثرات الصوتية الحقيقية لـ 05_sfx.py — أصوات WAV جاهزة (مثل Saoud SFX Pack, CC0) بدل المولَّدة.

  python3 sfx_lib.py list [مجلد]            ← شو لقيت بالمكتبة وأي حدث يخدمه كل صنف
  python3 sfx_lib.py install <pack.zip> [وجهة]   ← فكّ حزمة أصوات (الأسماء العربية تنفك صح) لـ ~/.video-ad-editor/sfx

مكان المكتبة (أول واحد موجود): متغير VAD_SFX_DIR ← ~/.video-ad-editor/sfx (ما ينمسح بالتحديث) ← <السكل>/sfx (الأصوات المرفقة).
الأصناف تنتعرف من **اسم الملف** (whoosh_* · swipe_* · riser_* · reverse_swell_* · pop · tick · click · typing · ding · sparkle ·
glitch · boom · low_thud · hit…) مو من اسم المجلد، فأي ترتيب مجلدات يمشي. صنف ناقص ← 05_sfx.py يرجع للصوت المولَّد لذاك الحدث.
"""
import os
import re
import sys
import wave
import zipfile

import numpy as np

SR = 48000
HERE = os.path.dirname(os.path.abspath(__file__))

CATS = {  # صنف ← regex على اسم الملف (بلا .wav وبحروف صغيرة)
    "whoosh":      r"^(whoosh|swipe)",
    "whoosh_soft": r"^whoosh_(soft|air)",
    "riser":       r"^(riser|reverse_swell|air_swell)",
    "hit":         r"^(hit|impact|impact_2|stinger|boom|bass_drop|low_thud)$",
    "sub":         r"^(boom|bass_drop|low_thud)$",
    "pop":         r"^pop",
    "tick":        r"^(tick|click|check)$|^interface_click",
    "typing":      r"^typing",   # (محفوظ للتصنيف — الكتابة تُبنى من أصوات tick)
    "chime":       r"^(ding|sparkle|soft_chime)",
    "glitch":      r"^(glitch|digital_zap\d*$|digital_phase_jump)",
}
MAXDUR = {"whoosh": 0.8, "whoosh_soft": 0.8, "riser": 3.0, "hit": 1.2, "sub": 1.0, "pop": 0.4, "tick": 0.15,
          "chime": 1.5, "glitch": 0.35}
TRIM = 0.82   # ≈ -1.7dB: بالاختبار الأصوات الحقيقية طلعت أعلى من المولَّدة بحوالي 1.6dB بالمجموع
EVENT_CAT = {"whoosh_up": "whoosh", "whoosh_down": "whoosh", "soft": "whoosh_soft", "riser": "riser", "thud": "hit",
             "sub": "sub", "pop": "pop", "click": "tick", "tap": "tick", "shimmer": "chime", "glitch": "glitch",
             "counter": "tick"}


def lib_dirs():
    out = []
    if os.environ.get("VAD_SFX_DIR"):
        out.append(os.environ["VAD_SFX_DIR"])
    out.append(os.path.join(os.path.expanduser("~"), ".video-ad-editor", "sfx"))
    out.append(os.path.join(HERE, "..", "sfx"))
    return [os.path.abspath(d) for d in out]


def decode(path):
    """WAV ← مصفوفة mono عند 48k. wave مباشرة لـ PCM16/24/32، وغيرها (فلوت/تردد ثاني) عبر ffmpeg."""
    try:
        with wave.open(path) as w:
            ch, sw, sr, n = w.getnchannels(), w.getsampwidth(), w.getframerate(), w.getnframes()
            raw = w.readframes(n)
        if sr == SR and sw in (2, 3, 4):
            if sw == 2:
                x = np.frombuffer(raw, "<i2").astype(np.float32) / 32768.0
            elif sw == 4:
                x = np.frombuffer(raw, "<i4").astype(np.float32) / 2147483648.0
            else:
                b = np.frombuffer(raw, np.uint8).reshape(-1, 3)
                v = (b[:, 0].astype(np.int32) | (b[:, 1].astype(np.int32) << 8) | (b[:, 2].astype(np.int32) << 16))
                x = (np.where(v >= 1 << 23, v - (1 << 24), v)).astype(np.float32) / 8388608.0
            return x.reshape(-1, ch).mean(1) if ch > 1 else x
    except Exception:
        pass
    import subprocess
    r = subprocess.run(["ffmpeg", "-v", "error", "-i", path, "-ac", "1", "-ar", str(SR), "-f", "f32le", "-"],
                       capture_output=True)
    return np.frombuffer(r.stdout, np.float32).copy() if r.returncode == 0 else np.zeros(0, np.float32)


def trim(x, db=-50):
    """يشيل السكوت من أول الصوت وآخره — حتى يطلع الصوت بلحظة الحدث بالضبط."""
    if len(x) == 0:
        return x
    th = np.max(np.abs(x)) * 10 ** (db / 20)
    idx = np.nonzero(np.abs(x) > th)[0]
    return x[idx[0]:idx[-1] + 1] if len(idx) else x[:0]


def active_rms(x):
    if len(x) == 0:
        return 1e-9
    a = np.abs(x)
    m = a > a.max() * 0.01
    return float(np.sqrt(np.mean(x[m] ** 2))) + 1e-12


def centroid(x):
    if len(x) < 512:
        return 0.0
    s = np.abs(np.fft.rfft(x * np.hanning(len(x))))
    f = np.fft.rfftfreq(len(x), 1 / SR)
    return float((s * f).sum() / (s.sum() + 1e-12))


def direction(x):
    """صاعد (يزداد السطوع) أو هابط أو محايد — من مركز الطيف بثلث الصوت الأول مقابل الأخير."""
    k = len(x) // 3
    if k < 1024:
        return "neutral"
    a, b = centroid(x[:k]), centroid(x[-k:])
    if a <= 0:
        return "neutral"
    r = b / a
    return "up" if r >= 1.2 else "down" if r <= 0.83 else "neutral"


def fades(x, cat):
    n_in, n_out = int(0.002 * SR), int(min(0.08, max(0.015, 0.12 * len(x) / SR)) * SR)
    x = x.copy()
    if len(x) > n_in:
        x[:n_in] *= np.linspace(0, 1, n_in)
    if len(x) > n_out:
        x[-n_out:] *= np.linspace(1, 0, n_out)
    return x


class Library:
    def __init__(self, root):
        self.root = root
        self.files = {c: [] for c in CATS}
        for dp, _, fs in os.walk(root):
            for f in sorted(fs):
                if not f.lower().endswith(".wav"):
                    continue
                stem = re.sub(r"\.wav$", "", f.lower())
                for c, rx in CATS.items():
                    if re.search(rx, stem):
                        self.files[c].append(os.path.join(dp, f))
        for c in self.files:
            self.files[c].sort()
        self._cache, self._rr, self._rng = {}, {}, np.random.RandomState(int(os.environ.get("VAD_SFX_SEED", "5")))

    def has(self, ev):
        return bool(self.files.get(EVENT_CAT.get(ev, ""), []))

    def _load(self, path, cat):
        if path not in self._cache:
            x = trim(decode(path))
            lim = int(MAXDUR.get(cat, 1.5) * SR)
            self._cache[path] = (fades(x[:lim], cat) if cat != "riser" else x, direction(x))
        return self._cache[path]

    def get(self, ev, ref_rms, want_dur=None):
        """ينتقي صوت للحدث (دوران مختلط بلا تكرار متتالي)، ويعايره على مستوى الصوت المولَّد الأصلي حتى يبقى التوازن."""
        cat = EVENT_CAT.get(ev)
        paths = self.files.get(cat or "", [])
        if not paths:
            return None
        if ev in ("whoosh_up", "whoosh_down"):
            want = "up" if ev == "whoosh_up" else "down"
            ok = [p for p in paths if self._load(p, cat)[1] in (want, "neutral")] or paths
        elif ev == "sub":
            ok = sorted(paths, key=lambda p: centroid(self._load(p, cat)[0]))[:3]
        elif ev == "riser" and want_dur:
            ok = sorted(paths, key=lambda p: abs(len(self._load(p, cat)[0]) / SR - want_dur))[:4]
        else:
            ok = paths
        key = (ev, tuple(ok))
        if key not in self._rr:
            order = list(self._rng.permutation(len(ok)))
            self._rr[key] = [order, 0]
        order, i = self._rr[key]
        p = ok[order[i % len(ok)]]
        self._rr[key][1] += 1
        x, _ = self._load(p, cat)
        if ev == "riser" and want_dur and len(x) > want_dur * SR:      # نبقي آخر الصوت (الذروة) ونقصّ أوله
            x = x[-int(want_dur * SR):].copy()
            n = int(0.04 * SR)
            x[:n] *= np.linspace(0, 1, n)
        if len(x) == 0:
            return None
        y = x * (ref_rms / active_rms(x)) * TRIM
        pk = np.max(np.abs(y))
        return (y / pk if pk > 1.0 else y).astype(np.float32)


def load():
    for d in lib_dirs():
        if os.path.isdir(d):
            lib = Library(d)
            if any(lib.files.values()):
                return lib
    return None


def pitch(x, ratio):
    """تغيير حدّة الصوت (وطوله) بإعادة أخذ عيّنات — للتكات المتصاعدة."""
    n = max(8, int(len(x) / ratio))
    return np.interp(np.linspace(0, len(x) - 1, n), np.arange(len(x)), x).astype(np.float32)


def install(zp, dest):
    dest = os.path.abspath(os.path.expanduser(dest))
    z = zipfile.ZipFile(zp)
    n = 0
    for i in z.infolist():
        if i.is_dir():
            continue
        name = i.filename
        if not (i.flag_bits & 0x800):                 # أسماء عربية بزب ما فيه علم UTF-8
            try:
                name = name.encode("cp437").decode("utf-8")
            except Exception:
                pass
        name = name.replace("\\", "/").lstrip("/")
        if ".." in name.split("/") or not name.lower().endswith((".wav", "license.txt")):
            continue
        out = os.path.join(dest, name)
        os.makedirs(os.path.dirname(out), exist_ok=True)
        with open(out, "wb") as f:
            f.write(z.read(i))
        n += name.lower().endswith(".wav")
    print(f"✅ نزّلت {n} صوت إلى {dest}")


def describe(root=None):
    lib = Library(root) if root else load()
    if not lib:
        print("ما لقيت مكتبة — المجلدات اللي بحثت فيها:\n  " + "\n  ".join(lib_dirs()))
        return
    print("📚 المكتبة:", lib.root)
    for c in CATS:
        evs = [e for e, cc in EVENT_CAT.items() if cc == c]
        print(f"  {c:<12}{len(lib.files[c]):>4} صوت   ← {', '.join(evs) or '(للتصنيف فقط)'}")
    miss = [e for e in EVENT_CAT if not lib.has(e)]
    print("  أحداث بلا صوت (تبقى مولَّدة):", ", ".join(miss) or "—")


if __name__ == "__main__":
    a = sys.argv[1:]
    if a[:1] == ["install"] and len(a) >= 2:
        install(a[1], a[2] if len(a) > 2 else os.path.join("~", ".video-ad-editor", "sfx"))
    elif a[:1] == ["list"]:
        describe(a[1] if len(a) > 1 else None)
    else:
        print(__doc__)
