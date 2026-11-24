"use strict";
/* ironlab console — 해시 라우팅 단일 페이지 앱 (프레임워크 없음) */

// ═════════════════════════ 유틸 ═════════════════════════
const $ = (s, el = document) => el.querySelector(s);
const $$ = (s, el = document) => [...el.querySelectorAll(s)];
const esc = (v) => String(v ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const store = {
  get(k, d) { try { const v = localStorage.getItem(k); return v === null ? d : v; } catch { return d; } },
  set(k, v) { try { v === null ? localStorage.removeItem(k) : localStorage.setItem(k, v); } catch {} },
};

async function api(path, opts = {}) {
  const headers = { ...(opts.headers || {}) };
  const t = store.get("ironlab_token", "");
  if (t) headers.Authorization = "Bearer " + t;
  if (opts.json !== undefined) { headers["Content-Type"] = "application/json"; opts.body = JSON.stringify(opts.json); }
  const res = await fetch(path, { ...opts, headers });
  if (res.status === 401) { showLogin(); throw new Error("로그인이 필요합니다"); }
  if (!res.ok) {
    let msg = `${res.status} ${res.statusText}`;
    try { const j = await res.json(); msg = typeof j.detail === "string" ? j.detail : JSON.stringify(j.detail); } catch {}
    throw new Error(msg);
  }
  if (opts.raw) return res;
  return (res.headers.get("content-type") || "").includes("json") ? res.json() : res.text();
}

const GiB = 2 ** 30;
const fmtBytes = (b) => {
  if (!b) return "0 B";
  const u = ["B", "KB", "MB", "GB", "TB"]; let i = 0;
  while (b >= 1024 && i < u.length - 1) { b /= 1024; i++; }
  return `${b.toFixed(b >= 100 || i === 0 ? 0 : 1)} ${u[i]}`;
};
const gb = (mb, d = 1) => (mb / 1024).toFixed(d);
const pct = (a, b) => (b ? Math.min(100, (a / b) * 100) : 0);
const tone = (p) => (p >= 90 ? "bad" : p >= 75 ? "warn" : "ok");
const bar = (p, cls) => `<div class="bar ${cls || tone(p)}"><span style="width:${p.toFixed(1)}%"></span></div>`;
const now = () => Date.now() / 1000;
function ago(ts) {
  if (!ts) return "—";
  const s = Math.max(0, now() - ts);
  if (s < 45) return "방금";
  if (s < 3600) return `${Math.round(s / 60)}분 전`;
  if (s < 86400) return `${Math.round(s / 3600)}시간 전`;
  return `${Math.round(s / 86400)}일 전`;
}
function dur(sec) {
  if (sec == null) return "—";
  sec = Math.max(0, Math.round(sec));
  const d = Math.floor(sec / 86400), h = Math.floor((sec % 86400) / 3600), m = Math.floor((sec % 3600) / 60), s = sec % 60;
  if (d) return `${d}일 ${h}시간`;
  if (h) return `${h}시간 ${m}분`;
  if (m) return `${m}분 ${s}초`;
  return `${s}초`;
}
const when = (ts) => (ts ? new Date(ts * 1000).toLocaleString("ko-KR", { month: "numeric", day: "numeric", hour: "2-digit", minute: "2-digit", second: "2-digit" }) : "—");

// 사용자 색: 목록 정렬 순서로 팔레트 칸 배정 (8명까지 겹치지 않음)
const PALETTE = ["#2f6fe4", "#e0702a", "#18a06a", "#9b4fd8", "#d4a20c", "#d0433c", "#1597a8", "#c2549a"];
let colorSlots = {};
function assignColors(names) { colorSlots = {}; [...names].sort().forEach((n, i) => (colorSlots[n] = PALETTE[i % PALETTE.length])); }
function userColor(name) {
  if (colorSlots[name]) return colorSlots[name];
  let h = 0; for (const c of name || "?") h = (h * 31 + c.charCodeAt(0)) >>> 0;
  return PALETTE[h % PALETTE.length];
}
const avatar = (n) => `<span class="avatar" style="background:${userColor(n)}">${esc((n || "?").slice(0, 1).toUpperCase())}</span>`;
const userCell = (n) => `<span class="user-cell">${avatar(n)}${esc(n)}</span>`;

const I = {
  dash: '<path d="M2.5 2.5h4.5v6h-4.5zM9 2.5h4.5v3.5H9zM9 8h4.5v5.5H9zM2.5 10.5h4.5v3H2.5z"/>',
  ws: '<rect x="2" y="3" width="12" height="9" rx="1.2"/><path d="M5 14h6M4.5 6.5 6.5 8l-2 1.5M8 9.5h3"/>',
  llm: '<path d="M3 3.5h10a1 1 0 0 1 1 1v6a1 1 0 0 1-1 1H8l-3 2.5v-2.5H3a1 1 0 0 1-1-1v-6a1 1 0 0 1 1-1z"/><path d="M5 7h.01M8 7h.01M11 7h.01"/>',
  send: '<path d="M2.5 8 13.5 3l-4 10.5-2-4.5z"/>',
  jobs: '<path d="M3 4h10M3 8h10M3 12h6"/><circle cx="12.5" cy="12" r="1.5"/>',
  files: '<path d="M2.5 4.5a1 1 0 0 1 1-1h3l1.5 1.5h4.5a1 1 0 0 1 1 1v6.5a1 1 0 0 1-1 1h-9a1 1 0 0 1-1-1z"/>',
  users: '<circle cx="6" cy="5.5" r="2.3"/><path d="M2 13c.4-2.2 2-3.5 4-3.5s3.6 1.3 4 3.5M11 3.5a2.2 2.2 0 0 1 0 4.2M12 9.7c1.2.4 2 1.6 2.2 3.3"/>',
  audit: '<path d="M4 2.5h6l2.5 2.5v8.5h-8.5zM6 7h4.5M6 9.5h4.5M6 12h2.5"/>',
  account: '<circle cx="8" cy="6" r="2.7"/><path d="M3 14c.6-2.6 2.6-4 5-4s4.4 1.4 5 4"/>',
  chev: '<path d="m6 4 4 4-4 4"/>',
  close: '<path d="m4 4 8 8M12 4l-8 8"/>',
  plus: '<path d="M8 3v10M3 8h10"/>',
  play: '<path d="M5 3.5v9l7-4.5z"/>',
  stop: '<rect x="4" y="4" width="8" height="8" rx="1"/>',
  upload: '<path d="M8 11V3M4.5 6.5 8 3l3.5 3.5M3 13h10"/>',
  download: '<path d="M8 3v8M4.5 7.5 8 11l3.5-3.5M3 13h10"/>',
  folder: '<path d="M2.5 4.5a1 1 0 0 1 1-1h3l1.5 1.5h4.5a1 1 0 0 1 1 1v6.5a1 1 0 0 1-1 1h-9a1 1 0 0 1-1-1z"/>',
  file: '<path d="M4 2.5h5l3 3v8H4zM9 2.5v3h3"/>',
  link: '<path d="M7 9a2.5 2.5 0 0 0 3.5 0l2-2a2.5 2.5 0 0 0-3.5-3.5l-.7.7M9 7a2.5 2.5 0 0 0-3.5 0l-2 2A2.5 2.5 0 0 0 7 12.5l.7-.7"/>',
  trash: '<path d="M3 4.5h10M6.5 4.5V3h3v1.5M4.5 4.5l.6 8.5h5.8l.6-8.5"/>',
  alert: '<path d="M8 2.5 14 13H2zM8 6.5v3M8 11.3v.2"/>',
  info: '<circle cx="8" cy="8" r="5.5"/><path d="M8 7.5v3.5M8 5.2v.2"/>',
  search: '<circle cx="7" cy="7" r="4.5"/><path d="m10.5 10.5 3 3"/>',
  menu: '<path d="M2.5 4.5h11M2.5 8h11M2.5 11.5h11"/>',
  key: '<circle cx="5.5" cy="10.5" r="2.5"/><path d="m7.3 8.7 5.2-5.2M10.5 5.5l1.5 1.5"/>',
  views: '<rect x="1.5" y="4" width="4" height="8" rx="1"/><rect x="6" y="3" width="4" height="10" rx="1"/><rect x="10.5" y="4" width="4" height="8" rx="1"/>',
  cube: '<path d="M8 2 13.5 5v6L8 14l-5.5-3V5zM2.5 5 8 8l5.5-3M8 8v6"/>',
  image: '<rect x="2.5" y="3" width="11" height="10" rx="1.2"/><circle cx="6" cy="6.5" r="1.2"/><path d="m3 12 3.5-3.5 2.5 2.5 2-2 2.5 2.5"/>',
  music: '<path d="M6 12.5V4l7-1.5v8.5"/><circle cx="4.5" cy="12.5" r="1.5"/><circle cx="11.5" cy="11" r="1.5"/>',
  video: '<rect x="1.8" y="4" width="8.7" height="8" rx="1.3"/><path d="m10.5 7 3.7-2.2v6.4L10.5 9"/>',
  mic: '<rect x="6" y="1.8" width="4" height="7.7" rx="2"/><path d="M3.6 7.8a4.4 4.4 0 0 0 8.8 0M8 12.2v2"/>',
  wave: '<path d="M2 7v2M4.7 5v6M7.4 2.5v11M10.1 4.5v7M12.8 6.5v3"/>',
  sliders: '<path d="M3.5 2.5v4.2M3.5 9.8v3.7M8 2.5v1.7M8 7.3v6.2M12.5 2.5v6.2M12.5 11.8v1.7"/><circle cx="3.5" cy="8.2" r="1.5"/><circle cx="8" cy="5.7" r="1.5"/><circle cx="12.5" cy="10.2" r="1.5"/>',
  redo: '<path d="M13 3v3.5H9.5M13 6.5A5 5 0 1 0 13 9.5"/>',
  flow: '<rect x="1.5" y="2.5" width="5.5" height="4" rx="1"/><rect x="9" y="9.5" width="5.5" height="4" rx="1"/><path d="M7 4.5h2.2a1.6 1.6 0 0 1 1.6 1.6v3.4"/>',
  logout: '<path d="M6 3H3.5v10H6M10 5l3 3-3 3M13 8H6"/>',
};
const icon = (n) => `<svg viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round">${I[n]}</svg>`;

function toast(msg, bad = false) {
  const el = document.createElement("div");
  el.className = "toast" + (bad ? " bad" : "");
  el.textContent = msg;
  $("#toasts").append(el);
  setTimeout(() => el.remove(), bad ? 6000 : 3200);
}
const fail = (e) => toast(e.message || String(e), true);

// 되묻기 대화상자. 브라우저 기본 대화상자는 화면과 따로 놀고, 사용자가 "이 페이지의 대화상자 차단"을
// 한 번 고르면 그 뒤로는 묻지도 않고 '취소'로 처리되어 버튼이 먹통처럼 보인다.
function confirmDialog(message, { ok = "확인", danger = false } = {}) {
  return new Promise((resolve) => {
    const wrap = document.createElement("div");
    wrap.className = "confirm-wrap";
    wrap.innerHTML = `<div class="confirm" role="alertdialog" aria-modal="true" aria-describedby="confirm-msg">
      <p id="confirm-msg">${esc(message)}</p>
      <div class="confirm-actions"><button class="btn" data-no>취소</button><button class="btn ${danger ? "danger solid" : "primary"}" data-yes>${esc(ok)}</button></div></div>`;
    const before = document.activeElement;
    const [no, yes] = wrap.querySelectorAll("button");
    const done = (v) => { document.removeEventListener("keydown", onKey, true); wrap.remove(); before?.focus?.(); resolve(v); };
    const onKey = (e) => {
      if (e.key === "Escape") { e.preventDefault(); e.stopPropagation(); done(false); }
      else if (e.key === "Tab") { e.preventDefault(); (document.activeElement === yes ? no : yes).focus(); }   // 초점을 대화상자 안에 둔다
    };
    document.addEventListener("keydown", onKey, true);      // 아래에 깔린 드로어가 Esc 를 먼저 받지 않도록 캡처 단계에서
    no.onclick = () => done(false);
    yes.onclick = () => done(true);
    wrap.onclick = (e) => { if (e.target === wrap) done(false); };
    document.body.appendChild(wrap);
    yes.focus();
  });
}

// 라벨과 입력을 연결한다 (라벨을 눌러 입력으로 가고, 화면 낭독기가 입력 이름을 읽게)
let fieldSeq = 0;
function linkLabels(root = document) {
  root.querySelectorAll(".field").forEach((f) => {
    const label = f.querySelector(":scope > label:not([for])");
    const ctl = f.querySelectorAll("input, select, textarea");
    if (!label || ctl.length !== 1) return;
    if (!ctl[0].id) ctl[0].id = `fld-${++fieldSeq}`;
    label.htmlFor = ctl[0].id;
  });
}
new MutationObserver(() => linkLabels()).observe(document.body, { childList: true, subtree: true });

async function copyText(text) {
  try { await navigator.clipboard.writeText(text); toast("클립보드에 복사했습니다"); } catch { toast("복사하지 못했습니다. 직접 선택해 복사하세요", true); }
}
document.addEventListener("click", (e) => {
  const b = e.target.closest("[data-copy]");
  if (b) copyText(b.dataset.copy);
});

// 정렬 가능한 표
function sortRows(rows, key, dir) {
  const k = typeof key === "function" ? key : (r) => r[key];
  return [...rows].sort((a, b) => {
    const x = k(a), y = k(b);
    if (x == null && y == null) return 0;
    if (x == null) return 1;
    if (y == null) return -1;
    const c = typeof x === "string" ? x.localeCompare(y, "ko") : x - y;
    return dir === "asc" ? c : -c;
  });
}
function th(label, key, sort, cls = "") {
  if (!key) return `<th class="${cls}">${label}</th>`;
  const on = sort.key === key;
  return `<th class="sortable ${cls}" data-sort="${key}">${label}<span class="arrow">${on ? (sort.dir === "asc" ? "▲" : "▼") : ""}</span></th>`;
}
function bindSort(table, sort, rerender) {
  table.querySelectorAll("th[data-sort]").forEach((h) => (h.onclick = () => {
    const k = h.dataset.sort;
    sort.dir = sort.key === k && sort.dir === "desc" ? "asc" : "desc";
    sort.key = k;
    rerender();
  }));
}

// ═════════════════════════ 상태 ═════════════════════════
const S = {
  me: null, health: null, overview: null, history: [], recent: [], histAt: 0, jobsAt: 0,
  procSort: { key: "cpu", dir: "desc" },
  page: "dashboard", param: null,
  refreshMs: Number(store.get("ironlab_refresh", 5000)),
  timer: null, busy: false,
  jobs: [], jobScope: store.get("ironlab_job_scope", "mine"), jobStatus: "all", jobQuery: "", jobSort: { key: "id", dir: "desc" },
  userSort: { key: "active", dir: "desc" },
  adminUsers: [], adminSort: { key: "name", dir: "asc" }, adminQuery: "",
  events: [], evKind: "all", evQuery: "",
  area: "home", path: "", files: null, fileSort: { key: "name", dir: "asc" }, fileQuery: "",
  chartRange: 3600,
  gen: {},           // 생성 화면의 상태. 종류(3d, image, music, video, voice, sfx, polish) → /api/gen/<종류> 가 돌려준 것
  flow: null,        // 노드 편집기의 상태. /api/flow 가 돌려준 것 (노드 종류, 저장한 그래프, 지난 실행)
  drawer: null,
};

const PAGES = {
  dashboard: { title: "대시보드", group: "모니터링", icon: "dash" },
  workspace: { title: "내 워크스페이스", group: "컴퓨팅", icon: "ws" },
  jobs: { title: "GPU 작업", group: "컴퓨팅", icon: "jobs" },
  llm: { title: "로컬 LLM", group: "컴퓨팅", icon: "llm" },
  // 생성은 두 메뉴로 나눈다. Easy: 종류마다 양식 하나에 단계별 상세 설정. Advanced: 노드를 이어 붙여 여러 단계를 한 번에 돌리는 편집기
  gen3d: { title: "3D 생성", group: "생성 · Easy", icon: "cube" },
  genmv: { title: "여러 방향 그림", group: "생성 · Easy", icon: "views" },
  genimage: { title: "이미지 생성", group: "생성 · Easy", icon: "image" },
  genmusic: { title: "음악 생성", group: "생성 · Easy", icon: "music" },
  genvideo: { title: "영상 생성", group: "생성 · Easy", icon: "video" },
  genvoice: { title: "음성 생성", group: "생성 · Easy", icon: "mic" },
  gensfx: { title: "효과음 생성", group: "생성 · Easy", icon: "wave" },
  genpolish: { title: "소리 다듬기", group: "생성 · Easy", icon: "sliders" },
  flow: { title: "노드 편집기", group: "생성 · Advanced", icon: "flow" },
  files: { title: "파일", group: "스토리지", icon: "files" },
  users: { title: "사용자", group: "관리", icon: "users", admin: true },
  audit: { title: "감사 로그", group: "관리", icon: "audit", admin: true },
  account: { title: "내 계정", group: "계정", icon: "account", hidden: true },
};

// ═════════════════════════ 로그인·부팅 ═════════════════════════
function showLogin(msg = "") {
  clearInterval(S.timer);
  closeDrawer();
  $("#app").classList.add("hidden");
  $("#login").classList.remove("hidden");
  $("#login-error").textContent = msg;
  $("#login-line").innerHTML = PALETTE.slice(0, 4).map((c, i) => `<div style="flex:${[5, 3, 2, 1][i]};background:${c}"></div>`).join("")
    + '<div style="flex:3"></div>';
  setTimeout(() => $("#token-input").focus(), 50);
}

$("#login-form").addEventListener("submit", async (e) => {
  e.preventDefault();
  store.set("ironlab_token", $("#token-input").value.trim());
  boot();
});

async function boot() {
  const t = store.get("ironlab_token", "");
  const res = await fetch("/api/me", { headers: t ? { Authorization: "Bearer " + t } : {} }).catch(() => null);
  if (!res || !res.ok) {
    const msg = !res ? "서버에 연결하지 못했습니다" : res.status === 401 ? (t ? "토큰이 맞지 않습니다. 관리자에게 새 토큰을 요청하세요." : "") : res.status === 403 ? "비활성화된 계정입니다" : `서버 오류 (${res.status})`;
    if (res && res.status === 401) store.set("ironlab_token", null);
    showLogin(msg);
    return;
  }
  S.me = await res.json();
  S.health = await api("/api/health").catch(() => ({}));
  $("#login").classList.add("hidden");
  $("#app").classList.remove("hidden");
  $("#env-tag").classList.toggle("hidden", !S.health.mock);
  $("#me-avatar").outerHTML = `<span class="avatar" id="me-avatar" style="background:#41506a">${esc(S.me.name[0].toUpperCase())}</span>`;
  $("#me-name").textContent = S.me.name;
  $("#menu-name").textContent = S.me.name;
  $("#menu-role").textContent = S.me.role === "admin" ? "관리자" : "사용자";
  $("#refresh-every").value = String(S.refreshMs);
  $("#menu-toggle").innerHTML = icon("menu");
  renderNav();
  route();
  schedule();
}

// ═════════════════════════ 라우팅·크롬 ═════════════════════════
function go(page, param = null) { location.hash = "#/" + page + (param != null ? "/" + encodeURIComponent(param) : ""); }
window.addEventListener("hashchange", route);

function route() {
  if (!S.me) return;
  const [, page = "dashboard", param = null] = location.hash.split("/");
  let p = PAGES[page] ? page : "dashboard";
  if (PAGES[p].admin && S.me.role !== "admin") p = "dashboard";
  const pageChanged = p !== S.page || !$("#page").dataset.page;
  S.page = p;
  S.param = param ? decodeURIComponent(param) : null;
  $("#sidebar").classList.remove("open");
  renderNav();
  renderCrumbs();
  if (pageChanged) {
    closeDrawer(true);
    $("#page").dataset.page = p;
    VIEWS[p].render();
  }
  VIEWS[p].onParam?.(S.param);
  refresh();
}

function renderNav() {
  const groups = {};
  for (const [k, p] of Object.entries(PAGES)) {
    if (p.hidden || (p.admin && S.me.role !== "admin")) continue;
    (groups[p.group] ||= []).push([k, p]);
  }
  const q = S.overview?.queue;
  $("#side-nav").innerHTML = Object.entries(groups).map(([g, items]) => `<div class="nav-group"><span>${g}</span>${items.map(([k, p]) => `
    <button class="nav-item ${S.page === k ? "active" : ""}" data-go="${k}">${icon(p.icon)}${p.title}
      ${k === "jobs" && q && q.queued ? `<span class="count">대기 ${q.queued}</span>` : ""}</button>`).join("")}</div>`).join("");
}
$("#side-nav").addEventListener("click", (e) => { const b = e.target.closest("[data-go]"); if (b) go(b.dataset.go); });
$("#menu-toggle").addEventListener("click", () => $("#sidebar").classList.toggle("open"));

function renderCrumbs(extra) {
  const p = PAGES[S.page];
  $("#crumbs").innerHTML = `<span class="parent">${p.group}</span>${icon("chev")}<b>${p.title}</b>${extra ? icon("chev") + `<b>${esc(extra)}</b>` : ""}`;
}

function renderSideGpu() {
  const o = S.overview;
  if (!o) return;
  const g = o.gpu, h = o.host;
  const vp = pct(g.used_mb, g.total_mb), mp = pct(h.mem_used, h.mem_total);
  const row = (label, value, p) => `<div class="row"><span>${label}</span><b>${value}</b></div><div class="mini"><span style="width:${p}%"></span></div>`;
  $("#side-gpu").innerHTML = (g.available ? `
    ${row("GPU 사용률", `${g.util ?? 0}%`, g.util ?? 0)}
    ${row("VRAM", `${gb(g.used_mb)} / ${gb(g.total_mb, 0)} GB`, vp)}
    <div class="row sep"><span>${g.temp != null ? Math.round(g.temp) + "°C" : ""}</span><span>${g.power_w != null ? g.power_w + " W" : ""}</span></div>`
    : `<div class="row sep"><span>GPU</span><b style="color:#f0645d">사용 불가</b></div>`) + `
    ${row("CPU", `${h.cpu_percent.toFixed(0)}%`, h.cpu_percent)}
    ${row("RAM", `${(h.mem_used / GiB).toFixed(1)} / ${(h.mem_total / GiB).toFixed(0)} GB`, mp)}
    ${o.llm && o.llm.state !== "off" ? `<div class="row llm-row" style="margin:8px 0 0"><span>LLM</span><b>${esc(o.llm.model ? o.llm.model.name : "")} ${o.llm.state === "ready" ? "" : o.llm.state === "loading" ? "(올리는 중)" : "(오류)"}</b></div>` : ""}`;
}

// 사용자 메뉴
$("#user-btn").addEventListener("click", (e) => { e.stopPropagation(); $("#user-menu").classList.toggle("hidden"); });
document.addEventListener("click", (e) => { if (!e.target.closest("#user-menu")) $("#user-menu").classList.add("hidden"); });
$("#user-menu").addEventListener("click", (e) => { const b = e.target.closest("[data-nav]"); if (b) { go(b.dataset.nav); $("#user-menu").classList.add("hidden"); } });
$("#logout").addEventListener("click", () => { store.set("ironlab_token", null); location.hash = ""; location.reload(); });

// ═════════════════════════ 새로고침 루프 ═════════════════════════
function schedule() {
  clearInterval(S.timer);
  if (S.refreshMs > 0) S.timer = setInterval(() => refresh(), S.refreshMs);
}
$("#refresh-every").addEventListener("change", (e) => { S.refreshMs = Number(e.target.value); store.set("ironlab_refresh", S.refreshMs); schedule(); });
$("#refresh-now").addEventListener("click", () => refresh(true));

async function refresh(manual = false) {
  if (S.busy || !S.me || document.hidden && !manual) return;
  S.busy = true;
  $("#refresh-now").classList.add("spin");
  try {
    S.overview = await api("/api/overview");
    assignColors(S.overview.users.map((u) => u.name));
    renderSideGpu();
    renderNav();
    await VIEWS[S.page].refresh?.();
    if (S.drawer?.refresh) await S.drawer.refresh();
    const stamp = new Date().toLocaleTimeString("ko-KR", { hour: "2-digit", minute: "2-digit", second: "2-digit" });
    const u = $("#updated");
    S.failedSince = null;
    // 서버가 응답은 하지만 수집이 멈춘 경우: 화면 숫자가 굳어 있다는 것을 알린다
    const stale = (S.overview.age_s || 0) > STALE_AFTER_S;
    u.classList.toggle("stale", stale);
    u.textContent = stale ? `수치가 ${Math.round(S.overview.age_s)}초 전 것입니다` : "업데이트 " + stamp;
    u.title = stale ? "서버의 사용량 수집이 멈췄습니다. journalctl -u ironlab 을 확인하세요." : "";
  } catch (e) {
    // 자동 갱신 실패는 알림을 띄우지 않는다(1초마다 쌓인다). 대신 언제부터 못 받고 있는지를 계속 보여 준다
    S.failedSince = S.failedSince || new Date();
    const u = $("#updated");
    u.classList.add("stale");
    u.textContent = `갱신 실패 (${S.failedSince.toLocaleTimeString("ko-KR", { hour: "2-digit", minute: "2-digit", second: "2-digit" })}부터)`;
    u.title = e.message || String(e);
    if (manual) fail(e);
  } finally {
    S.busy = false;
    setTimeout(() => $("#refresh-now").classList.remove("spin"), 300);
  }
}
document.addEventListener("visibilitychange", () => { if (!document.hidden) refresh(); });

// ═════════════════════════ 드로어 ═════════════════════════
function openDrawer({ title, sub = "", tabs = null, tab = null, body, foot = "", width = 560, onClose = null, refresh = null, onTab = null, dispose = null }) {
  S.drawer?.dispose?.();  // 다른 드로어 위에 열 때 이전 타이머 정리
  S.drawer = { onClose, refresh, onTab, tab, dispose };
  const d = $("#drawer");
  d.style.setProperty("--drawer-w", width + "px");
  d.innerHTML = `
    <div class="drawer-head"><div><h3>${title}</h3>${sub ? `<div class="sub">${sub}</div>` : ""}</div>
      <button class="icon-btn" data-drawer-close aria-label="닫기">${icon("close")}</button></div>
    ${tabs ? `<div class="drawer-tabs">${tabs.map(([k, l]) => `<button data-tab="${k}" class="${k === tab ? "on" : ""}">${l}</button>`).join("")}</div>` : ""}
    <div class="drawer-body" id="drawer-body">${body}</div>
    ${foot ? `<div class="drawer-foot" id="drawer-foot">${foot}</div>` : ""}`;
  $("#scrim").classList.remove("hidden");
  requestAnimationFrame(() => d.classList.remove("closed"));
  d.querySelectorAll("[data-drawer-close]").forEach((b) => (b.onclick = () => closeDrawer()));
  d.querySelectorAll(".drawer-tabs [data-tab]").forEach((b) => (b.onclick = () => {
    d.querySelectorAll(".drawer-tabs [data-tab]").forEach((x) => x.classList.toggle("on", x === b));
    S.drawer.tab = b.dataset.tab;
    S.drawer.onTab?.(b.dataset.tab);
  }));
  setTimeout(() => d.querySelector("input:not([type=hidden]), textarea, select")?.focus(), 60);
}
function closeDrawer(silent = false) {
  if (!S.drawer) return;
  const cb = S.drawer.onClose;
  S.drawer.dispose?.();
  S.drawer = null;
  $("#drawer").classList.add("closed");
  $("#scrim").classList.add("hidden");
  if (!silent) cb?.();
}
$("#scrim").addEventListener("click", () => closeDrawer());

// ═════════════════════════ 명령 팔레트 ═════════════════════════
let palItems = [], palIndex = 0;
function paletteCommands() {
  const cmds = [];
  for (const [k, p] of Object.entries(PAGES)) {
    if (p.admin && S.me.role !== "admin") continue;
    cmds.push({ label: p.title, grp: "이동", icon: p.icon, run: () => go(k) });
  }
  cmds.push({ label: "작업 제출", grp: "실행", icon: "plus", run: () => openSubmit() });
  cmds.push({ label: "워크스페이스 시작", grp: "실행", icon: "play", run: () => wsAction("start") });
  cmds.push({ label: "워크스페이스 중지", grp: "실행", icon: "stop", run: () => wsAction("stop") });
  cmds.push({ label: "SSH 키 등록", grp: "실행", icon: "key", run: () => go("account") });
  cmds.push({ label: "LLM에게 묻기", grp: "실행", icon: "llm", run: () => { go("llm"); setTimeout(() => $("#chat-input")?.focus(), 150); } });
  if (S.me.role === "admin") cmds.push({ label: "사용자 추가", grp: "관리", icon: "users", run: () => { go("users"); setTimeout(openCreateUser, 50); } });
  cmds.push({ label: "로그아웃", grp: "계정", icon: "logout", run: () => $("#logout").click() });
  return cmds;
}
function openPalette() {
  $("#palette").classList.remove("hidden");
  $("#palette-scrim").classList.remove("hidden");
  const inp = $("#palette-input");
  inp.value = "";
  filterPalette();
  inp.focus();
}
function closePalette() { $("#palette").classList.add("hidden"); $("#palette-scrim").classList.add("hidden"); }
function filterPalette() {
  const q = $("#palette-input").value.trim().toLowerCase();
  palItems = paletteCommands().filter((c) => !q || c.label.toLowerCase().includes(q) || c.grp.includes(q));
  const m = q.match(/^#?(\d+)$/);
  if (m) palItems.unshift({ label: `작업 #${m[1]} 열기`, grp: "작업", icon: "jobs", run: () => go("jobs", m[1]) });
  palIndex = 0;
  drawPalette();
}
function drawPalette() {
  $("#palette-list").innerHTML = palItems.length
    ? palItems.map((c, i) => `<li data-i="${i}" class="${i === palIndex ? "on" : ""}">${icon(c.icon)}${esc(c.label)}<span class="grp">${c.grp}</span></li>`).join("")
    : '<li class="none">일치하는 항목이 없습니다</li>';
}
$("#open-palette").addEventListener("click", openPalette);
$("#palette-scrim").addEventListener("click", closePalette);
$("#palette-input").addEventListener("input", filterPalette);
$("#palette-list").addEventListener("click", (e) => { const li = e.target.closest("[data-i]"); if (li) { closePalette(); palItems[li.dataset.i].run(); } });
$("#palette-input").addEventListener("keydown", (e) => {
  if (e.key === "ArrowDown") { palIndex = Math.min(palItems.length - 1, palIndex + 1); drawPalette(); e.preventDefault(); }
  if (e.key === "ArrowUp") { palIndex = Math.max(0, palIndex - 1); drawPalette(); e.preventDefault(); }
  if (e.key === "Enter" && palItems[palIndex]) { closePalette(); palItems[palIndex].run(); }
});
document.addEventListener("keydown", (e) => {
  if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === "k" && S.me) { e.preventDefault(); openPalette(); }
  if (e.key === "Escape") {
    if (!$("#palette").classList.contains("hidden")) closePalette();
    else if (S.drawer) closeDrawer();
  }
});

// ═════════════════════════ 차트 ═════════════════════════
function lineChart(el, points, { max, unit, series }) {
  // series: [{key, color, label}] — 여러 개면 누적 영역
  if (points.length < 2) { el.innerHTML = '<div class="empty" style="padding:50px 0">데이터를 모으는 중입니다</div>'; return; }
  const W = el.clientWidth || 600, H = el.clientHeight || 150, L = 34, B = 18, T = 6;
  const t0 = points[0].ts, t1 = points[points.length - 1].ts || t0 + 1;
  const x = (t) => L + ((t - t0) / Math.max(1, t1 - t0)) * (W - L - 4);
  const y = (v) => T + (H - T - B) * (1 - Math.min(v, max) / max);
  let base = points.map(() => 0);
  const paths = series.map((s) => {
    const top = points.map((p, i) => base[i] + (s.value(p) || 0));
    const up = points.map((p, i) => `${i ? "L" : "M"}${x(p.ts).toFixed(1)},${y(top[i]).toFixed(1)}`).join("");
    const down = points.map((p, i) => `L${x(p.ts).toFixed(1)},${y(base[i]).toFixed(1)}`).reverse().join("");
    base = top;
    return `<path d="${up}${down}Z" fill="${s.color}" fill-opacity="${series.length > 1 ? 0.78 : 0.14}"/>` +
      (series.length === 1 ? `<path d="${up}" fill="none" stroke="${s.color}" stroke-width="1.8"/>` : "");
  }).join("");
  const ticks = [0, 0.5, 1].map((f) => max * f);
  const fmtT = (t) => new Date(t * 1000).toLocaleTimeString("ko-KR", { hour: "2-digit", minute: "2-digit", ...(t1 - t0 < 300 ? { second: "2-digit" } : {}) });
  el.innerHTML = `<svg viewBox="0 0 ${W} ${H}">
    <g class="grid">${ticks.map((v) => `<line x1="${L}" x2="${W - 4}" y1="${y(v)}" y2="${y(v)}"/>`).join("")}</g>
    <g class="axis">${ticks.map((v) => `<text x="${L - 6}" y="${y(v) + 3}" text-anchor="end">${Math.round(v)}${unit}</text>`).join("")}
      <text x="${L}" y="${H - 3}">${fmtT(t0)}</text><text x="${W - 4}" y="${H - 3}" text-anchor="end">${fmtT(t1)}</text></g>
    ${paths}
    <line class="cross" x1="0" x2="0" y1="${T}" y2="${H - B}" stroke="var(--text-2)" stroke-dasharray="3 3" visibility="hidden"/>
  </svg><div class="tip hidden"></div>`;
  const svg = el.querySelector("svg"), tip = el.querySelector(".tip"), cross = el.querySelector(".cross");
  svg.onmousemove = (e) => {
    const r = svg.getBoundingClientRect();
    const mx = ((e.clientX - r.left) / r.width) * W;
    let best = 0;
    points.forEach((p, i) => { if (Math.abs(x(p.ts) - mx) < Math.abs(x(points[best].ts) - mx)) best = i; });
    const p = points[best], px = x(p.ts);
    cross.setAttribute("x1", px); cross.setAttribute("x2", px); cross.setAttribute("visibility", "visible");
    const rows = series.filter((s) => s.value(p)).map((s) => `<div><i style="display:inline-block;width:8px;height:8px;border-radius:2px;background:${s.color};margin-right:6px"></i>${esc(s.label)} ${s.value(p).toFixed(unit === "%" ? 0 : 1)}${unit}</div>`).join("");
    tip.innerHTML = `<b>${fmtT(p.ts)}</b>${rows || '<div class="muted">사용 없음</div>'}`;
    tip.style.left = (px / W) * r.width + "px";
    tip.style.top = "8px";
    tip.classList.remove("hidden");
  };
  svg.onmouseleave = () => { tip.classList.add("hidden"); cross.setAttribute("visibility", "hidden"); };
}

// ═════════════════════════ 페이지: 대시보드 ═════════════════════════
function pageHead(title, desc, actions = "") {
  return `<div class="page-head"><div><h1>${title}</h1>${desc ? `<p>${desc}</p>` : ""}</div>${actions ? `<div class="actions">${actions}</div>` : ""}</div>`;
}

const Dashboard = {
  render() {
    $("#page").innerHTML = pageHead("대시보드", "GPU 한 장과 이 서버를 지금 누가 얼마나 쓰고 있는지 보여줍니다.",
      `<button class="btn" data-go-page="workspace">${icon("ws")}내 워크스페이스</button><button class="btn primary" id="dash-submit">${icon("plus")}작업 제출</button>`) + `
      <div id="dash-alerts"></div>
      <div class="stats" id="dash-stats"></div>
      <section class="panel"><div class="panel-head"><h2>GPU 점유</h2><span class="sub" id="alloc-name"></span></div>
        <div class="panel-body" id="dash-alloc"></div></section>
      <div class="grid-2" style="margin-bottom:16px">
        <section class="panel"><div class="panel-head"><h2>GPU 사용률</h2><div class="right"><div class="seg" data-range>${rangeBtns()}</div></div></div>
          <div class="panel-body"><div class="chart" id="chart-util"></div></div></section>
        <section class="panel"><div class="panel-head"><h2>사용자별 VRAM</h2><div class="right"><div class="seg" data-range>${rangeBtns()}</div></div></div>
          <div class="panel-body"><div class="chart" id="chart-vram"></div></div></section>
      </div>
      <section class="panel"><div class="panel-head"><h2>CPU·메모리 점유</h2><span class="sub" id="sys-name"></span></div>
        <div class="panel-body" id="dash-sys"></div></section>
      <section class="panel"><div class="panel-head"><h2>저장공간 점유</h2><span class="sub" id="disk-name"></span></div>
        <div class="panel-body" id="dash-disk"></div></section>
      <div class="grid-2" style="margin-bottom:16px">
        <section class="panel"><div class="panel-head"><h2>CPU 사용</h2><div class="right"><div class="seg" data-range>${rangeBtns()}</div></div></div>
          <div class="panel-body"><div class="chart" id="chart-cpu"></div></div></section>
        <section class="panel"><div class="panel-head"><h2>메모리 사용</h2><div class="right"><div class="seg" data-range>${rangeBtns()}</div></div></div>
          <div class="panel-body"><div class="chart" id="chart-mem"></div></div></section>
      </div>
      <section class="panel flush"><div class="panel-head"><h2>사용자 활동</h2><span class="sub" id="act-sub"></span></div>
        <div class="panel-body table-wrap"><table class="dt" id="dash-users"></table></div></section>
      <section class="panel flush"><div class="panel-head"><h2>CPU·메모리 프로세스</h2><span class="sub">CPU나 메모리를 많이 쓰는 프로세스를 컨테이너와 사용자로 추적합니다</span></div>
        <div class="panel-body table-wrap"><table class="dt" id="dash-sysprocs"></table></div></section>
      <section class="panel flush"><div class="panel-head"><h2>GPU 프로세스</h2><span class="sub">NVML이 보고한 프로세스를 컨테이너와 사용자로 추적합니다</span></div>
        <div class="panel-body table-wrap"><table class="dt" id="dash-procs"></table></div></section>`;
    $("#dash-submit").onclick = () => openSubmit();
    $$("[data-go-page]").forEach((b) => (b.onclick = () => go(b.dataset.goPage)));
    $$("[data-range]").forEach((seg) => (seg.onclick = (e) => {
      const b = e.target.closest("button[data-r]");
      if (!b) return;
      S.chartRange = Number(b.dataset.r);
      S.histAt = 0;       // 구간이 바뀌면 그 구간의 기록을 바로 다시 받는다
      $$("[data-range] button").forEach((x) => x.classList.toggle("on", Number(x.dataset.r) === S.chartRange));
      Dashboard.charts();
      refresh(true);
    }));
  },
  async refresh() {
    const o = S.overview;
    // 1초 주기로 돌아도 부담이 없도록, 자주 바뀌지 않는 것은 덜 자주 받는다
    const t = now(), fast = S.chartRange <= FAST_RANGE;
    await Promise.all([
      !S.jobsAll || t - S.jobsAt >= 4 ? api("/api/jobs?scope=all&limit=300").then((j) => { S.jobsAll = j; S.jobsAt = t; }).catch(() => {}) : null,
      fast ? api("/api/history?res=fast").then((h) => { S.recent = h; }).catch(() => {})
        : t - S.histAt >= 9 ? api("/api/history").then((h) => { S.history = h; S.histAt = t; }).catch(() => {}) : null,
    ]);
    if (S.page !== "dashboard" || !$("#dash-stats")) return;     // 기다리는 사이 다른 화면으로 옮겼다
    const g = o.gpu, h = o.host, q = o.queue;
    $("#dash-alerts").innerHTML = (o.alerts || []).map((a) => `<div class="callout bad">${icon("alert")}<div>${esc(a)}</div></div>`).join("");

    const vp = pct(g.used_mb, g.total_mb), mp = pct(h.mem_used, h.mem_total);
    const stat = (label, value, foot, p, cls) => `<div class="stat"><div class="label">${label}</div><div class="value">${value}</div>
      <div class="foot">${foot || "&nbsp;"}</div>${p == null ? "" : bar(p, cls)}</div>`;
    $("#dash-stats").innerHTML = [
      stat("GPU 사용률", g.available ? `${g.util ?? 0}<small>%</small>` : "—", g.available ? `메모리 대역폭 ${g.mem_util ?? 0}%` : esc(g.error), g.util || 0, "ok"),
      stat("VRAM", g.available ? `${gb(g.used_mb)}<small>/ ${gb(g.total_mb, 0)} GB</small>` : "—", `예약 가능 ${q.budget_gb} GB`, vp),
      stat("GPU 온도", g.temp != null ? `${Math.round(g.temp)}<small>°C</small>` : "—", g.power_w != null ? `전력 ${g.power_w} W / ${g.power_limit_w} W` : "", pct(g.temp || 0, 90)),
      stat("CPU", `${h.cpu_percent.toFixed(0)}<small>%</small>`, `${h.cores}스레드, 부하 ${h.load[0]}${h.cpu_temp ? `, ${Math.round(h.cpu_temp)}°C` : ""}`, h.cpu_percent),
      stat("메모리", `${(h.mem_used / GiB).toFixed(1)}<small>/ ${(h.mem_total / GiB).toFixed(0)} GB</small>`, `디스크 ${pct(h.disk_used, h.disk_total).toFixed(0)}% 사용`, mp),
      stat("작업 큐", `${q.running}<small>실행</small> ${q.queued}<small>대기</small>`, `활동 중인 사용자 ${o.users.filter((u) => u.active).length}명`, null),
    ].join("");

    Dashboard.alloc(o);
    Dashboard.sys(o);
    Dashboard.disk(o);
    Dashboard.charts();
    Dashboard.users();
    Dashboard.procs();
    Dashboard.sysProcs();
  },
  // CPU·메모리를 누가 쓰는지: 사용자별, 공유 LLM, 그 밖(호스트)으로 나눠 한 줄에 쌓는다
  sys(o) {
    const h = o.host, svc = o.services || { cpu_cores: 0, mem_bytes: 0 };
    $("#sys-name").textContent = `${h.cpu_model ? h.cpu_model + ", " : ""}${h.cores}스레드, 메모리 ${(h.mem_total / GiB).toFixed(1)} GB`;
    const usedCores = (h.cpu_percent * h.cores) / 100;
    const track = (total, parts, fmt) => {
      const w = (v) => `${Math.min(100, (v / total) * 100).toFixed(2)}%`;
      return `<div class="alloc-track">${parts.filter((x) => x.v > 0).map((x) => `<div class="alloc-seg ${x.cls || ""}" style="flex:0 0 ${w(x.v)};${x.color ? `background:${x.color}` : ""}" title="${esc(x.label)} ${fmt(x.v)}">${x.v / total > 0.1 && !x.quiet ? `<b>${esc(x.label)}</b>` : ""}</div>`).join("")}</div>`;
    };
    const legend = (parts, fmt, free) => `<div class="legend">${parts.filter((x) => x.v > 0).map((x) => `<span><i ${x.cls === "cache" ? 'class="hatch"' : `style="background:${x.color || (x.cls === "llm" ? "var(--llm)" : "#7d8899")}"`}></i>${esc(x.label)} ${fmt(x.v)}</span>`).join("")}
      <span><i style="background:var(--free);box-shadow:inset 0 0 0 1px var(--border)"></i>비어 있음 ${fmt(free)}</span></div>`;
    const users = [...o.users].sort((a, b) => a.name.localeCompare(b.name));

    const cpuParts = users.filter((u) => u.cpu_cores >= 0.05).map((u) => ({ label: u.name, v: u.cpu_cores, color: userColor(u.name) }));
    if (svc.cpu_cores >= 0.05) cpuParts.push({ label: "공유 LLM", v: svc.cpu_cores, cls: "llm" });
    const cpuOther = Math.max(0, usedCores - cpuParts.reduce((a, x) => a + x.v, 0));
    if (cpuOther >= 0.05) cpuParts.push({ label: "호스트·기타", v: cpuOther, cls: "other" });
    const fc = (v) => `${v.toFixed(1)}코어`;

    const memParts = users.filter((u) => u.mem_bytes >= 0.05 * GiB).map((u) => ({ label: u.name, v: u.mem_bytes, color: userColor(u.name) }));
    if (svc.mem_bytes >= 0.05 * GiB) memParts.push({ label: "공유 LLM", v: svc.mem_bytes, cls: "llm" });
    const memOther = Math.max(0, h.mem_used - memParts.reduce((a, x) => a + x.v, 0));
    if (memOther >= 0.05 * GiB) memParts.push({ label: "호스트·기타", v: memOther, cls: "other" });
    const cached = Math.min(h.mem_cached || 0, h.mem_total - h.mem_used);
    if (cached >= 0.05 * GiB) memParts.push({ label: "파일 캐시 (필요하면 비워짐)", v: cached, cls: "cache", quiet: true });
    const fm = (v) => `${(v / GiB).toFixed(1)} GB`;

    // 논리 코어별 사용률. P/E 코어가 섞인 CPU 는 종류별로 줄을 나눈다
    const per = h.per_cpu || [];
    const split = h.p_threads && h.p_threads < per.length ? [["P코어", 0, h.p_threads], ["E코어", h.p_threads, per.length]] : [["", 0, per.length]];
    const cores = split.map(([label, a, b]) => `<div class="cores-row">${label ? `<span class="cores-label">${label}</span>` : ""}
      <div class="cores" style="grid-template-columns:repeat(${Math.min(16, b - a)},1fr)">${per.slice(a, b).map((v, i) => `<div class="core ${v >= 90 ? "hot" : ""}" title="논리 코어 ${h.cpu_ids ? h.cpu_ids[a + i] : a + i}: ${v}%"><i style="height:${v}%"></i></div>`).join("")}</div></div>`).join("");

    $("#dash-sys").innerHTML = `<div class="sys-grid">
      <div class="sys-block">
        <div class="alloc-top"><div class="big">${usedCores.toFixed(1)}<small> / ${h.cores} 코어 사용 중</small></div>
          <div class="note">부하 평균 ${h.load[0]}${h.cpu_temp ? `, ${Math.round(h.cpu_temp)}°C` : ""}</div></div>
        ${track(h.cores, cpuParts, fc)}${legend(cpuParts, fc, Math.max(0, h.cores - usedCores))}
        <div class="cores-wrap"><div class="cores-title">논리 코어별 사용률${(h.cpu_offline || []).length ? ` <span class="muted">(꺼 둔 코어 ${h.cpu_offline.join(", ")}번은 빠짐)</span>` : ""}</div>${cores}</div>
      </div>
      <div class="sys-block">
        <div class="alloc-top"><div class="big">${(h.mem_used / GiB).toFixed(1)}<small> / ${(h.mem_total / GiB).toFixed(1)} GB 사용 중</small></div>
          <div class="note">${h.swap_total ? `스왑 ${(h.swap_used / GiB).toFixed(1)} / ${(h.swap_total / GiB).toFixed(0)} GB` : "스왑 없음"}</div></div>
        ${track(h.mem_total, memParts, fm)}${legend(memParts, fm, Math.max(0, h.mem_total - h.mem_used - cached))}
      </div></div>`;
  },
  // 저장공간을 누가·무엇이 차지하는지: 사용자 홈과 스크래치, 공용 모델·데이터, 그 밖(운영체제·Docker 등)을 한 줄에 쌓는다.
  // 서버가 5분마다 폴더를 훑어 잰 값이라 GPU·메모리처럼 바로바로 바뀌지는 않는다.
  disk(o) {
    const st = o.storage, box = $("#dash-disk");
    if (!box) return;
    if (!st) {
      $("#disk-name").textContent = "";
      const h = o.host;
      box.innerHTML = `<div class="alloc-top"><div class="big">${fmtBytes(h.disk_used)}<small> / ${fmtBytes(h.disk_total)} 사용 중</small></div>
        <div class="note">누가 얼마나 쓰는지는 재는 중입니다. 서버를 켠 뒤 처음 몇 분이 걸리고, 그 뒤로 5분마다 다시 잽니다.</div></div>`;
      return;
    }
    const ago5 = Math.max(0, Math.round((now() - st.scanned_at) / 60));
    $("#disk-name").textContent = `${ago5 ? `${ago5}분 전에 잰 값` : "방금 잰 값"}, 5분마다 다시 잽니다`;
    const blocks = st.disks.map((d) => {
      // 같은 사용자의 홈과 스크래치는 한 칸으로 합친다 (색이 사용자를 뜻한다)
      const byUser = {};
      const parts = [];
      for (const x of d.parts) {
        if (x.user) {
          if (!byUser[x.user]) { byUser[x.user] = { label: x.user, v: 0, color: userColor(x.user), home: 0, scratch: 0 }; parts.push(byUser[x.user]); }
          byUser[x.user].v += x.bytes;
          byUser[x.user][x.kind] = x.bytes;
        } else parts.push({ label: x.label, v: x.bytes, cls: "llm" });
      }
      parts.sort((a, b) => (a.cls ? 1 : 0) - (b.cls ? 1 : 0) || b.v - a.v);
      if (d.other > 0) parts.push({ label: "그 밖 (운영체제, Docker, 로그 등)", v: d.other, cls: "other" });
      const w = (v) => `${Math.min(100, (v / d.total) * 100).toFixed(2)}%`;
      const track = `<div class="alloc-track">${parts.filter((x) => x.v > 0).map((x) => `<div class="alloc-seg ${x.cls || ""}" style="flex:0 0 ${w(x.v)};${x.color ? `background:${x.color}` : ""}"
          title="${esc(x.label)} ${fmtBytes(x.v)}${x.scratch ? ` (홈 ${fmtBytes(x.home)}, 스크래치 ${fmtBytes(x.scratch)})` : ""}">${x.v / d.total > 0.12 ? `<b>${esc(x.label)}</b><span>${fmtBytes(x.v)}</span>` : ""}</div>`).join("")}</div>`;
      const legend = `<div class="legend">${parts.filter((x) => x.v > 0).map((x) => `<span><i style="background:${x.color || (x.cls === "llm" ? "var(--llm)" : "#7d8899")}"></i>${esc(x.label)} ${fmtBytes(x.v)}</span>`).join("")}
        <span><i style="background:var(--free);box-shadow:inset 0 0 0 1px var(--border)"></i>비어 있음 ${fmtBytes(d.free)}</span></div>`;
      const free = pct(d.free, d.total);
      return `<div class="disk-block">
        <div class="alloc-top"><div class="big">${fmtBytes(d.used)}<small> / ${fmtBytes(d.total)} 사용 중</small></div>
          <div class="note">${esc(d.holds.join(", "))} 디스크 (${esc(d.path)}).
            ${free < 10 ? `<span style="color:var(--bad)">남은 공간이 ${free.toFixed(0)}%입니다.</span>` : `남은 공간 ${fmtBytes(d.free)} (${free.toFixed(0)}%).`}</div></div>
        ${track}${legend}</div>`;
    }).join("");
    // 사용자마다 홈 할당량 대비 쓴 양, 공용 모델 가운데 큰 것
    const users = [...st.users].sort((a, b) => b.home + b.scratch - (a.home + a.scratch));
    const userRows = users.map((u) => `<div class="disk-row"><span class="user-cell">${avatar(u.name)}${esc(u.name)}</span>
        ${bar(pct(u.home, u.quota))}<span class="disk-num">${fmtBytes(u.home)} / ${fmtBytes(u.quota)}${u.scratch ? `<em>스크래치 ${fmtBytes(u.scratch)}</em>` : ""}</span></div>`).join("");
    const top = st.models[0]?.bytes || 1;
    const modelRows = st.models.map((m) => `<div class="disk-row"><span class="mono disk-model" title="${esc(m.name)}">${esc(m.name)}</span>
        ${bar((m.bytes / top) * 100, "plain")}<span class="disk-num">${fmtBytes(m.bytes)}</span></div>`).join("");
    box.innerHTML = `${blocks}
      <div class="sys-grid disk-lists">
        <div><div class="cores-title">사용자 홈 <span class="muted">(할당량 대비)</span></div>${userRows || '<div class="muted">사용자가 없습니다</div>'}</div>
        <div><div class="cores-title">공용 모델 <span class="muted">${st.models_count}개, 모두 ${fmtBytes(st.models_total)}${st.models_count > st.models.length ? `, 큰 것 ${st.models.length}개만 보임` : ""}</span></div>
          ${modelRows || '<div class="muted">공용 모델 폴더(models/hub, models/llm)가 비어 있습니다</div>'}</div>
      </div>`;
  },
  sysProcs() {
    const o = S.overview, t = $("#dash-sysprocs"), s = S.procSort;
    const rows = sortRows(o.procs || [], ({ cpu: "cpu_cores", mem: "mem_bytes", name: "name", pid: "pid" })[s.key] || "cpu_cores", s.dir);
    if (!rows.length) { t.innerHTML = `<tbody><tr><td><div class="empty"><b>표시할 프로세스가 없습니다</b>잠시 뒤 다시 채워집니다.</div></td></tr></tbody>`; return; }
    const src = (p) => p.kind === "job" ? `작업 #${esc(p.job_id)}` : p.kind === "workspace" ? "워크스페이스" : p.kind === "service" ? `공유 LLM (${esc(p.model || "")})` : '<span class="muted">호스트</span>';
    t.innerHTML = `<thead><tr>${th("PID", "pid", s)}${th("프로세스", "name", s)}<th>사용자</th><th>출처</th>${th("CPU", "cpu", s)}${th("메모리", "mem", s)}</tr></thead><tbody>
      ${rows.map((p) => `<tr class="${p.kind === "job" ? "click" : ""}" ${p.kind === "job" ? `data-job="${p.job_id}"` : ""}>
        <td class="mono">${p.pid}</td><td class="mono">${esc(p.name)}</td>
        <td>${p.user ? userCell(p.user) : p.kind === "service" ? '<span class="muted">모두</span>' : '<span class="muted">—</span>'}</td><td>${src(p)}</td>
        <td><span class="cell-bar">${bar(pct(p.cpu_cores, o.host.cores), "")}${p.cpu_cores.toFixed(2)}코어</span></td>
        <td><span class="cell-bar">${bar(pct(p.mem_bytes, o.host.mem_total), "")}${fmtBytes(p.mem_bytes)}</span></td></tr>`).join("")}</tbody>`;
    bindSort(t, s, () => Dashboard.sysProcs());
    t.querySelectorAll("tr[data-job]").forEach((r) => (r.onclick = () => go("jobs", r.dataset.job)));
  },
  alloc(o) {
    const g = o.gpu, q = o.queue;
    $("#alloc-name").textContent = g.available ? g.name : "";
    if (!g.available) { $("#dash-alloc").innerHTML = `<div class="callout bad">${icon("alert")}<div>${esc(g.error || "GPU를 찾지 못했습니다")}</div></div>`; return; }
    const total = g.total_mb;
    const byUser = {};
    let attributed = 0;
    let llmMb = 0, llmModel = "";
    for (const p of g.processes) {
      if (p.user) { byUser[p.user] = (byUser[p.user] || 0) + p.used_mb; attributed += p.used_mb; }
      else if (p.kind === "service") { llmMb += p.used_mb; attributed += p.used_mb; llmModel = p.model || ""; }
    }
    const other = Math.max(0, g.used_mb - attributed);
    // 실행 중인 작업이 예약해 두고 아직 쓰지 않은 VRAM. 큐는 이만큼을 이미 내준 것으로 계산하므로,
    // 이걸 보여 주지 않으면 "비어 있는데 왜 못 쓰지?"가 된다.
    const usedByJob = {};
    for (const p of g.processes) if (p.kind === "job" && p.job_id != null) usedByJob[String(p.job_id)] = (usedByJob[String(p.job_id)] || 0) + p.used_mb;
    let room = Math.max(0, total - g.used_mb);
    const held = (S.jobsAll || []).filter((j) => j.status === "running")
      .map((j) => ({ label: `${j.user} 작업 #${j.id}`, color: userColor(j.user), mb: j.vram_gb * 1024 - (usedByJob[String(j.id)] || 0) }));
    if (o.llm && o.llm.state === "loading" && o.llm.model) held.push({ label: "LLM 올리는 중", color: "var(--llm)", mb: o.llm.model.vram_gb * 1024 - llmMb });
    for (const x of held) { x.mb = Math.min(Math.max(0, x.mb), room); room -= x.mb; }
    const heldSegs = held.filter((x) => x.mb >= 100), heldMb = heldSegs.reduce((a, x) => a + x.mb, 0);
    const reserve = Math.min(q.reserve_gb * 1024, Math.max(0, total - g.used_mb - heldMb));
    const segs = Object.entries(byUser).sort((a, b) => b[1] - a[1]).map(([u, mb]) => ({ u, mb }));
    const w = (mb) => `${((mb / total) * 100).toFixed(2)}%`;
    const queued = (S.jobsAll || []).filter((j) => j.status === "queued");
    const demand = queued.reduce((s, j) => s + j.vram_gb, 0);
    const steps = [0, 0.25, 0.5, 0.75, 1];
    $("#dash-alloc").innerHTML = `
      <div class="alloc-top">
        <div class="big">${gb(g.used_mb)}<small> / ${gb(total, 0)} GB 사용 중</small></div>
        <div class="note">지금 새 작업에 줄 수 있는 VRAM은 <b>${q.budget_gb} GB</b>입니다.</div>
        <div class="demand">${queued.length ? `대기 중인 작업 ${queued.length}건이 <b>${demand.toFixed(0)} GB</b>를 기다립니다` : "대기 중인 작업이 없습니다"}</div>
      </div>
      <div class="alloc">
        <div class="alloc-track">
          ${segs.map((s) => `<div class="alloc-seg" style="flex:0 0 ${w(s.mb)};background:${userColor(s.u)}" title="${esc(s.u)} ${gb(s.mb)} GB">
            ${s.mb / total > 0.08 ? `<b>${esc(s.u)}</b><span>${gb(s.mb)} GB</span>` : ""}</div>`).join("")}
          ${llmMb > 0 ? `<div class="alloc-seg llm" style="flex:0 0 ${w(llmMb)}" title="공유 LLM ${esc(llmModel)} ${gb(llmMb)} GB">
            ${llmMb / total > 0.12 ? `<b>LLM ${esc(llmModel)}</b><span>${gb(llmMb)} GB, 모두가 공유</span>` : ""}</div>` : ""}
          ${other > 0 ? `<div class="alloc-seg other" style="flex:0 0 ${w(other)}" title="드라이버와 ironlab 밖 프로세스 ${gb(other)} GB"></div>` : ""}
          ${heldSegs.map((x) => `<div class="alloc-seg held" style="flex:0 0 ${w(x.mb)};--c:${x.color}" title="${esc(x.label)}: 예약했지만 아직 쓰지 않은 ${gb(x.mb)} GB">
            ${x.mb / total > 0.12 ? `<b>${esc(x.label)}</b><span>예약 ${gb(x.mb)} GB</span>` : ""}</div>`).join("")}
          ${reserve > 0 ? `<div class="alloc-seg reserve" style="flex:0 0 ${w(reserve)}" title="안전 여유 ${gb(reserve)} GB"></div>` : ""}
        </div>
        <div class="alloc-demand">${queued.slice(0, 12).map((j) => `<div style="flex:0 0 calc(${Math.min(100, (j.vram_gb * 1024 / total) * 100)}% - 3px)" title="작업 #${j.id} ${esc(j.user)} ${j.vram_gb} GB 대기"></div>`).join("")}</div>
        <div class="alloc-scale">${steps.map((f) => `<span style="left:${f * 100}%">${Math.round((total / 1024) * f)} GB</span>`).join("")}</div>
      </div>
      <div class="legend">
        ${segs.map((s) => `<span><i style="background:${userColor(s.u)}"></i>${esc(s.u)} ${gb(s.mb)} GB</span>`).join("")}
        ${llmMb > 0 ? `<span><i style="background:var(--llm)"></i>공유 LLM ${gb(llmMb)} GB</span>` : ""}
        ${other > 0 ? `<span><i style="background:#7d8899"></i>드라이버·외부 ${gb(other)} GB</span>` : ""}
        <span><i class="hatch"></i>안전 여유 ${gb(reserve)} GB</span>
        ${heldMb > 0 ? `<span><i class="held-swatch"></i>예약됨, 아직 안 씀 ${gb(heldMb)} GB</span>` : ""}
        <span><i style="background:var(--free);box-shadow:inset 0 0 0 1px var(--border)"></i>비어 있음 ${gb(Math.max(0, total - g.used_mb - heldMb - reserve))} GB</span>
        ${queued.length ? `<span><i class="dash"></i>대기 작업 요청량</span>` : ""}
      </div>`;
  },
  charts() {
    // 1분 구간은 촘촘한 최근 기록을, 그보다 길면 긴 기록을 쓴다.
    // 구간의 끝은 브라우저 시계가 아니라 마지막 점의 시각으로 잡는다 (서버와 시계가 달라도 맞게).
    const src = S.chartRange <= FAST_RANGE ? S.recent : S.history;
    const end = src.length ? src[src.length - 1].ts : now();
    const pts = src.filter((p) => p.ts >= end - S.chartRange);
    const ut = $("#chart-util"), vr = $("#chart-vram");
    if (!ut) return;
    lineChart(ut, pts, { max: 100, unit: "%", series: [{ label: "GPU", color: "var(--accent)", value: (p) => p.gpu_util }] });
    const names = [...new Set(pts.flatMap((p) => Object.keys(p.users || {})))].sort();
    const total = (S.overview?.gpu.total_mb || 24576) / 1024;
    const series = names.map((n) => ({ label: n, color: userColor(n), value: (p) => (p.users || {})[n] || 0 }));
    series.push({ label: "기타", color: "#7d8899", value: (p) => Math.max(0, p.vram_gb - Object.values(p.users || {}).reduce((a, b) => a + b, 0)) });
    lineChart(vr, pts, { max: Math.ceil(total), unit: "GB", series });

    const h = S.overview?.host || { cores: 1, mem_total: GiB };
    const stacked = (users, llm, all, llmLabel) => {
      const ns = [...new Set(pts.flatMap((p) => Object.keys(p[users] || {})))].sort();
      const sr = ns.map((n) => ({ label: n, color: userColor(n), value: (p) => (p[users] || {})[n] || 0 }));
      sr.push({ label: llmLabel, color: "var(--llm)", value: (p) => p[llm] || 0 });
      sr.push({ label: "호스트·기타", color: "#7d8899", value: (p) => Math.max(0, (p[all] || 0) - (p[llm] || 0) - Object.values(p[users] || {}).reduce((a, b) => a + b, 0)) });
      return sr;
    };
    lineChart($("#chart-cpu"), pts, { max: h.cores, unit: "코어", series: stacked("cpu_users", "cpu_llm", "cpu_cores", "공유 LLM") });
    lineChart($("#chart-mem"), pts, { max: Math.ceil(h.mem_total / GiB), unit: "GB", series: stacked("mem_users", "mem_llm", "mem_gb", "공유 LLM") });
  },
  users() {
    const rows = sortRows(S.overview.users, ({
      active: (u) => (u.active ? 1 : 0) * 1e6 + u.vram_mb, name: "name", cpu: "cpu_cores", mem: "mem_bytes", vram: "vram_mb",
      disk: "disk_used", seen: "last_seen", jobs: "jobs_running", ssh: "ssh_sessions",
    })[S.userSort.key] || "name", S.userSort.dir);
    $("#act-sub").textContent = `${S.overview.users.filter((u) => u.active).length}명 활동 중, 전체 ${S.overview.users.length}명`;
    const t = $("#dash-users"), s = S.userSort;
    t.innerHTML = `<thead><tr>${th("사용자", "name", s)}${th("상태", "active", s)}<th>워크스페이스</th>${th("SSH", "ssh", s, "r")}${th("CPU 코어", "cpu", s)}${th("메모리", "mem", s)}${th("VRAM", "vram", s, "r")}${th("실행 작업", "jobs", s, "r")}${th("홈 디스크", "disk", s)}${th("최근 접속", "seen", s)}</tr></thead>
      <tbody>${rows.map((u) => {
        const ws = u.workspace && u.workspace.state === "running";
        const st = u.active ? `<span class="badge ok">활동 중</span>` : ws ? `<span class="badge warn">유휴</span>` : `<span class="badge">오프라인</span>`;
        return `<tr class="${S.me.role === "admin" ? "click" : ""}" data-user="${esc(u.name)}">
          <td>${userCell(u.name)}${u.role === "admin" ? ' <span class="badge plain">관리자</span>' : ""}</td>
          <td>${st}</td>
          <td>${ws ? `실행 ${dur(now() - u.workspace.started_at)}${u.idle_since ? `<span class="sub">유휴 ${dur(now() - u.idle_since)}</span>` : ""}` : '<span class="muted">꺼짐</span>'}</td>
          <td class="r">${u.ssh_sessions || '<span class="muted">0</span>'}</td>
          <td><span class="cell-bar">${bar(pct(u.cpu_cores, u.cpu_limit))}${u.cpu_cores.toFixed(1)} / ${u.cpu_limit}</span></td>
          <td><span class="cell-bar">${bar(pct(u.mem_bytes, u.mem_limit))}${fmtBytes(u.mem_bytes)}</span></td>
          <td class="r">${u.vram_mb ? gb(u.vram_mb) + " GB" : '<span class="muted">—</span>'}</td>
          <td class="r">${u.jobs_running || '<span class="muted">0</span>'}</td>
          <td><span class="cell-bar">${bar(pct(u.disk_used, u.disk_quota))}${fmtBytes(u.disk_used)}</span></td>
          <td class="muted">${ago(u.last_seen)}</td></tr>`;
      }).join("")}</tbody>`;
    bindSort(t, S.userSort, () => Dashboard.users());
    if (S.me.role === "admin") t.querySelectorAll("tr[data-user]").forEach((r) => (r.onclick = () => openUser(r.dataset.user)));
  },
  procs() {
    const procs = [...S.overview.gpu.processes].sort((a, b) => b.used_mb - a.used_mb);
    $("#dash-procs").innerHTML = procs.length ? `<thead><tr><th>PID</th><th>사용자</th><th>출처</th><th class="r">VRAM</th><th>VRAM 비중</th></tr></thead><tbody>
      ${procs.map((p) => `<tr class="${p.kind === "job" ? "click" : ""}" ${p.kind === "job" ? `data-job="${p.job_id}"` : ""}>
        <td class="mono">${p.pid}</td><td>${p.user ? userCell(p.user) : p.kind === "service" ? '<span class="muted">모두</span>' : '<span class="muted">호스트</span>'}</td>
        <td>${p.kind === "job" ? `작업 #${esc(p.job_id)}` : p.kind === "workspace" ? "워크스페이스" : p.kind === "service" ? `공유 LLM (${esc(p.model || "")})` : '<span class="badge warn">ironlab 외부</span>'}</td>
        <td class="r">${gb(p.used_mb)} GB</td><td><span class="cell-bar">${bar(pct(p.used_mb, S.overview.gpu.total_mb), "")}</span></td></tr>`).join("")}</tbody>`
      : `<tbody><tr><td><div class="empty"><b>GPU를 쓰는 프로세스가 없습니다</b>작업을 제출하거나 워크스페이스에서 모델을 올리면 여기에 나타납니다.</div></td></tr></tbody>`;
    $$("#dash-procs tr[data-job]").forEach((r) => (r.onclick = () => go("jobs", r.dataset.job)));
  },
};
const STALE_AFTER_S = 30;   // 수집 주기(10초)의 세 배가 지나도록 새 수치가 없으면 멈춘 것으로 본다
const FAST_RANGE = 60;      // 이 구간 이하는 서버의 촘촘한 최근 기록(res=fast)을 쓴다
const rangeBtns = () => [[60, "1분"], [900, "15분"], [3600, "1시간"]].map(([v, l]) => `<button data-r="${v}" class="${S.chartRange === v ? "on" : ""}">${l}</button>`).join("");

// ═════════════════════════ 페이지: 워크스페이스 ═════════════════════════
async function wsAction(kind) {
  if (S.wsBusy) return;
  const running = S.ws?.state === "running";
  // 실행 중일 때만 되묻는다. 이미 꺼진 컨테이너를 치우는 데는 잃을 것이 없다.
  if (kind === "stop" && running && !(await confirmDialog("워크스페이스를 중지할까요? 컨테이너는 지워지지만 홈과 스크래치 파일은 그대로 남습니다.", { ok: "중지", danger: true }))) return;
  S.wsBusy = kind;
  wsButtons();
  try {
    await api(`/api/workspace/${kind}`, { method: "POST" });
    toast(kind === "start" ? "워크스페이스를 시작했습니다" : "워크스페이스를 중지했습니다");
  } catch (e) { fail(e); }
  S.wsBusy = null;
  if (S.page === "workspace") { try { await Workspace.refresh(); } catch (e) { fail(e); } }
}

// 시작·중지 버튼 상태. 요청이 진행 중이면 둘 다 잠그고 무엇을 하는 중인지 보여 준다.
function wsButtons() {
  const start = $("#ws-start"), stop = $("#ws-stop");
  if (!start || !stop) return;
  const state = S.ws?.state || "stopped";
  start.disabled = !!S.wsBusy || state === "running";
  stop.disabled = !!S.wsBusy || state === "stopped";      // 꺼진 채 남은 컨테이너도 치울 수 있게 한다
  start.querySelector("span").textContent = S.wsBusy === "start" ? "시작하는 중…" : "시작";
  stop.querySelector("span").textContent = S.wsBusy === "stop" ? "중지하는 중…" : "중지";
}

const Workspace = {
  render() {
    $("#page").innerHTML = pageHead("내 워크스페이스", "GPU가 연결된 개인 컨테이너입니다. 중지하면 컨테이너는 지워지고, 홈과 스크래치의 파일은 남습니다.",
      `<button class="btn" id="ws-stop">${icon("stop")}<span>중지</span></button><button class="btn primary" id="ws-start">${icon("play")}<span>시작</span></button>`) + `
      <div id="ws-callout"></div>
      <div class="grid-2" style="margin-bottom:16px">
        <section class="panel"><div class="panel-head"><h2>상태</h2></div><div class="panel-body"><dl class="dl" id="ws-status"></dl></div></section>
        <section class="panel"><div class="panel-head"><h2>지금 사용량</h2><span class="sub">할당량 대비</span></div><div class="panel-body" id="ws-usage"></div></section>
      </div>
      <section class="panel"><div class="panel-head"><h2>접속</h2></div><div class="panel-body stack-16" id="ws-connect"></div></section>
      <section class="panel flush"><div class="panel-head"><h2>마운트</h2><span class="sub">컨테이너 안에서 보이는 경로</span></div>
        <div class="panel-body table-wrap"><table class="dt"><thead><tr><th>경로</th><th>내용</th><th>권한</th><th>유지</th></tr></thead><tbody>
          <tr><td class="mono">/home/dev</td><td>개인 홈. conda 환경과 코드는 여기에 두세요</td><td><span class="badge info plain">읽기·쓰기</span></td><td>영구, 할당량 적용</td></tr>
          <tr><td class="mono">/scratch</td><td>대용량 임시 출력</td><td><span class="badge info plain">읽기·쓰기</span></td><td>정리될 수 있음</td></tr>
          <tr><td class="mono">/shared/models</td><td>공용 모델 가중치 (HF 캐시)</td><td><span class="badge plain">읽기 전용</span></td><td>관리자가 관리</td></tr>
          <tr><td class="mono">/shared/datasets</td><td>공용 데이터셋</td><td><span class="badge plain">읽기 전용</span></td><td>관리자가 관리</td></tr>
        </tbody></table></div></section>`;
    $("#ws-start").onclick = () => wsAction("start");
    $("#ws-stop").onclick = () => wsAction("stop");
  },
  async refresh() {
    const w = await api("/api/workspace");
    S.ws = w;
    const on = w.state === "running";
    const leftover = !on && w.state !== "stopped";      // exited, created, dead 등: 컨테이너는 남아 있지만 꺼진 상태
    wsButtons();
    $("#ws-callout").innerHTML =
      (w.has_ssh_key ? "" : `<div class="callout warn">${icon("key")}<div>SSH 공개키가 아직 없습니다. 키를 등록해야 접속할 수 있습니다. <a href="#/account">키 등록하기</a></div></div>`)
      + (leftover ? `<div class="callout warn">${icon("info")}<div>워크스페이스가 꺼져 있습니다. 서버나 Docker가 다시 시작되면 이렇게 됩니다. 홈과 스크래치 파일은 그대로입니다. 시작을 누르면 새로 만들어집니다.</div></div>` : "");
    $("#ws-status").innerHTML = `
      <dt>상태</dt><dd>${on ? '<span class="badge ok run">실행 중</span>' : `<span class="badge${leftover ? " warn" : ""}">${w.state === "stopped" ? "중지됨" : `꺼짐 (${esc(w.state)})`}</span>`}</dd>
      <dt>실행 시간</dt><dd>${on ? dur(now() - w.started_at) : "—"}</dd>
      <dt>컨테이너</dt><dd class="mono">ironlab-ws-${esc(S.me.name)}</dd>
      <dt>CPU 할당</dt><dd>${w.limits.cpus}코어</dd>
      <dt>메모리 할당</dt><dd>${w.limits.mem_gb} GB</dd>
      <dt>GPU</dt><dd>${esc(S.overview?.gpu.name || "RTX 3090")}, 다른 사용자와 공유</dd>`;
    const u = S.overview?.users.find((x) => x.name === S.me.name);
    $("#ws-usage").innerHTML = u ? [
      ["CPU", `${u.cpu_cores.toFixed(1)} / ${u.cpu_limit} 코어`, pct(u.cpu_cores, u.cpu_limit)],
      ["메모리", `${fmtBytes(u.mem_bytes)} / ${fmtBytes(u.mem_limit)}`, pct(u.mem_bytes, u.mem_limit)],
      ["VRAM", `${gb(u.vram_mb)} GB (GPU 전체 ${gb(S.overview.gpu.total_mb, 0)} GB 중)`, pct(u.vram_mb, S.overview.gpu.total_mb)],
      ["홈 디스크", `${fmtBytes(u.disk_used)} / ${fmtBytes(u.disk_quota)}`, pct(u.disk_used, u.disk_quota)],
    ].map(([l, v, p]) => `<div style="margin-bottom:14px"><div style="display:flex;justify-content:space-between"><span>${l}</span><span class="muted">${v}</span></div>${bar(p)}</div>`).join("")
      + `<div class="muted" style="font-size:12.5px">SSH 세션 ${u.ssh_sessions}개 열림</div>` : "";
    const host = S.me.public_host || "서버주소";
    const cfg = `Host ironlab\n  HostName ${host}\n  Port ${w.ssh_port}\n  User dev`;
    $("#ws-connect").innerHTML = `
      <div class="field"><label>터미널</label><div class="copy"><code>${esc(w.ssh_command)}</code><button class="btn sm" data-copy="${esc(w.ssh_command)}">복사</button></div></div>
      <div class="field"><label>VS Code Remote-SSH</label><span class="hint">~/.ssh/config 에 붙여 넣고 원격 탐색기에서 ironlab 을 고르세요.</span>
        <div class="copy"><code style="white-space:pre">${esc(cfg)}</code><button class="btn sm" data-copy="${esc(cfg)}">복사</button></div></div>
      <div class="callout info">${icon("info")}<div>워크스페이스 안에서 GPU를 오래 쓰면 다른 사람의 작업이 기다려야 합니다. 무거운 생성과 학습은 <a href="#/jobs">GPU 작업</a>으로 큐에 넣어 주세요.</div></div>`;
  },
};

// ═════════════════════════ 페이지: GPU 작업 ═════════════════════════
const STATUS = {
  queued: ["대기", "warn"], running: ["실행 중", "ok run"], succeeded: ["성공", "info"], failed: ["실패", "bad"], cancelled: ["취소됨", ""],
};
const statusBadge = (j) => {
  const [l, c] = STATUS[j.status] || [j.status, ""];
  return `<span class="badge ${c}">${l}${j.status === "failed" && j.exit_code != null ? ` (${j.exit_code})` : ""}</span>`;
};

const Jobs = {
  render() {
    $("#page").innerHTML = pageHead("GPU 작업", "필요한 VRAM을 적어 큐에 넣으면, GPU에 자리가 나는 대로 순서대로 실행됩니다.",
      `<button class="btn primary" id="jobs-submit">${icon("plus")}작업 제출</button>`) + `
      <section class="panel flush">
        <div class="toolbar">
          <div class="search">${icon("search")}<input class="input" id="job-q" placeholder="이름, 명령, 사용자, #ID 검색" value="${esc(S.jobQuery)}"></div>
          <div class="chips" id="job-chips"></div>
          <div class="right"><div class="seg" id="job-scope">
            <button data-s="mine" class="${S.jobScope === "mine" ? "on" : ""}">내 작업</button><button data-s="all" class="${S.jobScope === "all" ? "on" : ""}">모든 사용자</button></div></div>
        </div>
        <div class="table-wrap"><table class="dt" id="jobs-table"></table></div>
      </section>`;
    $("#jobs-submit").onclick = () => openSubmit();
    $("#job-q").oninput = (e) => { S.jobQuery = e.target.value; Jobs.table(); };
    $("#job-scope").onclick = (e) => {
      const b = e.target.closest("[data-s]"); if (!b) return;
      S.jobScope = b.dataset.s; store.set("ironlab_job_scope", S.jobScope);
      $$("#job-scope button").forEach((x) => x.classList.toggle("on", x === b));
      refresh();
    };
    $("#job-chips").onclick = (e) => { const c = e.target.closest("[data-st]"); if (c) { S.jobStatus = c.dataset.st; Jobs.table(); } };
  },
  onParam(id) { if (id) openJob(Number(id)); },
  async refresh() {
    const [jobs, all] = await Promise.all([
      api(`/api/jobs?scope=${S.jobScope}&limit=300`),
      S.jobScope === "all" ? null : api("/api/jobs?scope=all&limit=300"),
    ]);
    S.jobs = jobs;
    S.jobsAll = all || jobs;
    Jobs.table();
  },
  table() {
    const counts = { all: S.jobs.length };
    S.jobs.forEach((j) => (counts[j.status] = (counts[j.status] || 0) + 1));
    $("#job-chips").innerHTML = [["all", "전체"], ["running", "실행 중"], ["queued", "대기"], ["succeeded", "성공"], ["failed", "실패"], ["cancelled", "취소됨"]]
      .map(([k, l]) => `<button class="chip ${S.jobStatus === k ? "on" : ""}" data-st="${k}">${l}<span class="n">${counts[k] || 0}</span></button>`).join("");
    const q = S.jobQuery.trim().toLowerCase();
    let rows = S.jobs.filter((j) => (S.jobStatus === "all" || j.status === S.jobStatus) &&
      (!q || `#${j.id} ${j.name} ${j.command || ""} ${j.user}`.toLowerCase().includes(q)));
    const t = $("#jobs-table"), s = S.jobSort;
    const waitOrRun = (j) => (j.status === "queued" ? now() - j.created_at : j.started_at ? (j.finished_at || now()) - j.started_at : null);
    rows = sortRows(rows, { id: "id", name: "name", user: "user", status: "status", vram: "vram_gb", time: waitOrRun, created: "created_at" }[s.key] || "id", s.dir);
    if (!S.jobs.length) {
      t.innerHTML = `<tbody><tr><td><div class="empty"><b>${S.jobScope === "mine" ? "아직 제출한 작업이 없습니다" : "작업이 없습니다"}</b>명령과 필요한 VRAM만 적으면 큐에 들어갑니다.<br>
        <button class="btn primary" onclick="openSubmit()">${icon("plus")}첫 작업 제출</button></div></td></tr></tbody>`;
      return;
    }
    t.innerHTML = `<thead><tr>${th("ID", "id", s)}${th("작업", "name", s)}${th("사용자", "user", s)}${th("상태", "status", s)}${th("VRAM", "vram", s, "r")}${th("대기·실행 시간", "time", s, "r")}${th("제출", "created", s)}</tr></thead>
      <tbody>${rows.map((j) => `<tr class="click ${S.drawer?.jobId === j.id ? "sel" : ""}" data-id="${j.id}">
        <td class="mono">#${j.id}</td>
        <td class="wrap"><b style="font-weight:500">${esc(j.name)}</b>
          ${j.wait_reason ? `<span class="sub reason">${esc(j.wait_reason)}</span>` : j.error ? `<span class="sub" style="color:var(--bad)">${esc(j.error)}</span>` : j.command != null ? `<span class="sub cmd">${esc(j.command)}</span>` : ""}</td>
        <td>${userCell(j.user)}</td>
        <td>${statusBadge(j)}</td>
        <td class="r">${j.exclusive ? "독점" : j.vram_gb + " GB"}${j.peak_vram_gb ? `<span class="sub">최대 ${j.peak_vram_gb} GB</span>` : ""}</td>
        <td class="r">${j.status === "queued" ? `<span class="reason">${dur(now() - j.created_at)}</span>` : dur(waitOrRun(j))}</td>
        <td class="muted">${ago(j.created_at)}</td></tr>`).join("") ||
        `<tr><td colspan="7"><div class="empty"><b>조건에 맞는 작업이 없습니다</b>검색어나 상태 필터를 바꿔 보세요.</div></td></tr>`}</tbody>`;
    bindSort(t, S.jobSort, () => Jobs.table());
    t.querySelectorAll("tr[data-id]").forEach((r) => (r.onclick = () => go("jobs", r.dataset.id)));
  },
};

async function openJob(id) {
  let j;
  try { j = await api(`/api/jobs/${id}`); } catch (e) { fail(e); go("jobs"); return; }
  const mine = j.user === S.me.name || S.me.role === "admin";
  renderCrumbs(`#${id}`);
  let follow = true, logTimer = null;
  const body = () => {
    const tl = [
      ["제출", j.created_at, "done"],
      ["시작", j.started_at, j.started_at ? "done" : j.status === "queued" ? "wait" : ""],
      [{ succeeded: "완료", failed: "실패", cancelled: "취소" }[j.status] || "종료", j.finished_at, j.finished_at ? (j.status === "succeeded" ? "done" : "fail") : j.status === "running" ? "now" : ""],
    ];
    return `
      ${j.wait_reason ? `<div class="callout warn">${icon("info")}<div><b>대기 이유</b><br>${esc(j.wait_reason)}</div></div>` : ""}
      ${j.error ? `<div class="callout bad">${icon("alert")}<div>${esc(j.error)}</div></div>` : ""}
      <dl class="dl">
        <dt>상태</dt><dd>${statusBadge(j)}</dd>
        <dt>사용자</dt><dd>${userCell(j.user)}</dd>
        <dt>VRAM 요청</dt><dd>${j.exclusive ? "GPU 독점" : j.vram_gb + " GB"}</dd>
        <dt>VRAM 최대 사용</dt><dd>${j.peak_vram_gb ? j.peak_vram_gb + " GB" : "—"}${j.peak_vram_gb > j.vram_gb * 1.1 + 0.5 ? ' <span class="badge bad">요청 초과</span>' : ""}</dd>
        <dt>CPU, 메모리</dt><dd>${j.cpus}코어, ${j.mem_gb} GB</dd>
        <dt>작업 폴더</dt><dd class="mono">${j.workdir == null ? "—" : j.workdir ? "/home/dev/" + esc(j.workdir) : "/home/dev"}</dd>
        <dt>종료 코드</dt><dd>${j.exit_code ?? "—"}</dd>
      </dl>
      ${j.command != null ? `<div class="section-title">명령</div><div class="copy"><code style="white-space:pre-wrap">${esc(j.command)}</code><button class="btn sm" data-copy="${esc(j.command)}">복사</button></div>` : ""}
      <div class="section-title">진행</div>
      <ul class="timeline">${tl.map(([l, ts, c]) => `<li class="${c === "wait" ? "now" : c}">${l}<small>${ts ? `${when(ts)} (${ago(ts)})` : c === "now" ? "진행 중" : c === "wait" ? `GPU 자리 대기 중, ${dur(now() - j.created_at)} 경과` : "—"}</small></li>`).join("")}</ul>`;
  };
  const logPane = () => `<div class="log-bar"><span id="log-state">${j.status === "running" ? "실시간으로 받는 중" : "저장된 로그"}</span>
    <div class="right"><label class="check"><input type="checkbox" id="log-follow" ${follow ? "checked" : ""}>맨 아래 따라가기</label>
    <button class="btn sm" id="log-copy">복사</button></div></div><pre class="log" id="log-pre">불러오는 중…</pre>`;
  const loadLog = async () => {
    const pre = $("#log-pre");
    if (!pre) return;
    try {
      const text = await api(`/api/jobs/${id}/logs?tail=2000`);
      pre.textContent = text || "아직 출력이 없습니다.";
      if (follow) pre.scrollTop = pre.scrollHeight;
    } catch (e) { pre.textContent = e.message; }
  };
  const foot = () => !mine ? "" : `
    <span class="left">${["queued", "running"].includes(j.status) ? `<button class="btn danger" id="job-cancel">${icon("stop")}작업 취소</button>` : ""}</span>
    <button class="btn" id="job-rerun">같은 설정으로 다시 제출</button>`;
  const bindFoot = () => {
    const c = $("#job-cancel");
    if (c) c.onclick = async () => {
      if (!(await confirmDialog(`작업 #${id}을 취소할까요? 실행 중이면 바로 종료됩니다.`, { ok: "작업 취소", danger: true }))) return;
      try { await api(`/api/jobs/${id}/cancel`, { method: "POST" }); toast(`작업 #${id}을 취소했습니다`); refresh(); } catch (e) { fail(e); }
    };
    const r = $("#job-rerun");
    if (r) r.onclick = () => openSubmit(j);
  };
  const showTab = (tab) => {
    clearInterval(logTimer);
    $("#drawer-body").innerHTML = tab === "logs" ? logPane() : body();
    if (tab === "logs") {
      $("#log-follow").onchange = (e) => (follow = e.target.checked);
      $("#log-copy").onclick = () => copyText($("#log-pre").textContent);
      loadLog();
      logTimer = setInterval(() => j.status === "running" && loadLog(), 2000);
    }
  };
  openDrawer({
    title: esc(j.name), sub: `작업 #${id}, ${ago(j.created_at)} 제출`,
    tabs: mine ? [["overview", "개요"], ["logs", "로그"]] : null, tab: "overview",
    body: body(), foot: foot(), width: 620,
    onTab: showTab,
    dispose: () => clearInterval(logTimer),
    onClose: () => { if (S.page === "jobs") { go("jobs"); renderCrumbs(); } },
    refresh: async () => {
      j = await api(`/api/jobs/${id}`);
      if (S.drawer?.tab !== "logs") $("#drawer-body").innerHTML = body();
      else if ($("#log-state")) $("#log-state").textContent = j.status === "running" ? "실시간으로 받는 중" : "저장된 로그";
      const f = $("#drawer-foot"); if (f) { f.innerHTML = foot(); bindFoot(); }
    },
  });
  S.drawer.jobId = id;
  bindFoot();
  $$("#jobs-table tr").forEach((r) => r.classList.toggle("sel", Number(r.dataset.id) === id));
}

function openSubmit(from = null) {
  const q = S.overview?.queue || { total_gb: 24, reserve_gb: 1, budget_gb: 0 };
  const cap = Math.floor(q.total_gb - q.reserve_gb);
  const maxV = S.me.max_vram_gb ? Math.min(cap, S.me.max_vram_gb) : cap;
  const v0 = from ? Math.min(from.vram_gb, maxV) : Math.min(8, maxV);
  openDrawer({
    title: from ? "작업 다시 제출" : "작업 제출",
    sub: "GPU에 자리가 나는 대로 먼저 낸 순서대로 실행됩니다.",
    width: 600,
    body: `<form id="submit-form" class="form-grid">
      <div class="field"><label for="f-name">이름</label><input class="input" id="f-name" name="name" placeholder="trellis-1536" value="${esc(from?.name || "")}"></div>
      <div class="field"><label for="f-wd">작업 폴더</label><input class="input" id="f-wd" name="workdir" placeholder="projects/TRELLIS.2" value="${esc(from?.workdir || "")}"><span class="hint">홈(/home/dev) 기준 경로</span></div>
      <div class="field span2"><label for="f-cmd">명령</label><textarea class="input" id="f-cmd" name="command" rows="5" required placeholder="conda activate trellis2 && python example.py --resolution 1024">${esc(from?.command || "")}</textarea>
        <span class="hint">bash -lc 로 실행됩니다. 출력은 로그 탭에서 실시간으로 볼 수 있습니다.</span></div>
      <div class="field span2"><label for="f-vram">필요한 VRAM</label>
        <div class="range-row"><input type="range" id="f-vram-r" min="1" max="${maxV}" step="1" value="${v0}"><input class="input" id="f-vram" name="vram_gb" type="number" min="1" max="${maxV}" step="0.5" value="${v0}"><span>GB</span></div>
        <span class="hint" id="vram-hint"></span></div>
      <label class="check span2"><input type="checkbox" name="exclusive" id="f-ex" ${from?.exclusive ? "checked" : ""}><span>GPU 독점<br><span class="muted">다른 작업이 모두 끝난 뒤 GPU 전체를 혼자 씁니다. 벤치마크나 1536³ 생성처럼 꼭 필요할 때만 쓰세요.</span></span></label>
      <details class="span2"><summary class="muted" style="cursor:pointer">CPU와 메모리 조정</summary>
        <div class="form-grid" style="margin-top:12px">
          <div class="field"><label>CPU 코어</label><input class="input" name="cpus" type="number" step="0.5" min="0.5" placeholder="기본값 (내 할당량)"></div>
          <div class="field"><label>메모리 (GB)</label><input class="input" name="mem_gb" type="number" min="1" placeholder="기본값 (내 할당량)"></div>
        </div></details>
    </form>`,
    foot: `<button class="btn" data-drawer-close>취소</button><button class="btn primary" id="submit-go">${icon("plus")}큐에 넣기</button>`,
  });
  const r = $("#f-vram-r"), n = $("#f-vram"), ex = $("#f-ex");
  const hint = () => {
    const v = Number(n.value);
    const budget = S.overview?.queue.budget_gb ?? 0;
    $("#vram-hint").innerHTML = ex.checked ? "독점 작업은 실행 중인 다른 작업이 모두 끝나야 시작합니다."
      : v <= budget ? `<span style="color:var(--ok)">지금 여유 ${budget} GB 안이라 바로 시작될 가능성이 높습니다.</span>`
      : `<span class="reason">지금 여유는 ${budget} GB입니다. 다른 작업이 끝날 때까지 대기열에서 기다립니다.</span>`;
    r.disabled = n.disabled = ex.checked;
  };
  r.oninput = () => { n.value = r.value; hint(); };
  n.oninput = () => { r.value = n.value; hint(); };
  ex.onchange = hint;
  hint();
  $("#drawer-foot [data-drawer-close]").onclick = () => closeDrawer();
  const submit = async (e) => {
    e?.preventDefault();
    const f = $("#submit-form").elements;
    if (!f.command.value.trim()) { f.command.focus(); return; }
    const body = { name: f.name.value.trim(), workdir: f.workdir.value.trim(), command: f.command.value, vram_gb: Number(f.vram_gb.value) || 1, exclusive: f.exclusive.checked };
    if (f.cpus.value) body.cpus = Number(f.cpus.value);
    if (f.mem_gb.value) body.mem_gb = Number(f.mem_gb.value);
    $("#submit-go").disabled = true;
    try {
      const j = await api("/api/jobs", { method: "POST", json: body });
      closeDrawer(true);
      toast(`작업 #${j.id}을 큐에 넣었습니다`);
      go("jobs", j.id);
    } catch (err) { fail(err); $("#submit-go").disabled = false; }
  };
  $("#submit-go").onclick = submit;
  $("#submit-form").onsubmit = submit;
}

// ═════════════════════════ 생성 (Easy): 3D, 이미지, 음악, 영상, 음성, 효과음 ═════════════════════════
// 올린 것과 글, 조절 값을 서버가 받아 GPU 작업으로 큐에 넣는다 (/api/gen/<종류>). 순서 기다리기, 로그, 취소는 그 작업을 그대로 쓴다.
// 입력 양식은 서버가 돌려주는 정의(texts, upload, params)를 읽어 그린다. 조절 값이 늘어도 화면 코드는 그대로다.
// 종류마다 다른 것은 결과를 보는 방법(open)과 카드의 한 줄 요약(sub)뿐이다. 양식·목록·카드·취소·지우기는 genPage 하나가 맡는다.
const GEN_STATE = { queued: ["대기", "warn"], running: ["만드는 중", "ok run"], succeeded: ["완성", "info"], failed: ["실패", "bad"], cancelled: ["취소됨", ""] };
const GEN_MAX_COUNT = 8;      // 한 번에 큐에 넣을 수 있는 개수. 시드를 1씩 올려 가며 그만큼 만든다
const homeFile = (rel) => api(`/api/files/download?area=home&path=${encodeURIComponent(rel)}`, { raw: true });
const isPicture = (file) => /\.(png|jpe?g|webp)$/i.test(file.name);
const isSound = (file) => /\.(wav|mp3|flac|ogg|m4a)$/i.test(file.name);
const isVideo = (file) => /\.(mp4|mov|webm|mkv)$/i.test(file.name);
// 올리는 칸 하나(서버 정의의 upload)가 받는 파일인지, 파일 고르기 창에 줄 종류, 세는 단위
// media 는 소리나 영상 가운데 하나 (소리 다듬기는 영상의 소리도 다듬는다)
const upOk = (up, file) => (up.media ? isSound(file) || isVideo(file) : up.audio ? isSound(file) : up.video ? isVideo(file) : isPicture(file));
const upAccept = (up) => (up.media ? "audio/*,video/*,.wav,.mp3,.flac,.ogg,.m4a,.mp4,.mov,.webm,.mkv"
  : up.audio ? "audio/*,.wav,.mp3,.flac,.ogg,.m4a" : up.video ? "video/*,.mp4,.mov,.webm,.mkv" : "image/png,image/jpeg,image/webp");
const upUnit = (up) => (up.audio || up.video || up.media ? "개" : "장");
const upWrong = (up, file) => (up.media
  ? `소리나 영상 파일이 아닙니다 (받은 이름 '${file.name}'). wav, mp3, flac, ogg, m4a 또는 mp4, mov, webm, mkv 파일을 고르세요.`
  : up.audio
  ? `소리 파일이 아닙니다 (받은 이름 '${file.name}'). wav, mp3, flac, ogg, m4a 파일을 고르세요.`
  : up.video ? `영상 파일이 아닙니다 (받은 이름 '${file.name}'). mp4, mov, webm, mkv 파일을 고르세요.`
  : `사진 파일이 아닙니다 (받은 이름 '${file.name}'). png, jpg, webp 파일을 고르세요.`);
// 고른 순서마다 붙는 이름: 서버가 준 이름(첫 장면, 끝 장면)이 있으면 그것, 소리면 화자 번호
// 참고 자료 하나가 어떤 종류(그림·영상·소리)인지. 서버 정의(refs.kinds)의 확장자로 가른다
const refKind = (rs, file) => rs.kinds.find((k) => k.exts.some((e) => file.name.toLowerCase().endsWith(e)));
const upSlot = (up, i) => up.slots?.[i] || (up.audio ? `화자${i + 1}` : "");
const shortTitle = (t) => (t.length > 30 ? t.slice(0, 30) + "…" : t);

// 목록에 넣을 작은 미리보기 사진 (긴 변 320px JPEG). 못 만들면 null: 없어도 동작한다
async function shrinkPicture(file) {
  try {
    const bmp = await createImageBitmap(file);
    const k = Math.min(1, 320 / Math.max(bmp.width, bmp.height));
    const c = document.createElement("canvas");
    c.width = Math.max(1, Math.round(bmp.width * k));
    c.height = Math.max(1, Math.round(bmp.height * k));
    const g = c.getContext("2d");
    g.fillStyle = "#fff";                      // 투명한 배경은 흰색으로 채운다
    g.fillRect(0, 0, c.width, c.height);
    g.drawImage(bmp, 0, 0, c.width, c.height);
    bmp.close?.();
    return await new Promise((done) => c.toBlob(done, "image/jpeg", 0.82));
  } catch { return null; }
}

// ── 조절 값 하나를 다루는 작은 함수들 (p 는 서버 정의의 params 한 줄) ──
// 입력 칸의 글자를 서버에 보낼 값으로 바꾼다. 빈 칸은 null: 서버가 기본값(또는 모델 기본값)을 쓴다
function genValue(p, raw) {
  if (p.type === "bool") return !!raw;
  if (p.type === "choice") return raw ?? p.default;
  const text = String(raw ?? "").trim();
  if (text === "") return null;
  if (p.type === "schedule") return text;
  const n = Number(text);
  if (!Number.isFinite(n)) throw new Error(`'${p.label}'은 숫자여야 합니다 (받은 값 '${text}')`);
  return n;
}
// 그 값이 기본값과 같은지. 빈 값(null)은 기본값을 쓴다는 뜻이므로 같다고 본다
function genSame(p, v) {
  if (v == null || v === "") return true;
  if (typeof v === "number" && typeof p.default === "number") return Math.abs(v - p.default) < 1e-9;
  return v === p.default;
}
function genShown(p, v) {
  if (v == null || v === "") return p.default == null ? "자동" : genShown(p, p.default);
  if (p.type === "bool") return v ? "켬" : "끔";
  if (p.type === "choice") return p.choices.find((c) => c[0] === v)?.[1] ?? String(v);
  return typeof v === "number" ? v.toLocaleString("ko-KR", { maximumFractionDigits: 4 }) : String(v);
}
const genGroupName = (name) => name.replace(/^\d+\.\s*/, "");
// 항목 하나(r)에서 기본값과 다르게 준 상세 설정의 수. 상세 설정에 적은 글(피할 내용, 준 악보)도 센다
const genChanged = (g, r) => g.params.filter((p) => p.group && p.key in r.params && !genSame(p, r.params[p.key])).length
  + g.texts.filter((t) => t.group && r.texts?.[t.key]).length;

// basic 이 참이면 기본 양식에 넣는 모양(이름 위, 입력 칸 아래), 아니면 상세 설정 묶음 안의 한 줄
function genParamField(p, raw, basic = false) {
  const id = `gp-${p.key}`, v = raw === undefined ? p.default : raw;
  if (p.type === "bool") {
    return `<label class="check ${basic ? "" : "gen-row wide"}" data-row="${p.key}"><input type="checkbox" id="${id}" data-param="${p.key}" ${v ? "checked" : ""}>
      <span><span class="gen-label">${esc(p.label)}</span><br><span class="${basic ? "muted" : "hint"}">${esc(p.help)}</span></span></label>`;
  }
  const range = p.min != null && p.max != null ? `<span class="gen-range">${p.min}~${p.max.toLocaleString("ko-KR")}</span>` : "";
  let input, wide = false;
  if (p.type === "choice") {
    wide = true;
    input = `<select class="input" id="${id}" data-param="${p.key}">${p.choices.map(([k, l]) => `<option value="${esc(k)}" ${k === v ? "selected" : ""}>${esc(l)}</option>`).join("")}</select>`;
  } else if (p.type === "schedule") {
    wide = true;
    input = `<input class="input mono" id="${id}" data-param="${p.key}" type="text" spellcheck="false" value="${esc(v ?? "")}" placeholder="비워 두면 쓰지 않음. 예) 1,0.95,0.85,0.6">`;
  } else {
    input = `<input class="input" id="${id}" data-param="${p.key}" type="number" inputmode="decimal" ${p.min != null ? `min="${p.min}"` : ""} ${p.max != null ? `max="${p.max}"` : ""}
      step="${p.step ?? "any"}" value="${v ?? ""}" placeholder="${p.default == null ? "자동" : p.default}">`;
  }
  return `<div class="${basic ? "field" : `gen-row ${wide ? "wide" : ""}`}" data-row="${p.key}"><label for="${id}"><span class="gen-label">${esc(p.label)}</span>${basic ? "" : range}</label>${input}
    <span class="hint">${esc(p.help)}</span></div>`;
}

function genTextField(t, value) {
  return `<div class="${t.group ? "gen-row wide" : "field"}" data-row="t-${t.key}"><label for="gt-${t.key}"><span class="gen-label">${esc(t.label)}</span>${t.required ? "" : ' <span class="muted">(선택)</span>'}</label>
    <textarea class="input ${t.mono ? "" : "gen-text"}" id="gt-${t.key}" data-text="${t.key}" rows="${t.rows}" maxlength="${t.max_chars}" spellcheck="false" placeholder="${esc(t.placeholder)}">${esc(value || "")}</textarea>
    <span class="hint">${esc(t.help)}</span></div>`;
}

// 결과를 보는 서랍에 넣는 "적은 글"과 "쓴 설정". 바꾼 설정은 굵게, 기본값을 옆에 적는다
function genFacts(g, r) {
  const texts = g.texts.filter((t) => r.texts?.[t.key]).map((t) => `
    <div class="section-title">${esc(t.label)}</div>
    <div class="copy gen-copy"><code>${esc(r.texts[t.key])}</code><button class="btn sm" data-copy="${esc(r.texts[t.key])}">복사</button></div>`).join("");
  const rows = g.params.filter((p) => p.key in r.params).map((p) => {
    const v = r.params[p.key], same = p.key === "seed" || genSame(p, v);      // 시드는 늘 바꾸는 값이라 굵게 하지 않는다
    return `<dt>${p.group ? `<span class="muted">${esc(genGroupName(p.group))} ·</span> ` : ""}${esc(p.label)}</dt>
      <dd>${same ? esc(genShown(p, v)) : `<b>${esc(genShown(p, v))}</b> <span class="muted">(기본 ${esc(genShown(p, null))})</span>`}</dd>`;
  }).join("");
  const n = g.params.filter((p) => p.key !== "seed" && p.key in r.params && !genSame(p, r.params[p.key])).length;
  // 이어 붙인 영상은 쓴 설정이 없다. 대신 이은 영상을 차례대로 보인다
  const joined = r.params?.joined ? `<dt>이은 영상</dt><dd>${r.params.joined.map((id, i) => {
    const x = g.runs.find((y) => y.id === id);
    return `${i + 1}. ${esc(x ? shortTitle(x.title) : `#${id} (목록에서 지움)`)}`;
  }).join("<br>")}</dd>` : "";
  return `${texts}
    ${joined ? "" : `<details class="gen-details" ${n ? "open" : ""}><summary>쓴 설정 <span class="muted">${n ? `기본값에서 ${n}개 바꿈` : "모두 기본값"}</span></summary><dl class="dl">${rows}</dl></details>`}
    <dl class="dl" style="margin-top:14px">${joined}
      <dt>파일</dt><dd class="mono">${esc(r.output)}${r.output_size != null ? ` (${fmtBytes(r.output_size)})` : ""}</dd>
      <dt>걸린 시간</dt><dd>${r.started_at && r.finished_at ? dur(r.finished_at - r.started_at) : "—"}</dd>
      <dt>VRAM 최대 사용</dt><dd>${r.peak_vram_gb ? r.peak_vram_gb + " GB" : "—"}</dd>
    </dl>`;
}

// 종류 하나의 화면을 만든다. o 는 그 종류만의 것: 제목과 글귀, 카드의 한 줄 요약(sub), 결과 보기(open)
function genPage(kind, o) {
  const view = {
    kind,
    thumbs: {},        // 항목 id → 작은 사진 주소. 받는 중이면 Promise
    shown: {},         // 항목 id → 마지막으로 그린 내용. 바뀐 카드만 다시 그려 사진이 깜빡이지 않게 한다
    busy: false,
    built: false,      // 서버 정의를 받아 양식을 그렸는지
    files: [],         // 올릴 파일 (File 배열)
    refs: [],          // 올릴 참고 자료 (File 배열, 영상의 Ref2VA). 종류마다 올린 차례가 <Picture 1>, <Video 1> 의 번호다
    roles: [],         // 파일마다 고른 역할 (3D: 찍은 방향). 정의에 roles 가 있을 때만 쓴다
    draft: { texts: {}, params: {}, count: "1", open: false },   // 양식에 적어 둔 내용. 다른 화면에 다녀와도 남게 한다
    presetsShown: "",
    state: () => S.gen[kind],

    render() {
      view.shown = {};
      view.built = false;
      $("#page").innerHTML = pageHead(o.title, o.desc) + `
        <div id="gen-setup"></div>
        <section class="panel gen-new" id="gen-form"><div class="gen-wait">양식을 불러오는 중…</div></section>
        <section class="panel flush">
          <div class="toolbar"><b>만든 것</b><span class="muted" id="gen-count"></span>${o.join ? `<span class="spacer"></span>
            <button class="btn sm" id="gen-join">${icon("link")}영상 이어 붙이기</button>` : ""}</div>
          ${o.join ? `<div class="gen-joinbar hidden" id="gen-joinbar" aria-live="polite"></div>` : ""}
          <div class="g3-grid" id="gen-grid"></div>
        </section>`;
      $("#gen-grid").onclick = (e) => {
        const b = e.target.closest("[data-act]"), card = e.target.closest(".g3-card");
        const r = card && view.state()?.runs.find((x) => String(x.id) === card.dataset.id);
        if (b && r) view.act(b.dataset.act, r);
      };
      if (o.join) $("#gen-join").onclick = () => view.pickJoin(view.joinPick ? null : []);
      if (view.state()) view.form();
    },

    // ── 영상 이어 붙이기: 다 만든 영상을 차례대로 고른다 ──
    joinPick: null,     // 고른 항목 id 차례. null 이면 고르는 중이 아니다
    pickJoin(ids) {
      view.joinPick = ids;
      view.shown = {};
      view.draw();
    },
    joinBar() {
      const bar = $("#gen-joinbar");
      if (!bar) return;
      const ids = view.joinPick, runs = view.state()?.runs || [];
      bar.classList.toggle("hidden", !ids);
      $("#gen-join").classList.toggle("primary", !!ids);
      if (!ids) { bar.innerHTML = ""; return; }
      const picked = ids.map((id) => runs.find((r) => r.id === id)).filter(Boolean);
      bar.innerHTML = `<div class="gen-joinbar-text"><b>이을 영상을 붙일 차례대로 누르세요.</b>
          ${picked.length ? `<ol>${picked.map((r) => `<li>${esc(shortTitle(r.title))}</li>`).join("")}</ol>` : '<span class="muted">아직 고른 영상이 없습니다. 다 만든 영상만 고를 수 있습니다.</span>'}
          <span class="hint">끝 장면에서 이어 만든 영상끼리는 겹치는 장면을 잘라 매끄럽게 잇고, 그렇지 않은 영상은 그대로 붙입니다. GPU 는 거의 쓰지 않습니다.</span></div>
        <div class="gen-joinbar-actions"><button class="btn ghost" id="gen-join-stop">그만두기</button>
          <button class="btn primary" id="gen-join-go" ${picked.length < 2 ? "disabled" : ""}>${icon("link")}${picked.length >= 2 ? `${picked.length}개 이어 붙이기` : "2개 이상 고르세요"}</button></div>`;
      $("#gen-join-stop").onclick = () => view.pickJoin(null);
      $("#gen-join-go").onclick = async () => {
        try {
          const made = await api(`/api/gen/${kind}/join`, { method: "POST", json: { ids: picked.map((r) => r.id) } });
          toast(`영상 ${picked.length}개를 이어 붙이는 작업을 큐에 넣었습니다 (작업 #${made.job_id})`);
          view.pickJoin(null);
          await view.refresh();
        } catch (e) { fail(e); }
      };
    },
    // 끝 장면에서 이어 만든 영상의 차례: 이 영상에서 앞 영상(params.after)을 따라 처음까지 거슬러 간다
    chainOf(r) {
      const runs = view.state()?.runs || [], chain = [r];
      for (let cur = r; cur.params?.after != null;) {
        cur = runs.find((x) => x.id === cur.params.after);
        if (!cur || chain.includes(cur)) break;
        chain.unshift(cur);
      }
      return chain.filter((x) => x.status === "succeeded" && x.output_size != null);
    },

    async refresh() {
      S.gen[kind] = await api(`/api/gen/${kind}`);
      view.draw();
    },

    // ── 양식: 서버 정의에서 그린다 ──
    form() {
      const g = view.state(), d = view.draft, box = $("#gen-form");
      if (!g || !box) return;
      const groups = [];        // 상세 설정의 묶음. 정의에 나온 순서대로
      const groupOf = (name) => groups.find((x) => x.name === name) || groups[groups.push({ name, texts: [], params: [] }) - 1];
      g.texts.filter((t) => t.group).forEach((t) => groupOf(t.group).texts.push(t));
      g.params.filter((p) => p.group).forEach((p) => groupOf(p.group).params.push(p));
      // 묶음 이름의 앞 번호(1. 2. …) 순서로 둔다. 글 칸이 뒤 묶음에 들어 있어도 묶음 차례가 흐트러지지 않게
      groups.sort((a, b) => (parseInt(a.name, 10) || 99) - (parseInt(b.name, 10) || 99));
      queueMicrotask(() => { if (g.vram_by) $(`#gp-${g.vram_by.param}`)?.addEventListener("change", () => view.note()); });
      // 시드가 없는 종류(소리 다듬기)는 같은 입력이면 같은 결과라 여러 개 만들 까닭이 없다
      const seeded = g.params.some((p) => p.key === "seed");
      const up = g.upload;
      const drop = !up ? "" : up.roles?.length
        ? `<div class="g3-drop gv-drop" id="gen-drop"><div class="gv-grid" id="gen-pick"></div><div class="gv-hint" id="gen-pick-hint"></div></div>`
        : up.max === 1
        ? `<label class="g3-drop" id="gen-drop"><input type="file" id="gen-file" accept="${upAccept(up)}" hidden><div class="g3-pick" id="gen-pick"></div></label>`
        : `<label class="g3-drop gi-drop" id="gen-drop"><input type="file" id="gen-file" accept="${upAccept(up)}" multiple hidden><div class="gi-refs" id="gen-pick"></div></label>`;
      const rs = g.refs;
      const refsZone = !rs ? "" : `<div class="gen-refs" id="gen-refs">
          <div class="gen-refs-head"><b>${esc(rs.label)}</b> <span class="muted">(선택)</span></div>
          <div class="gen-ref-list" id="gen-ref-list"></div>
          <div class="gen-refs-foot"><label class="btn sm"><input type="file" id="gen-ref-file" accept="${rs.exts.join(",")}" multiple hidden>${icon("upload")}참고 자료 더하기</label>
            <span class="hint">${esc(rs.help)}</span></div></div>`;
      box.innerHTML = `
        <div class="gen-main">${g.texts.filter((t) => !t.group).map((t) => genTextField(t, d.texts[t.key])).join("")}${drop}${refsZone}</div>
        <div class="g3-side">
          ${g.params.filter((p) => !p.group).map((p) => genParamField(p, d.params[p.key], true)).join("")}
          ${seeded ? `<div class="field"><label for="gen-n">만들 개수</label><input class="input" id="gen-n" type="number" min="1" max="${GEN_MAX_COUNT}" step="1" value="${esc(d.count)}">
            <span class="hint">2 이상이면 시드를 1씩 올려 가며 그만큼 만듭니다. 마음에 드는 것을 고를 때 씁니다.</span></div>` : `<input type="hidden" id="gen-n" value="1">`}
          <div class="g3-note" id="gen-note"></div>
          <button class="btn primary" id="gen-go" disabled>${icon(o.icon)}${o.go}</button>
        </div>
        <div class="gen-adv">
          <div class="gen-adv-head">
            <button class="btn sm gen-toggle ${d.open ? "open" : ""}" id="gen-toggle">${icon("chev")}상세 설정<span class="gen-count" id="gen-changed"></span></button>
            <span class="muted gen-adv-desc">${groups.map((x) => esc(genGroupName(x.name))).join(" → ")}</span>
            <span class="spacer"></span>
            <select class="input" id="gen-preset" title="저장해 둔 설정 묶음"></select>
            <button class="btn sm" id="gen-preset-load">불러오기</button>
            <button class="icon-btn" id="gen-preset-del" title="고른 설정 묶음 지우기">${icon("trash")}</button>
            <button class="btn sm" id="gen-preset-save">지금 설정 저장</button>
            <button class="btn sm ghost" id="gen-reset">기본값으로</button>
          </div>
          <div class="gen-groups ${d.open ? "" : "hidden"}" id="gen-groups">${groups.map((x) => `
            <div class="gen-group" data-group="${esc(x.name)}"><div class="gen-group-head"><b>${esc(x.name)}</b><span class="gen-count"></span></div>
              ${x.texts.map((t) => genTextField(t, d.texts[t.key])).join("")}${x.params.map((p) => genParamField(p, d.params[p.key])).join("")}</div>`).join("")}
          </div>
        </div>`;
      view.built = true;
      view.presetsShown = "";
      box.oninput = () => { view.draft = view.read(); view.marks(); view.note(); };
      $("#gen-go").onclick = () => view.go();
      $("#gen-toggle").onclick = () => view.openGroups($("#gen-groups").classList.contains("hidden"));
      $("#gen-reset").onclick = () => { view.draft = { ...view.read(), params: {} }; view.form(); toast("조절 값을 모두 기본값으로 돌렸습니다"); };
      $("#gen-preset-load").onclick = () => view.loadPreset();
      $("#gen-preset-save").onclick = () => view.savePreset();
      $("#gen-preset-del").onclick = () => view.deletePreset();
      if (up) {
        const dz = $("#gen-drop");
        // 방향을 고르는 사진 칸은 파일 고르기 단추를 showFiles 가 그리고 그때 연결한다
        if ($("#gen-file")) $("#gen-file").onchange = (e) => { view.addFiles([...e.target.files]); e.target.value = ""; };
        dz.ondragover = (e) => { e.preventDefault(); dz.classList.add("over"); };
        dz.ondragleave = (e) => { if (!dz.contains(e.relatedTarget)) dz.classList.remove("over"); };
        dz.ondrop = (e) => { e.preventDefault(); dz.classList.remove("over"); view.addFiles([...e.dataTransfer.files]); };
        $("#gen-pick").onclick = (e) => {
          const b = e.target.closest("[data-drop-file]"); if (!b) return;
          e.preventDefault();                     // 빼기 단추가 파일 고르기 창을 열지 않게 한다
          view.files.splice(Number(b.dataset.dropFile), 1);
          view.roles.splice(Number(b.dataset.dropFile), 1);
          view.showFiles();
        };
        // 사진마다 방향 고르기. 이미 다른 사진이 쓰는 방향을 고르면 두 사진의 방향을 맞바꾼다
        $("#gen-pick").onchange = (e) => {
          const sel = e.target.closest("[data-role-of]"); if (!sel) return;
          const i = Number(sel.dataset.roleOf), was = view.roles[i], j = view.roles.indexOf(sel.value);
          if (j >= 0 && j !== i) view.roles[j] = was;
          view.roles[i] = sel.value;
          view.showFiles();
        };
        view.showFiles();
      }
      if (rs) {
        const zone = $("#gen-refs");
        $("#gen-ref-file").onchange = (e) => { view.addRefs([...e.target.files]); e.target.value = ""; };
        zone.ondragover = (e) => { e.preventDefault(); zone.classList.add("over"); };
        zone.ondragleave = (e) => { if (!zone.contains(e.relatedTarget)) zone.classList.remove("over"); };
        zone.ondrop = (e) => { e.preventDefault(); zone.classList.remove("over"); view.addRefs([...e.dataTransfer.files]); };
        $("#gen-ref-list").onclick = (e) => {
          const drop = e.target.closest("[data-drop-ref]"), tag = e.target.closest("[data-tag]");
          if (drop) { view.refs.splice(Number(drop.dataset.dropRef), 1); view.showRefs(); return; }
          if (tag) {                                // 장면 글의 커서 자리에 <Picture 1> 같은 이름을 넣는다
            const ta = $(`#gt-${g.texts[0].key}`), at = ta.selectionStart ?? ta.value.length, to = ta.selectionEnd ?? at;
            ta.value = ta.value.slice(0, at) + tag.dataset.tag + ta.value.slice(to);
            ta.focus();
            ta.selectionStart = ta.selectionEnd = at + tag.dataset.tag.length;
            view.draft = view.read();
            view.note();
          }
        };
        view.showRefs();
      }
      view.presets();
      view.marks();
      view.note();
    },

    // ── 참고 자료 ──
    addRefs(list) {
      const g = view.state(), rs = g?.refs;
      if (!rs) return;
      if (rs.exclusive && view.files.length) {
        toast(`${g.upload.label}과 ${rs.label}는 함께 쓸 수 없습니다 (모델이 둘을 함께 받지 않습니다). 먼저 ${g.upload.label}을 빼세요.`, true);
        return;
      }
      for (const f of list) {
        const k = refKind(rs, f);
        if (!k) { toast(`참고 자료로 받지 않는 파일입니다 (받은 이름 '${f.name}'). 그림(png·jpg·webp), 영상(mp4·mov·webm·mkv), 소리(wav·mp3·flac·ogg·m4a)를 고르세요.`, true); continue; }
        const n = view.refs.filter((x) => refKind(rs, x)?.kind === k.kind).length;
        if (n >= k.max) { toast(`${rs.label} 가운데 ${k.label}은 ${k.max}개까지입니다`, true); continue; }
        view.refs.push(f);
      }
      view.showRefs();
    },
    showRefs() {
      const rs = view.state()?.refs, box = $("#gen-ref-list");
      if (!rs || !box) return;
      box.querySelectorAll("img").forEach((i) => URL.revokeObjectURL(i.src));
      const seen = {};
      box.innerHTML = view.refs.length ? view.refs.map((f, i) => {
        const k = refKind(rs, f);
        seen[k.kind] = (seen[k.kind] || 0) + 1;
        const tag = `<${k.tag} ${seen[k.kind]}>`;
        const face = k.kind === "image" ? `<img src="${URL.createObjectURL(f)}" alt="">` : `<i class="gen-ref-icon">${icon(k.kind === "video" ? "video" : "mic")}</i>`;
        return `<span class="gen-ref">${face}<span class="gen-ref-text"><b title="${esc(f.name)}">${esc(f.name)}</b><em>${esc(k.label)}, ${fmtBytes(f.size)}</em></span>
          <button class="btn sm mono" data-tag="${esc(tag)}" title="장면 글의 커서 자리에 ${esc(tag)} 넣기">${esc(tag)}</button>
          <button class="icon-btn" data-drop-ref="${i}" title="이 참고 자료 빼기">${icon("close")}</button></span>`;
      }).join("") : `<span class="muted">그림·영상·소리를 여기에 놓거나 아래 단추로 고르세요. 넣으면 장면 글에서 이름(&lt;Picture 1&gt; 등)으로 부릅니다.</span>`;
      $("#gen-refs").classList.toggle("has", view.refs.length > 0);
      view.note();
    },

    read() {
      const params = {}, texts = {};
      $$("#gen-form [data-param]").forEach((el) => { params[el.dataset.param] = el.type === "checkbox" ? el.checked : el.value; });
      $$("#gen-form [data-text]").forEach((el) => { texts[el.dataset.text] = el.value; });
      return { params, texts, count: $("#gen-n").value, open: !$("#gen-groups").classList.contains("hidden") };
    },

    openGroups(open) {
      $("#gen-groups").classList.toggle("hidden", !open);
      $("#gen-toggle").classList.toggle("open", open);
      view.draft.open = open;
    },

    // 기본값에서 바꾼 칸에 표시를 하고, 묶음마다 몇 개를 바꿨는지 센다
    marks() {
      const g = view.state(), d = view.draft;
      if (!g || !view.built) return;
      let total = 0;
      const per = {};
      for (const p of g.params) {
        let changed = false;
        try { changed = !genSame(p, genValue(p, d.params[p.key] === undefined ? p.default : d.params[p.key])); } catch { changed = true; }
        $(`#gen-form [data-row="${p.key}"]`)?.classList.toggle("changed", changed);
        if (changed && p.group) { total++; per[p.group] = (per[p.group] || 0) + 1; }
      }
      for (const t of g.texts.filter((x) => x.group)) {
        const filled = !!(d.texts[t.key] || "").trim();
        $(`#gen-form [data-row="t-${t.key}"]`)?.classList.toggle("changed", filled);
        if (filled) { total++; per[t.group] = (per[t.group] || 0) + 1; }
      }
      $("#gen-changed").textContent = total ? `${total}개 바꿈` : "";
      $$("#gen-groups .gen-group").forEach((el) => { el.querySelector(".gen-count").textContent = per[el.dataset.group] ? `${per[el.dataset.group]}개 바꿈` : ""; });
    },

    // ── 올릴 파일 ──
    addFiles(list) {
      const up = view.state()?.upload, rs = view.state()?.refs;
      if (!up) return;
      if (rs?.exclusive && view.refs.length) {
        toast(`${up.label}과 ${rs.label}는 함께 쓸 수 없습니다 (모델이 둘을 함께 받지 않습니다). 첫 장면 그림은 참고 자료에 넣고 장면 글에 'the shot begins from <Picture 1>' 처럼 쓰세요.`, true);
        return;
      }
      for (const f of list) {
        if (!upOk(up, f)) { toast(upWrong(up, f), true); continue; }
        if (up.roles?.length) {
          if (view.files.length >= up.max) { toast(`${up.label}은 방향마다 한 장씩, ${up.max}장까지입니다`, true); break; }
          view.files.push(f);
          view.roles.push(up.roles.map((r) => r[0]).find((r) => !view.roles.includes(r)));     // 아직 안 쓴 첫 방향
          continue;
        }
        if (up.max === 1) { view.files = [f]; break; }
        if (view.files.length >= up.max) { toast(`${up.label}은 ${up.max}${upUnit(up)}까지입니다`, true); break; }
        view.files.push(f);
      }
      view.showFiles();
    },
    showFiles() {
      const up = view.state()?.upload, box = $("#gen-pick");
      if (!up || !box) return;
      box.querySelectorAll("img").forEach((i) => URL.revokeObjectURL(i.src));
      box.classList.toggle("has", view.files.length > 0);
      if (up.roles?.length) {
        const name = Object.fromEntries(up.roles.map((r) => [r[0], r[1]]));
        const many = view.files.length > 1;
        box.innerHTML = view.files.map((f, i) => `<div class="gv-tile ${view.roles[i] === up.first_role ? "main" : ""}">
            <img src="${URL.createObjectURL(f)}" alt="" title="${esc(f.name)}">
            <button class="icon-btn" data-drop-file="${i}" title="이 ${esc(up.label)} 빼기">${icon("close")}</button>
            ${many ? `<select class="input" data-role-of="${i}" title="이 ${esc(up.label)}을 찍은 방향">${up.roles.map(([k, l, how]) =>
              `<option value="${k}" title="${esc(how)}" ${k === view.roles[i] ? "selected" : ""}>${esc(l)}</option>`).join("")}</select>`
              : `<span class="gv-role">${esc(name[up.first_role])}</span>`}
          </div>`).join("")
          + (view.files.length < up.max ? `<label class="gv-tile gv-add"><input type="file" id="gen-file" accept="${upAccept(up)}" multiple hidden>
            ${icon("upload")}<span>${view.files.length ? "다른 방향의<br>사진 더하기" : `${esc(up.label)} 고르기`}</span></label>` : "");
        const used = view.roles.slice(0, view.files.length);
        const how = up.roles.filter(([k]) => used.includes(k)).map(([, l, h]) => `<b>${esc(l)}</b> ${esc(h)}`).join("<br>");
        $("#gen-pick-hint").innerHTML = !view.files.length
          ? `<b>${esc(up.label)}을 여기에 놓거나 눌러서 고르세요.</b> png, jpg, webp. ${esc(up.help)}`
          : many ? `${esc(up.label)} ${view.files.length}장을 한 물체로 합칩니다. 사진 아래에서 찍은 방향을 고르세요. `
              + `${used.includes(up.first_role) ? "" : `<span class="reason">'${esc(name[up.first_role])}' 방향 사진이 하나 있어야 합니다.</span> `}<br>${how}`
          : `${esc(up.label)} 한 장으로 만듭니다. 같은 물체를 다른 방향에서 찍은 사진을 더 넣으면 그 사진들까지 보고 만듭니다 (방향마다 한 장, ${up.max}장까지). `
              + `같은 거리·같은 줌으로 찍어야 합니다.`;
        const input = $("#gen-file");
        if (input) input.onchange = (e) => { view.addFiles([...e.target.files]); e.target.value = ""; };
        view.note();
        return;
      }
      if (up.max === 1) {
        const f = view.files[0];
        const what = up.media ? "wav, mp3, flac, ogg, m4a, mp4, mov, webm, mkv" : up.video ? "mp4, mov, webm, mkv" : "png, jpg, webp";
        const look = (file) => (up.video || (up.media && isVideo(file)) ? `<video src="${URL.createObjectURL(file)}" muted playsinline preload="metadata"></video>`
          : up.media ? `<span class="gen-pick-sound">${icon("wave")}</span>` : `<img src="${URL.createObjectURL(file)}" alt="">`);
        box.innerHTML = f
          ? `${look(f)}<div><b>${esc(f.name)}</b><span>${fmtBytes(f.size)}. 다른 ${esc(up.label)}으로 바꾸려면 누르거나 새 파일을 놓으세요.${up.video ? " 빼려면 오른쪽 버튼을 누르세요." : ""}</span></div>${up.video && !up.min ? `<button class="icon-btn" data-drop-file="0" title="이 ${esc(up.label)} 빼기">${icon("close")}</button>` : ""}`
          : `${icon("upload")}<b>${esc(up.label)}을 여기에 놓거나 눌러서 고르세요${up.min ? "" : " (선택)"}</b><span>${what}. ${esc(up.help)}</span>`;
      } else {
        const unit = upUnit(up);
        const one = (f, i) => {
          const slot = upSlot(up, i), drop = `<button class="icon-btn" data-drop-file="${i}" title="이 ${esc(up.label)} 빼기">${icon("close")}</button>`;
          return up.audio
            ? `<span class="gi-ref gv-ref" title="${esc(f.name)}">${icon("mic")}<span><b>${esc(f.name)}</b><em>${esc(slot)} · ${fmtBytes(f.size)}</em></span>${drop}</span>`
            : `<span class="gi-ref ${slot ? "slotted" : ""}"><img src="${URL.createObjectURL(f)}" alt="" title="${esc(f.name)}">${slot ? `<em class="gi-slot">${esc(slot)}</em>` : ""}${drop}</span>`;
        };
        box.innerHTML = view.files.length
          ? view.files.map(one).join("") + `<span class="gi-refs-hint">${esc(up.label)} ${view.files.length}${unit}. ${esc(up.help)}</span>`
          : `<span class="gi-refs-hint">${icon("upload")} ${esc(up.label)}을 여기에 놓거나 눌러서 고르세요 (${up.min ? "필수" : "선택"}, ${up.max}${unit}까지). ${esc(up.help)}</span>`;
      }
      view.note();
    },

    // ── 설정 묶음 (이름을 붙여 저장해 둔 조절 값) ──
    presets() {
      const g = view.state(), sel = $("#gen-preset");
      if (!g || !sel) return;
      const key = JSON.stringify(g.presets.map((p) => [p.id, p.name]));
      if (key === view.presetsShown) return;
      view.presetsShown = key;
      const cur = sel.value;
      sel.innerHTML = `<option value="">${g.presets.length ? "저장한 설정 고르기…" : "저장한 설정 없음"}</option>` + g.presets.map((p) => `<option value="${p.id}">${esc(p.name)}</option>`).join("");
      if (g.presets.some((p) => String(p.id) === cur)) sel.value = cur;
    },
    pickedPreset() {
      const id = $("#gen-preset").value, p = view.state().presets.find((x) => String(x.id) === id);
      if (!p) toast("먼저 저장한 설정을 고르세요", true);
      return p;
    },
    loadPreset() {
      const p = view.pickedPreset();
      if (!p) return;
      view.draft = { ...view.read(), params: { ...p.params } };
      view.form();
      $("#gen-preset").value = String(p.id);
      toast(`'${p.name}' 설정을 불러왔습니다`);
    },
    async savePreset() {
      const g = view.state();
      let params;
      try { params = view.values(g, view.read().params); } catch (e) { fail(e); return; }
      const picked = g.presets.find((x) => String(x.id) === $("#gen-preset").value);
      const name = await genAskName(picked?.name || "");
      if (name == null) return;
      try {
        const saved = await api(`/api/gen/${kind}/presets`, { method: "POST", json: { name, params } });
        await view.refresh();
        $("#gen-preset").value = String(saved.id);
        toast(`'${saved.name}' 설정을 저장했습니다`);
      } catch (e) { fail(e); }
    },
    async deletePreset() {
      const p = view.pickedPreset();
      if (!p || !(await confirmDialog(`저장한 설정 '${p.name}'을 지울까요?`, { ok: "지우기", danger: true }))) return;
      try {
        await api(`/api/gen/${kind}/presets/${p.id}`, { method: "DELETE" });
        await view.refresh();
        toast("지웠습니다");
      } catch (e) { fail(e); }
    },

    values(g, raw) {
      const out = {};
      for (const p of g.params) out[p.key] = genValue(p, raw[p.key] === undefined ? p.default : raw[p.key]);
      return out;
    },

    filled() {
      const g = view.state();
      if (!g || !view.built) return false;
      const d = view.draft;
      return g.texts.every((t) => !t.required || (d.texts[t.key] || "").trim()) && (!g.upload || view.files.length >= g.upload.min);
    },

    // 만들기 버튼과 그 위 안내 줄
    note() {
      const g = view.state(), btn = $("#gen-go"), note = $("#gen-note");
      if (!btn) return;
      btn.disabled = !g?.ready || !view.filled() || view.busy;
      btn.lastChild.textContent = view.busy ? "큐에 넣는 중…" : o.go;
      if (!g) { note.textContent = ""; return; }
      const budget = S.overview?.queue.budget_gb ?? 0, llm = S.overview?.llm;
      // 3D 여러 장은 따로 정한 만큼, 소리 다듬기는 '넣은 것'에 따라(나누지 않으면 조금만) 예약한다
      const by = g.vram_by, picked = by && $(`#gp-${by.param}`)?.value;
      const vram = by && picked in by.gb ? by.gb[picked] : g.vram_multi_gb && view.files.length > 1 ? g.vram_multi_gb : g.vram_gb;
      note.innerHTML = `VRAM ${vram} GB를 씁니다. ` + (vram <= budget
        ? `<span style="color:var(--ok)">지금 여유 ${budget} GB 안이라 바로 시작될 가능성이 높습니다.</span>`
        : `<span class="reason">지금 여유는 ${budget} GB입니다. 자리가 날 때까지 대기열에서 기다립니다.</span>`
          + (llm && llm.state !== "off" && llm.model ? ` LLM(${esc(llm.model.name)})이 올라가 있으면 내려갈 때까지 기다립니다.` : ""));
    },

    async go() {
      const g = view.state();
      if (view.busy || !g || !view.filled()) return;
      const raw = view.read();
      let values, count;
      try {
        values = view.values(g, raw.params);
        count = Number(String(raw.count).trim() || "1");
        if (!Number.isInteger(count) || count < 1 || count > GEN_MAX_COUNT) throw new Error(`만들 개수는 1~${GEN_MAX_COUNT} 사이의 정수여야 합니다 (받은 값 '${raw.count}')`);
      } catch (e) { fail(e); return; }
      const texts = {};
      for (const t of g.texts) texts[t.key] = (raw.texts[t.key] || "").trim();
      const seed = values.seed ?? g.params.find((p) => p.key === "seed")?.default;
      let files = view.files, roles = [];
      if (g.upload?.roles?.length && files.length > 1) {
        const first = g.upload.first_role, at = view.roles.indexOf(first);
        if (at < 0) { toast(`'${g.upload.roles.find((r) => r[0] === first)[1]}' 방향에서 찍은 ${g.upload.label}이 하나 있어야 합니다. 사진 아래에서 방향을 고르세요.`, true); return; }
        const order = [at, ...files.map((_, i) => i).filter((i) => i !== at)];     // 첫 사진이 앞이어야 다른 사진이 그 기준으로 놓인다
        files = order.map((i) => view.files[i]);
        roles = order.map((i) => view.roles[i]);
      }
      view.busy = true;
      view.note();
      let made = 0, last = null;
      try {
        const thumb = g.client_thumb && files[0] ? await shrinkPicture(files[0]) : null;
        for (let i = 0; i < count; i++) {
          const body = new FormData();
          body.append("texts", JSON.stringify(texts));
          body.append("params", JSON.stringify(seed == null ? values : { ...values, seed: seed + i }));
          for (const f of files) body.append("files", f, f.name);
          if (roles.length) body.append("roles", JSON.stringify(roles));
          if (view.after && (files[0] === view.after.file || view.refs.includes(view.after.file))) body.append("after", String(view.after.id));
          for (const f of view.refs) body.append("refs", f, f.name);
          if (thumb) body.append("thumb", thumb, "thumb.jpg");
          last = await api(`/api/gen/${kind}`, { method: "POST", body });
          if (thumb) view.thumbs[last.id] = URL.createObjectURL(thumb);
          made++;
        }
        toast(count > 1 ? `${made}개를 큐에 넣었습니다 (시드 ${seed}~${seed + made - 1})` : `'${shortTitle(last.title)}'을 큐에 넣었습니다 (작업 #${last.job_id})`);
      } catch (e) { fail(made ? new Error(`${made}개를 큐에 넣은 뒤 멈췄습니다. ${e.message || e}`) : e); }
      finally {
        view.busy = false;
        await view.refresh().catch(() => {});
        view.note();
      }
    },

    // 만든 항목의 글과 설정을 양식에 다시 채운다. 올렸던 사진도 서버에서 다시 받아 넣는다
    async again(r, extraTexts = {}, quiet = false) {
      const g = view.state();
      const params = {};
      for (const p of g.params) if (p.key in r.params && r.params[p.key] != null) params[p.key] = r.params[p.key];
      const texts = { ...(r.texts || {}), ...extraTexts };
      const open = g.params.some((p) => p.group && !genSame(p, params[p.key])) || g.texts.some((t) => t.group && texts[t.key]);
      view.draft = { texts, params, count: "1", open };
      closeDrawer();
      if (g.upload) {
        // inputs 에는 글 파일(.txt, .abc)도 들어 있다. 올린 파일(사진이나 소리)만 고른다 (작은 미리보기는 inputs 에 들어 있지 않다)
        const isRef = (x) => /-r\d+\.[a-z0-9]+$/i.test(x);
        const paths = (r.inputs || []).filter((x) => g.upload.exts.some((e) => x.toLowerCase().endsWith(e)) && !/\.thumb\.jpg$/i.test(x) && !isRef(x));
        if (g.refs) {
          const refPaths = (r.inputs || []).filter(isRef).sort((a, b) => Number(a.match(/-r(\d+)\./)[1]) - Number(b.match(/-r(\d+)\./)[1]));
          const back = [];
          for (const rel of refPaths) {
            try { const blob = await (await homeFile(rel)).blob(); back.push(new File([blob], rel.split("/").pop(), { type: blob.type })); } catch { /* 지워진 파일 */ }
          }
          view.refs = back;
          if (back.length < refPaths.length) toast(`올렸던 참고 자료 ${refPaths.length - back.length}개를 찾지 못했습니다. 다시 골라 주세요.`, true);
        }
        const got = [];
        for (const rel of paths) {
          try {
            const blob = await (await homeFile(rel)).blob();
            // 한 장만 올리는 종류(3D)는 목록의 이름이 올린 파일 이름이다. 그 이름을 되살려 다음 항목도 같은 이름으로 보이게 한다
            const name = paths.length === 1 && g.upload.max === 1 && isPicture({ name: r.title }) ? r.title : rel.split("/").pop();
            got.push(new File([blob], name, { type: blob.type || (g.upload.audio || (g.upload.media && !isVideo({ name })) ? "audio/wav" : g.upload.video || g.upload.media ? "video/mp4" : "image/png") }));
          } catch { /* 지워진 파일은 건너뛴다 */ }
        }
        view.files = got;
        if (g.upload.roles?.length) {
          const saved = String(r.params.views || "").split(",").filter(Boolean);
          view.roles = got.map((_, i) => saved[i] || g.upload.roles.map((x) => x[0]).find((k, n) => n >= i && !saved.includes(k)) || g.upload.first_role);
        }
        if (got.length < paths.length) toast(`올렸던 ${g.upload.label} ${paths.length - got.length}${upUnit(g.upload)}을 찾지 못했습니다. 다시 골라 주세요.`, true);
      }
      if (S.page !== o.page) return;            // 받는 동안 다른 화면으로 갔으면 채우기만 해 둔다
      view.form();
      $("#gen-form").scrollIntoView({ behavior: "smooth", block: "start" });
      if (!quiet) toast("글과 설정을 양식에 채웠습니다. 바꿀 곳을 고친 뒤 다시 만드세요.");
    },

    draw() {
      const g = view.state(), grid = $("#gen-grid");
      if (!g || !grid) return;
      if (!view.built) view.form();
      view.presets();
      $("#gen-setup").innerHTML = g.ready ? "" : `<div class="callout warn">${icon("info")}<div><b>아직 쓸 수 없습니다</b><br>${esc(g.hint)}</div></div>`;
      $("#gen-count").textContent = g.runs.length ? `${g.runs.length}개` : "";
      view.note();
      grid.querySelector(".empty")?.remove();
      const seen = new Set();
      let prev = null;
      for (const r of g.runs) {
        const id = String(r.id);
        seen.add(id);
        let card = grid.querySelector(`.g3-card[data-id="${id}"]`);
        if (!card) {
          card = document.createElement("div");
          card.className = "g3-card";
          card.dataset.id = id;
          card.innerHTML = `<div class="g3-thumb">${icon(o.icon)}</div><div class="g3-info"></div>`;
          delete view.shown[id];
        }
        const want = prev ? prev.nextElementSibling : grid.firstElementChild;
        if (card !== want) grid.insertBefore(card, want);
        // 작은 사진은 올릴 때 바로 있는 종류도 있고(3D), 작업이 끝나야 생기는 종류도 있다(이미지). 생긴 뒤에 한 번만 받는다.
        // 음악의 가벼운 사본은 소리 파일이라 카드에 넣지 않는다 (o.picture === false)
        if (o.picture !== false && !card.dataset.thumbed && (view.thumbs[r.id] || (r.thumb && r.thumb_size))) view.thumb(r, card);
        const html = view.info(r, g);
        if (view.shown[id] !== html) { card.querySelector(".g3-info").innerHTML = html; view.shown[id] = html; }
        prev = card;
      }
      grid.querySelectorAll(".g3-card").forEach((c) => { if (!seen.has(c.dataset.id)) { c.remove(); delete view.shown[c.dataset.id]; } });
      if (!g.runs.length) grid.innerHTML = `<div class="empty"><b>아직 만든 것이 없습니다</b>${o.empty}</div>`;
      if (o.join) {
        if (view.joinPick) view.joinPick = view.joinPick.filter((id) => g.runs.some((r) => r.id === id));   // 지운 항목은 뺀다
        view.joinBar();
      }
    },

    info(r, g) {
      const [label, tone] = GEN_STATE[r.status] || ["알 수 없음", ""];
      const noFile = r.status === "succeeded" && r.output_size == null;
      const done = r.status === "succeeded" && !noFile;
      const live = r.status === "queued" || r.status === "running";
      const badge = noFile ? `<span class="badge bad">결과 없음</span>`
        : `<span class="badge ${tone}">${label}${r.status === "failed" && r.exit_code != null ? ` (${r.exit_code})` : ""}</span>`;
      let detail, progress = "";
      if (r.status === "queued") detail = `<span class="reason">${esc(r.wait_reason || "차례를 기다리는 중")}</span>, ${dur(now() - r.created_at)} 기다림`;
      else if (r.status === "running") {
        const p = r.progress, spent = dur(now() - (r.started_at || r.created_at));
        detail = p ? `${esc(p.stage)} (${p.step}/${p.steps}단계${p.percent != null ? `, ${p.percent}%` : ""}), ${spent} 지남` : `실행 중, ${spent} 지남`;
        if (p) progress = bar(((p.step - 1 + (p.percent ?? 50) / 100) / p.steps) * 100, "ok");
      } else if (noFile) detail = "작업은 끝났지만 결과 파일이 없습니다. 로그를 확인하세요.";
      else if (done) detail = `${fmtBytes(r.output_size)}, ${dur(r.finished_at - r.started_at)} 걸림, ${ago(r.finished_at)}`;
      else if (r.status === "failed") detail = `<span style="color:var(--bad)">${esc(r.error || "실행 중 오류가 났습니다. 로그를 확인하세요.")}</span>`;
      else if (r.status === "cancelled") detail = "취소한 작업입니다";
      else detail = "작업 기록을 찾지 못했습니다";
      const n = genChanged(g, r);
      const at = view.joinPick ? view.joinPick.indexOf(r.id) : -1;
      const pick = view.joinPick && done ? `<button class="btn sm ${at >= 0 ? "primary" : ""} gen-pick-btn" data-act="pick" aria-pressed="${at >= 0}">${at >= 0 ? `${at + 1}번째로 고름` : "고르기"}</button>` : "";
      const joined = r.params?.joined;
      return `<b class="g3-title" title="${esc(r.title)}">${esc(r.title)}</b>
        <div class="g3-state">${badge}<span class="muted">${esc(o.sub(r))}${n ? `, 상세 설정 ${n}개 바꿈` : ""}</span></div>
        <div class="g3-detail">${detail}</div>${progress}
        <div class="g3-actions">${pick ? `${pick}<button class="btn sm" data-act="view">${icon(o.icon)}${o.view}</button>` : `
          ${done ? `<button class="btn sm primary" data-act="view">${icon(o.icon)}${o.view}</button><button class="icon-btn" data-act="dl" title="내려받기">${icon("download")}</button>` : ""}
          ${joined ? "" : `<button class="icon-btn" data-act="again" title="이 글과 설정을 양식에 다시 채우기">${icon("redo")}</button>`}
          ${r.job_id ? `<button class="btn sm ghost" data-act="log">로그</button>` : ""}
          ${live ? `<button class="btn sm ghost danger" data-act="cancel">취소</button>` : `<button class="icon-btn" data-act="del" title="목록과 파일에서 지우기">${icon("trash")}</button>`}`}
        </div>`;
    },

    async thumb(r, card) {
      card.dataset.thumbed = "1";
      try {
        let url = view.thumbs[r.id];
        if (!url) url = view.thumbs[r.id] = homeFile(r.thumb).then((res) => res.blob()).then((b) => URL.createObjectURL(b));
        url = await url;
        card.querySelector(".g3-thumb").innerHTML = `<img src="${url}" alt="">`;
      } catch { delete view.thumbs[r.id]; delete card.dataset.thumbed; }      // 못 받으면 아이콘을 두고 다음에 다시 해 본다
    },

    async act(what, r) {
      const name = r.output.split("/").pop();
      if (what === "view") return o.open(r, view);
      if (what === "log") return go("jobs", r.job_id);
      if (what === "again") return view.again(r);
      if (what === "pick") {
        const ids = view.joinPick || [];
        return view.pickJoin(ids.includes(r.id) ? ids.filter((x) => x !== r.id) : [...ids, r.id]);
      }
      try {
        if (what === "dl") {
          toast(`'${name}'을 받는 중입니다`);
          saveBlob(await (await homeFile(r.output)).blob(), name);
        } else if (what === "cancel") {
          if (!(await confirmDialog(`'${r.title}' 만들기를 취소할까요?`, { ok: "작업 취소", danger: true }))) return;
          await api(`/api/jobs/${r.job_id}/cancel`, { method: "POST" });
          toast("작업을 취소했습니다");
          await view.refresh();
        } else if (what === "del") {
          if (!(await confirmDialog(`'${r.title}'을 목록에서 지우고, 올린 파일과 결과 파일(${name})도 지울까요? 되돌릴 수 없습니다.`, { ok: "지우기", danger: true }))) return;
          await api(`/api/gen/${kind}/${r.id}`, { method: "DELETE" });
          toast("지웠습니다");
          await view.refresh();
        }
      } catch (e) { fail(e); }
    },
  };
  return view;
}

// 설정 묶음의 이름을 묻는 작은 창. 취소하면 null
function genAskName(suggested) {
  return new Promise((done) => {
    openDrawer({
      title: "지금 설정 저장", sub: "조절 값(시드 포함)을 이름을 붙여 저장합니다. 글과 올린 파일은 저장하지 않습니다.", width: 420,
      body: `<div class="field"><label for="gen-preset-name">이름</label><input class="input" id="gen-preset-name" maxlength="40" value="${esc(suggested)}" placeholder="예) 게임용 가벼운 메시">
        <span class="hint">같은 이름이 있으면 덮어씁니다. 40자까지.</span></div>`,
      foot: `<button class="btn" data-drawer-close>취소</button><button class="btn primary" id="gen-preset-ok">저장</button>`,
      onClose: () => done(null),
    });
    const input = $("#gen-preset-name");
    const ok = () => {
      const name = input.value.trim().replace(/\s+/g, " ");
      if (!name) { toast("이름을 적어 주세요", true); return; }
      closeDrawer(true);
      done(name);
    };
    $("#gen-preset-ok").onclick = ok;
    input.onkeydown = (e) => { if (e.key === "Enter") ok(); };
  });
}

// 큰 파일을 받으면서 받은 양을 알려 준다
async function homeBytes(rel, onProgress) {
  const res = await homeFile(rel);
  const total = Number(res.headers.get("content-length")) || 0;
  if (!res.body) return res.arrayBuffer();
  const parts = [], reader = res.body.getReader();
  let got = 0;
  for (;;) {
    const { done, value } = await reader.read();
    if (done) break;
    parts.push(value);
    got += value.length;
    onProgress(got, total);
  }
  const out = new Uint8Array(got);
  let at = 0;
  for (const p of parts) { out.set(p, at); at += p.length; }
  return out.buffer;
}

// ── 3D: 사진 한 장(또는 여러 방향의 여러 장) → GLB ──
const G3_VIEW_NAME = { front: "앞", right: "오른쪽", back: "뒤", left: "왼쪽", top: "위", bottom: "아래" };
const g3Views = (r) => String(r.params.views || "").split(",").filter(Boolean).map((v) => G3_VIEW_NAME[v] || v);
const Gen3d = genPage("3d", {
  page: "gen3d", title: "3D 생성", icon: "cube", go: "3D 만들기", view: "미리보기",
  desc: "사진으로 3D 모델 파일(GLB)을 만듭니다. 한 장으로도 되고, 같은 물체를 여러 방향에서 찍은 사진을 함께 넣으면 그 사진들을 모두 보고 만듭니다. 상세 설정에서 단계마다 값을 바꿀 수 있습니다.",
  empty: '위에서 사진을 고르고 "3D 만들기"를 누르면 여기에 나타납니다.',
  sub: (r) => `시드 ${r.params.seed}${g3Views(r).length ? `, 사진 ${g3Views(r).length}장 (${g3Views(r).join("·")})` : ""}`,

  async open(r, view) {
    let viewer = null, dead = false;
    openDrawer({
      title: esc(r.title), sub: "끌어서 돌리고, 휠로 확대합니다. 두 번 누르면 처음 시점으로 돌아갑니다.", width: 820,
      body: `<div class="g3-view"><canvas id="g3-canvas"></canvas><div class="g3-msg" id="g3-msg">모델을 받는 중…</div>
          <div class="seg g3-mode hidden" id="g3-mode"><button data-m="0" class="on">색</button><button data-m="1">형상만</button></div></div>
        <dl class="dl" id="g3-facts" style="margin:14px 0"></dl>
        ${r.seen?.length ? `<div class="section-title">모델이 받은 사진</div>
          <div class="gv-seen" id="g3-seen">${r.seen.map((p) => `<figure><div class="gv-seen-img" data-seen="${esc(p)}"></div>
            <figcaption>${esc(G3_VIEW_NAME[(p.match(/_([a-z]+)\.png$/) || [])[1]] || "")}</figcaption></figure>`).join("")}</div>
          <p class="hint" style="margin:6px 0 0">배경을 지우고 크기를 맞춘 뒤 모델이 본 그대로입니다 (체크무늬가 지운 배경). 물체가 아닌 것이 남았거나 물체가 잘렸으면 결과가 틀어집니다.
            그럴 때는 배경이 단순한 곳에서 다시 찍거나, 배경을 미리 지운 PNG 를 넣으세요.</p>` : ""}
        ${genFacts(view.state(), r)}`,
      foot: `<button class="btn" id="g3-log">작업 로그</button><button class="btn" id="g3-again">${icon("redo")}이 설정으로 다시</button><button class="btn primary" id="g3-dl" disabled>${icon("download")}GLB 내려받기</button>`,
      dispose: () => { dead = true; viewer?.dispose(); },
    });
    $("#g3-log").onclick = () => go("jobs", r.job_id);
    $("#g3-again").onclick = () => view.again(r);
    const seenUrls = [];
    $$("#g3-seen [data-seen]").forEach(async (box) => {
      try {
        const u = URL.createObjectURL(await (await homeFile(box.dataset.seen)).blob());
        seenUrls.push(u);
        if (!dead) box.innerHTML = `<img src="${u}" alt="">`;
      } catch { box.textContent = "못 읽음"; }
    });
    const firstDispose = S.drawer.dispose;
    S.drawer.dispose = () => { if (firstDispose) firstDispose(); seenUrls.forEach((u) => URL.revokeObjectURL(u)); };
    const say = (t) => { const m = $("#g3-msg"); if (m && !dead) m.textContent = t; };
    try {
      const buf = await homeBytes(r.output, (got, total) => say(`모델을 받는 중… ${fmtBytes(got)}${total ? " / " + fmtBytes(total) : ""}`));
      if (dead) return;
      $("#g3-dl").disabled = false;
      $("#g3-dl").onclick = () => saveBlob(new Blob([buf], { type: "model/gltf-binary" }), r.output.split("/").pop());
      say("모델을 그리는 중…");
      viewer = await glbViewer($("#g3-canvas"), buf);
      if (dead) { viewer.dispose(); return; }
      $("#g3-msg").remove();
      const seg = $("#g3-mode");
      seg.classList.remove("hidden");
      seg.onclick = (e) => {
        const b = e.target.closest("[data-m]"); if (!b) return;
        seg.querySelectorAll("button").forEach((x) => x.classList.toggle("on", x === b));
        viewer.setMode(Number(b.dataset.m));
      };
      const s = viewer.stats;
      $("#g3-facts").innerHTML = `
        <dt>꼭짓점, 삼각형</dt><dd>${s.vertices.toLocaleString("ko-KR")}개, ${s.triangles.toLocaleString("ko-KR")}개</dd>
        <dt>텍스처</dt><dd>${s.texture ? esc(s.texture) : "없음"}</dd>
        <dt>사진</dt><dd>${g3Views(r).length ? `${g3Views(r).length}장 (${esc(g3Views(r).join(", "))})` : "1장"}</dd>`;
    } catch (e) { say(`미리보기를 열지 못했습니다: ${e.message || e}`); }
  },
});

// ── 이미지: 글 → 그림, 참고 그림 + 글 → 고친 그림 ──
const GenImage = genPage("image", {
  page: "genimage", title: "이미지 생성", icon: "image", go: "그리기", view: "크게 보기",
  desc: "글로 그림을 그립니다. 참고 그림을 함께 넣으면 글대로 그 그림을 고칩니다. 상세 설정에서 단계 수와 시간표, 가이던스를 바꿀 수 있습니다.",
  empty: '위에 그릴 내용을 적고 "그리기"를 누르면 여기에 나타납니다.',
  sub: (r) => `${String(r.params.size || "").replace("x", "×")}, ${r.params.model === "turbo" ? "Turbo" : "본체"}, 시드 ${r.params.seed}${r.params.transparent ? ", 투명" : ""}${r.params.refs ? `, 참고 ${r.params.refs}장` : ""}`,

  async open(r, view) {
    let url = null, dead = false;
    openDrawer({
      title: "그림", sub: esc(this.sub(r)), width: 860,
      body: `<div class="gi-view" id="gi-view"><div class="g3-msg" id="gi-msg">그림을 받는 중…</div></div>${genFacts(view.state(), r)}`,
      foot: `<button class="btn" id="gi-log">작업 로그</button><button class="btn" id="gi-again">${icon("redo")}이 설정으로 다시</button><button class="btn primary" id="gi-dl" disabled>${icon("download")}그림 내려받기</button>`,
      dispose: () => { dead = true; if (url) URL.revokeObjectURL(url); },
    });
    $("#gi-log").onclick = () => go("jobs", r.job_id);
    $("#gi-again").onclick = () => view.again(r);
    try {
      const blob = await (await homeFile(r.output)).blob();
      if (dead) return;
      url = URL.createObjectURL(blob);
      $("#gi-view").innerHTML = `<img src="${url}" alt="">`;
      $("#gi-dl").disabled = false;
      $("#gi-dl").onclick = () => saveBlob(blob, r.output.split("/").pop());
    } catch (e) { const m = $("#gi-msg"); if (m && !dead) m.textContent = `그림을 열지 못했습니다: ${e.message || e}`; }
  },
});

// ── 여러 방향 그림: 사진 한 장 → 물체를 여러 방향에서 본 그림 (MV-Adapter) ──
const GMV_VIEWS = [["front", "앞"], ["front-right", "오른쪽 앞 45°"], ["right", "오른쪽"], ["back", "뒤"], ["left", "왼쪽"], ["front-left", "왼쪽 앞 315°"]];
const GenMv = genPage("multiview", {
  page: "genmv", title: "여러 방향 그림", icon: "views", go: "그리기", view: "크게 보기",
  desc: "사진 한 장에서 물체를 여러 방향(6 또는 4)에서 본 그림을 한 번에 함께 그립니다. 방향마다 따로 그리지 않아 무늬와 모양이 서로 맞습니다. "
    + "3D 로 만들려면 노드 편집기의 본보기 '사진 한 장 → 3D (모양 먼저)'를 먼저 쓰세요. 이 그림들로 바로 만드는 본보기도 있지만, "
    + "사진의 자세가 정면·눈높이가 아니면 그림의 모양부터 어긋나 몸이 두 개로 겹친 3D 가 나오기도 합니다.",
  empty: '위에 사진을 넣고 "그리기"를 누르면 여기에 나타납니다.',
  sub: (r) => `${r.params.views || 6}방향, 시드 ${r.params.seed}${r.texts?.prompt ? `, '${String(r.texts.prompt).slice(0, 30)}'` : ""}`,

  async open(r, view) {
    let url = null, dead = false;
    const ex = r.extras || {};
    const views = GMV_VIEWS.filter(([k]) => ex[`.${k}.png`]);
    openDrawer({
      title: "여러 방향 그림", sub: esc(this.sub(r)), width: 900,
      body: `<div class="gi-view" id="gmv-view"><div class="g3-msg" id="gmv-msg">그림을 받는 중…</div></div>
        <p class="muted">방향마다 한 장씩 내려받을 수 있습니다. 묶음 파일(.views.json)은 3D 만들기가 각 그림의 각도와 틀을 읽는 파일입니다.</p>
        <div class="fn-links">${views.map(([k, l]) => `<button class="link" data-mv="${k}">${esc(l)}</button>`).join("")}${ex[".views.json"] ? `<button class="link" data-mv="bundle">묶음 파일</button>` : ""}</div>
        ${genFacts(view.state(), r)}`,
      foot: `<button class="btn" id="gmv-log">작업 로그</button><button class="btn" id="gmv-again">${icon("redo")}이 설정으로 다시</button><button class="btn primary" id="gmv-dl" disabled>${icon("download")}모은 그림 내려받기</button>`,
      dispose: () => { dead = true; if (url) URL.revokeObjectURL(url); },
    });
    $("#gmv-log").onclick = () => go("jobs", r.job_id);
    $("#gmv-again").onclick = () => view.again(r);
    $$("[data-mv]").forEach((b) => { b.onclick = async () => {
      const rel = b.dataset.mv === "bundle" ? ex[".views.json"] : ex[`.${b.dataset.mv}.png`];
      try { saveBlob(await (await homeFile(rel)).blob(), rel.split("/").pop()); } catch (e) { toast(`내려받지 못했습니다: ${e.message || e}`, true); }
    }; });
    try {
      const blob = await (await homeFile(r.output)).blob();
      if (dead) return;
      url = URL.createObjectURL(blob);
      $("#gmv-view").innerHTML = `<img src="${url}" alt="">`;
      $("#gmv-dl").disabled = false;
      $("#gmv-dl").onclick = () => saveBlob(blob, r.output.split("/").pop());
    } catch (e) { const m = $("#gmv-msg"); if (m && !dead) m.textContent = `그림을 열지 못했습니다: ${e.message || e}`; }
  },
});

// ── 음악: 분위기 설명 + 가사 → 노래 ──
const GM_COT = { full: "멜로디와 코드", melody: "멜로디만", off: "악보 없이" };
const GenMusic = genPage("music", {
  page: "genmusic", title: "음악 생성", icon: "music", go: "노래 만들기", view: "듣기", picture: false,
  desc: "분위기 설명과 가사로 노래와 반주가 든 곡을 만듭니다. 모델은 악보를 먼저 쓰고 그 악보대로 소리를 만듭니다. 만든 곡의 악보를 고쳐 다시 만들 수 있습니다.",
  empty: '위에 분위기와 가사를 적고 "노래 만들기"를 누르면 여기에 나타납니다.',
  sub: (r) => `악보 ${GM_COT[r.params.cot] || r.params.cot}, 시드 ${r.params.seed}${r.texts?.abc ? ", 준 악보 사용" : ""}`,

  async open(r, view) {
    let url = null, dead = false, score = null;
    const light = r.thumb && r.thumb_size ? r.thumb : null;         // 들어 보기 쉬운 mp3 사본. 없으면 원본(flac)을 튼다
    const scorePath = r.extras?.[".abc"];
    openDrawer({
      title: esc(shortTitle(r.title)), sub: esc(this.sub(r)), width: 760,
      body: `<div class="gm-player"><audio id="gm-audio" controls preload="metadata"></audio><div class="gm-msg muted" id="gm-msg">곡을 받는 중…</div></div>
        ${scorePath ? `<div class="section-title">악보 <span class="muted" style="font-weight:400">모델이 소리를 만들기 전에 쓴 멜로디와 코드 (ABC 표기). Q: 는 빠르기, K: 는 조, 따옴표 안은 코드입니다.</span></div>
          <pre class="gm-score" id="gm-score">악보를 받는 중…</pre>
          <div class="gm-score-actions"><button class="btn sm" id="gm-edit" disabled>${icon("redo")}이 악보를 고쳐서 다시 만들기</button><button class="btn sm" id="gm-copy" disabled>악보 복사</button></div>` : ""}
        ${genFacts(view.state(), r)}`,
      foot: `<button class="btn" id="gm-log">작업 로그</button><button class="btn" id="gm-again">${icon("redo")}이 설정으로 다시</button>${polishButton("gm-polish")}
        ${light ? `<button class="btn" id="gm-dl-light" disabled>${icon("download")}mp3</button>` : ""}<button class="btn primary" id="gm-dl">${icon("download")}원본(flac) 내려받기</button>`,
      dispose: () => { dead = true; $("#gm-audio")?.pause(); if (url) URL.revokeObjectURL(url); },
    });
    $("#gm-log").onclick = () => go("jobs", r.job_id);
    $("#gm-again").onclick = () => view.again(r);
    $("#gm-polish").onclick = () => sendToPolish(r.output, "song", scorePath);
    $("#gm-dl").onclick = async () => {
      const name = r.output.split("/").pop();
      toast(`'${name}'을 받는 중입니다`);
      try { saveBlob(await (await homeFile(r.output)).blob(), name); } catch (e) { fail(e); }
    };
    if (scorePath) {
      homeFile(scorePath).then((res) => res.text()).then((text) => {
        if (dead) return;
        score = text;
        $("#gm-score").textContent = text;
        $("#gm-edit").disabled = $("#gm-copy").disabled = false;
        $("#gm-copy").onclick = async () => { await copyText(text); toast("악보를 복사했습니다"); };
        // 악보를 "악보 직접 주기" 칸에 넣고 나머지 글과 설정도 채운다. 빠르기(Q:)나 코드를 고쳐 다시 만든다
        $("#gm-edit").onclick = async () => {
          await view.again(r, { abc: score });
          const box = $("#gt-abc");
          if (box) { box.focus(); box.scrollIntoView({ behavior: "smooth", block: "center" }); }
        };
      }).catch((e) => { if (!dead) $("#gm-score").textContent = `악보를 열지 못했습니다: ${e.message || e}`; });
    }
    try {
      const blob = await (await homeFile(light || r.output)).blob();
      if (dead) return;
      url = URL.createObjectURL(blob);
      const audio = $("#gm-audio");
      audio.src = url;
      $("#gm-msg").remove();
      if (light) {
        $("#gm-dl-light").disabled = false;
        $("#gm-dl-light").onclick = () => saveBlob(blob, light.split("/").pop());
      }
    } catch (e) { const m = $("#gm-msg"); if (m && !dead) m.textContent = `곡을 열지 못했습니다: ${e.message || e}`; }
  },
});

// 홈의 파일을 종류를 붙인 주소로 받는다. 받은 응답의 종류가 비어 있어도 <video>·<audio> 가 틀 수 있게 한다
async function homeMedia(rel, type) {
  const blob = await (await homeFile(rel)).blob();
  return { blob, url: URL.createObjectURL(blob.type && blob.type !== "application/octet-stream" ? blob : new Blob([blob], { type })) };
}

// 만든 결과(곡·목소리·효과음·영상)를 '소리 다듬기' 양식에 넣고 그 화면으로 간다. 곡이면 악보도 넣어 메아리를 박자에 맞춘다
const polishButton = (id) => `<button class="btn" id="${id}" title="이 결과를 '소리 다듬기' 양식에 넣습니다">${icon("sliders")}소리 다듬기로 보내기</button>`;
async function sendToPolish(rel, source, scoreRel = null) {
  try {
    toast("소리 다듬기 양식에 넣는 중입니다");
    const blob = await (await homeFile(rel)).blob();
    let score = "";
    if (scoreRel) { try { score = await (await homeFile(scoreRel)).text(); } catch { /* 악보가 없으면 빠르기 없이 다듬는다 */ } }
    const name = rel.split("/").pop();
    GenPolish.files = [new File([blob], name, { type: blob.type || (isVideo({ name }) ? "video/mp4" : "audio/flac") })];
    GenPolish.draft = { texts: score ? { score } : {}, params: { source }, count: "1", open: false };
    closeDrawer();
    if (S.page === "genpolish") GenPolish.form(); else go("genpolish");
    toast(`'${name}'을 넣었습니다. 넣은 것이 '${GP_SOURCE[source]}'인지 확인하고 "다듬기"를 누르세요.`);
  } catch (e) { fail(e); }
}

// ── 영상: 장면 설명 (+ 첫·끝 장면 사진) → 소리가 든 영상 ──
const GV_MODEL = { flash: "Flash", base: "본체" };
const GenVideo = genPage("video", {
  page: "genvideo", title: "영상 생성", icon: "video", go: "영상 만들기", view: "보기",
  desc: "장면 설명으로 소리가 든 영상(2~15초, 1초에 24장, 스테레오 소리)을 만듭니다. 첫 장면 사진을 넣으면 그 사진에서 시작하고, 끝 장면까지 넣으면 그 사진에서 끝납니다. 참고 자료(그림·영상·소리)를 넣으면 그 인물·움직임·목소리를 보고 만듭니다. 만든 영상은 '이 영상에서 이어 만들기'로 다음 장면을 잇고, '영상 이어 붙이기'로 하나로 붙입니다.",
  empty: '위에 장면을 적고 "영상 만들기"를 누르면 여기에 나타납니다.',
  join: true,
  sub: (r) => r.params.joined ? `영상 ${r.params.joined.length}개를 이어 붙임`
    : `${GV_MODEL[r.params.model] || r.params.model || ""}${r.params.model ? ", " : ""}${r.params.size === "auto" ? "크기 자동" : String(r.params.size || "").replace("x", "×")}, ${r.params.seconds}초, 시드 ${r.params.seed}${r.params.refs ? `, 장면 사진 ${r.params.refs}장` : ""}${r.params.after != null ? ", 앞 영상에서 이어 만듦" : ""}${r.params.references ? `, 참고 자료 ${Object.entries(r.params.references).filter(([, n]) => n).map(([k, n]) => `${{ image: "그림", video: "영상", audio: "소리" }[k]} ${n}`).join("·")}` : ""}`,

  async open(r, view) {
    let media = null, dead = false;
    const last = r.extras?.[".last.png"];
    const chain = view.chainOf(r);
    openDrawer({
      title: esc(shortTitle(r.title)), sub: esc(this.sub(r)), width: 900,
      body: `<div class="gv-view" id="gv-view"><div class="g3-msg" id="gv-msg">영상을 받는 중…</div></div>${genFacts(view.state(), r)}`,
      foot: `<button class="btn" id="gv-log">작업 로그</button>${r.params.joined ? "" : `<button class="btn" id="gv-again">${icon("redo")}이 설정으로 다시</button>`}
        ${chain.length > 1 ? `<button class="btn" id="gv-chain" title="끝 장면에서 이어 만든 앞 영상들과 이 영상을 차례대로 고릅니다">${icon("link")}앞 영상 ${chain.length - 1}개와 이어 붙이기</button>` : ""}
        ${last ? `<button class="btn" id="gv-next" title="이 영상의 마지막 장면 사진을 다음 영상의 첫 장면으로 넣습니다">${icon("play")}끝 장면에서 이어 만들기</button>` : ""}
        ${view.state().refs ? `<button class="btn" id="gv-cont" title="이 영상을 참고 영상으로 넣어, 인물·장소·움직임이 이어지는 다음 영상을 만듭니다 (Ref2VA)">${icon("video")}이 영상에서 이어 만들기</button>` : ""}
        ${polishButton("gv-polish")}<button class="btn primary" id="gv-dl" disabled>${icon("download")}MP4 내려받기</button>`,
      dispose: () => { dead = true; $("#gv-view video")?.pause(); if (media) URL.revokeObjectURL(media.url); },
    });
    $("#gv-log").onclick = () => go("jobs", r.job_id);
    if ($("#gv-again")) $("#gv-again").onclick = () => view.again(r);
    $("#gv-polish").onclick = () => sendToPolish(r.output, "song");     // 영상의 소리는 대사와 배경 소리로 나눠 다듬는다
    if ($("#gv-chain")) $("#gv-chain").onclick = () => {
      closeDrawer();
      view.pickJoin(chain.map((x) => x.id));
      $("#gen-joinbar")?.scrollIntoView({ behavior: "smooth", block: "center" });
      toast(`이어 만든 차례대로 영상 ${chain.length}개를 골라 두었습니다. 확인한 뒤 '이어 붙이기'를 누르세요.`);
    };
    // 이 영상 자체를 참고 영상으로 넣는다 (쓰임새: 이어 만든다). 끝 장면 사진 한 장보다 움직임·소리까지 이어진다
    if ($("#gv-cont")) $("#gv-cont").onclick = async () => {
      try {
        const blob = await (await homeFile(r.output)).blob();
        if (r.params.joined) { view.draft = { texts: {}, params: {}, count: "1", open: false }; closeDrawer(); }
        else await view.again(r, {}, true);
        const file = new File([blob], "이어 만들 영상.mp4", { type: "video/mp4" });
        view.files = [];
        view.refs = [file];
        view.draft.params = { ...view.draft.params, ref_video_role: "continue", size: "auto" };
        view.after = { id: r.id, file };
        view.form();
        $("#gen-form").scrollIntoView({ behavior: "smooth", block: "start" });
        toast("이 영상을 참고 영상(<Video 1>)으로 넣고 쓰임새를 '이어 만든다'로 맞췄습니다. 장면 글을 다음에 일어날 일로 고친 뒤 만드세요.");
      } catch (e) { fail(e); }
    };
    if (last) {
      // 이 영상의 마지막 장면을 첫 장면 칸에 넣고, 글과 설정도 채운다. 장면 설명을 다음 이야기로 고쳐 만든다
      $("#gv-next").onclick = async () => {
        try {
          const blob = await (await homeFile(last)).blob();
          if (r.params.joined) { view.draft = { texts: {}, params: {}, count: "1", open: false }; closeDrawer(); view.form(); }
          else await view.again(r, {}, true);
          const file = new File([blob], "이전 영상의 끝 장면.png", { type: "image/png" });
          view.files = [file];
          view.refs = [];                        // 첫 장면 사진과 참고 자료는 함께 쓸 수 없다
          view.after = { id: r.id, file };      // 만들 때 앞 영상 번호를 함께 보내, 나중에 차례대로 이어 붙일 수 있게 한다
          view.showFiles();
          view.showRefs();
          toast("이 영상의 끝 장면을 첫 장면으로 넣었습니다. 장면 설명을 다음 이야기로 고친 뒤 만드세요.");
        } catch (e) { fail(e); }
      };
    }
    try {
      media = await homeMedia(r.output, "video/mp4");
      if (dead) return;
      $("#gv-view").innerHTML = `<video src="${media.url}" controls playsinline preload="metadata"></video>`;
      $("#gv-dl").disabled = false;
      $("#gv-dl").onclick = () => saveBlob(media.blob, r.output.split("/").pop());
    } catch (e) { const m = $("#gv-msg"); if (m && !dead) m.textContent = `영상을 열지 못했습니다: ${e.message || e}`; }
  },
});

// ── 음성: 대본 (+ 참고 목소리) → 목소리 ──
const GenVoice = genPage("voice", {
  page: "genvoice", title: "음성 생성", icon: "mic", go: "목소리 만들기", view: "듣기", picture: false,
  desc: "대본을 목소리로 읽습니다. 여러 사람이 나오면 줄마다 '화자1:', '화자2:' 를 붙입니다. 참고 목소리 녹음과 그 녹음에서 하는 말을 함께 넣으면 그 목소리를 본뜹니다. 한국어를 포함해 80여 개 언어를 읽습니다.",
  empty: '위에 대본을 적고 "목소리 만들기"를 누르면 여기에 나타납니다.',
  sub: (r) => `${r.params.refs ? `참고 목소리 ${r.params.refs}개` : "기본 목소리"}, 시드 ${r.params.seed}`,

  async open(r, view) {
    let media = null, dead = false;
    const light = r.thumb && r.thumb_size ? r.thumb : null;         // 들어 보기 쉬운 mp3 사본. 없으면 원본(wav)을 튼다
    openDrawer({
      title: esc(shortTitle(r.title)), sub: esc(this.sub(r)), width: 760,
      body: `<div class="gm-player"><audio id="gv-audio" controls preload="metadata"></audio><div class="gm-msg muted" id="gv-msg">목소리를 받는 중…</div></div>
        ${genFacts(view.state(), r)}`,
      foot: `<button class="btn" id="gv-log">작업 로그</button><button class="btn" id="gv-again">${icon("redo")}이 설정으로 다시</button>${polishButton("gv-polish")}
        ${light ? `<button class="btn" id="gv-dl-light" disabled>${icon("download")}mp3</button>` : ""}<button class="btn primary" id="gv-dl">${icon("download")}원본(wav) 내려받기</button>`,
      dispose: () => { dead = true; $("#gv-audio")?.pause(); if (media) URL.revokeObjectURL(media.url); },
    });
    $("#gv-log").onclick = () => go("jobs", r.job_id);
    $("#gv-again").onclick = () => view.again(r);
    $("#gv-polish").onclick = () => sendToPolish(r.output, "voice");
    $("#gv-dl").onclick = async () => {
      const name = r.output.split("/").pop();
      toast(`'${name}'을 받는 중입니다`);
      try { saveBlob(await (await homeFile(r.output)).blob(), name); } catch (e) { fail(e); }
    };
    try {
      media = await homeMedia(light || r.output, light ? "audio/mpeg" : "audio/wav");
      if (dead) return;
      $("#gv-audio").src = media.url;
      $("#gv-msg").remove();
      if (light) {
        $("#gv-dl-light").disabled = false;
        $("#gv-dl-light").onclick = () => saveBlob(media.blob, light.split("/").pop());
      }
    } catch (e) { const m = $("#gv-msg"); if (m && !dead) m.textContent = `목소리를 열지 못했습니다: ${e.message || e}`; }
  },
});

// ── 효과음: 소리 설명(영어) 또는 영상 → 효과음 ──
const GS_MODEL = { fast: "빠른 판", full: "본판" };
const GenSfx = genPage("sfx", {
  page: "gensfx", title: "효과음 생성", icon: "wave", go: "효과음 만들기", view: "듣기", picture: false,
  desc: "영어로 적은 소리 설명대로 효과음(5초까지, 48kHz 모노 WAV)을 만듭니다. 영상을 넣으면 영상의 움직임에 맞춘 소리(8초까지)를 만들고 그 소리를 붙인 영상도 만듭니다. "
    + "게임에 넣기 좋게 앞뒤 무음 자르기, 끝 줄이기, 크기 맞추기, 이음매 없이 되풀이되는 배경 소리 만들기를 함께 합니다. 모델은 Sony AI 의 Woosh 이고, 만든 소리는 비상업 용도로만 씁니다.",
  empty: '위에 소리 설명을 영어로 적고 "효과음 만들기"를 누르면 여기에 나타납니다.',
  sub: (r) => {
    const video = (r.inputs || []).some((x) => /\.(mp4|mov|webm|mkv)$/i.test(x));
    const len = r.params.seconds != null ? `${r.params.seconds}초` : video ? "영상 길이" : "5초";
    return `${GS_MODEL[r.params.model] || r.params.model || ""}, ${len}${r.params.loop ? ", 되풀이 소리" : ""}${video ? ", 영상에 맞춤" : ""}, 시드 ${r.params.seed}`;
  },

  async open(r, view) {
    let media = null, clip = null, dead = false;
    const light = r.thumb && r.thumb_size ? r.thumb : null;         // 들어 보기 쉬운 mp3 사본. 없으면 원본(wav)을 튼다
    const mp4 = r.extras?.[".mp4"];
    openDrawer({
      title: esc(shortTitle(r.title)), sub: esc(this.sub(r)), width: 760,
      body: `${mp4 ? `<div class="gv-view gs-view"><video id="gs-video" controls playsinline preload="metadata"></video></div>` : ""}
        <div class="gm-player"><audio id="gs-audio" controls preload="metadata" ${r.params.loop ? "loop" : ""}></audio><div class="gm-msg muted" id="gs-msg">소리를 받는 중…</div>
          <label class="check gs-loop"><input type="checkbox" id="gs-loop" ${r.params.loop ? "checked" : ""}><span>끝나면 처음부터 다시 틀기 <span class="muted">(되풀이 소리의 이음매를 들어 볼 때)</span></span></label></div>
        ${genFacts(view.state(), r)}`,
      foot: `<button class="btn" id="gs-log">작업 로그</button><button class="btn" id="gs-again">${icon("redo")}이 설정으로 다시</button>${polishButton("gs-polish")}
        ${mp4 ? `<button class="btn" id="gs-dl-video">${icon("download")}영상(mp4)</button>` : ""}
        ${light ? `<button class="btn" id="gs-dl-light" disabled>${icon("download")}mp3</button>` : ""}<button class="btn primary" id="gs-dl">${icon("download")}원본(wav) 내려받기</button>`,
      dispose: () => {
        dead = true; $("#gs-audio")?.pause(); $("#gs-video")?.pause();
        if (media) URL.revokeObjectURL(media.url);
        if (clip) URL.revokeObjectURL(clip.url);
      },
    });
    $("#gs-log").onclick = () => go("jobs", r.job_id);
    $("#gs-again").onclick = () => view.again(r);
    $("#gs-polish").onclick = () => sendToPolish(r.output, "sfx");
    $("#gs-loop").onchange = (e) => { $("#gs-audio").loop = e.target.checked; };
    $("#gs-dl").onclick = async () => {
      const name = r.output.split("/").pop();
      toast(`'${name}'을 받는 중입니다`);
      try { saveBlob(await (await homeFile(r.output)).blob(), name); } catch (e) { fail(e); }
    };
    if (mp4) {
      $("#gs-dl-video").onclick = async () => {
        try { saveBlob(clip ? clip.blob : await (await homeFile(mp4)).blob(), mp4.split("/").pop()); } catch (e) { fail(e); }
      };
      homeMedia(mp4, "video/mp4").then((m) => { if (dead) { URL.revokeObjectURL(m.url); return; } clip = m; $("#gs-video").src = m.url; })
        .catch(() => { const v = $("#gs-video"); if (v && !dead) v.replaceWith(Object.assign(document.createElement("div"), { className: "muted", textContent: "소리를 붙인 영상을 열지 못했습니다." })); });
    }
    try {
      media = await homeMedia(light || r.output, light ? "audio/mpeg" : "audio/wav");
      if (dead) return;
      $("#gs-audio").src = media.url;
      $("#gs-msg").remove();
      if (light) {
        $("#gs-dl-light").disabled = false;
        $("#gs-dl-light").onclick = () => saveBlob(media.blob, light.split("/").pop());
      }
    } catch (e) { const m = $("#gs-msg"); if (m && !dead) m.textContent = `소리를 열지 못했습니다: ${e.message || e}`; }
  },
});

// ── 소리 다듬기: 곡·목소리·효과음·영상의 소리 → 다듬은 소리 ──
const GP_SOURCE = { song: "노래", voice: "목소리만", sfx: "효과음", mix: "곡 전체만" };
const GP_SEP = { htdemucs: "빠른 나누기", htdemucs_ft: "정밀한 나누기" };
const gpNum = (v, unit = "") => (v == null ? "—" : `${Number(v).toLocaleString("ko-KR", { maximumFractionDigits: 2 })}${unit}`);
const gpArrow = (a, b, unit) => `${gpNum(a, unit)} → <b>${gpNum(b, unit)}</b>`;

// 처리 보고(report.json)에서 사람이 볼 줄만 고른다. 숫자는 처리 전과 후를 같은 잣대로 잰 값이다
function gpReport(rep) {
  if (!rep) return "";
  const i = rep.input || {}, o = rep.output || {}, rows = [];
  rows.push(["음량", `${gpArrow(i.lufs, o.lufs, " LUFS")} <span class="muted">귀가 듣는 크기. 클수록(0 에 가까울수록) 크게 들립니다</span>`]);
  rows.push(["가장 큰 곳", `${gpArrow(i.true_peak_db, o.true_peak_db, " dBTP")} <span class="muted">표본 사이 봉우리까지 잰 값</span>`]);
  const vi = rep.source === "song" ? rep.vocals_in : i, vo = rep.source === "song" ? rep.vocals_out : o;
  if (vi?.sibilance_burst_db != null && vo?.sibilance_burst_db != null && (rep.source === "song" || rep.source === "voice")) {
    rows.push(["'ㅅ' 소리가 튀는 정도", `${gpArrow(vi.sibilance_burst_db, vo.sibilance_burst_db, " dB")} <span class="muted">${rep.source === "song" ? "나눈 목소리에서, " : ""}쉿 소리(5~9 kHz)가 둘레 0.3초의 소리보다 큰 정도 (상위 5%). 작아질수록 덜 거슬립니다</span>`]);
  }
  if (rep.separation) rows.push(["나누기", `${esc(GP_SEP[rep.separation.model] || rep.separation.model)}, ${gpNum(rep.separation.seconds, "초")}${rep.separation.device === "cpu" ? " (CPU)" : ""}`]);
  if (rep.resonances) rows.push(["깎은 울림", rep.resonances.length ? rep.resonances.map((c) => `${gpNum(Math.round(c.hz), " Hz")} −${gpNum(c.cut_db)} dB`).join(", ") : "튀어나온 곳이 없어 깎지 않음"]);
  const de = rep.deess?.before_compress ? [rep.deess.before_compress, rep.deess.after_compress] : rep.deess && rep.deess.band_hz ? [rep.deess] : [];
  if (de.length) rows.push(["치찰음 줄이기", `${gpNum(de[0].band_hz[0])}~${gpNum(de[0].band_hz[1])} Hz, ${de.map((d) => `소리 나는 시간의 ${gpNum(d.active_share * 100, "%")} 동안 최대 ${gpNum(d.deepest_db, " dB")}`).join(" / ")}`]);
  if (rep.compress?.stages) rows.push(["압축", rep.compress.stages.map((c) => `${esc(c.name)} 평균 ${gpNum(c.mean_gr_db, " dB")}`).join(", ")]);
  if (rep.tempo_bpm || rep.delay?.skipped) rows.push(["빠르기", rep.tempo_bpm ? `1분에 4분음표 ${gpNum(rep.tempo_bpm)}개${rep.delay?.seconds ? `, 메아리 간격 ${gpNum(rep.delay.seconds, "초")}` : ""}` : esc(rep.delay.skipped)]);
  if (rep.master?.note) rows.push(["알림", esc(rep.master.note)]);
  if (rep.times_s) rows.push(["걸린 시간", `${gpNum(rep.times_s.total, "초")}${rep.times_s.separate ? ` (나누기 ${gpNum(rep.times_s.separate, "초")})` : ""}`]);
  return `<div class="section-title">재서 바꾼 것 <span class="muted" style="font-weight:400">처리 전과 후를 같은 잣대로 잰 값입니다. 좋아졌는지는 들어 보고 정하세요.</span></div>
    <dl class="dl">${rows.map(([k, v]) => `<dt>${k}</dt><dd>${v}</dd>`).join("")}</dl>`;
}

const GenPolish = genPage("polish", {
  page: "genpolish", title: "소리 다듬기", icon: "sliders", go: "다듬기", view: "듣기", picture: false,
  desc: "만든 곡·목소리·효과음이나 영상의 소리를 다듬습니다. 노래는 목소리와 반주를 나눠(Demucs) 목소리에만 믹싱 처리를 겁니다: 낮은 웅웅거림 자르기, 거슬리는 울림 깎기, "
    + "'ㅅ' 소리 줄이기, 압축, 따뜻하게, 잔향·메아리. 그다음 반주와 다시 섞어 음량을 맞춥니다. 처리 순서와 기본값은 AI 목소리를 다듬는 일반 믹싱 요령을 따른 것이라, "
    + "결과는 '원래 소리'와 같은 크기로 번갈아 들어 보고 고르세요.",
  empty: '위에 다듬을 파일을 넣고 "다듬기"를 누르면 여기에 나타납니다. 음악·음성·효과음·영상 생성 결과의 "소리 다듬기로 보내기"로도 넣을 수 있습니다.',
  sub: (r) => `${GP_SOURCE[r.params.source] || r.params.source || ""}${r.params.source === "song" ? `, ${GP_SEP[r.params.separator] || r.params.separator}` : ""}`
    + `${r.params.loudness != null ? `, ${r.params.loudness} LUFS` : ""}${r.params.keep_loudness ? ", 음량 그대로" : ""}`,

  async open(r, view) {
    const urls = [];
    let dead = false, rep = null, which = "after", clip = null;
    const light = r.thumb && r.thumb_size ? r.thumb : null;         // 들어 보기 쉬운 mp3 사본
    const ex = r.extras || {};
    const original = (r.inputs || []).find((x) => /-1\.[a-z0-9]+$/i.test(x));
    const tracks = {                                                  // 단추 이름 → 틀 파일
      after: { label: "다듬은 소리", rel: light || r.output },
      before: original ? { label: "원래 소리", rel: original } : null,
      vocals: ex[".vocals.flac"] ? { label: "목소리만", rel: ex[".vocals.flac"] } : null,
      backing: ex[".backing.flac"] ? { label: "반주만", rel: ex[".backing.flac"] } : null,
    };
    const loaded = {};
    openDrawer({
      title: esc(shortTitle(r.title)), sub: esc(this.sub(r)), width: 820,
      body: `${ex[".mp4"] ? `<div class="gv-view gs-view"><video id="gp-video" controls playsinline preload="metadata"></video></div>` : ""}
        <div class="gm-player">
          <div class="seg gp-ab" id="gp-ab">${Object.entries(tracks).filter(([, t]) => t).map(([k, t]) => `<button class="${k === "after" ? "on" : ""}" data-ab="${k}">${t.label}</button>`).join("")}</div>
          <audio id="gp-audio" controls preload="metadata"></audio><div class="gm-msg muted" id="gp-msg">소리를 받는 중…</div>
          <label class="check"><input type="checkbox" id="gp-match" checked><span>같은 크기로 비교 <span class="muted" id="gp-match-note">(큰 소리가 더 좋게 들리는 착각을 막으려고, 둘 가운데 큰 쪽을 줄여 틉니다)</span></span></label>
        </div>
        <div id="gp-report"></div>
        ${genFacts(view.state(), r)}`,
      foot: `<button class="btn" id="gp-log">작업 로그</button><button class="btn" id="gp-again">${icon("redo")}이 설정으로 다시</button>
        ${ex[".vocals.flac"] ? `<button class="btn" id="gp-dl-vocals">${icon("download")}목소리만</button>` : ""}
        ${ex[".backing.flac"] ? `<button class="btn" id="gp-dl-backing">${icon("download")}반주만</button>` : ""}
        ${ex[".mp4"] ? `<button class="btn" id="gp-dl-video">${icon("download")}영상(mp4)</button>` : ""}
        ${light ? `<button class="btn" id="gp-dl-light">${icon("download")}mp3</button>` : ""}<button class="btn primary" id="gp-dl">${icon("download")}다듬은 소리(flac)</button>`,
      dispose: () => { dead = true; $("#gp-audio")?.pause(); $("#gp-video")?.pause(); urls.forEach((u) => URL.revokeObjectURL(u)); },
    });
    $("#gp-log").onclick = () => go("jobs", r.job_id);
    $("#gp-again").onclick = () => view.again(r);
    const save = (rel) => async () => {
      const name = rel.split("/").pop();
      toast(`'${name}'을 받는 중입니다`);
      try { saveBlob(await (await homeFile(rel)).blob(), name); } catch (e) { fail(e); }
    };
    $("#gp-dl").onclick = save(r.output);
    if (light) $("#gp-dl-light").onclick = save(light);
    if (ex[".vocals.flac"]) $("#gp-dl-vocals").onclick = save(ex[".vocals.flac"]);
    if (ex[".backing.flac"]) $("#gp-dl-backing").onclick = save(ex[".backing.flac"]);
    if (ex[".mp4"]) {
      $("#gp-dl-video").onclick = save(ex[".mp4"]);
      homeMedia(ex[".mp4"], "video/mp4").then((m) => { urls.push(m.url); if (dead) return; clip = m; $("#gp-video").src = m.url; }).catch(() => {});
    }
    // 같은 크기로 비교: 처리 전후의 음량(LUFS) 차이만큼 큰 쪽의 볼륨을 줄인다 (볼륨은 줄이기만 할 수 있다)
    const volumeFor = (k) => {
      if (!$("#gp-match")?.checked || !rep?.input?.lufs || !rep?.output?.lufs || (k !== "after" && k !== "before")) return 1;
      const diff = rep.output.lufs - rep.input.lufs;              // + 면 다듬은 소리가 크다
      return Math.min(1, Math.pow(10, -(k === "after" ? Math.max(0, diff) : Math.max(0, -diff)) / 20));
    };
    const play = async (k) => {
      const audio = $("#gp-audio"), t = tracks[k];
      if (!audio || !t) return;
      which = k;
      $("#gp-ab").querySelectorAll("button").forEach((b) => b.classList.toggle("on", b.dataset.ab === k));
      const at = audio.currentTime, going = !audio.paused;
      try {
        if (!loaded[k]) {
          const m = await homeMedia(t.rel, /\.mp3$/i.test(t.rel) ? "audio/mpeg" : /\.(mp4|mov|webm|mkv)$/i.test(t.rel) ? "video/mp4" : "audio/flac");
          urls.push(m.url);
          loaded[k] = m.url;
        }
        if (dead || which !== k) return;
        audio.src = loaded[k];
        audio.volume = volumeFor(k);
        audio.addEventListener("loadedmetadata", () => { audio.currentTime = Math.min(at, audio.duration || at); if (going) audio.play().catch(() => {}); }, { once: true });
        $("#gp-msg")?.remove();
      } catch (e) { const m = $("#gp-msg"); if (m && !dead) m.textContent = `${t.label}을 열지 못했습니다: ${e.message || e}`; }
    };
    $("#gp-ab").onclick = (e) => { const b = e.target.closest("[data-ab]"); if (b) play(b.dataset.ab); };
    $("#gp-match").onchange = () => { const a = $("#gp-audio"); if (a) a.volume = volumeFor(which); };
    play("after");
    if (ex[".report.json"]) {
      try {
        rep = JSON.parse(await (await homeFile(ex[".report.json"])).text());
        if (dead) return;
        $("#gp-report").innerHTML = gpReport(rep);
        const a = $("#gp-audio");
        if (a) a.volume = volumeFor(which);
        const diff = rep.output?.lufs != null && rep.input?.lufs != null ? rep.output.lufs - rep.input.lufs : null;
        if (diff != null) $("#gp-match-note").textContent = `(다듬은 소리가 ${Math.abs(diff).toFixed(1)} dB ${diff >= 0 ? "커서 다듬은 소리를" : "작아서 원래 소리를"} 그만큼 줄여 틉니다. 큰 소리가 더 좋게 들리는 착각을 막습니다)`;
      } catch { /* 보고가 없어도 듣기는 된다 */ }
    } else {
      $("#gp-match").closest("label").classList.add("hidden");
    }
  },
});

// ═════════════════════════ 생성 (Advanced): 노드 편집기 ═════════════════════════
// 노드를 놓고 선으로 이어 "글 → 그림 그리기 → 3D 만들기" 같은 그래프를 만든다. 실행하면 서버가 노드 하나를 GPU 작업 하나로
// 큐에 넣고, 앞 노드의 결과 파일을 뒤 노드의 입력으로 넘긴다 (/api/flow). 노드의 종류와 조절 값은 서버 정의에서 읽는다.
// 다시 실행할 때 서버는 고친 노드부터만 돌린다 (앞 노드는 지난 결과를 다시 쓴다).
const FLOW_STATUS = {
  waiting: ["기다림", ""], queued: ["대기", "warn"], running: ["만드는 중", "ok run"], succeeded: ["완성", "info"],
  reused: ["다시 씀", "info"], failed: ["실패", "bad"], cancelled: ["취소됨", ""], skipped: ["건너뜀", ""],
};
const FLOW_COLOR = { text: "#2f6fe4", image: "#18a06a", mesh: "#9b4fd8", audio: "#e0702a", video: "#d6336c", views: "#0e8f9c" };     // 선과 점의 색: 오가는 것의 종류
// 파일을 올려 내는 노드: 노드의 source → 받는 파일 (화면 쪽 검사. 서버도 같은 검사를 한다)
const FLOW_UPLOADS = {
  picture: { ok: isPicture, accept: "image/png,image/jpeg,image/webp", what: "사진", exts: "png, jpg, webp" },
  sound: { ok: isSound, accept: "audio/*,.wav,.mp3,.flac,.ogg,.m4a", what: "소리 파일", exts: "wav, mp3, flac, ogg, m4a" },
  clip: { ok: isVideo, accept: "video/*,.mp4,.mov,.webm,.mkv", what: "영상 파일", exts: "mp4, mov, webm, mkv" },
};
const FLOW_DRAFT = "ironlab_flow";      // 만들던 그래프를 브라우저에 남겨 두는 이름. 새로 고쳐도 이어서 한다
const flowLive = (run) => run && run.status === "running";

// 처음 쓸 때 고를 수 있는 본보기 그래프
const FLOW_EXAMPLES = {
  "글 → 그림 → 3D": {
    nodes: [{ id: "n1", type: "text", x: 30, y: 60, text: "작은 등대 모형, 흰 배경, 물체 하나가 가운데에 크게" },
      { id: "n2", type: "image", x: 330, y: 30, params: { size: "1024x1024" } },
      { id: "n3", type: "mesh", x: 640, y: 60, params: {} }],
    edges: [{ from: "n1", out: "text", to: "n2", in: "prompt" }, { from: "n2", out: "image", to: "n3", in: "image" }],
  },
  "가사 → 악보 → 노래": {
    nodes: [{ id: "n1", type: "text", x: 30, y: 20, text: "K-pop ballad, emotional, piano and strings, soft female vocal, slow tempo" },
      { id: "n2", type: "text", x: 30, y: 220, text: "[Verse]\n창밖에 비가 내리는 밤\n불 꺼진 방 안에 혼자 앉아\n\n[Chorus]\n돌아라 돌아라 밤새도록" },
      { id: "n3", type: "score", x: 340, y: 40, params: {} },
      { id: "n4", type: "song", x: 650, y: 120, params: {} }],
    edges: [{ from: "n1", out: "text", to: "n3", in: "style" }, { from: "n2", out: "text", to: "n3", in: "lyrics" },
      { from: "n1", out: "text", to: "n4", in: "style" }, { from: "n2", out: "text", to: "n4", in: "lyrics" },
      { from: "n3", out: "score", to: "n4", in: "score" }],
  },
  "가사 → 노래 → 소리 다듬기 (악보로 박자 맞춤)": {
    nodes: [{ id: "n1", type: "text", x: 30, y: 20, text: "K-pop ballad, emotional, piano and strings, soft female vocal, slow tempo" },
      { id: "n2", type: "text", x: 30, y: 220, text: "[Verse]\n창밖에 비가 내리는 밤\n불 꺼진 방 안에 혼자 앉아\n\n[Chorus]\n돌아라 돌아라 밤새도록" },
      { id: "n3", type: "song", x: 340, y: 60, params: {} },
      { id: "n4", type: "polish", x: 660, y: 100, params: {} }],
    edges: [{ from: "n1", out: "text", to: "n3", in: "style" }, { from: "n2", out: "text", to: "n3", in: "lyrics" },
      { from: "n3", out: "audio", to: "n4", in: "audio" }, { from: "n3", out: "score", to: "n4", in: "score" }],
  },
  "사진 → 3D 두 가지 (원본, 가벼운 메시)": {
    nodes: [{ id: "n1", type: "picture", x: 30, y: 120 },
      { id: "n2", type: "mesh", x: 340, y: 20, params: {} },
      { id: "n3", type: "mesh", x: 340, y: 300, params: { faces: 100000, texture_size: "2048" } }],
    edges: [{ from: "n1", out: "image", to: "n2", in: "image" }, { from: "n1", out: "image", to: "n3", in: "image" }],
  },
  "여러 방향의 사진 → 3D 하나": {
    nodes: [{ id: "n1", type: "picture", x: 30, y: 10 },
      { id: "n2", type: "picture", x: 30, y: 210 },
      { id: "n3", type: "picture", x: 30, y: 410 },
      { id: "n4", type: "mesh_mv", x: 360, y: 150, params: {} }],
    edges: [{ from: "n1", out: "image", to: "n4", in: "front" }, { from: "n2", out: "image", to: "n4", in: "right" },
      { from: "n3", out: "image", to: "n4", in: "top" }],
  },
  // 사진 한 장 → 3D 는 이 본보기를 먼저 권한다: 모양은 사진 한 장 3D 가 정하고 MV-Adapter 는 색만 칠한다 (2026-11-16 네 물체로 확인)
  "사진 한 장 → 3D (모양 먼저)": {
    nodes: [{ id: "n1", type: "picture", x: 30, y: 60 },
      { id: "n2", type: "text", x: 30, y: 330, text: "a high quality object" },
      { id: "n3", type: "mesh_shape", x: 380, y: 120, params: {} }],
    edges: [{ from: "n1", out: "image", to: "n3", in: "image" }, { from: "n2", out: "text", to: "n3", in: "prompt" }],
  },
  // 배경이 복잡한 사진(건물, 거리)은 그림 그리기로 물체 하나만 남긴 뒤 3D 로 만든다. 건물 사진(2026-11-16): 그대로 넣으면
  // 이웃 건물 벽과 나무가 3D 에 붙었고, 정리한 그림(약 40초)을 넣으면 건물 하나가 사방 창문과 평지붕으로 나왔다
  "배경이 복잡한 사진 → 물체만 남긴 그림 → 3D (모양 먼저)": {
    nodes: [{ id: "n1", type: "picture", x: 30, y: 40 },
      { id: "n2", type: "text", x: 30, y: 300, text: "Isolate the main object in this photo as a single standalone model. Remove everything around it: people, trees, ground, sky and neighboring objects or buildings. Keep its exact design, shape, colors and details. Show the whole object on a plain white background with soft even light." },
      { id: "n3", type: "image", x: 360, y: 40, params: { size: "1024x1024", seed: 1 } },
      { id: "n4", type: "text", x: 360, y: 400, text: "a high quality object" },
      { id: "n5", type: "mesh_shape", x: 690, y: 140, params: {} }],
    edges: [{ from: "n1", out: "image", to: "n3", in: "refs" }, { from: "n2", out: "text", to: "n3", in: "prompt" },
      { from: "n3", out: "image", to: "n5", in: "image" }, { from: "n4", out: "text", to: "n5", in: "prompt" }],
  },
  // 각도 바꾸기 그림 셋으로 3D 를 만들던 본보기는 뺐다: 방향마다 따로 그린 그림은 서로 맞지 않아 얼굴이 두 개인 머리가 나왔다 (2026-11-16)
  "사진 한 장 → 여러 방향 그림 → 3D 하나": {
    nodes: [{ id: "n1", type: "picture", x: 30, y: 60 },
      { id: "n2", type: "text", x: 30, y: 330, text: "a high quality object" },
      { id: "n3", type: "multiview", x: 360, y: 40, params: { views: "6" } },
      { id: "n4", type: "mesh_views", x: 700, y: 120, params: {} }],
    edges: [{ from: "n1", out: "image", to: "n3", in: "image" }, { from: "n2", out: "text", to: "n3", in: "prompt" },
      { from: "n3", out: "views", to: "n4", in: "views" }],
  },
  "글 → 소리 있는 영상": {
    nodes: [{ id: "n1", type: "text", x: 30, y: 20, text: "Live-action, cinematic. A small fishing boat rocks gently in a quiet harbor at dawn. The camera slowly pushes in as an old fisherman (S1) coils a rope and says: <d>[Korean] 오늘은 바람이 좋구나.</d>" },
      { id: "n2", type: "text", x: 30, y: 250, text: "Gentle waves lap against the hull, seagulls cry in the distance, and the rope creaks." },
      { id: "n3", type: "video", x: 360, y: 60, params: { size: "864x480", seconds: 5 } }],
    edges: [{ from: "n1", out: "text", to: "n3", in: "prompt" }, { from: "n2", out: "text", to: "n3", in: "sound" }],
  },
  "글 → 그림 → 영상 (그림이 첫 장면)": {
    nodes: [{ id: "n1", type: "text", x: 30, y: 30, text: "비 오는 밤의 골목, 네온 간판 아래 우산을 든 사람, 영화 같은 조명" },
      { id: "n2", type: "image", x: 330, y: 20, params: { size: "1024x1024" } },
      { id: "n3", type: "text", x: 30, y: 300, text: "Cinematic. The person under the neon sign slowly turns toward the camera as rain keeps falling; the camera pulls out at slow speed." },
      { id: "n4", type: "video", x: 650, y: 120, params: { size: "auto", seconds: 5 } }],
    edges: [{ from: "n1", out: "text", to: "n2", in: "prompt" }, { from: "n2", out: "image", to: "n4", in: "first" },
      { from: "n3", out: "text", to: "n4", in: "prompt" }],
  },
  "영상 두 개 이어 붙이기 (끝 장면 → 다음 첫 장면)": {
    nodes: [{ id: "n1", type: "text", x: 30, y: 20, text: "Cinematic. A paper airplane is thrown from a rooftop and glides over the city at sunset. The camera tracks it from behind." },
      { id: "n2", type: "video", x: 340, y: 20, params: { size: "864x480", seconds: 5 } },
      { id: "n3", type: "text", x: 30, y: 330, text: "Cinematic. The paper airplane keeps gliding and lands softly in a child's open hands in a park; the child laughs." },
      { id: "n4", type: "video", x: 660, y: 180, params: { size: "864x480", seconds: 5 } },
      { id: "n5", type: "video_join", x: 980, y: 60, params: {} }],
    edges: [{ from: "n1", out: "text", to: "n2", in: "prompt" }, { from: "n2", out: "last", to: "n4", in: "first" },
      { from: "n3", out: "text", to: "n4", in: "prompt" },
      { from: "n2", out: "video", to: "n5", in: "v1" }, { from: "n4", out: "video", to: "n5", in: "v2" }],
  },
  "대본 → 목소리 (참고 목소리 본뜨기)": {
    nodes: [{ id: "n1", type: "text", x: 30, y: 20, text: "화자1: 오늘 회의는 여기까지 하죠. 다들 수고 많으셨어요.\n화자2: [laughs] 네, 다음 주에는 꼭 끝내 봅시다!" },
      { id: "n2", type: "sound", x: 30, y: 250 },
      { id: "n3", type: "text", x: 30, y: 400, text: "참고 녹음에서 하는 말을 그대로 적습니다" },
      { id: "n4", type: "voice", x: 360, y: 120, params: {} }],
    edges: [{ from: "n1", out: "text", to: "n4", in: "script" }, { from: "n2", out: "audio", to: "n4", in: "refs" },
      { from: "n3", out: "text", to: "n4", in: "ref_text" }],
  },
  "글 → 효과음 셋 (한 번 나는 소리, 되풀이 배경 소리)": {
    nodes: [{ id: "n1", type: "text", x: 30, y: 20, text: "footsteps on dry leaves in a quiet forest, close up" },
      { id: "n2", type: "sfx", x: 360, y: 10, params: { seconds: 2, trim_silence: 50, peak: -1 } },
      { id: "n3", type: "sfx", x: 360, y: 300, params: { seconds: 2, trim_silence: 50, peak: -1, seed: 43 } },
      { id: "n4", type: "text", x: 30, y: 330, text: "steady rain falling on a tin roof, no thunder" },
      { id: "n5", type: "sfx", x: 360, y: 590, params: { loop: true, peak: -3 } }],
    edges: [{ from: "n1", out: "text", to: "n2", in: "prompt" }, { from: "n1", out: "text", to: "n3", in: "prompt" },
      { from: "n4", out: "text", to: "n5", in: "prompt" }],
  },
  "영상 파일 → 움직임에 맞춘 효과음": {
    nodes: [{ id: "n1", type: "clip", x: 30, y: 40 },
      { id: "n2", type: "text", x: 30, y: 230, text: "a person walks on a gravel path, wind in the trees" },
      { id: "n3", type: "sfx", x: 360, y: 80, params: {} }],
    edges: [{ from: "n1", out: "video", to: "n3", in: "video" }, { from: "n2", out: "text", to: "n3", in: "prompt" }],
  },
  "글 → 그림 → 글대로 고친 그림": {
    nodes: [{ id: "n1", type: "text", x: 30, y: 30, text: "숲속의 작은 오두막, 낮" },
      { id: "n2", type: "image", x: 330, y: 20, params: { size: "1024x1024" } },
      { id: "n3", type: "text", x: 330, y: 330, text: "같은 오두막을 눈 내리는 밤으로 바꾼다. 창문에 따뜻한 불빛" },
      { id: "n4", type: "image", x: 650, y: 150, params: { size: "1024x1024" } }],
    edges: [{ from: "n1", out: "text", to: "n2", in: "prompt" }, { from: "n3", out: "text", to: "n4", in: "prompt" },
      { from: "n2", out: "image", to: "n4", in: "refs" }],
  },
};

const Flow = {
  types: null,                      // 노드 종류 이름 → 서버 정의
  graph: { nodes: [], edges: [] },
  name: "",
  view: { x: 20, y: 20, k: 1 },     // 캔버스를 얼마나 밀고 키웠는지
  run: null,                        // 지금 보고 있는 실행 (서버가 준 것)
  urls: {},                         // 결과 미리보기: 홈 기준 경로 → 주소 (받는 중이면 Promise)
  act: null,                        // 끌고 있는 것: {kind: "node" | "pan" | "edge", ...}
  shown: {},                        // 노드 번호 → 마지막으로 그린 상태 줄. 바뀐 것만 다시 그린다
  loaded: false,

  // ── 화면 틀 ──
  render() {
    Flow.shown = {};
    if (!Flow.loaded) Flow.restore();       // 틀을 그리기 전에 되살린다 (이름 칸에 남겨 둔 이름이 들어가게)
    $("#page").innerHTML = pageHead("노드 편집기", "노드를 놓고 선으로 이어 여러 단계를 한 번에 돌립니다. 앞 노드가 만든 결과가 뒤 노드의 입력이 됩니다. 값을 고쳐 다시 실행하면 고친 노드부터만 다시 만듭니다.") + `
      <section class="panel flush flow-panel">
        <div class="toolbar flow-bar">
          <input class="input" id="flow-name" placeholder="그래프 이름" maxlength="60" value="${esc(Flow.name)}">
          <button class="btn sm" id="flow-save">저장</button>
          <select class="input" id="flow-open" title="저장한 그래프 열기"></select>
          <button class="icon-btn" id="flow-del" title="고른 저장본 지우기">${icon("trash")}</button>
          <select class="input" id="flow-example" title="본보기 그래프로 시작하기"><option value="">본보기 불러오기…</option>${Object.keys(FLOW_EXAMPLES).map((k) => `<option>${esc(k)}</option>`).join("")}</select>
          <button class="btn sm ghost" id="flow-new">비우기</button>
          <span class="spacer"></span>
          <span class="muted flow-status" id="flow-status"></span>
          <button class="btn sm ghost danger hidden" id="flow-cancel">실행 취소</button>
          <button class="btn primary" id="flow-run">${icon("play")}실행</button>
        </div>
        <div class="flow-body">
          <div class="flow-palette" id="flow-palette"></div>
          <div class="flow-canvas" id="flow-canvas">
            <div class="flow-world" id="flow-world"><svg class="flow-edges" id="flow-edges"></svg><div id="flow-nodes"></div></div>
            <div class="flow-hint" id="flow-hint"></div>
            <div class="flow-tools"><button class="btn sm" id="flow-fit" title="노드가 모두 보이게 맞추기">모두 보기</button><span class="muted" id="flow-zoom"></span></div>
          </div>
        </div>
      </section>
      <section class="panel flush">
        <div class="toolbar"><b>지난 실행</b><span class="muted" id="flow-run-count"></span></div>
        <div id="flow-runs"></div>
      </section>`;
    $("#flow-name").oninput = (e) => { Flow.name = e.target.value; Flow.keep(); };
    $("#flow-save").onclick = () => Flow.save();
    $("#flow-open").onchange = (e) => Flow.openSaved(e.target.value);
    $("#flow-del").onclick = () => Flow.deleteSaved();
    $("#flow-example").onchange = (e) => { const k = e.target.value; e.target.value = ""; if (k) Flow.replace(JSON.parse(JSON.stringify(FLOW_EXAMPLES[k])), k, `본보기 '${k}'`); };
    $("#flow-new").onclick = () => Flow.replace({ nodes: [], edges: [] }, "", "빈 그래프");
    $("#flow-run").onclick = () => Flow.start();
    $("#flow-cancel").onclick = () => Flow.cancel();
    $("#flow-fit").onclick = () => Flow.fit();
    $("#flow-runs").onclick = (e) => {
      const b = e.target.closest("[data-run-act]"); if (!b) return;
      const id = Number(b.closest("[data-run]").dataset.run);
      if (b.dataset.runAct === "open") Flow.openRun(id); else Flow.deleteRun(id);
    };
    Flow.bindCanvas();
    if (S.flow) Flow.draw();
  },

  async refresh() {
    S.flow = await api("/api/flow");
    if (flowLive(Flow.run)) {
      try { Flow.run = await api(`/api/flow/runs/${Flow.run.id}`); } catch (e) { Flow.run = null; }
    }
    Flow.draw();
  },

  // 서버에서 받은 것을 화면에 반영한다. 캔버스의 노드는 처음 한 번만 그리고, 그 뒤로는 상태 줄만 고친다
  draw() {
    const s = S.flow;
    if (!s || !$("#flow-canvas")) return;
    const first = !Flow.types;
    Flow.types = Object.fromEntries(s.types.map((t) => [t.key, t]));
    if (first || !$("#flow-nodes").children.length) Flow.drawGraph();
    // 목록들은 내용이 바뀐 때만 다시 그린다 (누르는 도중에 단추가 새로 그려져 눌림이 사라지지 않게)
    const put = (sel, html) => { const el = $(sel); if (el._html !== html) { el.innerHTML = html; el._html = html; } };
    put("#flow-palette", `<b>노드 넣기</b>` + s.types.map((t) => `
      <button class="flow-add ${t.ready ? "" : "off"}" data-add="${t.key}" title="${esc(t.ready ? t.help : t.hint)}">
        <i class="fn-dot" style="--c:${FLOW_COLOR[(t.outputs[0] || {}).type] || "#888"}"></i><span>${esc(t.title)}</span>${t.vram_gb ? `<em>${t.vram_gb} GB</em>` : ""}</button>`).join("")
      + `<p class="muted">오른쪽 점에서 다른 노드의 왼쪽 점으로 끌어 잇습니다. 같은 색끼리만 이어집니다. 선을 누르면 끊깁니다. 빈 곳을 끌면 화면이 움직입니다.</p>`);
    $("#flow-palette").onclick = (e) => { const b = e.target.closest("[data-add]"); if (b) Flow.add(b.dataset.add); };
    const sel = $("#flow-open"), cur = sel.value;
    put("#flow-open", `<option value="">${s.flows.length ? "저장한 그래프 열기…" : "저장한 그래프 없음"}</option>` + s.flows.map((f) => `<option value="${f.id}">${esc(f.name)}</option>`).join(""));
    sel.value = s.flows.some((f) => String(f.id) === cur) ? cur : "";
    $("#flow-run-count").textContent = s.runs.length ? `${s.runs.length}개` : "";
    put("#flow-runs", s.runs.length ? `<div class="table-wrap"><table class="dt"><thead><tr><th>실행</th><th>상태</th><th>단계</th><th>언제</th><th></th></tr></thead><tbody>${s.runs.map((r) => {
      const [label, tone] = { running: ["도는 중", "ok run"], succeeded: ["완성", "info"], failed: ["실패", "bad"], cancelled: ["취소됨", ""] }[r.status] || [r.status, ""];
      return `<tr data-run="${r.id}" class="${Flow.run && Flow.run.id === r.id ? "sel" : ""}"><td><b>#${r.id}</b> ${esc(r.name || "이름 없음")}</td>
        <td><span class="badge ${tone}">${label}</span></td>
        <td class="wrap">${esc(r.steps.join(" → "))} <span class="muted">(${r.done}/${r.steps.length} 완성${r.reused ? `, ${r.reused}개는 다시 씀` : ""})</span></td>
        <td class="muted">${when(r.created_at)}</td>
        <td><span class="flow-run-acts"><button class="btn sm" data-run-act="open">열기</button>${r.status === "running" ? "" : `<button class="icon-btn" data-run-act="del" title="실행 기록과 결과 파일 지우기">${icon("trash")}</button>`}</span></td></tr>`;
    }).join("")}</tbody></table></div>` : `<div class="empty"><b>아직 실행한 것이 없습니다</b>노드를 놓고 이은 뒤 "실행"을 누르면 여기에 남습니다. "열기"로 그때의 그래프와 결과를 다시 볼 수 있습니다.</div>`);
    Flow.drawRun();
  },

  // ── 그래프를 브라우저에 남기고 되살리기 ──
  keep() { store.set(FLOW_DRAFT, JSON.stringify({ name: Flow.name, graph: Flow.graph, view: Flow.view, run: Flow.run ? Flow.run.id : null })); },
  restore() {
    Flow.loaded = true;
    try {
      const d = JSON.parse(store.get(FLOW_DRAFT, "null"));
      if (!d || !Array.isArray(d.graph?.nodes)) return;
      Flow.graph = { nodes: d.graph.nodes, edges: d.graph.edges || [] };
      Flow.name = d.name || "";
      if (d.view) Flow.view = d.view;
      if (d.run) api(`/api/flow/runs/${d.run}`).then((r) => { Flow.run = r; Flow.drawRun(); }).catch(() => {});
    } catch { /* 남겨 둔 것을 읽지 못하면 빈 그래프로 시작한다 */ }
  },
  async replace(graph, name, what) {
    if (Flow.graph.nodes.length && !(await confirmDialog(`캔버스의 그래프를 바꿀까요? 불러올 것: ${what}. 저장하지 않은 그래프는 사라집니다.`, { ok: "바꾸기" }))) return;
    Flow.graph = graph; Flow.name = name; Flow.run = null;
    $("#flow-name").value = name;
    Flow.drawGraph(); Flow.fit(); Flow.keep();
  },

  // ── 노드 그리기 ──
  node(id) { return Flow.graph.nodes.find((n) => n.id === id); },
  drawGraph() {
    if (!Flow.types) return;
    Flow.shown = {};
    Flow.graph.nodes = Flow.graph.nodes.filter((n) => Flow.types[n.type]);       // 지금 판에 없는 종류의 노드는 뺀다
    const ids = new Set(Flow.graph.nodes.map((n) => n.id));
    Flow.graph.edges = Flow.graph.edges.filter((e) => ids.has(e.from) && ids.has(e.to));
    $("#flow-nodes").innerHTML = Flow.graph.nodes.map((n) => Flow.nodeHtml(n)).join("");
    Flow.graph.nodes.forEach((n) => { if (FLOW_UPLOADS[Flow.types[n.type].source]) Flow.showPicture(n); });
    Flow.place();
    Flow.drawEdges();
    Flow.drawRun();
    $("#flow-hint").innerHTML = Flow.graph.nodes.length ? "" : `<b>빈 캔버스입니다</b>왼쪽에서 노드를 넣거나, 위의 "본보기 불러오기"에서 하나를 골라 시작하세요.`;
  },
  nodeHtml(n) {
    const t = Flow.types[n.type];
    n.params = n.params || {};
    const port = (p, side) => `<div class="fn-port ${side}" data-port="${p.key}" data-type="${p.type}" title="${esc(p.help || S.flow.port_types[p.type])}">
      ${side === "in" ? `<i class="fn-dot" style="--c:${FLOW_COLOR[p.type]}"></i><span>${esc(p.label)}${p.required ? "" : ' <em>선택</em>'}</span>` : `<span>${esc(p.label)}</span><i class="fn-dot" style="--c:${FLOW_COLOR[p.type]}"></i>`}</div>`;
    let body = "";
    if (t.source === "text") body = `<textarea class="input fn-text" data-field="text" rows="5" spellcheck="false" placeholder="여기에 글을 적습니다">${esc(n.text || "")}</textarea>`;
    else if (FLOW_UPLOADS[t.source]) body = `<label class="fn-pic" data-pic><input type="file" accept="${FLOW_UPLOADS[t.source].accept}" hidden data-pic-file><span class="fn-pic-box"></span></label>`;
    else {
      const basic = t.params.filter((p) => !p.group);
      body = basic.map((p) => Flow.paramRow(p, n.params[p.key])).join("")
        + (t.params.some((p) => p.group) ? `<button class="btn sm fn-more" data-more>상세 설정<span class="gen-count" data-more-count></span></button>` : "");
    }
    return `<div class="flow-node" data-id="${n.id}" data-type="${n.type}">
      <div class="fn-head" title="${esc(t.help)}"><b>${esc(t.title)}</b><span class="fn-id">${n.id}</span><span class="fn-badge"></span><button class="icon-btn fn-del" data-del title="이 노드 지우기">${icon("close")}</button></div>
      <div class="fn-ports"><div>${t.inputs.map((p) => port(p, "in")).join("")}</div><div>${t.outputs.map((p) => port(p, "out")).join("")}</div></div>
      ${body ? `<div class="fn-body">${body}</div>` : ""}
      <div class="fn-state"></div><div class="fn-result"></div></div>`;
  },
  // 노드 안에 늘 보이는 조절 값 한 줄 (도움말은 마우스를 올리면 나온다)
  paramRow(p, raw) {
    const v = raw === undefined || raw === null ? p.default : raw;
    let input;
    if (p.type === "bool") input = `<input type="checkbox" data-param="${p.key}" ${v ? "checked" : ""}>`;
    else if (p.type === "choice") input = `<select class="input" data-param="${p.key}">${p.choices.map(([k, l]) => `<option value="${esc(k)}" ${k === v ? "selected" : ""}>${esc(l)}</option>`).join("")}</select>`;
    else input = `<input class="input" data-param="${p.key}" type="${p.type === "schedule" ? "text" : "number"}" ${p.min != null ? `min="${p.min}"` : ""} ${p.max != null ? `max="${p.max}"` : ""} step="${p.step ?? "any"}" value="${esc(v ?? "")}" placeholder="${p.default == null ? "자동" : p.default}">`;
    return `<label class="fn-row ${p.type === "bool" ? "check" : ""}" title="${esc(p.help)}"><span>${esc(p.label)}</span>${input}</label>`;
  },
  place() {
    Flow.graph.nodes.forEach((n) => { const el = Flow.el(n.id); if (el) { el.style.left = `${n.x || 0}px`; el.style.top = `${n.y || 0}px`; } });
    $("#flow-world").style.transform = `translate(${Flow.view.x}px, ${Flow.view.y}px) scale(${Flow.view.k})`;
    $("#flow-zoom").textContent = `${Math.round(Flow.view.k * 100)}%`;
    Flow.graph.nodes.forEach((n) => Flow.moreCount(n));
  },
  el(id) { return $(`#flow-nodes .flow-node[data-id="${id}"]`); },
  moreCount(n) {
    const t = Flow.types[n.type], el = Flow.el(n.id)?.querySelector("[data-more-count]");
    if (!el) return;
    let count = 0;
    for (const p of t.params.filter((x) => x.group)) { try { if (!genSame(p, genValue(p, n.params[p.key] ?? p.default))) count++; } catch { count++; } }
    el.textContent = count ? `${count}개 바꿈` : "";
  },

  // ── 좌표와 선 ──
  toWorld(cx, cy) { const r = $("#flow-canvas").getBoundingClientRect(); return { x: (cx - r.left - Flow.view.x) / Flow.view.k, y: (cy - r.top - Flow.view.y) / Flow.view.k }; },
  dot(id, side, port) {
    const d = Flow.el(id)?.querySelector(`.fn-port.${side}[data-port="${port}"] .fn-dot`);
    if (!d) return null;
    const r = d.getBoundingClientRect();
    return Flow.toWorld(r.left + r.width / 2, r.top + r.height / 2);
  },
  curve(a, b) { const dx = Math.max(40, Math.abs(b.x - a.x) / 2); return `M${a.x},${a.y} C${a.x + dx},${a.y} ${b.x - dx},${b.y} ${b.x},${b.y}`; },
  drawEdges(temp) {
    const svg = $("#flow-edges");
    if (!svg) return;
    let html = "";
    Flow.graph.edges.forEach((e, i) => {
      const a = Flow.dot(e.from, "out", e.out), b = Flow.dot(e.to, "in", e.in);
      if (!a || !b) return;
      const type = (Flow.types[Flow.node(e.from).type].outputs.find((o) => o.key === e.out) || {}).type;
      const d = Flow.curve(a, b);
      html += `<path class="fe" d="${d}" stroke="${FLOW_COLOR[type]}"/><path class="fe-hit" d="${d}" data-edge="${i}"><title>누르면 이 연결을 끊습니다</title></path>`;
    });
    if (temp) html += `<path class="fe temp" d="${Flow.curve(temp.a, temp.b)}" stroke="${FLOW_COLOR[temp.type]}"/>`;
    svg.innerHTML = html;
  },
  fit() {
    const box = $("#flow-canvas").getBoundingClientRect(), els = $$("#flow-nodes .flow-node");
    if (!els.length) { Flow.view = { x: 20, y: 20, k: 1 }; Flow.place(); return; }
    let x0 = Infinity, y0 = Infinity, x1 = -Infinity, y1 = -Infinity;
    Flow.graph.nodes.forEach((n) => { const el = Flow.el(n.id); x0 = Math.min(x0, n.x); y0 = Math.min(y0, n.y); x1 = Math.max(x1, n.x + el.offsetWidth); y1 = Math.max(y1, n.y + el.offsetHeight); });
    const k = Math.max(0.35, Math.min(1, (box.width - 40) / (x1 - x0), (box.height - 40) / (y1 - y0)));
    Flow.view = { k, x: 20 - x0 * k, y: 20 - y0 * k };
    Flow.place(); Flow.keep();
  },

  // ── 캔버스에서 하는 일: 끌기, 잇기, 적기 ──
  bindCanvas() {
    const cv = $("#flow-canvas");
    cv.onpointerdown = (e) => {
      if (e.button !== 0) return;
      const nodeEl = e.target.closest(".flow-node"), dotEl = e.target.closest(".fn-port.out .fn-dot");
      if (dotEl) {
        const port = dotEl.closest(".fn-port");
        Flow.act = { kind: "edge", from: nodeEl.dataset.id, out: port.dataset.port, type: port.dataset.type };
        cv.classList.add("linking");
        $$("#flow-nodes .fn-port.in").forEach((p) => p.classList.toggle("can", p.dataset.type === port.dataset.type && p.closest(".flow-node") !== nodeEl));
      } else if (nodeEl && e.target.closest(".fn-head") && !e.target.closest("button")) {
        const n = Flow.node(nodeEl.dataset.id), w = Flow.toWorld(e.clientX, e.clientY);
        Flow.act = { kind: "node", n, dx: w.x - n.x, dy: w.y - n.y };
        $$("#flow-nodes .flow-node").forEach((x) => x.classList.toggle("top", x === nodeEl));
      } else if (!nodeEl && !e.target.closest(".flow-tools") && !e.target.closest(".fe-hit")) {
        Flow.act = { kind: "pan", sx: e.clientX, sy: e.clientY, vx: Flow.view.x, vy: Flow.view.y };
        cv.classList.add("panning");
      } else return;
      try { cv.setPointerCapture(e.pointerId); } catch { /* 잡지 못해도 캔버스 안에서는 끌린다 */ }
      e.preventDefault();
    };
    cv.onpointermove = (e) => {
      const a = Flow.act;
      if (!a) return;
      if (a.kind === "node") {
        const w = Flow.toWorld(e.clientX, e.clientY);
        a.n.x = Math.round(w.x - a.dx); a.n.y = Math.round(w.y - a.dy);
        const el = Flow.el(a.n.id); el.style.left = `${a.n.x}px`; el.style.top = `${a.n.y}px`;
        Flow.drawEdges();
      } else if (a.kind === "pan") {
        Flow.view.x = a.vx + e.clientX - a.sx; Flow.view.y = a.vy + e.clientY - a.sy;
        $("#flow-world").style.transform = `translate(${Flow.view.x}px, ${Flow.view.y}px) scale(${Flow.view.k})`;
      } else if (a.kind === "edge") {
        Flow.drawEdges({ a: Flow.dot(a.from, "out", a.out), b: Flow.toWorld(e.clientX, e.clientY), type: a.type });
      }
    };
    const end = (e) => {
      const a = Flow.act;
      if (!a) return;
      Flow.act = null;
      cv.classList.remove("panning", "linking");
      if (a.kind === "edge") {
        $$("#flow-nodes .fn-port.in").forEach((p) => p.classList.remove("can"));
        const target = e.type === "pointerup" ? document.elementFromPoint(e.clientX, e.clientY)?.closest(".fn-port.in") : null;
        if (target) Flow.connect(a, target.closest(".flow-node").dataset.id, target.dataset.port);
        Flow.drawEdges();
      }
      Flow.keep();
    };
    cv.onpointerup = end;
    cv.onpointercancel = end;
    // 두 손가락으로 밀면 화면이 움직이고, Ctrl(또는 벌리기)과 함께면 그 자리를 중심으로 커지고 작아진다
    cv.onwheel = (e) => {
      if (e.target.closest("textarea, select")) return;       // 글 칸 안에서는 그 칸이 굴러간다
      e.preventDefault();
      if (e.ctrlKey || e.metaKey) {
        const r = cv.getBoundingClientRect(), px = e.clientX - r.left, py = e.clientY - r.top;
        const k = Math.max(0.35, Math.min(1.6, Flow.view.k * Math.exp(-e.deltaY * 0.01)));
        Flow.view.x = px - (px - Flow.view.x) * (k / Flow.view.k); Flow.view.y = py - (py - Flow.view.y) * (k / Flow.view.k); Flow.view.k = k;
      } else { Flow.view.x -= e.deltaX; Flow.view.y -= e.deltaY; }
      $("#flow-world").style.transform = `translate(${Flow.view.x}px, ${Flow.view.y}px) scale(${Flow.view.k})`;
      $("#flow-zoom").textContent = `${Math.round(Flow.view.k * 100)}%`;
      Flow.keep();
    };
    cv.onclick = (e) => {
      const hit = e.target.closest(".fe-hit"), nodeEl = e.target.closest(".flow-node");
      if (hit) { Flow.graph.edges.splice(Number(hit.dataset.edge), 1); Flow.drawEdges(); Flow.keep(); return; }
      if (!nodeEl) return;
      const n = Flow.node(nodeEl.dataset.id), b = e.target.closest("[data-del], [data-more], [data-result]");
      if (!b) return;
      if (b.matches("[data-del]")) Flow.remove(n);
      else if (b.matches("[data-more]")) Flow.settings(n);
      else Flow.result(n, b.dataset.result, b.dataset.port);
    };
    cv.oninput = (e) => {
      const nodeEl = e.target.closest(".flow-node"); if (!nodeEl) return;
      const n = Flow.node(nodeEl.dataset.id);
      if (e.target.dataset.field === "text") n.text = e.target.value;
      else if (e.target.dataset.param) n.params[e.target.dataset.param] = e.target.type === "checkbox" ? e.target.checked : e.target.value;
      Flow.keep();
    };
    cv.onchange = (e) => {
      if (!e.target.matches("[data-pic-file]")) return;
      const file = e.target.files[0], n = Flow.node(e.target.closest(".flow-node").dataset.id);
      e.target.value = "";
      if (file) Flow.upload(n, file);
    };
  },
  connect(a, to, port) {
    if (to === a.from) return;
    const t = Flow.types[Flow.node(to).type], inp = t.inputs.find((i) => i.key === port);
    if (inp.type !== a.type) { toast(`'${inp.label}'에는 ${S.flow.port_types[inp.type]}만 이을 수 있습니다 (끌어온 것은 ${S.flow.port_types[a.type]})`, true); return; }
    const edges = Flow.graph.edges, same = (e) => e.to === to && e.in === port;
    if (edges.some((e) => same(e) && e.from === a.from && e.out === a.out)) return;
    if (inp.max === 1) Flow.graph.edges = edges.filter((e) => !same(e));           // 하나만 받는 입력은 새 연결로 바꾼다
    else if (edges.filter(same).length >= inp.max) { toast(`'${inp.label}'에는 ${inp.max}개까지 이을 수 있습니다`, true); return; }
    Flow.graph.edges.push({ from: a.from, out: a.out, to, in: port });
  },
  add(type) {
    const t = Flow.types[type];
    if (!t.ready) { toast(t.hint, true); return; }
    if (Flow.graph.nodes.length >= S.flow.limits.nodes) { toast(`노드는 ${S.flow.limits.nodes}개까지입니다`, true); return; }
    let i = 1;
    while (Flow.node(`n${i}`)) i++;
    const box = $("#flow-canvas").getBoundingClientRect(), c = Flow.toWorld(box.left + box.width / 2, box.top + box.height / 3);
    const n = { id: `n${i}`, type, params: {}, x: Math.round(c.x - 120 + (i % 5) * 24), y: Math.round(c.y - 60 + (i % 5) * 24) };
    // 노드마다 시드를 따로 준다. 모두 같은 기본 시드면 모두 같은 시작 잡음에서 출발해, 그 잡음이 나쁘면 노드가 한꺼번에 망가진다
    // (2026-11-15: 같은 사진의 각도 바꾸기에서 시드 42 는 다섯 노드 모두 참고 그림을 베끼고 탔고, 시드 1·2 는 멀쩡했다).
    if (t.params.some((p) => p.key === "seed")) n.params.seed = String(1 + Math.floor(Math.random() * 99999));
    if (t.source === "text") n.text = "";
    Flow.graph.nodes.push(n);
    $("#flow-nodes").insertAdjacentHTML("beforeend", Flow.nodeHtml(n));
    if (FLOW_UPLOADS[t.source]) Flow.showPicture(n);
    Flow.place(); Flow.keep();
    $("#flow-hint").innerHTML = "";
    return n;
  },
  remove(n) {
    Flow.graph.nodes = Flow.graph.nodes.filter((x) => x !== n);
    Flow.graph.edges = Flow.graph.edges.filter((e) => e.from !== n.id && e.to !== n.id);
    Flow.el(n.id)?.remove();
    Flow.drawEdges(); Flow.keep();
  },

  // ── 사진 노드, 소리 파일 노드, 영상 파일 노드 ──
  async upload(n, file) {
    const u = FLOW_UPLOADS[Flow.types[n.type].source];
    if (!u.ok(file)) { toast(`${u.what}이 아닙니다 (받은 이름 '${file.name}'). ${u.exts} 파일을 고르세요.`, true); return; }
    const body = new FormData();
    body.append("file", file, file.name);
    try {
      const up = await api("/api/flow/upload", { method: "POST", body });
      n.path = up.path; n.name = up.name;
      Flow.urls[up.path] = URL.createObjectURL(file);
      Flow.showPicture(n); Flow.keep();
    } catch (e) { fail(e); }
  },
  async showPicture(n) {
    const box = Flow.el(n.id)?.querySelector(".fn-pic-box"), u = FLOW_UPLOADS[Flow.types[n.type].source];
    if (!box) return;
    if (!n.path) { box.innerHTML = `${icon("upload")}<span>눌러서 ${u.what}을 고르세요<br><em>${u.exts}</em></span>`; return; }
    if (u === FLOW_UPLOADS.sound) { box.innerHTML = `${icon("mic")}<span>${esc(n.name || "소리 파일")}<br><em>누르면 다른 소리 파일로 바꿉니다</em></span>`; return; }
    if (u === FLOW_UPLOADS.clip) { box.innerHTML = `${icon("video")}<span>${esc(n.name || "영상 파일")}<br><em>누르면 다른 영상 파일로 바꿉니다</em></span>`; return; }
    box.innerHTML = `<span class="muted">사진을 받는 중…</span>`;
    try { box.innerHTML = `<img src="${await Flow.url(n.path)}" alt=""><span>${esc(n.name || "사진")}<br><em>누르면 다른 사진으로 바꿉니다</em></span>`; }
    catch { box.innerHTML = `${icon("alert")}<span>사진을 찾지 못했습니다<br><em>눌러서 다시 올려 주세요</em></span>`; }
  },
  // 홈의 파일을 화면에 넣을 주소로 바꾼다. 한 번 받은 것은 다시 받지 않는다. 늘 Promise 를 돌려준다
  url(path) {
    if (!Flow.urls[path]) {
      Flow.urls[path] = homeFile(path).then((res) => res.blob()).then((b) => URL.createObjectURL(b));
      Flow.urls[path].catch(() => { delete Flow.urls[path]; });      // 못 받았으면 다음에 다시 해 본다
    }
    return Promise.resolve(Flow.urls[path]);
  },

  // ── 노드의 상세 설정 (쉬운 메뉴와 같은 정의, 같은 모양) ──
  settings(n) {
    const t = Flow.types[n.type];
    const groups = [];
    t.params.filter((p) => p.group).forEach((p) => (groups.find((g) => g.name === p.group) || groups[groups.push({ name: p.group, params: [] }) - 1]).params.push(p));
    openDrawer({
      title: `${esc(t.title)} <span class="muted">${n.id}</span>`, sub: "이 노드의 단계별 조절 값입니다. 바꾸면 바로 노드에 들어갑니다.", width: 560,
      body: `<div class="gen-groups one" id="fs-groups">${groups.map((g) => `<div class="gen-group" data-group="${esc(g.name)}"><div class="gen-group-head"><b>${esc(g.name)}</b></div>
        ${g.params.map((p) => genParamField(p, n.params[p.key] ?? undefined)).join("")}</div>`).join("")}</div>`,
      foot: `<button class="btn" id="fs-reset">이 노드를 기본값으로</button><button class="btn primary" data-drawer-close>닫기</button>`,
      onClose: () => { Flow.moreCount(n); },
    });
    const mark = () => t.params.filter((p) => p.group).forEach((p) => {
      let changed = true;
      try { changed = !genSame(p, genValue(p, n.params[p.key] ?? p.default)); } catch { /* 숫자가 아닌 글자: 바꾼 것으로 본다 */ }
      $(`#fs-groups [data-row="${p.key}"]`)?.classList.toggle("changed", changed);
    });
    $("#fs-groups").oninput = (e) => {
      const key = e.target.dataset.param; if (!key) return;
      n.params[key] = e.target.type === "checkbox" ? e.target.checked : e.target.value;
      mark(); Flow.moreCount(n); Flow.keep();
    };
    $("#fs-reset").onclick = () => { t.params.filter((p) => p.group).forEach((p) => delete n.params[p.key]); Flow.keep(); Flow.moreCount(n); Flow.settings(n); };
    mark();
  },

  // ── 저장한 그래프 ──
  // 서버에 보낼 그래프: 입력 칸의 글자를 수로 바꾸고, 화면에서만 쓰는 것은 뺀다
  clean() {
    const nodes = Flow.graph.nodes.map((n) => {
      const t = Flow.types[n.type], params = {};
      for (const p of t.params) {
        try { params[p.key] = genValue(p, n.params[p.key] === undefined ? p.default : n.params[p.key]); }
        catch (e) { throw new Error(`'${t.title}' 노드(${n.id}): ${e.message}`); }
      }
      const out = { id: n.id, type: n.type, x: n.x || 0, y: n.y || 0, params };
      if (t.source === "text") out.text = n.text || "";
      if (FLOW_UPLOADS[t.source]) { out.path = n.path || ""; out.name = n.name || ""; }
      return out;
    });
    return { nodes, edges: Flow.graph.edges.map((e) => ({ from: e.from, out: e.out, to: e.to, in: e.in })) };
  },
  async save() {
    const name = Flow.name.trim();
    if (!name) { toast("그래프 이름을 적어 주세요 (왼쪽 위 칸)", true); $("#flow-name").focus(); return; }
    try {
      const saved = await api("/api/flow/flows", { method: "POST", json: { name, graph: Flow.clean() } });
      await Flow.refresh();
      $("#flow-open").value = String(saved.id);
      toast(`그래프를 저장했습니다: ${saved.name}`);
    } catch (e) { fail(e); }
  },
  openSaved(id) {
    const f = S.flow.flows.find((x) => String(x.id) === id);
    if (f) Flow.replace(JSON.parse(JSON.stringify(f.graph)), f.name, `저장본 '${f.name}'`).then(() => { $("#flow-open").value = Flow.name === f.name ? id : ""; });
  },
  async deleteSaved() {
    const f = S.flow.flows.find((x) => String(x.id) === $("#flow-open").value);
    if (!f) { toast("먼저 저장한 그래프를 고르세요", true); return; }
    if (!(await confirmDialog(`저장한 그래프 '${f.name}'을 지울까요? 캔버스의 그래프와 지난 실행은 그대로 둡니다.`, { ok: "지우기", danger: true }))) return;
    try { await api(`/api/flow/flows/${f.id}`, { method: "DELETE" }); await Flow.refresh(); toast("지웠습니다"); } catch (e) { fail(e); }
  },

  // ── 실행 ──
  async start() {
    if (flowLive(Flow.run)) { toast("지금 도는 실행이 끝나거나 취소된 뒤에 다시 실행하세요", true); return; }
    let graph;
    try { graph = Flow.clean(); } catch (e) { fail(e); return; }
    $("#flow-run").disabled = true;
    try {
      Flow.run = await api("/api/flow/runs", { method: "POST", json: { name: Flow.name.trim(), graph } });
      Flow.shown = {};
      Flow.keep();
      const again = Object.values(Flow.run.nodes).filter((x) => x.status === "reused").length;
      toast(Flow.run.status === "succeeded" ? "바뀐 것이 없어 지난 결과를 그대로 다시 썼습니다" : `실행을 시작했습니다 (#${Flow.run.id}${again ? `, ${again}개는 지난 결과를 다시 씀` : ""})`);
      await Flow.refresh();
    } catch (e) { fail(e); }
    finally { $("#flow-run").disabled = flowLive(Flow.run); }      // 도는 동안에는 다시 실행하지 못하게 둔다
  },
  async cancel() {
    if (!flowLive(Flow.run) || !(await confirmDialog(`지금 도는 실행(#${Flow.run.id})을 취소할까요? 돌고 있는 작업을 멈추고, 아직 시작하지 않은 노드는 하지 않습니다. 이미 끝난 노드의 결과는 남습니다.`, { ok: "실행 취소", danger: true }))) return;
    try { Flow.run = await api(`/api/flow/runs/${Flow.run.id}/cancel`, { method: "POST" }); toast("실행을 취소했습니다"); await Flow.refresh(); } catch (e) { fail(e); }
  },
  async openRun(id) {
    try {
      const run = await api(`/api/flow/runs/${id}`);
      // 지금 캔버스가 그 실행의 그래프가 아니면 바꾸기 전에 묻는다
      if (Flow.graph.nodes.length && !(Flow.run && Flow.run.id === id)
        && !(await confirmDialog(`캔버스를 실행 #${id} 때의 그래프로 바꿀까요? 저장하지 않은 그래프는 사라집니다.`, { ok: "바꾸기" }))) return;
      Flow.graph = run.graph; Flow.name = run.name || ""; Flow.run = run;
      $("#flow-name").value = Flow.name;
      Flow.drawGraph(); Flow.fit(); Flow.keep(); Flow.draw();
      $("#flow-canvas").scrollIntoView({ behavior: "smooth", block: "center" });
    } catch (e) { fail(e); }
  },
  async deleteRun(id) {
    if (!(await confirmDialog(`실행 #${id}의 기록과 그 실행이 만든 결과 파일을 지울까요? 다른 실행이 다시 쓰고 있는 결과는 남깁니다. 되돌릴 수 없습니다.`, { ok: "지우기", danger: true }))) return;
    try {
      await api(`/api/flow/runs/${id}`, { method: "DELETE" });
      if (Flow.run && Flow.run.id === id) { Flow.run = null; Flow.shown = {}; Flow.keep(); }
      toast("지웠습니다");
      await Flow.refresh();
    } catch (e) { fail(e); }
  },

  // 실행 상태를 노드마다 그린다: 배지, 한 줄 설명, 진행 막대, 결과
  drawRun() {
    const run = Flow.run;
    if (!$("#flow-canvas") || !Flow.types) return;
    $("#flow-cancel").classList.toggle("hidden", !flowLive(run));
    $("#flow-run").disabled = flowLive(run);
    if (!run) $("#flow-status").textContent = "";
    else {
      const made = Object.entries(run.nodes).filter(([id]) => Flow.types[(run.graph.nodes.find((n) => n.id === id) || {}).type]?.kind).map(([, v]) => v);
      const ok = made.filter((v) => v.status === "succeeded" || v.status === "reused").length;
      const word = { running: "도는 중", succeeded: "완성", failed: "실패", cancelled: "취소됨" }[run.status] || run.status;
      $("#flow-status").textContent = `실행 #${run.id} ${word}: ${made.length}단계 가운데 ${ok}단계 끝남`;
    }
    for (const n of Flow.graph.nodes) {
      const el = Flow.el(n.id), st = run?.nodes[n.id], t = Flow.types[n.type];
      if (!el) continue;
      const key = JSON.stringify([run?.id, st]);
      if (Flow.shown[n.id] === key) continue;
      Flow.shown[n.id] = key;
      el.dataset.status = st ? st.status : "";
      const [label, tone] = st && t.kind ? FLOW_STATUS[st.status] || [st.status, ""] : ["", ""];
      el.querySelector(".fn-badge").innerHTML = label ? `<span class="badge ${tone}">${label}</span>` : "";
      let line = "", progress = "";
      if (st && t.kind) {
        if (st.status === "waiting") line = "앞 노드를 기다리는 중";
        else if (st.status === "queued") line = `<span class="reason">${esc(st.wait_reason || "GPU 차례를 기다리는 중")}</span>${st.batch ? `<br><span class="muted">${st.batch.n}개 노드를 모델 한 번 올려 함께 만듭니다</span>` : ""}`;
        else if (st.status === "running") {
          const p = st.progress;
          if (p && p.steps === 1) line = esc(p.stage);    // 묶음 안에서 차례를 기다리거나 이미 다 그린 노드
          else line = p ? `${esc(p.stage)} (${p.step}/${p.steps}단계${p.percent != null ? `, ${p.percent}%` : ""})` : "실행 중";
          if (p && !p.waiting) progress = bar(((p.step - 1 + (p.percent ?? 50) / 100) / p.steps) * 100, "ok");
        } else if (st.status === "succeeded") line = `${st.started_at && st.finished_at ? dur(st.finished_at - st.started_at) + " 걸림" : "끝남"}${st.batch ? ` (${st.batch.n}개를 한 작업으로)` : ""}${st.peak_vram_gb ? `, VRAM 최대 ${st.peak_vram_gb} GB` : ""}`;
        else if (st.status === "reused") line = `실행 #${st.reused_from}의 결과를 다시 씀 (새로 만들지 않음)`;
        else if (st.status === "failed") line = `<span style="color:var(--bad)">${esc(st.error || "실패했습니다. 로그를 확인하세요.")}</span>`;
        else if (st.status === "skipped") line = esc(st.error || "건너뛰었습니다");
        else if (st.status === "cancelled") line = "취소했습니다";
        if (st.job_id) line += ` <button class="link" data-result="log">로그</button>`;
      }
      el.querySelector(".fn-state").innerHTML = line ? `<div>${line}</div>${progress}` : "";
      Flow.drawResult(n, el, st);
    }
  },
  drawResult(n, el, st) {
    const box = el.querySelector(".fn-result"), out = st && (st.status === "succeeded" || st.status === "reused") ? st.outputs : null;
    if (!out || !Flow.types[n.type].kind) { box.innerHTML = ""; return; }
    const dl = (port, text) => `<button class="link" data-result="dl" data-port="${port}">${text}</button>`;
    const ext = (port) => (out[port].path.split(".").pop() || "").toLowerCase();
    const stems = `${out.vocals ? dl("vocals", "목소리만") : ""}${out.backing ? dl("backing", "반주만") : ""}`;     // 소리 다듬기가 나눈 것
    if (out.video) {
      box.innerHTML = `<button class="fn-shot fn-video" data-result="video" title="영상 보기">${icon("video")}</button><div class="fn-links">${dl("video", `mp4 (${fmtBytes(out.video.size)})`)}${out.last ? dl("last", "끝 장면 png") : ""}${out.audio ? dl("audio", `${n.type === "sfx" ? "효과음" : "소리"} ${ext("audio")} (${fmtBytes(out.audio.size)})`) : ""}${stems}</div>`;
      if (out.thumb) Flow.url(out.thumb.path).then((u) => { const b = box.querySelector(".fn-shot"); if (b) b.innerHTML = `<img src="${u}" alt=""><i class="fn-play">${icon("play")}</i>`; }).catch(() => {});
    } else if (out.image) {
      box.innerHTML = `<button class="fn-shot" data-result="image" title="크게 보기"><span class="muted">그림을 받는 중…</span></button><div class="fn-links">${dl("image", `내려받기 (${fmtBytes(out.image.size)})`)}</div>`;
      Flow.url((out.thumb || out.image).path).then((u) => { const b = box.querySelector(".fn-shot"); if (b) b.innerHTML = `<img src="${u}" alt="">`; }).catch(() => {});
    } else if (out.mesh) {
      box.innerHTML = `<button class="btn sm primary" data-result="mesh">${icon("cube")}3D 미리보기</button><div class="fn-links">${dl("mesh", `GLB 내려받기 (${fmtBytes(out.mesh.size)})`)}</div>`;
    } else if (out.audio) {
      box.innerHTML = `<audio controls preload="none"></audio><div class="fn-links">${out.score ? `<button class="link" data-result="score" data-port="score">쓴 악보 보기</button>` : ""}${out.listen ? dl("listen", "mp3") : ""}${dl("audio", `${ext("audio")} (${fmtBytes(out.audio.size)})`)}${stems}</div>`;
      Flow.url((out.listen || out.audio).path).then((u) => { const a = box.querySelector("audio"); if (a) a.src = u; }).catch(() => {});
    } else if (out.score) {
      box.innerHTML = `<button class="btn sm" data-result="score" data-port="score">악보 보기</button>`;
    }
  },
  // 노드의 결과에서 누른 것
  async result(n, what, port) {
    const st = Flow.run?.nodes[n.id];
    if (!st) return;
    if (what === "log") return go("jobs", st.job_id);
    const out = st.outputs || {};
    try {
      if (what === "dl") {
        const name = out[port].path.split("/").pop();
        toast(`'${name}'을 받는 중입니다`);
        saveBlob(await (await homeFile(out[port].path)).blob(), name);
      } else if (what === "image") {
        openDrawer({ title: "그림", sub: `${esc(Flow.types[n.type].title)} ${n.id}`, width: 860,
          body: `<div class="gi-view"><img src="${await Flow.url(out.image.path)}" alt=""></div><dl class="dl" style="margin-top:14px"><dt>파일</dt><dd class="mono">${esc(out.image.path)} (${fmtBytes(out.image.size)})</dd></dl>`,
          foot: `<button class="btn primary" id="fr-dl">${icon("download")}그림 내려받기</button>` });
        $("#fr-dl").onclick = () => Flow.result(n, "dl", "image");
      } else if (what === "video") {
        let media = null;
        openDrawer({ title: "영상", sub: `${esc(Flow.types[n.type].title)} ${n.id}`, width: 900,
          body: `<div class="gv-view" id="fr-video"><div class="g3-msg">영상을 받는 중…</div></div><dl class="dl" style="margin-top:14px"><dt>파일</dt><dd class="mono">${esc(out.video.path)} (${fmtBytes(out.video.size)})</dd></dl>`,
          foot: `${out.last ? `<button class="btn" id="fr-last">끝 장면 png</button>` : ""}<button class="btn primary" id="fr-dl">${icon("download")}MP4 내려받기</button>`,
          dispose: () => { $("#fr-video video")?.pause(); if (media) URL.revokeObjectURL(media.url); } });
        $("#fr-dl").onclick = () => Flow.result(n, "dl", "video");
        if (out.last) $("#fr-last").onclick = () => Flow.result(n, "dl", "last");
        media = await homeMedia(out.video.path, "video/mp4");
        const box = $("#fr-video");
        if (box) box.innerHTML = `<video src="${media.url}" controls playsinline autoplay preload="metadata"></video>`;
        else URL.revokeObjectURL(media.url);              // 받는 동안 서랍을 닫았다
      } else if (what === "mesh") Flow.showMesh(n, out.mesh);
      else if (what === "score") {
        const text = await (await homeFile(out[port].path)).text();
        openDrawer({ title: "악보", sub: "모델이 쓴 멜로디와 코드 (ABC 표기). Q: 는 빠르기, K: 는 조, 따옴표 안은 코드입니다.", width: 640,
          body: `<pre class="gm-score" style="max-height:60vh">${esc(text)}</pre>`,
          foot: `<button class="btn" id="fr-copy">악보 복사</button><button class="btn primary" id="fr-node">글 노드로 꺼내기</button>` });
        $("#fr-copy").onclick = () => copyText(text);
        // 악보를 고쳐 쓰려면 글 노드로 꺼내 고친 뒤 '노래 만들기'의 악보 입력에 잇는다
        $("#fr-node").onclick = () => {
          const made = Flow.add("text");
          if (!made) return;
          made.text = text; made.x = n.x + 40; made.y = n.y + (Flow.el(n.id)?.offsetHeight || 200) + 30;
          Flow.el(made.id).querySelector("textarea").value = text;
          Flow.place(); Flow.keep(); closeDrawer();
          toast("악보를 글 노드로 꺼냈습니다. 고친 뒤 '노래 만들기'의 악보 입력에 이으세요.");
        };
      }
    } catch (e) { fail(e); }
  },
  async showMesh(n, file) {
    let viewer = null, dead = false;
    openDrawer({
      title: "3D 모델", sub: "끌어서 돌리고, 휠로 확대합니다. 두 번 누르면 처음 시점으로 돌아갑니다.", width: 820,
      body: `<div class="g3-view"><canvas id="g3-canvas"></canvas><div class="g3-msg" id="g3-msg">모델을 받는 중…</div>
        <div class="seg g3-mode hidden" id="g3-mode"><button data-m="0" class="on">색</button><button data-m="1">형상만</button></div></div><dl class="dl" id="g3-facts" style="margin-top:14px"></dl>`,
      foot: `<button class="btn primary" id="g3-dl" disabled>${icon("download")}GLB 내려받기</button>`,
      dispose: () => { dead = true; viewer?.dispose(); },
    });
    const say = (t) => { const m = $("#g3-msg"); if (m && !dead) m.textContent = t; };
    try {
      const buf = await homeBytes(file.path, (got, total) => say(`모델을 받는 중… ${fmtBytes(got)}${total ? " / " + fmtBytes(total) : ""}`));
      if (dead) return;
      $("#g3-dl").disabled = false;
      $("#g3-dl").onclick = () => saveBlob(new Blob([buf], { type: "model/gltf-binary" }), file.path.split("/").pop());
      say("모델을 그리는 중…");
      viewer = await glbViewer($("#g3-canvas"), buf);
      if (dead) { viewer.dispose(); return; }
      $("#g3-msg").remove();
      const seg = $("#g3-mode");
      seg.classList.remove("hidden");
      seg.onclick = (e) => { const b = e.target.closest("[data-m]"); if (!b) return; seg.querySelectorAll("button").forEach((x) => x.classList.toggle("on", x === b)); viewer.setMode(Number(b.dataset.m)); };
      const s = viewer.stats;
      $("#g3-facts").innerHTML = `<dt>꼭짓점, 삼각형</dt><dd>${s.vertices.toLocaleString("ko-KR")}개, ${s.triangles.toLocaleString("ko-KR")}개</dd>
        <dt>텍스처</dt><dd>${s.texture ? esc(s.texture) : "없음"}</dd><dt>파일</dt><dd class="mono">${esc(file.path)} (${fmtBytes(buf.byteLength)})</dd>`;
    } catch (e) { say(`미리보기를 열지 못했습니다: ${e.message || e}`); }
  },
};

function saveBlob(blob, name) {
  const a = document.createElement("a");
  a.href = URL.createObjectURL(blob);
  a.download = name;
  a.click();
  setTimeout(() => URL.revokeObjectURL(a.href), 5000);
}

// ── GLB 미리보기 ──
// glTF 2.0 바이너리(GLB)를 외부 라이브러리 없이 WebGL2 로 그린다. 콘솔이 인터넷 없이 내부망에서만 열려도 동작하게 하려는 것이다.
// 다루는 범위: 삼각형 메시, 위치·법선·UV, 기본 색 텍스처(WebP 포함). 압축된 메시(Draco, meshopt)와 애니메이션은 다루지 않는다.
const GLB_ARRAY = { 5120: Int8Array, 5121: Uint8Array, 5122: Int16Array, 5123: Uint16Array, 5125: Uint32Array, 5126: Float32Array };
const GLB_WIDTH = { SCALAR: 1, VEC2: 2, VEC3: 3, VEC4: 4, MAT4: 16 };

function parseGlb(buf) {
  const dv = new DataView(buf);
  if (dv.byteLength < 20 || dv.getUint32(0, true) !== 0x46546c67) throw new Error("GLB 파일이 아닙니다");
  let at = 12, json = null, bin = null;
  while (at + 8 <= dv.byteLength) {
    const len = dv.getUint32(at, true), kind = dv.getUint32(at + 4, true);
    const chunk = new Uint8Array(buf, at + 8, len);
    if (kind === 0x4e4f534a) json = JSON.parse(new TextDecoder().decode(chunk));
    else if (kind === 0x004e4942 && !bin) bin = chunk;
    at += 8 + len;
  }
  if (!json || !bin) throw new Error("GLB 안에 장면 정보나 데이터가 없습니다");
  return { json, bin };
}

// 접근자(accessor) 하나를 형식 있는 배열로 읽는다
function glbAccessor(g, index) {
  const a = g.json.accessors[index], view = g.json.bufferViews[a.bufferView];
  const A = GLB_ARRAY[a.componentType], width = GLB_WIDTH[a.type];
  if (!A || !width || !view) throw new Error("읽을 수 없는 메시 데이터입니다");
  if (view.byteStride && view.byteStride !== width * A.BYTES_PER_ELEMENT) throw new Error("여러 속성을 한 버퍼에 섞어 둔 GLB 는 미리보기를 지원하지 않습니다");
  const start = g.bin.byteOffset + (view.byteOffset || 0) + (a.byteOffset || 0), n = a.count * width;
  return start % A.BYTES_PER_ELEMENT ? new A(g.bin.buffer.slice(start, start + n * A.BYTES_PER_ELEMENT)) : new A(g.bin.buffer, start, n);
}

// 4×4 행렬. WebGL 과 같은 열 우선 순서
const M4 = {
  ident: () => new Float32Array([1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1]),
  mul(a, b) {
    const o = new Float32Array(16);
    for (let c = 0; c < 4; c++) for (let r = 0; r < 4; r++)
      o[c * 4 + r] = a[r] * b[c * 4] + a[4 + r] * b[c * 4 + 1] + a[8 + r] * b[c * 4 + 2] + a[12 + r] * b[c * 4 + 3];
    return o;
  },
  trs(t = [0, 0, 0], q = [0, 0, 0, 1], s = [1, 1, 1]) {
    const [x, y, z, w] = q;
    return new Float32Array([
      (1 - 2 * (y * y + z * z)) * s[0], 2 * (x * y + z * w) * s[0], 2 * (x * z - y * w) * s[0], 0,
      2 * (x * y - z * w) * s[1], (1 - 2 * (x * x + z * z)) * s[1], 2 * (y * z + x * w) * s[1], 0,
      2 * (x * z + y * w) * s[2], 2 * (y * z - x * w) * s[2], (1 - 2 * (x * x + y * y)) * s[2], 0,
      t[0], t[1], t[2], 1]);
  },
  point: (m, p) => [m[0] * p[0] + m[4] * p[1] + m[8] * p[2] + m[12], m[1] * p[0] + m[5] * p[1] + m[9] * p[2] + m[13], m[2] * p[0] + m[6] * p[1] + m[10] * p[2] + m[14]],
  perspective(fovY, aspect, near, far) {
    const f = 1 / Math.tan(fovY / 2);
    return new Float32Array([f / aspect, 0, 0, 0, 0, f, 0, 0, 0, 0, (far + near) / (near - far), -1, 0, 0, (2 * far * near) / (near - far), 0]);
  },
  lookAt(eye, at, up) {
    const z = V3.unit(V3.sub(eye, at)), x = V3.unit(V3.cross(up, z)), y = V3.cross(z, x);
    return new Float32Array([x[0], y[0], z[0], 0, x[1], y[1], z[1], 0, x[2], y[2], z[2], 0, -V3.dot(x, eye), -V3.dot(y, eye), -V3.dot(z, eye), 1]);
  },
};
const V3 = {
  sub: (a, b) => [a[0] - b[0], a[1] - b[1], a[2] - b[2]],
  dot: (a, b) => a[0] * b[0] + a[1] * b[1] + a[2] * b[2],
  cross: (a, b) => [a[1] * b[2] - a[2] * b[1], a[2] * b[0] - a[0] * b[2], a[0] * b[1] - a[1] * b[0]],
  unit(a) { const n = Math.hypot(a[0], a[1], a[2]) || 1; return [a[0] / n, a[1] / n, a[2] / n]; },
};

// 장면의 노드를 따라 내려가며 그릴 삼각형 묶음과 그 위치 행렬을 모은다
function glbDraws(g) {
  const j = g.json, out = [];
  const roots = j.scenes?.[j.scene ?? 0]?.nodes ?? (j.nodes || []).map((_, i) => i);
  const walk = (i, parent, depth) => {
    const n = j.nodes?.[i];
    if (!n || depth > 64) return;
    const m = M4.mul(parent, n.matrix ? Float32Array.from(n.matrix) : M4.trs(n.translation, n.rotation, n.scale));
    if (n.mesh != null) for (const p of j.meshes[n.mesh].primitives) if ((p.mode ?? 4) === 4 && p.attributes.POSITION != null) out.push({ prim: p, model: m });
    (n.children || []).forEach((c) => walk(c, m, depth + 1));
  };
  roots.forEach((i) => walk(i, M4.ident(), 0));
  return out;
}

const GLB_VS = `#version 300 es
layout(location=0) in vec3 aPos; layout(location=1) in vec3 aNor; layout(location=2) in vec2 aUv;
uniform mat4 uMvp, uModel;
out vec3 vPos, vNor; out vec2 vUv;
void main() { vec4 w = uModel * vec4(aPos, 1.0); vPos = w.xyz; vNor = mat3(uModel) * aNor; vUv = aUv; gl_Position = uMvp * vec4(aPos, 1.0); }`;
const GLB_FS = `#version 300 es
precision highp float;
in vec3 vPos, vNor; in vec2 vUv;
uniform sampler2D uTex; uniform bool uHasTex, uHasNor; uniform vec4 uColor; uniform vec3 uLight; uniform int uMode;
out vec4 o;
void main() {
  vec3 n = uHasNor ? normalize(vNor) : normalize(cross(dFdx(vPos), dFdy(vPos)));
  if (!gl_FrontFacing) n = -n;
  vec3 base = uColor.rgb;
  if (uHasTex) base *= pow(texture(uTex, vUv).rgb, vec3(2.2));
  if (uMode == 1) base = vec3(0.55);
  float key = max(dot(n, uLight), 0.0), fill = max(dot(n, -uLight), 0.0);
  vec3 c = base * (0.36 + 0.78 * key + 0.14 * fill);
  o = vec4(pow(c, vec3(1.0 / 2.2)), 1.0);
}`;

async function glbViewer(canvas, buf) {
  const g = parseGlb(buf);
  const packed = (g.json.extensionsRequired || []).find((e) => /draco|meshopt/i.test(e));
  if (packed) throw new Error(`압축된 메시(${packed})는 미리보기를 지원하지 않습니다. 파일은 내려받아 다른 프로그램으로 열 수 있습니다.`);
  const gl = canvas.getContext("webgl2", { antialias: true, alpha: true });
  if (!gl) throw new Error("이 브라우저는 WebGL2 를 지원하지 않습니다");

  const shader = (type, src) => {
    const s = gl.createShader(type);
    gl.shaderSource(s, src);
    gl.compileShader(s);
    if (!gl.getShaderParameter(s, gl.COMPILE_STATUS)) throw new Error("셰이더를 만들지 못했습니다: " + gl.getShaderInfoLog(s));
    return s;
  };
  const prog = gl.createProgram();
  gl.attachShader(prog, shader(gl.VERTEX_SHADER, GLB_VS));
  gl.attachShader(prog, shader(gl.FRAGMENT_SHADER, GLB_FS));
  gl.linkProgram(prog);
  if (!gl.getProgramParameter(prog, gl.LINK_STATUS)) throw new Error("셰이더를 연결하지 못했습니다: " + gl.getProgramInfoLog(prog));
  const U = Object.fromEntries(["uMvp", "uModel", "uTex", "uHasTex", "uHasNor", "uColor", "uLight", "uMode"].map((n) => [n, gl.getUniformLocation(prog, n)]));

  const attrib = (accIndex, loc, width) => {
    const a = g.json.accessors[accIndex];
    gl.bindBuffer(gl.ARRAY_BUFFER, gl.createBuffer());
    gl.bufferData(gl.ARRAY_BUFFER, glbAccessor(g, accIndex), gl.STATIC_DRAW);
    gl.enableVertexAttribArray(loc);
    gl.vertexAttribPointer(loc, width, a.componentType, !!a.normalized, 0, 0);   // glTF 의 형식 번호는 WebGL 상수와 같다
  };

  const textures = new Map();
  let textureInfo = "";
  const texture = async (index) => {
    if (textures.has(index)) return textures.get(index);
    const t = g.json.textures?.[index], img = t && g.json.images?.[t.extensions?.EXT_texture_webp?.source ?? t.source];
    let tex = null;
    if (img && img.bufferView != null) {
      const v = g.json.bufferViews[img.bufferView];
      const bytes = new Uint8Array(g.bin.buffer, g.bin.byteOffset + (v.byteOffset || 0), v.byteLength);
      let bmp = await createImageBitmap(new Blob([bytes], { type: img.mimeType || "image/png" }), { premultiplyAlpha: "none", colorSpaceConversion: "none" });
      const full = `${bmp.width}×${bmp.height}`;
      // 미리보기에는 긴 변 2048 이면 충분하다. 4096 텍스처를 그대로 올리면 그래픽 메모리를 80MB 넘게 쓴다
      const cap = Math.min(gl.getParameter(gl.MAX_TEXTURE_SIZE), 2048), k = cap / Math.max(bmp.width, bmp.height);
      if (k < 1) {
        const small = await createImageBitmap(bmp, { resizeWidth: Math.round(bmp.width * k), resizeHeight: Math.round(bmp.height * k), resizeQuality: "high" });
        bmp.close();
        bmp = small;
      }
      textureInfo ||= k < 1 ? `${full} (미리보기는 ${bmp.width}×${bmp.height}로 줄여 그림)` : full;
      tex = gl.createTexture();
      gl.bindTexture(gl.TEXTURE_2D, tex);
      gl.pixelStorei(gl.UNPACK_FLIP_Y_WEBGL, false);
      gl.texImage2D(gl.TEXTURE_2D, 0, gl.RGBA, gl.RGBA, gl.UNSIGNED_BYTE, bmp);
      gl.generateMipmap(gl.TEXTURE_2D);
      gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_MIN_FILTER, gl.LINEAR_MIPMAP_LINEAR);
      gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_MAG_FILTER, gl.LINEAR);
      bmp.close();
    }
    textures.set(index, tex);
    return tex;
  };

  const draws = glbDraws(g);
  if (!draws.length) throw new Error("그릴 삼각형 메시가 없습니다");
  const lo = [Infinity, Infinity, Infinity], hi = [-Infinity, -Infinity, -Infinity], items = [];
  let vertices = 0, triangles = 0;
  for (const d of draws) {
    const p = d.prim, pa = g.json.accessors[p.attributes.POSITION];
    const vao = gl.createVertexArray();
    gl.bindVertexArray(vao);
    attrib(p.attributes.POSITION, 0, 3);
    const hasNor = p.attributes.NORMAL != null, hasUv = p.attributes.TEXCOORD_0 != null;
    if (hasNor) attrib(p.attributes.NORMAL, 1, 3);
    if (hasUv) attrib(p.attributes.TEXCOORD_0, 2, 2);
    let count = pa.count, indexType = 0;
    if (p.indices != null) {
      let idx = glbAccessor(g, p.indices);
      if (idx instanceof Uint8Array) idx = Uint16Array.from(idx);
      gl.bindBuffer(gl.ELEMENT_ARRAY_BUFFER, gl.createBuffer());
      gl.bufferData(gl.ELEMENT_ARRAY_BUFFER, idx, gl.STATIC_DRAW);
      count = idx.length;
      indexType = idx instanceof Uint32Array ? gl.UNSIGNED_INT : gl.UNSIGNED_SHORT;
    }
    gl.bindVertexArray(null);
    // 상자 크기: 접근자에 적힌 최소·최대를 쓰고, 없으면 꼭짓점을 훑는다
    let mn = pa.min, mx = pa.max;
    if (!mn || !mx) {
      const pos = glbAccessor(g, p.attributes.POSITION);
      mn = [Infinity, Infinity, Infinity]; mx = [-Infinity, -Infinity, -Infinity];
      for (let i = 0; i < pos.length; i += 3) for (let k = 0; k < 3; k++) { mn[k] = Math.min(mn[k], pos[i + k]); mx[k] = Math.max(mx[k], pos[i + k]); }
    }
    for (let c = 0; c < 8; c++) {
      const w = M4.point(d.model, [c & 1 ? mx[0] : mn[0], c & 2 ? mx[1] : mn[1], c & 4 ? mx[2] : mn[2]]);
      for (let k = 0; k < 3; k++) { lo[k] = Math.min(lo[k], w[k]); hi[k] = Math.max(hi[k], w[k]); }
    }
    const pbr = g.json.materials?.[p.material]?.pbrMetallicRoughness || {};
    const tex = hasUv && pbr.baseColorTexture ? await texture(pbr.baseColorTexture.index) : null;
    items.push({ vao, count, indexType, model: d.model, hasNor, tex, color: pbr.baseColorFactor || [1, 1, 1, 1] });
    vertices += pa.count;
    triangles += Math.floor(count / 3);
  }

  const center = [(lo[0] + hi[0]) / 2, (lo[1] + hi[1]) / 2, (lo[2] + hi[2]) / 2];
  const radius = Math.max(1e-6, Math.hypot(hi[0] - lo[0], hi[1] - lo[1], hi[2] - lo[2]) / 2);
  const FOV = (35 * Math.PI) / 180;
  const home = { yaw: 0.6, pitch: 0.22, dist: (radius / Math.sin(FOV / 2)) * 1.08 };
  const cam = { ...home };
  let mode = 0, frame = 0, dead = false, drag = null;

  const paint = () => {
    frame = 0;
    if (dead) return;
    const dpr = Math.min(window.devicePixelRatio || 1, 2);
    const w = Math.max(1, Math.round(canvas.clientWidth * dpr)), h = Math.max(1, Math.round(canvas.clientHeight * dpr));
    if (canvas.width !== w || canvas.height !== h) { canvas.width = w; canvas.height = h; }
    gl.viewport(0, 0, w, h);
    gl.clearColor(0, 0, 0, 0);
    gl.clear(gl.COLOR_BUFFER_BIT | gl.DEPTH_BUFFER_BIT);
    gl.enable(gl.DEPTH_TEST);
    const cp = Math.cos(cam.pitch);
    const eye = [center[0] + cam.dist * cp * Math.sin(cam.yaw), center[1] + cam.dist * Math.sin(cam.pitch), center[2] + cam.dist * cp * Math.cos(cam.yaw)];
    const vp = M4.mul(M4.perspective(FOV, w / h, Math.max(cam.dist - radius * 1.5, radius / 200), cam.dist + radius * 1.5), M4.lookAt(eye, center, [0, 1, 0]));
    // 빛은 카메라에서 살짝 위·오른쪽에 둔다. 어느 쪽으로 돌려도 보는 면이 밝다
    const back = V3.unit(V3.sub(eye, center)), right = V3.unit(V3.cross([0, 1, 0], back)), up = V3.cross(back, right);
    const light = V3.unit([back[0] + 0.5 * up[0] + 0.4 * right[0], back[1] + 0.5 * up[1] + 0.4 * right[1], back[2] + 0.5 * up[2] + 0.4 * right[2]]);
    gl.useProgram(prog);
    gl.uniform3fv(U.uLight, light);
    gl.uniform1i(U.uMode, mode);
    gl.uniform1i(U.uTex, 0);
    gl.activeTexture(gl.TEXTURE0);
    for (const it of items) {
      gl.uniformMatrix4fv(U.uMvp, false, M4.mul(vp, it.model));
      gl.uniformMatrix4fv(U.uModel, false, it.model);
      gl.uniform4fv(U.uColor, it.color);
      gl.uniform1i(U.uHasNor, it.hasNor ? 1 : 0);
      gl.uniform1i(U.uHasTex, it.tex ? 1 : 0);
      gl.bindTexture(gl.TEXTURE_2D, it.tex);
      gl.bindVertexArray(it.vao);
      if (it.indexType) gl.drawElements(gl.TRIANGLES, it.count, it.indexType, 0);
      else gl.drawArrays(gl.TRIANGLES, 0, it.count);
    }
    gl.bindVertexArray(null);
  };
  const ask = () => { if (!frame && !dead) frame = requestAnimationFrame(paint); };

  const clamp = (v, a, b) => Math.min(b, Math.max(a, v));
  const onDown = (e) => { drag = { x: e.clientX, y: e.clientY }; canvas.setPointerCapture?.(e.pointerId); };
  const onMove = (e) => {
    if (!drag) return;
    cam.yaw -= (e.clientX - drag.x) * 0.008;
    cam.pitch = clamp(cam.pitch + (e.clientY - drag.y) * 0.008, -1.5, 1.5);
    drag = { x: e.clientX, y: e.clientY };
    ask();
  };
  const onUp = () => { drag = null; };
  const onWheel = (e) => { e.preventDefault(); cam.dist = clamp(cam.dist * Math.exp(e.deltaY * 0.0012), radius * 0.2, radius * 12); ask(); };
  const onReset = () => { Object.assign(cam, home); ask(); };
  canvas.addEventListener("pointerdown", onDown);
  canvas.addEventListener("pointermove", onMove);
  canvas.addEventListener("pointerup", onUp);
  canvas.addEventListener("pointercancel", onUp);
  canvas.addEventListener("wheel", onWheel, { passive: false });
  canvas.addEventListener("dblclick", onReset);
  const watcher = new ResizeObserver(ask);
  watcher.observe(canvas);
  ask();

  return {
    stats: { vertices, triangles, texture: textureInfo },
    setMode(m) { mode = m; ask(); },
    dispose() {
      dead = true;
      cancelAnimationFrame(frame);
      watcher.disconnect();
      for (const [name, fn] of [["pointerdown", onDown], ["pointermove", onMove], ["pointerup", onUp], ["pointercancel", onUp], ["wheel", onWheel], ["dblclick", onReset]]) canvas.removeEventListener(name, fn);
      gl.getExtension("WEBGL_lose_context")?.loseContext();   // 메시와 텍스처가 잡은 그래픽 메모리를 바로 돌려준다
    },
  };
}

// ═════════════════════════ 페이지: 파일 ═════════════════════════
const AREAS = { home: ["홈", "/home/dev"], scratch: ["스크래치", "/scratch"], shared: ["공용", "/shared"] };
const Files = {
  render() {
    $("#page").innerHTML = pageHead("파일", "워크스페이스와 작업에서 보이는 것과 같은 파일입니다. 큰 폴더는 rsync나 scp로 옮기는 편이 빠릅니다.",
      `<button class="btn" id="f-mkdir">${icon("folder")}새 폴더</button><label class="btn primary" id="f-up">${icon("upload")}업로드<input type="file" id="f-input" multiple hidden></label>`) + `
      <section class="panel flush">
        <div class="toolbar">
          <div class="seg" id="f-area">${Object.entries(AREAS).map(([k, [l]]) => `<button data-a="${k}" class="${S.area === k ? "on" : ""}">${l}</button>`).join("")}</div>
          <div class="path" id="f-path"></div>
          <div class="right"><div class="search">${icon("search")}<input class="input" id="f-q" placeholder="이 폴더에서 찾기" value="${esc(S.fileQuery)}"></div></div>
        </div>
        <div class="toolbar" id="f-quota" style="font-size:12.5px;color:var(--muted)"></div>
        <div class="drop" id="f-drop"><div class="table-wrap"><table class="dt" id="f-table"></table></div><div class="drop-hint">놓으면 이 폴더에 업로드합니다</div></div>
      </section>`;
    $("#f-area").onclick = (e) => {
      const b = e.target.closest("[data-a]"); if (!b) return;
      S.area = b.dataset.a; S.path = "";
      $$("#f-area button").forEach((x) => x.classList.toggle("on", x === b));
      Files.load();
    };
    $("#f-q").oninput = (e) => { S.fileQuery = e.target.value; Files.table(); };
    $("#f-mkdir").onclick = async () => {
      const name = prompt("새 폴더 이름");
      if (!name) return;
      try { await api("/api/files/mkdir", { method: "POST", json: { area: S.area, path: S.path, name } }); toast(`폴더 '${name}'을 만들었습니다`); Files.load(); } catch (e) { fail(e); }
    };
    $("#f-input").onchange = (e) => { Files.upload(e.target.files); e.target.value = ""; };
    const dz = $("#f-drop");
    dz.ondragover = (e) => { if (S.files?.writable) { e.preventDefault(); dz.classList.add("over"); } };
    dz.ondragleave = (e) => { if (!dz.contains(e.relatedTarget)) dz.classList.remove("over"); };
    dz.ondrop = (e) => { e.preventDefault(); dz.classList.remove("over"); if (S.files?.writable && e.dataTransfer.files.length) Files.upload(e.dataTransfer.files); };
    Files.load();
  },
  refresh() { /* 파일 목록은 자동으로 새로고침하지 않는다 (탐색 중 깜빡임 방지) */ },
  async load() {
    try { S.files = await api(`/api/files?area=${S.area}&path=${encodeURIComponent(S.path)}`); }
    catch (e) { fail(e); S.path = ""; return; }
    S.path = S.files.path;
    $("#f-mkdir").classList.toggle("hidden", !S.files.writable);
    $("#f-up").classList.toggle("hidden", !S.files.writable);
    const parts = S.path ? S.path.split("/") : [];
    $("#f-path").innerHTML = `<button data-p="">${AREAS[S.area][1]}</button>` +
      parts.map((p, i) => `<span>/</span><button data-p="${esc(parts.slice(0, i + 1).join("/"))}">${esc(p)}</button>`).join("");
    $("#f-path").onclick = (e) => { const b = e.target.closest("[data-p]"); if (b) { S.path = b.dataset.p; Files.load(); } };
    Files.table();
    api("/api/files/usage").then((u) => {
      const p = pct(u.home_used, u.quota);
      $("#f-quota").innerHTML = `<span>홈 사용량 ${fmtBytes(u.home_used)} / ${fmtBytes(u.quota)}</span><span style="width:160px">${bar(p)}</span><span>스크래치 ${fmtBytes(u.scratch_used)}</span>
        ${S.files.writable ? "" : '<span class="right badge plain">읽기 전용 영역</span>'}`;
    }).catch(() => {});
  },
  table() {
    const d = S.files; if (!d) return;
    const q = S.fileQuery.trim().toLowerCase();
    const ents = d.entries.filter((f) => !f.name.endsWith(".uploading") && (!q || f.name.toLowerCase().includes(q)));
    const s = S.fileSort;
    const rows = sortRows(ents, { name: (f) => (f.is_dir ? "0" : "1") + f.name.toLowerCase(), size: "size", mtime: "mtime" }[s.key], s.dir);
    const t = $("#f-table");
    if (!d.entries.length) {
      t.innerHTML = `<tbody><tr><td><div class="empty"><b>빈 폴더입니다</b>${d.writable ? "파일을 여기로 끌어다 놓거나 업로드 버튼을 쓰세요." : "관리자가 아직 아무것도 올리지 않았습니다."}</div></td></tr></tbody>`;
      return;
    }
    t.innerHTML = `<thead><tr>${th("이름", "name", s)}${th("크기", "size", s, "r")}${th("수정한 날짜", "mtime", s)}<th class="r"></th></tr></thead><tbody>
      ${rows.map((f) => {
        const rel = (d.path ? d.path + "/" : "") + f.name;
        return `<tr class="click" data-rel="${esc(rel)}" data-dir="${f.is_dir ? 1 : 0}">
          <td><span class="fname ${f.is_dir ? "dir" : ""}">${icon(f.is_dir ? "folder" : f.is_link ? "link" : "file")}${esc(f.name)}</span></td>
          <td class="r">${f.is_dir ? '<span class="muted">—</span>' : fmtBytes(f.size)}</td>
          <td class="muted">${when(f.mtime)}</td>
          <td class="r"><span class="row-actions">
            ${f.is_dir ? "" : `<button class="icon-btn" data-dl="${esc(rel)}" title="다운로드">${icon("download")}</button>`}
            ${d.writable ? `<button class="icon-btn" data-del="${esc(rel)}" data-isdir="${f.is_dir ? 1 : 0}" title="삭제">${icon("trash")}</button>` : ""}</span></td></tr>`;
      }).join("") || `<tr><td colspan="4"><div class="empty">'${esc(q)}'와 일치하는 항목이 없습니다</div></td></tr>`}</tbody>`;
    bindSort(t, S.fileSort, () => Files.table());
    t.querySelectorAll("tr[data-rel]").forEach((r) => (r.onclick = (e) => {
      const dl = e.target.closest("[data-dl]"), del = e.target.closest("[data-del]");
      if (dl) return Files.download(dl.dataset.dl);
      if (del) return Files.remove(del.dataset.del, del.dataset.isdir === "1");
      if (r.dataset.dir === "1") { S.path = r.dataset.rel; S.fileQuery = ""; $("#f-q").value = ""; Files.load(); }
      else Files.download(r.dataset.rel);
    }));
  },
  async download(rel) {
    try {
      const res = await api(`/api/files/download?area=${S.area}&path=${encodeURIComponent(rel)}`, { raw: true });
      const a = document.createElement("a");
      a.href = URL.createObjectURL(await res.blob());
      a.download = rel.split("/").pop();
      a.click();
      setTimeout(() => URL.revokeObjectURL(a.href), 5000);
    } catch (e) { fail(e); }
  },
  async remove(rel, isDir) {
    if (!(await confirmDialog(`'${rel}'${isDir ? " 폴더와 그 안의 모든 파일" : ""}을 삭제할까요? 되돌릴 수 없습니다.`, { ok: "삭제", danger: true }))) return;
    try { await api(`/api/files?area=${S.area}&path=${encodeURIComponent(rel)}&recursive=${isDir}`, { method: "DELETE" }); toast(`'${rel.split("/").pop()}'을 삭제했습니다`); Files.load(); }
    catch (e) { fail(e); }
  },
  async upload(list) {
    const files = [...list];
    for (const [i, f] of files.entries()) {
      toast(`업로드 중 ${i + 1}/${files.length}: ${f.name}`);
      const fd = new FormData();
      fd.append("area", S.area); fd.append("path", S.path); fd.append("file", f, f.name);
      try { await api("/api/files/upload", { method: "POST", body: fd }); }
      catch (e) { fail(new Error(`${f.name}: ${e.message}`)); break; }
    }
    toast(`${files.length}개 파일을 업로드했습니다`);
    Files.load();
  },
};

// ═════════════════════════ 페이지: 사용자 (관리자) ═════════════════════════
const Users = {
  render() {
    $("#page").innerHTML = pageHead("사용자", "계정과 할당량을 관리합니다. CPU와 메모리 변경은 사용자가 워크스페이스를 다시 시작하면 적용됩니다.",
      `<button class="btn primary" id="u-add">${icon("plus")}사용자 추가</button>`) + `
      <section class="panel flush"><div class="toolbar"><div class="search">${icon("search")}<input class="input" id="u-q" placeholder="이름 또는 Tailscale 계정" value="${esc(S.adminQuery)}"></div>
        <div class="right muted" id="u-count"></div></div>
        <div class="table-wrap"><table class="dt" id="u-table"></table></div></section>`;
    $("#u-add").onclick = openCreateUser;
    $("#u-q").oninput = (e) => { S.adminQuery = e.target.value; Users.table(); };
  },
  onParam(name) { if (name) openUser(name); },
  async refresh() { S.adminUsers = await api("/api/admin/users"); Users.table(); },
  table() {
    const q = S.adminQuery.trim().toLowerCase();
    const live = Object.fromEntries((S.overview?.users || []).map((u) => [u.name, u]));
    let rows = S.adminUsers.filter((u) => !q || `${u.name} ${u.tailscale_login || ""}`.toLowerCase().includes(q));
    const s = S.adminSort;
    rows = sortRows(rows, { name: "name", role: "role", cpus: "cpus", mem: "mem_gb", disk: "disk_gb", vram: (u) => u.max_vram_gb ?? 999, port: "ssh_port", seen: "last_seen" }[s.key], s.dir);
    $("#u-count").textContent = `${S.adminUsers.length}명`;
    const t = $("#u-table");
    t.innerHTML = `<thead><tr>${th("사용자", "name", s)}${th("권한", "role", s)}<th>상태</th><th>Tailscale 계정</th>${th("CPU", "cpus", s, "r")}${th("메모리", "mem", s, "r")}${th("홈 디스크", "disk", s, "r")}${th("VRAM 한도", "vram", s, "r")}${th("SSH 포트", "port", s, "r")}${th("최근 접속", "seen", s)}</tr></thead>
      <tbody>${rows.map((u) => {
        const l = live[u.name];
        const st = u.disabled ? '<span class="badge bad">비활성</span>' : l?.active ? '<span class="badge ok">활동 중</span>' : '<span class="badge">오프라인</span>';
        return `<tr class="click" data-name="${esc(u.name)}"><td>${userCell(u.name)}</td>
          <td>${u.role === "admin" ? "관리자" : "사용자"}</td><td>${st}</td>
          <td class="${u.tailscale_login ? "" : "muted"}">${esc(u.tailscale_login || "연결 안 됨")}</td>
          <td class="r">${u.cpus}코어</td><td class="r">${u.mem_gb} GB</td><td class="r">${u.disk_gb} GB</td>
          <td class="r">${u.max_vram_gb ? u.max_vram_gb + " GB" : '<span class="muted">제한 없음</span>'}</td>
          <td class="r mono">${u.ssh_port}${u.has_ssh_key ? "" : ' <span class="badge warn plain">키 없음</span>'}</td>
          <td class="muted">${ago(u.last_seen)}</td></tr>`;
      }).join("")}</tbody>`;
    bindSort(t, S.adminSort, () => Users.table());
    t.querySelectorAll("tr[data-name]").forEach((r) => (r.onclick = () => (S.page === "users" ? go("users", r.dataset.name) : openUser(r.dataset.name))));
  },
};

function userFormHtml(u) {
  const v = (k, d = "") => esc(u && u[k] != null ? u[k] : d);
  return `<form id="user-form" class="form-grid">
    ${u ? "" : `<div class="field span2"><label>이름</label><input class="input" name="name" required pattern="[a-z][a-z0-9_\\-]{1,31}" placeholder="seoyeon"><span class="hint">영문 소문자로 시작하는 2~32자. 나중에 바꿀 수 없습니다.</span></div>`}
    <div class="field"><label>권한</label><select class="input" name="role"><option value="user">사용자</option><option value="admin" ${u?.role === "admin" ? "selected" : ""}>관리자</option></select></div>
    <div class="field"><label>Tailscale 계정</label><input class="input" name="tailscale_login" value="${v("tailscale_login")}" placeholder="name@github"><span class="hint">연결하면 토큰 없이 로그인합니다</span></div>
    <div class="field"><label>CPU 코어</label><input class="input" name="cpus" type="number" step="0.5" min="0.5" value="${v("cpus", 8)}">${S.overview ? `<span class="hint">이 서버는 ${S.overview.host.cores}스레드</span>` : ""}</div>
    <div class="field"><label>메모리 (GB)</label><input class="input" name="mem_gb" type="number" min="1" value="${v("mem_gb", 16)}">${S.overview ? `<span class="hint">이 서버는 ${(S.overview.host.mem_total / GiB).toFixed(1)} GB. 실제 한도는 여기서 2 GB를 뺀 값을 넘지 않습니다</span>` : ""}</div>
    <div class="field"><label>홈 디스크 (GB)</label><input class="input" name="disk_gb" type="number" min="1" value="${v("disk_gb", 200)}"></div>
    <div class="field"><label>VRAM 한도 (GB)</label><input class="input" name="max_vram_gb" type="number" min="1" step="1" value="${v("max_vram_gb")}" placeholder="제한 없음"></div>
    ${u ? `<label class="check span2"><input type="checkbox" name="disabled" ${u.disabled ? "checked" : ""}><span>계정 비활성화<br><span class="muted">로그인을 막고 실행 중인 워크스페이스를 중지합니다. 파일은 지우지 않습니다.</span></span></label>` : ""}
  </form>`;
}
function readUserForm(isNew) {
  const f = $("#user-form").elements;
  const num = (k) => (f[k].value === "" ? null : Number(f[k].value));
  const out = { role: f.role.value, tailscale_login: f.tailscale_login.value.trim() || null, cpus: num("cpus"), mem_gb: num("mem_gb"), disk_gb: num("disk_gb"), max_vram_gb: num("max_vram_gb") };
  if (isNew) out.name = f.name.value.trim(); else out.disabled = f.disabled.checked;
  return out;
}
function tokenPanel(name, token) {
  $("#drawer-body").innerHTML = `<div class="callout warn">${icon("key")}<div>이 토큰은 지금 한 번만 보입니다. 창을 닫기 전에 ${esc(name)} 님에게 안전한 경로로 전달하세요.</div></div>
    <div class="field"><label>${esc(name)} 의 접근 토큰</label><div class="copy"><code>${esc(token)}</code><button class="btn sm" data-copy="${esc(token)}">복사</button></div></div>
    <div class="section-title">사용자에게 보낼 안내</div>
    <div class="copy"><code style="white-space:pre-wrap">ironlab login --url https://${esc(S.me.public_host || "서버주소")} --token ${esc(token)}</code></div>`;
  $("#drawer-foot").innerHTML = `<button class="btn primary" data-drawer-close>완료</button>`;
  $("#drawer-foot [data-drawer-close]").onclick = () => closeDrawer();
}
function openCreateUser() {
  openDrawer({
    title: "사용자 추가", sub: "계정을 만들면 홈 디렉터리와 SSH 포트가 자동으로 준비됩니다.",
    body: userFormHtml(null),
    foot: `<button class="btn" data-drawer-close>취소</button><button class="btn primary" id="u-create">사용자 만들기</button>`,
  });
  const create = async (e) => {
    e?.preventDefault();
    if (!$("#user-form").reportValidity()) return;
    try {
      const r = await api("/api/admin/users", { method: "POST", json: readUserForm(true) });
      toast(`사용자 ${r.name}을 만들었습니다`);
      tokenPanel(r.name, r.token);
      refresh();
    } catch (err) { fail(err); }
  };
  $("#u-create").onclick = create;
  $("#user-form").onsubmit = create;
}
async function openUser(name) {
  if (!S.adminUsers.length) S.adminUsers = await api("/api/admin/users").catch(() => []);
  const u = S.adminUsers.find((x) => x.name === name);
  if (!u) return;
  if (S.page === "users") renderCrumbs(name);
  openDrawer({
    title: userCell(u.name), sub: `UID ${u.uid}, SSH 포트 ${u.ssh_port}, ${ago(u.created_at)} 생성`,
    body: userFormHtml(u),
    foot: `<span class="left" style="display:flex;gap:8px"><button class="btn" id="u-token">토큰 재발급</button><button class="btn" id="u-wsstop">워크스페이스 중지</button></span>
      <button class="btn" data-drawer-close>취소</button><button class="btn primary" id="u-save">변경 저장</button>`,
    onClose: () => { if (S.page === "users") { go("users"); renderCrumbs(); } },
  });
  $("#u-save").onclick = async () => {
    try { await api(`/api/admin/users/${u.name}`, { method: "PATCH", json: readUserForm(false) }); toast(`${u.name}의 설정을 저장했습니다`); closeDrawer(); refresh(); }
    catch (e) { fail(e); }
  };
  $("#u-token").onclick = async () => {
    if (!(await confirmDialog(`${u.name}의 토큰을 재발급할까요? 지금 쓰는 토큰은 바로 무효가 됩니다.`, { ok: "재발급", danger: true }))) return;
    try { const r = await api(`/api/admin/users/${u.name}/token`, { method: "POST" }); tokenPanel(r.name, r.token); } catch (e) { fail(e); }
  };
  $("#u-wsstop").onclick = async () => {
    if (!(await confirmDialog(`${u.name}의 워크스페이스를 중지할까요?`, { ok: "중지", danger: true }))) return;
    try { const r = await api(`/api/admin/workspaces/${u.name}/stop`, { method: "POST" }); toast(r.stopped ? "워크스페이스를 중지했습니다" : "실행 중인 워크스페이스가 없습니다"); refresh(); } catch (e) { fail(e); }
  };
}

// ═════════════════════════ 페이지: 감사 로그 ═════════════════════════
const EV_KINDS = { all: "전체", user: "계정", workspace: "워크스페이스", job: "작업", alert: "알림" };
const evTone = (k) => (k.includes("fail") || k === "alert" || k.includes("overuse") ? "bad" : k.includes("cancel") || k.includes("idle") ? "warn" : k.includes("succeeded") || k.includes("start") ? "ok" : "");
const Audit = {
  render() {
    $("#page").innerHTML = pageHead("감사 로그", "계정 변경, 워크스페이스, 작업, 알림 기록입니다. 최근 500건을 보여줍니다.") + `
      <section class="panel flush"><div class="toolbar">
        <div class="search">${icon("search")}<input class="input" id="ev-q" placeholder="사용자, 내용 검색" value="${esc(S.evQuery)}"></div>
        <div class="chips" id="ev-chips"></div></div>
        <div class="table-wrap"><table class="dt" id="ev-table"></table></div></section>`;
    $("#ev-q").oninput = (e) => { S.evQuery = e.target.value; Audit.table(); };
    $("#ev-chips").onclick = (e) => { const c = e.target.closest("[data-k]"); if (c) { S.evKind = c.dataset.k; Audit.table(); } };
  },
  async refresh() { S.events = await api("/api/admin/events?limit=500"); Audit.table(); },
  table() {
    const q = S.evQuery.trim().toLowerCase();
    const rows = S.events.filter((e) => (S.evKind === "all" || e.kind.split(".")[0] === S.evKind) && (!q || `${e.user || ""} ${e.kind} ${e.detail}`.toLowerCase().includes(q)));
    $("#ev-chips").innerHTML = Object.entries(EV_KINDS).map(([k, l]) => `<button class="chip ${S.evKind === k ? "on" : ""}" data-k="${k}">${l}</button>`).join("");
    $("#ev-table").innerHTML = `<thead><tr><th>시각</th><th>사용자</th><th>이벤트</th><th>내용</th></tr></thead><tbody>
      ${rows.map((e) => `<tr><td class="muted">${when(e.ts)}</td><td>${e.user ? userCell(e.user) : '<span class="muted">시스템</span>'}</td>
        <td><span class="badge ${evTone(e.kind)} plain mono" style="font-size:11.5px">${esc(e.kind)}</span></td><td class="wrap">${esc(e.detail)}</td></tr>`).join("")
        || '<tr><td colspan="4"><div class="empty">조건에 맞는 기록이 없습니다</div></td></tr>'}</tbody>`;
  },
};

// ═════════════════════════ 페이지: 내 계정 ═════════════════════════
const Account = {
  render() {
    const m = S.me;
    $("#page").innerHTML = pageHead("내 계정", "로그인 정보와 SSH 공개키를 관리합니다.") + `
      <div class="grid-2" style="margin-bottom:16px">
        <section class="panel"><div class="panel-head"><h2>계정</h2></div><div class="panel-body"><dl class="dl">
          <dt>이름</dt><dd>${userCell(m.name)}</dd><dt>권한</dt><dd>${m.role === "admin" ? "관리자" : "사용자"}</dd>
          <dt>Tailscale 계정</dt><dd>${esc(m.tailscale_login || "연결 안 됨")}</dd><dt>SSH 포트</dt><dd class="mono">${m.ssh_port}</dd></dl></div></section>
        <section class="panel"><div class="panel-head"><h2>할당량</h2></div><div class="panel-body"><dl class="dl">
          <dt>CPU</dt><dd>${m.cpus}코어</dd><dt>메모리</dt><dd>${m.mem_gb} GB</dd><dt>홈 디스크</dt><dd>${m.disk_gb} GB</dd>
          <dt>VRAM 한도</dt><dd>${m.max_vram_gb ? m.max_vram_gb + " GB" : "제한 없음 (GPU 전체)"}</dd></dl></div></section>
      </div>
      <section class="panel"><div class="panel-head"><h2>SSH 공개키</h2><span class="sub" id="key-state"></span></div><div class="panel-body">
        <div class="field"><label for="keys">새 키 목록</label>
          <textarea class="input" id="keys" rows="4" placeholder="ssh-ed25519 AAAA... you@laptop"></textarea>
          <span class="hint">저장하면 기존 키 목록을 바꿉니다. 키가 여러 개면 한 줄에 하나씩. 노트북에서 <code>cat ~/.ssh/id_ed25519.pub</code> 출력을 붙여 넣으세요. 워크스페이스를 다시 시작해야 적용됩니다.</span></div>
        <div style="margin-top:12px"><button class="btn primary" id="keys-save">${icon("key")}키 저장</button></div></div></section>`;
    $("#key-state").innerHTML = m.has_ssh_key ? '<span class="badge ok">등록됨</span>' : '<span class="badge warn">등록 안 됨</span>';
    $("#keys-save").onclick = async () => {
      try {
        const r = await api("/api/me/ssh-keys", { method: "PUT", json: { keys: $("#keys").value } });
        toast(`SSH 키 ${r.count}개를 저장했습니다`);
        S.me = await api("/api/me");
        Account.render();
      } catch (e) { fail(e); }
    };
  },
};

// ═════════════════════════ 페이지: 로컬 LLM ═════════════════════════
const LLM_STATE = { ready: ["사용 가능", "ok"], loading: ["올리는 중", "warn run"], off: ["내려가 있음", ""], error: ["오류", "bad"] };
S.chat = [];
S.chatAbort = null;
S.showThinking = store.get("ironlab_thinking", "0") === "1";
S.llmRange = "today";

function fmtMsg(text) {
  // 코드 블록만 구분하는 최소한의 표시 (나머지는 줄바꿈 유지)
  return esc(text).replace(/```(\w*)\n?([\s\S]*?)(```|$)/g, (_, lang, code) => `<pre class="chat-code">${code}</pre>`);
}

