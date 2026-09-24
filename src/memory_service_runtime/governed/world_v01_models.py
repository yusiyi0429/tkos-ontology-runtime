"""tkos.world/0.1 的严格载荷模型，由随包登记生成，块清单不写死在代码里。

载荷形状：属性与关系引用字段在顶层，内容块在 ``blocks`` 下；登记里的每个块都
出现在存储的载荷中，空块存 null（契约第 3 节）。

每类有两个模型：写入模型只收客户端能写的字段，引用是业务形式的字符串；存储
模型含登记的全部字段，引用是钉定后的结构化对象。只由事件写入的属性（``set_by``
非空，例如 responsible、core_battle）与只经 world_relate 写的关系引用字段不进写入
模型，存储时取默认值。
"""
from __future__ import annotations

from datetime import datetime, timezone
from functools import lru_cache
import re
from typing import Annotated, Any, Literal, Optional

from pydantic import (AfterValidator, BaseModel, Field, StrictBool, StrictStr, StringConstraints, create_model,
                      field_validator, model_validator)

from . import world_v01_registry as world_registry
from .a2_models import CanonicalUUID, NEStr, PositiveInt, StrictModel

ArtifactUrl = Annotated[str, StringConstraints(pattern=r"^https?://\S+$", max_length=2048)]
BlockId = Annotated[str, StringConstraints(pattern=r"^[a-z][a-z0-9_]*$")]

# 引用的业务形式 `<对象 id>@<版本号>#<块路径>`（契约第 5 节），每个引用只有一种写法。
_REF = re.compile(r"^([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})@([1-9][0-9]*)(?:#([a-z][a-z0-9_]*))?$")
RefText = Annotated[str, StringConstraints(pattern=_REF.pattern)]


def parse_ref(text: str) -> dict[str, Any]:
    """把业务形式拆成对象 id、版本号与块路径；修订 id 由服务端在写入时解析并钉住。"""
    match = _REF.match(text)
    if match is None:
        raise ValueError("a reference is written as <object id>@<version>#<block>")
    return {"object_id": match[1], "object_version": int(match[2]), "block": match[3]}


def _ref_to(block: str | None):
    """指向对象本身（block 为 None）或指向登记规定的那个块的引用。"""
    def check(text: str) -> str:
        if parse_ref(text)["block"] != block:
            raise ValueError(f"this reference must point to {'the object itself' if block is None else '#' + block}")
        return text
    return Annotated[RefText, AfterValidator(check)]


class PinnedRef(StrictModel):
    """钉定后的引用：写入时把版本号解析成修订 id，此后不随被引用对象更新而漂移。"""
    object_id: CanonicalUUID
    object_version: PositiveInt
    revision_id: CanonicalUUID
    block: Optional[BlockId]


class _BlockBase(StrictModel):
    text: StrictStr = ""
    artifacts: list[ArtifactUrl] = Field(default_factory=list)

    @model_validator(mode="after")
    def carries_content(self) -> "_BlockBase":
        if not self.text.strip() and not self.refs and not self.artifacts:
            raise ValueError("a non-null block needs text, refs or artifacts; store an empty block as null")
        return self


class Block(_BlockBase):
    refs: list[RefText] = Field(default_factory=list)


class StoredBlock(_BlockBase):
    refs: list[PinnedRef] = Field(default_factory=list)


def _utc_text(value: str) -> str:
    """时间戳规范成 UTC 文本 `YYYY-MM-DDTHH:MM:SS[.ffffff]Z`，同一时刻只有一种写法（契约第 7 节）。"""
    moment = datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(timezone.utc)
    text = moment.strftime("%Y-%m-%dT%H:%M:%S")
    return text + (f".{moment.microsecond:06d}" if moment.microsecond else "") + "Z"


Month = Annotated[str, StringConstraints(pattern=r"^[0-9]{4}-(0[1-9]|1[0-2])$")]
Timestamp = Annotated[str, StringConstraints(
    pattern=r"^[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}(\.[0-9]{1,6})?(Z|[+-][0-9]{2}:[0-9]{2})$"),
    AfterValidator(_utc_text)]


def _value_type(attribute: dict[str, Any], *, stored: bool) -> Any:
    kind = attribute["value"]
    if kind == "text":
        return NEStr
    if kind == "enum":
        return Literal[tuple(value["id"] for value in attribute["values"])]
    if kind == "month":
        return Month
    if kind == "timestamp":
        return Timestamp
    if kind == "ref":
        return PinnedRef if stored else _ref_to(None)
    if kind == "boolean":
        return StrictBool
    if kind == "principal":
        return CanonicalUUID
    raise NotImplementedError(f"attribute value kind {kind} is not implemented")


_SERVER_DEFAULTS = {"boolean": False, "principal": None}


