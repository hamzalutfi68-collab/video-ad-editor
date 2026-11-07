# -*- coding: utf-8 -*-
"""🎞️ محرك الانتقالات وحركات الكاميرا — يُستدعى من 03_cut_zoom.py (ما يتشغّل لحاله إلا للمعرض).

الفكرة: **مدة الفيديو الكلية ما تتغيّر أبداً** (فالصوت والكابشنز والـSFX تبقى مضبوطة). نوعان:
  • انتقال بالتداخل (xfade — 57 نوع ffmpeg: wipe · slide · circle · pixelize · radial …): نمزج آخر w ثانية من المقطع A مع
    «مقدمة» المقطع B (لقطات قبل بداية B بالأصل — بالعادة سكتة قبل الكلام) — الصوت ما ينلمس.
  • انتقال موضعي (بلا تداخل): punch · whip · flash · dip_black · glitch · shake · tilt · blur_zoom — حركة على ذيل A ورأس B.
  وحركات كاميرا لكل مقطع: zoom_in · zoom_out · breathe (دخول وخروج) · push_slow · pull_slow · punch_in · drift_*.

الإعداد (theme.json):
  "transitions": "auto" | "clean" | "pop" | "cinematic" | "glitch" | "bold" | "slide" | "geometric" | "off"
               | {"style":"auto","dur":0.25,"seed":7,"overrides":{"2":"glitch"},"off":[4]}     ← أرقام الانتقالات من 1 (بين المقطع 1 و2)
  "moves":      "auto" | "off" | {"first":"breathe","default":"auto","overrides":{"3":"zoom_out"},"amp":0.08}   ← أرقام المقاطع من 1
غير موجودة ← يشتغل السكل زي أول بالضبط (قطع مباشر).  المعرض:  python3 _transitions.py demo <مجلد> [اسم|all]
"""
import json
import math
import os
import random
import subprocess
import sys

W_, H_, FPS = 1080, 1920, 30

# ── الكتالوج ──────────────────────────────────────────────────────────────
XFADES = ("fade wipeleft wiperight wipeup wipedown slideleft slideright slideup slidedown circlecrop rectcrop distance "
          "fadeblack fadewhite radial smoothleft smoothright smoothup smoothdown circleopen circleclose vertopen vertclose "
          "horzopen horzclose dissolve pixelize diagtl diagtr diagbl diagbr hlslice hrslice vuslice vdslice hblur fadegrays "
          "wipetl wipetr wipebl wipebr squeezeh squeezev zoomin fadefast fadeslow hlwind hrwind vuwind vdwind coverleft "
          "coverright coverup coverdown revealleft revealright revealup revealdown").split()
XF_DUR = {"fadeblack": 0.40, "fadewhite": 0.35, "fadeslow": 0.55, "fadefast": 0.15, "circleopen": 0.40, "circleclose": 0.40,
          "radial": 0.40, "distance": 0.40, "pixelize": 0.30, "zoomin": 0.35, "dissolve": 0.35, "fade": 0.30, "hblur": 0.30}
INPLACE = {"punch": 0.16, "punch_big": 0.20, "whip_left": 0.20, "whip_right": 0.20, "whip_up": 0.20, "whip_down": 0.20,
           "flash": 0.14, "dip_black": 0.18, "glitch": 0.22, "shake": 0.20, "tilt_left": 0.20, "tilt_right": 0.20,
           "blur_zoom": 0.22}
MOVES = ("zoom_in zoom_out breathe push_slow pull_slow punch_in drift_left drift_right drift_up drift_down none").split()
ALIAS = {"zoom_in_out": "breathe", "zoomin_out": "breathe", "in_out": "breathe", "push": "push_slow", "pull": "pull_slow"}

STYLES = {
    "clean": "fade dissolve smoothleft smoothright smoothup smoothdown fadegrays wipeleft wiperight punch".split(),
    "cinematic": "fadeblack fadewhite fadeslow circleopen circleclose radial distance horzopen vertopen dissolve".split(),
    "pop": "punch punch_big whip_left whip_right whip_up flash zoomin slideleft slideright squeezeh shake tilt_left tilt_right blur_zoom".split(),
    "glitch": "glitch pixelize hlslice hrslice vuslice vdslice hlwind hrwind hblur flash shake".split(),
    "slide": "slideleft slideright slideup slidedown coverleft coverright coverup coverdown revealleft revealright revealup revealdown".split(),
    "geometric": "circlecrop rectcrop diagtl diagtr diagbl diagbr wipetl wipetr wipebl wipebr vertclose horzclose squeezev".split(),
}
STYLES["bold"] = STYLES["pop"] + STYLES["glitch"] + STYLES["geometric"] + STYLES["slide"]


