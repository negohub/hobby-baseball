"""NPB公式の月別日程ページ（schedule_MM_detail.html）を読み、
セ・リーグ球団が絡む試合を data/latest.json に保存する。
・一度「試合終了」になった試合は、ページ側の反映遅れで「試合前」に戻さない
・読み取りに失敗した月は取り直し、それでもダメなら前回のデータを残す
・ページ上部の当日の「試合終了」欄からも結果を拾う
"""
import json
import os
import re
from urllib.parse import quote
import sys
import time
import unicodedata
from datetime import datetime, timedelta, timezone

import requests
from bs4 import BeautifulSoup, NavigableString

JST = timezone(timedelta(hours=9))
MONTHS = range(3, 11)  # 3月〜10月
OUT = os.path.join(os.path.dirname(__file__), "..", "data", "latest.json")

TEAMS = [
    ("ソフトバンク", "H"), ("日本ハム", "F"), ("オリックス", "B"),
    ("ヤクルト", "S"), ("DeNA", "DB"), ("ロッテ", "M"), ("西武", "L"),
    ("楽天", "E"), ("ジャイアンツ", "G"), ("巨人", "G"), ("阪神", "T"), ("中日", "D"), ("広島", "C"),
]
CL = {"DB", "G", "T", "D", "S", "C"}
PL = {"H", "F", "B", "E", "L", "M"}
TEAM_RE = re.compile("|".join(re.escape(n) for n, _ in TEAMS))
CODE = dict(TEAMS)
ABBR = {"神": "T", "巨": "G", "デ": "DB", "中": "D", "広": "C", "ヤ": "S",
        "ソ": "H", "日": "F", "オ": "B", "楽": "E", "西": "L", "ロ": "M"}

URLCODE = {"g": "G", "db": "DB", "t": "T", "d": "D", "c": "C", "s": "S",
           "h": "H", "f": "F", "b": "B", "e": "E", "l": "L", "m": "M"}
DATE_RE = re.compile(r"^(\d{1,2})/(\d{1,2})")
SCORE_HREF = re.compile(r"/scores/(\d{4})/(\d{2})(\d{2})/([a-z]+)-([a-z]+)-\d+")


def norm(s):
    return unicodedata.normalize("NFKC", s or "").strip()


def key(g):
    return (g["d"], g["h"], g["a"])


def parse_month(html, season):
    soup = BeautifulSoup(html, "html.parser")
    games = []
    for table in soup.find_all("table"):
        cur = None
        for tr in table.find_all("tr"):
            cells = [norm(td.get_text(" ", strip=True)) for td in tr.find_all(["td", "th"])]
            if not cells:
                continue
            m = DATE_RE.match(cells[0])
            if m:
                cur = f"{season}-{int(m.group(1)):02d}-{int(m.group(2)):02d}"
                cells = cells[1:]
            if cur is None:
                continue
            idx = next((i for i, c in enumerate(cells) if len(TEAM_RE.findall(c)) >= 2), None)
            if idx is None:
                continue
            card = cells[idx]
            if "予備日" in card:
                continue
            found = list(TEAM_RE.finditer(card))
            home, away = CODE[found[0].group()], CODE[found[1].group()]
            middle = card[found[0].end():found[1].start()]
            vcell = cells[idx + 1] if idx + 1 < len(cells) else ""
            tm = re.search(r"(\d{1,2}:\d{2})", vcell)
            venue = re.sub(r"\s*\d{1,2}:\d{2}.*$", "", vcell).replace(" ", "")
            pitch = " ".join(cells[idx + 2:])
            g = {"d": cur, "h": home, "a": away, "v": venue}
            if "中止" in card or "ノーゲーム" in card:
                g["st"] = "canc"
            else:
                sc = re.search(r"(\d+)\s*-\s*(\d+)", middle)
                if sc and re.search(r"(勝|分)\s*:", pitch):
                    g.update(st="final", hs=int(sc.group(1)))
                    g["as"] = int(sc.group(2))
                elif sc:
                    g["st"] = "live"
                else:
                    g["st"] = "sched"
                    if tm:
                        g["t"] = tm.group(1)
            games.append(g)
    return games


def parse_ticker(html):
    """ページ上部の当日の試合欄から「試合終了」の結果を拾う。並びが判別できないものは使わない。"""
    soup = BeautifulSoup(html, "html.parser")
    res = {}
    for a in soup.find_all("a", href=True):
        m = SCORE_HREF.search(a["href"])
        if not m:
            continue
        parts = []
        for el in a.descendants:
            if isinstance(el, NavigableString):
                parts.append(str(el))
            elif getattr(el, "name", None) == "img" and el.get("alt"):
                parts.append(f" {el['alt']} ")
        text = norm(" ".join(parts))
        home, away = URLCODE.get(m.group(4)), URLCODE.get(m.group(5))
        # 中止・ノーゲーム：月の日程ページより先に、当日の試合欄に「中止」と出る（日程ページの更新を待たずに反映する）
        if home and away and re.search(r"中止|ノーゲーム", text) and "試合終了" not in text:
            res[(f"{m.group(1)}-{m.group(2)}-{m.group(3)}", home, away)] = "canc"
            continue
        if "試合終了" not in text:
            continue
        sc = re.search(r"(\d+)\s*-\s*(\d+)", text)
        names = [CODE[x.group()] for x in TEAM_RE.finditer(text)]
        if not (home and away and sc) or len(names) < 2 or {names[0], names[1]} != {home, away}:
            continue
        s1, s2 = int(sc.group(1)), int(sc.group(2))
        hs, as_ = (s1, s2) if names[0] == home else (s2, s1)
        d = f"{m.group(1)}-{m.group(2)}-{m.group(3)}"
        res[(d, home, away)] = (hs, as_)
    return res


BROWSER_UA = {"User-Agent": "Mozilla/5.0 (iPhone; CPU iPhone OS 18_0 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/18.0 Mobile/15E148 Safari/604.1",
              "Accept-Language": "ja,en;q=0.8", "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8"}


def fetch(url):
    denied = False
    for i in range(3):
        try:
            # 断られた（403など）ときは、ふつうのブラウザと同じ名乗りでもう一度（球団のサイトの一部）
            r = requests.get(url, timeout=30, headers=BROWSER_UA if denied else {"User-Agent": "Mozilla/5.0 (hobby-baseball)"})
            if r.status_code in (401, 403, 406, 429):
                denied = True
            if r.status_code == 200:
                try:
                    return r.content.decode("utf-8")
                except UnicodeDecodeError:
                    r.encoding = r.apparent_encoding or "utf-8"
                    return r.text
            print(f"  HTTP {r.status_code}（{i + 1}回目）")
        except requests.RequestException as e:
            print(f"  取得失敗（{i + 1}回目）: {e}")
        time.sleep(3)
    return None


# ---------- 成績（スポーツナビ） ----------
YAHOO = "https://baseball.yahoo.co.jp/npb"
# スポナビの個人成績の全項目（type= の値 → 表の見出し）
BAT_CATS = {"avg": "打率", "g": "試合", "pa": "打席", "ab": "打数", "h": "安打", "h2b": "二塁打", "h3b": "三塁打", "hr": "本塁打",
            "tb": "塁打", "rbi": "打点", "r": "得点", "so": "三振", "bb": "四球", "hbp": "死球", "sh": "犠打", "sf": "犠飛",
            "sb": "盗塁", "cs": "盗塁死", "gidp": "併殺打", "obp": "出塁率", "slg": "長打率", "ops": "OPS", "risp": "得点圏", "e": "失策"}
PIT_CATS = {"era": "防御率", "g": "登板", "gs": "先発", "cg": "完投", "sho": "完封", "qs": "QS", "w": "勝利", "l": "敗戦",
            "hld": "ホールド", "hldp": "HP", "sv": "セーブ", "wpct": "勝率", "ip": "投球回", "h": "被安打", "hr": "被本塁打",
            "so": "奪三振", "k9": "奪三振率", "bb": "与四球", "hbp": "与死球", "wp": "暴投", "bk": "ボーク", "r": "失点",
            "er": "自責点", "avg": "被打率", "kbb": "K/BB", "qs_pct": "QS率", "whip": "WHIP"}
TEAM_KEYS = ["打率", "本塁打", "得点", "盗塁", "防御率", "失点", "失策"]
STAMP_RE = re.compile(r"(\d{4})/(\d{1,2})/(\d{1,2})\s+(\d{1,2}):(\d{2})")


def clean(s):
    return re.sub(r"\s+", "", norm(s))


def stamp_of(html):
    m = STAMP_RE.search(norm(BeautifulSoup(html, "html.parser").get_text(" ")))
    return (int(m.group(1)), int(m.group(2)), int(m.group(3)), int(m.group(4)), int(m.group(5))) if m else None


def parse_yahoo_rank(html, label, limit=10):
    """部門別の個人成績ページ（最大30位）から上位を取る"""
    soup = BeautifulSoup(html, "html.parser")
    for table in soup.find_all("table"):
        rows = table.find_all("tr")
        if not rows:
            continue
        head = [clean(c.get_text(" ", strip=True)) for c in rows[0].find_all(["th", "td"])]
        if "選手名" not in head or label not in head:
            continue
        ni, vi = head.index("選手名"), head.index(label)
        res = []
        for tr in rows[1:]:
            cells = [norm(c.get_text(" ", strip=True)) for c in tr.find_all(["td", "th"])]
            if len(cells) != len(head) or not cells[0].isdigit():
                continue
            m = re.match(r"^(.+?)\s*\(\s*(.)\s*\)$", cells[ni])
            if not m or m.group(2) not in ABBR:
                continue
            r = int(cells[0])
            if r > limit:
                break
            res.append({"r": r, "n": m.group(1).strip(), "t": ABBR[m.group(2)], "v": cells[vi]})
        if res:
            return res
    return None


def parse_yahoo_team(html, league=None):
    """リーグの順位表（詳細）からチーム成績を取る。「-」の項目は入れない"""
    league = league or CL
    soup = BeautifulSoup(html, "html.parser")
    for table in soup.find_all("table"):
        rows = table.find_all("tr")
        if not rows:
            continue
        head = [clean(c.get_text(" ", strip=True)) for c in rows[0].find_all(["th", "td"])]
        if "チーム名" not in head or "防御率" not in head:
            continue
        ti = head.index("チーム名")
        out = {}
        for tr in rows[1:]:
            cells = [norm(c.get_text(" ", strip=True)) for c in tr.find_all(["td", "th"])]
            if len(cells) != len(head):
                continue
            m = TEAM_RE.search(cells[ti])
            if not m or CODE[m.group()] not in league:
                continue
            out[CODE[m.group()]] = {k: cells[head.index(k)] for k in TEAM_KEYS
                                    if k in head and re.search(r"\d", cells[head.index(k)])}
        if len(out) == 6:
            return out
    return None


# ---------- チーム内の成績（スポナビの球団ごとの打撃成績・投手成績：個人ランキングと同じ全項目） ----------
YAHOO_TEAM = {"G": 1, "S": 2, "DB": 3, "D": 4, "T": 5, "C": 6, "L": 7, "F": 8, "M": 9, "B": 11, "H": 12, "E": 376}
TSTATS_EVERY = 3 * 3600   # 3時間に1回


def parse_team_stats(html, team_name):
    """スポナビの「打撃成績」「投手成績」の表：見出し（打率・試合…）と、選手ごとの行 [名前, 位置, 値…]。
    その球団のページか（見出しの球団名）も確かめる。出場のない選手（全部「-」）は入れない"""
    soup = BeautifulSoup(html, "html.parser")
    title = norm((soup.find("title") or soup).get_text(" "))
    if team_name and team_name not in title:
        return None
    for table in soup.find_all("table"):
        rows = table.find_all("tr")
        if not rows:
            continue
        head = [re.sub(r"\s+", "", norm(c.get_text(" ", strip=True))) for c in rows[0].find_all(["th", "td"])]
        if "選手名" not in head:
            continue
        i_n, i_p = head.index("選手名"), (head.index("位置") if "位置" in head else None)
        cols = [h for k, h in enumerate(head) if k not in (i_n, i_p, head.index("背番号") if "背番号" in head else -1)]
        out = []
        for tr in rows[1:]:
            c = [norm(x.get_text(" ", strip=True)) for x in tr.find_all(["td", "th"])]
            if len(c) != len(head) or c[i_n] == "選手名":
                continue
            vals = [v for k, v in enumerate(c) if k not in (i_n, i_p, head.index("背番号") if "背番号" in head else -1)]
            if all(v in ("-", "") for v in vals):
                continue
            out.append([re.sub(r"\s+", " ", c[i_n]).strip(), c[i_p] if i_p is not None else ""] + vals)
        return {"cols": cols, "rows": out}
    return None


def fetch_team_stats(season, old):
    prev = (old or {}).get("tstats") or {}
    now = datetime.now(JST)
    if prev.get("at") and (now - datetime.fromisoformat(prev["at"])).total_seconds() < TSTATS_EVERY and prev.get("teams"):
        return prev
    names = {"G": "巨人", "S": "ヤクルト", "DB": "DeNA", "D": "中日", "T": "阪神", "C": "広島", "L": "西武", "F": "日本ハム", "M": "ロッテ", "B": "オリックス", "H": "ソフトバンク", "E": "楽天"}
    res = {"at": now.isoformat(timespec="seconds"), "asof": now.strftime("%-m/%-d %H:%M"), "cols": dict(prev.get("cols") or {}), "teams": dict(prev.get("teams") or {})}
    ok = 0
    for t, yid in YAHOO_TEAM.items():
        cur = dict(res["teams"].get(t) or {})
        for kind, page in (("bat", "battingstats"), ("pit", "pitchingstats")):
            html = fetch(f"{YAHOO}/teams/{yid}/{page}")
            got = parse_team_stats(html, names[t]) if html else None
            if got and got["rows"]:
                res["cols"][kind] = got["cols"]
                cur[kind] = got["rows"]
                ok += 1
            time.sleep(0.3)
        res["teams"][t] = cur
    print(f"[チーム内の成績] {ok}/24 ページ（スポナビ）")
    return res if ok else prev or None


def fetch_stats(season, old_stats, kind=1):
    """チーム成績と個人ランキング（kind=1 セ・リーグ、2 パ・リーグ）。取れなかった部分は前回の値を残す"""
    league, tag = (CL, "") if kind == 1 else (PL, "パ・")
    st = json.loads(json.dumps(old_stats or {}))
    st["team"] = st.get("team") if isinstance(st.get("team"), dict) else {}
    st.setdefault("leaders", {})
    stamps = []
    html = fetch(f"{YAHOO}/standings/detail/{kind}")
    tbl = parse_yahoo_team(html, league) if html else None
    if tbl:
        for t, row in tbl.items():
            cur = st["team"].get(t)
            cur = cur if isinstance(cur, dict) and "bat" not in cur else {}
            cur.update(row)
            st["team"][t] = cur
        stamps.append(stamp_of(html))
        print(f"[{tag}成績] チーム成績 OK")
    else:
        print(f"[{tag}成績] チーム成績 読み取れず（前回の値を使用）")
    # 個人ランキング：スポナビの成績の更新時刻が前回と同じなら、全項目の取り直しはしない（負担を減らす）
    probe = fetch(f"{YAHOO}/stats/batter?gameKindId={kind}&type=avg")
    probe_stamp = stamp_of(probe) if probe else None
    want = [f"b_{k}" for k in BAT_CATS] + [f"p_{k}" for k in PIT_CATS]
    have_all = all(k in st["leaders"] for k in want)
    if probe_stamp and have_all and st.get("rank_stamp") == list(probe_stamp):
        print(f"[{tag}成績] 個人ランキングは前回から更新なし（取り直さない）")
        stamps.append(probe_stamp)
    else:
        leaders = {k: v for k, v in st["leaders"].items() if k in want}
        for who, pre, cats in (("batter", "b_", BAT_CATS), ("pitcher", "p_", PIT_CATS)):
            for key, label in cats.items():
                html = probe if (who == "batter" and key == "avg") else fetch(f"{YAHOO}/stats/{who}?gameKindId={kind}&type={key}")
                rows = parse_yahoo_rank(html, label) if html else None
                if rows:
                    leaders[pre + key] = rows
                    stamps.append(stamp_of(html))
                else:
                    print(f"[{tag}成績] ランキング {label} 読み取れず（前回の値を使用）")
                if not (who == "batter" and key == "avg"):
                    time.sleep(1)
        st["leaders"] = leaders
        if probe_stamp:
            st["rank_stamp"] = list(probe_stamp)
    stamps = [x for x in stamps if x]
    if stamps:
        y, mo, d, h, mi = max(stamps)
        st["asof"] = f"{mo}/{d} {h}:{mi:02d}"
    st["src"] = "スポーツナビ"
    print(f"[{tag}成績] {st.get('asof')} 更新分 ランキング{len(st['leaders'])}部門")
    return st


def fetch_prev_order(season, old, lg="c"):
    """前年の最終順位（日程タブの球団の並びに使う）。一度取れたら次から取りに行かない"""
    league = CL if lg == "c" else PL
    prev = (old or {}).get("prev_order" if lg == "c" else "prev_order_p")
    if prev and prev.get("season") == season - 1 and len(prev.get("order", [])) == 6:
        return prev
    html = fetch(f"https://npb.jp/bis/{season - 1}/stats/std_{lg}.html")
    if html:
        soup = BeautifulSoup(html, "html.parser")
        for table in soup.find_all("table"):
            order = []
            for tr in table.find_all("tr"):
                cells = tr.find_all(["td", "th"])
                m = TEAM_RE.search(norm(cells[0].get_text(" ", strip=True))) if cells else None
                if m and CODE[m.group()] in league and CODE[m.group()] not in order:
                    order.append(CODE[m.group()])
            if len(order) == 6:
                print(f"[前年順位] {season - 1}年 {'セ' if lg == 'c' else 'パ'}: {order}")
                return {"season": season - 1, "order": order}
    print("[前年順位] 取得できず")
    return prev


ROSTER_CODE = {"T": "t", "G": "g", "DB": "db", "D": "d", "C": "c", "S": "s",
               "H": "h", "F": "f", "B": "b", "E": "e", "L": "l", "M": "m"}
POSITIONS = ("投手", "捕手", "内野手", "外野手")


def parse_roster(html):
    """NPBの選手一覧ページから、背番号・名前・ポジション・育成かどうかを取る"""
    soup = BeautifulSoup(html, "html.parser")
    out = []
    for table in soup.find_all("table"):
        h = table.find_previous(["h3", "h4"])
        dev = bool(h and "育成" in h.get_text())
        pos = None
        for tr in table.find_all("tr"):
            cells = [norm(c.get_text(" ", strip=True)) for c in tr.find_all(["td", "th"])]
            if len(cells) < 2:
                continue
            if cells[0] == "No.":
                pos = cells[1] if cells[1] in POSITIONS else None
                continue
            if pos and re.fullmatch(r"\d{1,3}", cells[0]) and cells[1]:
                out.append({"no": cells[0], "n": re.sub(r"\s+", " ", cells[1]), "p": pos, "dev": dev})
    return out


def parse_manager(html):
    """NPBの選手一覧ページから、監督の名前を取る（コーチは取らない）"""
    soup = BeautifulSoup(html, "html.parser")
    for table in soup.find_all("table"):
        role = None
        for tr in table.find_all("tr"):
            cells = [norm(c.get_text(" ", strip=True)) for c in tr.find_all(["td", "th"])]
            if len(cells) < 2:
                continue
            if cells[0] == "No.":
                role = cells[1]
                continue
            if role == "監督" and re.fullmatch(r"\d{1,3}", cells[0]) and cells[1]:
                return {"no": cells[0], "n": re.sub(r"\s+", " ", cells[1])}
    return None


