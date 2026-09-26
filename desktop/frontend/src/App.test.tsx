import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { App as AntApp, ConfigProvider } from "antd";
import zhCN from "antd/locale/zh_CN";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import App from "./App";

function jsonResponse(payload: unknown, status = 200): Response {
  return new Response(JSON.stringify(payload), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

function renderApp() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <ConfigProvider locale={zhCN}>
      <AntApp>
        <QueryClientProvider client={client}>
          <App />
        </QueryClientProvider>
      </AntApp>
    </ConfigProvider>,
  );
}

describe("工作台界面骨架", () => {
  beforeEach(() => {
    // 所有接口返回空数据，只验证界面能正常渲染出主要区块（防止白屏回归）
    vi.stubGlobal(
      "fetch",
      vi.fn((input: RequestInfo | URL) => {
        const url = String(input);
        if (url.includes("/api/selfcheck")) {
          return Promise.resolve(jsonResponse({
            overall: "ok",
            checks: [{ key: "runtime", label: "运行时", status: "ok", detail: "可用" }],
          }));
        }
        if (url.includes("/api/settings/params/schema")) {
          return Promise.resolve(
            jsonResponse({
              groups: [
                { id: "common", label: "常用" },
                { id: "advanced", label: "高级" },
              ],
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
        if (url.includes("/api/tasks")) {
          return Promise.resolve(jsonResponse({ items: [] }));
        }
        if (url.includes("/api/glossaries")) {
          return Promise.resolve(jsonResponse({ items: [] }));
        }
        return Promise.resolve(jsonResponse({ items: [] }));
      }),
    );
  });

  afterEach(() => {
    cleanup();
    vi.unstubAllGlobals();
    delete (window as unknown as { __WORKBENCH_TOKEN__?: string }).__WORKBENCH_TOKEN__;
  });

  it("渲染标题、标签页与设置入口", async () => {
    renderApp();
    await waitFor(() => {
      expect(screen.getAllByText("BabelDOC 工作台").length).toBeGreaterThan(0);
    });
    expect(screen.getAllByText("本地运行").length).toBeGreaterThan(0);
    expect(screen.getAllByText("翻译工作台").length).toBeGreaterThan(0);
    expect(screen.getAllByText("任务与文件").length).toBeGreaterThan(0);
    expect(screen.getAllByText("术语表").length).toBeGreaterThan(0);
    expect(screen.getAllByRole("button", { name: /设置/ }).length).toBeGreaterThan(0);
    expect(screen.queryByText(/所有文件与任务都保存在本机/)).toBeNull();
  });

  it("没有 API 配置时提示先去设置，而不是直接提交", async () => {
    renderApp();
    await waitFor(() => {
      expect(screen.getAllByText("还没有 API 配置").length).toBeGreaterThan(0);
    });
    expect(screen.getAllByRole("button", { name: /去设置/ }).length).toBeGreaterThan(0);
  });

  it("在当前窗口请求环境自检并携带会话令牌", async () => {
    (window as unknown as { __WORKBENCH_TOKEN__?: string }).__WORKBENCH_TOKEN__ = "test-token";
    renderApp();
    fireEvent.click(screen.getByRole("button", { name: "环境自检" }));

    await waitFor(() => {
      expect(screen.getByText("总体结论：通过")).toBeTruthy();
    });
    expect(screen.getByText("运行时")).toBeTruthy();
    const requests = vi.mocked(fetch).mock.calls;
    const selfCheckRequest = requests.find(([path]) => String(path) === "/api/selfcheck");
    expect(selfCheckRequest).toBeDefined();
    expect(selfCheckRequest?.[1]?.headers).toMatchObject({ "X-Workbench-Token": "test-token" });
  });
});
