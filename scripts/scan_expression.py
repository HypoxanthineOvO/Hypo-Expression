#!/usr/bin/env python3
"""扫描中文文本里的 AI 表达痕迹，输出终端报告、JSON 或 HTML。

只用 Python 3 标准库。

用法：
    python3 scripts/scan_expression.py <path...> [--format txt|json|html] [--output FILE]
    python3 scripts/scan_expression.py --benchmark --pos <path...> --neg <path...> \
        [--format txt|json|html] [--output FILE]

默认忽略代码块、行内代码、引用块和中文引号里的短词语；--raw 连引号一起扫。
"""

from __future__ import annotations

import argparse
import bisect
import html
import json
import re
import sys
from collections import Counter, OrderedDict
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Callable, Iterable, Sequence

VERSION = "1.0"

# ---------------------------------------------------------------------------
# 类别
# ---------------------------------------------------------------------------

GROUP_NAMES = OrderedDict(
    [
        ("A", "免责与自保尾巴"),
        ("B", "内部过程泄漏与自证"),
        ("C", "内部代号与哈希"),
        ("D", "句式模板与逻辑套话"),
        ("E", "英文 AI 腔调"),
        ("F", "伪专业抽象词"),
        ("G", "戏剧化短词与感官套话"),
        ("H", "对话姿态"),
        ("I", "软化虚词与破折号"),
    ]
)

GROUP_ADVICE = {
    "A": "删掉免责尾巴，把真正的条件写在结论旁",
    "B": "删掉自证标注，只写结果",
    "C": "换成读者能读懂的名称",
    "D": "改成直接陈述事实，去掉句式模板",
    "E": "换成日常说法或具体动词",
    "F": "改成具体动作和对象",
    "G": "写清具体分析，不用姿态词",
    "H": "直接回答问题，不重定义、不居高临下",
    "I": "删到只剩必要的连接词",
}

# 密度类组：命中位置默认不逐条当硬伤，只按千字密度判断。
DENSITY_GROUPS = {"D", "E", "F", "G"}

# 硬伤：出现一条就可以判阳性（级别为 alarm 的命中）。
HARD_GROUPS = {"A", "B", "C", "H"}

LEVEL_PRIORITY = {"alarm": 3, "warn": 2, "hint": 1, "info": 0}

# 判定阈值。每千字的可见字符数。
DENSITY_THRESHOLDS = {
    "D": {"min_hits": 3, "warn": 1.2, "fail": 2.8},
    "E": {"min_hits": 3, "warn": 0.6, "fail": 1.5},
    "F": {"min_hits": 3, "warn": 0.6, "fail": 1.5},
    "G": {"min_hits": 3, "warn": 1.0, "fail": 2.0},
}
# D/E/F/G 合计，对应“组合千字密度”这一条。
COMBINED_THRESHOLD = {"min_hits": 6, "warn": 2.0, "fail": 3.0}
SOFTENING_BASELINE = 6.0  # 每千字，超过时只给密度提示

# ---------------------------------------------------------------------------
# 规则
# ---------------------------------------------------------------------------

Validator = Callable[[str, "re.Match[str]"], bool]


@dataclass(frozen=True)
class Rule:
    code: str
    pattern: str
    level: str = "warn"
    advice: str = ""
    validator: Validator | None = None

    @property
    def group(self) -> str:
        return self.code[0]


def _near(text: str, start: int, end: int, radius: int = 24) -> str:
    return text[max(0, start - radius) : min(len(text), end + radius)]


def _line_start(text: str, pos: int) -> int:
    return text.rfind("\n", 0, pos) + 1


def validate_a2(text: str, match: re.Match[str]) -> bool:
    """“不能保证/无法保证”只在自我免责时报警，技术条件不算。"""
    before = text[max(0, match.start() - 28) : match.start()]
    after = text[match.end() : match.end() + 28]
    clause = re.split(r"[。！？；\n]", after, maxsplit=1)[0]
    if "擅自" in match.group(0):
        return True
    if re.search(r"(?:本文|本报告|本工具|本回答|以上内容|以上结论)", before):
        return True
    return bool(
        re.search(
            r"(?:准确|正确|完整|无误|最新|真实|全面|适用|成功|效果|不扣分|通过|"
            r"可靠|稳定|官方|任何|所有|一切|完全|一定|绝对)",
            clause,
        )
    )


def validate_a3(text: str, match: re.Match[str]) -> bool:
    """“不代表”只有主语是本文/本报告/我方或作免责尾句时才报警。"""
    after = text[match.end() : match.end() + 24]
    after_clause = re.split(r"[。！？；\n]", after, maxsplit=1)[0]
    before = text[max(0, match.start() - 36) : match.start()]

    # 先看是否是本文、本报告、本回答在替自己划边界。
    self_subject = re.search(
        r"(?:本文|本报告|本回答|本工具|本次测试|这次测试|以上内容|以上结果|我的回答|"
        r"这个回答|我方|上述|该内容|这些数据|这些数字|本结果)[^。！？\n]{0,10}$",
        before,
    )
    if self_subject:
        return True

    # 转述第三方说法时，不是作者在免责。
    if re.search(
        r"(?:他说|她说|它说|其说|发言人|辩护|声称|表示|认为|写道|提到|引述|转述|"
        r"话|说法|言论)[^。！？\n]{0,6}$",
        before,
    ):
        return False

    if re.match(r"(?:官方|权威|任何|其|平台)[^。！？\n]{0,8}", after_clause):
        return True
    return bool(
        re.search(
            r"(?:批准|验收|结论|承诺|保证|背书|立场|观点|事实|真相|定论|官方|权威|标准答案)",
            after_clause,
        )
    )


def validate_a4(text: str, match: re.Match[str]) -> bool:
    before = text[max(0, match.start() - 30) : match.start()]
    after = text[match.end() : match.end() + 30]
    return bool(
        re.search(
            r"(?:本文|本报告|本工具|本回答|以上|这份|该|此|这些|本次|本轮|这个结果|这次验证)",
            before,
        )
        or re.search(r"(?:官方|专业|医生|律师|审批|验收|批准|医疗|法律|投资|诊断|事实认定)", after)
    )


def validate_a6(text: str, match: re.Match[str]) -> bool:
    after = text[match.end() : match.end() + 24]
    return bool(
        re.match(
            r"[^。！？\n]{0,6}(?:建议|推荐|承诺|保证|结论|依据|投资|法律|专业|诊断|"
            r"批准|验收|证据|事实认定|对任何)",
            after,
        )
    )


def validate_a7(text: str, match: re.Match[str]) -> bool:
    """“以……为准”在课程、合同、操作说明里常是客观规则，只有兜底尾巴才报警。"""
    matched = match.group(0)
    before = text[max(0, match.start() - 18) : match.start()]
    if re.match(r"(?:具体|最终|一切|均|一律)以", matched):
        return True
    if re.match(r"以(?:官方|权威|平台|公告|发布)", matched):
        return True
    return bool(
        re.search(r"(?:本文|本报告|以上|本工具|本回答|请注意|最终)[^。！？\n]{0,8}$", before)
    )


def validate_a8(text: str, match: re.Match[str]) -> bool:
    """“如有疑问”后面有具体动作时是正常指引，不是免责套话。"""
    matched = match.group(0)
    after = text[match.end() : match.end() + 36]
    context = matched + after
    if re.search(r"(?:咨询专业人士|以官方为准|以实际为准|联系客服|仅供参考|否则|后果自负)", context):
        return True
    if re.search(r"(?:请|可|可以|在|到|向|通过|联系|提交|附上|参见|查看|访问|填写|发|使用)", context):
        return False
    return True


def validate_a19(text: str, match: re.Match[str]) -> bool:
    """“仅供参考”只有主语笼统或紧接不构成时才是免责尾巴。"""
    before = text[max(0, match.start() - 18) : match.start()]
    after = text[match.end() : match.end() + 16]
    if re.search(
        r"(?:以上|本文|本报告|本回答|本工具|以下|上述|本结果|以上内容|这个结论)[^。！？\n]{0,8}$",
        before,
    ):
        return True
    if re.match(r"\s*[，,]?\s*(?:不构成|不替代|不代表)", after):
        return True
    return _standalone_item(text, match.start())


def validate_a12(text: str, match: re.Match[str]) -> bool:
    """“免责声明”常被规则文档当反面例子讨论，只认真正的声明落款。"""
    after = text[match.end() : match.end() + 10].lstrip()
    before = text[max(0, match.start() - 12) : match.start()]
    line_start = _line_start(text, match.start())
    line_prefix = text[line_start : match.start()].strip()
    if after.startswith((":", "：")):
        return True
    if re.fullmatch(r"#{1,6}\s*", line_prefix):
        return True
    if re.search(r"(?:免责声明|声明)\s*$", line_prefix):
        return True
    if re.search(r"(?:一串|这类|这种|所谓|写成|换成|加了|加个|附上|末尾的)", before):
        return False
    return bool(re.search(r"(?:本文|本报告|以上|下面|最后|文末)[^。！？\n]{0,8}$", before))


def validate_a9(text: str, match: re.Match[str]) -> bool:
    """“这并不意味着”在正常论证里也会出现，只有拿去兜底时才报警。"""
    after = text[match.end() : match.end() + 32]
    clause = re.split(r"[。！？；\n]", after, maxsplit=1)[0]
    before = text[max(0, match.start() - 16) : match.start()]
    if re.search(r"(?:以上|本文|本报告|这份|该|此|这些|上述|前述)[^。！？\n]{0,4}$", before):
        return True
    return bool(
        re.search(
            r"(?:应该|应当|可以|能够|必须|一定|完全|必然|所有|任何|官方|权威|正确|错误|"
            r"完美|安全|有效|属实|成立|没问题|没有问题|就是|等于)",
            clause,
        )
    )


def validate_b5(text: str, match: re.Match[str]) -> bool:
    after = text[match.end() : match.end() + 12]
    before = text[max(0, match.start() - 6) : match.start()]
    return bool(
        re.match(r"\s*(?:[:：]\s*\d|\d)", after)
        or re.search(r"(?:已|完成|附|含|带)\s*$", before)
        or re.match(r"[^。！？\n]{0,8}(?:通过|一致|无误|完整|如下|正常|异常|合格)", after)
    )


def validate_b8(text: str, match: re.Match[str]) -> bool:
    after = text[match.end() : match.end() + 16]
    return bool(
        re.match(r"\s*(?:[:：]\s*\d|\d|[^\n]{0,8}(?:通过|完成|无误|一致))", after)
        or re.search(r"(?:已|完成|通过)\s*$", text[max(0, match.start() - 6) : match.start()])
    )


def validate_b18(text: str, match: re.Match[str]) -> bool:
    before = text[max(0, match.start() - 6) : match.start()]
    after = text[match.end() : match.end() + 18]
    if re.search(
        r"(?:档案|史料|实验|课程|原始|文献|研究|参考|教学|项目|报告|论文|教材|试卷|"
        r"会议|调查|访谈|录音|转录|给定)\s*$",
        before,
    ):
        return False
    return bool(
        re.search(r"(?:在|从|据|根据)$", before)
        and re.match(r"\s*(?:看到|发现|读到|记载|提到|显示|得知|找到|提取|说|写)", after)
    )


def _standalone_item(text: str, start: int) -> bool:
    line_start = _line_start(text, start)
    prefix = text[line_start:start]
    return bool(re.fullmatch(r"[-*+>#\s]*(?:\d+[.)、]\s*)?", prefix))


def validate_b6(text: str, match: re.Match[str]) -> bool:
    """“审核通过”在流程条件里是技术事实，只有独立状态项才报警。"""
    after = text[match.end() : match.end() + 18]
    before = text[max(0, match.start() - 10) : match.start()]
    if re.match(r"\s*(?:后|之后|的|前|时)", after):
        return False
    if re.match(r"\s*[:：]", after):
        return True
    if re.search(r"(?:核验|审核|验收|自查|状态|清单|记录)\s*[:：]\s*$", before):
        return True
    return _standalone_item(text, match.start())


