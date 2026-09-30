"""tkos.world/0.2 的严格载荷与参数模型，由随包的 0.2 登记生成，块清单与组件类型不写死在代码里。

载荷形状同 0.1：属性与关系引用字段在顶层，内容块在 ``blocks`` 下，登记里的每个块都出现，空块存 null。
块值是 0.2 的四个字段 ``{text, components, refs, artifacts}``（契约第 4 节）；组件的类型须是所在块
允许的，类型属性按登记，id 在对象内唯一。

每类有两个模型：写入模型只收客户端能写的字段，引用是业务形式的字符串，组件 id 可以不给；存储模型含
登记的全部字段，引用是钉定后的结构，组件都有 id，另有服务维护的组件台账 ``component_ledger``。
只由事件写的属性与只经 world_relate 写的关系字段不进写入模型；周期目标的依据复盘 ``review_ref`` 建对象时写（票 #60）。
"""
from __future__ import annotations

from functools import lru_cache
import re
from typing import Annotated, Any, Literal, Optional, Union
from uuid import uuid4

from pydantic import (AfterValidator, BaseModel, Field, StrictBool, StrictStr, StringConstraints, create_model,
                      field_validator, model_validator)

from . import world_v02_registry as world_registry
from .a2_models import CanonicalUUID, NEStr, PositiveInt, StrictModel
from .world_v01_models import Timestamp

CONTRACT_VERSION = "tkos.world/0.2"

ArtifactUrl = Annotated[str, StringConstraints(pattern=r"^https?://\S+$", max_length=2048)]
ShortText = Annotated[str, StringConstraints(pattern=r"\S", max_length=256)]
Month = Annotated[str, StringConstraints(pattern=r"^[0-9]{4}-(0[1-9]|1[0-2])$")]

# 引用的四种业务形式（契约第 5 节）。请求模型在导入时就要，不能等按需加载的登记，由测试与登记逐条对齐。
_UUID = r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}"
_BLOCK = r"[a-z][a-z0-9_]*"
COMPONENT_ID_PATTERN = r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}$"
_COMPONENT = COMPONENT_ID_PATTERN[1:-1]
REFERENCE_PATTERNS = {
    "object": rf"^({_UUID})@([1-9][0-9]*)$",
    "block": rf"^({_UUID})@([1-9][0-9]*)#({_BLOCK})$",
    "component": rf"^({_UUID})@([1-9][0-9]*)#({_BLOCK})/({_COMPONENT})$",
    "event": rf"^event:({_UUID})$",
}
_REFERENCES = {form: re.compile(pattern) for form, pattern in REFERENCE_PATTERNS.items()}
COMPONENT_ID = re.compile(_COMPONENT)
ComponentId = Annotated[str, StringConstraints(pattern=COMPONENT_ID_PATTERN)]
BlockId = Annotated[str, StringConstraints(pattern=rf"^{_BLOCK}$")]


def parse_ref(text: Any) -> dict[str, Any]:
    """把业务形式拆开：事件形式给事件 id，其余给对象 id、版本号、块与组件（没有的为 None）。"""
    for form, pattern in _REFERENCES.items():
        match = pattern.match(text) if isinstance(text, str) else None
        if match is None:
            continue
        if form == "event":
            return {"form": "event", "event_id": match[1]}
        return {"form": form, "object_id": match[1], "object_version": int(match[2]),
                "block": match[3] if form != "object" else None, "component": match[4] if form == "component" else None}
    raise ValueError("a reference is <object id>@<version>, optionally #<block> and /<component>, or event:<event id>")


def citation(object_id: str, version: int, block: str | None = None, component: str | None = None) -> str:
    """对象、块与组件形式引用的业务形式（契约第 5 节）。"""
    return f"{object_id}@{version}" + (f"#{block}" if block else "") + (f"/{component}" if component else "")


def cite(pinned: dict[str, Any]) -> str:
    """钉定引用的业务形式。"""
    if "event_id" in pinned:
        return f"event:{pinned['event_id']}"
    return citation(pinned["object_id"], pinned["object_version"], pinned["block"], pinned["component"])


def _ref_text(*forms: str):
    def check(text: str) -> str:
        if parse_ref(text)["form"] not in forms:
            raise ValueError(f"this reference must be in {' or '.join(forms)} form")
        return text
    return Annotated[str, AfterValidator(check)]


RefText = _ref_text("object", "block", "component", "event")
ObjectRefText = _ref_text("object")
SubjectRefText = _ref_text("object", "component")
EventRefText = _ref_text("event")


def _relation_ref(relation: dict[str, Any]):
    """关系字段的写法按登记：指对象本身，或指登记规定的块里、规定类型的组件（类型在服务里对着修订判）。"""
    block, component = relation["target_block"], relation["target_component"]

    def check(text: str) -> str:
        ref = parse_ref(text)
        form = "component" if component else "block" if block else "object"
        if ref["form"] != form or ref.get("block") != block:
            raise ValueError(f"{relation['field']} must be a {form} reference" + (f" into #{block}" if block else ""))
        return text
    return Annotated[str, AfterValidator(check)]


class PinnedObjectRef(StrictModel):
    """钉定后的对象、块或组件引用：写入时把版本号解析成修订 id，此后不随被引用对象更新而漂移。"""
    object_id: CanonicalUUID
    object_version: PositiveInt
    revision_id: CanonicalUUID
    block: Optional[BlockId]
    component: Optional[ComponentId]


class PinnedEventRef(StrictModel):
    event_id: CanonicalUUID


PinnedRef = Union[PinnedObjectRef, PinnedEventRef]


class ExternalRef(StrictModel):
    """外部系统与本体对象的对照（契约第 3.4 节）：一个对象里同一 (system, id) 只出现一次；scope 内按各对象的最新修订
    唯一，由服务判定（票 #63）。"""
    system: ShortText
    id: ShortText
    url: Optional[ArtifactUrl] = None


