# Hypo-Expression

让 AI 按作者的身份和意图写作，减少防御性声明、虚构反对观点和生硬的中文搭配。

Hypo-Expression 提供沟通、写作、改稿三个操作。它保留适合文体的正式表达、信息量和组织形式；需要背景时主动查阅资料，再处理确实需要作者决定的问题。

## 三种用法

| 功能 | 你可以这样说 | 模型应当做什么 |
|---|---|---|
| 沟通 | 从现在开始，按这个规范跟我交流，包括讨论和任务汇报 | 回答实际问题、解释原因，在对话中交代主要结果 |
| 写作 | 我口述几个观点，请按这个规范写成文章 | 理解立场，补充事实、例子和推理，组织成文 |
| 改稿 | 用这个规范润色下面的报告 | 处理具体表达问题，保留有用信息与格式，遇到疑点主动查证 |

Skill 调用名为 `clear-expression`。支持显式Skill调用的客户端中，可以使用：

```text
请用 $clear-expression 润色下面的文字。
```

总入口是 [SKILL.md](skills/clear-expression/SKILL.md)，它会根据任务读取对应操作及参考文件。[完整使用说明](docs/usage.md)提供报告、新闻、口述文章和论文改稿的示例请求。

## 主要要求

- 正面陈述观点；需要纠正误解时，回应实际存在且重要的误解。
- 清理与读者目的无关的自我辩护、制作过程说明和核验宣告。
- 检查自然搭配与完整句意，保留符合文体的正式程度。
- 根据作者、读者与用途组织叙述，体现恰当的作者立场。
- 默认保留信息和格式，不把润色自动做成摘要。
- 保留因果、比较、实验目的和研究推进关系。
- 信息不足时主动找资料；需要作者决定的问题在讨论中解决。

十条共同原则及具体解释见 [表达原则](skills/clear-expression/references/principles.md)。规则来自维护者反复审阅实际文本的反馈，具体偏好可以按使用者需要调整；句式、标点或单个词本身不作为通用的“AI味”判据。

## 获取与安装

从 [v1.0.0 Release](https://github.com/HypoxanthineOvO/Hypo-Expression/releases/tag/v1.0.0) 下载 `clear-expression-1.0.0.zip`，解压得到 `clear-expression/`。保留完整目录，里面的操作和参考文件也是Skill的一部分。

### Codex

将 `clear-expression/` 放到用户级 `~/.agents/skills/` 下，或放到项目内的 `.agents/skills/` 下。也可以从克隆的仓库建立链接：

```sh
git clone https://github.com/HypoxanthineOvO/Hypo-Expression.git
mkdir -p ~/.agents/skills
ln -s "$(pwd)/Hypo-Expression/skills/clear-expression" ~/.agents/skills/clear-expression
```

若该位置已经安装了同名Skill，直接更新原有安装。目录约定见 [Codex 官方Skill文档](https://learn.chatgpt.com/docs/build-skills)。

### 其他 AI

支持 Agent Skills 的客户端：把解压得到的目录放到该客户端规定的技能目录。

有文件读取能力的 AI：提供整个目录，要求它读取 `SKILL.md` 并沿文件中的指引完成任务。

仅支持附件的客户端：上传整个文件包，并要求模型读取包内总入口及相关文件。客户端需要能够解压、读取 Markdown 附件。

## 文件组织

```text
skills/clear-expression/
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
    styles/       # 报告、新闻、文章、论文
    expression-examples.md
    examples/     # 分类表达判例
```

三个操作共用表达原则，按需读取文体细则和方法文件。完整设计见 [结构说明](docs/design.md)。

## 评测与改进

开发过程中整理了八组真实文本，进行了多轮独立试写和作者评价；分层后又分别测试了沟通、写作、改稿及项目查阅。评价使用原文与生成结果对照，关注含义、格式、作者身份及表达，不以字数减少或模型评分作为效果结论。

[评测说明](docs/evaluation.md)记录已覆盖的场景和后续测试方法。公开判例采用通用场景，不包含私人会话和未发表研究全文。欢迎在 Issue 中提供可公开的输入、输出、使用模型及具体修改意见。

## 开发

仅打包 Markdown 文件，不依赖外部服务。使用 Python 3.9 或更新版本执行：

```sh
python3 scripts/build_release.py
```

脚本检查内部引用、单一入口和版本信息，然后在 `dist/` 生成便携包。它检查分发结构；文本效果由实际读者审阅。

[参考资料](docs/sources.md) · [更新记录](CHANGELOG.md) · [MIT License](LICENSE)