def validate_b27(text: str, match: re.Match[str]) -> bool:
    """“检查通过”只有在状态标签或独立清单项里才算自证。"""
    after = text[match.end() : match.end() + 18]
    before = text[max(0, match.start() - 16) : match.start()]
    if re.match(r"\s*(?:后|之后|的|前|时)", after):
        return False
    if re.match(r"\s*[:：]", after):
        return True
    if re.search(r"(?:核验|验收|审核|自查|状态|结果|清单|记录)\s*[:：]\s*$", before):
        return True
    return _standalone_item(text, match.start())


def validate_f26(text: str, match: re.Match[str]) -> bool:
    """数学与算法里的“收敛到”是术语，不是黑话。"""
    before = text[max(0, match.start() - 18) : match.start()]
    after = text[match.end() : match.end() + 18]
    if re.search(r"(?:序列|级数|函数|损失|误差|算法|迭代|训练|模型|数值|指标|解|过程)\s*$", before):
        return False
    if re.match(r"[^。！？\n]{0,8}(?:最优|局部|全局|稳定|解|阈值|极值|真值|收敛)", after):
        return False
    return True


def validate_c6(text: str, match: re.Match[str]) -> bool:
    around = _near(text, match.start(), match.end(), 30)
    return bool(re.search(r"(?:sha|hash|哈希|摘要|指纹|校验|digest|commit|版本|文件)", around, re.I))


# ---- A 组 ----

A_RULES = [
    Rule("A1", r"不能声称", "alarm", "把能说清的部分直接写出来"),
    Rule(
        "A2",
        r"(?:不能|无法|尚不能)(?:擅自|简单地|完全|绝对|轻易)?保证",
        "alarm",
        "写明实际限制，不替自己求免责",
        validate_a2,
    ),
    Rule("A3", r"不代表", "alarm", "只写真实关系；需要限制就写具体影响", validate_a3),
    Rule("A4", r"(?:不替代|不能替代)[^。！？\n]{0,30}", "alarm", "写清工具能做什么，不写不能替代谁", validate_a4),
    Rule(
        "A5",
        r"仅作为(?:参考|背景|锚点|示例|示范|补充|说明|讨论|起点|假设|个人观点)",
        "alarm",
        "删掉“仅作为”，直接说用途",
    ),
    Rule("A6", r"不构成[^。！？\n]{0,24}", "alarm", "删掉法律式免责，写实际结论", validate_a6),
    Rule(
        "A7",
        r"(?:具体|最终|一切|均|应)?以(?:官方|实际|最终|发布|公告|平台|机构|老师|模板|原文|课堂|合同|说明书)"
        r"[^。！？\n]{0,14}为准",
        "alarm",
        "把已经知道的条件写出来，不把判断推给“以为准”",
        validate_a7,
    ),
    Rule(
        "A8",
        r"如有(?:疑问|需要|问题|异议|变动|调整)[^。！？\n]{0,24}",
        "alarm",
        "删掉尾巴，把要读者做的动作前置",
        validate_a8,
    ),
    Rule(
        "A9",
        r"(?:这|但|那)(?:并)?不意味着[^。！？\n]{0,30}",
        "alarm",
        "写具体限制，不用“不意味着”兜底",
        validate_a9,
    ),
    Rule("A10", r"不能简单(?:地)?理解为", "alarm", "直接给出正确理解"),
    Rule(
        "A11",
        r"(?:不存在|没有)(?:一个|所谓)?(?:官方|标准|唯一)(?:的)?(?:标准答案|答案|结论)",
        "alarm",
        "直接给出具体判断或分歧，不回避问题",
    ),
    Rule("A12", r"免责声明", "alarm", "删掉免责声明", validate_a12),
    Rule("A13", r"(?:边界要说清|别理解成|不要误读|不要误解)", "alarm", "删掉提醒读者别误解的话"),
    Rule(
        "A14",
        r"不能(?:擅自|自行|简单地?)(?:断言|下结论|承诺|回答|判断)",
        "alarm",
        "把读者需要知道的条件写进正文",
    ),
    Rule("A15", r"等待[^。！？\n]{0,16}(?:校准|确认|核实|补充)", "hint", "需要确认的事直接问用户"),
    Rule("A17", r"(?:按用户授权|经[^。！？\n]{0,8}终审)", "hint", "删掉授权说明"),
    Rule("A18", r"仍保留[^。！？\n]{0,16}(?:身份|责任)", "hint", "删掉责任说明"),
    Rule(
        "A19",
        r"(?:仅供参考|仅代表个人(?:观点|意见)?|个人观点，仅供参考)",
        "alarm",
        "删掉参考声明",
        validate_a19,
    ),
    Rule("A20", r"不能作为[^。！？\n]{0,20}(?:依据|凭证|证明|标准)", "alarm", "写清它能证明什么"),
    Rule("A21", r"(?:如有变动|恕不另行通知|最终解释权)", "alarm", "删掉公告式尾巴"),
    Rule("A22", r"按(?:可见|现有|公开|目前)(?:信息|资料)", "alarm", "直接写已知事实和缺口"),
    Rule("A23", r"(?:正文|文章)(?:只能|仅)(?:保留|写|呈现)", "alarm", "直接呈现内容，不写编辑纪律"),
    Rule("A24", r"不把[^。！？\n]{0,20}(?:带入|写进|放进)本文", "alarm", "内部比较没必要向读者声明"),
    Rule("A25", r"不能当成[^。！？\n]{0,20}(?:结论|依据|证据|事实)", "alarm", "写清实际能说明什么"),
]

# ---- B 组 ----

B_RULES = [
    Rule("B1", r"已(?:经)?核验", "alarm", "删掉核验标注，只写核验后的结论"),
    Rule("B2", r"(?:尚未|未)核验|未经核验", "alarm", "未做的事不写进成稿"),
    Rule("B3", r"待(?:核实|核验|查证)", "alarm", "核实完再写，或直接问用户"),
    Rule("B4", r"待确认", "hint", "需要作者决定的事在对话里问"),
    Rule(
        "B5",
        r"(?:核验状态|核验结果|核验记录|核验通过|核验清单)",
        "alarm",
        "整块内部检查删掉，不换标签保留",
        validate_b5,
    ),
    Rule(
        "B6",
        r"(?:已审核|已审阅|审核通过|审阅通过)",
        "alarm",
        "删掉审核状态",
        validate_b6,
    ),
    Rule("B7", r"视觉通过", "alarm", "删掉“视觉通过”这类内部结论"),
    Rule("B8", r"文件身份校验", "alarm", "删掉文件身份校验记录", validate_b8),
    Rule("B9", r"(?:区间校验|计算区间校验)", "alarm", "删掉区间校验记录"),
    Rule("B11", r"全部事实均来自", "alarm", "删掉自证句"),
    Rule("B12", r"无内部话术", "alarm", "删掉自证句"),
    Rule("B13", r"无虚构细节", "alarm", "删掉自证句"),
    Rule("B14", r"(?:全文|本文|正文)(?:约|共)?\s*\d+\s*(?:字|词)", "alarm", "删掉字数统计"),
    Rule("B15", r"估算未验证", "hint", "标注具体不确定性，不写自证标签"),
    Rule("B17", r"(?:本次|本轮|这次)抓取", "alarm", "删掉抓取过程"),
    Rule("B18", r"材料中", "alarm", "写材料给出的内容，不写“材料中”这个位置", validate_b18),
    Rule("B19", r"已读(?:官方)?原文", "alarm", "删掉“已读原文”"),
    Rule(
        "B20",
        r"(?:已自查|自查通过|自查完毕|自查完成|自查无问题|自查结果|自查记录|自查\s*[:：])",
        "alarm",
        "删掉自查记录",
    ),
    Rule(
        "B21",
        r"(?:Hypo-Info\s*摘要|摘要页|核对摘要页)",
        "hint",
        "内部摘要标签，成稿中删掉",
    ),
    Rule("B22", r"(?:未验证|尚未验证|没有验证)", "alarm", "没验证的事不写进成稿"),
    Rule(
        "B23",
        r"(?:^|\n)\s*(?:[-*+]\s+)?(?:\*\*)?已确认(?:\*\*)?\s*[:：]|【已确认】|\[已确认\]",
        "alarm",
        "删掉确认状态，直接写结果",
    ),
    Rule("B24", r"未经(?:核实|证实)", "alarm", "核实后再写"),
    Rule("B25", r"(?:核实完了|核实完毕|(?<!未)经核实|据核实)", "alarm", "删掉“核实完了”这类开场"),
    Rule("B26", r"事实核查(?:结果|清单|完毕|报告)", "alarm", "删掉核查过程"),
    Rule(
        "B27",
        r"(?:验证通过|检查通过|核对通过|校对通过)",
        "alarm",
        "删掉过程结论",
        validate_b27,
    ),
    Rule("B28", r"(?:输出校验通过|输出校验值一致)", "alarm", "删掉内部校验记录"),
    Rule(
        "B29",
        r"(?:无|没有|不存在)(?:任何)?(?:虚构|编造)(?:的)?(?:细节|事实|内容|信息|成分|情节)",
        "alarm",
        "删掉自证句",
    ),
    Rule("B30", r"已(?:逐份|逐条|逐项)(?:读完|核对|检查|阅读)", "alarm", "删掉过程记录"),
    Rule("B31", r"(?:信息完整|内容完整|资料完整)", "hint", "写清缺什么，不写“完整”"),
    Rule(
        "B32",
        r"本文(?:不写|不讲|不介绍|不展开|只写|只讲|只介绍)",
        "warn",
        "删掉写作计划，直接写内容",
    ),
    Rule("B33", r"本文(?:只|仅)对[^。！？\n]{0,20}负责", "alarm", "删掉责任声明"),
]

# ---- C 组 ----

C_RULES = [
    Rule("C1", r"\bStage\s*\d+(?:\.\d+)?\b", "alarm", "用读者能懂的阶段名代替 Stage 编号"),
    Rule("C2", r"\bsha256\s*[:：]?\s*[0-9a-f]{8,}\b", "alarm", "删掉哈希"),
    Rule("C3", r"\b[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\b", "alarm", "删掉 UUID"),
    Rule("C4", r"\b\d{8}T\d{6}\b", "alarm", "删掉时间戳"),
    Rule("C5", r"\b\d{4}-\d{2}-\d{2}[_-]\d{6}\b", "alarm", "删掉时间戳文件名"),
    Rule("C6", r"\b(?=[a-f0-9]*[0-9])[a-f0-9]{16,64}\b", "warn", "删掉哈希", validate_c6),
    Rule(
        "C7",
        r"\bwd_\d{4}-\d{2}-\d{2}_[0-9a-f]{8,}\b",
        "alarm",
        "项目内部文件名，成稿中用可读名称",
    ),
    Rule(
        "C8",
        r"\bfact-[0-9a-f]{16,}\.(?:png|jpe?g)\b",
        "alarm",
        "项目内部图片名，成稿中换成说明",
    ),
    Rule(
        "C9",
        r"\b(?:manifest|receipt|ledger|registry)\.(?:json|ya?ml)\b",
        "hint",
        "内部清单文件名，成稿中用读者能懂的名称",
    ),
    Rule("C10", r"\bsession-\d+\b", "alarm", "删掉会话编号"),
    Rule(
        "C11",
        r"\b\d{4}-\d{2}-\d{2}-[a-z0-9][a-z0-9-]*/",
        "hint",
        "成稿中用有含义的名称代替裸露的日期目录",
    ),
    Rule(
        "C12",
        r"[-_](?:v2|v3|fixed|final-\d+|r2)(?=\.[a-z0-9]{1,5}\b|/)",
        "hint",
        "用有含义的名称代替版本后缀",
    ),
    Rule("C13", r"(?:文件指纹|内容指纹|摘要值|哈希值)", "hint", "删掉内部指纹"),
]

# ---- D 组 ----

