import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  App as AntApp,
  Button,
  Card,
  Empty,
  Form,
  Input,
  InputNumber,
  List,
  Popconfirm,
  Select,
  Space,
  Table,
  Tag,
  Typography,
  Upload,
} from "antd";
import type { ColumnsType } from "antd/es/table";
import { useState } from "react";

import { api, sessionToken, TOKEN_HEADER } from "../api";
import type { GlossaryEntryInfo, GlossaryInfo, GlossaryVersionInfo } from "../types";
import { describeError } from "./WorkbenchPanel";

const STATUS_LABELS: Record<string, string> = {
  new: "待审核",
  edited: "已修改",
  approved: "已审核",
  conflict: "冲突",
};

const STATUS_COLORS: Record<string, string> = {
  new: "default",
  edited: "blue",
  approved: "success",
  conflict: "error",
};

export default function GlossaryPanel() {
  const { message } = AntApp.useApp();
  const queryClient = useQueryClient();
  const [selectedId, setSelectedId] = useState<number | null>(null);
  const [statusFilter, setStatusFilter] = useState<string>("");
  const [languageFilter, setLanguageFilter] = useState<string>("");
  const [search, setSearch] = useState("");
  const [importForm] = Form.useForm<{ task_id: number; name?: string }>();
  const [csvForm] = Form.useForm<{ name: string; tgt_lng: string; file?: File }>();

  const glossariesQuery = useQuery({ queryKey: ["glossaries"], queryFn: api.listGlossaries });
  const glossaryId = selectedId ?? glossariesQuery.data?.items[0]?.id ?? null;

  const entriesQuery = useQuery({
    queryKey: ["glossary-entries", glossaryId, statusFilter, languageFilter, search],
    queryFn: () =>
      api.listGlossaryEntries(glossaryId as number, {
        status: statusFilter,
        tgt_lng: languageFilter,
        q: search,
        limit: 500,
      }),
    enabled: glossaryId !== null,
  });
  const versionsQuery = useQuery({
    queryKey: ["glossary-versions", glossaryId],
    queryFn: () => api.listGlossaryVersions(glossaryId as number),
    enabled: glossaryId !== null,
  });

  const refresh = () => {
    queryClient.invalidateQueries({ queryKey: ["glossaries"] });
    queryClient.invalidateQueries({ queryKey: ["glossary-entries"] });
    queryClient.invalidateQueries({ queryKey: ["glossary-versions"] });
  };

  const importTaskMutation = useMutation({
    mutationFn: (values: { task_id: number; name?: string }) =>
      api.importGlossaryFromTask(values.task_id, values.name),
    onSuccess: (glossary) => {
      message.success(`已导入 ${glossary.entry_count} 条术语，等待审核`);
      setSelectedId(glossary.id);
      importForm.resetFields();
      refresh();
    },
    onError: (error) => message.error(describeError(error)),
  });

  const importCsvMutation = useMutation({
    mutationFn: (values: { name: string; tgt_lng: string; file: File }) =>
      api.importGlossaryCsv(values.file, values.name, values.tgt_lng),
    onSuccess: (glossary) => {
      message.success(`已导入 ${glossary.entry_count} 条术语`);
      setSelectedId(glossary.id);
      csvForm.resetFields();
      refresh();
    },
    onError: (error) => message.error(describeError(error)),
  });

  const updateMutation = useMutation({
    mutationFn: ({ id, patch }: { id: number; patch: Record<string, unknown> }) =>
      api.updateGlossaryEntry(id, patch),
    onSuccess: () => {
      message.success("已保存条目");
      refresh();
    },
    onError: (error) => message.error(describeError(error)),
  });

  const deleteEntryMutation = useMutation({
    mutationFn: api.deleteGlossaryEntry,
    onSuccess: () => refresh(),
    onError: (error) => message.error(describeError(error)),
  });

  const bulkApproveMutation = useMutation({
    mutationFn: (ids: number[]) =>
      api.bulkGlossaryStatus(glossaryId as number, ids, "approved"),
    onSuccess: (result) => {
      message.success(`已将 ${result.updated} 条标记为已审核`);
      refresh();
    },
    onError: (error) => message.error(describeError(error)),
  });

  const resolveMutation = useMutation({
    mutationFn: ({ entryId, keep }: { entryId: number; keep: "existing" | "incoming" }) =>
      api.resolveGlossaryConflict(glossaryId as number, entryId, keep),
    onSuccess: () => {
      message.success("冲突已解决");
      refresh();
    },
    onError: (error) => message.error(describeError(error)),
  });

  const versionMutation = useMutation({
    mutationFn: (note?: string) => api.createGlossaryVersion(glossaryId as number, note),
    onSuccess: (version) => {
      message.success(`已生成版本 v${version.version}（${version.entry_count} 条）`);
      refresh();
    },
    onError: (error) => message.error(describeError(error)),
  });

  const columns: ColumnsType<GlossaryEntryInfo> = [
    {
      title: "源词",
      dataIndex: "source",
      render: (value: string, record) => (
        <Typography.Text
          editable={{
            onChange: (text) => {
              if (text && text !== value) {
                updateMutation.mutate({ id: record.id, patch: { source: text } });
              }
            },
          }}
        >
          {value}
        </Typography.Text>
      ),
    },
    {
      title: "译词",
      dataIndex: "target",
      render: (value: string, record) => (
        <Space direction="vertical" size={2}>
          <Typography.Text
            editable={{
              onChange: (text) => {
                if (text && text !== value) {
                  updateMutation.mutate({ id: record.id, patch: { target: text } });
                }
              },
            }}
          >
            {value}
          </Typography.Text>
          {record.conflict_target && (
            <span className="hint-text">新候选：{record.conflict_target}</span>
          )}
        </Space>
      ),
    },
    { title: "目标语言", dataIndex: "tgt_lng", width: 100 },
    {
      title: "状态",
      dataIndex: "status",
      width: 110,
      render: (status: string) => (
        <Tag color={STATUS_COLORS[status]}>{STATUS_LABELS[status] ?? status}</Tag>
      ),
    },
    {
      title: "操作",
      width: 260,
      render: (_value, record) => (
        <Space size="small" wrap>
          {record.status === "conflict" ? (
            <>
              <Button
                size="small"
                type="primary"
                onClick={() => resolveMutation.mutate({ entryId: record.id, keep: "existing" })}
              >
                保留当前
              </Button>
              <Button
                size="small"
                onClick={() => resolveMutation.mutate({ entryId: record.id, keep: "incoming" })}
              >
                采用新值
              </Button>
            </>
          ) : (
            <Button
              size="small"
              onClick={() => bulkApproveMutation.mutate([record.id])}
              disabled={record.status === "approved"}
            >
              标为已审核
            </Button>
          )}
          <Popconfirm
            title="删除该条目？"
            okText="删除"
            cancelText="取消"
            onConfirm={() => deleteEntryMutation.mutate(record.id)}
          >
            <Button size="small" danger type="link">
              删除
            </Button>
          </Popconfirm>
        </Space>
      ),
    },
  ];

  const selected = glossariesQuery.data?.items.find((item) => item.id === glossaryId);
  const entries = entriesQuery.data?.items ?? [];

  return (
    <div className="panel-grid">
      <Card title="术语表" size="small">
        <List
          size="small"
          bordered
          locale={{ emptyText: "还没有术语表" }}
          dataSource={glossariesQuery.data?.items ?? []}
          renderItem={(item: GlossaryInfo) => (
            <List.Item
              style={{
                background: item.id === glossaryId ? "rgba(22,119,255,0.06)" : undefined,
                cursor: "pointer",
              }}
              onClick={() => setSelectedId(item.id)}
            >
              <Space direction="vertical" size={0}>
                <span>
                  {item.name}
                  {item.source_task_id && (
                    <span className="hint-text"> · 来自任务 #{item.source_task_id}</span>
                  )}
                </span>
                <span className="hint-text">
                  {item.entry_count} 条 · 版本 {item.version_count} · 目标语言 {item.tgt_lng}
                  {item.conflict_count > 0 && ` · 冲突 ${item.conflict_count}`}
                </span>
              </Space>
            </List.Item>
          )}
        />
        <Typography.Title level={5} style={{ marginTop: 16 }}>
          从任务导入
        </Typography.Title>
        <Form
          form={importForm}
          layout="inline"
          onFinish={(values) => importTaskMutation.mutate(values)}
        >
          <Form.Item
            name="task_id"
            rules={[{ required: true, message: "请填写任务号" }]}
          >
            <InputNumber min={1} placeholder="任务号" style={{ width: 110 }} />
          </Form.Item>
          <Form.Item name="name">
            <Input placeholder="术语表名称（可留空）" style={{ width: 180 }} />
          </Form.Item>
          <Form.Item>
            <Button htmlType="submit" loading={importTaskMutation.isPending}>
              导入任务术语
            </Button>
          </Form.Item>
        </Form>

        <Typography.Title level={5} style={{ marginTop: 16 }}>
          导入 CSV
        </Typography.Title>
        <Form form={csvForm} layout="inline" onFinish={(values) => {
          const file = values.file;
          if (file) {
            importCsvMutation.mutate({ ...values, file });
          }
        }}>
          <Form.Item name="name" rules={[{ required: true, message: "请填写名称" }]}>
            <Input placeholder="术语表名称" style={{ width: 160 }} />
          </Form.Item>
          <Form.Item
            name="tgt_lng"
            initialValue="zh"
            rules={[{ required: true, message: "请填写目标语言" }]}
          >
            <Input placeholder="目标语言" style={{ width: 110 }} />
          </Form.Item>
          <Form.Item name="file" valuePropName="file">
            <Upload
              maxCount={1}
              beforeUpload={(file) => {
                csvForm.setFieldValue("file", file);
                return false;
              }}
              onRemove={() => csvForm.setFieldValue("file", undefined)}
            >
              <Button>选择 CSV</Button>
            </Upload>
          </Form.Item>
          <Form.Item>
            <Button htmlType="submit" loading={importCsvMutation.isPending}>
              导入
            </Button>
          </Form.Item>
        </Form>
        <span className="hint-text">CSV 需要 source、target 两列（可选 tgt_lng）。</span>
      </Card>

      <Card
        size="small"
        title={
          selected ? `条目 · ${selected.name}（目标语言 ${selected.tgt_lng}）` : "条目"
        }
        extra={
          glossaryId && (
            <Space>
              <Button
                size="small"
                loading={versionMutation.isPending}
                onClick={() => versionMutation.mutate(undefined)}
              >
                生成版本
              </Button>
              <Button
                size="small"
                onClick={async () => {
                  const response = await fetch(api.glossaryExportUrl(glossaryId), {
                    headers: { [TOKEN_HEADER]: sessionToken() },
                  });
                  if (!response.ok) {
                    message.error("导出失败");
                    return;
                  }
                  const blob = await response.blob();
                  const url = URL.createObjectURL(blob);
                  const anchor = document.createElement("a");
                  anchor.href = url;
                  anchor.download = `${selected?.name ?? "glossary"}.glossary.csv`;
                  anchor.click();
                  URL.revokeObjectURL(url);
                }}
              >
                导出 CSV
              </Button>
            </Space>
          )
        }
      >
        <Space wrap style={{ marginBottom: 8 }}>
          <Select
            style={{ width: 130 }}
            placeholder="状态"
            allowClear
            value={statusFilter || undefined}
            onChange={(value) => setStatusFilter(value ?? "")}
            options={Object.entries(STATUS_LABELS).map(([value, label]) => ({ value, label }))}
          />
          <Input
            style={{ width: 150 }}
            placeholder="目标语言"
            value={languageFilter}
            onChange={(event) => setLanguageFilter(event.target.value)}
          />
          <Input.Search
            style={{ width: 220 }}
            placeholder="搜索源词或译词"
            onSearch={(value) => setSearch(value)}
          />
          <Button
            onClick={() => bulkApproveMutation.mutate(entries.map((entry) => entry.id))}
            disabled={entries.length === 0}
          >
            当前列表全部标为已审核
          </Button>
        </Space>
        <Table
          rowKey="id"
          size="small"
          columns={columns}
          dataSource={entries}
          pagination={{ pageSize: 15, hideOnSinglePage: true }}
          locale={{ emptyText: <Empty description="没有符合条件的条目" /> }}
        />
        <Typography.Title level={5} style={{ marginTop: 16 }}>
          版本
        </Typography.Title>
        <List
          size="small"
          dataSource={versionsQuery.data?.items ?? []}
          locale={{ emptyText: "还没有版本；冲突清零后可生成版本" }}
          renderItem={(version: GlossaryVersionInfo) => (
            <List.Item>
              v{version.version} · {version.entry_count} 条 ·{" "}
              {version.created_at ?? ""}
              {version.note ? ` · ${version.note}` : ""}
            </List.Item>
          )}
        />
      </Card>
    </div>
  );
}
