/* ===== 計算ロジック（画面に依存しない部分） ===== */
// 表示しているリーグの6球団（リーグを切り替えると中身が入れ替わる）
const CL = ["DB", "G", "T", "D", "S", "C"];
const LG_TEAMS = { C: ["DB", "G", "T", "D", "S", "C"], P: ["H", "F", "B", "E", "L", "M"] };
let LEAGUE = "C";
function useLeague(lg) { LEAGUE = lg === "P" ? "P" : "C"; CL.splice(0, CL.length, ...LG_TEAMS[LEAGUE]); }
const isPL = () => LEAGUE === "P";
const TEAM = {
  DB: { s: "De", n: "DeNA", c: "#0A6FE0", i: "#FFF" }, G: { s: "巨", n: "巨人", c: "#F97709", i: "#000" },
  T: { s: "神", n: "阪神", c: "#FFE100", i: "#000" }, D: { s: "中", n: "中日", c: "#1F3FB0", i: "#FFF" },
  S: { s: "ヤ", n: "ヤクルト", c: "#00A660", i: "#FFF" }, C: { s: "広", n: "広島", c: "#E60012", i: "#FFF" },
  
  H: { s: "ソ", n: "ソフトバンク", c: "#F5C400", i: "#000" }, F: { s: "日", n: "日本ハム", c: "#0079C2", i: "#FFF" },
  B: { s: "オ", n: "オリックス", c: "#B89A5E", i: "#000" }, E: { s: "楽", n: "楽天", c: "#9A0F2A", i: "#FFF" },
  L: { s: "西", n: "西武", c: "#153A8A", i: "#FFF" }, M: { s: "ロ", n: "ロッテ", c: "#4A4A4A", i: "#FFF" },
};

function monthOf(d) { return +d.slice(5, 7); }
// 勝率の比較（引き分けは除外。試合0なら.000扱い）
function cmp(w1, l1, w2, l2) { return w1 * ((w2 + l2) || 1) - w2 * ((w1 + l1) || 1); }

// セ・リーグの同勝率時の順位決定（2022年〜）：①勝利数 ②当該球団間の対戦勝率（3球団以上は合算） ③リーグ内対戦（交流戦を除く）の勝率 ④前年度順位
function clSort(rows, games, inScope, prevOrder) {
  const fin = games.filter(g => g.st === "final" && inScope(g));
  const pct = (w, l) => (w + l ? w / (w + l) : 0);
  const rec = (t, filt) => { let w = 0, l = 0; for (const g of fin) { if (!filt(g)) continue; const me = g.h === t ? [g.hs, g.as] : g.a === t ? [g.as, g.hs] : null; if (!me || me[0] === me[1]) continue; me[0] > me[1] ? w++ : l++; } return pct(w, l); };
  const steps = [
    ...(isPL() ? [] : [grp => t => t.w]),
    grp => { const set = grp.map(r => r.t); return r => rec(r.t, g => set.includes(g.h) && set.includes(g.a)); },
    grp => r => rec(r.t, g => CL.includes(g.h) && CL.includes(g.a)),
    grp => r => -((prevOrder || []).indexOf(r.t) + 1 || 99),
  ];
  const rank = (grp, k) => {
    if (grp.length < 2 || k >= steps.length) return grp;
    const key = steps[k](grp), v = new Map(grp.map(r => [r, key(r)]));
    const sorted = grp.slice().sort((a, b) => v.get(b) - v.get(a));
    const out = []; let i = 0;
    while (i < sorted.length) { let j = i; while (j + 1 < sorted.length && v.get(sorted[j + 1]) === v.get(sorted[i])) j++; out.push(...rank(sorted.slice(i, j + 1), k + 1)); i = j + 1; }
    return out;
  };
  rows.sort((a, b) => cmp(b.w, b.l, a.w, a.l));
  const out = []; let i = 0;
  while (i < rows.length) { let j = i; while (j + 1 < rows.length && cmp(rows[j + 1].w, rows[j + 1].l, rows[i].w, rows[i].l) === 0) j++; out.push(...rank(rows.slice(i, j + 1), 0)); i = j + 1; }
  rows.splice(0, rows.length, ...out);
  return rows;
}