D_RULES = [
    Rule("D1", r"不是[^。！？\n]{1,30}(?:，|,)?(?:而是|，是)[^。！？\n]{0,40}", "warn", "直接写重心，不先否后肯"),
    Rule("D2", r"并非[^。！？\n]{1,30}(?:，|,)?而是", "warn", "直接写重心"),
    Rule("D3", r"不在于[^。！？\n]{1,30}而在于", "warn", "直接写重点"),
    Rule("D4", r"与其说[^。！？\n]{1,30}不如说", "warn", "直接写判断"),
    Rule("D5", r"(?:表面上?[^。！？\n]{1,30}(?:实际上?|实则)|看似[^。！？\n]{1,30}实则)", "warn", "写清事实关系"),
    Rule("D6", r"你以为[^。！？\n]{1,30}其实", "warn", "直接说事实"),
    Rule("D7", r"不仅[^。！？\n]{1,30}(?:而且|更|还)", "warn", "确有递进时再展开"),
    Rule(
        "D8",
        r"首先[，,](?:(?!\n\s*\n)[\s\S]){0,120}?其次[，,](?:(?!\n\s*\n)[\s\S]){0,120}?(?:最后|最终|再次)",
        "warn",
        "按内容分组，不用固定三段",
    ),
    Rule("D9", r"随着[^。！？\n]{1,30}(?:的发展|的进步|的普及|的兴起|的演进)", "warn", "直接写变化本身"),
    Rule("D10", r"在[^。！？\n]{1,30}(?:的背景下|的大背景下|的时代背景下)", "warn", "直接写当前事实"),
    Rule("D11", r"(?:值得一提的是|值得注意的是|不可否认|需要强调的是|不得不说)", "warn", "删掉过渡套话"),
    Rule("D12", r"(?:综上所述|总而言之|由此可见|不难看出|不难发现|毋庸置疑|毫无疑问)", "warn", "删掉总结套话"),
    Rule("D13", r"(?:换句话说|说白了|说到底|归根结底|一言以蔽之)", "warn", "直接说那句话"),
    Rule(
        "D14",
        r"(?:真正的[^。！？\n]{1,20}(?:(?<!不)是|在于)|问题的关键[^。！？\n]{0,10}(?:是|在于)|关键在于)",
        "warn",
        "写出具体内容，不宣布“关键”",
    ),
    Rule("D15", r"(?:先说结论|一句话总结|先说清楚一件事|先讲清楚一件事)", "warn", "直接给出内容"),
    Rule("D16", r"这不仅是[^。！？\n]{1,20}更是", "warn", "写清递进的两件事"),
    Rule("D17", r"没有[^。！？\n]{1,20}没有[^。！？\n]{1,20}只有", "warn", "改成正面说明"),
    Rule("D18", r"不[^。！？；\n，,]{1,10}不[^。！？；\n]{1,10}只是", "warn", "改成正面说明"),
    Rule("D19", r"不是[^。！？\n]{1,20}也不是[^。！？\n]{1,20}更不是", "warn", "改成正面说明"),
    Rule("D20", r"(?:让我们|下面我们|接下来我们|我们来看)", "warn", "直接开始写内容"),
    Rule("D21", r"(?:这也意味着|这就意味着|这说明|这表明)", "hint", "写清具体关系"),
    Rule("D22", r"(?:本质上|核心在于|从根本上说)", "hint", "保留有推理支撑的结论"),
    Rule("D23", r"(?:正因如此|正是因为如此|也正因如此)", "hint", "直接写原因"),
    Rule("D24", r"(?:不是[^。！？\n]{1,20}的问题[，,](?:而)?是[^。！？\n]{1,20}的问题)", "warn", "直接写要解决的问题"),
    Rule("D25", r"(?:这背后|背后是|背后反映)", "hint", "写清具体机制"),
    Rule("D26", r"从[^。！？\n]{1,15}到[^。！？\n]{1,15}[，,]再到", "hint", "确有发展关系时再写三步"),
    Rule("D27", r"与其[^。！？\n]{1,20}不如", "warn", "直接写选择"),
    Rule("D28", r"(?:所谓[^。！？\n]{1,20}(?:不过是|就是))", "warn", "直接写实际含义"),
]

# ---- E 组 ----

E_RULES = [
    Rule("E1", r"\b(?:delve|delves|delving|delved)\b", "warn", "用 look / read / examine 等普通动词", None),
    Rule("E2", r"\b(?:intricate|intricacies)\b", "warn", "说清具体复杂在哪"),
    Rule("E3", r"\b(?:underscore|underscores|underscored|underscoring)\b", "warn", "用 show / point to"),
    Rule("E4", r"\btestament\b", "warn", "用 evidence / proof"),
    Rule("E5", r"\btapestry\b", "warn", "删掉比喻"),
    Rule("E6", r"\blandscape\b", "warn", "说清具体领域或范围"),
    Rule("E7", r"\b(?:pivotal|crucial|vital)\b", "warn", "写清为什么重要"),
    Rule("E8", r"\brobust\b", "warn", "写清可靠性证据"),
    Rule("E9", r"\bseamless(?:ly)?\b", "warn", "写清衔接方式"),
    Rule("E10", r"\bleverage\b", "warn", "用 use"),
    Rule("E11", r"\bfoster(?:ing|s|ed)?\b", "warn", "用 support / encourage"),
    Rule("E12", r"\bgarner(?:ing|s|ed)?\b", "warn", "用 get / receive"),
    Rule("E13", r"\bshowcas(?:e|es|ing|ed)\b", "warn", "用 show"),
    Rule("E14", r"\balign(?:s|ed|ing)? with\b", "warn", "用 match / follow"),
    Rule("E15", r"\bboasts?\b", "warn", "用 has"),
    Rule("E16", r"\bbolster(?:s|ed|ing)?\b", "warn", "用 support"),
    Rule("E17", r"\bmeticulous(?:ly)?\b", "warn", "写清具体做法"),
    Rule("E18", r"\bnuanced?\b", "warn", "写清差别在哪"),
    Rule("E19", r"\binterplay\b", "warn", "写清两者如何相互影响"),
    Rule("E20", r"\b(?:myriad|plethora)\b", "warn", "用 many"),
    Rule("E21", r"\bparadigm\b", "warn", "用 model / approach"),
    Rule("E22", r"\bholistic\b", "warn", "写清包含哪些方面"),
    Rule("E23", r"\bsynerg(?:y|ies|istic)\b", "warn", "写清配合关系"),
    Rule("E24", r"\b(?:groundbreaking|cutting-edge|game-chang(?:er|ing))\b", "warn", "写清新在哪"),
    Rule("E25", r"\bnot (?:just|only|merely)\b[^.!?\n]{1,60}\bbut\b", "warn", "直接写两件事"),
    Rule("E26", r"(?:\b(?:it'?s|it is) worth noting\b|\bimportant to note\b)", "warn", "删掉提示语"),
    Rule("E27", r"(?:\bin today'?s (?:fast-paced|ever-changing) world\b|\bin an era of\b)", "warn", "直接写当下事实"),
    Rule("E28", r"\blet'?s (?:dive|unpack|explore)\b", "warn", "直接开始"),
    Rule("E29", r"(?:\bat the end of the day\b|\bwhen all is said and done\b)", "warn", "直接写结论"),
    Rule("E30", r"(?:\bit remains to be seen\b|\bonly time will tell\b)", "warn", "写清已知和未知"),
    Rule("E31", r"\b(?:serves?|stands?) as\b", "warn", "用 is"),
    Rule("E32", r"\b(?:in conclusion|to sum up)\b", "warn", "删掉总结语"),
    Rule(
        "E33",
        r"(?:\bshed light on\b|\bnavigate the complexities of\b|\bunlock the potential\b|\bpave the way\b)",
        "warn",
        "直接写发现或作用",
    ),
    Rule("E34", r"\b(?:ever-evolving|multifaceted)\b", "warn", "写清具体变化或方面"),
    Rule("E35", r"\b(?:furthermore|moreover)\b", "warn", "删掉或换成具体连接"),
    Rule("E36", r"\bin the realm of\b", "warn", "用 in"),
    Rule("E37", r"\bplays? a (?:crucial|key|vital) role\b", "warn", "写清起什么作用"),
]

# ---- F 组 ----

F_RULES = [
    Rule("F1", r"赋能", "warn", "改成“让……能够……”"),
    Rule("F2", r"助力", "warn", "改成“帮助”"),
    Rule("F4", r"闭环", "warn", "说清从哪一步到哪一步"),
    Rule("F5", r"抓手", "warn", "说清具体做什么"),
    Rule("F6", r"颗粒度", "warn", "说清分到多细"),
    Rule("F7", r"底层逻辑", "warn", "说清实际机制"),
    Rule("F8", r"范式", "warn", "用“做法”或“模式”"),
    Rule("F9", r"护城河", "warn", "说清具体优势"),
    Rule("F10", r"拉通", "warn", "说清谁和谁怎么接上"),
    Rule("F11", r"打通", "warn", "说清具体接上了什么"),
    Rule("F12", r"组合拳", "warn", "列出具体几项"),
    Rule("F13", r"生态位", "warn", "说清在哪个环节、和谁比"),
    Rule("F14", r"顶层设计", "warn", "说清设计了什么"),
    Rule("F15", r"全链路", "warn", "说清覆盖哪些环节"),
    Rule("F16", r"降维(?:打击|竞争)?", "warn", "说清具体优势"),
    Rule("F17", r"认知(?:升级|升维)", "warn", "说清改变了什么判断"),
    Rule("F18", r"价值锚点", "warn", "说清具体价值"),
    Rule("F19", r"第二曲线", "warn", "说清新的业务是什么"),
    Rule("F20", r"飞轮(?:效应)?", "warn", "说清循环的具体环节"),
    Rule("F21", r"心智", "warn", "说清读者实际怎么想"),
    Rule("F22", r"势能", "warn", "说清具体条件"),
    Rule("F23", r"破局", "warn", "说清打破了什么"),
    Rule("F24", r"卡点", "warn", "说清卡在哪一步"),
    Rule("F25", r"(?:对齐(?:目标|预期|认知|利益|诉求|节奏|方向|口径|需求)|(?:目标|预期|认知|利益|诉求|方向)对齐)", "warn", "说清对齐了什么"),
    Rule(
        "F26",
        r"(?:把[^。！？\n]{0,16}收敛(?:到|成|为)?|收敛(?:到|成|为))",
        "warn",
        "说清归到哪个具体结论",
        validate_f26,
    ),
    Rule("F27", r"(?:统一|同一|这个|按这个|对齐)口径", "warn", "说清统计的是什么"),
    Rule("F28", r"(?:内在|叙事|情感)张力|张力(?:十足|拉满)", "warn", "说清冲突在哪"),
    Rule("F29", r"悬置", "warn", "说清暂时不处理什么"),
    Rule("F30", r"在场者", "warn", "说清具体是谁"),
    Rule("F31", r"勾勒(?:出)?(?:一条|了)?(?:线索|轮廓)", "warn", "用“归纳”“说明”"),
    Rule("F33", r"沉淀(?:出|为|成)", "warn", "说清具体积累了什么"),
    Rule("F34", r"保持同频", "warn", "说清双方实际怎么配合"),
    Rule("F35", r"多维赋能", "warn", "说清具体提升了哪几个方面"),
]

# ---- G 组 ----

