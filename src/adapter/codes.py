"""Stable, stateless business codes used at the Clark boundary.

The adapter deliberately does not allocate or persist a code.  An issue code is
computed from its chain title and the caller-provided set of already-used slugs;
judgment and signal codes are computed from their one-based ordinal.  This makes
restarts harmless, while leaving collision allocation to the caller.

The Chinese transliteration table is intentionally small and dependency-free.
Common characters use a pinyin initial; an uncovered CJK character gets a
stable Unicode-derived letter.  The latter is only a deterministic fallback,
not a claim about that character's pronunciation.
"""
from __future__ import annotations

from dataclasses import dataclass
from types import MappingProxyType
from typing import Literal
from unicodedata import normalize


class UnknownCodeError(ValueError):
    """Raised when a string is not one of the adapter's canonical code forms."""


@dataclass(frozen=True, slots=True)
class CodeInfo:
    """Parsed canonical code.

    ``kind`` is the wire prefix (``ISS``, ``JDG`` or ``SGN``).  ``key`` is a
    slug for an issue and a one-based ``int`` ordinal for a judgment/signal.
    ``code`` is retained so downstream adapters can pass the exact canonical
    value through without reconstructing it.
    """

    kind: Literal["ISS", "JDG", "SGN", "AGR"]
    key: str | int
    code: str


# Compact immutable data: one string per pinyin initial.  This covers the
# common vocabulary used by issue/chain titles without importing pypinyin.
_INITIAL_GROUPS: tuple[tuple[str, str], ...] = (
    ("a", "阿啊哎唉爱碍安按案暗岸昂奥"),
    ("b", "八巴拔把爸白百摆败拜班般板版办半伴帮邦包保宝报抱暴北备被本奔笨比笔必闭边编变便遍表别宾冰兵并病波播博补不布步部"),
    ("c", "擦猜才材财采彩参餐残藏草册侧层茶查差柴拆产昌长常厂场唱超朝潮车彻沉陈晨成承城程吃持迟池驰赤充虫重宠抽仇初除楚处触川穿船传窗创春纯词此次从村存错仓促测策"),
    ("d", "大代带待袋戴丹单担胆当党刀到道得德灯登等低底地第弟帝点电店典丁定东冬懂动都斗豆读独度断段对队多朵夺"),
    ("e", "鹅额俄恶饿恩而耳二"),
    ("f", "发法罚翻凡反返犯饭方房防访放非飞肥费分份封风峰丰否夫肤扶服福府父富复负妇附"),
    ("g", "该改盖概干甘赶敢感刚钢港高告哥歌格隔个各给根跟更工公功共供够构古故固顾关观馆管光广归规贵国果过"),
    ("h", "哈还海害含寒汉好号浩喝何和河核合黑很恨横红后候乎呼互户护花华画话欢环换黄回会婚活火或货获惑"),
    ("j", "几己机鸡基积极即急级计记技际季既纪济价家加佳假架间简见件建健将江讲交教较接节结解界姐借今金仅进近经精景境静京究九久旧就居局举句据聚具决绝军均君警"),
    ("k", "开凯看康抗考靠科可克刻客空恐口扣苦快块宽况库"),
    ("l", "拉来莱兰蓝栏懒狼劳老乐雷类冷离李里理礼力立利连联练链脸两量亮聊料列烈林临灵领流留六龙楼路录论落"),
    ("m", "吗马买卖满慢忙毛美每妹门们梦米密民明名命摸某母木目"),
    ("n", "那哪内南男难脑呢能你年念娘鸟您宁牛农弄暖"),
    ("p", "怕拍排派盘判盼旁跑配碰批皮片篇票品平评凭破普"),
    ("q", "七期其奇骑齐起气器前钱千强墙桥且切亲琴青轻清情请秋求区取去曲全权泉却确群"),
    ("r", "然染让热人仁认任忍日容肉如入软"),
    ("s", "撒洒赛三散色森杀沙山上商伤尚少绍社设身深神沈生声省胜盛时实识食使始世市事试视是手首受书术树数双谁水睡顺说思死四送苏速算虽随岁孙所司"),
    ("t", "他它台太态谈探堂唐糖特提体天田条听停同通统头投图土团推退题"),
    ("w", "挖外完玩万王往网望为位味未文问我无五午物务武"),
    ("x", "下夏先显现县线相想向象消小校笑些写谢心新信行形性兴星醒兄需许序选学雪血"),
    ("y", "呀牙亚严言岩研眼演阳洋养样要也业夜一已以亿义议意因音引应英影用优由有又于与予育玉遇预元原圆员远院愿营"),
    ("z", "杂在再咱早造则责增曾怎站张找照者这着真阵正整知直只至志制治中种众周州主住注专转装追准资子自字总走足组最作做坐状战略"),
)
_PINYIN_INITIALS = MappingProxyType(
    {char: initial for initial, chars in _INITIAL_GROUPS for char in chars}
)
_CJK_RANGES: tuple[tuple[int, int], ...] = (
    (0x3400, 0x4DBF),
    (0x4E00, 0x9FFF),
    (0xF900, 0xFAFF),
    (0x20000, 0x2FA1F),
)
_MAX_SLUG_LENGTH = 48


