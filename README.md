# Echo Careers

Echo Careers 是一个简历驱动的岗位发现工作台。用户可以上传简历或选用公开样例，配置职位词和硬条件，查看岗位原文、证据分和关键缺口。常规搜索使用 Himalayas 的公开岗位 API。岗位 Agent 可以在受限预算内继续搜索、核对 JD、检索简历证据、询问缺失条件和生成推荐。招聘方联系与投递由用户自行进行。

## 本地启动

- Windows 本地工作台：双击 `START.cmd`，打开 `http://127.0.0.1:8766/`。也可用 `start.ps1` 启动已安装依赖的环境。此模式沿用已有本机数据库和 Windows 依赖清单。
- 公开体验容器：从包含 Dockerfile 的目录运行以下命令。Docker 镜像采用独立的 Linux 依赖锁，启动使用单 worker。Chroma 与 Playwright 属于可选的 Windows 本地能力，公开服务使用轻量检索，不加载浏览器控制入口。

```bash
docker build -t echo-careers .
docker run --rm -p 8766:8766 echo-careers
python scripts/smoke_public.py http://127.0.0.1:8766
```

如要验证端口注入，运行 `docker run --rm -e PORT=9000 -p 9000:9000 echo-careers`，再访问 9000 端口。进程通过 `exec uvicorn` 接收停止信号。

## GitHub → Render Blueprint

1. 从当前项目运行 `python scripts/export_public.py data/echo-careers-public.zip`。解压到独立目录，用新 GitHub 仓库提交解压内容。仓库根目录应直接看到 `render.yaml`、`Dockerfile` 和 `app/`；无需再套一层目录。提交前可运行 `python scripts/export_public.py --check-git` 核对 Git 索引白名单。检查旧仓库的历史提交需另行进行。
2. 在 Render Dashboard 选择 **New → Blueprint**。连接 GitHub 并对目标仓库点击 **Connect**，选择实际分支（通常是 `main`）。Blueprint Path 填 `render.yaml` 或保留默认。
3. 预览应显示一个 `echo-careers` Web Service，运行时 **Docker**，实例类型 **Free**，构建上下文 `.`，Dockerfile 路径 `./Dockerfile`，健康检查 `/health`。点击 **Deploy Blueprint**。
4. 默认环境为 `PUBLIC_MODE=true`、`MODEL_MODE=mock`、`APP_DATA_DIR=/app/data`、`MODEL_TIMEOUT=12`。Blueprint 自动生成 `PUBLIC_SESSION_SECRET`。Render 注入 `PORT` 和 `RENDER_EXTERNAL_HOSTNAME`。如使用自定义域名，通过 `PUBLIC_HOST` 填实际主机名，不含协议。
5. 部署状态为 Live 后，访问 Dashboard 显示的 HTTPS 首页及同域 `/health`。健康接口应返回 `status: ok`、`public: true` 和正确的模型模式。可执行 `python scripts/smoke_public.py https://你的服务.onrender.com`。构建失败看服务的 **Deploys** 对应部署日志，启动或运行失败看 **Logs**。
6. 启用真实模型时，在部署分支的 `render.yaml` 将 `MODEL_MODE` 改为 `live`，并在 Render 服务 **Environment → Add Environment Variable** 保存 `OPENAI_API_KEY`。需要覆盖模型时可设置 `LLM_MODEL_AGENT`，以及可选的 `LLM_MODEL_EXTRACT`、`LLM_MODEL_MATCH`、`LLM_MODEL_REPLY`。密钥只保存在 Render 环境。缺少密钥时在线 Agent 显示规则降级，认证失败时检查密钥权限与模型可用性。

[Blueprint 官方步骤](https://render.com/docs/infrastructure-as-code) · [Docker Web Service](https://render.com/docs/docker) · [Render 日志](https://render.com/docs/logging)

Free 实例可能冷启动，本地 SQLite 和上传文件在休眠、重启、重部署后可能清空。体验结束前可导出 JSON。需要保留数据时，可升级到带持久磁盘的付费实例，挂载 `APP_DATA_DIR` 并长期保留 `PUBLIC_SESSION_SECRET`；多人长期使用应进一步引入登录身份及数据库备份。Render 免费托管与 OpenAI 调用单独计费。 [Render Free 限制](https://render.com/docs/free)

公开容器只打包显式允许的应用文件和脱敏样例。已签名的 HttpOnly 会话 Cookie 决定访客的独立 SQLite、简历文件和知识索引。浏览器连接、BOSS、发送、自动工作流接口在公开服务入口被拒绝；公开数据不使用本地原件。示例数据虚构，在线岗位来自来源公开 API。Windows 单人模式仍可读取其本机配置。

## 三分钟演示

1. 打开首页，点「选用示例简历」。展开「岗位发现 Agent」，学历选择「本科」，运行「演示 · 首轮偏离后换词」。
2. 打开「Agent 执行记录」。核对 3 份博士硬条件与 1 条「博士优先」原文。查看补搜 `AI backend intern` 的事件，再点开推荐岗位看 JD、简历证据和待确认条件。
3. 选择「演示 · 首轮足够直接结束」重跑，对比只搜一轮的轨迹。可再运行地区确认案例，用 `AU` 回答问题，观察同一 run 续跑。页面可导出 JSON；右栏也可更换简历。

在线 Agent 选择「Himalayas 在线岗位」。模型由服务端 Provider 在动作点读取目标、条件、已读材料和预算；程序校验动作、调用固定来源接口、去重、核对硬条件和引用，选择合格推荐。来源 API 随搜索返回 JD；精读使用本轮实际提供的原文，只有摘要时标明证据不足。事件记录只包含动作与可核实结果。可配置 `AGENT_MAX_SEARCHES`、`AGENT_MAX_READS`、`AGENT_MAX_CALLS`、`AGENT_TIMEOUT`，默认分别为 3、10、18、90 秒。刷新读取已持久化的 run；明确澄清后续跑。进程重启时，中断的进行中步骤不自动重放。

岗位详情的「生成 HR 回复草稿」检索当前简历，展示命中片段；确认过的本访客历史问答可被检索作为写法参考。资料不足会显示待确认。

## 跨行业岗位推荐

上传简历后，先查看有原文引用的职业方向，再编辑本轮搜索词。Himalayas 使用相应英文查询，新搜索默认不限岗位类型。更换简历会生成新的建议和搜索草稿。历史批次仍可查看，也可用当前简历重评原候选，重评沿用原提交词和来源。

Windows 本地可使用项目根目录的 `Career_Workbench_Resume_JD_Benchmark/jobs.jsonl`，或在启动前设置 `OCCUPATION_TEMPLATES` 为模板文件路径。Docker 和公开演示使用内置的中英文职业目录。离线评测命令、数据边界和逐例报告说明见 [跨行业岗位基准验证](docs/cross-industry-benchmark.md)。

## 验证与评测

运行 `python -m pytest -q`、`python -m scripts.evaluate_agent`。评测方法、标签、调用次数与限制见 [岗位 Agent 对照评测](docs/agent-evaluation.md)。
