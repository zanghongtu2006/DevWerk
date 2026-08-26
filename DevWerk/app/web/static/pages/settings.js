import { escapeHtml } from "../core/format.js?v=20260804-debug1";

function settingValue(values, key) {
  return key.split(".").reduce((value, part) => value?.[part], values);
}

export function renderSettings(state) {
  const settings = state.settings;
  if (!settings) return '<div class="page-loading">正在加载全局设置…</div>';
  const fields = (settings.fields || []).map((field) => {
    const checked = Boolean(settingValue(settings.values, field.key));
    return `<label class="settings-row">
      <span class="settings-copy"><b>${escapeHtml(field.label)}</b><small>${escapeHtml(field.description)}</small><em>${escapeHtml(field.key)}${field.restart_required ? " · 保存后自动重启" : ""}</em></span>
      <span class="settings-switch"><input type="checkbox" data-setting-key="${escapeHtml(field.key)}" ${checked ? "checked" : ""}><i></i></span>
    </label>`;
  }).join("");
  return `<div class="settings-page">
    <header class="page-heading"><div><span class="eyebrow">GLOBAL SETTINGS</span><h1>全局设置</h1><p>设置保存在 config/global-settings.yaml。仅展示 DevWerk 已声明并验证的配置项。</p></div></header>
    <form id="global-settings-form" class="card settings-card">
      <div class="settings-section-head"><div><span class="eyebrow">WORKFLOW</span><h2>Workflow 启动行为</h2></div><span class="read-only-label">YAML BACKED</span></div>
      <div class="settings-list">${fields}</div>
      <footer class="settings-actions"><span>需要重启的变更会先保存，再由 startup.bat 使用项目 venv 自动拉起服务。</span><button class="button primary" type="submit">保存设置</button></footer>
    </form>
  </div>`;
}
