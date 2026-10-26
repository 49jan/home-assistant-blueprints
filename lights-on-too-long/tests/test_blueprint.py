from __future__ import annotations

import ast
import unittest
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import yaml
from jinja2.nativetypes import NativeEnvironment


REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
BLUEPRINT_PATH = REPOSITORY_ROOT / "lights-on-too-long" / "lights-on-too-long.yaml"
FIXED_NOW = datetime(2026, 10, 26, 12, 0, tzinfo=timezone.utc)


@dataclass(frozen=True)
class InputReference:
    name: str


class BlueprintLoader(yaml.SafeLoader):
    pass


def construct_input(loader: BlueprintLoader, node: yaml.Node) -> InputReference:
    return InputReference(loader.construct_scalar(node))


BlueprintLoader.add_constructor("!input", construct_input)


@dataclass
class EntityState:
    state: str
    last_changed: datetime
    name: str = ""


class States(dict):
    def __getitem__(self, key: str) -> EntityState:
        return super().__getitem__(key)


def load_blueprint() -> dict[str, Any]:
    with BLUEPRINT_PATH.open(encoding="utf-8") as handle:
        return yaml.load(handle, Loader=BlueprintLoader)


def walk(node: Any):
    yield node
    if isinstance(node, dict):
        for value in node.values():
            yield from walk(value)
    elif isinstance(node, list):
        for value in node:
            yield from walk(value)


def collect_templates(node: Any) -> list[str]:
    return [
        item
        for item in walk(node)
        if isinstance(item, str) and ("{{" in item or "{%" in item)
    ]


def render(template: str, variables: dict[str, Any], states: States) -> Any:
    env = NativeEnvironment()
    env.globals["now"] = lambda: FIXED_NOW
    env.globals["states"] = states
    env.globals["is_state"] = lambda entity, value: (
        entity in states and states[entity].state == value
    )
    env.tests["is_state"] = env.globals["is_state"]
    env.globals["device_attr"] = lambda device, attr: {"dev1": "Telefon Jan"}[device]
    env.filters["slugify"] = lambda value: str(value).lower().replace(" ", "_")
    result = env.from_string(template).render(**variables)
    if isinstance(result, str):
        # Home Assistant strips the output and parses it as a Python literal.
        try:
            return ast.literal_eval(result.strip())
        except (ValueError, SyntaxError):
            return result.strip()
    return result


class LightsOnTooLongBlueprintTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.blueprint = load_blueprint()

    def declared_inputs(self) -> set[str]:
        names: set[str] = set()
        for section in self.blueprint["blueprint"]["input"].values():
            names.update(section["input"])
        return names

    def test_all_input_references_are_declared(self) -> None:
        declared = self.declared_inputs()
        referenced = {
            item.name for item in walk(self.blueprint) if isinstance(item, InputReference)
        }
        self.assertTrue(referenced)
        self.assertEqual(referenced - declared, set())
        self.assertEqual(declared - referenced, set())

    def test_templates_compile(self) -> None:
        env = NativeEnvironment()
        templates = collect_templates(self.blueprint["variables"]) + collect_templates(
            self.blueprint["action"]
        )
        self.assertTrue(templates)
        for template in templates:
            with self.subTest(template=template[:60]):
                env.parse(template)

    def test_trigger_ids(self) -> None:
        ids = [trigger["id"] for trigger in self.blueprint["trigger"]]
        self.assertEqual(ids, ["too_long", "away", "away_light", "action"])

    def test_notify_services_built_from_devices(self) -> None:
        variables = {"notify_devices_in": ["dev1"], "notify_extra_in": []}
        result = render(self.blueprint["variables"]["notify_list"], variables, States())
        self.assertEqual(result, ["notify.mobile_app_telefon_jan"])

    def test_notify_extra_services_and_groups(self) -> None:
        variables = {
            "notify_devices_in": ["dev1"],
            "notify_extra_in": ["family_group", " notify.mobile_app_pixel_8 ", "notify.mobile_app_telefon_jan"],
        }
        result = render(self.blueprint["variables"]["notify_list"], variables, States())
        self.assertEqual(
            result,
            [
                "notify.mobile_app_telefon_jan",
                "notify.family_group",
                "notify.mobile_app_pixel_8",
            ],
        )

    def overdue(self, trigger_id: str, persons_state: str, minutes_on: int) -> list[str]:
        states = States(
            {
                "light.a": EntityState("on", FIXED_NOW - timedelta(minutes=minutes_on), "A"),
                "light.b": EntityState("on", FIXED_NOW - timedelta(minutes=minutes_on), "B"),
                "light.c": EntityState("off", FIXED_NOW - timedelta(minutes=minutes_on), "C"),
                "person.x": EntityState(persons_state, FIXED_NOW, "X"),
            }
        )
        variables: dict[str, Any] = {
            "monitored": ["light.a", "light.b", "light.c"],
            "excluded": ["light.b"],
            "persons": ["person.x"],
            "on_duration_in": {"hours": 1, "minutes": 0, "seconds": 0},
            "away_delay_in": {"hours": 0, "minutes": 5, "seconds": 0},
            "trigger": SimpleNamespace(id=trigger_id),
        }
        for name in ("on_s", "away_s", "all_away", "limit_s", "action_limit_s", "overdue"):
            variables[name] = render(self.blueprint["variables"][name], variables, states)
        return variables["overdue"]

    def test_overdue_after_one_hour_when_home(self) -> None:
        self.assertEqual(self.overdue("too_long", "home", 61), ["light.a"])
        self.assertEqual(self.overdue("too_long", "home", 30), [])

    def test_overdue_after_five_minutes_when_everyone_away(self) -> None:
        self.assertEqual(self.overdue("away", "not_home", 6), ["light.a"])
        self.assertEqual(self.overdue("away", "not_home", 2), [])


if __name__ == "__main__":
    unittest.main()