G_RULES = [
    Rule(
        "G1",
        r"(?:往深了拆|拆开看|拆解一下|往深里拆|掰开揉碎)",
        "warn",
        "直接写分析内容",
    ),
    Rule("G2", r"(?:切得很狠|足够狠|太狠了|够狠)", "warn", "直接写判断"),
    Rule("G3", r"(?:痛点|刺痛|最痛的)", "warn", "写清具体困难"),
    Rule(
        "G4",
        r"接(?:得|不)?住[^。！？\n]{0,10}(?:情绪|需求|期待|焦虑|用户|读者)",
        "warn",
        "说清具体回应了什么",
    ),
    Rule("G5", r"(?:精准命中|直击痛点|击中痛点|击中人心)", "warn", "说清命中了什么"),
    Rule("G6", r"拉满", "warn", "说清到什么程度"),
    Rule("G7", r"(?:打透|打穿)", "warn", "说清分析到什么程度"),
    Rule(
        "G8",
        r"(?:记忆|时间|焦虑|情绪|历史)[^。！？\n]{0,6}(?:仓库|抽屉|温度|钥匙|形状|重量)|"
        r"(?:时代的底色|历史的褶皱)",
        "warn",
        "删掉感官抽屉比喻",
    ),
    Rule("G9", r"(?:这样写很漂亮|读起来很丝滑)", "warn", "写清具体好在哪"),
    Rule("G10", r"(?:读起来会很舒服|很有呼吸感)", "warn", "写清具体阅读效果"),
    Rule("G11", r"很有意思的一点是", "warn", "直接写那一点"),
    Rule("G12", r"扎心", "warn", "说清为什么难受"),
    Rule("G13", r"窒息感", "warn", "说清具体压迫来自哪里"),
    Rule("G14", r"封神", "warn", "写清具体表现"),
    Rule("G15", r"炸裂", "warn", "写清具体程度"),
    Rule("G16", r"杀疯了", "warn", "写清具体表现"),
    Rule("G17", r"赢麻了", "warn", "写清赢了什么"),
    Rule("G18", r"细思极恐", "warn", "写清具体推论"),
    Rule("G19", r"信息量很大", "warn", "说清信息是什么"),
    Rule("G20", r"说句难听的", "warn", "直接说那句话"),
    Rule("G21", r"刀刀见血", "warn", "写清具体判断"),
]

# ---- H 组 ----

H_RULES = [
    Rule(
        "H1",
        r"(?:如果|若)你(?:问|说)的是|你(?:实际|真正)(?:想问|要问|问|想)的是|你真正关心的其实是",
        "alarm",
        "直接回应原问题",
    ),
    Rule(
        "H2",
        r"(?:很多人会误以为|很多人误以为|这是常见的误解|容易混淆的点(?:是|在于)|可能有点反直觉|很多人不知道)",
        "warn",
        "单次不判阳性；检查是否在预设误解",
    ),
    Rule(
        "H3",
        r"(?:你的批评成立|这一点我认|我先把话说清楚|你的观点完全站得住脚|不得不承认你说得对)",
        "alarm",
        "直接讨论问题本身",
    ),
    Rule("H4", r"(?:你需要记住的是|希望读者带走的是|你应该记住)", "alarm", "直接写内容和理由"),
    Rule("H5", r"(?:你可能会问|有人可能会说|你也许会想)", "hint", "删掉假想反对观点"),
]

RULES: list[Rule] = A_RULES + B_RULES + C_RULES + D_RULES + E_RULES + F_RULES + G_RULES + H_RULES

# I 组只做密度统计。
I_TERMS: list[tuple[str, str]] = [
    ("I1", "然而"),
    ("I2", "其实"),
    ("I3", "反而"),
    ("I4", "甚至"),
    ("I5", "尤其"),
    ("I6", "稍微"),
    ("I7", "有点"),
    ("I8", "某种意义上"),
    ("I9", "某种程度上"),
    ("I10", "挺"),
    ("I11", r"偏(?!好|见|差|执|离|颇|向|重|轻)"),
    ("I12", "——"),
]

COMPILED: list[tuple[Rule, re.Pattern[str]]] = [
    (rule, re.compile(rule.pattern, re.IGNORECASE if rule.group == "E" else 0)) for rule in RULES
]
COMPILED_I: list[tuple[str, re.Pattern[str]]] = [(code, re.compile(pat)) for code, pat in I_TERMS]
I_LABELS: dict[str, str] = {
    code: ("偏" if code == "I11" else pattern) for code, pattern in I_TERMS
}

# ---------------------------------------------------------------------------
# 预处理：保留字符位置，把不扫描的区间换成空格
# ---------------------------------------------------------------------------

FENCE_RE = re.compile(r"^\s*(?:```|~~~)")
QUOTE_PATTERNS = [
    re.compile(r"“([^”]{0,300})”"),
    re.compile(r"「([^」]{0,300})」"),
    re.compile(r"『([^』]{0,300})』"),
    re.compile(r"‘([^’]{0,300})’"),
    re.compile(r'"([^"]{0,300})"'),
]
FRONTMATTER_RE = re.compile(r"\A---\s*\n.*?\n---[ \t]*(?:\n|\Z)", re.S)
HTML_COMMENT_RE = re.compile(r"<!--.*?-->", re.S)
INLINE_CODE_RE = re.compile(r"`[^`\n]*`")
URL_RE = re.compile(r"https?://[^\s<>()\]）]+")
MDX_IMPORT_RE = re.compile(r"^\s*(?:import|export)\s+[^\n]*$", re.M)
MDX_JSX_RE = re.compile(r"<[A-Za-z][^>\n]{0,300}>|</[A-Za-z][^>\n]{0,100}>")
MDX_EXPR_RE = re.compile(r"^\s*\{[^\n]*\}\s*$", re.M)
ORIGINAL_MARK_RE = re.compile(
    r"<!--\s*原始文本开始\s*-->(.*?)<!--\s*原始文本结束\s*-->", re.S
)
HEADING_RE = re.compile(r"^(#{1,6})\s+\S")


def _blank(chars: list[str], start: int, end: int) -> None:
    for index in range(max(0, start), min(len(chars), end)):
        if chars[index] != "\n":
            chars[index] = " "


def _line_offsets(text: str) -> list[int]:
    offsets = [0]
    for match in re.finditer(r"\n", text):
        offsets.append(match.end())
    return offsets


def _mask_blockquotes(chars: list[str], text: str) -> None:
    offsets = _line_offsets(text)
    for index, offset in enumerate(offsets):
        end = offsets[index + 1] if index + 1 < len(offsets) else len(text)
        line = text[offset:end]
        if line.lstrip().startswith(">"):
            _blank(chars, offset, end)


def _mask_fences(chars: list[str], text: str) -> None:
    offsets = _line_offsets(text)
    fenced = False
    for index, offset in enumerate(offsets):
        end = offsets[index + 1] if index + 1 < len(offsets) else len(text)
        line = text[offset:end]
        if FENCE_RE.match(line):
            _blank(chars, offset, end)
            fenced = not fenced
            continue
        if fenced:
            _blank(chars, offset, end)


def _mask_quotes(chars: list[str], text: str) -> None:
    for pattern in QUOTE_PATTERNS:
        for match in pattern.finditer(text):
            inner = match.group(1)
            # 允许短引文跨一行；超过一行多半是整段引用，保留扫描。
            if inner.count("\n") <= 1 and len(re.sub(r"\s", "", inner)) < 25:
                _blank(chars, match.start(1), match.end(1))


def _original_spans(text: str) -> list[tuple[int, int]]:
    spans = [(match.start(1), match.end(1)) for match in ORIGINAL_MARK_RE.finditer(text)]
    if spans:
        return _merge_spans(spans)

    offsets = _line_offsets(text)
    spans = []
    for index, offset in enumerate(offsets):
        end = offsets[index + 1] if index + 1 < len(offsets) else len(text)
        line = text[offset:end]
        heading = HEADING_RE.match(line)
        if not heading or not re.search(r"(?:原文|原始)", line):
            continue
        level = len(heading.group(1))
        block_end = len(text)
        for later in range(index + 1, len(offsets)):
            later_start = offsets[later]
            later_end = offsets[later + 1] if later + 1 < len(offsets) else len(text)
            later_line = text[later_start:later_end]
            later_heading = HEADING_RE.match(later_line)
            if later_heading and len(later_heading.group(1)) <= level:
                block_end = later_start
                break
        spans.append((end, block_end))
    return _merge_spans(spans)


def _merge_spans(spans: Sequence[tuple[int, int]]) -> list[tuple[int, int]]:
    merged: list[tuple[int, int]] = []
    for start, end in sorted(spans):
        if merged and start <= merged[-1][1]:
            merged[-1] = (merged[-1][0], max(merged[-1][1], end))
        else:
            merged.append((start, end))
    return merged


def prepare_text(text: str, raw: bool = False, extract_original: bool = False) -> str:
    """返回与原文等长的扫描文本，不扫描的位置填空格。"""
    chars = list(text)

    if extract_original:
        spans = _original_spans(text)
        if spans:
            keep = bytearray(len(text))
            for start, end in spans:
                keep[start:end] = b"\x01" * (end - start)
            for index, char in enumerate(chars):
                if char != "\n" and not keep[index]:
                    chars[index] = " "

    frontmatter = FRONTMATTER_RE.match(text)
    if frontmatter:
        _blank(chars, frontmatter.start(), frontmatter.end())

    for match in HTML_COMMENT_RE.finditer(text):
        _blank(chars, match.start(), match.end())

    _mask_fences(chars, text)

    for match in INLINE_CODE_RE.finditer(text):
        _blank(chars, match.start(), match.end())

    for match in URL_RE.finditer(text):
        _blank(chars, match.start(), match.end())

    _mask_blockquotes(chars, text)

    for match in MDX_IMPORT_RE.finditer(text):
        _blank(chars, match.start(), match.end())
    for match in MDX_JSX_RE.finditer(text):
        _blank(chars, match.start(), match.end())
    for match in MDX_EXPR_RE.finditer(text):
        _blank(chars, match.start(), match.end())

    if not raw:
        _mask_quotes(chars, text)

    return "".join(chars)


def count_characters(text: str) -> int:
    """中文汉字和标点各算 1 字；英文连续字母、数字按 1 个词算 1 字。"""
    non_ascii = len(re.findall(r"[^\x00-\x7f\s]", text))
    words = len(re.findall(r"[A-Za-z0-9]+(?:[._'\u2019-][A-Za-z0-9]+)*", text))
    return non_ascii + words


def line_starts(text: str) -> list[int]:
    starts = [0]
    for match in re.finditer(r"\n", text):
        starts.append(match.end())
    return starts


# ---------------------------------------------------------------------------
# 扫描
# ---------------------------------------------------------------------------


@dataclass
class Finding:
    file: str
    line: int
    column: int  # 0-based start in the line
    end_column: int
    group: str
    code: str
    text: str
    level: str
    advice: str
    density: bool = False
    offset: int = 0
    end_offset: int = 0

    @property
    def group_name(self) -> str:
        return GROUP_NAMES[self.group]

    def as_dict(self) -> dict:
        return {
            "file": self.file,
            "line": self.line,
            "column": [self.column, self.end_column],
            "group": self.group,
            "group_name": self.group_name,
            "code": self.code,
            "text": self.text,
            "level": self.level,
            "density": self.density,
            "advice": self.advice,
        }

    def sort_key(self) -> tuple:
        return (self.line, self.column, -LEVEL_PRIORITY.get(self.level, 0), self.code)


@dataclass
class ScanResult:
    path: str
    display_name: str
    text: str
    char_count: int
    findings: list[Finding]
    group_counts: dict[str, int]
    group_alarms: dict[str, int]
    group_density: dict[str, float]
    softening_counts: dict[str, int]
    softening_density: float
    hard_alarms: list[Finding]
    fail_groups: list[str]
    warn_groups: list[str]
    combined_count: int
    combined_density: float
    combined_fail: bool
    combined_warn: bool
    verdict: str
    score: int
    is_positive: bool
    reasons: list[str]
    total_density: float

    @property
    def summary(self) -> str:
        return f"{self.verdict} · {self.score}/100"


def _match_spans(text: str, pattern: re.Pattern[str]) -> Iterable[re.Match[str]]:
    return pattern.finditer(text)


