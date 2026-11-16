#!/usr/bin/env python3
"""校验绘本分镜成品 Markdown 是否遵守两套强制输出格式。

用法:
    python check_storyboard_md.py 成品.md
    python check_storyboard_md.py 成品.md --strict

检查内容:
分镜包（主产出）：每组 6 行齐、绘图提示词含模板全部固定句、槽位无残留、
  正文无与画风冲突的词、绘图提示词行末自带「负面：」串、
  视频提示词紧跟绘图提示词下一行且自带负面串、关键词四项齐、
  配音提示词为末行且朗读文本与英文原句逐字一致、参数在平台取值范围内；
  若出现独立的「绘图负面参考」汇总行会告警。
独立视频提示词段（兼容旧格式）：每镜 ①-⑤ 齐、粘贴版存在且收尾带负面串、
  末尾统一负面存在、字数在合理区间。

退出码: 0 = 通过（可能有警告）, 1 = 有错误, 2 = 文件读不到。
仅依赖标准库。
"""

from __future__ import annotations

import argparse
import os
import re
import sys

HEADER = re.compile(r"^【第\s*([0-9]+|X)\s*镜】")
CJK = re.compile(r"[\u4e00-\u9fff]")

# 模板原文与负面串的唯一来源：直接从技能自己的 reference 文件读取，
# 成品必须与它们逐字一致。改模板只改那两个文件，脚本不另存副本。
BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DRAW_DOC = os.path.join(BASE_DIR, "references", "drawing-prompt.md")
VIDEO_DOC = os.path.join(BASE_DIR, "references", "video-prompt.md")
VOICE_DOC = os.path.join(BASE_DIR, "references", "voice-prompt.md")

PLACEHOLDER_SPLIT = re.compile(r"(【(?:画面比例|画面主体|主体互动关系|环境场景)】)")
TEMPLATE_PLACEHOLDERS = ["【画面主体】", "【主体互动关系】", "【环境场景】", "【画面比例】"]
STYLE_CONFLICTS = ["3D渲染", "皮克斯", "赛璐璐", "写实照片", "光滑无笔触", "纯平涂卡通"]
CAMERA_WORDS = ["镜头", "景别", "特写", "推近", "推拉", "摇镜"]
TIME_WORDS = ["闪回", "来回切换"]
KEYWORDS = ["color", "size", "action verb", "place"]
NEG_DRAW = "绘图负面参考"
NEG_VIDEO = "负面：纯平涂"
VIDEO_DIMS = ["① 主体", "② 动作", "③ 效果", "④ 节奏", "⑤ 统一性"]


def quoted_line(path, needle):
    """取 reference 文件里以 '>' 开头、含 needle 的那一行（去掉 '>' 与首尾空白）。"""
    try:
        with open(path, "r", encoding="utf-8") as f:
            for line in f:
                s = line.strip()
                if s.startswith(">") and needle in s:
                    return s[1:].strip()
    except FileNotFoundError:
        return None
    return None


def load_sources():
    tpl = quoted_line(DRAW_DOC, "【画面比例】，儿童绘本插画风格")
    draw_neg = quoted_line(DRAW_DOC, "纯平涂，光滑无笔触，写实照片")
    video_neg = quoted_line(VIDEO_DOC, "负面：纯平涂")
    voice_neg = quoted_line(VOICE_DOC, "负面：急促赶读")
    return tpl, draw_neg, video_neg, voice_neg


def check_template_order(prompt_pos, template, errors, tag):
    """按模板切段，要求成品里这些固定段依次出现（槽位处允许填任意本镜内容）。"""
    idx = 0
    for part in PLACEHOLDER_SPLIT.split(template):
        if not part or part in TEMPLATE_PLACEHOLDERS:
            continue
        found = prompt_pos.find(part, idx)
        if found < 0:
            errors.append(f"{tag}: 绘图提示词与模板原文不一致（缺片段或顺序错乱）：{part[:20]}…")
            return
        idx = found + len(part)


def split_blocks(lines):
    blocks, cur = [], None
    for raw in lines:
        line = raw.rstrip()
        if line.startswith("#") or line.startswith(NEG_DRAW) or line.startswith(NEG_VIDEO):
            if cur:
                blocks.append(cur)
                cur = None
            continue
        match = HEADER.match(line)
        if match:
            if cur:
                blocks.append(cur)
            cur = {"head": line, "body": []}
        elif cur:
            cur["body"].append(line)
    if cur:
        blocks.append(cur)
    return blocks


