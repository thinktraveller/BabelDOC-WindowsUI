import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { App as AntApp, ConfigProvider } from "antd";
import zhCN from "antd/locale/zh_CN";
import { cleanup, fireEvent, render, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import WorkbenchPanel, { withOutputMode } from "./WorkbenchPanel";
import { useWorkbench } from "../store";

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
                key: "lang_in",
                group: "common",
                label: "源语言",
                type: "choice",
                default: "en",
                options: [
                  { value: "en", label: "英语 (en)" },
                  { value: "zh-cn", label: "简体中文 (zh-cn)" },
                ],
              },
              {
                key: "lang_out",
                group: "common",
                label: "目标语言",
                type: "choice",
                default: "zh",
                options: [
                  { value: "en", label: "英语 (en)" },
                  { value: "zh", label: "中文 (zh)" },
                  { value: "zh-cn", label: "简体中文 (zh-cn)" },
                ],
              },
              {
                key: "pages",
                group: "common",
                label: "页码范围",
                type: "pages",
                default: null,
              },
              {
                key: "output_mode",
                group: "common",
                label: "输出类型",
                type: "choice",
                default: "both",
                options: [
                  { value: "both", label: "同时输出单语与双语" },
                  { value: "mono", label: "仅输出单语" },
                  { value: "dual", label: "仅输出双语" },
                ],
              },
              {
                key: "dual_original_position",
                group: "advanced",
                label: "双语原文位置",
                type: "choice",
                default: "left",
                options: [
                  { value: "left", label: "左侧（交替页时在前）" },
                  { value: "right", label: "右侧（交替页时在后）" },
                ],
              },
              {
                key: "use_alternating_pages_dual",
                group: "advanced",
                label: "双语排列为交替页",
                type: "bool",
                default: false,
              },
              {
                key: "auto_extract_glossary",
                group: "advanced",
                label: "自动提取术语",
                type: "bool",
                default: true,
              },
              {
                key: "glossary_version_id",
                group: "advanced",
                label: "使用术语版本",
                type: "glossary",
                default: null,
              },
              {
                key: "watermark_output_mode",
                group: "advanced",
                label: "水印输出模式",
                type: "choice",
                default: "watermarked",
                options: [
                  { value: "watermarked", label: "添加水印" },
                  { value: "no_watermark", label: "不添加水印" },
                  { value: "both", label: "同时输出两种版本" },
                ],
              },
              {
                key: "primary_font_family",
                group: "advanced",
                label: "译文字体风格",
                type: "choice",
                default: "auto",
                options: [
                  { value: "auto", label: "自动选择" },
                  { value: "serif", label: "衬线字体" },
                ],
              },
              {
                key: "only_include_translated_page",
                group: "advanced",
                label: "仅保留所选翻译页",
                type: "bool",
                default: false,
              },
              {
                key: "min_text_length",
                group: "advanced",
                label: "最短翻译文本长度",
                type: "int",
                default: 5,
              },
              {
                key: "disable_rich_text_translate",
                group: "advanced",
                label: "关闭富文本翻译",
                type: "bool",
                default: false,
              },
            ],
            defaults: {
              lang_in: "en",
              lang_out: "zh",
              pages: null,
              output_mode: "both",
              dual_original_position: "left",
              use_alternating_pages_dual: false,
              auto_extract_glossary: true,
              glossary_version_id: null,
              watermark_output_mode: "watermarked",
              primary_font_family: "auto",
              only_include_translated_page: false,
              min_text_length: 5,
              disable_rich_text_translate: false,
            },
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
    useWorkbench.getState().setParams({});
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

  it("页码和输出类型并列，双语原文位置位于高级参数", async () => {
    const user = userEvent.setup();
    const { container, findByText, queryByText } = renderPanel();
    expect(await findByText("输出类型")).toBeTruthy();
    const commonGrid = Array.from(container.querySelectorAll<HTMLElement>(".panel-grid")).find(
      (element) => element.style.gridTemplateColumns === "1fr 1fr",
    );
    expect(commonGrid).toBeTruthy();
    const cells = Array.from(commonGrid?.children ?? []).map((element) => element.textContent ?? "");
    expect(cells[0]).toContain("源语言");
    expect(cells[1]).toContain("目标语言");
    expect(cells[2]).toContain("页码范围");
    expect(cells[3]).toContain("输出类型");
    expect(queryByText("不输出单语")).toBeNull();
    expect(queryByText("不输出双语")).toBeNull();
    expect(queryByText("双语原文位置")).toBeNull();
    expect(queryByText("双语排列为交替页")).toBeNull();
    expect(queryByText("自动提取术语")).toBeNull();
    await user.click(await findByText("高级参数"));
    const alternatingLabel = await findByText("双语排列为交替页");
    expect(alternatingLabel).toBeTruthy();
    expect(await findByText("双语原文位置")).toBeTruthy();
    expect(await findByText("自动提取术语")).toBeTruthy();
    expect(await findByText("使用术语版本")).toBeTruthy();
    expect(await findByText("译文字体风格")).toBeTruthy();
    expect(await findByText("仅保留所选翻译页")).toBeTruthy();
    expect(await findByText("最短翻译文本长度")).toBeTruthy();
    expect(await findByText("关闭富文本翻译")).toBeTruthy();
    const alternatingSwitch = alternatingLabel.closest(".ant-form-item")?.querySelector<HTMLElement>("[role='switch']");
    expect(alternatingSwitch).toBeTruthy();
    await user.click(alternatingSwitch as HTMLElement);
    expect(await findByText("双语原文顺序")).toBeTruthy();
  });

  it("语言和水印使用下拉，已有其它合法语言代码仍可回显", async () => {
    const user = userEvent.setup();
    useWorkbench.getState().setParams({
      lang_in: "la",
      lang_out: "zh_cn",
      watermark_output_mode: "both",
    });
    const { container, findByText } = renderPanel();
    await findByText("源语言");
    expect(container.querySelector("#lang_in")?.getAttribute("role")).toBe("combobox");
    expect(container.querySelector("#lang_out")?.getAttribute("role")).toBe("combobox");

    expect(await findByText("已有语言代码 (la)")).toBeTruthy();
    expect(await findByText("已有语言代码 (zh_cn)")).toBeTruthy();

    await user.click(await findByText("高级参数"));
    expect(container.querySelector("#watermark_output_mode")?.getAttribute("role")).toBe(
      "combobox",
    );
    expect(await findByText("同时输出两种版本")).toBeTruthy();
  });

  it("旧输出开关组合转换为新下拉；两种都关闭时提示重选", () => {
    expect(withOutputMode({ no_mono: false, no_dual: false }).output_mode).toBe("both");
    expect(withOutputMode({ no_mono: false, no_dual: true }).output_mode).toBe("mono");
    expect(withOutputMode({ no_mono: true, no_dual: false }).output_mode).toBe("dual");
    expect(withOutputMode({ no_mono: true, no_dual: true }).output_mode).toBe(
      "legacy-invalid",
    );
    expect(withOutputMode({ no_mono: true, no_dual: true })).not.toHaveProperty("no_mono");
    expect(withOutputMode({ no_mono: true, no_dual: true })).not.toHaveProperty("no_dual");
    expect(withOutputMode({ output_mode: "both", no_mono: true, no_dual: true })).toEqual({
      output_mode: "both",
    });
    expect(withOutputMode({ output_mode: "dual" }).output_mode).toBe("dual");
  });
});
