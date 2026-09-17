import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react"
import { afterEach, expect, it, vi } from "vitest"
import { GovernanceApp } from "@/GovernanceApp"
import { SourceScenes, contextEnvelope, eventFor, resolveEvent, sceneEnvelope, sha256Hex } from "@/SourceScenes"
import { detail, methodMapAll, ontologyCatalog, overview } from "./fixtures"

const session = { identity: { principal_id: "p1", display_name: "合成 CEO" }, csrf: "c" }
const SCENE = {
  scene_id: "11111111-1111-1111-1111-111111111111", scene_type: "meeting",
  external_id: "meeting-1", title: "独立会议", owner_principal_id: "p1",
  participant_principal_ids: ["p2"], version: 4, created_at: "2026-09-17T00:00:00Z",
}
const DETAIL = {
  scene_id: SCENE.scene_id, version: 4, formal_effect: "none",
  scene: { ...SCENE }, principal_names: { p1: "合成 CEO", p2: "合成 DRI" },
  sources: [{
    source_id: "22222222-2222-2222-2222-222222222222", system: "feishu", external_id: "doc-1",
    title: "主会转写", media_type: "text/plain", acquired_at: "2026-09-17T00:10:00Z", status: "available",
    owner_principal_id: "p1", access: "owner",
    versions: [{ event_id: "33333333-3333-3333-3333-333333333333", version_seq: 1,
                 payload_hash: "a".repeat(64), fingerprint: "b".repeat(64), media_type: "text/plain",
                 acquired_at: "2026-09-17T00:10:00Z", status: "available",
                 segments: [{ speaker: "A", text: "原文片段", occurred_at: null }],
                 shares: [{ share_event_id: "44444444-4444-4444-4444-444444444444",
                            version_event_id: "33333333-3333-3333-3333-333333333333",
                            share_to_principal_id: "p2", active: true, note: null }] }],
  }],
  runs: [{ event_id: "55555555-5555-5555-5555-555555555555", status: "succeeded", purpose: "整理", withheld: false }],
  drafts: [{ event_id: "66666666-6666-6666-6666-666666666666", status: "available", title: "跟进草稿",
             items: [{ item_kind: "request", text: "请补充材料",
                       citations: [{ source_id: "22222222-2222-2222-2222-222222222222",
                                     version_event_id: "33333333-3333-3333-3333-333333333333",
                                     payload_hash: "a".repeat(64), segment_index: 0, quote: "本人答应补材料" }] }] },
           { event_id: "77777777-7777-7777-7777-777777777777", status: "withheld", withheld: true }],
  decisions: [{ event_id: "88888888-8888-8888-8888-888888888888",
                draft_event_id: "66666666-6666-6666-6666-666666666666", item_index: 0,
                decision: "accepted", note: "本人接受" }],
  operations: [
    { kind: 'source_version', allowed: true, reason: null },
    { kind: 'source_share', allowed: true, reason: null },
    { kind: 'source_correct', allowed: false, reason: 'no_active_owned_version' },
    { kind: 'followup_draft', allowed: true, reason: null },
    { kind: 'draft_decision', allowed: true, reason: null },
  ],
}

function setup() {
  vi.stubGlobal("fetch", vi.fn((input: RequestInfo | URL) => {
    const target = String(input)
    const body = target.includes("/governance/sources/contexts/") ? { items: [] }
      : /\/governance\/sources\/[0-9a-f-]{36}$/.test(target) ? DETAIL
      : target.includes("/governance/sources") ? { items: [SCENE], next_after: null }
      : { items: [] }
    return Promise.resolve(new Response(JSON.stringify(body),
      { status: 200, headers: { "Content-Type": "application/json" } }))
  }))
  const prepare = vi.fn(async (_body: Record<string, unknown>) => undefined)
  const onError = vi.fn()
  render(<SourceScenes session={session} prepare={prepare} onError={onError} />)
  return { prepare, onError }
}

afterEach(() => { cleanup(); vi.unstubAllGlobals(); window.history.replaceState(null, "", "/dashboard/") })