const Llm = {
  render() {
    $("#page").innerHTML = pageHead("로컬 LLM", "서버에 올린 언어 모델 하나를 모두가 함께 씁니다. 올라가 있는 동안 GPU 메모리를 크게 차지하므로, 아무도 쓰지 않으면 자동으로 내려갑니다.",
      `<span id="llm-actions" style="display:flex;gap:8px;align-items:center"></span>`) + `
      <div id="llm-callout"></div>
      <div class="grid-2" style="margin-bottom:16px">
        <section class="panel"><div class="panel-head"><h2>상태</h2></div><div class="panel-body"><dl class="dl" id="llm-status"></dl></div></section>
        <section class="panel"><div class="panel-head"><h2>코드에서 쓰기</h2><span class="sub">OpenAI 호환 API</span></div><div class="panel-body stack-16" id="llm-connect"></div></section>
      </div>
      <section class="panel chat-panel">
        <div class="panel-head"><h2>채팅</h2><span class="sub" id="chat-sub"></span>
          <div class="right"><label class="check" style="font-size:12.5px"><input type="checkbox" id="chat-think" ${S.showThinking ? "checked" : ""}>생각 과정 보기</label>
          <button class="btn sm" id="chat-clear">새 대화</button></div></div>
        <div class="chat-log" id="chat-log"></div>
        <form class="chat-form" id="chat-form">
          <textarea class="input" id="chat-input" rows="2" placeholder="메시지를 입력하세요. Enter로 보내고 Shift+Enter로 줄을 바꿉니다."></textarea>
          <button class="btn primary" id="chat-send" type="submit">${icon("send")}보내기</button>
        </form>
      </section>
      <section class="panel flush"><div class="panel-head"><h2>사용량</h2>
        <div class="right"><div class="seg" id="llm-range"><button data-r="today" class="${S.llmRange === "today" ? "on" : ""}">오늘</button><button data-r="week" class="${S.llmRange === "week" ? "on" : ""}">7일</button></div></div></div>
        <div class="panel-body table-wrap"><table class="dt" id="llm-usage"></table></div></section>
      ${S.me.role === "admin" ? `<section class="panel flush"><div class="panel-head"><h2>모델</h2><span class="sub">관리자만 보입니다</span>
        <div class="right"><button class="btn sm primary" id="llm-add">${icon("plus")}모델 등록</button></div></div>
        <div class="panel-body table-wrap"><table class="dt" id="llm-models"></table></div></section>` : ""}`;
    Llm.renderChat();
    $("#chat-form").onsubmit = (e) => { e.preventDefault(); S.chatAbort ? S.chatAbort.abort() : Llm.send(); };
    $("#chat-input").onkeydown = (e) => { if (e.key === "Enter" && !e.shiftKey && !e.isComposing) { e.preventDefault(); $("#chat-form").requestSubmit(); } };
    $("#chat-clear").onclick = () => { S.chatAbort?.abort(); S.chat = []; Llm.renderChat(); };
    $("#chat-think").onchange = (e) => { S.showThinking = e.target.checked; store.set("ironlab_thinking", S.showThinking ? "1" : "0"); Llm.renderChat(); };
    $("#llm-range").onclick = (e) => {
      const b = e.target.closest("[data-r]"); if (!b) return;
      S.llmRange = b.dataset.r; $$("#llm-range button").forEach((x) => x.classList.toggle("on", x === b)); Llm.usage();
    };
    if ($("#llm-add")) $("#llm-add").onclick = () => openRegisterModel();
  },
  async refresh() {
    S.llm = await api("/api/llm");
    const L = S.llm, m = L.model;
    const [label, cls] = LLM_STATE[L.state] || [L.state, ""];
    const canUnload = S.me.role === "admin" || L.loaded_by === S.me.name;

    // 버튼
    if (L.state === "off" || L.state === "error") {
      $("#llm-actions").innerHTML = L.models.length ? `<select class="input" id="llm-pick" style="width:auto">${L.models.map((x) =>
        `<option value="${esc(x.name)}">${esc(x.name)} (${x.vram_gb} GB)</option>`).join("")}</select>
        <button class="btn primary" id="llm-load">${icon("play")}모델 올리기</button>` : "";
      if ($("#llm-load")) $("#llm-load").onclick = Llm.load;
    } else {
      $("#llm-actions").innerHTML = canUnload ? `<button class="btn" id="llm-unload">${icon("stop")}내리기</button>` : "";
      if ($("#llm-unload")) $("#llm-unload").onclick = Llm.unload;
    }

    // 안내
    const idle = L.idle_unload_min;
    $("#llm-callout").innerHTML =
      L.state === "error" ? `<div class="callout bad">${icon("alert")}<div><b>llama-server가 멈췄습니다.</b><br>${esc(L.error || "")}<br>관리자가 모델 설정을 확인한 뒤 다시 올려 주세요.</div></div>`
      : (L.state === "ready" || L.state === "loading") && m ? `<div class="callout warn">${icon("info")}<div>LLM이 VRAM ${m.vram_gb} GB를 쓰는 동안 큰 3D 작업은 대기합니다.${idle > 0 ? ` ${idle}분 동안 아무도 쓰지 않으면 자동으로 내려갑니다.` : ""}</div></div>`
      : !L.models.length ? `<div class="callout info">${icon("info")}<div>등록된 모델이 없습니다. ${S.me.role === "admin" ? "아래 <b>모델</b> 패널에서 등록하세요." : "관리자에게 모델 등록을 요청하세요."}</div></div>`
      : `<div class="callout info">${icon("info")}<div>모델이 내려가 있습니다. 올리는 데 보통 30초에서 1분쯤 걸립니다. 지금 GPU 여유는 <b>${L.budget_gb} GB</b>입니다.</div></div>`;

    // 상태
    const left = L.state === "ready" && L.last_used && idle > 0 ? idle * 60 - (now() - L.last_used) : null;
    $("#llm-status").innerHTML = `
      <dt>상태</dt><dd><span class="badge ${cls}">${label}</span></dd>
      <dt>모델</dt><dd>${m ? `<b>${esc(m.name)}</b>` : "—"}</dd>
      <dt>파일</dt><dd class="mono" style="font-size:12px;word-break:break-all">${m ? esc(m.file) : "—"}</dd>
      <dt>컨텍스트</dt><dd>${m ? `${m.ctx.toLocaleString()} 토큰을 ${m.parallel}개 요청이 나눠 씀 (요청당 ${Math.floor(m.ctx / m.parallel).toLocaleString()})` : "—"}</dd>
      <dt>VRAM</dt><dd>${m ? `${m.vram_gb} GB 예약${L.vram_used_gb != null ? `, 지금 실제로 <b>${L.vram_used_gb.toFixed(1)} GB</b> 사용${L.vram_used_gb > m.vram_gb ? ' <span class="badge warn">예약보다 많음</span>' : ""}` : ""}` : "—"}</dd>
      <dt>올린 사람</dt><dd>${L.loaded_by ? `${userCell(L.loaded_by)} <span class="muted">${ago(L.loaded_at)}</span>` : "—"}</dd>
      <dt>마지막 사용</dt><dd>${L.last_used ? ago(L.last_used) : "—"}${left != null ? `<span class="muted">, 이대로면 ${dur(Math.max(0, left))} 뒤 자동으로 내려감</span>` : ""}</dd>`;

    // 연결 방법
    if (!$("#llm-connect").dataset.done) {
      const model = m ? m.name : (L.models[0] ? L.models[0].name : "모델이름");
      const py = `from openai import OpenAI\nclient = OpenAI()  # 주소와 키는 환경변수에 이미 들어 있음\nr = client.chat.completions.create(\n    model="${model}",\n    messages=[{"role": "user", "content": "안녕"}],\n)\nprint(r.choices[0].message.content)`;
      const sh = `export OPENAI_BASE_URL=${L.endpoints.external}\nexport OPENAI_API_KEY=<내 접근 토큰>`;
      $("#llm-connect").innerHTML = `
        <div class="field"><label>워크스페이스와 GPU 작업 안에서</label><span class="hint">키 없이 바로 됩니다. 요청은 내 이름으로 기록됩니다.</span>
          <div class="copy"><code style="white-space:pre">${esc(py)}</code><button class="btn sm" data-copy="${esc(py)}">복사</button></div></div>
        <div class="field"><label>노트북에서</label><span class="hint">Tailscale에 연결돼 있어야 합니다. 키에는 콘솔 접근 토큰을 넣으세요.</span>
          <div class="copy"><code style="white-space:pre">${esc(sh)}</code><button class="btn sm" data-copy="${esc(sh)}">복사</button></div></div>`;
      $("#llm-connect").dataset.done = "1";
    }
    $("#chat-sub").textContent = L.state === "ready" ? `${m.name}와 대화` : "모델을 올리면 바로 쓸 수 있습니다";
    $("#chat-send").disabled = L.state !== "ready" && !S.chatAbort;

    Llm.usage();
    Llm.models();
  },
  usage() {
    const rows = (S.llmRange === "week" ? S.llm.usage_week : S.llm.usage_today) || [];
    $("#llm-usage").innerHTML = rows.length ? `<thead><tr><th>사용자</th><th class="r">요청</th><th class="r">입력 토큰</th><th class="r">출력 토큰</th><th class="r">응답 시간 합계</th><th>마지막</th></tr></thead><tbody>
      ${rows.map((u) => `<tr><td>${userCell(u.user)}</td><td class="r">${u.requests}</td><td class="r">${(u.prompt_tokens || 0).toLocaleString()}</td>
        <td class="r">${(u.completion_tokens || 0).toLocaleString()}</td><td class="r">${dur((u.ms || 0) / 1000)}</td><td class="muted">${ago(u.last)}</td></tr>`).join("")}</tbody>`
      : `<tbody><tr><td><div class="empty">${S.llmRange === "week" ? "최근 7일" : "오늘"} 사용 기록이 없습니다</div></td></tr></tbody>`;
  },
  models() {
    const t = $("#llm-models");
    if (!t) return;
    const active = S.llm.model && S.llm.model.name;
    t.innerHTML = S.llm.models.length ? `<thead><tr><th>이름</th><th>파일</th><th class="r">컨텍스트</th><th class="r">동시 요청</th><th class="r">VRAM</th><th></th></tr></thead><tbody>
      ${S.llm.models.map((x) => `<tr><td><b style="font-weight:500">${esc(x.name)}</b>${x.name === active ? ' <span class="badge ok">사용 중</span>' : ""}</td>
        <td class="mono" style="font-size:12px">${esc(x.file)}${x.mmproj ? `<span class="sub">이미지 입력: ${esc(x.mmproj)}</span>` : ""}</td>
        <td class="r">${x.ctx.toLocaleString()}<span class="sub">요청당 ${Math.floor(x.ctx / x.parallel).toLocaleString()}</span></td><td class="r">${x.parallel}</td>
        <td class="r">${x.vram_gb} GB${x.measured_vram_gb ? `<span class="sub">실제 최대 ${x.measured_vram_gb.toFixed(1)} GB</span>` : ""}</td>
        <td class="r">${x.name === active ? '<span class="muted" title="올라가 있는 모델은 내린 뒤에 고치거나 지울 수 있습니다">내린 뒤 수정</span>'
          : `<button class="btn sm ghost" data-edit-model="${esc(x.name)}">수정</button><button class="btn sm ghost danger" data-del-model="${esc(x.name)}">삭제</button>`}</td></tr>`).join("")}</tbody>`
      : `<tbody><tr><td><div class="empty"><b>등록된 모델이 없습니다</b>GGUF 파일을 /srv/shared/models/llm 에 받은 뒤 등록하세요.</div></td></tr></tbody>`;
    t.querySelectorAll("[data-edit-model]").forEach((b) => (b.onclick = () => openRegisterModel(S.llm.models.find((x) => x.name === b.dataset.editModel))));
    t.querySelectorAll("[data-del-model]").forEach((b) => (b.onclick = async () => {
      if (!(await confirmDialog(`모델 '${b.dataset.delModel}' 등록을 지울까요? 파일은 지우지 않습니다.`, { ok: "등록 지우기", danger: true }))) return;
      try { await api(`/api/llm/models/${encodeURIComponent(b.dataset.delModel)}`, { method: "DELETE" }); toast("모델 등록을 지웠습니다"); refresh(); } catch (e) { fail(e); }
    }));
  },
  async load() {
    const name = $("#llm-pick").value;
    $("#llm-load").disabled = true;
    try { await api("/api/llm/load", { method: "POST", json: { name } }); toast(`${name}을 올리는 중입니다`); } catch (e) { fail(e); }
    refresh();
  },
  async unload() {
    if (!(await confirmDialog("모델을 내릴까요? 지금 답을 받고 있는 사람의 요청이 끊깁니다.", { ok: "내리기", danger: true }))) return;
    try { await api("/api/llm/unload", { method: "POST" }); toast("모델을 내렸습니다. GPU가 3D 작업에 돌아갑니다"); } catch (e) { fail(e); }
    refresh();
  },
  renderChat() {
    const log = $("#chat-log");
    if (!log) return;
    if (!S.chat.length) {
      log.innerHTML = `<div class="chat-empty">${icon("llm")}<b>무엇이든 물어보세요</b><span>대화는 이 브라우저에만 있고 서버에 저장되지 않습니다. 사용량(토큰 수)만 기록됩니다.</span></div>`;
      return;
    }
    log.innerHTML = S.chat.map((m, i) => m.role === "user"
      ? `<div class="msg user"><div class="bubble">${fmtMsg(m.content)}</div></div>`
      : `<div class="msg bot">${S.showThinking && m.reasoning ? `<details class="thinking" ${m.content ? "" : "open"}><summary>생각 과정</summary><div>${fmtMsg(m.reasoning)}</div></details>` : ""}
          <div class="bubble">${m.content ? fmtMsg(m.content) : m.error ? "" : '<span class="typing"><i></i><i></i><i></i></span>'}${m.error ? `<span class="chat-err">${esc(m.error)}</span>` : ""}</div>
          ${m.stats ? `<div class="msg-stats">${m.stats}</div>` : ""}</div>`).join("");
    log.scrollTop = log.scrollHeight;
  },
  async send() {
    const input = $("#chat-input");
    const text = input.value.trim();
    if (!text || S.llm?.state !== "ready") return;
    input.value = "";
    S.chat.push({ role: "user", content: text });
    const bot = { role: "assistant", content: "", reasoning: "" };
    S.chat.push(bot);
    Llm.renderChat();
    const ctrl = new AbortController();
    S.chatAbort = ctrl;
    $("#chat-send").innerHTML = `${icon("stop")}멈추기`;
    const t0 = performance.now();
    let usage = null, last = 0;
    try {
      const headers = { "Content-Type": "application/json", "x-ironlab-source": "console" };
      const tok = store.get("ironlab_token", "");
      if (tok) headers.Authorization = "Bearer " + tok;
      const messages = S.chat.filter((m) => m !== bot && !m.error).map((m) => ({ role: m.role, content: m.content }));
      const res = await fetch("/v1/chat/completions", { method: "POST", headers, signal: ctrl.signal,
        body: JSON.stringify({ messages, stream: true }) });
      if (!res.ok) {
        let msg = `${res.status}`;
        try { const j = await res.json(); msg = j.error?.message || j.detail || msg; } catch {}
        throw new Error(msg);
      }
      const reader = res.body.getReader(), dec = new TextDecoder();
      let buf = "";
      for (;;) {
        const { value, done } = await reader.read();
        if (done) break;
        buf += dec.decode(value, { stream: true });
        let i;
        while ((i = buf.indexOf("\n")) >= 0) {
          const line = buf.slice(0, i).trim(); buf = buf.slice(i + 1);
          if (!line.startsWith("data: ") || line === "data: [DONE]") continue;
          let d; try { d = JSON.parse(line.slice(6)); } catch { continue; }
          if (d.usage) usage = d.usage;
          const delta = d.choices && d.choices[0] && d.choices[0].delta;
          if (delta) { bot.content += delta.content || ""; bot.reasoning += delta.reasoning_content || ""; }
        }
        if (performance.now() - last > 60) { Llm.renderChat(); last = performance.now(); }
      }
    } catch (e) {
      if (e.name !== "AbortError") bot.error = e.message;
      else bot.error = bot.content ? "" : "멈췄습니다";
    } finally {
      const sec = (performance.now() - t0) / 1000;
      if (usage) bot.stats = `출력 ${usage.completion_tokens} 토큰, ${sec.toFixed(1)}초, 초당 ${(usage.completion_tokens / Math.max(sec, 0.1)).toFixed(1)} 토큰`;
      S.chatAbort = null;
      if ($("#chat-send")) $("#chat-send").innerHTML = `${icon("send")}보내기`;
      Llm.renderChat();
    }
  },
};

