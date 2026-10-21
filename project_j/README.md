# Project J 工程

本机应用按[共同合同](../design/shared/README.md)实现AI自学助理（[ADR-0018](../decisions/ADR-0018.md)）：建档、学习规划、备课与教学资源、课堂、本机发音评分与合成、课后评价及下一课，后台任务由应用协调层自动推进。模拟入口只用模拟提供者；正式学习入口用真实模型、本机评分与本机合成。GPT与Opus按[共建规则](../AGENTS.md#共建协作)分工、自测和交接，不要求互审。教学效果与长期使用仍未验证。

## 安装、检查和启动

从仓库根运行[AGENTS登记的命令](../AGENTS.md)：项目级uv同步、两个npm ci与审计、统一check、默认离线probe及模拟measure。统一check导出Python拥有的OpenAPI、生成前端类型、检查/构建页面、检查本地文档及运行临时库测试；全部成功才产生与工程摘要匹配的模拟检查依据。修改工程后原依据失效，不手改就绪字段。

完成检查后启动：

```sh
project_j/scripts/start.sh
```

默认仅监听127.0.0.1:8765，使用终端输出的临时链接（本机评分与合成加载完、服务开始接收请求时才输出）；`--onboarding`让模拟从建档开始，正式学习用`--study --run-id <id>`（见[技术文档](../docs/technology.md)），开发期清空一个运行编号用`project-j-reset --run-id <id>`；调试和UI测试加`--simulation-root project_j/test/simulation`，目录约定见[AGENTS调试与测试](../AGENTS.md#调试与测试)，可再加`--model-delay 秒数`让模拟模型每次调用等待，或`--fail-once [步骤:]故障点`注入一次保存/台账/模型失败（如fact:before_commit、ledger、model），两者默认关闭；链接中的本机访问能力不保存到Git或导出。主页面左侧导航，主页只给一件下一步；学生在课程详情确认并开始上课后进入全屏课堂，课堂只取得核心批准的内容与相应固定材料，不取得答案、帮助分支或教师简报。下课后评价、采用、规划与备课由后台任务接续，学生认可评价、确认新目标即可。

课堂运行期间不执行工程检查、Git或截图，测试运行的观察例外只按AGENTS；数据库、固定文件、导出与备份访问由存储门禁止。停止先取消核心和本端呈现，再独立封存。失败只保留明确缺口，按原操作查询/接续；进程丢失前未封存的内存不可伪造恢复。Ctrl-C可控结束由核心标中断并尝试保存已取得范围，不称正常完成。

## 布局与职责

| 位置 | 用途 |
| --- | --- |
| python/src/project_j/application/ | 协调对象及操作/保存机制（coordinator.py），按业务分开的用例：备课与确认（preparing.py）、开课与封存（teaching.py）、反馈与评价（evaluating.py）、规划与选择（adopting.py）；请求/步骤台账、后台推进（progress.py）、配置及模拟检查依据 |
| python/src/project_j/learning/、teacher/、preparation/、curriculum/ | 状态/计划/需求及复盘、模拟固定学习者（learning/fixtures.py）、唯一Jane、固定模板编制、内容核验和影响限制 |
| python/src/project_j/classroom/、interface/ | 单活动内存Harness、提交/识别修订、HTTP/WS及操作者令牌 |
| python/src/project_j/evaluation/、platform/ | 有据模拟解释/建议、SQLite迁移/不可变保存/回执、固定资产与一致恢复、模拟能力；本机评分与合成进程共用的进程管理（platform/local_process.py） |
| python/tests/ | 明确模拟输入、临时目录、故障注入和SDK MockTransport |
| web/src/ | React主页面（pages/）与全屏课堂（Classroom.tsx）；不拥有评分或转场规则 |
| web/schema/、web/src/client/ | Python导出的OpenAPI及生成类型；通过命令再生成，不能手改另一份合同 |
| web/codegen/ | 独立锁定的生成工具；TypeScript6.0.3供编译器API，应用仍用TypeScript7.0.2 |
| scripts/ | 项目级环境、统一检查与本机启动入口 |
| scorer/ | 本机英语发音评分独立子工程（ADR-0015）：自带pyproject、锁文件与环境，PyTorch不进主工程；与合成子工程各自维护 |
| tts/ | 本机语音合成独立子工程（ADR-0016）：自带pyproject、锁文件与环境，MLX不进主工程；与评分子工程各自维护 |
| test/ | 本机调试/UI测试的运行数据、产物与记录，不进Git，工程不依赖，第一版正式版发布时删除 |

类型生成使用Hey API0.99.0；传递解析器在同系列固定1.4.1，该版使用yaml2.8.3，避开1.4.4的js-yaml4告警。只输入本地导出的JSON，只生成类型；实际生成/类型编译/构建/审计验证此组合，不使用force或legacy-peer-deps。该工具独立锁定不更换应用技术基线。

解释器在project_j/.python，虚拟环境在python/.venv，依赖分别由uv.lock和两份npm锁固定；缓存/检查产物在.cache，构建产物在web/dist，均忽略。模拟数据库默认为private/simulation/<run_id>/project_j.sqlite3（`--simulation-root`可显式改为另一名为simulation的根目录），正式学习为private/study/<run_id>/，素材/导出/备份在同目录，目录0700、文件0600。检查用系统临时目录，不写真实学习状态。

project-j-probe为离线自检，不发请求、不读私有模型配置；真实探测改由模型适配层的初始化命令project-j-init（第一期已实现，实际API未验证，见[技术文档](../docs/technology.md#模型适配层初始化与探针)）。本机发音评分经project-j-scorer-init下载模型、探针与准入（见[技术文档](../docs/technology.md#本机发音评分初始化与探针)），本机语音合成经project-j-tts-init下载模型、探针、准入与试听（见[技术文档](../docs/technology.md#本机语音合成初始化与探针)）；统一check也运行两个子工程自己的测试；模拟入口仍只用模拟评分器与模拟合成。project-j-measure读取40轮明确模拟JSON，不能证明任何实际声音延迟。当前检查范围、限制与审查入口见[交接](../docs/handoff.md)。