def family(n):
    for k in ("whip", "tilt", "slide", "cover", "reveal", "wipe", "smooth", "fade", "circle", "diag", "punch", "flash", "glitch"):
        if n.startswith(k):
            return k
    return n


def info(name):
    """(نوع، مدة افتراضية)"""
    if name in INPLACE:
        return "inplace", INPLACE[name]
    if name in XFADES:
        return "xfade", XF_DUR.get(name, 0.30)
    return None, 0


# ── تعابير ffmpeg (بدلالة t = زمن المقطع المحلي) ──────────────────────────────
def _pout(D, w):
    return f"clip((t-{D - w:.4f})/{w:.4f},0,1)"


def _pin(w):
    return f"clip(t/{w:.4f},0,1)"


def _str(side, D, w, e=1.0):          # قوة التأثير 0..1 تزيد باتجاه نقطة القص
    return f"pow({_pout(D, w)},{e})" if side == "out" else f"pow(1-{_pin(w)},{e})"


def _win(side, D, w):
    return (D - w, D) if side == "out" else (0.0, w)


def _steps(side, D, w, levels):       # [(t0,t1,قيمة)] مرتبة باتجاه القص
    a, b = _win(side, D, w)
    n = len(levels)
    seq = levels if side == "out" else levels[::-1]
    return [(a + w * k / n, a + w * (k + 1) / n, v) for k, v in enumerate(seq)]


def _en(t0, t1):
    return f"enable='between(t,{t0:.4f},{t1:.4f})'"


def fx(name, side, D, w, dr=None):
    """تأثير موضعي على نافذة w من المقطع (side=out: آخره · in: أوله) → {zoom,dx,dy,rot,post}"""
    S = lambda e=1.0: _str(side, D, w, e)
    z, dx, dy, rot, post = [], [], [], [], []
    a, b = _win(side, D, w)
    if name in ("punch", "punch_big"):
        z.append(f"1+{0.20 if name == 'punch' else 0.34}*{S(2)}")
    elif name.startswith("whip_"):
        horiz = name in ("whip_left", "whip_right")
        sgn = 1 if name in ("whip_left", "whip_up") else -1
        z.append(f"1+0.26*{S(1)}")
        mg = "540*0.26*(" + S(1) + ")" if horiz else "960*0.26*(" + S(1) + ")"
        off = f"{sgn}*0.9*{mg}*{S(1.6)}"
        (dx if horiz else dy).append(off if side == "out" else f"-({off})")
        for t0, t1, v in _steps(side, D, w, [4, 14, 28]):
            post.append(f"gblur=sigma={v}:sigmaV=0.01:{_en(t0, t1)}" if horiz else f"gblur=sigma=0.01:sigmaV={v}:{_en(t0, t1)}")
    elif name == "flash":
        z.append(f"1+0.10*{S(2)}")
        post.append(f"eq=brightness='0.9*{S(3)}':eval=frame")
    elif name == "dip_black":
        post.append(f"eq=brightness='-1*{S(1.6)}':eval=frame")
    elif name == "glitch":
        z.append(f"1+0.12*{S(1)}")
        dx.append(f"{S(1)}*40*sin(t*733.3)")
        dy.append(f"{S(1)}*10*sin(t*517.7)")
        for t0, t1, v in _steps(side, D, w, [4, 10, 20]):
            post.append(f"rgbashift=rh=-{v}:bh={v}:gv={v // 2}:{_en(t0, t1)}")
        post.append(f"noise=alls=20:allf=t:{_en(a, b)}")
    elif name == "shake":
        z.append(f"1+0.14*{S(1)}")
        dx.append(f"{S(1)}*34*sin(t*105)")
        dy.append(f"{S(1)}*26*cos(t*87)")
    elif name in ("tilt_left", "tilt_right"):
        sg = -1 if name == "tilt_left" else 1
        z.append(f"1+0.24*{S(1)}")
        rot.append(f"{0.10 * sg}*{S(1.5)}" if side == "out" else f"{-0.10 * sg}*{S(1.5)}")
    elif name == "blur_zoom":
        z.append(f"1+0.09*{S(1)}")
        for t0, t1, v in _steps(side, D, w, [4, 10, 22]):
            post.append(f"gblur=sigma={v}:{_en(t0, t1)}")
    return {"zoom": z, "dx": dx, "dy": dy, "rot": rot, "post": post}


def _st(x):
    return f"(pow(clip({x},0,1),2)*(3-2*clip({x},0,1)))"


