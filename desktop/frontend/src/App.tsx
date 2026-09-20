import { Alert, Button, Layout, Space, Tabs, Tag, Typography } from "antd";
import { useState } from "react";

import SettingsDrawer from "./components/SettingsDrawer";
import GlossaryPanel from "./components/GlossaryPanel";
import TasksPanel from "./components/TasksPanel";
import WorkbenchPanel from "./components/WorkbenchPanel";

const { Header, Content } = Layout;

export default function App() {
  const [settingsOpen, setSettingsOpen] = useState(false);
  const [activeTab, setActiveTab] = useState("workbench");

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
          <Button href="/selfcheck" target="_blank">
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
    </Layout>
  );
}