def is_video_block(block):
    head = block["head"]
    body = [b for b in block["body"] if b.strip()]
    if "English:" in head or "English：" in head:
        return True
    return any(b.strip().startswith("①") for b in body)


def slot_contents(prompt_pos, template):
    """按模板固定段定位，切出四个槽位里实际填了什么。"""
    body = re.sub(r"^→ 绘图提示词：[：:]?\s*", "", prompt_pos)
    parts = PLACEHOLDER_SPLIT.split(template)
    texts = [p for i, p in enumerate(parts) if i % 2 == 0 and p]
    found, start = [], 0
    for t in texts:
        i = body.find(t, start)
        if i < 0:
            return None
        found.append((i, i + len(t)))
        start = i + len(t)
    slots, cur = [], 0
    for a, b in found:
        slots.append(body[cur:a])
        cur = b
    slots.append(body[cur:])
    return slots


def check_slot_depth(prompt_pos, template, errors, tag):
    """槽位是装画面的容器。只塞一个词、塞空白，等于没写画面。"""
    slots = slot_contents(prompt_pos, template)
    if not slots:
        return
    names = ["画面比例", "画面主体", "主体互动关系", "环境场景"]
    needs = [0, 6, 4, 4]
    for name, need, val in zip(names, needs, slots[:4]):
        clean = re.sub(r"[，。、；：\s]", "", val)
        if len(clean) < need:
            errors.append(
                f"{tag}: 「{name}」槽位只填了 {len(clean)} 字（「{val.strip()[:12]}」），"
                f"像把词塞进格子；先想清画面再写，不要机械填空"
            )


QUANTIFIER = re.compile(r"^[一两三几]?(颗|只|个|朵|片|条|群|位|座|块|根|张|把|粒|滴|束)")
CLAUSE_CUT = re.compile(r"(想|要|去|坐|跳|飞|找|等|回到|把|被|却|但|就|才|总|已经|正在|在|和|与|跟|同)")
LOCATIVE_PREFIX = re.compile(r"^(到|向|往|朝|在)")
LOCATIVE_SUFFIX = re.compile(r"(上|里|中|下|前|后|旁|边|顶|底)$")


def normalize_subject(part):
    """把一句话里的主体还原为可检索的短词：去掉数量词，在动词或虚词处截断。"""
    text = part.strip()
    text = QUANTIFIER.sub("", text)
    match = CLAUSE_CUT.search(text)
    if match and match.start() >= 2:
        text = text[: match.start()]
    return text.strip()


def subjects_from_part(part):
    """从一个短语或一句情节里取出主体：首段取名词，动词后带方位标记的段取处所对象。"""
    words = []
    segments = re.split(CLAUSE_CUT, part.strip())
    for index, segment in enumerate(segments):
        segment = segment.strip()
        if not segment:
            continue
        if index == 0:
            words.append(QUANTIFIER.sub("", segment))
        elif segments[index - 1] and LOCATIVE_PREFIX.match(segment):
            word = LOCATIVE_SUFFIX.sub("", LOCATIVE_PREFIX.sub("", segment))
            words.append(word)
    return [w.strip() for w in words if 2 <= len(w.strip()) <= 6]


def theme_subjects(theme):
    """从主题里取出必须出现的主体清单。

    兼容两种主题：短语式（「月亮与小星星」）与含情节的一句话
    （「一颗小星星想坐到月牙上，但总差最后一段」）。
    """
    subjects = []
    for part in re.split(r"[、，,；;与和及跟同×xX/／]+", theme):
        subjects.extend(subjects_from_part(part))
    return subjects


def check_theme(blocks, text, theme, errors, warnings):
    subjects = theme_subjects(theme)
    if not subjects:
        warnings.append(f"主题「{theme}」未能解析出主体，跳过主题覆盖检查")
        return
    for subject in subjects:
        if subject not in text:
            errors.append(f"主题主体「{subject}」未出现在成品任何一行：主题约束优先于创意方向")
            continue
        hits = sum(1 for b in blocks if subject in "\n".join(b["body"]) or subject in b["head"])
        if len(blocks) >= 4 and hits == 1:
            warnings.append(f"主题主体「{subject}」仅在 1 组中出现，其余组未提及，易读成偏题")


