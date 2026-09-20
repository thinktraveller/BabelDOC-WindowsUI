import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  Alert,
  App as AntApp,
  Button,
  Card,
  Checkbox,
  Collapse,
  Descriptions,
  Form,
  Input,
  InputNumber,
  List,
  Progress,
  Select,
  Space,
  Switch,
  Typography,
  Upload,
} from "antd";
import type { UploadFile } from "antd/es/upload/interface";
import { useEffect, useMemo, useState } from "react";

import { ApiError, api, type ImportProgress } from "../api";
import { useWorkbench } from "../store";
import type { ParamSpec } from "../types";

interface Props {
  onOpenSettings: () => void;
}

const { Dragger } = Upload;

export default function WorkbenchPanel({ onOpenSettings }: Props) {
  const { message } = AntApp.useApp();
  const queryClient = useQueryClient();
  const [form] = Form.useForm();
  const state = useWorkbench();
  const [pendingFiles, setPendingFiles] = useState<UploadFile[]>([]);
  const [uploadPercent, setUploadPercent] = useState<number | null>(null);
  const [fieldErrors, setFieldErrors] = useState<Record<string, string>>({});

  const schemaQuery = useQuery({
    queryKey: ["params-schema"],
    queryFn: api.paramsSchema,
  });
  const filesQuery = useQuery({ queryKey: ["files"], queryFn: api.listFiles });
  const profilesQuery = useQuery({ queryKey: ["profiles"], queryFn: api.listProfiles });
  const presetsQuery = useQuery({ queryKey: ["presets"], queryFn: api.listPresets });
  const langOut = (Form.useWatch("lang_out", form) as string | undefined) ?? "zh";
  const versionsQuery = useQuery({
    queryKey: ["glossary-versions", langOut],
    queryFn: () => api.listGlossaryVersions(undefined, langOut),
  });

  useEffect(() => {
    if (!schemaQuery.data) {
      return;
    }
    const defaults: Record<string, unknown> = { ...schemaQuery.data.defaults };
    schemaQuery.data.items.forEach((item) => {
      if (defaults[item.key] === undefined) {
        defaults[item.key] = item.default;
      }
    });
    state.setParams({ ...defaults, ...state.params });
    form.setFieldsValue({ ...defaults, ...state.params });
    // 只在拿到 schema 时初始化一次
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [schemaQuery.data]);

  useEffect(() => {
    if (state.profileId === null && profilesQuery.data?.items.length) {
      const preferred =
        profilesQuery.data.items.find((item) => item.is_default) ??
        profilesQuery.data.items[0];
      state.setProfileId(preferred.id);
    }
  }, [profilesQuery.data, state]);

  const importMutation = useMutation({
    mutationFn: async (files: File[]) =>
      api.importFiles(files, (progress: ImportProgress) => {
        setUploadPercent(
          progress.total ? Math.round((progress.loaded / progress.total) * 100) : null,
        );
      }),
    onSuccess: (items) => {
      setPendingFiles([]);
      setUploadPercent(null);
      state.setSelectedFileIds([
        ...state.selectedFileIds,
        ...items.map((item) => item.id),
      ]);
      queryClient.invalidateQueries({ queryKey: ["files"] });
      message.success(`已导入 ${items.length} 个文件`);
    },
    onError: (error) => {
      setUploadPercent(null);
      message.error(describeError(error));
    },
  });

  const deleteFileMutation = useMutation({
    mutationFn: api.deleteFile,
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ["files"] }),
    onError: (error) => message.error(describeError(error)),
  });

  const submitMutation = useMutation({
    mutationFn: async () => {
      const values = form.getFieldsValue();
      const params: Record<string, unknown> = {};
      (schemaQuery.data?.items ?? []).forEach((item) => {
        params[item.key] = values[item.key];
      });
      const validation = await api.validateParams(params);
      if (!validation.ok) {
        setFieldErrors(validation.errors);
        const first = Object.values(validation.errors)[0];
        throw new ApiError(first ?? "参数不合法", 400, validation.errors);
      }
      setFieldErrors({});
      state.setParams(params);
      return api.createTasks({
        file_ids: state.selectedFileIds,
        api_profile_id: state.profileId,
        params,
      });
    },
    onSuccess: (payload) => {
      message.success(`已提交 ${payload.items.length} 个任务`);
      state.setSelectedFileIds([]);
      queryClient.invalidateQueries({ queryKey: ["tasks"] });
    },
    onError: (error) => message.error(describeError(error)),
  });

  const savePresetMutation = useMutation({
    mutationFn: async () => {
      const name = state.presetName.trim();
      if (!name) {
        throw new ApiError("请先填写预设名称", 400);
      }
      const values = form.getFieldsValue();
      const params: Record<string, unknown> = {};
      (schemaQuery.data?.items ?? []).forEach((item) => {
        params[item.key] = values[item.key];
      });
      return api.savePreset(name, params);
    },
    onSuccess: () => {
      message.success("预设已保存");
      queryClient.invalidateQueries({ queryKey: ["presets"] });
    },
    onError: (error) => message.error(describeError(error)),
  });

  const files = filesQuery.data?.items ?? [];
  const profiles = profilesQuery.data?.items ?? [];
  const specs = schemaQuery.data?.items ?? [];
  const commonSpecs = specs.filter((item) => item.group === "common");
  const advancedSpecs = specs.filter((item) => item.group === "advanced");
  const selectedCount = state.selectedFileIds.length;

  const selectedProfile = useMemo(
    () => profiles.find((item) => item.id === state.profileId) ?? null,
    [profiles, state.profileId],
  );
  const glossaryOptions = (versionsQuery.data?.items ?? []).map((version) => ({
    value: version.id,
    label: `${version.glossary_name ?? `术语表 ${version.glossary_id}`} v${version.version}（${version.entry_count} 条）`,
  }));
  const selectedVersionId = Form.useWatch("glossary_version_id", form) as
    | number
    | undefined;
  const selectedVersionLabel =
    glossaryOptions.find((option) => option.value === selectedVersionId)?.label ?? "未选择";

  const submitDisabled =
    selectedCount === 0 ||
    state.profileId === null ||
    importMutation.isPending ||
    submitMutation.isPending;

  return (
    <div className="panel-grid">
      <Card title="导入 PDF 并提交翻译" size="small">
        {profiles.length === 0 && (
          <Alert
            type="warning"
            showIcon
            style={{ marginBottom: 12 }}
            message="还没有 API 配置"
            description="请先在“设置”中添加 OpenAI-compatible 服务的 Base URL、模型名与 API Key，然后再提交任务。"
            action={
              <Button size="small" onClick={onOpenSettings}>
                去设置
              </Button>
            }
          />
        )}
        <Dragger
          multiple
          accept=".pdf"
          fileList={pendingFiles}
          beforeUpload={(file, fileList) => {
            setPendingFiles(fileList);
            return false;
          }}
          onRemove={(file) => {
            setPendingFiles((current) => current.filter((item) => item.uid !== file.uid));
          }}
        >
          <p className="ant-upload-text">拖入 PDF 文件，或点击选择</p>
          <p className="hint-text">仅支持 PDF；导入后文件会复制到应用数据目录</p>
        </Dragger>
        <Space style={{ marginTop: 12 }}>
          <Button
            type="primary"
            disabled={pendingFiles.length === 0}
            loading={importMutation.isPending}
            onClick={() =>
              importMutation.mutate(
                pendingFiles
                  .map((item) => item.originFileObj as File | undefined)
                  .filter((file): file is File => Boolean(file)),
              )
            }
          >
            导入选中文件
          </Button>
          <Button onClick={() => setPendingFiles([])} disabled={!pendingFiles.length}>
            清空
          </Button>
        </Space>
        {uploadPercent !== null && (
          <Progress percent={uploadPercent} size="small" style={{ marginTop: 8 }} />
        )}

        <Typography.Title level={5} style={{ marginTop: 16 }}>
          已导入文件（勾选后提交）
        </Typography.Title>
        <List
          size="small"
          bordered
          locale={{ emptyText: "还没有导入文件" }}
          dataSource={files}
          renderItem={(item) => (
            <List.Item
              actions={[
                <Button
                  key="delete"
                  type="link"
                  danger
                  onClick={() => deleteFileMutation.mutate(item.id)}
                >
                  移除
                </Button>,
              ]}
            >
              <Checkbox
                checked={state.selectedFileIds.includes(item.id)}
                onChange={() => state.toggleFile(item.id)}
              >
                {item.name}
                <span className="hint-text">
                  {" "}
                  （{(item.size / 1024 / 1024).toFixed(1)} MB）
                </span>
              </Checkbox>
            </List.Item>
          )}
        />
      </Card>

      <Card title="参数与提交" size="small">
        <Form
          form={form}
          layout="vertical"
          onValuesChange={(_, values) => state.setParams({ ...values })}
        >
          <Form.Item label="API 配置" required>
            <Select
              placeholder="选择 API 配置"
              value={state.profileId ?? undefined}
              onChange={(value) => state.setProfileId(value)}
              options={profiles.map((item) => ({
                value: item.id,
                label: `${item.name}（${item.model}）${item.has_key ? "" : " · 未保存 Key"}`,
              }))}
            />
          </Form.Item>
          <Space wrap>
            <Select
              style={{ width: 220 }}
              placeholder="加载参数预设"
              allowClear
              value={state.presetName || undefined}
              onChange={(value) => {
                const preset = presetsQuery.data?.items.find((item) => item.name === value);
                if (preset) {
                  state.applyPreset(preset);
                  form.setFieldsValue(preset.params);
                } else {
                  state.setPresetName("");
                }
              }}
              options={(presetsQuery.data?.items ?? []).map((item) => ({
                value: item.name,
                label: item.name,
              }))}
            />
            <Input
              style={{ width: 160 }}
              placeholder="预设名称"
              value={state.presetName}
              onChange={(event) => state.setPresetName(event.target.value)}
            />
            <Button
              loading={savePresetMutation.isPending}
              onClick={() => savePresetMutation.mutate()}
            >
              保存预设
            </Button>
          </Space>

          <Typography.Title level={5} style={{ marginTop: 16 }}>
            常用参数
          </Typography.Title>
          <div className="panel-grid" style={{ gridTemplateColumns: "1fr 1fr" }}>
            {commonSpecs.map((spec) => renderField(spec, fieldErrors, glossaryOptions))}
          </div>

          <Collapse
            ghost
            style={{ marginTop: 8 }}
            items={[
              {
                key: "advanced",
                label: "高级参数",
                children: (
                  <div className="panel-grid" style={{ gridTemplateColumns: "1fr 1fr" }}>
                    {advancedSpecs.map((spec) =>
                      renderField(spec, fieldErrors, glossaryOptions),
                    )}
                  </div>
                ),
              },
            ]}
          />
        </Form>

        <Descriptions size="small" column={1} style={{ marginTop: 8 }}>
          <Descriptions.Item label="待提交文件">
            {selectedCount ? `${selectedCount} 个` : "尚未勾选"}
          </Descriptions.Item>
          <Descriptions.Item label="API 配置">
            {selectedProfile
              ? `${selectedProfile.name} · ${selectedProfile.model}`
              : "尚未选择"}
          </Descriptions.Item>
          <Descriptions.Item label="术语版本">{selectedVersionLabel}</Descriptions.Item>
          <Descriptions.Item label="输出位置">
            应用数据目录下的任务目录（任务详情中可见具体路径）
          </Descriptions.Item>
        </Descriptions>
        <Space>
          <Button
            type="primary"
            disabled={submitDisabled}
            loading={submitMutation.isPending}
            onClick={() => submitMutation.mutate()}
          >
            提交翻译任务
          </Button>
          {selectedCount === 0 && <span className="hint-text">请先勾选文件</span>}
          {state.profileId === null && profiles.length > 0 && (
            <span className="hint-text">请先选择 API 配置</span>
          )}
        </Space>
      </Card>
    </div>
  );
}

