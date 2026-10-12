# Project J 模块设计入口

版本：1.9.0；更新：2026年10月12日；阶段：产品与模块设计。

Project J 采用家长工作台与学生课堂两种界面，串起准备下一课、备课预览、课堂互动、离屏活动、课后反馈、评价与下节调整。当前梳理职责、流程、输入输出和质量条件，不选择最终技术栈，也不继续实现应用功能。

跨模块先读[shared共同入口](shared/README.md)：[业务合同](shared/business-contracts.md)、[执行/失败接续与下一课条件](shared/execution-recovery.md)、[端到端案例](shared/end-to-end-cases.md)。这是八模块的横向约定，不是新增模块；学科/能力目录维护规则见[learning目录](learning/capability-catalog.md)。

## 大模块

| 目录 | 负责什么 | 主要交付 |
| --- | --- | --- |
| [learning/](learning/README.md) | 孩子档案、全学科能力状态、长期与短期计划及课后更新 | 下一课需求、计划调整、可追溯状态修订 |
| [preparation/](preparation/README.md) | 将需求、状态和Jane方法转成核实课程、素材与简报 | 课程包、核验依据、家长预览、备课完成范围及启动要求 |
| [teacher/](teacher/README.md) | 唯一教师Jane的角色、通用规则、学科/课型策略和反馈迭代 | 有适用范围的教学配置、方法与改进版本 |
| [curriculum/](curriculum/README.md) | 资料/资源/模板/课程包的版本、复用、呈现与历史更正 | 固定内容及依赖、使用引用、检查范围与更正限制 |
| [classroom/](classroom/README.md) | 本堂教学核心、统一事件/动作/交付反馈、Harness与收尾 | 允许教学动作、本堂实际事件/条件与结束原因 |
| [evaluation/](evaluation/README.md) | 课后事实/评价、备课课堂对照、建议与更正回查 | 状态修订依据、下节预期、主责/关联改进与实际处理范围 |
| [interface/](interface/README.md) | 家长/学生视图、预览反馈、媒介与角色/设备接续 | 统一事件与允许呈现/交付、真实状态和操作反馈 |
| [platform/](platform/README.md) | 模型、实时语音、多模态、配置/权限、同步、存储和故障 | 实际能力/配置/版本、受控交付和取消、保存与缺口 |

每个模块先用README维护职责、输入输出和边界。后续需求、流程、接口与验证方案放进对应目录，出现实际需要后再细分。模块表示一个应用内部的职责，不要求拆成独立服务或多个Agent。

学习管理已细分为[档案](learning/profile.md)、[全学科状态](learning/state.md)、[计划](learning/plans.md)、[课后更新](learning/updates.md)和[模块交接](learning/handoffs.md)。这些是设计说明，功能尚未实现，也没有导入真实档案或生成儿童记录。

八个大模块中的teacher维护Jane唯一身份及其方法库。已细分[角色](teacher/role.md)、[通用规则](teacher/rules.md)、[学科策略](teacher/subject-strategies.md)、[课型策略](teacher/lesson-types.md)、[反馈与版本](teacher/improvement.md)和[模块交接](teacher/handoffs.md)。备课选择方法，课程保存绑定，课堂按允许范围应用，评价提供改进依据；模块尚未实现。

备课已细分[输入与流程](preparation/inputs-workflow.md)、[课程包与简报](preparation/course-package.md)、[多模态素材](preparation/multimodal-materials.md)、[核验](preparation/verification.md)、[家长预览](preparation/parent-preview.md)、[两层就绪](preparation/readiness.md)和[版本/交接](preparation/versions-handoffs.md)。仅为设计，未生产真实课程或媒体、未接入原型。

课程与教学内容已细分[分层组织](curriculum/organization.md)、[版本/生命周期](curriculum/versions-lifecycle.md)、[检索与复用](curriculum/discovery-reuse.md)、[素材呈现](curriculum/presentation-resources.md)、[历史与更正](curriculum/history-corrections.md)和[模块交接](curriculum/handoffs.md)。内容库和新状态系统未实现，不改写现有课程或真实历史。

