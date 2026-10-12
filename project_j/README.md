# Project J 阶段0工程入口

仅有P0独立合同与验证工具，没有可运行应用、课堂或儿童入口。业务规则以[shared](../design/shared/README.md)为准；阶段1最小切片在[路线](../docs/roadmap.md)。

- `python/src/project_j/`：部分Pydantic信封/提交字段、纯守卫、延迟分析和显式模型探针。
- `python/tests/fixtures/`：E2E01预期引用骨架、固定参考题集和模拟计时。不是已保存业务对象或实际课堂数据。
- `scripts/`：项目级uv环境、离线检查、探针和课后测量分析入口；检查命令唯一登记在[AGENTS](../AGENTS.md)。
- `python/uv.lock`：实际锁定依赖；`.python-version`固定3.14.4。项目环境/解释器和缓存均忽略，不改全局设置。

安装用`project_j/scripts/uv.sh sync --locked --project project_j/python`。uv依据pin在project_j/.python下载受维护的3.14运行时，并在python/.venv安装依赖；当前选择3.14.4的原因与补丁差异见[技术基线](../docs/technology.md)。Node26.11.1/npm11.20.0及React/Vite/TypeScript基础依赖在web/package-lock.json锁定，只有依赖导入检查，没有前端源码或运行页面；阶段1建立薄界面。安装和导入检查也登记在AGENTS。

`probe.py`默认离线，只报告密钥是否存在及目标配置；显式`--live`才发一条固定虚构技术请求，要求非秘密账号/项目标签，SDK自动重试关闭。具体命令与接入核验在技术文档。单次探针不证明完整参考题集、语音取消、儿童数据资格或教学效果。

`measure.py`只分析完成后提供的JSON，所有阶段时间投影到同一个浏览器单调时钟；不能用服务器合成完成冒充首播。模拟文件包含40轮及2次超时，专门检查分母/失败和门槛计算，输出始终不授予使用资格。实际数据只在允许的课后时点保存到private/adult_checks；P0不采音、不创建正式数据库。
