# Scholarime

[English](README.md) | **简体中文**

Scholarime 是一个源码公开的学术研究 Agent 工具集，包含 Codex Skill 和一组本地 Python
工具，用于文献检索、系统综述筛检、证据组织、数据分析、符号推导、形式证明检查、引用核验、
稿件组装和出版级图表制作。

Scholarime 不是 OSI 定义下的开源软件，因为项目禁止商业使用。详见[许可证](#许可证)。

检索前可设置联系邮箱和可选 API 凭据，以提高数据源限额：

```bash
export SCHOLARIME_CONTACT_EMAIL="you@example.org"
export NCBI_API_KEY="your-ncbi-key"   # 可选
```

## 主要能力

| 命令 | 用途 |
|---|---|
| `scholarime search` | 检索 OpenAlex、Crossref、arXiv 和 PubMed；配置 API Key 后查询 Semantic Scholar |
| `scholarime verify` | 用 DOI、DOI 解析器、图书目录和可选学术索引核对候选文献元数据 |
| `scholarime review` | 在本地 WebUI 中审阅、排序、补充、导入和引用候选文献 |
| `scholarime screening` | 导入记录、生成双盲筛检提示、合并裁决并输出 PRISMA 计数 |
| `scholarime workspace` | 用项目 SQLite 保存来源、授权 PDF、证据片段、论断、决定和产物 |
| `scholarime data` | 通过声明式 JSON 配方对表格进行画像、清洗、转换和审计 |
| `scholarime stats` | 执行描述统计、OLS/WLS/GLM/混合模型、诊断、多重比较和时间序列分析 |
| `scholarime symbolic` | 运行 SymPy 推导，并验证 Lean 4 或 Coq/Rocq 证明源码 |
| `scholarime manuscript` | 组装带图片、表格和引用的 LaTeX 或 DOCX 稿件 |
| `scholarime chart` | 从 CSV 数据生成学术风格 PNG 图表 |
| `scholarime figure` | 生成火山图、ROC、点图、边际分布、配对图、热图和森林图，并检查或审计导出 |

当用户要求“去 AI 味”“降低 AI 率”或“去 AI 腔”时，Scholarime 会加载完整的原版提示规则，
在保持事实、引用和文档结构的前提下直接交付改写后的文本。

CNKI、万方和维普目前没有获批的自动接口。它们导出的 RIS、BibTeX、EndNote 或 RefWorks
记录可以导入参考文献审阅界面。
数据源失败和部分覆盖会明确报告。OpenAlex、Crossref 使用联系邮箱进入礼貌请求池；PubMed
会同时发送 `tool=Scholarime` 标识。

检索完成后，Scholarime 会自动运行基于 CiteVerify 的元数据核验，并把状态保留在候选中。
核验只是证据，不是真伪判决：`unverified` 和 `unable_to_check` 不等于伪造，`conflict`
必须明确提示并人工复核。详见
[scholarime/references/citation-verification.md](scholarime/references/citation-verification.md)。

## 安装

Scholarime 需要 Python 3.10 或更高版本。

```bash
python -m venv .venv
source .venv/bin/activate
python -m pip install -e ".[all]"
```

Windows 可使用 `.\.venv\Scripts\Activate.ps1` 或 `.venv\Scripts\activate.bat` 激活环境。
也可以按需安装精简依赖组：`charts`、`figures`、`figure-pdf`、`ui`、`excel`、`symbolic`、
`workspace`、`stats` 和 `writing`。

各平台的安装步骤、pip 镜像选择规则、字体配置和外部工具安装方式见
[scholarime/install/INSTALL.md](scholarime/install/INSTALL.md)。

## 快速开始

```bash
scholarime --help
scholarime search "大语言模型 学术写作" \
  --query "large language models academic writing" --limit 10
scholarime verify /tmp/scholarime-search-.../candidates.json
scholarime screening init review-screening
scholarime workspace init research-project --title "我的研究项目"
scholarime data profile data.csv
scholarime symbolic doctor
scholarime figure list-templates
```

推荐的研究流程：

1. 检索文献，核验元数据，审阅并调整候选排序。
2. 创建研究项目并导入选中的来源。
3. 系统综述按确认的方案筛检记录，并生成可追踪的 PRISMA 计数。
4. 附加授权 PDF，提取页级文本、表格或 OCR 证据。
5. 把证据关联到明确论断，并导出证据矩阵。
6. 通过可复现配方清洗并分析数据。
7. 在需要数学核验时使用符号计算或形式验证。
8. 组装稿件，检查引用映射后完成最终审阅。

具体工作流和配置格式见
[scholarime/references/research-pipeline.md](scholarime/references/research-pipeline.md)。

## 项目结构

```text
scholarime/
|-- SKILL.md                 Codex 技能入口
|-- scripts/                 Python 工具
|-- references/              工作流和格式规范
|-- assets/citation-styles/  APA 与 GB/T 7714-2025 CSL 文件
|-- vendor/                  经审计的第三方元数据核验、图表和筛检代码
|-- formal/examples/         Lean 与 Coq 示例
|-- install/                 依赖和安装脚本
`-- tests/                   标准库 unittest 测试
```

仓库级 Agent 规范见 [AGENTS.md](AGENTS.md)。

## 外部工具

Lean 4、Coq/Rocq、Tesseract 语言数据和系统 WebView 不随项目打包。外部依赖缺失时，工具会
明确报告对应能力不可用。

- Lean：安装 elan、Lean 和 Lake，并通过 `--project` 使用现有 Lake 项目。
- Coq：Arch Linux 使用 `rocq` 和 `rocq-stdlib`；验证器同时支持 `coqc` 与 `rocq compile`。
- OCR：安装 Tesseract 及所需语言包，例如 `eng` 或 `chi_sim`。

## 安全与隐私

研究项目保存在本地。API Key 存放在当前用户配置目录，或通过环境变量提供。只处理用户有权
使用的 PDF。

统计结果、相关度排序、形式化命题和生成的稿件都需要人工复核。Scholarime 不会把符号化简
当成形式证明，也不会把 p 值当成因果结论。

## 测试

```bash
PYTHONDONTWRITEBYTECODE=1 python -m unittest discover -s scholarime/tests -v
```

依赖可用的可选集成测试会自动运行。

## 许可证

Scholarime 原创代码和文档采用
[CC BY-NC-SA 4.0](https://creativecommons.org/licenses/by-nc-sa/4.0/) 许可。
Copyright (c) 2026 Scholarime contributors。

禁止商业使用。分享改编版本时必须使用相同许可证并保留署名。该许可证不属于 OSI
开源许可证，也不会自动要求纯网络服务公开修改后的源码。

第三方 CSL、CiteVerify、nature-skills 图表工具、Academic Research Skills 筛检材料、
Statsmodels 和改编风格材料继续使用各自许可证。再分发仓库前请阅读
[THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md)。