it("builds a standalone scene command with zero expected version", () => {
  const body = sceneEnvelope("s1", 0, eventFor("scene_create", {
    scene_type: "meeting", external_id: "m1", title: "主会", owner_principal_id: "p1",
    participants: [{ principal_id: "p2" }],
    agent_bindings: [{ agent_principal_id: "a1", owner_principal_id: "p1" }] }), "k".repeat(16))
  expect(body).toMatchObject({ contract_version: "tkos.workspace/0.2", scene_id: "s1", expected_version: 0 })
  expect(body.event).toMatchObject({ kind: "scene_create", scene_type: "meeting",
    participant_principal_ids: ["p2"], agent_bindings: [{ agent_principal_id: "a1", owner_principal_id: "p1" }] })
})

it("keeps exact version, segments and optional evidence explicit", () => {
  const version = eventFor("source_version", {
    source_id: "s1", fingerprint: "b".repeat(64), media_type: "text/plain", acquired_at: "2026-09-17T00:00:00Z",
    segments: [{ speaker: "A", text: "原文", occurred_at: "" }] })
  expect(version).toEqual({ kind: "source_version", source_id: "s1", fingerprint: "b".repeat(64),
    media_type: "text/plain", acquired_at: "2026-09-17T00:00:00Z",
    segments: [{ speaker: "A", text: "原文" }] })
  const corrected = eventFor("source_correct", {
    source_id: "s1", corrects_event_id: "v1", reason: "转写更正", media_type: "text/plain",
    acquired_at: "2026-09-17T00:00:00Z", segments: [{ text: "更正" }],
    evidence_object_id: "e1", evidence_revision_id: "r1", evidence_payload_hash: "c".repeat(64) })
  expect(corrected.evidence_ref).toEqual({ object_id: "e1", revision_id: "r1", payload_hash: "c".repeat(64) })
  expect(eventFor("source_share", { source_id: "s1", version_event_id: "v1",
    payload_hash: "c".repeat(64), share_to_principal_id: "p2" }))
    .toEqual({ kind: "source_share", source_id: "s1", version_event_id: "v1",
               payload_hash: "c".repeat(64), share_to_principal_id: "p2" })
  expect(eventFor("source_unshare", { share_event_id: "sh1", reason: "本人取消" }))
    .toEqual({ kind: "source_unshare", share_event_id: "sh1", reason: "本人取消" })
})

it("requires a citation for each draft item and keeps decisions human", () => {
  const draft = eventFor("followup_draft", { title: "跟进", items: [
    { item_kind: "request", text: "请补充", source_id: "s1", version_event_id: "v1",
      payload_hash: "d".repeat(64), segment_index: "2", quote: "原文" }] })
  expect(draft.items).toEqual([{ item_kind: "request", text: "请补充",
    citations: [{ source_id: "s1", version_event_id: "v1", payload_hash: "d".repeat(64),
                  segment_index: 2, quote: "原文" }] }])
  expect(eventFor("draft_decision", { draft_event_id: "d1", item_index: "0",
    decision: "accepted", note: "本人接受" }))
    .toEqual({ kind: "draft_decision", draft_event_id: "d1", item_index: 0,
               decision: "accepted", note: "本人接受" })
  expect(contextEnvelope("s1", "准备跟进", [{ source_id: "s1", version_event_id: "v1",
    payload_hash: "e".repeat(64) }], "k".repeat(16)))
    .toMatchObject({ contract_version: "tkos.workspace/0.2", scene_id: "s1", purpose: "准备跟进" })
})

it("offers every human source action and no Agent run control", async () => {
  const { prepare } = setup()
  await screen.findByTestId(`source-scene-${SCENE.scene_id}`)
  fireEvent.click(screen.getByTestId(`source-scene-${SCENE.scene_id}`))
  await screen.findByTestId("source-list")
  for (const kind of ["source_add", "source_version", "source_correct", "source_withdraw",
                      "source_share", "source_unshare", "followup_draft", "draft_decision",
                      "context_create"]) {
    expect(screen.getByTestId(`source-action-${kind}`)).toBeInTheDocument()
  }
  expect(screen.queryByTestId("source-action-agent_run")).not.toBeInTheDocument()
  expect(screen.getByTestId("source-runs")).toHaveTextContent("整理")
  expect(screen.getByTestId("source-drafts")).toHaveTextContent("草稿已撤下（引用来源当前不可读）")
  expect(screen.getByTestId("source-drafts")).toHaveTextContent("本人/成员决定：条目 0 → accepted")
  expect(prepare).not.toHaveBeenCalled()
})

