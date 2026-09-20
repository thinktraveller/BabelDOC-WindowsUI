/** 展示用格式化（纯函数，便于单元测试）。 */
export function kindLabel(kind: string): string {
  switch (kind) {
    case "mono":
      return "单语 PDF";
    case "dual":
      return "双语 PDF";
    case "glossary":
      return "术语 CSV";
    case "log":
      return "任务日志";
    default:
      return kind;
  }
}

export function formatSize(bytes: number): string {
  if (!bytes) {
    return "0 B";
  }
  if (bytes < 1024) {
    return `${bytes} B`;
  }
  if (bytes < 1024 * 1024) {
    return `${(bytes / 1024).toFixed(1)} KB`;
  }
  return `${(bytes / 1024 / 1024).toFixed(1)} MB`;
}

export function formatDuration(
  startedAt: string | null,
  finishedAt: string | null,
  now: number = Date.now(),
): string {
  if (!startedAt) {
    return "—";
  }
  const started = Date.parse(startedAt);
  const finished = finishedAt ? Date.parse(finishedAt) : now;
  if (Number.isNaN(started) || Number.isNaN(finished)) {
    return "—";
  }
  const seconds = Math.max(0, Math.round((finished - started) / 1000));
  if (seconds < 60) {
    return `${seconds} 秒`;
  }
  return `${Math.floor(seconds / 60)} 分 ${seconds % 60} 秒`;
}
