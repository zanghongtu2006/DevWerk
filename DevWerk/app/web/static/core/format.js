export function escapeHtml(value) {
  return String(value ?? "").replace(/[&<>"']/g, (character) => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;",
  })[character]);
}
export function shortId(value) {
  const text = String(value || "");
  return text.length > 16 ? `${text.slice(0, 7)}…${text.slice(-5)}` : text || "—";
}

export function formatDate(value) {
  if (!value) return "—";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return String(value);
  return new Intl.DateTimeFormat("zh-CN", { month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit", second: "2-digit" }).format(date);
}

export function relativeTime(value) {
  if (!value) return "—";
  const seconds = Math.round((new Date(value).getTime() - Date.now()) / 1000);
  const formatter = new Intl.RelativeTimeFormat("zh-CN", { numeric: "auto" });
  const ranges = [[60, "second"], [3600, "minute"], [86400, "hour"], [604800, "day"]];
  for (const [limit, unit] of ranges) {
    if (Math.abs(seconds) < limit) {
      const divisor = unit === "second" ? 1 : unit === "minute" ? 60 : unit === "hour" ? 3600 : 86400;
      return formatter.format(Math.round(seconds / divisor), unit);
    }
  }
  return formatDate(value);
}

export function statusTone(status) {
  if (["done", "succeeded"].includes(status)) return "success";
  if (["failed", "interrupted"].includes(status)) return "danger";
  if (status === "running") return "active";
  if (["waiting", "recovering"].includes(status)) return "warning";
  return "neutral";
}

export function statusLabel(status) {
  if (!status) return "Unknown";
  const value = String(status);
  if (knownStatuses.size && !knownStatuses.has(value)) return `Unknown (${value})`;
  return value.split("_").map((part) => part.charAt(0).toUpperCase() + part.slice(1)).join(" ");
}

export function truncate(value, length = 120) {
  const text = String(value || "");
  return text.length > length ? `${text.slice(0, length)}…` : text;
}
let knownStatuses = new Set();

export function configureStatusCatalog(catalog) {
  knownStatuses = new Set(
    Object.values(catalog || {}).flatMap((group) => group?.values || [])
  );
}