def _distinct_external_refs(refs: list[ExternalRef]) -> list[ExternalRef]:
    if len({(ref.system, ref.id) for ref in refs}) != len(refs):
        raise ValueError("an external reference (system, id) appears once in an object")
    return refs


class LedgerEntry(StrictModel):
    """组件台账的一行（契约第 4 节）：出现与删除时的版本号，删除后 id 不再复用。"""
    id: ComponentId
    type: NEStr
    block: BlockId
    added_in_version: PositiveInt
    removed_in_version: Optional[PositiveInt]


def _value_type(attribute: dict[str, Any]) -> Any:
    kind = attribute["value"]
    if kind == "text":
        return NEStr
    if kind == "enum":
        return Literal[tuple(value["id"] for value in attribute["values"])]
    if kind == "month":
        return Month
    if kind == "boolean":
        return StrictBool
    if kind == "principal":
        return CanonicalUUID
    if kind == "external_refs":
        return Annotated[list[ExternalRef], AfterValidator(_distinct_external_refs)]
    if kind == "progress_entries":
        return list[_progress_entry_model()]
    raise NotImplementedError(f"world 0.2 attribute value kind {kind} is not implemented yet")


@lru_cache(maxsize=1)
def _progress_entry_model() -> type[BaseModel]:
    """进展条目的一条本期记录（契约第 4 节，字段取对接说明 11.2 草案）：时刻规范为 UTC 文本，来源按登记。"""
    sources = tuple(item["id"] for item in world_registry.registry()["components"]["progress_entry_sources"])
    return create_model("ProgressEntry", __config__=StrictModel.model_config, at=(Timestamp, ...),
                        source=(Literal[sources], ...), text=(NEStr, ...), url=(Optional[ArtifactUrl], None))


def _attribute_fields(attributes: list[dict[str, Any]]) -> dict[str, Any]:
    fields: dict[str, Any] = {}
    for attribute in attributes:
        value_type = _value_type(attribute)
        if attribute["value"] == "external_refs":
            fields[attribute["id"]] = (value_type, Field(default_factory=list))
        elif attribute["required"]:
            fields[attribute["id"]] = (value_type, ...)
        else:
            fields[attribute["id"]] = (Optional[value_type], None)
    return fields


def component_spec(type_id: str) -> dict[str, Any]:
    return next(item for item in world_registry.registry()["components"]["types"] if item["id"] == type_id)


@lru_cache(maxsize=None)
def _component_model(type_id: str, stored: bool) -> type[BaseModel]:
    spec = component_spec(type_id)
    name = "".join(part.title() for part in type_id.split("_")) + ("Stored" if stored else "Input")
    attributes = create_model(name + "Attributes", __config__=StrictModel.model_config,
                              **_attribute_fields(spec["attributes"]))
    required = any(attribute["required"] for attribute in spec["attributes"])
    return create_model(
        name + "Component", __config__=StrictModel.model_config,
        id=(ComponentId, ...) if stored else (Optional[ComponentId], None),
        type=(Literal[type_id], ...),
        scope=(Optional[PinnedObjectRef if stored else ObjectRefText], None),
        text=(StrictStr, ""),
        refs=(list[PinnedRef if stored else RefText], Field(default_factory=list)),
        artifacts=(list[ArtifactUrl], Field(default_factory=list)),
        attributes=(attributes, ...) if required else (attributes, Field(default_factory=attributes)))


def component_model(type_id: str) -> type[BaseModel]:
    """一种组件类型的写入模型：外壳七项（契约第 4 节），类型属性按登记。"""
    return _component_model(type_id, False)


class _BlockBase(StrictModel):
    text: StrictStr = ""
    artifacts: list[ArtifactUrl] = Field(default_factory=list)

    @model_validator(mode="after")
    def carries_content(self) -> "_BlockBase":
        if not self.text.strip() and not self.components and not self.refs and not self.artifacts:
            raise ValueError("a non-null block needs text, components, refs or artifacts; store an empty block as null")
        return self


@lru_cache(maxsize=None)
def _block_model(allowed: tuple[str, ...], stored: bool) -> type[BaseModel]:
    """块值模型：组件只收这个块允许的类型，按 type 分派到各类型的模型。"""
    if not allowed:
        components: Any = (list[dict[str, Any]], Field(default_factory=list, max_length=0))
    else:
        options = tuple(_component_model(type_id, stored) for type_id in allowed)
        item = options[0] if len(options) == 1 else Annotated[Union[options], Field(discriminator="type")]
        components = (list[item], Field(default_factory=list))
    return create_model("Block" + "".join(t.title() for t in allowed) + ("Stored" if stored else "Input"),
                        __base__=_BlockBase, components=components,
                        refs=(list[PinnedRef if stored else RefText], Field(default_factory=list)))


class _PayloadBase(StrictModel):
    @model_validator(mode="after")
    def component_ids_are_unique_within_the_object(self) -> "_PayloadBase":
        given = [component.id for block in vars(self.blocks).values() if block is not None
                 for component in block.components if component.id is not None]
        if len(given) != len(set(given)):
            raise ValueError("a component id is unique within its object")
        return self


def _written_by_client(relation: dict[str, Any]) -> bool:
    return relation["written_by"] == "world_create_object"


@lru_cache(maxsize=None)
def _model(object_type: str, stored: bool) -> type[BaseModel]:
    spec = world_registry.object_spec(object_type)
    prefix = object_type + ("V02Stored" if stored else "V02Input")
    blocks = create_model(prefix + "Blocks", __config__=StrictModel.model_config,
                          **{block["id"]: (Optional[_block_model(tuple(block["components"]), stored)], None)
                             for block in spec["blocks"]})
    fields: dict[str, Any] = {}
    for attribute in spec["attributes"]:
        if attribute["set_by"] is None:
            fields.update(_attribute_fields([attribute]))
        elif stored:
            fields[attribute["id"]] = (Optional[_value_type(attribute)], False if attribute["value"] == "boolean" else None)
    for relation in spec["relation_fields"]:
        if not stored and not _written_by_client(relation):
            continue
        ref_type = PinnedObjectRef if stored else _relation_ref(relation)
        if relation["many"]:
            fields[relation["field"]] = (list[ref_type], Field(default_factory=list))
        elif relation["required"]:
            fields[relation["field"]] = (ref_type, ...)
        else:
            fields[relation["field"]] = (Optional[ref_type], None)
    fields["blocks"] = (blocks, Field(default_factory=blocks))
    if stored:
        fields["component_ledger"] = (list[LedgerEntry], Field(default_factory=list))
    return create_model(prefix + "Payload", __base__=_PayloadBase, **fields)


