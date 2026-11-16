#!/usr/bin/env python3
"""为每一套绘本分镜抽取一组创作方向，并避开最近使用过的组合。

用法:
    python pick_direction.py                    # 抽一套新方向（会记入历史，并列出近期禁止复用的情景/动作/节拍）
    python pick_direction.py --seed 20261115    # 复现某一套方向
    python pick_direction.py --dry-run          # 只打印，不写历史
    python pick_direction.py --log "主题|情景|动作|节拍"
                                                # 成品完成后，把本套三要素写入刚才抽到的那条记录

输出为五行「维度｜取值」、一行指纹，以及「近期已用」清单。脚本只保证方向组合不同，
情景/动作/节拍是否重复由写作者对照清单判定。
"""

from __future__ import annotations

import argparse
import json
import os
import random
import sys

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
HISTORY_PATH = os.path.join(BASE_DIR, "assets", "direction-history.json")
KEEP_RECENT = 8

DIMENSIONS = {
    "叙事视角": [
        "主角自述",
        "旁观者",
        "被帮助者",
        "物件视角",
        "群体视角",
        "归来者视角",
        "误入者视角",
    ],
    "意象承载": [
        "主体本身即为意象",
        "主体的一部分承担意象",
        "主体的影子或痕迹承担意象",
        "主体带来的光或天气承担意象",
        "与主体交换位置的对象承担意象",
    ],
    "情绪基调": [
        "温暖",
        "雀跃",
        "安静",
        "悬念",
        "俏皮",
        "克制",
        "期待",
    ],
    "结构变体": [
        "线性推进",
        "重复累积",
        "误会解开",
        "交换与归还",
        "追逐与躲藏",
        "一夜之间",
        "由远及近",
    ],
    "转折类型": [
        "认知反转",
        "身份反转",
        "方向反转",
        "大小反转",
        "时间反转",
        "因果反转",
    ],
}


EMPTY_RECORD = {"fp": "", "theme": "", "scene": "", "action": "", "beat": ""}


def load_history():
    try:
        with open(HISTORY_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return []
    if not isinstance(data, list):
        return []
    records = []
    for item in data:
        if isinstance(item, str):
            records.append(dict(EMPTY_RECORD, fp=item))
        elif isinstance(item, dict) and item.get("fp"):
            records.append(dict(EMPTY_RECORD, **{k: str(item.get(k, "") or "") for k in EMPTY_RECORD}))
    return records


def save_history(records):
    os.makedirs(os.path.dirname(HISTORY_PATH), exist_ok=True)
    with open(HISTORY_PATH, "w", encoding="utf-8") as f:
        json.dump(records[-KEEP_RECENT:], f, ensure_ascii=False, indent=2)


def last_direction(recent):
    if not recent:
        return {}
    return dict(item.split("=", 1) for item in recent[-1]["fp"].split("|") if "=" in item)


def print_recent(recent):
    rows = [r for r in recent if r["scene"] or r["action"] or r["beat"]]
    if not rows:
        print("近期已用｜空（历史未记录情景/动作/节拍，本次无禁止项）")
        return
    print("近期已用（以下情景、动作、节拍禁止复用，换皮同样算复用）：")
    for i, r in enumerate(rows, start=1):
        theme = r["theme"] or "未记录主题"
        print(f"{i}. 主题｜{theme}　情景｜{r['scene'] or '—'}　动作｜{r['action'] or '—'}　节拍｜{r['beat'] or '—'}")


def draw(rng, avoid):
    """每一维都尽量避开上一次的取值，保证相邻两套方向逐维不同。"""
    out = {}
    for name, options in DIMENSIONS.items():
        pool = [o for o in options if o != avoid.get(name)]
        out[name] = rng.choice(pool or options)
    return out


def fingerprint(direction):
    return "|".join(f"{k}={direction[k]}" for k in DIMENSIONS)


def main(argv=None):
    parser = argparse.ArgumentParser(description="抽取绘本分镜创作方向")
    parser.add_argument("--seed", type=int, default=None, help="固定随机种子以复现同一套方向")
    parser.add_argument("--dry-run", action="store_true", help="只打印，不写入历史")
    parser.add_argument(
        "--log",
        default=None,
        metavar="主题|情景|动作|节拍",
        help="成品完成后记录本套三要素，供下一套规避；四项以竖线分隔",
    )
    args = parser.parse_args(argv)

    rng = random.Random(args.seed)
    recent = load_history()
    avoid = last_direction(recent)
    direction = draw(rng, avoid)
    fp = fingerprint(direction)

    for _ in range(30):
        if fp not in [r["fp"] for r in recent]:
            break
        direction = draw(rng, avoid)
        fp = fingerprint(direction)
    else:
        print("警告：三十次抽取均与最近记录重复，已放宽限制，请人工调整其中一维。", file=sys.stderr)

    print("主题主体由用户主题决定，不得替换；以下仅决定呈现方式：")
    for name in DIMENSIONS:
        print(f"{name}｜{direction[name]}")
    print(f"指纹｜{fp}")
    print_recent(recent)

    record = dict(EMPTY_RECORD, fp=fp)
    if args.log:
        fields = [x.strip() for x in args.log.split("|")]
        fields += [""] * (4 - len(fields))
        record.update(
            theme=fields[0], scene=fields[1], action=fields[2], beat=fields[3]
        )

    if not args.dry_run:
        save_history(recent + [record])

    return 0


if __name__ == "__main__":
    sys.exit(main())