def scan_text(
    path: str,
    display_name: str,
    original_text: str,
    *,
    raw: bool = False,
    extract_original: bool = False,
) -> ScanResult:
    work = prepare_text(original_text, raw=raw, extract_original=extract_original)
    char_count = count_characters(work)
    starts = line_starts(work)
    candidates: dict[str, list[Finding]] = {group: [] for group in GROUP_NAMES}

    def make_finding(span_start: int, span_end: int, template: Finding, text: str) -> Finding:
        line_index = bisect.bisect_right(starts, span_start) - 1
        line = line_index + 1
        column = span_start - starts[line_index]
        return Finding(
            file=path,
            line=line,
            column=column,
            end_column=column + (span_end - span_start),
            group=template.group,
            code=template.code,
            text=text,
            level=template.level,
            advice=template.advice,
            density=template.density,
            offset=span_start,
            end_offset=span_end,
        )

    for rule, pattern in COMPILED:
        for match in _match_spans(work, pattern):
            if rule.validator and not rule.validator(work, match):
                continue
            raw_text = match.group(0)
            stripped = raw_text.strip()
            if not stripped:
                continue
            start = match.start() + (len(raw_text) - len(raw_text.lstrip()))
            end = start + len(stripped)
            # 完全落在代码、引用或引号豁免区的匹配不保留。
            if not work[start:end].strip():
                continue
            # 展示文本取原文，避免把被抹空的引号拼成空格串。
            excerpt = re.sub(r"\s+", " ", original_text[start:end]).strip()
            if not excerpt:
                continue
            if len(excerpt) > 48:
                excerpt = excerpt[:47] + "…"
            template = Finding(
                file=path,
                line=1,
                column=0,
                end_column=0,
                group=rule.group,
                code=rule.code,
                text=excerpt,
                level=rule.level,
                advice=rule.advice or GROUP_ADVICE[rule.group],
                density=rule.group in DENSITY_GROUPS,
                offset=start,
                end_offset=end,
            )
            candidates[rule.group].append(make_finding(start, end, template, excerpt))

    softening_counts: Counter[str] = Counter()
    for code, pattern in COMPILED_I:
        count = len(pattern.findall(work))
        if count:
            softening_counts[code] = count

    findings: list[Finding] = []
    for group_items in candidates.values():
        group_items.sort(key=lambda item: (item.offset, item.end_offset))
        merged_items: list[Finding] = []
        for item in group_items:
            if merged_items and item.offset < merged_items[-1].end_offset:
                current = merged_items.pop()
                span_start = min(current.offset, item.offset)
                span_end = max(current.end_offset, item.end_offset)
                current_rank = (
                    LEVEL_PRIORITY.get(current.level, 0),
                    current.end_offset - current.offset,
                )
                item_rank = (
                    LEVEL_PRIORITY.get(item.level, 0),
                    item.end_offset - item.offset,
                )
                representative = item if item_rank > current_rank else current
                excerpt = re.sub(r"\s+", " ", original_text[span_start:span_end]).strip()
                if len(excerpt) > 48:
                    excerpt = excerpt[:47] + "…"
                merged_items.append(
                    make_finding(span_start, span_end, representative, excerpt)
                )
            else:
                merged_items.append(item)
        findings.extend(merged_items)
    findings.sort(key=lambda item: item.sort_key())

    group_counts = {group: 0 for group in GROUP_NAMES}
    group_alarms = {group: 0 for group in GROUP_NAMES}
    group_density = {group: 0.0 for group in GROUP_NAMES}
    for finding in findings:
        group_counts[finding.group] += 1
        if finding.level == "alarm":
            group_alarms[finding.group] += 1
    denominator = max(char_count, 1)
    for group in GROUP_NAMES:
        group_density[group] = round(group_counts[group] / denominator * 1000, 2)
    softening_total = sum(softening_counts.values())
    softening_density = round(softening_total / denominator * 1000, 2)

    combined_count = sum(group_counts[group] for group in DENSITY_GROUPS)
    combined_density = round(combined_count / denominator * 1000, 2)
    combined_fail = (
        combined_count >= COMBINED_THRESHOLD["min_hits"]
        and combined_density >= COMBINED_THRESHOLD["fail"]
    )
    combined_warn = (
        not combined_fail
        and combined_count >= COMBINED_THRESHOLD["min_hits"]
        and combined_density >= COMBINED_THRESHOLD["warn"]
    )

    hard_alarms = [
        finding
        for finding in findings
        if finding.level == "alarm" and finding.group in HARD_GROUPS and not finding.density
    ]
    fail_groups: list[str] = []
    warn_groups: list[str] = []
    for group, config in DENSITY_THRESHOLDS.items():
        hits = group_counts[group]
        density = group_density[group]
        if hits >= config["min_hits"] and density >= config["fail"]:
            fail_groups.append(group)
        elif hits >= config["min_hits"] and density >= config["warn"]:
            warn_groups.append(group)

    score = 0
    if hard_alarms:
        score += 60 + 15 * (len(hard_alarms) - 1)
    for group in fail_groups:
        score += 60
    for group in warn_groups:
        score += 18
    if combined_fail:
        score += 45
    elif combined_warn:
        score += 16
    if softening_density > SOFTENING_BASELINE:
        score += 6
    score = min(100, score)

    reasons: list[str] = []
    for finding in hard_alarms:
        reasons.append(
            f"{finding.code}「{finding.text}」：{finding.group_name}（单次即报警）"
        )
    for group in fail_groups:
        reasons.append(
            f"{group} 组 {group_density[group]:.1f}/千字（阈值 {DENSITY_THRESHOLDS[group]['fail']:.1f}），"
            f"{group_counts[group]} 条"
        )
    for group in warn_groups:
        reasons.append(
            f"{group} 组接近阈值 {group_density[group]:.1f}/千字，{group_counts[group]} 条"
        )
    if combined_fail:
        reasons.append(
            f"D–G 组合 {combined_density:.1f}/千字（阈值 {COMBINED_THRESHOLD['fail']:.1f}），"
            f"{combined_count} 条"
        )
    elif combined_warn:
        reasons.append(
            f"D–G 组合接近阈值 {combined_density:.1f}/千字，{combined_count} 条"
        )
    if softening_density > SOFTENING_BASELINE:
        reasons.append(f"I 组软化虚词 {softening_density:.1f}/千字，高于参考基线 {SOFTENING_BASELINE:.1f}")

    if hard_alarms or fail_groups or combined_fail:
        verdict = "FAIL"
    elif warn_groups or combined_warn:
        verdict = "WARN"
    else:
        verdict = "PASS"

    # 短文本的千字密度会被放大；没有硬伤时最多判 WARN。
    if char_count < 150 and not hard_alarms and verdict == "FAIL":
        verdict = "WARN"
        score = min(score, 55)
        reasons.append("文本不足 150 字，密度命中只按 WARN 提示")

    total_density = round(len(findings) / denominator * 1000, 2)

    return ScanResult(
        path=path,
        display_name=display_name,
        text=original_text,
        char_count=char_count,
        findings=findings,
        group_counts=group_counts,
        group_alarms=group_alarms,
        group_density=group_density,
        softening_counts=dict(softening_counts),
        softening_density=softening_density,
        hard_alarms=hard_alarms,
        fail_groups=fail_groups,
        warn_groups=warn_groups,
        combined_count=combined_count,
        combined_density=combined_density,
        combined_fail=combined_fail,
        combined_warn=combined_warn,
        verdict=verdict,
        score=score,
        is_positive=verdict == "FAIL",
        reasons=reasons,
        total_density=total_density,
    )


# ---------------------------------------------------------------------------
# 文件收集与预处理
# ---------------------------------------------------------------------------

TEXT_SUFFIXES = {".md", ".mdx", ".markdown", ".txt"}


def collect_files(paths: Sequence[str]) -> list[Path]:
    files: list[Path] = []
    seen: set[Path] = set()
    for item in paths:
        path = Path(item)
        if not path.exists():
            raise SystemExit(f"找不到路径：{item}")
        if path.is_dir():
            candidates = sorted(
                child
                for child in path.rglob("*")
                if child.is_file() and child.suffix.lower() in TEXT_SUFFIXES
            )
        else:
            candidates = [path]
        for child in candidates:
            resolved = child.resolve()
            if resolved in seen:
                continue
            seen.add(resolved)
            files.append(child)
    return files


def read_text(path: Path) -> str:
    return path.read_text(encoding="utf-8", errors="replace")


# ---------------------------------------------------------------------------
# 文本输出
# ---------------------------------------------------------------------------


def format_count(number: int) -> str:
    return f"{number:,}"


def format_scan_txt(result: ScanResult, verbose: bool = False) -> str:
    lines: list[str] = []
    lines.append(f"文件：{result.path}")
    lines.append(
        f"字数 {format_count(result.char_count)} · 判定 {result.verdict} · "
        f"AI 味得分 {result.score}/100 · 千字命中 {result.total_density:.1f}"
    )

    active_groups = [group for group in GROUP_NAMES if result.group_counts[group] or group == "I"]
    lines.append("")
    lines.append("类别")
    for group in GROUP_NAMES:
        if group == "I":
            if result.softening_density:
                note = ""
                if result.softening_density > SOFTENING_BASELINE:
                    note = f"（高于参考基线 {SOFTENING_BASELINE:.1f}）"
                lines.append(
                    f"  {group} {GROUP_NAMES[group]:<10} {result.softening_density:5.1f}/千字{note}"
                )
            continue
        count = result.group_counts[group]
        if not count and group not in active_groups:
            continue
        density = result.group_density[group]
        extra = ""
        if group in DENSITY_THRESHOLDS and count >= DENSITY_THRESHOLDS[group]["min_hits"]:
            threshold = DENSITY_THRESHOLDS[group]
            if group in result.fail_groups:
                extra = f"（{density:.1f}/千字，超过阈值 {threshold['fail']:.1f}）"
            elif group in result.warn_groups:
                extra = f"（{density:.1f}/千字，接近阈值）"
            else:
                extra = f"（{density:.1f}/千字）"
        lines.append(f"  {group} {GROUP_NAMES[group]:<10} {count:2d} 条{extra}")

    if result.combined_count:
        note = ""
        if result.combined_fail:
            note = f"（超过阈值 {COMBINED_THRESHOLD['fail']:.1f}）"
        elif result.combined_warn:
            note = f"（接近阈值 {COMBINED_THRESHOLD['warn']:.1f}）"
        lines.append(
            f"  D–G 合计 {result.combined_count:2d} 条 {result.combined_density:.1f}/千字{note}"
        )

    hard_findings = [finding for finding in result.findings if not finding.density]
    if hard_findings:
        lines.append("")
        lines.append("问题位置")
        for finding in hard_findings:
            lines.append(
                f"  {finding.line}: [{finding.code} {finding.group_name}] "
                f"命中「{finding.text}」 -> {finding.advice}"
            )

    density_findings = [
        finding
        for finding in result.findings
        if finding.density and finding.group in set(result.fail_groups) | set(result.warn_groups)
    ]
    if density_findings:
        lines.append("")
        lines.append("密度超标类别明细")
        shown = Counter()
        for finding in density_findings:
            if shown[finding.group] >= 20:
                continue
            shown[finding.group] += 1
            lines.append(
                f"  {finding.line}: [{finding.code} {finding.group_name}·密度] "
                f"命中「{finding.text}」 -> {finding.advice}"
            )
        for group, count in shown.items():
            if result.group_counts[group] > count:
                lines.append(f"  {group} 组另有 {result.group_counts[group] - count} 条未列出。")

    if result.softening_counts:
        items = "、".join(
            f"{I_LABELS[code]} {count}"
            for code, count in sorted(
                result.softening_counts.items(), key=lambda item: (-item[1], item[0])
            )
        )
        lines.append("")
        lines.append(
            f"软化虚词/破折号：{result.softening_density:.1f}/千字（{items}）"
        )
        if result.softening_density > SOFTENING_BASELINE:
            lines.append(f"  高于参考基线 {SOFTENING_BASELINE:.1f}/千字，仅作密度提示。")

    if result.reasons:
        lines.append("")
        lines.append("判定依据")
        for reason in result.reasons:
            lines.append(f"  - {reason}")

    if verbose:
        lines.append("")
        lines.append("全部命中")
        for finding in result.findings:
            lines.append(
                f"  {finding.line}:{finding.column + 1} [{finding.code} {finding.group_name}] "
                f"{finding.level} 命中「{finding.text}」"
            )

    return "\n".join(lines)