def _is_cjk(char: str) -> bool:
    point = ord(char)
    return any(start <= point <= end for start, end in _CJK_RANGES)


def _fallback_initial(char: str) -> str:
    """Return a deterministic one-letter fallback for an unmapped Han char."""
    return chr(ord("a") + ord(char) % 26)


def slug_of(title: str) -> str:
    """Convert a title to a deterministic lowercase URL-safe slug.

    ASCII letters/digits are retained (letters lowercased), mapped Han
    characters become pinyin initials, and every other run becomes one ``-``.
    Full-width forms are folded with NFKC.  The result is capped at 48
    characters; an empty/punctuation-only title is represented by ``x``.
    """
    if not isinstance(title, str):
        raise TypeError("title must be a string")

    normalized = normalize("NFKC", title)
    pieces: list[str] = []
    pending_separator = False
    for char in normalized:
        if char.isascii() and char.isalnum():
            if pending_separator and pieces:
                pieces.append("-")
            pending_separator = False
            pieces.append(char.lower())
            continue

        initial = _PINYIN_INITIALS.get(char)
        if initial is None and _is_cjk(char):
            initial = _fallback_initial(char)
        if initial is not None:
            if pending_separator and pieces:
                pieces.append("-")
            pending_separator = False
            pieces.append(initial)
        else:
            pending_separator = True

    slug = "".join(pieces).strip("-")[:_MAX_SLUG_LENGTH].strip("-")
    return slug or "x"


def dedupe(slug: str, taken: set[str]) -> str:
    """Return the first unused slug, trying ``slug-2``, ``slug-3``, ... .

    ``taken`` is a set of slugs, not complete ``ISS-`` codes.  It is never
    mutated; callers that allocate several codes add the returned value to
    their own set after each call.
    """
    if not isinstance(slug, str) or not slug:
        raise ValueError("slug must be a non-empty string")
    if slug not in taken:
        return slug
    ordinal = 2
    while f"{slug}-{ordinal}" in taken:
        ordinal += 1
    return f"{slug}-{ordinal}"


def _ordinal_code(prefix: Literal["JDG", "SGN", "AGR"], ordinal: int) -> str:
    if type(ordinal) is not int or ordinal < 1:
        raise ValueError("ordinal must be a positive integer starting at 1")
    return f"{prefix}-{ordinal:02d}"


def issue_code(chain_title: str, taken: set[str]) -> str:
    """Build ``ISS-{slug}``, deduplicating against already-used slugs."""
    return f"ISS-{dedupe(slug_of(chain_title), taken)}"


def judgment_code(ordinal: int) -> str:
    """Build a one-based ``JDG`` code, padded to at least two digits."""
    return _ordinal_code("JDG", ordinal)


def signal_code(ordinal: int) -> str:
    """Build a one-based ``SGN`` code, padded to at least two digits."""
    return _ordinal_code("SGN", ordinal)


def agreement_code(ordinal: int) -> str:
    """Build a one-based ``AGR`` code, padded to at least two digits."""
    return _ordinal_code("AGR", ordinal)


def _invalid(code: object) -> UnknownCodeError:
    return UnknownCodeError(f"unknown or malformed code: {code!r}")


def parse(code: str) -> CodeInfo:
    """Parse a canonical short code into a frozen :class:`CodeInfo`.

    Issue keys are lowercase slug grammar.  Judgment and signal keys must be
    the canonical two-or-more-digit representation emitted by this module;
    non-canonical forms such as ``JDG-1`` and ``JDG-001`` are rejected.
    """
    if not isinstance(code, str):
        raise _invalid(code)
    prefix, separator, key = code.partition("-")
    if not separator or prefix not in {"ISS", "JDG", "SGN", "AGR"} or not key:
        raise _invalid(code)

    if prefix == "ISS":
        if (
            len(key) > _MAX_SLUG_LENGTH
            or key[0] == "-"
            or key[-1] == "-"
            or "--" in key
            or any(not (char.isascii() and (char.isalnum() or char == "-")) for char in key)
            or any(char.isupper() for char in key)
        ):
            raise _invalid(code)
        return CodeInfo("ISS", key, code)

    if not key.isascii() or not key.isdigit():
        raise _invalid(code)
    ordinal = int(key)
    if ordinal < 1:
        raise _invalid(code)
    if prefix == "JDG":
        canonical = judgment_code(ordinal)
        kind: Literal["JDG", "SGN", "AGR"] = "JDG"
    elif prefix == "SGN":
        canonical = signal_code(ordinal)
        kind = "SGN"
    else:
        canonical = agreement_code(ordinal)
        kind = "AGR"
    if canonical != code:
        raise _invalid(code)
    return CodeInfo(kind, ordinal, code)


__all__ = (
    "CodeInfo",
    "UnknownCodeError",
    "agreement_code",
    "dedupe",
    "issue_code",
    "judgment_code",
    "parse",
    "signal_code",
    "slug_of",
)
