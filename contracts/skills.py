"""The skill condition language, shared by the admin (validation) and the brain (evaluation).

A condition says which agents may take a call, from their skill levels (1 to 10)::

    swahili >= 7 & billing >= 5
    (english > 3 | swahili > 3) & !trainee
    billing                     # has the skill at any level (>= 1)
    true                        # anyone

Operators: comparisons ``>= <= > < == !=`` against a whole number, ``&`` (and),
``|`` (or), ``!`` (not) and parentheses; ``&`` binds tighter than ``|``. A skill
the agent does not have counts as level 0. Skill names are lower-case identifiers.

A parsed condition is plain nested tuples, so it can be cached, compared and logged::

    ("or", a, b)  ("and", a, b)  ("not", a)  ("cmp", name, op, value)  ("has", name)  ("const", bool)
"""

import re
from collections.abc import Mapping
from typing import Any

SKILL_NAME = re.compile(r"^[a-z][a-z0-9_]{0,39}$")
_TOKEN = re.compile(r"\s*(?:(>=|<=|==|!=|>|<|&|\||!|\(|\))|(\d+)|([a-z][a-z0-9_]*)|(\S))")
_COMPARE = {
    ">=": lambda a, b: a >= b,
    "<=": lambda a, b: a <= b,
    ">": lambda a, b: a > b,
    "<": lambda a, b: a < b,
    "==": lambda a, b: a == b,
    "!=": lambda a, b: a != b,
}

Condition = tuple[Any, ...]


class SkillConditionError(ValueError):
    """A condition that does not follow the language."""


def _tokens(text: str) -> list[tuple[str, str]]:
    out = []
    for match in _TOKEN.finditer(text.lower()):
        op, number, name, other = match.groups()
        if other:
            raise SkillConditionError(f"unexpected {other!r}")
        out.append(("op", op) if op else ("num", number) if number else ("name", name))
    return out


class _Parser:
    def __init__(self, text: str):
        self.tokens = _tokens(text)
        self.at = 0

    def peek(self) -> tuple[str, str] | None:
        return self.tokens[self.at] if self.at < len(self.tokens) else None

    def take(self) -> tuple[str, str]:
        token = self.peek()
        if token is None:
            raise SkillConditionError("the condition ends too early")
        self.at += 1
        return token

    def parse(self) -> Condition:
        if not self.tokens:
            raise SkillConditionError("the condition is empty")
        result = self.either()
        if self.peek() is not None:
            raise SkillConditionError(f"unexpected {self.peek()[1]!r}")  # type: ignore[index]
        return result

    def either(self) -> Condition:
        left = self.both()
        while self.peek() == ("op", "|"):
            self.take()
            left = ("or", left, self.both())
        return left

    def both(self) -> Condition:
        left = self.negated()
        while self.peek() == ("op", "&"):
            self.take()
            left = ("and", left, self.negated())
        return left

    def negated(self) -> Condition:
        if self.peek() == ("op", "!"):
            self.take()
            return ("not", self.negated())
        return self.atom()

    def atom(self) -> Condition:
        kind, value = self.take()
        if (kind, value) == ("op", "("):
            inner = self.either()
            if self.take() != ("op", ")"):
                raise SkillConditionError("a '(' is not closed")
            return inner
        if kind == "name" and value in ("true", "false"):
            return ("const", value == "true")
        if kind != "name":
            raise SkillConditionError(f"expected a skill name, got {value!r}")
        following = self.peek()
        if following and following[0] == "op" and following[1] in _COMPARE:
            op = self.take()[1]
            number_kind, number = self.take()
            if number_kind != "num":
                raise SkillConditionError(f"{value} {op} needs a number")
            return ("cmp", value, op, int(number))
        return ("has", value)


def parse(text: str) -> Condition:
    return _Parser(text).parse()


def evaluate(condition: Condition, levels: Mapping[str, int]) -> bool:
    kind = condition[0]
    if kind == "const":
        return bool(condition[1])
    if kind == "has":
        return levels.get(condition[1], 0) >= 1
    if kind == "cmp":
        _, name, op, value = condition
        return _COMPARE[op](levels.get(name, 0), value)
    if kind == "not":
        return not evaluate(condition[1], levels)
    if kind == "and":
        return evaluate(condition[1], levels) and evaluate(condition[2], levels)
    if kind == "or":
        return evaluate(condition[1], levels) or evaluate(condition[2], levels)
    raise SkillConditionError(f"unknown node {kind!r}")


def skill_names(condition: Condition) -> set[str]:
    kind = condition[0]
    if kind in ("has", "cmp"):
        return {condition[1]}
    if kind == "const":
        return set()
    return set().union(*(skill_names(part) for part in condition[1:]))
