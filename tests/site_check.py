"""
hobby baseball：サイト全体の自動検査

  python tests/site_check.py            # すべて検査（index.html を直接開く）

検査すること（テーマ2つ × 画面幅2つ × 状態いろいろ × 全タブ）
  1. 画面を開いたり操作したりしてエラーが出ないか
  2. 表や画面が横にはみ出していないか
  3. 文字が背景に溶けて読めなくなっていないか（半透明の背景も重ねて計算）
  4. 大事な部品がちゃんと表示されているか（順位表・マジック・スコア・一球速報など）
  5. データの差し替え（試合以外だけ変わったとき）が反映されるか

問題があれば一覧を出して、終了コード 1 で終わる（GitHub Actions で赤い×になる）。
"""
import asyncio
import json
import os
import sys
from pathlib import Path

from playwright.async_api import async_playwright

ROOT = Path(__file__).resolve().parent.parent
URL = (ROOT / "index.html").as_uri()
LIVE = "https://live.example/"
PROBLEMS = []


def bad(msg):
    PROBLEMS.append(msg)
    print("  ✕", msg)
    # GitHub Actions で動いているときは、Actions の画面の「Annotations」にも出す（ログを開かなくても原因が見える）
    if os.environ.get("GITHUB_ACTIONS"):
        print("::error title=サイト検査::" + str(msg).replace("\n", " ").replace("%", "%25"))


# ---------- 中継プログラムの代わり（試合中の一球速報・打順・投手成績など） ----------
DETAIL = {
    "line": {"innings": [str(i) for i in range(1, 10)],
             "away": {"name": "ヤクルト", "inn": ["0", "0", "1", "0", "0", "", "", "", ""], "r": "1", "h": "4", "e": "0"},
             "home": {"name": "巨人", "inn": ["2", "0", "0", "0", "0", "", "", "", ""], "r": "2", "h": "6", "e": "1"}},
    "flows": [{"half": "1回裏", "runs": 2, "box": True, "steps": [
        {"order": "1番", "name": "丸 佳浩", "sit": None, "res": "左安"},
        {"order": "2番", "name": "泉口 友汰", "sit": None, "res": "空三振"},
        {"order": "3番", "name": "吉川 尚輝", "sit": None, "res": "四球"},
        {"order": "4番", "name": "岡本 和真", "sit": None, "res": "左本"}]}],
    "plays": [{"half": "1回裏", "order": "", "name": "巨人の攻撃", "kind": "2点", "score": "ヤ 0-2 巨"}],
    "cur": {"half": "6回表", "steps": [{"order": "1番", "name": "丸山 和郁", "res": "四球"}, {"order": "2番", "name": "長岡 秀樹", "res": "右安"}]},
    "lineups": [[{"order": 1, "pos": "中", "starter": True, "name": "丸山 和郁", "avg": ".262", "results": ["二ゴロ", "中安", "四球"]},
                 {"order": 2, "pos": "遊", "starter": True, "name": "長岡 秀樹", "avg": ".281", "results": ["見三振", "右2", "投犠打", "遊失"]},
                 {"order": 2, "pos": "打", "starter": False, "name": "北村 恵吾", "avg": ".210", "results": []}],
                [{"order": 1, "pos": "中", "starter": True, "name": "丸 佳浩", "avg": ".270", "results": ["左安", "右中本"]}]],
    "pitchers": [[{"name": "高橋 奎二", "dec": "", "era": "3.12", "ip": "5", "np": "92", "h": "5", "hr": "1", "so": "6", "bb": "2", "r": "2", "er": "2"}],
                 [{"name": "山﨑 伊織", "dec": "勝", "era": "2.41", "ip": "5.1", "np": "88", "h": "4", "hr": "0", "so": "6", "bb": "2", "r": "1", "er": "1"}]],
    "over": False,
}
PITCH = {"half": "6回表", "attack": "ヤクルト", "b": 2, "s": 1, "o": 1, "bases": {}, "runners": {},
         "batter": {"name": "オスナ", "no": "13", "hand": "右打", "avg": ".271", "game": {"ab": "2", "hit": "1", "rbi": "1", "hr": "0", "bb": "0", "results": ["中安", "遊ゴロ"]}},
         "pitcher": {"name": "山﨑 伊織", "no": "19", "hand": "右投", "era": "2.41", "game": {"ip": "5.1", "np": "88", "bf": "24", "h": "4", "so": "6", "bb": "2", "r": "1"}},
         "next": "エンカーナシオン",  # 長い名前でも切れないかを見るため
         "pitches": [{"n": 1, "type": "ストレート", "speed": "148km/h", "res": "ボール"}, {"n": 2, "type": "フォーク", "speed": "136km/h", "res": "空振り"},
                     {"n": 3, "type": "スライダー", "speed": "131km/h", "res": "ファウル"}]}


GAME = {"d": "2026-09-27", "h": "G", "a": "S", "h2": "DB", "a2": "C"}


# 歴代記録の試しのデータ（NPBの歴代最高記録と同じ形）
REC_SAMPLE = {"at": "2026-10-02T06:00:00+09:00", "src": "https://npb.jp/bis/history/",
  "kinds": [{"k": "lt", "n": "通算"}, {"k": "ac", "n": "現役"}, {"k": "ss", "n": "シーズン"}],
  "bat": [{"k": "hr", "n": "本塁打"}, {"k": "avg", "n": "打率"}, {"k": "sb", "n": "盗塁"}], "pit": [{"k": "w", "n": "勝利"}, {"k": "so", "n": "奪三振"}],
  "lists": {
    "ltb_hr": {"cols": ["順位", "選手", "本塁打", "実働期間", "試合", "打数"], "rows": [["1", "王 貞治", "868", "(1959-1980)", "2831", "9250"], ["2", "野村 克也", "657", "(1954-1980)", "3017", "10472"], ["10", "中村 剛也", "482", "(2003-2026)", "2168", "7317"]], "asof": "2026年10月1日(木)", "note": "", "act": [0, 0, 1]},
    "ltb_avg": {"cols": ["順位", "選手", "打率", "実働期間", "打数", "安打"], "rows": [["1", "リー", ".320", "(1977-1987)", "4934", "1579"]], "asof": "2026年10月1日(木)", "note": "4000打数以上"},
    "ltp_w": {"cols": ["順位", "選手", "勝利", "実働期間", "登板"], "rows": [["1", "金田 正一", "400", "(1950-1969)", "944"]], "asof": "2026年10月1日(木)", "note": ""},
    "acb_hr": {"cols": ["順位", "選手", "本塁打", "実働期間"], "rows": [["1", "中村 剛也", "482", "(2003-2026)"]], "asof": "2026年10月1日(木)", "note": ""},
    "ssb_avg": {"cols": ["順位", "選手", "(所属)", "打率", "年度", "打数", "安打"], "rows": [["1", "バース", "(阪 神)", ".389", "(1986)", "453", "176"], ["2", "イチロー", "(オリックス)", ".387", "(2000)", "395", "153"]], "asof": "2025年度シーズン終了", "note": "打率 .350 以上(各シーズン規定以上)"},
    "ssp_so": {"cols": ["順位", "選手", "(所属)", "奪三振", "年度"], "rows": [["1", "江夏 豊", "(阪 神)", "401", "(1968)"]], "asof": "2025年度シーズン終了", "note": ""}},
  "done": {}}


async def route_live(route):
    u = route.request.url
    if "records.json" in u:
        return await route.fulfill(status=200, content_type="application/json", body=json.dumps(REC_SAMPLE, ensure_ascii=False), headers={"Access-Control-Allow-Origin": "*"})
    if "pitch=" in u:
        body = PITCH
    elif "game=" in u:
        body = DETAIL
    elif "stats=team" in u:
        body = {}
    elif "rank=" in u:
        body = {}
    elif "pstats=" in u:
        body = {"asof": "9/26", "bat": {"丸山和郁": {"試合": "100", "打席": "350", "打率": ".262", "本塁打": "3", "打点": "25", "安打": "80", "出塁率": ".330", "長打率": ".350"}},
                "pit": {"山﨑伊織": {"登板": "24", "勝利": "10", "敗北": "5", "セーブ": "0", "投球回": "150.1", "三振": "120", "四球": "30", "安打": "120", "防御率": "2.41"}}}
    else:
        body = {"games": [{"d": GAME["d"], "h": GAME["h"], "a": GAME["a"], "st": "live", "hs": 2, "as": 1, "inn": "6回表"}]
                + ([{"d": GAME["d"], "h": GAME["h2"], "a": GAME["a2"], "st": "final", "hs": 3, "as": 5}] if GAME.get("h2") else [])}
    await route.fulfill(status=200, headers={"Access-Control-Allow-Origin": "*", "Content-Type": "application/json"}, body=json.dumps(body))


# ---------- 画面の中を調べる（ブラウザ側で動かす） ----------
CHECK_JS = r"""
(tab) => {
  const out = { overflow: [], contrast: [] };
  const view = document.getElementById('v-' + tab);
  // はみ出し：表・ページ
  document.querySelectorAll('#v-' + tab + ' .ywrap').forEach(wr => {
    const tb = wr.querySelector('table'); if (!tb || !wr.offsetParent) return;
    const a = wr.getBoundingClientRect(), c = tb.getBoundingClientRect();
    if (c.right > a.right + 1) out.overflow.push((tb.id || tb.className) + ' が ' + Math.round(c.right - a.right) + 'px はみ出し');
  });
  if (document.documentElement.scrollWidth > innerWidth + 1) out.overflow.push('ページ全体が横にはみ出し ' + document.documentElement.scrollWidth + 'px');
  // 文字の読みやすさ：背景を外側から重ねて実際の色を出し、文字色との明るさの比を見る
  const parse = c => {
    if (!c) return null;
    let m = c.match(/^color\(srgb ([\d.]+) ([\d.]+) ([\d.]+)(?: \/ ([\d.]+))?\)/);
    if (m) return [m[1] * 255, m[2] * 255, m[3] * 255, m[4] === undefined ? 1 : +m[4]];
    m = c.match(/rgba?\(([\d.]+),\s*([\d.]+),\s*([\d.]+)(?:,\s*([\d.]+))?\)/);
    if (m) return [+m[1], +m[2], +m[3], m[4] === undefined ? 1 : +m[4]];
    return null;
  };
  const lin = v => { v /= 255; return v <= .03928 ? v / 12.92 : Math.pow((v + .055) / 1.055, 2.4); };
  const L = c => .2126 * lin(c[0]) + .7152 * lin(c[1]) + .0722 * lin(c[2]);
  const pawa = document.documentElement.classList.contains('theme-pawa');
  const base = pawa ? [205, 235, 255, 1] : [5, 5, 6, 1];
  const bgAt = el => {
    const chain = []; for (let e = el; e && e !== document.documentElement; e = e.parentElement) chain.unshift(e);
    let col = base.slice(0, 3);
    for (const e of chain) {
      const cs = getComputedStyle(e);
      if (cs.backgroundImage && cs.backgroundImage !== 'none') {
        // グラデーション：一番上に敷いた色を代表として使う（半透明なら重ねる）
        const m = cs.backgroundImage.match(/(rgba?\([^)]+\)|color\(srgb[^)]+\))/);
        const g = m ? parse(m[1]) : null;
        if (g && g[3] > .6) col = [0, 1, 2].map(i => g[i] * g[3] + col[i] * (1 - g[3]));
        else if (!g) return null;
      }
      const b = parse(cs.backgroundColor);
      if (b && b[3] > 0) col = [0, 1, 2].map(i => b[i] * b[3] + col[i] * (1 - b[3]));
      if (+cs.opacity < .99 && e !== el) {} // 親の半透明は文字にも効くので比較には影響しない
    }
    return col;
  };
  view.querySelectorAll('*').forEach(el => {
    if (!el.offsetParent) return;
    const own = [...el.childNodes].some(n => n.nodeType === 3 && n.textContent.trim());
    if (!own) return;
    const cs = getComputedStyle(el);
    if (cs.visibility === 'hidden' || cs.textShadow !== 'none') return;
    if (el.closest('.wm, .twm')) return; // 背番号の透かしはわざと薄くしている
    let op = 1; for (let e = el; e; e = e.parentElement) op *= +getComputedStyle(e).opacity;
    if (op < .5) return; // 参考表示（広島など）はわざと薄くしている
    const fg = parse(cs.color); if (!fg) return;
    const bg = bgAt(el); if (!bg) return;
    const f = [0, 1, 2].map(i => fg[i] * fg[3] + bg[i] * (1 - fg[3]));
    const a = L(f), b = L(bg), ratio = (Math.max(a, b) + .05) / (Math.min(a, b) + .05);
    if (ratio < 1.6) out.contrast.push(`${el.tagName.toLowerCase()}.${el.className} 「${el.textContent.trim().slice(0, 10)}」 比${ratio.toFixed(2)}`);
  });
  out.contrast = [...new Set(out.contrast)].slice(0, 12);
  return out;
}
"""

TABS = ["magic", "game", "cal", "std", "stats", "rec", "song", "off"]   # off：戦力外・引退（オフだけ出るタブ）


async def open_page(browser, width, theme, me="S", touch=False):
    pg = await browser.new_page(viewport={"width": width, "height": 844}, has_touch=touch)
    errs = []
    pg.on("pageerror", lambda e: errs.append(str(e)))
    # 明るさ：これまでの検査はスタイリッシュ＝黒、パワプロ風＝昼で作っているので、そのまま（ライト・夜は light_check・night_check で）
    mode = "dark" if theme == "" else "light"
    await pg.add_init_script(f"localStorage.setItem('me','{me}'); localStorage.setItem('theme','{theme}'); localStorage.setItem('mode','{mode}'); localStorage.setItem('songTeam','T'); localStorage.setItem('league','C')")
    await pg.route(LIVE + "**", route_live)
    await pg.route("https://api.open-meteo.com/**", lambda r: r.fulfill(status=500, body="x"))
    await pg.goto(URL)
    await pg.wait_for_timeout(700)
    # 検査に使う日：データの中で、対象球団どうしの試合がある一番新しい日（来季以降も同じ検査が使えるように）
    pick = await pg.evaluate("""(() => {
      const T = g => TEAM[g.h] && TEAM[g.a];
      const days = [...new Set(DATA.games.filter(g => T(g) && g.st !== 'canc').map(g => g.d))].sort().reverse();
      for (const d of days) { const gs = DATA.games.filter(g => g.d === d && T(g) && g.st !== 'canc'); if (gs.length) return { d, gs: gs.map(g => [g.h, g.a]) }; }
      return null; })()""")
    if pick:
        GAME.update({"d": pick["d"], "h": pick["gs"][0][0], "a": pick["gs"][0][1]})
        if len(pick["gs"]) > 1:
            GAME.update({"h2": pick["gs"][1][0], "a2": pick["gs"][1][1]})
        else:
            GAME.pop("h2", None)
    y, m, d = GAME["d"].split("-")
    await pg.evaluate(f"CONFIG.recUrl='{LIVE}records.json'; loadRec(true);")
    await pg.evaluate(f"CONFIG.liveApi='{LIVE}'; jst=()=>({{y:{int(y)},m:{int(m)},d:{int(d)},iso:'{GAME['d']}'}}); liveWanted=()=>true; autoGame=false;")
    return pg, errs


async def scan(pg, label):
    for t in TABS:
        await pg.evaluate(f"setTab('{t}'); window.scrollTo(0,0)")
        await pg.wait_for_timeout(120)
        r = await pg.evaluate(CHECK_JS, t)
        for o in r["overflow"]:
            bad(f"{label} {t}: {o}")
        for c in r["contrast"]:
            bad(f"{label} {t}: 文字が読みにくい {c}")


async def essentials(pg, label):
    """大事な部品が出ているか"""
    n = await pg.evaluate("document.querySelectorAll('#cards tbody tr').length")
    if n < 5:
        bad(f"{label}: 戦況の順位表の行が足りない（{n}行）")
    if not await pg.evaluate("!!document.querySelector('#alert').innerText.trim()"):
        bad(f"{label}: 戦況の帯（このままだと支払い）が空")
    await pg.evaluate("setTab('std')")
    if await pg.evaluate("document.querySelectorAll('#std tbody tr').length") < 6:
        bad(f"{label}: 順位タブの順位表が足りない")
    await pg.evaluate("setTab('stats')")
    if await pg.evaluate("document.querySelectorAll('#tmTbl tbody tr').length") < 6:
        bad(f"{label}: チーム成績の表が足りない")
    await pg.evaluate("setTab('song')")
    if await pg.evaluate("document.querySelectorAll('#songList .ptile').length") < 5:
        bad(f"{label}: 応援歌の選手タイルが足りない")
    # パワプロ風：どの表でも、球団名のマスにチームカラーのグラデーションが付いているか（偶数行で消えていた不具合の再発防止）
    if await pg.evaluate("document.documentElement.classList.contains('theme-pawa')"):
        for tab, sel in (("magic", "#cards"), ("std", "#std"), ("stats", "#tmTbl")):
            await pg.evaluate(f"setTab('{tab}')")
            miss = await pg.evaluate(f"[...document.querySelectorAll('{sel} tbody tr:not(.ex) td.tnm')].filter(td => !getComputedStyle(td).backgroundImage.startsWith('linear')).map(td => td.innerText.split('\\n')[0])")
            if miss:
                bad(f"{label}: {tab} の表で球団名のグラデーションが消えている {miss}")


async def game_live(pg, label):
    """試合中：カードを開いて一球速報・打順・投手成績まで出るか"""
    await pg.evaluate("pollLive()")
    await pg.wait_for_timeout(500)
    await pg.evaluate("setTab('game'); window.scrollTo(0,0)")
    await pg.wait_for_timeout(200)
    card = await pg.query_selector(".tg.islive .tgx")
    if not card:
        bad(f"{label}: 試合中のカードが出ない")
        return
    await card.click()
    await pg.wait_for_timeout(900)
    for sel, name in [(".tg.islive .ls", "スコア表"), (".tg.islive .pbox", "一球速報"), (".tg.islive .lu", "打順"), (".tg.islive .pu", "投手成績"), (".tg.islive .bug", "スコアバグ")]:
        if not await pg.query_selector(sel):
            bad(f"{label}: 試合中の{name}が出ない")
    # 一球速報の投手・打者・次の打者の名前もタップできる形になっているか
    for sel, nm in ((".tg.islive .pc3 .pn3[data-pl]", "一球速報の投手・打者"), (".tg.islive .pnx3 b[data-pl]", "次の打者")):
        if not await pg.query_selector(sel):
            bad(f"{label}: {nm}の名前がタップできる形になっていない")
    # 打順の選手名をタップ → 選手の成績の画面が出るか
    name = await pg.query_selector(".tg.islive .lnm[data-pl]")
    if not name:
        bad(f"{label}: 打順の選手名がタップできる形になっていない")
    else:
        await name.click()
        await pg.wait_for_timeout(600)
        txt = await pg.evaluate("document.getElementById('songSheet').hidden ? '' : document.getElementById('songPick').innerText")
        if "打撃成績" not in txt and "投手成績" not in txt and "出場記録" not in txt:
            bad(f"{label}: 選手名をタップしても成績が出ない")
        r = await pg.evaluate(CHECK_JS, "game")
        await pg.evaluate("document.getElementById('songSheet').classList.remove('open'); document.getElementById('songSheet').hidden = true")
    clipped = await pg.evaluate("""(() => { const out = [];
      for (const sel of ['.tg.islive .pnx3 b', '.tg.islive .pr3 > *', '.tg.islive .pn3 b']) document.querySelectorAll(sel).forEach(e => {
        const box = e.closest('.pc3') || e.parentElement, r = e.getBoundingClientRect(), c = box.getBoundingClientRect();
        if (r.right > c.right + 1 || e.scrollWidth > e.clientWidth + 1) out.push(e.textContent.trim()); });
      return out; })()""")
    for c in clipped:
        bad(f"{label}: 試合中の一球速報で「{c}」が切れている")
    runners = await pg.evaluate("document.querySelectorAll('.tg.islive .fld .bs.on').length")
    if await pg.evaluate("document.documentElement.classList.contains('theme-pawa')"):
        r = await pg.evaluate("[document.querySelectorAll('.tg.islive .rtile').length, document.querySelectorAll('.tg.islive .fld g[data-pl]').length]")
        if r[0] < 1 or r[1] > 0:
            bad(f"{label}: パワプロ風でランナーが選手タイルになっていない（タイル{r[0]}・黒い札{r[1]}）")
    if await pg.evaluate("document.documentElement.classList.contains('theme-pawa')"):
        tiles = await pg.evaluate("document.querySelectorAll('.tg.islive .fldw .rtile').length")
        if tiles != runners:
            bad(f"{label}: パワプロ風のランナーの札が選手タイルになっていない（札{tiles}／ランナー{runners}）")
    if runners != 2:
        bad(f"{label}: ランナーの塁の数が想定と違う（{runners}）")
    r = await pg.evaluate(CHECK_JS, "game")
    for c in r["contrast"]:
        bad(f"{label} 試合中: 文字が読みにくい {c}")
    for o in r["overflow"]:
        bad(f"{label} 試合中: {o}")


async def cal_weather(pg, label):
    """雨の確率が高い日があっても、日程の7列が同じ幅のままか（名前のぶつかりで列が広がった不具合の再発防止）"""
    widths = await pg.evaluate("""(() => {
      const time = [], code = [], temp = [], pop = [];
      const d0 = new Date(jst().iso + 'T00:00:00');
      for (let k = -3; k < 10; k++) { const d = new Date(d0.getTime() + k * 864e5); const ds = d.toISOString().slice(0, 10);
        for (let h = 0; h < 24; h++) { time.push(ds + 'T' + String(h).padStart(2, '0') + ':00'); code.push(61); temp.push(22); pop.push(90); } }
      Object.keys(VENUE).forEach(k => (WX[k] = { at: Date.now(), h: { time, weather_code: code, temperature_2m: temp, precipitation_probability: pop } }));
      const out = [];
      for (const t of Object.keys(CONFIG.owners)) { S.calTeam = t; S.calMonth = null; setTab('cal'); renderCal();
        const w = [...document.querySelectorAll('.cal .wd')].slice(0, 7).map(e => Math.round(e.getBoundingClientRect().width));
        if (Math.max(...w) - Math.min(...w) > 2) out.push(t + ':' + w.join(',')); }
      return out; })()""")
    for w in widths:
        bad(f"{label}: 雨の日があると日程の列の幅がそろわない {w}")


async def starters_check(pg, label):
    """予告先発が分かっている試合前の試合で、日程の詳細に予告先発が出るか"""
    r = await pg.evaluate(r"""(() => {
      const g = DATA.games.find(x => x.st === 'sched' && TEAM[x.h] && TEAM[x.a]);
      if (!g) return 'skip';
      YK = { [`${g.d}|${g.h}|${g.a}`]: { h: 'テスト太郎', a: null } };
      const t = Object.keys(CONFIG.owners).includes(g.h) ? g.h : g.a;
      S.calTeam = t; S.calMonth = +g.d.slice(5, 7); S.calSel = g.d; setTab('cal'); renderCal();
      const e = document.querySelector('#detail .yk');
      const out = e ? e.innerText.replace(/\s+/g, ' ') : '';
      YK = {};
      return out; })()""")
    if r != "skip" and ("テスト太郎" not in r or "未発表" not in r):
        bad(f"{label}: 日程の詳細に予告先発が出ない（{r}）")


async def season_end(pg, label):
    await pg.evaluate("DATA.games.forEach(g=>{ if(g.st==='sched'||g.st==='live'||g.st==='canc'){ g.st='final'; g.hs=3; g.as=2; } }); renderAll(); renderAllNow(); setTab('magic')")   # 優勝ラインは順位タブにある。後回しの描画を先に済ませてから見る
    await pg.wait_for_timeout(200)
    t = await pg.evaluate("document.getElementById('ttl').textContent")
    if "最終結果" not in t:
        bad(f"{label}: シーズン終了後の表示に切り替わらない（{t}）")
    if await pg.evaluate("document.getElementById('raceBox').innerHTML.length") > 0:
        bad(f"{label}: 優勝が決まったあとも優勝ラインが出ている")
    await scan(pg, label + " シーズン終了後")


async def sheets(pg, label):
    await pg.evaluate("openSheet()")
    await pg.wait_for_timeout(350)
    await pg.evaluate("closeSheet(); openMePick()")
    await pg.wait_for_timeout(350)
    await pg.evaluate("closeMePick(); setTab('song')")
    await pg.wait_for_timeout(300)
    tile = await pg.query_selector("#songList .ptile")
    if tile:
        await tile.click()
        await pg.wait_for_timeout(350)
        if await pg.evaluate("document.getElementById('songSheet').hidden"):
            bad(f"{label}: 応援歌の選手をタップしても画面が出ない")


async def data_refresh(browser):
    """試合以外（守備位置）だけ変わった最新データも反映されるか"""
    d = json.loads((ROOT / "data" / "latest.json").read_text(encoding="utf-8"))
    old = dict(d); old.pop("fpos", None)
    new = dict(d); new["fpos"] = {"season": 2026, "teams": {"T": {"佐藤輝明": {"内": 118, "外": 18}}}, "roles": {}}
    served = {"body": json.dumps(old)}
    import http.server, threading, functools
    handler = functools.partial(http.server.SimpleHTTPRequestHandler, directory=str(ROOT))
    handler.log_message = lambda *a: None
    srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    try:
        pg = await browser.new_page(viewport={"width": 390, "height": 844})
        await pg.add_init_script("localStorage.setItem('me','S'); localStorage.setItem('theme',''); localStorage.setItem('mode','dark')")
        await pg.route("**/data/latest.json*", lambda r: r.fulfill(status=200, headers={"Content-Type": "application/json"}, body=served["body"]))
        await pg.goto(f"http://127.0.0.1:{srv.server_address[1]}/index.html")
        await pg.wait_for_timeout(900)
        served["body"] = json.dumps(new)
        await pg.reload()
        await pg.wait_for_timeout(1200)
        ok = await pg.evaluate("!!(DATA.fpos && DATA.fpos.teams && DATA.fpos.teams.T)")
        if not ok:
            bad("データ差し替え: 試合以外だけ変わった最新データが反映されない")
        await pg.close()
    finally:
        srv.shutdown()


async def calc_cache(browser):
    """計算結果の使い回し：元の計算と同じ答えになるか・試合が変わったら計算し直すか・書き換えても混ざらないか（両リーグ）"""
    for lg in ["C", "P"]:
        pg, errs = await open_page(browser, 390, "")
        if lg == "P":
            await pg.evaluate("switchLeague('P')")
            await pg.wait_for_timeout(200)
        r = await pg.evaluate("""() => {
          const strip = a => { const c = JSON.parse(JSON.stringify(a)); if (!c.exact) c.rows.forEach(r => { delete r.rate; delete r.avoid; delete r.self; }); return JSON.stringify(c); };
          const ng = [];
          for (const p of CONFIG.periods) {
            if (strip(analyze(DATA.games, p, CONFIG)) !== strip(analyzeRaw(DATA.games, p, CONFIG))) ng.push("一致しない " + p.id);
            if (JSON.stringify(analyze(DATA.games, p, CONFIG)) !== JSON.stringify(analyze(DATA.games, p, CONFIG))) ng.push("毎回ちがう " + p.id);
          }
          const g = DATA.games.find(g => g.st === "final" && CL.includes(g.h) && g.hs !== g.as);
          const p = CONFIG.periods.find(p => p.months.includes(+g.d.slice(5, 7)));
          const key = () => JSON.stringify(analyze(DATA.games, p, CONFIG).rows.map(r => [r.t, r.w, r.l]));
          const before = key(); const hs = g.hs, as = g.as; g.hs = as; g.as = hs;
          if (key() === before) ng.push("試合が変わっても計算し直さない");
          g.hs = hs; g.as = as;
          if (key() !== before) ng.push("元に戻しても答えが戻らない");
          // 使い回した結果の残り試合が、画面の試合データそのものを指しているか（「勝ったら」の計算で使う）
          for (const q of CONFIG.periods) {
            analyze(DATA.games, q, CONFIG); const y1 = analyze(DATA.games, q, CONFIG);   // 2回目は使い回し
            // 振替待ちの仮の試合（virt：日付が決まっていない試合）は試合データにないので除く
            if (y1.remaining.some(g => !g.virt && !DATA.games.includes(g))) ng.push(`使い回した結果の残り試合が、試合データそのものを指していない ${q.id}`);
            if (y1.rows.some(r => (r.left || []).some(g => !g.virt && !DATA.games.includes(g)))) ng.push(`使い回した結果の球団ごとの残り試合が、試合データそのものを指していない ${q.id}`);
          }
          const x = analyze(DATA.games, p, CONFIG); x.rows[0].w = 999;
          if (analyze(DATA.games, p, CONFIG).rows[0].w === 999) ng.push("返した結果の書き換えが使い回しに混ざる");
          return ng;
        }""")
        for m in r:
            bad(f"[計算の使い回し {lg}] {m}")
        for e in errs:
            bad(f"[計算の使い回し {lg}]: 画面のエラー {e}")
        await pg.close()


async def song_link_check(browser):
    """選手ごとの応援歌ページ（su）：あればそれを開く・おかしなURLは使わない"""
    pg, errs = await open_page(browser, 390, "")
    r = await pg.evaluate("""() => {
      const ng = [], L = (DATA.rosters || {}).L || [], a = L.find(x => x.song), b = L.filter(x => x.song)[1];
      if (!a || !b) return ng;
      const oa = a.su, ob = b.su;
      a.su = "https://www.yakyu-ouen.net/test-player/"; b.su = "javascript:alert(1)";
      if (songLink("L", a) !== a.su) ng.push("選手ごとのページが使われない");
      if (/^javascript/.test(songLink("L", b) || "")) ng.push("おかしなURLがそのまま使われる");
      a.su = oa; b.su = ob;
      return ng;
    }""")
    for m in r:
        bad(f"[応援歌のリンク] {m}")
    for e in errs:
        bad(f"[応援歌のリンク]: 画面のエラー {e}")
    await pg.close()


async def team_in_check(browser):
    """チーム内の成績：選手が多くても表がはみ出さない・全員表示・並べ替え（両テーマ×幅390/320×両リーグ）"""
    for theme in ["", "pawa"]:
        for width in [390, 320]:
            for lg in ["C", "P"]:
                label = f"[チーム内の成績 {'パワプロ風' if theme else 'スタイリッシュ'} 幅{width} {lg}]"
                pg, errs = await open_page(browser, width, theme)
                if lg == "P":
                    await pg.evaluate("switchLeague('P')")
                    await pg.wait_for_timeout(200)
                r = await pg.evaluate("""() => {
                  DATA.tstats = null; DATA.tstats_at = null;   // この試験は中継プログラム（NPB）の表で見る
                  const ng = [];
                  setTab("stats");
                  for (const t of CL) {
                    const bat = {}, pit = {};
                    (DATA.rosters[t] || []).forEach((x, i) => {
                      const k = x.n.replace(/\\s+/g, "");
                      if (x.p === "投手") pit[k] = { 登板: "52", 投球回: "160.1", 防御率: "12.34", 勝利: "12", 敗北: "10", 三振: "188" };
                      else bat[k] = { 試合: "143", 打席: String(600 - i), 打率: ".333", 本塁打: "44", 打点: "123", 出塁率: ".444", 長打率: ".666" };
                    });
                    // とても長い名前の選手も混ぜる（省略せずに収まるか）
                    bat["ダーウィンゾンヘルナンデスジュニア"] = { 試合: "143", 打席: "700", 打率: ".333", 本塁打: "44", 打点: "123", 出塁率: ".444", 長打率: ".666" };
                    pit["クリストファーアレクサンダー"] = { 登板: "52", 投球回: "200.2", 防御率: "12.34", 勝利: "12", 敗北: "10", 三振: "188" };
                    PST[t] = { at: Date.now(), d: { bat, pit, asof: "9/28" } };
                  }
                  const widths = {};
                  const clipped = () => [...document.querySelectorAll("#ptTbl td, #ptTbl th")].filter(c => c.scrollWidth > c.clientWidth + 1).length;
                  const over = () => { const tb = document.getElementById("ptTbl"); return tb && tb.scrollWidth > tb.parentElement.clientWidth + 1 ? tb.scrollWidth - tb.parentElement.clientWidth : 0; };
                  for (const t of CL) {
                    S.ptTeam = t;
                    for (const k of ["bat", "pit"]) {
                      S.ptKind = k; S.ptAll = false; renderTeamIn();
                      const n = document.querySelectorAll("#ptTbl tbody tr").length;
                      if (!n) ng.push(`${t} ${k}: 表が出ない`);
                      if (over()) ng.push(`${t} ${k}: 表が ${over()}px はみ出し`);
                      if (clipped()) ng.push(`${t} ${k}: 文字がマスからはみ出したセルが ${clipped()} 個`);
                      widths[k] = widths[k] || new Set(); widths[k].add(Math.round(document.getElementById("ptTbl").getBoundingClientRect().width) + "/" + [...document.querySelectorAll("#ptTbl thead th")].map(th => Math.round(th.getBoundingClientRect().width)).join(","));
                      const more = document.getElementById("ptMore");
                      if (more) { more.click(); if (document.querySelectorAll("#ptTbl tbody tr").length <= n) ng.push(`${t} ${k}: 全員を表示が効かない`); if (over()) ng.push(`${t} ${k}: 全員表示で ${over()}px はみ出し`); }
                      const th = document.querySelector("#ptTbl th.srt[data-col='2']"); if (th) { th.click(); if (!document.querySelector("#ptTbl th.srt.on")) ng.push(`${t} ${k}: 並べ替えが効かない`); }
                    }
                  }
                  for (const k in widths) if (widths[k].size > 1) ng.push(`${k}: 球団によって表の幅・列の幅が違う（${[...widths[k]].join(" | ")}）`);
                  return ng;
                }""")
                for m in r[:5]:
                    bad(f"{label} {m}")
                for e in errs:
                    bad(f"{label}: 画面のエラー {e}")
                await pg.close()


async def starter_order_check(browser):
    """予告先発：試合タブ・日程の詳細とも、ホームの投手が左（先）に来るか"""
    pg, errs = await open_page(browser, 390, "")
    r = await pg.evaluate("""() => {
      const g = { d: "2099-01-01", h: "T", a: "S", st: "sched" };
      YK[`${g.d}|${g.h}|${g.a}`] = { h: "才木 浩人", a: "奥川 恭伸" };
      const box = document.createElement("div");
      const ng = [];
      for (const html of [ykHTML(g), ykHTML(g, true)]) {
        box.innerHTML = html;
        const first = box.querySelector(".ykp");
        if (!first || !first.dataset.pl.startsWith("T|")) ng.push("ホームの投手が左になっていない");
      }
      delete YK[`${g.d}|${g.h}|${g.a}`];
      return ng;
    }""")
    for m in r:
        bad(f"[予告先発の並び] {m}")
    for e in errs:
        bad(f"[予告先発の並び]: 画面のエラー {e}")
    await pg.close()


