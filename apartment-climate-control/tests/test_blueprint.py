from __future__ import annotations

import math
import unittest
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import yaml
from jinja2.nativetypes import NativeEnvironment


REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
BLUEPRINT_PATH = (
    REPOSITORY_ROOT
    / "apartment-climate-control"
    / "smart-apartment-climate-control.yaml"
)
FIXED_NOW = datetime(2026, 10, 8, 12, 0, tzinfo=timezone.utc)


@dataclass(frozen=True)
class InputReference:
    name: str


class BlueprintLoader(yaml.SafeLoader):
    pass


def construct_input(loader: BlueprintLoader, node: yaml.Node) -> InputReference:
    return InputReference(loader.construct_scalar(node))


def construct_unique_mapping(
    loader: BlueprintLoader, node: yaml.MappingNode, deep: bool = False
) -> dict[Any, Any]:
    loader.flatten_mapping(node)
    result: dict[Any, Any] = {}
    for key_node, value_node in node.value:
        key = loader.construct_object(key_node, deep=deep)
        try:
            duplicate = key in result
        except TypeError as error:
            raise yaml.constructor.ConstructorError(
                "while constructing a mapping",
                node.start_mark,
                "found an unhashable key",
                key_node.start_mark,
            ) from error
        if duplicate:
            raise yaml.constructor.ConstructorError(
                "while constructing a mapping",
                node.start_mark,
                f"found duplicate key {key!r}",
                key_node.start_mark,
            )
        result[key] = loader.construct_object(value_node, deep=deep)
    return result


BlueprintLoader.add_constructor("!input", construct_input)
BlueprintLoader.add_constructor(
    yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG, construct_unique_mapping
)


@dataclass
class EntityState:
    state: str
    last_changed: datetime = FIXED_NOW - timedelta(hours=1)
    attributes: dict[str, Any] = field(default_factory=dict)
    parent_id: str | None = "automation.test"

    @property
    def context(self) -> SimpleNamespace:
        return SimpleNamespace(parent_id=self.parent_id)


class StateRegistry:
    def __init__(self, entities: dict[str, EntityState]):
        self.entities = entities

    def __call__(self, entity_id: str) -> str:
        entity = self.entities.get(entity_id)
        return entity.state if entity else "unknown"

    def __getitem__(self, entity_id: str) -> EntityState:
        if entity_id not in self.entities:
            raise KeyError(entity_id)
        return self.entities[entity_id]


class BlueprintHarness:
    def __init__(
        self,
        blueprint: dict[str, Any],
        input_overrides: dict[str, Any] | None = None,
        entities: dict[str, EntityState] | None = None,
        distances: dict[str, float | None] | None = None,
    ):
        self.blueprint = blueprint
        self.automation = {
            key: value for key, value in blueprint.items() if key != "blueprint"
        }
        self.inputs = self._default_inputs()
        self.inputs.update(input_overrides or {})
        self.states = StateRegistry(entities or {})
        self.distances = distances or {}
        self.environment = NativeEnvironment()
        self.environment.tests["is_number"] = self.is_number
        self.environment.globals.update(
            {
                "distance": self.distance,
                "expand": self.expand,
                "is_number": self.is_number,
                "is_state": self.is_state,
                "now": lambda: FIXED_NOW,
                "state_attr": self.state_attr,
                "states": self.states,
            }
        )

    def _default_inputs(self) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for name, definition in flatten_inputs(self.blueprint["blueprint"]["input"]).items():
            if "default" in definition:
                result[name] = definition["default"]
            elif name == "climate_entity":
                result[name] = "climate.test"
            else:
                result[name] = None
        return result

    @staticmethod
    def is_number(value: Any) -> bool:
        if value is None or isinstance(value, bool):
            return False
        try:
            return math.isfinite(float(value))
        except (TypeError, ValueError):
            return False

    def distance(self, entity_id: str) -> float | None:
        return self.distances.get(entity_id)

    def expand(self, entity_ids: str | list[str] | None) -> list[EntityState]:
        if not entity_ids:
            return []
        if isinstance(entity_ids, str):
            entity_ids = [entity_ids]
        return [self.states.entities[item] for item in entity_ids if item in self.states.entities]

    def is_state(self, entity_id: str, expected: str) -> bool:
        return self.states(entity_id) == expected

    def state_attr(self, entity_id: str, attribute: str) -> Any:
        entity = self.states.entities.get(entity_id)
        return entity.attributes.get(attribute) if entity else None

    def render(self, source: str, variables: dict[str, Any] | None = None) -> Any:
        rendered = self.environment.from_string(source).render(variables or {})
        if not isinstance(rendered, str):
            return rendered
        scalar = rendered.strip()
        if scalar == "True":
            return True
        if scalar == "False":
            return False
        if scalar in {"None", "none", "null"}:
            return None
        try:
            return float(scalar)
        except ValueError:
            return rendered

    def render_variables(self) -> dict[str, Any]:
        variables: dict[str, Any] = {}
        for name, value in self.automation.get("variables", {}).items():
            if isinstance(value, InputReference):
                variables[name] = self.inputs[value.name]
            elif isinstance(value, str) and ("{{" in value or "{%" in value):
                variables[name] = self.render(value, variables)
            else:
                variables[name] = value
        return variables

    def render_cooling_persistence_trigger(self) -> Any:
        trigger_variables: dict[str, Any] = {}
        for name, value in self.automation.get("trigger_variables", {}).items():
            if isinstance(value, InputReference):
                trigger_variables[name] = self.inputs[value.name]
            elif isinstance(value, str) and ("{{" in value or "{%" in value):
                trigger_variables[name] = self.render(value, trigger_variables)
            else:
                trigger_variables[name] = value
        trigger = next(
            item
            for item in self.automation["triggers"]
            if item.get("id") == "cooling_persistence_elapsed"
        )
        return self.render(trigger["value_template"], trigger_variables)