def check_voice_consistency(blocks, warnings):
    """六组须同一音色、同一音高与音量，仅情绪与重音随句变化。"""
    speakers, pitches, loudness = [], [], []
    for block in blocks:
        lines = [b.strip() for b in block["body"] if b.strip()]
        voice = next((l for l in lines if l.startswith("→ 配音提示词")), None)
        if not voice:
            continue
        match = re.search(r"speaker\s*([^；]+)", voice)
        if match:
            speakers.append(match.group(1).strip())
        for name, bucket in (("pitch_rate", pitches), ("loudness_rate", loudness)):
            match = re.search(rf"{name}\s*(-?\d+)", voice)
            if match:
                bucket.append(int(match.group(1)))
    for label, values in (("speaker", speakers), ("pitch_rate", pitches), ("loudness_rate", loudness)):
        if len(set(values)) > 1:
            warnings.append(f"配音「{label}」在六组之间不一致：{sorted(set(values))}；应全系列锁定")


def check_drawing(block, errors, warnings, tag, template, draw_neg, video_neg, voice_map=None, voice_neg=None):
    body = [b for b in block["body"] if b.strip()]
    if len(body) != 6:
        errors.append(f"{tag}: 应为 6 行（英文/中文/绘图提示词/视频提示词/关键词/配音提示词），实际 {len(body)} 行")
        return

    en, cn, prompt, video, keys, voice = body
    if en.startswith("→") or CJK.search(en):
        errors.append(f"{tag}: 第 1 行必须是英文短句（无箭头、无中文）")
    if not en.strip().endswith((".", "!", "?")):
        warnings.append(f"{tag}: 英文短句没有以句号/感叹号/问号结尾")

    if not cn.startswith("→"):
        errors.append(f"{tag}: 第 2 行必须以「→」开头")
    if not CJK.search(cn):
        errors.append(f"{tag}: 第 2 行必须是中文翻译")

    if not prompt.startswith("→ 绘图提示词"):
        errors.append(f"{tag}: 第 3 行必须以「→ 绘图提示词：」开头")

    pos, sep, neg = prompt.partition("负面：")
    if not sep:
        errors.append(f"{tag}: 绘图提示词行末必须接「负面：」+ 绘图负面串（写在同一行，不另起一行）")
    elif draw_neg and draw_neg not in neg:
        errors.append(f"{tag}: 绘图负面串与 references/drawing-prompt.md 的原文不一致")

    if template:
        check_template_order(pos, template, errors, tag)
        check_slot_depth(pos, template, errors, tag)
    else:
        warnings.append("未能从 references/drawing-prompt.md 读到模板原文，跳过逐字比对与槽位检查")

    for placeholder in TEMPLATE_PLACEHOLDERS:
        if placeholder in pos:
            errors.append(f"{tag}: 绘图提示词里的槽位未填充「{placeholder}」")
    if not re.match(r"^→ 绘图提示词：[：:]?\s*【(9:16|16:9|1:1|4:3|3:4)】", pos):
        errors.append(f"{tag}: 绘图提示词开头应紧跟「【画面比例】，」")
    if sep:
        for word in STYLE_CONFLICTS:
            if word in pos:
                errors.append(f"{tag}: 绘图提示词正文含与蜡笔彩铅画风冲突的词「{word}」")
        for word in CAMERA_WORDS:
            if word in pos:
                warnings.append(f"{tag}: 绘图提示词里出现镜头语言「{word}」——镜头运动写在视频行，绘图只描述静止画面")

    if not video.startswith("→ 视频提示词"):
        errors.append(f"{tag}: 第 4 行必须是「→ 视频提示词：」，紧跟绘图提示词之后")
    else:
        if video_neg and video_neg not in video:
            errors.append(f"{tag}: 视频提示词行末尾的负面串与 references/video-prompt.md 的原文不一致")
        elif not video_neg and "负面：" not in video:
            errors.append(f"{tag}: 视频提示词行末尾必须带「负面：」固定串")
        length = len(video.split("负面：")[0])
        if length > 320:
            warnings.append(f"{tag}: 视频提示词正文 {length} 字，超过 260 字建议值")
        if "②" in video or "①" in video:
            errors.append(f"{tag}: 视频提示词行不能带维度标签（①②③），应压成一段")
        if "首帧" not in video:
            warnings.append(f"{tag}: 视频提示词行建议以「以首帧为准」起句")
        for word in TIME_WORDS:
            if word in video:
                errors.append(f"{tag}: 视频提示词不得写「{word}」，时间只往前走")

    if not keys.startswith("→ 关键词"):
        errors.append(f"{tag}: 第 5 行必须以「→ 关键词」开头")
    for item in KEYWORDS:
        if item not in keys.lower():
            errors.append(f"{tag}: 关键词缺「{item}」")
    if "【" not in keys or "】" not in keys:
        warnings.append(f"{tag}: 关键词建议保留【】包裹")

    check_voice(voice, en, voice_neg, errors, warnings, tag)
    check_duration(en, video, errors, warnings, tag, (voice_map or {}).get(shot_index(tag)))


