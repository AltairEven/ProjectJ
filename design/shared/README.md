# 跨模块共同约定

版本：1.2.1；更新：2026年10月12日；范围：逻辑设计。

本目录维护共同业务含义、执行顺序和继续条件。八个模块保留业务维护责任，[应用协调层](../application/README.md)是显式的内部执行组件；shared提供规范，不充当运行组件或新增 Agent。

涉及继续条件的“关键”与阻断判定，统一采用[共同阻断标准](business-contracts.md#关键阻断的共同标准)。

## 权威规范入口

| 主题 | 唯一规范来源 |
| --- | --- |
| 对象维护、建议/需求、待决定方案、关键阻断、身份和证据 | [business-contracts.md](business-contracts.md) |
| 结束封存、反馈追加、按需评价与复用、失败接续、下一课及数据切换 | [execution-recovery.md](execution-recovery.md) |
| 用例协调、操作进度、并发接续 | [应用协调层](../application/README.md) |
| 验收报告、使用资格与失效 | [验收管理](../application/acceptance.md) |
| 本次开课依据、冲突与结果 | [开课判定](../classroom/session-start.md) |
| 输入完成、自动提交、补充/再试、更正与优先控制 | [输入提交规则](../interface/input-submission.md) |
| 教师简报内容与生产 | [课程包与简报](../preparation/course-package.md) |
| 学习者归属、能力身份、计划层次与复盘 | [档案归属](../learning/profile.md#学习者归属与扩展边界)、[能力目录](../learning/capability-catalog.md)、[计划](../learning/plans.md)、[复盘用例](../learning/plan-review.md) |
| 首版范围、技术基线与分阶段实施 | [最小版本](../../docs/mvp.md)、[技术选型](../../docs/technology.md)、[开发路线](../../docs/roadmap.md) |
| 质量门槛与儿童试教前提 | [总验收](../../docs/acceptance.md)；业务资格维护仍归应用验收管理 |
| 跨模块完整时序和异常结果 | [end-to-end-cases.md](end-to-end-cases.md) |

各模块保留本职责的应用说明，通过引用采用共同规则；交接摘要不另立标准。规范冲突先修源文档及消费引用，不在新文件追加另一个例外。根入口和AGENTS提供导航，历史ADR保留当时决定，后续替代关系由新ADR明确。

## 当前阶段与真实数据权威

项目处于设计阶段；本目录描述目标行为，新增流程与案例尚未实现或执行。0.1成人实验原型已删除，历史代码只读查阅Git标签prototype-0.1（[ADR-0012](../../decisions/ADR-0012.md)）；程序检查不证明新版流程、真实API/声音、30分钟或儿童教学效果通过。正式儿童入口仍未开放；首版范围、技术基线和阶段路线已由[ADR-0011](../../decisions/ADR-0011.md)采用；编码、依赖安装和实际接入由后续明确实现请求推进。

当前LearningWithAI是家庭档案、状态、计划与真实历史的权威入口。J只保留已指定课程材料，不执行真实数据迁移或双写。未来目标与具体切换条件以[共同执行](execution-recovery.md)为准；设计修改不表示已经切换。

2026年10月12日家长已明确采用“课堂结束时封存事实、家长反馈随后追加”，适用于J的目标应用设计；替代旧设计中反馈前只留内存的规则。课中必要内存及不监控原则保持，旧项目规范本次不修改。决定见[ADR-0010](../../decisions/ADR-0010.md)。

所有案例使用明确虚构/隔离来源；成人演练保持成人分类，均不生成真实儿童学习证据。已实现范围、实际检查记录与未来条件分别见[项目README](../../README.md)和[总验收](../../docs/acceptance.md)。
