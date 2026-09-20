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

import { api, subscribeTaskEvents } from "../api";
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
    mutationFn: api.deleteTask,
    onSuccess: () => {
      message.success("已删除任务记录（文件未被删除）");
      setDetailId(null);
      queryClient.invalidateQueries({ queryKey: ["tasks"] });
    },
    onError: (error) => message.error(describeError(error)),
  });

  const columns: ColumnsType<TaskInfo> = [
    { title: "任务", dataIndex: "id", width: 80, render: (id: number) => `#${id}` },
    {
      title: "文件",
      dataIndex: "input_name",
      ellipsis: true,
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
              <Descriptions.Item label="引擎版本">
                {detail.engine_version ?? "—"}
              </Descriptions.Item>
              {detail.error_message && (
                <Descriptions.Item label="错误">{detail.error_message}</Descriptions.Item>
              )}
            </Descriptions>
            <Typography.Title level={5}>成果文件</Typography.Title>
            {detail.outputs.length === 0 && <span className="hint-text">暂无成果</span>}
            {detail.outputs.map((output) => (
              <div key={output.path}>
                <Tag>{output.kind}</Tag>
                <Typography.Text
                  type={output.exists ? undefined : "danger"}
                  style={{ fontSize: 12 }}
                >
                  {output.path}
                  {output.exists ? "" : "（文件不存在或被移动）"}
                </Typography.Text>
              </div>
            ))}
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