def format_summary_txt(results: Sequence[ScanResult]) -> str:
    if len(results) <= 1:
        return ""
    verdicts = Counter(result.verdict for result in results)
    total_chars = sum(result.char_count for result in results)
    lines = [
        "",
        "汇总",
        f"  {len(results)} 个文件；字数 {format_count(total_chars)}；"
        f"PASS {verdicts['PASS']} / WARN {verdicts['WARN']} / FAIL {verdicts['FAIL']}",
    ]
    positives = [result for result in results if result.is_positive]
    if positives:
        lines.append("  判定阳性：" + "、".join(result.display_name for result in positives))
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# JSON
# ---------------------------------------------------------------------------


def scan_result_dict(result: ScanResult) -> dict:
    return {
        "file": result.path,
        "char_count": result.char_count,
        "verdict": result.verdict,
        "score": result.score,
        "is_positive": result.is_positive,
        "density_per_1000": result.total_density,
        "groups": {
            group: {
                "name": GROUP_NAMES[group],
                "count": (
                    sum(result.softening_counts.values())
                    if group == "I"
                    else result.group_counts[group]
                ),
                "alarm_count": result.group_alarms[group],
                "density_per_1000": (
                    result.softening_density if group == "I" else result.group_density[group]
                ),
            }
            for group in GROUP_NAMES
        },
        "combined_DEFG": {
            "count": result.combined_count,
            "density_per_1000": result.combined_density,
            "fail": result.combined_fail,
            "warn": result.combined_warn,
        },
        "softening": {
            "count": sum(result.softening_counts.values()),
            "density_per_1000": result.softening_density,
            "baseline": SOFTENING_BASELINE,
            "items": {
                I_LABELS[code]: count for code, count in result.softening_counts.items()
            },
        },
        "reasons": result.reasons,
    }


def build_scan_json(results: Sequence[ScanResult]) -> dict:
    total_chars = sum(result.char_count for result in results)
    total_findings = sum(len(result.findings) for result in results)
    group_counts = {group: 0 for group in GROUP_NAMES}
    group_density = {group: 0.0 for group in GROUP_NAMES}
    softening_counts: Counter[str] = Counter()
    for result in results:
        for group in GROUP_NAMES:
            group_counts[group] += result.group_counts[group]
        softening_counts.update(result.softening_counts)
    group_counts["I"] = sum(softening_counts.values())
    denominator = max(total_chars, 1)
    for group in GROUP_NAMES:
        group_density[group] = round(group_counts[group] / denominator * 1000, 2)
    verdicts = Counter(result.verdict for result in results)
    positives = [result for result in results if result.is_positive]
    summary = {
        "files": len(results),
        "char_count": total_chars,
        "finding_count": total_findings,
        "density_per_1000": round(total_findings / denominator * 1000, 2),
        "verdict": (
            "FAIL" if any(result.verdict == "FAIL" for result in results)
            else "WARN" if any(result.verdict == "WARN" for result in results)
            else "PASS"
        ),
        "verdicts": {"PASS": verdicts["PASS"], "WARN": verdicts["WARN"], "FAIL": verdicts["FAIL"]},
        "is_positive": bool(positives),
        "groups": {
            group: {
                "name": GROUP_NAMES[group],
                "count": group_counts[group],
                "density_per_1000": group_density[group],
            }
            for group in GROUP_NAMES
        },
        "thresholds": {
            group: dict(config) for group, config in DENSITY_THRESHOLDS.items()
        },
        "combined_DEFG_threshold": dict(COMBINED_THRESHOLD),
        "softening_baseline": SOFTENING_BASELINE,
        "reasons": [
            f"{result.display_name}：{reason}"
            for result in positives
            for reason in result.reasons
        ],
    }
    return {
        "tool": "scan_expression.py",
        "version": VERSION,
        "generated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "mode": "scan",
        "summary": summary,
        "files": [scan_result_dict(result) for result in results],
        "findings": [
            finding.as_dict()
            for result in results
            for finding in result.findings
        ],
    }


# ---------------------------------------------------------------------------
# HTML
# ---------------------------------------------------------------------------

GROUP_COLORS = {
    "A": "#b5534b",
    "B": "#a06a2c",
    "C": "#73558f",
    "D": "#3f6c93",
    "E": "#38786e",
    "F": "#8a7433",
    "G": "#9a5570",
    "H": "#7a6248",
    "I": "#6c737a",
}

HTML_STYLE = """
:root{color-scheme:light}
*{box-sizing:border-box}
body{margin:0;background:#f5f5f2;color:#20262c;font:15px/1.75 "Noto Sans CJK SC","PingFang SC","Microsoft YaHei",system-ui,sans-serif}
main{max-width:1500px;margin:0 auto;padding:26px 30px 80px}
h1{font-size:25px;line-height:1.35;margin:0 0 10px;font-weight:650}
h2{font-size:18px;margin:38px 0 12px;font-weight:650}
h3{font-size:16px;margin:22px 0 8px;font-weight:650}
a{color:#1d5c83;text-decoration:none}
a:hover{text-decoration:underline}
code,.mono{font-family:"SFMono-Regular",ui-monospace,Menlo,Consolas,monospace;font-size:.9em}
.dot{display:inline-block;width:8px;height:8px;border-radius:50%;margin-right:5px;vertical-align:0}
.muted{color:#5d6873}
.small{font-size:13px}
.summary{display:flex;flex-wrap:wrap;gap:10px 26px;align-items:baseline;margin:12px 0 4px}
.metric{font-variant-numeric:tabular-nums}
.metric strong{font-size:20px;font-weight:650}
.verdict{display:inline-block;padding:1px 8px;border-radius:3px;font-weight:650;color:#fff;font-size:13px;vertical-align:2px}
.verdict.PASS{background:#4b7d5b}.verdict.WARN{background:#9a7a35}.verdict.FAIL{background:#a44a43}
.chips{display:flex;flex-wrap:wrap;gap:6px;margin:12px 0 2px}
.chip{border:1px solid #d3d9dc;background:#fff;border-radius:3px;padding:2px 8px;font-size:12.5px;cursor:pointer;color:#36434d;font-family:inherit}
.chip b{font-weight:650}
.chip[aria-pressed="false"]{opacity:.38}
.chip .dot{display:inline-block;width:8px;height:8px;border-radius:50%;margin-right:5px;vertical-align:0}
.tabs{display:flex;flex-wrap:wrap;gap:4px;border-bottom:1px solid #d7dde0;margin:22px 0 0}
.tab{border:0;border-bottom:2px solid transparent;background:none;padding:7px 11px;font:inherit;font-size:13.5px;cursor:pointer;color:#52606b}
.tab.active{border-bottom-color:#20262c;color:#20262c}
.panel{display:none}.panel.active{display:block}
.doc-head{display:flex;flex-wrap:wrap;gap:14px 24px;align-items:baseline;margin:14px 0 10px;font-size:13.5px;color:#43505a}
.layout{display:grid;grid-template-columns:minmax(0,1fr) 360px;gap:26px;align-items:start}
.paper{background:#fff;border:1px solid #dce1e4;border-radius:5px;padding:18px 20px;min-width:0}
.lines{font-size:14.5px;line-height:1.9}
.line{display:grid;grid-template-columns:44px minmax(0,1fr);gap:10px;border-radius:2px}
.line:target{background:#f3f0df}
.ln{color:#98a1a8;text-align:right;font:11.5px/2.4 ui-monospace,Menlo,Consolas,monospace;user-select:none}
.tx{white-space:pre-wrap;overflow-wrap:anywhere;min-width:0}
.tx.empty:after{content:" "}
mark.hit{border-radius:2px;padding:0 1px;color:inherit;background:#eceff1}
mark.g-A{background:#f1d7d3}mark.g-B{background:#f1e3c9}mark.g-C{background:#e4dbee}
mark.g-D{background:#dbe7f1}mark.g-E{background:#d5e9e4}mark.g-F{background:#eee6c8}
mark.g-G{background:#f1dbe5}mark.g-H{background:#e5d8ca}mark.g-I{background:#e7e9ea}
.badge{font:10px/1 ui-monospace,Menlo,Consolas,monospace;font-weight:650;margin-left:1px;padding:1px 2px;border-radius:2px;color:#fff;vertical-align:super}
.badge.g-A{background:#b5534b}.badge.g-B{background:#a06a2c}.badge.g-C{background:#73558f}
.badge.g-D{background:#3f6c93}.badge.g-E{background:#38786e}.badge.g-F{background:#8a7433}
.badge.g-G{background:#9a5570}.badge.g-H{background:#7a6248}.badge.g-I{background:#6c737a}
aside{min-width:0}
.side-block{background:#fff;border:1px solid #dce1e4;border-radius:5px;padding:14px 16px;margin-bottom:16px}
.side-block h3{margin:0 0 10px;font-size:14px}
.counts{display:grid;grid-template-columns:1fr auto;gap:3px 12px;font-size:13px;color:#46535c}
.counts .n{font-variant-numeric:tabular-nums;color:#20262c}
ol.findings{list-style:none;margin:0;padding:0;max-height:62vh;overflow:auto}
ol.findings li{padding:6px 0;border-top:1px solid #eef0f1;font-size:13px;line-height:1.6}
ol.findings li:first-child{border-top:0}
.code{font:11.5px/1.6 ui-monospace,Menlo,Consolas,monospace;color:#5b6771;margin-right:5px}
.advice{display:block;color:#5d6873;font-size:12.5px}
.hit-text{font-weight:600}
body.hide-A mark.g-A,body.hide-B mark.g-B,body.hide-C mark.g-C,body.hide-D mark.g-D,
body.hide-E mark.g-E,body.hide-F mark.g-F,body.hide-G mark.g-G,body.hide-H mark.g-H,
body.hide-I mark.g-I{background:transparent;box-shadow:none}
body.hide-A .badge.g-A,body.hide-B .badge.g-B,body.hide-C .badge.g-C,body.hide-D .badge.g-D,
body.hide-E .badge.g-E,body.hide-F .badge.g-F,body.hide-G .badge.g-G,body.hide-H .badge.g-H,
body.hide-I .badge.g-I{display:none}
body.hide-A li[data-group="A"],body.hide-B li[data-group="B"],body.hide-C li[data-group="C"],
body.hide-D li[data-group="D"],body.hide-E li[data-group="E"],body.hide-F li[data-group="F"],
body.hide-G li[data-group="G"],body.hide-H li[data-group="H"],body.hide-I li[data-group="I"]{display:none}
table{border-collapse:collapse;width:100%;font-size:13px;background:#fff}
th,td{border:1px solid #dce1e4;padding:6px 8px;text-align:left;vertical-align:top}
th{background:#eef1f3;font-weight:650}
.matrix{max-width:520px;margin:12px 0 6px}
.matrix td.num{font:650 18px/1.4 ui-monospace,Menlo,Consolas,monospace;text-align:center;font-variant-numeric:tabular-nums}
.matrix .tp{background:#e2eee4}.matrix .tn{background:#e2eee4}.matrix .fp{background:#f4dfdc}.matrix .fn{background:#f5ead2}
.metrics{display:flex;flex-wrap:wrap;gap:22px;margin:14px 0 4px}
.metric .label{font-size:12px;color:#5d6873}
.metric .value{font:650 21px/1.2 ui-monospace,Menlo,Consolas,monospace;font-variant-numeric:tabular-nums}
.sample{border:1px solid #dce1e4;border-radius:5px;background:#fff;margin:8px 0;overflow:hidden}
.sample>summary{cursor:pointer;padding:9px 13px;display:flex;flex-wrap:wrap;gap:8px 14px;align-items:baseline;font-size:13.5px;list-style:none}
.sample>summary::-webkit-details-marker{display:none}
.sample>summary:before{content:"+";font-family:ui-monospace,monospace;color:#78838c;width:12px}
.sample[open]>summary:before{content:"−"}
.sample .body{padding:10px 14px 16px;border-top:1px solid #eef0f1}
.status{font-size:11.5px;font-weight:650;padding:1px 6px;border-radius:3px;color:#fff}
.status.TP{background:#3f7350}.status.TN{background:#5d7b63}.status.FP{background:#a44a43}.status.FN{background:#9a7a35}
.status.WARN{background:#9a7a35}.status.PASS{background:#5d7b63}.status.FAIL{background:#a44a43}
.tagline{font-size:12.5px;color:#5d6873}
details.text>summary{cursor:pointer;color:#1d5c83;font-size:13px;margin:6px 0}
@media(max-width:1000px){main{padding:18px 14px}.layout{grid-template-columns:1fr}aside{order:2}}
"""