// 모델 등록과 수정이 같은 서랍을 쓴다. edit 를 주면 그 모델의 값을 채워 열고, 저장하면 바뀐 설정으로 고친다.
async function openRegisterModel(edit) {
  let files = [];
  try { files = await api("/api/llm/files"); } catch (e) { fail(e); }
  const main = files.filter((f) => !f.mmproj), proj = files.filter((f) => f.mmproj);
  // 고치는 모델의 파일이 목록에 없어도(옮겼거나 지운 경우) 지금 값이 보이게 한다
  if (edit && !main.some((f) => f.path === edit.file)) main.unshift({ path: edit.file, size: null });
  if (edit && edit.mmproj && !proj.some((f) => f.path === edit.mmproj)) proj.unshift({ path: edit.mmproj });
  const v = (k, d) => (edit ? edit[k] : d);
  const sel = (a, b) => (a === b ? " selected" : "");
  const gpuGb = S.overview?.gpu?.total_mb ? S.overview.gpu.total_mb / 1024 : null;
  const dl = `sudo HF_HUB_CACHE=/tmp/hf /opt/ironlab/venv/bin/hf download unsloth/Qwen3.8-27B-GGUF \\\n  --include "*UD-Q4_K_XL*" --local-dir /srv/shared/models/llm/qwen3.8-27b`;
  openDrawer({
    title: edit ? "모델 수정" : "모델 등록",
    sub: edit ? "바꾼 설정은 다음에 모델을 올릴 때부터 쓰입니다." : "/srv/shared/models/llm 에 받아 둔 GGUF 파일을 콘솔에서 올릴 수 있게 등록합니다.", width: 620,
    body: `${!main.length ? `<div class="callout warn">${icon("info")}<div>아직 GGUF 파일이 없습니다. 서버에서 먼저 받아 주세요.</div></div>
        <div class="copy" style="margin-bottom:16px"><code style="white-space:pre-wrap;word-break:break-all">${esc(dl)}</code><button class="btn sm" data-copy="${esc(dl)}">복사</button></div>` : ""}
      <form id="model-form" class="form-grid">
        <div class="field span2"><label>이름</label><input class="input" name="name" value="${esc(v("name", "qwen3.8-27b"))}" required ${edit ? "disabled" : ""}><span class="hint">${edit ? "이름은 바꿀 수 없습니다. 다른 이름이 필요하면 새로 등록하세요" : "API에서 model 값으로 보이는 이름"}</span></div>
        <div class="field span2"><label>GGUF 파일</label>${main.length ? `<select class="input" name="file">${main.map((f) => `<option value="${esc(f.path)}"${sel(f.path, v("file", ""))}>${esc(f.path)}${f.size != null ? ` (${fmtBytes(f.size)})` : " (파일을 찾지 못함)"}</option>`).join("")}</select>`
          : `<input class="input" name="file" placeholder="qwen3.8-27b/Qwen3.8-27B-UD-Q4_K_XL.gguf">`}</div>
        <div class="field span2"><label>이미지 입력용 mmproj (선택)</label><select class="input" name="mmproj"><option value="">쓰지 않음</option>${proj.map((f) => `<option value="${esc(f.path)}"${sel(f.path, v("mmproj", ""))}>${esc(f.path)}</option>`).join("")}</select></div>
        <div class="field"><label>VRAM 예약 (GB)</label><input class="input" name="vram_gb" type="number" step="0.5" min="1" value="${v("vram_gb", 20)}"><span class="hint">${edit && edit.measured_vram_gb
          ? `지난번에 올렸을 때 실제로 최대 ${edit.measured_vram_gb.toFixed(1)} GB를 썼습니다${gpuGb ? ` (GPU 전체 ${gpuGb.toFixed(0)} GB)` : ""}`
          : "모델 크기 + 컨텍스트 몫. 4bit 27B는 20 정도"}</span></div>
        <div class="field"><label>동시 요청 수</label><input class="input" name="parallel" type="number" min="1" max="32" value="${v("parallel", 2)}"><span class="hint">1이면 컨텍스트 전체를 요청 하나가 씁니다. 대신 둘이 같이 쓰면 한 명은 기다립니다</span></div>
        <div class="field"><label>컨텍스트 (토큰)</label><input class="input" name="ctx" type="number" min="512" step="1024" value="${v("ctx", 32768)}"><span class="hint" id="ctx-hint"></span></div>
        <div class="field"><label>추가 llama-server 인자</label><input class="input" name="extra_args" placeholder="-ctk q8_0 -ctv q8_0" value="${esc(v("extra_args", ""))}"><span class="hint">-ctk q8_0 -ctv q8_0 을 넣으면 대화 기억용 메모리가 절반쯤으로 줄어 더 긴 컨텍스트가 들어갑니다</span></div>
      </form>`,
    foot: `<button class="btn" data-drawer-close>취소</button><button class="btn primary" id="model-save">${edit ? "저장" : "등록"}</button>`,
  });
  const f = $("#model-form").elements;
  // 요청 하나가 실제로 쓸 수 있는 길이를 바로 보여 준다 (전체 컨텍스트 ÷ 동시 요청 수)
  const showPer = () => {
    const ctx = Number(f.ctx.value), par = Math.max(1, Number(f.parallel.value) || 1);
    $("#ctx-hint").textContent = ctx > 0 ? `동시 요청 수로 나눠 씁니다. 요청 하나는 ${Math.floor(ctx / par).toLocaleString()} 토큰까지. 늘릴수록 GPU 메모리를 더 씁니다` : "동시 요청 수로 나눠 씁니다";
  };
  f.ctx.oninput = f.parallel.oninput = showPer;
  showPer();
  $("#model-save").onclick = async () => {
    const body = { file: f.file.value.trim(), mmproj: f.mmproj.value, vram_gb: Number(f.vram_gb.value),
      parallel: Number(f.parallel.value), ctx: Number(f.ctx.value), extra_args: f.extra_args.value };
    try {
      if (edit) {
        await api(`/api/llm/models/${encodeURIComponent(edit.name)}`, { method: "PATCH", json: body });
        toast(`모델 ${edit.name}의 설정을 고쳤습니다. 다음에 올릴 때부터 쓰입니다`);
      } else {
        body.name = f.name.value.trim();
        await api("/api/llm/models", { method: "POST", json: body });
        toast(`모델 ${body.name}을 등록했습니다`);
      }
      closeDrawer(); refresh();
    } catch (e) { fail(e); }
  };
}

const VIEWS = { dashboard: Dashboard, workspace: Workspace, jobs: Jobs, gen3d: Gen3d, genmv: GenMv, genimage: GenImage, genmusic: GenMusic, genvideo: GenVideo, genvoice: GenVoice, gensfx: GenSfx, genpolish: GenPolish, flow: Flow, llm: Llm, files: Files, users: Users, audit: Audit, account: Account };
window.openSubmit = openSubmit;

boot();