it("opens a typed form and previews a source without manual technical fields", async () => {
  const { prepare } = setup()
  await screen.findByTestId(`source-scene-${SCENE.scene_id}`)
  fireEvent.click(screen.getByTestId(`source-scene-${SCENE.scene_id}`))
  await screen.findByTestId("source-list")
  fireEvent.click(screen.getByTestId("source-action-source_add"))
  fireEvent.change(await screen.findByLabelText("来源系统"), { target: { value: "feishu" } })
  fireEvent.change(screen.getByLabelText("来源 ID"), { target: { value: "doc-9" } })
  fireEvent.change(screen.getByLabelText("标题"), { target: { value: "文档来源" } })
  fireEvent.change(screen.getByLabelText("内容类型"), { target: { value: "text/markdown" } })
  fireEvent.change(screen.getByLabelText("获取时间"), { target: { value: "2026-09-17T09:30" } })
  fireEvent.click(screen.getByRole("button", { name: "准备并预览" }))
  await waitFor(() => expect(prepare).toHaveBeenCalledTimes(1))
  const body = prepare.mock.calls[0][0]
  expect(body).toMatchObject({ contract_version: "tkos.workspace/0.2", scene_id: SCENE.scene_id,
                               expected_version: 4 })
  expect((body.event as Record<string, unknown>)).toMatchObject({
    kind: "source_add", sensitivity: "private", media_type: "text/markdown" })
  expect(String((body.event as Record<string, unknown>).acquired_at)).toMatch(/Z$/)
  expect(screen.queryByLabelText(/payload_hash/)).not.toBeInTheDocument()
})

it("computes the submitted-text fingerprint instead of asking for a hash", async () => {
  const hash = await sha256Hex(new TextEncoder().encode("abc"))
  expect(hash).toBe("ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad")
  const derived = resolveEvent("source_version", {
    source_id: "s1", fingerprint: hash, media_type: "text/plain", acquired_at: "2026-09-17T09:30",
    segments: [{ text: "原文" }] }, DETAIL as never)
  expect(derived.fingerprint).toBe(hash)
  expect(String(derived.acquired_at)).toMatch(/Z$/)
})

it("derives share hash from the chosen version and keeps correct fingerprint", () => {
  const share = resolveEvent("source_share", {
    source_id: "22222222-2222-2222-2222-222222222222",
    version_event_id: "33333333-3333-3333-3333-333333333333",
    share_to_principal_id: "p2" }, DETAIL as never)
  expect(share.payload_hash).toBe("a".repeat(64))
  const corrected = resolveEvent("source_correct", {
    source_id: "22222222-2222-2222-2222-222222222222",
    corrects_event_id: "33333333-3333-3333-3333-333333333333", reason: "转写更正",
    fingerprint: "f".repeat(64), media_type: "text/plain", acquired_at: "2026-09-17T09:30",
    segments: [{ text: "更正" }] }, DETAIL as never)
  expect(corrected.fingerprint).toBe("f".repeat(64))
  expect(share).not.toHaveProperty("fingerprint")
})

it("exposes 独立来源 from the governance sidebar", async () => {
  window.history.replaceState(null, "", "/dashboard/")
  vi.stubGlobal("fetch", vi.fn((input: RequestInfo | URL) => {
    const target = String(input)
    const body = target.includes("/governance/method/tasks") ? { items: [], next_after: null }
      : target.includes("/governance/tasks") ? { items: [], next_after: null }
      : target.includes("/governance/missions") ? { items: [], next_after: null }
      : target.includes("/governance/sources") ? { items: [SCENE], next_after: null }
      : target.includes("/ontology/method-map") ? methodMapAll()
      : target.includes("/ontology/catalog") ? ontologyCatalog()
      : target.endsWith("/session") ? { identity: { ...session.identity, scope_id: "s", auth_epoch: 1,
                                                    assignments: [{ role: "CEO", domain_id: "d", assignment_id: "a" }] },
                                       csrf: "c" }
      : target.includes("/overview") ? overview() : detail()
    return Promise.resolve(new Response(JSON.stringify(body),
      { status: 200, headers: { "Content-Type": "application/json" } }))
  }))
  render(<GovernanceApp />)
  fireEvent.click(await screen.findByRole("button", { name: "独立来源" }))
  await screen.findByTestId("source-scenes")
  expect(await screen.findByTestId(`source-scene-${SCENE.scene_id}`)).toBeInTheDocument()
})

