# Project J 最小版本技术选型

版本：1.1.0；更新：2026年10月12日；状态：实施基线已采用，P0环境已验证，真实接入未验证。

采用本机浏览器、模块化Python后端和本地数据库，保留八个业务模块及应用协调层的职责。选型服务于[E2E01](../design/shared/end-to-end-cases.md#e2e01-正常完成与下一课准备)的完整实现，范围见[最小版本](mvp.md)，阶段与验收见[路线](roadmap.md)和[标准](acceptance.md)。本文件不改变[业务合同](../design/shared/business-contracts.md)，涉及继续条件的缺口按其[共同阻断标准](../design/shared/business-contracts.md#关键阻断的共同标准)处理。

## 技术基线

| 层次 | 采用方案 | 原因与实现边界 |
| --- | --- | --- |
| 页面 | React、TypeScript、Vite | 组织家长与学生视图、材料和媒介状态；前端不维护另一套教学推进规则 |
| 后端接口 | Python、FastAPI、Pydantic | 延续现有Python基础；定义请求、事件与模型结果，导出接口合同供前端使用 |
| 应用协调 | 普通Python用例服务及持久化操作进度 | 按业务步骤接续；首版不引入工作流平台、消息集群或调度Agent |
| 课堂核心 | 独立Python状态机及受限模型提案 | 允许动作、任务/等待对象、帮助证据、停止与结束由核心控制，可脱离语音测试 |
| 结构数据 | SQLite、SQLAlchemy、Alembic | 保存对象修订和操作结果，维护数据库迁移；首版一个后端进程、一个正在进行的课堂 |
| 素材 | 受控本地文件目录与数据库版本引用 | 保存实际内容、用途及完整性依据，避免数据库记录与文件版本脱节 |
| 模型接入 | 官方Python SDK、Responses API、结构化输出 | 平台核对结构，业务核对内容及可采用范围；不把模型结果直接写成正式状态 |
| 应用通信 | HTTP处理普通请求，WebSocket传课堂事件与交付反馈 | 两者共享活动、任务、事件身份及版本；重连沿原活动核对 |
| 语音 | 浏览器WebRTC优先接实时转写，批准文本经后端合成并流式交付 | 输入提交、教学批准和实际播放分开；协议通过阶段0短验证后固定 |

Vite提供React/TypeScript模板，FastAPI支持基于Pydantic的验证、OpenAPI以及WebSocket。[Vite文档](https://vite.dev/guide/)、[FastAPI功能](https://fastapi.tiangolo.com/features/)、[WebSocket支持](https://fastapi.tiangolo.com/advanced/websockets/)

SQLite适合当前本机、低写并发的范围。以后出现多服务器或明显的并发写入需求再评估数据库替换；首版不将数据库文件放在供多机同时访问的共享网络盘。[SQLite适用范围](https://www.sqlite.org/whentouse.html)

阶段0锁定兼容的Python、Node、库和浏览器版本，形成依赖锁文件与实际运行配置。本机自带的Python 3.9已停止维护，新代码不沿用；选定仍受维护且所选依赖支持的版本，使用项目级独立环境。全部工程文件放在project_j/，不同语言按需分子目录，内部布局沿职责组织，在阶段0细化；现有design路径继续保存逻辑设计。

## 进程和数据组织

模型、识别与合成请求异步执行，不能长时间持有课堂状态锁。回包后重新核对活动修订、等待对象与取消状态；停止控制走独立优先路径，不排在模型请求之后。首版不启用多个后端worker分别持有同一课堂内存。

课前用例与结束后的操作进度保存在数据库；活动开始后的事件、语音缓冲与实际交付保留本堂必要内存，结束时封存。存储时点及丢失边界按[共同执行](../design/shared/execution-recovery.md)，不能用数据库日志、浏览器缓存或SDK追踪变相保存课中快照或原始音频。

正式对象有稳定学习者归属、身份与修订，业务模块通过存储接口维护自己的对象。固定内容与评价来源保留历史，当前有效引用可更新；不采用只能保留最新摘要的结构。数据库事务覆盖一次明确的保存边界，跨步骤采用原操作身份、保存回执和版本核对，不宣称一次事务覆盖模型调用及所有模块。

真实档案、实施/评价、凭据及个人素材按[存储规则](../design/platform/storage-lifecycle.md)留在私有区，不进入源码Git；非敏感设计、模板和课程来源可按项目维护规则管理。备份需包含一致的数据库与实际引用素材，恢复后核对对象关系和版本；阶段1用隔离数据验证导出和恢复，实施路径在阶段0明确。不能因本地Git存在就认为真实学习数据已经备份。

## 模型与语音配置

| 用途 | 首选配置 | 接入验证 |
| --- | --- | --- |
| 课堂判断与指导 | gpt-6.1-sol，reasoning.effort为low | 实际配置、语义、允许动作、取消、用量与响应时间 |
| 备课、评价、学习规划与复盘 | 同模型，reasoning.effort为medium | 来源、结构、证据限制、内容核验及结果复用 |
| 实时转写 | gpt-live-transcribe，浏览器WebRTC路径 | 中英输入、慢答/停顿、最终转写、取消、回声与数据条件 |
| 合成 | gpt-4o-mini-tts，固定经过试听的声音配置 | 中英文、可懂度、实际首播、打断与交付范围 |

Sol官方模型说明列明low、medium及结构化输出支持；格式正确仍可能有语义错误。模型页另列fast与ultrafast服务层级，核对时价格分别为标准层的2倍和6倍；层级按[质量门槛](acceptance.md#最小版本质量门槛)作为延迟测量变量，不默认启用。[模型说明](https://developers.openai.com/api/docs/models/gpt-6.1-sol)、[结构化输出](https://developers.openai.com/api/docs/guides/structured-outputs)

实时转写首选的gpt-live-transcribe需要客户端判断音频轮次并commit，不支持server_vad或semantic_vad。采用客户端VAD与任务停顿窗口：先结束本轮音频取得最终转写，再由interface按[输入提交规则](../design/interface/input-submission.md)决定是否向核心提交；中途续说与迟到修订不能被当成独立答案。[实时转写官方说明](https://developers.openai.com/api/docs/guides/realtime-transcription)

语音路径为“实时转写 → 完成输入提交 → 核心判断并允许动作 → 合成已允许文本 → 实际播放反馈”。合成可以流式播放已批准文本，但未批准的模型片段不提前发声。[语音合成官方说明](https://developers.openai.com/api/docs/guides/text-to-speech)

长期密钥由本地后端管理，浏览器直连服务时取得受限临时凭据；账号、接口、区域、权限、保留和实际参数均需阶段0核对。首选配置不可用时记录缺口和具体替代，不静默更换。声音配置与协议选择不会创建第二位教师。

浏览器直连意味着孩子的原始音频由浏览器直接发往服务商。2026年10月12日核对的官方数据控制页列出`/v1/realtime`和`/v1/audio/transcriptions`可申请零数据保留，未单独列出实时转写会话端点，也未说明临时凭据路径；阶段0须以实际账户核实所选转写路径的覆盖范围。覆盖未确认时，成人开发可继续使用该路径，但转写接入保持可替换；进入阶段5前必须确认或换成一条资格已核实的路径，候选包括经后端中转的连接和按轮转写接口，均以实际账户核实为准。[官方数据控制](https://developers.openai.com/api/docs/guides/your-data)

按轮上传转写可作为成人比较或备用候选；只有通过对应任务/输入条件检查后才采用，不将其冒充已通过自然语音验收。按键提交和文字备用同样需说明评价条件变化。

## Harness和模型的分工

程序维护合法状态与动作、版本、权限、帮助历史、停止、时间预算、保存和重复处理。模型承担范围内的语义判断、措辞与指导方案，返回可核对的依据及动作提案。内容核验、独立参考题集和实际体验共同检查质量，Harness不宣称能程序化证明所有学科答案正确。

明确控制、固定转场和已核实内容可直接处理；通常一次必要课堂语义调用同时提出判断及回应，不固定串联多个模型评审。课后模型调用遵守[按需评价与复用](../design/shared/execution-recovery.md#阶段性评价的按需触发)。性能和用量的验收口径集中在[质量门槛](acceptance.md#最小版本质量门槛)。

Skills用于开发、备课及维护工序；运行时依赖版本化策略、业务规则和明确接口。首版直接使用SDK，不引入Agents SDK或通用Agent循环；以后确有多工具长任务需求时再评估，仍保留当前教学与数据边界。

## 原型处理

0.1原型已删除，删除前状态保存在本地Git标签prototype-0.1。其课程检查、帮助/再试、停止和迟到结果等规则与测试思路可只读查阅，按新合同重写并逐项验收，不作迁移基础。决定及取舍见[ADR-0011](../decisions/ADR-0011.md)和[ADR-0012](../decisions/ADR-0012.md)。

## P0锁定环境与实际检查

2026年10月12日，P0在本机建立独立环境，未运行应用或课堂。版本不是服务端可用性/教学资格证据。

| 项目 | 固定版本/路径 | P0实际证据与限制 |
| --- | --- | --- |
| Python | CPython3.14.4；project_j/python/.python-version；project_j/.python及python/.venv | 实际安装、锁定同步、Ruff/pytest通过；不用系统3.9，不改全局设置 |
| Python库 | FastAPI0.143.0、Pydantic2.14.1、SQLAlchemy2.1.4、Alembic1.20.0、OpenAI3.29.0；pytest9.1.1、Ruff0.17.0 | uv.lock锁全部传递依赖及hash；Pydantic/FastAPI导出、SDK mock、临时SQLite事务验证。尚无应用迁移/服务 |
| SQLite | 本解释器3.50.4 | 临时库回滚验证；正式存储、备份恢复待阶段1 |
| Node/npm | Node26.11.1、npm11.20.0；project_j/.node-version及web/package.json | 实际读取版本；未更新本机全局工具 |
| 页面基础库 | React/ReactDOM19.3.0、TypeScript7.0.2、Vite8.3.4、React类型19.3.0 | web/package-lock.json锁定；npm ci禁安装脚本、依赖导入和独立React类型编译通过；没有应用页或浏览器行为验收 |
| 浏览器候选 | 本机macOS26.6.2/build25G83，Chrome154.0.8037.98 | 从本机系统/应用元数据读取；家长已选择内置麦克风＋内置扬声器；Chrome为本批拟定浏览器，实际许可/试听/回声与打断仍未验证，不能称声音已接入 |

Python3.14仍为受维护系列；官方已经发布安全补丁3.14.8。本机uv0.11.11内置清单无3.14.8下载，尝试官方uv远端下载元数据路径返回404，故当前pin3.14.4只用于隔离P0/P1验证；真实接入前更新到可取得的最新安全补丁并重跑相关检查，不静默沿用旧补丁。[Python3.14.8发布](https://www.python.org/downloads/release/python-3148/)、[uv下载元数据设置](https://docs.astral.sh/uv/reference/settings/#python-downloads-json-url)

工程/数据库/私有文件布局唯一维护在[存储规则](../design/platform/storage-lifecycle.md#阶段0路径与阶段1保存单位)，操作身份/保存单位与有限重试在[共同执行](../design/shared/execution-recovery.md#p0操作身份保存与有限接续)。锁定安装与检查命令见AGENTS，检查只验证声明范围。

### 独立探针与真实接入入口

`project_j/scripts/probe.py`默认离线，不载入.env，不连接服务、不读取麦克风；只返回配置目标与密钥是否存在。显式真实入口在具备账号/权限后使用以下两条，每条一份固定虚构技术样本；标签是非秘密识别名，密钥从后端进程环境提供，不放命令正文或Git。

```sh
project_j/scripts/uv.sh run --offline --locked --project project_j/python python project_j/scripts/probe.py --live --effort low --account-label adult-dev --project-label project-j-dev
project_j/scripts/uv.sh run --offline --locked --project project_j/python python project_j/scripts/probe.py --live --effort medium --account-label adult-dev --project-label project-j-dev
```

本批没有执行--live。SDK请求指定gpt-6.1-sol、reasoning.effort、default层级、store=false与结构化格式，自动重试关闭；输出请求/返回配置、UTC时间、样本、结果/未知、request_id、可取得用量和后台耗时。返回模型/effort/层级缺失或不符不能通过配置核验，不能靠提示词宣称Low。此单请求耗时不是声音延迟；完整参考题集语义验证、课堂总期限/取消和Medium实际内容核验另做。

### P0官方事实复核与接入缺口

以下在2026年10月12日重新打开官方页面核对；页面说明不等于本账户权限或配置通过。

| 项目 | 官方说明/实际状态 | 继续条件 |
| --- | --- | --- |
| Sol Low/Medium | 官方模型页支持两档及结构化输出；本机仅SDK mock通过，实际API**未验证** | API项目/密钥、模型权限/账单范围具备后分别运行探针；课堂参考题集与Medium内容另验 |
| 服务层级 | 官方列fast=标准2倍、ultrafast=6倍；本批只声明default，实际计费/可用性**未验证** | 记录返回层级/用量；更高成本层级待家长按实际成本决定，不为延迟暗改 |
| 转写WebRTC | 官方推荐gpt-live-transcribe，客户端commit且无server_vad/semantic_vad；本批未建音频连接 | 保留首选候选，成人实测临时凭据/连接/会话配置/最终item_id映射、客户端VAD和回声后再固定协议/窗口 |
| 凭据/数据条件 | 官方WebRTC示例有client_secrets及calls；数据控制保留表未单列这些或transcription_sessions。该页区域服务清单提到transcription_sessions，不能由此推断ZDR覆盖 | 实际组织/项目ZDR批准与所选模型/端点/功能/地区覆盖**待核实**；儿童入口关闭 |
| Responses/合成/按轮转写保留 | 官方表列responses、audio/speech、audio/transcriptions与realtime有ZDR资格，带适用限制；本账户资格**未验证** | 按实际发送范围核对全部所选路径，store=false不代账户批准 |
| 声音与设备 | 家长选择当前Mac内置麦克风＋内置扬声器，Chrome候选已记录版本；未请求麦克风或播放声音 | 成人在场授权设备、试听中英固定声音；回声/插话/停止/延迟实测，不能移用其他设备检查 |

上述事实来源：[Sol模型页](https://developers.openai.com/api/docs/models/gpt-6.1-sol)、[实时转写](https://developers.openai.com/api/docs/guides/realtime-transcription)、[WebRTC](https://developers.openai.com/api/docs/guides/voice-webrtc?voice-api=realtime)、[数据控制](https://developers.openai.com/api/docs/guides/your-data)。实际API调用样本数为0、语音操作样本数为0；账号条件缺失可继续隔离阶段1，不能把文档核对当接口通过。

### 合成维护风险与最小待决定方案

官方停用表于2026年10月1日公告gpt-4o-mini-tts的2025-03-20和2025-12-15两个快照将于2027年1月6日停用，并推荐gpt-realtime-2.1-mini；TTS指南仍有原模型示例，当前alias的具体路由/账户可用性**未验证**。[停用公告](https://developers.openai.com/api/docs/deprecations)、[TTS指南](https://developers.openai.com/api/docs/guides/text-to-speech)

最小提案：真实合成验证时增加该候选的成人对照，要求只能交付Harness已批准文本、实际内容/首播范围可核对、取消和迟到可靠，并重新核对费用/数据覆盖；取得结果后向家长提出具体替换配置。当前不替换gpt-4o-mini-tts、不引入自主语音教师，阶段1仍以模拟合成交付验证同一接口。此提案尚待家长确认，候选官方推荐不表示J条件已满足。
