/* Turning values into the words on screen. */

export function formatBytes(bytes) {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${Math.round(bytes / 1024)} KB`;
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
}

export function relativeTime(timestamp) {
  const seconds = Math.round((Date.now() - timestamp) / 1000);
  if (!Number.isFinite(seconds)) return "";
  if (seconds < 60) return "just now";
  const minutes = Math.round(seconds / 60);
  if (minutes < 60) return `${minutes} min ago`;
  const hours = Math.round(minutes / 60);
  if (hours < 24) return `${hours} h ago`;
  return new Date(timestamp).toLocaleDateString();
}

export function relativeDate(isoString) {
  const parsed = Date.parse(isoString);
  return Number.isNaN(parsed) ? "" : relativeTime(parsed);
}

export function displayName(pdfKey) {
  // uploads are stored as <prefix><name>-<8 hex>.pdf; neither the project
  // folder nor the suffix is worth reading, they only keep keys unique
  return String(pdfKey)
    .replace(/^.*\//, "")
    .replace(/-[0-9a-f]{8}(\.pdf)$/i, "$1");
}

export function plural(count, one, many) {
  return `${count} ${count === 1 ? one : many}`;
}
