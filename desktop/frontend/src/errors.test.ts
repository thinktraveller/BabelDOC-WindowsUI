import { describe } from "vitest";
import { expect, it } from "vitest";

import { describeErrorDetail } from "./errors";

describe("describeErrorDetail", () => {
  it("优先展示 detail 字符串", () => {
    expect(describeErrorDetail("仅支持 PDF 文件", 400)).toBe("仅支持 PDF 文件");
  });

  it("从 fastapi 的 detail 结构里取第一条参数错误", () => {
    const payload = {
      detail: { message: "参数不合法", errors: { pages: "页码范围超出文档页数" } },
    };
    expect(describeErrorDetail(payload, 400)).toBe(
      "参数不合法：页码范围超出文档页数",
    );
  });

  it("令牌错误有专门文案", () => {
    expect(describeErrorDetail({ detail: "缺少或无效的会话令牌" }, 403)).toBe(
      "缺少或无效的会话令牌",
    );
    expect(describeErrorDetail(null, 403)).toContain("会话令牌无效");
  });

  it("连接失败给出可操作提示", () => {
    expect(describeErrorDetail(null, 0)).toContain("无法连接本地服务");
  });

  it("未知错误回退到状态码文案", () => {
    expect(describeErrorDetail(null, 500)).toBe("请求失败（HTTP 500）");
  });
});