def move(name, D, amp):
    """حركة الكاميرا طول المقطع → {zoom,dx,dy}  +  (zoom0,dx0,dy0) حالة البداية كأرقام للـhandle"""
    z, dx, dy = [], [], []
    z0, dx0, dy0 = 1.0, "0", "0"
    p = f"clip(t/{max(D, 0.01):.4f},0,1)"
    if name == "zoom_in":
        z.append(f"1+{amp}*{_st('t/' + format(max(D, 0.01), '.4f'))}")
    elif name == "zoom_out":
        z.append(f"1+{amp}*(1-{_st('t/' + format(max(D, 0.01), '.4f'))})")
        z0 = 1 + amp
    elif name == "breathe":
        z.append(f"1+{amp}*sin(PI*{p})")
    elif name == "push_slow":
        z.append(f"1+{amp * 0.5:.4f}*{p}")
    elif name == "pull_slow":
        z.append(f"1+{amp * 0.5:.4f}*(1-{p})")
        z0 = 1 + amp * 0.5
    elif name == "punch_in":
        z.append(f"1+{amp}*(1-pow(1-clip(t/0.30,0,1),3))")
    elif name.startswith("drift_"):
        horiz = name in ("drift_left", "drift_right")
        sgn = 1 if name in ("drift_left", "drift_up") else -1
        k = 0.10
        z.append(f"1+{k}")
        z0 = 1 + k
        mg = f"{540 if horiz else 960}*{k}*0.85"
        e = f"{sgn}*{mg}*(2*{p}-1)"
        (dx if horiz else dy).append(e)
        if horiz:
            dx0 = f"{sgn}*{mg}*(-1)"
        else:
            dy0 = f"{sgn}*{mg}*(-1)"
    return {"zoom": z, "dx": dx, "dy": dy}, (z0, dx0, dy0)


# ── تركيب سلسلة فلاتر مقطع ─────────────────────────────────────────────────
def _expr(terms, default="0"):
    return "+".join(f"({t})" for t in terms) if terms else default


def dyn_chain(crop, zt, dxt, dyt, rot, post):
    """من مقطع مقصوص (crop=...) إلى 1080x1920 بزوم/إزاحة/دوران لكل فريم — تحجيم واحد من المصدر بلا تدهور"""
    M = "max(1," + ("*".join(f"({t})" for t in zt) if zt else "1") + ")"
    out = [crop]
    if rot:
        out.append(f"rotate=a='{_expr(rot)}':ow=iw:oh=ih:c=black")
    out.append(f"scale=w='trunc({W_}*{M}/2)*2':h='trunc({H_}*{M}/2)*2':eval=frame:flags=bicubic")
    out.append(f"crop={W_}:{H_}:x='{W_ // 2}*({M}-1)+({_expr(dxt)})':y='{H_ // 2}*({M}-1)+({_expr(dyt)})'")
    out += post
    out.append(f"setsar=1,fps={FPS},format=yuv420p")
    return ",".join(out)


def static_chain(crop):
    return f"{crop},scale={W_}:{H_}:flags=lanczos,setsar=1,fps={FPS},format=yuv420p"


# ── التخطيط ───────────────────────────────────────────────────────────────
def _norm(s):
    return ALIAS.get(str(s).strip().lower(), str(s).strip().lower())


def _parse(v, key):
    if v in (None, False, 0, "", "off", "none", "no"):
        return None
    if v is True:
        return {"style": "auto"} if key == "t" else {"default": "auto"}
    if isinstance(v, str):
        return {"style": v.lower()} if key == "t" else {"default": v.lower()}
    return dict(v) if isinstance(v, dict) else None