async def wording_check(browser):
    """説明文と実際の見た目・リーグが合っているか
    1) パ・リーグ表示で、セ・リーグだけの言葉（支払・広島・担当など）が出ていないか（逆も）
    2) 「白枠」「青い枠」「黄色」「オレンジ」「白」「金色」などの色の説明が、そのテーマの実際の色と合っているか"""
    NG = {"P": ["支払", "広島", "担当", "セ・リーグ", "5球団", "神の行", "巨の列"], "C": ["パ・リーグ", "6球団", "ソの行"]}
    OK_P = "担当者・支払いはセ・リーグだけの遊びです"
    for theme in ["", "pawa"]:
        for lg in ["C", "P"]:
            label = f"[説明文 {'パワプロ風' if theme else 'スタイリッシュ'} {lg}]"
            pg, errs = await open_page(browser, 390, theme)
            if lg == "P":
                await pg.evaluate("switchLeague('P')")
                await pg.wait_for_timeout(200)
            for tab in ["magic", "game", "cal", "std", "stats", "rec", "song", "off"]:
                txt = await pg.evaluate("""(tab) => { setTab(tab); const v = document.getElementById('v-' + tab); v.querySelectorAll('details').forEach(d => d.open = true); return v.innerText; }""", tab)
                for line in txt.split("\n"):
                    if lg == "P" and OK_P in line:
                        continue
                    for w in NG[lg]:
                        if w in line:
                            bad(f"{label} [{tab}] リーグに合わない言葉「{w}」: {line.strip()[:80]}")
                            break
            # 色の説明と実際の色
            r = await pg.evaluate("""() => {
              const ng = [], rgb = s => (s.match(/\\d+(\\.\\d+)?/g) || []).map(Number);
              const vis = el => el && el.offsetParent !== null;
              // 画面に見えている文字（テーマで隠れている言葉は含まない）から、書いてある色を読む
              const said = (root, words) => { const t = root.innerText; return words.find(w => t.includes(w)) || null; };
              const isWhite = c => { const [r, g, b] = rgb(c); return r > 230 && g > 230 && b > 230; };
              const isBlue = c => { const [r, g, b] = rgb(c); return b > 90 && b > r + 40; };
              setTab('magic');
              const lab = [...document.querySelectorAll('#condBody .lab')].find(l => l.textContent.includes('直接対決'));
              const vs = document.querySelector('#condBody .left span.vs'), nv = document.querySelector('#condBody .left span:not(.vs)');
              if (lab && vs) {
                const w = said(lab, ['白枠', '青い枠']), bc = getComputedStyle(vs).borderTopColor;
                if (!w) ng.push('直接対決の説明に色が書かれていない');
                if (w === '白枠' && !isWhite(bc)) ng.push(`直接対決は「白枠」と書いているのに枠の色が ${bc}`);
                if (w === '青い枠' && !isBlue(bc)) ng.push(`直接対決は「青い枠」と書いているのに枠の色が ${bc}`);
                if (nv && getComputedStyle(nv).borderTopColor === bc) ng.push('直接対決とほかの試合の枠の色が同じ');
              }
              setTab('stats');
              const best = document.querySelector('#tmTbl td.best'), li = [...document.querySelectorAll('#v-stats .howto li')].find(l => l.textContent.includes('リーグトップ'));
              if (best && li) {
                const w = said(li, ['黄色', 'オレンジ']), c = rgb(getComputedStyle(best).color);
                if (w === '黄色' && !(c[0] > 200 && c[1] > 170 && c[2] < 120)) ng.push(`チーム成績のトップは「黄色」と書いているのに ${c}`);
                if (w === 'オレンジ' && !(c[0] > 150 && c[0] > c[1] * 1.6 && c[1] > 40 && c[2] < 80))   /* 読みやすさのため濃いオレンジ（168,79,0 など）も可 */ ng.push(`チーム成績のトップは「オレンジ」と書いているのに ${c}`);
              }
              setTab('std');
              const first = document.querySelector('.ytbl td.yc2.first b'), li2 = [...document.querySelectorAll('#v-std .howto li')].find(l => l.textContent.includes('年間成績'));
              if (first && li2 && vis(li2)) {
                const w = said(li2, ['白＝', '金色＝']), cs = getComputedStyle(first);
                if (w === '白＝' && !isWhite(cs.backgroundColor)) ng.push(`年間成績の1位は「白」と書いているのに ${cs.backgroundColor}`);
                if (w === '金色＝' && !/255, 230, 128|242, 182, 0/.test(cs.backgroundImage + cs.backgroundColor)) ng.push(`年間成績の1位は「金色」と書いているのに ${cs.backgroundImage}`);
              }
              return ng;
            }""")
            for m in r:
                bad(f"{label} {m}")
            for e in errs:
                bad(f"{label}: 画面のエラー {e}")
            await pg.close()


# 指の操作をまねる（touchstart→touchmove→touchend）
SWIPE_JS = """([sel, dx, dy]) => {
  const el = typeof sel === "string" ? document.querySelector(sel) : sel;
  if (!el) return "no-element";
  el.scrollIntoView({ block: "center" });
  const b = el.getBoundingClientRect(), x = b.left + b.width / 2, y = b.top + Math.min(b.height / 2, 40);
  const mk = (cx, cy) => new Touch({ identifier: 1, target: el, clientX: cx, clientY: cy });
  const fire = (type, cx, cy) => { const t = mk(cx, cy); el.dispatchEvent(new TouchEvent(type, { touches: type === "touchend" ? [] : [t], targetTouches: type === "touchend" ? [] : [t], changedTouches: [t], bubbles: true, cancelable: true })); };
  fire("touchstart", x, y);
  for (let i = 1; i <= 8; i++) fire("touchmove", x + dx * i / 8, y + dy * i / 8);
  fire("touchend", x + dx, y + dy);
  return "ok";
}"""


async def tap_target_check(browser):
    """指で押す部品が縦横44px以上あるか（表の球団名はマス全体が押せるか）。両テーマ×幅390/320×両リーグ・全タブ"""
    for theme in ["", "pawa"]:
        for width in [390, 320]:
            for lg in ["C", "P"]:
                label = f"[押しやすさ {'パワプロ風' if theme else 'スタイリッシュ'} 幅{width} {lg}]"
                pg, errs = await open_page(browser, width, theme)
                if lg == "P":
                    await pg.evaluate("switchLeague('P')")
                    await pg.wait_for_timeout(200)
                for tab in ["magic", "game", "cal", "std", "stats", "rec", "song", "off"]:
                    r = await pg.evaluate("""(tab) => {
                      setTab(tab);
                      const v = document.getElementById('v-' + tab), ng = new Set();
                      v.querySelectorAll('a[href], button, summary, select, th.srt').forEach(e => {
                        if (!e.offsetParent || e.closest('.ptile, .howto li, .foot, p')) return;   // 文章の中のリンク・選手名の札は対象外
                        let b = e.getBoundingClientRect();
                        const td = e.matches('td.tnm a') ? e.closest('td') : null;
                        if (td) {
                          // マス全体が押せるか：リンクの見えない面（a::before）がマスいっぱいに広がっているか、
                          // さらにマスの左端・右端（文字のない所）を押したときにこのリンクに当たるか
                          const pb = getComputedStyle(e, '::before');
                          if (getComputedStyle(td).position !== 'relative' || pb.position !== 'absolute' || pb.top !== '0px' || pb.left !== '0px' || pb.right !== '0px' || pb.bottom !== '0px') {
                            ng.add(`球団名のマス全体が押せる作りになっていない：${e.textContent.trim().slice(0, 10)}`); return;
                          }
                          e.scrollIntoView({ block: 'center' }); b = td.getBoundingClientRect();
                          const y = b.top + b.height / 2;
                          for (const x of [b.left + 4, b.right - 4]) {
                            const hit = document.elementFromPoint(x, y);
                            if (!hit || !(hit === e || e.contains(hit) || hit.closest('a') === e)) {
                              const d = hit ? `${hit.tagName.toLowerCase()}${hit.id ? '#' + hit.id : ''}.${String(hit.className).split(' ')[0]}` : 'なし';
                              ng.add(`球団名のマスの端を押してもリンクにならない：${e.textContent.trim().slice(0, 10)}（押された所：${d}、位置 ${Math.round(x)},${Math.round(y)}、画面の高さ ${innerHeight}）`); return;
                            }
                          }
                        }
                        if (b.width < 1) return;
                        // 表の見出し（並べ替え）と日程のカレンダーの日付（7列）は、列の幅が画面幅で決まるので高さだけ確かめる
                        if (b.height < 43.5 || (b.width < 43.5 && !e.matches('th.srt, #cal button.day'))) ng.add(`${e.tagName.toLowerCase()}「${e.textContent.trim().slice(0, 10)}」${Math.round(b.width)}×${Math.round(b.height)}`);
                      });
                      return [...ng].slice(0, 6);
                    }""", tab)
                    for m in r:
                        bad(f"{label} [{tab}] 押す部品が小さい：{m}")
                r = await pg.evaluate("(() => { window.scrollTo(0, 0); const b = document.getElementById('gearBtn').getBoundingClientRect(); return b.width >= 43.5 && b.height >= 43.5 ? '' : `設定ボタンが小さい（${Math.round(b.width)}×${Math.round(b.height)}）`; })()")
                if r:
                    bad(f"{label} {r}")
                for e in errs:
                    bad(f"{label}: 画面のエラー {e}")
                await pg.close()


async def swipe_check(browser):
    """戦況の順位表のあたりを左右にスワイプすると月度が変わる・それ以外の場所ではタブが変わる"""
    for theme in ["", "pawa"]:
        label = f"[スワイプ {'パワプロ風' if theme else 'スタイリッシュ'}]"
        pg, errs = await open_page(browser, 390, theme, touch=True)
        await pg.evaluate("setTab('magic'); window.scrollTo(0, 0)")
        before = await pg.evaluate("S.period.id")
        list_ = await pg.evaluate("periods().map(p => p.id)")
        i = list_.index(before)
        await pg.evaluate(SWIPE_JS, ["#cards", 160, 0])      # 右へ：前の月度
        await pg.wait_for_timeout(800)
        after = await pg.evaluate("[S.tab, S.period.id]")
        if i > 0 and (after[0] != "magic" or after[1] != list_[i - 1]):
            bad(f"{label} 順位表を右へスワイプしても前の月度にならない（{before}→{after}）")
        await pg.evaluate(SWIPE_JS, ["#cards", -160, 0])     # 左へ：元の月度
        await pg.wait_for_timeout(800)
        after = await pg.evaluate("[S.tab, S.period.id]")
        if after != ["magic", before]:
            bad(f"{label} 順位表を左へスワイプしても元の月度に戻らない（{after}）")
        await pg.evaluate(SWIPE_JS, ["#cards", 30, 0])       # 少しだけ：変わらない
        await pg.wait_for_timeout(800)
        if await pg.evaluate("S.period.id") != before:
            bad(f"{label} 少し動かしただけで月度が変わる")
        # 最後の月度で順位表を左へ（次の月度がない）：試合タブへ進む
        if i == len(list_) - 1:
            await pg.evaluate(SWIPE_JS, ["#cards", -160, 0])
            await pg.wait_for_timeout(800)
            if await pg.evaluate("S.tab") != "game":
                bad(f"{label} 最後の月度で順位表を左へスワイプしても試合タブへ進まない")
            await pg.evaluate("setTab('magic'); window.scrollTo(0, 0)")
            await pg.wait_for_timeout(300)
        await pg.evaluate(SWIPE_JS, ["#formBlk", -160, 0])   # 順位表以外：タブが変わる
        await pg.wait_for_timeout(800)
        if await pg.evaluate("S.tab") != "game":
            bad(f"{label} 順位表以外の場所を左へスワイプしてもタブが変わらない")
        # 動き終わったあと、画面がずれたり透明のまま残ったりしていないか
        left = await pg.evaluate("[...document.querySelectorAll('.view, #alert, #meCard, #cards')].filter(el => el.style.transform || el.style.opacity).map(el => el.id)")
        if left:
            bad(f"{label} スワイプのあと、画面の位置や透明度が元に戻っていない：{left}")
        for e in errs:
            bad(f"{label}: 画面のエラー {e}")
        await pg.close()


async def pull_refresh_check(browser):
    """引っぱって更新：ホーム画面から開いたときだけ動き、更新の処理が呼ばれる"""
    pg, errs = await open_page(browser, 390, "", touch=True)
    r = await pg.evaluate("""async () => {
      const ng = []; let called = 0;
      window.scrollTo(0, 0);
      // ブラウザで開いているとき：動かない
      const sw = async () => { const el = document.querySelector('#alert'); const mk = y => new Touch({ identifier: 2, target: el, clientX: 200, clientY: y });
        const f = (type, y) => { const t = mk(y); el.dispatchEvent(new TouchEvent(type, { touches: type === 'touchend' ? [] : [t], changedTouches: [t], bubbles: true, cancelable: true })); };
        f('touchstart', 150); for (let i = 1; i <= 8; i++) f('touchmove', 150 + 16 * i); f('touchend', 278); await new Promise(r => setTimeout(r, 50)); };
      await sw();
      if (document.getElementById('ptrTxt').textContent !== '引っぱって更新' || getComputedStyle(document.getElementById('ptr')).opacity !== '0') ng.push('ブラウザで開いているのに引っぱって更新が動く');
      window.__ptrForce = true;
      const t0 = document.getElementById('ptrTxt').textContent;
      await sw();
      const txt = document.getElementById('ptrTxt').textContent;
      if (!/更新中|最新です|更新しました|更新できません/.test(txt)) ng.push(`ホーム画面から開いたときに引っぱって更新が動かない（表示：${txt}）`);
      await new Promise(r => setTimeout(r, 1500));
      window.__ptrForce = false;
      return ng;
    }""")
    for m in r:
        bad(f"[引っぱって更新] {m}")
    for e in errs:
        bad(f"[引っぱって更新]: 画面のエラー {e}")
    await pg.close()


async def memory_check(browser):
    """成績タブの打者/投手・項目・並べ替えを、開き直しても覚えているか
    （file:// で開くと、開き直したときに端末への保存が消えることがある検査環境の癖があるため、手元のサーバー経由で開く）"""
    import http.server, threading, functools

    class Quiet(http.server.SimpleHTTPRequestHandler):
        def log_message(self, *a):
            pass

    srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), functools.partial(Quiet, directory=str(ROOT)))
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    url = f"http://127.0.0.1:{srv.server_port}/index.html"
    try:
        pg = await browser.new_page(viewport={"width": 390, "height": 844})
        errs = []
        pg.on("pageerror", lambda e: errs.append(str(e)))
        await pg.add_init_script("localStorage.setItem('me','S'); localStorage.setItem('theme',''); localStorage.setItem('mode','dark'); localStorage.setItem('league','C')")
        await pg.route("https://**", lambda r: r.abort())
        await pg.goto(url)
        await pg.wait_for_timeout(700)
        await pg.evaluate("""() => {
          setTab('stats');
          document.querySelector('#tmSeg button[data-k="pit"]').click();
          document.querySelector('#tmTbl th.srt[data-col="1"]').click();
          document.querySelector('#rkSeg button[data-k="pit"]').click();
          S.cat = CATS.pit[2][0]; renderStats();
          document.querySelector('#ptSeg button[data-k="pit"]').click();
        }""")
        want = await pg.evaluate("[S.tmKind, JSON.stringify(S.tmSort), S.rkKind, S.cat, S.ptKind]")
        await pg.wait_for_timeout(300)
        await pg.reload()
        await pg.wait_for_timeout(700)
        got = await pg.evaluate("[S.tmKind, JSON.stringify(S.tmSort), S.rkKind, S.cat, S.ptKind]")
        if got != want:
            bad(f"[状態の記憶] 開き直すと成績タブの状態が戻る（前：{want} → 後：{got}）")
        # 壊れた値が入っていても画面が壊れない
        await pg.evaluate("v => localStorage.setItem('statsUI', v)", json.dumps({"tmKind": "xx", "cat": "nope", "tmSort": {"kind": "bat", "col": "a"}}))
        await pg.reload()
        await pg.wait_for_timeout(700)
        ok = await pg.evaluate("setTab('stats'), [S.tmKind, CATS[S.rkKind].some(c => c[0] === S.cat), !!document.querySelector('#tmTbl tbody tr')]")
        if ok != ["bat", True, True]:
            bad(f"[状態の記憶] 壊れた値が入っていると成績タブがおかしくなる（{ok}）")
        for e in errs:
            bad(f"[状態の記憶]: 画面のエラー {e}")
        await pg.close()
    finally:
        srv.shutdown()


async def loser_wording_check(browser):
    """負けた側のマジックが減る場面では「負けても自力脱出が残る」と書き、「M5 → M4」とは書かない"""
    pg, errs = await open_page(browser, 390, "")
    r = await pg.evaluate("""() => {
      // 今の月度の残り試合から、負けた側のマジックが減る場面を探す
      const p = S.period, base = analyze(DATA.games, p, CONFIG), by = {}; base.rows.forEach(x => by[x.t] = x);
      for (const g of base.remaining) {
        for (const hw of [true, false]) {
          const g2 = DATA.games.map(x => x === g ? { ...x, st: 'final', hs: hw ? 1 : 0, as: hw ? 0 : 1 } : x);
          const b = analyze(g2, p, CONFIG), lo = hw ? g.a : g.h, B = by[lo], R = b.rows.find(x => x.t === lo);
          if (B && R && !B.safe && !B.eliminated && !R.safe && !R.eliminated && B.self != null && R.self != null && R.self < B.self) {
            // その試合を今日の試合にして描く
            const today = g.d; jst = () => ({ y: +today.slice(0, 4), m: +today.slice(5, 7), d: +today.slice(8), iso: today });
            setTab('game'); renderGame();
            const box = [...document.querySelectorAll('#today .tgo')].find(el => el.querySelector('b').textContent.startsWith(fn(hw ? g.h : g.a) + 'が勝ったら'));
            if (!box) return ['場面は見つかったが、「勝ったら」の欄が出ない'];
            const t = box.textContent, ng = [];
            if (!t.includes(fn(lo) + 'は負けても自力脱出が残る')) ng.push(`「${fn(lo)}は負けても自力脱出が残る」と書かれていない：${t.slice(0, 80)}`);
            if (t.includes(fn(lo) + 'のマジック M' + B.self + ' → M' + R.self)) ng.push('負けた側のマジックが「M○ → M○」と減るように書かれている');
            return ng;
          }
        }
      }
      return [];
    }""")
    for m in r:
        bad(f"[負けた側の書き方] {m}")
    for e in errs:
        bad(f"[負けた側の書き方]: 画面のエラー {e}")
    await pg.close()


async def home_screen_check(browser):
    """ホーム画面に置いたときにアプリのように開く設定があるか"""
    pg, errs = await open_page(browser, 390, "")
    r = await pg.evaluate("""() => [!!document.querySelector('link[rel=manifest]'), (document.querySelector('meta[name=apple-mobile-web-app-capable]') || {}).content]""")
    if r != [True, "yes"]:
        bad(f"[ホーム画面] アプリとして開く設定が足りない（{r}）")
    mf = ROOT / "manifest.json"
    try:
        m = json.loads(mf.read_text(encoding="utf-8"))
        if m.get("display") != "standalone" or not m.get("icons"):
            bad("[ホーム画面] manifest.json の display／icons がおかしい")
    except Exception as e:
        bad(f"[ホーム画面] manifest.json が読めない（{e}）")
    await pg.close()


async def next_day_check(browser):
    """試合のない日に出る「次の試合」の「勝ったら」が、その日当日に見たときと同じ内容か（計算の使い回しで試合を見失わないか）"""
    pg, errs = await open_page(browser, 390, "")
    r = await pg.evaluate("""() => {
      const p = S.period, a = analyze(DATA.games, p, CONFIG);
      if (!a.remaining.length) return [];
      // 残り試合の期間の中で、試合のない日（D）と、その次に試合がある日（first）を探す
      const ds = [...new Set(a.remaining.map(g => g.d))].sort();
      let D = null, first = null;
      for (let t = new Date(ds[0] + 'T00:00:00Z'); t.toISOString().slice(0, 10) < ds[ds.length - 1]; t.setUTCDate(t.getUTCDate() + 1)) {
        const iso = t.toISOString().slice(0, 10);
        const mine = g => a.rows.some(r => r.t === g.h || r.t === g.a);   // 表示しているリーグの対象球団の試合
        if (!DATA.games.some(g => g.d === iso && g.st !== 'canc' && mine(g))) { D = iso; first = ds.find(x => x > iso); break; }
      }
      if (!D || !first) return [];
      const read = iso => { jst = () => ({ y: +iso.slice(0, 4), m: +iso.slice(5, 7), d: +iso.slice(8), iso }); setTab('game'); renderGame();
        return [...document.querySelectorAll('#today .tgo')].map(e => e.innerText.replace(/\\s+/g, ' ')).sort().join(' | '); };
      const nextView = read(D), dayView = read(first);
      return nextView === dayView ? [] : [`前の日に見た「次の試合」と当日の内容が違う：${nextView.slice(0, 120)} ／ ${dayView.slice(0, 120)}`];
    }""")
    for m in r:
        bad(f"[次の試合] {m}")
    for e in errs:
        bad(f"[次の試合]: 画面のエラー {e}")
    await pg.close()


async def name_center_check(browser):
    """パワプロ風の選手名のタイル（チーム内の成績）で、名前がタイルの真ん中にあるか（字間の分がずれていないか）"""
    for width in [390, 320]:
        pg, errs = await open_page(browser, width, "pawa")
        r = await pg.evaluate("""() => {
          setTab('stats'); const t = CL[0], bat = {}, pit = {};
          (DATA.rosters[t] || []).forEach((x, i) => { const k = x.n.replace(/\\s+/g, ''); if (x.p === '投手') pit[k] = { 登板: '9', 投球回: '9', 防御率: '1.00', 勝利: '1', 敗北: '1', 三振: '9' }; else bat[k] = { 試合: '9', 打席: String(600 - i), 打率: '.300', 本塁打: '9', 打点: '9', 出塁率: '.4', 長打率: '.5' }; });
          PST[t] = { at: Date.now(), d: { bat, pit, asof: '9/28' } }; S.ptTeam = t; S.ptAll = true;
          const ng = [];
          for (const k of ['bat', 'pit']) {
            S.ptKind = k; renderTeamIn();
            document.querySelectorAll('#ptTbl .ptile').forEach(tl => {
              const b = tl.querySelector('b'), r = document.createRange(); r.selectNodeContents(b);
              const ls = parseFloat(getComputedStyle(b).letterSpacing) || 0, tr = tl.getBoundingClientRect(), c = (tr.left + tr.right) / 2;
              for (const rc of r.getClientRects()) { const off = (rc.left + rc.right - ls) / 2 - c; if (Math.abs(off) > 1.5) { ng.push(`${b.textContent} が ${off.toFixed(1)}px ずれている`); break; } }
            });
          }
          return ng.slice(0, 5);
        }""")
        for m in r:
            bad(f"[名前の位置 パワプロ風 幅{width}] {m}")
        for e in errs:
            bad(f"[名前の位置 パワプロ風 幅{width}]: 画面のエラー {e}")
        await pg.close()


async def tabbar_check(browser):
    """下のタブバー：指でスクロールしている間は下へでも上へでも小さく、止まるといちばん上の近くでは元の大きさ"""
    pg, errs = await open_page(browser, 390, "", touch=True)
    r = await pg.evaluate("""async () => {
      const bar = document.querySelector('.tabbar'), P = () => window.__tabbarP().p, ng = [];
      setTab('magic');
      const el = document.getElementById('formBlk'), wait = ms => new Promise(r => setTimeout(r, ms));
      const f = (type, y) => { const t = new Touch({ identifier: 7, target: el, clientX: 200, clientY: y }); el.dispatchEvent(new TouchEvent(type, { touches: type === 'touchend' ? [] : [t], changedTouches: [t], bubbles: true, cancelable: true })); };
      window.scrollTo(0, 1200); await wait(900);
      f('touchstart', 300); for (let i = 1; i <= 8; i++) { f('touchmove', 300 - i * 15); window.scrollBy(0, 15); await wait(30); }
      if (P() < .99) ng.push(`下へ読み進めてもタブバーが小さくならない（${P()}）`);
      f('touchend', 180);
      // 元の大きさに戻る動き：1フレームごとの変化が小さく（カクつかない）、途中で飛ばない
      const tr = []; const t0 = performance.now();
      await new Promise(res => { const tick = () => { tr.push(P()); performance.now() - t0 < 1100 ? requestAnimationFrame(tick) : res(); }; requestAnimationFrame(tick); });
      const jumps = tr.slice(1).map((x, i) => Math.abs(x - tr[i]));
      if (Math.max(...jumps) > .2) ng.push(`元の大きさに戻るときに一気に変わるフレームがある（最大${Math.max(...jumps).toFixed(2)}）`);
      if (tr.filter((x, i) => i && x !== tr[i - 1]).length < 8) ng.push('元の大きさに戻る動きのフレームが少ない（なめらかでない）');
      if (P() > .01) ng.push(`スクロールが止まってもタブバーが元の大きさに戻らない（${P()}）`);
      const tf = getComputedStyle(bar).transform;
      if (tf !== 'none' && tf !== 'matrix(1, 0, 0, 1, 0, 0)') ng.push(`元の大きさに戻っても形が戻っていない（${tf}）`);
      f('touchstart', 300); for (let i = 1; i <= 8; i++) { f('touchmove', 300 + i * 15); window.scrollBy(0, -15); await wait(30); }
      if (P() < .99) ng.push(`上へ戻るときにタブバーが小さくならない（${P()}）`);
      f('touchend', 420);
      window.scrollTo(0, 20); f('touchstart', 300); f('touchmove', 310); window.scrollTo(0, 10); await wait(80);
      if (window.__tabbarP().target > .01) ng.push('いちばん上の近くでタブバーが元の大きさに向かわない');
      await wait(900);   // ばねで戻る動きを待つ
      if (P() > .01) ng.push(`いちばん上の近くでタブバーが元の大きさにならない（${P()}）`);
      f('touchend', 310);
      return ng;
    }""")
    for m in r:
        bad(f"[タブバー] {m}")
    for e in errs:
        bad(f"[タブバー]: 画面のエラー {e}")
    await pg.close()


