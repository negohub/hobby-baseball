"""スタイリッシュ（ライト）のスタイルを、スタイリッシュ（もともと黒い画面）のスタイルから自動で作る（build.py から呼ぶ）。
パワプロ風ではない指定（html:not(.theme-pawa) … と、テーマの付いていない指定）の色を、明るい画面用に置き換えた指定を
html.sty-light:not(.theme-pawa) … として足す。
・暗い地（黒・濃い灰色）→ 白〜うすい灰色、白の半透明の地 → 黒の半透明
・明るい文字（白・うすい灰色）→ 黒〜濃い灰色、中くらいの文字 → 少し濃く
・暗い線 → うすい灰色の線
・色付きの部品（球団の色・赤・青・黄など）はそのまま"""
import colorsys
import re

COL = re.compile(r"#[0-9A-Fa-f]{6}\b|#[0-9A-Fa-f]{3}\b|\bwhite\b|\bblack\b", re.I)
KEEP = re.compile(r"\.badge|ykb|offtag|\.stp|lamp|\.ldot|wxhi|\.hb\b|#splash|\.sp-icon|\.sp-ring|\.sp-dot|pressed|\.on\b|\.tb\b|\.tb-")   # .tb：CS・日本シリーズのトーナメント表（どの見た目でも夜空の舞台）


def rgb(c):
    c = c.lower()
    if c == "white":
        return (1.0, 1.0, 1.0)
    if c == "black":
        return (0.0, 0.0, 0.0)
    c = c[1:]
    if len(c) == 3:
        c = "".join(x * 2 for x in c)
    return tuple(int(c[i:i + 2], 16) / 255 for i in (0, 2, 4))


def hexc(r, g, b):
    return "#%02X%02X%02X" % tuple(max(0, min(255, round(x * 255))) for x in (r, g, b))


def hls(c):
    return colorsys.rgb_to_hls(*rgb(c))


def dark_bg(c):
    h, l, s = hls(c)
    return l <= .26 and (s < .35 or l <= .12)


def light_bg(c):
    h, l, s = hls(c)
    return hexc(*colorsys.hls_to_rgb(h, min(1, .985 - l * .45), min(s, .25)))


def text_c(c):
    h, l, s = hls(c)
    if s > .45:   # 色の付いた文字（黄・緑・水色・赤など）：白い地でも読めるように同じ色合いで濃く
        return hexc(*colorsys.hls_to_rgb(h, .36, min(s, .85))) if l > .42 else None
    if l >= .78:
        return hexc(*colorsys.hls_to_rgb(h, .09 + (1 - l) * .6, min(s, .2)))
    if l >= .45:
        return hexc(*colorsys.hls_to_rgb(h, .36, min(s, .2)))
    return None


def line_c(c):
    h, l, s = hls(c)
    if l <= .3 and s < .3:
        return hexc(*colorsys.hls_to_rgb(h, .86 + l * .2, min(s, .15)))
    return None


def sub(v, fn):
    return COL.sub(lambda m: fn(m.group(0)) or m.group(0), v)


def black_rgba(v):   # 黒の半透明の地（上の見出しのぼかしなど）→ 明るい灰色の半透明
    return re.sub(r"rgba\(\s*0\s*,\s*0\s*,\s*0\s*,\s*(0?\.\d+|1)\s*\)", lambda m: f"rgba(242,243,247,{m.group(1)})", v)


def dark_rgba(v):   # 暗い色の半透明の地（シートのくもりガラスなど）→ 明るい半透明
    def f(m):
        r, g, b, a = int(m.group(1)), int(m.group(2)), int(m.group(3)), float(m.group(4))
        return f"rgba(248,248,250,{a})" if max(r, g, b) <= 60 and a >= .5 else m.group(0)
    return re.sub(r"rgba\(\s*(\d+)\s*,\s*(\d+)\s*,\s*(\d+)\s*,\s*(0?\.\d+|1)\s*\)", f, v)


def white_rgba(v):   # 白の半透明（黒い画面の上のうすい地・線）→ 黒の半透明
    return re.sub(r"rgba\(\s*255\s*,\s*255\s*,\s*255\s*,\s*(0?\.\d+|1)\s*\)",
                  lambda m: f"rgba(20,24,40,{min(.12, float(m.group(1)) * .9):.3f})" if float(m.group(1)) < .5 else m.group(0), v)