@lru_cache(maxsize=None)
def _model(object_type: str, stored: bool) -> type[BaseModel]:
    spec = world_registry.object_spec(object_type)
    prefix = object_type + ("Stored" if stored else "Input")
    block_type = StoredBlock if stored else Block
    blocks = create_model(prefix + "Blocks", __config__=StrictModel.model_config,
                          **{block["id"]: (Optional[block_type], None) for block in spec["blocks"]})
    fields: dict[str, Any] = {}
    for attribute in spec["attributes"]:
        value_type = _value_type(attribute, stored=stored)
        if attribute["set_by"] is not None:
            if stored:
                fields[attribute["id"]] = (Optional[value_type], _SERVER_DEFAULTS[attribute["value"]])
        elif attribute["required"]:
            fields[attribute["id"]] = (value_type, ...)
        else:
            fields[attribute["id"]] = (Optional[value_type], None)
    for relation in spec["relation_fields"]:
        if not stored and relation["written_by"] != "world_create_object":
            continue
        ref_type = PinnedRef if stored else _ref_to(relation["target_block"])
        if relation["many"]:
            fields[relation["field"]] = (list[ref_type], Field(default_factory=list))
        elif relation["required"]:
            fields[relation["field"]] = (ref_type, ...)
        else:
            fields[relation["field"]] = (Optional[ref_type], None)
    fields["blocks"] = (blocks, Field(default_factory=blocks))
    return create_model(prefix + "Payload", __config__=StrictModel.model_config, **fields)


def input_model(object_type: str) -> type[BaseModel]:
    return _model(object_type, False)


def stored_model(object_type: str) -> type[BaseModel]:
    return _model(object_type, True)


def validate_input(object_type: str, payload: Any) -> dict[str, Any]:
    """按类型严格校验客户端写入的载荷，引用保持业务形式；违反契约抛 ValueError。"""
    return input_model(object_type).model_validate(payload).model_dump(mode="json")


def listed(value: Any) -> list[Any]:
    """关系引用字段的值（单个、列表或空）一律当列表处理。"""
    return value if isinstance(value, list) else [value] if value else []


def ref_texts(object_type: str, payload: dict[str, Any]) -> list[str]:
    """写入载荷里出现的全部引用（关系引用字段、ref 属性、块内引用），按出现顺序。"""
    spec = world_registry.object_spec(object_type)
    texts = [payload[a["id"]] for a in spec["attributes"] if a["value"] == "ref" and payload.get(a["id"])]
    for relation in spec["relation_fields"]:
        texts.extend(listed(payload.get(relation["field"])))
    for block in payload["blocks"].values():
        texts.extend(block["refs"] if block else [])
    return texts


def stored_payload(object_type: str, payload: dict[str, Any], pins: dict[str, dict[str, Any]]) -> dict[str, Any]:
    """把写入载荷里的引用换成钉定结果，补上只由服务写的字段，按存储模型校验后返回。"""
    spec = world_registry.object_spec(object_type)
    value = dict(payload)
    for attribute in spec["attributes"]:
        if attribute["value"] == "ref" and value.get(attribute["id"]):
            value[attribute["id"]] = pins[value[attribute["id"]]]
    for relation in spec["relation_fields"]:
        field = relation["field"]
        if isinstance(value.get(field), list):
            value[field] = [pins[text] for text in value[field]]
        elif value.get(field):
            value[field] = pins[value[field]]
    value["blocks"] = {block_id: block and {**block, "refs": [pins[text] for text in block["refs"]]}
                       for block_id, block in payload["blocks"].items()}
    return stored_model(object_type).model_validate(value).model_dump(mode="json")


CONTRACT_VERSION = "tkos.world/0.1"


class HumanAcceptance(StrictModel):
    required: StrictBool
    acceptor: Optional[CanonicalUUID] = None

    @model_validator(mode="after")
    def acceptor_iff_required(self) -> "HumanAcceptance":
        if self.required != (self.acceptor is not None):
            raise ValueError("name an acceptor exactly when human acceptance is required")
        return self


class Declaration(StrictModel):
    """写入声明三项（契约第 9 节）：场景（所属 Mission 或 Task）、触发事件、是否人工验收及验收人。"""
    scene: _ref_to(None)
    trigger: NEStr
    human_acceptance: HumanAcceptance


class WorldCreateObjectParams(StrictModel):
    domain_id: CanonicalUUID
    object_type: NEStr
    payload: dict[str, Any]
    declaration: Optional[Declaration] = None

    @field_validator("object_type")
    @classmethod
    def registered_type(cls, value: str) -> str:
        if value not in world_registry.object_types():
            raise ValueError(f"{value} is not a world object type")
        return value


ACTION_PARAMS = {"world_create_object": WorldCreateObjectParams}
# 动作 -> 允许的目标类型；空集表示该动作不带 target。
ACTION_TARGETS: dict[str, frozenset[str]] = {"world_create_object": frozenset()}
