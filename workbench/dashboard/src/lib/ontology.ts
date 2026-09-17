/** Curated ontology content for the map view.
 *
 * Every statement here is grounded in the frozen contracts
 * (docs/contracts/tkos-method-0.1|0.2|0.3.md) and the compiled Method
 * registries.  The *registered type directory* itself always comes from the
 * backend catalog endpoint; this module only supplies the business reading of
 * a type.  A registered type without curated content is shown explicitly as
 * undocumented — never filled with guessed text.  Definition items embedded in
 * another object (Battlefield/Capability/Outcome) are described on their owner
 * type and never listed as independent objects.
 */

export type RulesVersion = "0.1" | "0.2" | "0.3" | "0.4"

export const RULES_VERSIONS: RulesVersion[] = ["0.4", "0.3", "0.2", "0.1"]

export function contractVersionOf(rules: RulesVersion): string {
  return `tkos.method/${rules}`
}

export function rulesOfContractVersion(contractVersion: string | null | undefined): RulesVersion | null {
  if (contractVersion === "tkos.method/0.1") return "0.1"
  if (contractVersion === "tkos.method/0.2") return "0.2"
  if (contractVersion === "tkos.method/0.3") return "0.3"
  if (contractVersion === "tkos.method/0.4") return "0.4"
  return null
}

export interface TypeRelation {
  target: string
  label: string
}

export interface TypeInfo {
  definition: string
  keyFacts: string[]
  relations: TypeRelation[]
  lifecycle: string[]
  actors: string
  authority: string
  /** scene = 场景/过程记录；support = 证据或授权底座。 */
  marker?: "scene" | "support"
  embeddedNote?: string
}

export interface Area {
  id: string
  name: string
  hint: string
  types: string[]
}

export interface MapEdge {
  from: string
  to: string
  label: string
}