it("submits a draft whose citation is derived from the chosen readable segment", async () => {
  const { prepare } = setup()
  await screen.findByTestId(`source-scene-${SCENE.scene_id}`)
  fireEvent.click(screen.getByTestId(`source-scene-${SCENE.scene_id}`))
  await screen.findByTestId("source-list")
  fireEvent.click(screen.getByTestId("source-action-followup_draft"))
  fireEvent.change(await screen.findByLabelText("草稿标题"), { target: { value: "会后跟进" } })
  fireEvent.click(screen.getByRole("button", { name: "添加条目" }))
  fireEvent.change(screen.getByLabelText("条目类型-0"), { target: { value: "request" } })
  fireEvent.change(screen.getByLabelText("引用片段-0"), {
    target: { value: "22222222-2222-2222-2222-222222222222|33333333-3333-3333-3333-333333333333|" + "a".repeat(64) + "|0" } })
  fireEvent.change(screen.getAllByRole("textbox").find((input) =>
    (input as HTMLInputElement).closest("div")?.textContent?.startsWith("文本")) as HTMLInputElement,
    { target: { value: "请补充材料" } })
  fireEvent.click(screen.getByRole("button", { name: "准备并预览" }))
  await waitFor(() => expect(prepare).toHaveBeenCalledTimes(1))
  const event = (prepare.mock.calls[0][0] as Record<string, unknown>).event as Record<string, unknown>
  expect(event.items).toEqual([{ item_kind: "request", text: "请补充材料",
    citations: [{ source_id: "22222222-2222-2222-2222-222222222222",
                  version_event_id: "33333333-3333-3333-3333-333333333333",
                  payload_hash: "a".repeat(64), segment_index: 0, quote: "原文片段" }] }])
})

it("labels the exact-share grantee with the scene member name when available", async () => {
  setup()
  await screen.findByTestId(`source-scene-${SCENE.scene_id}`)
  fireEvent.click(screen.getByTestId(`source-scene-${SCENE.scene_id}`))
  await screen.findByTestId("source-list")
  fireEvent.click(screen.getByTestId("source-action-source_share"))
  const option = await screen.findByRole("option", { name: /合成 DRI/ })
  expect(option).toBeInTheDocument()
  expect(screen.queryByLabelText(/payload_hash/)).not.toBeInTheDocument()
})

it("disables actions the current role cannot perform and shows readable evidence", async () => {
  setup()
  await screen.findByTestId(`source-scene-${SCENE.scene_id}`)
  fireEvent.click(screen.getByTestId(`source-scene-${SCENE.scene_id}`))
  await screen.findByTestId("source-list")
  expect(screen.getByTestId("source-action-source_correct")).toBeDisabled()
  expect(screen.getByTestId("source-action-source_version")).toBeEnabled()
  fireEvent.click(screen.getByText(/可读原文/))
  expect(screen.getByTestId("source-segments-33333333-3333-3333-3333-333333333333"))
    .toHaveTextContent("原文片段")
  expect(screen.getByTestId("source-drafts")).toHaveTextContent("“本人答应补材料”")
})