def build(css):
    css = re.sub(r"/\*.*?\*/", "", css, flags=re.S)
    out = []
    for sel, body in re.findall(r"([^{}@]+)\{([^{}]*)\}", css):
        sel = sel.strip()
        if not sel or sel.startswith(("from", "to")) or re.match(r"^\d", sel) or "theme-pawa" in sel and "not(.theme-pawa)" not in sel or "sty-light" in sel:
            continue
        decls = [d.strip() for d in body.split(";") if d.strip() and ":" in d]
        bgv = " ".join(d.split(":", 1)[1] for d in decls if d.split(":", 1)[0].strip().startswith("background"))
        cols = COL.findall(bgv)
        colored_bg = any(not dark_bg(c) for c in cols)
        keep = bool(KEEP.search(sel))
        new = []
        for d in decls:
            p, v = [x.strip() for x in d.split(":", 1)]
            nv = v
            if keep:
                pass
            elif p.startswith("--"):   # 色の変数（:root）：名前で地・線・文字を分ける
                if re.search(r"line|border", p):
                    nv = white_rgba(sub(v, line_c))
                elif re.search(r"text|muted|dim|fg", p):
                    nv = sub(v, text_c)
                elif re.search(r"bg|card|p1|p2|panel", p):
                    nv = white_rgba(sub(v, lambda c: light_bg(c) if dark_bg(c) else None))
            elif p == "color":
                if not colored_bg:
                    nv = sub(v, text_c)
            elif p.startswith("background"):
                if not colored_bg:
                    nv = dark_rgba(black_rgba(white_rgba(sub(v, lambda c: light_bg(c) if dark_bg(c) else None))))
            elif p.startswith("border") or p.startswith("outline"):
                nv = white_rgba(sub(v, line_c))
            elif p in ("fill", "stroke"):
                nv = sub(v, text_c)
            if nv != v:
                new.append(f"{p}:{nv}")
        # 色付きの状態（1位の金・最下位の赤・選んだボタンなど）の指定は、そのままの色で夜（ライト）用にも出し直す。
        # 出し直さないと、同じ強さの「ふつうの状態」の夜（ライト）用の指定に負けて、色が消える
        if colored_bg and not new:
            # 黒い画面で「白い札・黒い文字」だった選んだ状態（月度・打撃・1位など）は、白い画面では「黒い札・白い文字」に反転（白の上の白で見えなくならないように）
            def inv_bg(c):
                h, l, s_ = hls(c)
                return "#15171D" if l >= .9 and s_ < .2 else None
            def inv_tx(c):
                h, l, s_ = hls(c)
                return "#FFFFFF" if l <= .15 else None
            new = []
            for p, v in ([x.strip() for x in d.split(":", 1)] for d in decls):
                if p.startswith("background"):
                    new.append(f"{p}:{sub(v, inv_bg)}")
                elif p == "color":
                    new.append(f"{p}:{sub(v, inv_tx)}")
                elif p == "box-shadow":
                    new.append(f"{p}:{v}")
        if not new:
            continue
        parts = []
        for s1 in sel.split(","):
            s1 = s1.strip()
            if "theme-pawa" in s1 and "not(.theme-pawa)" not in s1:
                continue
            if "html:not(.theme-pawa)" in s1:
                s1 = s1.replace("html:not(.theme-pawa)", "html.sty-light:not(.theme-pawa)", 1)
            elif s1.startswith("html") or s1.startswith(":root"):
                s1 = re.sub(r"^(html|:root)", r"html.sty-light:not(.theme-pawa)", s1, count=1)
            else:
                s1 = "html.sty-light:not(.theme-pawa) " + s1
            if re.search(r"(button|\.tbtn|\.chip)(\.[\w-]+)*(:[\w-]+)?$", s1) and "pressed" not in s1:
                s1 = re.sub(r"(:[\w-]+)?$", lambda m: ':not([aria-pressed="true"]):not(.on)' + (m.group(1) or ""), s1, count=1)
            parts.append(s1)
        if parts:
            out.append(",".join(parts) + "{" + ";".join(new) + "}")
    return "\n".join(out)