const BASE_TYPE_INFO: Record<string, TypeInfo> = {
  Strategy: {
    definition: "业务域的当前经营战略。由战略议题经研究、会议、战略协议与更新提案，最终由 CEO 确认后生效；每个业务域最多一个当前正式版本。",
    keyFacts: [
      "0.1/0.2 的战略内含战略地图（战场与能力定义项使用稳定标识）。",
      "0.3 的战略与责任结构配对：系统保存与该战略配对的确切责任结构版本。",
      "被取代的旧战略版本作为历史依据保留，仍生效的旧目标不被改写。",
    ],
    relations: [
      { target: "StrategicArchitecture", label: "配对责任结构（0.3）" },
      { target: "LTCO", label: "展开为长期目标" },
      { target: "StrategyUpdateProposal", label: "由更新提案生效" },
    ],
    lifecycle: ["议题立项", "研究与预审", "会议与纪要", "CEO 确认战略协议", "更新提案与审查", "CEO 确认生效", "被新战略取代（历史保留）"],
    actors: "CEO Agent 起草更新，Co-agent 审查，CEO 本人确认。",
    authority: "当前正式版本以系统当前采用的确切战略版本为准；历史确认与当前采用分别记录。",
  },
  StrategicArchitecture: {
    definition: "战略的责任结构：把战略划分为 Battlefield（战场）与 Capability（能力）定义项，各自记录名称、定义、战略依据、边界、接口与承接 Domain，整体独立版本化。",
    keyFacts: [
      "定义项使用稳定 ID，稳定标识不能从战场改为能力，或从能力改为战场。",
      "内容反向引用确切 Strategy；LTCO/PCO/Mission 引用确切 Architecture 版本。",
      "旧版本通过事件历史恢复，历史目标依据保留。",
    ],
    relations: [
      { target: "Strategy", label: "记录确切战略引用" },
      { target: "LTCO", label: "责任结构依据" },
      { target: "PCO", label: "责任结构依据" },
      { target: "Mission", label: "责任结构依据" },
    ],
    lifecycle: ["相关 DRI/Agent 提出或修订（候选）", "CEO 确认生效", "旧版本经事件历史恢复"],
    actors: "相关当前 DRI 或授权 Agent 提出；CEO 本人确认；战略同步变化时同一事务原子确认一致版本。",
    authority: "以当前生效内容版本且有覆盖该版本的责任结构确认记录为正式；候选不改变正式结构。",
    embeddedNote: "Battlefield 与 Capability 是本对象中使用稳定标识的定义项，不是独立注册对象，没有自己的生命周期。",
  },
  StrategicAgreement: {
    definition: "会议之后由 CEO 本人确认的正式战略共识，是后续战略更新的依据；页面范围止于协议，不自动更新战略。",
    keyFacts: ["以确认时的精确纪要版本为依据。"],
    relations: [{ target: "MeetingMinutes", label: "依据已确认纪要" }, { target: "StrategyUpdateProposal", label: "更新依据" }],
    lifecycle: ["纪要确认", "CEO 确认协议", "作为更新依据"],
    actors: "CEO 本人确认。",
    authority: "正式共识记录；后续战略更新必须另行确认。",
  },
  StrategicJudgment: {
    definition: "域内战略调整的正式产物：CEO 确认战略更新提案时，对提案中每个业务域的调整，在同一事务内创建或修订该域的判断记录，内容引用确切战略版本与责任单元。域内调整不直接改变 M2 承诺。",
    keyFacts: [
      "方向是提案确认产生判断：判断由已确认的更新提案在同一事务内产生或修订，不是判断发起提案。",
      "不自动关闭其他议题或创建执行任务。",
    ],
    relations: [
      { target: "Strategy", label: "记录确切战略引用" },
      { target: "StrategyUpdateProposal", label: "由该提案确认产生" },
    ],
    lifecycle: ["更新提案经审查", "CEO 确认更新", "同一事务产生或修订域内判断"],
    actors: "CEO 本人确认更新提案；判断随确认在同一事务内产生。",
    authority: "正式产物随 CEO 的更新确认生效；域内判断不等于 M2 承诺变化。",
  },
  StrategyUpdateProposal: {
    definition: "CEO Agent 起草的战略更新方案，必须绑定当前目标版本、战略协议与更新方案；确认后保留正式回执；经 Co-agent 审查、CEO 确认后使新战略生效。",
    keyFacts: ["0.3 的公司级提案同时携带责任结构定义，Runtime 在同一事务内分配配对正式版本。"],
    relations: [
      { target: "StrategicAgreement", label: "更新依据" },
      { target: "Strategy", label: "确认后生效" },
    ],
    lifecycle: ["CEO Agent 起草", "Co-agent 审查", "CEO 确认", "同一事务生效"],
    actors: "CEO Agent 起草，Co-agent 审查，CEO 本人确认。",
    authority: "未经 CEO 确认不生效；确认与生效在同一事务。",
  },
  LTCO: {
    definition: "长期经营目标，依据精确战略版本展开（0.3 另引用确切责任结构）。",
    keyFacts: ["周期以记录为准。", "历史确认版本不随后续提案改变。"],
    relations: [
      { target: "Strategy", label: "战略依据" },
      { target: "PCO", label: "承接为当期目标" },
      { target: "LTCOReviewAdvice", label: "审视建议" },
    ],
    lifecycle: ["建议/起草", "修订或退回", "CEO 确认精确版本", "周期复盘与审视"],
    actors: "CEO Agent 起草与修订，CEO 本人确认；0.3 的经营状态由其 CEO Owner 确认。",
    authority: "当前生效内容版本且有覆盖该精确版本的确认记录为正式。",
  },
  PCO: {
    definition: "当期经营目标，承接 LTCO，整组确认生效。",
    keyFacts: [
      "Outcome 是目标内的定义项，记录结果陈述、标准与 DRI。",
      "整组确认同时生效，不启用半套。",
    ],
    relations: [
      { target: "LTCO", label: "承接长期目标" },
      { target: "Mission", label: "拆解为 Mission" },
      { target: "CandidateSet", label: "经候选集合整组确认" },
    ],
    lifecycle: ["Co-agent 起草", "开窗共同核对", "收拢为候选集合", "CEO 整组确认生效"],
    actors: "Co-agent 起草，窗口参与人本人评论，CEO 整组确认；0.3 的 PCO 整体状态由同域当前唯一 CEO 确认，单个 Outcome 状态由其 DRI 确认。",
    authority: "当前生效内容版本且有覆盖该精确版本的整组确认记录为正式。",
    embeddedNote: "Outcome 是本对象内的定义项，不是独立注册对象，没有自己的生命周期。",
  },
  Mission: {
    definition: "承接 PCO 成果条目的任务。只有一个责任人；成果支持关系必须指向所属周期目标确切版本中的真实成果条目。",
    keyFacts: [
      "Mission 没有自己的周期字段：周期随其精确引用的 PCO 版本；交付期限只是期限，不是业务周期。",
      "0.3 的 Mission 另含确切责任结构引用与主要范围标识。",
      "CEO 确认后的正式 Mission 等待执行承接，不因此获得执行授权或验收。",
    ],
    relations: [
      { target: "PCO", label: "承接当期目标成果" },
      { target: "OperatingState", label: "经营状态观察（0.3）" },
    ],
    lifecycle: ["Co-agent 起草", "开窗共同核对", "CEO 整组确认", "待执行承接"],
    actors: "Co-agent 起草，CEO 整组确认；Owner 负责周进展材料但不增加确认权；0.3 的经营状态由 Mission Owner 确认。",
    authority: "正式 Mission 不等于执行授权；执行授权需独立约定。",
  },
  LTCOReviewAdvice: {
    definition: "对长期目标的审视建议，依据周期复盘生成，供 CEO 参考；不直接改变正式 LTCO。",
    keyFacts: [],
    relations: [
      { target: "LTCO", label: "审视对象" },
      { target: "PeriodReview", label: "依据周期复盘" },
    ],
    lifecycle: ["生成建议", "CEO 参考并决定退回或修订"],
    actors: "Co-agent 生成建议。",
    authority: "建议记录，不是正式目标。",
  },
  ReviewWindow: {
    definition: "共同核对窗口：冻结固定目标版本集合与参与人，在独立评论截止时间前收集本人的评论、替代与撤回。",
    keyFacts: [
      "反馈截止时间与业务周期相互独立。",
      "截止后评论、替代、撤回被拒绝；Co-agent 显式关窗。",
      "0.2 起开窗与重开必须指定未来截止时间；需要继续讨论由 CEO 显式重开新窗口。",
    ],
    relations: [{ target: "CandidateSet", label: "关窗收拢为候选" }],
    lifecycle: ["开窗（冻结版本集合）", "本人评论/替代/撤回", "关窗", "收拢"],
    actors: "Co-agent 开窗与关窗，窗口成员本人评论，CEO 重开。",
    authority: "窗口意见是核对记录，不代替正式确认。",
  },
  CandidateSet: {
    definition: "关窗收拢后形成的完整候选版本组（含逐意见取舍记录）；CEO 整组确认后候选目标同时生效，或说明原因按候选版本重开窗口。",
    keyFacts: ["整组同时生效，不启用半套。"],
    relations: [
      { target: "PCO", label: "整组确认生效" },
      { target: "Mission", label: "整组确认生效" },
    ],
    lifecycle: ["收拢生成", "CEO 整组确认或重开"],
    actors: "Co-agent 收拢，CEO 本人整组确认。",
    authority: "候选未确认前不改变任何正式版本。",
  },
  OperatingState: {
    definition: "对 Mission、LTCO、PCO 整体或单个 Outcome 在确切观察时点的经营状态：绿／黄／红／未知、摘要、判断基准与证据。推荐与正式分开；同一目标身份、Outcome 与观察时点只有一个状态身份。",
    keyFacts: [
      "无证据时只能记为未知 并列明数据缺口；未记录状态不推断为正常。",
      "确认人可附理由修正摘要与状态判断，保留原推荐与原始证据。",
      "重新推荐必须引用上次状态建议，防止出现第二个正式状态。",
    ],
    relations: [
      { target: "Mission", label: "观察对象" },
      { target: "LTCO", label: "观察对象" },
      { target: "PCO", label: "观察对象" },
      { target: "OperatingProblem", label: "问题来源" },
    ],
    lifecycle: ["Agent/有权人推荐", "责任人确认（可附理由修正）", "新推荐未确认期间旧正式状态继续有效", "确认后新推荐成为正式（历史确认版本保留）"],
    actors: "授权 Agent 或对象有权人员推荐；Mission 由其 Owner 确认，LTCO 由其 CEO Owner 确认，PCO 整体由同域当前唯一 CEO 确认，PCO Outcome 由其 DRI 确认，均重新检查当前任职。",
    authority: "以当前正式状态的精确版本为准；旧确认版本标为历史确认，新建议不冒充确认结论。",
  },
  OperatingProblem: {
    definition: "来源于正式经营状态的经营问题：核心问题、管理矛盾、重要性、层级、责任任职与证据。同一主体与规范化核心问题复用身份，不做文本相似度合并。",
    keyFacts: [
      "普通关闭区分“已解决”和“无需继续处理”，必须有理由及必要依据。",
      "战略层问题可移交 M1A 候选池；“已移交”不表示已解决。",
    ],
    relations: [
      { target: "OperatingState", label: "来源状态" },
      { target: "StrategicIssue", label: "移交战略立项" },
    ],
    lifecycle: ["开启", "修订（状态来源/分流层级）", "关闭或移交议题"],
    actors: "授权主体按层级责任任职开启与修订；当前绑定责任人关闭，Agent 不代关闭；0.3 的移交由 CEO Agent 立项。",
    authority: "关闭处置绑定精确版本；移交后只在议题跟踪，历史问题保留议题引用。",
  },
  BusinessFact: {
    definition: "独立记录的经营事实：指标、取值、单位、来源与观察时点。修正保留原始记录，不把事实复制进目标正文；主题型事实不自动挂到任何 Mission。",
    keyFacts: [],
    relations: [
      { target: "Mission", label: "事实记录对象" },
      { target: "EvidenceAsset", label: "原始证据" },
    ],
    lifecycle: ["记录", "更正（保留原始引用）"],
    actors: "现有授权身份记录与更正。",
    authority: "记录与更正即产生业务效力：更正公开保留原始引用，原始证据不改写；记录本身不证明事实为真，也不代表任何 Outcome 达成。",
  },
  PeriodReview: {
    definition: "Co-agent 生成的周期复盘分析材料：发现、学习、影响与来源事实；0.3 起必须引用目标的正式经营状态。",
    keyFacts: [],
    relations: [
      { target: "PCO", label: "复盘目标" },
      { target: "OperatingState", label: "引用正式状态（0.3）" },
    ],
    lifecycle: ["生成", "重新生成（保留版本）"],
    actors: "Co-agent 生成与重新生成。",
    authority: "始终是 Agent 分析材料，无需也没有审批；系统采用该材料不等于人工批准。",
  },
  Signal: {
    definition: "多来源采集的信号，仅记录来源；一个信号可支持多个议题。激活与归档是处置，不立题、不授执行权。",
    keyFacts: [
      "0.3 的正式立项由绑定 CEO Agent 执行；信号作为来源保留在候选与议题的来源列表中。",
    ],
    relations: [{ target: "PotentialIssue", label: "升级为候选议题" }],
    lifecycle: ["采集", "激活/归档处置", "作为来源进入候选与议题"],
    actors: "来源贡献身份采集；同域当前 CEO 处置。",
    authority: "仅来源记录。",
  },
  PotentialIssue: {
    definition: "进入候选池的升级建议、直接上报或复盘发现：记录分类、摘要、核心问题与来源引用（来源可指向信号、经营问题、周期复盘或证据）。",
    keyFacts: ["红色紧急度必须附理由；业务范围分类不扩大授权域。"],
    relations: [
      { target: "StrategicIssue", label: "确认为正式议题" },
      { target: "PeriodReview", label: "复盘发现来源" },
    ],
    lifecycle: ["提出", "修订", "确认立题或保留候选"],
    actors: "当前授权来源贡献者提出与修订；0.3 由绑定 CEO Agent 立项。",
    authority: "建议记录，不是正式议题。",
  },
  StrategicIssue: {
    definition: "正式立项的战略议题，驱动研究、会议与战略更新。",
    keyFacts: [
      "业务范围（战略层/战场层）与紧急度（红/黄/灰）是分类，与生命周期分开。",
      "0.3 的经营问题移交与议题关联在同一事务：原问题原子标记已移交并停止原跟踪。",
    ],
    relations: [
      { target: "PotentialIssue", label: "来源候选" },
      { target: "ResearchPlan", label: "指派深入研究" },
      { target: "MeetingRound", label: "会议讨论" },
    ],
    lifecycle: ["立题", "指派研究", "研究与预审", "会议与纪要", "协议与更新判断", "完成"],
    actors: "0.3 由绑定 CEO Agent 立项，CEO 本人随后独立指派研究 DRI 与 Agent。",
    authority: "0.3 的立项 Agent 与研究指派 CEO 分别保存；立项不授予战略确认权。",
  },
  ResearchBrief: {
    definition: "独立版本化的研究分析材料：问题、分析、选项、限制与来源。轻量路径由 CEO 确认材料充分即可进入会议；修改材料使旧确认失效。",
    keyFacts: [],
    relations: [
      { target: "StrategicIssue", label: "研究对象" },
      { target: "MeetingRound", label: "锁定进会议" },
    ],
    lifecycle: ["发布", "CEO 确认材料充分", "修改后旧确认失效"],
    actors: "已指派 CEO Agent 发布；CEO 本人确认。",
    authority: "版本化分析，不形成决定。",
  },
  ResearchMemo: {
    definition: "研究澄清备忘：Memo 与个人 Agent 澄清、双 Agent 校验或 CEO/DRI 直接澄清的记录。",
    keyFacts: [],
    relations: [{ target: "StrategicIssue", label: "澄清记录" }],
    lifecycle: ["发布备忘", "澄清与校验"],
    actors: "沿用 DRI、CEO、Agent 各自职责。",
    authority: "分析与质量检查记录。",
  },
  ResearchPlan: {
    definition: "已指派 DRI 发布的研究计划。",
    keyFacts: [],
    relations: [{ target: "StrategicIssue", label: "研究对象" }],
    lifecycle: ["发布计划", "按计划研究"],
    actors: "已指派 DRI 发布。",
    authority: "研究过程记录。",
  },
  ResearchReport: {
    definition: "研究报告：报告修订与 CEO Agent 预审；报告版本变化不能复用旧预审。",
    keyFacts: [],
    relations: [{ target: "StrategicIssue", label: "研究对象" }],
    lifecycle: ["提交", "修订", "CEO Agent 预审"],
    actors: "DRI 提交，CEO Agent 预审。",
    authority: "分析材料；预审绑定精确版本。",
  },
  MeetingMinutes: {
    definition: "双 Agent 分别起草并收拢的会议纪要，DRI 本人确认最终纪要；不自动形成战略协议。",
    keyFacts: ["材料修改后旧确认失效。"],
    relations: [
      { target: "MeetingRound", label: "所属会议" },
      { target: "StrategicAgreement", label: "确认后形成协议依据" },
    ],
    lifecycle: ["双 Agent 起草", "差异核对收拢", "DRI 确认最终纪要"],
    actors: "双 Agent 起草与收拢，DRI 本人确认。",
    authority: "精确最终纪要；纪要、协议、更新分别确认。",
  },
  MeetingRound: {
    definition: "会议轮次及其材料：已指派 DRI 开会，锁定轻量已确认材料或深入研究的已预审报告；会议目标未达成可再次讨论。",
    keyFacts: ["会议场景记录（已阅/开始/结束/发布）只记录协作过程，不产生正式决定。"],
    relations: [
      { target: "StrategicIssue", label: "讨论议题" },
      { target: "MeetingMinutes", label: "产生纪要" },
    ],
    lifecycle: ["发起会议", "开会讨论", "发布材料"],
    actors: "已指派 DRI 本人发起。",
    authority: "场景与过程记录，不是正式决定。",
    marker: "scene",
  },
  MethodRun: {
    definition: "Agent 的 M1A 方法运行：运行关联、步骤尝试、暂停与恢复；是研究 Context 读取的授权底座——只有运行中的 MethodRun 才能读取研究 Context。0.3 起绑定 CEO Agent 可以自主开启 M1A 战略接收运行。",
    keyFacts: [
      "运行内容只有名称与方法标识；与议题等根对象的关联由专门的运行关联动作记录，不写在运行内容里。",
      "研究快照恢复时重新核验同一 Agent、当前授权与运行中状态。",
    ],
    relations: [{ target: "StrategicIssue", label: "关联研究根对象" }],
    lifecycle: ["开启运行", "暂停/恢复", "完成"],
    actors: "CEO / CEO Agent 创建本人运行；CEO 保留暂停、恢复与研究指派权。",
    authority: "授权与恢复底座，不产生业务决定。",
    marker: "support",
  },
  EvidenceAsset: {
    definition: "原始证据文件：上传时记录内容校验值与版本，下载按当前权限并核验内容；证据内容不可变。",
    keyFacts: [],
    relations: [{ target: "BusinessFact", label: "事实证据" }],
    lifecycle: ["上传", "被引用"],
    actors: "现有授权身份上传。",
    authority: "证据与授权底座；事实、状态与材料的原始依据。",
    marker: "support",
  },
}

