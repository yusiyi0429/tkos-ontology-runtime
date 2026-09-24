"""tkos.world/0.1 的严格载荷模型，由随包登记生成，块清单不写死在代码里。

载荷形状：属性与关系引用字段在顶层，内容块在 ``blocks`` 下；登记里的每个块都
出现在存储的载荷中，空块存 null（契约第 3 节）。只由事件写入的属性
（``set_by`` 非空，例如 responsible、core_battle）不接受客户端填写。
"""
from __future__ import annotations

from functools import lru_cache
from typing import Annotated, Any, Optional

from pydantic import BaseModel, Field, StrictStr, StringConstraints, create_model, field_validator, model_validator

from . import world_v01_registry as world_registry
from .a2_models import CanonicalUUID, NEStr, PositiveInt, StrictModel

ArtifactUrl = Annotated[str, StringConstraints(pattern=r"^https?://\S+$", max_length=2048)]
BlockPath = Annotated[str, StringConstraints(pattern=r"^[a-z][a-z0-9_]*$")]


class RefInput(StrictModel):
    """写入时的引用：对象 id、版本号与可选块路径；修订 id 由服务端解析并钉住。"""
    object_id: CanonicalUUID
    object_version: PositiveInt
    block: BlockPath | None = None


class Block(StrictModel):
    text: StrictStr = ""
    refs: list[RefInput] = Field(default_factory=list)
    artifacts: list[ArtifactUrl] = Field(default_factory=list)

    @model_validator(mode="after")
    def carries_content(self) -> "Block":
        if not self.text.strip() and not self.refs and not self.artifacts:
            raise ValueError("a non-null block needs text, refs or artifacts; store an empty block as null")
        return self


_VALUE_TYPES: dict[str, Any] = {"text": NEStr}


@lru_cache(maxsize=None)
def payload_model(object_type: str) -> type[BaseModel]:
    spec = world_registry.object_spec(object_type)
    if spec["relation_fields"]:
        raise NotImplementedError(f"relation fields of {object_type} are not implemented yet")
    blocks = create_model(f"{object_type}Blocks", __config__=StrictModel.model_config,
                          **{block["id"]: (Optional[Block], None) for block in spec["blocks"]})
    fields: dict[str, Any] = {}
    for attribute in spec["attributes"]:
        if attribute["set_by"] is not None:
            continue
        value_type = _VALUE_TYPES.get(attribute["value"])
        if value_type is None:
            raise NotImplementedError(f"attribute value kind {attribute['value']} is not implemented yet")
        fields[attribute["id"]] = (value_type, ...) if attribute["required"] else (Optional[value_type], None)
    fields["blocks"] = (blocks, Field(default_factory=blocks))
    return create_model(f"{object_type}Payload", __config__=StrictModel.model_config, **fields)


def validate_payload(object_type: str, payload: Any) -> dict[str, Any]:
    """按类型严格校验并返回存储形状；违反契约抛 ValueError。"""
    return payload_model(object_type).model_validate(payload).model_dump(mode="json")


CONTRACT_VERSION = "tkos.world/0.1"


class WorldCreateObjectParams(StrictModel):
    domain_id: CanonicalUUID
    object_type: NEStr
    payload: dict[str, Any]

    @field_validator("object_type")
    @classmethod
    def registered_type(cls, value: str) -> str:
        if value not in world_registry.object_types():
            raise ValueError(f"{value} is not a world object type")
        return value


ACTION_PARAMS = {"world_create_object": WorldCreateObjectParams}
# 动作 -> 允许的目标类型；空集表示该动作不带 target。
ACTION_TARGETS: dict[str, frozenset[str]] = {"world_create_object": frozenset()}
