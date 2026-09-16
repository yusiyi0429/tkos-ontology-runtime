import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert"
import { Button } from "@/components/ui/button"
import { errorLabel } from "@/lib/errors"
import { relativeToNow } from "@/lib/format"

export function ErrorBanner({ error, updatedAt }: { error: unknown; updatedAt: string | null }) {
  if (!error) return null
  return (
    <Alert variant="destructive" role="alert" data-testid="error-banner">
      <AlertTitle>{errorLabel(error)}</AlertTitle>
      <AlertDescription>
        {updatedAt ? `上次成功读取：${relativeToNow(updatedAt)}。` : null}
        不会用旧数据冒充已刷新结果。
      </AlertDescription>
    </Alert>
  )
}

export function StaleBanner({ updatedAt }: { updatedAt: string | null }) {
  return (
    <div data-testid="stale-banner"
         className="border-b border-amber-300 bg-amber-50 px-3 py-1.5 text-xs text-amber-900">
      网络读取失败，当前显示的是上次成功读取的内容（{relativeToNow(updatedAt)}），不代表已刷新。
    </div>
  )
}

export function PendingUpdateBanner({ onAccept, onDismiss }: { onAccept: () => void; onDismiss: () => void }) {
  return (
    <div data-testid="pending-banner"
         className="flex flex-wrap items-center gap-2 border-b border-sky-300 bg-sky-50 px-3 py-1.5 text-xs text-sky-900">
      <span>服务端内容已更新；当前仍显示你正在阅读的版本。</span>
      <Button size="xs" variant="outline" onClick={onAccept}>查看最新</Button>
      <Button size="xs" variant="ghost" onClick={onDismiss}>保持当前版本</Button>
    </div>
  )
}

export function AuthLostPanel() {
  return (
    <div data-testid="auth-lost"
         className="flex h-full flex-col items-center justify-center gap-2 p-8 text-center">
      <p className="text-sm font-medium text-foreground">查看身份已失效</p>
      <p className="max-w-sm text-xs text-muted-foreground">
        受保护内容已清空。请联系看板部署者重新配置本机合成查看身份后刷新页面。
      </p>
    </div>
  )
}