/** Per-version content overrides; merged shallowly over the base entry. */
const VERSION_OVERRIDES: Record<string, Partial<Record<RulesVersion, Partial<TypeInfo>>>> = {
  Strategy: {
    "0.1": {
      keyFacts: [
        "本版本的战略内含战略地图（战场与能力定义项使用稳定标识），地图与战略在同一版本和事务内生效。",
        "被取代的旧战略版本作为历史依据保留，仍生效的旧目标不被改写。",
      ],
      relations: [
        { target: "LTCO", label: "展开为长期目标" },
        { target: "StrategyUpdateProposal", label: "由更新提案生效" },
      ],
    },
    "0.2": {
      keyFacts: [
        "本版本的战略内含战略地图（战场与能力定义项使用稳定标识）。",
        "被取代的旧战略版本作为历史依据保留，仍生效的旧目标不被改写。",
      ],
      relations: [
        { target: "LTCO", label: "展开为长期目标" },
        { target: "StrategyUpdateProposal", label: "由更新提案生效" },
      ],
    },
  },
  StrategicIssue: {
    "0.1": {
      keyFacts: [],
      actors: "CEO 本人确认候选议题立题。",
      authority: "仅 CEO 本人的确认产生正式议题。",
    },
    "0.2": {
      keyFacts: ["业务范围与紧急度是分类，与生命周期分开；红色紧急度必须附理由。"],
      actors: "CEO 本人确认立题；CEO 直接创建议题（不经候选池升级）须同时记录确认理由，不补造 Signal。",
      authority: "仅 CEO 本人的确认产生正式议题。",
    },
  },
  Signal: {
    "0.1": {
      definition: "多来源采集的信号，仅记录来源；一个信号可支持多个议题。0.1 仅有采集与来源记录，没有处置动作。",
      keyFacts: [],
      lifecycle: ["采集", "作为来源支持候选议题"],
      actors: "来源贡献身份采集。",
    },
    "0.2": {
      keyFacts: ["转化为议题只能由 CEO 本人确认真实议题派生，不接受单独标记转换的请求。"],
      lifecycle: ["采集", "激活/归档处置", "升级建议", "转化（由 CEO 确认立题派生）"],
    },
  },
  PotentialIssue: {
    "0.1": {
      definition: "进入候选池的升级建议，记录标题、摘要与来源信号。",
      keyFacts: [],
      relations: [{ target: "StrategicIssue", label: "确认为正式议题" }],
      actors: "当前授权来源贡献者提出与修订；CEO 本人确认立题。",
    },
    "0.2": {
      definition: "进入候选池的升级建议、直接上报或复盘发现，记录来源类别（直接上报/信号升级/复盘发现）与来源引用。",
      actors: "当前授权来源贡献者提出与修订；CEO 本人确认立题。",
    },
  },
  MethodRun: {
    "0.1": {
      definition: "Agent 的 M1A 方法运行：运行关联、步骤尝试、暂停与恢复；是研究 Context 读取的授权底座——只有运行中的 MethodRun 才能读取研究 Context。",
      keyFacts: ["研究快照恢复时重新核验同一 Agent、当前授权与运行中状态。"],
      actors: "CEO 本人创建运行并管理暂停与恢复，Agent 在运行授权范围内研究。",
    },
    "0.2": {
      definition: "Agent 的 M1A 方法运行：运行关联、步骤尝试、暂停与恢复；是研究 Context 读取的授权底座——只有运行中的 MethodRun 才能读取研究 Context。",
      keyFacts: ["研究快照恢复时重新核验同一 Agent、当前授权与运行中状态。"],
      actors: "CEO 本人创建运行并管理暂停与恢复，Agent 在运行授权范围内研究。",
    },
  },
  LTCO: {
    "0.1": {
      relations: [
        { target: "Strategy", label: "战略依据" },
        { target: "PCO", label: "承接为当期目标" },
        { target: "LTCOReviewAdvice", label: "审视建议" },
      ],
      actors: "CEO Agent 起草与修订，CEO 本人确认。",
    },
    "0.2": {
      relations: [
        { target: "Strategy", label: "战略依据" },
        { target: "PCO", label: "承接为当期目标" },
        { target: "LTCOReviewAdvice", label: "审视建议" },
      ],
      actors: "CEO Agent 起草与修订，CEO 本人确认。",
    },
  },
  PCO: {
    "0.1": {
      actors: "Co-agent 起草，窗口参与人本人评论，CEO 整组确认。",
    },
    "0.2": {
      actors: "Co-agent 起草，窗口参与人本人评论，CEO 整组确认。",
    },
  },
  Mission: {
    "0.1": {
      keyFacts: [
        "Mission 没有自己的周期字段：周期随其精确引用的 PCO 版本；交付期限只是期限，不是业务周期。",
        "CEO 确认后的正式 Mission 等待执行承接，不因此获得执行授权或验收。",
      ],
      relations: [{ target: "PCO", label: "承接当期目标成果" }],
      actors: "Co-agent 起草，CEO 整组确认；Owner 负责周进展材料但不增加确认权。",
    },
    "0.2": {
      keyFacts: [
        "Mission 没有自己的周期字段：周期随其精确引用的 PCO 版本；交付期限只是期限，不是业务周期。",
        "CEO 确认后的正式 Mission 等待执行承接，不因此获得执行授权或验收。",
      ],
      relations: [{ target: "PCO", label: "承接当期目标成果" }],
      actors: "Co-agent 起草，CEO 整组确认；Owner 负责周进展材料但不增加确认权。",
    },
  },
  ReviewWindow: {
    "0.1": {
      definition: "共同核对窗口：冻结固定目标版本集合与参与人，收集本人的评论、替代与撤回。",
      keyFacts: ["窗口关闭后评论、替代、撤回被拒绝；Co-agent 显式关窗。"],
    },
  },
  PeriodReview: {
    "0.1": {
      definition: "Co-agent 生成的周期复盘分析材料：发现、学习、影响与来源事实。",
      relations: [{ target: "PCO", label: "复盘目标" }],
    },
    "0.2": {
      definition: "Co-agent 生成的周期复盘分析材料：发现、学习、影响与来源事实。",
      relations: [{ target: "PCO", label: "复盘目标" }],
    },
  },
}

