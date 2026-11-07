# -*- coding: utf-8 -*-
"""🎛️ أنماط جاهزة بأمر واحد — تضبط الانتقالات + حركات الكاميرا + الإيقاع (theme.json) وتقترح معالجة الصوت.
  python3 38_preset.py list                                   ← الأنماط الستة
  python3 38_preset.py <work> <نمط> [--audio]                 ← يدمج النمط بـ<work>/theme.json (النسخة القديمة theme_before_preset.json)
                                                               --audio يشغّل دكتور الصوت بقوة النمط
  python3 38_preset.py <work> --whoosh                         ← بعد 03_cut_zoom: يضيف ووش/ثَد عند كل انتقال بـ<work>/sfx.json
بعد النمط: 03_cut_zoom.py ثم --whoosh ثم 05_sfx.py. أي مفتاح بـtheme.json تكتبه بيدك بعد النمط يتغلّب عليه."""
import sys as _sys
try:
    _sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass
import json, os, shutil, subprocess, sys

P = {
    "clean":     {"desc": "هادي واحترافي — شرح، مقابلة، علامة فاخرة. زوم خفيف ومسحات ناعمة",
                  "theme": {"pace": "calm", "transitions": {"style": "clean", "dur": 0.30},
                            "moves": {"first": "push_slow", "default": "auto", "amp": 0.04}},
                  "audio": 0.80, "whoosh": "soft"},
    "pop":       {"desc": "ريلز سريع — قطعات punch وwhip وflash، زوم قوي، طاقة عالية",
                  "theme": {"transitions": {"style": "pop"}, "moves": {"first": "punch_in", "default": "auto", "amp": 0.09}},
                  "audio": 0.85, "whoosh": "up"},
    "cinematic": {"desc": "سينمائي — fade وcircle وradial، حركات بطيئة دايمة",
                  "theme": {"transitions": {"style": "cinematic"}, "moves": {"first": "zoom_in", "default": "auto", "amp": 0.06}},
                  "audio": 0.85, "whoosh": "soft"},
    "bold":      {"desc": "جريء — خليط من كل الأنماط (pop + glitch + أشكال + slide) بأعلى تنوّع",
                  "theme": {"transitions": {"style": "bold"}, "moves": {"first": "breathe", "default": "auto", "amp": 0.10}},
                  "audio": 0.85, "whoosh": "up"},
    "glitch":    {"desc": "تقني/نيون — glitch وpixelize وشرائح، مناسب تقنية وتطبيقات وسوشيال",
                  "theme": {"transitions": {"style": "glitch"}, "moves": {"first": "punch_in", "default": "auto", "amp": 0.08}},
                  "audio": 0.85, "whoosh": "glitch"},
    "slide":     {"desc": "انزلاق نظيف — slide وcover وreveal، حركة drift جانبية، مناسب منتجات وعروض",
                  "theme": {"transitions": {"style": "slide"},
                            "moves": {"first": "zoom_out", "default": "drift_right", "amp": 0.07}},
                  "audio": 0.80, "whoosh": "up"},
}


def whoosh_event(tr, mode):
    n = tr["name"]
    if n == "glitch" or mode == "glitch" and tr["kind"] == "inplace":
        return "glitch"
    if n == "punch_big":
        return "thud"
    if n in ("flash", "fadewhite"):
        return "shimmer"
    if n in ("dip_black", "fadeblack", "fadeslow", "fade", "dissolve", "fadegrays") or mode == "soft":
        return "soft"
    if n.endswith("down") or n in ("whip_down",):
        return "whoosh_down"
    return "whoosh_up"


def apply(W, name, audio):
    tp = os.path.join(W, "theme.json")
    cur = json.load(open(tp, encoding="utf-8")) if os.path.exists(tp) else {}
    if cur and not os.path.exists(os.path.join(W, "theme_before_preset.json")):
        shutil.copy(tp, os.path.join(W, "theme_before_preset.json"))
    for k in ("pace", "transitions", "moves"):               # مفاتيح النمط: نمسح القديم حتى ما يتسرّب نمط سابق (مثلاً calm)
        cur.pop(k, None)
    cur.update(P[name]["theme"])
    json.dump(cur, open(tp, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    print(f"✅ نمط «{name}» اندمج بـtheme.json — {P[name]['desc']}")
    st = P[name]["audio"]
    cmd = f'bash {os.path.join(os.path.dirname(os.path.abspath(__file__)), "02b_enhance_audio.sh")} "{W}" --doctor {st}'
    if audio:
        subprocess.call(cmd, shell=True)
    else:
        print(f"🎙️ الصوت (قبل التفريغ): {cmd}")
    print("▶️ بعدها: 03_cut_zoom.py ثم  38_preset.py <work> --whoosh  ثم 05_sfx.py")


def whoosh(W):
    tj = os.path.join(W, "transitions.json")
    if not os.path.exists(tj):
        sys.exit("❌ ما في transitions.json — شغّل 03_cut_zoom.py بعد تفعيل نمط أولاً")
    sp = os.path.join(W, "sfx.json")
    s = json.load(open(sp, encoding="utf-8")) if os.path.exists(sp) else {}
    mode = "up"
    th = os.path.join(W, "theme_before_preset.json")
    for nm, d in P.items():                                      # آخر نمط مطبّق = اللي يطابق transitions بـtheme.json
        try:
            if json.load(open(os.path.join(W, "theme.json"), encoding="utf-8")).get("transitions", {}).get("style") == d["theme"]["transitions"]["style"]:
                mode = d["whoosh"]
        except Exception:
            pass
    LEAD = 0.10
    added = {}
    for tr in json.load(open(tj, encoding="utf-8"))["cuts"]:
        ev = whoosh_event(tr, mode)
        t = round(max(0.0, tr["t"] - LEAD), 3)
        if any(abs(t - x) < 0.15 for v in ("whoosh_up", "whoosh_down", "soft", "thud", "glitch", "shimmer")
               for x in [(e[0] if isinstance(e, list) else e) for e in s.get(v, [])]):
            continue                                              # عندك ووش قريب — ما نكرّر
        s.setdefault(ev, []).append([t, 0.06] if ev == "soft" else t)
        added[ev] = added.get(ev, 0) + 1
    json.dump(s, open(sp, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    print("🔊 أضفت أصوات عند الانتقالات:", " · ".join(f"{k}×{v}" for k, v in added.items()) or "لا شي (كلها مغطّاة)")


if __name__ == "__main__":
    a = sys.argv[1:]
    if not a or a[0] == "list":
        for k, v in P.items():
            print(f"  {k:<10} {v['desc']}")
    elif "--whoosh" in a:
        whoosh(os.path.abspath(a[0]))
    elif len(a) >= 2 and a[1] in P:
        apply(os.path.abspath(a[0]), a[1], "--audio" in a)
    else:
        sys.exit("❌ نمط مجهول — الأنماط: " + " · ".join(P))