it("ignores a late detail response after switching scenes", async () => {
  const A = { ...SCENE, scene_id: "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa", title: "场景 A" }
  const B = { ...SCENE, scene_id: "bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb", title: "场景 B" }
  const deferred: { resolve?: (value: Response) => void } = {}
  vi.stubGlobal("fetch", vi.fn((input: RequestInfo | URL) => {
    const target = String(input)
    if (target.includes(A.scene_id)) {
      return new Promise<Response>((resolve) => { deferred.resolve = resolve })
    }
    const body = target.includes(B.scene_id) ? { ...DETAIL, scene: { ...DETAIL.scene, title: "场景 B" } }
      : { items: [A, B], next_after: null }
    return Promise.resolve(new Response(JSON.stringify(body), { status: 200,
      headers: { "Content-Type": "application/json" } }))
  }))
  render(<SourceScenes session={session} prepare={vi.fn()} onError={vi.fn()} />)
  fireEvent.click(await screen.findByTestId(`source-scene-${A.scene_id}`))
  fireEvent.click(await screen.findByRole("button", { name: "← 返回场景列表" }))
  fireEvent.click(await screen.findByTestId(`source-scene-${B.scene_id}`))
  await screen.findByText("场景 B")
  deferred.resolve?.(new Response(JSON.stringify({ ...DETAIL, scene: { ...DETAIL.scene, title: "场景 A" } }),
    { status: 200, headers: { "Content-Type": "application/json" } }))
  await new Promise((resolve) => setTimeout(resolve, 30))
  expect(screen.getByText("场景 B")).toBeInTheDocument()
  expect(screen.queryByText("场景 A")).not.toBeInTheDocument()
})

it("removes the named focus listener on unmount", async () => {
  const add = vi.spyOn(window, "addEventListener")
  const remove = vi.spyOn(window, "removeEventListener")
  vi.stubGlobal("fetch", vi.fn((input: RequestInfo | URL) => {
    const target = String(input)
    const body = target.includes("/governance/sources") ? { items: [SCENE], next_after: null } : DETAIL
    return Promise.resolve(new Response(JSON.stringify(body),
      { status: 200, headers: { "Content-Type": "application/json" } }))
  }))
  const view = render(<SourceScenes session={session} prepare={vi.fn()} onError={vi.fn()} />)
  await screen.findByTestId(`source-scene-${SCENE.scene_id}`)
  view.unmount()
  const added = add.mock.calls.filter(([name]) => name === "focus").length
  const removed = remove.mock.calls.filter(([name]) => name === "focus").length
  expect(removed).toBe(added)
})

it("re-reads the Context snapshot under current authorization after a commit", async () => {
  let contextReads = 0
  vi.stubGlobal("fetch", vi.fn((input: RequestInfo | URL) => {
    const target = String(input)
    if (target.includes("/governance/sources/contexts/")) {
      contextReads += 1
      return Promise.resolve(new Response(JSON.stringify({ context_id: "ctx-1",
        items: [{ status: "withheld", source_id: "22222222-2222-2222-2222-222222222222" }] }),
        { status: 200, headers: { "Content-Type": "application/json" } }))
    }
    const body = target.endsWith(SCENE.scene_id) ? DETAIL : { items: [SCENE], next_after: null }
    return Promise.resolve(new Response(JSON.stringify(body),
      { status: 200, headers: { "Content-Type": "application/json" } }))
  }))
  const prepare = vi.fn(async (_body: Record<string, unknown>) => undefined)
  render(<SourceScenes session={session} prepare={prepare} onError={vi.fn()} />)
  fireEvent.click(await screen.findByTestId(`source-scene-${SCENE.scene_id}`))
  await screen.findByTestId("source-list")
  fireEvent.change(screen.getByLabelText("Context 快照编号"), { target: { value: "ctx-1" } })
  // Typing a snapshot id already triggers the authorized read; the explicit
  // button remains available for a manual refresh.
  await waitFor(() => expect(contextReads).toBeGreaterThanOrEqual(1))
  fireEvent.click(screen.getByRole("button", { name: "读取" }))
  fireEvent.click(screen.getByTestId("source-action-source_add"))
  fireEvent.change(await screen.findByLabelText("来源系统"), { target: { value: "feishu" } })
  fireEvent.change(screen.getByLabelText("来源 ID"), { target: { value: "doc-1" } })
  fireEvent.change(screen.getByLabelText("标题"), { target: { value: "来源" } })
  fireEvent.change(screen.getByLabelText("内容类型"), { target: { value: "text/plain" } })
  fireEvent.change(screen.getByLabelText("获取时间"), { target: { value: "2026-09-17T09:30" } })
  fireEvent.click(screen.getByRole("button", { name: "准备并预览" }))
  await waitFor(() => expect(prepare).toHaveBeenCalledTimes(1))
  await waitFor(() => expect(contextReads).toBeGreaterThanOrEqual(2))
  expect(screen.getByTestId("source-context-view")).toHaveTextContent("正文已撤下")
})

