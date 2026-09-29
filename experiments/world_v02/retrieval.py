"""四种取法对照（票 #68）里全量塞入与 RAG 两组的文本：经读投影取 scope 的全部内容，切成分块，全量组按文档顺序全写，
RAG 组按字符二元组 BM25 取前若干块到字符预算为止。纯 Python，不接任何外部服务或 embedding。

取数（read_corpus）只经 HTTP 读投影：列对象（GET /v1/world/objects，分页）列出 scope 内全部 world 对象（业务对象与状态
快照），逐个取对象（最新版），再逐个取事件，事件按 id 去重。其余函数都是纯函数。

切分（chunks）：每个分块只带一条自己的引用，也就是它「取到」的那一项：

- 对象表头：业务对象与状态快照各一块，引用是对象形式 ``对象@版本``。写类型、标题、生命周期、责任人、正式内容、属性与
  关系；快照写主体、时点、生成者与来源事件。标准答案的「谁负责」「现在怎样」应引对象本身，没有这一块按构造就答不了；
- 块：一个块一块（空块写标准句），引用是块形式 ``对象@版本#块``，不含块里的组件；
- 组件：一个组件一块，引用是组件形式 ``对象@版本#块/组件``；
- 事件：每条一块，引用是 ``event:<事件 id>``。否则「发生了什么」这一问按构造就答不了。

状态快照同样按表头、块与组件切。正文里照读投影写出钉着的别处引用（关系、块内引用、事件的主体），它们只是引用，不算
取到，同 MCP 运行日志的口径。「取到」用 ``tkos_world_mcp.server._content`` 判（与 MCP 运行日志、实验 E 的回放一致）；
这是对一个私有函数的依赖，MCP 改了识别口径这里跟着变。每个分块的来源是它在读投影里的那一段，切分时核对
``_content(来源)`` 恰好是这个分块的引用，不是就报错。

检索（Index、retrieve）：分块的检索文本是它的正文去掉业务引用（UUID 的二元组只会把分数偏向起点附近的分块）与时刻
（每次播种都不同，留着会让同样的内容在不同的播种上排出不同的名次）；小写后
按非字母数字字符断开，每段取相邻两个字符为一个词，只有一个字符的段取这个字符。BM25 取 k1=1.2、b=0.75，
idf=ln(1+(N-df+0.5)/(df+0.5))，查询词去重后求和。按分数从高到低、同分按文档顺序排，分数为 0 的不取；分数先舍入到
小数点后 9 位再排，免得不同机器上最后一位的浮点差改变名次。

装入（fill）：按名次逐块装入，分块之间用一个空行连接；装得下就装，装不下的记下、接着看下一块，直到看完。装入的文本
按名次排列。字符上限由调用者给：跑器取固定路径组同一问交给模型的字符数（同成本对照，见 experiment）。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
import math
import re
from typing import Any
from urllib.parse import quote

from tkos_world_mcp.server import _content
from tkos_world_mcp.tools_v02 import FACE

from . import spec

K1, B = 1.2, 0.75
SEPARATOR = '\n\n'
_UUID = r'[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}'
_REFS = re.compile(rf'`?(?:event:{_UUID}|{_UUID}(?:@[0-9]+(?:#[A-Za-z0-9_]+(?:/[A-Za-z0-9_.:-]+)?)?)?)`?')
_TIMES = re.compile(r'[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}(?:\.[0-9]+)?(?:Z|[+-][0-9]{2}:[0-9]{2})')
_TYPES = {item['type']: item for item in spec.REGISTRY['objects']}
_COMPONENTS = {item['id']: item for item in spec.REGISTRY['components']['types']}
_KINDS = {item['kind']: item['display_name'] for item in spec.REGISTRY['event_kinds']}
_VALUES = {group: {item['id']: item['display_name'] for item in values}
           for group, values in spec.REGISTRY['event_attribute_values'].items()}
_SOURCES = {item['id']: item['display_name'] for item in spec.REGISTRY['components']['progress_entry_sources']}


@dataclass
class Chunk:
    """一个分块：自己的引用、种类（object、block、component、event）、文档顺序、给人看的名字、正文，以及它在读投影里
    的来源（交给 _content 判取到）。"""
    ref: str
    kind: str
    order: int
    label: str
    text: str
    source: dict[str, Any] = field(repr=False)


# ------------------------------------------------------------------ reading
def read_corpus(client) -> dict[str, Any]:
    """经读投影取 scope 的全部内容：对象按列对象的顺序（建立时刻与 id），每个对象是取对象的返回（最新版）；事件按
    发生时刻、记录时刻与 id 排序。client 是有 json(method, path) 的 HTTP 客户端。"""
    heads, cursor = [], None
    while True:
        page = client.json('GET', '/v1/world/objects?limit=100' + (f'&cursor={quote(cursor)}' if cursor else ''))
        heads += page['items']
        cursor = page['next_cursor']
        if not cursor:
            break
    objects, events = [], {}
    for head in heads:
        if head['contract_version'] != 'tkos.world/0.2':
            raise ValueError(f"only tkos.world/0.2 objects are read, not {head['contract_version']}")
        objects.append(client.json('GET', f"/v1/world/objects/{head['object_id']}"))
        for event in client.json('GET', f"/v1/world/objects/{head['object_id']}/events")['events']:
            events[event['event_id']] = event
    ordered = sorted(events.values(), key=lambda item: (moment(item['occurred_at']), moment(item['recorded_at']),
                                                         item['event_id']))
    return {'objects': objects, 'events': ordered}


def moment(text: str) -> datetime:
    """UTC 规范文本（小数秒可有可无，按字符串排会排错）转成时刻。"""
    return datetime.fromisoformat(text.replace('Z', '+00:00'))


# ------------------------------------------------------------------ chunking
def _names(corpus: dict[str, Any]) -> dict[str, str]:
    """读投影里见得到的主体 id -> 显示名：事件的记录者与被代记的人、对象的责任人、快照的生成者。"""
    names = {}
    for event in corpus['events']:
        names[event['principal']['principal_id']] = event['principal']['display_name']
        if event['on_behalf_of']:
            names[event['on_behalf_of']['principal_id']] = event['on_behalf_of']['display_name']
    for view in corpus['objects']:
        people = [view['generator']] if 'business' not in view else view['identity']['responsible']['principals']
        names.update((person['principal_id'], person['display_name']) for person in people)
    return names


def _codes(refs: list[dict[str, Any]]) -> str:
    return '、'.join(f"`{item['ref']}`" for item in refs)


def _attribute(attribute: dict[str, Any], value: Any, names: dict[str, str]) -> str:
    if attribute['value'] == 'principal':
        return names.get(value, value)
    if attribute['value'] == 'progress_entries':
        return '；'.join(f"{entry['at']} {_SOURCES.get(entry['source'], entry['source'])}：{entry['text']}"
                        + (f"（{entry['url']}）" if entry.get('url') else '') for entry in value)
    return str(value)


def _block_text(block: dict[str, Any], heading: str) -> str:
    """一块：标题带块引用，然后是块自己的文字（空块是标准句）、块内引用与文档链接；组件另成分块。"""
    value = block['value']
    lines = [heading] + ([block['text']] if block['text'] else [])
    if value and value['refs']:
        lines.append('引用：' + _codes(value['refs']))
    if value and value['artifacts']:
        lines.append('文档：' + '、'.join(value['artifacts']))
    return '\n'.join(lines)


def _component_text(component: dict[str, Any], heading: str, names: dict[str, str]) -> str:
    kind = _COMPONENTS[component['type']]
    lines = [f"{heading}（{kind['display_name']}）" + (f"：{component['text']}" if component['text'] else '')]
    lines += [f"{attribute['display_name']}：{_attribute(attribute, component['attributes'][attribute['id']], names)}"
              for attribute in kind['attributes'] if component['attributes'].get(attribute['id']) not in (None, '', [])]
    if component['scope']:
        lines.append(f"适用范围：`{component['scope']['ref']}`")
    if component['refs']:
        lines.append('引用：' + _codes(component['refs']))
    if component['artifacts']:
        lines.append('文档：' + '、'.join(component['artifacts']))
    return '\n'.join(lines)


def _object_header(view: dict[str, Any], names: dict[str, str]) -> tuple[str, str, dict[str, Any]]:
    """业务对象的表头：返回（名字、正文、来源）。来源是 business 组去掉块（只剩对象形式的引用）。"""
    business, identity, records = view['business'], view['identity'], view['records']
    kind = _TYPES[business['object_type']]
    ref = f"{business['object_id']}@{business['version']}"
    label = f"{business['type_display_name']}《{business['title']}》"
    responsible = identity['responsible']
    source = '属性 responsible' if responsible['source'] == 'attribute' else f"角色 {responsible.get('role')}"
    stage = records['lifecycle']
    lines = [f"## {label} `{ref}`", f"类别：{business['category']['display_name']}",
             '生命周期：' + (f"{stage['display_name']}（事件 `event:{stage['event_id']}`）" if stage else '无（只有版本）'),
             f"责任人（来自{source}）：" + ('、'.join(person['display_name'] for person in responsible['principals'])
                                        or '未指派')]
    if kind['gated']:
        lines.append('正式内容：' + ('已确认' if business['formal']['lifecycle_status'] == 'confirmed' else '尚未确认'))
    if business['round']:
        lines.append(f"进行中的一轮：{business['round']['display_name']}")
    attributes = [f"{attribute['display_name']}：{_attribute(attribute, business['attributes'][attribute['id']], names)}"
                  for attribute in kind['attributes'] if attribute['id'] not in {'title', 'responsible'}
                  and business['attributes'].get(attribute['id']) not in (None, '', [])]
    if attributes:
        lines.append('属性：' + '；'.join(attributes))
    relations = []
    for relation in business['relations']:
        value = relation['value']
        values = value if isinstance(value, list) else [value] if value else []
        if values:
            relations.append(f"{relation['field']}：" + _codes(values))
    if relations:
        lines.append('关系：' + '；'.join(relations))
    if records['latest_state']:
        lines.append(f"最新状态快照：`{records['latest_state']['ref']}`（未经确认）")
    if records['confirmed_review']:
        lines.append(f"已确认的复盘：`{records['confirmed_review']['snapshot']['ref']}`"
                     f"（确认事件 `{records['confirmed_review']['ref']}`）")
    if records['open_issues']:
        lines.append('还没处置的问题：' + _codes([item['issue_ref'] for item in records['open_issues']]))
    return label, '\n'.join(lines), {**business, 'blocks': []}


def _snapshot_header(view: dict[str, Any], names: dict[str, str]) -> tuple[str, str, dict[str, Any]]:
    label = f"状态快照《{view['title']}》"
    shell = [f"主体：`{view['subject_ref']['ref']}`", f"截至：{view['as_of']}"] \
        + ([f"周期：{view['period']}"] if view['period'] else []) \
        + [f"payload：{view['payload_type']['display_name']}", f"生成者：{view['generator']['display_name']}"]
    lines = [f"## {label} `{view['ref']}`（未经确认）", '；'.join(shell),
             '来源事件：' + _codes(view['source_event_refs'])]
    return label, '\n'.join(lines), {key: value for key, value in view.items() if key != 'protocol'} | {'blocks': []}


def _event(event: dict[str, Any], names: dict[str, str]) -> tuple[str, str]:
    """一条事件：发生时刻、种类与取值、事件引用、谁记的（代记写两个人，指派另写被指派者）、原事件、迟记、被更正与
    被撤回、主体与内容。"""
    label = _KINDS[event['kind']] + ''.join(f"·{_VALUES[group][event[group]]}" for group in ('category', 'outcome',
                                                                                         'disposition') if event[group])
    recorder = event['principal']['display_name'] + (f" 代 {event['on_behalf_of']['display_name']}"
                                                     if event['on_behalf_of'] else '')
    notes = [f'{recorder} 记']
    if event['kind'] == 'assign':
        assignee = (event['detail'] or {}).get('principal_id')
        notes.append(f"指派给 {names.get(assignee, assignee)}")
    if event['supersedes_event_id']:
        notes.append(f"原事件 `event:{event['supersedes_event_id']}`")
    if event['late']:
        notes.append('迟记')
    notes += [f'被 `event:{other}` 更正' for other in event['corrected_by']]
    notes += [f'被 `event:{other}` 撤回' for other in event['withdrawn_by']]
    content = event['content']
    lines = [f"- {event['occurred_at']} {label}（事件 `event:{event['event_id']}`，{'，'.join(notes)}）"
             f"主体：{_codes(event['subject_refs'])}" + (f"：{content['text']}" if content and content['text'] else '')]
    if content and content.get('refs'):
        lines.append('  引用：' + _codes(content['refs']))
    if content and content.get('artifacts'):
        lines.append('  文档：' + '、'.join(content['artifacts']))
    return f"{_KINDS[event['kind']]}（{event['occurred_at']}）", '\n'.join(lines)


def chunks(corpus: dict[str, Any]) -> list[Chunk]:
    """按文档顺序切分：每个对象先表头，再逐块（登记顺序），块之后是它的组件；对象之后是全部事件。每个分块核对
    _content(来源) 恰好是它自己的引用。"""
    names = _names(corpus)
    found: list[Chunk] = []

    def add(ref: str, kind: str, label: str, text: str, source: dict[str, Any]) -> None:
        refs, events = _content(source, FACE)
        taken = refs | {f'event:{event_id}' for event_id in events}
        if taken != {ref}:
            raise ValueError(f'the chunk {ref} carries {sorted(taken)}, not exactly its own reference')
        found.append(Chunk(ref, kind, len(found), label, text, source))

    for view in corpus['objects']:
        snapshot = 'business' not in view
        label, text, source = _snapshot_header(view, names) if snapshot else _object_header(view, names)
        add(source['ref'] if snapshot else f"{source['object_id']}@{source['version']}", 'object', label, text, source)
        for block in (view if snapshot else view['business'])['blocks']:
            name = f"{label}·{block['display_name']}"
            add(block['ref'], 'block', name, _block_text(block, f"### {name} `{block['ref']}`"),
                {key: value for key, value in block.items() if key not in {'components', 'value'}})
            for component in block['components']:
                heading = f"- {name} `{component['ref']}`"
                add(component['ref'], 'component', f"{name}/{component['id']}",
                    _component_text(component, heading, names), component)
    for event in corpus['events']:
        label, text = _event(event, names)
        add(f"event:{event['event_id']}", 'event', label, text, event)
    return found


def taken(selected: list[Chunk]) -> tuple[set[str], set[str]]:
    """一组分块取到的引用与事件 id（_content 口径，同 MCP 运行日志的 read_refs、read_event_ids）。"""
    return _content([chunk.source for chunk in selected], FACE)


def render(selected: list[Chunk]) -> str:
    return SEPARATOR.join(chunk.text for chunk in selected)


# ------------------------------------------------------------------ BM25
def tokens(text: str) -> list[str]:
    """检索用的词：去掉业务引用与时刻、小写，按非字母数字字符断开，每段取字符二元组（一个字符的段取这个字符）。"""
    words = []
    for run in ''.join(char if char.isalnum() else ' '
                       for char in _TIMES.sub(' ', _REFS.sub(' ', text)).lower()).split():
        if len(run) == 1:
            words.append(run)
        words += [run[index:index + 2] for index in range(len(run) - 1)]
    return words


class Index:
    """分块的 BM25 索引。"""

    def __init__(self, found: list[Chunk]):
        self.chunks = found
        self.docs = [tokens(chunk.text) for chunk in found]
        self.lengths = [len(doc) for doc in self.docs]
        self.average = sum(self.lengths) / len(self.docs) if self.docs else 0.0
        self.counts = [{} for _ in self.docs]
        frequency: dict[str, int] = {}
        for counts, doc in zip(self.counts, self.docs):
            for word in doc:
                counts[word] = counts.get(word, 0) + 1
            for word in counts:
                frequency[word] = frequency.get(word, 0) + 1
        total = len(self.docs)
        self.idf = {word: math.log(1 + (total - df + 0.5) / (df + 0.5)) for word, df in frequency.items()}

    def score(self, query: str) -> list[float]:
        words = sorted(set(tokens(query)))
        scores = []
        for counts, length in zip(self.counts, self.lengths):
            total = 0.0
            for word in words:
                tf = counts.get(word, 0)
                if tf:
                    total += self.idf[word] * tf * (K1 + 1) / (tf + K1 * (1 - B + B * length / self.average))
            scores.append(round(total, 9))
        return scores

    def rank(self, query: str) -> list[tuple[float, Chunk]]:
        """分数从高到低、同分按文档顺序；分数为 0 的不列。"""
        scored = [(score, chunk) for score, chunk in zip(self.score(query), self.chunks) if score > 0]
        return sorted(scored, key=lambda item: (-item[0], item[1].order))


def fill(ranked: list[tuple[float, Chunk]], max_chars: int) -> dict[str, Any]:
    """按名次装入到字符预算：装得下就装，装不下的记下、接着看下一块。字符数按 render 的结果计（分块之间一个空行）。"""
    kept, skipped, used = [], [], 0
    for score, chunk in ranked:
        cost = len(chunk.text) + (len(SEPARATOR) if kept else 0)
        if used + cost <= max_chars:
            kept.append((score, chunk))
            used += cost
        else:
            skipped.append((score, chunk))
    return {'kept': kept, 'skipped': skipped, 'chars': used}


def query(start_title: str, question: str) -> str:
    """检索的查询：起点对象的标题加问题。有的问题（「这个 Task 要做成什么？」）不带标题，只用问题会检索到别处。"""
    return f'{start_title}\n{question}'


def retrieve(index: Index, start_title: str, question: str, max_chars: int) -> dict[str, Any]:
    """RAG 组一问：检索、装入到 max_chars，返回装入的分块（按名次）、渲染后的文本、取到的集合与字符数。"""
    packed = fill(index.rank(query(start_title, question)), max_chars)
    selected = [chunk for _, chunk in packed['kept']]
    refs, events = taken(selected)
    text = render(selected)
    assert len(text) == packed['chars']
    return {'query': query(start_title, question), 'max_chars': max_chars, 'text': text, 'chars': len(text),
            'refs': sorted(refs), 'event_ids': sorted(events),
            'kept': [{'ref': chunk.ref, 'score': score, 'chars': len(chunk.text)} for score, chunk in packed['kept']],
            'skipped': [{'ref': chunk.ref, 'score': score, 'chars': len(chunk.text)} for score, chunk in packed['skipped']]}


def full(found: list[Chunk]) -> dict[str, Any]:
    """全量塞入：scope 内每个对象的最新版与每条事件，按文档顺序全写。"""
    refs, events = taken(found)
    text = render(found)
    return {'text': text, 'chars': len(text), 'refs': sorted(refs), 'event_ids': sorted(events)}
