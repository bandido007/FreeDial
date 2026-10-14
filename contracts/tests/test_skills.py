import pytest

from contracts.skills import SkillConditionError, evaluate, parse, skill_names

ASHA = {"swahili": 9, "billing": 4}
BARAKA = {"english": 6, "billing": 8, "trainee": 1}


@pytest.mark.parametrize(
    ("condition", "asha", "baraka"),
    [
        ("swahili >= 7", True, False),
        ("billing >= 5", False, True),
        ("swahili >= 7 & billing >= 5", False, False),
        ("swahili >= 7 | billing >= 5", True, True),
        ("billing", True, True),
        ("english", False, True),
        ("!trainee", True, False),
        ("(english > 3 | swahili > 3) & !trainee", True, False),
        ("swahili == 9", True, False),
        ("swahili != 9", False, True),
        ("swahili < 1", False, True),  # a missing skill is level 0
        ("true", True, True),
        ("false", False, False),
        ("SWAHILI>=7", True, False),  # case and spaces do not matter
    ],
)
def test_conditions(condition, asha, baraka):
    parsed = parse(condition)
    assert (evaluate(parsed, ASHA), evaluate(parsed, BARAKA)) == (asha, baraka)


def test_and_binds_tighter_than_or():
    assert parse("a | b & c") == ("or", ("has", "a"), ("and", ("has", "b"), ("has", "c")))


@pytest.mark.parametrize(
    "bad", ["", "swahili >=", "swahili >= x", "(billing", "billing)", "7 >= swahili", "a ; b", "a & "]
)
def test_mistakes_are_refused(bad):
    with pytest.raises(SkillConditionError):
        parse(bad)


def test_the_skills_a_condition_names():
    assert skill_names(parse("(english > 3 | swahili > 3) & !trainee & true")) == {
        "english",
        "swahili",
        "trainee",
    }
