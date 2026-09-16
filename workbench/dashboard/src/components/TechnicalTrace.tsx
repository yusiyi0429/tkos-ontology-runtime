import { Collapsible, CollapsibleContent, CollapsibleTrigger } from "@/components/ui/collapsible"
import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import { ApiError } from "@/lib/errors"
import { formatTime, shortId } from "@/lib/format"
import type { Detail } from "@/lib/types"

export function TechnicalTrace({ detail, error }: { detail: Detail | null; error?: unknown }) {
  const missing = detail?.missing ?? []
  return (
    <div className="space-y-3" data-testid="technical-trace">
      <p className="text-[11.5px] text-muted-foreground">
        UUID、payload hash、原始 JSON 与错误技术信息收在此处。阅读主界面不依赖这些字段。
      </p>
      {missing.length ? (
        <div className="rounded border border-border bg-muted/40 p-2">
          <div className="text-[11.5px] font-medium">显式缺失/不可用</div>
          <ul className="mt-1 space-y-0.5 text-[11px] text-muted-foreground">
            {missing.map((entry, index) => (
              <li key={`${entry.field}-${index}`}>{entry.field}：{entry.reason}</li>
            ))}
          </ul>
        </div>
      ) : null}
      {error ? (
        <div className="rounded border border-destructive/30 bg-destructive/5 p-2 text-[11px]">
          <div className="font-medium">技术错误</div>
          <div className="text-muted-foreground">
            {error instanceof ApiError ? `HTTP ${error.status} · ${error.code}` : "网络/未知错误"}
          </div>
          {error instanceof ApiError && error.serverMessage ? (
            <div className="mt-1 break-all text-muted-foreground/80">{error.serverMessage}</div>
          ) : null}
        </div>
      ) : null}
      {detail ? (
        <>
          <dl className="grid grid-cols-[auto_1fr] gap-x-3 gap-y-1 text-[11px]">
            <dt className="text-muted-foreground">对象</dt>
            <dd className="font-mono break-all">{detail.object.object_id}</dd>
            <dt className="text-muted-foreground">对象版本</dt>
            <dd>{detail.object.object_version}</dd>
            <dt className="text-muted-foreground">精确 revision</dt>
            <dd className="font-mono break-all">{detail.selected_revision.revision_id}</dd>
            <dt className="text-muted-foreground">payload hash</dt>
            <dd className="font-mono break-all">{detail.selected_revision.payload_hash}</dd>
            <dt className="text-muted-foreground">latest</dt>
            <dd className="font-mono break-all">{shortId(detail.object.latest_revision_id, 40)}</dd>
            <dt className="text-muted-foreground">effective</dt>
            <dd className="font-mono break-all">{shortId(detail.object.effective_revision_id, 40)}</dd>
            <dt className="text-muted-foreground">协议</dt>
            <dd>{String(detail.protocol.contract_version ?? "未记录")}
              <Badge variant="outline" className="ml-2 rounded-sm text-[10px]">
                {String(detail.protocol.interpretation_status ?? "unknown")}
              </Badge>
            </dd>
            <dt className="text-muted-foreground">读取时间</dt>
            <dd>{formatTime(detail.read_at)}</dd>
          </dl>
          <Collapsible>
            <CollapsibleTrigger asChild>
              <Button size="xs" variant="outline">展开精确引用（对象/版本/hash）</Button>
            </CollapsibleTrigger>
            <CollapsibleContent>
              <ul className="mt-2 space-y-0.5 text-[10.5px]" data-testid="exact-refs">
                {[...detail.relations.own_basis_refs, ...detail.evidence.items,
                  ...detail.relations.downstream.items.map((edge) => ({ ...edge.ref,
                    object_type: edge.object_type, title: edge.title }))].map((ref, index) => (
                  <li key={`${ref.object_id}-${ref.revision_id}-${index}`} className="break-all">
                    <span className="text-muted-foreground">{ref.object_type}：</span>
                    <span className="font-mono">{ref.object_id}</span>
                    <span className="text-muted-foreground"> · 版本 </span>
                    <span className="font-mono">{ref.revision_id}</span>
                    {ref.payload_hash ? <span className="text-muted-foreground"> · hash {ref.payload_hash}</span> : null}
                  </li>
                ))}
              </ul>
            </CollapsibleContent>
          </Collapsible>
          <Collapsible>
            <CollapsibleTrigger asChild>
              <Button size="xs" variant="outline">展开原始 payload（精确内容）</Button>
            </CollapsibleTrigger>
            <CollapsibleContent>
              <pre className="mt-2 max-h-96 overflow-auto rounded bg-muted/50 p-2 text-[10.5px] leading-relaxed"
                   data-testid="raw-payload">
                {JSON.stringify(detail.content, null, 2)}
              </pre>
            </CollapsibleContent>
          </Collapsible>
        </>
      ) : null}
    </div>
  )
}