def validate_input(object_type: str, payload: Any) -> dict[str, Any]:
    """按类型严格校验客户端写入的载荷，引用保持业务形式；违反契约抛 ValueError。"""
    return _model(object_type, False).model_validate(payload).model_dump(mode="json")


def listed(value: Any) -> list[Any]:
    """关系引用字段的值（单个、列表或空）一律当列表处理。"""
    return value if isinstance(value, list) else [value] if value else []


def ref_texts(object_type: str, payload: dict[str, Any]) -> list[str]:
    """写入载荷里出现的全部引用：关系字段，然后逐块的组件（引用、适用范围）与块内引用，按出现顺序。"""
    spec = world_registry.object_spec(object_type)
    texts = [text for relation in spec["relation_fields"] for text in listed(payload.get(relation["field"]))]
    for block in payload["blocks"].values():
        if block is None:
            continue
        for component in block["components"]:
            texts.extend(component["refs"])
            if component["scope"]:
                texts.append(component["scope"])
        texts.extend(block["refs"])
    return texts


def ledger_after(ledger: list[dict[str, Any]], blocks: dict[str, Any], version: int) -> list[dict[str, Any]]:
    """这一版之后的组件台账：原有的行保留，不在这一版里的记为这一版删除，新出现的 id 按块与出现顺序追加。"""
    present = {component["id"]: (block_id, component["type"]) for block_id, block in blocks.items() if block
               for component in block["components"]}
    known = {entry["id"] for entry in ledger}
    after = [{**entry, "removed_in_version": version} if entry["removed_in_version"] is None
             and entry["id"] not in present else entry for entry in ledger]
    return after + [{"id": cid, "type": ctype, "block": block_id, "added_in_version": version, "removed_in_version": None}
                    for cid, (block_id, ctype) in present.items() if cid not in known]


def stored_payload(object_type: str, payload: dict[str, Any], pins: dict[str, dict[str, Any]], *, version: int,
                   ledger: list[dict[str, Any]] | None = None, server: dict[str, Any] | None = None) -> dict[str, Any]:
    """把写入载荷里的引用换成钉定结果，给没有 id 的组件生成 id，更新组件台账，只由服务写的字段取 server
    （修订时沿用当前版本）或默认值，按存储模型校验后返回。"""
    spec = world_registry.object_spec(object_type)
    value = dict(payload)
    for relation in spec["relation_fields"]:
        field = relation["field"]
        if isinstance(value.get(field), list):
            value[field] = [pins[text] for text in value[field]]
        elif value.get(field):
            value[field] = pins[value[field]]

    def component(item: dict[str, Any]) -> dict[str, Any]:
        return {**item, "id": item["id"] or str(uuid4()), "scope": item["scope"] and pins[item["scope"]],
                "refs": [pins[text] for text in item["refs"]]}
    value["blocks"] = {block_id: block and {**block, "components": [component(item) for item in block["components"]],
                                            "refs": [pins[text] for text in block["refs"]]}
                       for block_id, block in payload["blocks"].items()}
    value["component_ledger"] = ledger_after(ledger or [], value["blocks"], version)
    value.update(server or {})
    return stored(object_type, value)


def stored(object_type: str, value: dict[str, Any]) -> dict[str, Any]:
    """按存储模型校验一份存储载荷。"""
    return _model(object_type, True).model_validate(value).model_dump(mode="json")


# ------------------------------------------------------------------ revision (契约第 12 节)
def server_owned(object_type: str) -> list[str]:
    """只由服务写的字段：由事件写的属性，与客户端不写的关系字段（只经 world_relate 写的）。"""
    spec = world_registry.object_spec(object_type)
    return ([attribute["id"] for attribute in spec["attributes"] if attribute["set_by"] is not None]
            + [relation["field"] for relation in spec["relation_fields"] if not _written_by_client(relation)])


def server_fields(object_type: str, value: dict[str, Any]) -> dict[str, Any]:
    return {field: value[field] for field in server_owned(object_type)}


def written_form(object_type: str, value: dict[str, Any]) -> dict[str, Any]:
    """存储载荷中客户端可写的部分，钉定引用还原成业务形式；合并修订从它出发。"""
    spec = world_registry.object_spec(object_type)
    owned = {*server_owned(object_type), "component_ledger"}
    written = {key: item for key, item in value.items() if key not in owned}
    for relation in spec["relation_fields"]:
        field = relation["field"]
        if isinstance(written.get(field), list):
            written[field] = [cite(pin) for pin in written[field]]
        elif written.get(field):
            written[field] = cite(written[field])

    def component(item: dict[str, Any]) -> dict[str, Any]:
        return {**item, "scope": item["scope"] and cite(item["scope"]), "refs": [cite(pin) for pin in item["refs"]]}
    written["blocks"] = {block_id: block and {**block, "components": [component(item) for item in block["components"]],
                                              "refs": [cite(pin) for pin in block["refs"]]}
                         for block_id, block in value["blocks"].items()}
    return written


_EMPTY_BLOCK = {"text": "", "components": [], "refs": [], "artifacts": []}


