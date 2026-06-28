from __future__ import annotations

from html import escape


def render_web_ui(active_page: str) -> str:
    page = active_page if active_page in {"overview", "projects", "kanban", "tasks"} else "overview"
    return f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>DevWerk</title>
  <style>{_CSS}</style>
</head>
<body data-page="{escape(page)}">
  <div class="app-shell">
    <aside class="global-nav">
      <div class="brand">
        <span class="brand-mark"></span>
        <span class="brand-name">DevWerk</span>
        <button class="nav-collapse" title="Collapse navigation">&laquo;</button>
      </div>
      <nav class="nav-list">
        <a class="nav-link" data-nav="overview" href="/workbench"><span data-icon="O"></span>Overview</a>
        <a class="nav-link" data-nav="projects" href="/dashboard"><span data-icon="P"></span>Projects</a>
        <a class="nav-link" data-nav="kanban" href="/kanban"><span data-icon="K"></span>Kanban</a>
        <a class="nav-link" data-nav="tasks" href="/tasks"><span data-icon="T"></span>Tasks</a>
        <a class="nav-link" data-nav="events" href="/dashboard#events"><span data-icon="E"></span>Events</a>
        <a class="nav-link" data-nav="memory" href="/dashboard#memory"><span data-icon="M"></span>Memory</a>
        <a class="nav-link" data-nav="analytics" href="/dashboard#analytics"><span data-icon="A"></span>Analytics</a>
        <a class="nav-link" data-nav="settings" href="/dashboard#settings"><span data-icon="S"></span>Settings</a>
      </nav>
      <div class="nav-footer">
        <div class="identity"><span class="avatar">EE</span><span><b>Evan Engineer</b><small>evan@devwerk.dev</small></span></div>
        <div class="footer-links"><span>Docs</span><span>Help</span></div>
      </div>
    </aside>
    <aside class="project-rail">
      <div class="rail-head">
        <h2>Projects</h2>
        <button id="newProjectRail" class="icon-button" title="New project">+</button>
      </div>
      <label class="search-box"><span></span><input id="projectSearch" placeholder="Search projects..." /></label>
      <div id="projectList" class="project-list"></div>
      <a class="rail-link" href="/dashboard">View all projects &rarr;</a>
    </aside>
    <main class="workspace">
      <header class="topbar">
        <div class="context-pill"><span class="hex"></span><b id="ctxProject">default</b></div>
        <span id="ctxStatus" class="badge green">Active</span>
        <label class="top-select"><span>Environment</span><select id="ctxEnv"><option>default</option></select></label>
        <label class="top-select wide"><span>Model Route</span><select id="ctxModelRoute"><option>default</option></select></label>
        <span class="top-spacer"></span>
        <button id="refresh" class="button ghost">Refresh</button>
        <button class="icon-button" title="Notifications">N</button>
        <button class="icon-button" title="Settings">S</button>
      </header>
      <section id="page" class="page"></section>
    </main>
  </div>
  <script>{_JS}</script>
