# -*- coding: utf-8 -*-
"""🗣️ اختبار دقة وِسبر على لهجتك — يقارن موديلات × «برومبت لهجة» ويعطيك أحسن إعداد بالأرقام.
  1) python3 02e_dialect_bench.py ريلك.mp4 --draft          ← يفرّغ بأقوى موديل ويكتب ref.txt مسودة؛ صحّحها بإيدك (النص الصح 100%)
  2) python3 02e_dialect_bench.py ريلك.mp4 --ref ref.txt     ← يجرّب الموديلات والبرومبتات ويطبع WER/CER وأحسن إعداد
خيارات: --models small,medium,turbo (الافتراضي)   --prompts none,syrian[,ملف.txt]   --seconds 60 (أول N ثانية فقط)
النتيجة تنكتب بـbench.json جنب الملف. تطبيقها: WHISPER_MODEL=<الأحسن> + WHISPER_PROMPT=syrian (يقرأها 02a/18 لما تنضاف)
WER = نسبة الكلمات الغلط · CER = نسبة الحروف الغلط (الأدق للعربي). التطبيع: بلا تشكيل/تطويل/علامات، أ إ آ→ا، ى→ي، ة→ه."""
import sys as _sys
try:
    _sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass
import argparse, json, os, re, subprocess, time

PROMPTS = {
    "none": None,
    "syrian": "شو يا جماعة، هلق رح نحكي عن الإعلانات على ميتا. كتير مهم تعرفوا ليش الحملة ما عم تجيب نتيجة، "
              "بدك تجرّب هيك وهيك، مو كل شي بيمشي. منيح، يعني بدنا ميزانية أقل وتارغيت أحسن، ولسا في كريتيف.",
}


def norm(s):
    s = re.sub(r"[ً-ْـ]", "", s or "")
    s = re.sub(r"[أإآٱ]", "ا", s).replace("ى", "ي").replace("ة", "ه")
    s = re.sub(r"[^\w\s]", " ", s, flags=re.U)
    return re.sub(r"\s+", " ", s).strip()


def edit(a, b):
    """مسافة التعديل (Levenshtein) بين قائمتين"""
    p = list(range(len(b) + 1))
    for i, x in enumerate(a, 1):
        c = [i]
        for j, y in enumerate(b, 1):
            c.append(min(p[j] + 1, c[j - 1] + 1, p[j - 1] + (x != y)))
        p = c
    return p[-1]


def wer(ref, hyp):
    r, h = norm(ref).split(), norm(hyp).split()
    return edit(r, h) / max(1, len(r))


def cer(ref, hyp):
    r, h = list(norm(ref).replace(" ", "")), list(norm(hyp).replace(" ", ""))
    return edit(r, h) / max(1, len(r))


def load_wav(src, secs, out):
    cmd = ["ffmpeg", "-v", "error", "-y", "-i", src, "-vn", "-ac", "1", "-ar", "16000"]
    if secs:
        cmd += ["-t", str(secs)]
    subprocess.run(cmd + [out], check=True)
    return out


def make_runner():
    try:
        import whisper
        def run(model, wav, prompt):
            m = RUN.setdefault(model, whisper.load_model(model))
            r = m.transcribe(wav, language="ar", initial_prompt=prompt, condition_on_previous_text=False, fp16=False)
            return r["text"]
        return run
    except ImportError:
        pass
    try:
        from faster_whisper import WhisperModel
        def run(model, wav, prompt):
            m = RUN.setdefault(model, WhisperModel(model, compute_type="int8"))
            segs, _ = m.transcribe(wav, language="ar", initial_prompt=prompt, condition_on_previous_text=False)
            return " ".join(s.text for s in segs)
        return run
    except ImportError:
        sys.exit("❌ لازم openai-whisper أو faster-whisper (00_setup.sh)")


RUN = {}
sys = _sys

if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("src")
    ap.add_argument("--ref")
    ap.add_argument("--draft", action="store_true")
    ap.add_argument("--models", default="small,medium,turbo")
    ap.add_argument("--prompts", default="none,syrian")
    ap.add_argument("--seconds", type=float, default=0)
    a = ap.parse_args()
    base = os.path.splitext(os.path.abspath(a.src))[0]
    wav = load_wav(a.src, a.seconds, base + "_bench.wav")
    run = make_runner()
    if a.draft:
        txt = run(os.environ.get("WHISPER_MODEL", "large-v3"), wav, PROMPTS["syrian"])
        open(os.path.join(os.path.dirname(base), "ref.txt"), "w", encoding="utf-8").write(txt.strip() + "\n")
        print("✍️ كتبت ref.txt (مسودة) — صحّحها حرف بحرف حسب ما قلته فعلاً، وبعدين شغّل مع --ref ref.txt")
        sys.exit(0)
    if not a.ref:
        sys.exit("❌ لازم --ref ref.txt (أو --draft لتوليد مسودة)")
    ref = open(a.ref, encoding="utf-8").read()
    pr = {}
    for p in a.prompts.split(","):
        pr[p] = PROMPTS[p] if p in PROMPTS else open(p, encoding="utf-8").read().strip()
    rows = []
    for m in a.models.split(","):
        for pn, pt in pr.items():
            t0 = time.time()
            hyp = run(m, wav, pt)
            rows.append({"model": m, "prompt": pn, "wer": round(wer(ref, hyp), 4), "cer": round(cer(ref, hyp), 4),
                         "sec": round(time.time() - t0, 1), "text": hyp.strip()})
            print(f"{m:>10} | {pn:>8} | WER {rows[-1]['wer']*100:5.1f}% | CER {rows[-1]['cer']*100:5.1f}% | {rows[-1]['sec']}s", flush=True)
    best = min(rows, key=lambda r: (r["cer"], r["wer"]))
    print(f"\n🏆 الأحسن: موديل {best['model']} + برومبت {best['prompt']} (CER {best['cer']*100:.1f}%)")
    print(f"   WHISPER_MODEL={best['model']}" + (f" WHISPER_PROMPT={best['prompt']}" if best["prompt"] != "none" else ""))
    json.dump({"best": {k: best[k] for k in ("model", "prompt", "wer", "cer")}, "rows": rows},
              open(base + "_bench.json", "w", encoding="utf-8"), ensure_ascii=False, indent=1)