def flatten_inputs(sections: dict[str, Any]) -> dict[str, dict[str, Any]]:
    result: dict[str, dict[str, Any]] = {}
    for name, definition in sections.items():
        if isinstance(definition, dict) and "input" in definition:
            result.update(flatten_inputs(definition["input"]))
        else:
            result[name] = definition
    return result


def collect_values(value: Any):
    yield value
    if isinstance(value, dict):
        for key, item in value.items():
            yield from collect_values(key)
            yield from collect_values(item)
    elif isinstance(value, list):
        for item in value:
            yield from collect_values(item)


def find_input_references(document: Any) -> set[str]:
    return {
        item.name
        for item in collect_values(document)
        if isinstance(item, InputReference)
    }


def collect_templates(value: Any):
    if isinstance(value, str) and ("{{" in value or "{%" in value):
        yield value
    elif isinstance(value, dict):
        for item in value.values():
            yield from collect_templates(item)
    elif isinstance(value, list):
        for item in value:
            yield from collect_templates(item)


def make_entity(
    state: str,
    *,
    age_seconds: int = 3600,
    attributes: dict[str, Any] | None = None,
    parent_id: str | None = "automation.test",
) -> EntityState:
    return EntityState(
        state=state,
        last_changed=FIXED_NOW - timedelta(seconds=age_seconds),
        attributes=attributes or {},
        parent_id=parent_id,
    )


def base_entities(climate_mode: str = "off") -> dict[str, EntityState]:
    return {
        "climate.test": make_entity(
            climate_mode,
            attributes={
                "hvac_modes": ["off", "heat", "cool", "dry", "fan_only"],
                "fan_modes": ["auto", "low", "high"],
                "current_temperature": 24.0,
                "current_humidity": 45.0,
                "temperature": 25.0,
                "fan_mode": "auto",
            },
        )
    }


class BlueprintTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        with BLUEPRINT_PATH.open(encoding="utf-8") as stream:
            cls.document = yaml.load(stream, Loader=BlueprintLoader)
        cls.blueprint = cls.document["blueprint"]
        cls.automation = {key: value for key, value in cls.document.items() if key != "blueprint"}

    def harness(
        self,
        inputs: dict[str, Any] | None = None,
        entities: dict[str, EntityState] | None = None,
        distances: dict[str, float | None] | None = None,
    ) -> BlueprintHarness:
        return BlueprintHarness(self.document, inputs, entities or base_entities(), distances)

    def fan_branch(self) -> dict[str, Any]:
        branches = self.automation["actions"][0]["choose"]
        return next(
            branch
            for branch in branches
            if branch.get("alias") == "Preventivni FAN premor med dolgotrajnim samodejnim hlajenjem"
        )

    def render_fan_start_condition(self, harness: BlueprintHarness) -> Any:
        condition = next(
            item
            for item in self.fan_branch()["conditions"]
            if item.get("condition") == "template"
        )
        return harness.render(condition["value_template"], harness.render_variables())

    def test_blueprint_metadata_and_input_references(self) -> None:
        self.assertEqual(self.blueprint["domain"], "automation")
        self.assertEqual(self.blueprint["homeassistant"]["min_version"], "2024.10.0")
        inputs = flatten_inputs(self.blueprint["input"])
        references = find_input_references(self.document)
        self.assertTrue(references)
        self.assertEqual(references - inputs.keys(), set())

    def test_blueprint_has_no_duplicate_yaml_mapping_keys(self) -> None:
        # Loading through BlueprintLoader rejects duplicate keys before this assertion runs.
        trigger_variables = self.automation["trigger_variables"]
        self.assertEqual(len(trigger_variables), len(set(trigger_variables)))

    def test_every_jinja_template_compiles(self) -> None:
        templates = list(collect_templates(self.document))
        self.assertGreater(len(templates), 20)
        for source in templates:
            with self.subTest(template=source[:100]):
                self.harness().environment.from_string(source)

    def test_fan_wait_is_a_sibling_action_after_fan_mode_call(self) -> None:
        sequence = self.fan_branch()["sequence"]
        self.assertEqual(sequence[0]["action"], "climate.set_hvac_mode")
        self.assertEqual(sequence[0]["data"]["hvac_mode"], "fan_only")
        self.assertIn("wait_template", sequence[1])
        self.assertIn("timeout", sequence[1])
        self.assertTrue(sequence[1]["continue_on_timeout"])
        self.assertIn("choose", sequence[2])

    def test_temperature_sensors_are_averaged_after_stability_window(self) -> None:
        entities = base_entities()
        entities.update(
            {
                "sensor.room_a": make_entity("20", age_seconds=60),
                "sensor.room_b": make_entity("24", age_seconds=60),
            }
        )
        harness = self.harness({"temp_sensors": ["sensor.room_a", "sensor.room_b"]}, entities)
        self.assertAlmostEqual(harness.render_variables()["current_temp"], 22.0)

    def test_unstable_selected_temperature_does_not_fall_back_to_climate(self) -> None:
        entities = base_entities()
        entities["sensor.room_a"] = make_entity("28", age_seconds=5)
        harness = self.harness({"temp_sensors": ["sensor.room_a"]}, entities)
        self.assertIsNone(harness.render_variables()["current_temp"])

    def test_humidity_can_use_average_or_maximum(self) -> None:
        entities = base_entities()
        entities.update(
            {
                "sensor.hum_a": make_entity("50", age_seconds=60),
                "sensor.hum_b": make_entity("70", age_seconds=60),
            }
        )
        selected = ["sensor.hum_a", "sensor.hum_b"]
        average = self.harness({"humidity_sensors": selected}, entities).render_variables()
        maximum = self.harness(
            {"humidity_sensors": selected, "humidity_aggregation": "maximum"}, entities
        ).render_variables()
        self.assertEqual(average["current_hum"], 60.0)
        self.assertEqual(maximum["current_hum"], 70.0)

    def test_empty_person_list_disables_presence_gate(self) -> None:
        harness = self.harness()
        self.assertTrue(harness.render_variables()["is_present"])

    def test_person_within_radius_counts_as_present(self) -> None:
        entities = base_entities()
        entities["person.guest"] = make_entity("not_home")
        harness = self.harness(
            {"person_entities": ["person.guest"], "presence_distance_km": 10},
            entities,
            {"person.guest": 8.5},
        )
        self.assertTrue(harness.render_variables()["is_present"])

    def test_absence_blocks_heating_but_high_humidity_still_allows_dry(self) -> None:
        entities = base_entities("heat")
        entities["person.guest"] = make_entity("not_home")
        entities["sensor.humidity"] = make_entity("75", age_seconds=60)
        harness = self.harness(
            {"person_entities": ["person.guest"], "humidity_sensors": ["sensor.humidity"]},
            entities,
            {"person.guest": 30},
        )
        variables = harness.render_variables()
        self.assertEqual(variables["desired_mode"].strip(), "dry")

    def test_absence_turns_heating_off_when_humidity_is_normal(self) -> None:
        entities = base_entities("heat")
        entities["climate.test"].attributes["current_temperature"] = 18.0
        entities["person.guest"] = make_entity("not_home")
        entities["sensor.humidity"] = make_entity("45", age_seconds=60)
        harness = self.harness(
            {"person_entities": ["person.guest"], "humidity_sensors": ["sensor.humidity"]},
            entities,
            {"person.guest": 30},
        )
        self.assertEqual(harness.render_variables()["desired_mode"].strip(), "off")

    def test_radiators_allow_dry_during_active_schedule(self) -> None:
        entities = base_entities("cool")
        entities["binary_sensor.radiators"] = make_entity("on")
        entities["sensor.humidity"] = make_entity("75", age_seconds=60)
        harness = self.harness(
            {"heating_sensors": ["binary_sensor.radiators"], "humidity_sensors": ["sensor.humidity"]},
            entities,
        )
        self.assertEqual(harness.render_variables()["desired_mode"].strip(), "dry")

    def test_unavailable_radiator_sensor_obeys_fail_safe_setting(self) -> None:
        entities = base_entities("cool")
        entities["binary_sensor.radiators"] = make_entity("unavailable")
        sensors = ["binary_sensor.radiators"]
        permissive = self.harness(
            {"heating_sensors": sensors, "heating_unavailable_blocks": False}, entities
        )
        fail_safe = self.harness(
            {"heating_sensors": sensors, "heating_unavailable_blocks": True}, entities
        )
        self.assertFalse(permissive.render_variables()["radiators_heating"])
        self.assertTrue(fail_safe.render_variables()["radiators_heating"])

    def test_cooling_persistence_trigger_becomes_true_when_cooling_is_due(self) -> None:
        entities = base_entities()
        entities["climate.test"].attributes["current_temperature"] = 27.0
        harness = self.harness({"cooling_persistence_minutes": 15}, entities)
        self.assertTrue(harness.render_cooling_persistence_trigger())

    def test_cooling_persistence_trigger_is_blocked_by_radiators(self) -> None:
        entities = base_entities()
        entities["climate.test"].attributes["current_temperature"] = 27.0
        entities["binary_sensor.radiators"] = make_entity("on")
        harness = self.harness(
            {
                "cooling_persistence_minutes": 15,
                "heating_sensors": ["binary_sensor.radiators"],
            },
            entities,
        )
        self.assertFalse(harness.render_cooling_persistence_trigger())

    def test_cooling_persistence_requires_temperature_strictly_above_start_threshold(self) -> None:
        entities = base_entities()
        entities["climate.test"].attributes["current_temperature"] = 25.5
        harness = self.harness({"cooling_persistence_minutes": 10}, entities)
        self.assertFalse(harness.render_cooling_persistence_trigger())

        entities["climate.test"].attributes["current_temperature"] = 25.51
        above_threshold = self.harness({"cooling_persistence_minutes": 10}, entities)
        self.assertTrue(above_threshold.render_cooling_persistence_trigger())

    def test_cooling_persistence_waits_for_schedule_and_compressor_idle(self) -> None:
        entities = base_entities()
        entities["climate.test"].attributes["current_temperature"] = 28.0
        entities["schedule.climate"] = make_entity("off")
        paused = self.harness(
            {"cooling_persistence_minutes": 10, "schedule_entity": "schedule.climate"},
            entities,
        )
        self.assertFalse(paused.render_cooling_persistence_trigger())

        entities["schedule.climate"] = make_entity("on")
        entities["climate.test"] = make_entity(
            "off",
            age_seconds=30,
            attributes=entities["climate.test"].attributes,
        )
        compressor_idle = self.harness(
            {"cooling_persistence_minutes": 10, "min_idle_minutes": 5, "schedule_entity": "schedule.climate"},
            entities,
        )
        self.assertFalse(compressor_idle.render_cooling_persistence_trigger())

    def test_cooling_persistence_waits_for_manual_hold_to_expire(self) -> None:
        entities = base_entities()
        entities["climate.test"] = make_entity(
            "off",
            age_seconds=30,
            parent_id=None,
            attributes={**entities["climate.test"].attributes, "current_temperature": 28.0},
        )
        harness = self.harness(
            {
                "cooling_persistence_minutes": 10,
                "allow_manual": True,
                "manual_hold_minutes": 60,
            },
            entities,
        )
        self.assertFalse(harness.render_cooling_persistence_trigger())

    def test_open_window_overrides_manual_cooling_and_high_humidity(self) -> None:
        entities = base_entities("cool")
        entities["binary_sensor.window"] = make_entity("on", age_seconds=120)
        entities["sensor.humidity"] = make_entity("80", age_seconds=60)
        harness = self.harness(
            {
                "window_sensors": ["binary_sensor.window"],
                "humidity_sensors": ["sensor.humidity"],
                "allow_manual": True,
            },
            entities,
        )
        variables = harness.render_variables()
        self.assertTrue(variables["window_blocked"])
        self.assertTrue(variables["forced"])
        self.assertEqual(variables["desired_mode"].strip(), "off")

    def test_window_close_delay_prevents_restart_until_delay_expires(self) -> None:
        entities = base_entities("off")
        entities["climate.test"].attributes["current_temperature"] = 18.0
        entities["binary_sensor.window"] = make_entity("off", age_seconds=30)
        inputs = {
            "window_sensors": ["binary_sensor.window"],
            "window_close_delay": {"hours": 0, "minutes": 2, "seconds": 0},
        }
        before_delay = self.harness(inputs, entities).render_variables()
        self.assertTrue(before_delay["window_blocked"])
        self.assertEqual(before_delay["desired_mode"].strip(), "off")

        entities["binary_sensor.window"] = make_entity("off", age_seconds=121)
        after_delay = self.harness(inputs, entities).render_variables()
        self.assertFalse(after_delay["window_blocked"])
        self.assertEqual(after_delay["desired_mode"].strip(), "heat")

    def test_radiators_during_schedule_pause_override_manual_dry_and_humidity(self) -> None:
        entities = base_entities("dry")
        entities["schedule.climate"] = make_entity("off")
        entities["binary_sensor.radiators"] = make_entity("on")
        entities["sensor.humidity"] = make_entity("80", age_seconds=60)
        harness = self.harness(
            {
                "schedule_entity": "schedule.climate",
                "heating_sensors": ["binary_sensor.radiators"],
                "humidity_sensors": ["sensor.humidity"],
                "dehum_during_schedule_pause": True,
                "allow_manual": True,
            },
            entities,
        )
        variables = harness.render_variables()
        self.assertTrue(variables["forced"])
        self.assertEqual(variables["desired_mode"].strip(), "off")

    def test_schedule_pause_preserves_manual_mode_even_when_absent(self) -> None:
        entities = base_entities("cool")
        entities["schedule.climate"] = make_entity("off")
        entities["person.guest"] = make_entity("not_home")
        harness = self.harness(
            {
                "schedule_entity": "schedule.climate",
                "person_entities": ["person.guest"],
                "allow_manual": True,
            },
            entities,
            {"person.guest": 50},
        )
        variables = harness.render_variables()
        self.assertFalse(variables["schedule_active"])
        self.assertEqual(variables["desired_mode"].strip(), "cool")

    def test_presence_radius_boundary_is_inclusive(self) -> None:
        for distance, expected in [(10.0, "heat"), (10.01, "off")]:
            with self.subTest(distance=distance):
                entities = base_entities("off")
                entities["climate.test"].attributes["current_temperature"] = 18.0
                entities["person.guest"] = make_entity("not_home")
                harness = self.harness(
                    {
                        "person_entities": ["person.guest"],
                        "presence_distance_km": 10,
                    },
                    entities,
                    {"person.guest": distance},
                )
                self.assertEqual(harness.render_variables()["desired_mode"].strip(), expected)

    def test_highest_humidity_can_trigger_dry_when_average_does_not(self) -> None:
        entities = base_entities("off")
        entities["sensor.hum_a"] = make_entity("50", age_seconds=60)
        entities["sensor.hum_b"] = make_entity("70", age_seconds=60)
        selected = ["sensor.hum_a", "sensor.hum_b"]
        average = self.harness({"humidity_sensors": selected}, entities).render_variables()
        maximum = self.harness(
            {"humidity_sensors": selected, "humidity_aggregation": "maximum"}, entities
        ).render_variables()
        self.assertEqual(average["desired_mode"].strip(), "off")
        self.assertEqual(maximum["desired_mode"].strip(), "dry")

    def test_unstable_selected_humidity_does_not_use_climate_fallback(self) -> None:
        entities = base_entities("off")
        entities["climate.test"].attributes["current_humidity"] = 85.0
        entities["sensor.humidity"] = make_entity("85", age_seconds=5)
        harness = self.harness({"humidity_sensors": ["sensor.humidity"]}, entities)
        variables = harness.render_variables()
        self.assertIsNone(variables["current_hum"])
        self.assertEqual(variables["desired_mode"].strip(), "off")

    def test_cooling_persistence_is_blocked_by_window_absence_or_high_humidity(self) -> None:
        scenarios = [
            (
                "open window",
                {"window_sensors": ["binary_sensor.window"]},
                {"binary_sensor.window": make_entity("on", age_seconds=120)},
                {},
            ),
            (
                "person absent",
                {"person_entities": ["person.guest"]},
                {"person.guest": make_entity("not_home")},
                {"person.guest": 40},
            ),
            (
                "dehumidification needed",
                {"humidity_sensors": ["sensor.humidity"]},
                {"sensor.humidity": make_entity("80", age_seconds=60)},
                {},
            ),
        ]
        for label, inputs, extra_entities, distances in scenarios:
            with self.subTest(blocker=label):
                entities = base_entities()
                entities["climate.test"].attributes["current_temperature"] = 27.0
                entities.update(extra_entities)
                harness = self.harness(
                    {"cooling_persistence_minutes": 15, **inputs}, entities, distances
                )
                self.assertFalse(harness.render_cooling_persistence_trigger())

    def test_preventive_fan_start_requires_enabled_automatic_cooling(self) -> None:
        entities = base_entities("cool")
        enabled = self.harness({"fan_cycle_enabled": True}, entities)
        disabled = self.harness({"fan_cycle_enabled": False}, entities)
        self.assertTrue(self.render_fan_start_condition(enabled))
        self.assertFalse(self.render_fan_start_condition(disabled))

    def test_preventive_fan_cycle_does_not_interrupt_manual_cooling(self) -> None:
        entities = base_entities("cool")
        entities["climate.test"] = make_entity(
            "cool",
            age_seconds=120,
            parent_id=None,
            attributes=entities["climate.test"].attributes,
        )
        harness = self.harness({"fan_cycle_enabled": True}, entities)
        variables = harness.render_variables()
        self.assertTrue(variables["manual_active"])
        self.assertFalse(self.render_fan_start_condition(harness))

    def test_fan_wait_is_interrupted_when_radiators_start_heating(self) -> None:
        branch = self.fan_branch()
        wait_template = branch["sequence"][1]["wait_template"]
        entities = base_entities("fan_only")
        no_blocker = self.harness({"fan_cycle_enabled": True}, entities)
        self.assertFalse(no_blocker.render(wait_template, no_blocker.render_variables()))

        entities["binary_sensor.radiators"] = make_entity("on")
        blocked = self.harness(
            {"fan_cycle_enabled": True, "heating_sensors": ["binary_sensor.radiators"]},
            entities,
        )
        self.assertTrue(blocked.render(wait_template, blocked.render_variables()))

    def test_fan_wait_is_interrupted_by_window_schedule_or_master_switch(self) -> None:
        branch = self.fan_branch()
        wait_template = branch["sequence"][1]["wait_template"]
        cases = [
            (
                "window opens",
                {"window_sensors": ["binary_sensor.window"]},
                {"binary_sensor.window": make_entity("on", age_seconds=120)},
                {},
            ),
            (
                "schedule pauses",
                {"schedule_entity": "schedule.climate"},
                {"schedule.climate": make_entity("off")},
                {},
            ),
            (
                "master switch turns off",
                {"master_switch": "input_boolean.master"},
                {"input_boolean.master": make_entity("off")},
                {},
            ),
        ]
        for label, inputs, extra_entities, distances in cases:
            with self.subTest(interruption=label):
                entities = base_entities("fan_only")
                entities.update(extra_entities)
                harness = self.harness(inputs, entities, distances)
                self.assertTrue(harness.render(wait_template, harness.render_variables()))

    def test_fan_cycle_does_not_resume_cooling_when_humidity_requires_dry(self) -> None:
        entities = base_entities("fan_only")
        entities["climate.test"].attributes["current_temperature"] = 29.0
        entities["sensor.humidity"] = make_entity("80", age_seconds=60)
        harness = self.harness(
            {"humidity_sensors": ["sensor.humidity"], "enable_dehum": True},
            entities,
        )
        resume_condition = self.fan_branch()["sequence"][2]["choose"][0]["conditions"][0]
        self.assertEqual(harness.render_variables()["desired_mode"].strip(), "dry")
        self.assertFalse(
            harness.render(resume_condition["value_template"], harness.render_variables())
        )


if __name__ == "__main__":
    unittest.main()