def setup(theme, cutj, PIECES, calm=False, work=None):
    tc = _parse(theme.get("transitions", os.environ.get("VAD_TRANSITIONS")), "t")
    mc = _parse(theme.get("moves", os.environ.get("VAD_MOVES")), "m")
    n = len(PIECES)
    if not (tc or mc) or n == 0:
        return None
    D = [e - s for s, e, _ in PIECES]
    rng = random.Random(int((tc or {}).get("seed", 7)))
    cuts, moves = [], []
    # ── الانتقالات
    last = []
    for i in range(n - 1):
        name = None
        if tc:
            ov = {str(k): v for k, v in (tc.get("overrides") or {}).items()}
            if (i + 1) in [int(x) for x in tc.get("off", [])]:
                cuts.append(None)
                continue
            if str(i + 1) in ov:
                name = _norm(ov[str(i + 1)])
                if info(name)[0] is None:
                    print(f"⚠️ انتقال مجهول «{name}» برقم {i + 1} — اخترت تلقائياً")
                    name = None
            if not name:
                style = tc.get("style", "auto")
                if calm and style == "auto":
                    style = "clean"
                if style == "auto":
                    nxt = D[i + 1]
                    pool = (STYLES["pop"] if nxt < 1.6 else
                            STYLES["pop"] + STYLES["clean"] + STYLES["slide"] if nxt < 4 else
                            STYLES["clean"] + STYLES["cinematic"] + STYLES["pop"][:4])
                else:
                    pool = STYLES.get(style) or STYLES["clean"]
                cand = [x for x in pool if x not in last[-3:] and (not last or family(x) != family(last[-1]))] or pool
                name = rng.choice(cand)
            kind, dd = info(name)
            w = float(tc.get("dur", dd)) if "dur" in tc and kind == "xfade" else dd
            if "dur" in tc and kind == "inplace":
                w = dd * float(tc["dur"]) / 0.25
            w = round(max(0.08, min(w, 0.9)), 3)
            last.append(name)
            cuts.append({"name": name, "kind": kind, "w": w})
        else:
            cuts.append(None)
    # حماية: xfade يحتاج لقطات قبل بداية B وطول كافي لـA
    for i, c in enumerate(cuts):
        if not c:
            continue
        sB = PIECES[i + 1][0]
        if c["kind"] == "xfade":
            w = min(c["w"], D[i] * 0.45, max(0.0, sB))
            if w < 0.10:
                c.update(name="punch", kind="inplace", w=0.16)
            else:
                c["w"] = round(w, 3)
        if c["kind"] == "inplace":
            c["w"] = round(min(c["w"], D[i] * 0.4, D[i + 1] * 0.4), 3)
            if c["w"] < 0.06:
                cuts[i] = None
    # ── حركات الكاميرا
    cyc = ["push_slow", "pull_slow", "zoom_in", "zoom_out", "drift_right", "drift_left"]
    amp = float((mc or {}).get("amp", 0.04 if calm else 0.08))
    for i in range(n):
        nm = "none"
        if mc:
            ov = {str(k): v for k, v in (mc.get("overrides") or {}).items()}
            if str(i + 1) in ov:
                nm = _norm(ov[str(i + 1)])
            elif i == 0 and mc.get("first"):
                nm = _norm(mc["first"])
            else:
                df = _norm(mc.get("default", "auto"))
                nm = (cyc[(i - 1) % len(cyc)] if df == "auto" else df)
                if calm and nm not in ("push_slow", "pull_slow", "none"):
                    nm = "push_slow"
            if nm not in MOVES:
                print(f"⚠️ حركة مجهولة «{nm}» للمقطع {i + 1} — تجاهلتها")
                nm = "none"
            if D[i] < 0.9 and not (i == 0 and mc.get("first")) and str(i + 1) not in ov:
                nm = "none"
        moves.append({"name": nm, "amp": amp})
    plan = {"cuts": cuts, "moves": moves, "D": D}
    # خطة للمعرض/الـSFX: وقت كل انتقال بخط الفيديو النهائي
    t = 0.0
    out = []
    for i in range(n - 1):
        t += D[i]
        if cuts[i]:
            out.append({"n": i + 1, "t": round(t, 3), **cuts[i]})
    plan["timeline"] = out
    if work:
        json.dump({"cuts": out, "moves": [{"clip": i + 1, **m} for i, m in enumerate(moves) if m["name"] != "none"]},
                  open(os.path.join(work, "transitions.json"), "w"), indent=1, ensure_ascii=False)
    return plan


