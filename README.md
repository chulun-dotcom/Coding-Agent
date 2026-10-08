# Coding-Agent（SHI Agent / RepoAgent）

本地 Coding Agent：输入一个干净的 Git 仓库和任务，Agent 在独立副本中搜索、读取、修改代码，在受限 Docker 容器中运行测试，最终保存补丁、验证报告和执行记录。源仓库不会被修改。

## 已实现

- 单 Agent 工具循环：文件列表、代码搜索、分段读取、补丁编辑、精确文本替换、命令、测试、Diff；模型动作使用明确的 JSON 契约。
- Python/pytest 和 Java/Maven 两种执行配置；容器默认无网络、非 root、资源受限，工作区的 `.git` 只读。
- 基线与最终验证、失败后继续修复、可选的工作区外只读验收测试、步数/时间/Token 预算；成功补丁会在干净副本上检查可应用性。
- SQLite 任务索引、JSONL 事件、文件快照；中断后从最近稳定快照恢复，不自动重放未完成的命令；取消会停止正在运行的容器。
- Python AST 与 Java 定义的 Repo Map，每次按当前工作区重建；上下文按大小限制最近事件。
- CLI、仅监听本机的 FastAPI、SSE 事件流、批量评测和 CI。

支持 DeepSeek 等兼容 Chat Completions 的模型服务，密钥只需保存在被 Git 忽略的 `.env`。真实模型已完成代码修改、Docker 测试和独立验收；脚本模型与模拟 HTTP 响应用于稳定的自动化测试。MCP 外部工具、GitHub Issue/PR 自动交付尚未实现。

## 安装与验证

需要 Python 3.11+、Git、Docker Desktop Linux containers。Docker 镜像首次构建需要网络。

```powershell
python -m pip install uv
python -m uv sync --extra api
docker pull python:3.13-slim
docker pull maven:3.9-eclipse-temurin-21
docker build --pull=false -t shi-agent-python:test docker/python
docker build --pull=false -t shi-agent-java:test docker/java
$env:SHI_AGENT_RUN_DOCKER_TESTS = "1"
python -m uv run python scripts/run_tests.py
python -m uv run python scripts/demo.py
```

`demo.py` 不需要模型密钥。它临时创建示例 Git 仓库，走完读取、补丁、容器测试、最终验证和导出。产物在 `data/tasks/<task_id>/artifacts/`，包括 `report.json`、`summary.md` 和 `patch.diff`。当前版本已通过 46 项测试，包含 Docker 集成测试。

## 运行真实任务

在项目根目录复制 `.env.example` 为 `.env`，填写 `SHI_AGENT_API_KEY`。示例使用 DeepSeek；换用其他兼容接口时同时修改 `SHI_AGENT_MODEL` 和 `SHI_AGENT_BASE_URL`。程序会自动读取当前目录的 `.env`，已有进程环境变量优先；也可用 `SHI_AGENT_ENV_FILE` 指定其他文件。`.env` 已被 Git 忽略，不要把密钥写入其他仓库文件。源仓库须无未提交或未跟踪文件。

```powershell
Copy-Item .env.example .env
notepad .env
```

```powershell
python -m uv run shi-agent doctor
python -m uv run shi-agent run --repo C:\path\to\clean-repo --task "修复分页边界错误" --profile python
python -m uv run shi-agent status <task-id>
python -m uv run shi-agent cancel <task-id>
python -m uv run shi-agent resume <task-id>
python -m uv run shi-agent export <task-id> --output C:\path\to\result
```

如需独立验收，把验收 pytest 文件放在源仓库外，并给 `run` 增加 `--acceptance C:\path\to\hidden-tests`。该目录只在独立验收容器中只读挂载，Agent 的命令容器无法访问；常规测试通过后才执行独立验收，失败不会把隐藏测试反馈给模型继续修改。

Java 项目用 `--profile java`；镜像预装了示例 JUnit 5 依赖，项目若有其他依赖，应先扩展 `docker/java/Dockerfile` 并重建镜像。运行阶段保持容器无网络。

批量评测：每行一个 JSON 对象，字段为 `name`、`repo`、`base_commit`、`instruction`、`profile`、`max_steps`，可选 `acceptance`。`repo` 和 `acceptance` 可以是相对 manifest 的路径。

```powershell
python -m uv run python scripts/prepare_evaluation.py
python -m uv run shi-agent evaluate --manifest data/evaluations/dev.jsonl
```

准备脚本生成 5 个固定任务及仓库外验收测试，并记录准确的基准 commit。五个任务已核对：可见基线均通过，隐藏验收在未修复时均失败。评测结果写入 `data/evaluations/latest.json`，保留成功与失败任务的状态和原因。实际评测需要模型密钥；脚本演示不计入模型完成率。

2026-10-08 的 DeepSeek 评测在每题 25 步限制下通过 5/5，所有任务均通过仓库外独立验收。运行评测后，结果保存在 `data/evaluations/`，任务日志保存在 `data/tasks/`；这些本地结果与密钥都不会进入 Git 仓库。测试条件和局限见[真实模型评测记录](docs/真实模型评测.md)。

代码排版与导入检查：

```powershell
python -m uv run ruff format --check src tests scripts examples
python -m uv run ruff check src tests scripts examples
```

本地页面与 API：

```powershell
python -m uv run shi-agent serve --host 127.0.0.1 --port 8000
```

打开 `http://127.0.0.1:8000/` 可使用单页任务台：提交仓库任务、查看实时步骤、测试结果、Diff 与完整报告。`/docs` 提供 API 文档。主要接口：`POST /tasks`、`GET /tasks`、`GET /tasks/{id}`、`POST /tasks/{id}/cancel`、`POST /tasks/{id}/resume`、`GET /tasks/{id}/events`、`GET /tasks/{id}/artifacts`、`GET /tasks/{id}/artifacts/{name}`。事件流支持 `Last-Event-ID` 续接。页面不接收模型密钥；服务仅设计用于可信开发者的本机环境，不要直接暴露到公网。

设计与验收标准见 [项目设计文档](docs/项目设计文档.md)。