def _merged_components(current: list[dict[str, Any]], given: Any) -> list[dict[str, Any]]:
    """组件按 id 合并：给出的 id 改写（整条替换、位置不变），{"id": …, "removed": true} 删除，
    新组件追加在后，未提到的保留。"""
    if not isinstance(given, list):
        raise ValueError("the components of a block patch are a list")
    merged, positions = list(current), {item["id"]: index for index, item in enumerate(current)}
    seen: set[str] = set()
    removed: set[str] = set()
    for item in given:
        if not isinstance(item, dict):
            raise ValueError("a component in a block patch is an object")
        cid = item.get("id")
        if cid is not None:
            if not isinstance(cid, str) or cid in seen:
                raise ValueError("a revision names a component id once, as text")
            seen.add(cid)
        if "removed" in item:
            if set(item) != {"id", "removed"} or item["removed"] is not True:
                raise ValueError('a removal is exactly {"id": <component id>, "removed": true}')
            if cid not in positions:
                raise ValueError("a removal names a component present in that block")
            removed.add(cid)
        elif cid in positions:
            merged[positions[cid]] = item
        else:
            merged.append(item)
    return [item for item in merged if item.get("id") not in removed]


def _merged_block(current: dict[str, Any] | None, given: Any) -> dict[str, Any] | None:
    """块补丁按字段合并：text、refs、artifacts 给出即整体替换，components 按 id 合并；null 清空；
    合并后什么都没有的块存 null（契约第 4 节）。"""
    if given is None:
        return None
    if not isinstance(given, dict):
        raise ValueError("a block patch is an object or null")
    block = {**(current or _EMPTY_BLOCK), **{key: item for key, item in given.items() if key != "components"}}
    if "components" in given:
        block["components"] = _merged_components(block["components"], given["components"])
    if (set(block) == set(_EMPTY_BLOCK) and isinstance(block["text"], str) and not block["text"].strip()
            and block["components"] == block["refs"] == block["artifacts"] == []):
        return None
    return block


def _check_ledger(ledger: list[dict[str, Any]], blocks: dict[str, Any]) -> None:
    """对着组件台账：删除后 id 不复用，组件不跨块移动（换块即删除再新增），同一 id 不换类型（契约第 4 节）。"""
    entries = {entry["id"]: entry for entry in ledger}
    for block_id, block in blocks.items():
        for item in (block["components"] if isinstance(block, dict) else []):
            entry = entries.get(item.get("id"))
            if entry is None:
                continue
            if entry["removed_in_version"] is not None:
                raise ValueError("a removed component id is not reused")
            if entry["block"] != block_id:
                raise ValueError("a component does not move between blocks; remove it and add a new one")
            if entry["type"] != item.get("type"):
                raise ValueError("a component keeps its type")


def merge_revision(object_type: str, value: dict[str, Any], patch: Any) -> dict[str, Any]:
    """合并修订（契约第 12 节）：只改给出的字段与块，块内按字段合并、组件按 id 合并，对着台账核对组件 id；
    合并结果按写入模型校验，违反契约抛 ValueError。"""
    if not isinstance(patch, dict) or not isinstance(patch.get("blocks", {}), dict):
        raise ValueError("a revision patch is an object, and its blocks, if given, an object")
    current = written_form(object_type, value)
    blocks = {**current["blocks"], **{block_id: _merged_block(current["blocks"].get(block_id), given)
                                      for block_id, given in patch.get("blocks", {}).items()}}
    _check_ledger(value["component_ledger"], blocks)
    return validate_input(object_type, {**current, **{key: item for key, item in patch.items() if key != "blocks"},
                                        "blocks": blocks})


def touches_formal(object_type: str, patch: dict[str, Any]) -> bool:
    """补丁里出现了正式块、正式属性或建对象时写的关系字段（契约第 3.2、3.3、9.3 节），不论值变没变。"""
    spec = world_registry.object_spec(object_type)
    formal = {attribute["id"] for attribute in spec["attributes"] if attribute["class"] == "formal"}
    formal |= {relation["field"] for relation in spec["relation_fields"]}
    formal_blocks = {block["id"] for block in spec["blocks"] if block["class"] == "formal"}
    return (any(key in formal for key in patch if key != "blocks")
            or any(block_id in formal_blocks for block_id in patch.get("blocks", {})))


# ------------------------------------------------------------------ candidate and write-back (契约第 12 节)
def _formal_fields(object_type: str) -> list[str]:
    """随正式块走门、客户端可写的顶层字段：正式属性与建对象时写的关系字段（含周期目标的依据复盘；只经 world_relate
    写的关系不在内，它们只由服务写）。"""
    spec = world_registry.object_spec(object_type)
    return ([attribute["id"] for attribute in spec["attributes"]
             if attribute["class"] == "formal" and attribute["set_by"] is None]
            + [relation["field"] for relation in spec["relation_fields"] if _written_by_client(relation)])


def _formal_blocks(object_type: str) -> list[str]:
    return [block["id"] for block in world_registry.object_spec(object_type)["blocks"] if block["class"] == "formal"]


def check_candidate(object_type: str, patch: Any) -> None:
    """候选是合并补丁，只含正式块、正式属性与建对象时写的关系：活动块与活动属性直接修订、不走候选，只由服务写的
    字段不经候选写。违反抛 ValueError。"""
    if not isinstance(patch, dict) or not isinstance(patch.get("blocks", {}), dict):
        raise ValueError("a candidate is a revision patch: an object, and its blocks, if given, an object")
    others = ([key for key in patch if key != "blocks" and key not in _formal_fields(object_type)]
              + [block for block in patch.get("blocks", {}) if block not in _formal_blocks(object_type)])
    if others:
        raise ValueError(f"a candidate carries only formal blocks and formal attributes, not {', '.join(others)}")


def with_component_ids(patch: Any) -> Any:
    """候选里没有 id 的新组件先定下 id（UUID 文本）再留存：同一份候选无论写回几次，组件都是同一个 id。
    给了 id 的、删除标记与形状不对的原样留给合并去判。"""
    if not isinstance(patch, dict) or not isinstance(patch.get("blocks"), dict):
        return patch

    def component(item: Any) -> Any:
        if isinstance(item, dict) and item.get("id") is None and "removed" not in item:
            return {**item, "id": str(uuid4())}
        return item
    blocks = {block_id: {**block, "components": [component(item) for item in block["components"]]}
              if isinstance(block, dict) and isinstance(block.get("components"), list) else block
              for block_id, block in patch["blocks"].items()}
    return {**patch, "blocks": blocks}


