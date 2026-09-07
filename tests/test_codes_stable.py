from __future__ import annotations

from dataclasses import FrozenInstanceError
from types import MappingProxyType, ModuleType

import adapter.codes as codes
from adapter.codes import (
    CodeInfo,
    UnknownCodeError,
    agreement_code,
    issue_code,
    judgment_code,
    parse,
    signal_code,
    slug_of,
)


def test_code_module_has_no_mutable_global_state() -> None:
    before = dict(vars(codes))
    for n in range(1, 100):
        judgment_code(n)
        signal_code(n)
        agreement_code(n)
        slug_of("海外仓库存周转预警")
    assert dict(vars(codes)) == before

    mutable_types = (dict, list, set, bytearray)
    for name, value in vars(codes).items():
        if name.startswith("__") or isinstance(value, ModuleType):
            continue
        assert not isinstance(value, mutable_types), f"mutable module global: {name}"
    assert isinstance(codes._PINYIN_INITIALS, MappingProxyType)
    try:
        codes._PINYIN_INITIALS["测试"] = "x"
    except TypeError:
        pass
    else:  # pragma: no cover - protects the structural invariant
        raise AssertionError("pinyin table must be read-only")


def test_code_info_is_frozen_and_typed() -> None:
    info = parse("JDG-01")
    assert info == CodeInfo(kind="JDG", key=1, code="JDG-01")
    try:
        info.key = 2  # type: ignore[misc]
    except FrozenInstanceError:
        pass
    else:  # pragma: no cover - frozen dataclass contract
        raise AssertionError("CodeInfo unexpectedly mutable")


def test_ordinal_code_roundtrip_is_exact() -> None:
    for ordinal in (1, 2, 9, 10, 99, 100, 1_000):
        for builder, prefix in (
            (judgment_code, "JDG"),
            (signal_code, "SGN"),
            (agreement_code, "AGR"),
        ):
            code = builder(ordinal)
            info = parse(code)
            assert info == CodeInfo(kind=prefix, key=ordinal, code=code)
            assert parse(info.code) == info


def test_issue_code_roundtrip_and_stable_collision_resolution() -> None:
    title_a = "Growth plan"
    title_b = "growth-plan"
    assert slug_of(title_a) == slug_of(title_b) == "growth-plan"

    taken: set[str] = set()
    first = issue_code(title_a, taken)
    taken.add(slug_of(title_a))
    second = issue_code(title_b, taken)
    assert first == "ISS-growth-plan"
    assert second == "ISS-growth-plan-2"
    assert parse(first) == CodeInfo("ISS", "growth-plan", first)
    assert parse(second) == CodeInfo("ISS", "growth-plan-2", second)

    # Rebuilding the same allocation inputs after a restart gives the same codes.
    taken_again = {slug_of(title_a)}
    assert issue_code(title_b, taken_again) == second


def test_parse_rejects_unknown_prefix_and_malformed_keys() -> None:
    for code in (
        "",
        "ISS",
        "ISS-",
        "DOC-01",
        "iss-abc",
        "ISS-ABC",
        "ISS-a--b",
        "ISS-a_1",
        "ISS-" + "a" * 49,
        "JDG-",
        "JDG-0",
        "JDG-00",
        "JDG-1",
        "JDG-001",
        "JDG-abc",
        "JDG--1",
        "SGN-0",
        "SGN--2",
        "AGR-0",
        "AGR-1",
        "AGR-001",
        "AGR-abc",
    ):
        try:
            parse(code)
        except UnknownCodeError:
            pass
        else:  # pragma: no cover - boundary contract
            raise AssertionError(f"accepted malformed code: {code!r}")


def test_builders_reject_non_positive_or_non_integer_ordinals() -> None:
    for builder in (judgment_code, signal_code, agreement_code):
        for ordinal in (0, -1, True, "1"):
            try:
                builder(ordinal)  # type: ignore[arg-type]
            except ValueError:
                pass
            else:  # pragma: no cover - boundary contract
                raise AssertionError(f"accepted invalid ordinal: {ordinal!r}")
