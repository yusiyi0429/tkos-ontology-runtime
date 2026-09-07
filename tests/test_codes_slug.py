from __future__ import annotations

from adapter.codes import dedupe, issue_code, slug_of


def test_common_chinese_title_uses_stable_initials() -> None:
    assert slug_of("海外仓库存周转预警") == "hwckczzyj"
    assert slug_of("AI 供应链") == "ai-gyl"


def test_slug_is_deterministic_for_mixed_inputs() -> None:
    titles = (
        "海外仓库存周转预警",
        "Q3现金流",
        "中文 / English -- 42",
        "Ｑ３",
        "生僻字𠀀",
        "!!!",
    )
    for title in titles:
        outputs = {slug_of(title) for _ in range(1_000)}
        assert len(outputs) == 1


def test_slug_handles_empty_and_illegal_runs() -> None:
    assert slug_of("") == "x"
    assert slug_of("   !!! ___ ") == "x"
    assert slug_of("A！！B") == "a-b"
    assert slug_of("  A\tB  ") == "a-b"


def test_slug_normalizes_and_caps_long_titles() -> None:
    slug = slug_of("Ｑ３" + "业务" * 100)
    assert slug.startswith("q3")
    assert len(slug) <= 48
    assert slug == slug_of("Ｑ３" + "业务" * 100)


def test_dedupe_does_not_mutate_taken() -> None:
    taken = {"alpha", "alpha-2"}
    before = taken.copy()
    assert dedupe("alpha", taken) == "alpha-3"
    assert taken == before
    assert dedupe("fresh", taken) == "fresh"


def test_issue_code_uses_slug_namespace_for_dedupe() -> None:
    taken = {"growth-plan", "growth-plan-2"}
    assert issue_code("Growth plan", taken) == "ISS-growth-plan-3"
    assert taken == {"growth-plan", "growth-plan-2"}