async def offseason_check(browser):
    """今オフの戦力外・引退：一覧・札が出るか、はみ出さないか、押しやすいか（両テーマ×幅390/320×両リーグ）。
    あわせて、データ更新側の読み取り（球団の発表ページから選手を拾う処理）を試験用のページで確かめる"""
    for theme in ["", "pawa"]:
        for width in [390, 320]:
            for lg in ["C", "P"]:
                label = f"[戦力外・引退 {'パワプロ風' if theme else 'スタイリッシュ'} 幅{width} {lg}]"
                pg, errs = await open_page(browser, width, theme)
                if lg == "P":
                    await pg.evaluate("switchLeague('P')")
                    await pg.wait_for_timeout(200)
                r = await pg.evaluate("""() => {
                  const ng = [], items = [];
                  // 表示中のリーグの各球団から、名簿の選手を2人ずつ（長い名前の選手も入れる）
                  CL.forEach((t, i) => {
                    const ro = (DATA.rosters[t] || []).slice().sort((a, b) => b.n.length - a.n.length);
                    if (ro[0]) items.push({ t, n: ro[0].n, no: ro[0].no, dev: false, kind: 'offer', date: '2026-09-29', url: 'https://example.com/a', title: '' });
                    if (ro[1] && i % 2 === 0) items.push({ t, n: ro[1].n, no: ro[1].no, dev: false, kind: 'retire', date: '2026-09-23', url: 'https://example.com/b', title: '' });
                    if (i === 1) items.push({ t, n: '井上 一樹', no: '99', dev: false, kind: 'mgr', role: '監督', date: '2026-09-29', url: '', title: '' });
                  });
                  // 去年の「公示 任意引退・自由契約」のページから拾ったもの（出してはいけない）
                  const bogus = { t: CL[0], n: 'ニセ 公示太郎', no: '1', dev: false, kind: 'retire', date: '2026-09-29', url: 'https://www.example.jp/news/announce/retire/', title: '' };
                  const mgrRec = { '井上 一樹': { seasons: [{ y: '2025', t: CL[1], rank: '4', g: 143, w: 63, l: 78, d: 2 }, { y: '2026', t: CL[1], rank: '1', g: 141, w: 59, l: 80, d: 2 }], asof: '9/28' } };
                  DATA.offseason = { season: 2026, checked_at: '2026-09-29T15:00:00+09:00', items: items.concat([bogus]), teams: {}, seen: {}, mgr_rec: mgrRec };
                  jst = () => ({ y: 2026, m: 10, d: 1, iso: '2026-10-01' });
                  renderAll(); setTab('off');
                  const tb = document.querySelector('#subNav button[data-p="off"]'), blk = document.getElementById('v-off');
                  if (!tb || document.getElementById('subNav').hidden || blk.hidden) { ng.push('「選手」の中に入退団の切り替えが出ない'); return ng; }
                  if (getComputedStyle(document.querySelector('.tabbar nav')).gridTemplateColumns.split(' ').length !== 5) ng.push('下のタブが5つ並んでいない');
                  const tbb = [...document.querySelectorAll('.tabbar button:not([hidden])')].map(b => b.getBoundingClientRect());
                  if (tbb.some((b, i) => i && b.left < tbb[i - 1].right - 1)) ng.push('タブのボタンが重なっている');
                  if (tbb.some(b => b.right > innerWidth)) ng.push('タブバーが画面からはみ出している');
                  const rows = blk.querySelectorAll('.ofr');
                  if (blk.textContent.includes('ニセ 公示太郎')) ng.push('去年の公示の一覧から拾ったものが出ている');
                  if (blk.querySelector('#offList a')) ng.push('一覧に「発表」などのリンクが残っている');
                  if (rows.length !== items.length) ng.push(`一覧の人数が違う（${rows.length}／${items.length}）`);
                  // 監督の退任：球団のいちばん上に出て、名前は押せない（成績がないため）
                  const mrow = [...rows].find(r => r.textContent.includes('井上 一樹'));
                  if (!mrow) ng.push('監督の退任が出ない');
                  else {
                    if (mrow.querySelector('[data-pl]')) ng.push('監督の名前が選手の成績画面につながっている');
                    if (mrow.previousElementSibling && mrow.previousElementSibling.classList.contains('ofr')) ng.push('監督の退任が球団のいちばん上にない');
                    // パワプロ風：監督の名前は現役時代のポジション（井上一樹＝外野手）の色のタイル
                    if (isPawa() && !mrow.querySelector('.onm .ptile.po')) ng.push('パワプロ風で、監督の名前が現役時代のポジションの色のタイルになっていない');
                    // 名前をタップすると、監督としての通算成績
                    mrow.querySelector('[data-mgr]').click();
                    const sp = document.getElementById('songPick'), txt = sp.textContent;
                    if (document.getElementById('songSheet').hidden || !txt.includes('監督としての通算成績（2年）')) ng.push('監督の名前をタップしても通算成績が出ない');
                    else {
                      if (!/284/.test(txt) || !/122/.test(txt) || !/158/.test(txt) || !/[.]436/.test(txt)) ng.push('監督の通算成績の数字が違う（284試合122勝158敗.436のはず）');
                      if (!txt.includes('1回')) ng.push('リーグ優勝の回数が違う');
                      if (sp.scrollWidth > sp.clientWidth + 1) ng.push('監督の成績画面が横にはみ出している');
                      // 年度ごとの表：文字がマスからはみ出さない（年度の4桁が隣の列に食い込まない）
                      const over = [...sp.querySelectorAll('.mgtab td, .mgtab th')].filter(c => { const r = document.createRange(); r.selectNodeContents(c); const rr = r.getBoundingClientRect(), cr = c.getBoundingClientRect(); return rr.width && (rr.left < cr.left - 0.5 || rr.right > cr.right + 0.5); }).map(c => c.textContent.trim());
                      if (over.length) ng.push(`監督の年度ごとの表で文字がマスからはみ出している：${over.slice(0, 3).join('、')}`);
                    }
                    document.getElementById('songSheet').hidden = true; document.getElementById('songSheet').classList.remove('open');
                    // シーズン途中で辞任した監督：その年は辞任した日より前の試合だけで数える
                    const tm = CL[2], cut = (DATA.games.filter(g => g.st === 'final').map(g => g.d).sort()[40] || '2026-05-01');
                    DATA.offseason.items.push({ t: tm, n: 'テスト 途中', kind: 'mgr', mid: true, date: cut });
                    DATA.offseason.mgr_rec['テスト 途中'] = { seasons: [{ y: '2025', t: tm, rank: '3', g: 143, w: 70, l: 69, d: 4 }], asof: '9/28' };
                    openManager(tm, 'テスト 途中');
                    const gs = DATA.games.filter(g => (g.h === tm || g.a === tm) && g.st === 'final' && g.d < cut);
                    const w = gs.filter(g => (g.h === tm ? g.hs > g.as : g.as > g.hs)).length, l = gs.filter(g => (g.h === tm ? g.hs < g.as : g.as < g.hs)).length;
                    const t2 = document.getElementById('songPick').innerText;
                    if (gs.length && (!t2.includes(`${w}-${l}-${gs.length - w - l}`) || !t2.includes('シーズン途中で辞任') || !t2.includes('途中'))) ng.push(`途中で辞任した監督のその年の成績が、辞任前の試合だけになっていない（${w}-${l}）`);
                    document.getElementById('songSheet').hidden = true; document.getElementById('songSheet').classList.remove('open');
                    DATA.offseason.items.pop(); delete DATA.offseason.mgr_rec['テスト 途中'];
                  }
                  // オフの動き：移籍・FA宣言・加入・ドラフトの並びと札
                  const t0 = CL[0], ro0 = DATA.rosters[t0] || [];
                  DATA.offseason.items.push({ t: t0, n: ro0[3].n, no: ro0[3].no, kind: 'out', via: 'trade', to: CL[1], date: '2026-11-10' },
                    { t: t0, n: ro0[4].n, no: ro0[4].no, kind: 'fa_decl', date: '2026-11-05' },
                    { t: t0, n: 'テスト 新人', kind: 'draft', round: '1位', pos: '投手', from: 'テスト大', date: '2026-10-22' },
                    { t: t0, n: 'ジョン・テスト', kind: 'in', via: 'newfor', pos: '外野手', date: '2026-12-01' });
                  renderOff();
                  const blk2 = document.getElementById('offList'), card = [...blk2.querySelectorAll('.ofteam')].find(c => c.textContent.includes(fn(t0)));
                  const secs = card ? [...card.querySelectorAll('.ofsec')].map(x => x.textContent) : [];
                  if (!secs.some(x => x === '退団') || !secs.some(x => x.startsWith('FA宣言')) || !secs.some(x => x === '入団')) ng.push(`オフの動きの「退団／FA宣言／入団」の分け方が出ない（${secs}）`);
                  if (card && [...card.querySelectorAll('.ofr')].some(r => /テスト 新人|ジョン・テスト/.test(r.textContent) && r.querySelector('[data-pl]'))) ng.push('名簿にいない加入選手の名前が押せてしまう');
                  if (card && !card.textContent.includes('ドラフト1位指名（テスト大）')) ng.push('ドラフトの内容の書き方が違う');
                  if (card && !card.textContent.includes(fn(CL[1]) + 'へトレードで移籍')) ng.push('トレードで出ていく書き方が違う');
                  // 種類のボタン：ドラフトを押すとドラフトだけ、人数の説明は出さない
                  const bt = document.querySelector('#offCats button[data-oc="draft"]');
                  if (!bt) ng.push('種類の切り替えボタン（ドラフト）が出ない');
                  else { bt.click(); const rows2 = [...document.querySelectorAll('#offList .ofr')]; if (!rows2.length || rows2.some(r => !r.querySelector('.offtag.k-draft'))) ng.push('ドラフトで絞り込んでも、ほかの種類が混ざる'); document.querySelector('#offCats button[data-oc="all"]').click(); }
                  if (/\\d+人/.test(document.getElementById('offAsof').textContent + [...document.querySelectorAll('#offList .ofh')].map(h => h.textContent).join(''))) ng.push('人数の説明が残っている');
                  // 首脳陣：コーチの退団・就任・配置転換。「首脳陣」のボタンで監督とコーチだけ
                  DATA.offseason.items.push({ t: t0, n: 'テスト 退団', kind: 'coach_out', role: 'ヘッドコーチ', no: '77', date: '2026-10-08' },
                    { t: t0, n: 'テスト 就任', kind: 'coach_in', role: '投手コーチ', date: '2026-11-02' },
                    { t: t0, n: 'テスト 異動', kind: 'coach_move', role: '二軍監督', date: '2026-10-08' });
                  renderOff();
                  const card3 = [...document.querySelectorAll('#offList .ofteam')].find(c => c.textContent.includes(fn(t0)));
                  const txt3 = card3 ? card3.textContent : '';
                  if (!txt3.includes('ヘッドコーチ・今季限りで退団') || !txt3.includes('投手コーチに就任') || !txt3.includes('首脳陣 配置転換')) ng.push('コーチの退団・就任・配置転換の書き方が違う');
                  if (card3 && [...card3.querySelectorAll('.ofr')].some(r => /テスト 退団|テスト 就任|テスト 異動/.test(r.textContent) && r.querySelector('[data-pl]'))) ng.push('コーチの名前が選手の成績画面につながっている');
                  const sb = document.querySelector('#offCats button[data-oc="staff"]');
                  if (!sb || sb.textContent !== '首脳陣') ng.push('「首脳陣」のボタンが出ない');
                  else { sb.click(); const r3 = [...document.querySelectorAll('#offList .ofr')]; if (r3.some(r => !/k-(mgr|coach_)/.test(r.querySelector('.offtag').className))) ng.push('首脳陣で絞り込んでも選手が混ざる'); document.querySelector('#offCats button[data-oc="all"]').click(); }
                  if (document.querySelector('#offCats button[data-oc="mgr"]')) ng.push('「監督」のボタンが残っている');
                  DATA.offseason.items.splice(-3, 3);
                  DATA.offseason.items.splice(-4, 4); renderOff();
                  // 今季の一軍の登板がない投手は、データ更新で調べた過去の役割（先発）の色になるか
                  const pr = (DATA.rosters[CL[0]] || []).find(x => x.p === '投手' && !DATA.offseason.items.some(y => y.t === CL[0] && y.n === x.n) && posGroups(CL[0], x.n, '投手').join() === '投');
                  if (pr && isPawa()) {
                    DATA.offseason.items.push({ t: CL[0], n: pr.n, no: pr.no, dev: false, kind: 'cut', role: '先', date: '2026-09-29', url: '', title: '' });
                    renderOff();
                    const row = [...document.querySelectorAll('#offList .ofr')].find(r => r.textContent.includes(pr.n));
                    if (!row || !row.querySelector('.onm .ptile.ps')) ng.push(`今季の一軍の登板がない投手（${pr.n}）が、過去の役割（先発）の色になっていない`);
                    DATA.offseason.items.pop(); renderOff();
                  }
                  // パワプロ風は、名前が守備位置の色のタイルになっているか
                  if (isPawa() && [...rows].some(r => !r.querySelector('.onm .ptile'))) ng.push('パワプロ風なのに、名前がタイルになっていない');
                  if (items.some(x => x.kind === 'mgr' && offOf(x.t, x.n))) ng.push('監督に選手の札が付く');
                  const W = blk.getBoundingClientRect().right + 1;
                  blk.querySelectorAll('.ofr, .ofr *').forEach(e => { const b = e.getBoundingClientRect(); if (b.width && b.right > W) ng.push(`一覧が横にはみ出し：${e.className}`); });
                  blk.querySelectorAll('.oln, .onm').forEach(e => { const b = e.getBoundingClientRect(); if (e.matches('.oln') && (b.height < 43.5 || b.width < 43.5)) ng.push(`「発表」が小さい ${Math.round(b.width)}×${Math.round(b.height)}`); });
                  // 札：チーム内の成績・応援歌・選手の成績画面
                  const it = items[0], k = it.n.replace(/\\s+/g, '');
                  PST[it.t] = { at: Date.now(), d: { bat: { [k]: { 試合: '1', 打席: '9', 打率: '.1', 本塁打: '0', 打点: '0', 出塁率: '.1', 長打率: '.1' } }, pit: { [k]: { 登板: '1', 投球回: '1', 防御率: '1.00', 勝利: '0', 敗北: '0', 三振: '1' } }, asof: '9/28' } };
                  // 打者の表は打者だけ・投手の表は投手だけなので、その選手の守備位置の側の表で見る
                  S.ptKind = (((DATA.rosters[it.t] || []).find(r => r.n === it.n) || {}).p === '投手') ? 'pit' : 'bat';
                  DATA.tstats = null; DATA.tstats_at = null;   // この試験は中継プログラム（NPB）の表で見る
                  setTab('stats'); S.ptTeam = it.t; renderTeamIn();
                  if (!document.querySelector('#ptTbl .offtag')) ng.push('チーム別成績に札が出ない');
                  S.ptKind = 'bat';
                  if (document.getElementById('offList').closest('#v-stats')) ng.push('一覧が成績タブに残っている');
                  const over = [...document.querySelectorAll('#ptTbl td, #ptTbl th')].filter(c => c.scrollWidth > c.clientWidth + 1).length;
                  if (over) ng.push(`札を付けたチーム内の成績で、文字がマスからはみ出したセルが ${over} 個`);
                  return ng;
                }""")
                for m in r[:6]:
                    bad(f"{label} {m}")
                # オフでない時期（発表もない）はタブが消えて6つに戻り、戦力外のタブを見ていたら戦況に戻る
                r2 = await pg.evaluate("""() => {
                  const keep = DATA.offseason, keepJst = jst;
                  setTab('off'); DATA.offseason = null; jst = () => ({ y: 2026, m: 6, d: 1, iso: '2026-06-01' }); renderAll();
                  const ng = [];
                  if (document.querySelector('#subNav button[data-p="off"]') || !document.getElementById('subNav').hidden) ng.push('オフでないのに入退団の切り替えが出ている');
                  if (getComputedStyle(document.querySelector('.tabbar nav')).gridTemplateColumns.split(' ').length !== 5) ng.push('下のタブが5つのままでない');
                  if (S.tab !== 'song') ng.push('入退団が消えたのに、その画面のまま（応援歌に戻るはず）');
                  if (tabOrder().includes('off')) ng.push('消えたタブにスワイプで行ける');
                  DATA.offseason = keep; jst = keepJst; renderAll();   // 元に戻す
                  return ng;
                }""")
                for m in r2:
                    bad(f"{label} {m}")
                # 選手の成績画面の札
                ok = await pg.evaluate("""async () => { const it = DATA.offseason.items[0]; await openPlayer(it.t, it.n); return !!document.querySelector('#songPick .sp-h .offtag'); }""")
                if not ok:
                    bad(f"{label} 選手の成績画面に札が出ない")
                for e in errs:
                    bad(f"{label}: 画面のエラー {e}")
                await pg.close()
    # データ更新側：発表ページの読み取り（requests・BeautifulSoup が入っている環境だけ）
    try:
        import sys as _s
        _s.path.insert(0, str(ROOT / "scripts"))
        import update_data as ud
    except ImportError:
        print("  （データ更新側の読み取りの試験は、requests・BeautifulSoup がないため省略）")
        return
    roster = [{"n": "酒居 知史", "no": "28"}, {"n": "林 優樹", "no": "64"}, {"n": "今野 龍太", "no": "66"}, {"n": "伊藤 樹", "no": "20"}, {"n": "辛島 航", "no": "58"}, {"n": "松田 啄磨", "no": "061", "dev": True}]
    lst = '<ul class="news-list"><li><a href="/news/1.html">2026/09/28 来季の選手契約について</a></li><li><a href="/news/2.html">辛島 航選手 現役引退に関して</a></li><li><a href="/news/3.html">伊藤 樹選手がプロ初勝利</a></li><li><a href="/news/4.html">契約更改について</a></li></ul>'
    art = ('<header>伊藤 樹選手</header><article><h1>来季の選手契約について</h1><p>2026/09/28</p><p>以下の選手と2027シーズンの選手契約を行わないことを通知しました。</p>'
           '<p>投手 酒居 知史<br>投手 林 優樹<br>投手 今野 龍太<br>【育成】投手 松田 啄磨</p><p>なお、酒居 知史投手、林 優樹投手には育成選手契約を打診しております。</p></article>'
           '<aside class="side">伊藤 樹選手がプロ初勝利</aside>')
    links = [t for _, t in ud.off_links("https://www.example.jp/news/", lst)]
    if len(links) != 2 or not any("契約" in t for t in links) or not any("引退" in t for t in links):
        bad(f"[戦力外・引退の読み取り] ニュース一覧から発表を正しく選べない：{links}")
    got, _ = ud.off_article("E", "u", "来季の選手契約について", art, roster, 2026)
    want = {"酒居 知史": "offer", "林 優樹": "offer", "今野 龍太": "cut", "松田 啄磨": "cut"}
    if {x["n"]: x["kind"] for x in got} != want:
        bad(f"[戦力外・引退の読み取り] 発表から選手を正しく拾えない：{[(x['n'], x['kind']) for x in got]}")
    old, why = ud.off_article("E", "u", "来季の選手契約について", "<h1>来季の選手契約について</h1><p>2025/10/05</p><p>酒居 知史投手と来季の契約を結ばない</p>", roster, 2026)
    if old:
        bad("[戦力外・引退の読み取り] 去年の発表を今年のものとして拾っている")
    # オフの動き：トレード（出る・入る）・FA宣言・新外国人・ドラフト
    R2 = {"T": [{"n": "阪神 太郎", "no": "1", "p": "投手"}, {"n": "阪神 次郎", "no": "2", "p": "内野手"}], "G": [{"n": "巨人 三郎", "no": "3", "p": "外野手"}]}
    got = ud.off_moves("T", "u", "トレードのお知らせ", "<h1>トレードのお知らせ</h1><p>2026/11/10</p><p>阪神 太郎選手と巨人 三郎選手の交換トレードが成立</p>", R2, 2026)
    want = {("T", "巨人 三郎", "in"), ("G", "巨人 三郎", "out"), ("T", "阪神 太郎", "out"), ("G", "阪神 太郎", "in")}
    if {(x["t"], x["n"], x["kind"]) for x in got} != want:
        bad(f"[オフの動きの読み取り] トレードを正しく読めない：{[(x['t'], x['n'], x['kind']) for x in got]}")
    got = ud.off_moves("T", "u", "阪神 次郎選手 FA権行使について", "<h1>阪神 次郎選手 FA権行使について</h1><p>2026/11/05</p><p>阪神 次郎選手がFA権を行使</p>", R2, 2026)
    if [(x["n"], x["kind"]) for x in got] != [("阪神 次郎", "fa_decl")]:
        bad(f"[オフの動きの読み取り] FA宣言を正しく読めない：{[(x['n'], x['kind']) for x in got]}")
    got = ud.off_moves("T", "u", "新外国人選手 ジョン・スミス投手 契約合意のお知らせ", "<h1>新外国人選手 ジョン・スミス投手 契約合意のお知らせ</h1><p>2026/12/10</p>", R2, 2026)
    if [(x["n"], x["kind"], x.get("via"), x.get("pos")) for x in got] != [("ジョン・スミス", "in", "newfor", "投手")]:
        bad(f"[オフの動きの読み取り] 新外国人を正しく読めない：{got}")
    # トレード（NPB公式の公示）：シーズン中のトレードも。前のオフ（1月）の分は入れない。「FANCLUB」は FA ではない
    tr = ud.parse_trades('<table><tr><td>2026/5/13</td><td>山本 祐大</td><td>捕 手</td><td>50</td><td>横浜DeNA</td><td>→</td><td>39</td><td>福岡ソフトバンク</td></tr><tr><td>2026/5/13</td><td>尾形 崇斗</td><td>投 手</td><td>39</td><td>福岡ソフトバンク</td><td>→</td><td>36</td><td>横浜DeNA</td></tr><tr><td>2026/1/30</td><td>田中 千晴</td><td>投 手</td><td>48</td><td>読売</td><td>→</td><td>29</td><td>東北楽天</td></tr></table>', 2026)
    if [(x["n"], x["from"], x["to"], x["d"]) for x in tr] != [("山本 祐大", "DB", "H", "2026-05-13"), ("尾形 崇斗", "H", "DB", "2026-05-13")]:
        bad(f"[オフの動きの読み取り] NPBのトレードの公示を正しく読めない：{tr}")
    # コーチ・監督の退団（ニュースの新着から）：球団発表の記事だけ・今の首脳陣の名前・名前のすぐ後ろに退団があるコーチだけ。
    # 同じ記事に出てくるほかのコーチ（後任など）・観測記事（「〜か」）は入れない
    feed = ('<rss><channel>'
            '<item><title><![CDATA[オリックス、コーチ2人が退団 球団発表]]></title><link>https://example.com/a</link></item>'
            '<item><title>巨人・○○コーチ退団か</title><link>https://example.com/b</link></item>'
            '<item><title>阪神が優勝</title><link>https://example.com/c</link></item></channel></rss>')
    art = {"https://example.com/a": '<article><p>2026.10.04</p><p>オリックスは4日、波留敏夫ヘッドコーチ、川島慶三打撃コーチの退団を発表した。波留コーチは契約満了。後任には福川将和コーチの昇格が有力。</p></article>',
           "https://example.com/b": '<article><p>2026.10.04</p><p>巨人の杉内俊哉投手チーフコーチが退団する見通しとなった。</p></article>'}
    keep_fetch, keep_feeds = ud.fetch, ud.STAFF_NEWS_FEEDS
    try:
        ud.STAFF_NEWS_FEEDS = ["https://example.com/feed"]
        ud.fetch = lambda u: feed if u.endswith("/feed") else art.get(u)
        staff_t = {"B": [{"role": "ヘッドコーチ", "no": "81", "n": "波留 敏夫"}, {"role": "打撃コーチ", "no": "82", "n": "川島 慶三"}, {"role": "打撃コーチ", "no": "79", "n": "福川 将和"}],
                   "G": [{"role": "投手チーフコーチ", "no": "81", "n": "杉内 俊哉"}]}
        got = ud.staff_news(staff_t, {"B": {"n": "岸田 護", "no": "71"}}, 2026, {}, "2026-10-04")
        names = sorted((x["t"], x["n"], x["kind"]) for x in got)
        if names != [("B", "川島 慶三", "coach_out"), ("B", "波留 敏夫", "coach_out")]:
            bad(f"[首脳陣の退団（ニュース）] 拾い方が違う：{names}")
    finally:
        ud.fetch, ud.STAFF_NEWS_FEEDS = keep_fetch, keep_feeds
    # ベースボールチャンネルの一覧の名前の欄の札（「NEW」「育成」）は名前ではない
    for raw, want in [("松原快 育成", "松原快"), ("髙野光海 NEW 育成", "髙野光海"), ("S・コンスエグラ 育成", "S・コンスエグラ"), ("新井 貴浩", "新井 貴浩"), ("奥村光一 ※", "奥村光一")]:
        if ud.clean_off_name(raw) != want:
            bad(f"[入退団の名前] 「{raw}」が「{ud.clean_off_name(raw)}」になる（{want} のはず）")
    bb = ud.parse_bbc('<h2>戦力外通告</h2><table><tr><td>9月29日</td><td>阪神</td><td>松原快 <span>育成</span></td><td>投手</td></tr><tr><td>10月4日</td><td>ロッテ</td><td>髙野光海 <b>NEW</b> <span>育成</span></td><td>外野手</td></tr></table>', 2026)
    if [(x["n"], x["dev"]) for x in bb] != [("松原快", True), ("髙野光海", True)]:
        bad(f"[入退団の名前] ベースボールチャンネルの名前の札を外せない：{[(x['n'], x['dev']) for x in bb]}")
    # 歴代記録の1ページ（NPBの歴代最高記録）：見出しの空の列（現役の印「*」）を外して行ごとの印に。注記の行は読まない
    rp = ud.parse_record_page('<p>■ 2026年10月1日(木) 現在</p><table><tr><th>順位</th><th></th><th>選手</th><th>本塁打</th><th>実働期間</th></tr>'
                              '<tr><td>1</td><td></td><td>王 貞治</td><td>868</td><td>(1959-1980)</td></tr><tr><td>10</td><td>*</td><td>中村 剛也</td><td>482</td><td>(2003-2026)</td></tr>'
                              '<tr><td colspan="5">( * 2026シーズンの現役選手 )</td></tr></table>')
    if not rp or rp["cols"] != ["順位", "選手", "本塁打", "実働期間"] or len(rp["rows"]) != 2 or rp.get("act") != [0, 1] or "2026年10月1日" not in rp["asof"]:
        bad(f"[歴代記録の読み取り] NPBの歴代最高記録のページを正しく読めない：{rp}")
    # 育成から支配下登録（NPB公式の公示「新規支配下選手登録」）：育成から移行した選手だけ。新外国人・去年の分は取らない
    rg = ud.parse_registered('<table><tr><td>2026/7/31</td><td><a href="https://npb.jp/announcement/2026/registered_l.html">埼玉西武ライオンズ</a></td><td><a href="x">是澤 涼輔</a></td><td>捕 手</td><td>122 → 65</td><td>（育成選手から移行）</td></tr>'
                             '<tr><td>2026/7/30</td><td><a href="https://npb.jp/announcement/2026/registered_h.html">福岡ソフトバンクホークス</a></td><td>Ｌ．ロドリゲス</td><td>投 手</td><td>156 → 85</td><td>（育成選手から移行）</td></tr>'
                             '<tr><td>2026/7/30</td><td><a href="https://npb.jp/announcement/2026/registered_h.html">福岡ソフトバンクホークス</a></td><td>Ｊ．ラトリッジ</td><td>投 手</td><td>14</td><td></td></tr>'
                             '<tr><td>2025/7/31</td><td><a href="https://npb.jp/announcement/2025/registered_t.html">阪神タイガース</a></td><td>去年 太郎</td><td>投 手</td><td>120 → 90</td><td>（育成選手から移行）</td></tr></table>', 2026)
    if [(x["t"], x["n"], x["no_dev"], x["no"], x["d"]) for x in rg] != [("L", "是澤 涼輔", "122", "65", "2026-07-31"), ("H", "L.ロドリゲス", "156", "85", "2026-07-30")]:
        bad(f"[入退団の読み取り] NPBの新規支配下選手登録の公示を正しく読めない：{rg}")
    if ud.OFF_TITLE_MOVE.search("FANCLUB 2026/9/25 あなたの推し") or not ud.OFF_TITLE_MOVE_NG.search("新入団選手情報"):
        bad("[オフの動きの読み取り] ファンクラブの記事や新入団選手の一覧を、移籍の発表として読んでしまう")
    # 首脳陣：NPBの監督・コーチ一覧と、コーチの退団・配置転換・留任・新任の読み取り
    st = ud.parse_staff('<h5>一覧</h5><table><tr><th>位置</th><th>番号</th><th>氏名</th></tr><tr><td>監督</td><td>99</td><td>井上 一樹</td></tr><tr><td>ヘッドコーチ</td><td>77</td><td>嶋 基宏</td></tr><tr><td>投手コーチ</td><td>83</td><td>山井 大介</td></tr><tr><td>二軍監督</td><td>74</td><td>飯山 裕志</td></tr></table><h5>監督・コーチ登録公示以降の動き</h5><table><tr><td>◎</td><td>2026/3/25</td><td>内野守備走塁コーチ</td><td>森越 祐人</td></tr></table>')
    if [x["n"] for x in st] != ["井上 一樹", "嶋 基宏", "山井 大介", "飯山 裕志"]:
        bad(f"[オフの動きの読み取り] NPBの監督・コーチ一覧を正しく読めない：{st}")
    got = ud.off_coach("D", "u", "コーチングスタッフについて", "<h1>コーチングスタッフについて</h1><p>2026/10/08</p><p>嶋基宏ヘッドコーチが今季限りで退団することになりました。</p><p>また飯山裕志二軍監督は来季、一軍内野守備走塁コーチへ配置転換となります。</p><p>投手コーチ　山井 大介（留任）</p><p>新任 打撃コーチ　立浪 和義</p>", st, set(), 2026)
    if sorted((x["n"], x["kind"]) for x in got) != sorted([("嶋 基宏", "coach_out"), ("飯山 裕志", "coach_move"), ("立浪 和義", "coach_in")]):
        bad(f"[オフの動きの読み取り] コーチの退団・配置転換・就任を正しく読めない：{[(x['n'], x['kind']) for x in got]}")
    # スポナビの入退団情報：今オフの退団（戦力外・引退・育成再契約の打診）だけ。移籍・入団・去年の分は取らない
    tr_html = """<section id="5"><h3>阪神</h3><table><tr><th>更新日</th><th>状況</th><th>選手名</th><th>守備</th><th>備考</th></tr>
      <tr><td>2026/9/29</td><td>退団</td><td><a>松原 快</a></td><td>投手</td><td>自由契約</td></tr>
      <tr><td>2026/9/28</td><td>退団</td><td>岩貞 祐太</td><td>投手</td><td>引退</td></tr>
      <tr><td>2026/7/21</td><td>入団</td><td>ガルシア</td><td>外野手</td><td>支配下契約(新外国人)</td></tr>
      <tr><td>2025/10/1</td><td>退団</td><td>佐藤 蓮</td><td>投手</td><td>自由契約</td></tr></table></section>
      <section id="1"><h3>巨人</h3><table><tr><td>2026/9/30</td><td>退団</td><td>板東 湧梧  ※</td><td>投手</td><td>自由契約</td></tr>
      <tr><td>2026/9/30</td><td>退団</td><td>石田 隼都  ※</td><td>投手</td><td>自由契約→引退</td></tr>
      <tr><td>2026/7/29</td><td>退団</td><td>若林 楽人</td><td>外野手</td><td>金銭トレード(西武）</td></tr></table></section>
      <section id="3"><h3>DeNA</h3><table><tr><td>2026/9/30</td><td>退団</td><td>大貫 晋一</td><td>投手</td><td>自由契約→育成再契約を打診</td></tr></table></section>"""
    got = [(x["t"], x["n"], x["kind"], x["date"], x["dev"]) for x in ud.parse_transfer(tr_html, 2026)]
    want = [("T", "松原 快", "cut", "2026-09-29", False), ("T", "岩貞 祐太", "retire", "2026-09-28", False),
            ("G", "板東 湧梧", "cut", "2026-09-30", True), ("G", "石田 隼都", "retire", "2026-09-30", True), ("DB", "大貫 晋一", "offer", "2026-09-30", False)]
    if got != want:
        bad(f"[入退団情報] 読み取りが違う：{got}")
    _f = ud.fetch
    try:
        ud.fetch = lambda url: tr_html
        from datetime import datetime as _dt
        o = ud.merge_transfer({"season": 2026, "items": [{"t": "T", "n": "岩貞 祐太", "kind": "retire", "date": "2026-09-28", "url": "x"}]},
                              2026, {"T": [{"n": "松原 快", "no": "46", "dev": False}]}, _dt(2026, 9, 30, 14, 0, tzinfo=ud.JST))
        names = sorted((x["t"], x["n"]) for x in o["items"])
        if names != sorted([("T", "岩貞 祐太"), ("T", "松原 快"), ("G", "板東 湧梧"), ("G", "石田 隼都"), ("DB", "大貫 晋一")]):
            bad(f"[入退団情報] 足し方が違う：{names}")
        if next(x for x in o["items"] if x["n"] == "岩貞 祐太").get("url") != "x":
            bad("[入退団情報] 球団の発表から見つけていた選手を上書きしている")
    finally:
        ud.fetch = _f
    # 応援歌：移籍して出ていった選手は元の球団の応援歌なし。移籍してきた選手は背番号だけでは判定しない
    _bk = (ud.SONG_SOURCES, ud.SONG_BY_NUMBER, ud.SONG_LINK_PAGE, ud.SONG_EXTRA, ud.fetch)
    try:
        ud.SONG_SOURCES, ud.SONG_BY_NUMBER, ud.SONG_LINK_PAGE, ud.SONG_EXTRA = {"DB": ["x"]}, {"x"}, {}, {}
        ud.fetch = lambda url: "<p>" + ("背番号40 前の選手 かっとばせー " * 20) + "背番号50 山本祐大 背番号2 牧秀悟</p>"
        rows = [{"n": "井上 朋也", "no": "40", "dev": False}, {"n": "山本 祐大", "no": "50", "dev": False}, {"n": "牧 秀悟", "no": "2", "dev": False}]
        ud.mark_songs("DB", rows, {ud.squash("井上 朋也")}, {ud.squash("山本 祐大")})
        if [(r["n"], r["song"]) for r in rows] != [("井上 朋也", False), ("山本 祐大", False), ("牧 秀悟", True)]:
            bad(f"[応援歌の判定] 移籍した選手の扱いが違う：{[(r['n'], r['song']) for r in rows]}")
    finally:
        ud.SONG_SOURCES, ud.SONG_BY_NUMBER, ud.SONG_LINK_PAGE, ud.SONG_EXTRA, ud.fetch = _bk
    # ベースボールチャンネルの戦力外・引退の一覧：見出しごとに種類（戦力外・引退・退団）。移籍の表と、今オフより前は取らない
    bb = ud.parse_bbc("""<h2>戦力外通告</h2><table><tr><th>日付</th><th>球団</th><th>選手</th><th>ポジション</th></tr><tr><td>10月1日</td><td>広島</td><td>小園海斗</td><td>内野手</td></tr>
      <tr><td>9月30日</td><td>巨人</td><td>板東湧梧※</td><td>投手</td></tr></table><h2>引退表明</h2><table><tr><td>9月28日</td><td>阪神</td><td>岩貞祐太</td><td>投手</td></tr>
      <tr><td>8月15日</td><td>中日</td><td>中田翔</td><td>内野手</td></tr></table><h2>退団</h2><table><tr><td>9月30日</td><td>ロッテ</td><td>中村奨吾</td><td>内野手</td></tr></table>
      <h2>移籍</h2><table><tr><td>10月1日</td><td>西武</td><td>誰か</td><td>投手</td></tr></table>""", 2026)
    if [(x["t"], x["n"], x["kind"], x["date"], x["dev"]) for x in bb] != [("C", "小園海斗", "cut", "2026-10-01", False), ("G", "板東湧梧", "cut", "2026-09-30", True), ("T", "岩貞祐太", "retire", "2026-09-28", False), ("M", "中村奨吾", "leave", "2026-09-30", False)]:
        bad(f"[戦力外の一覧（ベースボールチャンネル）] 読み取りが違う：{bb}")
    # 応援歌（DeNA）：公式ページの「選手ごとの見出し」の選手だけ。「選手の呼び方」の表やテーマ曲（汎用）の選手は入れない。
    # まとめサイトは応援歌の表（背番号｜名前）の選手だけ（本文の「〇〇選手の応援歌を流用」などは使わない）
    _bk2 = (ud.SONG_SOURCES, ud.fetch)
    try:
        ud.SONG_SOURCES = {"DB": ["https://sp.baystars.co.jp/player_songs/index", "https://www.yakyu-ouen.net/baystars/"]}
        off_html = ("<h1>選手応援歌</h1><h2>投手</h2><h3>投手のテーマ（右投手）</h3><h3>外国人投手のテーマ</h3>"
                    "<h4>選手の呼び方（苗字以外の場合）</h4><table><tr><td>#11 東 克樹 →アズマ</td><td>#19 山﨑 康晃 →ヤスアキ</td></tr></table>"
                    "<h2>野手</h2><h3>林 琢真</h3><p>" + "シャープに打ち返し " * 30 + "</p><h3>牧 秀悟</h3><h3>度会 隆輝</h3><h3>松尾 汐恩</h3>"
                    "<h3>宮﨑 敏郎</h3><h3>J.エンカーナシオン</h3><h2>監督・コーチ</h2><h3>代打のテーマ</h3><h3>捕手のテーマ</h3><h3>その他の右打者</h3>"
                    "<h4>選手の呼び方（苗字以外の場合）</h4><table><tr><td>#57 東妻 純平 →ジュンペイ</td><td>#40 井上 朋也 →トモヤ</td></tr></table>")
        ouen_html = ("<p>" + "石上泰輝選手にシュワーズ選手の応援歌が流用 東妻純平 " * 20 + "</p><table><tr><td>2</td><td>牧秀悟</td></tr><tr><td>4</td><td>度会隆輝</td></tr>"
                     "<tr><td>5</td><td>松尾汐恩</td></tr><tr><td>6</td><td>森敬斗</td></tr><tr><td>7</td><td>佐野恵太</td></tr><tr><td>57</td><td>東妻純平</td></tr></table>")
        names = ["林 琢真", "牧 秀悟", "度会 隆輝", "宮﨑 敏郎", "エンカーナシオン", "東 克樹", "山﨑 康晃", "東妻 純平", "井上 朋也", "石上 泰輝", "森 敬斗"]
        ud.fetch = lambda url: off_html if "baystars.co.jp" in url else ouen_html
        rows = [{"n": n, "no": "0", "dev": False} for n in names]
        ud.mark_songs("DB", rows)
        got = [r["n"] for r in rows if r["song"]]
        if got != ["林 琢真", "牧 秀悟", "度会 隆輝", "宮﨑 敏郎", "エンカーナシオン"]:
            bad(f"[応援歌の判定 DeNA] 公式の見出しの選手だけにならない：{got}")
        ud.fetch = lambda url: None if "baystars.co.jp" in url else ouen_html
        rows = [{"n": n, "no": "0", "dev": False} for n in names]
        ud.mark_songs("DB", rows)
        got = [r["n"] for r in rows if r["song"]]
        if got != ["牧 秀悟", "度会 隆輝", "東妻 純平", "森 敬斗"]:
            bad(f"[応援歌の判定 DeNA] 公式が読めないとき、まとめサイトの表の選手だけにならない：{got}")
    finally:
        ud.SONG_SOURCES, ud.fetch = _bk2
    dr = ud.parse_draft("<h3>阪神タイガース</h3><table><tr><td>1位</td><td>立石 正広</td><td>内野手</td><td>創価大</td></tr><tr><td>育成1位</td><td>山田 太郎</td><td>投手</td><td>○○高</td></tr></table><h3>読売ジャイアンツ</h3><table><tr><td>1位</td><td>竹丸 和幸</td><td>投手</td><td>鷺宮製作所</td></tr></table>")
    if [(x["t"], x["n"], x["round"]) for x in dr] != [("T", "立石 正広", "1位"), ("T", "山田 太郎", "育成1位"), ("G", "竹丸 和幸", "1位")]:
        bad(f"[オフの動きの読み取り] ドラフトの指名選手を正しく読めない：{dr}")
    # 監督の通算成績：NPBの年度別成績（表の行でも箇条書きの行でも）から、年度・監督・順位・勝敗を読む
    rows, asof = ud.parse_yearly('<ul><li>2026年9月28日(月) 現在</li><li>1936※ 池田 豊 16 7 9 0 .438 .237 9 5.72</li><li>1939 根本・小西 6 96 38 53 5 .418 27.5</li><li>2025 井上 一樹 4 143 63 78 2 .447 23.0 .232 83 2.97</li></ul>')
    got = [(r["y"], r["m"], r["rank"], r["g"], r["w"], r["l"], r["d"]) for r in rows]
    if asof != "9/28" or ("2025", "井上 一樹", "4", 143, 63, 78, 2) not in got or ("1936", "池田 豊", "", 16, 7, 9, 0) not in got:
        bad(f"[監督の通算成績の読み取り] NPBの年度別成績を正しく読めない：{asof} {got}")
    if ud.mgr_key("髙木 守道") != ud.mgr_key("高木守道"):
        bad("[監督の通算成績の読み取り] 監督の名前の字体（髙・高）をそろえられない")
    # 監督：NPBの選手一覧の「監督」の欄から名前を取り、辞任の発表で退任として拾う（二軍監督の話・選手の発表の中の監督のコメントは拾わない）
    mgr = ud.parse_manager('<table><tr><th>No.</th><th>監督</th></tr><tr><td>99</td><td>井上 一樹</td></tr><tr><th>No.</th><th>投手</th></tr><tr><td>11</td><td>中西 聖輝</td></tr></table>')
    if not mgr or mgr.get("n") != "井上 一樹":
        bad(f"[戦力外・引退の読み取り] NPBの選手一覧から監督の名前を取れない：{mgr}")
    ttl = [t for _, t in ud.off_links("https://www.example.jp/news/", '<a href="/1">井上一樹監督 辞任のお知らせ</a><a href="/2">二軍監督 退任について</a>')]
    # 二軍監督の退任は、首脳陣（コーチ）の発表として拾う
    if ttl != ["井上一樹監督 辞任のお知らせ", "二軍監督 退任について"]:
        bad(f"[戦力外・引退の読み取り] 監督・首脳陣の発表を正しく選べない：{ttl}")
    got, _ = ud.off_article("D", "u", "井上一樹監督 辞任のお知らせ", "<h1>井上一樹監督 辞任のお知らせ</h1><p>2026.09.29</p><p>井上一樹監督から今季限りで辞任したいとの申し入れがあり、受理しました。</p>", roster, 2026, mgr)
    if [(x["n"], x["kind"]) for x in got] != [("井上 一樹", "mgr")]:
        bad(f"[戦力外・引退の読み取り] 監督の辞任を拾えない：{[(x['n'], x['kind']) for x in got]}")
    got, _ = ud.off_article("E", "u", "来季の選手契約について", "<h1>来季の選手契約について</h1><p>2026.09.29</p><p>今野 龍太投手と来季の契約を結ばない。井上一樹監督のコメント</p>", roster, 2026, mgr)
    if any(x["kind"] == "mgr" for x in got):
        bad("[戦力外・引退の読み取り] 選手の発表の中の監督のコメントを、監督の退任として拾っている")


