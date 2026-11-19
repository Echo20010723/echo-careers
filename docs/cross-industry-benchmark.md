# 跨行业岗位基准验证

## 复现

把原始数据包放在项目根目录的 `Career_Workbench_Resume_JD_Benchmark/`，执行以下命令。脚本也接受数据包路径作为位置参数。

```powershell
.venv/Scripts/python.exe -m scripts.evaluate_cross_industry
.venv/Scripts/python.exe -m pytest -q
```

逐例结果写入 `data/cross-industry-evaluation.json`。每份简历包含行业、阶段、推荐方向、搜索词和证据核对结果。每条查询包含相关岗位、困难负例排名和两者的相对次序。

## 语料与数据边界

- `config/occupations.tsv` 是独立的中英文职业目录。Windows 本地模式可通过 `OCCUPATION_TEMPLATES` 指定 `jobs.jsonl`，默认尝试项目根目录下的数据包。JSONL 包含 456 条职业模板。参考按模板 ID 追溯到原 Markdown 或档案成员。相同名称的模板保留各自的来源。Markdown 用来人工查阅，程序只加载 JSONL。
- 简历原文及行号支撑方向推荐。模板辅助识别同义称呼和职业职责。岗位匹配使用这一条 JD 或用户导入的摘要，缺失的任职条件继续显示待核对。
- 评测样本和标注只由显式运行的脚本使用。35 条改写 JD 的索引用词法、字段权重和职责相似度排序，与 Himalayas 的在线候选独立。
- 用户可上传普通岗位数组、含 `jobs` 的 JSON 或含 `records` 的 JD 摘要文件。摘要保留为仅列表，来源 URL 有值时可打开原页。公开镜像和源码 ZIP 只含自维护职业目录及已有脱敏演示样例。

## 结果

2026-11-20，Windows，本地规则，基准包内自带标注。简历命中表示前三个建议中至少一个与合理方向同义。匹配时忽略实习、初级等阶段词和标注中的条件后缀，所以这个指标不能衡量年资判断的准确性。简历语料含用于开发的样本。留出样本单列。

| 项目 | 结果 |
| --- | ---: |
| 简历合理方向命中 | 27 / 30 |
| 应届 / 在职 / 转行 / 混合 | 9 / 10、9 / 10、6 / 6、3 / 4 |
| 留出简历合理方向命中 | 3 / 6 |
| 标注的不适合方向进入前三 | 0 / 30 |
| 引用可定位 | 41 / 41 |
| 输出资质待核对提示的简历 | 4 份 |
| JD 标注相关岗位 Recall@5 | 17 条查询的宏平均 100% |
| 相关岗位排在标注困难负例之前 | 39 / 41 对 |
| 留出 JD 查询 Recall@5 / 相对排序 | 3 条查询宏平均 100% / 6 对全部正确 |

留出组中的建筑工程、餐饮运营、体育推广样本未命中。两条 JD 查询各有一对困难负例排在相关项之前。未标注岗位不参与准确率判定。这个小型、封闭的数据集无法验证线上搜索召回率、资质提示准确率或生产环境推荐效果。

PDF 回归覆盖工作经历标题在视觉位置正确但在 PDF 文本流中错序的情况，以及超过 120 行的项目交付。读取器在确认标题与正文顺序改善且文字量保留时采用视觉布局。项目背景与实施过程用于恢复分段，技术栈清单仍按技能处理。运维词条参考 [O*NET 系统与网络管理员](https://www.onetonline.org/link/details/15-1244.00)、[Kubernetes 集群管理](https://kubernetes.io/docs/concepts/cluster-administration/) 和 [GitLab CI/CD](https://docs.gitlab.com/topics/build_your_application/)。

A/B 回归使用可控的 Himalayas 响应。B 的新请求实际到达适配器，关键词为 `Kubernetes Operations Engineer` 和 `Cloud Operations Engineer`，均从第 1 页开始，未加实习筛选。用 B 重评 A 的岗位时，外部请求次数不增加，旧搜索原词、候选和结果保留。真实 Himalayas 来源通过独立的容器在线冒烟验证。真实付费模型和 Render 线上实例待验证。