export function typeInfo(type: string, rules: RulesVersion): TypeInfo | null {
  const base = BASE_TYPE_INFO[type]
  if (!base) return null
  // 0.4 has its own compiled object set and curated reading; a type that only
  // exists in 0.1-0.3 must never be labeled with the old rules under 0.4.
  if (rules === "0.4" && !V04_TYPES.includes(type)) return null
  const override = rules === "0.4" ? V04_OVERRIDES[type] : VERSION_OVERRIDES[type]?.[rules]
  return override ? { ...base, ...override } : base
}

const AREAS: Area[] = [
  { id: "strategy-structure", name: "战略与责任结构", hint: "战略共识、更新与责任划分",
    types: ["Strategy", "StrategicArchitecture", "StrategicAgreement", "StrategicJudgment", "StrategyUpdateProposal"] },
  { id: "objectives", name: "目标与 Mission", hint: "长期目标、当期目标与任务承接",
    types: ["LTCO", "PCO", "Mission", "LTCOReviewAdvice"] },
  { id: "joint-review", name: "共同核对", hint: "窗口、评论与整组确认",
    types: ["ReviewWindow", "CandidateSet"] },
  { id: "operations", name: "经营与复盘", hint: "状态、事实、复盘与问题",
    types: ["OperatingState", "BusinessFact", "PeriodReview", "OperatingProblem"] },
  { id: "research", name: "战略研究", hint: "信号、议题、研究与会议",
    types: ["Signal", "PotentialIssue", "StrategicIssue", "ResearchBrief", "ResearchMemo",
            "ResearchPlan", "ResearchReport", "MeetingMinutes", "MeetingRound"] },
  { id: "support", name: "共同支撑", hint: "证据、身份与授权底座，不构成业务层级",
    types: ["EvidenceAsset", "MethodRun"] },
]

