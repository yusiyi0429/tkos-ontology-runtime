/** Dashboard error mapping.  The UI never shows raw English API text as content. */

export class ApiError extends Error {
  constructor(
    public status: number,
    public code: string,
    public serverMessage: string,
  ) {
    super(code)
    this.name = "ApiError"
  }
}

export const ERROR_LABELS: Record<string, string> = {
  UNAUTHENTICATED: "查看身份已失效，受保护内容已清空",
  FORBIDDEN: "当前身份无权读取该内容",
  NOT_FOUND: "对象不存在或当前不可见",
  INVALID_REQUEST: "筛选或分页参数不受支持",
  VERSION_CONFLICT: "内容版本已变化，请重新读取",
  STALE_DEPENDENCY: "引用的版本已变化，请重新读取",
  EVIDENCE_UNAVAILABLE: "证据存储暂不可用",
  DASHBOARD_VIEWER_UNAVAILABLE: "看板查看身份未配置，无法读取业务内容",
  DASHBOARD_HOST_POLICY_UNAVAILABLE: "看板访问策略未配置",
  DASHBOARD_ASSETS_MISSING: "看板静态资源未安装",
  PROTOCOL_NOT_SUPPORTED: "该对象的协议登记不支持读取",
  NETWORK: "网络连接中断，显示的是上次成功读取的内容",
}

export function errorLabel(error: unknown): string {
  if (error instanceof ApiError) {
    return ERROR_LABELS[error.code] ?? "读取失败，请稍后重试"
  }
  if (error instanceof DOMException && error.name === "AbortError") {
    return "请求已取消"
  }
  return ERROR_LABELS.NETWORK
}

/**
 * Protected content must be cleared (not kept as stale) when authority is gone.
 * A revoked assignment/strategy can surface as 403/404 while the credential
 * itself stays valid, and an unavailable viewer means no business read at all.
 */
export function isAccessDenial(error: unknown): boolean {
  if (!(error instanceof ApiError)) return false
  if (error.status === 401 || error.status === 403 || error.status === 404) return true
  return ["UNAUTHENTICATED", "FORBIDDEN", "NOT_FOUND",
          "DASHBOARD_VIEWER_UNAVAILABLE", "DASHBOARD_HOST_POLICY_UNAVAILABLE"].includes(error.code)
}

export function isAbort(error: unknown): boolean {
  return error instanceof DOMException && error.name === "AbortError"
}