it("distinguishes a failed source list from an empty one", async () => {
  vi.stubGlobal("fetch", vi.fn(() => Promise.resolve(
    new Response(JSON.stringify({ error: { code: "UNAVAILABLE" } }), { status: 500 }))))
  render(<SourceScenes session={session} prepare={vi.fn()} onError={vi.fn()} />)
  await screen.findByTestId("source-list-failed")
  expect(screen.queryByTestId("source-empty")).not.toBeInTheDocument()
})

it("ignores a late Context response from the previous scene", async () => {
  const A = { ...SCENE, scene_id: "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa", title: "场景 A" }
  const B = { ...SCENE, scene_id: "bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb", title: "场景 B" }
  const deferred: { resolve?: (value: Response) => void } = {}
  vi.stubGlobal("fetch", vi.fn((input: RequestInfo | URL) => {
    const target = String(input)
    if (target.includes("/contexts/ctx-a")) {
      return new Promise<Response>((resolve) => { deferred.resolve = resolve })
    }
    if (target.includes("/contexts/ctx-b")) {
      return Promise.resolve(new Response(JSON.stringify({ context_id: "ctx-b", items: [{ status: "available" }] }),
        { status: 200, headers: { "Content-Type": "application/json" } }))
    }
    const body = target.endsWith(A.scene_id) ? { ...DETAIL, scene: { ...DETAIL.scene, title: "场景 A" } }
      : target.endsWith(B.scene_id) ? { ...DETAIL, scene: { ...DETAIL.scene, title: "场景 B" } }
      : { items: [A, B], next_after: null }
    return Promise.resolve(new Response(JSON.stringify(body),
      { status: 200, headers: { "Content-Type": "application/json" } }))
  }))
  render(<SourceScenes session={session} prepare={vi.fn()} onError={vi.fn()} />)
  fireEvent.click(await screen.findByTestId(`source-scene-${A.scene_id}`))
  fireEvent.change(await screen.findByLabelText("Context 快照编号"), { target: { value: "ctx-a" } })
  fireEvent.click(screen.getByRole("button", { name: "读取" }))
  fireEvent.click(screen.getByRole("button", { name: "← 返回场景列表" }))
  fireEvent.click(await screen.findByTestId(`source-scene-${B.scene_id}`))
  fireEvent.change(await screen.findByLabelText("Context 快照编号"), { target: { value: "ctx-b" } })
  fireEvent.click(screen.getByRole("button", { name: "读取" }))
  await screen.findByTestId("source-context-view")
  deferred.resolve?.(new Response(JSON.stringify({ context_id: "ctx-a", items: [{ status: "withheld" }] }),
    { status: 200, headers: { "Content-Type": "application/json" } }))
  await new Promise((resolve) => setTimeout(resolve, 30))
  expect(screen.queryByText(/ctx-a/)).not.toBeInTheDocument()
})

function setupWithDetail(detailValue: unknown) {
  vi.stubGlobal("fetch", vi.fn((input: RequestInfo | URL) => {
    const target = String(input)
    const body = target.includes("/governance/sources") && target.endsWith(SCENE.scene_id)
      ? detailValue : { items: [SCENE], next_after: null }
    return Promise.resolve(new Response(JSON.stringify(body),
      { status: 200, headers: { "Content-Type": "application/json" } }))
  }))
  const prepare = vi.fn(async (_body: Record<string, unknown>) => undefined)
  render(<SourceScenes session={session} prepare={prepare} onError={vi.fn()} />)
  return { prepare }
}