def video_graph(plan, PIECES, src="[0:v]"):
    """فلاتر الفيديو كاملة، تنتهي بـ [vc0] (بديل concat القديم). الطول الكلي = مجموع المقاطع بالضبط."""
    cuts, moves, D = plan["cuts"], plan["moves"], plan["D"]
    n = len(PIECES)
    fc, order = [], []
    for i, (s, e, crop) in enumerate(PIECES):
        zt, dxt, dyt, rot, post = [], [], [], [], []
        mv, _ = move(moves[i]["name"], D[i], moves[i]["amp"]) if moves[i]["name"] != "none" else ({"zoom": [], "dx": [], "dy": []}, None)
        zt += mv["zoom"]; dxt += mv["dx"]; dyt += mv["dy"]
        cin = cuts[i - 1] if i > 0 else None
        cout = cuts[i] if i < n - 1 else None
        if cin and cin["kind"] == "inplace":
            f = fx(cin["name"], "in", D[i], cin["w"])
            zt += f["zoom"]; dxt += f["dx"]; dyt += f["dy"]; rot += f["rot"]; post += f["post"]
        if cout and cout["kind"] == "inplace":
            f = fx(cout["name"], "out", D[i], cout["w"])
            zt += f["zoom"]; dxt += f["dx"]; dyt += f["dy"]; rot += f["rot"]; post += f["post"]
        body = (dyn_chain(crop, zt, dxt, dyt, rot, post) if (zt or dxt or dyt or rot or post) else static_chain(crop))
        fc.append(f"{src}trim=start={s:.4f}:end={e:.4f},setpts=PTS-STARTPTS,fps={FPS},{body}[m{i}]")
        if cout and cout["kind"] == "xfade":
            w = cout["w"]
            fc.append(f"[m{i}]split=2[m{i}a][m{i}b]")
            fc.append(f"[m{i}a]trim=end={D[i] - w:.4f},setpts=PTS-STARTPTS[p{i}]")
            fc.append(f"[m{i}b]trim=start={D[i] - w:.4f},setpts=PTS-STARTPTS,fps={FPS}[ta{i}]")
            sB, _eB, cropB = PIECES[i + 1]
            mvB, st = move(moves[i + 1]["name"], D[i + 1], moves[i + 1]["amp"]) if moves[i + 1]["name"] != "none" else (None, None)
            if st and (st[0] != 1.0 or st[1] != "0" or st[2] != "0"):
                hb = dyn_chain(cropB, [str(st[0])], [st[1]], [st[2]], [], [])
            else:
                hb = static_chain(cropB)
            fc.append(f"{src}trim=start={sB - w:.4f}:end={sB:.4f},setpts=PTS-STARTPTS,fps={FPS},{hb}[hd{i}]")
            fc.append(f"[ta{i}][hd{i}]xfade=transition={cout['name']}:duration={w:.4f}:offset=0,format=yuv420p[x{i}]")
            order += [f"[p{i}]", f"[x{i}]"]
        else:
            order.append(f"[m{i}]")
    fc.append("".join(order) + f"concat=n={len(order)}:v=1:a=0[vc0]")
    return fc


# ── معرض/اختبار ───────────────────────────────────────────────────────────
def _run(cmd):
    r = subprocess.run(cmd, capture_output=True, text=True)
    if r.returncode:
        raise RuntimeError(r.stderr[-600:])


def demo(outdir, which="all", style_dur=None):
    """يبني مصدر تجريبي (نمطين مختلفين يتناوبون) ويرندر كل انتقال لحاله: demo_<اسم>.mp4 (مقطعين × ~1.6ث)"""
    os.makedirs(outdir, exist_ok=True)
    src = os.path.join(outdir, "demo_src.mp4")
    if not os.path.exists(src):
        a = f"testsrc2=s={W_}x{H_}:r={FPS}:d=8"
        b = f"mandelbrot=s={W_}x{H_}:r={FPS}"
        c = f"smptebars=s={W_}x{H_}:r={FPS}:d=8"
        _run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", a, "-f", "lavfi", "-i", c, "-filter_complex",
              "[0:v]format=yuv420p[a];[1:v]format=yuv420p[b];[a][b]concat=n=2:v=1:a=0[v]", "-map", "[v]",
              "-t", "16", "-c:v", "libx264", "-crf", "16", "-pix_fmt", "yuv420p", src])
    # مقطع A من 1.0→3.0 (testsrc2) ، مقطع B من 9.0→11.0 (smptebars) — قبل B فيه 8 ثوانٍ من «مقدمة»
    pieces = [(1.0, 3.0, f"crop={W_}:{H_}:0:0"), (9.0, 11.0, f"crop={W_}:{H_}:0:0")]
    names = (list(XFADES) + list(INPLACE)) if which == "all" else [which]
    done = []
    for nm in names:
        kind, dd = info(nm)
        if not kind:
            print("مجهول:", nm)
            continue
        plan = {"cuts": [{"name": nm, "kind": kind, "w": dd if not style_dur else style_dur}],
                "moves": [{"name": "none", "amp": 0.08}] * 2, "D": [2.0, 2.0]}
        fc = video_graph(plan, pieces)
        out = os.path.join(outdir, f"demo_{nm}.mp4")
        _run(["ffmpeg", "-v", "error", "-y", "-i", src, "-filter_complex", ";".join(fc), "-map", "[vc0]", "-an",
              "-c:v", "libx264", "-crf", "18", "-pix_fmt", "yuv420p", out])
        done.append(out)
    return done


if __name__ == "__main__":
    a = sys.argv[1:]
    if a[:1] == ["demo"] and len(a) >= 2:
        r = demo(a[1], a[2] if len(a) > 2 else "all")
        print(f"✅ {len(r)} معرض بـ {a[1]}")
    else:
        print(__doc__)
