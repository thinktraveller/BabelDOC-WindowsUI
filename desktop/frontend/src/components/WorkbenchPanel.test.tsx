import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { App as AntApp, ConfigProvider } from "antd";
import zhCN from "antd/locale/zh_CN";
import { cleanup, fireEvent, render, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import WorkbenchPanel from "./WorkbenchPanel";

/**
 * 回归测试：用户报告「导入选中文件时显示 HTTP 422」。
 *
 * 根因是导入按钮用 ``item.originFileObj`` 取真实文件，而 Ant Design 传给
 * ``beforeUpload`` 的 fileList 是原始文件对象、没有 ``originFileObj`` 字段，
 * 过滤后变成空数组，上传请求里没有任何文件字段，FastAPI 于是返回 422。
 */

class FakeXhr {
  static instances: FakeXhr[] = [];

  upload: { onprogress: ((event: ProgressEvent) => void) | null } = {
    onprogress: null,
  };

  status = 0;
  responseText = "";
  onload: (() => void) | null = null;
  onerror: (() => void) | null = null;
  onabort: (() => void) | null = null;
  body: FormData | null = null;

  open(): void {
    /* 测试桩：不需要真的发请求 */
  }

  setRequestHeader(): void {
    /* 测试桩 */
  }

  send(body: FormData): void {
    this.body = body;
    FakeXhr.instances.push(this);
  }

  abort(): void {
    /* 测试桩 */
  }
}

function jsonResponse(payload: unknown, status = 200): Response {
  return new Response(JSON.stringify(payload), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

function renderPanel() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <ConfigProvider locale={zhCN}>
      <AntApp>
        <QueryClientProvider client={client}>
          <WorkbenchPanel onOpenSettings={() => {}} />
        </QueryClientProvider>
      </AntApp>
    </ConfigProvider>,
  );
}

describe("导入选中文件", () => {
  function importButton(): HTMLButtonElement {
    const label = Array.from(document.querySelectorAll("button span")).find(
      (node) => node.textContent?.trim() === "导入选中文件",
    );
    if (!label) {
      throw new Error("找不到「导入选中文件」按钮");
    }
    return label.closest("button") as HTMLButtonElement;
  }

  beforeEach(() => {
    FakeXhr.instances = [];
    // 模拟窗口注入的会话令牌：没有它请求会先等待令牌注入
    (window as unknown as { __WORKBENCH_TOKEN__?: string }).__WORKBENCH_TOKEN__ =
      "test-session-token";
    vi.stubGlobal("XMLHttpRequest", FakeXhr);
    vi.stubGlobal("fetch", (input: RequestInfo | URL) => {
      const url = String(input);
      if (url.includes("/api/settings/params/schema")) {
        return Promise.resolve(
          jsonResponse({
            groups: [{ id: "common", label: "常用" }],
            items: [
              {
                key: "lang_out",
                group: "common",
                label: "目标语言",
                type: "str",
                default: "zh",
              },
            ],
            defaults: { lang_out: "zh" },
          }),
        );
      }
      if (url.includes("/api/settings/api-profiles")) {
        return Promise.resolve(
          jsonResponse({
            items: [
              {
                id: 1,
                name: "deepseek",
                base_url: "https://api.deepseek.com/v1",
                model: "deepseek-flash",
                is_default: true,
                has_key: true,
              },
            ],
          }),
        );
      }
      return Promise.resolve(jsonResponse({ items: [] }));
    });
  });

  afterEach(() => {
    cleanup();
    delete (window as unknown as { __WORKBENCH_TOKEN__?: string }).__WORKBENCH_TOKEN__;
    vi.unstubAllGlobals();
  });

  it("选中 PDF 后上传请求必须包含文件字段", async () => {
    const user = userEvent.setup();
    renderPanel();

    const input = document.querySelector<HTMLInputElement>('input[type="file"]');
    expect(input).not.toBeNull();

    const file = new File(["%PDF-1.4 test"], "论文.pdf", { type: "application/pdf" });
    fireEvent.change(input as HTMLInputElement, { target: { files: [file] } });

    const button = await waitFor(importButton);
    await waitFor(() => expect(button.disabled).toBe(false));
    await user.click(button);

    await waitFor(() => expect(FakeXhr.instances.length).toBe(1));
    const body = FakeXhr.instances[0].body as FormData;
    const uploaded = body.getAll("files");
    expect(uploaded.length).toBe(1);
    expect(uploaded[0]).toBeInstanceOf(File);
    expect((uploaded[0] as File).name).toBe("论文.pdf");
  });

  it("没有选中文件时按钮不可点击，避免发出空上传", async () => {
    renderPanel();
    const button = await waitFor(importButton);
    expect(button.disabled).toBe(true);
  });
});
