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


def citation(object_id: str, version: int, block: str | None = None) -> str:
    """引用的业务形式。"""
    return f"{object_id}@{version}" + (f"#{block}" if block else "")


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


def utc_text(value: str) -> str:
    """时间戳规范成 UTC 文本 `YYYY-MM-DDTHH:MM:SS[.ffffff]Z`，同一时刻只有一种写法（契约第 7 节）。"""
    moment = datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(timezone.utc)
    text = moment.strftime("%Y-%m-%dT%H:%M:%S")
    return text + (f".{moment.microsecond:06d}" if moment.microsecond else "") + "Z"


Month = Annotated[str, StringConstraints(pattern=r"^[0-9]{4}-(0[1-9]|1[0-2])$")]
Timestamp = Annotated[str, StringConstraints(
    pattern=r"^[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}(\.[0-9]{1,6})?(Z|[+-][0-9]{2}:[0-9]{2})$"),
    AfterValidator(utc_text)]


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


def server_owned(object_type: str) -> list[str]:
    """只由服务写的字段：由事件写的属性与只经 world_relate 写的关系引用字段。"""
    spec = world_registry.object_spec(object_type)
    return ([a["id"] for a in spec["attributes"] if a["set_by"] is not None]
            + [f["field"] for f in spec["relation_fields"] if f["written_by"] != "world_create_object"])


def server_fields(object_type: str, stored: dict[str, Any]) -> dict[str, Any]:
    return {field: stored[field] for field in server_owned(object_type)}


def written_form(object_type: str, stored: dict[str, Any]) -> dict[str, Any]:
    """存储载荷中客户端可写的部分，钉定引用还原成业务形式；合并修订从它出发。"""
    spec = world_registry.object_spec(object_type)
    owned = server_owned(object_type)
    value = {key: item for key, item in stored.items() if key not in owned}

    def text(pinned: dict[str, Any]) -> str:
        return citation(pinned["object_id"], pinned["object_version"], pinned["block"])
    fields = [a["id"] for a in spec["attributes"] if a["value"] == "ref"] + [f["field"] for f in spec["relation_fields"]]
    for field in fields:
        if value.get(field):
            value[field] = text(value[field])
    value["blocks"] = {block_id: block and {**block, "refs": [text(ref) for ref in block["refs"]]}
                       for block_id, block in stored["blocks"].items()}
    return value


def merge_revision(object_type: str, stored: dict[str, Any], patch: Any) -> dict[str, Any]:
    """合并修订（契约第 11 节）：只改给出的字段与块，块给 null 即清空，其余沿用当前版本；校验同建对象。"""
    if not isinstance(patch.get("blocks", {}), dict):
        raise ValueError("the blocks of a revision patch, if given, are an object")
    current = written_form(object_type, stored)
    merged = {**current, **{key: item for key, item in patch.items() if key != "blocks"},
              "blocks": {**current["blocks"], **patch.get("blocks", {})}}
    return validate_input(object_type, merged)


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


def stored_payload(object_type: str, payload: dict[str, Any], pins: dict[str, dict[str, Any]],
                   server: dict[str, Any] | None = None) -> dict[str, Any]:
    """把写入载荷里的引用换成钉定结果，只由服务写的字段取 server（修订时沿用当前版本）或默认值，
    按存储模型校验后返回。"""
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
    value.update(server or {})
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


class WorldReviseObjectParams(StrictModel):
    """合并修订的补丁：按类型的校验在服务里对合并结果做（契约第 11 节）。"""
    payload: dict[str, Any]
    declaration: Optional[Declaration] = None


class WorldRelateParams(StrictModel):
    """整体替换一个跨链关系字段的列表（契约第 6 节）；列表里的引用指向对象本身，同一对象只出现一次。"""
    field: Literal["depends_on", "contributes_to"]
    refs: list[_ref_to(None)]
    declaration: Optional[Declaration] = None

    @field_validator("refs")
    @classmethod
    def distinct_objects(cls, refs: list[str]) -> list[str]:
        if len({parse_ref(text)["object_id"] for text in refs}) != len(refs):
            raise ValueError("a relation list names each object once")
        return refs


class WorldAssignParams(StrictModel):
    """指派责任人（契约第 9 节）：生效时间即事件的 occurred_at，0.1 不做未来生效。指派者都是人，不带写入声明。"""
    principal_id: CanonicalUUID


class WorldRefreshStateParams(StrictModel):
    """写状态快照：载荷按 StateSnapshot 校验，所在域随主体（契约第 7 节）。"""
    payload: dict[str, Any]
    declaration: Optional[Declaration] = None


class WorldRecordEventParams(StrictModel):
    """记外部事件（契约第 8 节）：category 必填，更正且只有更正以 supersedes_event_id 引用原事件。"""
    category: Literal["meeting", "review", "delivery", "acceptance", "other", "correction"]
    subject_refs: list[RefText] = Field(min_length=1)
    occurred_at: Timestamp
    content: Block
    supersedes_event_id: Optional[CanonicalUUID] = None
    declaration: Optional[Declaration] = None

    @field_validator("subject_refs")
    @classmethod
    def distinct_subjects(cls, refs: list[str]) -> list[str]:
        if len({parse_ref(text)["object_id"] for text in refs}) != len(refs):
            raise ValueError("an event names each subject object once")
        return refs

    @model_validator(mode="after")
    def correction_references_its_original(self) -> "WorldRecordEventParams":
        if (self.category == "correction") != (self.supersedes_event_id is not None):
            raise ValueError("a correction, and only a correction, references the event it corrects")
        return self


ACTION_PARAMS = {"world_create_object": WorldCreateObjectParams, "world_revise_object": WorldReviseObjectParams,
                 "world_relate": WorldRelateParams, "world_refresh_state": WorldRefreshStateParams,
                 "world_record_event": WorldRecordEventParams, "world_assign": WorldAssignParams}
# 不落在某个对象上、按 scope 判权的动作（契约第 8 节）：外部事件。
SCOPE_ACTIONS = frozenset({"world_record_event"})
# 责任人须持的角色（契约第 1、4 节）：按身份类型。责任单元只用于指派其 DRI（DRI 仍按角色解析）。
RESPONSIBLE_ROLES: dict[str, dict[str, str]] = {
    "ResponsibilityUnit": {"human": "DOMAIN_DRI"},
    "Mission": {"human": "OWNER"},
    "Task": {"human": "IC"},
    "Activity": {"human": "IC", "agent": "AGENT"},
}
# 指派由往上几级对象的责任人来做（契约第 9 节）：Activity 由其 Task 所属 Mission 的 Owner 指派。
ASSIGNED_FROM_LEVELS_UP = {"ResponsibilityUnit": 1, "Mission": 1, "Task": 1, "Activity": 2}
# 动作 -> 允许的目标类型；空集表示该动作不带 target。状态快照不修订（错快照用新快照），
# 只有带跨链关系字段的 Mission、Task 能建关系。
ACTION_TARGETS: dict[str, frozenset[str]] = {
    "world_create_object": frozenset(),
    "world_revise_object": frozenset({"Company", "Strategy", "ResponsibilityUnit", "LongTermGoal", "PeriodGoal",
                                      "Mission", "Task", "Activity"}),
    "world_relate": frozenset({"Mission", "Task"}),
    "world_refresh_state": frozenset(),
    "world_record_event": frozenset(),
    "world_assign": frozenset({"ResponsibilityUnit", "Mission", "Task", "Activity"}),
}