// ===== 計算結果の使い回し（同じデータ・同じ月度なら計算し直さない。画面の再描画を軽くしてカクつきを防ぐ） =====
// 鍵：リーグ・月度・設定・その月度の試合の中身。試合が1つでも変われば計算し直す
const __anMemo = new Map();
function analyze(games, period, cfg) {
  let sig = LEAGUE + "|" + CL.join(",") + "|" + period.id + "|" + period.months.join(",") + "|" + (cfg.excluded || []).join(",") + "|" + !!cfg.tieIsSafe + "|" + (cfg.prevOrder || []).join(",")
    + "|" + (cfg.finalPid === period.id ? JSON.stringify(cfg.virtual || []) : "");
  for (const g of games) if (period.months.includes(monthOf(g.d))) sig += "|" + g.d + g.h + g.a + g.st + g.hs + "-" + g.as;
  const hit = __anMemo.get(sig);
  if (hit) return __relink(__clone(hit), games, period);
  const res = analyzeRaw(games, period, cfg);
  if (__anMemo.size > 80) __anMemo.clear();
  __anMemo.set(sig, __clone(res));
  return res;
}
function __clone(o) { return typeof structuredClone === "function" ? structuredClone(o) : JSON.parse(JSON.stringify(o)); }
// 使い回した結果の「残り試合」を、今渡された試合データそのものに付け替える
// （画面では「この試合が勝ったら」を試合データの同一性で探すため。複製のままだと見つからない）
function __relink(res, games, period) {
  const pool = new Map();
  for (const g of games) {
    if (!period.months.includes(monthOf(g.d))) continue;
    const k = g.d + "|" + g.h + "|" + g.a;
    if (!pool.has(k)) pool.set(k, []);
    pool.get(k).push(g);
  }
  const used = new Map();
  const pick = x => {
    const k = x.d + "|" + x.h + "|" + x.a, list = pool.get(k);
    if (!list) return x;
    // 同じ日・同じカードが2試合あるとき（ダブルヘッダー）は、開始時刻・状態が同じものを順に使う
    const u = used.get(k) || new Set();
    const g = list.find(y => !u.has(y) && y.t === x.t && y.st === x.st) || list.find(y => !u.has(y)) || list[0];
    u.add(g); used.set(k, u);
    return g;
  };
  res.remaining = res.remaining.map(pick);
  // 球団ごとの残り試合も、同じ試合データを指すように
  const byKey = new Map();
  res.remaining.forEach(g => { const k = g.d + "|" + g.h + "|" + g.a + "|" + (g.t || ""); if (!byKey.has(k)) byKey.set(k, []); byKey.get(k).push(g); });
  const re = arr => { const cnt = new Map(); return (arr || []).map(x => { const k = x.d + "|" + x.h + "|" + x.a + "|" + (x.t || ""), list = byKey.get(k); if (!list) return x; const i = cnt.get(k) || 0; cnt.set(k, i + 1); return list[Math.min(i, list.length - 1)]; }); };
  for (const r of res.rows) r.left = re(r.left);
  for (const t in res.R) res.R[t].left = re(res.R[t].left);
  return res;
}
function analyzeRaw(games, period, cfg) {
  const targets = CL.filter(t => !cfg.excluded.includes(t));
  const R = {};
  CL.forEach(t => (R[t] = { w: 0, l: 0, d: 0, left: [] }));
  const remaining = [];
  // 最後の月度（シーズンの終わりを含む月度）では、中止になって振替日がまだ決まっていない試合（振替待ち）も
  // 必ずこの月度のうちに行われるので、残り試合に入れる（日付が決まっていない仮の試合：virt）
  const extra = cfg.finalPid === period.id ? (cfg.virtual || []) : [];
  for (const g of extra.length ? games.concat(extra) : games) {
    if (!period.months.includes(monthOf(g.d)) || g.st === "canc") continue;
    const inH = !!R[g.h], inA = !!R[g.a];
    if (g.st === "final") {
      if (g.hs > g.as) { if (inH) R[g.h].w++; if (inA) R[g.a].l++; }
      else if (g.hs < g.as) { if (inH) R[g.h].l++; if (inA) R[g.a].w++; }
      else { if (inH) R[g.h].d++; if (inA) R[g.a].d++; }
    } else {
      if (inH) R[g.h].left.push(g);
      if (inA) R[g.a].left.push(g);
      if (targets.includes(g.h) || targets.includes(g.a)) remaining.push(g);
    }
  }
  CL.forEach(t => (R[t].rem = R[t].left.length));

  // 残り試合の内訳：対象球団どうし(h2h) と それ以外(out)
  const h2h = {}, out = {};
  targets.forEach(t => { h2h[t] = {}; targets.forEach(u => (h2h[t][u] = 0)); out[t] = 0; });
  for (const g of remaining) {
    const a = targets.includes(g.h), b = targets.includes(g.a);
    if (a && b) { h2h[g.h][g.a]++; h2h[g.a][g.h]++; }
    else if (a) out[g.h]++;
    else if (b) out[g.a]++;
  }
  let tieSafe = !!cfg.tieIsSafe;

  // X の勝ち方(winsVs)を固定したとき、残り4球団が全員Xの勝率以上になれるか（=Xを最下位にできるか）
  function advCanMakeLast(X, winsVs) {
    let tot = 0; for (const k in winsVs) tot += winsVs[k];
    const xw = R[X].w + tot, xl = R[X].l + R[X].rem - tot, xd = (xw + xl) || 1;
    const others = targets.filter(t => t !== X);
    const need = {};
    let sum = 0;
    for (const Y of others) {
      const baseW = R[Y].w + (h2h[X][Y] - (winsVs[Y] || 0)) + out[Y];
      const den = (R[Y].w + R[Y].l + R[Y].rem) || 1;
      const minW = tieSafe ? Math.floor((xw * den) / xd) + 1 : Math.ceil((xw * den) / xd);
      need[Y] = Math.max(0, minW - baseW);
      sum += need[Y];
    }
    if (sum === 0) return true;
    // 最大流：対象球団どうしの残り試合の勝ちを、足りない球団に配れるか
    const pairs = [];
    for (let i = 0; i < others.length; i++)
      for (let j = i + 1; j < others.length; j++) {
        const n = h2h[others[i]][others[j]];
        if (n > 0) pairs.push([others[i], others[j], n]);
      }
    const N = 2 + pairs.length + others.length, S = 0, T = N - 1;
    const cap = Array.from({ length: N }, () => new Array(N).fill(0));
    const tIdx = {}; others.forEach((y, i) => (tIdx[y] = 1 + pairs.length + i));
    pairs.forEach(([y, z, n], i) => { cap[S][1 + i] = n; cap[1 + i][tIdx[y]] = n; cap[1 + i][tIdx[z]] = n; });
    others.forEach(y => (cap[tIdx[y]][T] = need[y]));
    let flow = 0;
    while (true) {
      const prev = new Array(N).fill(-1); prev[S] = S;
      const q = [S];
      while (q.length && prev[T] < 0) {
        const u = q.shift();
        for (let v = 0; v < N; v++) if (prev[v] < 0 && cap[u][v] > 0) { prev[v] = u; q.push(v); }
      }
      if (prev[T] < 0) break;
      let f = Infinity;
      for (let v = T; v !== S; v = prev[v]) f = Math.min(f, cap[prev[v]][v]);
      for (let v = T; v !== S; v = prev[v]) { cap[prev[v]][v] -= f; cap[v][prev[v]] += f; }
      flow += f;
    }
    return flow >= sum;
  }

  // 自力マジック：あと何勝すれば（どの試合で勝っても）確定するか
  function selfMagic(X) {
    const rivals = targets.filter(t => t !== X && h2h[X][t] > 0);
    for (let k = 0; k <= R[X].rem; k++) {
      const ow = Math.min(k, out[X]);
      let r = k - ow, bad = false;
      const cur = { OUT: ow };
      (function rec(i, left) {
        if (bad) return;
        if (i === rivals.length) { if (left === 0 && advCanMakeLast(X, cur)) bad = true; return; }
        const c = h2h[X][rivals[i]];
        for (let v = Math.min(c, left); v >= 0; v--) { cur[rivals[i]] = v; rec(i + 1, left - v); if (bad) return; }
        delete cur[rivals[i]];
      })(0, r);
      if (!bad) return k;
    }
    return null;
  }

  // 相手別マジック：自分の勝ち＋相手の負けが合計いくつで、その相手より上が確定するか
  function pairMagic(X, Y) {
    const h = h2h[X][Y], oX = R[X].rem - h, oY = R[Y].rem - h;
    const maxEv = oX + oY + 2 * h;
    let maxBad = -1;
    for (let a = 0; a <= oX; a++)
      for (let c = 0; c <= h; c++)
        for (let b = 0; b <= oY; b++) {
          const s = cmp(R[X].w + a + c, R[X].l + (oX - a) + (h - c), R[Y].w + b + (h - c), R[Y].l + (oY - b) + c);
          if (tieSafe ? s < 0 : s <= 0) maxBad = Math.max(maxBad, a + c + (oY - b) + c);
        }
    if (maxBad < 0) return 0;
    if (maxBad >= maxEv) return null;
    return maxBad + 1;
  }

  // 回避率：残り試合を五分五分と仮定（少なければ全通り、多ければ抽選）
  function avoidRates() {
    const n = remaining.length, idx = {};
    targets.forEach((t, i) => (idx[t] = i));
    const cnt = targets.map(() => 0);
    const exact = n <= 17, trials = exact ? 1 << n : 20000;
    const w = new Array(targets.length), l = new Array(targets.length);
    for (let m = 0; m < trials; m++) {
      targets.forEach((t, i) => { w[i] = R[t].w; l[i] = R[t].l; });
      for (let i = 0; i < n; i++) {
        const g = remaining[i];
        const homeWin = exact ? (m >> i) & 1 : Math.random() < 0.5;
        const win = homeWin ? g.h : g.a, lose = homeWin ? g.a : g.h;
        if (win in idx) w[idx[win]]++;
        if (lose in idx) l[idx[lose]]++;
      }
      for (let i = 0; i < targets.length; i++) {
        for (let j = 0; j < targets.length; j++) {
          if (i === j) continue;
          const s = cmp(w[i], l[i], w[j], l[j]);
          if (tieSafe ? s >= 0 : s > 0) { cnt[i]++; break; }
        }
      }
    }
    const res = {};
    targets.forEach((t, i) => (res[t] = cnt[i] / trials));
    return { res, exact };
  }

  const rows = targets.map(X => {
    const r = R[X];
    tieSafe = false; // 回避確定・マジック：同率で並ぶ可能性があるうちは確定させない
    const safe = !advCanMakeLast(X, { OUT: 0 });
    const pairs = {};
    targets.filter(Y => Y !== X).forEach(Y => (pairs[Y] = pairMagic(X, Y)));
    tieSafe = true; // 支払い確定：同率までは持ち込めるなら、順位決定の規定で助かる可能性があるので確定させない
    const eliminated = !safe && targets.filter(Y => Y !== X).every(Y => pairMagic(X, Y) === null);
    tieSafe = !!cfg.tieIsSafe;
    const self = safe || eliminated ? null : selfMagic(X);
    const helpVals = Object.entries(pairs).filter(([, v]) => v !== null && v > 0);
    helpVals.sort((a, b) => a[1] - b[1]);
    return { t: X, ...r, safe, eliminated, self, pairs, best: helpVals[0] || null };
  });
  clSort(rows, games, g => period.months.includes(monthOf(g.d)), cfg.prevOrder);
  const top = rows[0];
  rows.forEach((r, i) => (r.gb = i === 0 ? null : ((rows[i - 1].w - r.w) + (r.l - rows[i - 1].l)) / 2)); // 1つ上の球団とのゲーム差
  const { res, exact } = avoidRates();
  rows.forEach(r => (r.rate = r.safe ? 1 : r.eliminated ? 0 : res[r.t]));
  const played = CL.some(t => R[t].w + R[t].l + R[t].d > 0);
  const finished = remaining.length === 0 && played;
  return { rows, R, remaining, exact, finished, played, excluded: cfg.excluded };
}

