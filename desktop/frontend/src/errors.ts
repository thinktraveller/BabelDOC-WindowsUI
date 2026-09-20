/** 把后端返回的错误体转换成用户可读文案（纯函数，便于单元测试）。 */
export function describeErrorDetail(detail: unknown, status: number): string {
  if (typeof detail === "string" && detail) {
    return detail;
  }
  if (detail && typeof detail === "object") {
    const record = detail as Record<string, unknown>;
    if (typeof record.detail === "string") {
      return record.detail;
    }
    if (record.detail && typeof record.detail === "object") {
      const nested = record.detail as Record<string, unknown>;
      if (typeof nested.message === "string") {
        const errors = nested.errors as Record<string, string> | undefined;
        const first = errors ? Object.values(errors)[0] : undefined;
        return first ? `${nested.message}：${first}` : nested.message;
      }
    }
    if (typeof record.message === "string") {
      return record.message;
    }
  }
  if (status === 403) {
    return "会话令牌无效或请求来源不被允许，请重启工作台后重试。";
  }
  if (status === 0) {
    return "无法连接本地服务：请确认 BabelDOC 工作台正在运行，然后重试。";
  }
  return `请求失败（HTTP ${status}）`;
}
