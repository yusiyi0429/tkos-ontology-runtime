"""tkos.world/0.2 的严格载荷与参数模型，由随包的 0.2 登记生成，块清单与组件类型不写死在代码里。

载荷形状同 0.1：属性与关系引用字段在顶层，内容块在 ``blocks`` 下，登记里的每个块都出现，空块存 null。
块值是 0.2 的四个字段 ``{text, components, refs, artifacts}``（契约第 4 节）；组件的类型须是所在块
允许的，类型属性按登记，id 在对象内唯一。

每类有两个模型：写入模型只收客户端能写的字段，引用是业务形式的字符串，组件 id 可以不给；存储模型含
登记的全部字段，引用是钉定后的结构，组件都有 id，另有服务维护的组件台账 ``component_ledger``。
只由事件写的属性、只经 world_relate 写的关系字段与周期目标的 ``review_ref``（票 #60）不进写入模型。
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
    """外部系统与本体对象的对照（契约第 3.4 节）；scope 内按 (system, id) 唯一在票 #63。"""
    system: ShortText
    id: ShortText
    url: Optional[ArtifactUrl] = None


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
        return list[ExternalRef]
    raise NotImplementedError(f"world 0.2 attribute value kind {kind} is not implemented yet")


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
    # 依据复盘（review_ref）随票 #60 开放。
    return relation["written_by"] == "world_create_object" and relation["relation"] != "based_on_review"


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
                   ledger: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    """把写入载荷里的引用换成钉定结果，给没有 id 的组件生成 id，更新组件台账，按存储模型校验后返回。"""
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
    return _model(object_type, True).model_validate(value).model_dump(mode="json")


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


# 本进程已实现的 0.2 动作与可建类型。支持登记只能在这之内收窄，不能扩大（与协议支持集合同理）。
ACTION_PARAMS = {"world_create_object": WorldV02CreateObjectParams}
# 动作 -> 允许的目标类型；空集表示该动作不带 target。
ACTION_TARGETS: dict[str, frozenset[str]] = {"world_create_object": frozenset()}
CREATABLE = frozenset({"Company", "Strategy", "ResponsibilityUnit", "LongTermGoal", "PeriodGoal", "Mission", "Task",
                       "Activity"})