def check_voice(voice, en, voice_neg, errors, warnings, tag):
    """第 6 行配音提示词：朗读文本须与英文原句逐字一致，参数须在平台取值范围内。"""
    if not voice.startswith("→ 配音提示词"):
        errors.append(f"{tag}: 第 6 行必须以「→ 配音提示词：」开头，位于关键词行之后")
        return

    quote = re.search(r'朗读文本\s*"([^"]+)"', voice)
    if not quote:
        errors.append(f"{tag}: 配音提示词缺「朗读文本 \"<英文原句>\"」")
    elif quote.group(1).strip() != en.strip():
        errors.append(f"{tag}: 朗读文本与第 1 行英文短句不一致，不得改写原句")

    if voice_neg and voice_neg not in voice:
        errors.append(f"{tag}: 配音负面串与 references/voice-prompt.md 的原文不一致")
    elif not voice_neg and "负面：" not in voice:
        errors.append(f"{tag}: 配音提示词行末必须带「负面：」固定串")

    for field, low, high in (
        ("speech_rate", -50, 100),
        ("pitch_rate", -12, 12),
        ("loudness_rate", -50, 100),
    ):
        match = re.search(rf"{field}\s*(-?\d+)", voice)
        if not match:
            errors.append(f"{tag}: 配音提示词缺参数「{field}」，取值 {low}~{high}")
            continue
        value = int(match.group(1))
        if not low <= value <= high:
            errors.append(f"{tag}: 「{field}」取值 {value} 超出 {low}~{high}")

    silence = re.search(r"句末静音\s*(\d+)\s*(?:ms|毫秒)", voice)
    if not silence:
        errors.append(f"{tag}: 配音提示词缺「句末静音 <毫秒>」")
    else:
        ms = int(silence.group(1))
        if ms < 250:
            errors.append(f"{tag}: 句末静音 {ms}ms 过短，会切断句尾音（下限 250ms）")
        elif ms > 500:
            warnings.append(f"{tag}: 句末静音 {ms}ms 偏长，可能挤压朗读预算（建议 250-500ms）")

    if "情绪" not in voice:
        errors.append(f"{tag}: 配音提示词缺「情绪 <一词>」")
    if "指令" not in voice:
        errors.append(f"{tag}: 配音提示词缺朗读指令")


SPEAK_PER_WORD = 0.4      # 预检用：儿童向朗读每秒约 2.5 个英文词
SPEAK_PAUSE = 0.5         # 句末停顿
TAIL_ROOM = 0.7           # 朗读结束后留给收势与定格的最小余量
MAX_WORDS = 8             # 5 秒容器内的词数上限，保证配音有压缩空间
SECONDS_RE = re.compile(r"(\d+(?:\.\d+)?)\s*秒")
SHOT_RE = re.compile(r"第\s*(\d+)\s*镜")


def shot_index(tag):
    match = SHOT_RE.search(tag)
    return int(match.group(1)) if match else 0


def parse_voice_seconds(text):
    """解析「1:3.8,2:4.2」形式的实测朗读时长。"""
    table = {}
    for item in text.split(","):
        item = item.strip()
        if not item:
            continue
        key, _, value = item.partition(":")
        try:
            table[int(key.strip())] = float(value.strip())
        except ValueError:
            continue
    return table


def check_duration(en, video, errors, warnings, tag, voice_seconds=None):
    """校验朗读时长能否被视频时长连同收势余量覆盖。配音迁就视频，不延长视频。"""
    words = len(re.findall(r"[A-Za-z][A-Za-z'-]*", en))
    if words == 0:
        return
    if words > MAX_WORDS:
        errors.append(
            f"{tag}: 英文短句 {words} 词，超出 4-8 词上限，5 秒容器内配音没有压缩空间"
        )
    elif words < 4:
        warnings.append(f"{tag}: 英文短句仅 {words} 词，低于 4 词下限")

    match = SECONDS_RE.search(video)
    if not match:
        errors.append(f"{tag}: 视频提示词未写时长，无法判断画面能否覆盖朗读")
        return
    seconds = float(match.group(1))
    budget = seconds - TAIL_ROOM
    voice = (
        voice_seconds
        if voice_seconds is not None
        else words * SPEAK_PER_WORD + SPEAK_PAUSE
    )
    label = "实测朗读" if voice_seconds is not None else "折算朗读"
    if voice > budget:
        errors.append(
            f"{tag}: {label}需 {voice:.1f} 秒，视频 {seconds:g} 秒扣除收势 {TAIL_ROOM:g} 秒后预算仅 "
            f"{budget:.1f} 秒；先改短句子，其次与配音侧调语速，不延长视频"
        )
    elif seconds > 8:
        warnings.append(f"{tag}: 视频 {seconds:g} 秒，超过原文「5 秒，生长或累积类可 8 秒」的取值范围")


