// J SPORTS支払い回避マジック ライブ中継用 Cloudflare Worker
// NPB公式ページ上部の「今日の試合」欄を読んで、スコア・イニング・試合状況をJSONで返す。
// 取得結果は20秒だけ使い回して、NPBに負担をかけないようにしている。

const CODE = { g: "G", db: "DB", t: "T", d: "D", c: "C", s: "S", h: "H", f: "F", b: "B", e: "E", l: "L", m: "M" };
const CACHE_SEC = 20;

export default {
  // Cloudflare の「Cron Triggers」で決まった時刻に呼ばれる：GitHub のデータの自動更新（update-data）を動かす。
  // GitHub だけの予定（15分ごと）は、GitHub の都合で数時間あくことがあるため。Cloudflare の予定はほぼ時間どおりに動く
  async scheduled(event, env, ctx) {
    ctx.waitUntil(kickUpdate(env));
  },
  async fetch(request, env, ctx) {
    const cors = {
      "Access-Control-Allow-Origin": "*",
      "Access-Control-Allow-Methods": "GET, OPTIONS",
      "Content-Type": "application/json; charset=utf-8",
      "Cache-Control": `public, max-age=${CACHE_SEC}`,
    };
    if (request.method === "OPTIONS") return new Response(null, { headers: cors });

    const cache = caches.default;
    const url = new URL(request.url);
    const gameKey = url.searchParams.get("game");
    if (gameKey) return gameDetail(gameKey, cache, ctx, cors);
    // 成績（チーム成績・個人ランキング）：スポナビから直接取って10分使い回す
    const lg = url.searchParams.get("lg") === "P" ? "P" : "C";   // リーグ（C＝セ、P＝パ）
    if (url.searchParams.get("stats") === "team") return statsTeam(cache, cors, lg);
    // 選手の今季成績（NPB公式の球団別 個人打撃・投手成績）：球団ごとに1時間使い回す
    // 予告先発（スポナビの週間日程の「(予)○○」）：今日と明日の週を読み、15分使い回す
    if (url.searchParams.get("starters")) return starters(cache, cors);
    // 今日の試合の放送予定（テレビ・ネット配信・ラジオ）とスタメン（発表されたら）：スポナビの試合トップ。5分使い回す
    if (url.searchParams.get("pre")) return preGame(cache, cors);
    // 戦力外・引退など今オフの退団（スポナビの入退団情報）：GitHub の自動更新を待たずに、開いたときの最新を返す。10分使い回す
    if (url.searchParams.get("transfer")) return transferList(cache, cors);
    // GitHub のデータの自動更新を今すぐ動かす（サイトが「結果が反映されていない」と気づいたとき）。10分に1回まで
    if (url.searchParams.get("kick")) {
      if (!env || !env.GH_TOKEN) return new Response(JSON.stringify({ kicked: false, reason: "no_token" }), { headers: cors });
      const hit = await cache.match(new Request("https://cache.local/kick-update"));
      if (hit) return new Response(JSON.stringify({ kicked: false, reason: "recent" }), { headers: cors });
      const ok = await kickUpdate(env);
      await cache.put(new Request("https://cache.local/kick-update"), new Response("1", { headers: { "Cache-Control": "max-age=600" } }));
      return new Response(JSON.stringify({ kicked: !!ok }), { headers: cors });
    }
    // 選手の対チーム別・対左右別の成績（スポナビの選手のページ）。30分使い回す
    const spl = url.searchParams.get("split");
    if (spl && /^\d{3,9}$/.test(spl)) return playerSplit(spl, cache, cors);
    const pst = url.searchParams.get("pstats");
    if (pst) return playerStats(pst, cache, cors);
    const rankKey = url.searchParams.get("rank");
    if (rankKey) return statsRank(rankKey, cache, cors, lg);
    // 確かめ用：スポナビの試合のページを、中継プログラムが読んだ形（行ごと）で返す（?lines=<試合ID または T-C>&p=text|score）
    const linesKey = url.searchParams.get("lines");
    if (linesKey) return pageLines(linesKey, url.searchParams.get("p") === "score" ? "score" : "text", cache, cors);
    const pitchKey = url.searchParams.get("pitch");
    if (pitchKey) return pitchDetail(pitchKey, cache, cors);
    const keys = (url.searchParams.get("games") || "").split(",").filter(k => /^[A-Z]{1,2}-[A-Z]{1,2}$/.test(k)).slice(0, 6);
    if (keys.length) return liveFromYahoo(keys, cache, cors);
    const key = new Request("https://jsports-live.internal/v1");
    const hit = await cache.match(key);
    if (hit) return new Response(await hit.text(), { headers: cors });

    const now = new Date(Date.now() + 9 * 3600e3); // 日本時間
    const y = now.getUTCFullYear(), m = String(now.getUTCMonth() + 1).padStart(2, "0");
    let games = [], error = null;
    try {
      const r = await fetch(`https://npb.jp/games/${y}/schedule_${m}_detail.html`, {
        headers: { "User-Agent": "Mozilla/5.0 (jsports-live)" },
      });
      games = parse(await r.text());
    } catch (e) {
      error = String(e);
    }
    const body = JSON.stringify({ updated: new Date().toISOString(), games, error });
    ctx.waitUntil(cache.put(key, new Response(body, { headers: { "Cache-Control": `public, max-age=${CACHE_SEC}` } })));
    return new Response(body, { headers: cors });
  },
};

// ================= 雨などによる中断・開始の遅れ・中止・ノーゲーム・コールド =================
// 行ごとに見て、いちばん新しい状態を返す：{ kind: "中断"|"再開"|"遅延"|"中止"|"ノーゲーム"|"コールド", reason, at, from, text }
// チケットの払い戻しなどの案内（「中止時の払戻」など）は見ない
const WX_REASON = /(豪雨|大雨|降雨|雨天|雷雨|落雷|雷|濃霧|霧|強風|台風|天候不良|停電|地震|機器(?:の)?(?:トラブル|不具合)|照明(?:の)?(?:トラブル|不具合))/;
const WX_SKIP = /払い?戻|チケット|お知らせ|について|一覧|情報|ページ|予報|場合|時の|申込|販売|お問い?合わせ/;
// ニュースの見出し・ほかの日の日程や結果（例：「9月29日中止振替分『阪神-ヤクルト』が8日に決定 日テレNEWS 2026/10/2 14:10」）は、この試合の状態ではない
const WX_NEWS = /【|】|『|』|\d{4}\/\d{1,2}\/\d{1,2}|(?:^|\s)\d{1,2}\/\d{1,2}(?:\s|\(|$)|\d{1,2}\s*月\s*\d{1,2}\s*日|配信|ニュース|NEWS|新聞|報知|日刊|スポニチ|デイリー|サンスポ|サンケイ|共同|時事|Full-Count|ベースボール|振替|代替|追加日程|予備日|発表/;
// 試合ページの見出し（「10月2日(金) 18:00 神宮」〜「先攻」）：この試合の状態（試合前・中止・試合終了など）が書かれている所
function headerOf(lines) {
  const i = lines.findIndex(l => /^\d{1,2}月\d{1,2}日\s*\(.\)/.test(String(l).normalize("NFKC")));
  if (i < 0) return [];
  const out = [lines[i]];
  for (let k = i + 1; k < Math.min(lines.length, i + 12); k++) {
    const l = String(lines[k]).normalize("NFKC");
    if (/最近|日程|順位|ニュース|見どころ|予告先発|スタメン|放送|対戦/.test(l)) break;
    out.push(lines[k]);
    if (/^(先攻|後攻)$/.test(l) && out.some(x => /^(先攻|後攻)$/.test(String(x)) && x !== lines[k])) break;
  }
  return out;
}
// lines：中断・再開・遅延を探す所。finals：中止・ノーゲーム・コールドを探す所（試合ページでは見出しだけ。NPBの試合欄はその試合の文だけなので同じでよい）
function weatherOf(lines, finals = lines) {
  const ev = [], res = [];
  const finalSet = new Set(finals.map(x => String(x || "").normalize("NFKC")));
  lines = [...new Set([...lines, ...finals])];
  lines.forEach((raw, i) => {
    const l = String(raw || "").normalize("NFKC");
    const inHead = finalSet.has(l);
    if (l.length > 80 || WX_SKIP.test(l)) return;
    if (WX_NEWS.test(l) && !(inHead && /^\d{1,2}月\d{1,2}日\s*\(.\)/.test(l) && !/【|】|『|』|振替|代替|追加日程|予備日|ニュース|NEWS|配信/.test(l))) return;
    const tm = (l.match(/(\d{1,2}):(\d{2})/) || [])[0] || null, why = (l.match(WX_REASON) || [])[1] || null;
    if (/再開/.test(l) && !/中断/.test(l.replace(/再開.*$/, "")) || /^(?:\d{1,2}:\d{2}\s*)?試合再開/.test(l)) { res.push({ i, at: tm }); return; }
    let kind = null;
    if (/ノーゲーム/.test(l)) kind = inHead ? "ノーゲーム" : null;
    else if (/コールド/.test(l)) kind = inHead ? "コールド" : null;
    else if (/(試合|本日)?中止/.test(l) && !/中止(?:の)?場合/.test(l)) kind = inHead ? "中止" : null;
    else if (/中断/.test(l)) kind = "中断";
    else if (/開始(?:時刻|時間)?(?:を|が)?(?:遅らせ|繰り下げ|遅れ|遅延)|開始遅延|試合(?:開始)?(?:の)?遅延/.test(l)) kind = "遅延";
    if (!kind) return;
    // 「中断」「遅延」は理由（雨・雷など）か時刻が書かれている行だけ（ほかの話の「中断」を拾わないように）
    if ((kind === "中断" || kind === "遅延") && !why && !tm && !/^試合(?:一時)?中断|中断中/.test(l)) return;
    ev.push({ i, kind, reason: why, at: tm, text: l.slice(0, 60) });
  });
  if (!ev.length) return null;
  const pick = k => ev.find(e => e.kind === k);
  for (const k of ["中止", "ノーゲーム", "コールド"]) { const e = pick(k); if (e) return { kind: k, reason: e.reason || (ev.find(x => x.reason) || {}).reason || null, at: e.at, text: e.text }; }
  const toMin = t => t ? +t.split(":")[0] * 60 + +t.split(":")[1] : null;
  const stop = pick("中断");
  if (stop) {
    // 中断のあとに再開があれば「再開」（時刻で比べる。時刻がなければ、再開の行があれば再開とみなす）
    const st = ev.filter(e => e.kind === "中断").sort((a, b) => (toMin(b.at) ?? -1) - (toMin(a.at) ?? -1))[0];
    const back = res.filter(r => st.at && r.at ? toMin(r.at) >= toMin(st.at) : true).sort((a, b) => (toMin(b.at) ?? -1) - (toMin(a.at) ?? -1))[0];
    const reason = st.reason || (ev.find(x => x.reason) || {}).reason || null;
    return back ? { kind: "再開", reason, from: st.at, at: back.at, text: st.text } : { kind: "中断", reason, at: st.at, text: st.text };
  }
  const late = pick("遅延");
  return late ? { kind: "遅延", reason: late.reason, at: late.at, text: late.text } : null;
}

function parse(html) {
  const out = [], seen = new Set();
  const re = /<a[^>]+href="[^"]*\/scores\/(\d{4})\/(\d{2})(\d{2})\/([a-z]+)-([a-z]+)-\d+\/?"[^>]*>([\s\S]*?)<\/a>/g;
  let mt;
  while ((mt = re.exec(html))) {
    const h = CODE[mt[4]], a = CODE[mt[5]];
    if (!h || !a) continue;
    const d = `${mt[1]}-${mt[2]}-${mt[3]}`, k = d + h + a;
    if (seen.has(k)) continue;
    const text = mt[6]
      .replace(/<img[^>]*alt="([^"]*)"[^>]*>/g, " $1 ")
      .replace(/<[^>]+>/g, " ")
      .replace(/&nbsp;/g, " ")
      .normalize("NFKC")
      .replace(/\s+/g, " ")
      .trim();
    const g = { d, h, a };
    const w = weatherOf([text]);
    if (w) g.w = w;
    const sc = text.match(/(\d+)\s*-\s*(\d+)/);
    const inn = text.match(/(\d+)回(表|裏)/);
    const tm = text.match(/(\d{1,2}:\d{2})/);
    if (/試合終了/.test(text) && sc) Object.assign(g, { st: "final", hs: +sc[1], as: +sc[2] });
    else if (inn && sc) Object.assign(g, { st: "live", hs: +sc[1], as: +sc[2], inn: `${inn[1]}回${inn[2]}` });
    else if (/中止|ノーゲーム/.test(text)) g.st = "canc";
    else if (tm) Object.assign(g, { st: "sched", t: tm[1] });
    else continue; // 下の日程表の中のリンク（状況が書かれていないもの）は使わない
    seen.add(k);
    out.push(g);
  }
  return out;
}

