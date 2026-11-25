"""A closed symbolic schema: runtime libraries cannot contain observations."""
from dataclasses import dataclass, asdict


@dataclass(frozen=True)
class PrimitiveRule:
    pattern: str = "complementary_inverter_5v_v1"
    alignment: str = "align:right"
    order: str = "p_above_n"
    category: str = "learned_style_preference"

    def validate(self):
        if self.pattern != "complementary_inverter_5v_v1" or self.alignment not in {"align:left", "align:right", "align:center"} or self.order != "p_above_n":
            raise ValueError("Unsupported symbolic primitive")
        if self.category not in {"learned_style_preference", "manual_seed"}:
            raise ValueError("Invalid primitive provenance")


def library_dict(rules):
    for rule in rules:
        rule.validate()
    return {"schema": "clay.primitive-rules.v1", "rules": [asdict(r) for r in rules]}


def load_library(data):
    if set(data) != {"schema", "rules"} or data["schema"] != "clay.primitive-rules.v1":
        raise ValueError("Unknown fields/schema in frozen library")
    if not isinstance(data["rules"], list) or len(data["rules"]) > 1:
        raise ValueError("This experiment supports zero or one role primitive")
    rules = []
    for value in data["rules"]:
        if set(value) != {"pattern", "alignment", "order", "category"}:
            raise ValueError("Library contains non-symbolic/unknown fields")
        r = PrimitiveRule(**value)
        r.validate()
        rules.append(r)
    return tuple(rules)
