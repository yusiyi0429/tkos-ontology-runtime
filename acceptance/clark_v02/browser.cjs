/* Real browser acceptance. No business writes bypass the Clark UI.
 * Supply TKOS_PLAYWRIGHT_MODULE for an existing Playwright install, not a new
 * production dependency. Access codes stay inside this process and private file.
 */
const fs = require("node:fs");
const path = require("node:path");
const assert = require("node:assert/strict");
const { chromium } = require(process.env.TKOS_PLAYWRIGHT_MODULE || "playwright");
const [stateFile, cdp, phase = "delivery"] = process.argv.slice(2);
const state = JSON.parse(fs.readFileSync(stateFile, "utf8"));
const codes = JSON.parse(fs.readFileSync(state.codes_file, "utf8"));
const output = path.join(state.report_directory, "browser");
fs.mkdirSync(output, { recursive: true });
const reportFile = path.join(output, "report.json");
const report = fs.existsSync(reportFile) ? JSON.parse(fs.readFileSync(reportFile, "utf8")) : { groups: [], actions: [], screenshots: [], testOnly: true, released: false, productionDeployed: false };
let browser, page;
function save() { fs.writeFileSync(reportFile, JSON.stringify(report, null, 2) + "\n"); }
async function readObject(id) { const r = await page.request.get(`${state.clark_url}/api/runtime/objects/${id}`); assert.equal(r.status(), 200); return r.json(); }
async function login(actor) {
  await page.goto(`${state.clark_url}/delivery`);
  const current = await page.request.get(`${state.clark_url}/api/runtime/session`);
  if (current.status() === 200 && (await current.json()).actor.id === actor) {
    await page.getByRole("button", { name: "退出", exact: true }).waitFor();
    return;
  }
  if (current.status() === 200) await page.getByRole("button", { name: "退出", exact: true }).click();
  await page.getByLabel("个人访问码", { exact: true }).fill(codes[actor]);
  await page.getByRole("button", { name: "进入交付工作台", exact: true }).click();
  await page.getByRole("button", { name: "退出", exact: true }).waitFor();
  const session = await (await page.request.get(`${state.clark_url}/api/runtime/session`)).json();
  assert.equal(session.actor.id, actor);
}
async function ready() { await page.getByRole("heading", { name: "Clark 联调：浏览器交付闭环", exact: true }).waitFor(); }
async function act(name, action) {
  const response = page.waitForResponse((r) => r.url().endsWith("/api/runtime/actions") && r.request().method() === "POST" && r.request().postDataJSON().action_type === action);
  await page.getByRole("button", { name, exact: true }).click();
  const r = await response;
  const body = await r.json();
  assert.equal(r.status(), 200, `${action} returned ${r.status()} ${body.error?.code || ""}`);
  report.actions.push({ action_type: action, receipt_id: body.receipt_id, actor_id: body.actor_id });
  await page.getByRole("button", { name: "退出", exact: true }).waitFor({ state: "visible" });
  await page.waitForFunction(() => ![...document.querySelectorAll("button")].find((b) => b.textContent.trim() === "退出")?.disabled);
  save();
  return body;
}
async function upload(name, content) {
  const response = page.waitForResponse((r) => r.url().endsWith("/api/runtime/evidence-assets") && r.request().method() === "POST");
  await page.getByLabel("上传证据文件", { exact: true }).setInputFiles({ name, mimeType: "text/plain", buffer: Buffer.from(content) });
  const r = await response;
  assert.equal(r.status(), 200);
  await page.getByRole("link", { name, exact: true }).waitFor();
  return r.json();
}
async function screen(name, mobile = false) {
  await page.setViewportSize(mobile ? { width: 390, height: 844 } : { width: 1440, height: 1080 });
  await page.screenshot({ path: path.join(output, `${name}.png`), fullPage: true });
  report.screenshots.push(`${name}.png`); save();
}
async function judgments(expected) {
  const session = await (await page.request.get(`${state.clark_url}/api/runtime/session`)).json();
  const item = session.workItems.find((x) => x.title === "Clark 联调：浏览器交付闭环");
  assert.ok(item);
  const [work, outcome, mf] = await Promise.all([readObject(item.objectId), readObject(item.outcomeObjectId), readObject(item.feedbackObjectId)]);
  assert.deepEqual([work.lifecycle_status, outcome.outcome_achievement, mf.lifecycle_status], expected);
  return { work, outcome, mf };
}
async function review(accepted) {
  await page.getByLabel("标准 1 核验结果", { exact: true }).selectOption(accepted ? "passed" : "failed");
  await page.getByLabel("标准 1 核验说明", { exact: true }).fill(accepted ? "原始来源已完整补充并逐项核对。" : "缺少原始来源，请补齐后重新提交。");
  await page.getByLabel("标准 2 核验结果", { exact: true }).selectOption("passed");
  await page.getByLabel("标准 2 核验说明", { exact: true }).fill("现有数字已与材料逐项对应核验。");
  await page.getByLabel("整体评审意见", { exact: true }).fill(accepted ? "两项冻结标准均通过，接受当前 v2 交付版本。" : "请补充原始数据，保留现有核验说明后提交 v2。");
  return act(accepted ? "确认交付通过" : "退回补充", "review_deliverable");
}
async function deliver() {
  await login("mission_dri"); await ready();
  const initialSession = await (await page.request.get(`${state.clark_url}/api/runtime/session`)).json();
  const item = initialSession.workItems.find((x) => x.title === "Clark 联调：浏览器交付闭环");
  const initial = await readObject(item.objectId);
  if (initial.lifecycle_status !== "submitted") {
    if (initial.lifecycle_status === "offered") await act("我已理解并承接", "accept_work_item");
    else assert.equal(initial.lifecycle_status, "in_progress");
    await upload("browser-delivery-v1.txt", "Synthetic browser delivery v1: summary 72; raw sources omitted.\n");
    await page.getByLabel("交付标题", { exact: true }).fill("浏览器提交 v1：交付核验包");
    await page.getByLabel("交付说明", { exact: true }).fill("已提交初步汇总与指标核验，等待验收人检查原始来源完整性。");
    await page.getByRole("checkbox", { name: /browser-delivery-v1\.txt/ }).check();
    await act("提交 v1，交由验收", "submit_deliverable");
  } else assert.equal(initial.delivery.state.submission_seq, 1, "Only resume after the recorded first browser submission");
  await judgments(["submitted", "not_assessed", "investigating"]);
  await login("verifier"); await ready(); await review(false);
  await judgments(["changes_requested", "not_assessed", "investigating"]);
  await screen("01-v1-returned");
  await login("mission_dri"); await ready();
  await upload("browser-delivery-v2.txt", "Synthetic browser delivery v2: summary 72; original rows and reconciliation supplied.\n");
  await page.getByLabel("交付标题", { exact: true }).fill("浏览器提交 v2：补齐原始来源");
  await page.getByLabel("交付说明", { exact: true }).fill("回应 v1 退回意见，补齐原始数据和逐项对账材料，申请再次验收。");
  await page.getByRole("checkbox", { name: /browser-delivery-v2\.txt/ }).check();
  // Real response-loss fault: upstream commits, browser receives a transport error.
  let lost;
  await page.route("**/api/runtime/actions", async (route) => {
    if (!lost && route.request().postDataJSON().action_type === "submit_deliverable") {
      const response = await route.fetch(); assert.equal(response.status(), 200);
      lost = await response.json();
      await route.abort("failed");
    } else await route.continue();
  });
  await page.getByRole("button", { name: "提交 v2，交由验收", exact: true }).click();
  await page.getByRole("button", { name: "按原请求重试", exact: true }).waitFor();
  await page.unroute("**/api/runtime/actions");
  await page.reload();
  await page.getByRole("button", { name: "按原请求重试", exact: true }).waitFor();
  const retried = await act("按原请求重试", "submit_deliverable");
  assert.equal(retried.receipt_id, lost.receipt_id);
  report.responseLoss = { committed_receipt: lost.receipt_id, replay_receipt: retried.receipt_id, survived_page_reload: true };
  await judgments(["submitted", "not_assessed", "investigating"]);
  await login("verifier"); await ready(); await review(true);
  const result = await judgments(["delivery_accepted", "not_assessed", "investigating"]);
  assert.equal(result.work.delivery.submissions.length, 2);
  assert.deepEqual(result.work.delivery.acceptances.map((x) => x.verification_result), ["changes_requested", "accepted"]);
  await actReadReceipt();
  await screen("02-delivery-accepted-independent");
  report.groups.push({ name: "browser_delivery_v1_return_v2_accept_and_response_loss", status: "passed", work_item_id: item.objectId });
}
async function actReadReceipt() {
  await page.getByRole("button", { name: "重新核对回执", exact: true }).click();
  await page.getByText("已从 Runtime 重新读取此回执。", { exact: true }).waitFor();
}
async function assess() {
  if (phase !== "outcome_resume") await login("ceo");
  await ready();
  for (const value of [72, 80]) {
    const filename = `browser-outcome-${value}.txt`;
    if (!(phase === "outcome_resume" && value === 72)) {
    await upload(filename, `Synthetic independently measured outcome: ${value}; target 80.\n`);
    await page.getByLabel("本次实际指标值", { exact: true }).fill(String(value));
    await page.getByRole("checkbox", { name: new RegExp(filename.replace(".", "\\.")) }).check();
    await act("先记录实测指标", "create_object");
    }
    await page.getByLabel("本次 Outcome 评估结论").selectOption(value === 72 ? "not_achieved" : "achieved");
    await page.getByLabel("评估依据", { exact: true }).fill(value === 72 ? "交付材料已通过，但本期实测为72，尚未达到80的目标。" : "后续独立实测达到80，依据新证据记录目标达成。");
    await act("记录 Outcome 判断", "record_outcome_assessment");
    await judgments(["delivery_accepted", value === 72 ? "not_achieved" : "achieved", "investigating"]);
    await screen(`03-outcome-${value}-mf-open`);
  }
  report.groups.push({ name: "browser_outcome_not_achieved_then_achieved_mf_stays_open", status: "passed" });
}
async function feedback() {
  await login("domain_dri"); await ready();
  await page.getByLabel("处理决策说明（本次按无需调整承诺基线处理）", { exact: true }).fill("已补充来源并完成独立核验；本条反馈无需调整承诺基线，提请单独验收后关闭。");
  await act("记录处理决策", "create_object");
  await act("确认处理决策", "confirm_decision");
  await act("提请独立反馈验收", "request_feedback_acceptance");
  await login("verifier"); await ready();
  const field = page.getByRole("group", { name: "本次反馈验收引用的证据", exact: true });
  await field.getByRole("checkbox").last().check();
  await page.getByRole("checkbox", { name: "我已核验处理决策及证据，可以独立判断反馈处理结果。", exact: true }).check();
  await act("通过反馈验收", "record_acceptance");
  await judgments(["delivery_accepted", "achieved", "awaiting_acceptance"]);
  await login("ceo"); await ready();
  await page.getByLabel("反馈关闭说明", { exact: true }).fill("依据反馈负责人确认的处理决策与独立验收记录，单独关闭本条反馈，不改变交付或Outcome判断。");
  await act("独立关闭反馈", "confirm_closure");
  const result = await judgments(["delivery_accepted", "achieved", "closed"]);
  assert.equal(result.mf.feedback.acceptances.length, 1);
  assert.equal(result.mf.feedback.resolution_decision_revision.revision_id, result.mf.feedback.state.resolution_decision_revision_id);
  await screen("04-three-independent-judgments");
  await screen("05-mobile-390", true);
  const width = await page.evaluate(() => ({ scroll: document.documentElement.scrollWidth, viewport: innerWidth }));
  assert.ok(width.scroll <= width.viewport, `Mobile horizontal overflow ${JSON.stringify(width)}`);
  report.groups.push({ name: "browser_mf_independent_three_person_closure_and_mobile", status: "passed" });
}
(async () => {
  browser = await chromium.connectOverCDP(cdp);
  page = browser.contexts()[0].pages().find((p) => p.url().startsWith(state.clark_url)) || await browser.contexts()[0].newPage();
  page.setDefaultTimeout(20000);
  await page.setViewportSize({ width: 1440, height: 1080 });
  if (phase === "delivery") await deliver();
  else if (phase === "outcome" || phase === "outcome_resume") await assess();
  else if (phase === "mf") await feedback();
  else throw new Error("Unknown phase");
  report.status = report.groups.length >= 3 ? "passed" : "incomplete";
  delete report.failure;
  save(); console.log(`PASS browser ${phase}; groups=${report.groups.length}`);
  await browser.close();
})().catch(async (error) => {
  report.status = "failed"; report.failure = { phase, error: String(error).slice(0, 1800) }; save();
  if (page) await page.screenshot({ path: path.join(output, "failure.png"), fullPage: true }).catch(() => {});
  console.error(`FAIL browser ${phase}: ${String(error).slice(0, 900)}`);
  if (browser) await browser.close().catch(() => {});
  process.exitCode = 1;
});