YEARLY_ROW = re.compile(r"^(\d{4})\S*\s+(.+?)\s+(?:(\d{1,2})\s+)?(\d{1,3})\s+(\d{1,3})\s+(\d{1,3})\s+(\d{1,3})\s+(?:0|1)?\.\d{3}(?:\s|$)")


def parse_yearly(html):
    """NPBの球団の年度別成績ページ（1936年〜今季）から、年度・監督・順位・試合・勝利・敗北・引分を取る
    （表の行でも箇条書きの行でも読めるように、1行ぶんの文字を並びで読む：年度 監督 [順位] 試合 勝利 敗北 引分 勝率 …）"""
    soup = BeautifulSoup(html, "html.parser")
    rows, asof, seen = [], "", set()
    m = re.search(r"(\d{4})年(\d{1,2})月(\d{1,2})日\S*\s*現在", norm(soup.get_text(" ")))
    if m:
        asof = f"{int(m.group(2))}/{int(m.group(3))}"
    for el in soup.find_all(["tr", "li"]):
        text = re.sub(r"\s+", " ", norm(el.get_text(" ", strip=True))).strip()
        mm = YEARLY_ROW.match(text)
        if not mm:
            continue
        y, name, rank, g, w, l, d = mm.groups()
        key = (y, name, g, w)
        if key in seen:
            continue
        seen.add(key)
        rows.append({"y": y, "m": name, "rank": rank or "", "g": int(g), "w": int(w), "l": int(l), "d": int(d)})
    return rows, asof


def parse_player_ids(html):
    """NPBの選手一覧ページから、選手名 → NPBの選手ページの番号（/bis/players/○○.html）"""
    out = {}
    for a in BeautifulSoup(html, "html.parser").find_all("a", href=True):
        m = re.search(r"/bis/players/(\d+)\.html", a["href"])
        if m:
            out[squash(a.get_text())] = m.group(1)
    return out


def pitcher_role(html):
    """NPBの選手ページの投手成績（年度ごと）から、最近の一軍の役割を決める：抑え・先発・中継ぎ
    直近3年（一軍で投げた年）を合わせて、セーブが15以上なら抑え、(勝利+敗北)÷登板が0.4以上か完投があれば先発、ほかは中継ぎ"""
    soup = BeautifulSoup(html, "html.parser")
    for table in soup.find_all("table"):
        rows = [[norm(c.get_text(" ", strip=True)) for c in tr.find_all(["td", "th"])] for tr in table.find_all("tr")]
        head = next((r for r in rows if "登板" in r and "セーブ" in r), None)
        if not head:
            continue
        ix = {k: head.index(k) for k in ("登板", "勝利", "敗北", "セーブ", "完投") if k in head}
        if len(ix) < 4:
            continue
        years = []
        for r in rows:
            if len(r) < len(head) - 2 or not re.match(r"^\d{4}", r[0] if r else ""):
                continue
            try:
                g = int(r[ix["登板"]] or 0)
            except (ValueError, IndexError):
                continue
            if g <= 0:
                continue
            num = lambda k: int(r[ix[k]]) if k in ix and re.fullmatch(r"\d+", r[ix[k]] or "") else 0
            years.append({"y": r[0][:4], "g": g, "w": num("勝利"), "l": num("敗北"), "sv": num("セーブ"), "cg": num("完投")})
        if not years:
            return None
        last = sorted(years, key=lambda x: x["y"])[-3:]
        g, sv, cg = (sum(x[k] for x in last) for k in ("g", "sv", "cg"))
        wl = sum(x["w"] + x["l"] for x in last)
        if sv >= 15:
            return "抑"
        if cg > 0 or (g and wl / g >= 0.4):
            return "先"
        return "中"
    return None


def mgr_key(name):
    """監督の名前の照合用（空白を除き、髙→高 などの字体をそろえる）"""
    return re.sub(r"\s+", "", norm(name or "")).replace("髙", "高").replace("﨑", "崎").replace("濵", "浜")


# 個別の応援歌がある選手を調べるページ（名前だけを照合し、歌詞は保存しない）
SONG_SOURCES = {
    "T": ["https://m.hanshintigers.jp/data/march/", "https://www.yakyu-ouen.net/tigers/"],
    "DB": ["https://sp.baystars.co.jp/player_songs/index", "https://www.yakyu-ouen.net/baystars/"],
    "G": ["https://giants-cheeringclub.com/cheeringsong/", "https://www.yakyu-ouen.net/giants/"],
    "D": ["https://www.yakyu-ouen.net/dragons/"],
    "C": ["https://www.carp.co.jp/team/songs", "https://www.yakyu-ouen.net/carp/"],
    "S": ["https://www.yakult-swallows.co.jp/players/song", "https://www.yakyu-ouen.net/swallows/"],
    # パ・リーグ：公式の応援歌ページ＋応援歌まとめサイト（西武は公式にページがないのでまとめサイトだけ）
    "H": ["https://www.softbankhawks.co.jp/team/song/", "https://www.yakyu-ouen.net/hawks/"],
    "F": ["https://www.fighters.co.jp/entertainment/cheer_player/", "https://www.yakyu-ouen.net/fighters/"],
    "B": ["https://www.buffaloes.co.jp/team/playersong.html", "https://www.yakyu-ouen.net/buffaloes/"],
    "E": ["https://www.rakuteneagles.jp/team/rooterssong/", "https://www.yakyu-ouen.net/eagles/"],
    "L": ["https://www.yakyu-ouen.net/lions/"],
    "M": ["https://www.marines.co.jp/fans/supportersong/", "https://www.yakyu-ouen.net/marines/"],
}
# 公式がPDFで配っている球団は、PDFに載っている選手名をここに書いておく（中日：cheersong2026.pdf）
SONG_EXTRA = {
    "D": ["岡林勇希", "田中幹也", "高橋周平", "カリステ", "村松開人", "福永裕基", "大島洋平", "石伊雄太", "大野雄大",
          "ボスラー", "石川昂弥", "根尾昂", "木下拓哉", "宇佐見真吾", "ブライト健太", "土田龍空", "阿部寿樹",
          "加藤匠馬", "上林誠知", "細川成也", "山本泰寛", "鵜飼航丞"],
}
# 公式に応援歌ページがない球団：まとめサイトの球団ページの表から、選手ごとのページ（なければ表の位置）を拾う
SONG_LINK_PAGE = {"L": "https://www.yakyu-ouen.net/lions/"}
SONG_REV = 6  # 判定のしかたを変えたら数字を上げる（上げると時期に関係なく1回やり直す）
# 選手ごとの応援歌が見出し（1人1つ）になっているページ：見出しの選手だけを「応援歌あり」にする
# （DeNA公式：ページの中に「選手の呼び方」の表があり、テーマ曲（汎用）を使う選手の名前も並んでいるので、本文に名前があるだけでは判定しない）
SONG_HEADINGS = {"https://sp.baystars.co.jp/player_songs/index"}
SONG_HEAD_NG = re.compile(r"テーマ|その他|代打|汎用|呼び方|投手|野手|監督|コーチ|応援歌|チャンス")
# 背番号で並んでいるページ（ヤクルト公式）は背番号でも照合する
SONG_BY_NUMBER = {"https://www.yakult-swallows.co.jp/players/song"}
VARIANT = str.maketrans({"髙": "高", "﨑": "崎", "濵": "浜", "德": "徳", "瀨": "瀬", "邊": "辺", "邉": "辺", "塚": "塚", "・": "", "＝": "", "=": ""})


def squash(s):
    return re.sub(r"\s+", "", norm(s)).translate(VARIANT)


def song_links(url, html):
    """まとめサイトの球団ページの選手応援歌の表から [(背番号, 表の名前, 選手ページのURL or None)] を取る"""
    out = []
    for tr in BeautifulSoup(html, "html.parser").find_all("tr"):
        tds = tr.find_all(["td", "th"])
        if len(tds) < 2:
            continue
        no = squash(tds[0].get_text())
        name = squash(tds[1].get_text())
        if not re.fullmatch(r"\d{1,3}", no) or len(name) < 2:
            continue
        a = tds[1].find("a", href=True)
        out.append((no, name, a["href"] if a and a["href"].startswith("https://") else None))
    return out


def song_heads(html):
    """見出し（h2〜h4）のうち、選手の名前のもの（「J.エンカーナシオン」は「エンカーナシオン」）"""
    out = []
    for h in BeautifulSoup(html, "html.parser").find_all(["h2", "h3", "h4"]):
        x = squash(h.get_text(" "))
        x = re.sub(r"^[A-Za-z]{1,2}\.", "", x)
        if 2 <= len(x) <= 12 and not SONG_HEAD_NG.search(x):
            out.append(x)
    return out


def song_hit(name, heads):
    """名簿の名前が、個別の応援歌の選手名（見出し・表）のどれかと同じか（外国人選手は短い呼び名と長い名前のずれを許す）"""
    for h in heads:
        if name == h or (len(name) >= 3 and len(h) >= 3 and (name.endswith(h) or h.endswith(name))):
            return True
    return False


def mark_songs(t, rows, moved_in=(), moved_out=()):
    """応援歌ページに名前（または背番号）が出てくる選手に song=True を付ける。どのページも読めなければ None
    SONG_LINK_PAGE の球団は、応援歌がある選手に su（タップしたときに開くページ）も付ける"""
    texts, numbers, ok = [squash(" ".join(SONG_EXTRA.get(t, [])))], set(), bool(SONG_EXTRA.get(t))
    links, heads, official = [], [], []
    for url in SONG_SOURCES.get(t, []):
        html = fetch(url)
        if not html:
            continue
        tl = song_links(url, html) if "yakyu-ouen.net" in url else []
        if SONG_LINK_PAGE.get(t) == url:
            links = tl
        # 見出しが選手ごとの公式ページ：見出しの選手だけ（読めたら、この球団はこのページが正）
        if url in SONG_HEADINGS:
            hs = song_heads(html)
            if len(hs) >= 5:
                official += hs
                ok = True
                continue
        # まとめサイト：選手の応援歌の表（背番号｜名前）の選手だけ。本文には「〇〇選手の応援歌を流用」などほかの選手の名前も出てくるので使わない
        if tl and len(tl) >= 5:
            heads += [x[1] for x in tl]
            ok = True
            continue
        text = squash(BeautifulSoup(html, "html.parser").get_text(" "))
        if len(text) < 200:
            continue
        ok = True
        texts.append(text)
        if url in SONG_BY_NUMBER:
            numbers |= set(re.findall(r"背番号(\d{1,3})", text))
    if not ok:
        return None
    if official:
        # 公式の見出しが読めた球団は、まとめサイトの表や本文は使わない（公式にない選手を入れない）
        heads, texts, numbers = official, [], set()
    blob = "\n".join(texts)
    n = 0
    for r in rows:
        name = squash(r["n"])
        if name in moved_out:
            # ほかの球団へ移籍した選手：元の球団の応援歌は、もう使われない（公式のページからも消える）
            r["song"] = False
            continue
        # 移籍してきた選手は、背番号だけでは判定しない（その背番号の前の選手の応援歌を拾ってしまうため）。名前が出ているときだけ
        r["song"] = song_hit(name, heads) or (len(name) >= 2 and name in blob) or (name not in moved_in and not r["dev"] and r["no"] in numbers)
        n += r["song"]
        if r["song"] and links:
            # 名前が含まれる行を優先、なければ背番号が同じ行（表の名前が短い「ネビン」など）
            hit = next((x for x in links if x[1] in name or name in x[1]), None) or next((x for x in links if x[0] == r["no"]), None)
            if hit:
                r["su"] = hit[2] or f"{SONG_LINK_PAGE[t]}#:~:text={quote(hit[1])}"
    return n


FPOS_GROUP = {"一塁手": "内", "二塁手": "内", "三塁手": "内", "遊撃手": "内", "外野手": "外", "捕手": "捕", "投手": "投"}


# ---------- オフの戦力外・引退（各球団の公式サイトの発表から） ----------
# 各球団のニュース一覧から「来季の選手契約について」「現役引退」などの発表を探し、
# 記事の中に出てくる名簿の選手名と照らし合わせる（記事の作りは球団ごとに違うので、名前の照合で拾う）
# パ・リーグ6球団は同じ作りのサイトで、ニュース一覧は /news/list/（チームのニュースは /news/list/0/00000001/）
# （/news/announce/retire/ は「公示 任意引退・自由契約」の去年までの一覧なので使わない）
_PA = lambda base: [base + "/news/list/0/00000001/", base + "/news/list/"]
OFF_LISTS = {
    "G": ["https://www.giants.jp/news/", "https://www.giants.jp/news/list/", "https://www.giants.jp/"],
    "T": ["https://hanshintigers.jp/news/topics/", "https://hanshintigers.jp/news/"],
    "DB": ["https://www.baystars.co.jp/news/"],
    "C": ["https://www.carp.co.jp/news", "https://www.hub.carp.co.jp/news/news26/index.html", "https://www.carp.co.jp/"],
    "S": ["https://www.yakult-swallows.co.jp/news/"],
    "D": ["https://dragons.jp/news/", "https://dragons.jp/"],
    "H": _PA("https://www.softbankhawks.co.jp"),
    "F": _PA("https://www.fighters.co.jp"),
    "B": _PA("https://www.buffaloes.co.jp"),
    "E": _PA("https://www.rakuteneagles.jp"),
    "L": _PA("https://www.seibulions.jp"),
    "M": _PA("https://www.marines.co.jp"),
}
# 見出しで拾う発表：「来季の選手契約について」「選手契約に関して」「○○選手 現役引退」「○○選手について」（阪神の引退の発表の見出し）など
OFF_TITLE = re.compile(r"契約|退団|戦力外|自由契約|選手について|選手に関して")
OFF_TITLE_MGR = re.compile(r"監督.{0,20}(辞任|退任|解任)|(辞任|退任|解任).{0,20}監督")   # 監督の辞任・退任
# 移籍・加入（オフの動き）：トレード・FA・新外国人・入団・獲得など
# コーチ・首脳陣（退団・就任・配置転換）
OFF_TITLE_COACH = re.compile(r"コーチ|首脳陣|組閣|スタッフ|二軍監督|ファーム監督")
OFF_TITLE_COACH_NG = re.compile(r"グッズ|チケット|販売|イベント|教室|スクール|アカデミー|ジュニア|野球教室|トークショー|サイン会|出演|放送|配信|ハニーズ|チアリーダー|DJ|MC|ボールパーク")
FA_RE = re.compile(r"(?<![A-Za-z])FA(?![A-Za-z])|フリーエージェント")
OFF_TITLE_MOVE = re.compile(r"トレード|(?<![A-Za-z])FA(?![A-Za-z])|フリーエージェント|新外国人|外国人選手|入団|獲得|加入|移籍|現役ドラフト")
OFF_TITLE_MOVE_NG = re.compile(r"FANCLUB|FAN CLUB|推し|会員|新入団選手|入団選手情報|グッズ|チケット|販売|ファンクラブ|イベント|記念|会見の(?:お知らせ|模様)|テスト|募集|タイトル|受賞|賞|達成|記録|ドラフト会議|指名|入団式|キャンプ|放送|配信")
OFF_TITLE_NG = re.compile(r"更改|合意|締結|獲得|入団|加入|新外国人|育成選手契約を結ぶ|スポンサー|パートナー|協定|提携|ファンクラブ|チケット|グッズ|放送|配信|中継|販売|募集|キャンプ|約款|規約|観戦|公示|登録|抹消|出演|誕生日|登場曲|達成|記録|受賞|選出|手術|負傷|故障|けが|怪我|復帰|結婚|入籍|出産")
OFF_URL_NG = re.compile(r"/announce/|/stadium/|/ticket|/fanclub|/shop|/goods|/company/|/recruit")
# 本文の終わりの目印（ここから後ろは「関連ニュース」などなので見ない）
OFF_END = re.compile(r"関連ニュース|関連記事|一覧へ戻る|もっと見る|RELATEDNEWS|RelatedNews|おすすめ記事|最新ニュース|ニュース一覧")
# 今季より前に引退を表明していた選手など、ニュース一覧の最初のページに出てこない発表（見つけたら公式の発表で上書き・補う）
OFF_SEED = [
    {"t": "L", "n": "栗山 巧", "kind": "retire", "date": "2025-11-24"},
    {"t": "DB", "n": "ビシエド", "kind": "retire", "date": "2026-05-25"},
    {"t": "H", "n": "中村 晃", "kind": "retire", "date": "2026-07-03", "url": "https://www.softbankhawks.co.jp/news/detail/202601046312.html"},
    {"t": "M", "n": "角中 勝也", "kind": "retire", "date": "2026-07-20"},
    {"t": "B", "n": "平野 佳寿", "kind": "retire", "date": "2026-08-09"},
    {"t": "S", "n": "石川 雅規", "kind": "retire", "date": "2026-09-02"},
    {"t": "M", "n": "唐川 侑己", "kind": "retire", "date": "2026-09-04"},
    {"t": "F", "n": "中島 卓也", "kind": "retire", "date": "2026-09-11"},
    {"t": "E", "n": "辛島 航", "kind": "retire", "date": "2026-09-23", "url": "https://www.rakuteneagles.jp/news/detail/202601195444.html"},
    {"t": "B", "n": "山田 修義", "kind": "retire", "date": "2026-09-24"},
    {"t": "B", "n": "西野 真弘", "kind": "retire", "date": "2026-09-24"},
    {"t": "T", "n": "西 勇輝", "kind": "retire", "date": "2026-09-25", "url": "https://hanshintigers.jp/news/topics/info_11241.html"},
    # 10/5 巨人の発表（球団のニュース一覧・ベースボールチャンネルにまだ出ていなかったので補う）
    {"t": "G", "n": "岡田 悠希", "kind": "cut", "date": "2026-10-05", "url": "https://news.yahoo.co.jp/articles/c1b91c1a5acbd8e36a59ac20663f9b80c5b58b62", "title": "巨人 岡田悠希ら支配下3選手に戦力外通告（球団発表）"},
    {"t": "G", "n": "山田 龍聖", "kind": "cut", "date": "2026-10-05", "url": "https://news.yahoo.co.jp/articles/c1b91c1a5acbd8e36a59ac20663f9b80c5b58b62", "title": "巨人 岡田悠希ら支配下3選手に戦力外通告（球団発表）"},
    {"t": "G", "n": "郡 拓也", "kind": "cut", "date": "2026-10-05", "url": "https://news.yahoo.co.jp/articles/c1b91c1a5acbd8e36a59ac20663f9b80c5b58b62", "title": "巨人 岡田悠希ら支配下3選手に戦力外通告（球団発表）"},
    {"t": "G", "n": "萩尾 匡也", "kind": "offer", "date": "2026-10-05", "url": "https://www.tokyo-sports.co.jp/articles/-/405558", "title": "巨人 萩尾匡也が自由契約 育成契約を打診の見込み（球団発表）"},
    # コーチの退団（10/4 球団発表。球団のニュース一覧から拾えなかったので補う）
    {"t": "B", "n": "波留 敏夫", "kind": "coach_out", "role": "ヘッドコーチ", "date": "2026-10-04", "url": "https://full-count.jp/2026/10/04/post2026060/", "title": "波留敏夫ヘッドコーチ 契約満了で退団（球団発表）"},
    {"t": "B", "n": "川島 慶三", "kind": "coach_out", "role": "打撃コーチ", "date": "2026-10-04", "url": "https://full-count.jp/2026/10/04/post2026060/", "title": "川島慶三打撃コーチ 本人の申し入れで退団（球団発表）"},
    {"t": "T", "n": "岩貞 祐太", "kind": "retire", "date": "2026-09-28"},
    {"t": "D", "n": "井上 一樹", "kind": "mgr", "role": "監督", "date": "2026-09-29"},
    {"t": "G", "n": "阿部 慎之助", "kind": "mgr", "role": "監督", "date": "2026-05-26", "mid": True},   # シーズン途中で辞任（橋上秀樹が監督代行）
]
OFF_KEY = re.compile(r"結ばない|行わない|締結しない|更新しない|結ばず|行わず|戦力外|自由契約|退団|引退|辞任|退任|解任")
OFF_JUNK = re.compile(r"side|related|recommend|ranking|breadcrumb|pickup|banner|share|sns|pager|pagination|footer|header|menu|gnav|global|topics-list|news-list|other", re.I)
OFF_DATE = re.compile(r"(20\d\d)\s*[./年-]\s*(\d{1,2})\s*[./月-]\s*(\d{1,2})")
OFF_EVERY = 3600   # 球団サイトを見に行く間隔（秒）。15分ごとの自動更新のたびには見に行かない（戦力外の発表が続く時期なので1時間ごと）