async def weather_stop_check(browser):
    """雨などによる中断・開始の遅れ・中止・ノーゲーム・コールド：中継プログラムから届いたら試合カードに帯が出るか。
    中止の試合も今日の試合に並ぶか（「勝ったら」は出さない）。パ・リーグで「西武（西武）」のように球団名が重ならないか。
    あわせて、中継プログラム（worker/worker.js があれば）の見つけ方を、いろいろな書き方で確かめる"""
    for theme in ["", "pawa"]:
        for lg in ["C", "P"]:
            label = f"[中断・中止 {'パワプロ風' if theme else 'スタイリッシュ'} {lg}]"
            pg, errs = await open_page(browser, 390, theme)
            if lg == "P":
                await pg.evaluate("switchLeague('P')")
                await pg.wait_for_timeout(200)
            r = await pg.evaluate("""async () => {
              const ng = [];
              // 表示中のリーグの試合が2試合以上ある、いちばん新しい日を「今日」にする
              const cnt = {}; DATA.games.filter(g => inLg(g) && CL.includes(g.h) && CL.includes(g.a)).forEach(g => cnt[g.d] = (cnt[g.d] || 0) + 1);
              const today = Object.keys(cnt).filter(d => cnt[d] >= 2).sort().pop();
              if (!today) return ['試験に使える日（2試合以上ある日）がない'];
              jst = () => ({ y: +today.slice(0, 4), m: +today.slice(5, 7), d: +today.slice(8), iso: today });
              S.period = null; S.periodPicked = false; renderAll();
              const games = DATA.games.filter(g => g.d === today && inLg(g) && CL.includes(g.h) && CL.includes(g.a));
              const [g1, g2] = games;
              // 中継プログラムの返事をまねる：1試合目は試合中に降雨で中断、2試合目は降雨で中止
              const body = { games: [
                { d: today, h: g1.h, a: g1.a, st: 'live', hs: 1, as: 0, inn: '5回表', w: { kind: '中断', reason: '降雨', at: '19:05' } },
                { d: today, h: g2.h, a: g2.a, st: 'canc', w: { kind: '中止', reason: '降雨' } } ] };
              const of = window.fetch;
              window.fetch = async (u, o) => String(u).includes('games=') ? new Response(JSON.stringify(body)) : of(u, o);
              g1.st = 'sched'; g2.st = 'sched';
              await pollLive();
              window.fetch = of;
              setTab('game'); renderGame();
              const cards = [...document.querySelectorAll('#today .tg')];
              const cardOf = g => cards.find(c => c.textContent.includes(fn(g.h)) && c.textContent.includes(fn(g.a)));
              const c1 = cardOf(g1), c2 = cardOf(g2);
              if (!c1 || !(c1.querySelector('.wxb') || {}).textContent?.includes('降雨のため試合中断中（19:05〜）')) ng.push('中断の帯が出ない');
              if (!c2) ng.push('中止になった試合が今日の試合から消えている');
              else {
                if (!(c2.querySelector('.wxb') || {}).textContent?.includes('降雨のため試合中止')) ng.push('中止の帯が出ない');
                if (c2.querySelector('.tgo')) ng.push('中止になった試合に「勝ったら」が出ている');
                if (!c2.querySelector('.bi.canc')) ng.push('中止の試合の右側が「試合中止」になっていない');
              }
              // ほかの状態の書き方
              const k = today + g1.h + g1.a;
              const cases = [['再開', { kind: '再開', reason: '降雨', from: '19:05', at: '19:40' }, '19:40に試合再開（降雨のため19:05から中断）'],
                ['遅延', { kind: '遅延', reason: '雷雨' }, '雷雨のため試合開始が遅れています'],
                ['ノーゲーム', { kind: 'ノーゲーム', reason: '降雨' }, '降雨のためノーゲーム'],
                ['コールド', { kind: 'コールド', reason: '降雨' }, '降雨のためコールドゲーム']];
              for (const [n, w, want] of cases) {
                GSTOP[k] = w; g1.st = n === '遅延' ? 'sched' : n === 'ノーゲーム' ? 'canc' : 'live';
                const t = wxHTML(g1);
                if (!t.includes(want)) ng.push(`${n}の帯の文言が違う：${t.replace(/<[^>]+>/g, '')}`);
              }
              // パ・リーグ（担当者なし）で球団名が「西武（西武）」のように重ならない
              const dup = [...document.querySelectorAll('#today li')].map(li => li.textContent).find(t => CL.some(tm => t.includes(`${fn(tm)}（${fn(tm)}）`)));
              if (dup) ng.push(`球団名が重なっている：${dup}`);
              return ng;
            }""")
            for m in r:
                bad(f"{label} {m}")
            for e in errs:
                bad(f"{label}: 画面のエラー {e}")
            await pg.close()
    # 中継プログラムの見つけ方（worker/worker.js がある環境だけ）
    wk = ROOT / "worker" / "worker.js"
    if not wk.exists():
        print("  （中継プログラムの試験は、worker/worker.js がないため省略）")
        return
    import subprocess, tempfile
    src = wk.read_text(encoding="utf-8") + "\nexport { weatherOf, headerOf, parse, parseGame, fixDoublePlays };\n"
    test = r"""
import { weatherOf, headerOf, parse, parseGame, fixDoublePlays } from "./w.mjs";
const ok = (n, got, want) => { if (JSON.stringify(got) !== JSON.stringify(want)) console.log("NG " + n + " " + JSON.stringify(got)); };
const k = l => (weatherOf(l) || {}).kind || null;
ok("中断", k(["19:05 降雨のため試合中断"]), "中断");
ok("再開", k(["19:40 試合再開", "19:05 降雨のため試合中断"]), "再開");
ok("遅延", k(["雷雨のため試合開始を遅らせています"]), "遅延");
ok("中止", k(["降雨のため試合中止"]), "中止");
ok("ノーゲーム", k(["5回表 降雨ノーゲーム"]), "ノーゲーム");
ok("コールド", k(["7回裏 降雨コールドゲーム"]), "コールド");
ok("案内は拾わない", k(["試合中止時の払い戻しについて", "雨天中止の場合のチケット"]), null);
ok("関係ない中断", k(["ビデオ判定のため中断"]), null);
// 10/2 の誤り：試合ページの「新着ニュース」の見出し（ほかの試合の中止振替）や、下の「今日の日程・結果」のほかの試合の中断を、この試合の中止・中断と読んでいた
const news = "【NPB】追加日程＆予備日を発表 9 月29 日中止振替分『阪神ｰヤクルト』が8日に決定 日テレNEWS NNN 2026/10/2 14:10";
ok("ニュースの見出しは拾わない", ((l, f) => (weatherOf(l, f) || {}).kind || null)([news, "阪神 10月8日、甲子園でヤクルト戦 追加日程を発表 デイリースポーツ 2026/10/2 13:55", "29 阪神 - 試合中止 甲子園", "9/29 試合中止"], []), null);
const pg = (status, extra) => `<html><body><div>セ・リーグ 25回戦</div><div>10月2日（金） 18:00 神宮</div><div>ヤクルト</div><div>${status}</div><div>巨人</div><div>後攻</div><div>先攻</div>${extra || ""}<h2>10月2日（金）の日程・結果</h2><div>ロッテ</div><div>試合中断</div><div>雨天中止</div><h2>順位表</h2><h2>新着ニュース</h2><ul><li>${news}</li><li>巨人―広島 降雨コールド 2026/10/2 21:00</li></ul></body></html>`;
ok("試合前のページ（ニュース・ほかの試合に中止・中断）", (parseGame(pg("18:00")) || {}).w || null, null);
ok("見出しに試合中止", ((parseGame(pg("試合中止")) || {}).w || {}).kind, "中止");
ok("本文の降雨中断", ((parseGame(pg("3 - 1", "<p>5回裏</p><p>19:05 降雨のため試合中断</p>")) || {}).w || {}).kind, "中断");
// 併殺打：テキスト速報のダブルプレーを、出場成績の「三ゴロ」→「三併打」に。ゲッツー崩れ・三振ゲッツー・すでに「併」付きは変えない
{ const t = `<h2>テキスト速報</h2><h1>1回裏</h1><ol><li><p>5番 大山 悠輔 一死満塁</p><p>5-4-3のダブルプレー 3アウト</p></li><li><p>4番 佐藤 輝明 無死満塁</p><p>空振り三振 盗塁失敗でダブルプレー</p></li><li><p>3番 森下 翔太 無死一塁</p><p>ショートゴロ ゲッツー崩れ</p></li><li><p>2番 中野 拓夢 無死一塁</p><p>セカンドゴロ併殺打</p></li></ol>`;
  const d = parseGame(t), nm = x => (x || "").normalize("NFKC").replace(/\\s+/g, "");
  const box = { lineups: [[], [{ name: "大山 悠輔", inn: ["三ゴロ"] }, { name: "佐藤 輝明", inn: ["空三振"] }, { name: "森下 翔太", inn: ["遊ゴロ"] }, { name: "中野 拓夢", inn: ["二併打"] }]] };
  fixDoublePlays(box, d.dps, nm);
  ok("併殺打", box.lineups[1].map(r => (r.results || r.inn).join(",")), ["三併打", "空三振", "遊ゴロ", "二併打"]); }
const g = parse('<a href="/scores/2026/0929/t-s-24/">阪神 1 - 0 ヤクルト 5回表 中断 降雨</a><a href="/scores/2026/0929/g-c-25/">巨人 - 広島 中止</a>');
ok("NPB 中断", [g[0].st, g[0].w && g[0].w.kind], ["live", "中断"]);
ok("NPB 中止", g[1].st, "canc");
"""
    with tempfile.TemporaryDirectory() as d:
        (Path(d) / "w.mjs").write_text(src, encoding="utf-8")
        (Path(d) / "t.mjs").write_text(test, encoding="utf-8")
        out = subprocess.run(["node", str(Path(d) / "t.mjs")], capture_output=True, text=True, timeout=60)
        for line in (out.stdout + out.stderr).splitlines():
            if line.strip():
                bad(f"[中継プログラムの中断・中止の見つけ方] {line.strip()[:160]}")


async def call_name_check(browser):
    """一球速報・スコア・打順の名前は苗字（同じ球団に同じ苗字がいれば区別）"""
    pg, errs = await open_page(browser, 390, "")
    r = await pg.evaluate("""() => {
      const ng = [];
      for (const t of Object.keys(DATA.rosters || {})) {
        for (const x of (DATA.rosters[t] || []).slice(0, 40)) {
          const want = shortName(t, x.n);
          if (callName(t, x.n) !== want) ng.push(`${x.n}（フルネーム）→ ${callName(t, x.n)}（${want} のはず）`);
          if (callName(t, x.n.replace(/\\s+/g, '')) !== want) ng.push(`${x.n}（空白なし）→ ${callName(t, x.n.replace(/\\s+/g, ''))}`);
          if (ng.length > 4) return ng;
        }
      }
      // 速報の表示（パワプロ風でなくても）に空白入りのフルネームが出ない
      const t = Object.keys(DATA.rosters)[0], x = DATA.rosters[t].find(y => /\\s/.test(y.n));
      if (x && /\\s/.test(pwName(t, x.n).replace(/<[^>]+>/g, ''))) ng.push(`速報の名前がフルネームのまま：${pwName(t, x.n)}`);
      return ng;
    }""")
    for m in r:
        bad(f"[速報の名前] {m}")
    for e in errs:
        bad(f"[速報の名前]: 画面のエラー {e}")
    await pg.close()
    # データ更新側：過去の一軍の成績から役割（先発・中継ぎ・抑え）を決める
    try:
        import sys as _s
        _s.path.insert(0, str(ROOT / "scripts"))
        import update_data as ud
    except ImportError:
        return
    def page(rows):
        head = "<tr><th>年度</th><th>所属球団</th><th>登板</th><th>勝利</th><th>敗北</th><th>セーブ</th><th>H</th><th>HP</th><th>完投</th><th>完封勝</th><th>無四球</th><th>勝率</th><th>打者</th><th>投球回</th></tr>"
        body = "".join(f"<tr><td>{y}</td><td>阪 神</td><td>{g}</td><td>{w}</td><td>{l}</td><td>{sv}</td><td>0</td><td>0</td><td>{cg}</td><td>0</td><td>0</td><td>.500</td><td>1</td><td><table><tr><td>6</td><td>.1</td></tr></table></td></tr>" for y, g, w, l, sv, cg in rows)
        return "<table>" + head + body + "</table>"
    cases = {"先": [(2023, 18, 8, 6, 0, 0), (2024, 12, 2, 3, 0, 0), (2025, 3, 0, 2, 0, 0)],
             "中": [(2023, 51, 1, 2, 1, 0), (2024, 40, 2, 3, 0, 0), (2025, 30, 1, 1, 0, 0)],
             "抑": [(2024, 50, 3, 2, 30, 0), (2025, 45, 2, 3, 25, 0)]}
    for want, rows in cases.items():
        got = ud.pitcher_role(page(rows))
        if got != want:
            bad(f"[過去の役割の読み取り] {want} のはずが {got}")


async def pitch_tile_check(browser):
    """パワプロ風の一球速報・打順・投手の表・走者の名前の札が、名前の長さに関係なく同じ大きさで、長い名前も札からはみ出さないか"""
    for width in [390, 320]:
        pg, errs = await open_page(browser, width, "pawa")
        r = await pg.evaluate("""() => {
          const ng = [], t = CL[0], o = CL[1], T = DATA.rosters[t] || [], O = DATA.rosters[o] || [];
          const g = DATA.games.find(x => x.h === t && x.a === o) || DATA.games.find(x => x.h === t);
          if (!g) return ng;
          const today = g.d; jst = () => ({ y: +today.slice(0, 4), m: +today.slice(5, 7), d: +today.slice(8), iso: today }); liveWanted = () => false;
          Object.assign(g, { st: 'live', hs: 1, as: 0, inn: '5回裏' });
          const k = g.d + gkey(g), longest = arr => arr.slice().sort((a, b) => b.n.replace(/\\\\s/g, '').length - a.n.replace(/\\\\s/g, '').length)[0];
          const bats = T.filter(x => x.p !== '投手'), pits = O.filter(x => x.p === '投手');
          GD[k] = { line: { innings: ['1','2','3','4','5','6','7','8','9'], away: { name: '', inn: ['0','0','0','0','0','','','',''], r: '0', h: '1', e: '0' }, home: { name: '', inn: ['1','0','0','0','','','','',''], r: '1', h: '3', e: '0' } },
            plays: [], flows: [], pitchers: [pits.slice(0, 3).map(x => ({ name: x.n, ip: '1', np: 10, h: 0, hr: 0, so: 1, bb: 0, r: 0, er: 0, era: '1.00' })).concat([{ name: longest(pits).n, ip: '1', np: 10, h: 0, hr: 0, so: 1, bb: 0, r: 0, er: 0, era: '1.00' }]), []],
            lineups: [O.filter(x => x.p !== '投手').slice(0, 9).map((x, i) => ({ order: i + 1, name: x.n, pos: '遊', avg: '.250', starter: true, results: [] })), bats.slice(0, 8).concat([longest(bats)]).map((x, i) => ({ order: i + 1, name: x.n, pos: '遊', avg: '.250', starter: true, results: [] }))] };
          PD[k] = { half: '5回裏', b: 1, s: 1, o: 1, bases: { '1': true, '3': true }, runners: { '1': longest(bats).n, '3': bats[0].n },
            batter: { name: longest(bats).n, no: '1', hand: '左打', avg: '.250' }, pitcher: { name: longest(pits).n, no: '1', hand: '右投', np: 50, bf: 10, era: '3.00' }, next: bats[1].n, pitches: [] };
          S.open[k] = true; S.lu = S.lu || {}; renderAll(); setTab('game');
          const box = document.querySelector('.tgd');
          if (!box) return ['一球速報が開けない'];
          const groups = { '投手・打者': '.pn3 .ptile', '走者': '.ptile.rtile', '打順': '.lnm .ptile:not(.posic)', '打順の守備位置': '.lnm .ptile.posic', '投手の表': '.putab td.pnx .ptile' };
          for (const [n, sel] of Object.entries(groups)) {
            const ws = [...box.querySelectorAll(sel)].map(e => Math.round(e.getBoundingClientRect().width));
            if (ws.length && new Set(ws).size > 1) ng.push(`${n}の札の大きさがそろっていない：${[...new Set(ws)].join(',')}`);
          }
          box.querySelectorAll('.ptile').forEach(e => { const b = e.querySelector('b'), r = document.createRange(); r.selectNodeContents(b); const rr = r.getBoundingClientRect(), tr = e.getBoundingClientRect(); if (rr.width && (rr.left < tr.left + 0.5 || rr.right > tr.right - 0.5)) ng.push(`名前が札からはみ出している：${b.textContent}`); });
          box.querySelectorAll('.pn3').forEach(e => { const tl = e.querySelector('.ptile'), h = e.querySelector('.hd'); if (tl && h && Math.abs(tl.getBoundingClientRect().top - h.getBoundingClientRect().top) > 12) ng.push('名前の札と「右投」などが同じ行に並んでいない'); });
          // 打順の上の色の説明は出さない（色で分かる）。一球速報はシンプル版（投手は球数・回・安・振・失・防御率、打者は打率・今日の結果だけ）
          if (document.querySelector('.tgd .rleg')) ng.push('打順の上に色の説明が出ている');
          if (!box.querySelector('.pbox.v2') && !box.closest('.pbox.v2') && !document.querySelector('.tgd .pbox.v2')) ng.push('一球速報がシンプル版になっていない');
          if (document.querySelector('.tgd .pg3')) ng.push('一球速報に細かい成績の表が残っている');
          // ランナー：一球速報のページの塁の埋まり方を信じる（計算と違っても）。ダイヤモンドの走者の名前を当てはめる。リクエストは帯で
          PD[k].occ = ['1', '3']; PD[k].rnames = ['リチャード', '大城']; PD[k].runners = {}; PD[k].req = 'リクエスト 判定変更 セーフ→アウト'; renderGame();
          const rt = [...document.querySelectorAll('.tgd .rtile, .tgd .fld text')].map(e => e.textContent).join(',');
          const rn = [...document.querySelectorAll('.tgd .ptile.rtile, .tgd .rname')].length;
          if (!document.querySelector('.tgd .preq') || !document.querySelector('.tgd .preq').textContent.includes('判定変更')) ng.push('リクエストが出ない');
          const occTiles = document.querySelectorAll('.tgd .fldw .ptile.rtile').length;
          if (isPawa() && occTiles !== 2) ng.push(`ランナーの札の数が塁の埋まり方（2人）と合わない：${occTiles}`);
          PD[k].occ = []; PD[k].rnames = []; PD[k].req = null; renderGame();
          if (isPawa() && document.querySelectorAll('.tgd .fldw .ptile.rtile').length) ng.push('ランナーなしなのに走者の札が出ている');
          // NEXT の打者もパワプロ風の札。スコアボードは赤い枠なし・攻撃中のマスは「-」
          if (!box.querySelector('.pnx3 .ptile')) ng.push('NEXT の打者がパワプロ風の札になっていない');
          if (document.querySelector('.ls td.cur, .ls th.cur')) ng.push('スコアボードに今の回の枠（赤）が残っている');
          const nowTd = document.querySelector('.ls td.now');
          if (!nowTd) ng.push('スコアボードの攻撃中のマスが分からない'); else if (!nowTd.textContent.trim()) ng.push('スコアボードの攻撃中のマスに「-」がない');
          // 名前の字間は成績タブの札と同じ（2文字は .7em）
          box.querySelectorAll('.pn3 .sptile.sp b, .lnm .sptile.sp b').forEach(b => { const cs = getComputedStyle(b); if (Math.abs(parseFloat(cs.letterSpacing) - parseFloat(cs.fontSize) * 0.7) > 0.6) ng.push(`2文字の名前の字間が成績タブと違う：${b.textContent}（${cs.letterSpacing}）`); });
          // 球数：投球ごとの「通算」がいちばん新しい（出場成績や投手の欄より大きければ、そちらを出す）
          PD[k].pitcher.game = { np: '50', ip: '3', h: '2', so: '1', bb: '0', r: '0', bf: '12' }; PD[k].pitcher.np = 55;
          PD[k].pitches = [{ n: 1, total: '57', type: 'ストレート', speed: '146km/h', res: 'ボール' }, { n: 2, total: '58', type: 'フォーク', speed: '137km/h', res: 'ファウル' }, { n: 3, total: '59', type: 'スライダー', speed: '127km/h', res: '空振り' }];
          renderGame();
          const gv = document.querySelector('.tgd .pgauge em');
          if (!gv || gv.textContent.trim() !== '59') ng.push(`球数がいちばん新しい数（59）になっていない：${gv && gv.textContent}`);
          const cn = [...document.querySelectorAll('.tgd .pcn')].map(e => e.textContent).join(',');
          if (cn !== '1-0,1-1,1-2') ng.push(`1球ごとのカウントが違う：${cn}`);
          // ホームのチームは左：〇回裏（ホームの攻撃）は左が打者、〇回表（ビジターの攻撃）は左が投手
          const side = () => { const c = [...document.querySelectorAll('.tgd .pvs3 .pc3')].sort((a, b) => a.getBoundingClientRect().left - b.getBoundingClientRect().left);
            return c.length === 2 ? (c[0].classList.contains('pcp') ? 'pitcher' : 'batter') : ''; };
          if (side() !== 'batter') ng.push('ホームの攻撃中（裏）なのに、左が打者（ホーム）になっていない');
          PD[k].half = '6回表'; g.inn = '6回表'; renderGame();
          if (side() !== 'pitcher') ng.push('ビジターの攻撃中（表）なのに、左が投手（ホーム）になっていない');
          return ng.slice(0, 6);
        }""")
        for m in r:
            bad(f"[一球速報の名前の札 パワプロ風 幅{width}] {m}")
        for e in errs:
            bad(f"[一球速報の名前の札 パワプロ風 幅{width}]: 画面のエラー {e}")
        await pg.close()


async def makeup_check(browser):
    """雨などで中止になって振替日が決まっていない試合（振替待ち）も、残り試合に数えるか（順位表・戦況の表・確定の条件）"""
    for lg in ["C", "P"]:
        label = f"[振替待ちの残り試合 {lg}]"
        pg, errs = await open_page(browser, 390, "")
        if lg == "P":
            await pg.evaluate("switchLeague('P')")
            await pg.wait_for_timeout(200)
        r = await pg.evaluate("""() => {
          const ng = [], fin = periods()[periods().length - 1];
          // 最後の月度の、まだ行っていない同じリーグどうしの試合を1つ選び、その日を「今日」にする
          const g = DATA.games.filter(x => x.st === 'sched' && CL.includes(x.h) && CL.includes(x.a) && fin.months.includes(monthOf(x.d))).sort((a, b) => a.d < b.d ? -1 : 1)[0];
          if (!g) return ng;
          jst = () => ({ y: +g.d.slice(0, 4), m: +g.d.slice(5, 7), d: +g.d.slice(8), iso: g.d }); S.period = null; S.periodPicked = false;
          const read = () => { renderAll(); setTab('magic');
            const std = {}; document.querySelectorAll('#std tbody tr').forEach(tr => { const t = CL.find(x => tr.querySelector('.tnm').textContent.includes(fn(x))); if (t) std[t] = tr.lastElementChild.textContent.trim(); });
            const a = analyze(DATA.games, S.period, CONFIG), mag = {}; a.rows.forEach(r => mag[r.t] = r.rem);
            return { std, mag }; };
          const b0 = read();
          g.st = 'canc';
          const b1 = read();
          for (const t of [g.h, g.a]) {
            if (b0.std[t] !== b1.std[t]) ng.push(`中止になると順位表の${fn(t)}の残試合が変わる（${b0.std[t]}→${b1.std[t]}）`);
            if (b0.mag[t] !== undefined && b0.mag[t] !== b1.mag[t]) ng.push(`中止になると戦況の${fn(t)}の残試合が変わる（${b0.mag[t]}→${b1.mag[t]}）`);
          }
          const chips = [...document.querySelectorAll('#condBody .left span')].map(s => s.textContent);
          // 中止になった試合の球団の「確定の条件」があれば、その残り試合に「振替」が出る
          const cards = [...document.querySelectorAll('#condBody .cond')].filter(c => [g.h, g.a].some(t => (c.querySelector('.ch b') || {}).textContent === fn(t)));
          if (cards.length && !cards.every(c => [...c.querySelectorAll('.left span')].some(s => s.textContent.startsWith('振替')))) ng.push('確定の条件の残り試合に「振替」が出ない');
          // 次の試合の一覧に、日付の決まっていない仮の試合が出ない
          setTab('game'); if ([...document.querySelectorAll('#today .tg')].some(c => c.textContent.includes('undefined'))) ng.push('今日の試合に仮の試合が出ている');
          g.st = 'sched'; renderAll();
          return ng;
        }""")
        for m in r:
            bad(f"{label} {m}")
        for e in errs:
            bad(f"{label}: 画面のエラー {e}")
        await pg.close()


async def post_bracket_check(browser):
    """CS・日本シリーズの勝ち上がり表：勝ち数（ファイナルの1位はアドバンテージ込み）・勝ち上がり・並んだときの扱い・はみ出し"""
    for theme in ["", "pawa"]:
        for lg in ["C", "P"]:
            label = f"[勝ち上がり表 {'パワプロ風' if theme else 'スタイリッシュ'} {lg}]"
            pg, errs = await open_page(browser, 390, theme)
            if lg == "P":
                await pg.evaluate("switchLeague('P')")
                await pg.wait_for_timeout(200)
            r = await pg.evaluate("""(lg) => {
              const ng = [], P = DATA.post || [];
              if (!P.some(g => g.stage === 'CS1' && (g.lg || 'C') === lg)) return ng;
              jst = () => ({ y: 2026, m: 10, d: 20, iso: '2026-10-20' });
              DATA.games.forEach(g => { if (g.st !== 'final' && g.st !== 'canc') { g.st = 'final'; g.hs = 2; g.as = 1; } });
              const rk = lgRank(lg).rk, mine = st => P.filter(g => g.stage === st && (g.lg || 'C') === lg).sort((a, b) => a.no - b.no);
              const c1 = mine('CS1'), cf = mine('CSF');
              // ファースト：1勝1敗1分 → 並んだので2位が勝ち上がり
              Object.assign(c1[0], { h: rk[1], a: rk[2], hs: 3, as: 1, st: 'final' }); Object.assign(c1[1], { h: rk[1], a: rk[2], hs: 2, as: 5, st: 'final' }); Object.assign(c1[2], { h: rk[1], a: rk[2], hs: 4, as: 4, st: 'final' });
              // ファイナル：1位が3勝（＋アドバンテージ1＝4）で勝ち上がり
              for (let i = 0; i < 3; i++) Object.assign(cf[i], { h: rk[0], a: rk[1], hs: 5, as: 1, st: 'final' });
              renderAll(); setTab('std');
              const s = lgPost(lg);
              if (s.s1.win !== rk[1]) ng.push(`ファーストステージで並んだのに2位が勝ち上がらない（${s.s1.win}）`);
              // 2026年からの新ルール：アドバンテージは1勝か2勝（ファースト勝者が1位と10ゲーム差以上か勝率5割未満なら2勝）
              if (s.sf.wh !== 3 + s.R.adv || s.sf.win !== rk[0]) ng.push(`ファイナルの1位の勝ち数（アドバンテージ${s.R.adv}込み）・勝ち上がりが違う（${s.sf.wh}・${s.sf.win}）`);
              const box = document.getElementById('bracketBox'); box.style.contentVisibility = 'visible';   // 画面の外は並べるのを後回しにしているので、読む前に並べる
              const txt = box.innerText;
              if (!txt.includes(fn(rk[1]) + 'がファイナルステージへ')) ng.push('ファーストステージの勝ち上がりの文言が出ない');
              if (!txt.includes(fn(rk[0]) + 'が日本シリーズへ')) ng.push('ファイナルステージの勝ち上がりの文言が出ない');
              if (!/○ 3-1/.test(txt) || !/● 2-5/.test(txt) || !/△ 4-4/.test(txt)) ng.push('1試合ずつの結果（○●△）が出ない');
              const W = box.getBoundingClientRect().right + 1;
              box.querySelectorAll('*').forEach(e => { const b = e.getBoundingClientRect(); if (b.width && b.right > W) ng.push(`はみ出し：${e.className}`); });
              return [...new Set(ng)].slice(0, 6);
            }""", lg)
            for m in r:
                bad(f"{label} {m}")
            for e in errs:
                bad(f"{label}: 画面のエラー {e}")
            await pg.close()


async def archive_check(browser):
    """前のシーズンの記録：設定から切り替えて見られるか・ライブや自動の差し替えが止まるか・今季に戻れるか"""
    import http.server, threading, functools
    class Quiet(http.server.SimpleHTTPRequestHandler):
        def log_message(self, *a):
            pass
    srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), functools.partial(Quiet, directory=str(ROOT)))
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{srv.server_port}/"
    try:
        d = json.loads((ROOT / "data" / "latest.json").read_text(encoding="utf-8"))
        a = dict(d, season=2025, games=[dict(g, d=g["d"].replace(str(d["season"]), "2025"), st=("canc" if g["st"] == "canc" else "final"), hs=g.get("hs", 2), **{"as": g.get("as", 1)}) for g in d["games"]])
        pg = await browser.new_page(viewport={"width": 390, "height": 844})
        errs = []
        pg.on("pageerror", lambda e: errs.append(str(e)))
        await pg.add_init_script("localStorage.setItem('me','T'); localStorage.setItem('league','C')")
        await pg.route("https://**", lambda r: r.abort())
        await pg.route("**/data/archive/index.json*", lambda r: r.fulfill(status=200, content_type="application/json", body=json.dumps({"seasons": [d["season"], 2025]})))
        await pg.route("**/data/archive/2025.json*", lambda r: r.fulfill(status=200, content_type="application/json", body=json.dumps(a, ensure_ascii=False)))
        await pg.goto(base + "index.html")
        await pg.wait_for_timeout(800)
        if not await pg.evaluate("document.getElementById('archBar').hidden"):
            bad("[シーズンの記録] ふだんから「記録を表示中」の帯が出ている")
        await pg.evaluate("openSheet()")
        await pg.wait_for_timeout(500)
        if await pg.evaluate("document.getElementById('archSec').hidden"):
            bad("[シーズンの記録] 設定に「シーズン」の切り替えが出ない")
        else:
            await pg.click('#archSeg button[data-arch="2025"]')
            await pg.wait_for_timeout(800)
            r = await pg.evaluate("[ARCH, String(DATA.season), document.getElementById('archBar').hidden, liveWanted(), jst().iso.slice(0, 4)]")
            if r != [2025, "2025", False, False, "2025"]:
                bad(f"[シーズンの記録] 2025年の記録に切り替わらない（{r}）")
            txt = await pg.evaluate("setTab('std'), document.getElementById('v-std').innerText")
            if "undefined" in txt or "NaN" in txt:
                bad("[シーズンの記録] 記録の表示におかしな文字が出る")
        for e in errs:
            bad(f"[シーズンの記録]: 画面のエラー {e}")
        await pg.close()
    finally:
        srv.shutdown()
    # データ更新側：公式戦が全部終わったら data/archive/<年>.json と index.json を作る
    try:
        import sys as _s, tempfile
        _s.path.insert(0, str(ROOT / "scripts"))
        import update_data as ud
    except ImportError:
        return
    with tempfile.TemporaryDirectory() as tmp:
        ud.ARCH_DIR = str(Path(tmp) / "archive")
        ud.write_archive(dict(d, games=[dict(g, st="sched") for g in d["games"][:3]]))
        if (Path(tmp) / "archive").exists() and any((Path(tmp) / "archive").iterdir()) and datetime_month() < 11:
            bad("[シーズンの記録] 試合が残っているのに記録を保存している")
        ud.write_archive(dict(d, season=2025), force=True)
        idx = json.loads((Path(tmp) / "archive" / "index.json").read_text(encoding="utf-8"))
        if 2025 not in idx.get("seasons", []) or not (Path(tmp) / "archive" / "2025.json").exists():
            bad(f"[シーズンの記録] 記録のファイルができない（{idx}）")


def datetime_month():
    import datetime as _dt
    return (_dt.datetime.utcnow() + _dt.timedelta(hours=9)).month


async def song_list_check(browser):
    """応援歌タブ：応援歌がある選手だけを出し、ほかの球団へ移籍した選手は出さない"""
    pg, errs = await open_page(browser, 390, "")
    r = await pg.evaluate("""() => {
      const ng = [], t = 'T', ro = DATA.rosters[t] || [];
      const withSong = ro.filter(x => x.song), noSong = ro.filter(x => 'song' in x && !x.song);
      if (!withSong.length) return ng;
      const mv = withSong[0];
      DATA.offseason = { season: 2026, items: [{ t, n: mv.n, no: mv.no, kind: 'out', via: 'trade', to: 'G', date: '2026-11-10' }], teams: {}, seen: {} };
      S.songTeam = t; S.songQ = ''; setTab('song'); renderSong();
      const txt = document.getElementById('v-song').innerText.replace(/\\s+/g, '');
      if (txt.includes(mv.n.replace(/\\s+/g, '')) || txt.includes(shortName(t, mv.n) + '内野') && false) ng.push(`移籍した選手（${mv.n}）が応援歌タブに出ている`);
      const tiles = [...document.querySelectorAll('#v-song [data-song]')].map(b => b.dataset.song.split('|')[1]);
      if (tiles.includes(mv.n)) ng.push(`移籍した選手（${mv.n}）が応援歌タブに出ている`);
      const extra = tiles.filter(n => noSong.some(x => x.n === n));
      if (extra.length) ng.push(`応援歌がない選手が出ている：${extra.slice(0, 3).join('、')}`);
      // 応援歌ページの飛び先：名字から名前までの範囲（text=名字,名前）。ソフトバンクは転送されないURL（最後の「/」なし）
      for (const tt of ['H', 'F', 'DB']) {
        const x = (DATA.rosters[tt] || []).find(r => r.song && /\\s/.test(r.n));
        if (!x) continue;
        const [a, b] = x.n.trim().split(/\\s+/), u = songLink(tt, x);
        if (!u.includes('#:~:text=' + encodeURIComponent(a) + ',' + encodeURIComponent(b))) ng.push(`応援歌ページの飛び先が「名字,名前」の範囲になっていない（${tt}）：${decodeURIComponent(u)}`);
        if (tt === 'H' && !u.startsWith('https://www.softbankhawks.co.jp/team/song#')) ng.push(`ソフトバンクの応援歌ページのURLが転送されない形になっていない：${u}`);
      }
      // 応援歌のページを開くボタンの文字は、どの球団も「応援歌」（「公式」と混ざらない）
      const labels = new Set([...document.querySelectorAll('#v-song a.sof')].map(a => a.textContent.trim()));
      if (labels.size && (labels.size > 1 || !labels.has('応援歌'))) ng.push(`応援歌のボタンの文字がそろっていない：${[...labels].join('・')}`);
      for (const tt of ['L', 'H', 'T']) { const x = (DATA.rosters[tt] || []).find(r => r.song); if (x) { openPlayer(tt, x.n); const a = document.querySelector('#songPick a.sof'); if (a && a.textContent.trim() !== '応援歌') ng.push(`選手の画面の応援歌のボタンが「${a.textContent.trim()}」（${tt}）`); } }
      document.getElementById('songSheet').hidden = true; document.getElementById('songSheet').classList.remove('open');
      // 移籍した選手を開いても、元の球団の応援歌のボタンは出ない
      DATA.offseason = { season: 2026, items: [{ t, n: mv.n, no: mv.no, kind: 'out', via: 'trade', to: 'G', date: '2026-05-13' }], teams: {}, seen: {} };
      openPlayer(t, mv.n);
      if (document.querySelector('#songPick .sof, #songPick .sgo')) ng.push(`移籍した選手（${mv.n}）の画面に、元の球団の応援歌のボタンが出ている`);
      if (songLink(t, mv)) ng.push('移籍した選手に応援歌のリンクが作られる');
      document.getElementById('songSheet').hidden = true; document.getElementById('songSheet').classList.remove('open');
      DATA.offseason = null; renderSong();
      return ng;
    }""")
    for m in r:
        bad(f"[応援歌の選手] {m}")
    for e in errs:
        bad(f"[応援歌の選手]: 画面のエラー {e}")
    await pg.close()


