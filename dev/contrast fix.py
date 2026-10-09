"""4つの見た目（パワプロ風の昼・夜、スタイリッシュの黒・白）で、文字と地の明るさの差を一般的な基準（ふつうの文字4.5）以上にそろえる（build.py から呼ぶ）。
できあがったスタイル（自動で作った夜・ライトの指定も含む）を見て、その見た目で効く「文字の色」の指定ごとに、
・同じ指定に地の色があればその地、なければその見た目のいちばん暗い（ライト）／いちばん明るい（ダーク）パネルの色、と比べ、
・足りなければ、色合いはそのままで文字を濃く（ライト）／明るく（ダーク）した指定を、いちばん強い形で足す。
・色付きの札（白い文字の札）は、札の地のほうを濃くする。
・縁取りの文字（空の上の白抜きの見出し）や、ライトの白い文字・ダークの黒い文字（色付きの札の上の文字）は対象外。"""
import colorsys
import re

COL = re.compile(r"#[0-9A-Fa-f]{6}\b|#[0-9A-Fa-f]{3}\b|\bwhite\b|\bblack\b", re.I)
MODES = {
    # 名前: (このモードで効く指定の見分け方, 足す指定の頭, ライトか, 比べる地)
    "pawa_day": (lambda s: "html.theme-pawa" in s and "pawa-dark" not in s and "not(.theme-pawa)" not in s, "html.theme-pawa:not(.pawa-dark)", True, "#E3ECF7"),
    "pawa_night": (lambda s: "html.theme-pawa.pawa-dark" in s, "html.theme-pawa.pawa-dark", False, "#1F3155"),
    "sty_dark": (lambda s: "theme-pawa" not in s and "sty-light" not in s or ("not(.theme-pawa)" in s and "sty-light" not in s), "html:not(.theme-pawa):not(.sty-light)", False, "#1E1E22"),
    "sty_light": (lambda s: "html.sty-light" in s, "html.sty-light:not(.theme-pawa)", True, "#E6E7EC"),
}


def split_sel(sel):
    """セレクタをカンマで分ける。:is(a,b) や :not(a,b) のかっこの中のカンマでは分けない（10/9 分けてしまい、壊れた指定のせいでその後ろのスタイルが全部効かなくなった）"""
    out, depth, cur = [], 0, ""
    for ch in sel:
        if ch == "(":
            depth += 1
        elif ch == ")":
            depth -= 1
        if ch == "," and depth == 0:
            out.append(cur)
            cur = ""
        else:
            cur += ch
    out.append(cur)
    return out


def rgb(c):
    c = c.lower()
    if c == "white": return (1.0, 1.0, 1.0)
    if c == "black": return (0.0, 0.0, 0.0)
    c = c[1:]
    if len(c) == 3: c = "".join(x * 2 for x in c)
    return tuple(int(c[i:i + 2], 16) / 255 for i in (0, 2, 4))


def lum(c):
    f = lambda x: x / 12.92 if x <= .03928 else ((x + .055) / 1.055) ** 2.4
    r, g, b = rgb(c) if isinstance(c, str) else c
    return .2126 * f(r) + .7152 * f(g) + .0722 * f(b)


def cr(a, b):
    la, lb = lum(a), lum(b)
    return (max(la, lb) + .05) / (min(la, lb) + .05)


def hexc(t):
    return "#%02X%02X%02X" % tuple(max(0, min(255, round(x * 255))) for x in t)


def adjust(c, bg, light, need=4.5):
    """色合いはそのままで、地との差が need 以上になるまで濃く（ライト）／明るく（ダーク）する"""
    h, l, s = colorsys.rgb_to_hls(*rgb(c))
    for _ in range(80):
        if cr(hexc(colorsys.hls_to_rgb(h, l, s)), bg) >= need:
            return hexc(colorsys.hls_to_rgb(h, l, s))
        l = l - .01 if light else l + .01
        if l <= 0 or l >= 1:
            break
    return "#101218" if light else "#F2F4F8"


def build(css):
    css = re.sub(r"/\*.*?\*/", "", css, flags=re.S)
    out = []
    for name, (applies, head, light, panel) in MODES.items():
        for sel, body in re.findall(r"([^{}@]+)\{([^{}]*)\}", css):
            sel = sel.strip()
            if not sel or sel.startswith(("from", "to")) or re.match(r"^\d", sel):
                continue
            parts = [s1.strip() for s1 in split_sel(sel) if applies(s1.strip()) and not re.search(r"\.tb\b|\.tb-", s1)]   # .tb：トーナメント表（どの見た目でも夜空の舞台。色は手で決めている）
            if not parts:
                continue
            decls = dict((d.split(":", 1)[0].strip(), d.split(":", 1)[1].strip()) for d in body.split(";") if ":" in d)
            col = decls.get("color", "")
            m = COL.search(col)
            if not m or "var(" in col:
                continue
            if "text-shadow" in decls and decls["text-shadow"].count("rgb") + decls["text-shadow"].count("#") >= 3:
                continue
            fg = m.group(0)
            bgv = " ".join(v for k, v in decls.items() if k.startswith("background")).replace("!important", "").strip()
            bgs = COL.findall(bgv)
            l_fg = colorsys.rgb_to_hls(*rgb(fg))[1]
            if bgs:   # 同じ指定に地がある（札など）
                bg = bgs[len(bgs) // 2]
                if cr(fg, bg) >= 4.5:
                    continue
                # 白い文字の札：札の地を濃く。濃い文字の札：文字をもっと濃く
                if l_fg >= .9:
                    nb = adjust(bg, "#FFFFFF", True)
                    new_bg = COL.sub(lambda mm: adjust(mm.group(0), "#FFFFFF", True) if colorsys.rgb_to_hls(*rgb(mm.group(0)))[1] > .35 else mm.group(0), bgv)
                    decl = f"background:{new_bg} !important"
                else:
                    decl = f"color:{adjust(fg, bg, colorsys.rgb_to_hls(*rgb(bg))[1] > .5)} !important"
            else:
                if light and l_fg >= .93: continue      # ライトの白い文字＝色付きの札の上
                if not light and l_fg <= .12: continue  # ダークの黒い文字＝明るい札の上
                if cr(fg, panel) >= 4.5:
                    continue
                decl = f"color:{adjust(fg, panel, light)} !important"
            sels = []
            for s1 in parts:
                s2 = re.sub(r"^html(\.[\w-]+|:not\([^)]*\))*", "", s1).strip()
                if s2.startswith(":root") or not s2:
                    continue
                last = s2.split()[-1]
                if "::" in last:
                    continue
                sels.append(f"{head} {s2}:not(#z)" if not re.search(r":[\w-]+(\([^)]*\))?$", s2) else f"{head} {s2}")
            if sels:
                out.append(",".join(sels) + "{" + decl + "}")
    return "\n".join(dict.fromkeys(out))