def written_back(object_type: str, base: dict[str, Any], patch: Any, latest: dict[str, Any]) -> dict[str, Any]:
    """写回的内容（契约第 12 节，补 33）：候选是对它所钉的修订 base 的合并补丁；正式块、正式属性与建对象时写的关系
    取 base 合并候选的结果，活动块与活动属性取写回时最新修订 latest 的当前值（只由服务写的字段在存储时另取
    latest 的当前值）。组件按 latest 的台账核对（删除后不复用、不换块、不换类型）。返回写入形式；违反抛 ValueError。"""
    check_candidate(object_type, patch)
    proposed = merge_revision(object_type, base, patch)
    written = {**written_form(object_type, latest), **{field: proposed[field] for field in _formal_fields(object_type)}}
    written["blocks"] = {**written["blocks"], **{block: proposed["blocks"][block] for block in _formal_blocks(object_type)}}
    _check_ledger(latest["component_ledger"], written["blocks"])
    return validate_input(object_type, written)


# ------------------------------------------------------------ state snapshots
def payload_spec(payload_type: str) -> dict[str, Any]:
    return next(item for item in world_registry.registry()["state"]["payload_types"] if item["id"] == payload_type)


def payload_type_for(object_type: str) -> str | None:
    """主体类型登记的那一种 payload（契约第 7 节）；不是业务对象的没有。"""
    return next((item["id"] for item in world_registry.registry()["state"]["payload_types"]
                 if object_type in item["subjects"]), None)


@lru_cache(maxsize=None)
def _snapshot_model(payload_type: str, stored: bool) -> type[BaseModel]:
    """快照外壳加这种 payload 的块（契约第 7 节）。写入模型不收生成者：它由服务按凭证填。"""
    spec = payload_spec(payload_type)
    name = "".join(part.title() for part in payload_type.split("_")) + ("Stored" if stored else "Input")
    blocks = create_model(name + "Blocks", __config__=StrictModel.model_config,
                          **{block["id"]: (Optional[_block_model(tuple(block["components"]), stored)], None)
                             for block in spec["blocks"]})
    fields: dict[str, Any] = {
        "title": (NEStr, ...),
        "subject_ref": (PinnedObjectRef if stored else ObjectRefText, ...),
        "as_of": (Timestamp, ...), "period": (Optional[Month], None),
        "payload_type": (Literal[payload_type], ...),
        "source_event_refs": (list[PinnedEventRef if stored else EventRefText], Field(min_length=1)),
        "blocks": (blocks, Field(default_factory=blocks)),
    }
    if stored:
        fields["generator"] = (CanonicalUUID, ...)
    return create_model(name + "Snapshot", __base__=_PayloadBase, **fields)


def validate_snapshot(payload: Any) -> dict[str, Any]:
    """按 payload 类型严格校验客户端写的快照，引用保持业务形式；违反契约抛 ValueError。"""
    payload_type = payload.get("payload_type") if isinstance(payload, dict) else None
    if payload_type not in {item["id"] for item in world_registry.registry()["state"]["payload_types"]}:
        raise ValueError("payload_type is not a registered state payload type")
    value = _snapshot_model(payload_type, False).model_validate(payload).model_dump(mode="json")
    if len(set(value["source_event_refs"])) != len(value["source_event_refs"]):
        raise ValueError("a snapshot names each source event once")
    return value


def snapshot_ref_texts(written: dict[str, Any]) -> list[str]:
    """快照里出现的全部引用：主体、来源事件，然后逐块的组件与块内引用，按出现顺序。"""
    return [written["subject_ref"], *written["source_event_refs"], *ref_texts("StateSnapshot", written)]


def stored_snapshot(written: dict[str, Any], pins: dict[str, dict[str, Any]], *, generator: str) -> dict[str, Any]:
    """把快照里的引用换成钉定结果，给没有 id 的组件生成 id，写上生成者，按存储模型校验后返回。
    快照不修订，所以不维护组件台账。"""
    def component(item: dict[str, Any]) -> dict[str, Any]:
        return {**item, "id": item["id"] or str(uuid4()), "scope": item["scope"] and pins[item["scope"]],
                "refs": [pins[text] for text in item["refs"]]}
    value = {**written, "generator": generator, "subject_ref": pins[written["subject_ref"]],
             "source_event_refs": [pins[text] for text in written["source_event_refs"]],
             "blocks": {block_id: block and {**block, "components": [component(item) for item in block["components"]],
                                             "refs": [pins[text] for text in block["refs"]]}
                        for block_id, block in written["blocks"].items()}}
    return _snapshot_model(written["payload_type"], True).model_validate(value).model_dump(mode="json")


class HumanAcceptance(StrictModel):
    required: StrictBool
    acceptor: Optional[CanonicalUUID] = None

    @model_validator(mode="after")
    def acceptor_iff_required(self) -> "HumanAcceptance":
        if self.required != (self.acceptor is not None):
            raise ValueError("name an acceptor exactly when human acceptance is required")
        return self


class Declaration(StrictModel):
    """写入声明三项（契约第 9.3 节）：场景是任一业务对象的对象形式引用、触发事件、是否人工验收及验收人。"""
    scene: ObjectRefText
    trigger: NEStr
    human_acceptance: HumanAcceptance


class OnBehalfOf(StrictModel):
    """代记（契约第 14 节）：被代记的人、外部系统里的记录 id、外部确认时刻；确认时刻不晚于记录时刻，在服务里判。
    只有登记列在代记族里的动作带它，代记写入不带写入声明。"""
    principal_id: CanonicalUUID
    external_record_id: ShortText
    external_confirmed_at: Timestamp


