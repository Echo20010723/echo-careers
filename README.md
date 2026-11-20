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
6. 在现有 Render 服务的 **Environment** 设置 `CLOUDFLARE_ACCOUNT_ID` 和 `CLOUDFLARE_API_TOKEN`。新建 Blueprint 时已默认启用 Agent 开关，两个凭据由 Render 提示填写。已存在的公开服务保存两项凭据后会自动启用，也可用 `CLOUDFLARE_AGENT_ENABLED=false` 单独关闭 Agent。使用 Workers Free 账户生成仅用于 Workers AI 推理的 Token。保留 `MODEL_MODE=mock`，常规匹配继续用本地规则，在线 Agent 和草稿分别按 Cloudflare 配置工作。未配置、超时、上游拒绝、无效动作或额度耗尽时，在线 Agent 记录原因并继续按规则处理。

[Blueprint 官方步骤](https://render.com/docs/infrastructure-as-code) · [Docker Web Service](https://render.com/docs/docker) · [Render 日志](https://render.com/docs/logging)

Free 实例可能冷启动，本地 SQLite 和上传文件在休眠、重启、重部署后可能清空。体验结束前可导出 JSON。需要保留数据时，可升级到带持久磁盘的付费实例，挂载 `APP_DATA_DIR` 并长期保留 `PUBLIC_SESSION_SECRET`；多人长期使用应进一步引入登录身份及数据库备份。Render 托管与 Cloudflare Workers AI 额度独立计算。 [Render Free 限制](https://render.com/docs/free)

公开容器只打包显式允许的应用文件和脱敏样例。已签名的 HttpOnly 会话 Cookie 决定访客的独立 SQLite、简历文件和知识索引。浏览器连接、BOSS、发送、自动工作流接口在公开服务入口被拒绝；公开数据不使用本地原件。示例数据虚构，在线岗位来自来源公开 API。Windows 单人模式仍可读取其本机配置。

## 三分钟演示

1. 打开首页，点「选用示例简历」。展开「岗位发现 Agent」，学历选择「本科」，运行「演示 · 首轮偏离后换词」。
2. 打开「Agent 执行记录」。核对 3 份博士硬条件与 1 条「博士优先」原文。查看补搜 `AI backend intern` 的事件，再点开推荐岗位看 JD、简历证据和待确认条件。
3. 选择「演示 · 首轮足够直接结束」重跑，对比只搜一轮的轨迹。可再运行地区确认案例，用 `AU` 回答问题，观察同一 run 续跑。页面可导出 JSON；右栏也可更换简历。

在线 Agent 选择「Himalayas 在线岗位」。Cloudflare Workers AI 可在动作点读取求职目标、条件、岗位材料摘要、简历技能和预算。简历原文和证据文本留在会话内。程序校验动作、调用固定来源接口、去重、核对硬条件和引用，选择合格推荐。来源 API 随搜索返回 JD；精读使用本轮实际提供的原文，只有摘要时标明证据不足。事件记录只包含动作与可核实结果。可配置 `AGENT_MAX_SEARCHES`、`AGENT_MAX_READS`、`AGENT_MAX_CALLS`、`AGENT_TIMEOUT`，默认分别为 3、10、18、90 秒。刷新读取已持久化的 run；明确澄清后续跑。进程重启时，中断的进行中步骤不自动重放。

岗位详情的「生成 HR 回复草稿」检索当前简历，展示命中片段；确认过的本访客历史问答可被检索作为写法参考。资料不足会显示待确认。

## 跨行业岗位推荐

上传简历后，先查看有原文引用的职业方向，再编辑本轮搜索词。Himalayas 使用相应英文查询，新搜索默认不限岗位类型。更换简历会生成新的建议和搜索草稿。历史批次仍可查看，也可用当前简历重评原候选，重评沿用原提交词和来源。

Windows 本地可使用项目根目录的 `Career_Workbench_Resume_JD_Benchmark/jobs.jsonl`，或在启动前设置 `OCCUPATION_TEMPLATES` 为模板文件路径。Docker 和公开演示使用内置的中英文职业目录。离线评测命令、数据边界和逐例报告说明见 [跨行业岗位基准验证](docs/cross-industry-benchmark.md)。

## 搜索国内 AI 岗位

在服务端设置个人 `OFFERDAO_API_KEY` 后重启服务。可选 `OFFERDAO_BASE` 默认 `https://offerdao.ai`。本地从设置了变量的 PowerShell 运行 `.\start.ps1`，Windows 仍可用原启动入口。Render 发布后，在服务的 **Environment** 设置同名变量。Key 仅设在服务端，部署文件无需写入 Key。应用按用户操作逐页查询，遵循来源条款。 [开发者文档](https://offerdao.ai/docs) · [服务条款](https://offerdao.ai/terms)

1. 上传简历或选用示例。在「岗位来源」选择「国内 AI 岗位 · Offer岛」，填写关键词、城市和岗位类型。
2. 搜索后可翻页、打开 Offer岛原帖或发布方来源。关键词保留来源的空格 AND 语义。按类型筛选时，Offer岛只检查卡片第一个子岗位。若要查混合卡片中的实习，可以取消类型筛选，再核对子岗位。
3. 有简历时搜索会自动匹配本页，分析结束后按证据档位和分数排序。也可以先浏览 Offer岛，再在岗位详情添加简历。无法加载岗位时可切回 Himalayas，或点「更多方式 → 粘贴 JD」。
4. 本地工作台可用来源明确的招聘邮箱撰写投递邮件，发送前手动附上简历。公开工作台引导访客在 Offer岛原帖查看邮箱。来源链接和职位投递链接分别展示。 [岗位字段](https://offerdao.ai/skill/references/posting-fields.md)

接口联调可运行 `python -m pytest -q tests/test_offerdao.py`。测试 Key 的每日额度和查询频率限制会分别提示，程序不自动重试、不领取共享测试 Key。

## 验证与评测

运行 `python -m pytest -q`、`python -m scripts.evaluate_agent`。评测方法、标签、调用次数与限制见 [岗位 Agent 对照评测](docs/agent-evaluation.md)。


## 岗位申请与 Workers AI 草稿

Himalayas 有申请 URL 时可直接点击「前往申请」，也可展开「生成申请陈述」，选择 English 或中文，填写可选招聘问题和长度限制，再主动生成。英文按词计，中文按非空白字符计。岗位、语言、问题和限制各自保留编辑内容。Offer岛按招聘邮箱、申请链接、原帖顺序提供投递入口，无邮箱和链接时可在原帖旁展开手填邮箱。

岗位详情的邮件和申请陈述在当前简历和当前岗位中匹配 1–2 条相关经历。服务端仅将无身份信息的岗位主题和经验片段发送到 Cloudflare。收件人、主题、建议附件名、签名和已确认到岗信息由本机或 Render 服务组装。公开工作台的收件人可从原帖确认后手填。

在线 Agent、邮件和申请陈述共用同一个 Workers AI 客户端和预算，独立于 `MODEL_MODE`。默认模型为 `@cf/qwen/qwen3.8-27b`。Cloudflare Workers Free 的 10,000 Neurons 每日免费额度在 UTC 00:00 重置。公开访客默认每小时可发起 12 次云端尝试，服务默认每天 100 次。预算以现有访客会话为单位，应用日预算以 UTC 日期计算。缓存、合并请求、预览、编辑和复制均不计入。`/health` 的 `draft_usage` 提供当前进程在 UTC 当天的尝试数、失败数、上游耗时总和、Agent 尝试及有效结构化响应数，以及响应实际提供的 Token 用量。应用预算和生成缓存存在进程内存，重启会清空，多实例各自计量。Cloudflare Free 是账户最终额度保护。

| Render 环境变量 | 值 |
| --- | --- |
| `PUBLIC_MODE` | `true` |
| `MODEL_MODE` | `mock`，保持岗位搜索与排名的现有设置 |
| `APP_DATA_DIR` | `/app/data` |
| `PUBLIC_SESSION_SECRET` | Render Blueprint 生成，保留现有值 |
| `MODEL_TIMEOUT` | Blueprint 默认 `12` |
| `OFFERDAO_API_KEY` | 可选，启用 Offer岛时用个人 Key |
| `OFFERDAO_BASE` | 可选，默认 `https://offerdao.ai` |
| `CLOUDFLARE_AGENT_ENABLED` | `true` 显式启用；公开环境配置有效凭据后自动启用，本地默认关闭 |
| `CLOUDFLARE_ACCOUNT_ID` | Workers AI 账户 ID |
| `CLOUDFLARE_API_TOKEN` | Workers AI REST API Token，启用云端草稿时填写 |
| `CLOUDFLARE_MODEL` | 可选，默认 `@cf/qwen/qwen3.8-27b` |
| `CLOUDFLARE_TIMEOUT` | 可选，默认 `9` 秒 |
| `CLOUDFLARE_DAILY_DRAFTS` | 可选，本进程 UTC 每天默认最多尝试 `100` 次 |
| `CLOUDFLARE_VISITOR_HOURLY_DRAFTS` | 可选，公开会话滚动一小时默认 `12` 次 |
| `CLOUDFLARE_LOCAL_HOURLY_DRAFTS` | 可选，仅本地模式滚动一小时默认 `30` 次 |

发布获准后，从 Cloudflare Workers AI Dashboard 的 **Use REST API** 创建凭据。在现有 Render 服务的 **Environment** 添加上表的 Cloudflare 凭据并保存，等部署健康后打开站点，选用脱敏简历。先检查 `/health` 的 `agent_provider` 是 `cloudflare`，再运行「Himalayas 在线岗位」。打开 Agent 执行记录，核对「Cloudflare 已选择下一步」和紧随其后的真实工具结果。在 Himalayas 岗位点「前往申请」核对目标链接，再分别生成 English 和中文陈述，核对字数与证据行号。在有招聘邮箱的 Offer岛岗位点「写投递邮件」，核对草稿标识、建议附件名和可编辑的主题与正文。再用 390px 视口编辑正文、比较另一个岗位并返回，确认原编辑保留。无需真实发信。未配置凭据和 Cloudflare 限额均走基础草稿，仍可编辑。模拟测试覆盖限额和超时。建议首次部署将 `CLOUDFLARE_DAILY_DRAFTS` 设为 `50`，再通过脱敏试跑的成功率、响应 Token 与 Cloudflare 仪表盘 Neurons 调整。100 次是代码中的试运行软上限。

[Cloudflare REST API](https://developers.cloudflare.com/workers-ai/get-started/rest-api/) · [Qwen 模型](https://developers.cloudflare.com/workers-ai/models/qwen3.8-27b/) · [计费与免费额度](https://developers.cloudflare.com/workers-ai/platform/pricing/)
