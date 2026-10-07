"""パワプロ風（夜）のスタイルを、パワプロ風（昼）のスタイルから自動で作る（build.py から呼ぶ）。
パワプロ風の指定（html.theme-pawa …）の色を夜の色に置き換えた指定を、html.theme-pawa.pawa-dark … として足す。
・明るい地（白・うすい水色・うすい緑など）→ 同じ色合いのまま暗く（明るさ16〜20%）
・うすい線 → 暗い線、太い紺の枠 → 明るめの青の枠
・暗い文字（紺など）→ 明るい文字、中くらいの文字 → うすい水色
・色付きの部品（黄色のボタン・名前の札・結果の札・球団の丸など）は、地も文字もそのまま"""
import colorsys
import re

KEEP = re.compile(r"ptile|otile|sptile|rtile|\.rc\b|rcw|offtag|\.badge|ykb|\.stp|pk-f|\.hd\b|hand|pressed|\.lamp|\.ldot|\.lv\b|wxhi|\.hb\b|\.stg\b|\.mt\.ng|first b|last b|\.ytag|\.tag\b|mvp|\.pcnt|\.abd|\.tb\b|\.tb-")
COL = re.compile(r"#[0-9A-Fa-f]{6}\b|#[0-9A-Fa-f]{3}\b|\bwhite\b", re.I)


def rgb(c):
    c = c.lower()
    if c == "white":
        return (1.0, 1.0, 1.0)
    c = c[1:]
    if len(c) == 3:
        c = "".join(x * 2 for x in c)
    return tuple(int(c[i:i + 2], 16) / 255 for i in (0, 2, 4))


def hexc(r, g, b):
    return "#%02X%02X%02X" % tuple(max(0, min(255, round(x * 255))) for x in (r, g, b))


def hls(c):
    return colorsys.rgb_to_hls(*rgb(c))


def light_bg(c):   # 明るい地（夜は暗くする）
    h, l, s = hls(c)
    return l >= .86 or (l >= .80 and s < .35)


# 夜の地の色は紺の1系統だけ（うすい緑・うすい黄色などの色味は消す。色がばらばらにならないように）
def dark_bg(c, deeper=False):
    h, l, s = hls(c)
    return hexc(*colorsys.hls_to_rgb(0.605, .155 + min(1 - l, .2) * .35, .42))


def text_c(c):
    h, l, s = hls(c)
    if l <= .36:
        return hexc(*colorsys.hls_to_rgb(h, .91, min(s, .45)))
    if l <= .62:
        return hexc(*colorsys.hls_to_rgb(h, .76, min(s, .45)))
    return None


def line_c(c):
    u = c.upper()
    if u in ("#1D3E7C", "#163A78", "#13264A"):
        return "#3F65AE"   # パネルの太い紺の枠
    h, l, s = hls(c)
    if l >= .72:
        return hexc(*colorsys.hls_to_rgb(h if s >= .12 else .61, .27, min(max(s, .3), .5)))
    return None


def sub(v, fn):
    return COL.sub(lambda m: fn(m.group(0)) or m.group(0), v)


def white_rgba(v):
    return re.sub(r"rgba\(\s*255\s*,\s*255\s*,\s*255\s*,\s*(0?\.\d+|1)\s*\)",
                  lambda m: f"rgba(22,36,63,{m.group(1)})" if float(m.group(1)) >= .5 else m.group(0), v)


def build(css):
    css = re.sub(r"/\*.*?\*/", "", css, flags=re.S)
    out = []
    for sel, body in re.findall(r"([^{}@]+)\{([^{}]*)\}", css):
        sel = sel.strip()
        if "theme-pawa" not in sel or "not(.theme-pawa)" in sel or "pawa-dark" in sel:
            continue
        decls = [d.strip() for d in body.split(";") if d.strip() and ":" in d]
        bgv = " ".join(d.split(":", 1)[1] for d in decls if d.split(":", 1)[0].strip().startswith("background"))
        cols = COL.findall(bgv)
        colored_bg = any(not light_bg(c) for c in cols)
        keep = bool(KEEP.search(sel))
        new = []
        for d in decls:
            p, v = [x.strip() for x in d.split(":", 1)]
            nv = v
            if keep:
                pass
            elif p == "color":
                if not colored_bg:
                    nv = sub(v, text_c)
            elif p.startswith("background"):
                if not colored_bg:
                    # 球団の色を混ぜた地（color-mix(… var(--tc) …, 白)）→ 夜は混ぜずに紺だけ（球団の色は左の帯などに残る）
                    v2 = re.sub(r"color-mix\(in srgb,\s*var\(--tc[^)]*\)\s*\d+%,\s*(#[0-9A-Fa-f]{3,6}|white)\)", lambda m: m.group(1), v)
                    nv = white_rgba(sub(v2, lambda c: dark_bg(c) if light_bg(c) else None))
            elif p.startswith("border") or p.startswith("outline"):
                nv = sub(v, line_c)
            elif p in ("fill", "stroke"):
                nv = sub(v, text_c)
            elif p == "text-shadow" and not colored_bg:
                nv = white_rgba(v)
            if nv != v:
                new.append(f"{p}:{nv}")
        # 色付きの状態（1位の金・最下位の赤・選んだボタンなど）の指定は、そのままの色で夜（ライト）用にも出し直す。
        # 出し直さないと、同じ強さの「ふつうの状態」の夜（ライト）用の指定に負けて、色が消える
        if colored_bg and not new:
            new = [f"{p}:{v}" for p, v in ([x.strip() for x in d.split(":", 1)] for d in decls) if p.startswith("background") or p in ("color", "box-shadow")]
        if not new:
            continue
        parts = []
        for s1 in sel.split(","):
            s1 = s1.strip()
            if "html.theme-pawa" not in s1:
                continue
            s1 = s1.replace("html.theme-pawa", "html.theme-pawa.pawa-dark", 1)
            # 選んだ状態のボタン（黄色）の色は変えない：ボタンの指定は「選んでいないとき」だけに効かせる
            if re.search(r"(button|\.tbtn|\.chip)(\.[\w-]+)*(:[\w-]+)?$", s1) and "pressed" not in s1:
                s1 = re.sub(r"(:[\w-]+)?$", lambda m: ':not([aria-pressed="true"]):not(.on)' + (m.group(1) or ""), s1, count=1)
            parts.append(s1)
        if not parts:
            continue
        out.append(",".join(parts) + "{" + ";".join(new) + "}")
    return "\n".join(out)