class WorldV02CreateObjectParams(StrictModel):
    """建对象。写入声明只对 Agent 强制，人带了按同样规则校验；Agent 不在建对象的 Agent 面上。"""
    domain_id: CanonicalUUID
    object_type: NEStr
    payload: dict[str, Any]
    declaration: Optional[Declaration] = None

    @field_validator("object_type")
    @classmethod
    def registered_type(cls, value: str) -> str:
        if value not in world_registry.object_types():
            raise ValueError(f"{value} is not a world 0.2 object type")
        return value


class WorldV02ReviseObjectParams(StrictModel):
    """合并修订的补丁：按类型的校验在服务里对合并结果做（契约第 12 节）。"""
    payload: dict[str, Any]
    declaration: Optional[Declaration] = None


class WorldV02RelateParams(StrictModel):
    """整体替换一个跨链关系字段的列表（契约第 6 节）；列表里的引用指向对象本身，同一对象只出现一次。"""
    field: Literal["depends_on", "contributes_to"]
    refs: list[ObjectRefText]
    declaration: Optional[Declaration] = None

    @field_validator("refs")
    @classmethod
    def distinct_objects(cls, refs: list[str]) -> list[str]:
        if len({parse_ref(text)["object_id"] for text in refs}) != len(refs):
            raise ValueError("a relation list names each object once")
        return refs


class WorldV02RefreshStateParams(StrictModel):
    """写状态快照：载荷是外壳加 payload，按 payload 类型在服务里校验；所在域随主体（契约第 7 节）。"""
    payload: dict[str, Any]
    declaration: Optional[Declaration] = None


# 外部事件的类别与可以补记的事件种类（契约第 8.2、11 节）。请求模型在导入时就要，由测试与登记逐条对齐。
EVENT_CATEGORIES = ("meeting", "review", "delivery", "acceptance", "other", "correction")
BACKDATED_KINDS = frozenset({"event.recorded", "state.refreshed"})


class WorldV02RecordEventParams(StrictModel):
    """记外部事件（契约第 8 节）：category 必带，可以补记过去的 occurred_at；更正且只有更正以
    supersedes_event_id 指向被更正的事件。主体是对象或组件形式，逐条不重复；内容是不带组件的块值。"""
    category: Literal[EVENT_CATEGORIES]
    subject_refs: list[SubjectRefText] = Field(min_length=1)
    occurred_at: Timestamp
    content: _block_model((), False)
    supersedes_event_id: Optional[CanonicalUUID] = None
    declaration: Optional[Declaration] = None

    @field_validator("subject_refs")
    @classmethod
    def distinct_subjects(cls, refs: list[str]) -> list[str]:
        if len(set(refs)) != len(refs):
            raise ValueError("an event names each subject once")
        return refs

    @model_validator(mode="after")
    def correction_references_its_original(self) -> "WorldV02RecordEventParams":
        if (self.category == "correction") != (self.supersedes_event_id is not None):
            raise ValueError("a correction, and only a correction, references the event it corrects")
        return self


class WorldV02AssignParams(StrictModel):
    """指派（契约第 9.1 节）：只给被指派者；生效时间是事件的发生时刻。可以代记（指派族）。"""
    principal_id: CanonicalUUID
    on_behalf_of: Optional[OnBehalfOf] = None


class WorldV02LifecycleParams(StrictModel):
    """六个通用生命周期动作（契约第 9.1、11 节）：目标是对象；可选内容；撤回带 outcome 与原事件。
    写入声明只对 Agent 强制（开始、交付在 Agent 面上），人带了按同样规则校验；代记（生命周期族）不带声明。"""
    content: Optional[_block_model((), False)] = None
    outcome: Optional[Literal["withdrawn"]] = None
    supersedes_event_id: Optional[CanonicalUUID] = None
    declaration: Optional[Declaration] = None
    on_behalf_of: Optional[OnBehalfOf] = None

    @model_validator(mode="after")
    def withdrawal_names_the_original(self) -> "WorldV02LifecycleParams":
        if (self.outcome is None) != (self.supersedes_event_id is None):
            raise ValueError("a withdrawal carries outcome withdrawn and the event it withdraws, and only it does")
        if self.on_behalf_of is not None and self.declaration is not None:
            raise ValueError("a write on behalf of a person carries no declaration")
        return self


LIFECYCLE_ACTIONS = ("world_start", "world_deliver", "world_accept", "world_reject", "world_reopen", "world_cancel")


class _GateParams(StrictModel):
    """门动作（契约第 9.1 节）：content 是事件内容（例如退回理由、候选稿的链接），撤回带 outcome withdrawn 并以
    supersedes_event_id 引用原事件。候选 payload 是合并补丁，只随承诺与长期目标接受的确认提交。门只由人记，
    不带写入声明；0.2 没有阶段。可以代记（门族）。"""
    content: Optional[_block_model((), False)] = None
    supersedes_event_id: Optional[CanonicalUUID] = None
    on_behalf_of: Optional[OnBehalfOf] = None

    @model_validator(mode="after")
    def withdrawal_names_the_original(self) -> "_GateParams":
        outcome = getattr(self, "outcome", None)
        if (outcome == "withdrawn") != (self.supersedes_event_id is not None):
            raise ValueError("a withdrawal, and only a withdrawal, references the event it withdraws")
        if getattr(self, "payload", None) is not None and outcome not in (None, "accepted"):
            raise ValueError("a candidate travels only with a commitment or an accepting confirmation")
        return self


class WorldV02CommitParams(_GateParams):
    """承诺（周期目标、Mission）：可带候选，也可不带（补 30）；只在撤回时带结果。"""
    outcome: Optional[Literal["withdrawn"]] = None
    payload: Optional[dict[str, Any]] = None


class WorldV02ConfirmParams(_GateParams):
    """确认（周期目标、Mission）：必带结果——接受、退回或撤回。"""
    outcome: Literal["accepted", "returned", "withdrawn"]


# 长期目标的确认与再确认可以带「返回 M1-A」的路由结果（契约第 10.2 节，补 23），写进事件的 detail.returns_to，只作记录。
ReturnsTo = Literal["strategy"]