def off_links(list_url, html, hosts=None):
    """ニュース一覧のページから、戦力外・引退の発表らしい記事のリンク（URL, 見出し）を取る
    hosts：一覧とは別のサイトへのリンクを受け付けるとき（NPBの「12球団ニュース」→ 各球団の公式サイト）"""
    from urllib.parse import urljoin, urlparse
    host = urlparse(list_url).netloc.replace("www.", "")
    ok_hosts = {h.replace("www.", "") for h in hosts} if hosts else {host}
    out, seen = [], set()
    for a in BeautifulSoup(html, "html.parser").find_all("a", href=True):
        title = re.sub(r"\s+", " ", norm(a.get_text(" "))).strip()
        if not title or len(title) > 120:
            continue
        retire = "引退" in title and not re.search(r"公示|グッズ|チケット|販売", title)
        mgr = bool(OFF_TITLE_MGR.search(title)) and not re.search(r"公示|グッズ|チケット|販売|二軍|ファーム", title)
        move = bool(OFF_TITLE_MOVE.search(title)) and not OFF_TITLE_MOVE_NG.search(title)
        coach = bool(OFF_TITLE_COACH.search(title)) and not OFF_TITLE_COACH_NG.search(title)
        if not retire and not mgr and not move and not coach and not (OFF_TITLE.search(title) and not OFF_TITLE_NG.search(title)):
            continue
        url = re.sub(r"^http://", "https://", urljoin(list_url, a["href"]).split("#")[0])   # 同じ記事を http と https で2回読まないように
        if urlparse(url).netloc.replace("www.", "") not in ok_hosts or url in seen or url.rstrip("/") == list_url.rstrip("/") or OFF_URL_NG.search(url):
            continue
        seen.add(url)
        out.append((url, title))
    return out


def off_article(t, url, list_title, html, roster, season, manager=None):
    """1つの発表記事から、戦力外（来季契約せず）・育成再契約の打診・現役引退の選手を取り出す"""
    soup = BeautifulSoup(html, "html.parser")
    h = soup.find("h1") or soup.find("title")
    title = re.sub(r"\s+", " ", norm(h.get_text(" "))).strip() if h else ""
    title = title if len(title) >= 4 else list_title
    for tag in soup(["script", "style", "noscript", "header", "footer", "nav", "aside", "form"]):
        tag.decompose()
    for tag in soup.find_all(True):
        if tag.attrs is None:
            continue
        cls = " ".join(tag.get("class") or []) + " " + str(tag.get("id") or "")
        if OFF_JUNK.search(cls) and tag.name not in ("body", "html", "main", "article"):
            tag.decompose()
    text = norm(soup.get_text("\n"))
    retire_page = "引退" in list_title or "引退" in title
    # 発表日：本文の先頭あたり（見出しの近く）の日付。今季の9月より前なら去年などの古い記事なので使わない
    # 見つからなければ空（見つけた日を使う）。本文の途中の日付（生年月日など）は見ない
    date = ""
    head = text[:max(400, text.find(title[:10]) + 400 if title[:10] and title[:10] in text else 400)]
    for m in OFF_DATE.finditer(head):
        y, mo, d = int(m.group(1)), int(m.group(2)), int(m.group(3))
        if not (1 <= mo <= 12 and 1 <= d <= 31):
            continue   # 「10月0日」のような読み違い
        if y < season - 1:
            continue
        old = y == season - 1 or (y == season and mo < 9)
        if retire_page and old and (y == season or mo >= 10):
            old = False   # 引退の表明は今季の途中や去年の秋のこともある（今の名簿にいる選手だけ拾うので、去年の引退選手は出ない）
        if old:
            return [], "古い記事"
        date = f"{y:04d}-{mo:02d}-{d:02d}"
        break
    S = squash(text)
    k0 = OFF_KEY.search(S)
    if not k0:
        return [], "戦力外・引退の文言なし"
    # 本文の終わり（「関連ニュース」「一覧へ戻る」など）から後ろは見ない
    e = OFF_END.search(S, k0.end())
    if e:
        S = S[:e.start()]
    keys = [(m.start(), m.group(0)) for m in OFF_KEY.finditer(S)]
    mkeys = keys   # 監督用（辞任・退任・解任も含む）
    keys = [kk for kk in keys if kk[1] not in ("辞任", "退任", "解任")]   # 選手用
    # 名前を探す範囲：「結ばない」「引退」などの言葉の前後（記事の横の一覧などを拾わないように）
    wins = [(max(0, p - 700), p + 700) for p, _ in keys]
    if not keys:
        roster = []   # 監督の辞任などの発表だけで、選手の話はない
    sq_title = squash(title)
    # 「育成選手契約を打診」の文（改行・句点で区切った1文ずつ。名前の並びと混ざらないよう、詰める前の本文で区切る）
    offer_sents = [squash(x) for x in re.split(r"[。\n]", text) if "育成" in x and re.search(r"打診|再契約|提示|予定", x)]
    items = []
    for r in roster:
        k = squash(r.get("n", ""))
        if len(k) < 2:
            continue
        pos = [m.start() for m in re.finditer(re.escape(k), S)]
        if len(k) == 2:   # 2文字の名前は「○○投手」「○○選手」のように続くときだけ
            pos = [p for p in pos if re.match(r"(投手|捕手|内野手|外野手|選手)", S[p + 2:p + 6])]
        in_title = k in sq_title
        if not in_title and not any(a <= p <= b for p in pos for a, b in wins):
            continue
        # 種類：名前にいちばん近い言葉が「引退」なら引退、「結ばない・行わない・戦力外」などなら戦力外（見出しに引退があれば引退）
        near = min(keys, key=lambda kk: min((abs(kk[0] - p) for p in pos), default=10 ** 9))[1] if pos else ""
        if retire_page and near in ("", "引退"):
            kind = "retire"
        elif near == "引退":
            kind = "retire"
        elif any(k in x for x in offer_sents) or re.match(r".{0,12}育成.{0,12}(打診|再契約)", S[pos[0] + len(k):pos[0] + len(k) + 30] if pos else ""):
            kind = "offer"
        else:
            kind = "cut"
        items.append({"t": t, "n": r["n"], "no": r.get("no", ""), "dev": bool(r.get("dev")), "kind": kind,
                      "date": date, "url": url, "title": title[:80]})
    # 監督：名前が「辞任・退任・解任」の近く（または見出し）に出てくれば、監督の退任（二軍監督・コーチの話は除く）
    if manager and manager.get("n"):
        k = squash(manager["n"])
        pos = [m.start() for m in re.finditer(re.escape(k), S)]
        near = [kk for p in pos for kk in mkeys if kk[1] in ("辞任", "退任", "解任") and abs(kk[0] - p) < 120]
        if (near or (k in sq_title and OFF_TITLE_MGR.search(title))) and not re.search(k + r".{0,4}(二軍|ファーム)", S):
            items.append({"t": t, "n": manager["n"], "no": manager.get("no", ""), "dev": False, "kind": "mgr", "role": "監督",
                          "date": date, "url": url, "title": title[:80]})
    return items, ""


FOREIGN_NAME = re.compile(r"([ァ-ヶー]{2,}(?:[・＝=][ァ-ヶー]{2,}){0,3})\s*(?:投手|捕手|内野手|外野手|選手)")


def off_moves(t, url, list_title, html, rosters, season):
    """移籍・加入の発表から：トレード（出る・入る）、FA宣言、FAでの加入、新外国人、そのほかの加入を取り出す。
    ほかの球団の名簿にいる選手が出てくれば「その球団から加入」、自分の球団の名簿の選手なら「出る」側"""
    soup = BeautifulSoup(html, "html.parser")
    h = soup.find("h1") or soup.find("title")
    title = re.sub(r"\s+", " ", norm(h.get_text(" "))).strip() if h else ""
    title = title if len(title) >= 4 and OFF_TITLE_MOVE.search(title) else list_title
    for tag in soup(["script", "style", "noscript", "header", "footer", "nav", "aside", "form"]):
        tag.decompose()
    text = norm(soup.get_text("\n"))
    S = squash(text)
    e = OFF_END.search(S, 200)
    if e:
        S = S[:e.start()]
    date = ""
    for m in OFF_DATE.finditer(text[:600]):
        y, mo, d = int(m.group(1)), int(m.group(2)), int(m.group(3))
        if not (1 <= mo <= 12 and 1 <= d <= 31):
            continue   # 「10月0日」のような読み違い
        if y < season - 1:
            continue
        if y == season - 1 or (y == season and mo < 9):
            return []   # オフより前の記事
        date = f"{y:04d}-{mo:02d}-{d:02d}"
        break
    via = "trade" if "トレード" in title else "fa" if FA_RE.search(title) else "genekidraft" if "現役ドラフト" in title else "move"
    fa_decl = via == "fa" and re.search(r"宣言|行使|権利", title) and not re.search(r"獲得|入団|加入|合意", title)
    base = {"date": date, "url": url, "title": title[:80]}
    out, found = [], {}
    for tt, ro in rosters.items():
        for r in ro:
            k = squash(r.get("n", ""))
            if len(k) >= 3 and k in S:
                found.setdefault(tt, []).append(r)
    if fa_decl:
        return [{"t": t, "n": r["n"], "no": r.get("no", ""), "dev": bool(r.get("dev")), "kind": "fa_decl", **base} for r in found.get(t, [])]
    others = {tt: rs for tt, rs in found.items() if tt != t}
    for tt, rs in others.items():
        for r in rs:
            out.append({"t": t, "n": r["n"], "no": "", "dev": False, "kind": "in", "via": via, "from": tt, "pos": r.get("p", ""), **base})
            out.append({"t": tt, "n": r["n"], "no": r.get("no", ""), "dev": bool(r.get("dev")), "kind": "out", "via": via, "to": t, **base})
    if via == "trade" and others:   # トレード：自分の球団の選手は相手の球団へ
        to = next(iter(others))
        for r in found.get(t, []):
            out.append({"t": t, "n": r["n"], "no": r.get("no", ""), "dev": bool(r.get("dev")), "kind": "out", "via": "trade", "to": to, **base})
            out.append({"t": to, "n": r["n"], "no": "", "dev": False, "kind": "in", "via": "trade", "from": t, "pos": r.get("p", ""), **base})
    # 新外国人：どの名簿にもいない名前を見出しから（カタカナの名前＋「投手」「選手」など）
    if re.search(r"新外国人|外国人選手", title) or (not others and re.search(r"獲得|入団|契約合意|契約締結", title)):
        known = {squash(r.get("n", "")) for ro in rosters.values() for r in ro}
        for m in FOREIGN_NAME.finditer(title):
            n = m.group(1)
            if squash(n) not in known and not any(squash(n) in k for k in known):
                pos = re.search(r"(投手|捕手|内野手|外野手)", title[m.end() - 4:m.end()])
                out.append({"t": t, "n": n, "no": "", "dev": False, "kind": "in", "via": "newfor", "pos": pos.group(1) if pos else "", **base})
    return out


# ドラフト会議の指名選手（NPB公式）：表の行でも箇条書きでも「○位 名前 … 守備 … 所属」の並びを、球団の見出しごとに読む
DRAFT_ROW = re.compile(r"^(育成)?\s*(\d{1,2})\s*位\s+(.+?)\s+(投手|捕手|内野手|外野手)\s*(.*)$")
FULLNAME = {"福岡ソフトバンクホークス": "H", "北海道日本ハムファイターズ": "F", "オリックス・バファローズ": "B", "東北楽天ゴールデンイーグルス": "E",
            "埼玉西武ライオンズ": "L", "千葉ロッテマリーンズ": "M", "阪神タイガース": "T", "横浜DeNAベイスターズ": "DB", "読売ジャイアンツ": "G",
            "中日ドラゴンズ": "D", "広島東洋カープ": "C", "東京ヤクルトスワローズ": "S"}


TRADE_TEAM = dict(TEAMS + [("読売", "G")])
TRADE_TEAM_RE = re.compile("|".join(re.escape(n) for n in sorted(TRADE_TEAM, key=len, reverse=True)))


def parse_trades(html, season):
    """NPBの公示「トレード」の表：日付・名前・守備・元の背番号・元の球団 → 新しい背番号・新しい球団"""
    out = []
    for tr in BeautifulSoup(html, "html.parser").find_all("tr"):
        cells = [norm(c.get_text(" ", strip=True)) for c in tr.find_all(["td", "th"])]
        if len(cells) < 7:
            continue
        m = re.match(r"^(\d{4})/(\d{1,2})/(\d{1,2})$", cells[0])
        if not m or int(m.group(1)) != season or int(m.group(2)) < 2:   # その年の2月以降（前のオフの分は入れない）
            continue
        try:
            i = cells.index("→")
        except ValueError:
            continue
        fr = TRADE_TEAM_RE.search(cells[i - 1]) if i >= 1 else None
        to = TRADE_TEAM_RE.search(" ".join(cells[i + 1:])) if i + 1 < len(cells) else None
        if not fr or not to:
            continue
        out.append({"d": f"{m.group(1)}-{int(m.group(2)):02d}-{int(m.group(3)):02d}", "n": re.sub(r"\s+", " ", cells[1]).strip(),
                    "pos": re.sub(r"\s+", "", cells[2]), "no_from": cells[3] if i >= 2 else "", "from": TRADE_TEAM[fr.group()],
                    "no_to": cells[i + 1] if re.fullmatch(r"\d{1,3}", cells[i + 1] or "") else "", "to": TRADE_TEAM[to.group()]})
    return out


def parse_registered(html, season):
    """NPBの公示「新規支配下選手登録」の表：日付｜球団（registered_<球団>.html へのリンク）｜名前｜守備｜背番号（育成の番号 → 新しい番号）｜（育成選手から移行）
    育成から支配下に上がった選手だけ取る（新外国人などの新しい登録は取らない）"""
    codes = {v: k for k, v in ROSTER_CODE.items()}
    out = []
    for tr in BeautifulSoup(html, "html.parser").find_all("tr"):
        cells = [norm(c.get_text(" ", strip=True)) for c in tr.find_all(["td", "th"])]
        if len(cells) < 5 or not any("育成選手から移行" in c for c in cells):
            continue
        m = re.match(r"^(\d{4})/(\d{1,2})/(\d{1,2})$", cells[0])
        if not m or int(m.group(1)) != season:
            continue
        a = tr.find("a", href=re.compile(r"registered_([a-z]+)\.html"))
        t = codes.get(re.search(r"registered_([a-z]+)\.html", a["href"]).group(1)) if a else None
        if not t:
            continue
        nos = re.findall(r"\d{1,3}", cells[4])
        out.append({"t": t, "n": re.sub(r"\s+", " ", cells[2]).strip(), "pos": re.sub(r"\s+", "", cells[3]),
                    "no": nos[-1] if nos else "", "no_dev": nos[0] if len(nos) > 1 else "",
                    "d": f"{m.group(1)}-{int(m.group(2)):02d}-{int(m.group(3)):02d}"})
    return out


def parse_staff(html):
    """NPBの「監督・コーチ一覧」の表（位置・番号・氏名）。「〜以降の動き」の表は読まない"""
    soup = BeautifulSoup(html, "html.parser")
    out = []
    for table in soup.find_all("table"):
        prev = table.find_previous(["h4", "h5", "h3"])
        if prev and "動き" in prev.get_text():
            continue
        for tr in table.find_all("tr"):
            c = [norm(x.get_text(" ", strip=True)) for x in tr.find_all(["td", "th"])]
            if len(c) >= 3 and ("コーチ" in c[0] or "監督" in c[0] or "コーディネーター" in c[0]) and c[2] and c[0] != "位置":
                out.append({"role": c[0], "no": c[1], "n": re.sub(r"\s+", " ", c[2]).strip()})
        if out:
            break
    return out


COACH_ROLE = r"(?:一軍|二軍|三軍|四軍|ファーム)?[^\s、。（）()「」]{0,16}?(?:コーチ|監督|コーディネーター)"
COACH_NEW = re.compile(r"(" + COACH_ROLE + r")\s*[：:　 ]\s*([一-龥々]{1,4}[ 　][一-龥々ぁ-んァ-ヶー]{1,5}|[ァ-ヶー]{2,}(?:・[ァ-ヶー]{2,})+)")


def off_coach(t, url, list_title, html, staff, known, season):
    """コーチ・首脳陣の発表：今の首脳陣（NPBの一覧）の名前が出てきたら、近くの言葉で退団・就任・配置転換を決める。
    一覧にいない人は「○○コーチ　名前」の並びから就任として拾う（発表の見出しに就任・新任・スタッフなどがあるときだけ）"""
    soup = BeautifulSoup(html, "html.parser")
    h = soup.find("h1") or soup.find("title")
    title = re.sub(r"\s+", " ", norm(h.get_text(" "))).strip() if h else ""
    title = title if len(title) >= 4 and OFF_TITLE_COACH.search(title) else list_title
    for tag in soup(["script", "style", "noscript", "header", "footer", "nav", "aside", "form"]):
        tag.decompose()
    text = norm(soup.get_text("\n"))
    e = OFF_END.search(text, 200)
    if e:
        text = text[:e.start()]
    date = ""
    for m in OFF_DATE.finditer(text[:600]):
        y, mo, d = int(m.group(1)), int(m.group(2)), int(m.group(3))
        if not (1 <= mo <= 12 and 1 <= d <= 31):
            continue   # 「10月0日」のような読み違い
        if y < season or (y == season and mo < 9):
            return []   # オフより前の記事（シーズン中の配置転換などは入れない）
        date = f"{y:04d}-{mo:02d}-{d:02d}"
        break
    base = {"date": date, "url": url, "title": title[:80]}
    flat = re.sub(r"\s+", "", text)
    out, got = [], set()
    for s_ in staff:
        if s_["role"] == "監督":
            continue   # 一軍の監督はこれまで通り（監督の退任）
        k = squash(s_["n"])
        i = flat.find(k)
        if len(k) < 2 or i < 0:
            continue
        # 名前にいちばん近い言葉で決める（前の文の「退団」などを拾わないように）。留任・続投は変化なし
        lo, hi = max(0, i - 40), i + len(k) + 60
        best, kind = None, ""
        for kd, pat in (("", r"留任|残留|続投"), ("coach_out", r"退団|退任|辞任|契約を結ばない|契約満了|勇退"),
                        ("coach_move", r"配置転換|担当変更|異動|へ変更|に変更|兼任"), ("coach_in", r"就任|新任|入閣|復帰")):
            for m in re.finditer(pat, flat[lo:hi]):
                pos = lo + m.start()
                dist = pos - (i + len(k)) if pos >= i + len(k) else (i - pos) * 1.5   # 名前の後ろの言葉を少し優先
                if best is None or dist < best:
                    best, kind = dist, kd
        if best is None and re.search(r"退団|退任", title):
            kind = "coach_out"
        if kind:
            out.append({"t": t, "n": s_["n"], "no": s_.get("no", ""), "dev": False, "kind": kind, "role": s_["role"], **base})
            got.add(k)
    if re.search(r"就任|新任|入閣|コーチングスタッフ|首脳陣|組閣", title):
        for m in COACH_NEW.finditer(text):
            role, n = m.group(1).strip(), re.sub(r"[ 　]+", " ", m.group(2)).strip()
            k = squash(n)
            if k in got or k in known or k in {squash(x["n"]) for x in staff}:   # 選手・今の首脳陣（留任など）は新任ではない
                continue
            got.add(k)
            out.append({"t": t, "n": n, "no": "", "dev": False, "kind": "coach_in", "role": role, **base})
    return out