// ================= 試合詳細（スポナビのテキスト速報ページから、スコア表・得点シーン・今の打席を作る） =================
const YNAME = { "DeNA": "DB", "阪神": "T", "巨人": "G", "中日": "D", "広島": "C", "ヤクルト": "S",
  "ソフトバンク": "H", "日本ハム": "F", "オリックス": "B", "楽天": "E", "西武": "L", "ロッテ": "M" };
const ABBR = "デ|神|巨|中|広|ヤ|ソ|日|オ|楽|西|ロ";

function toText(html) {
  return html
    .replace(/<script[\s\S]*?<\/script>|<style[\s\S]*?<\/style>/g, " ")
    .replace(/<br\s*\/?>/g, "\n")
    .replace(/<\/(p|div|li|h[1-6]|tr|table|section|ul|ol|dd|dt)>/g, "\n")
    .replace(/<[^>]+>/g, " ")
    .replace(/&nbsp;/g, " ").replace(/&amp;/g, "&").replace(/&#39;/g, "'").replace(/&quot;/g, '"')
    .normalize("NFKC")
    .split("\n").map(x => x.replace(/\s+/g, " ").trim()).filter(Boolean);
}

async function cachedFetch(cache, key, sec, fn) {
  const k = new Request("https://jsports-live.internal/" + encodeURIComponent(key));
  const hit = await cache.match(k);
  if (hit) return JSON.parse(await hit.text());
  const v = await fn();
  const empty = !v || (typeof v === "object" && !Array.isArray(v) && Object.keys(v).length === 0);
  await cache.put(k, new Response(JSON.stringify(v), { headers: { "Cache-Control": `public, max-age=${empty ? 60 : sec}` } }));
  return v;
}

// 試合ページのタイトル（例：「2026年9月27日 読売ジャイアンツvs.東京ヤクルトスワローズ」）の球団名
const FULL = { "読売ジャイアンツ": "G", "横浜DeNAベイスターズ": "DB", "阪神タイガース": "T", "中日ドラゴンズ": "D",
  "広島東洋カープ": "C", "東京ヤクルトスワローズ": "S", "福岡ソフトバンクホークス": "H", "北海道日本ハムファイターズ": "F",
  "オリックス・バファローズ": "B", "東北楽天ゴールデンイーグルス": "E", "埼玉西武ライオンズ": "L", "千葉ロッテマリーンズ": "M" };
const UA = { headers: { "User-Agent": "Mozilla/5.0 (jsports-live)" } };

// 今日の試合のスポナビの試合ID（"G-DB" → ID）。スポナビのプロ野球トップと、今日の日程ページ（CS・日本シリーズも載る）の試合ページのリンクを拾い、
// 各試合ページのタイトル（例：「2026年9月27日 読売ジャイアンツvs.東京ヤクルトスワローズ」）から日付と対戦カードを読む。一球速報・スコア・試合中の点数で使う
async function todayIds() {
  const now = new Date(Date.now() + 9 * 3600e3);
  const today = `${now.getUTCFullYear()}年${now.getUTCMonth() + 1}月${now.getUTCDate()}日`, iso = now.toISOString().slice(0, 10);
  const pages = await Promise.all(["https://baseball.yahoo.co.jp/npb/", `https://baseball.yahoo.co.jp/npb/schedule/first/all?date=${iso}`]
    .map(u => fetch(u, UA).then(r => (r.ok ? r.text() : "")).catch(() => "")));
  const ids = [...new Set(pages.flatMap(h => [...h.matchAll(/\/npb\/game\/(\d{8,12})\//g)].map(m => m[1])))].slice(0, 24);
  const map = {};
  await Promise.all(ids.map(async id => {
    try {
      const html = await (await fetch(`https://baseball.yahoo.co.jp/npb/game/${id}/text`, UA)).text();
      const t = (html.match(/<title>([^<]*)<\/title>/) || [])[1] || "";
      const m = t.normalize("NFKC").match(/(\d{4}年\d{1,2}月\d{1,2}日)\s*(.+?)vs\.(.+?)\s/);
      if (m && m[1] === today && FULL[m[2]] && FULL[m[3]]) map[`${FULL[m[2]]}-${FULL[m[3]]}`] = id;
    } catch {}
  }));
  return map;
}


function kindOf(t) {
  if (/ホームラン|本塁打|ランニングホーマー/.test(t)) return "本塁打";
  if (/犠牲フライ|犠飛/.test(t)) return "犠飛";
  if (/押し出し/.test(t)) return "押し出し";
  if (/スクイズ/.test(t)) return "スクイズ";
  if (/タイムリー|適時/.test(t)) return "適時打";
  if (/暴投|ワイルドピッチ/.test(t)) return "暴投";
  if (/捕逸|パスボール/.test(t)) return "捕逸";
  if (/エラー|失策|後逸|悪送球|落球/.test(t)) return "敵失";
  if (/ボーク/.test(t)) return "ボーク";
  if (/併殺|ゲッツー|ダブルプレー/.test(t)) return "併殺の間";
  if (/ゴロ/.test(t)) return "ゴロの間";
  if (/盗塁|本盗/.test(t)) return "盗塁";
  return "得点";
}

async function pageLines(key, kind, cache, cors) {
  try {
    let id = /^\d{8,12}$/.test(key) ? key : null;
    if (!id) { const d = new Date(Date.now() + 9 * 3600e3).toISOString().slice(0, 10); id = (await cachedFetch(cache, "ids2-" + d, 300, todayIds))[key]; }
    if (!id) return new Response(JSON.stringify({ error: "not_found", key }), { headers: cors });
    const html = await (await fetch(`https://baseball.yahoo.co.jp/npb/game/${id}/${kind}`, UA)).text();
    const lines = toText(html), i0 = Math.max(0, lines.findIndex(l => kind === "text" ? l === "テキスト速報" : /^\d+回(表|裏)/.test(l)));
    const cls = kind === "score" ? [...new Set([...html.matchAll(/class="([^"]*(?:[Bb]ase|[Rr]unner)[^"]*)"/g)].map(m => m[1]))].slice(0, 40) : undefined;
    const parsed = kind === "text" ? (({ live, now, over, flows }) => ({ live, now, over, flows: (flows || []).length }))(parseGame(html)) : parsePitch(html);
    return new Response(JSON.stringify({ id, kind, lines: lines.slice(i0, i0 + 260), cls, parsed }), { headers: cors });
  } catch (e) {
    return new Response(JSON.stringify({ error: String(e) }), { headers: cors });
  }
}

function debugLines(html) {
  const lines = toText(html);
  const st = lines.findIndex(l => l === "テキスト速報");
  return lines.slice(Math.max(0, st), Math.max(0, st) + 80);
}

const DP_RE = /ダブルプレー|併殺|ゲッツー/, DP_NG = /崩れ|狙|ならず|取れず|逃|失敗/;
function parseGame(html) {
  // スコア表
  let line = null;
  const tables = html.match(/<table[\s\S]*?<\/table>/g) || [];
  for (const tb of tables) {
    const rows = (tb.match(/<tr[\s\S]*?<\/tr>/g) || []).map(tr =>
      (tr.match(/<t[dh][\s\S]*?<\/t[dh]>/g) || []).map(c => c.replace(/<[^>]+>/g, " ").normalize("NFKC").replace(/\s+/g, " ").trim()));
    if (rows.length >= 3 && rows[0].includes("計") && rows[0].includes("安") && rows[0].includes("失")) {
      const head = rows[0], n = head.indexOf("計") - 1;
      const team = r => ({ name: r[0], inn: r.slice(1, 1 + n), r: r[1 + n], h: r[2 + n], e: r[3 + n] });
      line = { innings: head.slice(1, 1 + n), away: team(rows[1]), home: team(rows[2]) };
      break;
    }
  }
  // 経過（事実だけを取り出す）。試合中は新しい回が上に来ることがあるので、並びを確かめてから古い順に直す
  const lines = toText(html);
  const st = lines.findIndex(l => l === "テキスト速報");
  const halves = [];
  let cur = null, bat = null, over = false;
  // 打席の見出し（「4番 小野寺 暖 無死走者なし」）。ページによって打順・名前・状況が別々の行になることがあるので、3行先までつないで確かめる
  const PA1 = /(?:^|\s|:)(\d+番|代打|代走)\s*:?\s*(.+?)\s+(無死|一死|二死)\s*(走者なし|満塁|[一二三]+塁)/;
  const PAN = /^(\d+番|代打|代走)\s*:?\s*(.+?)\s+(無死|一死|二死)\s*(走者なし|満塁|[一二三]+塁)$/;
  const TOK = /^(\d+番|代打|代走)/;
  for (let i = st < 0 ? 0 : st + 1; i < lines.length; i++) {
    const l = lines[i];
    // 「テキスト速報」の見出しのあとなら、下の「今日の日程・結果」などで止める（見出しが見つからないときは、本文が始まってから）
    if ((st >= 0 || halves.length) && /^新着動画|の日程・結果$/.test(l)) break;
    let m;
    if ((m = l.match(/^(\d+)回(表|裏)(?:\s|$)/))) { cur = { half: `${m[1]}回${m[2]}`, n: +m[1] * 2 + (m[2] === "裏" ? 1 : 0), bats: [] }; halves.push(cur); bat = null; continue; }
    if (!cur) continue;
    m = l.match(PA1);
    let used = 0;
    // 打順・名前・状況が別々の行のとき：この行が「4番」「4番 小野寺 暖」「代打:松山」だけのときに限って、次の行（打席の見出しで始まらない行）とつなぐ
    if (!m && /^(\d+番|代打|代走)(\s*:?\s*[^\s:]+(\s[^\s:]+)?)?$/.test(l))
      for (let k = 1; k <= 3 && !m && i + k < lines.length && !TOK.test(lines[i + k]) && !PA1.test(lines[i + k]); k++) { const j = lines.slice(i, i + k + 1).join(" "); if ((m = j.match(PAN))) used = k; }
    if (m) { bat = { order: m[1], name: m[2], sit: m[3] + m[4], ev: [] }; cur.bats.push(bat); i += used; continue; }
    if (bat && /試合終了/.test(l)) over = true;   // 打席の中の「試合終了」だけ（ほかの試合の「試合終了」は拾わない）
    if (bat) bat.ev.push(l);
  }
  // 打席のない回（本文でない所の「7回表」など）は捨てる
  for (let i = halves.length - 1; i >= 0; i--) if (!halves[i].bats.length) halves.splice(i, 1);
  // 並び：試合中は新しい回・新しい打席が上に来る。回の並びと、回の中のアウトの数（無死→一死→二死）・打順で、古い順に直す
  const desc = halves.length > 1 && halves[0].n > halves[halves.length - 1].n;
  if (desc) halves.reverse();
  const outsOf = b => ({ 無死: 0, 一死: 1, 二死: 2 })[String(b.sit).slice(0, 2)] ?? 0;
  const ordOf = b => { const m2 = String(b.order).match(/^(\d+)番/); return m2 ? +m2[1] : null; };
  for (const h of halves) {
    const B = h.bats; if (B.length < 2) continue;
    const o0 = outsOf(B[0]), o1 = outsOf(B[B.length - 1]);
    let rev = o0 > o1;
    if (o0 === o1) { const a = ordOf(B[0]), b2 = ordOf(B[1]); rev = a != null && b2 != null && a !== b2 ? (a - b2 + 9) % 9 === 1 : desc; }
    if (rev) B.reverse();
  }
  // 併殺打（ダブルプレー）になった打席：出場成績が「三ゴロ」のままのときに「三併打」に直すため（何回の、その打者の何打席目か）
  const dps = [];
  for (const h of halves) {
    const seen = {};
    for (const b of h.bats) {
      const k = seen[b.name] || 0; seen[b.name] = k + 1;
      if (b.ev.some(l => DP_RE.test(l) && !DP_NG.test(l))) dps.push({ half: h.half, name: b.name, k });
    }
  }
  const scoreRe = new RegExp(`(${ABBR}) (\\d+)-(\\d+) (${ABBR})`, "g");
  const plays = [], flows = [];
  let lastScore = null, now = null;
  for (const h of halves) {
    const steps = [];
    let scored = false;
    for (const b of h.bats) {
      const txt = b.ev.join(" ");
      const all = [...txt.matchAll(scoreRe)];
      let score = null;
      if (all.length) {
        const sc = all[all.length - 1], key = `${sc[1]} ${sc[2]}-${sc[3]} ${sc[4]}`;
        if (key !== lastScore) { plays.push({ half: h.half, order: b.order, name: b.name, kind: kindOf(txt), score: key }); lastScore = key; score = key; scored = true; }
      }
      steps.push({ order: b.order, name: b.name, sit: b.sit, score });
      now = { half: h.half, order: b.order, name: b.name, sit: b.sit };
    }
    if (scored) flows.push({ half: h.half, steps });
  }
  // 目立つできごと：リクエスト（リプレー検証）・判定の変更・危険球・退場・警告など（テキスト速報の文から）
  const notes = [];
  for (const h of halves) for (const b of h.bats) for (const l of b.ev) {
    const t = l.normalize("NFKC").replace(/\s+/g, " ").trim();
    if (/リクエスト|リプレー検証|リプレイ検証|判定(が|は)?(変更|覆|変わ|変わらず|どおり|通り)|危険球|退場|警告試合|警告|抗議|没収/.test(t) && t.length <= 80) notes.push({ half: h.half, name: b.name, text: t });
  }
  // 中断・遅延は、この試合の本文だけで探す（下の「今日の日程・結果」「順位表」「新着ニュース」には、ほかの試合の中断・中止やニュースが並ぶ）
  const side = lines.findIndex(l => /^(?:\d{1,2}月\d{1,2}日\s*\(.\)\s*の日程・結果|順位表|新着ニュース|ニュース一覧|セ・リーグ順位表|パ・リーグ順位表)$/.test(String(l).normalize("NFKC")));
  const w = weatherOf(lines.slice(0, side > 0 ? Math.min(side, 400) : 400), headerOf(lines));
  // いちばん新しい回の打席ごとの状況（「一死一三塁」）とできごと（代走・盗塁・けん制など）：画面で塁上の走者を順に追いかけるため
  const lastH = halves[halves.length - 1];
  const live = over || !lastH ? null : { half: lastH.half, bats: lastH.bats.map(b => ({ order: b.order, name: b.name, sit: b.sit, ev: b.ev.slice(0, 10).map(x => String(x).slice(0, 100)) })) };
  return { line, plays, flows, notes: notes.slice(-6), now: over ? null : now, live, over, dps, ...(w ? { w } : {}) };
}

const SHORTN = { "DeNA": "デ", "阪神": "神", "巨人": "巨", "中日": "中", "広島": "広", "ヤクルト": "ヤ",
  "ソフトバンク": "ソ", "日本ハム": "日", "オリックス": "オ", "楽天": "楽", "西武": "西", "ロッテ": "ロ" };
function flowsFromBox(line, lineups) {
  if (!line || !line.away || !line.home || !lineups || lineups.length < 2) return { plays: [], flows: [], all: [] };
  const plays = [], flows = [], all = [];
  const sh = n => SHORTN[n] || (n || "").slice(0, 1);
  const hS = sh(line.home.name), aS = sh(line.away.name);
  let hs = 0, as = 0;
  const lead = [1, 1]; // 各チームの次の回の先頭打者の打順
  const n = line.innings.length;
  for (let i = 0; i < n; i++) {
    for (const side of [0, 1]) { // 0=表（ビジター）, 1=裏（ホーム）
      const cell = (side ? line.home : line.away).inn[i];
      const runs = /^\d+X?$/i.test(String(cell || "").trim()) ? parseInt(cell, 10) : null;   // サヨナラの回は「1X」
      // この回の打席を、打順ごとに並べる
      const slots = {};
      for (const r of lineups[side] || []) {
        const v = String((r.inn || [])[i] || "").split(/[ 、]+/).filter(Boolean);
        if (!v.length) continue;
        (slots[r.order] = slots[r.order] || []).push(...v.map(res => ({ name: r.name, res })));
      }
      const total = Object.values(slots).reduce((s2, x) => s2 + x.length, 0);
      if (!total) continue;
      const steps = [];
      let k = lead[side], guard = 0, last = lead[side];
      while (steps.length < total && guard++ < 40) {
        const q = slots[k];
        if (q && q.length) { const x = q.shift(); steps.push({ order: `${k}番`, name: x.name, sit: null, res: x.res, score: null }); last = k; }
        k = k % 9 + 1;
      }
      lead[side] = last % 9 + 1;
      all.push({ half: `${i + 1}回${side ? "裏" : "表"}`, steps: steps.map(x => ({ ...x })), runs: runs || 0 });
      if (runs == null) continue;
      if (side) hs += runs; else as += runs;
      if (runs > 0) {
        const half = `${i + 1}回${side ? "裏" : "表"}`, score = `${hS} ${hs}-${as} ${aS}`;
        if (steps.length) steps[steps.length - 1].score = null;
        const hr = steps.some(x => /本/.test(x.res));
        plays.push({ half, order: "", name: `${side ? line.home.name : line.away.name}の攻撃`, kind: `${runs}点${hr ? "・本塁打" : ""}`, score });
        flows.push({ half, steps, runs, box: true });
      }
    }
  }
  return { plays, flows, all };
}

async function gameDetail(key, cache, ctx, cors) {
  try {
    const d = new Date(Date.now() + 9 * 3600e3).toISOString().slice(0, 10);
    const ids = await cachedFetch(cache, "ids2-" + d, 300, todayIds);
    const id = ids[key];
    if (!id) return new Response(JSON.stringify({ error: "not_found", key, found: Object.keys(ids) }), { headers: cors });
    const [data, box] = await Promise.all([
      cachedFetch(cache, "game4-" + id, 8, async () => {
        const r = await fetch(`https://baseball.yahoo.co.jp/npb/game/${id}/text`, UA);
        return parseGame(await r.text());
      }),
      cachedFetch(cache, "box6-" + id, 15, async () =>
        parseBox(await (await fetch(`https://baseball.yahoo.co.jp/npb/game/${id}/stats`, UA)).text())).catch(() => ({ lineups: [] })),
    ]);
    // lineups[0] がビジター、lineups[1] がホーム（スポナビの並び順）
    const nm = x => (x || "").normalize("NFKC").replace(/\s+/g, "").replace(/髙/g, "高").replace(/﨑/g, "崎");
    fixDoublePlays(box, data.dps, nm);
    for (const f of data.flows || []) {
      const lu = (box.lineups || [])[f.half.includes("表") ? 0 : 1] || [], idx = parseInt(f.half, 10) - 1, used = {};
      for (const st of f.steps) {
        const row = lu.find(r => nm(r.name) === nm(st.name));
        const parts = row && row.inn ? String(row.inn[idx] || "").split(/[ 、]+/).filter(Boolean) : [];
        const k = used[st.name] || 0;
        st.res = parts[k] || null; used[st.name] = k + 1;
      }
    }
    if (data.line) {
      const fb = flowsFromBox(data.line, box.lineups);
      if (!(data.plays && data.plays.length) && fb.plays.length) { data.plays = fb.plays; data.flows = fb.flows; data.fromBox = true; }
      data.cur = fb.all[fb.all.length - 1] || null; // 走者名の割り出し用：いちばん新しい回の打席
    }
    const lineups = (box.lineups || []).map(l => l.map(({ inn, ...r }) => r));
    // pitchers[0] がビジター、pitchers[1] がホーム（スポナビの並び順）
    return new Response(JSON.stringify({ id, url: `https://baseball.yahoo.co.jp/npb/game/${id}/text`, ...data, lineups, pitchers: box.pitchers || [] }), { headers: cors });
  } catch (e) {
    return new Response(JSON.stringify({ error: String(e) }), { headers: cors });
  }
}

// ================= 途中経過（スポナビ優先、取れない試合だけNPB公式で補う） =================
function statusOf(data) {
  const L = data && data.line;
  if (!L || !L.away || !L.home) return null;
  const has = x => x.inn.some(v => /\d/.test(v));
  if (!has(L.away) && !has(L.home)) return null; // まだ始まっていない
  const hs = parseInt(L.home.r, 10) || 0, as = parseInt(L.away.r, 10) || 0;
  const w = data.w ? { w: data.w } : {};
  if (data.over) return { st: "final", hs, as, ...w };
  let inn = data.now && data.now.half;
  if (!inn) { // 経過が読めないときはスコア表の埋まり方からイニングを出す
    const last = x => { let k = -1; x.inn.forEach((v, i) => { if (/\d|X|x/.test(v)) k = i; }); return k; };
    const a = last(L.away), h = last(L.home);
    inn = h >= a ? `${h + 2}回表` : `${a + 1}回裏`;
  }
  return { st: "live", hs, as, inn, ...w };
}

async function liveFromYahoo(keys, cache, cors) {
  const d = new Date(Date.now() + 9 * 3600e3).toISOString().slice(0, 10);
  const games = [], missing = [], wx = {};
  let ids = {};
  try { ids = await cachedFetch(cache, "ids2-" + d, 300, todayIds); } catch {}
  await Promise.all(keys.map(async k => {
    const id = ids[k];
    if (!id) { missing.push(k); return; }
    try {
      const data = await cachedFetch(cache, "game4-" + id, 8, async () =>
        parseGame(await (await fetch(`https://baseball.yahoo.co.jp/npb/game/${id}/text`, UA)).text()));
      const st = statusOf(data);
      if (!st) { if (data && data.w) wx[k] = data.w; missing.push(k); return; }
      const [h, a] = k.split("-");
      games.push({ d, h, a, src: "yahoo", ...st });
    } catch { missing.push(k); }
  }));
  if (missing.length) {
    try {
      const y = d.slice(0, 4), m = d.slice(5, 7);
      const npb = await cachedFetch(cache, "npb-" + d, 10, async () =>
        parse(await (await fetch(`https://npb.jp/games/${y}/schedule_${m}_detail.html`, UA)).text()));
      for (const k of missing) {
        const [h, a] = k.split("-");
        const g = npb.find(x => x.d === d && x.h === h && x.a === a);
        if (g) games.push({ ...g, ...(wx[k] && !g.w ? { w: wx[k] } : {}), src: "npb" });
        else if (wx[k]) games.push({ d, h, a, st: "sched", w: wx[k], src: "yahoo" });
      }
    } catch {
      for (const k of missing) if (wx[k]) { const [h, a] = k.split("-"); games.push({ d, h, a, st: "sched", w: wx[k], src: "yahoo" }); }
    }
  }
  return new Response(JSON.stringify({ updated: new Date().toISOString(), games }), { headers: cors });
}

// ================= 一球速報（スポナビの一球速報ページから、今の打席の事実データだけを取り出す） =================
function parsePitch(html) {
  const lines = toText(html);
  const i0 = lines.findIndex(l => /^\d+回(表|裏)/.test(l));
  if (i0 < 0) return null;
  const seg = lines.slice(i0, i0 + 120).join(" ");
  const half = (seg.match(/^(\d+回[表裏])/) || [])[1] || null;
  const bso = seg.match(/B\s*(●*)\s*S\s*(●*)\s*O\s*(●*)/);
  const bat = seg.match(/打者\s+(\S+(?:\s\S+)?)\s+#(\d+)\s+(右打|左打|両打)\s+打率\s+([.\d-]+)/);
  const pit = seg.match(/投手\s+(\S+(?:\s\S+)?)\s+#(\d+)\s+(右投|左投)\s+投球数\s+打者数\s+防御率\s+(\d+)\s+(\d+)\s+([\d.-]+)/);
  const nxt = seg.match(/次の打者\s+(\S+(?:\s\S+)?)/);
  const atk = seg.match(/(\S+)攻撃中/);
  const pitches = [];
  const tables = html.match(/<table[\s\S]*?<\/table>/g) || [];
  for (const tb of tables) {
    const t = tb.replace(/<[^>]+>/g, " ");
    if (!/球種/.test(t) || !/球速/.test(t)) continue;
    for (const tr of tb.match(/<tr[\s\S]*?<\/tr>/g) || []) {
      const c = (tr.match(/<td[\s\S]*?<\/td>/g) || []).map(x => x.replace(/<[^>]+>/g, " ").normalize("NFKC").replace(/\s+/g, " ").trim());
      if (c.length < 5 || !/^\d+$/.test(c[0])) continue;
      const n = c.length;
      pitches.push({ n: +c[0], total: c[n - 4], type: c[n - 3], speed: c[n - 2], res: c[n - 1].replace(/\[[^\]]*\]/g, "").trim() });
    }
    break;
  }
  // 塁上のランナー名：「runner」「base」を含む要素に、一塁／二塁／三塁の手がかりと名前がそろっているときだけ使う
  const runners = {};
  for (const m of html.matchAll(/<[^>]+class="[^"]*(?:[Rr]unner|[Bb]ase)[^"]*"[^>]*>([\s\S]{0,200}?)<\/(?:div|span|p|li|a|dd)>/g)) {
    const tag = m[0].slice(0, 200), name = m[1].replace(/<[^>]+>/g, " ").normalize("NFKC").replace(/\s+/g, " ").trim();
    if (!/^[\p{Script=Han}\p{Script=Katakana}\p{Script=Hiragana}ー・. ]{1,10}$/u.test(name)) continue;
    const b = /first|base1|1st|一塁/i.test(tag) ? "1" : /second|base2|2nd|二塁/i.test(tag) ? "2" : /third|base3|3rd|三塁/i.test(tag) ? "3" : null;
    if (b && !runners[b]) runners[b] = name;
  }
  const bases = {};
  for (const m of html.matchAll(/<[a-z]+[^>]*class="([^"]*)"[^>]*>/g)) {
    const c = m[1];
    if (!/base|runner|Base|Runner/.test(c)) continue;
    const b = /first|1st|base1|Base1|--1\b|-1\b/.test(c) ? "1" : /second|2nd|base2|Base2|--2\b|-2\b/.test(c) ? "2" : /third|3rd|base3|Base3|--3\b|-3\b/.test(c) ? "3" : null;
    if (b && /(?:is-|--)?(?:on|active|runner|exist|occupied|stay)\b|isOn|isActive/.test(c.replace(/base/gi, ""))) bases[b] = true;
  }
  // 今の打者と投手の今季の対戦成績（スポナビの一球速報の「対戦成績」）。表でも文でも読めるように
  let vs = null;
  for (const tb of tables) {
    const t = tb.replace(/<[^>]+>/g, " ").normalize("NFKC");
    if (!/対戦/.test(t) || !/打数/.test(t)) continue;
    const rows = (tb.match(/<tr[\s\S]*?<\/tr>/g) || []).map(tr => (tr.match(/<t[dh][\s\S]*?<\/t[dh]>/g) || []).map(c => c.replace(/<[^>]+>/g, " ").normalize("NFKC").replace(/\s+/g, " ").trim()));
    const hi = rows.findIndex(r => r.includes("打数"));
    if (hi < 0 || !rows[hi + 1]) continue;
    const h = rows[hi], r = rows[hi + 1], off = r.length - h.length, at = k => { const i = h.indexOf(k); return i < 0 ? null : r[i + off]; };
    vs = { avg: at("打率"), ab: at("打数"), h: at("安打"), hr: at("本塁打"), rbi: at("打点"), so: at("三振"), bb: at("四死球") || at("四球") };
    break;
  }
  if (!vs) {
    const j = lines.findIndex(l => /対戦成績|対戦打率/.test(l));
    if (j >= 0) {
      const t = lines.slice(j, j + 14).join(" ").normalize("NFKC");
      const m = t.match(/(\d+)\s*打数\s*(\d+)\s*安打/);
      if (m) vs = { ab: m[1], h: m[2], avg: (t.match(/打率\s*([.\d-]+)/) || [])[1] || null, hr: (t.match(/(\d+)\s*本塁打/) || [])[1] || null,
        rbi: (t.match(/(\d+)\s*打点/) || [])[1] || null, so: (t.match(/(\d+)\s*三振/) || [])[1] || null, bb: (t.match(/(\d+)\s*四死球/) || [])[1] || null };
    }
  }
  // 塁の埋まり方：見出しの「ランナー1,2塁」「ランナーなし」「満塁」（いちばん新しい状態）
  const top15 = lines.slice(i0, i0 + 25).join(" ").normalize("NFKC");
  let occ = null;
  if (/満塁/.test(top15)) occ = ["1", "2", "3"];
  else if (/ランナーなし/.test(top15)) occ = [];
  else { const om = top15.match(/ランナー\s*([1-3](?:\s*[,、・]\s*[1-3])*)\s*塁/); if (om) occ = om[1].split(/[,、・]/).map(x => x.trim()).sort(); }
  // ダイヤモンドに出ている走者（「24 大城」のように背番号と名前）：見出しのあと、投手の欄の前
  const rnames = [];
  const iP = lines.findIndex((l, k) => k > i0 && /^投手/.test(l));
  for (const l of lines.slice(i0 + 1, iP > 0 ? iP : i0 + 20)) { const rm = l.match(/^(\d{1,3})\s+([^\s\d][^\s]*(?:\s[^\s\d][^\s]*)?)$/); if (rm && !/回|打|投|攻撃/.test(rm[2])) rnames.push(rm[2]); }
  // リクエスト（リプレー検証）：見出しに出ていれば、その中身（判定変更・判定どおりなど）
  const rl = lines.slice(i0, i0 + 25).find(l => /リクエスト/.test(l));
  const req = rl ? rl.normalize("NFKC").replace(/\s+/g, " ").replace(/\s*ランナー.*$/, "").trim().slice(0, 40) : null;
  // ページに出ている選手のID（名前→ID）：選手を押したときに、その選手の対チーム・対左右の成績を取るため
  const ids = {};
  for (const m of html.matchAll(/\/npb\/player\/(\d+)\/top"[^>]*>([^<]{1,30})<\/a>/g)) { const n = clean(m[2]); if (n && !/^\s*$/.test(n)) ids[n] = m[1]; }
  return {
    half, attack: atk ? atk[1] : null, runners, bases, vs, ids, occ, rnames, req,
    b: bso ? bso[1].length : null, s: bso ? bso[2].length : null, o: bso ? bso[3].length : null,
    batter: bat ? { name: bat[1], no: bat[2], hand: bat[3], avg: bat[4] } : null,
    pitcher: pit ? { name: pit[1], no: pit[2], hand: pit[3], np: +pit[4], bf: +pit[5], era: pit[6] } : null,
    next: nxt ? nxt[1] : null, pitches,
  };
}

// 併殺打：テキスト速報でダブルプレーになった打席が、出場成績では「三ゴロ」のままのことがある → 「三併打」にそろえる（すでに「併」があれば何もしない）
function fixDoublePlays(box, dps, nm) {
  for (const dp of dps || []) {
    const lu = ((box && box.lineups) || [])[dp.half.includes("表") ? 0 : 1] || [], idx = parseInt(dp.half, 10) - 1;
    const row = lu.find(r => nm(r.name) === nm(dp.name));
    if (!row || !row.inn) continue;
    const parts = String(row.inn[idx] || "").split(/[ 、]+/).filter(Boolean), p = parts[dp.k];
    const m = p && !/併/.test(p) && p.match(/^(投|捕|一|二|三|遊|左|中|右)ゴロ$/);
    if (!m) continue;
    parts[dp.k] = m[1] + "併打";
    row.inn[idx] = parts.join(" ");
    row.results = row.inn.filter(Boolean).flatMap(x => String(x).split(/[ 、]+/)).filter(Boolean);
  }
}

function parseBox(html) {
  const pit = {}, bat = {}, lineups = [], pitchers = [], batS = [{}, {}], pitS = [{}, {}];   // batS・pitS：チームごと（0＝ビジター、1＝ホーム）。同じ名前の選手が両チームにいても取り違えない
  const nm = x => x.normalize("NFKC").replace(/\s+/g, "").replace(/髙/g, "高").replace(/﨑/g, "崎");
  for (const tb of html.match(/<table[\s\S]*?<\/table>/g) || []) {
    const raws = (tb.match(/<tr[\s\S]*?<\/tr>/g) || []).map(tr => tr.match(/<t[dh][\s\S]*?<\/t[dh]>/g) || []);
    const rows = raws.map(cs => cs.map(c => c.replace(/<br\s*\/?>/g, " ").replace(/<[^>]+>/g, " ").normalize("NFKC").replace(/\s+/g, " ").trim()));
    if (!rows.length) continue;
    const h = rows[0];
    if (h.includes("投球回") && h.includes("奪三振")) {
      // 見出しの列の数と行の列の数がずれることがある（勝敗が付くと左端に「勝・敗・S・H」の列が増える）ので、右端からそろえて読む
      const list = [];
      for (const r of rows.slice(1)) {
        const off = r.length - h.length;
        const at = k => { const i = h.indexOf(k); return i < 0 ? "" : r[i + off]; };
        let ni = h.indexOf("選手名"); ni = ni < 0 ? 0 : ni + off;
        let name = r[ni] || "", dec = "";
        if (/^(勝|敗|S|H|Ｓ|Ｈ)$/.test(name) && r[ni + 1]) { dec = name; name = r[ni + 1]; }
        else if (ni > 0 && /^(勝|敗|S|H|Ｓ|Ｈ)$/.test(r[ni - 1] || "")) dec = r[ni - 1];
        const m = name.match(/^(.*?)\s*\((勝|敗|S|Ｓ|H|Ｈ)\)\s*$/); if (m) { name = m[1]; dec = dec || m[2]; }
        if (!name || /^(勝|敗|S|H)$/.test(name)) continue;
        dec = dec.normalize("NFKC");
        const row = { ip: at("投球回"), np: at("投球数"), bf: at("打者"), h: at("被安打"), hr: at("被本塁打"), so: at("奪三振"), bb: at("与四球"), hbp: at("与死球"), r: at("失点"), er: at("自責点") };
        pit[nm(name)] = row;
        if (pitS[pitchers.length]) pitS[pitchers.length][nm(name)] = row;
        list.push({ name, dec, era: at("防御率"), ...row });
      }
      if (list.length) pitchers.push(list);
    } else if (h.includes("打数") && h.includes("1回")) {
      const ix = k => h.indexOf(k), i1 = h.indexOf("1回");
      const lu = [];
      let order = 0;
      for (const [ri, r] of rows.slice(1).entries()) {
        if (!r[1] || r[0] === "合計") continue;
        const results = r.slice(i1).filter(Boolean).flatMap(x => x.split(/[ 、]+/)).filter(Boolean);
        // 打点の付いた打席は太字（<b>・<strong>・bold/rbi のclass）。1打席ずつ太字かどうかを調べる
        const bold = [];
        for (const cell of raws[ri + 1].slice(i1)) {
          const parts = cell.replace(/^<t[dh][^>]*>|<\/t[dh]>$/g, "").split(/<br\s*\/?>|、/);
          for (const part of parts) {
            const t = part.replace(/<[^>]+>/g, " ").normalize("NFKC").trim();
            if (!t) continue;
            for (const w of t.split(/\s+/).filter(Boolean)) bold.push(/<(b|strong)[\s>]|class="[^"]*(bold|rbi|strong)[^"]*"/i.test(part));
          }
        }
        const total = parseInt(r[h.indexOf("打点")], 10) || 0;
        const rbis = results.map(() => 0);
        if (total > 0) {
          let idx = results.map((x, i) => (bold[i] ? i : -1)).filter(i => i >= 0);
          if (!idx.length) idx = results.map((x, i) => (/本/.test(x) ? i : -1)).filter(i => i >= 0);
          if (idx.length) {
            idx.forEach(i => (rbis[i] = 1));
            let rest = total - idx.length;
            const hr = idx.filter(i => /本/.test(results[i]));
            const tgt = hr.length ? hr : idx;
            for (let k = 0; rest > 0; k++, rest--) rbis[tgt[k % tgt.length]]++;
          }
        }
        const row = { ab: r[ix("打数")], hit: r[ix("安打")], rbi: r[ix("打点")], hr: r[ix("本塁打")], bb: r[ix("四球")], so: r[ix("三振")], sb: r[ix("盗塁")], results, rbis, inn: r.slice(i1) };
        bat[nm(r[1])] = row;
        if (batS[lineups.length]) batS[lineups.length][nm(r[1])] = row;
        const starter = /^\(.+\)$/.test(r[0]);
        if (starter || !order) order++;
        lu.push({ order, pos: r[0].replace(/[()]/g, ""), starter, name: r[1], avg: r[ix("打率")], ...row });
      }
      if (lu.length) lineups.push(lu);
    }
  }
  return { pit, bat, lineups, pitchers, batS, pitS };
}

async function pitchDetail(key, cache, cors) {
  try {
    const d = new Date(Date.now() + 9 * 3600e3).toISOString().slice(0, 10);
    const ids = await cachedFetch(cache, "ids2-" + d, 300, todayIds);
    const id = ids[key];
    if (!id) return new Response(JSON.stringify({ error: "not_found", key }), { headers: cors });
    const [data, box] = await Promise.all([
      cachedFetch(cache, "pitch3-" + id, 3, async () =>
        parsePitch(await (await fetch(`https://baseball.yahoo.co.jp/npb/game/${id}/score`, UA)).text()) || {}),
      cachedFetch(cache, "box6-" + id, 15, async () =>
        parseBox(await (await fetch(`https://baseball.yahoo.co.jp/npb/game/${id}/stats`, UA)).text())).catch(() => ({ pit: {}, bat: {} })),
    ]);
    const nm = x => (x || "").normalize("NFKC").replace(/\s+/g, "").replace(/髙/g, "高").replace(/﨑/g, "崎");
    // 今日の成績：攻撃中のチームの打者・守っているチームの投手から探す（両チームに同じ名前の選手がいても取り違えない）
    const atkC = ({ ...TEAM_NAMES, ...TEAM_NAMES_P })[String(data.attack || "").normalize("NFKC")] || null, homeC = String(key).split("-")[0];
    const bs = atkC ? (atkC === homeC ? 1 : 0) : null;
    const pick = (side, all, n) => side != null && side[n] !== undefined ? side[n] : side != null ? null : all[n];
    if (data.pitcher) data.pitcher.game = pick(bs == null ? null : (box.pitS || [])[1 - bs] || null, box.pit || {}, nm(data.pitcher.name)) || null;
    if (data.batter) data.batter.game = pick(bs == null ? null : (box.batS || [])[bs] || null, box.bat || {}, nm(data.batter.name)) || null;
    return new Response(JSON.stringify({ id, url: `https://baseball.yahoo.co.jp/npb/game/${id}/score`, ...data }), { headers: cors });
  } catch (e) {
    return new Response(JSON.stringify({ error: String(e) }), { headers: cors });
  }
}


// ================= 成績（チーム成績・個人ランキング） =================
const RANK_ABBR = { "神": "T", "巨": "G", "デ": "DB", "中": "D", "広": "C", "ヤ": "S", "ソ": "H", "日": "F", "オ": "B", "楽": "E", "西": "L", "ロ": "M" };
const TEAM_NAMES = { "阪神": "T", "巨人": "G", "DeNA": "DB", "中日": "D", "広島": "C", "ヤクルト": "S" };
const TEAM_NAMES_P = { "ソフトバンク": "H", "日本ハム": "F", "オリックス": "B", "楽天": "E", "西武": "L", "ロッテ": "M" };
const BAT_LABEL = { avg: "打率", g: "試合", pa: "打席", ab: "打数", h: "安打", h2b: "二塁打", h3b: "三塁打", hr: "本塁打", tb: "塁打", rbi: "打点", r: "得点", so: "三振",
  bb: "四球", hbp: "死球", sh: "犠打", sf: "犠飛", sb: "盗塁", cs: "盗塁死", gidp: "併殺打", obp: "出塁率", slg: "長打率", ops: "OPS", risp: "得点圏", e: "失策" };
const PIT_LABEL = { era: "防御率", g: "登板", gs: "先発", cg: "完投", sho: "完封", qs: "QS", w: "勝利", l: "敗戦", hld: "ホールド", hldp: "HP", sv: "セーブ", wpct: "勝率",
  ip: "投球回", h: "被安打", hr: "被本塁打", so: "奪三振", k9: "奪三振率", bb: "与四球", hbp: "与死球", wp: "暴投", bk: "ボーク", r: "失点", er: "自責点", avg: "被打率",
  kbb: "K/BB", qs_pct: "QS率", whip: "WHIP" };
const clean = x => x.replace(/<br\s*\/?>/g, " ").replace(/<[^>]+>/g, " ").normalize("NFKC").replace(/\s+/g, " ").trim();
const cleanH = x => clean(x).replace(/\s+/g, "");
// 更新時刻：「〇〇/〇/〇 〇:〇〇 更新」のように「更新」と書いてある時刻だけ。なければ取りに行った時刻（ページの関係ない日付を拾って時刻が止まって見えないように）
function stampOf(html) {
  const t = clean(html);
  const m = t.match(/(\d{4})\/(\d{1,2})\/(\d{1,2})\s+(\d{1,2}):(\d{2})\s*(?:現在|更新)/) || t.match(/(?:更新|現在)\s*[:：]?\s*(\d{4})\/(\d{1,2})\/(\d{1,2})\s+(\d{1,2}):(\d{2})/);
  if (m) return `${+m[2]}/${+m[3]} ${+m[4]}:${m[5]}`;
  const n = new Date(Date.now() + 9 * 3600e3);
  return `${n.getUTCMonth() + 1}/${n.getUTCDate()} ${n.getUTCHours()}:${String(n.getUTCMinutes()).padStart(2, "0")}`;
}
function tablesOf(html) {
  return (html.match(/<table[\s\S]*?<\/table>/g) || []).map(tb => (tb.match(/<tr[\s\S]*?<\/tr>/g) || []).map(tr => tr.match(/<t[dh][\s\S]*?<\/t[dh]>/g) || []));
}
async function statsTeam(cache, cors, lg = "C") {
  try {
    const NAMES = lg === "P" ? TEAM_NAMES_P : TEAM_NAMES;
    const data = await cachedFetch(cache, "st-team" + (lg === "P" ? "-p" : ""), 120, async () => {
      const html = await (await fetch(`https://baseball.yahoo.co.jp/npb/standings/detail/${lg === "P" ? 2 : 1}`, UA)).text();
      const KEYS = ["打率", "本塁打", "得点", "盗塁", "防御率", "失点", "失策"];
      for (const rows of tablesOf(html)) {
        if (!rows.length) continue;
        const head = rows[0].map(cleanH);
        if (!head.includes("チーム名") || !head.includes("防御率")) continue;
        const ti = head.indexOf("チーム名"), out = {};
        for (const r of rows.slice(1)) {
          const c = r.map(clean);
          if (c.length !== head.length) continue;
          const nm = Object.keys(NAMES).find(k => c[ti].includes(k));
          if (!nm) continue;
          const row = {};
          for (const k of KEYS) { const i = head.indexOf(k); if (i >= 0 && /\d/.test(c[i])) row[k] = c[i]; }
          out[NAMES[nm]] = row;
        }
        if (Object.keys(out).length === 6) return { team: out, asof: stampOf(html) };
      }
      return {};
    });
    return new Response(JSON.stringify(data), { headers: cors });
  } catch (e) { return new Response(JSON.stringify({ error: String(e) }), { headers: cors }); }
}
async function statsRank(key, cache, cors, lg = "C") {
  try {
    const m = String(key).match(/^(b|p)_([a-z0-9_]+)$/);
    const kind = m && (m[1] === "b" ? "batter" : "pitcher"), type = m && m[2];
    const label = m && (m[1] === "b" ? BAT_LABEL : PIT_LABEL)[type];
    if (!label) return new Response(JSON.stringify({ error: "unknown" }), { headers: cors });
    const data = await cachedFetch(cache, "st-" + key + (lg === "P" ? "-p" : ""), 120, async () => {
      const html = await (await fetch(`https://baseball.yahoo.co.jp/npb/stats/${kind}?gameKindId=${lg === "P" ? 2 : 1}&type=${type}`, UA)).text();
      for (const rows of tablesOf(html)) {
        if (!rows.length) continue;
        const head = rows[0].map(cleanH);
        if (!head.includes("選手名") || !head.includes(label)) continue;
        const ni = head.indexOf("選手名"), vi = head.indexOf(label), res = [];
        for (const r of rows.slice(1)) {
          const c = r.map(clean);
          if (c.length !== head.length || !/^\d+$/.test(c[0])) continue;
          const mm = c[ni].match(/^(.+?)\s*\(\s*(.)\s*\)$/);
          if (!mm || !RANK_ABBR[mm[2]]) continue;
          const rk = +c[0];
          if (rk > 10) break;
          res.push({ r: rk, n: mm[1].trim(), t: RANK_ABBR[mm[2]], v: c[vi] });
        }
        if (res.length) return { rows: res, asof: stampOf(html) };
      }
      return {};
    });
    return new Response(JSON.stringify(data), { headers: cors });
  } catch (e) { return new Response(JSON.stringify({ error: String(e) }), { headers: cors }); }
}

// ================= 選手の今季成績（NPB公式の球団別 個人成績） =================
const NPB_CODE = { T: "t", G: "g", DB: "db", D: "d", C: "c", S: "s", H: "h", F: "f", B: "b", E: "e", L: "l", M: "m" };
function npbTable(html, must) {
  for (const rows of tablesOf(html)) {
    if (rows.length < 2) continue;
    const head = rows[0].map(cleanH);
    if (!must.every(k => head.includes(k))) continue;
    const out = {};
    for (const r of rows.slice(1)) {
      const c = r.map(clean);
      if (c.length !== head.length) continue;
      const raw = c[0], name = raw.replace(/[\s*＊+＋]/g, "");
      if (!name || name === "選手") continue;
      const row = { _l: /[*＊]/.test(raw), _s: /[+＋]/.test(raw) };
      head.forEach((h, i) => { if (i > 0) row[h] = c[i]; });
      out[name] = row;
    }
    return out;
  }
  return {};
}
// 選手の成績：まずスポナビの球団別の個人成績（試合が終わると早めに更新される）。読めなければNPB公式（翌日の更新）
const YAHOO_TID = { G: 1, S: 2, DB: 3, D: 4, T: 5, C: 6, L: 7, F: 8, M: 9, B: 11, H: 12, E: 376 };
// スポナビの見出し → NPB公式と同じ名前（画面はNPBの名前で読む）
const Y2N_PIT = { "敗戦": "敗北", "奪三振": "三振", "被安打": "安打", "被本塁打": "本塁打", "与四球": "四球", "与死球": "死球", "完封": "完封勝" };
function yahooTable(html, must, map) {
  for (const rows of tablesOf(html)) {
    if (rows.length < 2) continue;
    const head = rows[0].map(cleanH);
    if (!head.includes("選手名") || !must.every(k => head.includes(k))) continue;
    const iN = head.indexOf("選手名"), out = {};
    for (const r of rows.slice(1)) {
      const c = r.map(clean);
      if (c.length !== head.length || c[iN] === "選手名") continue;
      const name = c[iN].replace(/[\s*＊+＋]/g, "");
      if (!name) continue;
      const row = {};
      head.forEach((h, i) => { if (i !== iN) row[(map && map[h]) || h] = c[i]; });
      if (Object.entries(row).every(([k, v]) => k === "背番号" || k === "位置" || v === "-" || v === "")) continue;   // 出場なし
      out[name] = row;
    }
    return out;
  }
  return {};
}
async function playerStats(t, cache, cors) {
  try {
    const code = NPB_CODE[t];
    if (!code) return new Response(JSON.stringify({ error: "unknown" }), { headers: cors });
    const y = new Date(Date.now() + 9 * 3600e3).getUTCFullYear();
    const yid = YAHOO_TID[t];
    const yd = yid ? await cachedFetch(cache, `yst1-${t}-${y}`, 120, async () => {
      const [bh, ph] = await Promise.all([
        fetch(`https://baseball.yahoo.co.jp/npb/teams/${yid}/battingstats`, UA).then(r => (r.ok ? r.text() : "")),
        fetch(`https://baseball.yahoo.co.jp/npb/teams/${yid}/pitchingstats`, UA).then(r => (r.ok ? r.text() : "")),
      ]);
      const bat = yahooTable(bh, ["打率", "打席"]), pit = yahooTable(ph, ["防御率", "投球回"], Y2N_PIT);
      if (Object.keys(bat).length < 5 && Object.keys(pit).length < 5) return {};
      const now = new Date(Date.now() + 9 * 3600e3);
      return { bat, pit, src: "sponavi", at: `${now.getUTCMonth() + 1}/${now.getUTCDate()} ${String(now.getUTCHours()).padStart(2, "0")}:${String(now.getUTCMinutes()).padStart(2, "0")}` };
    }) : {};
    if (yd && (yd.bat || yd.pit)) return new Response(JSON.stringify(yd), { headers: cors });
    const data = await cachedFetch(cache, `pst2-${t}-${y}`, 1800, async () => {
      const [bh, ph] = await Promise.all([
        fetch(`https://npb.jp/bis/${y}/stats/idb1_${code}.html`, UA).then(r => (r.ok ? r.text() : "")),
        fetch(`https://npb.jp/bis/${y}/stats/idp1_${code}.html`, UA).then(r => (r.ok ? r.text() : "")),
      ]);
      const bat = npbTable(bh, ["打率", "打席"]), pit = npbTable(ph, ["防御率", "投球回"]);
      const m = clean(bh || ph).match(/(\d{4})年(\d{1,2})月(\d{1,2})日\s*現在/);
      if (!Object.keys(bat).length && !Object.keys(pit).length) return {};
      return { bat, pit, asof: m ? `${+m[2]}/${+m[3]}` : null };
    });
    return new Response(JSON.stringify(data), { headers: cors });
  } catch (e) { return new Response(JSON.stringify({ error: String(e) }), { headers: cors }); }
}

// ================= 予告先発 =================
const YTEAM = { "5": "T", "1": "G", "3": "DB", "4": "D", "6": "C", "2": "S", "12": "H", "8": "F", "11": "B", "376": "E", "7": "L", "9": "M" };
function parseStarters(html, year) {
  const out = {};
  const rows = (html.match(/<tr[\s\S]*?<\/tr>/g) || []);
  let date = null;
  for (const tr of rows) {
    const cells = tr.match(/<t[dh][\s\S]*?<\/t[dh]>/g) || [];
    if (cells.length < 2) continue;
    const dm = clean(cells[0]).match(/(\d{1,2})月(\d{1,2})日/);
    if (dm) date = `${year}-${String(+dm[1]).padStart(2, "0")}-${String(+dm[2]).padStart(2, "0")}`;
    if (!date) continue;
    // 対戦のマス：球団へのリンクの直後にある「(予)名前」がその球団の予告先発
    const cell = cells.find(c => /teams\/\d+\//.test(c) && /予|試合前|見どころ|:/.test(clean(c)));
    if (!cell) continue;
    const links = [...cell.matchAll(/teams\/(\d+)\/[^"']*["'][^>]*>/g)];
    if (links.length < 2) continue;
    const seg = i => cell.slice(links[i].index, i + 1 < links.length ? links[i + 1].index : cell.length);
    const pick = i => { const m = clean(seg(i)).match(/[(（]予[)）]\s*([^\s<>()（）]+)/); return m ? m[1] : null; };
    const h = YTEAM[links[0][1]], a = YTEAM[links[1][1]];
    if (!h || !a) continue;
    const ph = pick(0), pa = pick(1);
    if (ph || pa) out[`${date}|${h}|${a}`] = { h: ph, a: pa };
  }
  return out;
}
async function starters(cache, cors) {
  try {
    const now = new Date(Date.now() + 9 * 3600e3);
    const ds = d => d.toISOString().slice(0, 10);
    const today = ds(now), tomorrow = ds(new Date(now.getTime() + 864e5));
    const data = await cachedFetch(cache, `yk-${today}`, 300, async () => {
      const pages = await Promise.all([today, tomorrow].map(d => fetch(`https://baseball.yahoo.co.jp/npb/schedule/first/all?date=${d}`, UA).then(r => (r.ok ? r.text() : "")).catch(() => "")));
      const out = {};
      for (const html of pages) Object.assign(out, parseStarters(html, now.getUTCFullYear()));
      return Object.keys(out).length ? { starters: out } : {};
    });
    return new Response(JSON.stringify(data), { headers: cors });
  } catch (e) { return new Response(JSON.stringify({ error: String(e) }), { headers: cors }); }
}

// ===== 試合前の情報：放送予定・スタメン（スポナビの試合トップ /npb/game/<ID>/top） =====
const SHORT = { "巨人": "G", "DeNA": "DB", "阪神": "T", "中日": "D", "広島": "C", "ヤクルト": "S", "ソフトバンク": "H", "日本ハム": "F",
  "オリックス": "B", "楽天": "E", "西武": "L", "ロッテ": "M" };
const cells = tr => [...tr.matchAll(/<t[dh][^>]*>([\s\S]*?)<\/t[dh]>/g)].map(m => clean(m[1]));
function parsePre(html) {
  const out = {};
  // 放送予定：テレビ放送・ネット配信・ラジオ放送
  const bi = html.indexOf("放送予定");
  if (bi >= 0) {
    const seg = html.slice(bi, bi + 6000);
    const tbl = (seg.match(/<table[\s\S]*?<\/table>/) || [""])[0];
    for (const tr of tbl.match(/<tr[\s\S]*?<\/tr>/g) || []) {
      const c = cells(tr);
      if (c.length < 2) continue;
      const v = c[1].replace(/[（(]\s*番組表\.?\s*Gガイド\s*[)）]/g, "").replace(/番組表\.?\s*Gガイド/g, "").replace(/\s+/g, " ").trim().replace(/[、,]\s*$/, "");
      if (!v || v === "-") continue;
      if (/テレビ/.test(c[0])) out.tv = v;
      else if (/ネット|配信/.test(c[0])) out.net = v;
      else if (/ラジオ/.test(c[0])) out.radio = v;
    }
  }
  // スターティングメンバー：チームごとに「先発投手の表」と「打順の表」（ホームが先）
  const si = html.indexOf("スターティングメンバー");
  if (si >= 0) {
    let end = html.indexOf("ベンチ入り", si); if (end < 0) end = si + 40000;
    const seg = html.slice(si, end);
    const teams = [];
    // チーム名の見出し → その後ろの表
    const parts = seg.split(/<h[1-4][^>]*>/).slice(1);
    for (const part of parts) {
      const head = clean(part.split(/<\/h[1-4]>/)[0]);
      const code = SHORT[head];
      const tables = part.match(/<table[\s\S]*?<\/table>/g) || [];
      if (!code && !tables.length) continue;
      const team = { t: code || null, p: null, bat: [] };
      for (const tb of tables) {
        const rows = (tb.match(/<tr[\s\S]*?<\/tr>/g) || []).map(cells);
        const h = rows[0] || [];
        if (h.includes("打順")) {
          for (const r of rows.slice(1)) if (/^[1-9]$/.test(r[0]) && r[2]) team.bat.push({ o: +r[0], pos: r[1], n: r[2], ba: r[3] || "", avg: r[4] || "" });
        } else if (h.includes("投手")) {
          const r = rows.find(x => x[0] === "先発");
          if (r && r[2]) team.p = { n: r[2], th: r[3] || "", era: r[4] || "" };
        }
      }
      if (team.p || team.bat.length) teams.push(team);
    }
    if (teams.length >= 1) out.lu = teams.slice(0, 2);
  }
  // ベンチ入り選手：守備位置の見出し（投手・捕手・内野手・外野手）ごとに、ホーム・ビジターの順で表が2つ
  const bi2 = html.indexOf("ベンチ入り選手");
  if (bi2 >= 0) {
    let end = html.indexOf("調子の詳細", bi2); if (end < 0) end = html.indexOf("審判", bi2); if (end < 0) end = bi2 + 40000;
    const seg = html.slice(bi2, end);
    const bench = [{}, {}];
    for (const part of seg.split(/<h[1-4][^>]*>/).slice(1)) {
      const head = clean(part.split(/<\/h[1-4]>/)[0]);
      if (!/^(投手|捕手|内野手|外野手)$/.test(head)) continue;
      (part.match(/<table[\s\S]*?<\/table>/g) || []).slice(0, 2).forEach((tb, i) => {
        const rows = (tb.match(/<tr[\s\S]*?<\/tr>/g) || []).map(cells).filter(r => r[0] && r[0] !== "選手名");
        bench[i][head] = rows.map(r => ({ n: r[0], bt: r[1] || "", st: r[2] || "" }));
      });
    }
    if (Object.keys(bench[0]).length || Object.keys(bench[1]).length) out.bench = bench;
  }
  return out;
}
// その日の試合（スポナビの日程ページ https://baseball.yahoo.co.jp/npb/schedule/first/all?date=YYYY-MM-DD にある試合ページ）の放送予定・スタメン。
// CS・日本シリーズも同じページに出る。試合ページ（/top）のタイトルの日付がその日のものだけ使う
async function preOn(date) {
  const html = await fetch(`https://baseball.yahoo.co.jp/npb/schedule/first/all?date=${date}`, UA).then(r => (r.ok ? r.text() : "")).catch(() => "");
  const ids = [...new Set([...html.matchAll(/\/npb\/game\/(\d{8,12})\//g)].map(m => m[1]))].slice(0, 14);
  const [y, m, d] = date.split("-").map(Number), want = `${y}年${m}月${d}日`;
  const games = {};
  await Promise.all(ids.map(async id => {
    try {
      const page = await (await fetch(`https://baseball.yahoo.co.jp/npb/game/${id}/top`, UA)).text();
      const t = ((page.match(/<title>([^<]*)<\/title>/) || [])[1] || "").normalize("NFKC").match(/(\d{4}年\d{1,2}月\d{1,2}日)\s*(.+?)vs\.(.+?)\s/);
      if (!t || t[1] !== want || !FULL[t[2]] || !FULL[t[3]]) return;
      const k = `${FULL[t[2]]}-${FULL[t[3]]}`, dd = parsePre(page);
      if (dd.lu) {   // どちらがホームか：見出しの球団名で。なければ並び順（ホームが先）
        const [h, a] = k.split("-");
        const byT = {}; dd.lu.forEach((x, i) => (byT[x.t || (i === 0 ? h : a)] = x));
        dd.lu = { h: byT[h] || null, a: byT[a] || null };
      }
      if (dd.bench) { const [b0, b1] = dd.bench; dd.bench = { h: b0, a: b1 }; }   // ホームが先
      if (dd.tv || dd.net || dd.radio || dd.lu || dd.bench) games[k] = { id, ...dd };
    } catch {}
  }));
  return games;
}
// 今日（スタメンが出るので1分ごと）と、あした（放送予定。30分ごと）
async function preGame(cache, cors) {
  try {
    const now = new Date(Date.now() + 9 * 3600e3);
    const today = now.toISOString().slice(0, 10), tomorrow = new Date(now.getTime() + 864e5).toISOString().slice(0, 10);
    const [g0, g1] = await Promise.all([
      cachedFetch(cache, `pre2-${today}`, 60, async () => { const g = await preOn(today); return Object.keys(g).length ? { games: g } : {}; }),
      cachedFetch(cache, `pre2-${tomorrow}`, 1800, async () => { const g = await preOn(tomorrow); return Object.keys(g).length ? { games: g } : {}; }),
    ]);
    const data = {};
    if (g0 && g0.games) Object.assign(data, { date: today, games: g0.games });
    if (g1 && g1.games) data.next = { date: tomorrow, games: g1.games };
    return new Response(JSON.stringify(data), { headers: cors });
  } catch (e) { return new Response(JSON.stringify({ error: String(e) }), { headers: cors }); }
}

// ===== 今オフの退団（スポナビ「入退団情報」 https://baseball.yahoo.co.jp/npb/transfer） =====
// 表：更新日｜状況（退団・入団）｜選手名（育成は「※」）｜守備｜備考。退団のうち 自由契約→戦力外、引退→引退、育成再契約（の打診）→打診
function parseTransfer(html, year) {
  const out = [];
  for (const part of html.split(/<h[2-4][^>]*>/).slice(1)) {
    const head = clean(part.split(/<\/h[2-4]>/)[0]);
    const t = SHORT[head];
    if (!t) continue;
    for (const tr of part.match(/<tr[\s\S]*?<\/tr>/g) || []) {
      const c = cells(tr);
      // 入団のうち「育成から支配下」は支配下登録（今季の1月から）
      if (c.length >= 5 && c[1] === "入団" && /育成.{0,8}支配下|支配下.{0,8}育成/.test(c[4])) {
        const m0 = c[0].match(/(\d{4})\/(\d{1,2})\/(\d{1,2})/), n0 = c[2].replace(/※/g, "").trim();
        if (m0 && n0) { const d0 = `${m0[1]}-${m0[2].padStart(2, "0")}-${m0[3].padStart(2, "0")}`; if (d0 >= `${year}-01-01`) out.push({ t, n: n0, dev: false, kind: "promote", date: d0, note: c[4] }); }
        continue;
      }
      if (c.length < 5 || c[1] !== "退団") continue;
      const m = c[0].match(/(\d{4})\/(\d{1,2})\/(\d{1,2})/);
      if (!m) continue;
      const date = `${m[1]}-${m[2].padStart(2, "0")}-${m[3].padStart(2, "0")}`;
      if (date < `${year}-09-01`) continue;
      const note = c[4], name = c[2].replace(/※/g, "").trim();
      if (!name || /トレード|現役ドラフト|FA|ポスティング|人的補償/.test(note)) continue;
      const kind = /引退/.test(note) ? "retire" : /育成.{0,3}再契約|育成.{0,4}打診/.test(note) ? "offer" : /自由契約|戦力外/.test(note) ? "cut" : /退団/.test(note) ? "leave" : null;
      if (kind) out.push({ t, n: name, dev: /※/.test(c[2]), kind, date, note });
    }
  }
  return out;
}
// ベースボールチャンネルの「今季の戦力外通告・現役引退・自由契約・退団選手一覧」（発表の当日に更新される）。記事の場所はタグの一覧から探す
// 自由契約は戦力外とは別の種類（free）。シーズン途中の自由契約も、その年の分はすべて
const BBC_KIND = [[/引退/, "retire"], [/戦力外/, "cut"], [/自由契約/, "free"], [/退団/, "leave"]];
function parseBbc(html, year) {
  const out = [];
  for (const part of html.split(/<h[2-4][^>]*>/).slice(1)) {
    const head = clean(part.split(/<\/h[2-4]>/)[0]);
    const k = BBC_KIND.find(([rx]) => rx.test(head));
    if (!k || /移籍|トレード|FA|入団|加入/.test(head)) continue;
    for (const tr of part.match(/<tr[\s\S]*?<\/tr>/g) || []) {
      const c = cells(tr);
      if (c.length < 3) continue;
      const m = c[0].match(/(\d{1,2})月(\d{1,2})日/), t = SHORT[c[1]];
      if (!m || !t) continue;
      const mo = +m[1], d = +m[2]; if (mo < 1 || mo > 12 || d < 1 || d > 31) continue;
      // 年がない日付：1・2月は翌年とみなす。ただし今日より後になるなら今年（2月の自由契約が「来年」になっていた）
      const mmdd = `${String(mo).padStart(2, "0")}-${String(d).padStart(2, "0")}`, today = new Date(Date.now() + 9 * 3600e3).toISOString().slice(0, 10);
      let date = `${mo >= 3 ? year : year + 1}-${mmdd}`;
      if (date > today) date = `${year}-${mmdd}`;
      if (date < (k[1] === "free" ? `${year}-01-01` : `${year}-09-01`)) continue;
      const name = c[2].replace(/※/g, "").trim();
      const pos = /^(投手|捕手|内野手|外野手)$/.test(c[3] || "") ? c[3] : "";
      if (name) out.push({ t, n: name, dev: /※/.test(c[2]), kind: k[1], date, src: "bbc", pos });
    }
  }
  return out;
}
async function bbcList(year) {
  let url = null;
  for (const tag of ["https://www.baseballchannel.jp/tag/%E8%87%AA%E7%94%B1%E5%A5%91%E7%B4%84/", "https://www.baseballchannel.jp/tag/%E6%88%A6%E5%8A%9B%E5%A4%96%E9%80%9A%E5%91%8A/"]) {
    const r = await fetch(tag, UA).catch(() => null); if (!r || !r.ok) continue;
    const html = await r.text();
    for (const m of html.matchAll(/<a[^>]+href="(https:\/\/www\.baseballchannel\.jp\/[^"]+)"[^>]*>([\s\S]*?)<\/a>/g)) {
      const tx = clean(m[2]);
      if (tx.includes(`${year}年`) && tx.includes("戦力外") && tx.includes("一覧") && !/今日の|月\d+日発表/.test(tx)) { url = m[1].split("?")[0]; if (tx.includes("プロ野球")) break; }
    }
    if (url) break;
  }
  if (!url) return [];
  const base = url.replace(/\/$/, ""), out = [], seen = new Set();
  let prev = "";
  for (let p = 1; p <= 6; p++) {
    const r = await fetch(p === 1 ? base + "/" : `${base}/${p}/`, UA).catch(() => null); if (!r || !r.ok) break;
    const rows = parseBbc(await r.text(), year);
    const sig = rows.map(x => x.t + x.n).join(",");
    if (p > 1 && (!rows.length || sig === prev)) break;   // 次のページがない（同じページが返ってくる）ときはそこまで
    prev = sig;
    for (const x of rows) {   // 同じ選手は1回だけ
      const k = x.t + "|" + x.n.replace(/\s+/g, "");
      if (seen.has(k)) continue;
      seen.add(k); x.url = base + "/"; out.push(x);
    }
  }
  return out;
}
async function transferList(cache, cors) {
  try {
    const now = new Date(Date.now() + 9 * 3600e3), year = now.getUTCFullYear();
    const data = await cachedFetch(cache, "transfer2", 120, async () => {
      const [ya, bb] = await Promise.all([
        fetch("https://baseball.yahoo.co.jp/npb/transfer", UA).then(r => (r.ok ? r.text() : "")).then(h => (h ? parseTransfer(h, year) : [])).catch(() => []),
        bbcList(year).catch(() => []),
      ]);
      const key = x => x.t + "|" + x.n.normalize("NFKC").replace(/^\s*[A-Za-z]{1,2}\s*[・.．]\s*/, "").replace(/\s+/g, "");   // 「F・グズマン」と「グズマン」は同じ人
      const seen = new Set(), items = [];
      for (const x of ya.concat(bb)) { if (seen.has(key(x))) continue; seen.add(key(x)); items.push(x); }   // 同じ選手は1回だけ
      return items.length ? { at: new Date().toISOString(), items } : {};
    });
    return new Response(JSON.stringify(data), { headers: cors });
  } catch (e) { return new Response(JSON.stringify({ error: String(e) }), { headers: cors }); }
}

// ===== GitHub のデータの自動更新（update-data）を動かす =====
// Cloudflare のワーカーの「設定 → 変数とシークレット」に GH_TOKEN（GitHub のトークン：このリポジトリの Actions を動かせるもの）を入れておく
async function kickUpdate(env) {
  if (!env || !env.GH_TOKEN) return;
  const repo = env.GH_REPO || "negohub/hobby-baseball";
  try {
    const r = await fetch(`https://api.github.com/repos/${repo}/actions/workflows/update.yaml/dispatches`, {
      method: "POST",
      headers: { Authorization: `Bearer ${env.GH_TOKEN}`, Accept: "application/vnd.github+json", "X-GitHub-Api-Version": "2022-11-28", "User-Agent": "hobby-baseball-worker", "Content-Type": "application/json" },
      body: JSON.stringify({ ref: "main" }),
    });
    return r.status === 204;
  } catch { return false; }
}

// ===== 選手の対チーム別・対左右別の成績（https://baseball.yahoo.co.jp/npb/player/<ID>/top） =====
// 打者：「対チーム別成績」（チーム名｜打率｜試合｜打席｜打数｜安打…｜ＯＰＳ…）と「対左右別成績」（投手｜打者｜打率…）
// 投手：同じ見出しの表（防御率・登板など）。見出しの言葉で表を探すので、打者・投手どちらでも読める
function sectionTable(html, title) {
  const i = html.indexOf(title);
  if (i < 0) return null;
  const rest = html.slice(i), j = rest.search(/<h[23][^>]*>/);
  const seg = j > 0 ? rest.slice(0, rest.indexOf("</table>") + 8 || j) : rest;
  return (tablesOf(seg)[0] || null);
}
async function playerSplit(id, cache, cors) {
  try {
    const data = await cachedFetch(cache, "split1-" + id, 1800, async () => {
      const r = await fetch(`https://baseball.yahoo.co.jp/npb/player/${id}/top`, UA);
      if (!r.ok) return {};
      const html = await r.text();
      const out = { id, team: {}, lr: [] };
      const tt = sectionTable(html, "対チーム別成績");
      if (tt && tt.length > 1) {
        const head = tt[0].map(cleanH);
        for (const row of tt.slice(1)) {
          const c = row.map(clean);
          const t = SHORT[c[0]]; if (!t) continue;
          const o = {}; head.forEach((h, k) => { if (k) o[h.replace(/ＯＰＳ/, "OPS")] = c[k]; });
          out.team[t] = o;
        }
      }
      const lr = sectionTable(html, "対左右別成績");
      if (lr && lr.length > 1) {
        const head = lr[0].map(cleanH); let p = "";
        for (const row of lr.slice(1)) {
          const c = row.map(clean); if (c[0]) p = c[0];
          const o = { p, b: c[1] }; head.forEach((h, k) => { if (k > 1) o[h] = c[k]; });
          out.lr.push(o);
        }
      }
      out.kind = /防御率/.test((tt && tt[0] || []).map(cleanH).join(",")) ? "pit" : "bat";
      return Object.keys(out.team).length || out.lr.length ? out : {};
    });
    return new Response(JSON.stringify(data), { headers: cors });
  } catch (e) { return new Response(JSON.stringify({ error: String(e) }), { headers: cors }); }
}