课堂已细分[职责与统一交接](classroom/boundaries-events.md)、[会话/开课](classroom/session-start.md)、[互动/Harness](classroom/interaction-harness.md)、[帮助纠错](classroom/guidance-correction.md)、[材料/离屏](classroom/materials-offscreen.md)、[时间/异常](classroom/time-exceptions.md)和[收尾/交接](classroom/closing-handoffs.md)。核心管理教学，interface适配最终输入/呈现，platform提供技术能力；仅设计，未接入或验证新版运行。

评价已细分[输入/反馈](evaluation/inputs-feedback.md)、[事实/证据](evaluation/facts-evidence.md)、[任务判断](evaluation/assessment.md)、[备课课堂对照](evaluation/lesson-alignment.md)、[下节建议](evaluation/next-lesson.md)、[报告/修订](evaluation/reports-revisions.md)和[模块交接](evaluation/handoffs.md)。学生表现、教学行为与准备/实际差异分别解释，对照保留课前基线并反馈主责/关联；本轮仅设计，未评价真实孩子或实现下游更新。

界面已细分[家长工作台](interface/parent-workbench.md)、[学生课堂](interface/student-classroom.md)、[预览/启动](interface/preview-start.md)、[反馈/报告](interface/feedback-reports.md)、[媒介适配](interface/input-output-adapters.md)、[角色/设备](interface/roles-devices.md)和[模块交接](interface/handoffs.md)。视图不自建评分/转场，媒介遵守任务条件，切换原会话、来源与权限分开，停止/同步状态如实呈现；仅设计，未实现新页面或多端。

基础服务已细分[模型](platform/models.md)、[实时语音](platform/realtime-voice.md)、[多模态](platform/multimodal-materials.md)、[配置/权限与儿童数据](platform/configuration-permissions.md)、[会话同步](platform/sessions-sync.md)、[数据存储](platform/storage-lifecycle.md)、[故障/性能](platform/failures-performance.md)和[模块交接](platform/handoffs.md)。统一技术请求与结果，课堂核心先允许再发声，运行与业务版本相连，服务商留存与本地策略分别检查；仅设计，实际服务/账户和新版流程未验收。

## 使用流程与责任归属

| 使用步骤 | 主要负责模块 | 结果 |
| --- | --- | --- |
| 1. 准备下一堂课 | learning | 本课目标、时间与材料条件 |
| 2. AI备课与核对 | preparation、teacher、curriculum | 选用Jane方法的课程草案、教师简报、素材与检查结果 |
| 3. 家长预览 | preparation、interface | 与课程版本一致的预览、条件反馈、材料清单与缺口 |
| 4. 开始本堂会话 | classroom、platform、interface | 核对本次条件后进入绑定版本的独立课堂 |
| 5. 教学与练习 | classroom、interface、platform | 核心判断/动作，经适配取得原答、交付/帮助与接续 |
| 6. 离屏活动 | classroom、interface | 实际完成报告及原答，未做保留未观察 |
| 7. 正常收尾或明确停止 | classroom | 结束原因、完成与未完成项 |
| 8. 家长一次反馈 | interface、evaluation | 家长原话与必要澄清 |
| 9. 课后评价与状态更新 | evaluation、learning，应用层协调 | 先保存事实/评价依据，再落实状态/计划；教学改进独立交主责 |
| 10. 调整下一课 | learning、preparation，应用层协调 | 保存并复核有效需求，处理关键依赖后备课/本次检查，汇总实际结果 |

platform为这些步骤提供能力，不决定教学目标、难度、评分或学习进度。interface负责呈现与交互，不另建一套教学判断规则。

## 共享边界