STAFF_NEWS_FEEDS = ["https://full-count.jp/feed/", "https://full-count.jp/category/npb/feed/"]
STAFF_NEWS_TITLE = re.compile(r"(コーチ|監督|首脳陣).{0,40}(退団|退任|辞任|契約満了|契約を結ばない)|(退団|退任|辞任|契約満了).{0,40}(コーチ|監督)")
STAFF_NEWS_ANNOUNCE = re.compile(r"球団発表|発表した|発表しました|を発表")


PLAYER_NEWS_FEEDS = ["https://full-count.jp/feed/", "https://full-count.jp/category/npb/feed/", "https://baseballking.jp/feed", "https://www.baseballchannel.jp/feed/"]
PLAYER_NEWS_TITLE = re.compile(r"戦力外|来季の契約を結ばない|契約を結ばない|自由契約")
PLAYER_CUT_RE = re.compile(r"戦力外|来季の?(?:選手)?契約を結ばない|契約を結ばない(?:こと)?を(?:発表|通告|通知)|自由契約")


def player_news(rosters, season, seen, today):
    """ニュースの新着（RSS）から、選手の戦力外・自由契約の球団発表を拾う → [{t, n, no, dev, kind: cut|offer, date, url, title}]
    名前は今の名簿（12球団）にいて、戦力外などの言葉と同じ文の中にある選手だけ。同じ名前が2球団にいれば入れない。観測記事（「〜か」「見通し」）は入れない"""
    from html import unescape
    names = {}
    for t, ro in (rosters or {}).items():
        for r in ro:
            k = squash(r["n"])
            names.setdefault(k, []).append((t, r))
    out, done = [], set()
    for feed in PLAYER_NEWS_FEEDS:
        xml = fetch(feed)
        if not xml:
            continue
        for item in re.findall(r"<item>(.*?)</item>", xml, re.S)[:50]:
            g = lambda tag: unescape(re.sub(r"^<!\[CDATA\[|\]\]>$", "", (re.search(rf"<{tag}>(.*?)</{tag}>", item, re.S) or [None, ""])[1].strip()))
            title, link = norm(g("title")), g("link").strip()
            if not link or link in done or not PLAYER_NEWS_TITLE.search(title) or re.search(r"[かか]？?$|見通し|濃厚|へ$", title):
                continue
            done.add(link)
            if seen.get(link):
                continue
            html = fetch(link)
            seen[link] = today
            if not html:
                continue
            soup = BeautifulSoup(html, "html.parser")
            for tag in soup(["script", "style", "noscript", "header", "footer", "nav", "aside", "form"]):
                tag.decompose()
            text = norm(soup.get_text("\n"))
            e = OFF_END.search(text, 200)
            if e:
                text = text[:e.start()]
            if not STAFF_NEWS_ANNOUNCE.search(title + text[:1500]) or re.search(r"見通し|濃厚|とみられる", title):
                continue
            m = OFF_DATE.search(text[:600])
            d = f"{int(m.group(1)):04d}-{int(m.group(2)):02d}-{int(m.group(3)):02d}" if m else today
            if d < f"{season}-09-01":
                continue
            for sent in re.split(r"[。\n]", text[:4000]):
                flat = re.sub(r"\s+", "", sent)
                if not PLAYER_CUT_RE.search(flat):
                    continue
                # この記事の球団：見出し（なければ最初の文）に出てくる球団名。球団が分からない記事・2球団以上の記事は使わない
                head_teams = {t for nm, t in TEAMS if nm in title} or {t for nm, t in TEAMS if nm in text[:300]}
                if len(head_teams) != 1:
                    break
                for k, cands in names.items():
                    if len(k) < 2 or k not in flat or len(cands) != 1:
                        continue
                    t, r = cands[0]
                    if t not in head_teams:
                        continue   # 記事の球団と違う球団の選手（「昨年○○を戦力外になった」など）は入れない
                    i = flat.find(k)
                    kind = "offer" if re.search(r"育成(?:選手)?(?:として|での)?(?:再)?契約を(?:打診|結ぶ|予定)|育成契約を打診|育成で再契約", flat[i:]) else "cut"
                    out.append({"t": t, "n": r["n"], "no": r.get("no", ""), "dev": bool(r.get("dev")), "kind": kind, "date": d, "url": link, "title": title[:80], "src": "news"})
    # 同じ選手は1回だけ
    uniq = {}
    for x in out:
        uniq.setdefault((x["t"], squash(x["n"])), x)
    return list(uniq.values())


def staff_news(staff, managers, season, seen, today):
    """ニュースの新着（RSS）から、首脳陣の退団・退任の球団発表を拾う → [{t, n, kind: coach_out|mgr, role, date, url, title}]"""
    from html import unescape
    out, done = [], set()
    for feed in STAFF_NEWS_FEEDS:
        xml = fetch(feed)
        if not xml:
            continue
        for item in re.findall(r"<item>(.*?)</item>", xml, re.S)[:40]:
            g = lambda tag: unescape(re.sub(r"^<!\[CDATA\[|\]\]>$", "", (re.search(rf"<{tag}>(.*?)</{tag}>", item, re.S) or [None, ""])[1].strip()))
            title, link = norm(g("title")), g("link").strip()
            if not link or link in done or not STAFF_NEWS_TITLE.search(title):
                continue
            done.add(link)
            if seen.get(link):
                continue
            html = fetch(link)
            seen[link] = today
            if not html:
                continue
            soup = BeautifulSoup(html, "html.parser")
            for tag in soup(["script", "style", "noscript", "header", "footer", "nav", "aside", "form"]):
                tag.decompose()
            text = norm(soup.get_text("\n"))
            e = OFF_END.search(text, 200)
            if e:
                text = text[:e.start()]
            if not STAFF_NEWS_ANNOUNCE.search(title + text[:1500]):
                continue   # 「〜へ」「〜か」などの観測記事は入れない（球団の発表だけ）
            for t, st_ in (staff or {}).items():
                flat = re.sub(r"\s+", "", text)
                for it in off_coach(t, link, title, html, st_, set(), season):
                    k = squash(it["n"]); i = flat.find(k)
                    # 名前のすぐ後ろ（30字以内）に退団・退任などがあるときだけ（同じ記事に出てくるほかのコーチを取り違えない）
                    if it["kind"] == "coach_out" and i >= 0 and re.search(r"退団|退任|辞任|契約満了|契約を結ばない", flat[i:i + len(k) + 30]):
                        it["src"] = "news"
                        out.append(it)
                mg = (managers or {}).get(t)
                if mg and mg.get("n"):
                    k, flat = squash(mg["n"]), re.sub(r"\s+", "", text)
                    i = flat.find(k)
                    if i >= 0 and re.search(r"退任|辞任|退団", flat[i:i + len(k) + 40]):
                        m = OFF_DATE.search(text[:600])
                        d = f"{int(m.group(1)):04d}-{int(m.group(2)):02d}-{int(m.group(3)):02d}" if m else today
                        if d >= f"{season}-09-01":
                            out.append({"t": t, "n": mg["n"], "no": mg.get("no", ""), "dev": False, "kind": "mgr", "date": d, "url": link, "title": title[:80], "src": "news"})
    return out


def parse_draft(html):
    soup = BeautifulSoup(html, "html.parser")
    out, cur = [], None
    for el in soup.find_all(["h2", "h3", "h4", "h5", "caption", "th", "tr", "li", "p", "div"]):
        if el.name in ("tr", "li"):
            row = re.sub(r"\s+", " ", norm(el.get_text(" ", strip=True)))
            m = DRAFT_ROW.match(row)
            if m and cur:
                name = re.sub(r"\s*[（(].*$", "", m.group(3)).strip()
                out.append({"t": cur, "n": name, "round": ("育成" if m.group(1) else "") + m.group(2) + "位", "pos": m.group(4), "from": m.group(5).strip()[:30]})
            continue
        txt = norm(el.get_text(" ", strip=True))
        if len(txt) > 40 or el.find(["tr", "li"]):
            continue
        for full, code in FULLNAME.items():
            if full in txt:
                cur = code
                break
    seen, res = set(), []
    for x in out:
        if (x["t"], x["n"]) not in seen:
            seen.add((x["t"], x["n"]))
            res.append(x)
    return res


def fetch_offseason(season, old, rosters, force=False, any_month=False):
    """各球団の公式サイトから、今オフの戦力外・引退の発表を集める（前回までに見つけたものは残す）"""
    prev = (old or {}).get("offseason") or {}
    if prev.get("season") != season:
        prev = {}
    now = datetime.now(JST)
    if now.month < 9 and not any_month:   # 9月〜12月だけ（手動で実行しても、この時期以外は見に行かない）
        return prev or None
    last = prev.get("checked_at")
    if last and not force:
        try:
            if (now - datetime.fromisoformat(last)).total_seconds() < OFF_EVERY:
                return prev
        except ValueError:
            pass
    # 前回までに見つけたもの（「公示」の一覧など、今は使わないページから拾ったものは消す）
    items = {(x["t"], x["n"]): x for x in prev.get("items", []) if not OFF_URL_NG.search(x.get("url") or "")}
    # 各球団の監督の名前（NPBの選手一覧ページから、1日1回）
    managers = dict(prev.get("managers") or {})
    staff = dict(prev.get("staff") or {})   # 今の首脳陣（NPBの監督・コーチ一覧）
    player_ids = {}
    if prev.get("managers_date") != now.strftime("%Y-%m-%d") or not managers:
        for t, code in ROSTER_CODE.items():
            html = fetch(f"https://npb.jp/bis/teams/rst_{code}.html")
            m = parse_manager(html) if html else None
            if m:
                managers[t] = m
            if html:
                player_ids[t] = parse_player_ids(html)
            sh = fetch(f"https://npb.jp/announcement/{season}/managers_{code}.html")
            st_ = parse_staff(sh) if sh else []
            if st_:
                staff[t] = st_
        print(f"[監督] {len(managers)}球団: " + " ".join(f"{t}:{m['n']}" for t, m in managers.items()))
    # 監督としての通算成績（NPBの12球団の年度別成績ページから、今の監督と退任する監督の分だけ。1日1回）
    mgr_rec = dict(prev.get("mgr_rec") or {})
    if prev.get("mgr_rec_date") != now.strftime("%Y-%m-%d") or not mgr_rec:
        want = {mgr_key(m["n"]): m["n"] for m in managers.values()}
        want.update({mgr_key(x["n"]): x["n"] for x in prev.get("items", []) if x.get("kind") == "mgr"})
        want.update({mgr_key(x["n"]): x["n"] for x in OFF_SEED if x.get("kind") == "mgr"})
        seasons, asof = {k: [] for k in want}, ""
        ok = 0
        for t, code in ROSTER_CODE.items():
            html = fetch(f"https://npb.jp/bis/teams/yearly_{code}.html")
            if not html:
                continue
            rows, a = parse_yearly(html)
            if rows:
                ok += 1
                asof = asof or a
            for r in rows:
                k = mgr_key(r["m"])
                if k in seasons:   # 2人で分けた年（「根本・小西」など）は数えない
                    seasons[k].append({"y": r["y"], "t": t, "rank": r["rank"], "g": r["g"], "w": r["w"], "l": r["l"], "d": r["d"]})
        if ok >= 10:   # 年度別成績のページがほとんど読めたときだけ入れ替える
            mgr_rec = {want[k]: {"seasons": sorted(v, key=lambda x: (x["y"], x["t"])), "asof": asof} for k, v in seasons.items() if v}
            print(f"[監督の通算成績] {len(mgr_rec)}人（{ok}球団の年度別成績から）")
    seen = {u: d for u, d in (prev.get("seen") or {}).items() if not OFF_URL_NG.search(u)}
    teams = {}
    today = now.strftime("%Y-%m-%d")
    for t, urls in OFF_LISTS.items():
        st = {"lists": {}, "links": 0, "new": 0, "found": 0, "err": ""}
        roster = rosters.get(t) or []
        cands, got = [], set()
        # 候補の一覧ページを全部見て、発表らしい記事をまとめる（球団ごとの状況も残す：開けたか・何件あったか）
        # 最後に NPB公式の「12球団ニュース」の球団別ページも見る（球団のサイトの一覧が読めないときの助け）
        from urllib.parse import urlparse
        team_hosts = {urlparse(u).netloc for u in urls}
        npb_list = f"https://npb.jp/news/teamnews_{ROSTER_CODE[t]}.html"
        for u in urls + [npb_list]:
            html = fetch(u)
            if not html:
                st["lists"][u] = "開けない"
                continue
            found_links = off_links(u, html, team_hosts if u == npb_list else None)
            st["lists"][u] = len(found_links)
            for url, ltitle in found_links:
                if url not in got:
                    got.add(url)
                    cands.append((url, ltitle))
        st["links"] = len(cands)
        if not any(isinstance(v, int) for v in st["lists"].values()):
            st["err"] = "ニュース一覧を開けない"
        for url, ltitle in cands[:15]:
            if url in seen:
                continue
            html = fetch(url)
            time.sleep(1)
            if not html:
                continue
            found, why = off_article(t, url, ltitle, html, roster, season, managers.get(t))
            if OFF_TITLE_MOVE.search(ltitle) and not OFF_TITLE_MOVE_NG.search(ltitle):
                found += off_moves(t, url, ltitle, html, rosters, season)
            if OFF_TITLE_COACH.search(ltitle) and not OFF_TITLE_COACH_NG.search(ltitle):
                known = {squash(r.get("n", "")) for ro in rosters.values() for r in ro}
                found += off_coach(t, url, ltitle, html, staff.get(t) or [], known, season)
            seen[url] = today
            st["new"] += 1
            for it in found:
                it["date"] = it["date"] or today
                old_it = items.get((it["t"], it["n"]))
                # 同じ選手の発表が2つあるとき（戦力外→引退など）は、新しい方
                if not old_it or it["date"] >= old_it.get("date", ""):
                    items[(it["t"], it["n"])] = it
                st["found"] += 1
            print(f"  [戦力外・引退 {t}] {ltitle[:40]} → {len(found)}人{('（' + why + '）') if why else ''}")
        teams[t] = st
    # ニュースの新着（Full-Count）：コーチ・監督の退団・退任の「球団発表」の記事を読み、今の首脳陣（NPBの一覧）の名前と照らし合わせる。
    # 球団のサイトのニュース一覧から拾えなかった発表を補う網。経歴（「〜でコーチを経て」など）を読み違えないよう、拾うのは退団・退任だけ
    try:
        got_news = staff_news(staff, managers, season, seen, today)
        got_players = player_news(rosters, season, seen, today)
        for it in got_players:
            if not any(k[0] == it["t"] and squash(k[1]) == squash(it["n"]) for k in items):
                items[(it["t"], it["n"])] = it
        if got_players:
            print(f"[戦力外（ニュース）] {len(got_players)}人")
        for it in got_news:
            old_it = items.get((it["t"], it["n"]))
            if not old_it or (old_it.get("kind") not in ("coach_out", "mgr") and it["date"] >= old_it.get("date", "")):
                items[(it["t"], it["n"])] = it
        if got_news:
            print(f"[首脳陣（ニュース）] {len(got_news)}人")
    except Exception as e:   # ニュースが読めなくても、ほかの更新は止めない
        print(f"[首脳陣（ニュース）] 読めませんでした: {e}")
    # 前の回に「松原快 育成」「髙野光海 NEW 育成」のような札つきの名前で入ったものを、名前だけに直して1つにまとめる
    for key in list(items.keys()):
        it = items[key]
        cn = clean_off_name(it.get("n", ""))
        if cn and cn != it.get("n"):
            del items[key]
            if re.search(r"育成", it.get("n", "")):
                it["dev"] = True
            it["n"] = cn
            k2 = (it["t"], cn)
            if k2 not in items:
                items[k2] = it
    # 前の回に、関係のないページ（ファンクラブの記事・新入団選手の一覧など）から拾ってしまった移籍・加入は消す
    for k in [k for k, it in items.items() if it.get("kind") in ("in", "out") and (OFF_TITLE_MOVE_NG.search(it.get("title", "")) or "/newcomer" in it.get("url", ""))]:
        del items[k]
    # トレード（NPB公式の公示）：シーズン中のトレードも入れる。戦力外・引退などのあとの発表があれば、そちらを残す
    trades = []
    for y in (season, season + 1):
        html = fetch(f"https://npb.jp/announcement/{y}/pn_traded.html")
        trades += parse_trades(html, y) if html else []
    for x in trades:
        base = {"date": x["d"], "url": f"https://npb.jp/announcement/{x['d'][:4]}/pn_traded.html", "title": "トレード（NPB公示）", "via": "trade"}
        for key, it in (((x["from"], x["n"]), {"t": x["from"], "n": x["n"], "no": x["no_from"], "dev": False, "kind": "out", "to": x["to"], **base}),
                        ((x["to"], x["n"]), {"t": x["to"], "n": x["n"], "no": x["no_to"], "dev": False, "kind": "in", "from": x["from"], "pos": x["pos"], **base})):
            cur = items.get(key)
            if not cur or cur.get("kind") in ("in", "out") or cur.get("date", "") < x["d"]:
                items[key] = it
    if trades:
        print(f"[トレード] {len(trades)}人（NPB公示）")
    # 育成から支配下登録（NPB公式の公示「新規支配下選手登録」）：今季の1月〜7月末の分。そのあと戦力外・引退などがあれば、そちらを残す
    reg_url = f"https://npb.jp/announcement/{season}/pn_registered.html"
    html = fetch(reg_url)
    promos = parse_registered(html, season) if html else []
    for x in promos:
        key = (x["t"], x["n"])
        cur = items.get(key)
        if cur and cur.get("kind") != "promote":
            continue
        items[key] = {"t": x["t"], "n": x["n"], "no": x["no"], "dev": False, "kind": "promote", "date": x["d"], "pos": x["pos"], "no_dev": x["no_dev"],
                      "url": reg_url, "title": "新規支配下選手登録（NPB公示）"}
    if promos:
        print(f"[支配下登録] {len(promos)}人（NPB公示）")
    # ドラフト会議の指名選手（10月〜）
    draft_st = {}
    if now.month >= 10 or any_month:
        for u in (f"https://npb.jp/draft/{season}/", f"https://npb.jp/draft/{season}/result.html", "https://npb.jp/draft/"):
            html = fetch(u)
            got = parse_draft(html) if html else []
            draft_st[u] = len(got) if html else "開けない"
            if got:
                for x in got:
                    key = (x["t"], x["n"])
                    if key not in items:
                        items[key] = {"t": x["t"], "n": x["n"], "no": "", "dev": x["round"].startswith("育成"), "kind": "draft",
                                      "round": x["round"], "pos": x["pos"], "from": x["from"], "date": today, "url": u, "title": ""}
                break
        print(f"[ドラフト] {draft_st}")
    # 投手の過去の一軍の役割（先発・中継ぎ・抑え）：NPBの選手ページから。1回調べたら残す（1回に30人まで）
    looked = 0
    need = {t for (t, n), it in items.items() if not it.get("role") and it.get("kind") != "mgr"
            and any(r.get("p") == "投手" and squash(r.get("n", "")) == squash(n) for r in rosters.get(t) or [])}
    for t in need - set(player_ids):   # 監督の名前を取らなかった回でも、調べる球団の選手ページの番号は取る
        html = fetch(f"https://npb.jp/bis/teams/rst_{ROSTER_CODE[t]}.html")
        if html:
            player_ids[t] = parse_player_ids(html)
    for (t, n), it in items.items():
        if it.get("role") or it.get("kind") in ("mgr", "draft", "in", "coach_out", "coach_in", "coach_move") or looked >= 30:
            continue
        ro = next((r for r in rosters.get(t) or [] if squash(r.get("n", "")) == squash(n)), None)
        if not ro or ro.get("p") != "投手":
            continue
        pid = (player_ids.get(t) or {}).get(squash(n))
        if not pid:
            continue
        html = fetch(f"https://npb.jp/bis/players/{pid}.html")
        looked += 1
        time.sleep(0.5)
        role = pitcher_role(html) if html else None
        if role:
            it["role"] = role
    # 補う分：OFF_SEED と data/offseason_fix.json（{"exclude": [{"t","n"}], "add": [{...}]}）
    # すでに公式の発表から見つけている選手は、発表日・種類だけ補う（リンクは公式の発表のまま）
    def patch(x):
        if not (x.get("t") and x.get("n") and x.get("kind")):
            return
        key = (x["t"], x["n"])
        ro = next((r for r in rosters.get(x["t"]) or [] if squash(r.get("n", "")) == squash(x["n"])), None)
        if key in items:
            for f in ("date", "kind"):
                if x.get(f):
                    items[key][f] = x[f]
            if x.get("url") and not items[key].get("url"):
                items[key]["url"] = x["url"]
        else:
            items[key] = {"t": x["t"], "n": x["n"], "no": (ro or {}).get("no", "") or (managers.get(x["t"]) or {}).get("no", "") if x["kind"] == "mgr" else (ro or {}).get("no", ""),
                          "dev": bool((ro or {}).get("dev")), "kind": x["kind"], "date": x.get("date") or today, "url": x.get("url", ""), "title": x.get("title", "")}
            for f in ("role", "mid"):
                if x.get(f):
                    items[key][f] = x[f]
            if x["kind"].startswith("coach"):
                sf = next((s_ for s_ in staff.get(x["t"]) or [] if squash(s_["n"]) == squash(x["n"])), None)
                if sf:
                    items[key]["no"] = items[key].get("no") or sf.get("no", "")
                    items[key]["role"] = items[key].get("role") or sf.get("role", "")
    for x in OFF_SEED:
        patch(x)
    fix_path = os.path.join(os.path.dirname(OUT), "offseason_fix.json")
    if os.path.exists(fix_path):
        try:
            with open(fix_path, encoding="utf-8") as f:
                fix = json.load(f)
            for x in fix.get("exclude", []):
                items.pop((x.get("t"), x.get("n")), None)
            for x in fix.get("add", []):
                patch(x)
        except (OSError, json.JSONDecodeError) as e:
            print(f"  offseason_fix.json を読めません: {e}")
    # 見た記事の記録は60日分だけ残す
    lim = (now - timedelta(days=60)).strftime("%Y-%m-%d")
    seen = {u: d for u, d in seen.items() if d >= lim}
    out = sorted(items.values(), key=lambda x: (x["date"], x["t"], x["n"]), reverse=True)
    print(f"[戦力外・引退] {len(out)}人（" + " ".join(f"{t}:{v['found']}" for t, v in teams.items()) + "）")
    return {"season": season, "checked_at": now.isoformat(timespec="seconds"), "items": out, "teams": teams, "seen": seen,
            "managers": managers, "managers_date": now.strftime("%Y-%m-%d") if managers else prev.get("managers_date"),
            "mgr_rec": mgr_rec, "mgr_rec_date": now.strftime("%Y-%m-%d") if mgr_rec else prev.get("mgr_rec_date"),
            "draft_status": draft_st or prev.get("draft_status"), "staff": staff}


