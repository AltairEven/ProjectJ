# Project J 开发环境搭建

版本：1.2.2；更新：2026年11月21日（导出含扩写的书`books/`，[ADR-0035](../decisions/ADR-0035.md)）。

在一台新Mac上从零搭好开发与正式学习环境，或从旧电脑迁移过来。本文只排顺序；登记检查以[AGENTS](../AGENTS.md)为准，初始化细节与继续条件见[技术选型](technology.md)，两者与本文不一致时以它们为准。所有命令都从仓库根运行。

## 1. 前提

- Apple Silicon的Mac：评分用MPS、合成用MLX，两个子工程的锁文件只解析Apple Silicon。
- 磁盘预留约10 GB：三套Python环境与前端依赖约1.8 GB，uv缓存约1.7 GB，本机评分与合成模型约5.4 GB。内存紧张时模型加载会从十几秒拖到一分多钟。
- 自行安装的全局工具只有三样：Git、uv（当前用Homebrew装的0.11.11）、Node 26.11.1与npm 11.20.0（`project_j/web/package.json`的devEngines不符即报错）。
- 不需要另装：Python 3.14.8由`project_j/scripts/uv.sh`按`project_j/scripts/python-downloads.json`下载到`project_j/.python`并核对摘要；espeak随评分依赖安装；评分探针用的语音Samantha是macOS自带的。
- 正式学习需要能登录Codex的ChatGPT账号。

## 2. 取得代码

仓库目前没有远端。可以从旧电脑拷贝整个项目文件夹，或先配置一个私有远端再克隆。拷贝时以下目录与本机绑定，不必带走，第3步会重建：`project_j/.python`、`project_j/.cache`、`project_j/python/.venv`、`project_j/scorer/.venv`、`project_j/tts/.venv`、`project_j/web/node_modules`、`project_j/web/codegen/node_modules`、`project_j/web/dist`。`project_j/private/`的处理见第5步。

## 3. 安装依赖并检查

```sh
project_j/scripts/uv.sh sync --locked --project project_j/python
project_j/scripts/uv.sh sync --locked --project project_j/scorer
project_j/scripts/uv.sh sync --locked --project project_j/tts
npm ci --prefix project_j/web --ignore-scripts --no-audit --no-fund --cache "$PWD/project_j/.cache/npm"
npm ci --prefix project_j/web/codegen --ignore-scripts --no-audit --no-fund --cache "$PWD/project_j/.cache/npm"
npm audit --prefix project_j/web --cache "$PWD/project_j/.cache/npm"
npm audit --prefix project_j/web/codegen --cache "$PWD/project_j/.cache/npm"
project_j/scripts/uv.sh run --offline --locked --project project_j/python python project_j/scripts/check.py
project_j/scripts/uv.sh run --offline --locked --project project_j/python project-j-probe
project_j/scripts/uv.sh run --offline --locked --project project_j/python project-j-measure project_j/python/tests/fixtures/latency_simulated.json
```

一律经`project_j/scripts/uv.sh`调用uv：它把解释器、缓存和环境限定在project_j里，并指向固定的Python下载清单；直接用全局uv会下载不到3.14.8。评分子工程含PyTorch，首次同步约1–2分钟。统一检查全部通过后写出模拟检查依据，此时`project_j/scripts/start.sh`即可启动模拟运行，不需要真实模型。

## 4. 接入真实模型与本机语音

正式学习需要三项初始化，每项的status都显示就绪才算完成：

```sh
project_j/scripts/uv.sh run --offline --locked --project project_j/python project-j-init login codex
project_j/scripts/uv.sh run --offline --locked --project project_j/python project-j-init configure --default codex:gpt-6.1-sol:xhigh --purpose classroom_judgment=codex:gpt-6.1-sol:low:fast
project_j/scripts/uv.sh run --offline --locked --project project_j/python project-j-init probe
project_j/scripts/uv.sh run --offline --locked --project project_j/python project-j-init status

project_j/scripts/uv.sh run --offline --locked --project project_j/python project-j-scorer-init download
project_j/scripts/uv.sh run --offline --locked --project project_j/python project-j-scorer-init probe
project_j/scripts/uv.sh run --offline --locked --project project_j/python project-j-scorer-init status

project_j/scripts/uv.sh run --offline --locked --project project_j/python project-j-tts-init download
project_j/scripts/uv.sh run --offline --locked --project project_j/python project-j-tts-init probe
project_j/scripts/uv.sh run --offline --locked --project project_j/python project-j-tts-init status
```

login会打开浏览器登录ChatGPT，登录状态只存在`project_j/private/models/codex-home/`。两个download按固定修订下载并逐一核对SHA-256，是评分与合成唯一的联网步骤。说明与探针内容见技术选型的[模型适配层](technology.md#模型适配层初始化与探针)、[发音评分](technology.md#本机发音评分初始化与探针)和[语音合成](technology.md#本机语音合成初始化与探针)三节。

## 5. 从旧电脑迁移私有数据

`project_j/private/`不进Git，按内容分别处理：

| 内容 | 位置 | 做法 |
| --- | --- | --- |
| 学习记录 | `project_j/private/study/<run_id>/`（如me） | 唯一无法重建的数据，用下面的导出导入搬运；不搬则首次打开重新建档 |
| Codex登录状态 | `project_j/private/models/codex-home/` | 不要拷贝，在新电脑按第4步重新登录 |
| 评分与合成模型 | `project_j/private/scorer/`、`project_j/private/tts/` | 可以整个拷过来省掉下载，之后仍在新电脑运行两个probe |

学习记录在旧电脑停止服务后导出，拿到新电脑上，在启动服务之前导入：

```sh
project_j/scripts/uv.sh run --offline --locked --project project_j/python project-j-transfer export --run-id me ~/Desktop/me.tar.gz
project_j/scripts/uv.sh run --offline --locked --project project_j/python project-j-transfer import --run-id me ~/Desktop/me.tar.gz
```

导出文件含数据库、`lessons/`（课程文件夹）、`drafts/`（未确认资源的课）、`vocabulary/`（生词发音）、`books/`（扩写的书）里的全部文件及其SHA-256清单，只本人可读；里面是你的作答与评价，不加密，自己保存，不要放进仓库或上传共享。服务运行时导出会拒绝。导入只建一个新的运行编号：新电脑上已有me（例如已经启动过并建档）就拒绝，确认不要那份后在服务停止时用project-j-reset清空再导入；导入先放在暂存目录，全部核对通过才改名为me，失败时me不受影响。旧版本导出的包里书在`readings/`，导入后自动改为`books/`。

## 6. 启动

- 正式学习：双击`project_j/scripts/study.command`（可拖到程序坞右侧单击），以运行编号me启动，服务开始监听后自动打开页面；也可用`project_j/scripts/start.sh --study --run-id <id>`，见[正式学习入口](technology.md#正式学习入口)。停止时在终端按Ctrl+C或关闭终端窗口，进行中的课按中断封存。双击启动时诊断日志另存在`project_j/private/logs/`，每次启动一个文件。
- 模拟运行：`project_j/scripts/start.sh`，使用终端输出的链接。
- 课堂沙盒：`npm run --prefix project_j/web sandbox`后打开http://127.0.0.1:5174/，不连后端、不保存数据。

## 7. 不用迁移的

`project_j/test/`是本机调试数据与产物，不进Git，工程不依赖它；`.codegraph/`、`.superpowers/`是开发工具的本机数据，会自动重建或可丢弃。
