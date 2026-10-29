"""Glyph-variant normalization for *comparison only*.

Two OCR engines reading the same scanned line can disagree without disagreeing
about the text: one emits 爲 and the other 為, one 鴉 and the other 鸦, one the
full-width form and the other the half-width. Treating those as substantive
disagreement wastes human review on pages that are already correct, and — worse —
teaches reviewers to approve risky substitutions.

The rule this module enforces:

    **Normalization never rewrites accepted text.**

Export, decisions and evidence keep exactly what the engines produced and what a
human accepted. Normalization exists so that *comparison* (alignment similarity,
diff classification, duplicate detection) is not confused by orthographic noise,
and so that reviewers can be told "these two differ only by glyph variant".

Coverage is deliberately explicit rather than clever: a curated table of the
variant groups that actually appear in Chinese book scans, applied after Unicode
NFKC folding. It is not OpenCC. A partial table that silently misses a group is
safe here (comparison simply stays conservative); a table that guesses wrong would
merge distinct characters, so every group is checked by hand and none is derived
by rule.
"""

from __future__ import annotations

import unicodedata
from collections.abc import Iterable, Mapping
from types import MappingProxyType

__all__ = [
    "VARIANT_GROUPS",
    "fold_chars",
    "fold_for_comparison",
    "is_variant_only_difference",
    "similarity_for_comparison",
    "variant_groups_in",
]

