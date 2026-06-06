import pytest

from app.taxonomy import TAXONOMY, is_valid


def test_every_subcategory_is_valid() -> None:
    for main, subs in TAXONOMY.items():
        assert subs, f"Category {main!r} has no sub-categories"
        for sub in subs:
            assert is_valid(main, sub), f"Expected valid: ({main!r}, {sub!r})"


def test_fake_main_is_invalid() -> None:
    assert not is_valid("NotARealCategory", "Feature Gap")


def test_fake_sub_is_invalid() -> None:
    real_main = next(iter(TAXONOMY))
    assert not is_valid(real_main, "Totally Invented Sub")


def test_empty_strings_are_invalid() -> None:
    assert not is_valid("", "")
    assert not is_valid("Product", "")
    assert not is_valid("", "Feature Gap")


def test_cross_category_sub_is_invalid() -> None:
    # A valid sub from one main must not validate under a different main.
    keys = list(TAXONOMY.keys())
    assert len(keys) >= 2, "Need at least two categories for this test"
    main_a, main_b = keys[0], keys[1]
    sub_b = TAXONOMY[main_b][0]
    assert not is_valid(main_a, sub_b), (
        f"Sub {sub_b!r} from {main_b!r} should not be valid under {main_a!r}"
    )


@pytest.mark.parametrize("main,sub", [
    ("Misuse / Noise", "Incorrect usage"),
    ("Misuse / Noise", "Duplicate questions"),
    ("Misuse / Noise", "Incomplete or low-quality questions"),
])
def test_noise_subcategories_are_valid(main: str, sub: str) -> None:
    assert is_valid(main, sub)
