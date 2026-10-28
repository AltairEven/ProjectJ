# 跨模块共同约定

版本：1.7.0；更新：2026年10月28日；范围：业务合同及工程映射；课文插图与带图题（[ADR-0026](../../decisions/ADR-0026.md)）；课程资源按学生与课程整理（[ADR-0027](../../decisions/ADR-0027.md)）。

本目录维护共同业务含义、执行顺序和继续条件。八个模块保留业务维护责任，[应用协调层](../application/README.md)是显式的内部执行组件；shared提供规范，不充当运行组件或新增 Agent。

涉及继续条件的“关键”与阻断判定，统一采用[共同阻断标准](business-contracts.md#关键阻断的共同标准)。

## 权威规范入口

| 主题 | 唯一规范来源 |
| --- | --- |
| 对象维护、建议/需求、待决定方案、关键阻断、身份和证据、P0最小字段/核心状态 | [business-contracts.md](business-contracts.md) |
| 结束封存、反馈追加、按需评价与复用、失败接续、下一课及数据切换 | [execution-recovery.md](execution-recovery.md) |
| 用例协调、操作进度、并发接续 | [应用协调层](../application/README.md) |
| 验收报告、使用资格与失效 | [验收管理](../application/acceptance.md) |
| 本次开课依据、冲突与结果 | [开课判定](../classroom/session-start.md) |
| 输入完成、自动提交、补充/再试、更正与优先控制 | [输入提交规则](../interface/input-submission.md) |
| 教师简报内容与生产 | [课程包与简报](../preparation/course-package.md) |
| 学习者归属、能力身份、计划层次与复盘 | [档案归属](../learning/profile.md#学习者归属与扩展边界)、[能力目录](../learning/capability-catalog.md)、[计划](../learning/plans.md)、[复盘用例](../learning/plan-review.md) |
| 首版范围、技术基线与分阶段实施 | [最小版本](../../docs/mvp.md)、[技术选型](../../docs/technology.md)、[开发路线](../../docs/roadmap.md) |
| 质量门槛 | [总验收](../../docs/acceptance.md)；业务资格维护仍归应用验收管理 |
| 跨模块完整时序和异常结果 | [end-to-end-cases.md](end-to-end-cases.md) |

各模块保留本职责的应用说明，通过引用采用共同规则；交接摘要不另立标准。规范冲突先修源文档及消费引用，不在新文件追加另一个例外。根入口和AGENTS提供导航，历史ADR保留当时决定，后续替代关系由新ADR明确。

## 当前阶段与真实数据权威

2026年10月20日用户把定位改为AI自学助理（[ADR-0018](../../decisions/ADR-0018.md)），按[实施计划](../../docs/superpowers/plans/2026-10-20-self-study-assistant.md)分批改为单一主页面、建档与正式学习空间。此前已建立八模块最小职责、应用协调器、模拟与真实提供者，工程检查、调试及真实核对分层记录见[交接](../../docs/handoff.md)，研发协作按[共建规则](../../AGENTS.md#共建协作)执行。0.1成人实验原型已删除，历史代码只读查阅Git标签prototype-0.1（[ADR-0012](../../decisions/ADR-0012.md)）；程序检查不证明真实使用或教学效果通过。

正式学习空间（`project_j/private/study/<run_id>`）里的学习资料、状态、计划与历史以J为准；开发期间用户会定期清空。LearningWithAI的家庭档案不迁移、不双写。模拟空间只用于开发与测试，其中的内容不是学习记录。

课堂结束时封存已取得事实、随后追加补记的规则见[ADR-0010](../../decisions/ADR-0010.md)与[共同执行](execution-recovery.md)；ADR-0018起评价在下课后自动进行，不再等待反馈。课中必要内存及不监控原则保持。

## 术语规则

ADR-0018之后，程序只有“学生”一个身份；“人类教师”只是设计说法，程序不区分操作者。下列文档已按新定位改写：README、AGENTS、[产品范围](../../docs/product.md)、[最小版本](../../docs/mvp.md)、[开发路线](../../docs/roadmap.md)、[总验收](../../docs/acceptance.md)、[技术选型](../../docs/technology.md)、本文与[业务合同](business-contracts.md)的角色与来源部分、interface模块的[入口](../interface/README.md)、[角色与设备](../interface/roles-devices.md)、[主页面](../interface/main-page.md)、[课程详情与开始](../interface/preview-start.md)、[课后评价页](../interface/feedback-reports.md)、[课堂页](../interface/student-classroom.md)，learning的[档案与建档](../learning/profile.md)、[复盘](../learning/plan-review.md)、[更新](../learning/updates.md)，preparation的[入口](../preparation/README.md)、[课程详情](../preparation/parent-preview.md)、[开课准备](../preparation/readiness.md)，evaluation的[输入与补记](../evaluation/inputs-feedback.md)、[报告与修订](../evaluation/reports-revisions.md)，以及platform的[配置与权限](../platform/configuration-permissions.md)。

其余文档暂不逐篇改写，阅读时：“家长”“孩子/儿童”统一读作“学生”；“家长工作台”读作“主页面”；“家长反馈”读作“学生补记”；“成人检查/成人演练”读作“正式学习空间”或“模拟”，按上下文；儿童入口、儿童数据条件、家长陪伴试教等限制已取消。与ADR-0018冲突时以ADR为准。