# ===== スポナビの「入退団情報」（12球団の退団・入団を1ページにまとめた表）=====
# 球団のサイトが読めないとき（巨人・広島・ソフトバンクなど）や、発表の記事を見に行く前の回でも、戦力外・引退を拾えるように
# 毎回（15分ごと）1ページだけ読む。表：更新日｜状況（退団・入団）｜選手名（育成は「※」）｜守備｜備考（自由契約・引退・トレードなど）
TRANSFER_URL = "https://baseball.yahoo.co.jp/npb/transfer"
YTEAM_SHORT = {"阪神": "T", "DeNA": "DB", "巨人": "G", "中日": "D", "広島": "C", "ヤクルト": "S", "ソフトバンク": "H", "日本ハム": "F",
               "オリックス": "B", "楽天": "E", "西武": "L", "ロッテ": "M"}


def parse_transfer(html, season):
    """入退団情報の表から、今オフ（今季の9月以降）の退団（戦力外・引退・育成再契約の打診）を取り出す"""
    soup = BeautifulSoup(html, "html.parser")
    ids = {str(v): k for k, v in YAHOO_TEAM.items()}
    out = []
    for tb in soup.find_all("table"):
        # どの球団の表か：直前の見出し（「阪神」など）か、まわりの id（スポナビの球団番号）
        t = None
        h = tb.find_previous(["h2", "h3", "h4"])
        if h:
            t = YTEAM_SHORT.get(norm(h.get_text(" ")).strip())
        if not t:
            par = tb.find_parent(id=True)
            t = ids.get(str(par.get("id"))) if par else None
        if not t:
            continue
        for tr in tb.find_all("tr"):
            c = [re.sub(r"\s+", " ", norm(td.get_text(" "))).strip() for td in tr.find_all(["td", "th"])]
            # 入団のうち「育成から支配下」は支配下登録（今季の1月から）。それ以外の入団は取らない
            if len(c) >= 5 and c[1] == "入団" and re.search(r"育成.{0,8}支配下|支配下.{0,8}育成", c[4]):
                m0 = re.match(r"(\d{4})/(\d{1,2})/(\d{1,2})", c[0])
                if m0:
                    d0 = f"{int(m0.group(1)):04d}-{int(m0.group(2)):02d}-{int(m0.group(3)):02d}"
                    nm0 = c[2].replace("※", "").strip()
                    if d0 >= f"{season}-01-01" and nm0:
                        out.append({"t": t, "n": nm0, "dev": False, "kind": "promote", "date": d0, "note": c[4]})
                continue
            if len(c) < 5 or c[1] != "退団":
                continue
            m = re.match(r"(\d{4})/(\d{1,2})/(\d{1,2})", c[0])
            if not m:
                continue
            date = f"{int(m.group(1)):04d}-{int(m.group(2)):02d}-{int(m.group(3)):02d}"
            if date < f"{season}-09-01":   # 今オフの分だけ（シーズン中の退団・去年のオフは使わない）
                continue
            note = c[4]
            dev = "※" in c[2]
            name = c[2].replace("※", "").strip()
            if not name:
                continue
            if re.search(r"トレード|現役ドラフト|FA|ポスティング|人的補償", note):
                continue   # 移籍はNPBの公示・球団の発表から
            if "引退" in note:
                kind = "retire"
            elif re.search(r"育成.{0,3}再契約|育成契約を打診|育成.{0,4}打診", note):
                kind = "offer"
            elif re.search(r"自由契約|戦力外", note):
                kind = "cut"
            elif "退団" in note:
                kind = "leave"   # 自由契約・引退・移籍ではない退団（「退団」とだけあるもの）
            else:
                continue
            out.append({"t": t, "n": name, "dev": dev, "kind": kind, "date": date, "note": note})
    return out


# ===== ベースボールチャンネルの「今季の戦力外通告・現役引退・自由契約・退団選手一覧」（12球団。発表の当日に更新される） =====
# スポナビの入退団情報は反映が遅れることがあるので、もう1つの元として使う。記事の場所は毎年変わるので「自由契約」のタグの一覧から探す
BBC_TAGS = ["https://www.baseballchannel.jp/tag/%E8%87%AA%E7%94%B1%E5%A5%91%E7%B4%84/", "https://www.baseballchannel.jp/tag/%E6%88%A6%E5%8A%9B%E5%A4%96%E9%80%9A%E5%91%8A/"]
BBC_KIND = [(re.compile(r"引退"), "retire"), (re.compile(r"戦力外"), "cut"), (re.compile(r"自由契約"), "cut"), (re.compile(r"退団"), "leave")]
BBC_SHORT = {"阪神": "T", "DeNA": "DB", "巨人": "G", "中日": "D", "広島": "C", "ヤクルト": "S", "ソフトバンク": "H", "日本ハム": "F",
             "オリックス": "B", "楽天": "E", "西武": "L", "ロッテ": "M"}


OFF_NAME_TAGS = re.compile(r"(?:^|\s)(?:NEW|New|new|新着|育成|※|[（(]育成[）)])(?=\s|$)")


def clean_off_name(n):
    """発表の一覧の名前から「NEW」「育成」「※」などの札を外す（名前の一部ではない）"""
    s = norm(n).replace("※", " ")
    prev = None
    while prev != s:
        prev, s = s, OFF_NAME_TAGS.sub(" ", s)
    return re.sub(r"\s+", " ", s).strip()


def parse_bbc(html, season):
    """一覧の表（日付｜球団｜選手｜ポジション）を、直前の見出し（戦力外通告・引退表明・自由契約・退団）ごとに読む"""
    soup = BeautifulSoup(html, "html.parser")
    out = []
    for tb in soup.find_all("table"):
        h = tb.find_previous(["h2", "h3", "h4"])
        head = norm(h.get_text(" ")) if h else ""
        kind = next((k for rx, k in BBC_KIND if rx.search(head)), None)
        if not kind or re.search(r"移籍|トレード|FA|入団|加入", head):
            continue
        for tr in tb.find_all("tr"):
            c = [re.sub(r"\s+", " ", norm(td.get_text(" "))).strip() for td in tr.find_all(["td", "th"])]
            if len(c) < 3:
                continue
            m = re.match(r"(\d{1,2})月(\d{1,2})日", c[0])
            t = BBC_SHORT.get(c[1])
            if not m or not t:
                continue
            mo, d = int(m.group(1)), int(m.group(2))
            if not (1 <= mo <= 12 and 1 <= d <= 31):
                continue
            y = season if mo >= 3 else season + 1
            date = f"{y:04d}-{mo:02d}-{d:02d}"
            if date < f"{season}-09-01":
                continue
            # 名前の欄には「NEW」「育成」などの札も並ぶ（10/4 から）→ 名前だけにして、育成は dev に
            name = clean_off_name(c[2])
            if name:
                out.append({"t": t, "n": name, "dev": bool(re.search(r"※|育成", c[2])), "kind": kind, "date": date, "note": head})
    return out


def fetch_bbc(season):
    """タグの一覧から今季の「戦力外通告 現役引退 自由契約 退団選手」の一覧の記事を探し、各ページ（［1/4ページ］など）を読む"""
    url = None
    for tag in BBC_TAGS:
        html = fetch(tag)
        if not html:
            continue
        for a in BeautifulSoup(html, "html.parser").find_all("a", href=True):
            tx = norm(a.get_text(" "))
            if f"{season}年" in tx and "戦力外" in tx and "一覧" in tx and "baseballchannel.jp" in a["href"] and not re.search(r"今日の|月\d+日発表", tx):
                url = a["href"].split("?")[0]
                if "プロ野球" in tx:   # 12球団まとめを優先
                    break
        if url:
            break
    if not url:
        return None, []
    base = url.rstrip("/")
    got, seen = [], set()
    for page in range(1, 7):
        u = base + "/" if page == 1 else f"{base}/{page}/"
        html = fetch(u)
        if not html:
            break
        rows = parse_bbc(html, season)
        fresh = [x for x in rows if (x["t"], squash(x["n"])) not in seen]
        if page > 1 and not fresh:
            break   # 次のページがない（同じ表がくり返される）
        for x in fresh:
            seen.add((x["t"], squash(x["n"])))
        got += fresh
    return url, got


def okey(n):
    """照らし合わせ用：空白なし・外国人の名前の頭の「F・」「J.」なども外す（「F・グズマン」と「グズマン」は同じ人）"""
    return squash(re.sub(r"^\s*[A-Za-z]{1,2}\s*[・.．]\s*", "", norm(n or "")))


def merge_transfer(off, season, rosters, now=None):
    """スポナビの入退団情報を、オフシーズン情報に足す（すでに球団の発表から見つけている選手はそのまま。種類が変わったときだけ直す）"""
    now = now or datetime.now(JST)
    if now.month < 9 or not off or off.get("season") != season:
        return off
    html = fetch(TRANSFER_URL)
    got = parse_transfer(html, season) if html else []
    # もう1つの元：ベースボールチャンネルの一覧（スポナビより早いことがある）
    try:
        bbc_url, bbc = fetch_bbc(season)
    except Exception as e:
        bbc_url, bbc = None, []
        print(f"[戦力外の一覧（ベースボールチャンネル）] 読めない：{e}")
    have = set()
    uniq = []
    for x in got + bbc:   # 同じ選手は1回だけ（スポナビが先）
        k = (x["t"], okey(x["n"]))
        if k not in have:
            have.add(k); uniq.append(x)
    got = uniq
    print(f"[戦力外の一覧（ベースボールチャンネル）] {bbc_url or '見つからない'}：今オフ {len(bbc)}件")
    items = {(x["t"], okey(x["n"])): x for x in off.get("items") or []}
    add = upd = 0
    for x in got:
        ro = next((r for r in rosters.get(x["t"]) or [] if okey(r.get("n", "")) == okey(x["n"])), None)
        key = (x["t"], okey(x["n"]))
        cur = items.get(key)
        if cur:
            # 戦力外→引退などに変わったとき（新しい日付のとき）だけ直す。移籍・監督などの項目には手を出さない
            if cur.get("kind") in ("cut", "offer", "retire", "leave") and cur.get("kind") != x["kind"] and x["date"] > (cur.get("date") or ""):
                cur["kind"], cur["date"] = x["kind"], x["date"]
                upd += 1
            continue
        _bbc = x in bbc
        items[key] = {"t": x["t"], "n": (ro or {}).get("n") or x["n"], "no": (ro or {}).get("no", ""), "dev": bool((ro or {}).get("dev")) or x["dev"],
                      "kind": x["kind"], "date": x["date"], "url": bbc_url if _bbc else TRANSFER_URL,
                      "title": "ベースボールチャンネル 戦力外・引退一覧" if _bbc else "スポーツナビ 入退団情報", "src": "bbc" if _bbc else "sponavi"}
        add += 1
    off = dict(off)
    # 同じ球団・同じ選手（名前の空白の有無だけ違うもの）・同じ種類は1つにまとめる
    uniq = {}
    for x in items.values():
        uniq.setdefault((x["t"], okey(x["n"]), x.get("kind")), x)
    off["items"] = sorted(uniq.values(), key=lambda x: (x["date"], x["t"], x["n"]), reverse=True)
    off["transfer"] = {"at": now.isoformat(timespec="seconds"), "rows": len(got) if (html or bbc) else "開けない", "added": add, "bbc": len(bbc)}
    print(f"[入退団情報（スポナビ）] 今オフの退団 {len(got) if html else '開けない'}件 → 追加 {add}人・種類の更新 {upd}人")
    return off


def fetch_fpos(season, old):
    """各球団の個人守備成績から、選手ごとに今季守ったポジション（投・捕・内・外）と試合数を取る（1日1回）"""
    today = datetime.now(JST).strftime("%Y-%m-%d")
    prev = (old or {}).get("fpos") or {}
    if prev.get("date") == today and prev.get("season") == season and len(prev.get("teams", {})) == len(ROSTER_CODE):
        return prev
    teams = dict(prev.get("teams", {})) if prev.get("season") == season else {}
    for t, code in ROSTER_CODE.items():
        html = fetch(f"https://npb.jp/bis/{season}/stats/idf1_{code}.html")
        if not html:
            print(f"[守備位置] {t} 読み取れず（前回の値を使用）")
            continue
        soup = BeautifulSoup(html, "html.parser")
        out = {}
        for table in soup.find_all("table"):
            h = table.find_previous(["h5", "h4", "h3"])
            grp = FPOS_GROUP.get(clean(h.get_text()) if h else "")
            if not grp:
                continue
            for tr in table.find_all("tr")[1:]:
                c = [norm(x.get_text(" ", strip=True)) for x in tr.find_all(["td", "th"])]
                if len(c) < 2 or not c[1].isdigit():
                    continue
                name = re.sub(r"[\s*＊]", "", c[0])
                if not name:
                    continue
                d = out.setdefault(name, {})
                d[grp] = d.get(grp, 0) + int(c[1])
        if out:
            teams[t] = out
            time.sleep(1)
        else:
            print(f"[守備位置] {t} 表が見つからず（前回の値を使用）")
    print(f"[守備位置] {sum(len(v) for v in teams.values())}人（{len(teams)}球団）")
    # 投手の役割（先発・中継ぎ・抑え）：個人投手成績の「1登板あたりの投球回」と「セーブ・ホールド」から判定
    roles = dict(prev.get("roles", {})) if prev.get("season") == season else {}
    for t, code in ROSTER_CODE.items():
        html = fetch(f"https://npb.jp/bis/{season}/stats/idp1_{code}.html")
        if not html:
            continue
        soup = BeautifulSoup(html, "html.parser")
        out = {}
        for table in soup.find_all("table"):
            rows = table.find_all("tr")
            if not rows:
                continue
            head = [clean(x.get_text()) for x in rows[0].find_all(["th", "td"])]
            if "登板" not in head or "投球回" not in head or "セーブ" not in head:
                continue
            ix = {k: head.index(k) for k in ("登板", "投球回", "セーブ", "ホールド") if k in head}
            for tr in rows[1:]:
                c = [norm(x.get_text(" ", strip=True)) for x in tr.find_all(["td", "th"])]
                if len(c) < len(head):
                    continue
                name = re.sub(r"[\s*＊]", "", c[0])
                try:
                    g = int(c[ix["登板"]])
                    ipt = c[ix["投球回"]].split(".")
                    ip = int(ipt[0] or 0) + (int(ipt[1]) / 3 if len(ipt) > 1 and ipt[1] else 0)
                    sv = int(c[ix["セーブ"]] or 0)
                    hd = int(c[ix["ホールド"]] or 0) if "ホールド" in ix else 0
                except (ValueError, KeyError):
                    continue
                if not name or g <= 0:
                    continue
                per = ip / g
                if sv >= 10:
                    r = ["抑"] + (["中"] if hd >= 5 else [])
                elif per >= 4:
                    r = ["先"]
                elif per >= 3.25:
                    r = ["先", "中"]
                elif per >= 2.5:
                    r = ["中", "先"]
                else:
                    r = ["中"] + (["抑"] if sv >= 3 else [])
                out[name] = r
        if out:
            roles[t] = out
            time.sleep(1)
    # スポナビの球団別投手成績に「登板」「先発」があるので、取れた球団はそれで上書きする
    # （先発＝先発した試合数、中継ぎ＝登板−先発。セーブ10以上の投手の救援は「抑」）
    YID = {"T": 5, "G": 1, "DB": 3, "D": 4, "C": 6, "S": 2, "H": 12, "F": 8, "B": 11, "E": 376, "L": 7, "M": 9}
    for t, yid in YID.items():
        html = fetch(f"https://baseball.yahoo.co.jp/npb/teams/{yid}/pitchingstats")
        if not html:
            continue
        soup = BeautifulSoup(html, "html.parser")
        out = {}
        for table in soup.find_all("table"):
            rows = table.find_all("tr")
            if not rows:
                continue
            head = [re.sub(r"\s+", "", clean(x.get_text())) for x in rows[0].find_all(["th", "td"])]
            if "先発" not in head or "登板" not in head or "選手名" not in head:
                continue
            ix = {k: head.index(k) for k in ("選手名", "登板", "先発", "セーブ") if k in head}
            for tr in rows[1:]:
                c = [norm(x.get_text(" ", strip=True)) for x in tr.find_all(["td", "th"])]
                if len(c) < len(head):
                    continue
                name = re.sub(r"[\s*＊]", "", c[ix["選手名"]])
                try:
                    g, gs = int(c[ix["登板"]]), int(c[ix["先発"]])
                    sv = int(c[ix["セーブ"]]) if "セーブ" in ix else 0
                except ValueError:
                    continue  # 一軍登板なし（「-」）
                if not name or g <= 0:
                    continue
                rel = g - gs
                d = {}
                if gs > 0:
                    d["先"] = gs
                if rel > 0:
                    d["抑" if sv >= 10 else "中"] = rel
                out[name] = d
            break
        if out:
            roles[t] = out
            time.sleep(1)
    print(f"[投手の役割] {sum(len(v) for v in roles.values())}人（{len(roles)}球団）")
    return {"season": season, "date": today, "teams": teams, "roles": roles}