# Each group lists characters that are orthographically interchangeable for the
# purposes of comparison. The first character is the canonical form; the rest fold
# onto it. Groups mix 繁体/简体 pairs and 异体字 because a scan of an old Chinese
# book routinely mixes them within one page.
_VARIANT_GROUP_DATA: tuple[str, ...] = (
    "为爲為",
    "这這",
    "说說説",
    "书書",
    "门門",
    "时時",
    "国國",
    "学學",
    "会會",
    "个個箇",
    "来來",
    "后後",
    "里裡裏",
    "发發髮",
    "面麵",
    "台臺檯",
    "只隻祇",
    "钟鐘鍾",
    "历歷曆",
    "复復複",
    "干乾幹",
    "于於",
    "与與",
    "万萬",
    "无無",
    "尔爾",
    "义義",
    "乐樂",
    "买買",
    "卖賣",
    "东東",
    "马馬",
    "鸟鳥",
    "鱼魚",
    "龙龍",
    "龟龜",
    "体體",
    "边邊",
    "过過",
    "还還",
    "进進",
    "远遠",
    "连連",
    "问問",
    "间間",
    "开開",
    "关關",
    "应應",
    "实實",
    "现現",
    "见見",
    "话話",
    "语語",
    "读讀",
    "写寫",
    "听聽",
    "记記",
    "认認",
    "议議",
    "论論",
    "证證",
    "试試",
    "诗詩",
    "谁誰",
    "请請",
    "谢謝",
    "变變",
    "处處",
    "条條",
    "务務",
    "动動",
    "势勢",
    "劳勞",
    "区區",
    "医醫",
    "单單",
    "卫衛",
    "厂廠",
    "厅廳",
    "压壓",
    "县縣",
    "参參",
    "双雙",
    "号號",
    "员員",
    "吗嗎",
    "响響",
    "严嚴",
    "园園",
    "图圖",
    "执執",
    "报報",
    "担擔",
    "拥擁",
    "择擇",
    "换換",
    "据據",
    "数數",
    "断斷",
    "旧舊",
    "显顯",
    "术術",
    "机機",
    "权權",
    "标標",
    "样樣",
    "树樹",
    "桥橋",
    "梦夢",
    "毕畢",
    "气氣",
    "汉漢",
    "汤湯",
    "没沒",
    "泽澤",
    "测測",
    "济濟",
    "浑渾",
    "浓濃",
    "润潤",
    "灭滅",
    "灯燈",
    "点點",
    "烈烈",
    "烟煙",
    "爱愛",
    "状狀",
    "独獨",
    "环環",
    "电電",
    "画畫",
    "畅暢",
    "盖蓋",
    "尽盡儘",
    "监監",
    "盘盤",
    "着著",
    "确確",
    "礼禮",
    "祸禍",
    "离離",
    "种種",
    "称稱",
    "积積",
    "稳穩",
    "穷窮",
    "窗窗",
    "笔筆",
    "筛篩",
    "简簡",
    "类類",
    "粮糧",
    "紧緊",
    "绪緒",
    "线線",
    "练練",
    "组組",
    "细細",
    "终終",
    "经經",
    "结結",
    "绝絕",
    "统統",
    "继繼",
    "续續",
    "维維",
    "缘緣",
    "编編",
    "网網",
    "罗羅",
    "习習",
    "翻翻",
    "联聯",
    "职職",
    "肃肅",
    "胜勝",
    "脑腦",
    "脸臉",
    "临臨",
    "举舉",
    "节節",
    "艺藝",
    "苏蘇甦",
    "荐薦",
    "荣榮",
    "药藥",
    "获獲穫",
    "虽雖",
    "蛮蠻",
    "补補",
    "装裝",
    "观觀",
    "规規",
    "视視",
    "觉覺",
    "览覽",
    "触觸",
    "计計",
    "订訂",
    "训訓",
    "讲講",
    "许許",
    "识識",
    "诉訴",
    "词詞",
    "译譯",
    "该該",
    "详詳",
    "误誤",
    "课課",
    "调調",
    "谈談",
    "谋謀",
    "谓謂",
    "费費",
    "资資",
    "赛賽",
    "赞贊讚",
    "赶趕",
    "车車",
    "轮輪",
    "轻輕",
    "载載",
    "输輸",
    "转轉",
    "辞辭",
    "达達",
    "迁遷",
    "运運",
    "违違",
    "遗遺",
    "邮郵",
    "邻鄰",
    "郑鄭",
    "释釋",
    "针針",
    "钦欽",
    "铁鐵",
    "银銀",
    "错錯",
    "长長",
    "闪閃",
    "闭閉",
    "阅閱",
    "阴陰",
    "阳陽",
    "队隊",
    "险險",
    "随隨",
    "难難",
    "雾霧",
    "静靜",
    "页頁",
    "顺順",
    "须須",
    "题題",
    "额額",
    "风風",
    "飞飛",
    "饭飯",
    "饮飲",
    "饰飾",
    "养養",
    "驻駐",
    "验驗",
    "骑騎",
    "骨骨",
    "魂魂",
    "魔魔",
    "黄黃",
    "群羣",
    "峰峯",
    "岛島",
    "床牀",
    "教敎",
    "既旣",
    "迹跡蹟",
    "秘祕",
    "略畧",
    "叹歎嘆",
    "挂掛罣",
    "异異",
    "并並併",
    "众眾",
    "污汚汙",
    "法法",
    "泛氾汎",
    "游遊",
    "溪谿",
    "考攷",
    "胸胷",
    "肯肎",
    "花蘤",
    "蛇虵",
    "村邨",
    "灾災菑",
    "真眞",
    "碎碎",
    "神神",
    "窗窓牕",
    "笑咲",
    "第第",
    "脏臟髒",
    "舍捨",
    "艳艷豔",
    "艹艸",
    "虚虛",
    "蜜蜜",
    "要要",
    "踏踏",
    "身身",
    "造造",
    "遇遇",
    "道道",
    "醒醒",
    "金金",
    "镜鏡",
    "闻聞",
    "陆陸",
    "院院",
    "雅雅",
    "雕彫琱",
    "雨雨",
    "韵韻",
    "顶頂",
    "项項",
    "顿頓",
    "飘飘",
    "高髙",
    "鸦鴉鵶",
    "鸭鴨",
    "鹅鵝鵞",
    "鹤鶴",
    "鹿鹿",
    "么麼麽",
    "什甚",
    "们們",
    "儿兒",
    "声聲",
    "产產",
    "业業",
    "农農",
    "别別",
    "对對",
    "乡鄉",
    "乱亂",
    "争爭",
    "亏虧",
    "亚亞",
    "仅僅",
    "从從",
    "仓倉",
    "仪儀",
    "价價",
    "优優",
    "余餘",
    "佛佛",
    "作作",
    "侧側",
    "俭儉",
    "债債",
    "倾傾",
    "像像",
    "党黨",
    "内內",
    "军軍",
    "冲衝",
    "决決",
    "况況",
    "冻凍",
    "净淨",
    "减減",
    "凤鳳",
    "凭憑",
    "击擊",
    "则則",
    "刚剛",
    "创創",
    "划劃",
    "刘劉",
    "劲勁",
    "勋勳",
    "华華",
    "协協",
    "厉厲",
    "厌厭",
    "叙敘",
    "叶葉",
    "叹歎",
    "吓嚇",
    "启啟",
    "呜嗚",
    "哑啞",
    "唤喚",
    "啰囉",
    "喷噴",
    "嘱囑",
    "团團糰",
    "围圍",
    "圆圓",
    "圣聖",
    "场場",
    "坏壞",
    "块塊",
    "坚堅",
    "坛壇罈",
    "坟墳",
    "垄壟",
    "垒壘",
    "垦墾",
    "垫墊",
    "墙牆",
    "壮壯",
    "壳殼",
    "备備",
    "够夠",
    "头頭",
    "夹夾",
    "夺奪",
    "奖獎",
    "奋奮",
    "妆妝",
    "妇婦",
    "妈媽",
    "娱娛",
    "婴嬰",
    "孙孫",
    "宁寧",
    "宝寶",
    "宠寵",
    "审審",
    "宪憲",
    "宫宮",
    "宾賓",
    "寻尋",
    "导導",
    "寿壽",
    "将將",
    "尘塵",
    "层層",
    "屉屜",
    "届屆",
    "属屬",
    "岁歲",
    "岂豈",
    "岗崗",
    "岭嶺",
    "峡峽",
    "币幣",
    "帅帥",
    "师師",
    "帐帳",
    "带帶",
    "帮幫",
    "广廣",
    "庄莊",
    "庆慶",
    "庐廬",
    "庙廟",
    "废廢",
    "弃棄",
    "张張",
    "弥彌",
    "弯彎",
    "弹彈",
    "强強",
    "归歸",
    "当當噹",
    "录錄",
    "彻徹",
    "径徑",
    "忆憶",
    "忏懺",
    "忧憂",
    "怀懷",
    "态態",
    "总總",
    "恋戀",
    "恳懇",
    "恶惡",
    "恼惱",
    "悦悅",
    "悬懸",
    "惊驚",
    "惧懼",
    "惨慘",
    "惩懲",
    "愿願",
    "慑懾",
    "懒懶",
    "戏戲",
    "战戰",
    "户戶",
    "扑撲",
    "扩擴",
    "扫掃",
    "扬揚",
    "扰擾",
    "抚撫",
    "抛拋",
    "抢搶",
    "护護",
    "拟擬",
    "挤擠",
    "挥揮",
    "损損",
    "掷擲",
    "揽攬",
    "撑撐",
    "摄攝",
    "摆擺",
    "摇搖",
    "摊攤",
    "撵攆",
    "拧擰",
    "拨撥",
)