</body>
</html>"""


_CSS = r"""
:root {
  --bg: #f7f9fc;
  --surface: #ffffff;
  --surface-soft: #f9fbff;
  --border: #dce4f0;
  --border-strong: #b9cdf8;
  --text: #111827;
  --muted: #64748b;
  --soft: #94a3b8;
  --blue: #2563eb;
  --blue-soft: #eaf1ff;
  --green: #16a34a;
  --green-soft: #e9f8ef;
  --orange: #f59e0b;
  --orange-soft: #fff5e5;
  --red: #ef4444;
  --red-soft: #fff1f2;
  --shadow: 0 1px 2px rgba(15, 23, 42, .05), 0 10px 28px rgba(15, 23, 42, .04);
  font-family: Inter, ui-sans-serif, system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
}
* { box-sizing: border-box; }
html, body { height: 100%; margin: 0; color: var(--text); background: var(--bg); }
body { overflow: hidden; }
button, input, textarea, select { font: inherit; }
.app-shell { height: 100vh; display: grid; grid-template-columns: 192px minmax(240px, 320px) minmax(0, 1fr); background: var(--bg); }
.global-nav, .project-rail { background: rgba(255,255,255,.94); border-right: 1px solid var(--border); height: 100vh; overflow-y: auto; overflow-x: hidden; }
.global-nav { display: flex; flex-direction: column; padding: 24px 14px; }
.brand { display: flex; align-items: center; gap: 12px; height: 34px; margin-bottom: 28px; }
.brand-mark { width: 25px; height: 25px; border-radius: 8px; display: inline-block; background: conic-gradient(from 30deg, #1d4ed8, #60a5fa, #2563eb, #1d4ed8); box-shadow: inset 0 0 0 5px #fff; }
.brand-name { font-size: 18px; font-weight: 850; letter-spacing: 0; }
.nav-collapse { margin-left: auto; border: 0; background: transparent; color: var(--muted); font-weight: 800; cursor: pointer; }
.nav-list { display: grid; gap: 8px; }
.nav-link { color: #1f2937; text-decoration: none; display: flex; align-items: center; gap: 12px; padding: 11px 10px; border-radius: 8px; font-weight: 650; font-size: 14px; }
.nav-link[data-active="true"], .nav-link:hover { background: var(--blue-soft); color: var(--blue); }
.nav-link span[data-icon] { width: 18px; height: 18px; border: 1.5px solid currentColor; border-radius: 5px; display: grid; place-items: center; font-size: 9px; font-weight: 850; }
.nav-footer { margin-top: auto; display: grid; gap: 22px; }
.identity { display: flex; gap: 10px; align-items: center; }
.identity small { display: block; color: var(--muted); font-size: 12px; margin-top: 2px; }
.avatar { width: 32px; height: 32px; border-radius: 50%; background: #e9d5ff; color: #6d28d9; display: inline-grid; place-items: center; font-size: 12px; font-weight: 750; flex: 0 0 auto; }
.avatar.green { background: #bbf7d0; color: #15803d; }
.avatar.yellow { background: #fef08a; color: #854d0e; }
.avatar.blue { background: var(--blue-soft); color: var(--blue); }
.footer-links { display: flex; gap: 18px; color: #334155; font-weight: 650; font-size: 13px; }
.project-rail { resize: horizontal; min-width: 240px; max-width: 420px; padding: 26px 16px; display: flex; flex-direction: column; }
.rail-head { display: flex; align-items: center; justify-content: space-between; margin-bottom: 20px; }
.rail-head h2 { margin: 0; font-size: 17px; }
.icon-button { width: 34px; height: 34px; border-radius: 8px; border: 1px solid var(--border); background: #fff; color: #0f172a; display: inline-grid; place-items: center; cursor: pointer; font-weight: 750; }
.icon-button:hover, .button:hover { border-color: var(--border-strong); box-shadow: var(--shadow); }
.search-box { height: 34px; border: 1px solid var(--border); border-radius: 8px; background: #fff; display: flex; align-items: center; gap: 8px; padding: 0 12px; margin-bottom: 18px; }
.search-box span { width: 13px; height: 13px; border: 1.5px solid var(--soft); border-radius: 50%; position: relative; }
.search-box span:after { content: ""; position: absolute; width: 6px; height: 1.5px; background: var(--soft); transform: rotate(45deg); right: -5px; bottom: -3px; }
.search-box input { border: 0; outline: 0; min-width: 0; flex: 1; color: var(--text); }
.project-list { display: grid; gap: 8px; padding-bottom: 24px; }
.project-row { width: 100%; text-align: left; border: 1px solid transparent; border-radius: 10px; padding: 13px 12px; background: transparent; cursor: pointer; }
.project-row:hover, .project-row.active { border-color: #7aa2ff; background: linear-gradient(180deg, #f8fbff, #eef5ff); }
.project-row-title { display: flex; justify-content: space-between; gap: 8px; font-weight: 850; }
.project-row small { color: var(--muted); display: block; margin-top: 7px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.rail-link { color: #334155; text-decoration: none; margin: auto auto 0; font-size: 13px; }
.workspace { min-width: 0; height: 100vh; display: grid; grid-template-rows: 72px minmax(0, 1fr); }
.topbar { border-bottom: 1px solid var(--border); background: rgba(255,255,255,.86); display: flex; align-items: center; gap: 16px; padding: 0 24px; }
.context-pill { height: 42px; min-width: 220px; display: flex; align-items: center; gap: 12px; border: 1px solid var(--border); background: #fff; border-radius: 10px; padding: 0 16px; font-size: 16px; }
.hex { width: 16px; height: 16px; border: 2px solid var(--muted); border-radius: 5px; transform: rotate(45deg); display: inline-block; }
.badge { display: inline-flex; align-items: center; gap: 6px; border-radius: 999px; padding: 5px 10px; font-size: 12px; font-weight: 750; }
.badge:before { content: ""; width: 6px; height: 6px; border-radius: 50%; background: currentColor; }
.badge.green { color: var(--green); background: var(--green-soft); }
.badge.blue { color: var(--blue); background: var(--blue-soft); }
.badge.orange { color: var(--orange); background: var(--orange-soft); }
.badge.red { color: var(--red); background: var(--red-soft); }
.top-select { border-left: 1px solid var(--border); padding-left: 20px; display: flex; align-items: center; gap: 10px; color: var(--muted); font-size: 14px; }
.top-select select, .select-pill { height: 34px; border: 1px solid var(--border); border-radius: 8px; background: #fff; color: var(--text); padding: 0 12px; min-width: 92px; }
.top-select.wide select { min-width: 220px; }
.top-spacer { flex: 1; }
.button { min-height: 34px; border-radius: 8px; border: 1px solid var(--border); background: #fff; padding: 0 14px; cursor: pointer; font-weight: 700; color: #0f172a; }
.button.primary, .send-button { background: var(--blue); border-color: var(--blue); color: #fff; }
.button.ghost { background: #fff; }
.page { min-height: 0; overflow: auto; padding: 16px 24px 28px; }
.card { background: var(--surface); border: 1px solid var(--border); border-radius: 10px; box-shadow: var(--shadow); }
.card-pad { padding: 18px; }
.h1 { margin: 0; font-size: 25px; line-height: 1.2; }
.h2 { margin: 0; font-size: 22px; line-height: 1.2; }
.h3 { margin: 0; font-size: 14px; font-weight: 850; }
.muted { color: var(--muted); }
.soft { color: var(--soft); }
.link { color: var(--blue); text-decoration: none; cursor: pointer; font-weight: 700; }
.overview-grid { display: grid; grid-template-columns: 190px minmax(520px, 1fr) 280px; gap: 14px; align-items: start; }
.hero { grid-column: 1 / -1; padding: 22px; display: flex; align-items: center; justify-content: space-between; gap: 18px; }
.hero-title { display: flex; align-items: center; gap: 14px; }
.folder-icon { width: 40px; height: 40px; border-radius: 10px; background: var(--blue-soft); border: 1px solid #8eb1ff; position: relative; }
.folder-icon:before { content: ""; position: absolute; width: 18px; height: 5px; background: var(--blue); top: 10px; left: 10px; border-radius: 3px 3px 0 0; }
.folder-icon:after { content: ""; position: absolute; inset: 15px 8px 9px; background: #60a5fa; border-radius: 3px; }
.hero-actions, .toolbar { display: flex; gap: 10px; align-items: center; flex-wrap: wrap; }
.pipeline { grid-column: 1 / -1; padding: 18px 16px; }
.pipeline-title { font-weight: 850; margin-bottom: 14px; }
.pipeline-row { display: grid; grid-template-columns: repeat(9, minmax(80px, 1fr)); gap: 10px; align-items: center; }
.stage { border: 1px solid transparent; border-radius: 10px; min-height: 58px; padding: 10px; display: grid; grid-template-columns: 24px minmax(0,1fr); gap: 8px; align-items: center; position: relative; }
.stage:not(:last-child):after { content: ""; position: absolute; height: 1px; background: var(--border); right: -10px; width: 10px; top: 50%; }
.stage.active { border-color: var(--blue); background: var(--blue-soft); color: var(--blue); }
.stage.done .stage-icon { background: var(--green-soft); color: var(--green); }
.stage-icon { width: 24px; height: 24px; border-radius: 50%; border: 1px solid var(--border); display: grid; place-items: center; font-weight: 850; background: #fff; }
.stage-label { font-size: 12px; font-weight: 750; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
.chat-card { padding: 0; overflow: hidden; min-height: 520px; display: grid; grid-template-rows: auto minmax(260px, 1fr) auto; }
.tabs { display: flex; gap: 24px; border-bottom: 1px solid var(--border); padding: 0 16px; min-height: 48px; align-items: end; }
.tab { padding: 14px 0 11px; color: #334155; font-weight: 750; font-size: 13px; border-bottom: 2px solid transparent; }
.tab.active { color: var(--blue); border-color: var(--blue); }
.chat-body { padding: 22px 24px; overflow-y: auto; display: flex; flex-direction: column; gap: 20px; }
.message { display: grid; grid-template-columns: 28px minmax(0, 1fr); gap: 12px; max-width: 760px; }
.bot-badge { width: 26px; height: 26px; border-radius: 8px; background: var(--blue-soft); border: 1px solid #bed3ff; display: grid; place-items: center; color: var(--blue); font-weight: 850; }
.message-content { background: #fff; border: 1px solid var(--border); border-radius: 12px; padding: 14px 16px; white-space: pre-wrap; line-height: 1.6; }
.message-meta { font-size: 11px; color: var(--muted); margin-bottom: 6px; }
.user-bubble { align-self: flex-end; max-width: 720px; background: #eaf2ff; border: 1px solid #b8d0ff; border-radius: 14px; padding: 12px 16px; white-space: pre-wrap; line-height: 1.55; }
.composer { border-top: 1px solid var(--border); padding: 14px 18px; background: #fff; }
.composer-box { border: 1px solid var(--border); border-radius: 10px; background: #fff; padding: 10px; }
.composer-input { width: 100%; min-height: 58px; resize: vertical; border: 0; outline: 0; color: var(--text); }
.composer-actions { display: flex; justify-content: space-between; gap: 12px; align-items: center; }
.tool-row { display: flex; gap: 8px; }
.tool { width: 30px; height: 30px; border-radius: 8px; border: 1px solid var(--border); display: grid; place-items: center; color: var(--blue); font-weight: 850; }
.send-button { width: 38px; height: 34px; border-radius: 8px; border: 0; cursor: pointer; font-weight: 900; }
.send-button:disabled { opacity: .55; cursor: wait; }
.side-stack { display: grid; gap: 14px; }
.metric-lines { display: grid; gap: 10px; }
.metric-line { display: flex; justify-content: space-between; gap: 12px; color: #334155; font-size: 13px; }
.progress { height: 5px; border-radius: 999px; background: #e2e8f0; overflow: hidden; margin-top: 12px; }
.progress span { height: 100%; display: block; background: var(--blue); }
.sparkbars { display: flex; align-items: end; height: 58px; gap: 2px; margin-top: 12px; }
.sparkbars i { flex: 1; border-radius: 2px 2px 0 0; background: linear-gradient(#9dbaff, #dbe7ff); }
.quick-grid { grid-column: 1 / -1; display: grid; grid-template-columns: repeat(6, 1fr); gap: 12px; }
.kpi { padding: 16px; min-height: 92px; }
.kpi-label { color: #334155; font-weight: 750; display: flex; justify-content: space-between; gap: 8px; }
.kpi-value { font-size: 24px; font-weight: 900; margin-top: 10px; }
.kpi-trend { color: var(--green); font-size: 12px; font-weight: 800; }
.projects-page { display: grid; gap: 16px; }
.page-head { display: flex; justify-content: space-between; align-items: center; gap: 16px; }
.title-block { display: flex; align-items: center; gap: 14px; }
.project-cards { display: grid; grid-template-columns: repeat(5, minmax(180px, 1fr)); gap: 14px; }
.project-card { border: 1px solid var(--border); border-radius: 10px; background: #fff; padding: 16px; text-align: left; cursor: pointer; min-height: 170px; position: relative; }
.project-card.selected { border-color: var(--blue); box-shadow: inset 0 0 0 1px #7aa2ff, var(--shadow); }
.card-top { display: flex; justify-content: space-between; gap: 10px; }
.project-title { font-weight: 900; }
.card-meta { margin-top: 18px; display: grid; grid-template-columns: repeat(2, 1fr); gap: 12px; font-size: 12px; }
.meta-label { color: var(--muted); }
.meta-value { font-weight: 850; color: #0f172a; margin-top: 4px; }
.mini-spark { display: flex; gap: 3px; align-items: end; position: absolute; right: 16px; bottom: 14px; height: 28px; }
.mini-spark i { width: 14px; background: linear-gradient(#8fb0ff, #eaf1ff); border-radius: 3px 3px 0 0; }
.config-panel { padding: 16px; display: grid; gap: 14px; }
.panel-head { display: flex; align-items: center; justify-content: space-between; gap: 16px; border-bottom: 1px solid var(--border); padding-bottom: 12px; }
.config-grid { display: grid; grid-template-columns: minmax(320px,1fr) minmax(320px,1fr) 300px; gap: 14px; }
.editor-card { padding: 12px; }
.editor-head, .editor-foot { display: flex; justify-content: space-between; align-items: center; gap: 12px; }
.editor { margin-top: 12px; border: 1px solid var(--border); border-radius: 8px; background: #fbfdff; min-height: 250px; display: grid; grid-template-columns: 42px minmax(0,1fr); overflow: auto; }
.line-nos { background: #f4f7fb; color: #94a3b8; text-align: right; padding: 12px 10px; line-height: 1.65; font: 12px Consolas, monospace; user-select: none; }
.code { margin: 0; padding: 12px; line-height: 1.65; font: 12px Consolas, monospace; color: #7f1d1d; white-space: pre; }
.editor-foot { margin-top: 10px; font-size: 12px; }
.side-card { padding: 16px; }
.preset-row { display: flex; justify-content: space-between; gap: 10px; padding: 8px 0; border-bottom: 1px solid #eef2f7; font-size: 13px; }
.project-overview { padding: 16px; display: grid; grid-template-columns: repeat(5, 1fr); gap: 14px; }
.info-item { display: flex; gap: 10px; align-items: center; font-size: 12px; }
.info-icon { width: 28px; height: 28px; border-radius: 8px; border: 1px solid var(--border); display: grid; place-items: center; color: var(--muted); }
.section-grid { display: grid; grid-template-columns: minmax(0, 1fr) 320px; gap: 14px; align-items: start; }
.section-stack { display: grid; gap: 14px; }
.dense-grid { display: grid; grid-template-columns: repeat(3, minmax(0, 1fr)); gap: 14px; }
.data-table { width: 100%; border-collapse: collapse; font-size: 13px; }
.data-table th, .data-table td { text-align: left; padding: 11px 10px; border-bottom: 1px solid #eef2f7; vertical-align: top; }
.data-table th { color: var(--muted); font-size: 12px; font-weight: 800; background: var(--surface-soft); }
.pill-list { display: flex; flex-wrap: wrap; gap: 8px; margin-top: 10px; }
.pill { border: 1px solid var(--border); border-radius: 999px; padding: 6px 10px; background: #fff; color: #334155; font-size: 12px; font-weight: 700; }
.tab-button { border: 0; background: transparent; cursor: pointer; }
.tab-button.active { color: var(--blue); border-color: var(--blue); }
.timeline-list { display: grid; gap: 10px; }
.timeline-item { display: grid; grid-template-columns: 18px minmax(0,1fr) auto; gap: 10px; align-items: start; border-bottom: 1px solid #eef2f7; padding-bottom: 10px; }
.json-panel { max-height: 360px; overflow: auto; border: 1px solid var(--border); border-radius: 8px; background: #fbfdff; padding: 12px; font: 12px Consolas, monospace; white-space: pre; color: #334155; }
.kanban-page { display: grid; grid-template-columns: minmax(0,1fr) 260px; gap: 14px; min-height: calc(100vh - 104px); }
.kanban-main { min-width: 0; display: grid; grid-template-rows: auto minmax(0,1fr); }
.kanban-header { padding: 18px; display: flex; justify-content: space-between; align-items: center; gap: 14px; }
.filter-row { display: flex; align-items: center; gap: 8px; flex-wrap: wrap; }
.chip { border: 1px solid var(--border); background: #fff; border-radius: 8px; padding: 8px 12px; font-size: 12px; font-weight: 750; }
.chip.active { color: var(--blue); background: var(--blue-soft); border-color: #c6d7ff; }
.board { display: grid; grid-auto-flow: column; grid-auto-columns: minmax(150px, 1fr); gap: 8px; overflow-x: auto; padding-top: 14px; min-height: 0; }
.column { background: rgba(255,255,255,.58); border-left: 1px solid var(--border); border-right: 1px solid var(--border); padding: 10px 8px; min-width: 150px; }
.column.active { border-color: var(--blue); background: #f8fbff; }
.col-head { display: flex; justify-content: space-between; align-items: center; font-weight: 850; font-size: 13px; margin-bottom: 10px; }
.count { display: inline-grid; place-items: center; min-width: 20px; height: 20px; border-radius: 999px; background: #eef2f7; margin-left: 4px; color: #334155; font-size: 11px; }
.cards { display: grid; gap: 8px; }
.task-card { border: 1px solid var(--border); background: #fff; border-radius: 8px; padding: 12px; text-align: left; cursor: pointer; box-shadow: var(--shadow); }
.task-card:hover { border-color: var(--border-strong); }
.task-id { color: var(--muted); font-size: 11px; font-weight: 800; margin-bottom: 8px; }
.task-title { font-weight: 850; font-size: 12px; line-height: 1.35; }
.task-desc { color: #475569; font-size: 12px; line-height: 1.45; margin-top: 8px; }
.tags { display: flex; gap: 6px; flex-wrap: wrap; margin-top: 10px; }
.tag, .command { display: inline-flex; border-radius: 6px; background: #eef2f7; color: #475569; padding: 4px 7px; font-size: 11px; margin: 0 6px 6px 0; }
.task-foot { margin-top: 10px; display: flex; align-items: center; justify-content: space-between; color: var(--muted); font-size: 11px; }
.priority { font-weight: 850; color: #0f172a; }
.dot { display: inline-block; width: 6px; height: 6px; border-radius: 50%; margin-right: 6px; background: var(--soft); }
.dot.green { background: var(--green); }
.dot.blue { background: var(--blue); }
.dot.orange { background: var(--orange); }
.dot.red { background: var(--red); }
.add-task { color: var(--blue); text-align: center; font-size: 12px; padding: 10px; font-weight: 750; }
.inspector { padding: 18px; display: flex; flex-direction: column; gap: 20px; overflow-y: auto; }
.ring { width: 116px; height: 116px; margin: 10px auto; border-radius: 50%; background: conic-gradient(var(--green) 0 70%, #facc15 70% 84%, var(--red) 84% 100%); display: grid; place-items: center; }
.ring-inner { width: 86px; height: 86px; background: #fff; border-radius: 50%; display: grid; place-items: center; align-content: center; }
.ring-number { font-size: 28px; font-weight: 900; }
.blocked { padding: 12px; border: 1px solid var(--border); border-radius: 8px; margin-top: 10px; font-size: 12px; background: #fff; }
.task-page { display: grid; grid-template-columns: minmax(0, 1fr) 320px; gap: 14px; }
.task-main { display: grid; gap: 14px; }
.task-header { padding: 18px; display: grid; gap: 14px; }
.breadcrumb { color: var(--muted); font-size: 13px; }
.task-title-row { display: flex; align-items: center; justify-content: space-between; gap: 12px; }
.task-title-left, .task-actions, .task-meta-row { display: flex; align-items: center; gap: 10px; flex-wrap: wrap; }
.timeline-dot { width: 14px; height: 14px; border-radius: 50%; border: 2px solid var(--blue); display: inline-block; background: #fff; }
.meta-chip { border-right: 1px solid var(--border); padding-right: 12px; color: #334155; }
.detail-tabs { display: flex; gap: 26px; border-bottom: 1px solid var(--border); padding: 0 18px; }
.summary-grid { padding: 16px; display: grid; grid-template-columns: repeat(2, minmax(0,1fr)); gap: 14px; }
.summary-card { border: 1px solid var(--border); border-radius: 8px; padding: 16px; background: #fff; }
.summary-card.wide { grid-column: 1 / -1; }
.clean { margin: 10px 0 0; padding-left: 18px; line-height: 1.7; }
.check-list { display: grid; grid-template-columns: repeat(2, 1fr); gap: 9px 20px; margin-top: 12px; }
.check-item { display: flex; gap: 8px; align-items: center; }
.ok { width: 17px; height: 17px; border-radius: 50%; border: 1px solid var(--green); color: var(--green); display: inline-grid; place-items: center; font-size: 11px; }
.files-row { display: flex; justify-content: space-between; gap: 10px; padding: 6px 0; color: #334155; font-size: 12px; border-bottom: 1px solid #f1f5f9; }
.plus { color: var(--green); font-weight: 800; }
.minus { color: var(--red); font-weight: 800; }
.exec-log .log-head { display: flex; justify-content: space-between; }
.log-body { display: grid; grid-template-columns: 190px minmax(0,1fr); gap: 14px; margin-top: 12px; }
.steps { display: grid; gap: 4px; }
.step { display: grid; grid-template-columns: 20px minmax(0,1fr) auto; gap: 8px; padding: 8px; border-radius: 7px; font-size: 12px; }
.step.active { background: var(--blue-soft); color: var(--blue); }
.log-lines { font: 12px Consolas, monospace; line-height: 1.7; color: #334155; overflow-x: auto; }
.task-side { display: grid; gap: 14px; align-content: start; }
.side-title { display: flex; justify-content: space-between; gap: 12px; margin-bottom: 12px; }
.list { display: grid; gap: 10px; }
.list-row { display: flex; align-items: center; gap: 10px; font-size: 13px; }
.list-row-title { font-weight: 750; }
.list-row-sub { color: var(--muted); font-size: 12px; margin-top: 3px; }
.grow { flex: 1; min-width: 0; }
.snippet { border: 1px solid var(--border); background: var(--surface-soft); border-radius: 8px; padding: 12px; margin-top: 10px; font-size: 12px; color: #334155; line-height: 1.5; }
.small-button { min-height: 28px; border: 1px solid var(--border); border-radius: 7px; background: #fff; padding: 0 10px; cursor: pointer; font-size: 12px; font-weight: 750; }
@media (max-width: 1280px) {
  .app-shell { grid-template-columns: 78px minmax(220px, 300px) minmax(0,1fr); }
  .brand-name, .nav-link:not(span), .nav-link { font-size: 0; }
  .nav-link span[data-icon] { font-size: 9px; }
  .overview-grid { grid-template-columns: minmax(0,1fr); }
  .project-cards { grid-template-columns: repeat(2, minmax(0, 1fr)); }
  .config-grid, .kanban-page, .task-page { grid-template-columns: minmax(0,1fr); }
}
"""


_JS = r"""
const API = "/v1";
const STAGES = ["draft","context_indexed","planned","coding","reviewed","ready_to_apply","applied","verified","done"];
const STAGE_TITLES = {draft:"Draft", context_indexed:"Context Indexed", planned:"Planned", coding:"Coding", reviewed:"Reviewed", ready_to_apply:"Ready to Apply", applied:"Applied", verified:"Verified", done:"Done", failed:"Failed"};
const DEMO_TASKS = [
  {id:"T-1042", title:"Implement NextAuth setup", description:"Add credentials and session strategy.", status_key:"draft", priority:1},
  {id:"T-1038", title:"Add OAuth providers", description:"Configure Google and GitHub OAuth providers.", status_key:"context_indexed", priority:2},
  {id:"T-1036", title:"Build tenant management UI", description:"Page for creating, viewing, and managing tenants.", status_key:"planned", priority:2},
  {id:"T-1034", title:"Implement auth API routes", description:"Create API routes and handlers.", status_key:"coding", priority:3},
  {id:"T-1032", title:"Code review: auth API routes", description:"Review correctness, security, and edge cases.", status_key:"reviewed", priority:3},
  {id:"T-1030", title:"Migrate DB schema", description:"Apply database migration for auth tables.", status_key:"ready_to_apply", priority:2},
  {id:"T-1029", title:"Deploy to staging", description:"Deploy latest build and run smoke tests.", status_key:"applied", priority:1},
  {id:"T-1028", title:"QA: Auth flow E2E", description:"Verify sign in and protected routes.", status_key:"verified", priority:3}
];
const state = {
  page: document.body.dataset.page || "overview",
  projectId: new URLSearchParams(location.search).get("project_id") || "default",
  projects: [],
  board: null,
  events: [],
  conversation: [],
  settings: {},
  workflow: {},
  memory: {},
  usage: {},
  activeTask: null,
  projectTab: "configuration",
  busy: false
};
const $ = (id) => document.getElementById(id);
async function api(path, options = {}) {
  const headers = {"Content-Type": "application/json", ...(options.headers || {})};
  const response = await fetch(path, {...options, headers});
  if (!response.ok) throw new Error(`HTTP ${response.status}: ${await response.text()}`);
  const text = await response.text();
  return text ? JSON.parse(text) : null;
}
async function refreshAll() {
  await loadProjects();
  await Promise.allSettled([loadBoard(), loadEvents(), loadConversation(), loadSettings(), loadWorkflow(), loadMemory(), loadUsage()]);
  renderShell();
}
async function loadProjects() {
  const data = await api(`${API}/kanban/projects`);
  state.projects = Array.isArray(data.projects) ? data.projects : [];
  if (!state.projects.some(p => p.id === state.projectId) && state.projects[0]) state.projectId = state.projects[0].id;
}
async function loadBoard() { state.board = await api(`${API}/kanban/board?project_id=${encodeURIComponent(state.projectId)}`); }
async function loadEvents() { const data = await api(`${API}/kanban/events?project_id=${encodeURIComponent(state.projectId)}&limit=80`); state.events = data.events || []; }
async function loadConversation() {
  const data = await api(`${API}/kanban/projects/${encodeURIComponent(state.projectId)}/conversation`);
  state.conversation = normalizeMessages(data.messages || []);
  state.activeTask = data.active_task || state.activeTask;
}
async function loadSettings() { try { const data = await api(`${API}/kanban/projects/${encodeURIComponent(state.projectId)}/settings`); state.settings = data.settings || data || {}; } catch (_) { state.settings = {}; } }
async function loadWorkflow() { try { const data = await api(`${API}/kanban/projects/${encodeURIComponent(state.projectId)}/workflow`); state.workflow = data.workflow || data || {}; } catch (_) { state.workflow = {}; } }
async function loadMemory() { try { state.memory = await api(`${API}/kanban/projects/${encodeURIComponent(state.projectId)}/memory`); } catch (_) { state.memory = {}; } }
async function loadUsage() { try { state.usage = await api(`${API}/usage/summary`); } catch (_) { state.usage = {}; } }
function renderShell() {
  state.section = activeSection();
  document.querySelectorAll(".nav-link").forEach(link => link.dataset.active = String(link.dataset.nav === activeNav()));
  $("ctxProject").textContent = currentProject().name || state.projectId;
  $("ctxStatus").textContent = projectStatus(currentProject()).label;
  $("ctxStatus").className = `badge ${projectStatus(currentProject()).badge}`;
  $("ctxModelRoute").innerHTML = modelRoutes().map(route => `<option>${esc(route)}</option>`).join("") || "<option>default</option>";
  renderProjectRail();
  if (state.section) renderSectionPage(state.section);
  else if (state.page === "projects") renderProjectsPage();
  else if (state.page === "kanban") renderKanbanPage();
  else if (state.page === "tasks") renderTaskPage();
  else renderOverviewPage();
}
function renderProjectRail() {
  const q = ($("projectSearch").value || "").toLowerCase();
  const list = state.projects.filter(p => `${p.name || ""} ${p.id}`.toLowerCase().includes(q));
  $("projectList").innerHTML = list.map(project => `<button class="project-row ${project.id === state.projectId ? "active" : ""}" data-project="${escAttr(project.id)}"><span class="project-row-title"><span>${esc(project.name || project.id)}</span><span><i class="dot ${projectStatus(project).dot}"></i>${projectStatus(project).label}</span></span><small>${esc(project.description || project.id)}</small></button>`).join("");
}
function renderOverviewPage() {
  const usage = usageTotals();
  $("page").innerHTML = `
    <div class="overview-grid">
      <section class="card hero">
        <div class="hero-title"><span class="folder-icon"></span><div><h1 class="h1">${esc(currentProject().name || state.projectId)} <span class="soft">...</span></h1><p class="muted">${esc(currentProject().description || "General purpose development assistant project.")}<br/>DevWerk will plan, build, and ship based on your goals.</p></div></div>
        <div class="hero-actions"><button id="heroNewTask" class="button primary">New Task</button><button id="heroPlan" class="button">Plan</button><button class="button">Add Context</button><a class="button" href="/dashboard?project_id=${escAttr(state.projectId)}">Settings</a></div>
      </section>
      ${pipelineHtml()}
      <aside class="side-stack">${healthCard()}${usageCard(usage)}${routingCard()}</aside>
      ${conversationCard()}
      <aside class="side-stack">${recentTasksCard()}${memoryCard()}${recentEventsCard()}</aside>
      <div class="quick-grid">${kpi("Requests (24h)", usage.calls || 0, "12%")}${kpi("LLM Calls", usage.calls || 0, "8%")}${kpi("Input Tokens", compact(usage.input), "15%")}${kpi("Output Tokens", compact(usage.output), "9%")}${kpi("Total Tokens", compact(usage.total), "12%")}${kpi("Duration (24h)", duration(usage.duration), "6%")}</div>
    </div>`;
  wireConversation();
}
function renderProjectsPage() {
  $("page").innerHTML = `
    <div class="projects-page">
      <div class="page-head"><div class="title-block"><span class="folder-icon"></span><div><h1 class="h2">Projects</h1><div class="muted">Manage and configure your development assistant projects.</div></div></div><div class="toolbar"><label class="search-box" style="margin:0;width:230px"><span></span><input placeholder="Search projects..." /></label><select class="select-pill"><option>All Environments</option></select><select class="select-pill"><option>All Statuses</option></select><button id="newProjectMain" class="button primary">+ New Project</button></div></div>
      <div class="project-cards">${(state.projects.length ? state.projects : [{id:"default",name:"default",description:"General purpose development assistant"}]).map(projectCard).join("")}</div>
      <section class="card config-panel">
        <div class="panel-head"><div class="title-block"><span class="folder-icon" style="width:28px;height:28px"></span><div><div class="muted">Project Configuration</div><h2 class="h2">${esc(currentProject().name || state.projectId)} <span class="badge green">Active</span></h2></div></div><div class="toolbar"><button class="button">Preview</button><button class="button">Clone</button><button class="icon-button">...</button></div></div>
        <div class="tabs">${projectTabs().map(tab=>`<button class="tab tab-button ${state.projectTab===tab.key?"active":""}" data-project-tab="${tab.key}">${tab.label}</button>`).join("")}</div>
        ${projectTabContent()}
        <div class="card project-overview">${infoItem("Environment","default")}${infoItem("Model Route", modelRoutes()[0] || "default")}${infoItem("Created", dateShort(currentProject().created_at))}${infoItem("Last Updated", relative(currentProject().updated_at))}${infoItem("Project ID", state.projectId)}</div>
      </section>
    </div>`;
  $("newProjectMain").onclick = createProjectFromPrompt;
  document.querySelectorAll("[data-project-tab]").forEach(button => {
    button.onclick = () => {
      state.projectTab = button.dataset.projectTab || "configuration";
      renderProjectsPage();
    };
  });
}
function renderSectionPage(section) {
  const renderers = {
    events: renderEventsSection,
    memory: renderMemorySection,
    analytics: renderAnalyticsSection,
    settings: renderSettingsSection
  };
  (renderers[section] || renderProjectsPage)();
}
function renderEventsSection() {
  $("page").innerHTML = `<div class="section-grid">
    <section class="card card-pad"><div class="page-head"><div><h1 class="h2">Events</h1><div class="muted">Project and task event stream. Use this to audit workflow movement and agent decisions.</div></div><button class="button" onclick="refreshAll()">Refresh</button></div><table class="data-table" style="margin-top:16px"><thead><tr><th>Time</th><th>Event</th><th>Task</th><th>Transition</th><th>Detail</th></tr></thead><tbody>${state.events.length ? state.events.map(eventRow).join("") : `<tr><td colspan="5" class="muted">No events recorded for this project.</td></tr>`}</tbody></table></section>
    <aside class="side-stack">${recentEventsCard()}${workflowHealthSmallCard()}${routingCard()}</aside>
  </div>`;
}
function renderMemorySection() {
  const mem = state.memory || {};
  $("page").innerHTML = `<div class="section-grid">
    <section class="card card-pad"><div class="page-head"><div><h1 class="h2">Memory</h1><div class="muted">Project-level memory carried into future tasks. This is not a raw prompt log.</div></div><span class="badge green">Project Memory</span></div>
      <div class="dense-grid" style="margin-top:16px">${memoryBucket("Frameworks", mem.frameworks)}${memoryBucket("Codebase Paths", mem.paths)}${memoryBucket("Commands", mem.commands)}${memoryBucket("Rules", mem.rules)}${memorySummaries(mem)}<div class="card card-pad"><div class="h3">Raw Memory</div><pre class="json-panel">${esc(JSON.stringify(mem, null, 2))}</pre></div></div>
    </section>
    <aside class="side-stack">${memoryCard()}${recentTasksCard()}${routingCard()}</aside>
  </div>`;
}
function renderAnalyticsSection() {
  const usage = usageTotals();
  $("page").innerHTML = `<div class="section-stack">
    <div class="page-head"><div><h1 class="h2">Analytics</h1><div class="muted">Usage, throughput, and workflow distribution for the current project.</div></div><button class="button" onclick="refreshAll()">Refresh</button></div>
    <div class="quick-grid">${kpi("Requests", usage.calls || 0, "12%")}${kpi("Input Tokens", compact(usage.input), "15%")}${kpi("Output Tokens", compact(usage.output), "9%")}${kpi("Total Tokens", compact(usage.total), "12%")}${kpi("Duration", duration(usage.duration), "6%")}${kpi("Tasks", allTasks().length, "stable")}</div>
    <div class="section-grid"><section class="card card-pad"><div class="h3">Workflow Distribution</div><table class="data-table" style="margin-top:12px"><thead><tr><th>Stage</th><th>Tasks</th><th>Share</th></tr></thead><tbody>${columns().map(c=>`<tr><td>${esc(c.title || c.status_key)}</td><td>${(c.tasks || []).length}</td><td><div class="progress"><span style="width:${Math.min(100, Math.max(3, Math.round(((c.tasks||[]).length / Math.max(1, allTasks().length)) * 100)))}%"></span></div></td></tr>`).join("")}</tbody></table></section><aside class="side-stack">${healthCard()}${usageCard(usage)}</aside></div>
  </div>`;
}
function renderSettingsSection() {
  $("page").innerHTML = `<div class="section-grid">
    <section class="card card-pad"><div class="page-head"><div><h1 class="h2">Settings</h1><div class="muted">System-facing settings view. Project-specific workflow and agent settings remain in Projects.</div></div><span class="badge blue">Read Only</span></div>
      <div class="dense-grid" style="margin-top:16px">${settingsTile("Default Route", modelRoutes()[0] || "default", "The route used when a project or agent does not override model selection.")}${settingsTile("Fallback Route", modelRoutes()[1] || modelRoutes()[0] || "default", "Secondary model route exposed by current configuration.")}${settingsTile("Thinking Mode", (state.settings.parameters || {}).thinking_mode || "balanced", "Runtime reasoning parameter inherited by agents when not overridden.")}</div>
      <div style="margin-top:14px" class="config-grid">${editorCard("Effective Project Settings","Settings currently loaded for the selected project.","JSON", JSON.stringify(state.settings, null, 2))}${editorCard("Effective Workflow","Workflow definition used by the state machine.","JSON", JSON.stringify(state.workflow, null, 2))}<div class="side-stack">${routingSummaryCard()}${teamCard()}</div></div>
    </section>
    <aside class="side-stack">${memoryCard()}${recentEventsCard()}</aside>
  </div>`;
}
function projectTabs() {
  return [
    {key:"configuration", label:"Configuration"},
    {key:"settings", label:"Settings"},
    {key:"workflow", label:"Workflow"},
    {key:"routing", label:"Routing"},
    {key:"integrations", label:"Integrations"},
    {key:"history", label:"History"},
    {key:"activity", label:"Activity"}
  ];
}
function projectTabContent() {
  const renderers = {
    configuration: projectConfigurationTab,
    settings: projectSettingsTab,
    workflow: projectWorkflowTab,
    routing: projectRoutingTab,
    integrations: projectIntegrationsTab,
    history: projectHistoryTab,
    activity: projectActivityTab
  };
  return (renderers[state.projectTab] || projectConfigurationTab)();
}
function projectConfigurationTab() {
  return `<div class="config-grid">${editorCard("Agents","Define the agents available in this project.","YAML", yamlish(state.settings.agents || defaultAgents()))}${editorCard("Parameters","Configure runtime parameters and defaults.","JSON", JSON.stringify(state.settings.parameters || defaultParameters(), null, 2))}<div class="side-stack">${workflowPresetCard()}${routingSummaryCard()}${teamCard()}</div></div>`;
}
function projectSettingsTab() {
  return `<div class="config-grid">${editorCard("Project Settings","Identity, defaults, and runtime settings for this project.","JSON", JSON.stringify({project: currentProject(), settings: state.settings}, null, 2))}${editorCard("Task Policy","How DevWerk should manage task continuity, memory and approvals.","JSON", JSON.stringify(defaultTaskPolicy(), null, 2))}<div class="side-stack">${settingsTile("Task Continuity","Conversation-driven","Same topic continues active task; explicit new work starts a task.")}${settingsTile("Approval Mode","Automatic","Kanban state machine drives work without manual tab buttons.")}${settingsTile("Memory Scope","Project + Task","Project memory is injected into every task context.")}</div></div>`;
}
function projectWorkflowTab() {
  return `<div class="config-grid">${editorCard("Workflow Definition","Columns, semantic actions, and transition rules.","JSON", JSON.stringify(state.workflow || {}, null, 2))}<div class="card card-pad"><div class="h3">Columns</div><table class="data-table" style="margin-top:12px"><thead><tr><th>Key</th><th>Title</th><th>Tasks</th></tr></thead><tbody>${columns().map(c=>`<tr><td>${esc(c.status_key)}</td><td>${esc(c.title || STAGE_TITLES[c.status_key] || c.status_key)}</td><td>${(c.tasks || []).length}</td></tr>`).join("")}</tbody></table></div><div class="side-stack">${workflowPresetCard()}${workflowHealthSmallCard()}</div></div>`;
}
function projectRoutingTab() {
  const agents = state.settings.agents || defaultAgents();
  return `<div class="section-grid"><section class="card card-pad"><div class="h3">Agent Routing</div><table class="data-table" style="margin-top:12px"><thead><tr><th>Agent</th><th>Role</th><th>Model Route</th><th>Tools</th></tr></thead><tbody>${Object.entries(agents).map(([id,agent])=>`<tr><td>${esc(id)}</td><td>${esc(agent.role || agent.name || "-")}</td><td>${esc(agent.model_route || agent.model || "default")}</td><td>${esc((agent.tools || []).join(", ") || "-")}</td></tr>`).join("")}</tbody></table></section><aside class="side-stack">${routingSummaryCard()}${routingCard()}</aside></div>`;
}
function projectIntegrationsTab() {
  const integrations = [{name:"IDE Capability Provider", status:"Available via plugin/MCP contract", detail:"Compilation, diagnostics, source map, file read/write and command execution are capability requests."},{name:"MCP Server", status:"Backend exposed", detail:"External clients can call DevWerk backend capabilities without changing plugin APIs."},{name:"CI / Terminal", status:"Project-configured", detail:"Verification commands belong to project settings, not hardcoded backend behavior."},{name:"Git / PR", status:"Planned", detail:"Task artifacts and events are ready to support repository integrations."}];
  return `<div class="section-grid"><section class="card card-pad"><div class="h3">Integrations</div><table class="data-table" style="margin-top:12px"><thead><tr><th>Name</th><th>Status</th><th>Contract</th></tr></thead><tbody>${integrations.map(i=>`<tr><td>${esc(i.name)}</td><td>${esc(i.status)}</td><td>${esc(i.detail)}</td></tr>`).join("")}</tbody></table></section><aside class="side-stack">${commandsCard()}${routingSummaryCard()}</aside></div>`;
}
function projectHistoryTab() {
  return `<div class="section-grid"><section class="card card-pad"><div class="h3">Project History</div><div class="timeline-list" style="margin-top:12px">${state.events.length ? state.events.map(eventTimeline).join("") : `<div class="muted">No project history yet.</div>`}</div></section><aside class="side-stack">${recentEventsCard()}${memoryCard()}</aside></div>`;
}
function projectActivityTab() {
  return `<div class="section-grid"><section class="card card-pad"><div class="h3">Activity</div><table class="data-table" style="margin-top:12px"><thead><tr><th>Task</th><th>Status</th><th>Updated</th><th>Priority</th></tr></thead><tbody>${allTasks().map(t=>`<tr><td>${esc(t.title)}</td><td>${esc(STAGE_TITLES[t.status_key] || t.status_key || "-")}</td><td>${relative(t.updated_at)}</td><td>${priorityLabel(t.priority)}</td></tr>`).join("") || `<tr><td colspan="4" class="muted">No tasks yet.</td></tr>`}</tbody></table></section><aside class="side-stack">${recentTasksCard()}${healthCard()}</aside></div>`;
}
function renderKanbanPage() {
  $("page").innerHTML = `<div class="kanban-page"><section class="kanban-main">
    <div class="card kanban-header"><div class="title-block"><span class="folder-icon" style="width:32px;height:32px"></span><div><h1 class="h2">Workflow Pipeline <span class="soft">...</span></h1><div class="muted">${allTasks().length} tasks across ${columns().length} stages</div></div></div><div class="filter-row"><span class="muted">Quick filters:</span><span class="chip active">All</span><span class="chip">My tasks</span><span class="chip">Blocked</span><span class="chip">High priority</span><span class="muted" style="margin-left:18px">Assignee:</span><select class="select-pill"><option>All assignees</option></select><button id="createTask" class="button primary">+ Create Task</button><button class="icon-button">...</button></div></div>
    <div class="board">${columns().map(columnHtml).join("")}</div>
  </section><aside class="card inspector">${inspectorHtml()}</aside></div>`;
  $("createTask").onclick = createTaskFromPrompt;
}
function renderTaskPage() {
  const task = activeBoardTask() || DEMO_TASKS[0];
  const events = state.events.filter(e => !task.id || e.task_id === task.id).slice(0, 8);
  const artifacts = task.artifacts || [];
  $("page").innerHTML = `<div class="task-page"><section class="task-main">
    <div class="card task-header"><div class="breadcrumb">Projects &gt; ${esc(state.projectId)} &gt; Tasks &gt; ${esc(task.id)}</div><div class="task-title-row"><div class="task-title-left"><span class="timeline-dot"></span><h1 class="h1">${esc(task.title || "Task detail")}</h1><span class="soft">...</span></div><div class="task-actions"><button class="button">Review</button><button class="button primary">Apply</button><button class="button">Re-run</button><button class="button">Open PR</button><button class="icon-button">...</button></div></div><div class="task-meta-row"><span class="badge ${task.status_key === "failed" ? "red" : "green"}">${esc(STAGE_TITLES[task.status_key] || task.status_key || "Active")}</span><span class="meta-chip">Stage: <b>${esc(STAGE_TITLES[task.status_key] || task.status_key || "-")}</b></span><span class="meta-chip">Priority: <b>${priorityLabel(task.priority)}</b></span><span class="meta-chip">Owner: <span class="avatar" style="width:24px;height:24px;font-size:10px">EE</span> <b>Evan Engineer</b></span></div><div class="task-meta-row"><span class="meta-chip">Created: <b>${dateShort(task.created_at)}</b></span><span class="meta-chip">Updated: <b>${relative(task.updated_at)}</b></span><span>Task ID: <b>${esc(task.id)}</b></span><span style="margin-left:auto"><button class="button">Open in editor</button></span></div></div>
    <section class="card"><div class="detail-tabs">${["Summary","Plan","Diff / Artifacts","Events","Memory Context"].map((t,i)=>`<span class="tab ${i===0?"active":""}">${t}</span>`).join("")}</div><div class="summary-grid">
      <div class="summary-card"><div class="h3">What was done</div><p class="muted">${esc(task.description || "DevWerk collected evidence, planned the work, and moved it through the workflow.")}</p><div class="h3">Acceptance criteria</div><ul class="clean"><li>Task has a workflow status</li><li>Events are recorded</li><li>Artifacts and memory can be inspected</li></ul></div>
      <div class="summary-card"><div class="h3">Scope</div><ul class="clean"><li>Project: ${esc(task.project_id || state.projectId)}</li><li>Status: ${esc(task.status_key || "-")}</li><li>Priority: ${esc(String(task.priority || 0))}</li></ul><div class="h3" style="margin-top:14px">Out of scope</div><ul class="clean"><li>Manual column dragging</li><li>Bypassing workflow state machine</li></ul></div>
      <div class="summary-card wide"><div class="h3">Checklist</div><div class="check-list">${["Plan recorded","Context gathered","Implementation tracked","Review evidence available","Verification tracked","Memory updated"].map(x=>`<div class="check-item"><span class="ok">✓</span><span>${x}</span></div>`).join("")}</div></div>
      <div class="summary-card"><div class="h3">Touched paths</div>${filesHtml(artifacts)}<a class="link">Show more files</a></div>
      <div class="summary-card"><div class="h3">Assistant notes</div><p class="muted">${esc(latestArtifactSummary(task) || "No assistant notes recorded yet.")}</p><div class="soft" style="font-size:12px">Generated by DevWerk Assistant <span style="float:right">${relative(task.updated_at)}</span></div></div>
      <div class="summary-card wide exec-log"><div class="log-head"><div class="h3">Execution log (reasoning + actions)</div><button class="small-button">Copy log</button></div><div class="log-body"><div class="steps">${columns().slice(0,6).map(c=>`<div class="step ${c.status_key===task.status_key?"active":""}"><span class="timeline-dot" style="width:12px;height:12px"></span><b>${esc(c.title)}</b><span>${statusCount(c.status_key)}</span></div>`).join("")}</div><div class="log-lines">${events.length ? events.map(e=>`${dateTime(e.created_at)}  ${esc(e.event_type)} ${esc(e.from_status||"")} ${e.to_status ? "-> "+esc(e.to_status) : ""}`).join("<br/>") : "No task events recorded yet."}</div></div></div>
    </div></section></section><aside class="task-side">${timelineCard(events)}${linkedFilesCard(artifacts)}${commandsCard()}${memorySnippetsCard()}</aside></div>`;
}
function pipelineHtml() {
  const active = activeStage();
  let activeSeen = false;
  return `<div class="card pipeline"><div class="pipeline-title">Workflow Pipeline</div><div class="pipeline-row">${STAGES.map(stage => {
    const done = !activeSeen && stage !== active;
    if (stage === active) activeSeen = true;
    return `<div class="stage ${stage === active ? "active" : done ? "done" : ""}"><span class="stage-icon">${done ? "✓" : ""}</span><span class="stage-label">${STAGE_TITLES[stage]} <b>${statusCount(stage)}</b></span></div>`;
  }).join("")}</div></div>`;
}
function healthCard(){ return `<div class="card card-pad"><div style="display:flex;gap:10px;align-items:center"><span class="ok">✓</span><div><div class="h3">Healthy</div><div class="muted" style="font-size:12px">All systems operational</div></div></div><div style="margin-top:18px" class="muted">Active Stage</div><div style="margin-top:8px"><span class="badge blue">${esc(STAGE_TITLES[activeStage()] || activeStage())}</span></div><div class="muted" style="font-size:12px;margin-top:6px">${statusCount(activeStage())} tasks in progress</div><div class="progress"><span style="width:${Math.max(12, Math.round(((STAGES.indexOf(activeStage()) + 1) / STAGES.length) * 100))}%"></span></div></div>`; }
function usageCard(u){ return `<div class="card card-pad"><div style="display:flex;justify-content:space-between"><div class="h3">Token Usage</div><button class="small-button">Today</button></div><div style="font-size:20px;font-weight:850;margin-top:12px">${compact(u.total)} / 1,000,000</div><div class="progress"><span style="width:${Math.min(100, Math.round((u.total || 0) / 10000))}%"></span></div><div class="metric-lines"><div class="metric-line"><span>Input Tokens</span><b>${compact(u.input)}</b></div><div class="metric-line"><span>Output Tokens</span><b>${compact(u.output)}</b></div><div class="metric-line"><span>Total Tokens</span><b>${compact(u.total)}</b></div></div><div class="sparkbars">${[16,24,11,30,18,26,14,20,22,36,28,34,18,42,24,30,16,52,34,44,48,28,36,22].map(h=>`<i style="height:${h}px"></i>`).join("")}</div></div>`; }
function routingCard(){ return `<div class="card card-pad"><div class="h3">Model Routing</div><div class="metric-lines" style="margin-top:12px"><div><div class="muted">Current Route</div><b>${esc(modelRoutes()[0] || "default")}</b></div><div><div class="muted">Fallback Route</div><b>${esc(modelRoutes()[1] || modelRoutes()[0] || "default")}</b></div><div><div class="muted">Thinking Mode</div><b>${esc((state.settings.parameters || {}).thinking_mode || "Balanced")}</b></div></div></div>`; }
function conversationCard(){ return `<section class="card chat-card"><div class="tabs"><span class="tab active">Conversation</span><span class="tab">Workflow Log</span><span class="tab">Artifacts</span><span style="margin-left:auto;display:flex;gap:8px;align-items:center;margin-bottom:8px"><button class="small-button">Auto</button><button class="icon-button">Run</button><button id="clearChat" class="small-button">Clear</button></span></div><div id="chatBody" class="chat-body">${conversationHtml()}</div><div class="composer"><div class="composer-box"><textarea id="prompt" class="composer-input" placeholder="Message DevWerk about this project, workflow, or task..."></textarea><div class="composer-actions"><div class="tool-row"><span class="tool">A</span><span class="tool">F</span><span class="tool">&lt;/&gt;</span><span class="tool">B</span></div><div style="display:flex;gap:10px"><select class="select-pill">${modelRoutes().map(m=>`<option>${esc(m)}</option>`).join("")}</select><button id="send" class="send-button">></button></div></div></div></div></section>`; }
function conversationHtml() {
  const msgs = state.conversation.length ? state.conversation : [{role:"assistant", content:"I will help you break this down into actionable tasks and move them through the workflow. Tell me what you want DevWerk to build, review, research, or organize."}];
  return msgs.map(message => message.role === "user"
    ? `<div class="user-bubble"><div class="message-meta">You</div>${esc(displayMessageContent(message))}</div>`
    : `<div class="message"><span class="bot-badge">D</span><div class="message-content"><div class="message-meta">DevWerk Assistant</div>${esc(displayMessageContent(message))}</div></div>`
  ).join("");
}
function wireConversation() {
  const send = $("send"); const prompt = $("prompt");
  if (!send || !prompt) return;
  send.onclick = sendProjectMessage;
  prompt.addEventListener("keydown", event => { if (event.key === "Enter" && (event.ctrlKey || event.metaKey)) { event.preventDefault(); sendProjectMessage(); }});
  const task = $("heroNewTask"); if (task) task.onclick = () => { prompt.value = "Start a new workflow task for this project."; prompt.focus(); };
  const plan = $("heroPlan"); if (plan) plan.onclick = () => { prompt.value = "Design or revise the workflow plan for this project."; prompt.focus(); };
}
async function sendProjectMessage() {
  const prompt = $("prompt"); const content = (prompt.value || "").trim();
  if (!content || state.busy) return;
  state.busy = true; prompt.disabled = true; $("send").disabled = true;
  state.conversation.push({role:"user", content});
  renderOverviewPage();
  try {
    const result = await api(`${API}/kanban/projects/${encodeURIComponent(state.projectId)}/conversation`, {method:"POST", body: JSON.stringify({action:"message", message:content, messages:state.conversation, metadata:{active_task_id: state.activeTask?.id || state.activeTask?.task_id || null}})});
    if (result && result.task_id) state.activeTask = {id: result.task_id, status_key: result.status_key || "queued"};
    await Promise.allSettled([loadConversation(), loadBoard(), loadEvents()]);
    renderOverviewPage();
  } catch (error) {
    alert(error.message || String(error));
  } finally {
    state.busy = false;
    const currentPrompt = $("prompt");
    const currentSend = $("send");
    if (currentPrompt) currentPrompt.disabled = false;
    if (currentSend) currentSend.disabled = false;
  }
}
function recentTasksCard(){ const tasks=allTasks().slice(0,5); return `<div class="card card-pad"><div style="display:flex;justify-content:space-between;margin-bottom:16px"><div class="h3">Recent Tasks</div><a class="link" href="/tasks?project_id=${escAttr(state.projectId)}">View all</a></div><div class="list">${tasks.map(t=>`<div class="list-row"><span class="timeline-dot"></span><div class="grow"><div class="list-row-title">${esc(t.title)}</div></div><span><i class="dot ${stageColor(t.status_key)}"></i>${esc(STAGE_TITLES[t.status_key] || t.status_key || "Draft")}</span></div>`).join("")}<div class="list-row"><span>+</span><a class="link">New Task</a></div></div></div>`; }
function memoryCard(){ const mem=state.memory || {}; return `<div class="card card-pad"><div style="display:flex;justify-content:space-between;margin-bottom:16px"><div class="h3">Memory Status</div><a class="link">View</a></div><div class="metric-lines"><div class="metric-line"><b>Frameworks</b><b>${countMemory(mem,"framework")}</b></div><div class="metric-line"><b>Codebase Paths</b><b>${countMemory(mem,"path")}</b></div><div class="metric-line"><b>Commands</b><b>${countMemory(mem,"command")}</b></div><div class="metric-line"><b>Recent Summaries</b><b>${countMemory(mem,"summary")}</b></div></div><div class="muted" style="margin-top:20px;font-size:12px">Last updated: ${relative(mem.updated_at)} <i class="dot green" style="float:right"></i></div></div>`; }
function recentEventsCard(){ return `<div class="card card-pad"><div style="display:flex;justify-content:space-between;margin-bottom:16px"><div class="h3">Recent Events</div><a class="link">View all</a></div><div class="list">${state.events.slice(0,5).map(e=>`<div class="list-row"><span class="timeline-dot"></span><div><div class="list-row-title">${esc(dateTime(e.created_at))} ${esc(eventTitle(e))}</div><div class="list-row-sub">${esc(e.task_title || e.to_status || state.projectId)}</div></div></div>`).join("") || `<div class="muted">No events yet.</div>`}</div></div>`; }
function kpi(label,value,trend){ return `<div class="card kpi"><div class="kpi-label">${label}<span class="kpi-trend">up ${trend}</span></div><div class="kpi-value">${value}</div></div>`; }
function projectCard(project){ const stats=project.stats || {}; return `<button class="project-card ${project.id === state.projectId ? "selected" : ""}" data-project-card="${escAttr(project.id)}"><div class="card-top"><div class="project-title">${esc(project.name || project.id)}</div><span class="badge ${projectStatus(project).badge}">${projectStatus(project).label}</span></div><div class="muted" style="font-size:12px;line-height:1.45;margin-top:12px">default<br/>${esc(modelRoutes()[0] || "default")}</div><div class="card-meta"><div><div class="meta-label">Tasks</div><div class="meta-value">${stats.task_count || stats.tasks || 0}</div></div><div><div class="meta-label">In Progress</div><div class="meta-value">${stats.in_progress || 0}</div></div><div><div class="meta-label">Last activity</div><div class="meta-value">${relative(project.updated_at)}</div></div><div><div class="meta-label">Health</div><div class="meta-value">Healthy</div></div></div><div class="mini-spark"><i style="height:8px"></i><i style="height:14px"></i><i style="height:11px"></i><i style="height:18px"></i></div></button>`; }
function editorCard(title, desc, mode, content){ const lines=String(content || "").split("\n"); return `<div class="editor-card card"><div class="editor-head"><div><div class="h3">${title}</div><div class="muted" style="font-size:12px;margin-top:4px">${desc}</div></div><div><button class="small-button">${mode}</button><button class="icon-button">X</button></div></div><div class="editor"><div class="line-nos">${lines.map((_,i)=>i+1).join("<br/>")}</div><pre class="code">${esc(lines.join("\n"))}</pre></div><div class="editor-foot"><span><a class="link">Validate</a> <i class="dot green"></i> Valid</span><span><button class="small-button">Format</button> <button class="small-button">Save</button></span></div></div>`; }
function workflowPresetCard(){ return `<div class="card side-card"><div class="h3">Workflow Presets</div><div class="muted" style="font-size:12px;margin-top:4px">Manage reusable workflow configurations.</div><div style="margin-top:12px">${["Standard Dev Flow","Code Review Flow","Bug Triage Flow"].map((p,i)=>`<div class="preset-row"><span>${p} ${i===0?'<span class="badge blue">Default</span>':""}</span><span>${i===1?"✓":"..."}</span></div>`).join("")}<a class="link">+ New Preset</a></div></div>`; }
function routingSummaryCard(){ return `<div class="card side-card"><div class="h3">Routing Summary</div><div class="muted" style="font-size:12px;margin-top:4px">How requests are routed in this project.</div><div class="metric-lines" style="margin-top:12px"><div class="metric-line"><b>Default Model</b><span>${esc(modelRoutes()[0] || "default")}</span></div><div class="metric-line"><b>Fallback Model</b><span>${esc(modelRoutes()[1] || modelRoutes()[0] || "default")}</span></div><div class="metric-line"><b>Routing Strategy</b><span>Balanced</span></div></div><a class="link">View routing rules &rarr;</a></div>`; }
function teamCard(){ return `<div class="card side-card"><div class="h3">Team & Access</div><div class="muted" style="font-size:12px;margin-top:4px">Who can access and edit this project.</div><div style="display:flex;gap:6px;margin:14px 0"><span class="avatar">EE</span><span class="avatar">JS</span><span class="avatar">AM</span><span class="avatar">KT</span><span class="avatar blue">+3</span></div><div class="metric-lines"><div class="metric-line"><b>Role</b><span>Admin</span></div><div class="metric-line"><b>Access</b><span>7 members</span></div></div><a class="link">Manage access &rarr;</a></div>`; }
function infoItem(label,value){ return `<div class="info-item"><span class="info-icon">I</span><div><div class="muted">${label}</div><b>${esc(value || "-")}</b></div></div>`; }
function columnHtml(col){ return `<div class="column ${col.status_key === activeStage() ? "active" : ""}"><div class="col-head"><span>${esc(col.title || STAGE_TITLES[col.status_key] || col.status_key)} <span class="count">${(col.tasks || []).length}</span></span><span>+</span></div><div class="cards">${(col.tasks || []).slice(0,5).map(taskCardHtml).join("")}<div class="add-task">+ Add task</div></div></div>`; }
function taskCardHtml(t){ return `<button class="task-card" data-task="${escAttr(t.id)}"><div class="task-id">${esc(shortTaskId(t.id))}</div><div class="task-title">${esc(t.title || "Untitled task")}</div><div class="task-desc">${esc(t.description || "Workflow-managed task.")}</div><div class="tags"><span class="tag">${esc(t.status_key || "task")}</span></div><div class="task-foot"><span class="avatar ${avatarColor(t.priority)}" style="width:24px;height:24px;font-size:10px">EE</span><span class="priority"><i class="dot ${priorityColor(t.priority)}"></i>${priorityLabel(t.priority)}</span></div><div class="task-foot"><span>${relative(t.updated_at)}</span><span>${(t.metadata && t.metadata.files) || 0} files</span></div></button>`; }
function inspectorHtml(){ const blocked=allTasks().filter(t=>t.status_key === "failed").slice(0,2); return `<div style="display:flex;justify-content:space-between"><div class="h3">Workflow Health</div><span>^</span></div><div class="ring"><div class="ring-inner"><div class="ring-number">92</div><div class="muted" style="font-size:11px">Health Score</div></div></div><div class="metric-lines"><div class="metric-line"><span><i class="dot green"></i> On Track</span><b>${allTasks().length}</b></div><div class="metric-line"><span><i class="dot orange"></i> At Risk</span><b>${allTasks().filter(t=>t.priority===2).length}</b></div><div class="metric-line"><span><i class="dot red"></i> Blocked</span><b>${blocked.length}</b></div></div><hr style="border:none;border-top:1px solid var(--border);width:100%"/><div><div style="display:flex;justify-content:space-between"><b>Cycle Time (avg)</b><a class="link">View trend</a></div><div class="kpi-value">4h 32m <span class="kpi-trend">down 12%</span></div><div class="muted">vs last 7 days</div></div><div><b>Throughput (7d)</b><div class="kpi-value">${allTasks().filter(t=>["done","verified"].includes(t.status_key)).length} <span class="kpi-trend">up 18%</span></div><div class="muted">tasks completed</div></div><div><div style="display:flex;justify-content:space-between"><b>Blocked Tasks</b><a class="link">View all</a></div>${(blocked.length?blocked:DEMO_TASKS.slice(0,2)).map(t=>`<div class="blocked"><div class="task-id">${esc(shortTaskId(t.id))} <span class="priority"><i class="dot ${priorityColor(t.priority)}"></i>${priorityLabel(t.priority)}</span></div><b>${esc(t.title)}</b><div class="muted">${esc(t.description || "Waiting for workflow progress.")}<br/>Since ${relative(t.updated_at)}</div></div>`).join("")}</div><div><div style="display:flex;justify-content:space-between"><b>Recent Activity</b><a class="link">View all</a></div><div class="list" style="margin-top:10px">${state.events.slice(0,4).map(e=>`<div class="list-row"><span class="timeline-dot"></span><div class="grow">${esc(eventTitle(e))}</div><span class="muted">${relative(e.created_at)}</span></div>`).join("")}</div></div><button class="button">Export Board</button>`; }
function timelineCard(events){ return `<div class="card side-card"><div class="side-title"><div class="h3">Timeline</div><a class="link">View all</a></div><div class="list">${(events.length ? events : state.events.slice(0,5)).map(e=>`<div class="list-row"><span class="timeline-dot"></span><div class="grow">${esc(eventTitle(e))}</div><span class="muted">${dateTime(e.created_at)}</span></div>`).join("") || "<div class='muted'>No timeline yet.</div>"}</div></div>`; }
function linkedFilesCard(artifacts){ return `<div class="card side-card"><div class="side-title"><div class="h3">Linked files</div><a class="link">View diff</a></div>${filesHtml(artifacts)}<a class="link">Show more files</a></div>`; }
function commandsCard(){ return `<div class="card side-card"><div class="h3" style="margin-bottom:10px">Related commands</div>${["project.compile","source.diagnostics","process.run","workspace.search"].map(c=>`<span class="command">${c}</span>`).join("")}<br/><a class="link">Show more</a></div>`; }
function memorySnippetsCard(){ return `<div class="card side-card"><div class="side-title"><div class="h3">Memory / Context</div><a class="link">View in Memory</a></div><div class="muted" style="font-size:12px">Relevant snippets</div><div class="snippet"><b>Project Memory</b><br/>${esc(JSON.stringify(state.memory || {}).slice(0,120) || "No memory recorded yet.")}</div><div class="snippet"><b>Workflow Context</b><br/>Kanban remains the state-machine driver.</div></div>`; }
function filesHtml(artifacts){ const paths=[]; (artifacts || []).forEach(a => { if (a.path) paths.push(a.path); const payload = a.payload || {}; (payload.changed_paths || payload.paths || []).forEach(x => paths.push(String(x))); }); const list=(paths.length ? paths : ["workflow_request_body","plan_bundle","code_change_bundle"]).slice(0,6); return `<div class="files" style="margin-top:10px">${list.map((p,i)=>`<div class="files-row"><span>${esc(p)}</span><span><span class="plus">+${(i + 1) * 14}</span> <span class="minus">-0</span></span></div>`).join("")}</div>`; }
function eventRow(event){ const payload = event.payload || {}; return `<tr><td>${esc(dateTime(event.created_at))}</td><td>${esc(eventTitle(event))}</td><td>${esc(event.task_title || event.task_id || "-")}</td><td>${esc(event.from_status || "")}${event.to_status ? " -> " + esc(event.to_status) : ""}</td><td>${esc(payload.reason || payload.summary || payload.action || JSON.stringify(payload).slice(0, 120) || "-")}</td></tr>`; }
function eventTimeline(event){ const payload = event.payload || {}; return `<div class="timeline-item"><span class="timeline-dot"></span><div><b>${esc(eventTitle(event))}</b><div class="muted">${esc(payload.reason || payload.summary || payload.action || event.task_title || event.task_id || state.projectId)}</div></div><span class="muted">${relative(event.created_at)}</span></div>`; }
function memoryBucket(title, items){ const list = Array.isArray(items) ? items : []; return `<div class="card card-pad"><div class="h3">${title}</div><div class="pill-list">${list.length ? list.slice(-18).map(item=>`<span class="pill">${esc(typeof item === "string" ? item : JSON.stringify(item))}</span>`).join("") : `<span class="muted">No ${title.toLowerCase()} recorded yet.</span>`}</div></div>`; }
function memorySummaries(mem){ const summaries = Array.isArray(mem.phase_summaries) ? mem.phase_summaries : []; return `<div class="card card-pad"><div class="h3">Recent Summaries</div><div class="timeline-list" style="margin-top:12px">${summaries.length ? summaries.slice(-6).reverse().map(item=>`<div class="timeline-item"><span class="timeline-dot"></span><div><b>${esc(item.phase || item.task_id || "summary")}</b><div class="muted">${esc(item.summary || JSON.stringify(item).slice(0, 160))}</div></div><span class="muted">${relative(item.created_at || item.updated_at)}</span></div>`).join("") : `<div class="muted">No compact summaries yet.</div>`}</div></div>`; }
function settingsTile(title, value, detail){ return `<div class="card card-pad"><div class="h3">${title}</div><div class="kpi-value" style="font-size:18px">${esc(value)}</div><div class="muted" style="font-size:12px;line-height:1.55">${esc(detail)}</div></div>`; }
function workflowHealthSmallCard(){ return `<div class="card card-pad"><div class="h3">Workflow Health</div><div class="metric-lines" style="margin-top:12px"><div class="metric-line"><span><i class="dot green"></i> On Track</span><b>${allTasks().filter(t=>t.status_key !== "failed").length}</b></div><div class="metric-line"><span><i class="dot red"></i> Failed</span><b>${allTasks().filter(t=>t.status_key === "failed").length}</b></div><div class="metric-line"><span><i class="dot blue"></i> Active Stage</span><b>${esc(STAGE_TITLES[activeStage()] || activeStage())}</b></div></div></div>`; }
function defaultTaskPolicy(){ return {task_identity:"conversation_groups_related_messages", new_task_trigger:"explicit_new_work_or_agent_decision", approval_mode:"auto", memory_policy:"task_and_project", workflow_driver:"kanban_state_machine", manual_actions:["retry","abandon"]}; }
function activeSection(){ const hash = location.hash.replace("#", "").trim().toLowerCase(); return ["events","memory","analytics","settings"].includes(hash) ? hash : ""; }
function activeNav(){ return state.section || state.page; }
function normalizeMessages(messages) {
  const seen = new Set();
  const out = [];
  for (const message of messages) {
    const content = displayMessageContent(message).trim();
    if (!content) continue;
    const key = [message.role || "assistant", message.kind || "message", message.task_id || "", content].join("\u0001");
    if (seen.has(key)) continue;
    seen.add(key);
    out.push({...message, content});
  }
  return out;
}
function displayMessageContent(message) {
  const content = String(message && message.content != null ? message.content : "");
  const parsed = parseJsonDecision(content);
  if (!parsed) return content;
  if (parsed.reply && !looksLikeJson(parsed.reply)) return String(parsed.reply);
  if (parsed.action === "start_task") return "Starting the workflow task.";
  if (parsed.action === "continue_task") return "Continuing the active workflow task.";
  if (parsed.action === "save_design" || parsed.action === "design") return "Updating the project workflow design.";
  return "Project conversation updated.";
}
function parseJsonDecision(text) {
  const value = String(text || "").trim();
  if (!looksLikeJson(value)) return null;
  const start = value.indexOf("{");
  const end = firstJsonObjectEnd(value, start);
  if (start < 0 || end <= start) return null;
  try {
    const parsed = JSON.parse(value.slice(start, end + 1));
    return parsed && typeof parsed === "object" && ("action" in parsed || "reply" in parsed) ? parsed : null;
  } catch (_) {
    return null;
  }
}
function firstJsonObjectEnd(text, start) {
  if (start < 0) return -1;
  let depth = 0, inString = false, escaped = false;
  for (let i = start; i < text.length; i++) {
    const ch = text[i];
    if (inString) {
      if (escaped) escaped = false;
      else if (ch === "\\") escaped = true;
      else if (ch === "\"") inString = false;
      continue;
    }
    if (ch === "\"") inString = true;
    else if (ch === "{") depth++;
    else if (ch === "}") { depth--; if (depth === 0) return i; }
  }
  return -1;
}
function looksLikeJson(value) {
  const text = String(value || "").trim();
  return text.startsWith("{") || text.startsWith("[") || (text.includes('"action"') && text.includes('"reply"'));
}
function defaultAgents(){ return {"dev-assistant":{name:"DevWerk Assistant",role:"primary",description:"General purpose development assistant",model_route:"default",tools:["code","search","file_editor","terminal"]}}; }
function defaultParameters(){ return {model:modelRoutes()[0] || "default",temperature:0.2,max_tokens:8192,top_p:1,stream:true,thinking_mode:"balanced",retry:{attempts:3,backoff_ms:500}}; }
async function createProjectFromPrompt(){ const name=prompt("Project name","Untitled Project"); if(!name)return; const id=`project-${new Date().toISOString().replace(/[-:TZ.]/g,"").slice(0,17)}`; await api(`${API}/kanban/projects`,{method:"POST",body:JSON.stringify({project_id:id,name})}); state.projectId=id; await refreshAll(); location.href=`/workbench?project_id=${encodeURIComponent(id)}&new=1&project_name=${encodeURIComponent(name)}`; }
async function createTaskFromPrompt(){ const title=prompt("Task title","New workflow task"); if(!title)return; await api(`${API}/kanban/tasks`,{method:"POST",body:JSON.stringify({project_id:state.projectId,title,description:"Created from DevWerk Web UI."})}); await Promise.allSettled([loadBoard(),loadEvents()]); renderKanbanPage(); }
function currentProject(){ return state.projects.find(p => p.id === state.projectId) || {id: state.projectId, name: state.projectId, description: ""}; }
function projectStatus(project){ const raw = String(project.status || project.state || "").toLowerCase(); if(raw === "draft") return {label:"Draft", badge:"orange", dot:"orange"}; if(raw === "planned") return {label:"Planned", badge:"blue", dot:"blue"}; if(raw === "idle") return {label:"Idle", badge:"", dot:""}; return {label:"Active", badge:"green", dot:"green"}; }
function columns(){ const items = (state.board && state.board.columns) || []; if(items.length) return items; return STAGES.map(stage => ({status_key: stage, title: STAGE_TITLES[stage], tasks: DEMO_TASKS.filter(task => task.status_key === stage)})); }
function allTasks(){ return columns().flatMap(column => column.tasks || []); }
function activeStage(){ if(state.activeTask && state.activeTask.status_key) return state.activeTask.status_key; const task = allTasks().find(t => ["coding","reviewed","ready_to_apply"].includes(t.status_key)); return task ? task.status_key : "coding"; }
function statusCount(stage){ return columns().find(column => column.status_key === stage)?.tasks?.length || 0; }
function activeBoardTask(){ const requested = new URLSearchParams(location.search).get("task_id"); const tasks = allTasks(); return tasks.find(t => t.id === requested) || tasks.find(t => t.status_key === activeStage()) || tasks[0]; }
function modelRoutes(){ const agents = state.settings.agents || {}; const routes = new Set(); Object.values(agents).forEach(agent => { if(agent && agent.model_route) routes.add(agent.model_route); if(agent && agent.model) routes.add(agent.model); }); const params = state.settings.parameters || {}; if(params.model) routes.add(params.model); routes.add("anthropic/claude-3-5-sonnet"); return Array.from(routes); }
function usageTotals(){ const rows = state.usage.projects || []; return rows.reduce((a,r)=>{ a.calls += r.calls || 0; a.input += r.input_tokens || 0; a.output += r.output_tokens || 0; a.total += r.total_tokens || 0; a.duration += r.duration_ms || 0; return a; }, {calls:0,input:0,output:0,total:0,duration:0}); }
function countMemory(mem, key){ const text = JSON.stringify(mem || {}).toLowerCase(); return (text.match(new RegExp(key, "g")) || []).length; }
function latestArtifactSummary(task){ const artifacts = task.artifacts || []; const item = [...artifacts].reverse().find(a => a.payload && typeof a.payload.summary === "string"); return item ? item.payload.summary : ""; }
function yamlish(obj, indent = 0){ if(obj == null) return ""; if(typeof obj !== "object") return String(obj); return Object.entries(obj).map(([k,v]) => `${" ".repeat(indent)}${k}: ${typeof v === "object" ? "\n" + yamlish(v, indent + 2) : String(v)}`).join("\n"); }
function compact(n){ n = Number(n || 0); if(n >= 1000000) return (n / 1000000).toFixed(1) + "M"; if(n >= 1000) return (n / 1000).toFixed(1) + "K"; return String(n); }
function duration(ms){ ms = Number(ms || 0); if(!ms) return "0m"; const h = Math.floor(ms / 3600000), m = Math.round((ms % 3600000) / 60000); return h ? `${h}h ${m}m` : `${m}m`; }
function dateShort(value){ if(!value) return "-"; const d = new Date(value); return isNaN(d) ? String(value).slice(0,16) : d.toLocaleDateString(undefined,{month:"short",day:"numeric",year:"numeric"}); }
function dateTime(value){ if(!value) return ""; const d = new Date(value); return isNaN(d) ? String(value).slice(0,16) : d.toLocaleTimeString(undefined,{hour:"2-digit",minute:"2-digit"}); }
function relative(value){ if(!value) return "-"; const d = new Date(value); if(isNaN(d)) return String(value).slice(0,16); const diff = Math.max(0, Date.now() - d.getTime()); const m = Math.floor(diff / 60000); if(m < 1) return "now"; if(m < 60) return `${m}m ago`; const h = Math.floor(m / 60); if(h < 48) return `${h}h ago`; return `${Math.floor(h / 24)}d ago`; }
function eventTitle(e){ return String(e.event_type || "event").replace(/_/g, " "); }
function stageColor(s){ if(["coding","planned","ready_to_apply"].includes(s)) return "blue"; if(["done","verified","applied"].includes(s)) return "green"; if(s === "failed") return "red"; if(s === "draft") return "orange"; return ""; }
function priorityColor(p){ return Number(p || 0) >= 3 ? "red" : Number(p || 0) === 2 ? "orange" : "blue"; }
function avatarColor(p){ return Number(p || 0) >= 3 ? "" : Number(p || 0) === 2 ? "yellow" : "green"; }
function priorityLabel(p){ return Number(p || 0) >= 3 ? "P1" : Number(p || 0) === 2 ? "P2" : "P3"; }
function shortTaskId(id){ const text = String(id || "task"); return text.startsWith("T-") ? text : `T-${text.slice(0,4)}`; }
function esc(value){ return String(value ?? "").replace(/[&<>"']/g, ch => ({"&":"&amp;","<":"&lt;",">":"&gt;","\"":"&quot;","'":"&#39;"}[ch])); }
function escAttr(value){ return esc(value).replace(/`/g, "&#96;"); }
$("projectList").addEventListener("click", async event => { const button = event.target.closest("[data-project]"); if(!button) return; state.projectId = button.dataset.project; const url = state.page === "projects" ? "/dashboard" : state.page === "kanban" ? "/kanban" : state.page === "tasks" ? "/tasks" : "/workbench"; history.replaceState(null, "", `${url}?project_id=${encodeURIComponent(state.projectId)}`); await refreshAll(); });
$("projectSearch").addEventListener("input", renderShell);
$("newProjectRail").onclick = createProjectFromPrompt;
$("refresh").onclick = () => refreshAll().catch(err => alert(err.message || String(err)));
window.addEventListener("hashchange", () => { state.section = activeSection(); renderShell(); });
document.addEventListener("click", async event => {
  const project = event.target.closest("[data-project-card]");
  if(project) { state.projectId = project.dataset.projectCard; history.replaceState(null, "", `/dashboard?project_id=${encodeURIComponent(state.projectId)}`); await refreshAll(); }
  const task = event.target.closest("[data-task]");
  if(task) location.href = `/tasks?project_id=${encodeURIComponent(state.projectId)}&task_id=${encodeURIComponent(task.dataset.task)}`;
});
refreshAll().catch(error => { $("page").innerHTML = `<div class="card card-pad"><h1 class="h2">DevWerk UI failed to load</h1><p class="muted">${esc(error.message || String(error))}</p></div>`; });
"""
