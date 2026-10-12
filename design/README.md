# Project J 模块设计入口

版本：1.3.0；更新：2026年10月12日；阶段：产品与模块设计。

Project J 采用家长工作台与学生课堂两种界面，串起准备下一课、备课预览、课堂互动、离屏活动、课后反馈、评价与下节调整。当前梳理职责、流程、输入输出和质量条件，不选择最终技术栈，也不继续实现应用功能。

## 大模块

| 目录 | 负责什么 | 主要交付 |
| --- | --- | --- |
| [learning/](learning/README.md) | 孩子档案、全学科能力状态、长期与短期计划及课后更新 | 下一课需求、计划调整、可追溯状态修订 |
| [preparation/](preparation/README.md) | 将需求、状态和Jane方法转成核实课程、素材与简报 | 课程包、核验依据、家长预览、备课完成范围及启动要求 |
| [teacher/](teacher/README.md) | 唯一教师Jane的角色、通用规则、学科/课型策略和反馈迭代 | 有适用范围的教学配置、方法与改进版本 |
| [curriculum/](curriculum/README.md) | 课程包、教材与素材、评价依据及绑定的Jane版本与本课简报 | 可追溯的课程版本与教学资源 |
| [classroom/](classroom/README.md) | 本堂会话、教学动作、Harness、帮助纠错和收尾 | 当前教学回应、本堂实际事件与帮助条件 |
| [evaluation/](evaluation/README.md) | 课后反馈、事实整理、评价与下节建议 | 课堂报告、学习状态更新依据、下节预期 |
| [interface/](interface/README.md) | 家长和孩子各自看到什么、怎样操作 | 两种界面及完整使用流程 |
| [platform/](platform/README.md) | 模型、语音、多模态、数据存储和通用运行能力 | 可替换的接入与存储能力、通用故障处理 |

每个模块先用README维护职责、输入输出和边界。后续需求、流程、接口与验证方案放进对应目录，出现实际需要后再细分。模块表示一个应用内部的职责，不要求拆成独立服务或多个Agent。

学习管理已细分为[档案](learning/profile.md)、[全学科状态](learning/state.md)、[计划](learning/plans.md)、[课后更新](learning/updates.md)和[模块交接](learning/handoffs.md)。这些是设计说明，功能尚未实现，也没有导入真实档案或生成儿童记录。

八个大模块中的teacher维护Jane唯一身份及其方法库。已细分[角色](teacher/role.md)、[通用规则](teacher/rules.md)、[学科策略](teacher/subject-strategies.md)、[课型策略](teacher/lesson-types.md)、[反馈与版本](teacher/improvement.md)和[模块交接](teacher/handoffs.md)。备课选择方法，课程保存绑定，课堂按允许范围应用，评价提供改进依据；模块尚未实现。

备课已细分[输入与流程](preparation/inputs-workflow.md)、[课程包与简报](preparation/course-package.md)、[多模态素材](preparation/multimodal-materials.md)、[核验](preparation/verification.md)、[家长预览](preparation/parent-preview.md)、[两层就绪](preparation/readiness.md)和[版本/交接](preparation/versions-handoffs.md)。仅为设计，未生产真实课程或媒体、未接入原型。

## 使用流程与责任归属

| 使用步骤 | 主要负责模块 | 结果 |
| --- | --- | --- |
| 1. 准备下一堂课 | learning | 本课目标、时间与材料条件 |
| 2. AI备课与核对 | preparation、teacher、curriculum | 选用Jane方法的课程草案、教师简报、素材与检查结果 |
| 3. 家长预览 | preparation、interface | 与课程版本一致的预览、条件反馈、材料清单与缺口 |
| 4. 开始本堂会话 | classroom、platform、interface | 核对本次条件后进入绑定版本的独立课堂 |
| 5. 教学与练习 | classroom | 原答、判断、实际帮助与接续 |
| 6. 离屏活动 | classroom、interface | 实际完成报告及原答，未做保留未观察 |
| 7. 正常收尾或明确停止 | classroom | 结束原因、完成与未完成项 |
| 8. 家长一次反馈 | interface、evaluation | 家长原话与必要澄清 |
| 9. 课后评价与状态更新 | evaluation、learning、teacher | 学生状态修订及独立的教学行为改进依据 |
| 10. 调整下一课 | learning、preparation | 巩固、拓展或进阶需求，重新进入备课 |

platform为这些步骤提供能力，不决定教学目标、难度、评分或学习进度。interface负责呈现与交互，不另建一套教学判断规则。

## 共享边界

- 学习计划、课程设计、本堂实施事实、评价与状态修订分别维护并互相引用。下节建议不等于课程已备好，计划到期不等于已实施。
- 备课是生产和核对内容的过程；curriculum维护该过程产出的课程版本与素材。原始材料不在各模块复制多个权威副本。
- 目标和验证任务先行，必需素材提前核验，课堂生成限定用途且有备用。preparation提供备课完成依据；classroom/platform/interface核对本次设备、材料和家庭条件。预览与家长实际阅读分别记，不能以预览或格式检查替代就绪。
- Jane始终是唯一教师。teacher维护全局角色与策略，preparation编制本课简报，curriculum保存实际绑定的版本与历史；学科、课型和模型变化不创建另一位教师。课堂不热更新全局教师规则。
- Harness保持会话状态、允许的教学动作、帮助条件与停止规则；模型可在课程允许范围内理解和调整指导。程序通过不能替代内容与教学效果评价。
- 课中保留必要内存，结束且收到家长实际反馈后统一归档。成人、技术模拟与儿童证据分开。
- 家长不重复填基本信息或手工给能力评级。AI按基础参考推算背景，依据真实反馈维护学习状态；重要方向与预算变化由家长决定。
- 学习状态覆盖全部学科、跨学科学习能力和综合应用表现；英语优先只影响当前目标与投入。各维度保留实际帮助与证据，同一活动跨维度引用不能增加样本次数，未观察不判失败。
- 学生学习状态与Jane教学行为分别维护；评价用实际证据支持两类调整。Jane改进兼顾独立能力、保持/迁移、体验与家庭投入，不以顺畅、喜好或完成量代替效果依据。
- LearningWithAI暂保留家庭档案与历史权威；本轮不迁移或回写。未来对接需明确来源、版本、数据责任和验证方法。

## 现有文件与后续实现

现有[课程素材](../courses/paper-bridge/lesson.json)逻辑归curriculum管理，暂不移动。现有project_j/、web/、tests/与scripts/保留为0.1实验原型，当前设计不沿用其所有限制；原型说明见[架构文档](../docs/architecture.md)。

design/存设计文档，courses/存课程与素材，未来应用代码的物理目录待技术方案确定。真实学习数据不放入design/，也不因建立模块目录复制儿童档案。根README与AGENTS是项目入口。