def _build_groups() -> Mapping[str, str]:
    """Map every character in every group to the group's canonical form."""

    mapping: dict[str, str] = {}
    for group in _VARIANT_GROUP_DATA:
        canonical = group[0]
        for character in group:
            if character == canonical:
                continue
            existing = mapping.get(character)
            if existing is not None and existing != canonical:
                # Two groups disagree about the same character. Refusing to
                # merge is the only safe answer: silently picking one would fold
                # genuinely distinct characters together.
                raise RuntimeError(
                    f"variant table is contradictory for {character!r}: "
                    f"{existing!r} vs {canonical!r}"
                )
            mapping[character] = canonical
    return MappingProxyType(mapping)


VARIANT_GROUPS: Mapping[str, str] = _build_groups()


def fold_chars(text: str) -> str:
    """Fold variant characters and apply Unicode NFKC.

    NFKC handles compatibility forms (full-width latin, CJK compatibility
    ideographs, combined marks) that scans and OCR engines produce freely, without
    touching meaningfully distinct characters.
    """

    if not text:
        return ""
    normalized = unicodedata.normalize("NFKC", text)
    return "".join(VARIANT_GROUPS.get(character, character) for character in normalized)


_WHITESPACE = " \t\r\n\u3000\ufeff"
_PUNCTUATION_FOLD = {
    "，": ",",
    "、": ",",
    "。": ".",
    "：": ":",
    "；": ";",
    "？": "?",
    "！": "!",
    "（": "(",
    "）": ")",
    "「": '"',
    "」": '"',
    "『": '"',
    "』": '"',
    "“": '"',
    "”": '"',
    "‘": "'",
    "’": "'",
    "—": "-",
    "–": "-",
    "－": "-",
    "．": ".",
}


def fold_for_comparison(text: str) -> str:
    """Aggressively fold a string into a comparison key.

    Removes whitespace, folds punctuation width and variant forms. Use this only
    to decide whether two strings *mean the same thing*; never to produce text a
    reader will see.
    """

    folded = fold_chars(text)
    folded = "".join(_PUNCTUATION_FOLD.get(character, character) for character in folded)
    # Case folding matters for the Latin fragments old Chinese books are full of
    # ("IBM", "V."), where engines freely differ on case.
    folded = folded.casefold()
    return "".join(character for character in folded if character not in _WHITESPACE)


def variant_groups_in(texts: Iterable[str]) -> Mapping[str, tuple[str, ...]]:
    """Characters in ``texts`` that belong to a variant group, grouped by canonical.

    Lets a reviewer be told "these two readings differ only by glyph variant"
    instead of being asked to adjudicate an orthographic difference that has no
    bearing on the text.
    """

    observed: dict[str, set[str]] = {}
    for text in texts:
        for character in unicodedata.normalize("NFKC", text):
            canonical = VARIANT_GROUPS.get(character)
            if canonical is not None:
                observed.setdefault(canonical, set()).add(character)
                observed[canonical].add(canonical)
    return MappingProxyType(
        {
            canonical: tuple(sorted(characters))
            for canonical, characters in observed.items()
        }
    )


def similarity_for_comparison(left: str, right: str) -> float:
    """Ratio of two strings after folding, for alignment confidence."""

    from difflib import SequenceMatcher

    folded_left = fold_for_comparison(left)
    folded_right = fold_for_comparison(right)
    if not folded_left and not folded_right:
        return 1.0
    if not folded_left or not folded_right:
        return 0.0
    return SequenceMatcher(a=folded_left, b=folded_right, autojunk=False).ratio()


def is_variant_only_difference(left: str, right: str) -> bool:
    """True when two readings differ only by glyph variant or punctuation width.

    Requires a real textual difference: identical strings are not a "variant"
    difference, they are agreement, and callers must not treat them as a finding.
    """

    if left == right:
        return False
    return fold_for_comparison(left) == fold_for_comparison(right)
