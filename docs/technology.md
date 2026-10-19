# Project J 最小版本技术选型

版本：1.6.0；更新：2026年10月19日；状态：实施基线已采用，P1隔离工程已实现，真实接入未验证。

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
| 语音 | 作答为文字/点选，不设语音识别；英语朗读按键录音交发音评分；批准文本经后端合成并流式交付 | 输入提交、教学批准和实际播放分开；模型不听录音，见[ADR-0014](../decisions/ADR-0014.md) |

Vite提供React/TypeScript模板，FastAPI支持基于Pydantic的验证、OpenAPI以及WebSocket。[Vite文档](https://vite.dev/guide/)、[FastAPI功能](https://fastapi.tiangolo.com/features/)、[WebSocket支持](https://fastapi.tiangolo.com/advanced/websockets/)

SQLite适合当前本机、低写并发的范围。以后出现多服务器或明显的并发写入需求再评估数据库替换；首版不将数据库文件放在供多机同时访问的共享网络盘。[SQLite适用范围](https://www.sqlite.org/whentouse.html)

阶段0锁定兼容的Python、Node、库和浏览器版本，形成依赖锁文件与实际运行配置。本机自带的Python 3.9已停止维护，新代码不沿用；选定仍受维护且所选依赖支持的版本，使用项目级独立环境。全部工程文件放在project_j/，不同语言按需分子目录，内部布局沿职责组织，在阶段0细化；现有design路径继续保存逻辑设计。

## 进程和数据组织

模型、发音评分与合成请求异步执行，不能长时间持有课堂状态锁。回包后重新核对活动修订、等待对象与取消状态；停止控制走独立优先路径，不排在模型请求之后。首版不启用多个后端worker分别持有同一课堂内存。

课前用例与结束后的操作进度保存在数据库；活动开始后的事件、语音缓冲与实际交付保留本堂必要内存，结束时封存。存储时点及丢失边界按[共同执行](../design/shared/execution-recovery.md)，不能用数据库日志、浏览器缓存或SDK追踪变相保存课中快照或原始音频。

正式对象有稳定学习者归属、身份与修订，业务模块通过存储接口维护自己的对象。固定内容与评价来源保留历史，当前有效引用可更新；不采用只能保留最新摘要的结构。数据库事务覆盖一次明确的保存边界，跨步骤采用原操作身份、保存回执和版本核对，不宣称一次事务覆盖模型调用及所有模块。

真实档案、实施/评价、凭据及个人素材按[存储规则](../design/platform/storage-lifecycle.md)留在私有区，不进入源码Git；非敏感设计、模板和课程来源可按项目维护规则管理。备份需包含一致的数据库与实际引用素材，恢复后核对对象关系和版本；阶段1用隔离数据验证导出和恢复，实施路径在阶段0明确。不能因本地Git存在就认为真实学习数据已经备份。

## 模型与语音配置

| 用途 | 首选配置 | 接入验证 |
| --- | --- | --- |
| 课堂判断与指导 | gpt-6.1-sol，reasoning.effort为low，Codex订阅Fast层级 | 实际配置、语义、允许动作、取消、用量与响应时间 |
| 备课、评价、学习规划与复盘 | 同模型，reasoning.effort为xhigh（extra high） | 来源、结构、证据限制、内容核验及结果复用 |
| 合成 | 本机Qwen3-TTS 1.7B CustomVoice（MLX），预设音色Serena加老师语气指令，独立子工程与后端子进程 | 中英文、可懂度、流式首段与实际首播、打断与取消、GPU争用时的合成速度 |
| 英语发音评分 | 本机评分器：wav2vec2-lv-60-espeak-cv-ft加GOP，独立子工程与后端子进程 | 读错、漏读、多读的标出与误报，评分耗时与内存，门槛未校准 |

Sol官方模型说明列明low、medium及结构化输出支持；格式正确仍可能有语义错误。模型页另列fast与ultrafast服务层级，核对时价格分别为标准层的2倍和6倍；层级按[质量门槛](acceptance.md#最小版本质量门槛)作为延迟测量变量，不默认启用。2026年10月18日家长决定课堂判断先用Codex订阅的Fast层级跑通全流程，之后再考虑是否换模型；其他用途保持默认层级。Codex订阅列表中gpt-6.1-sol另列high、xhigh、max与ultra档；家长同日决定学习规划、备课与课后评价用xhigh（OpenAI适配器仍只接受low、medium、high）。[模型说明](https://developers.openai.com/api/docs/models/gpt-6.1-sol)、[结构化输出](https://developers.openai.com/api/docs/guides/structured-outputs)

课堂路径为“文字/点选提交（英语朗读为按键录音 → 发音评分）→ 课堂判断模型据文字或评分结果判断 → 核心允许动作 → 合成已允许文本 → 实际播放反馈”，依据见[ADR-0014](../decisions/ADR-0014.md)。模型不听录音，不设语音识别，原首选的gpt-live-transcribe浏览器WebRTC转写路径不再采用；朗读由孩子按结束决定读完，不用客户端VAD或停顿窗口。合成在本机流式生成并播放已批准文本，未批准的模型片段不提前发声；固定教师语句与示范音在备课时预合成。

2026年10月16日实测Codex订阅：底层协议有音频输入类型，但订阅中全部模型声明的输入只有文字和图片，按协议发送16kHz wav（本地文件与data URL各一次）时模型回答未收到音频。按ADR-0014模型不听录音，此事实只作记录；课堂判断仍是文字用途，可继续绑定Codex订阅，但其单次5–8秒的判断耗时不满足声音延迟目标，是否换更快的接入待实测后由家长决定。

英语发音评分按[ADR-0015](../decisions/ADR-0015.md)采用本机评分器：facebook/wav2vec2-lv-60-espeak-cv-ft音素识别加espeak-ng转音素、CTC强制对齐与GOP打分，作为后端子进程常驻，独立子工程与锁定依赖，与合成各自维护，评分初始化负责模型下载、摘要核对与探针（待实现）。2026年10月18日在M3 Pro（MPS、18 GB内存）上用合成成人语音实测：16.6秒音频0.24秒、74.9秒1.7秒出结果，加载约6秒，MPS分配峰值5.45 GB；读错、读成别的词、漏读与重复都能标出，慢读长段有1处误报。门槛按合成语音试算，未经语料校准，儿童效果与CPU运行未验证；faster-whisper、FunASR与云端口语评测的比较见ADR-0015。此前查到的云端上限（腾讯云段落模式文本不超过120词、录音不超过300秒）只作记录，评分段改按产品上限切分：首版每段不超过100词、录音不超过300秒。[模型卡](https://huggingface.co/facebook/wav2vec2-lv-60-espeak-cv-ft)

语音合成按[ADR-0016](../decisions/ADR-0016.md)采用本机Qwen3-TTS 1.7B CustomVoice的MLX版本，经mlx-audio（须锁定版本）运行：预设音色Serena加老师语气指令“语气温柔亲切，像给小学生上课的老师，吐字清楚，语速稍慢。”，中英文同一声音，独立子工程与常驻子进程，与评分器各自维护；同音色加同一指令的0.6B为备选。2026年10月18日在同机比较5个离线候选并经家长试听：Kokoro与macOS自带语音电子音明显，CosyVoice3在Mac上只能用CPU、首段11–18秒，MeloTTS部署依赖多；两档Qwen3用12句复杂句复测，1.7B稍优。1.7B实测首段0.27–0.42秒、RTF约0.76、GPU峰值5.1 GB、加载约7秒，离线运行正常；两个模型同时合成时RTF升到1.78，因此课中不并行运行其他GPU任务。合成需要Apple Silicon，评分与合成同时常驻时GPU峰值合计约10.6 GB。[模型](https://huggingface.co/mlx-community/Qwen3-TTS-12Hz-1.7B-CustomVoice-bf16)

长期密钥由本地后端管理，不在浏览器持有；朗读录音由后端交本机评分进程，不出本机。账号、接口、区域、权限、保留和实际参数均需实测核对。首选配置不可用时记录缺口和具体替代，不静默更换。声音配置与接入选择不会创建第二位教师。

按ADR-0015，发音评分在本机完成，孩子的原始录音不发往第三方；按ADR-0016，合成也在本机完成。模型请求按[ADR-0013](../decisions/ADR-0013.md)，零数据保留不再是真实数据接入条件；各服务记录实际发送范围与保留条款，由家长知情采用，接入保持可替换。[官方数据控制](https://developers.openai.com/api/docs/guides/your-data)

文字/点选明确提交是首版作答方式；发音观察只由朗读评分承接。

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
| Python库 | FastAPI0.143.0、Pydantic2.14.1、SQLAlchemy2.1.4、Alembic1.20.0、OpenAI3.29.0；开发测试依赖见uv.lock | uv.lock锁运行/开发依赖及hash；uv_build0.11.11在build-system精确固定，使用uv内置后端。Pydantic/FastAPI导出、SDK mock、临时SQLite事务验证；P1有隔离Alembic迁移及loopback服务 |
| SQLite | 本解释器3.50.4 | 临时库回滚验证；正式存储、备份恢复待阶段1 |
| Node/npm | Node26.11.1、npm11.20.0；web/package.json的devEngines（onFail=error） | 实际读取版本；未更新本机全局工具 |
| 页面基础库 | React/ReactDOM19.3.0、TypeScript7.0.2、Vite8.3.4、React类型19.3.0 | web/package-lock.json锁定；React/ReactDOM为dependencies，类型/TypeScript/Vite及remark为devDependencies。npm ci禁安装脚本、tsconfig独立React类型检查；没有应用页或浏览器行为验收 |
| 浏览器候选 | 本机macOS26.6.2/build25G83，Chrome154.0.8037.98 | 从本机系统/应用元数据读取；家长已选择内置麦克风＋内置扬声器；Chrome为本批拟定浏览器，实际许可/试听/回声与打断仍未验证，不能称声音已接入 |

Python3.14仍为受维护系列；官方已经发布安全补丁3.14.8。本机uv0.11.11内置清单无3.14.8下载，尝试官方uv远端下载元数据路径返回404，故当前pin3.14.4只用于隔离P0/P1验证；真实接入前更新到可取得的最新安全补丁并重跑相关检查，不静默沿用旧补丁。[Python3.14.8发布](https://www.python.org/downloads/release/python-3148/)、[uv下载元数据设置](https://docs.astral.sh/uv/reference/settings/#python-downloads-json-url)

工程/数据库/私有文件布局唯一维护在[存储规则](../design/platform/storage-lifecycle.md#阶段0路径与阶段1保存单位)，操作身份/保存单位与有限重试在[共同执行](../design/shared/execution-recovery.md#p0操作身份保存与有限接续)。锁定安装与检查命令见AGENTS，检查只验证声明范围。

### 模型适配层、初始化与探针

模型经platform的模型适配层接入，设计见[模型服务](../design/platform/models.md)，决定见[ADR-0013](../decisions/ADR-0013.md)。`project-j-probe`保留为登记检查中的离线自检：列出已登记适配器及其声明能力，不读取私有模型配置、不联网、不读取麦克风。真实探测统一经初始化命令（第一期已实现；实际API与订阅接入未验证）。用ChatGPT订阅经Codex SDK接入：

```sh
project_j/scripts/uv.sh run --offline --locked --project project_j/python project-j-init login codex
project_j/scripts/uv.sh run --offline --locked --project project_j/python project-j-init configure --default codex:gpt-6.1-sol:xhigh --purpose classroom_judgment=codex:gpt-6.1-sol:low:fast
project_j/scripts/uv.sh run --offline --locked --project project_j/python project-j-init probe
project_j/scripts/uv.sh run --offline --locked --project project_j/python project-j-init status
```

四个用途（planning学习规划、preparation备课、classroom_judgment课堂判断、evaluation课后评价）先取默认绑定，再按`--purpose 用途=适配器:模型:档位[:fast]`逐项覆盖，可各绑不同适配器。末尾`:fast`选Fast层级，目前只有Codex适配器提供，对应订阅列表中的priority层级（标称2倍速度、额度消耗增加）；SDK不回报每轮实际层级，记未核实。用API密钥时改为先`project-j-init set-key openai`，绑定写`openai:gpt-6.1-sol:medium`等，其余相同。

Codex适配器使用官方Python SDK `openai-codex`（锁定0.163.0，随包附带codex运行时），在本机启动`codex app-server`；login打开ChatGPT浏览器登录，登录状态存于`project_j/private/models/codex-home/`，不读也不改家长全局的`~/.codex`。每次调用开一个不留存的新会话，空临时目录、只读沙箱、拒绝全部提权，以中性基础指令替换Codex自带的代理提示词，用途指令作开发者指令，按pydantic严格JSON结构输出，档位按绑定逐轮传入；配置覆盖关闭记忆、历史、项目说明文档、网页搜索、分析统计、shell工具、应用、插件、技能及环境/权限/应用/协作说明（后几项据真实运行实测：去掉插件与技能注入后输入不再逐次增长，剩余约1.1万token为运行时自带内容）。返回条目出现任何工具调用即记“工具”明确不符而失败。取消与超时会请求服务端中断该轮。SDK不回报每轮实际模型与档位，二者记未核实；探针以订阅模型列表核对模型与所声明的档位，记录鉴权、结构化输出、工具关闭、取消、图片输入与耗时。

set-key以隐藏输入把密钥写入本地模型配置，不放命令正文、日志或Git。OpenAI适配器使用Responses API，请求指定模型、reasoning.effort、default层级、store=false与严格结构，SDK自动重试关闭；探针逐项记录实际模型、档位、层级（已核实、未核实或不支持）及结构化输出、取消、图片输入、request_id、可取得用量与耗时。明确不符判失败，未核实如实记录，不能靠提示词宣称Low。探测只用虚构技术样本，单请求耗时不是声音延迟；完整参考题集语义验证、课堂总期限/取消和Medium实际内容核验另做。OpenAI API实际调用样本数为0。Codex订阅2026年10月16日实测：probe准入通过，6个课堂判断样例6/6与预期一致，单次4.9–8.8秒、课堂判断中位约5.4秒，不能满足声音延迟目标，备课与课后评价等非实时用途可用。2026年10月18日同6个样例逐题交替比较：默认层级中位6.15秒（5.14–9.54），Fast层级中位5.25秒（4.19–7.64），约快15%，24次判断均与预期一致；每次输入约1.13万token（多为Codex运行时自带内容）、输出约37 token，Fast主要加速输出，对此帮助有限。按Fast配置重新probe通过，经正式入口6次调用服务端均回报priority，中位5.63秒；仍不满足声音延迟目标。改为xhigh后重新probe通过，xhigh探测7.1秒（推理122 token）；第一期课后评价占位调用（只带活动编号，结果不采用）两次11.4与12.2秒，在60秒期限内；完整评价内容接入后须按实际输入复测期限。

### 本机发音评分初始化与探针

评分器按[ADR-0015](../decisions/ADR-0015.md)是独立子工程`project_j/scorer/`（Python 3.14.4，torch 2.14.1、transformers 5.20.1，锁文件只解析Apple Silicon Mac），与主工程、合成各自维护。主工程经`project-j-scorer-init`初始化，模型与记录存于`project_j/private/scorer/`：

```sh
project_j/scripts/uv.sh sync --locked --project project_j/scorer
project_j/scripts/uv.sh run --offline --locked --project project_j/python project-j-scorer-init download
project_j/scripts/uv.sh run --offline --locked --project project_j/python project-j-scorer-init probe
project_j/scripts/uv.sh run --offline --locked --project project_j/python project-j-scorer-init status
```

download按固定修订ae45363bf3413b374fecd9dc8bc1df0e24c3b7f4只取4个文件并逐一核对SHA-256，是评分唯一的联网步骤；评分进程每次启动重新核对摘要、离线加载并预热，经标准输入输出每行一个JSON通信，不开端口、不写临时文件。probe用macOS自带语音Samantha在本机生成三段英文（正确、把bridge读成brige、约100词带停顿慢读），要求正确朗读无标出、读错一段只标出bridge，慢读只记耗时与内存；记录写入init.json，模型清单或评分器版本、门槛版本变化即需重新探针。`score 文件.wav --text "原文"`供成人自测，只打印不保存；QuickTime录音可先用`afconvert -f WAVE -d LEI16@16000 -c 1 输入.m4a 输出.wav`转换。录音超过40秒按40秒一窗（两侧各加2秒上下文）推理，以限制注意力内存。

2026年10月18日在M3 Pro上的真实探针：MPS，加载约5.8秒（含预热），GPU分配峰值3.30 GB；3秒录音评分约70毫秒，44秒慢读约1.2秒。用6个macOS英文语音朗读整篇故事复测：Samantha无误报；其他语音各有1–2个口音相关误报（如falls、bends、Mia），Flo有6个；整句漏读能标出，但其前后的词会被误标；把paper读成peeper未被标出。慢读长段在Samantha上仍有4个误报（模型在该上下文中把wants、toy、car的元音听成别的音，得分接近0，与真实读错无法靠门槛区分）。这些只说明合成成人语音上的表现，真人与儿童效果未验证，门槛仍为暂定。

### 本机语音合成初始化与探针

合成器按[ADR-0016](../decisions/ADR-0016.md)是独立子工程`project_j/tts/`（Python 3.14.4，mlx-audio 0.5.9，锁定mlx 0.32.4、transformers 5.20.1，锁文件只解析Apple Silicon Mac），与主工程、评分器各自维护。主工程经`project-j-tts-init`初始化，模型与记录存于`project_j/private/tts/`：

```sh
project_j/scripts/uv.sh sync --locked --project project_j/tts
project_j/scripts/uv.sh run --offline --locked --project project_j/python project-j-tts-init download
project_j/scripts/uv.sh run --offline --locked --project project_j/python project-j-tts-init probe
project_j/scripts/uv.sh run --offline --locked --project project_j/python project-j-tts-init status
```

download按固定修订52f4770fd9726457eae3d3b6aa92047a25a10776只取运行所需的12个文件（约4.5 GB）并逐一核对SHA-256，是合成唯一的联网步骤；合成进程每次启动重新核对摘要、离线加载并预热，经标准输入输出每行一个JSON通信，按约0.32秒一段输出24 kHz单声道16位PCM，收到取消即在下一段边界停止，不开端口、不写文件。声音配置版本teacher-1包括音色Serena、老师语气指令与显式的采样参数，改任何一项即升版本并须重新探针；每次文字不超过1000字符，生成上限为5秒加每字0.5秒（不超过4096个语音token），达到上限记为未正常结束。probe合成中文、英文、中英混读三句，要求都有声；合成一段长文并在第一段后取消，要求2秒内确认停止且之后还能合成；固定种子两次合成是否相同只记录；记录写入init.json。`speak --text "…" --out 文件.wav`供成人试听，只写到尚不存在的文件。

2026年10月18日在M3 Pro上的真实探针：GPU，模型刚复制后的首次加载41.6秒（冷读4.5 GB，含摘要核对与预热），再次加载6.7秒；三句首段0.27–0.41秒，RTF 0.74–0.78；取消后0.23–0.24秒停止；固定种子两次合成相同；内存峰值5.11 GB。用本机评分器给英文样本打分，只标出人名Mia（评分器此前对Mia结尾的音也有口音误报），其余11词正常。音色与听感须成人试听，课中实际声音、延迟与GPU争用下的表现未验证。

### 成人实际检查入口

模型、评分与合成的初始化都显示就绪后，用`project_j/scripts/start.sh --adult-check --run-id <id>`启动成人实际检查：同一回环后端，课堂判断等用途按project-j-init的真实绑定（课堂判断codex:gpt-6.1-sol:low:fast），朗读交本机评分器，Jane的声音由本机合成器流式生成；数据写`project_j/private/adult_checks/<run_id>/`，对象realm为adult_check、来源adult，课程仍为固定模板备课。该入口不能与`--simulation-root`、`--model-delay`、`--fail-once`同用。

评分与合成两个本机进程在服务启动时各加载一次（再次加载约6–7秒，模型刚复制后首次更久），启动失败时服务照常运行、开课准入列出原因；停止服务时关闭两个进程与Codex运行时。M3 Pro（18 GB）上合成峰值约5.1 GB、评分约3.3 GB，同时常驻。学生页经`GET /api/student/speech/{action_id}`取已批准动作的24 kHz单声道16位PCM流，声音先出、播完再显示文字（见[实时语音](../design/platform/realtime-voice.md#播放与打断)）；朗读录音为16 kHz单声道WAV，接口上限10 MiB（容纳300秒）。该入口只供成人检查，不开放儿童入口；课中不运行工程检查或Git。

### 课堂判断参考题集

题集在courses/paper-bridge/judgment-reference.json，共37题：两课四道回答题的标准答案、换说法、纯中文、中英混答、错答、含糊、连续三次求助与帮助后再答，另有4道朗读纠正题（给模型构造的发音评分结果，不录音）。多数作答不在课程包答案表里，测的是语义判断。何时重跑或补题以[总验收](acceptance.md#最小版本质量门槛)为准。

模型初始化显示课堂判断就绪、且没有课堂在运行时，用`project_j/scripts/uv.sh run --offline --locked --project project_j/python project-j-judge-reference --run-id <id>`运行（`--repeats N`每题调用N次）。请求与真实课堂逐字段相同（同一指令、Jane方法、本课目标、核心求助判定，截止10秒），顺序调用、不重试，超时或格式无效都算不通过。自动比对判断标签、原文依据是否原样返回（课堂会拒收改写）、禁止词（防泄露答案，子串粗查）与必含词（如第二次求助须给句框）；报告写`project_j/private/adult_checks/<run_id>/judgment-reference.json`，保存每题回应原文供成人逐条阅读。全部通过退出码0，有不通过1，模型未就绪、题集与课程不一致或报告已存在为2。

不通过时先修代码、调整指令或限制用途，不改预期迎合模型。报告只是判断质量与判断耗时的证据，不是声音延迟，也不授予使用资格。一次约37次调用，每次输入约1.1万token。

### P0官方事实复核与接入缺口

以下在2026年10月12日重新打开官方页面核对；页面说明不等于本账户权限或配置通过。

| 项目 | 官方说明/实际状态 | 继续条件 |
| --- | --- | --- |
| Sol Low/Medium | 官方模型页支持两档及结构化输出；本机仅SDK mock/假运行时通过，实际API与订阅**未验证** | 订阅经project-j-init login codex登录，或API项目/密钥、模型权限、费用归属与测试预算具备后，经project-j-init probe探测；课堂参考题集与Medium内容另验 |
| 服务层级 | 官方列fast=标准2倍、ultrafast=6倍；OpenAI适配器只发default，实际计费/可用性**未验证**；Codex订阅课堂判断按家长2026年10月18日决定用Fast层级，实测约快15%，额度消耗未计量 | 记录返回层级/用量；更高成本层级待家长按实际成本决定，不为延迟暗改 |
| 转写WebRTC | 官方推荐gpt-live-transcribe，客户端commit且无server_vad/semantic_vad；本批未建音频连接 | 2026年10月16日按[ADR-0014](../decisions/ADR-0014.md)不再采用，作答改为文字/点选，只有英语朗读录音交发音评分；本行保留为当时核对记录 |
| 凭据/数据条件 | 官方WebRTC示例有client_secrets及calls；数据控制保留表未单列这些或transcription_sessions。该页区域服务清单提到transcription_sessions，不能由此推断ZDR覆盖 | ADR-0013起不以ZDR为数据条件，记录实际发送范围与条款；儿童入口按验收关闭 |
| Responses/合成/按轮转写保留 | 官方表列responses、audio/speech、audio/transcriptions与realtime有ZDR资格，带适用限制；本账户资格**未验证** | 记录所选路径的发送范围与保留条款，store=false不代账户批准 |
| 声音与设备 | 家长选择当前Mac内置麦克风＋内置扬声器，Chrome候选已记录版本；未请求麦克风或播放声音 | 成人在场授权设备、试听中英固定声音；回声/插话/停止/延迟实测，不能移用其他设备检查 |

上述事实来源：[Sol模型页](https://developers.openai.com/api/docs/models/gpt-6.1-sol)、[实时转写](https://developers.openai.com/api/docs/guides/realtime-transcription)、[WebRTC](https://developers.openai.com/api/docs/guides/voice-webrtc?voice-api=realtime)、[数据控制](https://developers.openai.com/api/docs/guides/your-data)。实际API调用样本数为0、语音操作样本数为0；账号条件缺失可继续隔离阶段1，不能把文档核对当接口通过。

### 合成维护风险与最小待决定方案

官方停用表于2026年10月1日公告gpt-4o-mini-tts的2025-03-20和2025-12-15两个快照将于2027年1月6日停用，并推荐gpt-realtime-2.1-mini；TTS指南仍有原模型示例，当前alias的具体路由/账户可用性**未验证**。[停用公告](https://developers.openai.com/api/docs/deprecations)、[TTS指南](https://developers.openai.com/api/docs/guides/text-to-speech)

最小提案：真实合成验证时增加该候选的成人对照，要求只能交付Harness已批准文本、实际内容/首播范围可核对、取消和迟到可靠，并重新核对费用/数据覆盖；取得结果后向家长提出具体替换配置。当前不替换gpt-4o-mini-tts、不引入自主语音教师，阶段1仍以模拟合成交付验证同一接口。此提案尚待家长确认，候选官方推荐不表示J条件已满足。

2026年10月18日家长按[ADR-0016](../decisions/ADR-0016.md)改用本机合成（Qwen3-TTS 1.7B），不再接入gpt-4o-mini-tts；上述停用事实与提案保留为当时核对记录。

P0-R1按Opus工程审查采用安装后的project-j-probe/project-j-measure入口，不修改src搜索路径；Python版本由.python-version维护。pytest-socket负责禁网，httpx2为显式开发依赖；文档检查交remark/unified-engine/remark-validate-links官方API，未配远程origin时关闭其repository探测，仍检查本地文件与标题。初试remark-cli时审计发现其监听依赖含无补丁braces告警，故删除CLI及监听依赖；不降级、不强制跨主版本覆盖、不恢复自制链接规则。实际检查和有限审查范围见[交接](handoff.md#p0-r1-工具清理)。

## P1隔离运行与生成工具

FastAPI单后端仅监听127.0.0.1，Vite产物和固定材料在课堂开始前读入内存；课堂中的数据库/固定文件/导出/备份访问禁止。家长临时访问能力和单活动学生访问能力分开，学生不获取简报/答案/帮助分支。配置由pydantic-settings载入PROJECT_J_前缀，默认不读.env，提供者和数据空间固定模拟；设置课堂low/课后medium只说明目标配置，不冒称真实请求参数已生效。

Python新增pydantic-settings2.15.0、tenacity9.2.1、uvicorn0.54.0、websockets17.2，由uv.lock固定。保存使用SQLAlchemy/Alembic/SQLite约束；只有明确未提交的临时失败用tenacity限定次数与原期限，其他保存未知先查询。原P0 guards.py已删除，课堂判断与相关测试在真实调用的Harness边界。

前端采用官方Vite react-ts模板；应用保留TypeScript7.0.2。类型生成需要编译器API，因此web/codegen单独锁TypeScript6.0.3/Hey API0.99.0；解析器同系列固定1.4.1，避免1.4.4的js-yaml4已知告警，不使用force、legacy-peer-deps或跨主版替换。它只读取本地导出JSON并生成类型；两份锁、生成/编译/构建及审计均需检查，完整证据见[交接](handoff.md)。

当前模拟无需API账号、密钥或预算。未来“账号条件”指J实际调用所用的API组织/项目、调用凭据、模型/语音权限及费用归属；“账单范围”应理解为允许的调用与测试预算，不要求开放账单管理权限。订阅类适配器使用家长在对应工具中的订阅登录，额度与条款按订阅另计；开发代理自身的登录不自动成为J的模型接入。

本机工作台/单活动学生的临时访问令牌只用于J的loopback访问控制，不是服务商API密钥，不代表模型权限或产生模型调用费用。