function fmtPct(w, l) {
  if (w + l === 0) return ".000";
  const p = w / (w + l);
  return p >= 1 ? "1.000" : p.toFixed(3).replace(/^0/, "");
}
function badge(r) {
  if (r.safe) return "⭐︎";
  if (r.eliminated) return "💸";
  return r.self === null ? "--" : "M" + r.self;
}
function copyText(a, label) {
  const lines = [`【J SPORTS支払い回避マジック（${label}）】`];
  for (const r of a.rows) {
    const gb = r.gb === null ? "---" : r.gb.toFixed(1);
    lines.push(`${TEAM[r.t].s} ( ${badge(r)} )： ${r.w}勝${r.l}敗${r.d}分 (${fmtPct(r.w, r.l)}) [残${r.rem}] ${gb}`);
  }
  a.excluded.forEach(t => lines.push(`${TEAM[t].s} (外)： ━━━━ 対象外 ━━━━`));
  return lines.join("\n");
}

const TEAM_URL = {
  T: "https://hanshintigers.jp/", G: "https://www.giants.jp/", DB: "https://www.baystars.co.jp/",
  D: "https://dragons.jp/", C: "https://www.carp.co.jp/", S: "https://www.yakult-swallows.co.jp/",
  H: "https://www.softbankhawks.co.jp/", F: "https://www.fighters.co.jp/", B: "https://www.buffaloes.co.jp/",
  E: "https://www.rakuteneagles.jp/", L: "https://www.seibulions.jp/", M: "https://www.marines.co.jp/",
};