class WorldV02ConfirmCandidateParams(WorldV02ConfirmParams):
    """长期目标的确认：没有承诺门，接受时可以带候选（第 10.2 节）；确认（接受或退回）可以注明返回 M1-A，撤回不是确认
    结果，不带它。"""
    payload: Optional[dict[str, Any]] = None
    returns_to: Optional[ReturnsTo] = None

    @model_validator(mode="after")
    def a_withdrawal_returns_nowhere(self) -> "WorldV02ConfirmCandidateParams":
        if self.returns_to is not None and self.outcome == "withdrawn":
            raise ValueError("a withdrawal carries no returns_to")
        return self


class WorldV02ReconfirmParams(StrictModel):
    """再确认（周期目标、Strategy 的继续有效；契约第 10.1、10.3、12 节）：不带候选、不出新修订、没有结果，也不能撤回
    （登记 rules.withdrawal.never 的 reconfirm）；可选内容写进事件。门只由人记，不带写入声明；可以代记（门族）。"""
    content: Optional[_block_model((), False)] = None
    on_behalf_of: Optional[OnBehalfOf] = None


class WorldV02ReconfirmLongTermGoalParams(WorldV02ReconfirmParams):
    """长期目标的再确认（保持）：同周期目标的再确认，另可注明返回 M1-A（补 23）。"""
    returns_to: Optional[ReturnsTo] = None


class WorldV02ConfirmReviewParams(_GateParams):
    """复盘确认（契约第 7、10.3、11 节）：目标是被确认的状态快照，不带候选；只在撤回时带结果。以周期目标为主体的复盘
    确认可以撤回，公司复盘的确认不能（服务按登记判）。"""
    outcome: Optional[Literal["withdrawn"]] = None


class WorldV02AssignStrategyRoundParams(StrictModel):
    """指定本轮责任人（契约第 9.1、10.1 节）：至少一人、各不相同，是不是 scope 内有效的人在服务里判；已生效时开轮
    可带候选（合并补丁，只含正式块与正式属性）。它是记录事件，不撤回、不带事件内容；可以代记（指派族）。"""
    principal_ids: list[CanonicalUUID] = Field(min_length=1)
    payload: Optional[dict[str, Any]] = None
    on_behalf_of: Optional[OnBehalfOf] = None

    @field_validator("principal_ids")
    @classmethod
    def distinct(cls, values: list[str]) -> list[str]:
        if len(set(values)) != len(values):
            raise ValueError("a round designates each person once")
        return values


class WorldV02AgreeParams(_GateParams):
    """Agreement（契约第 9.1、10.1 节）：内容是记录者的判断（可选）；只在撤回时带结果。所同意的内容由服务钉住，
    不带候选。"""
    outcome: Optional[Literal["withdrawn"]] = None


class WorldV02MarkCoreBattleParams(StrictModel):
    """关注标记（契约第 9.1、10.4 节，方案 A）：目标是 Mission，可选内容写进事件（例如关注的理由）。它是记录事件，
    不撤回、不带候选，core_battle 由服务置；只由人记，不带写入声明；可以代记（门族）。"""
    content: Optional[_block_model((), False)] = None
    on_behalf_of: Optional[OnBehalfOf] = None


# 代记的动作族，本版是门、指派、生命周期（补 38）与议题（补 49）；建对象不在本版（待决 6）。请求模型在导入时就要，由测试与登记逐条对齐。
DELEGATION_FAMILIES = ("gate", "assign", "lifecycle", "issue")
DELEGATION_ACTIONS = ("world_grant_delegation", "world_revoke_delegation")


class WorldV02GrantDelegationParams(StrictModel):
    """登记委托（契约第 14 节）：受托的服务主体（本 scope 的 Agent 主体，在服务里判）、动作族、域列表与必填的
    有效期；族与域各至少一个、不重复。不带 on_behalf_of：委托由委托人本人记，不可转委托。"""
    delegate_principal_id: CanonicalUUID
    families: list[Literal[DELEGATION_FAMILIES]] = Field(min_length=1)
    domain_ids: list[CanonicalUUID] = Field(min_length=1)
    valid_until: Timestamp

    @field_validator("families", "domain_ids")
    @classmethod
    def distinct(cls, values: list[str]) -> list[str]:
        if len(set(values)) != len(values):
            raise ValueError("a delegation names each family and each domain once")
        return values


class WorldV02RevokeDelegationParams(StrictModel):
    """撤销委托（契约第 14 节）：引用登记事件，即时生效。"""
    delegation_event_id: CanonicalUUID


# Issue 的五个动作与六类处置（契约第 9.1、13 节）。请求模型在导入时就要，由测试与登记逐条对齐。
ISSUE_ACTIONS = ("world_raise_issue", "world_route_issue", "world_own_issue", "world_dispose_issue",
                 "world_return_issue")
DISPOSITIONS = ("no_action_close", "current_layer_action", "roll_forward", "immediate_reopen", "route_escalate",
                "pushback")
IssueRefText = _ref_text("component")


class _IssueParams(StrictModel):
    """Issue 动作（契约第 9.1、13 节）：不带目标，以 issue_ref（状态快照 issues 块里问题组件的组件引用）指明问题；
    可选 content 写进事件。Issue 事件是记录事件，不撤回。承接、处置与退回形成可以代记（议题族，补 49），另带
    on_behalf_of；提出与路由不可代记。"""
    issue_ref: IssueRefText
    content: Optional[_block_model((), False)] = None


class WorldV02RaiseIssueParams(_IssueParams):
    """提出问题：在 Agent 面上，Agent 带写入声明（人带了按同样规则校验）。"""
    declaration: Optional[Declaration] = None


class WorldV02RouteIssueParams(_IssueParams):
    """路由问题：指定一名承接人（人，在服务里判）；已路由时可以改路由。在 Agent 面上。"""
    to_principal_id: CanonicalUUID
    declaration: Optional[Declaration] = None