async def consistency_check(browser):
    """言葉・書き方の統一：同じものを別の書き方で出していないか（全タブ・設定・選手の画面）"""
    NG = [(r"^(チーム内の成績|今オフの動き|.*のオフの動き|今日の試合|次の試合|直近の勝敗|順位の推移|担当者ごとの年間成績|月度ごとの支払い|首脳陣の配置転換)", "見出しに「〜の」が残っている"),
          (r"\d{1,2}:\d{2} 確認", "時刻の後ろは「更新」にそろえる（「確認」になっている）"),
          (r"\d+月\d+日時点", "日付は「M/D 時点」にそろえる（「○月○日時点」になっている）"),
          (r"現在$", "「現在」ではなく「時点」「更新」にそろえる"),
          (r"取得できませんでした|開き直して", "読み込めなかったときの文言がそろっていない"),
          (r"^支払$|^消滅$|自力消滅", "「支払い」「自力脱出消滅」にそろえる（略した書き方が残っている）"),
          (r"^公式$", "応援歌のボタンは「応援歌」にそろえる")]
    for lg in ["C", "P"]:
        pg, errs = await open_page(browser, 390, "")
        if lg == "P":
            await pg.evaluate("switchLeague('P')")
            await pg.wait_for_timeout(200)
        texts = await pg.evaluate("""() => {
          const out = [];
          for (const t of ['magic', 'game', 'cal', 'std', 'stats', 'rec', 'song', 'off']) {
            setTab(t);
            document.querySelectorAll('#v-' + t + ' *').forEach(e => { if (!e.children.length || /^(SMALL|P|B|SPAN|EM)$/.test(e.tagName)) { const x = e.innerText ? e.innerText.trim() : ''; if (x && x.length < 200) out.push(t + '｜' + x); } });
          }
          return out;
        }""")
        import re as _re
        seen = set()
        for line in texts:
            tab, _, x = line.partition("｜")
            if x.replace("\n", "") == "自力脱出消滅":
                continue   # 表のマジック欄は「自力脱出／消滅」の2行で出している
            for pat, why in NG:
                for part in x.split("\n"):
                    if _re.search(pat, part.strip()) and (why, part) not in seen:
                        seen.add((why, part))
                        bad(f"[言葉の統一 {lg}] {why}：{tab}「{part.strip()[:40]}」")
        for e in errs:
            bad(f"[言葉の統一 {lg}]: 画面のエラー {e}")
        await pg.close()


async def owner_pos_check(browser):
    """設定の「名前の色（パワプロ風）」：担当者ごとに好きなポジションの色を選べて、タイルの色が変わり、端末に残るか"""
    pg, errs = await open_page(browser, 390, "pawa")
    r = await pg.evaluate("""() => {
      const ng = [], t = Object.keys(CONFIG.owners || {})[0];
      if (!t) return ng;
      openSheet();
      if (document.getElementById('opSec').hidden) return ['設定に「名前の色」の欄が出ない'];
      for (const [k, cls] of [['先', 'ps'], ['捕', 'pc'], ['外', 'po']]) {
        document.querySelector(`#opList [data-op="${t}|${k}"]`).click();
        if (ownerPosOf(t) !== k || JSON.parse(localStorage.getItem('ownerPos') || '{}')[t] !== k) ng.push(`${k}を選んでも保存されない`);
        const tile = document.createElement('div'); tile.innerHTML = ownT(t);
        if (!tile.querySelector('.ptile.' + cls)) ng.push(`${k}を選んでもタイルの色が変わらない（${tile.innerHTML.slice(0, 60)}）`);
      }
      localStorage.removeItem('ownerPos');
      if (ownerPosOf(t) !== (CONFIG.ownerPos || {})[t]) ng.push('選んでいないときに最初の設定の色に戻らない');
      switchLeague('P'); openSheet();
      if (!document.getElementById('opSec').hidden) ng.push('パ・リーグ（担当者なし）でも「名前の色」の欄が出る');
      return ng;
    }""")
    for m in r:
        bad(f"[名前の色] {m}")
    for e in errs:
        bad(f"[名前の色]: 画面のエラー {e}")
    await pg.close()


async def team_rank_menu_check(browser):
    """個人ランキングの下のボタンの列がないこと。チーム内の成績も項目のメニューで全項目を選べて、表がはみ出さず5列のままか"""
    SET = """() => { const t = ptTeam(), ro = (DATA.rosters[t] || []), bat = {}, pit = {};
      ro.filter(x => x.p !== '投手').slice(0, 9).forEach((x, i) => { bat[x.n] = { 試合: 100 + i, 打席: 350 + i * 10, 打数: 300, 安打: 80 + i, 二塁打: 10, 三塁打: 1, 本塁打: 5 + i, 塁打: 120, 打点: 40 + i, 得点: 30, 盗塁: i * 3, 盗塁刺: 1, 犠打: 2, 犠飛: 1, 四球: 30, 死球: 2, 三振: 60, 併殺打: 5, 打率: '.2' + (60 + i), 長打率: '.40' + i, 出塁率: '.33' + i }; });
      ro.filter(x => x.p === '投手').slice(0, 6).forEach((x, i) => { pit[x.n] = { 登板: 20 + i, 勝利: 5, 敗北: 3, セーブ: i, ホールド: 10, HP: 12, 完投: 0, 完封勝: 0, 無四球: 0, 勝率: '.625', 打者: 300, 投球回: '60.1', 安打: 50, 本塁打: 5, 四球: 20, 死球: 2, 三振: 55 + i, 暴投: 1, ボーク: 0, 失点: 20, 自責点: 18, 防御率: '2.' + (10 + i) }; });
      DATA.tstats = null; DATA.tstats_at = null;   // この試験は中継プログラム（NPB）の表の形で見る
      PST[t] = { at: Date.now(), d: { bat, pit, asof: '9/28' } }; renderTeamIn(); }"""
    for theme in ["", "pawa"]:
        for width in [320, 390]:
            label = f"[チーム内の成績の項目 {'パワプロ風' if theme else 'スタイリッシュ'} 幅{width}]"
            pg, errs = await open_page(browser, width, theme)
            await pg.evaluate("setTab('stats')")
            await pg.wait_for_timeout(300)
            if await pg.evaluate("!!document.getElementById('catQuick')"):
                bad(f"{label} 個人ランキングの下のボタンの列が残っている")
            await pg.evaluate(SET)
            r = await pg.evaluate("""() => {
              const ng = [];
              for (const kind of ['bat', 'pit']) {
                S.ptKind = kind; renderTeamIn();
                const sel = document.getElementById('ptCat'), opts = [...sel.options].map(o => o.value).filter(Boolean);
                const want = CATS[kind].map(c => c[1]);
                if (opts.join() !== want.join()) ng.push(`${kind}の項目が個人ランキングと違う（${opts.length}項目）`);
                for (const v of opts) {
                  sel.value = v; sel.dispatchEvent(new Event('change'));
                  const tb = document.getElementById('ptTbl');
                  if (!tb) { ng.push(`${v}を選ぶと表が出ない`); continue; }
                  if (tb.querySelectorAll('thead th').length !== 6) ng.push(`${v}を選ぶと列の数が変わる`);
                  if (tb.scrollWidth > tb.parentElement.clientWidth + 1 || [...tb.querySelectorAll('td, th')].some(c => c.scrollWidth > c.clientWidth + 1)) ng.push(`${v}を選ぶと表がはみ出す`);
                  if (!tb.querySelector('th.on')) ng.push(`${v}を選んでも、その項目の見出しが選ばれた形にならない`);
                }
              }
              // 打者の表に投手、投手の表に野手が出ない（スポナビの「位置」）
              const t = ptTeam(); DATA.tstats = { at: '2026-09-29T21:00:00+09:00', asof: '9/29 21:00', cols: { bat: ['打率', '試合', '打席'], pit: ['防御率', '登板', '投球回'] },
                teams: { [t]: { bat: [['野手 一郎', '内', '.300', '100', '400'], ['投手 二郎', '投', '.100', '20', '30']], pit: [['投手 二郎', '投', '2.50', '20', '100.1'], ['野手 一郎', '内', '0.00', '1', '1']] } } };
              S.ptSort = null; S.ptKind = 'bat'; renderTeamIn();
              let names = [...document.querySelectorAll('#ptTbl tbody tr')].map(tr => tr.querySelector('[data-pl]').dataset.pl.split('|')[1]);
              if (names.join() !== '野手 一郎') ng.push(`打者の表に投手が混ざる（${names}）`);
              S.ptKind = 'pit'; renderTeamIn();
              names = [...document.querySelectorAll('#ptTbl tbody tr')].map(tr => tr.querySelector('[data-pl]').dataset.pl.split('|')[1]);
              if (names.join() !== '投手 二郎') ng.push(`投手の表に野手が混ざる（${names}）`);
              if (!document.getElementById('ptAsof').textContent.includes('9/29 21:00 更新')) ng.push('チーム別成績の更新時刻が出ない');
              DATA.tstats = null; S.ptKind = 'bat';
              S.ptSort = null; saveStatsUI(); return ng.slice(0, 6);
            }""")
            for m in r:
                bad(f"{label} {m}")
            for e in errs:
                bad(f"{label}: 画面のエラー {e}")
            await pg.close()


async def speed_health_check(browser):
    """起動を軽く：チーム別成績は data/tstats.json に分けて、成績タブを開いたときに読む。
    サイト一式の保存（sw.js）の中身と登録のしかた。データの状態に各項目の行が出るか。
    チーム別成績のメニューに「出場の多い順」がないこと、スタイリッシュに「名前の色」の欄がないこと"""
    import http.server, threading, functools, subprocess
    sw = ROOT / "sw.js"
    if not sw.exists():
        bad("[起動の速さ] sw.js（サイト一式の保存）がない")
    else:
        out = subprocess.run(["node", "--check", str(sw)], capture_output=True, text=True)
        if out.returncode:
            bad(f"[起動の速さ] sw.js に書き間違いがある：{out.stderr[:120]}")
        src = sw.read_text(encoding="utf-8")
        for need, why in [("skipWaiting", "新しい版にすぐ切り替わらない"), ('req.mode === "navigate"', "サイト本体を保存していない"),
                          ("/data/", "データを保存していない"), ('searchParams.has("v")', "新しい版への切り替え（?v=）で最新を取りに行かない"),
                          ("url.origin !== self.location.origin", "ほかのサイトへの通信（速報など）に手を出してしまう")]:
            if need not in src:
                bad(f"[起動の速さ] sw.js：{why}")
    html = (ROOT / "index.html").read_text(encoding="utf-8")
    if 'navigator.serviceWorker.register("sw.js")' not in html or 'location.protocol === "https:"' not in html:
        bad("[起動の速さ] サイト一式の保存を https のときだけ登録していない")
    class Quiet(http.server.SimpleHTTPRequestHandler):
        def log_message(self, *a):
            pass
    srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), functools.partial(Quiet, directory=str(ROOT)))
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{srv.server_port}/"
    try:
        d = json.loads((ROOT / "data" / "latest.json").read_text(encoding="utf-8"))
        t0 = "T"
        ts = {"at": "2026-09-29T21:00:00+09:00", "asof": "9/29 21:00", "cols": {"bat": ["打率", "試合", "打席"], "pit": ["防御率", "登板", "投球回"]},
              "teams": {t0: {"bat": [["読込 太郎", "内", ".300", "100", "400"]], "pit": [["読込 次郎", "投", "2.50", "20", "100.1"]]}}}
        dd = dict(d); dd.pop("tstats", None); dd["tstats_at"] = ts["at"]
        seen = []
        pg = await browser.new_page(viewport={"width": 390, "height": 844})
        errs = []
        pg.on("pageerror", lambda e: errs.append(str(e)))
        await pg.add_init_script("localStorage.clear(); localStorage.setItem('me','T'); localStorage.setItem('league','C')")
        await pg.route("https://**", lambda r: r.abort())
        await pg.route("**/data/latest.json*", lambda r: r.fulfill(status=200, content_type="application/json", body=json.dumps(dd, ensure_ascii=False)))
        async def ts_route(r):
            seen.append(r.request.url)
            await r.fulfill(status=200, content_type="application/json", body=json.dumps(ts, ensure_ascii=False))
        await pg.route("**/data/tstats.json*", ts_route)
        await pg.goto(base + "index.html")
        await pg.wait_for_timeout(900)
        if seen:
            bad("[起動の速さ] 開いた瞬間にチーム別成績（data/tstats.json）まで読んでいる")
        await pg.evaluate("S.ptTeam = 'T'; setTab('stats')")
        await pg.wait_for_timeout(900)
        if not seen:
            bad("[起動の速さ] 成績タブを開いてもチーム別成績（data/tstats.json）を読みに行かない")
        txt = await pg.evaluate("document.getElementById('ptList').innerText")
        if "読込" not in txt:
            bad("[起動の速さ] 読んだチーム別成績が表に出ない")
        opts = await pg.evaluate("[...document.querySelectorAll('#ptCat option')].map(o => o.textContent)")
        if "出場の多い順" in opts or (opts and opts[0] != "打率"):
            bad(f"[チーム別成績] メニューの最初が打率になっていない・「出場の多い順」が残っている：{opts[:2]}")
        # データの状態：チーム別成績の行
        rows = await pg.evaluate("openSheet(), [...document.querySelectorAll('#healthBox .hi b')].map(b => b.textContent)")
        if "チーム別成績" not in rows:
            bad(f"[データの状態] チーム別成績の行がない：{rows}")
        # パ・リーグ（担当者なし）では「あなたの担当」を出さない。セ・リーグでは出す
        if await pg.evaluate("document.getElementById('meSec').hidden"):
            bad("[あなたの担当] セ・リーグで設定に「あなたの担当」が出ない")
        await pg.evaluate("switchLeague('P'); setTab('magic')")
        if not await pg.evaluate("document.getElementById('meSec').hidden"):
            bad("[あなたの担当] パ・リーグでも設定に「あなたの担当」が出ている")
        if await pg.evaluate("document.getElementById('meCard').innerText.trim()"):
            bad("[あなたの担当] パ・リーグの戦況に「あなた」のカードが出ている")
        await pg.evaluate("switchLeague('C')")
        # スタイリッシュでは「名前の色（パワプロ風）」の欄を出さない（最初はパワプロ風なので、スタイリッシュに切り替えてから見る）
        await pg.evaluate("document.querySelector('#themeSeg button[data-theme=\"\"]').click()")
        await pg.wait_for_timeout(700)
        await pg.evaluate("openSheet()")
        if not await pg.evaluate("document.getElementById('opSec').hidden"):
            bad("[名前の色] スタイリッシュでも「名前の色（パワプロ風）」の欄が出ている")
        for e in errs:
            bad(f"[起動の速さ]: 画面のエラー {e}")
        await pg.close()
    finally:
        srv.shutdown()
    # オフシーズンの行（開けていない球団の知らせ・監督コーチ一覧・ドラフト）
    pg, errs = await open_page(browser, 390, "")
    r = await pg.evaluate("""() => {
      DATA.offseason = { season: 2026, checked_at: new Date(Date.now()).toISOString(), items: [{ t: 'G', n: 'テスト', kind: 'cut', date: '2026-10-01' }],
        teams: { G: { err: 'ニュース一覧を開けない' } }, staff: { T: [], G: [], DB: [], D: [], C: [], S: [] }, managers_date: '2026-10-01', draft_status: { 'https://npb.jp/draft/2026/': 0 } };
      const it = healthItems(), get = k => it.find(x => x.k === k) || {};
      const ng = [];
      if (get('入退団（球団・NPBの発表）').lv !== 'warn' || !String(get('入退団（球団・NPBの発表）').v).includes('巨人')) ng.push('開けていない球団（巨人）がデータの状態に出ない');
      if (get('監督・コーチ一覧').lv !== 'ok') ng.push('監督・コーチ一覧の行が出ない');
      if (!get('ドラフト').k) ng.push('ドラフトの行が出ない');
      return ng;
    }""")
    for m in r:
        bad(f"[データの状態] {m}")
    await pg.close()


async def brand_check(browser):
    """サイト名（hobby baseball）・ホーム画面のアイコン・OGP・開いたときの演出・設定のデータの状態（折りたたみ）"""
    html = (ROOT / "index.html").read_text(encoding="utf-8")
    for need, why in [("<title>hobby baseball</title>", "ページの名前"), ('apple-mobile-web-app-title" content="hobby baseball"', "ホーム画面の名前"),
                      ('og:title" content="hobby baseball"', "OGPの名前"), ('og:image" content="https://negohub.github.io/hobby-baseball/ogp.png"', "OGPの画像"),
                      ('rel="apple-touch-icon" href="apple-touch-icon.png?v=', "ホーム画面のアイコン（中身の目印付き）"), ('id="splash"', "開いたときの演出")]:
        if need not in html:
            bad(f"[サイト名・アイコン] {why}が設定されていない")
    if "J SPORTS ペナントレース" in html:
        bad("[サイト名・アイコン] 古いサイト名（J SPORTS ペナントレース）が残っている")
    try:
        m = json.loads((ROOT / "manifest.json").read_text(encoding="utf-8"))
        if m.get("name") != "hobby baseball" or not any(i.get("sizes") == "512x512" for i in m.get("icons", [])):
            bad("[サイト名・アイコン] manifest.json の名前・アイコンが違う")
        for i in m.get("icons", []):
            if not (ROOT / i["src"].split("?")[0]).exists():
                bad(f"[サイト名・アイコン] アイコンの画像がない：{i['src']}")
    except (OSError, ValueError) as e:
        bad(f"[サイト名・アイコン] manifest.json を読めない：{e}")
    # ホーム画面から開いた瞬間の起動画面（機種ごと）：指定があって、画像がそろっているか
    import re as _re
    starts = _re.findall(r'<link rel="apple-touch-startup-image" media="[^"]+" href="([^"]+)">', html)
    if len(starts) < 10:
        bad(f"[サイト名・アイコン] 起動画面（apple-touch-startup-image）の指定が足りない：{len(starts)}")
    for f in starts:
        if "?v=" not in f:
            bad(f"[サイト名・アイコン] 起動画面の画像に中身の目印（?v=）がない：{f}")
        f = f.split("?")[0]
        if not (ROOT / f).exists():
            bad(f"[サイト名・アイコン] 起動画面の画像がない：{f}")
    for f in ["ogp.png", "apple-touch-icon.png", "favicon.png", "splash.jpg"]:
        if not (ROOT / f).exists():
            bad(f"[サイト名・アイコン] {f} がない")
    pg, errs = await open_page(browser, 390, "")
    r = await pg.evaluate("""() => {
      const ng = [];
      if (document.getElementById('splash')) ng.push('自動の検査のときに開いたときの演出が消えていない（ほかの検査の邪魔になる）');
      openSheet();
      const det = document.querySelector('#sheet details.sh-det');
      if (!det) ng.push('データの状態が折りたたみになっていない');
      else if (det.open) ng.push('データの状態が最初から開いている');
      renderHealth();
      if (!document.getElementById('gearDot').hidden) ng.push('歯車の右上に点が出ている');
      if (!document.querySelector('.minibar .mk') || document.querySelector('.minibar .mk').textContent !== 'hobby baseball') ng.push('小さい見出しのサイト名が違う');
      return ng;
    }""")
    for m in r:
        bad(f"[サイト名・アイコン] {m}")
    await pg.close()
    # 演出が出て、終わると消えるか（自動の検査ではない状態をまねる）
    # 「視差効果を減らす」がオンでも出す
    pg = await browser.new_page(viewport={"width": 390, "height": 844}, reduced_motion="reduce")
    await pg.add_init_script("Object.defineProperty(navigator, 'webdriver', { get: () => false })")
    await pg.goto(URL)
    await pg.wait_for_timeout(200)
    if not await pg.evaluate("!!document.getElementById('splash')"):
        bad("[開いたときの演出] 演出が出ない")
    src = await pg.evaluate("(document.querySelector('#splash .sp-icon') || {}).getAttribute ? document.querySelector('#splash .sp-icon').getAttribute('src') : ''")
    if "?v=" not in (src or ""):
        bad(f"[開いたときの演出] 演出の絵に中身の目印（?v=）がない（前の絵が出てしまう）：{src}")
    await pg.wait_for_timeout(2000)
    if await pg.evaluate("!!document.getElementById('splash') || document.documentElement.classList.contains('splashing')"):
        bad("[開いたときの演出] 演出が終わっても消えない")
    await pg.close()


async def pre_game_check(browser):
    """今日の試合：放送予定（テレビ・ネット・ラジオ）と、発表されたスタメン（打順・守備・打率・先発）。はみ出さないか"""
    for theme in ["", "pawa"]:
        for width in [320, 390]:
            label = f"[放送予定・スタメン {'パワプロ風' if theme else 'スタイリッシュ'} 幅{width}]"
            pg, errs = await open_page(browser, width, theme)
            r = await pg.evaluate("""() => {
              const ng = [], d = jst().iso;
              let g = DATA.games.find(x => x.d === d);
              if (!g) { g = { d, h: CL[0], a: CL[1], st: 'sched', t: '18:00', v: '', hs: 0, as: 0 }; DATA.games.push(g); }
              g.st = 'sched';
              const nine = t => (DATA.rosters[t] || []).filter(x => x.p !== '投手').slice(0, 9).map((x, i) => ({ o: i + 1, pos: ['中','二','三','一','左','右','捕','遊','投'][i], n: x.n, ba: '右', avg: '.25' + i }));
              const pit = t => ({ n: ((DATA.rosters[t] || []).find(x => x.p === '投手') || {}).n || 'テスト', th: '右', era: '2.50' });
              const bench = t => { const ro = DATA.rosters[t] || []; const pk = (p, k) => ro.filter(x => x.p === p).slice(0, k).map(x => ({ n: x.n, bt: '右右', st: '.250' }));
                return { '投手': pk('投手', 8), '捕手': pk('捕手', 2), '内野手': pk('内野手', 4), '外野手': pk('外野手', 3) }; };
              PRE = {}; PRE[`${g.d}|${g.h}|${g.a}`] = { tv: 'サンテレビ1、GAORA SPORTS', net: 'DAZN、虎テレ', radio: 'MBSラジオ、ABCラジオ',
                lu: { h: { p: pit(g.h), bat: nine(g.h) }, a: { p: pit(g.a), bat: nine(g.a) } }, bench: { h: bench(g.h), a: bench(g.a) } };
              S.stmOpen = {}; S.stmOpen['b|' + g.d + gkey(g)] = true;   // ベンチ入りを開いた状態で
              setTab('game'); renderGame();
              const card = [...document.querySelectorAll('#today .tg')].find(c => c.querySelector('.stm'));
              if (!card) return ['スタメンが出ない'];
              if (card.querySelectorAll('.stm:not(.bench) .stl li').length !== 18) ng.push(`打順の数が違う（${card.querySelectorAll('.stm:not(.bench) .stl li').length}）`);
              if (!card.querySelector('.stm.bench .stl li')) ng.push('ベンチ入りが出ない');
              // ホームのチームは左
              for (const st of card.querySelectorAll('.stm')) {
                const first = st.querySelector('.stc .sth');
                if (!first || !first.textContent.includes(fn(g.h))) ng.push(`${st.classList.contains('bench') ? 'ベンチ入り' : 'スタメン'}の左がホームのチームになっていない`);
              }
              if (card.querySelectorAll('.stpit').length !== 2) ng.push('先発投手が出ない');
              if (card.querySelectorAll('.bc .bcr').length !== 3) ng.push('テレビ・ネット・ラジオが出ない');
              if (card.querySelector('.yk')) ng.push('スタメンが出ているのに予告先発の行も出ている');
              // パワプロ風：名前の枠は全員同じ幅。長い名前も枠からはみ出さない
              const tw = [...card.querySelectorAll('.stm .stnm .sptile')].map(e => Math.round(e.getBoundingClientRect().width));
              if (tw.length && Math.max(...tw) - Math.min(...tw) > 1) ng.push(`名前の枠の大きさがそろっていない（${Math.min(...tw)}〜${Math.max(...tw)}）`);
              const cut = [...card.querySelectorAll('.stm .stnm .sptile b')].filter(b => b.scrollWidth > b.clientWidth + 1).length;
              if (cut) ng.push(`名前が枠に収まっていない（${cut}人：${[...card.querySelectorAll('.stm .stnm .sptile b')].filter(b => b.scrollWidth > b.clientWidth + 1).slice(0, 3).map(b => b.textContent + ' ' + b.scrollWidth + '/' + b.clientWidth + ' ' + getComputedStyle(b).fontSize).join('、')}）`);
              const over = [...card.querySelectorAll('.stm *, .bc *')].filter(e => e.getBoundingClientRect().right > innerWidth + 1).length;
              if (over) ng.push(`スタメン・放送予定が画面の外にはみ出す（${over}）`);
              // 試合が始まったら：スタメンは出さず（打順は速報で見る）、放送予定だけ
              g.st = 'live'; g.inn = '1回表'; renderGame();
              const c2 = [...document.querySelectorAll('#today .tg')].find(c => c.querySelector('.bc'));
              if (!c2) ng.push('試合中に放送予定が出ない');
              if (document.querySelector('#today .stm')) ng.push('試合が始まってもスタメン発表が出ている');
              g.st = 'sched'; PRE = {}; renderGame();
              return ng;
            }""")
            for m in r:
                bad(f"{label} {m}")
            for e in errs:
                bad(f"{label}: 画面のエラー {e}")
            await pg.close()


async def player_head_check(browser):
    """選手の画面の見出し：長い名前（外国人選手）でも名前の枠・「中継ぎ」などの丸がはみ出さない・改行しない"""
    for theme in ["", "pawa"]:
        for width in [320, 390]:
            label = f"[選手の画面の見出し {'パワプロ風' if theme else 'スタイリッシュ'} 幅{width}]"
            pg, errs = await open_page(browser, width, theme)
            r = await pg.evaluate("""() => { const ng = [];
              const t = CL[0], ro = (DATA.rosters[t] || []).find(x => x.p === '投手');
              for (const n of ['デュプランティエ', 'カスティーヨ', ro ? ro.n : 'テスト']) {
                DATA.offseason = { season: 2026, items: [{ t, n, no: '140', kind: 'cut', role: '中', date: '2026-09-29', url: '', title: '' }], teams: {}, seen: {} };
                openPlayer(t, n, { kind: 'pit' });
                const h = document.querySelector('.sp-h'); if (!h) { ng.push('見出しが出ない'); continue; }
                const R = h.getBoundingClientRect().right;
                if ([...h.querySelectorAll('*')].some(e => e.getBoundingClientRect().right > R + 1)) ng.push(`${n}：見出しからはみ出す`);
                if ([...h.querySelectorAll('.ps-pos')].some(e => e.getBoundingClientRect().height > 26)) ng.push(`${n}：守備・役割の丸が改行している`);
                const tile = h.querySelector('.pstile'), b = tile && tile.querySelector('b');
                if (b && b.scrollWidth > tile.clientWidth + 1) ng.push(`${n}：名前が枠からはみ出す`);
              }
              DATA.offseason = null; return ng; }""")
            for m in r:
                bad(f"{label} {m}")
            for e in errs:
                bad(f"{label}: 画面のエラー {e}")
            await pg.close()


async def uniform_check(browser):
    """同じ種類の部品は、どの画面でも同じ見た目か（パワプロ風）
    名前の札：字間（2文字以下 .7em・3〜4文字 .08em・5〜6文字 0・7文字以上 -.02em）・太さ・色がそろう。一覧の札は高さ34px・文字14px（5文字以上は小さく）
    切り替えボタン：文字14px。打席の結果の札：同じ大きさ"""
    for width in [390, 320]:
        label = f"[見た目の統一 パワプロ風 幅{width}]"
        pg, errs = await open_page(browser, width, "pawa")
        ng = []
        for tab in ["magic", "game", "cal", "std", "stats", "rec", "song", "off"]:
            await pg.evaluate(f"setTab('{tab}')")
            await pg.wait_for_timeout(700)
            if tab == "game":
                await pg.evaluate("""() => { const t = CL[0], o = CL[1]; const g = DATA.games.find(x => x.h === t && x.a === o) || DATA.games.find(x => x.h === t);
                  const today = g.d; jst = () => ({ y: +today.slice(0, 4), m: +today.slice(5, 7), d: +today.slice(8), iso: today }); liveWanted = () => false;
                  Object.assign(g, { st: 'live', hs: 3, as: 2, inn: '4回裏' }); S.open[g.d + gkey(g)] = true; renderAll(); setTab('game'); }""")
                await pg.wait_for_timeout(1200)
            if tab == "off":
                await pg.evaluate("""() => { const t = CL[0], ro = DATA.rosters[t];
                  DATA.offseason = { season: 2026, items: ro.slice(0, 10).map(x => ({ t, n: x.n, no: x.no, kind: 'cut', date: '2026-09-29', url: '', title: '' })), teams: {}, seen: {} }; renderAll(); setTab('off'); }""")
                await pg.wait_for_timeout(700)
            ng += await pg.evaluate("""(tab) => { const ng = [];
              for (const b of document.querySelectorAll('.ptile:not(.posic):not(.pschip) b')) { const tile = b.closest('.ptile'), r = tile.getBoundingClientRect(); if (!r.width) continue;   // 守備位置のアイコンは名前ではない
                const cs = getComputedStyle(b), n = [...b.textContent.replace(/\\s/g, '')].length, fs = parseFloat(cs.fontSize), ls = (parseFloat(cs.letterSpacing) || 0) / fs;
                const want = n <= 2 ? .7 : n <= 4 ? .08 : n <= 6 ? 0 : -.02;
                if (Math.abs(ls - want) > .015) ng.push(`${tab}：名前の札の字間がほかと違う（${b.textContent} ${ls.toFixed(2)}em）`);
                if (cs.color !== 'rgb(31, 42, 68)' || cs.fontWeight !== '700') ng.push(`${tab}：名前の札の文字の色・太さがほかと違う（${b.textContent}）`);
                if (tile.closest('#rankList, #songList, #offList, #ptTbl, .sp-h, .pn3')) {
                  if (Math.round(r.height) < 34) ng.push(`${tab}：一覧の名前の札の高さがほかと違う（${b.textContent} ${Math.round(r.height)}px）`);
                  if (n <= 4 && fs !== 14) ng.push(`${tab}：一覧の名前の札の文字の大きさがほかと違う（${b.textContent} ${fs}px）`); } }
              // 見出しの更新日：成績タブの3つの見出し（チーム成績・個人ランキング・チーム別成績）すべてに出す
              if (tab === 'stats') for (const id of ['stAsof', 'rkAsof']) { const e = document.getElementById(id); if (!e || !/\\d+\\/\\d+/.test(e.textContent)) ng.push(`成績タブの見出しに更新日がない（${id}）`); }
              for (const e of document.querySelectorAll('.chip, .chips2 button, .seg button')) if (e.getBoundingClientRect().width && getComputedStyle(e).fontSize !== '14px') ng.push(`${tab}：切り替えボタンの文字の大きさがほかと違う（${e.textContent.trim()}）`);
              const rcs = [...document.querySelectorAll('.rc')].filter(e => e.getBoundingClientRect().width).map(e => getComputedStyle(e).fontSize + '/' + Math.round(e.getBoundingClientRect().height));
              if (new Set(rcs).size > 1) ng.push(`${tab}：打席の結果の札の大きさがそろっていない（${[...new Set(rcs)].join('・')}）`);
              return ng; }""", tab)
        for m in list(dict.fromkeys(ng))[:8]:
            bad(f"{label} {m}")
        for e in errs:
            bad(f"{label}: 画面のエラー {e}")
        await pg.close()


async def default_theme_check(browser):
    """まだテーマを選んでいない人はパワプロ風。スタイリッシュを選んだ人はスタイリッシュのまま。LINEの画像もテーマに合わせる"""
    ctx = await browser.new_context(viewport={"width": 390, "height": 844})
    pg = await ctx.new_page()
    await pg.add_init_script("Object.defineProperty(navigator, 'webdriver', { get: () => true }); if (!sessionStorage.getItem('x')) { localStorage.clear(); sessionStorage.setItem('x', '1'); }")
    await pg.goto(f"file://{ROOT}/index.html"); await pg.wait_for_timeout(700)
    if not await pg.evaluate("document.documentElement.classList.contains('theme-pawa')"):
        bad("[最初のテーマ] まだ選んでいない人がパワプロ風になっていない")
    px = await pg.evaluate("(() => { const c = drawCard(analyze(DATA.games, S.period, CONFIG)); const d = c.getContext('2d').getImageData(10, 10, 1, 1).data; return [d[0], d[1], d[2]]; })()")
    if not (px[2] > 180 and px[0] < 120):
        bad(f"[LINEの画像] パワプロ風のとき空色の画像になっていない（左上の色 {px}）")
    await pg.evaluate("localStorage.setItem('theme', ''); localStorage.setItem('mode', 'dark')")   # スタイリッシュ（ダーク）
    await pg.wait_for_timeout(300)
    await pg.reload(wait_until="load"); await pg.wait_for_timeout(1200)
    if await pg.evaluate("localStorage.getItem('theme')") != "":   # 重いときに保存が間に合わないことがあるので、もう一度
        await pg.evaluate("localStorage.setItem('theme', ''); localStorage.setItem('mode', 'dark')"); await pg.wait_for_timeout(300)
        await pg.reload(wait_until="load"); await pg.wait_for_timeout(1200)
    if await pg.evaluate("document.documentElement.classList.contains('theme-pawa')"):
        bad("[最初のテーマ] スタイリッシュを選んだ人がパワプロ風に戻っている")
    px = await pg.evaluate("(() => { const c = drawCard(analyze(DATA.games, S.period, CONFIG)); const d = c.getContext('2d').getImageData(10, 10, 1, 1).data; return [d[0], d[1], d[2]]; })()")
    if max(px) > 40:
        bad(f"[LINEの画像] スタイリッシュのとき黒い画像になっていない（左上の色 {px}）")
    await ctx.close()


