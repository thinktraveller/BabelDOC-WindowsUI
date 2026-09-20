import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  Alert,
  App as AntApp,
  Button,
  Drawer,
  Form,
  Input,
  List,
  Popconfirm,
  Select,
  Space,
  Table,
  Tag,
  Typography,
} from "antd";
import type { ColumnsType } from "antd/es/table";
import { useEffect, useState } from "react";

import { api } from "../api";
import type { ApiProfile } from "../types";
import { describeError } from "./WorkbenchPanel";

interface Props {
  open: boolean;
  onClose: () => void;
}

interface ProfileFormValues {
  name: string;
  base_url: string;
  model: string;
  api_key?: string;
  reasoning?: string;
  thinking?: string;
  make_default?: boolean;
}

export default function SettingsDrawer({ open, onClose }: Props) {
  const { message } = AntApp.useApp();
  const queryClient = useQueryClient();
  const [form] = Form.useForm<ProfileFormValues>();
  const [editingId, setEditingId] = useState<number | null>(null);
  const [testResult, setTestResult] = useState<string | null>(null);

  const profilesQuery = useQuery({
    queryKey: ["profiles"],
    queryFn: api.listProfiles,
    enabled: open,
  });
  const presetsQuery = useQuery({
    queryKey: ["presets"],
    queryFn: api.listPresets,
    enabled: open,
  });

  useEffect(() => {
    if (!open) {
      return;
    }
    if (editingId === null) {
      form.resetFields();
      return;
    }
    const profile = profilesQuery.data?.items.find((item) => item.id === editingId);
    if (profile) {
      form.setFieldsValue({
        name: profile.name,
        base_url: profile.base_url,
        model: profile.model,
        reasoning: profile.reasoning ?? undefined,
        thinking: profile.thinking ?? undefined,
        api_key: undefined,
        make_default: profile.is_default,
      });
    }
  }, [editingId, form, open, profilesQuery.data]);

  const saveMutation = useMutation({
    mutationFn: async (values: ProfileFormValues) => {
      if (editingId === null) {
        return api.createProfile({ ...values });
      }
      return api.updateProfile(editingId, { ...values });
    },
    onSuccess: () => {
      message.success(editingId === null ? "已添加 API 配置" : "已更新 API 配置");
      setEditingId(null);
      form.resetFields();
      queryClient.invalidateQueries({ queryKey: ["profiles"] });
    },
    onError: (error) => message.error(describeError(error)),
  });

  const deleteMutation = useMutation({
    mutationFn: api.deleteProfile,
    onSuccess: () => {
      message.success("已删除配置及其凭据");
      queryClient.invalidateQueries({ queryKey: ["profiles"] });
    },
    onError: (error) => message.error(describeError(error)),
  });

  const defaultMutation = useMutation({
    mutationFn: api.setDefaultProfile,
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ["profiles"] }),
    onError: (error) => message.error(describeError(error)),
  });

  const testMutation = useMutation({
    mutationFn: api.testProfile,
    onSuccess: (result) => {
      setTestResult(result.message);
      if (result.ok) {
        message.success(result.message);
      } else {
        message.warning(result.message);
      }
    },
    onError: (error) => message.error(describeError(error)),
  });

  const deletePresetMutation = useMutation({
    mutationFn: api.deletePreset,
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ["presets"] }),
    onError: (error) => message.error(describeError(error)),
  });

  const columns: ColumnsType<ApiProfile> = [
    {
      title: "名称",
      dataIndex: "name",
      render: (value: string, record) => (
        <Space size="small">
          {value}
          {record.is_default && <Tag color="blue">默认</Tag>}
          {!record.has_key && <Tag color="orange">未保存 Key</Tag>}
        </Space>
      ),
    },
    { title: "模型", dataIndex: "model", ellipsis: true },
    { title: "Base URL", dataIndex: "base_url", ellipsis: true },
    {
      title: "操作",
      width: 240,
      render: (_value, record) => (
        <Space size="small">
          <Button type="link" size="small" onClick={() => setEditingId(record.id)}>
            编辑
          </Button>
          <Button
            type="link"
            size="small"
            loading={testMutation.isPending}
            onClick={() => testMutation.mutate(record.id)}
          >
            连接测试
          </Button>
          {!record.is_default && (
            <Button
              type="link"
              size="small"
              onClick={() => defaultMutation.mutate(record.id)}
            >
              设为默认
            </Button>
          )}
          <Popconfirm
            title="删除该配置？"
            description="会同时删除系统凭据存储中的 API Key。"
            okText="删除"
            cancelText="取消"
            onConfirm={() => deleteMutation.mutate(record.id)}
          >
            <Button type="link" size="small" danger>
              删除
            </Button>
          </Popconfirm>
        </Space>
      ),
    },
  ];

  return (
    <Drawer
      width={680}
      title="设置"
      open={open}
      onClose={onClose}
      destroyOnClose={false}
    >
      {profilesQuery.data && !profilesQuery.data.credentials_available && (
        <Alert
          type="warning"
          showIcon
          style={{ marginBottom: 12 }}
          message="系统凭据存储不可用"
          description="当前无法保存 API Key。请检查 Windows 凭据管理器后重试；应用不会把 Key 明文保存到文件。"
        />
      )}
      <Typography.Title level={5}>API 配置</Typography.Title>
      <Table
        rowKey="id"
        size="small"
        pagination={false}
        columns={columns}
        dataSource={profilesQuery.data?.items ?? []}
      />
      <Form
        form={form}
        layout="vertical"
        style={{ marginTop: 16 }}
        onFinish={(values) => saveMutation.mutate(values)}
      >
        <Form.Item name="name" label="配置名称" rules={[{ required: true, message: "请填写名称" }]}>
          <Input placeholder="例如 主服务" />
        </Form.Item>
        <Form.Item
          name="base_url"
          label="Base URL"
          rules={[{ required: true, message: "请填写 Base URL" }]}
        >
          <Input placeholder="https://api.example.com/v1" />
        </Form.Item>
        <Form.Item name="model" label="模型名" rules={[{ required: true, message: "请填写模型名" }]}>
          <Input placeholder="例如 gpt-4o-mini" />
        </Form.Item>
        <Form.Item
          name="api_key"
          label={editingId === null ? "API Key" : "API Key（留空表示不修改）"}
          rules={editingId === null ? [{ required: true, message: "请填写 API Key" }] : []}
        >
          <Input.Password placeholder="只保存到系统凭据存储，不写入数据库" autoComplete="new-password" />
        </Form.Item>
        <Space>
          <Form.Item name="reasoning" label="推理强度（可选）" style={{ marginBottom: 0 }}>
            <Select
              allowClear
              style={{ width: 180 }}
              options={["minimal", "low", "medium", "high"].map((value) => ({
                value,
                label: value,
              }))}
            />
          </Form.Item>
          <Form.Item name="thinking" label="思考模式（可选）" style={{ marginBottom: 0 }}>
            <Select
              allowClear
              style={{ width: 180 }}
              options={[
                { value: "enabled", label: "enabled" },
                { value: "disabled", label: "disabled" },
              ]}
            />
          </Form.Item>
          <Form.Item name="make_default" label="设为默认" valuePropName="checked">
            <Input type="checkbox" style={{ width: 20, height: 20 }} />
          </Form.Item>
        </Space>
        <Space style={{ marginTop: 12 }}>
          <Button type="primary" htmlType="submit" loading={saveMutation.isPending}>
            {editingId === null ? "添加配置" : "保存修改"}
          </Button>
          {editingId !== null && (
            <Button
              onClick={() => {
                setEditingId(null);
                form.resetFields();
              }}
            >
              取消编辑
            </Button>
          )}
        </Space>
      </Form>
      {testResult && (
        <Alert
          style={{ marginTop: 12 }}
          type="info"
          showIcon
          message={`最近一次连接测试：${testResult}`}
        />
      )}

      <Typography.Title level={5} style={{ marginTop: 24 }}>
        参数预设
      </Typography.Title>
      <List
        size="small"
        bordered
        locale={{ emptyText: "还没有保存的预设" }}
        dataSource={presetsQuery.data?.items ?? []}
        renderItem={(preset) => (
          <List.Item
            actions={[
              <Button
                key="delete"
                type="link"
                danger
                onClick={() => deletePresetMutation.mutate(preset.id)}
              >
                删除
              </Button>,
            ]}
          >
            {preset.name}
          </List.Item>
        )}
      />
    </Drawer>
  );
}