HTML_SCRIPT = """
document.querySelectorAll('.tab').forEach(function(tab){
  tab.addEventListener('click',function(){
    document.querySelectorAll('.tab').forEach(function(t){t.classList.remove('active')});
    document.querySelectorAll('.panel').forEach(function(p){p.classList.remove('active')});
    tab.classList.add('active');
    var panel=document.getElementById(tab.dataset.panel);
    if(panel){panel.classList.add('active')}
  });
});
document.querySelectorAll('.chip').forEach(function(chip){
  var group=chip.dataset.group;
  chip.setAttribute('aria-pressed','true');
  chip.addEventListener('click',function(){
    if(!group){
      document.body.classList.remove('hide-A','hide-B','hide-C','hide-D','hide-E','hide-F','hide-G','hide-H','hide-I');
      document.querySelectorAll('.chip').forEach(function(c){c.setAttribute('aria-pressed','true')});
      return;
    }
    var pressed=chip.getAttribute('aria-pressed')==='true';
    chip.setAttribute('aria-pressed',pressed?'false':'true');
    document.body.classList.toggle('hide-'+group,pressed);
  });
});
document.querySelectorAll('ol.findings a[href^="#"]').forEach(function(link){
  link.addEventListener('click',function(){
    var target=document.querySelector(link.getAttribute('href'));
    if(!target)return;
    target.style.background='#f3f0df';
    setTimeout(function(){target.style.background=''},1600);
  });
});
"""


def esc(text: str) -> str:
    return html.escape(text, quote=True)


def _line_render(
    line: str,
    findings: Sequence[Finding],
    file_key: str,
) -> str:
    if not findings:
        return esc(line) if line else ""
    local: list[tuple[int, int, Finding]] = []
    for finding in findings:
        start = max(0, finding.column)
        end = min(len(line), finding.end_column)
        if end <= start:
            continue
        local.append((start, end, finding))
    local.sort(key=lambda item: (item[0], -LEVEL_PRIORITY.get(item[2].level, 0), -(item[1] - item[0])))
    chosen: list[tuple[int, int, Finding]] = []
    cursor = 0
    for start, end, finding in local:
        if end <= cursor:
            continue
        start = max(start, cursor)
        chosen.append((start, end, finding))
        cursor = end
    out: list[str] = []
    position = 0
    for start, end, finding in chosen:
        out.append(esc(line[position:start]))
        title = esc(f"{finding.group_name} / {finding.code}：{finding.advice}")
        if finding.density:
            out.append(
                f'<mark class="hit g-{finding.group}" title="{title}">{esc(line[start:end])}</mark>'
            )
        else:
            out.append(
                f'<mark class="hit g-{finding.group}" title="{title}">{esc(line[start:end])}</mark>'
                f'<sup class="badge g-{finding.group}" title="{title}">{finding.code}</sup>'
            )
        position = end
    out.append(esc(line[position:]))
    return "".join(out)


def render_document(result: ScanResult, file_key: str) -> str:
    lines = result.text.split("\n")
    by_line: dict[int, list[Finding]] = {}
    for finding in result.findings:
        if 1 <= finding.line <= len(lines):
            by_line.setdefault(finding.line, []).append(finding)
    parts: list[str] = ['<div class="lines">']
    for number, line in enumerate(lines, start=1):
        content = _line_render(line, by_line.get(number, []), file_key)
        parts.append(
            f'<div class="line" id="{esc(file_key)}-L{number}">'
            f'<span class="ln">{number}</span>'
            f'<span class="tx">{content}</span></div>'
        )
    parts.append("</div>")
    return "".join(parts)


def render_findings(result: ScanResult, file_key: str) -> str:
    if not result.findings:
        return '<p class="muted small">没有命中。</p>'
    visible = [
        finding
        for finding in result.findings
        if not (
            finding.density
            and finding.group not in result.fail_groups
            and finding.group not in result.warn_groups
        )
    ]
    if not visible:
        return '<p class="muted small">单项未超标。</p>'
    parts = ['<ol class="findings">']
    for finding in result.findings:
        group = finding.group
        if finding.density and finding.group not in result.fail_groups and finding.group not in result.warn_groups:
            continue
        parts.append(
            f'<li data-group="{group}"><a href="#{esc(file_key)}-L{finding.line}">'
            f'<span class="code">{finding.code}</span>'
            f'<span class="hit-text">「{esc(finding.text)}」</span>'
            f'<span class="muted small"> 第 {finding.line} 行</span>'
            f'<span class="advice">{esc(finding.advice)}</span></a></li>'
        )
    parts.append("</ol>")
    return "".join(parts)


def render_group_counts(result: ScanResult) -> str:
    rows = []
    for group in GROUP_NAMES:
        if group == "I":
            value = f"{result.softening_density:.1f}/千字"
        else:
            value = str(result.group_counts[group])
        rows.append(
            f'<div><span><i class="dot" style="background:{GROUP_COLORS[group]}"></i>'
            f'{group} {esc(GROUP_NAMES[group])}</span><span class="n">{value}</span></div>'
        )
    rows.append(
        f'<div><span>D–G 组合</span><span class="n">{result.combined_density:.1f}/千字</span></div>'
    )
    return '<div class="counts">' + "".join(rows) + "</div>"


def render_scan_chips(result: ScanResult) -> str:
    chips = [
        '<button class="chip" data-group="" aria-pressed="true">全部</button>'
    ]
    for group in GROUP_NAMES:
        count = result.group_counts[group]
        if group == "I":
            count_text = f"{result.softening_density:.1f}/千字"
        else:
            count_text = str(count)
        chips.append(
            f'<button class="chip" data-group="{group}" aria-pressed="true">'
            f'<span class="dot" style="background:{GROUP_COLORS[group]}"></span>'
            f'{group} <b>{count_text}</b></button>'
        )
    return '<div class="chips">' + "".join(chips) + "</div>"


def render_file_panel(result: ScanResult, index: int) -> str:
    file_key = f"f{index}"
    classes = "panel active" if index == 0 else "panel"
    return (
        f'<section class="{classes}" id="{file_key}">'
        f'<div class="doc-head"><span>{esc(result.path)}</span>'
        f'<span class="metric">字数 <strong>{format_count(result.char_count)}</strong></span>'
        f'<span class="metric">AI 痕迹密度 <strong>{result.total_density:.1f}</strong>/千字</span>'
        f'<span class="metric">得分 <strong>{result.score}</strong>/100</span>'
        f'<span><span class="verdict {result.verdict}">{result.verdict}</span></span></div>'
        f"{render_scan_chips(result)}"
        f'<div class="layout"><div class="paper">{render_document(result, file_key)}</div>'
        f'<aside><div class="side-block"><h3>各组命中</h3>{render_group_counts(result)}</div>'
        f'<div class="side-block"><h3>问题清单</h3>{render_findings(result, file_key)}</div>'
        f"</aside></div></section>"
    )


def render_html_report(results: Sequence[ScanResult], title: str) -> str:
    total_chars = sum(result.char_count for result in results)
    total_findings = sum(len(result.findings) for result in results)
    density = total_findings / max(total_chars, 1) * 1000
    verdict = (
        "FAIL" if any(result.verdict == "FAIL" for result in results)
        else "WARN" if any(result.verdict == "WARN" for result in results)
        else "PASS"
    )
    tabs: list[str] = []
    panels: list[str] = []
    for index, result in enumerate(results):
        active = " active" if index == 0 else ""
        tabs.append(
            f'<button class="tab{active}" data-panel="f{index}">{esc(result.display_name)}</button>'
        )
        panels.append(render_file_panel(result, index))
    tabs_html = '<div class="tabs">' + "".join(tabs) + "</div>" if len(results) > 1 else ""
    body = (
        f"<main><h1>{esc(title)}</h1>"
        f'<div class="summary">'
        f'<span class="metric">文件 <strong>{len(results)}</strong></span>'
        f'<span class="metric">字数 <strong>{format_count(total_chars)}</strong></span>'
        f'<span class="metric">千字命中 <strong>{density:.1f}</strong></span>'
        f'<span><span class="verdict {verdict}">{verdict}</span></span>'
        f"</div>"
        f"{tabs_html}{''.join(panels)}</main>"
        f"<script>{HTML_SCRIPT}</script>"
    )
    return (
        '<!doctype html><html lang="zh-CN"><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width,initial-scale=1">'
        f"<title>{esc(title)}</title><style>{HTML_STYLE}</style>{body}</html>"
    )


# ---------------------------------------------------------------------------
# 基准评测
# ---------------------------------------------------------------------------


@dataclass
class BenchmarkSample:
    path: str
    display_name: str
    expected: str  # positive / negative
    result: ScanResult
    outcome: str

    def as_dict(self) -> dict:
        return {
            "file": self.path,
            "expected": self.expected,
            "predicted_positive": self.result.is_positive,
            "outcome": self.outcome,
            "verdict": self.result.verdict,
            "score": self.result.score,
            "char_count": self.result.char_count,
            "density_per_1000": self.result.total_density,
            "groups": {
                group: {
                    "count": (
                        sum(self.result.softening_counts.values())
                        if group == "I"
                        else self.result.group_counts[group]
                    ),
                    "density_per_1000": (
                        self.result.softening_density
                        if group == "I"
                        else self.result.group_density[group]
                    ),
                }
                for group in GROUP_NAMES
            },
            "combined_DEFG": {
                "count": self.result.combined_count,
                "density_per_1000": self.result.combined_density,
                "fail": self.result.combined_fail,
                "warn": self.result.combined_warn,
            },
            "softening": {
                "count": sum(self.result.softening_counts.values()),
                "density_per_1000": self.result.softening_density,
                "items": {
                    I_LABELS[code]: count
                    for code, count in self.result.softening_counts.items()
                },
            },
            "reasons": self.result.reasons,
            "findings": [finding.as_dict() for finding in self.result.findings],
        }


def _metric(numerator: float, denominator: float) -> float:
    if denominator == 0:
        return 0.0
    return round(numerator / denominator, 4)


def run_benchmark(
    pos_paths: Sequence[str],
    neg_paths: Sequence[str],
    *,
    raw: bool = False,
    extract_original: bool = True,
) -> tuple[list[BenchmarkSample], dict]:
    samples: list[BenchmarkSample] = []
    for expected, paths in (("positive", pos_paths), ("negative", neg_paths)):
        for path in collect_files(paths):
            text = read_text(path)
            result = scan_text(
                str(path),
                path.name,
                text,
                raw=raw,
                extract_original=extract_original,
            )
            if expected == "positive":
                outcome = "TP" if result.is_positive else "FN"
            else:
                outcome = "FP" if result.is_positive else "TN"
            samples.append(
                BenchmarkSample(
                    path=str(path),
                    display_name=path.name,
                    expected=expected,
                    result=result,
                    outcome=outcome,
                )
            )

    tp = sum(1 for sample in samples if sample.outcome == "TP")
    fp = sum(1 for sample in samples if sample.outcome == "FP")
    tn = sum(1 for sample in samples if sample.outcome == "TN")
    fn = sum(1 for sample in samples if sample.outcome == "FN")
    total = len(samples)
    metrics = {
        "tp": tp,
        "fp": fp,
        "tn": tn,
        "fn": fn,
        "total": total,
        "positives": tp + fn,
        "negatives": tn + fp,
        "accuracy": _metric(tp + tn, total),
        "precision": _metric(tp, tp + fp),
        "recall": _metric(tp, tp + fn),
        "specificity": _metric(tn, tn + fp),
        "f1": _metric(2 * tp, 2 * tp + fp + fn),
        "false_positive_rate": _metric(fp, fp + tn),
        "false_negative_rate": _metric(fn, fn + tp),
    }
    summary = {
        "mode": "benchmark",
        "samples": total,
        "positives": tp + fn,
        "negatives": tn + fp,
        "confusion": {"TP": tp, "FP": fp, "TN": tn, "FN": fn},
        "metrics": metrics,
        "thresholds": {
            group: dict(config) for group, config in DENSITY_THRESHOLDS.items()
        },
        "combined_DEFG_threshold": dict(COMBINED_THRESHOLD),
        "hard_rule": "A/B/C/H 组任一 alarm 命中判阳性",
        "density_rule": (
            "D/E/F/G 任一组或四组合计达到 min_hits，且千字密度达到 fail 阈值判阳性；"
            "达到 warn 只记 WARN，不计阳性"
        ),
        "generated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
    }
    return samples, summary