function renderField(
  spec: ParamSpec,
  fieldErrors: Record<string, string>,
  glossaryOptions: { value: number; label: string }[] = [],
) {
  const error = fieldErrors[spec.key];
  const common = {
    name: spec.key,
    label: spec.label,
    help: error ?? spec.hint,
    validateStatus: error ? ("error" as const) : undefined,
  };
  if (spec.type === "bool") {
    return (
      <Form.Item key={spec.key} {...common} valuePropName="checked">
        <Switch />
      </Form.Item>
    );
  }
  if (spec.type === "int") {
    return (
      <Form.Item key={spec.key} {...common}>
        <InputNumber min={1} style={{ width: "100%" }} placeholder="留空使用默认值" />
      </Form.Item>
    );
  }
  if (spec.type === "float") {
    return (
      <Form.Item key={spec.key} {...common}>
        <InputNumber min={0.05} step={0.05} style={{ width: "100%" }} />
      </Form.Item>
    );
  }
  if (spec.type === "glossary") {
    return (
      <Form.Item key={spec.key} {...common}>
        <Select
          allowClear
          placeholder={glossaryOptions.length ? "选择已生成的术语版本" : "暂无可用版本"}
          options={glossaryOptions}
        />
      </Form.Item>
    );
  }
  return (
    <Form.Item key={spec.key} {...common}>
      <Input placeholder={spec.type === "pages" ? "例如 1-5,8，留空表示全部" : undefined} />
    </Form.Item>
  );
}

export function describeError(error: unknown): string {
  if (error instanceof ApiError) {
    return error.message;
  }
  if (error instanceof Error) {
    return error.message;
  }
  return "操作失败，请重试";
}