def check_video(block, errors, warnings, tag):
    body = [b for b in block["body"] if b.strip()]
    head = block["head"]
    if "English:" not in head and "English：" not in head:
        errors.append(f"{tag}: 段落首行应为「【第 X 镜】English: <英文原句>」")

    for dim in VIDEO_DIMS:
        if not any(b.strip().startswith(dim) for b in body):
            errors.append(f"{tag}: 缺维度「{dim}」")

    paste = [b for b in body if b.strip().startswith("粘贴版")]
    if not paste:
        errors.append(f"{tag}: 缺「粘贴版｜」")
    else:
        text = paste[0]
        if "负面：" not in text:
            errors.append(f"{tag}: 粘贴版末尾必须接「负面」固定串")
        length = len(text)
        if length > 320:
            warnings.append(f"{tag}: 粘贴版 {length} 字，超过 260 字上限较多")
        if not paste[0].startswith("粘贴版｜"):
            warnings.append(f"{tag}: 粘贴版行建议写成「粘贴版｜」")

    dim_text = "".join(b for b in body if b.strip().startswith(tuple(d.split(" ")[0] for d in VIDEO_DIMS)))
    dim_len = len(re.sub(r"\s", "", dim_text))
    if dim_len < 100:
        warnings.append(f"{tag}: 五维合计 {dim_len} 字，偏短（目标 120-200 字）")
    elif dim_len > 400:
        warnings.append(f"{tag}: 五维合计 {dim_len} 字，偏长，可能在复述画面（目标 120-200 字，硬上限 260 字以内更稳）")


def main():
    ap = argparse.ArgumentParser(description="校验绘本分镜成品 Markdown 格式")
    ap.add_argument("md_path")
    ap.add_argument("--strict", action="store_true", help="把警告也当错误")
    ap.add_argument("--theme", default=None, help="绘本主题；提供后校验主题主体是否出现在成品中")
    ap.add_argument("--voice-seconds", default=None, help="实测朗读时长，形如 1:3.8,2:4.2；提供后用它替代折算值")
    args = ap.parse_args()

    try:
        with open(args.md_path, "r", encoding="utf-8") as f:
            text = f.read()
    except FileNotFoundError:
        print(f"找不到文件: {args.md_path}")
        return 2
    except UnicodeDecodeError as exc:
        print(f"文件不是 UTF-8: {exc}")
        return 2

    blocks = split_blocks(text.splitlines())
    if not blocks:
        print("没有找到任何「【第 X 镜】」段落")
        return 1

    template, draw_neg, video_neg, voice_neg = load_sources()
    voice_map = parse_voice_seconds(args.voice_seconds) if args.voice_seconds else {}

    errors, warnings = [], []
    drawing_count = video_count = 0
    for index, block in enumerate(blocks, start=1):
        tag = header_tag = block["head"].split("English")[0].strip()
        if not tag:
            tag = f"#{index}"
        if is_video_block(block):
            video_count += 1
            check_video(block, errors, warnings, tag)
        else:
            drawing_count += 1
            check_drawing(block, errors, warnings, tag, template, draw_neg, video_neg, voice_map, voice_neg)

    if "绘图负面参考" in text:
        warnings.append("检测到独立的「绘图负面参考」汇总行：负面串应写在每条绘图提示词行末，不要另起汇总")
    check_voice_consistency(blocks, warnings)
    if args.theme:
        check_theme(blocks, text, args.theme, errors, warnings)
    if video_count:
        last_lines = [l.strip() for l in text.strip().splitlines() if l.strip()][-2:]
        if not any(l.startswith(NEG_VIDEO) for l in last_lines):
            warnings.append("模式二末尾建议单独附一次统一负面串（独立一行）")

    for msg in errors:
        print(f"ERROR  {msg}")
    for msg in warnings:
        print(f"WARN   {msg}")

    fail = bool(errors) or (args.strict and bool(warnings))
    print(f"\n共 {len(blocks)} 段（图文包 {drawing_count} 段 / 视频提示 {video_count} 段）："
          f"{len(errors)} 个错误，{len(warnings)} 个警告 → {'不通过' if fail else '通过'}")
    return 1 if fail else 0


if __name__ == "__main__":
    sys.exit(main())
