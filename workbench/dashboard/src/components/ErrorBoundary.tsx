import { Component, type ReactNode } from "react"
import { Button } from "@/components/ui/button"

interface ErrorBoundaryProps {
  children: ReactNode
  /** A new value (another page or item) clears an earlier failure. */
  resetKey?: string
  /** Leave the failed part for a known-good page; without it the panel offers a page reload. */
  onBack?: () => void
  backLabel?: string
}

interface ErrorBoundaryState {
  failed: boolean
  resetKey?: string
}

/**
 * A render error inside one part shows a recoverable panel instead of letting
 * React unmount the whole page (and every unsent form with it).
 */
export class ErrorBoundary extends Component<ErrorBoundaryProps, ErrorBoundaryState> {
  state: ErrorBoundaryState = { failed: false, resetKey: this.props.resetKey }

  static getDerivedStateFromError(): Partial<ErrorBoundaryState> {
    return { failed: true }
  }

  static getDerivedStateFromProps(props: ErrorBoundaryProps,
                                  state: ErrorBoundaryState): Partial<ErrorBoundaryState> | null {
    return props.resetKey !== state.resetKey ? { failed: false, resetKey: props.resetKey } : null
  }

  render() {
    if (!this.state.failed) return this.props.children
    const { onBack, backLabel } = this.props
    return (
      <div role="alert" data-testid="pane-error"
           className="mx-auto my-10 flex max-w-xl flex-col items-center gap-3 rounded-xl border border-dashed border-border bg-card p-8 text-center">
        <h3 className="text-base font-semibold">这部分页面暂时无法显示</h3>
        <p className="text-xs leading-relaxed text-muted-foreground">
          显示时出现异常，这一部分已停止显示；其它部分和已保存的数据不受影响。可以重新显示，或离开后再回来。
        </p>
        <div className="flex flex-wrap justify-center gap-2">
          <Button variant="outline" onClick={() => this.setState({ failed: false })}>重新显示</Button>
          {onBack ? (
            <Button onClick={() => { this.setState({ failed: false }); onBack() }}>{backLabel ?? "返回"}</Button>
          ) : (
            <Button onClick={() => window.location.reload()}>重新加载页面</Button>
          )}
        </div>
      </div>
    )
  }
}