/** Business-labeled relations between registered types.  Endpoints that are
 *  not registered in the selected rule version are dropped by the caller. */
const MAP_EDGES: Array<MapEdge & { versions?: RulesVersion[] }> = [
  { from: "Signal", to: "PotentialIssue", label: "升级为候选议题" },
  { from: "PotentialIssue", to: "StrategicIssue", label: "确认立题" },
  { from: "StrategicIssue", to: "StrategicAgreement", label: "进入正式人类共识", versions: ["0.4"] },
  { from: "ReviewWindow", to: "LTCO", label: "冻结确切长期结果集合", versions: ["0.4"] },
  { from: "StrategicIssue", to: "ResearchPlan", label: "指派深入研究" },
  { from: "StrategicIssue", to: "ResearchBrief", label: "指派研究（轻量）", versions: ["0.2", "0.3"] },
  { from: "ResearchMemo", to: "StrategicIssue", label: "澄清记录" },
  { from: "ResearchReport", to: "StrategicIssue", label: "预审报告" },
  { from: "ResearchBrief", to: "MeetingRound", label: "锁定已确认材料", versions: ["0.2", "0.3"] },
  { from: "MeetingRound", to: "MeetingMinutes", label: "双 Agent 起草纪要" },
  { from: "MeetingMinutes", to: "StrategicAgreement", label: "CEO 确认共识" },
  { from: "StrategicAgreement", to: "StrategyUpdateProposal", label: "更新依据" },
  { from: "StrategyUpdateProposal", to: "StrategicJudgment", label: "确认产生域内判断" },
  { from: "StrategyUpdateProposal", to: "Strategy", label: "CEO 确认生效" },
  { from: "Strategy", to: "StrategicArchitecture", label: "配对责任结构", versions: ["0.3", "0.4"] },
  { from: "Strategy", to: "LTCO", label: "展开为长期目标" },
  { from: "StrategicArchitecture", to: "LTCO", label: "责任结构依据", versions: ["0.3", "0.4"] },
  { from: "StrategicArchitecture", to: "PCO", label: "责任结构依据", versions: ["0.3", "0.4"] },
  { from: "StrategicArchitecture", to: "Mission", label: "责任结构依据", versions: ["0.3", "0.4"] },
  { from: "LTCO", to: "PCO", label: "承接为当期目标" },
  { from: "PeriodReview", to: "LTCOReviewAdvice", label: "复盘支撑审视建议" },
  { from: "LTCOReviewAdvice", to: "LTCO", label: "审视建议" },
  { from: "PCO", to: "Mission", label: "拆解为 Mission" },
  { from: "ReviewWindow", to: "CandidateSet", label: "关窗收拢" },
  { from: "CandidateSet", to: "PCO", label: "整组确认生效" },
  { from: "CandidateSet", to: "Mission", label: "整组确认生效" },
  { from: "OperatingState", to: "Mission", label: "经营状态观察", versions: ["0.3", "0.4"] },
  { from: "OperatingState", to: "LTCO", label: "经营状态观察", versions: ["0.3", "0.4"] },
  { from: "OperatingState", to: "PCO", label: "经营状态观察", versions: ["0.3", "0.4"] },
  { from: "OperatingProblem", to: "OperatingState", label: "来源状态", versions: ["0.3", "0.4"] },
  { from: "OperatingProblem", to: "StrategicIssue", label: "移交战略立项", versions: ["0.3", "0.4"] },
  { from: "PeriodReview", to: "OperatingState", label: "引用正式状态", versions: ["0.3", "0.4"] },
  { from: "PeriodReview", to: "PotentialIssue", label: "复盘发现进入候选池", versions: ["0.2", "0.3"] },
  { from: "PeriodReview", to: "PCO", label: "复盘目标" },
  { from: "BusinessFact", to: "Mission", label: "事实记录对象" },
  { from: "EvidenceAsset", to: "BusinessFact", label: "原始证据" },
  { from: "EvidenceAsset", to: "OperatingState", label: "状态证据", versions: ["0.3", "0.4"] },
  { from: "MethodRun", to: "StrategicIssue", label: "Agent 研究运行" },
]

