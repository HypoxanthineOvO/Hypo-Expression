# Hypo-Expression

[![Version v1.0.1](https://img.shields.io/badge/version-v1.0.1-blue)](https://github.com/HypoxanthineOvO/Hypo-Expression/releases/tag/v1.0.1) [![License MIT](https://img.shields.io/badge/license-MIT-green)](LICENSE)

**你有没有在使用 AI 的过程中遇到这些情况？**

- **它先反驳一个你根本没提出的观点。** 你让它“把这段话说清楚”，它却开始解释：“把话说清楚，并不是把所有专业术语都改成大白话，而是在保留专业性的同时提高可读性。”你只是想改段文字，读完却觉得它在把你当傻子。
- **它在报告里堆满检查记录，却没讲你关心的内容。** 你想看结果和分析，迎面而来的是“已核验”“SHA-256 一致”“运行完整”“输出一致”。翻了半天，报告的核心内容反而没几句。
- **它把整理要求写成了一连串“不能声称”。** 你让它整理老师的课程要求，它回复“不能声称已覆盖全部要求”“不能擅自保证请假后不扣分”。正经要求没看到几条，“不能声称”倒是一屏，眼睛先受到了攻击。

Hypo-Expression 的开发者回顾了近半年来使用 AI 的会话，整理其中反复提出的批评和修改意见，从这些具体问题中归纳出几类原则：

- **直接表达观点。** 解释实际内容，回应确实存在的误解，不凭空树立一个反对观点。
- **在对话中完成回答。** 先交代结论、理由和必要例子，让读者当场理解结果；文件和网站承载详细内容，不代替回答。
- **把读者需要的内容放在正文里。** 写结果、依据和具体提醒，减少自我辩护与制作过程说明。
- **使用自然的中文，保留作者立场。** 词语组合要符合表达习惯，写出来的文章要体现作者本来的意思。
- **保留有用的信息和格式。** 润色不自动变成摘要，数字、条件、论证衔接、编号和加粗都各有作用。
- **信息不足时主动查阅。** 先读相关材料，再把确实需要作者决定的问题拿来讨论。

在这些反馈的基础上，开发者又参考了 [Humanizer](https://github.com/blader/humanizer)、[Stop That Shit](https://github.com/lennney/stop-that-shit) 等开源 Skill，以及技术写作规范，整理成 Hypo-Expression 的**表达原则、写作方法和文体细则**。[十条共同原则](skills/hypo-exp/references/principles.md)和[参考资料](docs/sources.md)中有更详细的说明。

## 三种用法

日常使用 AI 时，**让它与自己沟通、让它起草内容、让它修改已有文本**，是三种不同的需求。Hypo-Expression 针对各自的作者身份、处理过程和交付方式，分别整理了操作要求，共用同一套表达原则。

安装后，直接向 AI 说明任务：

| 功能 | 示例请求 | 处理重点 |
|---|---|---|
| 沟通 | 使用 Hypo-Expression，从现在开始按这套规范和我交流 | 直接回答问题，解释结果与实际影响，提供必要例子，不用附件代替回答 |
| 写作 | 使用 Hypo-Expression，把这些观点写成文章 | 理解作者立场，补充必要的事实、背景、例子和推理，组织成文 |
| 改稿 | 使用 Hypo-Expression，润色这份报告 | 处理表达与衔接，默认保留信息和有用格式，必要时查阅背景 |

Skill 短名是 **`hypo-exp`**，也可以直接说“用 hypo-exp 帮我写一份报告”。[使用说明](docs/usage.md)提供报告、新闻、口述文章、论文和仓库文档的具体用法。

## 获取与安装

从 [v1.0.1 Release](https://github.com/HypoxanthineOvO/Hypo-Expression/releases/tag/v1.0.1) 下载 `hypo-exp-1.0.1.zip`，解压得到 `hypo-exp/`。保留完整目录，入口引用的操作和参考文件也是 Skill 的组成部分。

### Codex

将 `hypo-exp/` 放到用户级 `~/.agents/skills/`，或项目内的 `.agents/skills/`。也可以克隆仓库后建立链接：

```sh
git clone https://github.com/HypoxanthineOvO/Hypo-Expression.git
mkdir -p ~/.agents/skills
ln -s "$(pwd)/Hypo-Expression/skills/hypo-exp" ~/.agents/skills/hypo-exp
```

软链接安装后，客户端直接读取仓库中的 Skill；更新仓库即可使用新内容。已有独立副本时，先移到技能目录之外备份，再建立同名链接。目录约定见 [Codex 官方 Skill 文档](https://learn.chatgpt.com/docs/build-skills)。

### 其他 AI

支持 Agent Skills 的客户端，将目录放到其规定的技能目录。有文件读取能力的 AI，可从包内 `SKILL.md` 开始，按指引读取相关文件。仅支持附件的客户端，需要能够解压文件包并读取 Markdown 附件。

## 调试与评测

**目前主要针对 GPT-6 和 GPT-6.1 的输出调试，其他模型尚未测试，后续会补充。** 已记录的独立试写使用 GPT-6-Sol，覆盖八组真实文本及分层后的六项任务。开发者通过原文、生成结果和逐项反馈调整规则。

[评测说明](docs/evaluation.md)介绍具体方法。欢迎在 Issue 中提供可公开的输入、输出、使用模型及修改意见。

[参考资料](docs/sources.md) · [更新记录](CHANGELOG.md) · [MIT License](LICENSE)