async def live_off_check(browser):
    """オフのタブ：GitHub の自動更新を待たずに、スポナビの入退団情報（中継プログラム経由）で入っていない選手を足す"""
    pg, errs = await open_page(browser, 390, "pawa")
    r = await pg.evaluate("""() => { const ng = [], t = CL[0], ro = DATA.rosters[t] || [];
      DATA.offseason = { season: 2026, items: [{ t, n: ro[0].n, no: ro[0].no, kind: 'cut', date: '2026-09-29', url: 'x', title: '' }], teams: {}, seen: {} };
      LIVE_OFF.items = [{ t, n: ro[0].n, kind: 'cut', date: '2026-09-29' }, { t, n: ro[1].n, kind: 'cut', date: '2026-09-30' }, { t, n: 'テスト 退団', kind: 'leave', date: '2026-09-30' },
        { t, n: ro[1].n.replace(/\\s+/g, ''), kind: 'cut', date: '2026-09-30' }, { t, n: ro[1].n, kind: 'cut', date: '2026-09-30' }, { t, n: 'テスト退団', kind: 'leave', date: '2026-09-30' }];   // 同じ選手が何度も（空白あり・なし）
      LIVE_OFF.checked = new Date().toISOString();
      const it = offItems().filter(x => x.t === t);
      if (it.length !== 3) ng.push(`入退団情報の最新が足されていない（${it.length}人）`);
      if (it.filter(x => x.n === ro[0].n).length !== 1) ng.push('同じ選手が2回入っている');
      if (!it.some(x => x.kind === 'leave')) ng.push('「退団」が入っていない');
      // 外国人の名前の書き方の違い（「F・グズマン」と「グズマン」）は同じ人。育成から支配下登録も出す
      DATA.offseason.items.push({ t, n: 'グズマン', no: '042', dev: true, kind: 'cut', date: '2026-10-01', url: 'x' }, { t, n: 'F・グズマン', kind: 'cut', date: '2026-10-01', src: 'sponavi' },
        { t, n: ro[2].n, no: ro[2].no, kind: 'promote', date: '2026-07-25', url: 'y' });
      LIVE_OFF.items.push({ t, n: 'F・グズマン', kind: 'cut', date: '2026-10-01', src: 'bbc' });
      const gz = offItems().filter(x => x.t === t && /グズマン/.test(x.n));
      if (gz.length !== 1) ng.push(`「F・グズマン」と「グズマン」が別々に出ている（${gz.length}件）`);
      else if (gz[0].no !== '042') ng.push('同じ人をまとめたとき、背番号のある方が残っていない');
      if (!offItems().some(x => x.kind === 'promote')) ng.push('育成から支配下登録が出ない');
      jst = () => ({ y: 2026, m: 10, d: 1, iso: '2026-10-01' }); renderAll(); setTab('off'); renderOff();
      document.querySelectorAll('#offList .ofteam').forEach(e => (e.style.contentVisibility = 'visible'));   // 画面の外は並べるのを後回しにしているので、読む前に並べる
      if (!/育成から支配下登録/.test(document.getElementById('offList').innerText)) ng.push('オフの一覧に「育成から支配下登録」が出ない');
      const txt = document.getElementById('offList').innerText;
      if (!txt.includes('退団')) ng.push('オフの一覧に「退団」が出ない');
      LIVE_OFF.items = []; return ng; }""")
    for m in r:
        bad(f"[オフの最新] {m}")
    for e in errs:
        bad(f"[オフの最新]: 画面のエラー {e}")
    await pg.close()


async def off_pos_check(browser):
    """オフの一覧の名前の札の色＝選手の画面の札の色＝名簿の守備位置（「A・マルティネス」など頭文字つきの名前も名簿の選手として扱う）"""
    pg, errs = await open_page(browser, 390, "pawa")
    for lg in ["C", "P"]:
        r = await pg.evaluate("""async (lg) => { const ng = [];
          switchLeague(lg); jst = () => ({ y: 2026, m: 10, d: 2, iso: '2026-10-02' });
          // 頭文字つきの名前（ベースボールチャンネルの書き方）を、名簿の外国人選手の各守備位置で1人ずつ足す
          const want = { '投手': ['pp', 'ps'], '捕手': ['pc'], '内野手': ['pi'], '外野手': ['po'] };
          for (const t of CL) for (const pos of Object.keys(want)) {
            const ro = (DATA.rosters[t] || []).find(r => r.p === pos && /^[ァ-ヴー]+$/.test(r.n));
            if (ro && !offItems().some(x => x.t === t && nkOff(x.n) === nkOff(ro.n))) (DATA.offseason.items = DATA.offseason.items || []).push({ t, n: 'Z・' + ro.n, no: '', dev: !!ro.dev, kind: 'cut', date: '2026-10-01', url: 'https://www.baseballchannel.jp/npb/291604/', title: 'x', src: 'bbc' });
          }
          setTab('off'); S.offCat = 'all'; renderOff();
          const rows = [...document.querySelectorAll('#offList .onm[data-pl]')];
          if (!rows.length) ng.push('オフの一覧に選手がいない');
          const col = el => { const c = [...el.classList].find(k => ['pp', 'ps', 'pc', 'pi', 'po'].includes(k)); return c + '|' + (el.getAttribute('style') || ''); };
          for (const el of rows) {
            const [t, n] = el.dataset.pl.split('|'), tile = el.querySelector('.ptile');
            if (!tile) { ng.push(`${n}：名前の札がない`); continue; }
            const ro = rosterOf(t, n);
            if (/^[A-Za-z]{1,2}[・.．]/.test(n.normalize('NFKC')) && ro) ng.push(`${n}：名簿の名前（${ro.n}）にそろっていない`);
            const row = el.closest('.ofr');
            if (ro && ro.no && !row.querySelector('.ono')) ng.push(`${n}：名簿に背番号があるのに出ていない`);
            const first = [...tile.classList].find(k => ['pp', 'ps', 'pc', 'pi', 'po'].includes(k));
            if (ro && want[ro.p] && !want[ro.p].includes(first)) ng.push(`${n}：札の色が名簿の守備位置（${ro.p}）と違う（${first}）`);
            await openPlayer(t, n);
            const st = document.querySelector('#songPick .ptile');
            if (!st) ng.push(`${n}：選手の画面に名前の札がない`);
            else if (col(st).split('|')[0] !== col(tile).split('|')[0] || col(st).split('|')[1] !== col(tile).split('|')[1]) ng.push(`${n}：一覧と選手の画面で札の色が違う（${col(tile)} / ${col(st)}）`);
            const chip = document.querySelector('#songPick .ps-poss i');
            if (ro && chip && ro.p !== '投手' && chip.textContent !== ro.p) ng.push(`${n}：選手の画面の守備位置が名簿（${ro.p}）と違う（${chip.textContent}）`);
            if (ro && chip && ro.p === '投手' && !['投手', '先発', '中継ぎ', '抑え'].includes(chip.textContent)) ng.push(`${n}：選手の画面で投手なのに「${chip.textContent}」`);
          }
          document.getElementById('songSheet').classList.remove('open'); document.getElementById('songSheet').hidden = true;
          return ng; }""", lg)
        for m in r[:30]:
            bad(f"[オフの札の色 {lg}] {m}")
    for e in errs:
        bad(f"[オフの札の色]: 画面のエラー {e}")
    await pg.close()


async def promote_check(browser):
    """入退団のタブ：タブの名前は「入退団」。育成から支配下登録は自分の見出しの下に（退団の中に混ぜない）。退団ではないので選手の札は付けない"""
    pg, errs = await open_page(browser, 390, "pawa")
    r = await pg.evaluate("""() => { const ng = [], t = CL.find(x => (DATA.rosters[x] || []).length >= 3), ro = DATA.rosters[t];
      DATA.offseason = { season: 2026, checked_at: new Date().toISOString(), teams: {}, seen: {}, items: [
        { t, n: ro[0].n, no: ro[0].no, dev: false, kind: 'promote', date: '2026-03-11', pos: ro[0].p, no_dev: '128', url: 'https://npb.jp/announcement/2026/pn_registered.html', title: 'x' },
        { t, n: ro[1].n, no: ro[1].no, dev: false, kind: 'cut', date: '2026-09-29', url: 'u', title: 'x' }] };
      jst = () => ({ y: 2026, m: 10, d: 2, iso: '2026-10-02' }); renderAll(); setTab('off'); S.offCat = 'all'; renderOff();
      const tl = document.querySelector('#subNav button[data-p="off"]');
      if (!tl || tl.textContent !== '入退団') ng.push(`切り替えの名前が「入退団」でない（${tl && tl.textContent}）`);
      if (/オフ/.test(document.getElementById('offHead').textContent)) ng.push('見出しに「オフ」が残っている');
      const box = [...document.querySelectorAll('#offList .ofteam')].find(e => e.querySelector('.ofh').textContent.includes(fn(t)));
      if (!box) { ng.push('球団の欄がない'); return ng; }
      const secs = [...box.querySelectorAll('.ofsec')].map(e => e.textContent);
      if (!secs.includes('育成から支配下登録')) ng.push(`「育成から支配下登録」の見出しがない（${secs}）`);
      // 並び：退団の見出しのあと、支配下登録の見出しの下に支配下の選手
      const kids = [...box.children], iP = kids.findIndex(e => e.classList.contains('ofsec') && e.textContent === '育成から支配下登録');
      const promoRow = kids.findIndex(e => e.classList.contains('ofr') && e.querySelector('.k-promote'));
      if (promoRow < iP) ng.push('支配下登録の選手が「退団」の中に入っている');
      if (!/128 → /.test(box.textContent)) ng.push('育成のときの背番号から新しい背番号への変化が出ない');
      if (offOf(t, ro[0].n)) ng.push('支配下登録の選手に、退団の札（offTag）が付いてしまう');
      if (!offOf(t, ro[1].n)) ng.push('戦力外の選手の札が出なくなった');
      // 絞り込みのボタンの並び：出ていく人 → 移る人 → 入ってくる人 → 首脳陣
      const ORDER = ['すべて', '戦力外', '引退', 'トレード', 'FA', 'そのほかの移籍', 'ドラフト', '新外国人', '支配下登録', '首脳陣'];
      const shown = [...document.querySelectorAll('#offCats button')].map(b => b.textContent);
      if (shown.join() !== ORDER.filter(x => shown.includes(x)).join()) ng.push(`絞り込みのボタンの並びが違う（${shown}）`);
      S.offCat = 'promote'; renderOff();
      if (!document.querySelector('#offList .k-promote')) ng.push('「支配下登録」で絞り込むと出ない');
      return ng; }""")
    for m in r:
        bad(f"[入退団・支配下登録] {m}")
    for e in errs:
        bad(f"[入退団・支配下登録]: 画面のエラー {e}")
    await pg.close()


async def tab_lens_check(browser):
    """開いているタブの文字とアイコンが、その下の実際の地（レンズ）の上で読めるか（4つの見た目すべて・画面の色を実際に読む）"""
    import io
    from PIL import Image
    def lum(c):
        f = lambda x: x / 12.92 if x <= .03928 else ((x + .055) / 1.055) ** 2.4
        r, g, b = [v / 255 for v in c[:3]]
        return .2126 * f(r) + .7152 * f(g) + .0722 * f(b)
    def ratio(a, b):
        la, lb = lum(a), lum(b)
        return (max(la, lb) + .05) / (min(la, lb) + .05)
    for theme, scheme, label in [("pawa", "light", "パワプロ風（昼）"), ("pawa", "dark", "パワプロ風（夜）"), ("", "dark", "スタイリッシュ（黒）"), ("", "light", "スタイリッシュ（白）")]:
        ctx = await browser.new_context(viewport={"width": 390, "height": 844}, color_scheme=scheme, device_scale_factor=2)
        pg = await ctx.new_page()
        await pg.add_init_script(f"localStorage.setItem('me','S'); localStorage.setItem('theme','{theme}'); localStorage.setItem('mode','auto'); localStorage.setItem('league','C')")
        await pg.goto(URL); await pg.wait_for_timeout(1000)
        for tab in ["magic", "song"]:
            await pg.evaluate(f"setTab('{tab}')"); await pg.wait_for_timeout(900)
            fg = await pg.evaluate("""() => { const b = document.querySelector('.tabbar button[aria-selected="true"]'); const p = s => getComputedStyle(s).color.match(/\\d+/g).map(Number);
              const r = e => { const x = e.getBoundingClientRect(); return [x.left, x.top, x.width, x.height]; };
              return { tl: p(b.querySelector('.tl')), sv: p(b.querySelector('svg')), rt: r(b.querySelector('.tl')), rs: r(b.querySelector('svg')) }; }""")
            await pg.add_style_tag(content=".tabbar button .tl,.tabbar button svg{visibility:hidden !important}")
            await pg.wait_for_timeout(150)
            img = Image.open(io.BytesIO(await pg.screenshot())).convert("RGB")
            for key, rect, need, what in [("tl", "rt", 4.5, "文字"), ("sv", "rs", 3.0, "アイコン")]:
                x, y, w, h = fg[rect]
                px = [img.getpixel((int((x + w * i / 6) * 2), int((y + h * j / 4) * 2))) for i in range(1, 6) for j in range(1, 4)]
                worst = min(ratio(fg[key], c) for c in px)
                if worst < need:
                    bad(f"[開いているタブ {label} {tab}] {what}が下の地に埋もれる（比 {worst:.2f}、必要 {need}）")
            await pg.evaluate("document.querySelectorAll('style').forEach(s => { if (s.textContent.includes('visibility:hidden !important') && s.textContent.includes('.tabbar button .tl')) s.remove(); })")
        await ctx.close()


async def fast_start_check(browser):
    """起動の速さ：開いたタブだけすぐ描き、ほかのタブは後で描く。まだ描いていないタブも、押した瞬間に描いてから出す。
    データが変わったら全部のタブが描き直される。タブの切り替えは透明から始めない（押した瞬間に中身が見える）"""
    pg, errs = await open_page(browser, 390, "pawa")
    r = await pg.evaluate("""async () => { const ng = [];
      // 1) データが変わったとき：開いているタブはすぐ、ほかは後で（押したら必ず最新）
      setTab('magic'); const before = document.getElementById('today').innerHTML;
      DATA = JSON.parse(JSON.stringify(DATA)); DATA.games.filter(g => g.d === jst().iso).forEach(g => { g.st = 'final'; g.hs = 9; g.as = 8; });
      renderAll();
      if (!TAB_DIRTY.has('song') && !TAB_DIRTY.has('game') && !TAB_DIRTY.has('std')) { /* 手が空いて全部描き終わっていてもよい */ }
      setTab('game');
      if (TAB_DIRTY.has('game')) ng.push('試合タブを押しても描かれない');
      const got = document.getElementById('today').innerHTML;
      if (DATA.games.some(g => g.d === jst().iso) && got === before) ng.push('データが変わったのに、試合タブが前のまま');
      // 2) 手が空けば全部描き終わる
      renderAll(); await new Promise(r => setTimeout(r, 2500));
      if (TAB_DIRTY.size) ng.push(`手が空いても描き終わらないタブがある（${[...TAB_DIRTY]}）`);
      // 3) 切り替えの動きは透明から始めない
      const kf = [...document.styleSheets].flatMap(s => { try { return [...s.cssRules]; } catch { return []; } }).filter(r => r.type === CSSRule.KEYFRAMES_RULE && (r.name === 'vin' || r.name === 'vin2'));
      for (const k of kf) for (const f of k.cssRules) if (/opacity\\s*:\\s*0(\\.0*)?\\s*(;|$)/.test(f.style.cssText)) ng.push(`タブの切り替え（${k.name}）が透明から始まる`);
      // 4) 予告先発・放送とスタメンを端末に覚えていて、開き直したときすぐ出せる
      keepLive('pre-v1', { 'x|T|G': { tv: 'テスト' } });
      if (!restoreLive('pre-v1')['x|T|G']) ng.push('放送とスタメンを端末に覚えていない');
      return ng; }""")
    for m in r:
        bad(f"[起動の速さ] {m}")
    for e in errs:
        bad(f"[起動の速さ]: 画面のエラー {e}")
    await pg.close()


async def pos_icon_check(browser):
    """守備位置のアイコン（パワプロ風）：名前の札と同じ札（同じ字・同じ文字の色・同じ枠と色）を小さな四角にしたもの。
    色の種類は名前の札と同じ。並びはパワプロのオーダー画面のように名前の右（スタメン・打順の表）。選手の画面の守備位置も同じ札"""
    for scheme in ["light", "dark"]:
        ctx = await browser.new_context(viewport={"width": 390, "height": 844}, color_scheme=scheme)
        pg = await ctx.new_page()
        await pg.add_init_script("localStorage.setItem('me','S'); localStorage.setItem('theme','pawa'); localStorage.setItem('mode','auto'); localStorage.setItem('league','C')")
        await pg.route(LIVE + "**", route_live)
        await pg.goto(URL); await pg.wait_for_timeout(800)
        r = await pg.evaluate("""() => { const ng = [];
          const want = { '投': 'pp', '捕': 'pc', '一': 'pi', '二': 'pi', '三': 'pi', '遊': 'pi', '左': 'po', '中': 'po', '右': 'po', '打': 'pnu', '走': 'pnu', '打左': 'po' };
          const g = { d: '2026-10-02', h: 'S', a: 'G', st: 'final', hs: 1, as: 0 }, ro = DATA.rosters.G.filter(r => !r.dev);
          const pick = p => ro.find(r => r.p === p) || ro[0];
          const rows = Object.keys(want).map((p, i) => ({ order: Math.min(i + 1, 9), pos: p, starter: i < 9, name: pick({ '投': '投手', '捕': '捕手', '左': '外野手', '中': '外野手', '右': '外野手' }[p] || '内野手').n, avg: '.250', results: [] }));
          const lu = document.createElement('div'); lu.innerHTML = lineupHTML(g, { lineups: [rows, rows] });
          document.getElementById('v-magic').prepend(lu);
          [...lu.querySelectorAll('.lnm')].forEach((m, i) => {
            const p = Object.keys(want)[i], nm = m.querySelector('.ptile:not(.posic)'), ic = m.querySelector('.posic');
            if (!ic) { ng.push(`「${p}」のアイコンがない`); return; }
            if (!ic.classList.contains('ptile') || !ic.classList.contains(want[p])) ng.push(`「${p}」が名前と同じ札でない・色の種類が違う（${ic.className}）`);
            if (nm.compareDocumentPosition(ic) !== Node.DOCUMENT_POSITION_FOLLOWING || ic.getBoundingClientRect().left < nm.getBoundingClientRect().right - 1) ng.push(`打順の表：「${p}」が名前の右にない`);
            const a = getComputedStyle(ic), b = getComputedStyle(nm), ab = getComputedStyle(ic.querySelector('b')), bb = getComputedStyle(nm.querySelector('b'));
            if (ab.fontFamily !== bb.fontFamily || ab.fontWeight !== bb.fontWeight || ab.color !== bb.color) ng.push(`「${p}」の字が名前と違う（${ab.fontWeight} ${ab.color} / ${bb.fontWeight} ${bb.color}）`);
            if (a.borderTopWidth !== b.borderTopWidth || a.borderTopStyle !== b.borderTopStyle || a.boxShadow !== b.boxShadow) ng.push(`「${p}」の枠が名前の札と違う`);
            if (Math.abs(ic.getBoundingClientRect().height - nm.getBoundingClientRect().height) > 1) ng.push(`「${p}」の高さが名前の札と違う`);
            if (ic.scrollWidth > ic.clientWidth + 1) ng.push(`「${p}」の字が札からはみ出す`);
          });
          // 同じ色の種類なら、名前の札とまったく同じ色（地・枠）
          const same = (cls) => { const t = document.createElement('div'); t.innerHTML = `<span class="ptile sptile ${cls}"><b>名前</b></span>` + posIcon(cls === 'pc' ? '捕' : cls === 'pi' ? '遊' : cls === 'po' ? '左' : '投'); lu.append(t);
            const [x, y] = t.children; const cx = getComputedStyle(x), cy = getComputedStyle(y);
            if (cx.backgroundImage !== cy.backgroundImage || cx.borderTopColor !== cy.borderTopColor) ng.push(`${cls}：アイコンの色が名前の札と違う`); };
          ['pp', 'pc', 'pi', 'po'].forEach(same);
          if (lu.querySelector('td.lp')) ng.push('打順の表に、名前の左の守備位置の列が残っている');
          lu.remove();
          // 今の打者の印（▶）は行の頭に1つだけ。盗塁成功の札。「三併打」は凡打の色
          { const gg = { d: '2026-10-02', h: 'S', a: 'G', st: 'live', hs: 0, as: 0 }, k = gg.d + gkey(gg), atkName = Object.keys(YSHORT).find(x => YSHORT[x] === 'G');
            PD[k] = { half: '1回表', attack: atkName, batter: { name: ro[0].n } };
            const rr = [{ order: 1, pos: '中', starter: true, name: ro[0].n, avg: '.250', results: ['三併打', '左安'], sb: '1' }, { order: 2, pos: '走', starter: false, name: ro[1].n, avg: '.250', results: [], sb: '2' }];
            const saveLu = S.lu[k]; S.lu[k] = 'G';
            const w = document.createElement('div'); w.innerHTML = lineupHTML(gg, { lineups: [rr, rr] }); document.getElementById('v-magic').prepend(w);
            const cur = w.querySelector('tr.cur');
            if (!cur) ng.push('今の打者の行が出ない');
            else { const marks = [...cur.querySelectorAll('*')].filter(e => getComputedStyle(e, '::before').content.includes('▶')).length;
              if (marks !== 1) ng.push(`今の打者の印（▶）が${marks}個（1個だけのはず）`);
              if (getComputedStyle(cur.querySelector('.posic'), '::before').content.includes('▶') || getComputedStyle(cur.querySelector('.lnm .ptile:not(.posic)'), '::before').content.includes('▶')) ng.push('名前・守備位置の札の中に▶が付いている'); }
            const sbs = [...w.querySelectorAll('tr')].map(t => t.querySelectorAll('.rc.sb').length);
            if (sbs.join() !== '1,2') ng.push(`盗塁成功の札の数が違う（${sbs}）`);
            if (![...w.querySelectorAll('.rc.sb')].every(e => e.textContent === '盗塁成功')) ng.push('盗塁成功の札の文字が違う');
            const dp = [...w.querySelectorAll('.rc')].find(e => e.textContent === '三併打');
            if (!dp || !dp.classList.contains('o')) ng.push('「三併打」が凡打の色になっていない');
            const leg = document.createElement('div'); leg.innerHTML = RES_LEGEND; if (![...leg.querySelectorAll('.sb')].length) ng.push('見方に盗塁成功がない');
            w.remove(); delete PD[k]; if (saveLu === undefined) delete S.lu[k]; else S.lu[k] = saveLu; }
          // スタメン
          const so = DATA.rosters.S;
          PRE['2026-10-02|S|G'] = { lu: { h: { bat: so.slice(0, 2).map((r, i) => ({ o: i + 1, pos: '三', n: r.n, avg: '.3' })) }, a: { bat: [] } } };
          const pre = document.createElement('div'); pre.innerHTML = preHTML({ d: '2026-10-02', h: 'S', a: 'G', st: 'sched' }); document.getElementById('v-magic').prepend(pre);
          const lis = pre.querySelectorAll('.stl li'); if (!lis.length) ng.push('スタメンの試しの表示が出ない');
          lis.forEach(li => { const nm = li.querySelector('.stnm .ptile'), ic = li.querySelector('.posic');
            if (!nm || !ic || !ic.classList.contains('ptile') || ic.getBoundingClientRect().left < nm.getBoundingClientRect().right - 1) ng.push('スタメン：守備位置が名前の右の札になっていない');
            else if (Math.abs(ic.getBoundingClientRect().height - nm.getBoundingClientRect().height) > 1) ng.push('スタメン：守備位置の高さが名前の札と違う'); });
          pre.remove(); delete PRE['2026-10-02|S|G'];
          // 選手の画面の守備位置も名前と同じ札
          const n = DATA.rosters.S.find(r => r.p === '捕手').n; openPlayer('S', n);
          const c = document.querySelector('#songPick .ps-poss .ptile');
          if (!c || !c.classList.contains('pc')) ng.push(`選手の画面の守備位置が名前と同じ札でない（${c && c.className}）`);
          return ng; }""")
        for m in r[:20]:
            bad(f"[守備位置のアイコン {'夜' if scheme == 'dark' else '昼'}] {m}")
        await ctx.close()


async def pitch_align_check(browser):
    """投手成績の表：今の投手の▶・勝敗の印があっても、名前（札）の位置はどの行も同じ。▶は1つだけ"""
    for th in ["pawa", ""]:
        pg, errs = await open_page(browser, 390, th)
        r = await pg.evaluate("""() => { const ng = [];
          const gg = { d: '2026-10-02', h: 'S', a: 'G', st: 'live', hs: 0, as: 0 }, k = gg.d + gkey(gg), ro = DATA.rosters.G.filter(r => r.p === '投手');
          PD[k] = { half: '3回裏', attack: Object.keys(YSHORT).find(x => YSHORT[x] === 'S'), pitcher: { name: ro[2].n } }; S.pu = S.pu || {}; S.pu[k] = 'G';
          for (const decs of [['H', '', ''], ['', '', ''], ['勝', 'S', '']]) {
            const P = ro.slice(0, 3).map((r, i) => ({ name: r.n, dec: decs[i], era: '3.15', ip: '1', np: '20', h: '0', hr: '0', so: '1', bb: '0', r: '0', er: '0' }));
            const w = document.createElement('div'); w.innerHTML = pitchingHTML(gg, { pitchers: [P, P] }); document.getElementById('v-game').prepend(w); setTab('game');
            const xs = [...w.querySelectorAll('td.pnx')].map(td => { const t = td.querySelector('.ptile') || [...td.querySelector('b').childNodes].find(n => n.nodeType === 3); if (t.nodeType === 3) { const rg = document.createRange(); rg.selectNodeContents(t); return Math.round(rg.getBoundingClientRect().left); } return Math.round(t.getBoundingClientRect().left); });
            if (new Set(xs).size > 1) ng.push(`印（${decs.join('・') || 'なし'}）と▶があると名前の位置がずれる（${xs}）`);
            const cur = w.querySelector('tr.cur'), marks = cur ? [...cur.querySelectorAll('*')].filter(e => getComputedStyle(e, '::before').content.includes('▶')).length : 0;
            if (marks !== 1) ng.push(`今の投手の▶が${marks}個`);
            w.remove();
          }
          delete PD[k]; return ng; }""")
        for m in r:
            bad(f"[投手成績の並び {'パワプロ風' if th else 'スタイリッシュ'}] {m}")
        for e in errs:
            bad(f"[投手成績の並び]: 画面のエラー {e}")
        await pg.close()


async def rec_check(browser):
    """歴代記録のタブ：通算・現役・シーズン × 打撃・投手 × 部門で切り替わる・記録の列が太字・現役の印・いつ現在か・注記。
    読み込めないときは知らせる。タブが8つでも名前が収まる"""
    for th in ["pawa", ""]:
        pg, errs = await open_page(browser, 390, th)
        r = await pg.evaluate("""async () => { const ng = [];
          await loadRec(true); setTab('rec');
          const txt = () => document.getElementById('recTbl').innerText;
          if (!/王 貞治/.test(txt()) || !/868/.test(txt())) ng.push('通算の本塁打が出ない');
          const rv = [...document.querySelectorAll('#recTbl tbody td.rv')].map(e => e.textContent);
          if (rv[0] !== '868') ng.push(`記録の列が太字の列になっていない（${rv}）`);
          if (!document.querySelector('#recTbl .recact')) ng.push('現役の印が出ない');
          // パワプロ風は名前の札：今の選手は名簿の守備位置の色、引退した打者は白、投手の記録はピンク。スタイリッシュは文字
          const tile = n => [...document.querySelectorAll('#recTbl td.rn')].find(td => td.textContent.includes(n))?.querySelector('.ptile');
          if (isPawa()) {
            if (!tile('王 貞治') || !tile('王 貞治').classList.contains('pwh')) ng.push(`引退した打者（王 貞治）が白い札でない（${tile('王 貞治')?.className}）`);
            const nk2 = tile('中村 剛也'); if (!nk2 || nk2.classList.contains('pwh') || !nk2.classList.contains(TILEC[posGroups('L', '中村 剛也', '内野手')[0]])) ng.push(`今の選手（中村 剛也）が名簿の守備位置の色でない（${nk2?.className}）`);
            if (document.querySelectorAll('#recTbl td.rn').length !== document.querySelectorAll('#recTbl td.rn .ptile').length) ng.push('札になっていない名前がある');
            const ov = [...document.querySelectorAll('#recTbl .rectile b')].filter(b => b.scrollWidth > b.clientWidth + 1).map(b => b.textContent); if (ov.length) ng.push(`名前が札に収まらない（${ov}）`);
            const t1 = tile('中村 剛也'), a1 = t1 && t1.parentElement.querySelector('.recact'); if (a1 && a1.getBoundingClientRect().top < t1.getBoundingClientRect().bottom - 1) ng.push('現役の印が札に重なっている');
          } else if (document.querySelector('#recTbl .ptile')) ng.push('スタイリッシュで札になっている');
          // 実働期間：全員「1959-1980」の形（4けた-4けた）。行ごとに1行・2行がばらばらにならない
          { const ps = [...document.querySelectorAll('#recTbl td.rp')];
            if (!ps.length) ng.push('実働期間の列がない');
            const bad1 = ps.map(td => td.textContent).filter(x => !/^\\d{4}-\\d{4}$/.test(x)); if (bad1.length) ng.push(`実働期間の書き方がそろっていない（${bad1}）`);
            const lines = ps.map(td => new Set([...td.querySelectorAll('.yr')].map(y => Math.round(y.getBoundingClientRect().top))).size); if (new Set(lines).size > 1) ng.push(`実働期間の行数がそろっていない（${lines}）`); }
          if (!/^10\\/1 時点$/.test(document.getElementById('recAsof').textContent)) ng.push(`いつ現在かが出ない（${document.getElementById('recAsof').textContent}）`);
          const sel = document.getElementById('recCat'); sel.value = 'avg'; sel.dispatchEvent(new Event('change'));
          if (!/\\.320/.test(txt())) ng.push('部門（打率）に切り替わらない');
          if (!/4000打数以上/.test(document.getElementById('recNote').textContent)) ng.push('注記が出ない');
          document.querySelector('#recKind button[data-k="ss"]').click();
          if (!/バース/.test(txt()) || !/阪神/.test(txt()) || /\\(阪 神\\)/.test(txt())) ng.push(`シーズンの打率が出ない・所属の書き方が違う（${txt().slice(0, 80)}）`);
          if (/\\(1986\\)/.test(txt())) ng.push('年度のかっこが残っている');
          document.querySelector('#recSide button[data-k="p"]').click();
          if (!/江夏 豊/.test(txt()) || !/401/.test(txt())) ng.push('シーズンの投手（奪三振）に切り替わらない');
          if (isPawa() && [...document.querySelectorAll('#recTbl td.rn .ptile')].some(t => !t.classList.contains('pp') && !t.classList.contains('ps'))) ng.push('投手の記録の札がピンク（投手の色）でない');
          if ([...document.querySelectorAll('#recCat option')].map(o => o.value).join() !== 'so') ng.push('記録のない部門が選べてしまう');
          document.querySelector('#recKind button[data-k="ac"]').click(); document.querySelector('#recSide button[data-k="b"]').click();
          if (!/中村 剛也/.test(txt())) ng.push('現役の通算に切り替わらない');
          // 読み込めないとき
          const keep = REC; REC = null; recAt = Date.now(); recBusy = false; localStorage.removeItem('rec-v1'); CONFIG.recUrl = 'https://live.example/none.json'; renderRec();
          await new Promise(r => setTimeout(r, 600));
          if (!/読み込(み中|めませんでした)/.test(txt())) ng.push('読み込めないときの知らせがない');
          REC = keep; renderRec();
          // 下のタブの名前が収まる
          for (const b of document.querySelectorAll('.tabbar button .tl')) if (b.scrollWidth > b.clientWidth + 1 || b.getBoundingClientRect().width > b.closest('button').getBoundingClientRect().width + 1) ng.push(`タブの名前がはみ出す（${b.textContent}）`);
          return ng; }""")
        for m in r:
            bad(f"[歴代記録 {'パワプロ風' if th else 'スタイリッシュ'}] {m}")
        for e in errs:
            bad(f"[歴代記録]: 画面のエラー {e}")
        await pg.close()


