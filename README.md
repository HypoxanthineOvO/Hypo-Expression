# Hypo-Expression

[![Version v1.0.0](https://img.shields.io/badge/version-v1.0.0-blue)](https://github.com/HypoxanthineOvO/Hypo-Expression/releases/tag/v1.0.0) [![License MIT](https://img.shields.io/badge/license-MIT-green)](LICENSE)

**你有没有在使用 AI 的过程中遇到这些情况？**

- **让它解释一个概念，它先反驳没人提出的观点：**“这里说的‘清楚’，并不是要求所有文字都变短，也不是要把专业内容一律改成浅白口号。”
- **让它写一份报告，它却忙着证明自己检查过：**“已核验：文件身份、完整运行与输出、程序与计算区间一致……”
- **让它整理要求，它把给自己的纪律写给你看：**“不能擅自保证‘发完邮件就肯定不扣分’。”

这些片段根据开发者的历史反馈节选、脱敏。类似的问题还有生硬的词语组合、缺少作者立场，以及一说“润色”就删掉信息、加粗和编号。

**Hypo-Expression 把这些反复纠正过的问题整理成一套可复用的表达规范。** 它通过沟通、写作、改稿三个功能，让 AI 按作者意图和文本用途组织内容，减少防御性声明、虚构反对观点和不自然的中文搭配。正式表达、长文和有用的格式都可以保留。

## 三种用法

安装后，直接向 AI 说明任务：

| 功能 | 示例请求 | 处理重点 |
|---|---|---|
| 沟通 | 使用 Hypo-Expression，从现在开始按这套规范和我交流 | 回答实际问题，解释原因，在对话中交代主要结果 |
| 写作 | 使用 Hypo-Expression，把这些观点写成文章 | 理解作者立场，补充必要的事实、背景、例子和推理，组织成文 |
| 改稿 | 使用 Hypo-Expression，润色这份报告 | 处理表达与衔接，默认保留信息和有用格式，必要时查阅背景 |

Skill 短名是 **`hypo-exp`**，也可以直接说“用 hypo-exp 帮我写一份报告”。三个功能共用一个入口，根据任务分流。

[使用说明](docs/usage.md)提供报告、新闻、口述文章、论文和仓库文档的具体用法。

## 它怎样处理表达

- **直接表达实际观点。** 需要纠正误解时，回应上下文中确实存在且重要的误解。
- **清理防御性声明和自证。** 读者需要的事实、条件和提醒正常表达，制作过程和模型纪律留在正文之外。
- **保留作者身份与自然语体。** 检查中文搭配，按文体选择叙述方式，不默认把正式文本改成口语。
- **保留信息、格式和论证。** 润色不自动变成摘要，解释、因果、比较与研究推进关系都要保留其作用。
- **信息不足时主动查阅。** 先找相关资料，再把确实需要作者决定的问题拿来讨论。

共同要求见 [十条表达原则](skills/hypo-exp/references/principles.md)。文体细则分别处理报告、新闻、文章、论文和仓库文档；口述观点可以展开成文，README、版本说明和提交信息也按各自用途组织。句式、标点或单个词本身不作为统一的“AI味”判据。

## 获取与安装

从 [v1.0.0 Release](https://github.com/HypoxanthineOvO/Hypo-Expression/releases/tag/v1.0.0) 下载 `hypo-exp-1.0.0.zip`，解压得到 `hypo-exp/`。保留完整目录，入口引用的操作和参考文件也是 Skill 的组成部分。

### Codex

将 `hypo-exp/` 放到用户级 `~/.agents/skills/`，或项目内的 `.agents/skills/`。也可以克隆仓库后建立链接：

```sh
git clone https://github.com/HypoxanthineOvO/Hypo-Expression.git
mkdir -p ~/.agents/skills
ln -s "$(pwd)/Hypo-Expression/skills/hypo-exp" ~/.agents/skills/hypo-exp
```

若已经安装同名 Skill，更新原有目录。目录约定见 [Codex 官方 Skill 文档](https://learn.chatgpt.com/docs/build-skills)。

### 其他 AI

支持 Agent Skills 的客户端，将目录放到其规定的技能目录。有文件读取能力的 AI，可从包内 `SKILL.md` 开始，按指引读取相关文件。仅支持附件的客户端，需要能够解压文件包并读取 Markdown 附件。

## 文件组织

```text
skills/hypo-exp/
  SKILL.md
  operations/
    communication/instructions.md
    writing/instructions.md
    revision/instructions.md
  references/
    principles.md
    content-and-structure.md
    project-context.md
    styles.md
    styles/       # 报告、新闻、文章、论文、仓库文档
    expression-examples.md
    examples/     # 分类表达判例
```

[总入口](skills/hypo-exp/SKILL.md)按任务读取共同原则、操作文件和参考资料。完整设计见 [结构说明](docs/design.md)。

## 调试与评测

**目前主要针对 GPT-6 和 GPT-6.1 的输出调试，其他模型尚未测试，后续会补充。** 已记录的独立试写使用 GPT-6-Sol，覆盖八组真实文本及分层后的六项任务。开发者通过原文、生成结果和逐项反馈调整规则。

[评测说明](docs/evaluation.md)介绍具体方法。欢迎在 Issue 中提供可公开的输入、输出、使用模型及修改意见。

## 开发与贡献

仓库使用 Markdown 组织规则，无需部署服务。构建便携包需要 Python 3.9 或更新版本：

```sh
python3 scripts/build_release.py
```

输出位于 `dist/`。

- **HypoxanthineOvO**：提出需求和表达偏好、审阅文本、维护项目。
- **OpenAI Codex（AI 助手）**：协助规则整理、文件组织、试写编排和文档编写。

[参考资料](docs/sources.md) · [更新记录](CHANGELOG.md) · [MIT License](LICENSE)
