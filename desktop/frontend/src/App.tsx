import { Alert, App as AntApp, Button, Layout, Modal, Space, Tabs, Tag, Typography } from "antd";
import { useEffect, useState } from "react";

import { api, type SelfCheckResult } from "./api";
import SettingsDrawer from "./components/SettingsDrawer";
import GlossaryPanel from "./components/GlossaryPanel";
import TasksPanel from "./components/TasksPanel";
import WorkbenchPanel from "./components/WorkbenchPanel";

const { Header, Content } = Layout;

export default function App() {
  const { message } = AntApp.useApp();
  const [settingsOpen, setSettingsOpen] = useState(false);
  const [activeTab, setActiveTab] = useState("workbench");
  const [closingOpen, setClosingOpen] = useState(false);
  const [closingBusy, setClosingBusy] = useState<string | null>(null);
  const [selfCheckOpen, setSelfCheckOpen] = useState(false);
  const [selfCheckLoading, setSelfCheckLoading] = useState(false);
  const [selfCheckResult, setSelfCheckResult] = useState<SelfCheckResult | null>(null);
  const [selfCheckError, setSelfCheckError] = useState("");

  const openSelfCheck = async () => {
    setSelfCheckOpen(true);
    setSelfCheckLoading(true);
    setSelfCheckError("");
    try {
      setSelfCheckResult(await api.selfCheck());
    } catch (error) {
      setSelfCheckError(error instanceof Error ? error.message : String(error));
    } finally {
      setSelfCheckLoading(false);
    }
  };

  useEffect(() => {
    (window as unknown as { __WORKBENCH_ON_CLOSE__?: () => void }).__WORKBENCH_ON_CLOSE__ =
      () => setClosingOpen(true);
    return () => {
      delete (window as unknown as { __WORKBENCH_ON_CLOSE__?: () => void })
        .__WORKBENCH_ON_CLOSE__;
    };
  }, []);

  const sleep = (ms: number) => new Promise((resolve) => setTimeout(resolve, ms));

  const finishClose = () => {
    const bridge = (
      window as unknown as { pywebview?: { api?: { finish_close?: () => void } } }
    ).pywebview?.api;
    if (bridge?.finish_close) {
      bridge.finish_close();
      return;
    }
    setClosingOpen(false);
    setClosingBusy(null);
    message.info("浏览器模式下请手动关闭标签页。");
  };

  const waitForIdle = async () => {
    for (let attempt = 0; attempt < 3600; attempt += 1) {
      const status = await api.appStatus();
      if (status.active_count === 0) {
        return;
      }
      await sleep(2000);
    }
  };

  const handleWaitAndClose = async () => {
    setClosingBusy("wait");
    await waitForIdle();
    finishClose();
  };

  const handleCancelAndClose = async () => {
    setClosingBusy("cancel");
    const status = await api.appStatus();
    for (const task of status.active_tasks) {
      try {
        await api.cancelTask(task.id);
      } catch {
        /* 任务可能刚好结束 */
      }
    }
    await waitForIdle();
    finishClose();
  };

  const handleForceClose = async () => {
    setClosingBusy("force");
    await api.forceInterrupt();
    finishClose();
  };

  return (
    <Layout className="app-layout">
      <Header className="app-header">
        <Space>
          <Typography.Title level={4} className="app-title">
            BabelDOC 工作台
          </Typography.Title>
          <Tag color="blue">本地运行</Tag>
        </Space>
        <Space>
          <Button onClick={openSelfCheck}>
            环境自检
          </Button>
          <Button type="primary" onClick={() => setSettingsOpen(true)}>
            设置
          </Button>
        </Space>
      </Header>
      <Content className="app-content">
        <Alert
          type="info"
          showIcon
          className="app-hint"
          message="所有文件与任务都保存在本机；取消任务表示停止本次翻译，不是暂停续译。"
        />
        <Tabs
          activeKey={activeTab}
          onChange={setActiveTab}
          items={[
            {
              key: "workbench",
              label: "翻译工作台",
              children: <WorkbenchPanel onOpenSettings={() => setSettingsOpen(true)} />,
            },
            { key: "tasks", label: "任务与文件", children: <TasksPanel /> },
            { key: "glossary", label: "术语表", children: <GlossaryPanel /> },
          ]}
        />
      </Content>
      <SettingsDrawer open={settingsOpen} onClose={() => setSettingsOpen(false)} />
      <Modal
        open={selfCheckOpen}
        title="环境自检"
        onCancel={() => setSelfCheckOpen(false)}
        footer={<Button onClick={() => setSelfCheckOpen(false)}>关闭</Button>}
        width={760}
      >
        {selfCheckLoading && <p>正在检查…</p>}
        {selfCheckError && <Alert type="error" message={`读取自检结果失败：${selfCheckError}`} />}
        {!selfCheckLoading && selfCheckResult && !selfCheckError && (
          <>
            <p>总体结论：{selfCheckResult.overall === "ok" ? "通过" : selfCheckResult.overall === "warn" ? "注意" : "失败"}</p>
            <table>
              <thead><tr><th>检查项</th><th>状态</th><th>详情</th></tr></thead>
              <tbody>
                {selfCheckResult.checks.map((item) => (
                  <tr key={item.key}>
                    <td>{item.label}</td>
                    <td>{item.status === "ok" ? "通过" : item.status === "warn" ? "注意" : "失败"}</td>
                    <td>{item.detail}{item.hint && <div>{item.hint}</div>}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </>
        )}
      </Modal>
      <Modal
        open={closingOpen}
        title="还有任务正在运行"
        closable={false}
        maskClosable={false}
        footer={[
          <Button key="wait" loading={closingBusy === "wait"} onClick={handleWaitAndClose}>
            等待完成
          </Button>,
          <Button
            key="cancel"
            loading={closingBusy === "cancel"}
            onClick={handleCancelAndClose}
          >
            取消任务并退出
          </Button>,
          <Button
            key="force"
            danger
            loading={closingBusy === "force"}
            onClick={handleForceClose}
          >
            结束并标记为已中断
          </Button>,
        ]}
      >
        <p>请选择退出方式；选择“结束并标记为已中断”后，任务不会保留断点，需要重新执行。</p>
        <p className="hint-text">
          取消表示停止当前任务，不是暂停；中断后的任务可在任务列表中重新执行。
        </p>
      </Modal>
    </Layout>
  );
}