/** Compiled tkos.method/0.4 object types (docs/runtime-method-registry-0.4.json).
 *  Used only as the no-catalog fallback so 0.4 never borrows 0.1-0.3 concepts. */
const V04_TYPES: string[] = [
  "StrategicIssue", "StrategicAgreement", "Strategy", "StrategicArchitecture", "StrategyUpdateProposal",
  "LTCO", "PCO", "Mission", "ReviewWindow", "CandidateSet", "OperatingState",
  "OperatingProblem", "PeriodReview", "MethodRun", "EvidenceAsset",
]

/** 0.4 curated readings.  Only entries that differ from the base entry appear
 *  here; unlisted 0.4 types keep their base reading.  0.1-0.3 never merge
 *  these overrides. */
const V04_OVERRIDES: Record<string, Partial<TypeInfo>> = {
  Strategy: {
    definition: "0.4 的公司战略：与独立版本化的责任结构配对采用。Required Capability 是本战略的分析语义与字段，分配给 Domain 的主要责任，不是独立对象。",
    keyFacts: [
      "成对采用保留未变对象的确切版本并附适用性理由；只有变更对象产生新版本，同一事务原子生效。",
      "旧正式版本与仍生效的历史目标依据保留，不被自动改写。",
    ],
    relations: [
      { target: "StrategicArchitecture", label: "配对责任结构" },
      { target: "StrategyUpdateProposal", label: "由 CEO 确认的提案生效" },
    ],
    lifecycle: ["议题与全体共识", "CEO Agent 确切变更提案", "Co-agent 复核", "CEO 原子确认", "仅变更对象新版本"],
    actors: "CEO Agent 提案，Co-agent 复核，CEO 本人确认；全体人类共识先行成立。",
    authority: "以当前采用的确切战略版本与配对责任结构版本为准。",
  },
  StrategicArchitecture: {
    definition: "0.4 的责任结构：Battlefield（价值创造场域）与 Domain（长期能力责任）各自稳定 ID；Domain 显式映射既有授权域与当前责任人，映射只解释责任、不隐式授权。",
    keyFacts: [
      "Battlefield 与 Domain 都可作为结果的主业务 Scope；Required Capability 仍归 Domain 主要责任。",
      "稳定 ID 不得改变定义项类型；整体独立版本化并与战略成对采用。",
      "未变对象保留确切版本并附适用性理由。",
    ],
    relations: [{ target: "Strategy", label: "与战略配对采用" }],
    actors: "CEO Agent 根据共识提案，Co-agent 复核，CEO 本人确认。",
    authority: "以当前采用的整份责任结构版本为准；字段映射不授予任何权限。",
  },
  StrategicAgreement: {
    definition: "0.4 的正式人类共识：CEO 提名的全部当前人类参与人对同一确切议题版本的共同确认；Agent 只负责起草。",
    keyFacts: [
      "必要参与人必须包括 CEO 本人；任一必要参与人缺失则不成立。",
      "名单、正文或所附证据变化使全部待确认失效，必须重新确认。",
      "正式化重查全部必要签署人的当前任职。",
      "未形成变化时只记录共识，不触发正式更新。",
    ],
    relations: [
      { target: "StrategicIssue", label: "议题依据" },
      { target: "StrategyUpdateProposal", label: "更新依据" },
    ],
    lifecycle: ["CEO Agent 起草", "全体当前人类确认同一版本", "正式成立", "作为更新依据"],
    actors: "CEO 提名参与人；每个当前人类本人确认；Agent 不得代替任何人确认。",
    authority: "全体确认同一确切版本后方正式；历史正式记录保持不变。",
  },
  StrategicIssue: {
    definition: "0.4 的战略议题：由绑定 CEO Agent 使用本人运行直接创建、重新界定或关联；不设候选池前置门槛。",
    keyFacts: [
      "CEO 指定参与人与研究责任；研究与补证按需发生，不强制会议链。",
      "该 scope 尚无正式战略与责任结构对时，议题可以明确缺少现有依据。",
      "重新界定产生新一轮次并保留历史共识与当时的依据引用。",
    ],
    relations: [
      { target: "StrategicAgreement", label: "进入正式人类共识" },
      { target: "StrategyUpdateProposal", label: "形成更新提案" },
    ],
    lifecycle: ["CEO Agent 直接创建/重新界定/关联", "CEO 指定参与人与研究", "全体共识", "正式更新", "完成"],
    actors: "绑定 CEO Agent 立项；CEO 本人指定参与人并确认正式更新。",
    authority: "直接立项按规则生效；正式更新仍需全体共识与 CEO 本人确认。",
  },
  LTCO: {
    definition: "0.4 的 Scope 级长期结果，独立身份与版本，归属唯一主业务 Scope（Domain 或 Battlefield）。",
    keyFacts: [
      "状态责任人为当前 CEO。",
      "主业务 Scope 与依据在候选与确认中保持一致，旧版本作为历史依据保留。",
    ],
    relations: [
      { target: "Strategy", label: "战略依据" },
      { target: "StrategicArchitecture", label: "责任结构依据" },
      { target: "PCO", label: "展开为周期结果" },
    ],
    lifecycle: ["CEO Agent 起草", "CEO 本人确认", "作为周期结果的确切父级", "周期复盘与审视"],
    actors: "CEO Agent 起草；CEO 本人确认并承担状态责任。",
    authority: "当前生效版本以覆盖该确切版本的确认记录为准。",
  },
  PCO: {
    definition: "0.4 的周期结果：唯一主业务 Scope、确切父级长期结果与确切责任结构；状态责任人为该 Scope 的当前责任人。",
    keyFacts: [
      "同一公司周期的评审窗口冻结完整的周期结果与必要结果单元成员集合与版本。",
      "候选内容变化使已收集的本人承诺失效，需重新确认。",
    ],
    relations: [
      { target: "LTCO", label: "确切父级" },
      { target: "StrategicArchitecture", label: "责任结构依据" },
      { target: "Mission", label: "拆解为必要结果单元" },
      { target: "CandidateSet", label: "整组激活" },
    ],
    lifecycle: ["Co-agent 起草", "窗口共同核对", "候选与本人承诺", "CEO 最后整组激活"],
    actors: "Co-agent 起草；窗口参与人本人评论；具名 Scope 责任人承诺本人责任；CEO 最后整组激活。",
    authority: "整组原子激活；不产生执行授权或验收。",
  },
  Mission: {
    definition: "0.4 的独立结果单元：唯一 Owner（可以是责任人以外的人）、唯一主业务 Scope、确切父级周期结果、为什么、标准、证据与时间。",
    keyFacts: [
      "Owner 本人在确切候选集合中承诺本人责任，并承担状态责任。",
      "正式结果不等于执行授权或验收。",
    ],
    relations: [
      { target: "PCO", label: "确切父级" },
      { target: "OperatingState", label: "经营状态观察" },
    ],
    lifecycle: ["Co-agent 起草", "窗口共同核对", "Owner 本人承诺", "CEO 整组激活", "待执行承接"],
    actors: "Co-agent 起草；具名 Owner 本人承诺；CEO 最后整组激活。",
    authority: "正式结果不等于执行授权；执行授权需独立约定。",
  },
  ReviewWindow: {
    definition: "0.4 的共同核对窗口：冻结完整公司周期的周期结果与必要结果单元成员集合与版本；本人在窗口内发表、替代或撤回意见。",
    keyFacts: [
      "收拢必须恰好覆盖每个冻结成员与每份有效意见一次。",
      "成员集合变化必须由 CEO 显式重开，不静默替换。",
    ],
    relations: [{ target: "CandidateSet", label: "关窗收拢" }],
    lifecycle: ["Co-agent 开窗冻结集合", "本人评论/替代/撤回", "关窗", "收拢"],
    actors: "Co-agent 开窗与关窗；窗口成员本人评论；CEO 显式重开。",
    authority: "窗口意见是核对记录，不代替正式确认。",
  },
  CandidateSet: {
    definition: "0.4 的完整候选集合：逐份意见取舍；具名责任人/结果 Owner 对确切候选版本承诺本人责任，CEO 最后整组激活。",
    keyFacts: [
      "候选、参与人或相关依据变化使已收集承诺失效，需重新确认。",
      "存在关键未决依赖或分歧时阻止激活；非阻塞说明随整组确认记录。",
    ],
    relations: [
      { target: "PCO", label: "整组激活" },
      { target: "Mission", label: "整组激活" },
    ],
    lifecycle: ["收拢生成", "本人责任承诺", "CEO 整组原子激活 或 显式重开"],
    actors: "Co-agent 收拢；具名责任人/结果 Owner 本人承诺；CEO 整组激活。",
    authority: "整组同时生效，不启用半套，不产生执行授权。",
  },
  OperatingState: {
    definition: "0.4 的经营状态：保留确切主体、观察时点、摘要、正式评级、基准、证据与缺口；推荐与正式状态分开。",
    keyFacts: [
      "LTCO 由当前 CEO、PCO 由对应 Scope 责任人、Mission 由唯一 Owner 确认，并在确认时重查当前任职。",
      "无证据只能是未知并列明缺口；不自动平均下层评级。",
      "不生成公司/场域/域的聚合状态对象。",
    ],
    relations: [
      { target: "LTCO", label: "观察对象" },
      { target: "PCO", label: "观察对象" },
      { target: "Mission", label: "观察对象" },
      { target: "OperatingProblem", label: "问题来源" },
    ],
    lifecycle: ["Agent 或有权人推荐", "责任人确认并可附理由修正", "新推荐未确认期间旧正式状态继续有效"],
    actors: "授权 Agent 或有权人推荐；长期结果由当前 CEO、周期结果由对应 Scope 责任人、结果单元由唯一 Owner 确认。",
    authority: "以确切的正式状态版本为准；旧确认版本作为历史保留。",
  },
  OperatingProblem: {
    definition: "0.4 的经营问题：来源正式状态，记录核心问题、层级、责任任职与证据；普通关闭区分已解决与无需继续处理。",
    keyFacts: [
      "战略层级问题可移交活跃战略轮次；移交不等于解决。",
      "已完成轮次先显式重新界定后再移交。",
    ],
    relations: [
      { target: "OperatingState", label: "来源状态" },
      { target: "StrategicIssue", label: "移交活跃议题" },
    ],
    lifecycle: ["开启", "修订", "关闭 或 移交活跃议题"],
    actors: "按层级责任任职开启与修订；当前责任人关闭；战略移交由绑定 CEO Agent 执行。",
    authority: "关闭处置绑定确切版本；移交后问题停止原跟踪，历史引用保留。",
  },
  PeriodReview: {
    definition: "0.4 的周期复盘分析材料：引用目标的正式经营状态精确版本；是分析材料，无需也没有审批。",
    keyFacts: [
      "必须引用正式经营状态的确切版本。",
      "以正式状态与证据为分析依据，不把未记录的推断写成事实。",
    ],
    relations: [
      { target: "OperatingState", label: "引用正式状态" },
      { target: "PCO", label: "复盘目标" },
    ],
    lifecycle: ["生成", "重新生成（保留版本）"],
    actors: "Co-agent 生成与重新生成。",
    authority: "分析材料无审批；系统采用该材料不等于人工批准。",
  },
  MethodRun: {
    definition: "0.4 的 Agent 运行：绑定 CEO Agent 使用本人运行直接创建、重新界定或关联战略议题；是研究 Context 的授权底座。",
    keyFacts: [
      "运行内容只有名称与方法标识；与议题等根对象的关联由专门的运行关联动作记录。",
      "恢复研究快照时重新核验同一 Agent、当前授权与运行中状态。",
    ],
    relations: [{ target: "StrategicIssue", label: "关联战略议题" }],
    lifecycle: ["开启运行", "关联根对象", "暂停/恢复", "完成"],
    actors: "CEO Agent 创建本人运行；CEO 保留参与人与研究责任指定。",
    authority: "授权与恢复底座，不产生业务决定。",
  },
  StrategyUpdateProposal: {
    definition: "0.4 的更新提案：依据正式人类共识提出确切变更，Co-agent 复核，CEO 最终确认后在事务内原子生成正式版本。",
    keyFacts: [
      "未变对象保留确切版本并附适用性理由；只有变更对象产生新版本。",
      "提案锁定确切的共识引用与当前依据，任一项变化即拒绝并重新准备。",
    ],
    relations: [
      { target: "StrategicAgreement", label: "更新依据" },
      { target: "Strategy", label: "确认后生效" },
      { target: "StrategicArchitecture", label: "配对变更" },
    ],
    lifecycle: ["CEO Agent 起草", "Co-agent 复核", "CEO 确认", "同一事务原子生效"],
    actors: "CEO Agent 起草，Co-agent 复核，CEO 本人确认。",
    authority: "未经 CEO 确认不生效；确认与生效在同一事务。",
  },
}

