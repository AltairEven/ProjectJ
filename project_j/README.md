# Project J 隔离模拟工程

P1首版工程以现有[共同合同](../design/shared/README.md)实现D1 → C1 → A1 → F1 → FB1/E1 → 状态/计划处理 → D2 → 实际保存的C2。模型判断与评价是明确模拟，文字呈现有交付回执，识别/音频只接收技术模拟信号。Opus审查及GPT对P1-R1的复审均已取得，剩余3项在P1-R2由GPT修复并自测，现准备隔离模拟调试；新修复待Opus独立审查，真实API、声音、成人时长、儿童入口和教学效果仍未验证。

## 安装、检查和启动

从仓库根运行[AGENTS登记的命令](../AGENTS.md)：项目级uv同步、两个npm ci与审计、统一check、默认离线probe及模拟measure。统一check导出Python拥有的OpenAPI、生成前端类型、检查/构建页面、检查本地文档及运行临时库测试；全部成功才产生与工程摘要匹配的模拟检查依据。修改工程后原依据失效，不手改就绪字段。

完成检查后启动：

```sh
project_j/scripts/start.sh
```

默认仅监听127.0.0.1:8765，使用终端输出的临时工作台链接；链接中的本机访问能力不保存到Git或导出。工作台准备/预览后显式开始虚构课堂，学生链接在独立窗口打开。学生只能取得核心批准的内容与相应固定材料，不取得答案、帮助分支或教师简报；学生凭证只可停止、暂停、请求重复和离屏返回，恢复、跳过、收尾和未确认结束属于家长控制。家长可停止、查看封存、追加一次反馈或明确稍后，再按需准备C2；C2模板由当前有效D2决定，并逐项列出D2要求的落实或沿用理由。

课堂运行期间不执行工程检查、Git或截图；数据库、固定文件、导出与备份访问由存储门禁止。停止先取消核心和本端呈现，再独立封存。失败只保留明确缺口，按原操作查询/接续；进程丢失前未封存的内存不可伪造恢复。Ctrl-C可控结束由核心标中断并尝试保存已取得范围，不称正常完成。

## 布局与职责

| 位置 | 用途 |
| --- | --- |
| python/src/project_j/application/ | 版本化用例、请求/步骤台账、配置及模拟检查依据 |
| python/src/project_j/learning/、teacher/、preparation/、curriculum/ | 状态/计划/需求及复盘、唯一Jane、固定模板编制、内容核验和影响限制 |
| python/src/project_j/classroom/、interface/ | 单活动内存Harness、提交/识别修订、HTTP/WS及角色访问 |
| python/src/project_j/evaluation/、platform/ | 有据模拟解释/建议、SQLite迁移/不可变保存/回执、固定资产与一致恢复、模拟能力 |
| python/tests/ | 明确模拟输入、临时目录、故障注入和SDK MockTransport |
| web/src/ | React家长工作台与虚构课堂；不拥有评分或转场规则 |
| web/schema/、web/src/client/ | Python导出的OpenAPI及生成类型；通过命令再生成，不能手改另一份合同 |
| web/codegen/ | 独立锁定的生成工具；TypeScript6.0.3供编译器API，应用仍用TypeScript7.0.2 |
| scripts/ | 项目级环境、统一检查与本机启动入口 |

类型生成使用Hey API0.99.0；传递解析器在同系列固定1.4.1，该版使用yaml2.8.3，避开1.4.4的js-yaml4告警。只输入本地导出的JSON，只生成类型；实际生成/类型编译/构建/审计验证此组合，不使用force或legacy-peer-deps。该工具独立锁定不更换应用技术基线。

解释器在project_j/.python，虚拟环境在python/.venv，依赖分别由uv.lock和两份npm锁固定；缓存/检查产物在.cache，构建产物在web/dist，均忽略。模拟数据库为private/simulation/<run_id>/project_j.sqlite3，素材/导出/备份在同目录，目录0700、文件0600；不创建family或adult_checks区。检查用系统临时目录，不写真实学习状态。

P0独立project-j-probe默认不发请求、不加载.env；其显式真实入口和未来接入条件见[技术文档](../docs/technology.md)，本批未执行。project-j-measure读取40轮明确模拟JSON，不能证明任何实际声音延迟。当前检查范围、限制与审查入口见[交接](../docs/handoff.md)。