it("derives Context items from a readable source-version selector", async () => {
  const { prepare } = setupWithDetail(DETAIL)
  fireEvent.click(await screen.findByTestId(`source-scene-${SCENE.scene_id}`))
  await screen.findByTestId("source-list")
  fireEvent.click(screen.getByTestId("source-action-context_create"))
  fireEvent.change(screen.getByLabelText("Context 用途"), { target: { value: "准备跟进" } })
  fireEvent.click(screen.getByRole("button", { name: "添加一项" }))
  fireEvent.change(screen.getByLabelText("item-0-version"), {
    target: { value: "22222222-2222-2222-2222-222222222222|33333333-3333-3333-3333-333333333333|" + "a".repeat(64) } })
  fireEvent.click(screen.getByRole("button", { name: "准备并预览" }))
  await waitFor(() => expect(prepare).toHaveBeenCalledTimes(1))
  expect((prepare.mock.calls[0][0] as Record<string, unknown>).items).toEqual([
    { source_id: "22222222-2222-2222-2222-222222222222",
      version_event_id: "33333333-3333-3333-3333-333333333333", payload_hash: "a".repeat(64) }])
})

it("disables Context creation when no readable version exists", async () => {
  const noVersions = { ...DETAIL, sources: [{ ...DETAIL.sources[0], versions: [
    { ...DETAIL.sources[0].versions[0], status: "withdrawn", segments: null }] }] }
  setupWithDetail(noVersions)
  fireEvent.click(await screen.findByTestId(`source-scene-${SCENE.scene_id}`))
  await screen.findByTestId("source-list")
  fireEvent.click(screen.getByTestId("source-action-context_create"))
  fireEvent.click(screen.getByRole("button", { name: "添加一项" }))
  expect(await screen.findByTestId("context-no-readable")).toBeInTheDocument()
  expect(screen.getByLabelText("item-0-version")).toBeDisabled()
})

it("filters correction version options to the selected source", async () => {
  const second = { ...DETAIL.sources[0], source_id: "99999999-9999-9999-9999-999999999999", title: "另一来源" }
  setupWithDetail({ ...DETAIL, sources: [...DETAIL.sources, second],
    operations: [...DETAIL.operations.filter((op) => op.kind !== "source_correct"),
                 { kind: "source_correct", allowed: true, reason: null }] })
  fireEvent.click(await screen.findByTestId(`source-scene-${SCENE.scene_id}`))
  await screen.findByTestId("source-list")
  fireEvent.click(screen.getByTestId("source-action-source_correct"))
  fireEvent.change(await screen.findByLabelText("来源"), {
    target: { value: "22222222-2222-2222-2222-222222222222" } })
  const options = Array.from((screen.getByLabelText(/被更正版本/) as HTMLSelectElement).options)
    .map((option) => option.label)
  expect(options.some((label) => label.includes("主会转写"))).toBe(true)
  expect(options.some((label) => label.includes("另一来源"))).toBe(false)
})

it("keeps the source form and inputs when prepare rejects", async () => {
  const { prepare } = setup()
  ;(prepare as unknown as { mockImplementation: (fn: () => Promise<void>) => void }).mockImplementation(
    async () => { throw new Error("prepare rejected") })
  fireEvent.click(await screen.findByTestId(`source-scene-${SCENE.scene_id}`))
  await screen.findByTestId("source-list")
  fireEvent.click(screen.getByTestId("source-action-source_add"))
  fireEvent.change(await screen.findByLabelText("来源系统"), { target: { value: "feishu" } })
  fireEvent.change(screen.getByLabelText("来源 ID"), { target: { value: "doc-x" } })
  fireEvent.change(screen.getByLabelText("标题"), { target: { value: "来源" } })
  fireEvent.change(screen.getByLabelText("内容类型"), { target: { value: "text/plain" } })
  fireEvent.change(screen.getByLabelText("获取时间"), { target: { value: "2026-09-17T09:30" } })
  fireEvent.click(screen.getByRole("button", { name: "准备并预览" }))
  await waitFor(() => expect(prepare).toHaveBeenCalledTimes(1))
  expect(screen.getByTestId("source-form-source_add")).toBeInTheDocument()
  expect(screen.getByLabelText("来源系统")).toHaveValue("feishu")
})