function versionTypes(rules: RulesVersion, registered: Set<string> | null): Set<string> {
  if (registered) return registered
  // Without a catalog the map is not rendered; this fallback only keeps type
  // references internally consistent and is never presented as a directory.
  if (rules === "0.4") return new Set(V04_TYPES)
  const all = new Set<string>()
  for (const area of AREAS) for (const type of area.types) all.add(type)
  if (rules === "0.1" || rules === "0.2") {
    all.delete("StrategicArchitecture")
    all.delete("OperatingState")
    all.delete("OperatingProblem")
  }
  if (rules === "0.1") all.delete("ResearchBrief")
  return all
}

export function areasFor(rules: RulesVersion, registered: Set<string> | null): Area[] {
  const present = versionTypes(rules, registered)
  return AREAS.map((area) => ({ ...area, types: area.types.filter((type) => present.has(type)) }))
    .filter((area) => area.types.length > 0)
}

/** Registered types that have no curated area (kept visible, never hidden). */
export function undocumentedTypes(rules: RulesVersion, registered: Set<string> | null): string[] {
  const present = versionTypes(rules, registered)
  const curated = new Set<string>()
  for (const area of AREAS) for (const type of area.types) curated.add(type)
  return [...present].filter((type) => !curated.has(type)).sort()
}

export function mapEdges(rules: RulesVersion, registered: Set<string> | null): MapEdge[] {
  const present = versionTypes(rules, registered)
  return MAP_EDGES.filter((edge) =>
    (!edge.versions || edge.versions.includes(rules))
    && present.has(edge.from) && present.has(edge.to))
    .map(({ from, to, label }) => ({ from, to, label }))
}

export const MARKER_LABELS: Record<string, string> = {
  scene: "场景/过程记录",
  support: "证据·授权底座",
}