class WorldV02OwnIssueParams(_IssueParams):
    """承接问题：路由指定的承接人本人记，只由人记、不在 Agent 面上，不带写入声明（同指派）；可以代记（议题族）。"""
    on_behalf_of: Optional[OnBehalfOf] = None


class WorldV02DisposeIssueParams(_IssueParams):
    """处置问题：六类之一，content 必带，最低理由写在它的文字里（补 35）；只由已承接的承接人本人记，不带写入声明；
    可以代记（议题族）。"""
    disposition: Literal[DISPOSITIONS]
    content: _block_model((), False)
    on_behalf_of: Optional[OnBehalfOf] = None

    @model_validator(mode="after")
    def states_its_reason(self) -> "WorldV02DisposeIssueParams":
        if not self.content.text.strip():
            raise ValueError("a disposition states its minimal reason in content.text")
        return self


class WorldV02ReturnIssueParams(_IssueParams):
    """退回形成：路由者或承接人记；在 Agent 面上（作为路由者）。可以代记（议题族），代记不带写入声明。"""
    declaration: Optional[Declaration] = None
    on_behalf_of: Optional[OnBehalfOf] = None

    @model_validator(mode="after")
    def on_behalf_carries_no_declaration(self) -> "WorldV02ReturnIssueParams":
        if self.on_behalf_of is not None and self.declaration is not None:
            raise ValueError("a write on behalf of a person carries no declaration")
        return self


# 已接入的门动作 -> 目标类型（门按目标类型拆名，ADR-0005）。关注标记是记录事件，但和门一样由持策略角色（CEO）的人
# 记，按目标类型拆名，所以也列在这里（#55）。再确认与复盘确认随票 #60：复盘确认的目标是状态快照。Strategy 的指定本轮
# 也是记录事件，但它开一轮、带候选，和门一起交给生命周期引擎判（#59）。
GATE_ACTIONS = {"world_commit_period_goal": "PeriodGoal", "world_confirm_period_goal": "PeriodGoal",
                "world_confirm_long_term_goal": "LongTermGoal", "world_commit_mission": "Mission",
                "world_confirm_mission": "Mission", "world_mark_core_battle": "Mission",
                "world_reconfirm_long_term_goal": "LongTermGoal", "world_reconfirm_period_goal": "PeriodGoal",
                "world_confirm_review": "StateSnapshot",
                "world_assign_strategy_round": "Strategy", "world_agree_strategy": "Strategy",
                "world_confirm_strategy": "Strategy", "world_reconfirm_strategy": "Strategy"}

# 本进程已实现的 0.2 动作与可建类型。支持登记只能在这之内收窄，不能扩大（与协议支持集合同理）。
ACTION_PARAMS = {"world_create_object": WorldV02CreateObjectParams, "world_revise_object": WorldV02ReviseObjectParams,
                 "world_relate": WorldV02RelateParams, "world_refresh_state": WorldV02RefreshStateParams,
                 "world_record_event": WorldV02RecordEventParams, "world_assign": WorldV02AssignParams,
                 **{action: WorldV02LifecycleParams for action in LIFECYCLE_ACTIONS},
                 "world_commit_period_goal": WorldV02CommitParams, "world_commit_mission": WorldV02CommitParams,
                 "world_confirm_period_goal": WorldV02ConfirmParams, "world_confirm_mission": WorldV02ConfirmParams,
                 "world_confirm_long_term_goal": WorldV02ConfirmCandidateParams,
                 "world_reconfirm_long_term_goal": WorldV02ReconfirmLongTermGoalParams,
                 "world_reconfirm_period_goal": WorldV02ReconfirmParams,
                 "world_confirm_review": WorldV02ConfirmReviewParams,
                 "world_mark_core_battle": WorldV02MarkCoreBattleParams,
                 "world_assign_strategy_round": WorldV02AssignStrategyRoundParams,
                 "world_agree_strategy": WorldV02AgreeParams, "world_confirm_strategy": WorldV02ConfirmParams,
                 "world_reconfirm_strategy": WorldV02ReconfirmParams,
                 "world_grant_delegation": WorldV02GrantDelegationParams,
                 "world_revoke_delegation": WorldV02RevokeDelegationParams,
                 "world_raise_issue": WorldV02RaiseIssueParams, "world_route_issue": WorldV02RouteIssueParams,
                 "world_own_issue": WorldV02OwnIssueParams, "world_dispose_issue": WorldV02DisposeIssueParams,
                 "world_return_issue": WorldV02ReturnIssueParams}
BUSINESS_TYPES = frozenset({"Company", "Strategy", "ResponsibilityUnit", "LongTermGoal", "PeriodGoal", "Mission",
                            "Task", "Activity"})
# 状态快照只经 world_refresh_state 写入，建对象在服务里先拒绝它。
CREATABLE = BUSINESS_TYPES | {"StateSnapshot"}
# 动作 -> 允许的目标类型；空集表示该动作不带 target。状态快照不修订，只有带跨链关系字段的类型能建关系。
# 生命周期动作接 Mission、Task 与 Activity（Mission 随票 #55）；取消另接长期目标与周期目标（票 #60）。
ACTION_TARGETS: dict[str, frozenset[str]] = {
    "world_create_object": frozenset(), "world_revise_object": BUSINESS_TYPES,
    "world_relate": frozenset({"PeriodGoal", "Mission", "Task"}),
    "world_refresh_state": frozenset(), "world_record_event": frozenset(),
    **{action: frozenset() for action in DELEGATION_ACTIONS},
    **{action: frozenset() for action in ISSUE_ACTIONS},  # Issue 以 issue_ref 指明问题，不带目标
    "world_assign": frozenset({"ResponsibilityUnit", "Mission", "Task", "Activity"}),
    **{action: frozenset({"Mission", "Task", "Activity"}) for action in LIFECYCLE_ACTIONS},
    "world_cancel": frozenset({"LongTermGoal", "PeriodGoal", "Mission", "Task", "Activity"}),
    **{action: frozenset({target}) for action, target in GATE_ACTIONS.items()},
}
