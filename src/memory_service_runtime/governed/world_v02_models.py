"""tkos.world/0.2 的严格载荷与参数模型，由随包的 0.2 登记生成，块清单不写死在代码里。

载荷形状同 0.1：属性在顶层，内容块在 ``blocks`` 下，登记里的每个块都出现，空块存 null。
块值是 0.2 的四个字段 ``{text, components, refs, artifacts}``（契约第 4 节）。本票只接通
Company：组件与块内引用随票 #50 开放，在此之前两个列表只能为空；只有 Company 用到的属性
取值种类（文本、外部引用）已实现，其余类型的模型随各自的票补齐。
"""
from __future__ import annotations

from functools import lru_cache
from typing import Annotated, Any, Optional

from pydantic import BaseModel, Field, StrictStr, StringConstraints, create_model, field_validator, model_validator

from . import world_v02_registry as world_registry
from .a2_models import CanonicalUUID, NEStr, StrictModel

CONTRACT_VERSION = "tkos.world/0.2"

ArtifactUrl = Annotated[str, StringConstraints(pattern=r"^https?://\S+$", max_length=2048)]
ShortText = Annotated[str, StringConstraints(pattern=r"\S", max_length=256)]


def citation(object_id: str, version: int, block: str | None = None) -> str:
    """对象与块形式引用的业务形式（契约第 5 节）；组件与事件形式随票 #50。"""
    return f"{object_id}@{version}" + (f"#{block}" if block else "")


class ExternalRef(StrictModel):
    """外部系统与本体对象的对照（契约第 3.4 节）；scope 内按 (system, id) 唯一在票 #63。"""
    system: ShortText
    id: ShortText
    url: Optional[ArtifactUrl] = None


class Block(StrictModel):
    """块值（契约第 4 节）。非空块至少含文字、组件、引用或链接之一；只有空白的块必须存 null。"""
    text: StrictStr = ""
    # 组件与块内引用随票 #50 开放。
    components: list[dict[str, Any]] = Field(default_factory=list, max_length=0)
    refs: list[StrictStr] = Field(default_factory=list, max_length=0)
    artifacts: list[ArtifactUrl] = Field(default_factory=list)

    @model_validator(mode="after")
    def carries_content(self) -> "Block":
        if not self.text.strip() and not self.components and not self.refs and not self.artifacts:
            raise ValueError("a non-null block needs text, components, refs or artifacts; store an empty block as null")
        return self


def _value_type(attribute: dict[str, Any]) -> Any:
    kind = attribute["value"]
    if kind == "text":
        return NEStr
    if kind == "external_refs":
        return list[ExternalRef]
    raise NotImplementedError(f"world 0.2 attribute value kind {kind} is not implemented yet")


@lru_cache(maxsize=None)
def model(object_type: str) -> type[BaseModel]:
    """写入与存储同一个模型：本票的类型没有引用，不需要区分业务形式与钉定结构。"""
    spec = world_registry.object_spec(object_type)
    if spec["relation_fields"]:
        raise NotImplementedError(f"world 0.2 relation fields of {object_type} are not implemented yet")
    blocks = create_model(object_type + "V02Blocks", __config__=StrictModel.model_config,
                          **{block["id"]: (Optional[Block], None) for block in spec["blocks"]})
    fields: dict[str, Any] = {}
    for attribute in spec["attributes"]:
        value_type = _value_type(attribute)
        if attribute["set_by"] is not None:
            raise NotImplementedError(f"world 0.2 event-written attributes of {object_type} are not implemented yet")
        if attribute["value"] == "external_refs":
            fields[attribute["id"]] = (value_type, Field(default_factory=list))
        elif attribute["required"]:
            fields[attribute["id"]] = (value_type, ...)
        else:
            fields[attribute["id"]] = (Optional[value_type], None)
    fields["blocks"] = (blocks, Field(default_factory=blocks))
    return create_model(object_type + "V02Payload", __config__=StrictModel.model_config, **fields)


def validate_input(object_type: str, payload: Any) -> dict[str, Any]:
    """按类型严格校验客户端写入的载荷；违反契约抛 ValueError。"""
    return model(object_type).model_validate(payload).model_dump(mode="json")


class WorldV02CreateObjectParams(StrictModel):
    """建对象。写入声明（场景放宽到任一业务对象）随票 #50 开放；Company 只由 CEO 本人建，不带声明。"""
    domain_id: CanonicalUUID
    object_type: NEStr
    payload: dict[str, Any]

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
CREATABLE = frozenset({"Company"})
