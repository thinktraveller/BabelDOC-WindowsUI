import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  App as AntApp,
  Button,
  Card,
  Descriptions,
  Drawer,
  Empty,
  Popconfirm,
  Progress,
  Space,
  Table,
  Tag,
  Typography,
} from "antd";
import type { ColumnsType } from "antd/es/table";
import { useEffect, useState } from "react";

import { api, chooseDirectory, subscribeTaskEvents } from "../api";
import { formatDuration, formatSize, kindLabel } from "../format";
import type { TaskEvent, TaskInfo, TaskStatus } from "../types";
import { describeError } from "./WorkbenchPanel";

const STATUS_LABELS: Record<TaskStatus, string> = {
  queued: "排队中",
  preparing: "准备中",
  running: "翻译中",
  succeeded: "已完成",
  failed: "失败",
  cancelled: "已取消",
  interrupted: "已中断",
};

const STATUS_COLORS: Record<TaskStatus, string> = {
  queued: "default",
  preparing: "processing",
  running: "processing",
  succeeded: "success",
  failed: "error",
  cancelled: "warning",
  interrupted: "warning",
};

export default function TasksPanel() {
  const { message } = AntApp.useApp();
  const queryClient = useQueryClient();
  const [detailId, setDetailId] = useState<number | null>(null);
  const [live, setLive] = useState<{ progress: number | null; stage: string | null }>({
    progress: null,
    stage: null,
  });
  const [events, setEvents] = useState<TaskEvent[]>([]);

  const tasksQuery = useQuery({
    queryKey: ["tasks"],
    queryFn: () => api.listTasks(),
    refetchInterval: 2000,
  });
  const detailQuery = useQuery({
    queryKey: ["task", detailId],
    queryFn: () => api.getTask(detailId as number),
    enabled: detailId !== null,
  });

  const detail = detailQuery.data;
  const running = detail?.status === "running" || detail?.status === "queued";

  useEffect(() => {
    setLive({ progress: detail?.progress ?? null, stage: detail?.stage_label ?? null });
    setEvents(detail?.events ?? []);
  }, [detail?.id]);

  useEffect(() => {
    if (detailId === null || !running) {
      return;
    }
    const controller = new AbortController();
    void subscribeTaskEvents(detailId, {
      signal: controller.signal,
      onEvent: (event) => {
        if (event.type === "snapshot") {
          const snapshot = event.payload as unknown as TaskInfo;
          setLive({ progress: snapshot.progress ?? null, stage: snapshot.stage_label ?? null });
          setEvents(snapshot.events ?? []);
          return;
        }
        if (event.type === "progress_update" || event.type === "progress_end") {
          const progress = event.payload.overall_progress;
          setLive({
            progress: typeof progress === "number" ? progress : null,
            stage: (event.payload.stage_label as string) ?? null,
          });
        }
        setEvents((current) =>
          [
            ...current,
            {
              sequence: current.length + 1,
              type: event.type,
              payload: event.payload,
              ts: null,
            },
          ].slice(-100),
        );
        if (["finish", "error", "cancelled", "status"].includes(event.type)) {
          queryClient.invalidateQueries({ queryKey: ["tasks"] });
          queryClient.invalidateQueries({ queryKey: ["task", detailId] });
        }
      },
      onError: (text) => message.warning(text),
    });
    return () => controller.abort();
  }, [detailId, running, queryClient, message]);

  const cancelMutation = useMutation({
    mutationFn: api.cancelTask,
    onSuccess: () => {
      message.info("已请求取消；取消表示停止本次翻译，不会保留断点。");
      queryClient.invalidateQueries({ queryKey: ["tasks"] });
    },
    onError: (error) => message.error(describeError(error)),
  });
  const rerunMutation = useMutation({
    mutationFn: api.rerunTask,
    onSuccess: (task) => {
      message.success(`已创建新任务 #${task.id}`);
      queryClient.invalidateQueries({ queryKey: ["tasks"] });
    },
    onError: (error) => message.error(describeError(error)),
  });
  const deleteMutation = useMutation({
    mutationFn: (id: number) => api.deleteTask(id, false),
    onSuccess: () => {
      message.success("已删除任务记录（文件未被删除）");
      setDetailId(null);
      queryClient.invalidateQueries({ queryKey: ["tasks"] });
    },
    onError: (error) => message.error(describeError(error)),
  });
  const cleanupMutation = useMutation({
    mutationFn: api.cleanupTask,
    onSuccess: (result) => {
      message.success(`已清理临时文件，释放 ${formatSize(result.freed_bytes)}`);
      queryClient.invalidateQueries({ queryKey: ["tasks"] });
      queryClient.invalidateQueries({ queryKey: ["task", detailId] });
    },
    onError: (error) => message.error(describeError(error)),
  });
  const deleteFilesMutation = useMutation({
    mutationFn: api.deleteTaskFiles,
    onSuccess: (result) => {
      message.success(`已删除应用管理的文件，释放 ${formatSize(result.freed_bytes)}`);
      queryClient.invalidateQueries({ queryKey: ["tasks"] });
      queryClient.invalidateQueries({ queryKey: ["task", detailId] });
    },
    onError: (error) => message.error(describeError(error)),
  });
  const openMutation = useMutation({
    mutationFn: ({ id, kind }: { id: number; kind: string }) => api.openOutput(id, kind),
    onError: (error) => message.error(describeError(error)),
  });
  const revealMutation = useMutation({
    mutationFn: ({ id, kind }: { id: number; kind: string }) => api.revealOutput(id, kind),
    onError: (error) => message.error(describeError(error)),
  });
  const saveAsMutation = useMutation({
    mutationFn: async ({ id, kind }: { id: number; kind: string }) => {
      const settings = await api.getAppSettings();
      const configured = kind === "mono" ? settings.default_mono_output_dir
        : kind === "dual" ? settings.default_dual_output_dir
          : kind === "glossary" ? settings.default_glossary_output_dir : "";
      const directory = await chooseDirectory(configured);
      if (!directory) {
        throw new Error("已取消另存");
      }
      return api.saveOutputAs(id, kind, directory);
    },
    onSuccess: (result) => message.success(`已另存到 ${result.target}`),
    onError: (error) => message.error(describeError(error)),
  });

  const taskFilesQuery = useQuery({
    queryKey: ["task-files", detailId],
    queryFn: () => api.taskFiles(detailId as number),
    enabled: detailId !== null,
  });

  const exportEvent = [...(detail?.events ?? [])].reverse().find((event) => event.type === "exported");
  const exportItems = Array.isArray(exportEvent?.payload.items)
    ? exportEvent.payload.items as { kind: string; source: string; path: string }[]
    : [];
  const exportErrors = Array.isArray(exportEvent?.payload.errors)
    ? exportEvent.payload.errors as string[]
    : [];

  const columns: ColumnsType<TaskInfo> = [
    { title: "任务", dataIndex: "id", width: 80, render: (id: number) => `#${id}` },
    {
      title: "文件",
      dataIndex: "input_name",
      ellipsis: true,
    },
    {
      title: "目标语言",
      width: 100,
      render: (_value, task) => String(task.params?.lang_out ?? "—"),
    },
    {
      title: "状态",
      dataIndex: "status",
      width: 110,
      render: (status: TaskStatus) => (
        <Tag color={STATUS_COLORS[status]}>{STATUS_LABELS[status] ?? status}</Tag>
      ),
    },
    {
      title: "处理进度",
      width: 220,
      render: (_value, task) => (
        <div className="task-progress">
          {task.status === "running" || task.status === "succeeded" ? (
            <Progress percent={Math.round(task.progress ?? 0)} size="small" />
          ) : (
            <span className="hint-text">{task.stage_label ?? "—"}</span>
          )}
          {task.stage_label && (task.status === "running" || task.status === "succeeded") && (
            <div className="hint-text">{task.stage_label}</div>
          )}
        </div>
      ),
    },
    {
      title: "错误",
      dataIndex: "error_message",
      ellipsis: true,
      render: (text: string | null) => text ?? "—",
    },
    {
      title: "耗时",
      width: 100,
      render: (_value, task) => formatDuration(task.started_at, task.finished_at),
    },
    {
      title: "操作",
      width: 230,
      render: (_value, task) => (
        <Space size="small">
          <Button type="link" size="small" onClick={() => setDetailId(task.id)}>
            详情
          </Button>
          {(task.status === "running" || task.status === "queued") && (
            <Button
              type="link"
              size="small"
              danger
              onClick={() => cancelMutation.mutate(task.id)}
            >
              取消
            </Button>
          )}
          {["succeeded", "failed", "cancelled", "interrupted"].includes(task.status) && (
            <>
              <Button
                type="link"
                size="small"
                onClick={() => rerunMutation.mutate(task.id)}
              >
                重新执行
              </Button>
              <Popconfirm
                title="删除任务记录？"
                description="只删除数据库记录，不会删除输入文件与成果文件。"
                okText="删除记录"
                cancelText="取消"
                onConfirm={() => deleteMutation.mutate(task.id)}
              >
                <Button type="link" size="small" danger>
                  删除记录
                </Button>
              </Popconfirm>
            </>
          )}
        </Space>
      ),
    },
  ];

  return (
    <Card title="任务列表" size="small">
      <Table
        rowKey="id"
        size="small"
        columns={columns}
        dataSource={tasksQuery.data?.items ?? []}
        pagination={{ pageSize: 20, hideOnSinglePage: true }}
        locale={{ emptyText: <Empty description="还没有任务" /> }}
      />
      <Drawer
        width={560}
        title={detail ? `任务 #${detail.id} · ${detail.input_name}` : "任务详情"}
        open={detailId !== null}
        onClose={() => setDetailId(null)}
      >
        {detail && (
          <Space direction="vertical" style={{ width: "100%" }}>
            <Descriptions size="small" column={1} bordered>
              <Descriptions.Item label="状态">
                <Tag color={STATUS_COLORS[detail.status]}>
                  {STATUS_LABELS[detail.status] ?? detail.status}
                </Tag>
                {detail.status === "interrupted" && (
                  <span className="hint-text"> 成果可能不完整，可重新执行</span>
                )}
              </Descriptions.Item>
              <Descriptions.Item label="当前阶段">
                {live.stage ?? detail.stage_label ?? "—"}
              </Descriptions.Item>
              <Descriptions.Item label="处理进度">
                <Progress percent={Math.round(live.progress ?? detail.progress ?? 0)} size="small" />
              </Descriptions.Item>
              <Descriptions.Item label="参数快照">
                <Typography.Text code>
                  {JSON.stringify(detail.params)}
                </Typography.Text>
              </Descriptions.Item>
              <Descriptions.Item label="术语版本">
                {detail.glossary_version_id ?? "未使用"}
              </Descriptions.Item>
              <Descriptions.Item label="耗时">
                {formatDuration(detail.started_at, detail.finished_at)}
              </Descriptions.Item>
              <Descriptions.Item label="引擎版本">
                {detail.engine_version ?? "—"}
              </Descriptions.Item>
              {detail.error_message && (
                <Descriptions.Item label="错误">
                  <div>{detail.error_message}</div>
                  <Typography.Paragraph
                    style={{ marginBottom: 0 }}
                    copyable={{ text: JSON.stringify(detail.params) }}
                  >
                    <span className="hint-text">错误码：{detail.error_code ?? "—"}</span>
                  </Typography.Paragraph>
                </Descriptions.Item>
              )}
            </Descriptions>
            <Typography.Title level={5}>成果文件</Typography.Title>
            {exportErrors.length > 0 && (
              <Typography.Text type="warning">
                默认目录导出未全部完成：{exportErrors.join("；")}。可用「另存为」补存。
              </Typography.Text>
            )}
            {detail.outputs.length === 0 && <span className="hint-text">暂无成果</span>}
            {detail.outputs.map((output) => (
              <div key={output.path} style={{ marginBottom: 6 }}>
                <Space size="small" wrap>
                  <Tag>{kindLabel(output.kind)}</Tag>
                  {output.exists ? (
                    <>
                      <Button
                        size="small"
                        onClick={() => openMutation.mutate({ id: detail.id, kind: output.kind })}
                      >
                        打开
                      </Button>
                      <Button
                        size="small"
                        onClick={() => saveAsMutation.mutate({ id: detail.id, kind: output.kind })}
                      >
                        另存为
                      </Button>
                      <Button size="small" onClick={() => revealMutation.mutate({ id: detail.id, kind: output.kind })}>
                        在文件夹中显示
                      </Button>
                      <span className="hint-text">{formatSize(output.size)}</span>
                    </>
                  ) : (
                    <>
                      <Typography.Text type="danger">应用管理原件不存在或被移动</Typography.Text>
                      {exportItems.some((item) => item.kind === output.kind && item.source === output.path) && (
                        <Button size="small" onClick={() => revealMutation.mutate({ id: detail.id, kind: output.kind })}>
                          在文件夹中显示副本
                        </Button>
                      )}
                      <Button
                        size="small"
                        onClick={() => rerunMutation.mutate(detail.id)}
                      >
                        重新执行
                      </Button>
                    </>
                  )}
                </Space>
                <div className="hint-text" style={{ wordBreak: "break-all" }}>
                  应用管理原件：{output.path}
                </div>
                {exportItems.filter((item) => item.source === output.path).map((item) => (
                  <div key={item.path} className="hint-text" style={{ wordBreak: "break-all" }}>
                    默认输出目录副本：{item.path}
                  </div>
                ))}
              </div>
            ))}
            <Typography.Title level={5}>文件管理</Typography.Title>
            <Space direction="vertical" style={{ width: "100%" }}>
              <span className="hint-text">
                任务目录：{taskFilesQuery.data?.root ?? detail.output_dir}
                （占用 {formatSize(taskFilesQuery.data?.total_size ?? 0)}）
              </span>
              <Space wrap>
                <Button
                  size="small"
                  loading={cleanupMutation.isPending}
                  onClick={() => cleanupMutation.mutate(detail.id)}
                >
                  清理任务临时文件
                </Button>
                <Popconfirm
                  title="删除应用管理的文件？"
                  description={
                    <div style={{ maxWidth: 320 }}>
                      将删除任务目录内的输入副本与成果（不可恢复）：
                      {(taskFilesQuery.data?.items ?? [])
                        .filter((item) => item.exists)
                        .map((item) => (
                          <div key={item.path} className="hint-text">
                            {item.name}：{formatSize(item.size)}
                          </div>
                        ))}
                      用户原始导入位置的文件不会被删除。
                    </div>
                  }
                  okText="删除文件"
                  cancelText="取消"
                  onConfirm={() => deleteFilesMutation.mutate(detail.id)}
                >
                  <Button size="small" danger>
                    删除应用管理的文件
                  </Button>
                </Popconfirm>
              </Space>
            </Space>
            <Typography.Title level={5}>事件时间线</Typography.Title>
            <div style={{ maxHeight: 260, overflow: "auto", fontSize: 12 }}>
              {events.slice(-60).map((event, index) => (
                <div key={`${event.sequence}-${index}`} className="hint-text">
                  [{event.type}] {JSON.stringify(event.payload).slice(0, 160)}
                </div>
              ))}
            </div>
          </Space>
        )}
      </Drawer>
    </Card>
  );
}