def fetch_rosters(old):
    """各球団の選手一覧（1日1回だけ取りに行く）"""
    now = datetime.now(JST)
    today = now.strftime("%Y-%m-%d")
    rosters = dict((old or {}).get("rosters") or {})
    have_all = (len(rosters) == len(ROSTER_CODE) and all(any("song" in r for r in v) for v in rosters.values())
                and (old or {}).get("song_rev") == SONG_REV)
    # 更新は3月〜7月だけ（支配下登録の期限が7月末のため）。まだ全球団そろっていなければ時期に関係なく取る
    if have_all and not (3 <= now.month <= 7):
        return rosters, (old or {}).get("roster_date")
    # 取りに行くのは1日1回まで（応援歌ページが読めない球団があっても、何度も取りに行かない）
    if len(rosters) == len(ROSTER_CODE) and (old or {}).get("roster_date") == today and (old or {}).get("song_rev") == SONG_REV:
        return rosters, today
    # 移籍（トレードなど）した選手：前の回のオフシーズン情報から
    moves = [x for x in (((old or {}).get("offseason") or {}).get("items") or []) if x.get("kind") in ("in", "out")]
    for t, code in ROSTER_CODE.items():
        html = fetch(f"https://npb.jp/bis/teams/rst_{code}.html")
        rows = parse_roster(html) if html else []
        if len(rows) >= 20:
            n = mark_songs(t, rows, {squash(x["n"]) for x in moves if x["t"] == t and x["kind"] == "in"},
                           {squash(x["n"]) for x in moves if x["t"] == t and x["kind"] == "out"})
            if n is None and t in rosters:  # 応援歌ページが読めなかったときは前回の判定を引き継ぐ
                prev = {(r["no"], r["n"]): r for r in rosters[t]}
                for r in rows:
                    o = prev.get((r["no"], r["n"]))
                    if o and o.get("song") is not None:
                        r["song"] = o["song"]
                        if o.get("su"):
                            r["su"] = o["su"]
            print(f"[応援歌] {t}: 個別応援歌あり {n if n is not None else '判定できず'}人")
            rosters[t] = rows
        else:
            print(f"[選手一覧] {t} 読み取れず（前回の値を使用）")
    print(f"[選手一覧] {sum(len(v) for v in rosters.values())}人（{len(rosters)}球団）")
    return rosters, today


# ===== ポストシーズン（CS・日本シリーズ）：日程カレンダー用。戦況の計算には使わない =====
def parse_post_rows(html, season, stage_of):
    out = []
    soup = BeautifulSoup(html, "html.parser")
    for tr in soup.find_all("tr"):
        text = norm(tr.get_text(" ", strip=True))
        m = re.search(r"(\d{1,2})/(\d{1,2})", text)
        if not m or "予備日" in text:
            continue
        st = stage_of(text)
        n = re.search(r"第(\d)戦", text)
        if not st or not n:
            continue
        g = {"d": f"{season}-{int(m.group(1)):02d}-{int(m.group(2)):02d}", "stage": st, "no": int(n.group(1))}
        teams = [CODE[x] for x in TEAM_RE.findall(text)]
        if len(teams) >= 2:
            g["h"], g["a"] = teams[0], teams[1]
            sc = re.search(r"(\d+)\s*-\s*(\d+)", text.split(")")[-1]) if ")" in text else None
            if sc:
                g["hs"], g["as"], g["st"] = int(sc.group(1)), int(sc.group(2)), "final"
        if "中止" in text:
            g["st"] = "canc"
        tm = re.search(r"(\d{1,2}:\d{2})", text)
        if tm:
            g["t"] = tm.group(1)
        out.append(g)
    return out


def fetch_post(season, old):
    prev = [p for p in ((old or {}).get("post") or []) if p.get("d", "").startswith(str(season))]
    games = []
    stage = lambda t: "CSF" if "ファイナルステージ" in t else "CS1" if "ファーストステージ" in t else None
    html = fetch(f"https://npb.jp/games/{season}/schedule_climax_cl.html")
    if html:
        games += parse_post_rows(html, season, stage)
    html = fetch(f"https://npb.jp/games/{season}/schedule_climax_pl.html")
    if html:
        games += [dict(g, lg="P") for g in parse_post_rows(html, season, stage)]
    html = fetch(f"https://npb.jp/nippons/{season}/")
    if html:
        text = norm(BeautifulSoup(html, "html.parser").get_text(" ", strip=True))
        for m in re.finditer(r"【第(\d)戦】\s*(\d{1,2})月(\d{1,2})日[^【]*?(セ・リーグ|パ・リーグ)出場チーム本拠地", text):
            games.append({"d": f"{season}-{int(m.group(2)):02d}-{int(m.group(3)):02d}", "stage": "JS", "no": int(m.group(1)),
                          "home": "セ" if m.group(4).startswith("セ") else "パ"})
    if not games:
        print("[ポストシーズン] 読み取れず（前回の値を使用）")
        return prev
    # 前回に結果があって今回取れなかった試合は、結果を引き継ぐ
    old_by = {(p["d"], p["stage"], p["no"], p.get("lg", "C")): p for p in prev}
    for g in games:
        o = old_by.get((g["d"], g["stage"], g["no"], g.get("lg", "C")))
        if o and o.get("st") == "final" and g.get("st") != "final":
            for k in ("h", "a", "hs", "as", "st"):
                if k in o:
                    g[k] = o[k]
    print(f"[ポストシーズン] {len(games)}試合（CS {sum(g['stage'] != 'JS' for g in games)}・日本シリーズ {sum(g['stage'] == 'JS' for g in games)}）")
    return games


TSTATS_OUT = os.path.join(os.path.dirname(OUT), "tstats.json")


def write_json(data):
    """data/latest.json（開いた瞬間に使うもの）と data/tstats.json（チーム別成績：成績タブを開いたときに読むもの）に分けて保存。
    シーズンの記録（data/archive）には、分けたものも入れて丸ごと残す"""
    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    main = {k: v for k, v in data.items() if k != "tstats"}
    main["tstats_at"] = (data.get("tstats") or {}).get("at")
    with open(OUT, "w", encoding="utf-8") as f:
        json.dump(main, f, ensure_ascii=False, separators=(",", ":"))
    if data.get("tstats"):
        with open(TSTATS_OUT, "w", encoding="utf-8") as f:
            json.dump(data["tstats"], f, ensure_ascii=False, separators=(",", ":"))
    write_archive(data)


ARCH_DIR = os.path.join(os.path.dirname(OUT), "archive")