def format_benchmark_txt(samples: Sequence[BenchmarkSample], summary: dict) -> str:
    metrics = summary["metrics"]
    lines = [
        "基准评测",
        f"  样本 {metrics['total']} 个（阳性 {metrics['positives']}，阴性 {metrics['negatives']}）",
        "",
        "混淆矩阵",
        "              预测阳性   预测阴性",
        f"  实际阳性      TP {metrics['tp']:<3}    FN {metrics['fn']:<3}",
        f"  实际阴性      FP {metrics['fp']:<3}    TN {metrics['tn']:<3}",
        "",
        "指标",
        f"  准确率 Accuracy          {metrics['accuracy']:.2%}",
        f"  精确率 Precision         {metrics['precision']:.2%}",
        f"  召回率 Recall            {metrics['recall']:.2%}",
        f"  特异度 Specificity       {metrics['specificity']:.2%}",
        f"  F1                        {metrics['f1']:.2%}",
        "",
        "漏报 FN",
    ]
    fn_samples = [sample for sample in samples if sample.outcome == "FN"]
    if fn_samples:
        for sample in fn_samples:
            lines.append(
                f"  {sample.path}  判定 {sample.result.verdict}，{sample.result.char_count} 字，"
                f"{sample.result.total_density:.1f}/千字"
            )
    else:
        lines.append("  无")

    lines.append("")
    lines.append("误报 FP")
    fp_samples = [sample for sample in samples if sample.outcome == "FP"]
    if fp_samples:
        for sample in fp_samples:
            reasons = "；".join(sample.result.reasons) or "无"
            lines.append(f"  {sample.path}  原因：{reasons}")
    else:
        lines.append("  无")

    lines.append("")
    lines.append("全部样本")
    for sample in samples:
        lines.append(
            f"  [{sample.outcome}] {sample.result.verdict:<4} {sample.path}"
            f"  {sample.result.char_count} 字 {sample.result.total_density:.1f}/千字"
            f" 得分 {sample.result.score}"
        )
    return "\n".join(lines)


def build_benchmark_json(samples: Sequence[BenchmarkSample], summary: dict) -> dict:
    return {
        "tool": "scan_expression.py",
        "version": VERSION,
        "mode": "benchmark",
        "summary": summary,
        "samples": [sample.as_dict() for sample in samples],
    }


def render_benchmark_html(samples: Sequence[BenchmarkSample], summary: dict, title: str) -> str:
    metrics = summary["metrics"]

    def pct(value: float) -> str:
        return f"{value * 100:.1f}%"

    matrix = (
        '<table class="matrix"><thead><tr><th></th><th>预测阳性</th><th>预测阴性</th></tr></thead>'
        "<tbody>"
        f'<tr><th>实际阳性</th><td class="num tp">TP<br>{metrics["tp"]}</td>'
        f'<td class="num fn">FN<br>{metrics["fn"]}</td></tr>'
        f'<tr><th>实际阴性</th><td class="num fp">FP<br>{metrics["fp"]}</td>'
        f'<td class="num tn">TN<br>{metrics["tn"]}</td></tr>'
        "</tbody></table>"
    )
    metric_cards = "".join(
        f'<div class="metric"><div class="label">{label}</div>'
        f'<div class="value">{pct(value)}</div></div>'
        for label, value in (
            ("准确率 Accuracy", metrics["accuracy"]),
            ("精确率 Precision", metrics["precision"]),
            ("召回率 Recall", metrics["recall"]),
            ("特异度 Specificity", metrics["specificity"]),
            ("F1", metrics["f1"]),
        )
    )

    bucket_rows = []
    for bucket, title_text in (
        ("TP", "真阳性"),
        ("FN", "漏报"),
        ("FP", "误报"),
        ("TN", "真阴性"),
    ):
        bucket_samples = [sample for sample in samples if sample.outcome == bucket]
        items = []
        for sample in bucket_samples:
            label = f'<span class="mono">{esc(sample.display_name)}</span>'
            if bucket in ("FP", "FN"):
                reason = "；".join(sample.result.reasons) or "未达到任何阳性阈值"
                label += f'<span class="tagline">（{esc(reason)}）</span>'
            items.append(label)
        names = "、".join(items) or '<span class="muted">无</span>'
        bucket_rows.append(
            f"<tr><th>{bucket} {title_text}（{len(bucket_samples)}）</th><td>{names}</td></tr>"
        )

    details: list[str] = []
    for sample in samples:
        result = sample.result
        reasons = "；".join(result.reasons) if result.reasons else "无"
        top_groups = " ".join(
            f"{group} {result.group_counts[group]}"
            for group in GROUP_NAMES
            if result.group_counts[group]
        )
        details.append(
            f'<details class="sample"><summary>'
            f'<span class="status {sample.outcome}">{sample.outcome}</span>'
            f'<span class="mono">{esc(sample.path)}</span>'
            f'<span class="status {result.verdict}">{result.verdict}</span>'
            f'<span>{format_count(result.char_count)} 字</span>'
            f'<span>{result.total_density:.1f}/千字</span>'
            f'<span class="muted small">{top_groups}</span>'
            f"</summary>"
            f'<div class="body">'
            f'<p class="small"><b>预期</b> {sample.expected}　<b>判定</b> {result.verdict}'
            f'　<b>得分</b> {result.score}/100</p>'
            f'<p class="tagline">触发原因：{esc(reasons)}</p>'
            f'<details class="text"><summary>查看原文高亮与问题清单</summary>'
            f'<div class="layout"><div class="paper">{render_document(result, "s" + str(len(details)))}</div>'
            f'<aside><div class="side-block"><h3>各组命中</h3>{render_group_counts(result)}</div>'
            f'<div class="side-block"><h3>问题清单</h3>{render_findings(result, "s" + str(len(details)))}</div>'
            f"</aside></div></details></div></details>"
        )

    body = (
        f"<main><h1>{esc(title)}</h1>"
        f'<div class="summary">'
        f'<span class="metric">样本 <strong>{metrics["total"]}</strong></span>'
        f'<span class="metric">阳性 <strong>{metrics["positives"]}</strong></span>'
        f'<span class="metric">阴性 <strong>{metrics["negatives"]}</strong></span>'
        f"</div>"
        f"<h2>混淆矩阵</h2>{matrix}"
        f'<table><tbody>{"".join(bucket_rows)}</tbody></table>'
        f"<h2>核心指标</h2><div class=\"metrics\">{metric_cards}</div>"
        f"<h2>逐样本结果</h2>"
        f'<div class="samples">{"".join(details)}</div>'
        f"</main><script>{HTML_SCRIPT}</script>"
    )
    return (
        '<!doctype html><html lang="zh-CN"><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width,initial-scale=1">'
        f"<title>{esc(title)}</title><style>{HTML_STYLE}</style>{body}</html>"
    )


# ---------------------------------------------------------------------------
# 命令行
# ---------------------------------------------------------------------------


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="扫描中文文本里的 AI 表达痕迹，支持基准评测。",
    )
    parser.add_argument("paths", nargs="*", help="要扫描的文件或目录")
    parser.add_argument(
        "--format",
        choices=("txt", "json", "html"),
        default="txt",
        help="输出格式，默认 txt",
    )
    parser.add_argument("--output", help="写入文件；省略时打印到标准输出")
    parser.add_argument(
        "--raw",
        action="store_true",
        help="连中文引号内部一起扫描；默认豁免 25 字以内的引号短语",
    )
    parser.add_argument(
        "--extract-original",
        action="store_true",
        help="只扫描“# 原文”“## 原始片段”区块或 <!-- 原始文本开始 --> 标记内的内容",
    )
    parser.add_argument(
        "--whole-file",
        action="store_true",
        help="基准评测默认提取原文区块；加此项改为扫描整个文件",
    )
    parser.add_argument(
        "--benchmark",
        action="store_true",
        help="跑正负样本基准评测，输出 TP/FP/TN/FN",
    )
    parser.add_argument(
        "--pos",
        nargs="+",
        metavar="PATH",
        help="已知有 AI 味的样本文件或目录",
    )
    parser.add_argument(
        "--neg",
        nargs="+",
        metavar="PATH",
        help="已知纯人类手写或已验收的样本文件或目录",
    )
    parser.add_argument("--title", help="HTML 标题")
    parser.add_argument("--list-rules", action="store_true", help="列出规则和词条数")
    parser.add_argument("--verbose", action="store_true", help="txt 输出附加全部命中")
    return parser


def list_rules() -> str:
    lines = ["规则词库", ""]
    for group in GROUP_NAMES:
        rules = [rule for rule in RULES if rule.group == group]
        if group == "I":
            lines.append(f"{group} {GROUP_NAMES[group]}  {len(I_TERMS)} 个词/符号（仅密度）")
            for code, pattern in I_TERMS:
                lines.append(f"  {code}  {pattern}")
        else:
            lines.append(f"{group} {GROUP_NAMES[group]}  {len(rules)} 条")
            for rule in rules:
                lines.append(f"  {rule.code}  {rule.pattern}  [{rule.level}]")
        lines.append("")
    total = sum(len([rule for rule in RULES if rule.group == group]) for group in GROUP_NAMES)
    lines.append(f"合计规则 {total} 条，软化词/符号 {len(I_TERMS)} 个")
    return "\n".join(lines)


def write_output(content: str, output: str | None) -> None:
    if output:
        path = Path(output)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
        print(f"已写入 {path}")
    else:
        print(content)


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    if args.list_rules:
        print(list_rules())
        return 0

    if args.benchmark:
        if not args.pos or not args.neg:
            parser.error("--benchmark 需要同时给出 --pos 和 --neg")
        samples, summary = run_benchmark(
            args.pos,
            args.neg,
            raw=args.raw,
            extract_original=not args.whole_file,
        )
        if args.format == "json":
            content = json.dumps(
                build_benchmark_json(samples, summary), ensure_ascii=False, indent=2
            )
        elif args.format == "html":
            title = args.title or "AI 表达痕迹扫描基准评测"
            content = render_benchmark_html(samples, summary, title)
        else:
            content = format_benchmark_txt(samples, summary)
        write_output(content, args.output)
        return 0

    if not args.paths:
        parser.error("请给出要扫描的文件或目录，或使用 --benchmark")

    files = collect_files(args.paths)
    results = [
        scan_text(
            str(path),
            path.name,
            read_text(path),
            raw=args.raw,
            extract_original=args.extract_original,
        )
        for path in files
    ]
    if not results:
        print("没有找到可扫描的文本文件。")
        return 1

    if args.format == "json":
        content = json.dumps(build_scan_json(results), ensure_ascii=False, indent=2)
    elif args.format == "html":
        title = args.title or "AI 表达痕迹扫描报告"
        content = render_html_report(results, title)
    else:
        blocks = [format_scan_txt(result, verbose=args.verbose) for result in results]
        content = "\n\n".join(blocks) + format_summary_txt(results)

    write_output(content, args.output)
    return 0


if __name__ == "__main__":
    sys.exit(main())