/* ===== シーズン順位表（スポナビ形式） ===== */
function seasonTable(games, prevOrder, pend = []) {
  const R = {};
  CL.forEach(t => (R[t] = { t, w: 0, l: 0, d: 0, rem: 0, h2h: {} }));
  CL.forEach(t => CL.forEach(u => (R[t].h2h[u] = 0)));
  // 振替待ち（中止になって振替日がまだ決まっていない試合）も、これから必ず行う試合なので残り試合に入れる
  for (const p of pend || []) {
    if (R[p.a]) R[p.a].rem += p.n;
    if (p.b && R[p.b]) { R[p.b].rem += p.n; if (R[p.a]) { R[p.a].h2h[p.b] += p.n; R[p.b].h2h[p.a] += p.n; } }
  }
  for (const g of games) {
    if (g.st === "canc") continue;
    const inH = !!R[g.h], inA = !!R[g.a];
    if (g.st === "final") {
      if (g.hs > g.as) { if (inH) R[g.h].w++; if (inA) R[g.a].l++; }
      else if (g.hs < g.as) { if (inH) R[g.h].l++; if (inA) R[g.a].w++; }
      else { if (inH) R[g.h].d++; if (inA) R[g.a].d++; }
    } else {
      if (inH) R[g.h].rem++;
      if (inA) R[g.a].rem++;
      if (inH && inA) { R[g.h].h2h[g.a]++; R[g.a].h2h[g.h]++; }
    }
  }
  const rows = CL.map(t => R[t]);
  clSort(rows, games, () => true, prevOrder);
  rows.forEach((r, i) => {
    r.g = r.w + r.l + r.d;
    r.gb = i === 0 ? null : ((rows[i - 1].w - r.w) + (r.l - rows[i - 1].l)) / 2;
  });
  // 優勝マジック：首位があと何勝すれば、どの球団にも抜かれないか（同率は未確定扱い）
  const X = rows[0];
  let magic = null;
  for (let k = 0; k <= X.rem && magic === null; k++) {
    const ok = rows.slice(1).every(Y => {
      const h = X.h2h[Y.t];
      const vsY = Math.max(0, k - (X.rem - h)); // 他の相手で先に勝ちを使い切ったあと、残りを直接対決で勝つ
      return cmp(X.w + k, X.l + X.rem - k, Y.w + Y.rem - vsY, Y.l + vsY) > 0;
    });
    if (ok) magic = k;
  }
  X.magic = magic;
  return rows;
}