async def tab_group_check(browser):
    """下のタブは5つ（戦況・試合・順位・データ・選手）で、季節で数が変わらない。グループの中は上の切り替え（今日｜日程・今季｜歴代・応援歌｜入退団）。
    タブを押すと最後に開いていたページへ。今のタブをもう一度押すといちばん上へ。スワイプはページの順。開き直しても最後のページを覚えている"""
    for th in ["pawa", ""]:
        pg, errs = await open_page(browser, 390, th)
        r = await pg.evaluate("""async () => { const ng = [];
          const names = [...document.querySelectorAll('.tabbar button')].map(b => b.querySelector('.tl').textContent).join();
          if (names !== '戦況,試合,順位,データ,選手') ng.push(`下のタブの並び・名前が違う（${names}）`);
          const click = g => document.querySelector(`.tabbar button[data-grp="${g}"]`).click();
          const sub = () => [...document.querySelectorAll('#subNav:not([hidden]) button')].map(b => b.textContent + (b.getAttribute('aria-pressed') === 'true' ? '*' : '')).join();
          click('game'); if (S.tab !== 'game' || sub() !== '今日*,日程') ng.push(`試合：${S.tab} ${sub()}`);
          document.querySelector('#subNav button[data-p="cal"]').click(); if (S.tab !== 'cal' || sub() !== '今日,日程*') ng.push(`日程に切り替わらない（${S.tab} ${sub()}）`);
          if (document.querySelector('.tabbar button[aria-selected="true"]').dataset.grp !== 'game') ng.push('日程のときに「試合」のタブが選ばれていない');
          click('data'); if (S.tab !== 'stats' || sub() !== '今季*,歴代') ng.push(`データ：${S.tab} ${sub()}`);
          document.querySelector('#subNav button[data-p="rec"]').click(); if (S.tab !== 'rec') ng.push('歴代に切り替わらない');
          click('std'); if (S.tab !== 'std' || !document.getElementById('subNav').hidden) ng.push('順位で上の切り替えが出ている');
          click('game'); if (S.tab !== 'cal') ng.push(`試合を押すと最後に開いていた日程に戻らない（${S.tab}）`);
          click('data'); if (S.tab !== 'rec') ng.push(`データを押すと最後に開いていた歴代に戻らない（${S.tab}）`);
          window.scrollTo(0, 400); click('data'); await new Promise(r => setTimeout(r, 700));
          if (S.tab !== 'rec' || scrollY > 5) ng.push(`今のタブをもう一度押してもいちばん上に戻らない（${S.tab} ${scrollY}）`);
          if (JSON.parse(localStorage.getItem('sub-v1') || '{}').game !== 'cal') ng.push('最後に開いていたページを端末に覚えていない');
          const ord = tabOrder().join(); if (!/^magic,game,cal,std,stats,rec,song/.test(ord)) ng.push(`スワイプの順が違う（${ord}）`);
          // 季節で下のタブの数が変わらない
          const cols = () => getComputedStyle(document.querySelector('.tabbar nav')).gridTemplateColumns.split(' ').length;
          if (cols() !== 5) ng.push(`下のタブが5列でない（${cols()}）`);
          click('player'); const withOff = sub();
          if (OFF_SHOWN && withOff !== '応援歌*,入退団') ng.push(`選手：${withOff}`);
          if (!OFF_SHOWN && !document.getElementById('subNav').hidden) ng.push('入退団がない時期に上の切り替えが出ている');
          const tbb = [...document.querySelectorAll('.tabbar button')].map(b => b.getBoundingClientRect());
          if (tbb.some((b, i) => i && b.left < tbb[i - 1].right - 1) || tbb.some(b => b.right > innerWidth)) ng.push('下のタブが重なる・はみ出す');
          const sb = [...document.querySelectorAll('#subNav button')].map(b => b.getBoundingClientRect().width); if (sb.length && Math.max(...sb) - Math.min(...sb) > 1) ng.push('上の切り替えのボタンの幅がそろっていない');
          return ng; }""")
        for m in r:
            bad(f"[タブの整理 {'パワプロ風' if th else 'スタイリッシュ'}] {m}")
        for e in errs:
            bad(f"[タブの整理]: 画面のエラー {e}")
        await pg.close()


async def pennant_check(browser):
    """リーグ優勝：試合タブのいちばん上に優勝マジックと「今日決まる条件」。各試合の「勝ったら」に優勝決定・マジックの動き。
    決まったら「リーグ優勝」と決まった日。直接対決の札は、最下位争いが動く試合だけ"""
    for th in ["pawa", ""]:
        pg, errs = await open_page(browser, 390, th, me="T")
        r = await pg.evaluate("""() => { const ng = [];
          const base = seasonTable(DATA.games, calOrder(), pendingMakeups());
          const X = base[0];
          const next = DATA.games.filter(g => g.st !== 'final' && g.st !== 'canc' && (g.h === X.t || g.a === X.t)).map(g => g.d).sort()[0];
          if (X.magic == null || X.magic === 0 || !next) return ['skip'];
          const [y, m, d] = next.split('-').map(Number); jst = () => ({ y, m, d, iso: next });
          renderAll(); setTab('game');
          const box = document.getElementById('pennantBox').innerText;
          if (!box.includes('M' + X.magic)) ng.push(`優勝マジックが出ない（${box}）`);
          const st = pennantState();
          const decides = [...document.querySelectorAll('#today .tgo')].some(o => /リーグ優勝が決定/.test(o.innerText));
          if (st.combos.length && !/優勝が決まります/.test(box)) ng.push(`今日決まる条件が出ない（${box}）`);
          if (!st.combos.length && /優勝が決まります/.test(box)) ng.push('今日は決まらないのに、決まると書いている');
          if (decides && !st.combos.length) ng.push('試合の「勝ったら」と上の箱の言うことが食い違う');
          // 条件の言葉が正しいか：言葉どおりの結果にすると本当に決まる（主な条件の1つ目で試す）
          if (st.combos.length) {
            const w = pennantWords(st);
            if (!w || !w.main) ng.push('主な条件が空');
            if (/undefined|null|NaN/.test(box)) ng.push(`条件の言葉が壊れている（${box}）`);
          }
          // 直接対決の札：両方とも「大きな動きなし」の試合には付かない
          for (const tg of document.querySelectorAll('#today .tg')) {
            const sides = [...tg.querySelectorAll('.tgo')].map(o => o.innerText);
            if (sides.length === 2 && sides.every(x => /大きな動きなし/.test(x)) && tg.querySelector('.vsb')) ng.push(`動きのない試合に直接対決の札（${tg.dataset.gk}）`);
          }
          // 優勝が決まったあと：首位の残りを全部勝ちにして、決まった日が出る
          const keep = DATA.games;
          DATA = { ...DATA, games: DATA.games.map(g => g.st !== 'final' && g.st !== 'canc' && (g.h === X.t || g.a === X.t) ? { ...g, st: 'final', hs: g.h === X.t ? 5 : 0, as: g.a === X.t ? 5 : 0 } : g) };
          renderAll(); setTab('game');
          const st2 = pennantState(), box2 = document.getElementById('pennantBox').innerText;
          if (!st2.done || !/リーグ優勝/.test(box2) || !/\\d+\\/\\d+ に決定/.test(box2)) ng.push(`優勝が決まったあとの表示が違う（${box2}）`);
          DATA = { ...DATA, games: keep }; renderAll();
          return ng; }""")
        if r == ["skip"]:
            print("[リーグ優勝] 優勝マジックがない時期なので一部を飛ばしました")
            r = []
        for m in r:
            bad(f"[リーグ優勝 {'パワプロ風' if th else 'スタイリッシュ'}] {m}")
        for e in errs:
            bad(f"[リーグ優勝]: 画面のエラー {e}")
        await pg.close()
    # 条件の言葉：組み合わせから決まった言い方（決まった例）
    pg, errs = await open_page(browser, 390, "pawa", me="T")
    w = await pg.evaluate("""() => { const G = (h, a) => ({ d: '2026-10-03', h, a, st: 'sched' });
      const open = [G('C', 'T'), G('G', 'DB')];
      // 阪神が勝つ（a）か、巨人が負ける（DeNAの勝ち a）、どちらも引き分け（dd）で決まる
      const combos = ['ah','aa','ad','hh'.replace('hh','ha'),'da','ca'].filter(x => x.length === 2 && !x.includes('c'));
      const list = ['ah', 'aa', 'ad', 'ha', 'da', 'dd'];
      return pennantWords({ t: 'T', open, combos: list }); }""")
    if not w or w.get("main") != "阪神が勝つか、巨人が負ける" or w.get("extra") != "阪神と巨人がどちらも引き分け":
        bad(f"[リーグ優勝] 条件の言葉が違う：{w}")
    await pg.close()


TEAM_MARK_JS = """() => { const bad = [];
  const norm = c => { const d = document.createElement('i'); d.style.color = c; document.body.append(d); const v = getComputedStyle(d).color; d.remove(); return v; };
  for (const e of document.querySelectorAll('body *')) {
    if (!e.offsetParent && getComputedStyle(e).position !== 'fixed') continue;
    const own = [...e.childNodes].filter(n => n.nodeType === 3).map(n => n.textContent.trim()).join('');
    if (!own || own.length > 3) continue;
    const cs = getComputedStyle(e), tc = cs.getPropertyValue('--tc').trim(), ti = cs.getPropertyValue('--ti').trim();
    if (!tc || !ti || cs.backgroundColor !== norm(tc)) continue;
    if (cs.color !== norm(ti)) bad.push(`${e.className || e.tagName}「${own}」の文字が${cs.color}（球団の文字色は${norm(ti)}）`);
  }
  return [...new Set(bad)]; }"""


PRESSED_JS = """() => { const bad = [];
  const groups = new Map();
  for (const b of document.querySelectorAll('[aria-pressed]')) { if (!b.offsetParent) continue; const p = b.parentElement; if (!groups.has(p)) groups.set(p, []); groups.get(p).push(b); }
  for (const [p, bs] of groups) {
    const on = bs.filter(b => b.getAttribute('aria-pressed') === 'true'), off = bs.filter(b => b.getAttribute('aria-pressed') === 'false');
    if (!on.length || !off.length) continue;
    const sig = b => { const c = getComputedStyle(b); return [c.backgroundColor, c.backgroundImage, c.color, c.borderTopColor].join(); };
    if (on.some(a => off.some(b => sig(a) === sig(b)))) bad.push((p.id || p.className) + ': 選んでいる／いないが同じ見た目（' + on[0].textContent.trim() + '）');
  }
  return bad; }"""


async def team_mark_check(browser):
    """球団の印（球団色の丸・四角に1文字）の文字は、どの画面・4つの見た目でも球団ごとの文字色（阪神・巨人は黒、ほかは白）。
    日程の詳細・設定・選手の画面も。設定の「名前の色」は、選んでいるボタンがひと目で分かる"""
    for th, scheme in [("pawa", "dark"), ("pawa", "light"), ("", "dark"), ("", "light")]:
        ctx = await browser.new_context(viewport={"width": 390, "height": 844}, color_scheme=scheme)
        pg = await ctx.new_page()
        await pg.add_init_script(f"localStorage.setItem('me','T'); localStorage.setItem('theme','{th}'); localStorage.setItem('mode','auto'); localStorage.setItem('league','C')")
        await pg.route(LIVE + "**", route_live)
        await pg.goto(URL); await pg.wait_for_timeout(800)
        label = {("pawa", "dark"): "パワプロ風（夜）", ("pawa", "light"): "パワプロ風（昼）", ("", "dark"): "スタイリッシュ（黒）", ("", "light"): "スタイリッシュ（白）"}[(th, scheme)]
        await pg.evaluate(f"CONFIG.recUrl='{LIVE}records.json'; loadRec(true)")
        # 予告先発がある日：日程の詳細にも予告先発の行が出るように
        await pg.evaluate("""() => { const g = DATA.games.find(g => g.st === 'sched' && CL.includes(g.h) && CL.includes(g.a)) || DATA.games.find(g => CL.includes(g.h));
          const [y, m, d] = g.d.split('-').map(Number); jst = () => ({ y, m, d, iso: g.d });
          YK[`${g.d}|${g.h}|${g.a}`] = { h: (DATA.rosters[g.h] || [{}])[0].n || 'テスト', a: (DATA.rosters[g.a] || [{}])[0].n || 'テスト' };
          S.calTeam = g.h; S.calMonth = m; S.calSel = g.d; g.st = 'sched'; renderAll(); }""")
        out = set()
        for tab in TABS:
            await pg.evaluate(f"setTab('{tab}'); if ('{tab}' === 'cal') renderCal();")
            await pg.wait_for_timeout(120)
            for x in await pg.evaluate(TEAM_MARK_JS):
                out.add(f"{tab}: {x}")
            for x in await pg.evaluate(PRESSED_JS):   # 切り替えのボタン：選んでいる／いないが見た目で分かる
                out.add(f"{tab}: {x}")
        await pg.evaluate("openSheet()"); await pg.wait_for_timeout(400)
        for x in await pg.evaluate(TEAM_MARK_JS):
            out.add(f"設定: {x}")
        for x in await pg.evaluate(PRESSED_JS):
            out.add(f"設定: {x}")
        if th == "pawa":
            r = await pg.evaluate("""() => { const ng = [], rows = [...document.querySelectorAll('#opList .op-row')];
              if (!rows.length) return ['名前の色の設定が出ない'];
              const lum = c => { const v = c.match(/[\\d.]+/g).slice(0, 3).map(x => +x / 255).map(x => x <= .03928 ? x / 12.92 : ((x + .055) / 1.055) ** 2.4); return .2126 * v[0] + .7152 * v[1] + .0722 * v[2]; };
              for (const row of rows) {
                const on = row.querySelectorAll('.op-b[aria-pressed="true"]'), off = row.querySelector('.op-b[aria-pressed="false"]');
                if (on.length !== 1) { ng.push(`選んでいるボタンが${on.length}個`); continue; }
                const a = getComputedStyle(on[0]), b = getComputedStyle(off);
                if (a.backgroundColor === b.backgroundColor) ng.push('選んでいるボタンと選んでいないボタンが同じ見た目');
                const c = getComputedStyle(on[0]).getPropertyValue('--c').trim(); const t = document.createElement('i'); t.style.color = c; document.body.append(t); const cc = getComputedStyle(t).color; t.remove();
                if (a.backgroundColor !== cc) ng.push(`選んでいるボタンがその色で塗られていない（${a.backgroundColor} / ${cc}）`);
                const l1 = lum(a.color), l2 = lum(a.backgroundColor); if ((Math.max(l1, l2) + .05) / (Math.min(l1, l2) + .05) < 4.5) ng.push('選んでいるボタンの文字が読みにくい');
              }
              // 押したら選び直せる
              const b0 = rows[0].querySelector('.op-b[aria-pressed="false"]'); const k = b0.dataset.op; b0.click();
              if (document.querySelector(`#opList .op-b[data-op="${k}"]`).getAttribute('aria-pressed') !== 'true') ng.push('押しても選んだ状態にならない');
              return ng; }""")
            for m in r:
                out.add(f"名前の色: {m}")
        await pg.evaluate("closeSheet()"); await pg.wait_for_timeout(350)
        await pg.evaluate("setTab('song'); openPlayer(CL[0], DATA.rosters[CL[0]][0].n)"); await pg.wait_for_timeout(400)
        for x in await pg.evaluate(TEAM_MARK_JS):
            out.add(f"選手の画面: {x}")
        for m in sorted(out)[:20]:
            bad(f"[球団の印・{label}] {m}")
        await ctx.close()


async def cal_score_check(browser):
    """日程：終わった試合の日にちに、○×△の下にスコア。並びはすぐ上の対戦（ホーム - ビジター）と同じ。試合中も同じ並び。マスからはみ出さない"""
    for th in ["pawa", ""]:
        for w in [320, 390]:
            pg, errs = await open_page(browser, w, th)
            r = await pg.evaluate("""() => { const ng = [];
              const t = CL[0], fin = DATA.games.filter(g => g.st === 'final' && (g.h === t || g.a === t));
              const g0 = fin[fin.length - 1]; S.calTeam = t; S.calMonth = +g0.d.slice(5, 7); S.calSel = null; setTab('cal'); renderCal();
              const mon = DATA.games.filter(g => (g.h === t || g.a === t) && g.d.slice(5, 7) === g0.d.slice(5, 7));
              for (const g of mon.filter(g => g.st === 'final')) {
                const cell = document.querySelector(`#cal .day[data-d="${g.d}"]`); if (!cell) continue;
                const day = mon.filter(x => x.d === g.d); if (day[day.length - 1] !== g) continue;   // その日の最後の試合だけ出す（ダブルヘッダーなど）
                const sc = cell.querySelector('.fsc');
                if (!sc) { ng.push(`${g.d}：スコアがない`); continue; }
                if (sc.textContent !== `${g.hs}-${g.as}`) ng.push(`${g.d}：スコアの並びが対戦（${calSn(g.h)} - ${calSn(g.a)}）と違う（${sc.textContent}、本当は${g.hs}-${g.as}）`);
                const rs = cell.querySelector('.rs'); if (rs && rs.getBoundingClientRect().bottom > sc.getBoundingClientRect().top + 1) ng.push(`${g.d}：スコアが○×△の下にない`);
              }
              // 全球団・全部の月で、マスからはみ出さない（CS・日本シリーズの日も）
              for (const tt of CL) for (const mo of [3, 4, 5, 6, 7, 8, 9, 10, 11]) {
                S.calTeam = tt; S.calMonth = mo; renderCal();
                const ov = [...document.querySelectorAll('#cal .day.has')].filter(d => d.scrollWidth > d.clientWidth + 1 || d.scrollHeight > d.clientHeight + 1).map(d => d.dataset.d);
                if (ov.length) ng.push(`${tt} ${mo}月：マスからはみ出す（${ov.slice(0, 3)}）`);
              }
              S.calTeam = t; S.calMonth = +g0.d.slice(5, 7); renderCal();
              // 試合中も同じ並び
              const g1 = mon.find(g => g.st === 'final'); const keep = { ...g1 };
              Object.assign(g1, { st: 'live', hs: 7, as: 1, inn: '5回裏' }); renderCal();
              const lv = document.querySelector(`#cal .day[data-d="${g1.d}"] .lvc`); if (!lv || lv.textContent !== '7-1') ng.push(`試合中のスコアの並びが対戦と違う（${lv && lv.textContent}）`);
              Object.assign(g1, keep); renderCal();
              return ng; }""")
            for m in r[:10]:
                bad(f"[日程のスコア {'パワプロ風' if th else 'スタイリッシュ'} 幅{w}] {m}")
            for e in errs:
                bad(f"[日程のスコア]: 画面のエラー {e}")
            await pg.close()


async def pitch_count_check(browser):
    """一球速報の球数のめやす：先発は100球（80球から黄・100球から赤）、中継ぎは30球（20球から黄・30球から赤）。
    先発か中継ぎかは、その試合の投手成績でいちばん上か（まだなければ予告先発）"""
    pg, errs = await open_page(browser, 390, "pawa")
    r = await pg.evaluate("""() => { const ng = [];
      const g = { d: '2026-10-04', h: 'T', a: 'G', st: 'live', hs: 1, as: 0, inn: '7回表' }, k = g.d + gkey(g);
      const atk = Object.keys(YSHORT).find(x => YSHORT[x] === 'G');
      const P = DATA.rosters.T.filter(r => r.p === '投手');
      const one = (name, np, withBox, yk) => {
        PD[k] = { half: '7回表', attack: atk, pitcher: { name, np: String(np), game: { np: String(np) } }, batter: { name: 'x' }, pitches: [] };
        if (withBox) GD[k] = { pitchers: [[], [{ name: P[0].n, np: '95' }, { name: P[1].n, np: String(np) }]], lineups: [[], []] }; else delete GD[k];
        if (yk) YK[`${g.d}|${g.h}|${g.a}`] = { h: P[0].n, a: 'x' }; else delete YK[`${g.d}|${g.h}|${g.a}`];
        const w = document.createElement('div'); w.innerHTML = pitchHTML(g); const ga = w.querySelector('.pgauge');
        return ga ? { cls: ga.className, label: ga.querySelector('.pgnote').textContent, barW: ga.querySelector('i').getBoundingClientRect().width, width: parseFloat(ga.querySelector('b').style.width), line: parseFloat(ga.querySelector('u').style.left) } : null; };
      let x = one(P[0].n, 85, true); if (!x || !/先発・めやす100/.test(x.label) || !/mid/.test(x.cls)) ng.push(`先発85球：${JSON.stringify(x)}`);
      x = one(P[0].n, 101, true); if (!x || !/hi/.test(x.cls)) ng.push(`先発101球が赤でない：${JSON.stringify(x)}`);
      x = one(P[1].n, 15, true); if (!x || !/中継ぎ・めやす30/.test(x.label) || /mid|hi/.test(x.cls)) ng.push(`中継ぎ15球：${JSON.stringify(x)}`);
      if (x && Math.abs(x.width - 15 / 36 * 100) > .5) ng.push(`中継ぎ15球のバーの長さが違う（${x.width}%）`);
      if (x && Math.abs(x.line - 30 / 36 * 100) > .5) ng.push(`中継ぎのめやすの線の位置が違う（${x.line}%）`);
      x = one(P[1].n, 22, true); if (!x || !/mid/.test(x.cls) || /hi/.test(x.cls)) ng.push(`中継ぎ22球が黄色でない：${JSON.stringify(x)}`);
      x = one(P[1].n, 31, true); if (!x || !/hi/.test(x.cls)) ng.push(`中継ぎ31球が赤でない：${JSON.stringify(x)}`);
      // 投手成績がまだないとき：予告先発の名前で決める
      x = one(P[0].n, 50, false, true); if (!x || !/先発/.test(x.label)) ng.push(`予告先発の投手が先発にならない：${JSON.stringify(x)}`);
      x = one(P[1].n, 10, false, true); if (!x || !/中継ぎ/.test(x.label)) ng.push(`予告先発でない投手が中継ぎにならない：${JSON.stringify(x)}`);
      // 実際の画面の幅で：バーが細くつぶれない（先発・中継ぎの説明はバーの下）
      one(P[1].n, 22, true); setTab('game'); const box = document.createElement('div'); box.className = 'tg'; box.innerHTML = pitchHTML(g); document.getElementById('today').prepend(box);
      const bw = box.querySelector('.pgauge i').getBoundingClientRect().width, nb = box.querySelector('.pgnote').getBoundingClientRect(), ib = box.querySelector('.pgauge i').getBoundingClientRect();
      if (bw < 50) ng.push(`球数のバーが細すぎる（${Math.round(bw)}px）`);
      if (nb.top < ib.bottom - 1) ng.push('先発・中継ぎの説明がバーの下にない');
      box.remove();
      delete PD[k]; delete GD[k]; return ng; }""")
    for m in r:
        bad(f"[球数のめやす] {m}")
    for e in errs:
        bad(f"[球数のめやす]: 画面のエラー {e}")
    await pg.close()


async def off_name_tag_check(browser):
    """入退団：発表の一覧から「松原快 育成」「髙野光海 NEW 育成」のような札つきの名前が来ても、名前だけにして、同じ選手は1回だけ"""
    pg, errs = await open_page(browser, 390, "pawa")
    r = await pg.evaluate("""() => { const ng = [], ro = DATA.rosters.T[0];
      jst = () => ({ y: 2026, m: 10, d: 4, iso: '2026-10-04' });
      DATA.offseason = { season: 2026, checked_at: new Date().toISOString(), teams: {}, seen: {}, items: [
        { t: 'T', n: ro.n, no: ro.no, dev: false, kind: 'cut', date: '2026-09-29', url: 'u', title: 'x' },
        { t: 'T', n: ro.n.replace(/\\s+/g, '') + ' 育成', no: '', dev: false, kind: 'cut', date: '2026-09-29', url: 'u', title: 'x', src: 'bbc' },
        { t: 'T', n: 'テスト太郎 NEW 育成', no: '', dev: false, kind: 'cut', date: '2026-10-04', url: 'u', title: 'x', src: 'bbc' }] };
      LIVE_OFF.items = [{ t: 'T', n: 'テスト太郎 NEW', kind: 'cut', date: '2026-10-04', url: 'u' }];
      renderAll(); setTab('off'); S.offCat = 'all'; renderOff();
      const names = [...document.querySelectorAll('#offList .onm')].map(e => e.textContent);
      if (names.some(n => /育成|NEW/.test(n))) ng.push(`名前に札が残っている（${names.filter(n => /育成|NEW/.test(n))}）`);
      const k = offItems().filter(x => x.t === 'T').map(x => nkOff(x.n));
      if (k.length !== new Set(k).size) ng.push(`同じ選手が2回出ている（${k}）`);
      const tt = offItems().find(x => nkOff(x.n) === 'テスト太郎'); if (!tt || !tt.dev) ng.push('「育成」の札から育成の印（dev）にならない');
      LIVE_OFF.items = []; return ng; }""")
    for m in r:
        bad(f"[入退団の名前] {m}")
    for e in errs:
        bad(f"[入退団の名前]: 画面のエラー {e}")
    await pg.close()


async def player_today_check(browser):
    """選手の画面：今日の試合（試合中・試合後）に出ていれば、その試合の成績をいちばん上に出す"""
    pg, errs = await open_page(browser, 390, "pawa")
    await pg.evaluate("""() => { const t = CL[0], o = CL[1]; const g = DATA.games.find(x => x.h === t && x.a === o) || DATA.games.find(x => x.h === t);
      const today = g.d; jst = () => ({ y: +today.slice(0, 4), m: +today.slice(5, 7), d: +today.slice(8), iso: today }); liveWanted = () => false;
      Object.assign(g, { st: 'live', hs: 3, as: 2, inn: '6回表' }); S.open[g.d + gkey(g)] = true; renderAll(); setTab('game'); }""")
    await pg.wait_for_timeout(1500)
    r = await pg.evaluate("""async () => { const ng = [], t = CL[0]; const g = DATA.games.find(x => x.d === jst().iso && (x.h === t || x.a === t)); const d = GD[g.d + gkey(g)];
      if (!d) return ['試合の出場成績が読み込まれていない']; const side = g.a === t ? 0 : 1;
      const bt = (d.lineups[side] || [])[0], pt = (d.pitchers[side] || [])[0];
      await openPlayer(t, bt.name); await new Promise(r => setTimeout(r, 700));
      const a = document.querySelector('.ps-today'); if (!a || !a.textContent.includes('今日の試合')) ng.push(`打者（${bt.name}）の画面に今日の試合の成績が出ない`);
      // 今日の箱は今日の数字だけ（今季の打率は出さない）。四死球は今日の打席の結果の四球・死球の数
      if (a) { const labs = [...a.querySelectorAll('.ps-main .ps-c b, .ps-main span, .ps-main i')].map(e => e.textContent); const t = a.textContent;
        if (/打率/.test(t)) ng.push('今日の試合の箱に今季の打率が出ている');
        if (!/四死球/.test(t)) ng.push('今日の試合の箱に四死球がない');
        const want = (bt.results || []).filter(r => /四|死球|敬遠/.test(r)).length;
        const cellv = [...a.querySelectorAll('.ps-main > *')].find(e => /四死球/.test(e.textContent));
        if (cellv && !new RegExp('(^|\\D)' + Math.max(want, parseInt(bt.bb, 10) || 0) + '($|\\D)').test(cellv.textContent.replace('四死球', ''))) ng.push(`四死球の数が違う（${cellv.textContent}・結果では${want}）`); }
      await openPlayer(t, pt.name, { kind: 'pit' }); await new Promise(r => setTimeout(r, 700));
      const c = document.querySelector('.ps-today'); if (!c || !c.textContent.includes('球数')) ng.push(`投手（${pt.name}）の画面に今日の試合の成績が出ない`);
      // 今の打者・投手を開くと、今季の対戦成績（打者 vs 投手）
      const k = g.d + gkey(g), p = PD[k];
      if (p && p.batter && p.pitcher) {
        // 今の打者を開くと、今日の相手との今季の成績と、今の投手の左右の成績（スポナビの選手のページ）
        const bteam = /裏/.test(p.half || '') ? g.h : g.a, opp = bteam === g.h ? g.a : g.h;
        p.ids = Object.assign({}, p.ids, { [p.batter.name]: '1700044' });
        SPLIT['1700044'] = { at: Date.now(), d: { kind: 'bat', team: { [opp]: { '打率': '.267', '打数': '30', '安打': '8', '本塁打': '1', 'OPS': '.771' } },
          lr: [{ p: '右投', b: '左打者', '打率': '.287', '打数': '181', '安打': '52', '本塁打': '7' }, { p: '右投', b: '右打者', '打率': '.250', '打数': '40', '安打': '10', '本塁打': '1' }, { p: '左投', b: '左打者', '打率': '.246', '打数': '65', '安打': '16', '本塁打': '1' }, { p: '左投', b: '右打者', '打率': '.300', '打数': '20', '安打': '6', '本塁打': '0' }] } };
        await openPlayer(bteam, p.batter.name); await new Promise(r => setTimeout(r, 700));
        const v = document.querySelector('.ps-vs');
        if (!v || !v.textContent.includes('.267')) ng.push('今の打者の画面に、今日の相手との今季の成績が出ない');
        if (!v || !/対[左右]投手/.test(v.textContent)) ng.push('今の打者の画面に、今の投手の左右との成績が出ない');
      }
      return ng; }""")
    for m in r:
        bad(f"[選手の画面・今日の試合] {m}")
    for e in errs:
        bad(f"[選手の画面・今日の試合]: 画面のエラー {e}")
    await pg.close()


async def peek_check(browser):
    """長押しでのぞく：選手の名前・試合のカード・順位表のチーム・日程の日にち。長押しで出て、指を離すと閉じ、そのあとのタップは無視"""
    LP = """async (sel) => { const el = document.querySelector(sel); if (!el) return ['見つからない']; el.scrollIntoView({ block: 'center' }); await new Promise(r => setTimeout(r, 150));
      const r = el.getBoundingClientRect(), x = r.left + Math.min(20, r.width / 2), y = r.top + r.height / 2;
      const tt = new Touch({ identifier: 1, target: el, clientX: x, clientY: y });
      el.dispatchEvent(new TouchEvent('touchstart', { touches: [tt], targetTouches: [tt], changedTouches: [tt], bubbles: true, cancelable: true }));
      await new Promise(r => setTimeout(r, 600));
      const ng = [], card = document.querySelector('#peek .pk-card');
      if (!PEEK.open || !card || !card.textContent.trim()) ng.push('長押ししても中身が出ない');
      else { const cr = card.getBoundingClientRect(); if (cr.left < 0 || cr.right > innerWidth + 1) ng.push('のぞく画面が画面からはみ出す'); }
      el.dispatchEvent(new TouchEvent('touchend', { changedTouches: [tt], bubbles: true, cancelable: true }));
      if (PEEK.open) ng.push('指を離しても閉じない');
      if (Date.now() >= PEEK.until) ng.push('指を離したあとのタップを無視していない');
      return ng; }"""
    for theme in ["pawa", ""]:
        for width in [320, 390]:
            pg, errs = await open_page(browser, width, theme, touch=True)
            for tab, sel, what in [("magic", "tr[data-tm]", "戦況のチーム"), ("game", "[data-gk]", "試合のカード"), ("std", "#v-std tr[data-tm]", "順位表のチーム"),
                                   ("stats", "#rankList [data-pl]", "選手の名前"), ("cal", ".day.has", "日程の日にち"), ("song", ".ptile[data-song]", "応援歌の選手")]:
                await pg.evaluate(f"setTab('{tab}')"); await pg.wait_for_timeout(600)
                for m in await pg.evaluate(LP, sel):
                    bad(f"[長押し {'パワプロ風' if theme else 'スタイリッシュ'} 幅{width}] {what}：{m}")
                await pg.wait_for_timeout(500)
            for e in errs:
                bad(f"[長押し]: 画面のエラー {e}")
            await pg.close()


async def league_switch_check(browser):
    """設定でリーグ・テーマを切り替えたら設定が閉じて画面が切り替わる。セ→パ→セと戻したとき、選んでいた球団（日程・応援歌・チーム別成績）が元に戻る"""
    pg, errs = await open_page(browser, 390, "pawa")
    ng = []
    await pg.evaluate("setTab('cal'); S.calTeam = 'C'; keepTeam('calTeam', 'C'); S.songTeam = 'D'; keepTeam('songTeam', 'D'); S.ptTeam = 'S'; keepTeam('ptTeam', 'S'); renderAll()")
    await pg.evaluate("openSheet()"); await pg.wait_for_timeout(400)
    await pg.evaluate("document.querySelector('#lgSeg button[data-lg=\"P\"]').click()"); await pg.wait_for_timeout(900)
    r = await pg.evaluate("[isPL(), document.getElementById('sheet').hidden, document.querySelector('main').classList.contains('swapping')]")
    if not r[0]: ng.append("パ・リーグに切り替わっていない")
    if not r[1]: ng.append("リーグを切り替えても設定の画面が閉じない")
    if r[2]: ng.append("画面の切り替えの途中のまま（薄いまま）")
    await pg.evaluate("S.calTeam = 'H'; keepTeam('calTeam', 'H'); openSheet()"); await pg.wait_for_timeout(400)
    await pg.evaluate("document.querySelector('#lgSeg button[data-lg=\"C\"]').click()"); await pg.wait_for_timeout(900)
    r = await pg.evaluate("[isPL(), S.calTeam, S.songTeam, S.ptTeam]")
    if r[0]: ng.append("セ・リーグに戻っていない")
    if r[1:] != ["C", "D", "S"]: ng.append(f"セ・リーグに戻したとき、選んでいた球団が戻らない（日程・応援歌・チーム別成績＝{r[1:]}）")
    await pg.evaluate("openSheet()"); await pg.wait_for_timeout(400)
    await pg.evaluate("document.querySelector('#lgSeg button[data-lg=\"P\"]').click()"); await pg.wait_for_timeout(900)
    if await pg.evaluate("S.calTeam") != "H": ng.append("パ・リーグに戻したとき、パで選んでいた球団が戻らない")
    await pg.evaluate("openSheet()"); await pg.wait_for_timeout(400)
    await pg.evaluate("document.querySelector('#themeSeg button[data-theme=\"\"]').click()"); await pg.wait_for_timeout(900)
    r = await pg.evaluate("[isPawa(), document.getElementById('sheet').hidden]")
    if r[0]: ng.append("テーマが切り替わっていない")
    if not r[1]: ng.append("テーマを切り替えても設定の画面が閉じない")
    for m in ng:
        bad(f"[設定の切り替え] {m}")
    for e in errs:
        bad(f"[設定の切り替え]: 画面のエラー {e}")
    await pg.close()


async def cal_post_check(browser):
    """日程：CS・日本シリーズに出ない（出る可能性がない）球団の日程には、ポストシーズンの試合を出さない"""
    pg, errs = await open_page(browser, 390, "pawa")
    r = await pg.evaluate("""() => { const ng = [];
      if (!(DATA.post || []).length) return ['ポストシーズンの日程がない（検査できない）'];
      // シーズンを最後まで終わらせる（残りは全部ホームの勝ち）→ 順位が決まる
      DATA.games.forEach(g => { if (inLg(g) && g.st !== 'final') { g.st = 'final'; g.hs = 3; g.as = 1; } });
      const rk = seasonTable(DATA.games, calOrder()).map(r => r.t);
      const cnt = t => { const ms = calMonths(); let n = 0; for (const m of ms) n += postGames().filter(g => monthOf(g.d) === m && postMayPlay(g, t)).length; return n; };
      if (cnt(rk[4]) || cnt(rk[5] || rk[4])) ng.push(`4位以下の球団（${fn(rk[4])}）の日程にポストシーズンが出ている`);
      if (!cnt(rk[0])) ng.push(`1位の球団（${fn(rk[0])}）の日程にポストシーズンが出ない`);
      if (!postGames().some(g => g.stage === 'CS1' && postMayPlay(g, rk[1]))) ng.push(`2位の球団（${fn(rk[1])}）の日程にファーストステージが出ない`);
      if (postGames().some(g => g.stage === 'CS1' && postMayPlay(g, rk[0]))) ng.push(`1位の球団の日程にファーストステージが出ている`);
      S.calTeam = rk[4]; setTab('cal'); renderCal();
      return ng; }""")
    for m in r:
        bad(f"[日程のポストシーズン] {m}")
    for e in errs:
        bad(f"[日程のポストシーズン]: 画面のエラー {e}")
    await pg.close()


