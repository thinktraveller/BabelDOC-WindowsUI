import { describe, expect, it } from "vitest";

import { formatDuration, formatSize, kindLabel } from "./format";

describe("formatSize", () => {
  it("按量级显示单位", () => {
    expect(formatSize(0)).toBe("0 B");
    expect(formatSize(512)).toBe("512 B");
    expect(formatSize(2048)).toBe("2.0 KB");
    expect(formatSize(3 * 1024 * 1024)).toBe("3.0 MB");
  });
});

describe("formatDuration", () => {
  it("没有开始时间时显示占位符", () => {
    expect(formatDuration(null, null)).toBe("—");
  });

  it("计算秒与分", () => {
    expect(formatDuration("2026-09-20T10:00:00", "2026-09-20T10:00:45")).toBe("45 秒");
    expect(formatDuration("2026-09-20T10:00:00", "2026-09-20T10:02:05")).toBe("2 分 5 秒");
  });

  it("进行中的任务用当前时间计算", () => {
    const now = Date.parse("2026-09-20T10:00:10");
    expect(formatDuration("2026-09-20T10:00:00", null, now)).toBe("10 秒");
  });

  it("时间格式异常时返回占位符", () => {
    expect(formatDuration("not-a-date", "2026-09-20T10:00:00")).toBe("—");
  });
});

describe("kindLabel", () => {
  it("把成果类型翻译成中文", () => {
    expect(kindLabel("mono")).toBe("单语 PDF");
    expect(kindLabel("dual")).toBe("双语 PDF");
    expect(kindLabel("glossary")).toBe("术语 CSV");
    expect(kindLabel("log")).toBe("任务日志");
    expect(kindLabel("other")).toBe("other");
  });
});