def write_archive(data, force=False):
    """シーズンの記録を残す：公式戦がすべて終わったら（または11月以降）、data/archive/<年>.json に丸ごと保存し、
    data/archive/index.json（残っているシーズンの一覧）を更新する。次のシーズンになっても前の年の記録を見られるように"""
    try:
        season = int(data.get("season") or 0)
        gs = data.get("games") or []
        done = gs and all(g.get("st") in ("final", "canc") for g in gs)
        if not season or not (force or done or datetime.now(JST).month >= 11 or datetime.now(JST).year > season):
            return
        os.makedirs(ARCH_DIR, exist_ok=True)
        with open(os.path.join(ARCH_DIR, f"{season}.json"), "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, separators=(",", ":"))
        idx_path = os.path.join(ARCH_DIR, "index.json")
        seasons = []
        if os.path.exists(idx_path):
            try:
                with open(idx_path, encoding="utf-8") as f:
                    seasons = json.load(f).get("seasons", [])
            except (OSError, json.JSONDecodeError):
                seasons = []
        seasons = sorted({int(x) for x in seasons} | {season}, reverse=True)
        with open(idx_path, "w", encoding="utf-8") as f:
            json.dump({"seasons": seasons}, f, ensure_ascii=False)
        print(f"[記録] {season}年のシーズンを data/archive/{season}.json に保存（{seasons}）")
    except Exception as e:   # 記録の保存に失敗しても、ふだんのデータ更新は止めない
        print(f"[記録] 保存できませんでした: {e}")


# ================= 歴代記録（NPB公式「歴代最高記録」：通算・通算（現役）・シーズン） =================
# 1日1回だけ取りに行く（約130ページ）。取れなかったページは前の分を残す。data/records.json（記録タブを開いたときに読む）
RECORDS_OUT = os.path.join(os.path.dirname(OUT), "records.json")
REC_BAT = [("g", "試合"), ("tpa", "打席"), ("ab", "打数"), ("r", "得点"), ("h", "安打"), ("2b", "二塁打"), ("3b", "三塁打"), ("hr", "本塁打"),
           ("tb", "塁打"), ("rbi", "打点"), ("sb", "盗塁"), ("cs", "盗塁刺"), ("sh", "犠打"), ("sf", "犠飛"), ("bb", "四球"), ("ibb", "故意四球"),
           ("hp", "死球"), ("so", "三振"), ("gdp", "併殺打"), ("avg", "打率"), ("slg", "長打率"), ("obp", "出塁率")]
REC_PIT = [("g", "登板"), ("cg", "完投"), ("sho", "完封勝"), ("nbb", "無四球試合"), ("w", "勝利"), ("l", "敗北"), ("sv", "セーブ"), ("hld", "ホールド"),
           ("hldp", "HP"), ("pct", "勝率"), ("ip", "投球回"), ("h", "被安打"), ("hr", "被本塁打"), ("bb", "与四球"), ("hb", "与死球"), ("so", "奪三振"),
           ("wp", "暴投"), ("bk", "ボーク"), ("r", "失点"), ("er", "自責点"), ("era", "防御率")]
REC_KINDS = [("lt", "通算"), ("ac", "現役"), ("ss", "シーズン")]
REC_ROWS = 50          # 1つの記録で残す順位（ページの上から）
REC_EVERY = 20 * 3600  # これより新しければ取りに行かない
REC_BUDGET = 150       # 1回の更新で記録に使う秒数の上限（超えたら残りは次の回に）
REC_PARSER = 2         # ページの読み方の版（2：投手のページの見出し「投手」も読む）


def parse_record_page(html):
    """NPBの歴代最高記録の1ページ：見出し（順位・選手・(所属)・記録・年度…）と行。いつ現在か（「2026年10月1日（木） 現在」「2025年度シーズン終了 現在」）と条件の注記"""
    soup = BeautifulSoup(html, "html.parser")
    text = norm(soup.get_text("\n"))
    asof = ""
    m = re.search(r"(\d{4}年(?:\d{1,2}月\d{1,2}日(?:\s*\(.\))?|度シーズン終了))\s*現在", text)
    if m:
        asof = m.group(1).replace(" ", "")
    for tb in soup.find_all("table"):
        trs = tb.find_all("tr")
        if len(trs) < 2:
            continue
        head = [norm(c.get_text(" ", strip=True)) for c in trs[0].find_all(["th", "td"])]
        # 見出しは「順位｜選手」（打撃）か「順位｜投手」（投手）。投手のページを読めていなかった（10/5 直し）
        if not head or head[0] != "順位" or not any(re.search(r"選手|投手|打者", h) for h in head):
            continue
        rows = []
        for tr in trs[1:]:
            cells = [re.sub(r"\s+", " ", norm(c.get_text(" ", strip=True))).strip() for c in tr.find_all(["td", "th"])]
            # 順位の数字で始まる行だけ（最後の「( * 2026シーズンの現役選手 )」などの注記の行は読まない）
            if len(cells) < 3 or not re.fullmatch(r"\d+", cells[0]):
                continue
            rows.append((cells + [""] * len(head))[:len(head)])
            if len(rows) >= REC_ROWS:
                break
        if not rows:
            continue
        # 見出しが空の列（「*」＝今シーズンの現役選手の印）は、行ごとの印 a にして列から外す
        act = None
        for i, h in enumerate(head):
            if i and not h and all(r[i] in ("", "*") for r in rows):
                act = i
                break
        flags = []
        if act is not None:
            flags = [1 if r[act] == "*" else 0 for r in rows]
            head = head[:act] + head[act + 1:]
            rows = [r[:act] + r[act + 1:] for r in rows]
        note = ""
        prev = tb.find_previous(string=re.compile(r"以上|規定|以下"))
        if prev:
            note = norm(str(prev)).strip()[:60]
        out = {"cols": head, "rows": rows, "asof": asof, "note": note}
        if any(flags):
            out["act"] = flags
        return out
    return None


def rec_fetch(url):
    """歴代記録の1ページ：ないページ（404）で何度も待たないよう、試すのは2回まで・待ちは短く"""
    denied = False
    for i in range(3):
        try:
            # 断られた（403など）ときは、ふつうのブラウザと同じ名乗りでもう一度（在籍者名簿のページなど）
            r = requests.get(url, timeout=20, headers=BROWSER_UA if denied else {"User-Agent": "Mozilla/5.0 (hobby-baseball)"})
            if r.status_code == 404:
                return None
            if r.status_code in (401, 403, 406, 429):
                denied = True
            if r.status_code == 200:
                try:
                    return r.content.decode("utf-8")
                except UnicodeDecodeError:
                    r.encoding = r.apparent_encoding or "utf-8"
                    return r.text
        except requests.RequestException:
            pass
        time.sleep(1)
    return None


# ---------- プロ野球在籍者名簿（NPB）：通算記録の選手の所属球団を全員分入れるため ----------
# 1行＝「名前｜在籍年数｜05～13西武,14～19ロッテ,…」。「イチロー （→ 鈴木 一朗）」のような別名の行もある。（コ）（監）は選手ではないので数えない
# ページの名前は訓令式（index_si・index_ti …。10/5 に確かめた）。違ったときのためにヘボン式も試す
REG_KANA = ["a", "i", "u", "e", "o", "ka", "ki", "ku", "ke", "ko", "sa", "si", "su", "se", "so", "ta", "ti", "tu", "te", "to",
            "na", "ni", "nu", "ne", "no", "ha", "hi", "hu", "he", "ho", "ma", "mi", "mu", "me", "mo", "ya", "yu", "yo",
            "ra", "ri", "ru", "re", "ro", "wa"]
REG_ALT = {"si": "shi", "ti": "chi", "tu": "tsu", "hu": "fu"}
REG_OUT = os.path.join(os.path.dirname(OUT), "register.json")
REG_EVERY = 7 * 86400   # 名簿は週に1回だけ取り直す
REG_PARSER = 3          # 名簿の読み方の版（2：［改名］の名前も覚える、3：選手のページの番号も覚える）。版が変わったらすぐ取り直す
POS_BUDGET = 120        # 1回の更新で選手のページ（守備位置）を読むのに使う秒数の上限
REG_MIN_PEOPLE = 3000   # これより少ない名簿は「取り損ね」とみなす（ほんとうは約1万人）
POS_VERSION = 2         # 引退した打者の守備位置の調べ方の版（2：記事の書き出し「元プロ野球選手（内野手）」も読む・断られたら次の回）


def reg_year(y):
    y = int(y)
    return 1900 + y if y >= 36 else 2000 + y


def parse_reg_history(h):
    """「05～13西武,14～19ロッテ」「77,78クラウン」「20開幕～途巨人（育）」→ [(球団, [年…]), …]（コーチ・監督の期間は入れない）"""
    out = []
    pat = r"([\d～~開幕途春秋閉,，.]+?)([^\d～~,，・、（(開幕途春秋閉]+)(?:[（(]([^）)]*)[）)])?"
    for m in re.finditer(pat, norm(h)):
        span, team, role = m.group(1), m.group(2).strip(), m.group(3) or ""
        if re.search(r"コ|監", role) and not re.search(r"兼", role):
            continue
        years = []
        for part in re.split(r"[,，]", span):
            nums = re.findall(r"\d{2}", part)
            if not nums:
                continue
            if len(nums) >= 2 and re.search(r"[～~]", part):
                years += list(range(reg_year(nums[0]), reg_year(nums[-1]) + 1))
            else:
                years += [reg_year(x) for x in nums]
        if years and team:
            out.append((team, sorted(set(years))))
    return out


def parse_register_page(html):
    """名簿の1ページ → {名前: [[球団, 年…], …]}・別名 {別名: 本名}"""
    soup = BeautifulSoup(html, "html.parser")
    people, alias, renames = {}, {}, {}
    for tr in soup.find_all("tr"):
        cells = [norm(c.get_text(" ", strip=True)) for c in tr.find_all(["td", "th"])]
        cells = [c for c in cells if c]
        if not cells:
            continue
        name = re.sub(r"\s*[（(][A-Za-z'\-. ]+[）)]\s*$", "", cells[0]).strip()
        m = re.match(r"^(.+?)\s*[（(]→\s*(.+?)[）)]$", cells[0])
        if m:
            alias[m.group(1).strip()] = m.group(2).strip()
            continue
        if len(cells) >= 3 and re.fullmatch(r"\d+", cells[1]):
            hist = parse_reg_history(re.split(r"[［\[]", cells[2])[0])   # 「［改名］…」から後ろは名前の変わった時期なので読まない
            if hist:
                a_ = tr.find_parent("a") or tr.find("a")
                m_id = re.search(r"/bis/players/(\d+)\.html", (a_.get("href") if a_ else "") or "")
                people.setdefault(name, []).append({"h": [[t, y] for t, y in hist], "id": m_id.group(1) if m_id else ""})
                # ［改名］のあとに出てくる名前（例：～05大塚晶文,06～大塚晶則・～10金子千尋,11～金子弌大）も、同じ人の別の名前として覚える
                m2 = re.search(r"[［\[]改名[］\]](.*)$", cells[2])
                if m2:
                    for nm in re.findall(r"[^\d～~,，.\s（）()・\[\]［］]+", m2.group(1)):
                        nm = re.sub(r"^(?:開幕|途|閉幕|春|秋|第.*?試合)+|(?:開幕|途|閉幕)+$", "", nm)
                        if len(nm) >= 2 and nm != re.sub(r"\s+", "", name):
                            renames.setdefault(nm, name)
    return people, alias, renames


def update_register(force=False):
    prev = {}
    if os.path.exists(REG_OUT):
        try:
            with open(REG_OUT, encoding="utf-8") as f:
                prev = json.load(f)
        except (OSError, json.JSONDecodeError):
            prev = {}
    if not force and prev.get("at_ts") and time.time() - prev["at_ts"] < REG_EVERY and prev.get("people") and prev.get("pv") == REG_PARSER:
        return prev
    people, alias, renames, got, empty, failed = {}, {}, {}, 0, [], []
    for k in REG_KANA:
        p = a = rn = None
        for kk in [k] + ([REG_ALT[k]] if k in REG_ALT else []):
            html = rec_fetch(f"https://npb.jp/history/register/index_{kk}.html")
            if html:
                p, a, rn = parse_register_page(html)
                if p:
                    break
        if p is None:
            failed.append(k)
            continue
        if not p:
            empty.append(k)
            continue
        for n, lst in p.items():
            people.setdefault(n, []).extend(lst)
        alias.update(a)
        renames.update(rn or {})
        got += 1
    diag = {"got": got, "people": len(people), "failed": failed, "empty": empty}
    print(f"[在籍者名簿] {diag}")
    if got < len(REG_KANA) * 0.7 or len(people) < REG_MIN_PEOPLE:   # 半端にしか取れなかったときは前の名簿を使う
        prev = dict(prev or {})
        prev["diag"] = diag
        return prev
    out = {"at_ts": time.time(), "people": people, "alias": alias, "renames": renames, "diag": diag, "pv": REG_PARSER,
           "pos": (prev or {}).get("pos") or {}, "role": (prev or {}).get("role") or {}}   # 守備位置・先発／中継ぎは取り直さずに引き継ぐ
    with open(REG_OUT, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, separators=(",", ":"))
    print(f"[在籍者名簿] {got}ページ・{len(people)}人")
    return out


def reg_key(n):
    return re.sub(r"\s+", "", re.sub(r"^\s*[A-Za-zＡ-Ｚ]{1,2}\s*[・.．]\s*", "", norm(n)))


def record_person(name, period, reg):
    """通算・シーズン記録の選手を名簿の本人に：名前（外国人は頭文字を外す・別名・改名・「ジオ・アルバラード」の前半）と期間の重なりで決める
    → (いちばん長くいた球団から並べた所属, 選手のページの番号) または None"""
    if not reg or not reg.get("people"):
        return None
    alias = {reg_key(k): v for k, v in (reg.get("alias") or {}).items()}
    idx = reg.get("_idx")
    if idx is None:
        idx = {}
        for n, lst in reg["people"].items():
            keys = {reg_key(n)}
            if "・" in norm(n):
                keys.add(reg_key(norm(n).split("・")[0]))   # 登録名「ジオ」→ 名簿「ジオ・アルバラード」
            for kk in keys:
                for e in lst:
                    idx.setdefault(kk, []).append(e)
        for other, main in (reg.get("renames") or {}).items():   # 改名前・改名後の名前
            for e in reg["people"].get(main, []):
                idx.setdefault(reg_key(other), []).append(e)
        reg["_idx"] = idx
    k = reg_key(name)
    if k in alias:
        k = reg_key(alias[k])
    cands = idx.get(k) or []
    m = re.search(r"(\d{4})\D+(\d{4})", period or "") or re.search(r"(\d{4})()", period or "")
    lo = int(m.group(1)) if m else 0
    hi = int(m.group(2)) if m and m.group(2) else (lo if m else 9999)
    if not cands and m and m.group(2) and " " in norm(name).strip():
        # 名前が見つからないとき（名簿は本名、記録は登録名：金子 千尋＝金子 弌大、大塚 晶則＝大塚 晶文 など）：
        # 同じ名字で、在籍した年の最初と最後が実働期間とぴったり（±1年）合う人が1人だけなら、その人
        sur = norm(name).strip().split()[0]
        hits = []
        for n2, lst in reg["people"].items():
            if norm(n2).strip().split()[0] != sur:
                continue
            for e in lst:
                yrs = [y for _, ys in (e["h"] if isinstance(e, dict) else e) for y in ys]
                if yrs and abs(min(yrs) - lo) <= 1 and abs(max(yrs) - hi) <= 1:
                    hits.append(e)
        if len(hits) == 1:
            cands = hits
    best = None
    for e in cands:
        hist = e["h"] if isinstance(e, dict) else e
        yrs = [y for _, ys in hist for y in ys]
        if not yrs or max(yrs) < lo - 1 or min(yrs) > hi + 1:
            continue
        cnt, first = {}, {}
        for t, ys in hist:
            cnt.setdefault(t, set()).update(y for y in ys if lo - 1 <= y <= hi + 1)
            first.setdefault(t, min(ys))
        cnt = {t: len(v) for t, v in cnt.items()}
        order = [t for t in sorted(cnt, key=lambda t: (-cnt[t], first[t])) if cnt[t] > 0]   # 長くいた球団から（同じなら先にいた球団）。期間に重ならない球団は入れない
        if best is None or sum(cnt.values()) > best[2]:
            best = (order, (e.get("id") if isinstance(e, dict) else "") or "", sum(cnt.values()))
    return (best[0], best[1]) if best else None


def record_teams(name, period, reg):
    r = record_person(name, period, reg)
    return r[0] if r else None


def parse_player_role(html):
    """NPBの選手のページの投手成績の「通算」の行：1登板あたりの投球回が3回以上なら先発、それより少なければ中継ぎ"""
    soup = BeautifulSoup(html, "html.parser")
    for tb in soup.find_all("table"):
        trs = tb.find_all("tr")
        if not trs:
            continue
        head = [norm(c.get_text("", strip=True)).replace(" ", "") for c in trs[0].find_all(["th", "td"])]
        if "登板" not in head or "投球回" not in head:
            continue
        for tr in trs[1:]:
            cells = [norm(c.get_text("", strip=True)).replace(" ", "") for c in tr.find_all(["th", "td"])]
            if not any(c == "通算" for c in cells[:3]):
                continue
            off = len(cells) - len(head)   # 投球回が「整数｜端数」の2マスに分かれていても、前の列の位置は同じ
            try:
                g = int(cells[head.index("登板")])
                ip = int(re.sub(r"\D", "", cells[head.index("投球回")]) or 0)
            except (ValueError, IndexError):
                return ""
            if g <= 0:
                return ""
            return "先発" if ip / g >= 3.0 else "中継ぎ"
    return ""


WIKI_API = "https://ja.wikipedia.org/w/api.php"
WIKI_UA = {"User-Agent": "hobby-baseball/1.0 (https://negohub.github.io/hobby-baseball/; personal use)"}
WIKI_POS = re.compile(r"(投手|捕手|一塁手|二塁手|三塁手|遊撃手|内野手|外野手)")
# 名簿の球団名（当時の短い名前）→ 記事に書かれる球団の名前（本人かどうかを確かめるため）
WIKI_TEAM = {"巨人": ["ジャイアンツ", "読売"], "阪神": ["タイガース"], "中日": ["ドラゴンズ"], "広島": ["カープ"], "ヤクルト": ["スワローズ"],
             "国鉄": ["スワローズ"], "サンケイ": ["スワローズ", "アトムズ"], "大洋": ["ホエールズ"], "横浜": ["ベイスターズ"], "DeNA": ["ベイスターズ"],
             "南海": ["ホークス"], "ダイエー": ["ホークス"], "ソフトバンク": ["ホークス"], "西鉄": ["ライオンズ"], "太平洋": ["ライオンズ"],
             "クラウン": ["ライオンズ"], "西武": ["ライオンズ"], "阪急": ["ブレーブス"], "オリックス": ["ブルーウェーブ", "バファローズ"],
             "近鉄": ["バファローズ", "バッファローズ", "パールス"], "東映": ["フライヤーズ"], "日拓": ["フライヤーズ"], "日本ハム": ["ファイターズ"],
             "毎日": ["オリオンズ"], "大毎": ["オリオンズ"], "東京": ["オリオンズ"], "ロッテ": ["オリオンズ", "マリーンズ"], "楽天": ["イーグルス"],
             "松竹": ["ロビンス"], "大映": ["スターズ"], "高橋": ["ユニオンズ"]}


WIKI_DIAG = {"ok": 0, "http": 0, "err": 0, "found": 0, "nopos": 0, "last": ""}


class WikiBusy(Exception):
    """ウィキペディアに断られた（混んでいる）：この回はやめて、次の回にもう一度"""


def wiki_get(params):
    time.sleep(0.25)   # 続けて頼みすぎない
    try:
        r = requests.get(WIKI_API, params={**params, "format": "json", "formatversion": "2"}, headers=WIKI_UA, timeout=20)
    except requests.RequestException as e:
        WIKI_DIAG["err"] += 1; WIKI_DIAG["last"] = str(e)[:120]
        raise WikiBusy()
    if r.status_code in (403, 429, 503):
        WIKI_DIAG["http"] += 1; WIKI_DIAG["last"] = f"HTTP {r.status_code} {r.text[:100]}"
        raise WikiBusy()
    if r.status_code != 200:
        WIKI_DIAG["http"] += 1; WIKI_DIAG["last"] = f"HTTP {r.status_code}"
        return {}
    WIKI_DIAG["ok"] += 1
    try:
        return r.json()
    except ValueError:
        return {}


def wiki_pos_of(text, teams):
    """ウィキペディアの記事（野球選手の情報欄）の「ポジション」の最初の守備位置。記事の中にその選手の球団名がなければ別人とみなす"""
    if not text or "曖昧さ回避" in text[:3000]:
        return ""
    if teams and not any(any(a in text for a in [t] + WIKI_TEAM.get(t, [])) for t in teams[:3] if t):
        return ""
    # 情報欄の「ポジション」（「守備位置」の書き方もある）→ なければ記事の書き出し「…元プロ野球選手（内野手）」
    m = re.search(r"\|\s*(?:ポジション|守備位置)\s*=\s*([^\n]+)", text)
    p = WIKI_POS.search(m.group(1)) if m else None
    if not p:
        m2 = re.search(r"プロ野球選手[（(]([^）)]{1,30})[）)]", text[:6000])
        p = WIKI_POS.search(m2.group(1)) if m2 else None
    if not p:
        return ""
    return {"一塁手": "内野手", "二塁手": "内野手", "三塁手": "内野手", "遊撃手": "内野手"}.get(p.group(1), p.group(1))


def wiki_position(name, teams):
    """引退した選手の守備位置（NPBの選手のページに載っていない）：ウィキペディアの記事から。名前（・(野球)）→ 見つからなければ検索"""
    nm = re.sub(r"\s+", "", norm(name))
    def contents(titles):
        d = wiki_get({"action": "query", "prop": "revisions", "rvprop": "content", "rvslots": "main", "redirects": 1, "titles": "|".join(titles)})
        out = []
        for pg in (d.get("query") or {}).get("pages") or []:
            rv = (pg.get("revisions") or [{}])[0]
            out.append(((rv.get("slots") or {}).get("main") or {}).get("content") or rv.get("content") or "")
        return out
    for txt in contents([nm, f"{nm} (野球)"]):
        pos = wiki_pos_of(txt, teams)
        if pos:
            return pos
    d = wiki_get({"action": "query", "list": "search", "srsearch": f"{nm} {teams[0] if teams else ''} プロ野球選手", "srlimit": 3})
    titles = [x.get("title") for x in (d.get("query") or {}).get("search") or [] if x.get("title")]
    if titles:
        for txt in contents(titles):
            pos = wiki_pos_of(txt, teams)
            if pos:
                return pos
    return ""


def parse_player_pos(html):
    """NPBの選手のページ：「ポジション 内野手」→ 内野手（投手・捕手・内野手・外野手）"""
    text = norm(BeautifulSoup(html, "html.parser").get_text(" "))
    m = re.search(r"ポジション\s*[:：]?\s*(投手|捕手|内野手|外野手)", text)
    return m.group(1) if m else ""


# ---------- 歴代の順位表（NPBの年度別成績：1950年〜、セ・パ） ----------
HIST_FROM = 1950


def parse_hist_standings(html):
    """年度別成績のページの「チーム勝敗表」：[[球団名, 試合, 勝利, 敗北, 引分, 勝率, ゲーム差], …]（順位の順）"""
    soup = BeautifulSoup(html, "html.parser")
    out = []
    for tr in soup.find_all("tr"):
        cells = [norm(c.get_text(" ", strip=True)).strip() for c in tr.find_all(["td", "th"])]
        cells = [c for c in cells]
        if len(cells) < 7 or not cells[0] or re.search(r"\d", cells[0]):
            if out:
                break
            continue
        nums = cells[1:5]
        pct = next((c for c in cells[5:] if re.fullmatch(r"\.\d{3}|1\.000|0\.000", c)), None)
        if not all(re.fullmatch(r"\d+", c or "") for c in nums) or not pct:
            if out:
                break   # 勝敗表のすぐ後ろ（チーム打撃成績など）は読まない
            continue
        gb = cells[-1] if cells[-1] != pct else ""
        gb = "" if re.fullmatch(r"[-－‐\s]*", gb) else gb
        out.append([cells[0], int(nums[0]), int(nums[1]), int(nums[2]), int(nums[3]), pct, gb])
    return out


def update_hist_standings(prev_std, season, budget=60):
    std = dict(prev_std or {})
    start = time.time()
    for y in range(season - 1, HIST_FROM - 1, -1):
        for lg, slug in (("C", "centralleague"), ("P", "pacificleague")):
            if (std.get(str(y)) or {}).get(lg):
                continue
            if time.time() - start > budget:
                return std
            html = rec_fetch(f"https://npb.jp/bis/yearly/{slug}_{y}.html")
            rows = parse_hist_standings(html) if html else []
            if rows:
                std.setdefault(str(y), {})[lg] = rows
    return std


def update_records(force=False):
    prev = {}
    if os.path.exists(RECORDS_OUT):
        try:
            with open(RECORDS_OUT, encoding="utf-8") as f:
                prev = json.load(f)
        except (OSError, json.JSONDecodeError):
            prev = {}
    lists = dict(prev.get("lists") or {})
    done = dict(prev.get("done") or {})
    tried = dict(prev.get("tried") or {})   # 取りに行った時刻（ないページ：たとえば通算の出塁率 も、1日1回だけ試す）
    if prev.get("pv") != REC_PARSER:
        tried = {}   # 読み方を直したら、読めなかったページをすぐ取り直す
    now = time.time()
    start, got, fail = time.time(), 0, 0
    for kind, _ in REC_KINDS:
        for side, keys in (("b", REC_BAT), ("p", REC_PIT)):
            for key, label in keys:
                k = f"{kind}{side}_{key}"
                if not force and max(done.get(k, 0), tried.get(k, 0)) > now - REC_EVERY:
                    continue
                if time.time() - start > REC_BUDGET:
                    break
                html = rec_fetch(f"https://npb.jp/bis/history/{k}.html")
                tried[k] = now
                page = parse_record_page(html) if html else None
                if page:
                    lists[k] = page; done[k] = now; got += 1
                else:
                    fail += 1
    # 通算・現役の記録に、在籍者名簿から所属球団を付ける（行ごと：[いちばん長くいた球団, ほかの球団…]）
    try:
        reg = update_register()
    except Exception as e:
        print(f"[在籍者名簿] 読めませんでした: {e}")
        reg = None
    teamed, posc, pos_start, reg_dirty = 0, 0, time.time(), False
    poscache = dict((reg or {}).get("pos") or {})
    rolecache = dict((reg or {}).get("role") or {})
    wiki_stop = [False]
    if (reg or {}).get("posv") != POS_VERSION:
        poscache = {k: ("" if v == "-" else v) for k, v in poscache.items()}   # 読み方を直したので、分からなかった選手をもう一度調べる
        if reg:
            reg["posv"] = POS_VERSION
        reg_dirty = True
    for k, L in lists.items():
        ni = next((i for i, c in enumerate(L["cols"]) if re.search(r"選手|投手|打者", c)), -1)
        pi = next((i for i, c in enumerate(L["cols"]) if re.search(r"実働|期間|年度", c)), -1)
        if ni < 0 or not reg or not reg.get("people"):
            continue
        persons = [record_person(r[ni], r[pi] if pi >= 0 else "", reg) for r in L["rows"]]
        if not k.startswith("ss"):
            new_team = [(p_[0] if p_ else []) for p_ in persons]
            teamed += sum(1 for x in new_team if x)
            if new_team != L.get("team"):
                L["team"] = new_team
                got = got or 1   # 所属が変わったときだけ書き出す（毎回書き出すと、毎回コミットされてしまう）
        if k[2] != "b":
            # 投手の記録：選手のページの通算（登板・投球回）から先発・中継ぎ（読んだ投手は覚えておく）
            new_role = []
            for p_ in persons:
                pid = p_[1] if p_ else ""
                if pid and pid not in rolecache and time.time() - pos_start < POS_BUDGET:
                    html = rec_fetch(f"https://npb.jp/bis/players/{pid}.html")
                    rolecache[pid] = parse_player_role(html) if html else ""
                    reg_dirty = True
                new_role.append(rolecache.get(pid, "") if pid else "")
            if new_role != L.get("role"):
                L["role"] = new_role
                got = got or 1
            continue
        # 打撃の記録：選手のページの守備位置（現役の選手は載っている）。引退した選手は載っていないので、ウィキペディアの記事から。
        # 読んだ選手は覚えておく（"-"＝調べたが分からなかった）。1回あたり POS_BUDGET 秒まで
        new_pos = []
        for p_ in persons:
            pid, tms = (p_[1], p_[0]) if p_ else ("", [])
            if pid and pid not in poscache and time.time() - pos_start < POS_BUDGET:
                html = rec_fetch(f"https://npb.jp/bis/players/{pid}.html")
                poscache[pid] = parse_player_pos(html) if html else ""
                reg_dirty = True
            if pid and poscache.get(pid) == "" and time.time() - pos_start < POS_BUDGET and not wiki_stop[0]:
                nm0 = L["rows"][len(new_pos)][ni]
                try:
                    wp_ = wiki_position(nm0, tms)
                    WIKI_DIAG["found" if wp_ else "nopos"] += 1
                    poscache[pid] = wp_ or "-"
                    reg_dirty = True
                except WikiBusy:
                    wiki_stop[0] = True
            v_ = poscache.get(pid, "") if pid else ""
            new_pos.append(v_ if v_ not in ("-",) else "")
        posc += sum(1 for x in new_pos if x)
        if new_pos != L.get("pos"):
            L["pos"] = new_pos
            got = got or 1
    if reg_dirty and reg:
        reg["pos"] = poscache
        reg["role"] = rolecache
        try:
            with open(REG_OUT, "w", encoding="utf-8") as f:
                json.dump({k2: v2 for k2, v2 in reg.items() if k2 != "_idx"}, f, ensure_ascii=False, separators=(",", ":"))
        except OSError:
            pass
    if reg and reg.get("_idx"):
        reg.pop("_idx", None)
    # 歴代の順位表（一度読んだ年は読み直さない。新しい年はシーズンが終わってNPBのページができたら足す）
    try:
        season_now = int(os.environ.get("SEASON") or datetime.now(JST).year)
        hist = update_hist_standings(prev.get("std"), season_now)
        if hist != (prev.get("std") or {}):
            got = got or 1
    except Exception as e:
        print(f"[歴代の順位表] 読めませんでした: {e}")
        hist = prev.get("std") or {}
    out = {"src": "https://npb.jp/bis/history/", "at": datetime.now(JST).isoformat(timespec="seconds"), "at_ts": now, "std": hist,
           "kinds": [{"k": k, "n": n} for k, n in REC_KINDS],
           "bat": [{"k": k, "n": n} for k, n in REC_BAT], "pit": [{"k": k, "n": n} for k, n in REC_PIT],
           "lists": lists, "done": done, "tried": tried, "pv": REC_PARSER,
           "reg": (reg or {}).get("diag") if isinstance(reg, dict) else None, "teamed": teamed, "posd": posc, "wiki": dict(WIKI_DIAG)}
    wiki_act = WIKI_DIAG["ok"] + WIKI_DIAG["http"] + WIKI_DIAG["err"] > 0
    if got or fail or not os.path.exists(RECORDS_OUT) or prev.get("reg") != out.get("reg") or (wiki_act and prev.get("wiki") != out.get("wiki")):
        with open(RECORDS_OUT, "w", encoding="utf-8") as f:
            json.dump(out, f, ensure_ascii=False, separators=(",", ":"))
    print(f"[歴代記録] 取得 {got}ページ・取れず {fail}ページ（全{len(lists)}件）")


# ================= 球場別成績（今季の全試合の出場成績を、球団・球場・選手ごとに足し合わせる） =================
# スポナビの週の日程（/npb/schedule/?date=）から試合のIDを集め、試合ごとの出場成績（/npb/game/{id}/stats）を読む。
# 読んだ試合は data/boxcache.json に覚えておき（毎回読み直さない）、足し合わせた結果を data/venues.json（データタブの「球場別」が読む）に
BOX_OUT = os.path.join(os.path.dirname(OUT), "boxcache.json")
VEN_OUT = os.path.join(os.path.dirname(OUT), "venues.json")
VEN_BUDGET = 150   # 1回の更新で使う秒数の上限（超えたら続きは次の回）
YFULL = {"読売ジャイアンツ": "G", "横浜DeNAベイスターズ": "DB", "阪神タイガース": "T", "中日ドラゴンズ": "D", "広島東洋カープ": "C",
         "東京ヤクルトスワローズ": "S", "福岡ソフトバンクホークス": "H", "北海道日本ハムファイターズ": "F", "オリックス・バファローズ": "B",
         "東北楽天ゴールデンイーグルス": "E", "埼玉西武ライオンズ": "L", "千葉ロッテマリーンズ": "M"}


def team_code(name):
    n = norm(name).strip()
    if n in YFULL:
        return YFULL[n]
    for full, t in YFULL.items():
        if n and (n in full or full in n):
            return t
    for nm, t in TEAMS:
        if nm in n:
            return t
    return None


def ybox_title(html):
    """出場成績のページの見出し：「2026年10月1日 阪神vs.巨人」→ (日付, ホーム, ビジター)"""
    t = norm((re.search(r"<title>([^<]*)</title>", html) or [None, ""])[1])
    m = re.search(r"(\d{4})年(\d{1,2})月(\d{1,2})日\s*(.+?)vs\.(.+?)(?:\s|$|[-|｜])", t)
    if not m:
        return None
    h, a = team_code(m.group(4)), team_code(m.group(5))
    if not h or not a:
        return None
    return f"{int(m.group(1)):04d}-{int(m.group(2)):02d}-{int(m.group(3)):02d}", h, a


def parse_ybox(html):
    """出場成績：打者 [[ビジター], [ホーム]]・投手 [[ビジター], [ホーム]]（worker.js の parseBox と同じ読み方）"""
    bats, pits = [], []
    for tb in re.findall(r"<table[\s\S]*?</table>", html):
        rows = []
        for tr in re.findall(r"<tr[\s\S]*?</tr>", tb):
            cells = re.findall(r"<t[dh][\s\S]*?</t[dh]>", tr)
            rows.append([re.sub(r"\s+", " ", norm(re.sub(r"<[^>]+>", " ", re.sub(r"<br\s*/?>", " ", c)))).strip() for c in cells])
        if not rows:
            continue
        h = rows[0]
        if "投球回" in h and "奪三振" in h:
            lst = []
            for r in rows[1:]:
                off = len(r) - len(h)
                at = lambda k: (r[h.index(k) + off] if k in h and 0 <= h.index(k) + off < len(r) else "")
                ni = h.index("選手名") + off if "選手名" in h else 0
                name, dec = (r[ni] if 0 <= ni < len(r) else ""), ""
                if re.fullmatch(r"勝|敗|S|H", name) and ni + 1 < len(r):
                    dec, name = name, r[ni + 1]
                elif ni > 0 and re.fullmatch(r"勝|敗|S|H", r[ni - 1] or ""):
                    dec = r[ni - 1]
                m = re.match(r"^(.*?)\s*\((勝|敗|S|H)\)\s*$", name)
                if m:
                    name, dec = m.group(1), dec or m.group(2)
                if not name or re.fullmatch(r"勝|敗|S|H|合計", name):
                    continue
                lst.append({"n": name, "dec": dec, "ip": at("投球回"), "h": at("被安打"), "so": at("奪三振"), "bb": at("与四球"), "hbp": at("与死球"), "er": at("自責点")})
            if lst:
                pits.append(lst)
        elif "打数" in h and "1回" in h:
            i1, lst = h.index("1回"), []
            ix = lambda k: h.index(k) if k in h else -1
            for r in rows[1:]:
                if len(r) < 2 or not r[1] or r[0] == "合計":
                    continue
                res = [x for c in r[i1:] if c for x in re.split(r"[ 、]+", c) if x]
                g = lambda k: (r[ix(k)] if 0 <= ix(k) < len(r) else "")
                lst.append({"n": r[1], "ab": g("打数"), "h": g("安打"), "rbi": g("打点"), "hr": g("本塁打"), "bb": g("四球"), "res": res})
            if lst:
                bats.append(lst)
    return bats, pits


def _i(x):
    try:
        return int(str(x).strip() or 0)
    except ValueError:
        return 0


def _outs(ip):
    m = re.match(r"^(\d+)(?:\.(\d))?$", str(ip).strip())
    return int(m.group(1)) * 3 + int(m.group(2) or 0) if m else 0


def box_compact(bats, pits, h, a):
    """1試合の出場成績を小さくまとめる：打者 [球団, 名前, 打数, 安打, 本塁打, 打点, 四球, 死球, 犠飛, 塁打]・投手 [球団, 名前, アウト数, 自責点, 勝, 敗, S, H, 奪三振, 被安打, 与四死球]"""
    if len(bats) != 2 or len(pits) != 2:
        return None
    B, P = [], []
    for side, t in ((0, a), (1, h)):
        for x in bats[side]:
            res = x["res"]
            hbp = sum(1 for r in res if "死球" in r)
            sf = sum(1 for r in res if "犠飛" in r)
            bb = max(_i(x["bb"]), sum(1 for r in res if re.search(r"四球|敬遠|故四", r)))
            tb = sum(4 if "本" in r else 3 if re.search(r"3$", r) else 2 if re.search(r"2$", r) else 1 if "安" in r else 0 for r in res)
            B.append([t, x["n"], _i(x["ab"]), _i(x["h"]), _i(x["hr"]), _i(x["rbi"]), bb, hbp, sf, tb])
        for x in pits[side]:
            d = x["dec"]
            P.append([t, x["n"], _outs(x["ip"]), _i(x["er"]), int(d == "勝"), int(d == "敗"), int(d == "S"), int(d == "H"), _i(x["so"]), _i(x["h"]), _i(x["bb"]) + _i(x["hbp"])])
    return {"bat": B, "pit": P}


def update_venues(games, season, budget=VEN_BUDGET):
    cache = {}
    if os.path.exists(BOX_OUT):
        try:
            with open(BOX_OUT, encoding="utf-8") as f:
                cache = json.load(f)
        except (OSError, json.JSONDecodeError):
            cache = {}
    if cache.get("season") != season:
        cache = {"season": season, "weeks": {}, "ids": {}, "games": {}, "bad": {}}
    today = datetime.now(JST).strftime("%Y-%m-%d")
    fin = {(g["d"], g["h"], g["a"]): g for g in games if g.get("st") == "final" and g.get("d", "")[:4] == str(season) and g["d"] < today}
    want = {f"{d}|{h}|{a}" for (d, h, a) in fin}
    have = set(cache["games"])
    start, fetched = time.time(), 0
    # 1) 試合のID：まだIDのない試合がある週の日程を読む（月曜はじまりの週ごと）
    need_days = sorted({k.split("|")[0] for k in want - have - set(cache["ids"].values())})
    weeks = []
    for d in need_days:
        dt = datetime.strptime(d, "%Y-%m-%d")
        wk = (dt - timedelta(days=dt.weekday())).strftime("%Y-%m-%d")
        if wk not in weeks:
            weeks.append(wk)
    for wk in weeks:
        if time.time() - start > budget:
            break
        if cache["weeks"].get(wk) == today:
            continue
        html = fetch(f"{YAHOO}/schedule/?date={wk}")
        fetched += 1
        cache["weeks"][wk] = today
        for gid in dict.fromkeys(re.findall(r"/npb/game/(\d{8,12})/", html or "")):
            if gid in cache["ids"] or cache["bad"].get(gid):
                continue
            cache["ids"][gid] = ""   # 中身（どの試合か）は出場成績のページの見出しで決める
    # 2) 出場成績：まだ読んでいない試合のページを読む
    for gid in list(cache["ids"]):
        if time.time() - start > budget:
            break
        if cache["ids"][gid] and cache["ids"][gid] in cache["games"]:
            continue
        if cache["bad"].get(gid) in (today, "x"):
            continue
        k0 = cache["ids"][gid]
        if k0 and k0 not in want and k0.split("|")[0] < today:
            cache["bad"][gid] = "x"   # 終わった日の、今季の公式戦でない試合（ポストシーズン・中止など）はもう読まない
            continue
        html = fetch(f"{YAHOO}/game/{gid}/stats")
        fetched += 1
        tt = ybox_title(html or "")
        if not tt:
            cache["bad"][gid] = today
            continue
        d, h, a = tt
        key = f"{d}|{h}|{a}"
        cache["ids"][gid] = key
        g = fin.get((d, h, a))
        if not g:
            # まだ終わっていない試合は明日もう一度。終わった日なのに公式戦の結果にない試合（ポストシーズンなど）はもう読まない
            cache["bad"][gid] = today if d >= today else "x"
            continue
        comp = box_compact(*parse_ybox(html), h, a)
        if comp:
            comp.update({"v": g.get("v", ""), "id": gid})
            cache["games"][key] = comp
    # 3) 足し合わせ：球団 → 球場 → 選手
    agg = {}
    for key, gm in cache["games"].items():
        if key not in want:
            continue
        v = gm.get("v") or ""
        for row in gm["bat"]:
            t, n = row[0], row[1]
            cur = agg.setdefault(t, {}).setdefault(v, {"bat": {}, "pit": {}, "g": set()})["bat"].setdefault(n, [0] * 9)
            cur[0] += 1
            for i in range(8):
                cur[i + 1] += row[i + 2]
            agg[t][v]["g"].add(key)
        for row in gm["pit"]:
            t, n = row[0], row[1]
            cur = agg.setdefault(t, {}).setdefault(v, {"bat": {}, "pit": {}, "g": set()})["pit"].setdefault(n, [0] * 10)
            cur[0] += 1
            for i in range(9):
                cur[i + 1] += row[i + 2]
            agg[t][v]["g"].add(key)
    teams = {t: {v: {"games": len(x["g"]), "bat": [[n] + r for n, r in x["bat"].items()], "pit": [[n] + r for n, r in x["pit"].items()]}
                 for v, x in vs.items()} for t, vs in agg.items()}
    got = len(want & set(cache["games"]))
    out = {"season": season, "at": datetime.now(JST).isoformat(timespec="seconds"), "have": got, "total": len(want),
           "bat_cols": ["試合", "打数", "安打", "本塁打", "打点", "四球", "死球", "犠飛", "塁打"],
           "pit_cols": ["登板", "アウト", "自責点", "勝", "敗", "S", "H", "奪三振", "被安打", "与四死球"], "teams": teams}
    with open(BOX_OUT, "w", encoding="utf-8") as f:
        json.dump(cache, f, ensure_ascii=False, separators=(",", ":"))
    old = None
    if os.path.exists(VEN_OUT):
        try:
            with open(VEN_OUT, encoding="utf-8") as f:
                old = json.load(f)
        except (OSError, json.JSONDecodeError):
            old = None
    if not old or {k: v for k, v in old.items() if k != "at"} != {k: v for k, v in out.items() if k != "at"}:
        with open(VEN_OUT, "w", encoding="utf-8") as f:
            json.dump(out, f, ensure_ascii=False, separators=(",", ":"))
    print(f"[球場別成績] {got}/{len(want)}試合（今回 {fetched}ページ）")


def main():
    try:
        update_records()
    except Exception as e:   # 記録が取れなくても、ふだんのデータ更新は止めない
        print(f"[歴代記録] 取得できませんでした: {e}")
    season = int(os.environ.get("SEASON") or datetime.now(JST).year)
    old = None
    if os.path.exists(OUT):
        with open(OUT, encoding="utf-8") as f:
            try:
                old = json.load(f)
            except json.JSONDecodeError:
                old = None
    try:
        update_venues((old or {}).get("games") or [], season)
    except Exception as e:   # 球場別成績が取れなくても、ふだんのデータ更新は止めない
        print(f"[球場別成績] 取得できませんでした: {e}")
    if old is not None and "tstats" not in old and os.path.exists(TSTATS_OUT):
        try:
            with open(TSTATS_OUT, encoding="utf-8") as f:
                old["tstats"] = json.load(f)
        except (OSError, json.JSONDecodeError):
            pass
    if old and old.get("season") and int(old["season"]) < season and not os.path.exists(os.path.join(ARCH_DIR, f"{int(old['season'])}.json")):
        write_archive(old, force=True)   # 前のシーズンの記録を残してから、新しいシーズンのデータにする
    old_games = old["games"] if old and old.get("season") == season else []
    old_month = {}
    for g in old_games:
        old_month.setdefault(int(g["d"][5:7]), []).append(g)

    all_games, ticker = [], {}
    for mo in MONTHS:
        url = f"https://npb.jp/games/{season}/schedule_{mo:02d}_detail.html"
        gs = []
        for attempt in range(3):
            html = fetch(url)
            if html is None:
                break
            ticker.update(parse_ticker(html))
            gs = parse_month(html, season)
            if gs:
                break
            print(f"[{mo}月] 0試合でした。取り直します（{attempt + 1}回目）")
            time.sleep(5)
        prev = old_month.get(mo, [])
        if prev and len(gs) < len(prev) * 0.8:
            print(f"[{mo}月] 読み取り不足（{len(gs)}件）のため前回のデータ{len(prev)}件を使います")
            gs = prev
        st = {k: sum(g["st"] == k for g in gs) for k in ("final", "sched", "live", "canc")}
        print(f"[{mo}月] {len(gs)}試合 {st}")
        all_games += gs

    month = datetime.now(JST).strftime("%Y-%m")
    if not all_games:
        if old:
            # オフシーズン（新しい年の日程がまだ出ていない）→ 前のデータをそのまま残す
            print(f"{season}年の試合はまだありません。前のデータを残します")
            if old.get("checked") != month:
                old["checked"] = month  # 月1回ファイルを更新して、GitHubの自動実行が止まらないようにする
                write_json(old)
                print("月1回の生存確認を記録しました")
            return
        print("試合が1件も取れませんでした。ページ構成が変わった可能性があります。")
        sys.exit(1)

    # 当日の「試合終了」欄の結果を反映
    for g in all_games:
        k = key(g)
        if g["st"] in ("sched", "live") and k in ticker:
            if ticker[k] == "canc":
                g["st"] = "canc"
                g.pop("t", None)
                print(f"  速報から反映: {k} 中止")
            else:
                g["st"], g["hs"], g["as"] = "final", ticker[k][0], ticker[k][1]
                g.pop("t", None)
                print(f"  速報から反映: {k} {g['hs']}-{g['as']}")
    # 一度確定した結果は巻き戻さない
    done = {key(g): g for g in old_games if g["st"] in ("final", "canc")}
    for i, g in enumerate(all_games):
        k = key(g)
        if g["st"] in ("sched", "live") and k in done:
            all_games[i] = done[k]
            print(f"  前回の確定結果を維持: {k}")

    all_games.sort(key=lambda g: (g["d"], g["h"]))
    old_stats = old.get("stats") if old and old.get("season") == season else None

    # 試合結果以外の取り込みは、1つが失敗しても前回の値を使って続ける（試合結果の更新まで止めない）
    def safe(label, fn, fallback):
        try:
            return fn()
        except Exception as e:  # noqa: BLE001
            print(f"[{label}] 取り込みでエラー（前回の値を使います）: {type(e).__name__}: {e}")
            return fallback

    stats = safe("成績", lambda: fetch_stats(season, old_stats), old_stats)
    old_stats_p = old.get("stats_p") if old and old.get("season") == season else None
    stats_p = safe("パ・成績", lambda: fetch_stats(season, old_stats_p, 2), old_stats_p)
    tstats = safe("チーム内の成績", lambda: fetch_team_stats(season, old if old and old.get("season") == season else None), (old or {}).get("tstats"))
    prev_order = safe("前年の順位", lambda: fetch_prev_order(season, old), (old or {}).get("prev_order"))
    prev_order_p = safe("パ・前年の順位", lambda: fetch_prev_order(season, old, "p"), (old or {}).get("prev_order_p"))
    rosters, roster_date = safe("選手一覧", lambda: fetch_rosters(old), ((old or {}).get("rosters") or {}, (old or {}).get("roster_date")))
    post = safe("ポストシーズン", lambda: fetch_post(season, old), (old or {}).get("post"))
    # CS・日本シリーズの結果：当日の試合欄（ticker）から、その日のその段階の試合を1つに決められたら結果を入れる
    for g in post or []:
        if g.get("st") == "final":
            continue
        lg = g.get("lg", "C")
        cands = [k for k in ticker if k[0] == g["d"] and (
            (k[1] in CL) != (k[2] in CL) if g["stage"] == "JS" else all(x in (PL if lg == "P" else CL) for x in k[1:]))]
        if len(cands) == 1:
            k = cands[0]
            g["h"], g["a"] = k[1], k[2]
            if ticker[k] == "canc":
                g["st"] = "canc"
            else:
                g["hs"], g["as"], g["st"] = ticker[k][0], ticker[k][1], "final"
    fpos = safe("守備位置", lambda: fetch_fpos(season, old), (old or {}).get("fpos"))
    # Actions の画面で「Run workflow」を押したとき（手動で実行したとき）は、3時間の間隔を待たずに球団サイトを見に行く
    off_force = os.environ.get("OFF_FORCE") == "1" or os.environ.get("GITHUB_EVENT_NAME") == "workflow_dispatch"
    offseason = safe("戦力外・引退", lambda: fetch_offseason(season, old, rosters, off_force, os.environ.get("OFF_FORCE") == "1"), (old or {}).get("offseason"))
    # スポナビの入退団情報（毎回。球団のサイトが読めない球団の分も拾う）
    offseason = safe("入退団情報", lambda: merge_transfer(offseason, season, rosters), offseason)
    # 前の回までに入った「10月0日」のような日付は、見つけた日（今日）に直す
    if offseason and offseason.get("items"):
        _today = datetime.now(JST).strftime("%Y-%m-%d")
        for _x in offseason["items"]:
            if re.search(r"-00$|-00-", _x.get("date") or ""):
                _x["date"] = _today
    if (old and old_games == all_games and old_stats == stats and old.get("prev_order") == prev_order
            and old_stats_p == stats_p and old.get("prev_order_p") == prev_order_p
            and old.get("checked") == month and old.get("rosters") == rosters and old.get("song_rev") == SONG_REV
            and old.get("post") == post and old.get("fpos") == fpos
            and old.get("offseason") == offseason and old.get("tstats") == tstats):
        print("変化なし")
        return
    data = {
        "updated": datetime.now(JST).strftime("%Y-%m-%dT%H:%M:%S+09:00") if old_games != all_games or not old else old.get("updated"),
        "season": season,
        "games": all_games,
        "stats": stats,
        "stats_p": stats_p,
        "prev_order": prev_order,
        "prev_order_p": prev_order_p,
        "checked": month,
        "rosters": rosters,
        "roster_date": roster_date,
        "song_rev": SONG_REV,
        "post": post,
        "fpos": fpos,
        "offseason": offseason,
        "tstats": tstats,
    }
    write_json(data)
    print(f"保存しました: {len(all_games)}試合")


if __name__ == "__main__":
    main()