NIGHT_CONTRAST_JS = r"""(tab) => {
  const parse = s => { const cm = String(s).match(/color\(srgb ([\d.]+) ([\d.]+) ([\d.]+)(?: \/ ([\d.]+))?\)/); if (cm) return { r: cm[1] * 255, g: cm[2] * 255, b: cm[3] * 255, a: cm[4] == null ? 1 : +cm[4] };
    const m = String(s).match(/rgba?\(([^)]+)\)/); if (!m) return null; const v = m[1].split(",").map(x => parseFloat(x)); return { r: v[0], g: v[1], b: v[2], a: v.length > 3 ? v[3] : 1 }; };
  const lum = c => { const f = x => { x /= 255; return x <= .03928 ? x / 12.92 : Math.pow((x + .055) / 1.055, 2.4); }; return .2126 * f(c.r) + .7152 * f(c.g) + .0722 * f(c.b); };
  const bgOf = el => {
    for (let e = el; e; e = e.parentElement) {
      const cs = getComputedStyle(e);
      const bi = cs.backgroundImage;
      if (bi && bi !== "none") { const cols = [...bi.matchAll(/rgba?\([^)]+\)/g)].map(m => parse(m[0])).filter(c => c && c.a > .5); if (cols.length) return cols[Math.floor(cols.length / 2)]; }
      const c = parse(cs.backgroundColor); if (c && c.a > .5) return c;
    }
    return { r: 11, g: 23, b: 51, a: 1 };
  };
  const out = [];
  const walker = document.createTreeWalker(document.body, NodeFilter.SHOW_TEXT);
  const seen = new Set();
  while (walker.nextNode()) {
    const t = walker.currentNode; if (!t.textContent.trim()) continue;
    const el = t.parentElement; if (!el || seen.has(el) || el instanceof SVGElement) continue; seen.add(el);
    const r = el.getBoundingClientRect(); if (!r.width || !r.height || r.bottom < 0 || r.top > innerHeight) continue;
    const cs = getComputedStyle(el); if (cs.visibility === "hidden" || +cs.opacity < .3 || el.closest("[hidden],.tabbar,#sheet:not(.open),#peek:not(.on),.badge,.ykb,.hb,.bk,.k1,.k2")) continue;
    const fg = parse(cs.color); if (!fg || fg.a < .3) continue;
    // 縁取りの文字（空の上の白抜きの見出しなど）は、縁取りで読めるので数えない
    if ((cs.webkitTextStrokeWidth && parseFloat(cs.webkitTextStrokeWidth) > 0) || (cs.textShadow && (cs.textShadow.match(/rgb/g) || []).length >= 3)) continue;
    const fs = parseFloat(cs.fontSize), fw = +cs.fontWeight || 400, big = fs >= 24 || (fs >= 18.66 && fw >= 700);
    const bg = bgOf(el), L1 = lum(fg), L2 = lum(bg), cr = (Math.max(L1, L2) + .05) / (Math.min(L1, L2) + .05);
    if (cr < (big ? 3 : 4.5)) out.push(`${tab}：${t.textContent.trim().slice(0, 14)}（${el.className || el.tagName}・コントラスト ${cr.toFixed(1)}）`);
  }
  return out.slice(0, 12);
}
"""


async def night_check(browser):
    """パワプロ風（夜）：自動（iPhone のダークモード）・昼・夜の切り替えと、夜のとき文字が背景に埋もれていないか（明るさの差）"""
    ctx = await browser.new_context(viewport={"width": 390, "height": 844}, color_scheme="dark")
    pg = await ctx.new_page()
    await pg.add_init_script("Object.defineProperty(navigator, 'webdriver', { get: () => true }); localStorage.setItem('me','S'); localStorage.setItem('theme','pawa'); localStorage.setItem('league','C')")
    await pg.goto(f"file://{ROOT}/index.html"); await pg.wait_for_timeout(700)
    ng = []
    if not await pg.evaluate("document.documentElement.classList.contains('pawa-dark')"): ng.append("自動なのに、iPhone がダークモードのとき夜にならない")
    await pg.evaluate("store('mode','light'); applyPawaMode()")
    if await pg.evaluate("document.documentElement.classList.contains('pawa-dark')"): ng.append("「昼」を選んでも夜のまま")
    await pg.evaluate("store('mode','dark'); applyPawaMode(); renderAll()")
    if not await pg.evaluate("document.documentElement.classList.contains('pawa-dark')"): ng.append("「夜」を選んでも夜にならない")
    for tab in ["magic", "game", "cal", "std", "stats", "rec", "song", "off"]:
        await pg.evaluate(f"setTab('{tab}')"); await pg.wait_for_timeout(600)
        h = await pg.evaluate("document.documentElement.scrollHeight")
        for y in range(0, min(h, 6000), 700):
            await pg.evaluate(f"window.scrollTo(0,{y})"); await pg.wait_for_timeout(120)
            ng += await pg.evaluate(NIGHT_CONTRAST_JS, tab)
    await pg.evaluate("window.scrollTo(0,0); openSheet()"); await pg.wait_for_timeout(500)
    ng += await pg.evaluate(NIGHT_CONTRAST_JS, "設定")
    await pg.evaluate("closeSheet()"); await pg.wait_for_timeout(400)
    # 試合を開いたとき（一球速報）と、選手の画面
    await pg.evaluate("""() => { const t = CL[0], o = CL[1]; const g = DATA.games.find(x => x.h === t && x.a === o) || DATA.games.find(x => x.h === t);
      const today = g.d; jst = () => ({ y: +today.slice(0, 4), m: +today.slice(5, 7), d: +today.slice(8), iso: today }); liveWanted = () => false;
      Object.assign(g, { st: 'live', hs: 3, as: 2, inn: '4回裏' }); S.open[g.d + gkey(g)] = true; renderAll(); setTab('game'); }""")
    await pg.wait_for_timeout(1200)
    h = await pg.evaluate("document.documentElement.scrollHeight")
    for y in range(0, min(h, 9000), 700):
        await pg.evaluate(f"window.scrollTo(0,{y})"); await pg.wait_for_timeout(120)
        ng += await pg.evaluate(NIGHT_CONTRAST_JS, "一球速報")
    await pg.evaluate("window.scrollTo(0,0)")
    await pg.evaluate("(async () => { const t = CL[0], ro = DATA.rosters[t].find(x => x.p === '投手'); await openPlayer(t, ro.n, { kind: 'pit' }); })()")
    await pg.wait_for_timeout(900)
    ng += await pg.evaluate(NIGHT_CONTRAST_JS, "選手の画面")
    # 夜は色を紺の1系統に：表の地は紺だけ（球団の色で塗り分けない）、見出しは緑にしない、タブバーは紺のガラス
    await pg.evaluate("S.open = {}; renderAll(); setTab('std')"); await pg.wait_for_timeout(600)
    ng += await pg.evaluate("""() => { const ng = [];
      const hue = c => { const m = c.match(/\\d+(\\.\\d+)?/g); if (!m) return null; const [r, g, b] = m.map(Number); const mx = Math.max(r, g, b), mn = Math.min(r, g, b); return { r, g, b, l: (mx + mn) / 510, blue: b >= r && b >= g }; };
      const tds = [...document.querySelectorAll('#v-std .ytab tbody td, #v-magic .ytab tbody td')].filter(e => e.getBoundingClientRect().width);
      const odd = tds.map(e => hue(getComputedStyle(e).backgroundColor)).filter(c => c && (!c.blue || c.l > .4));
      if (odd.length) ng.push(`表の地に紺以外の色がある（${odd.length}マス）`);
      const h2 = document.querySelector('#v-std h2'); if (h2 && /98, 216, 115|31, 168, 58/.test(getComputedStyle(h2).backgroundImage)) ng.push('見出しが緑のまま');
      const tb = getComputedStyle(document.querySelector('.tabbar')).backgroundImage; const m = tb.match(/rgba?\\(([^)]+)\\)/);
      if (m) { const [r, g, b] = m[1].split(',').map(parseFloat); if ((r + g + b) / 3 > 150) ng.push('タブバーが明るいガラスのまま'); }
      return ng; }""")
    for m in list(dict.fromkeys(ng))[:10]:
        bad(f"[パワプロ風（夜）] {m}")
    await ctx.close()


async def light_check(browser):
    """スタイリッシュ（ライト）：自動・ライト・ダークの切り替えと、ライトのとき文字が背景に埋もれていないか"""
    ctx = await browser.new_context(viewport={"width": 390, "height": 844}, color_scheme="light")
    pg = await ctx.new_page()
    await pg.add_init_script("Object.defineProperty(navigator, 'webdriver', { get: () => true }); localStorage.setItem('me','S'); localStorage.setItem('theme',''); localStorage.setItem('league','C')")
    await pg.goto(f"file://{ROOT}/index.html"); await pg.wait_for_timeout(700)
    ng = []
    if not await pg.evaluate("document.documentElement.classList.contains('sty-light')"): ng.append("自動なのに、iPhone がライトモードのときライトにならない")
    await pg.evaluate("store('mode','dark'); applyPawaMode()")
    if await pg.evaluate("document.documentElement.classList.contains('sty-light')"): ng.append("「ダーク」を選んでもライトのまま")
    await pg.evaluate("store('mode','light'); applyPawaMode(); renderAll()")
    for tab in ["magic", "game", "cal", "std", "stats", "rec", "song", "off"]:
        await pg.evaluate(f"setTab('{tab}')"); await pg.wait_for_timeout(600)
        if tab == "game":
            await pg.evaluate("""() => { const t = CL[0], o = CL[1]; const g = DATA.games.find(x => x.h === t && x.a === o) || DATA.games.find(x => x.h === t);
              const today = g.d; jst = () => ({ y: +today.slice(0, 4), m: +today.slice(5, 7), d: +today.slice(8), iso: today }); liveWanted = () => false;
              Object.assign(g, { st: 'live', hs: 3, as: 2, inn: '4回裏' }); S.open[g.d + gkey(g)] = true; renderAll(); setTab('game'); }""")
            await pg.wait_for_timeout(1200)
        h = await pg.evaluate("document.documentElement.scrollHeight")
        for y in range(0, min(h, 9000), 700):
            await pg.evaluate(f"window.scrollTo(0,{y})"); await pg.wait_for_timeout(120)
            ng += await pg.evaluate(NIGHT_CONTRAST_JS, tab)
    await pg.evaluate("window.scrollTo(0,0); openSheet()"); await pg.wait_for_timeout(500)
    ng += await pg.evaluate(NIGHT_CONTRAST_JS, "設定")
    # ガラス（タブバー・上の小さい見出し）もライトは明るく
    ng += await pg.evaluate("""() => { const ng = [];
      for (const sel of ['.tabbar', '.minibar']) { const e = document.querySelector(sel); if (!e) continue; const m = getComputedStyle(e).backgroundImage.match(/rgba?\\(([^)]+)\\)/);
        if (m) { const [r, g, b] = m[1].split(',').map(parseFloat); if ((r + g + b) / 3 < 150) ng.push(`${sel === '.tabbar' ? '下のタブバー' : '上の小さい見出し'}のガラスが暗いまま`); } }
      return ng; }""")
    for m in list(dict.fromkeys(ng))[:10]:
        bad(f"[スタイリッシュ（ライト）] {m}")
    await ctx.close()


async def line_image_check(browser):
    """LINEで送る画像：パワプロ風の昼・夜、スタイリッシュの黒・白の4つ、それぞれ画面と同じ明るさで描く"""
    for theme, mode, want, label in [("pawa", "light", "sky", "パワプロ風（昼）"), ("pawa", "dark", "navy", "パワプロ風（夜）"), ("", "dark", "black", "スタイリッシュ（ダーク）"), ("", "light", "white", "スタイリッシュ（ライト）")]:
        pg, errs = await open_page(browser, 390, theme)
        await pg.evaluate(f"store('mode','{mode}'); applyPawaMode()")
        px = await pg.evaluate("(() => { const c = drawCard(analyze(DATA.games, S.period, CONFIG)); const d = c.getContext('2d').getImageData(10, 10, 1, 1).data; return [d[0], d[1], d[2]]; })()")
        r, g, b = px
        ok = {"sky": b > 180 and r < 120 and g > 100, "navy": b < 110 and r < 40 and b > r + 20, "black": max(px) < 30, "white": min(px) > 225}[want]
        if not ok:
            bad(f"[LINEの画像] {label}の色になっていない（左上の色 {px}）")
        for e in errs:
            bad(f"[LINEの画像 {label}]: 画面のエラー {e}")
        await pg.close()


async def contrast_all_check(browser):
    """4つの見た目（パワプロ風の昼・夜、スタイリッシュの黒・白）すべてで、文字と地の明るさの差が一般的な基準（ふつうの文字4.5・大きい文字3）以上か。
    セ・パ、全タブ、試合を開いたところ、設定の画面"""
    for theme, mode, label in [("pawa", "light", "パワプロ風（昼）"), ("pawa", "dark", "パワプロ風（夜）"), ("", "dark", "スタイリッシュ（黒）"), ("", "light", "スタイリッシュ（白）")]:
        pg, errs = await open_page(browser, 390, theme)
        await pg.evaluate(f"store('mode','{mode}'); applyPawaMode(); renderAll()")
        ng = []
        for lg in ["C", "P"]:
            if lg == "P":
                await pg.evaluate("switchLeague('P')"); await pg.wait_for_timeout(300)
            for tab in ["magic", "game", "cal", "std", "stats", "rec", "song", "off"]:
                await pg.evaluate(f"setTab('{tab}')"); await pg.wait_for_timeout(450)
                if tab == "game" and lg == "C":
                    await pg.evaluate("""() => { const t = CL[0], o = CL[1]; const g = DATA.games.find(x => x.h === t && x.a === o) || DATA.games.find(x => x.h === t);
                      const today = g.d; jst = () => ({ y: +today.slice(0, 4), m: +today.slice(5, 7), d: +today.slice(8), iso: today }); liveWanted = () => false;
                      Object.assign(g, { st: 'live', hs: 3, as: 2, inn: '4回裏' }); S.open[g.d + gkey(g)] = true; renderAll(); setTab('game'); }""")
                    await pg.wait_for_timeout(1100)
                h = await pg.evaluate("document.documentElement.scrollHeight")
                for y in range(0, min(h, 9000), 760):
                    await pg.evaluate(f"window.scrollTo(0,{y})"); await pg.wait_for_timeout(70)
                    ng += await pg.evaluate(NIGHT_CONTRAST_JS, f"{lg}:{tab}")
            await pg.evaluate("window.scrollTo(0,0); openSheet(); document.querySelectorAll('#sheet details').forEach(d => (d.open = true))"); await pg.wait_for_timeout(450)
            ng += await pg.evaluate(NIGHT_CONTRAST_JS, f"{lg}:設定")
            await pg.evaluate("closeSheet()"); await pg.wait_for_timeout(300)
        # 隠れている所：折りたたみ（見方など）を全部開いた各タブ、日程の詳しい欄（勝ち・負け・引き分け）、長押しの中身、選手の画面、オフの全部の種類
        await pg.evaluate("switchLeague('C')"); await pg.wait_for_timeout(300)
        for tab in ["magic", "game", "cal", "std", "stats", "rec", "song", "off"]:
            await pg.evaluate(f"setTab('{tab}'); document.querySelectorAll('#v-{tab} details').forEach(d => (d.open = true))"); await pg.wait_for_timeout(350)
            h = await pg.evaluate("document.documentElement.scrollHeight")
            for y in range(0, min(h, 12000), 760):
                await pg.evaluate(f"window.scrollTo(0,{y})"); await pg.wait_for_timeout(60)
                ng += await pg.evaluate(NIGHT_CONTRAST_JS, f"開いた:{tab}")
        await pg.evaluate("setTab('cal'); window.scrollTo(0,0)"); await pg.wait_for_timeout(300)
        for kind in ["w", "l", "d"]:
            ok = await pg.evaluate("""(kd) => { const t = S.calTeam; const g = DATA.games.find(x => x.st === 'final' && (x.h === t || x.a === t) && (kd === 'd' ? x.hs === x.as : kd === 'w' ? ((x.h === t ? x.hs - x.as : x.as - x.hs) > 0) : ((x.h === t ? x.hs - x.as : x.as - x.hs) < 0)));
              if (!g) return false; S.calMonth = +g.d.slice(5, 7); S.calSel = g.d; renderCal(); const e = document.getElementById('detail'); if (e) e.scrollIntoView({ block: 'center' }); return true; }""", kind)
            if ok:
                await pg.wait_for_timeout(250)
                ng += await pg.evaluate(NIGHT_CONTRAST_JS, f"日程の詳しい欄（{kind}）")
        for js, lab in [("openPeek({ kind: 'team', t: CL[0] })", "長押し（チーム）"), ("(() => { const g = DATA.games.find(x => x.st === 'final'); return g && openPeek({ kind: 'game', d: g.d, h: g.h, a: g.a }); })()", "長押し（試合）"),
                        ("openPeek({ kind: 'pl', t: CL[0], n: (DATA.rosters[CL[0]] || [])[3].n })", "長押し（選手）")]:
            await pg.evaluate(js); await pg.wait_for_timeout(350)
            ng += await pg.evaluate(NIGHT_CONTRAST_JS.replace("#peek:not(.on),", ""), lab)
            await pg.evaluate("closePeek()"); await pg.wait_for_timeout(250)
        await pg.evaluate("(async () => { const t = CL[0], ro = (DATA.rosters[t] || []).find(x => x.p === '投手'); await openPlayer(t, ro.n, { kind: 'pit' }); })()"); await pg.wait_for_timeout(800)
        ng += await pg.evaluate(NIGHT_CONTRAST_JS, "選手の画面")
        await pg.evaluate("""() => { const t = CL[0], ro = DATA.rosters[t] || [], kinds = ['cut', 'offer', 'leave', 'retire', 'mgr', 'out', 'in', 'fa_decl', 'draft', 'coach_in', 'coach_out', 'coach_move'];
          DATA.offseason = { season: 2026, items: kinds.map((k, i) => ({ t, n: k === 'mgr' || /coach/.test(k) ? 'テスト 監督' + i : ro[i].n, no: ro[i].no, kind: k, via: k === 'out' || k === 'in' ? 'trade' : '', to: k === 'out' ? CL[1] : '', from: k === 'in' ? CL[1] : '', role: '中', date: '2026-10-01', url: '', title: '', round: '1位' })), teams: {}, seen: {} };
          jst = () => ({ y: 2026, m: 10, d: 1, iso: '2026-10-01' }); S.offCat = 'all'; closeSheet(); renderAll(); setTab('off'); }""")
        await pg.wait_for_timeout(600)
        h = await pg.evaluate("document.documentElement.scrollHeight")
        for y in range(0, min(h, 6000), 760):
            await pg.evaluate(f"window.scrollTo(0,{y})"); await pg.wait_for_timeout(60)
            ng += await pg.evaluate(NIGHT_CONTRAST_JS, "オフ（全部の種類）")
        for m in list(dict.fromkeys(ng))[:10]:
            bad(f"[読みやすさ {label}] {m}")
        await pg.close()


async def runner_request_check(browser):
    """一球速報：打者が塁に出た直後の走者（ページのダイヤモンドは打席の前のまま）と、リクエストなどのできごとの表示"""
    pg, errs = await open_page(browser, 390, "pawa")
    await pg.evaluate("""() => { const t = CL[0], o = CL[1]; const g = DATA.games.find(x => x.h === t && x.a === o) || DATA.games.find(x => x.h === t);
      const today = g.d; jst = () => ({ y: +today.slice(0, 4), m: +today.slice(5, 7), d: +today.slice(8), iso: today }); liveWanted = () => false;
      Object.assign(g, { st: 'live', hs: 1, as: 1, inn: '4回表' }); S.open[g.d + gkey(g)] = true; renderAll(); setTab('game'); }""")
    await pg.wait_for_timeout(1300)
    r = await pg.evaluate("""() => { const ng = [], g = DATA.games.find(x => x.st === 'live'), k = g.d + gkey(g), p = PD[k], d = GD[k];
      const ro = (DATA.rosters[g.a] || []).filter(x => x.p !== '投手');
      // 死球で一二塁：ページの走者は打席の前（一塁にいた選手だけ）
      Object.assign(p, { half: '4回表', occ: ['1', '2'], rnames: [ro[0].n], runners: {}, bases: {}, req: null,
        batter: { name: ro[1].n, no: '52', hand: '右打', avg: '.125' },
        pitches: [{ n: 1, total: '58', type: 'ストレート', speed: '', res: '見逃し' }, { n: 2, total: '59', type: 'ストレート', speed: '147km/h', res: '死球' }] });
      d.notes = [{ half: '4回表', name: ro[1].n, text: 'リクエスト（判定変わらず）' }];
      renderGame();
      const tiles = [...document.querySelectorAll('.tgd .fldw .rtile')].map(e => e.textContent.replace(/\\s+/g, ''));
      const want1 = callName(g.a, ro[1].n).replace(/\\s+/g, ''), want2 = callName(g.a, ro[0].n).replace(/\\s+/g, '');
      if (tiles.length !== 2 || !tiles.includes(want1) || !tiles.includes(want2)) ng.push(`死球の直後の走者が違う（${tiles.join('・')}／正しくは ${want1}・${want2}）`);
      const one = [...document.querySelectorAll('.tgd .fldw .rtile')].find(e => e.textContent.replace(/\\s+/g, '') === want1);
      const two = [...document.querySelectorAll('.tgd .fldw .rtile')].find(e => e.textContent.replace(/\\s+/g, '') === want2);
      if (one && two && !(one.getBoundingClientRect().left > two.getBoundingClientRect().left)) ng.push('打者が一塁（右側）、前からいた走者が二塁（上）になっていない');
      // ベンチ：もう出た選手（登板した投手・打席に立った野手）は外す
      const bp = (DATA.rosters[g.h] || []).filter(x => x.p === '投手').slice(0, 3), bf = (DATA.rosters[g.h] || []).filter(x => x.p !== '投手').slice(0, 2);
      PRE[`${g.d}|${g.h}|${g.a}`] = Object.assign({}, PRE[`${g.d}|${g.h}|${g.a}`] || {}, { bench: { h: { '投手': bp.map(x => ({ n: x.n, st: '2.00' })), '内野手': bf.map(x => ({ n: x.n, st: '.250' })) }, a: {} } });
      d.pitchers = d.pitchers || [[], []]; d.pitchers[1] = (d.pitchers[1] || []).concat([{ name: bp[0].n, ip: '1', np: 15 }]);
      d.lineups = d.lineups || [[], []]; d.lineups[1] = (d.lineups[1] || []).concat([{ name: bf[0].n, order: '7', pos: '代打', results: ['中飛'] }]);
      S.stmOpen['b|' + g.d + gkey(g)] = true; renderGame();
      const bnames = [...document.querySelectorAll('.tgd .stm.bench .stl li')].map(e => e.dataset.pl.split('|')[1]);
      if (bnames.includes(bp[0].n)) ng.push(`登板した投手（${bp[0].n}）がベンチに残っている`);
      if (bnames.includes(bf[0].n)) ng.push(`代打で出た野手（${bf[0].n}）がベンチに残っている`);
      if (!bnames.includes(bp[1].n)) ng.push(`まだ出ていない投手（${bp[1].n}）がベンチから消えている`);
      const rq = document.querySelector('.tgd .preq');
      if (!rq || !/リクエスト/.test(rq.textContent) || !/判定変わらず/.test(rq.textContent)) ng.push('リクエストのできごとが一球速報に出ない');
      return ng; }""")
    for m in r:
        bad(f"[一球速報の走者・リクエスト] {m}")
    for e in errs:
        bad(f"[一球速報の走者・リクエスト]: 画面のエラー {e}")
    await pg.close()


async def meaning_color_check(browser):
    """色で意味を表す所が、4つの見た目すべてで説明どおりの色か：優勝ラインの「黄色＝ペース」、チーム成績のトップの数字（暗い画面は黄色・明るい画面は濃いオレンジ）。
    あわせて、呼び名からの戦力外の照らし合わせ（「山野」を山野辺翔と取り違えない）"""
    for theme, mode, label, dark in [("pawa", "light", "パワプロ風（昼）", False), ("pawa", "dark", "パワプロ風（夜）", True), ("", "dark", "スタイリッシュ（黒）", True), ("", "light", "スタイリッシュ（白）", False)]:
        pg, errs = await open_page(browser, 390, theme)
        await pg.evaluate(f"store('mode','{mode}'); applyPawaMode(); setTab('std'); renderAll()"); await pg.wait_for_timeout(500)
        r = await pg.evaluate("""(dark) => { const ng = [], rgb = s => (s.match(/[\\d.]+/g) || []).map(Number);
          const pc = document.querySelector('.race td.pace');
          if (pc) { const [r, g, b, a = 1] = rgb(getComputedStyle(pc).backgroundColor); if (!(r > b + 40 && g > b + 20 && a > .1)) ng.push(`優勝ラインの「黄色」の行が黄色くない（${getComputedStyle(pc).backgroundColor}）`); }
          setTab('stats'); renderAll();
          const bt = document.querySelector('#tmTbl td.best');
          if (bt) { const [r, g, b] = rgb(getComputedStyle(bt).color);
            if (dark ? !(r > 200 && g > 170 && b < 140) : !(r > 120 && r > g + 30 && b < 60)) ng.push(`チーム成績のトップの数字が${dark ? '黄色' : '濃いオレンジ'}でない（${getComputedStyle(bt).color}）`); }
          return ng; }""", dark)
        for m in r:
            bad(f"[色の意味 {label}] {m}")
        await pg.close()
    pg, errs = await open_page(browser, 390, "pawa")
    r = await pg.evaluate("""() => { DATA.rosters.S = DATA.rosters.S || []; for (const x of [{ n: '山野 太一', no: '47', p: '投手' }, { n: '山野辺 翔', no: '2', p: '内野手' }]) if (!DATA.rosters.S.some(r => r.n === x.n)) DATA.rosters.S.push(x);
      DATA.offseason = { season: 2026, items: [{ t: 'S', n: '山野辺 翔', kind: 'cut', date: '2026-09-29' }], teams: {}, seen: {} };
      return [offOf('S', '山野'), offOf('S', '山野 太一'), offOf('S', '山野辺')].map(x => x && x.n); }""")
    if r[0] or r[1]:
        bad(f"[戦力外の照らし合わせ] 「山野（山野太一）」を戦力外（山野辺翔）と取り違えている：{r}")
    if r[2] != "山野辺 翔":
        bad(f"[戦力外の照らし合わせ] 「山野辺」が戦力外の山野辺翔にならない：{r}")
    await pg.close()


async def kick_check(browser):
    """試合の結果が反映されていないとき：中継プログラムに自動更新を頼み（10分に1回まで）、上のお知らせを「取り込み直しています」に"""
    pg, errs = await open_page(browser, 390, "pawa")
    r = await pg.evaluate("""async () => { const ng = [], g = DATA.games.find(x => x.st === 'final' && inLg(x)); g.st = 'sched'; const calls = []; const of = window.fetch;
      window.fetch = (u, ...a) => { if (String(u).includes('kick=1')) { calls.push(u); return Promise.resolve(new Response(JSON.stringify({ kicked: true }))); } return of(u, ...a); };
      KICK.at = 0; await kickIfStuck(); await kickIfStuck(); renderHealth(); window.fetch = of;
      if (calls.length !== 1) ng.push(`取り込み直しの頼み方が違う（${calls.length}回）`);
      if (!/取り込み直しています/.test(document.getElementById('hWarn').textContent)) ng.push('上のお知らせが「取り込み直しています」にならない');
      g.st = 'final'; return ng; }""")
    for m in r:
        bad(f"[自動更新の取り込み直し] {m}")
    await pg.close()


async def csf_rule_check(browser):
    """2026年からのCSファイナルステージ：ファースト勝者が1位と10ゲーム差以上か勝率5割未満なら、2勝のアドバンテージ・7試合制（先に5勝・10/20に第7戦）"""
    pg, errs = await open_page(browser, 390, "pawa")
    r = await pg.evaluate("""() => { const ng = [];
      const rows = [{ t: 'A', w: 80, l: 60 }, { t: 'B', w: 72, l: 68 }, { t: 'C', w: 69, l: 71 }, { t: 'D', w: 71, l: 69 }];
      const b = csfRule(rows, 'A', 'B'), c = csfRule(rows, 'A', 'C'), d = csfRule(rows, 'A', 'D');
      if (b.two || b.adv !== 1 || b.need !== 4 || b.total !== 6) ng.push(`8ゲーム差・勝率5割以上なのに2勝のアドバンテージになる：${JSON.stringify(b)}`);
      if (!c.two || c.adv !== 2 || c.need !== 5 || c.total !== 7) ng.push(`勝率5割未満なのに2勝のアドバンテージにならない：${JSON.stringify(c)}`);
      if (d.two) ng.push(`9ゲーム差・勝率5割以上なのに2勝のアドバンテージになる：${JSON.stringify(d)}`);
      const e = csfRule([{ t: 'A', w: 85, l: 55 }, { t: 'E', w: 75, l: 65 }], 'A', 'E');
      if (!e.two) ng.push(`ちょうど10ゲーム差なのに2勝のアドバンテージにならない：${JSON.stringify(e)}`);
      // 勝ち抜け：2勝のアドバンテージで1位が3勝（計5勝）したら1位の勝ち
      const s = seriesOf([{ st: 'final', h: 'A', a: 'C', hs: 3, as: 1 }, { st: 'final', h: 'A', a: 'C', hs: 3, as: 1 }, { st: 'final', h: 'A', a: 'C', hs: 3, as: 1 }], 'A', 'C', 5, 2, 7);
      if (s.win !== 'A' || s.wh !== 5) ng.push(`2勝のアドバンテージで3勝しても勝ち抜けにならない：${JSON.stringify(s)}`);
      // パ・リーグ（今のデータでは1位が独走）：勝ち上がり表に新ルールの説明、日程に第7戦（10/20）
      switchLeague('P'); setTab('std'); renderAll();
      if (!/新ルール/.test((document.querySelector('.bk-rule') || {}).textContent || '')) ng.push('勝ち上がり表に新ルールの説明が出ない');
      const lp = lgPost('P');
      if ((lp.rule2.two || lp.rule3.two) && !postGames().some(g => g.stage === 'CSF' && g.no === 7)) ng.push('7試合制になりうるのに、日程に第7戦がない');
      switchLeague('C');
      return ng.filter(Boolean); }""")
    for m in r:
        bad(f"[CSの新ルール] {m}")
    for e in errs:
        bad(f"[CSの新ルール]: 画面のエラー {e}")
    await pg.close()


async def main():
    async with async_playwright() as p:
        browser = await p.chromium.launch()
        for theme in ["", "pawa"]:
            for width in [390, 320]:
                label = f"[{'パワプロ風' if theme else 'スタイリッシュ'} 幅{width}]"
                print(label)
                pg, errs = await open_page(browser, width, theme)
                await scan(pg, label)
                await essentials(pg, label)
                await game_live(pg, label)
                await sheets(pg, label)
                await cal_weather(pg, label)
                await starters_check(pg, label)
                await season_end(pg, label)
                for e in errs:
                    bad(f"{label}: 画面のエラー {e}")
                await pg.close()
                # パ・リーグに切り替えて、同じように全タブを検査する
                plabel = label.replace("]", " パ・リーグ]")
                pg, errs = await open_page(browser, width, theme)
                await pg.evaluate("switchLeague('P')")
                await pg.wait_for_timeout(300)
                if await pg.evaluate("LEAGUE") != "P" or await pg.evaluate("CL.join(',')") != "H,F,B,E,L,M":
                    bad(f"{plabel}: パ・リーグに切り替わらない")
                await scan(pg, plabel)
                shown = await pg.evaluate("[...document.querySelectorAll('.cl-only')].filter(e => e.offsetParent).length")
                if shown:
                    bad(f"{plabel}: セ・リーグだけの部分（支払いなど）が {shown} か所出ている")
                if await pg.evaluate("document.getElementById('stdTtl').textContent") != "パ・リーグ順位表":
                    bad(f"{plabel}: 順位表の見出しがパ・リーグになっていない")
                await pg.evaluate("switchLeague('C')")
                await pg.wait_for_timeout(200)
                if await pg.evaluate("CL.join(',')") != "DB,G,T,D,S,C":
                    bad(f"{plabel}: セ・リーグに戻らない")
                for e in errs:
                    bad(f"{plabel}: 画面のエラー {e}")
                await pg.close()
        # 担当を選んでいない人・選ぶ前の人
        for me in ["none", ""]:
            pg, errs = await open_page(browser, 390, "", me=me) if me else await open_page(browser, 390, "", me="")
            await scan(pg, f"[担当{me or 'まだ選んでいない'}]")
            for e in errs:
                bad(f"[担当{me}]: 画面のエラー {e}")
            await pg.close()
        await data_refresh(browser)
        await calc_cache(browser)
        await song_link_check(browser)
        await team_in_check(browser)
        await starter_order_check(browser)
        await wording_check(browser)
        await tap_target_check(browser)
        await swipe_check(browser)
        await pull_refresh_check(browser)
        await memory_check(browser)
        await loser_wording_check(browser)
        await next_day_check(browser)
        await home_screen_check(browser)
        await name_center_check(browser)
        await tabbar_check(browser)
        await offseason_check(browser)
        await weather_stop_check(browser)
        await call_name_check(browser)
        await pitch_tile_check(browser)
        await makeup_check(browser)
        await post_bracket_check(browser)
        await archive_check(browser)
        await song_list_check(browser)
        await consistency_check(browser)
        await owner_pos_check(browser)
        await team_rank_menu_check(browser)
        await speed_health_check(browser)
        await brand_check(browser)
        await pre_game_check(browser)
        await player_head_check(browser)
        await uniform_check(browser)
        await default_theme_check(browser)
        await live_off_check(browser)
        await off_pos_check(browser)
        await promote_check(browser)
        await tab_lens_check(browser)
        await fast_start_check(browser)
        await pos_icon_check(browser)
        await pitch_align_check(browser)
        await rec_check(browser)
        await tab_group_check(browser)
        await pennant_check(browser)
        await team_mark_check(browser)
        await cal_score_check(browser)
        await pitch_count_check(browser)
        await off_name_tag_check(browser)
        await player_today_check(browser)
        await runner_request_check(browser)
        await peek_check(browser)
        await league_switch_check(browser)
        await cal_post_check(browser)
        await night_check(browser)
        await light_check(browser)
        await line_image_check(browser)
        await contrast_all_check(browser)
        await meaning_color_check(browser)
        await kick_check(browser)
        await csf_rule_check(browser)
        await browser.close()
    print()
    # Actions の実行結果のページ（Summary）にも一覧を書く
    summary = os.environ.get("GITHUB_STEP_SUMMARY")
    if summary:
        with open(summary, "a", encoding="utf-8") as f:
            f.write("## サイト検査\n\n" + ("すべて問題なし\n" if not PROBLEMS else f"問題 {len(PROBLEMS)} 件\n\n" + "\n".join(f"- {m}" for m in PROBLEMS[:100]) + "\n"))
    if PROBLEMS:
        print(f"問題 {len(PROBLEMS)} 件")
        sys.exit(1)
    print("すべて問題なし")


if __name__ == "__main__":
    asyncio.run(main())