- 学习计划、课程设计、本堂实施事实、评价与状态修订分别维护并互相引用。下节建议不等于课程已备好，计划到期不等于已实施。
- 备课是生产和核对内容的过程；curriculum维护该过程产出的课程版本与素材。原始材料不在各模块复制多个权威副本。
- 课程生命周期、备课完成、本次启动条件和真实使用关系分别维护，固定或本地发布不表示可开课，课程用过一次不永久完成。复用看目标/条件/熟悉程度，更正保留原内容并回查相关评价与状态，停用/归档不自动删除历史依赖。
- 目标和验证任务先行，必需素材提前核验，课堂生成限定用途且有备用。preparation提供备课完成依据；classroom/platform/interface核对本次设备、材料和家庭条件。预览与家长实际阅读分别记，不能以预览或格式检查替代就绪。
- Jane始终是唯一教师。teacher维护全局角色与策略，preparation编制本课简报，curriculum保存实际绑定的版本与历史；学科、课型和模型变化不创建另一位教师。课堂不热更新全局教师规则。
- classroom管理教学状态与Harness，interface适配最终输入/呈现，platform提供模型/媒介/计时/取消，统一事件/动作/交付反馈复用核心。动作生成、实际交付、学生收到和理解分开；界面不另建评分/转场，停止即时取消且同步状态，迟到不推进。模型在本课范围指导，程序通过不替代教学效果。
- 课中保留必要内存，结束且收到家长实际反馈后统一归档。成人、技术模拟与儿童证据分开。
- 家长不重复填基本信息或手工给能力评级。AI按基础参考推算背景，依据真实反馈维护学习状态；重要方向与预算变化由家长决定。
- 学习状态覆盖全部学科、跨学科学习能力和综合应用表现；英语优先只影响当前目标与投入。各维度保留实际帮助与证据，同一活动跨维度引用不能增加样本次数，未观察不判失败。
- 学生学习状态与Jane教学行为分别维护；评价用实际证据支持两类调整。Jane改进兼顾独立能力、保持/迁移、体验与家庭投入，不以顺畅、喜好或完成量代替效果依据。
- 评价维护有来源事实与任务判断，learning维护状态/计划，teacher维护Jane；备课与实际五维对照区分合理适配、设计/执行问题和未知，反馈主责/关联。覆盖率、顺畅与模型自信不证明效果，保存/下游落实分别报告，更正回查而不重复计样本。
- 家长/学生界面只取当前许可内容，角色标签不证明实际提供者；字幕/重播/语速及替代媒介按任务条件，核心收到交付反馈。切换页面/设备原会话接续，明确当前学习输入与语音输出；本端取消与核心同步分别显示，断网不承诺远端已停。
- platform按用途调用必要能力，实际模型/参数、内容/运行版本和交付范围可核对，不把格式或接口成功当教学完成。活动/连接/服务商会话分开，取消/重连核对核心状态；保存与下游落实分别回报。儿童外发数据条件、本地内存策略、Git和服务商保留分别处理，缺口不靠配置声明覆盖。
- 应用层协调跨模块用例，八模块保持判断/维护职责；事实和评价依据、状态、计划核对及需求依次取得保存/处理结果，家长汇总报告后形成。待反馈仅内存，延后/失败不造事实；旧基线是否可继续由业务适用结果决定，候选改进不阻塞全部课。共同对象/尝试/帮助与目录规则统一引用shared和learning目录，案例未执行。
- LearningWithAI暂保留家庭档案与历史权威，本轮不迁移/回写。未来按执行设计满足具体切换条件后，J维护新正式数据、旧项目只读历史；不自动双写或丢弃原依据。

## 现有文件与后续实现

现有[课程素材](../courses/paper-bridge/lesson.json)逻辑归curriculum管理，暂不移动。现有project_j/、web/、tests/与scripts/保留为0.1实验原型，当前设计不沿用其所有限制；原型说明见[架构文档](../docs/architecture.md)。

design/存设计文档，courses/存课程与素材，未来应用代码的物理目录待技术方案确定。真实学习数据不放入design/，也不因建立模块目录复制儿童档案。根README与AGENTS是项目入口。
