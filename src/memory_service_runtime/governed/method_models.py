"""Explicit compiled Method action/type registry, separate from legacy unions."""
from typing import Literal, Union
from .method_common_models import METHOD_COMMON_PARAMS, METHOD_COMMON_TARGETS, METHOD_COMMON_PAYLOADS
from .method_m1a_models import M1A_ACTION_PARAMS, M1A_ACTION_TARGETS, M1A_PAYLOAD_MODELS
from .method_m1b_models import M1B_ACTION_PARAMS, M1B_ACTION_TARGETS, M1B_PAYLOAD_MODELS

METHOD_ACTION_PARAMS = {**METHOD_COMMON_PARAMS, **M1A_ACTION_PARAMS, **M1B_ACTION_PARAMS}
METHOD_ACTION_TARGETS = {**METHOD_COMMON_TARGETS, **M1A_ACTION_TARGETS, **M1B_ACTION_TARGETS}
METHOD_PAYLOAD_MODELS = {**METHOD_COMMON_PAYLOADS, **M1A_PAYLOAD_MODELS, **M1B_PAYLOAD_MODELS}
METHOD_OBJECT_TYPES = frozenset(METHOD_PAYLOAD_MODELS) | {"EvidenceAsset"}
MethodActionType = Literal[tuple(METHOD_ACTION_PARAMS)]
MethodActionParams = Union[tuple(METHOD_ACTION_PARAMS.values())]


def registry(version):
    if version == "tkos.method/0.3":
        from . import method_v03_models as v03
        return v03.ACTION_PARAMS, v03.ACTION_TARGETS, v03.PAYLOAD_MODELS
    if version == "tkos.method/0.2":
        from . import method_v02_models as v02
        return v02.ACTION_PARAMS, v02.ACTION_TARGETS, v02.PAYLOAD_MODELS
    if version != "tkos.method/0.1":
        from .errors import GovernedError
        raise GovernedError("PROTOCOL_NOT_SUPPORTED")
    return METHOD_ACTION_PARAMS, METHOD_ACTION_TARGETS, METHOD_PAYLOAD_MODELS
