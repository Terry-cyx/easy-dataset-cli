<div align="center">

<img src="assets/banner.svg" alt="easyds — 从终端驱动 Easy-Dataset" width="820">

<br>

<img alt="Python" src="https://img.shields.io/badge/python-3.10%2B-3776AB?logo=python&logoColor=white">
<img alt="CLI" src="https://img.shields.io/badge/CLI-Click_8-blue">
<img alt="Transport" src="https://img.shields.io/badge/传输-HTTP%2FJSON-orange">
<img alt="Tests" src="https://img.shields.io/badge/测试-287_通过-22c55e">
<img alt="Version" src="https://img.shields.io/badge/版本-v1.0.1-f97316">
<img alt="License" src="https://img.shields.io/badge/协议-AGPL--3.0-green.svg">

**一个给 [Easy-Dataset](https://github.com/ConardLi/easy-dataset) 的 CLI 与 Agent 外壳 —— 从终端驱动完整的微调数据集流水线。**

**简体中文** | [English](./README.md) | [Türkçe](./README.tr.md)

[特性](#特性) • [快速开始](#本地运行) • [文档](easyds/skills/SKILL.md) • [贡献](#贡献) • [协议](#协议)

如果你喜欢这个项目，请点一个 Star ⭐️！

</div>

## 概述

**easy-dataset-cli**（`easyds`）是一个带状态的命令行外壳，让人类和 AI Agent 无需打开 GUI 就能驱动 [Easy-Dataset](https://github.com/ConardLi/easy-dataset) 的每一项能力 —— 而 Easy-Dataset 是目前最干净的"文档 → LLM 微调语料"开源流水线。`easyds` 通过纯 HTTP/JSON 与正在运行的 Easy-Dataset Next.js 服务器对话，因此上游的 Prompt 库、切分器、领域树构建器、GA 扩展器、评估器和导出器都保持原样工作。在这个基础之上，`easyds` 叠加了一个打磨良好的 CLI：17 个命令组、约 80 条子命令、带稳定退出码协议的 `--json` 模式、一个交互式 REPL，以及内嵌的 Agent skill 索引 —— 让 CI 流水线、自动化脚本和 LLM Agent 终于有了一个一等公民的接口。它就是 Easy-Dataset 强大的服务端与那些想要用它的自动化工作流之间那一层缺失的胶水。

<div align="center">
  <img src="assets/architecture.svg" alt="easyds 架构：Click CLI 通过 HTTP/JSON 与正在运行的 Easy-Dataset Next.js 服务器通信，后者持有切分、问题、数据集、评估、导出，并通过 Prisma 持久化到 SQLite" width="860">
</div>

## 动态

🎉🎉 **easy-dataset-cli v1.0.1 —— dataset-eval 反馈闭环上线！** 除了包装 Easy-Dataset 的每一项能力，`easyds` 现在还带有一项 GUI 无法提供的独占闭环特性：`datasets eval` 会对任何最终的 Alpaca/ShareGPT 文件跑一组确定性 Schema 检查，把每条失败归因到负责修复的那一步流水线，通过 `--fix {chunk-join,unwrap-labels,render-placeholders}` 在本地做安全修复，并可选地用 LLM judge 针对**有据性 / 正确性 / 清晰度**三个维度打分 —— 全过程不需要再碰服务端一次。也就是说，LLM Agent 现在可以*自行评估自己产出的数据集、决定该重跑哪一步、并在本地修复记录*，全部在一个紧凑的闭环里完成。完整故事见 [`easyds/skills/reference/11-dataset-eval.md`](easyds/skills/reference/11-dataset-eval.md)。

## 特性

### 🤖 为 AI Agent 而生

- **每条命令都支持 `--json`**，配合稳定的退出码协议（`0` OK、`2` 服务端错误、`3` 校验错误、`4` 找不到…），Agent 不用解析人话就能对失败做出反应
- **内嵌 Agent skill 索引**：[`easyds/skills/SKILL.md`](easyds/skills/SKILL.md) 加上 16 份参考文档和 11 个场景工作流 —— LLM 零上下文就能吃进操作规则
- **从真实生产运行中提炼的操作规则** —— `永远 --ga`、`model use 写服务端`、客户端 `ReadTimeout` ≠ 失败、自定义 Prompt 必须产出严格 JSON
- **稳定的 session 状态** 保存在 `~/.easyds/session.json`，Agent 无需每次都重复传 `--project`

### 🔌 Easy-Dataset 能力全覆盖

- **17 个命令组与 Easy-Dataset API 1:1 对齐** —— projects、models、prompts、files、chunks、tags、GA 对、questions、datasets、tasks、distill、eval、eval-task、blind、export、status、repl
- **所有有文档的能力都已包装** —— 切分策略（文本/文档/分隔符/代码）、自定义 Prompt、GA 扩展、多维评估、盲测 A/B、零样本蒸馏、多轮数据集、图像 VQA
- **按项目的 LLM 配置**，支持 OpenAI、Ollama、Zhipu、Kimi、OpenRouter、阿里百炼、MiniMax 以及任何 OpenAI 兼容的 endpoint
- **13 个已知服务端怪癖已绕过** —— 不用再一次一次生产运行地踩坑（详见 [`docs/SERVER_QUIRKS.md`](docs/SERVER_QUIRKS.md)）

### 📊 数据集评估与反馈闭环（easyds 独有）

- **确定性 Schema 检查** —— 9 条规则覆盖空字段、双重编码输出、占位符泄漏、形态异常的多轮记录、重复项以及长度异常值
- **失败归因** —— 每条失败规则都被交叉引用到负责修复它的那一步流水线和命令
- **安全的本地修复** —— `--fix chunk-join`、`--fix unwrap-labels`、`--fix render-placeholders` 在原文件上就地修复常见失败模式，无需重跑服务端
- **可选的 LLM judge** —— `--llm-judge` 会对记录做采样，直接打到任意 OpenAI 兼容 endpoint 上，按有据性 / 正确性 / 清晰度三个轴打分
- **按会话的 eval 历史** —— `datasets eval-history` 让 Agent 能识别重试死循环，并追踪多次精修的进度

### 📤 导出与集成

- **三种导出格式** —— Alpaca、ShareGPT、multilingual-thinking —— 支持 `--include-cot`、`--score-gte` 和确定性的 `--split train/valid/test`
- **后台任务编排** —— `easyds task wait` 会轮询长任务直到完成，带超时，Agent 不用手写轮询逻辑
- **按标签的均衡采样** 导出，与 Easy-Dataset GUI 语义一致

### 🛠️ 开发者体验

- **287 个测试全绿** —— 单测、Mock HTTP、桩服务器和已安装子进程 —— 外加两次针对 Kimi-K2.5 的真实端到端生产运行产物
- **Editable 安装 + uv 锁定依赖**，保证可复现的开发环境
- **单个干净的 Python 包**（PEP 621 + uv），唯一入口命令就是 `easyds`

### 🌐 对人类也很友好

- **交互式 REPL**：持久历史、品牌提示符、Tab 补全 —— 不带子命令直接 `easyds` 就能进
- **默认富文本人类输出**；只有想用 parser 时才切到 `--json`
- **多语言文档** —— 简体中文 / English / Türkçe —— 包括本 README

## 快速演示

> **正在录制。** 针对真实 Easy-Dataset 服务器跑完标准 7 步流水线的终端录屏很快会出现在这里。欢迎用 [`vhs`](https://github.com/charmbracelet/vhs) 或 [`asciinema`](https://asciinema.org/) 贡献 —— 直接向 `assets/demo.gif` 提 PR。

在那之前，已经有两条真实的端到端运行被编码成了可复现的配方：

- **Kimi-K2.5 + 中文规范文档** —— 完整 Alpaca 导出，200+ 问答对
- **Kimi-K2.5 + ANSYS CFX 教程** —— 自定义 Prompt 流水线，英文问答，ShareGPT 导出

生产级配方见 [`easyds/skills/reference/workflows/custom-prompt-pipeline.md`](easyds/skills/reference/workflows/custom-prompt-pipeline.md)。

## 本地运行

### 前置条件：启动 Easy-Dataset 服务器

`easyds` 是一个薄 HTTP 客户端 —— 它**不会**重新实现切分、领域树生成或 LLM 调用，而是把一切都转发给真正的 Easy-Dataset 服务器。在任何命令运行前，服务器必须是可达的。

```bash
git clone https://github.com/ConardLi/easy-dataset
cd easy-dataset
pnpm install        # 只需首次
pnpm dev            # 监听 http://localhost:1717
```

> Easy-Dataset **没有内建鉴权** —— 请跑在 localhost 或你自己的认证代理之后。

### 安装 easyds

```bash
# 用 uv 安装（推荐 —— 最快，独立的 tool 环境）：
uv tool install easy-dataset-cli

# 或用 uv 装到当前环境：
uv pip install easy-dataset-cli

# 或用普通 pip：
pip install easy-dataset-cli
```

需要 **Python 3.10+**。PyPI 包名为 `easy-dataset-cli`，安装后的二进制命令是 **`easyds`**。

### 跑一遍标准的 7 步流水线

```bash
# 0. 确认服务器可达。
easyds --json status

# 1. 创建项目。
easyds --json project new --name my_dataset

# 2. 注册一个 LLM 模型并激活（本地 session 和服务端 defaultModelConfigId 都会写 ——
#    GA / 图像 VQA 必须两边都写好）。
easyds --json model set \
    --provider-id openai \
    --endpoint   https://api.openai.com/v1 \
    --api-key    sk-... \
    --model-id   gpt-4o-mini
easyds --json model use <第 2 步返回的 id>

# 3. 上传文档（仅支持 .md / .pdf）。
easyds --json files upload ./spec.md

# 4. 切分（同时用 LLM 构建领域树）。
easyds --json chunks split --file spec.md

# 5. 生成问题。**必须加 --ga** —— 非 GA 模式在服务端已坏。
easyds --json questions generate --ga --language 中文

# 6. 为每个未回答的问题生成答案 + Chain-of-Thought。
easyds --json datasets generate --language 中文

# 7. 导出。
easyds --json export run \
    -o ./alpaca.json \
    --format alpaca \
    --all --overwrite

# 8. （easyds 独有）评估并自动修复最终文件。
easyds --json datasets eval ./alpaca.json
```

这就是完整闭环：**status → project → model → upload → chunk → questions → answers → export → evaluate** —— 可复现、可脚本化、Agent 可驱动。

## 文档

- **[`easyds/skills/SKILL.md`](easyds/skills/SKILL.md)** —— 精简的 Agent skill 索引，LLM 会自动读取
- **[`easyds/skills/reference/`](easyds/skills/reference/)** —— 16 份参考文档，涵盖标准流水线、自定义 Prompt 规则、操作规则、Agent 协议、任务设置、PDF/数据清洗、问题模板以及 dataset-eval 反馈闭环
- **[`easyds/skills/reference/workflows/`](easyds/skills/reference/workflows/)** —— 11 个场景配方（自定义 Prompt 流水线、情感分类、文档清洗、图像 VQA、多轮蒸馏、GA/MGA 对、评估 & 盲测、领域树编辑、导入/清洗/优化、后台任务、质量控制）
- **[`docs/SERVER_QUIRKS.md`](docs/SERVER_QUIRKS.md)** —— CLI 已绕过的 13 个 Easy-Dataset 服务端怪癖
- **上游 Easy-Dataset 文档**：[https://docs.easy-dataset.com/](https://docs.easy-dataset.com/)

## 社区实践

- **基于 Kimi-K2.5 的自定义 Prompt 流水线** —— 从 ANSYS CFX 教程端到端产出英文问答，配合自定义问题 + 评估 Prompt
- **情感分类数据集** —— 分隔符切分 + 标签模板 + `--fix chunk-join` 修复，并通过 `datasets eval` 反馈闭环验证
- **文档清洗重做** —— 长篇噪声 PDF → 批量清洗 → 带评分的问答 → 按分数过滤导出
- **从一堆幻灯片目录生成图像 VQA 数据集** —— 视觉模型答案生成

以上所有案例都被编码成了可运行的场景配方，位于 [`easyds/skills/reference/workflows/`](easyds/skills/reference/workflows/) 下。

## 贡献

非常欢迎社区贡献！要为 `easy-dataset-cli` 贡献代码：

1. Fork 仓库
2. 创建新分支（`git checkout -b feature/amazing-feature`）
3. 搭建开发环境：
   ```bash
   uv sync --extra test
   uv run easyds --version
   uv run pytest                       # → 287 passed
   ```
4. 在 `tests/` 下修改代码并补上测试
5. 提交改动（`git commit -m 'Add some amazing feature'`）
6. 推送分支（`git push origin feature/amazing-feature`）
7. 针对 `main` 分支提 Pull Request

请确保 `pytest` 保持全绿，并遵循现有编码风格（CLI 用 Click，后端基于薄 `requests`，每个 Easy-Dataset 领域一个 `core/` 模块）。

## 协议

本项目基于 **AGPL-3.0-or-later** 协议开源 —— 详见 [LICENSE](LICENSE)。与上游 Easy-Dataset 保持一致。

## 相关项目

- **[Easy-Dataset](https://github.com/ConardLi/easy-dataset)** —— `easyds` 所驱动的上游 Next.js + Prisma 服务端。必须的运行时依赖。

## Star 趋势

[![Star History Chart](https://api.star-history.com/svg?repos=Terry-cyx/easy-dataset-cli&type=Date)](https://www.star-history.com/#Terry-cyx/easy-dataset-cli&Date)

<div align="center">
  <sub>一个为 <a href="https://github.com/ConardLi/easy-dataset">Easy-Dataset</a> 打造的 CLI 外壳 —— 同时服务于人类与 Agent。</sub>
</div>
