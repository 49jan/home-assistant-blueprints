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
            # Home Assistant strips whitespace from rendered template results.
            return scalar

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
        return self.render_trigger("cooling_persistence_elapsed")

    def render_trigger(self, trigger_id: str) -> Any:
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
            if item.get("id") == trigger_id
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

    def test_manual_off_does_not_hold_heating_when_room_is_cold(self) -> None:
        # Regression test: a manual turn-OFF must not block the automation from
        # turning heating back on within manual_hold_minutes, unlike a manual
        # turn-ON/mode-change, which should still be respected for that long.
        entities = base_entities("off")
        entities["climate.test"] = make_entity(
            "off",
            age_seconds=30 * 60,
            parent_id=None,
            attributes={**entities["climate.test"].attributes, "current_temperature": 20.0},
        )
        harness = self.harness(
            {
                "heat_target": 21,
                "heat_tolerance": 0.9,
                "allow_manual": True,
                "manual_hold_minutes": 60,
            },
            entities,
        )
        variables = harness.render_variables()
        self.assertFalse(variables["manual_active"])
        self.assertEqual(variables["desired_mode"].strip(), "heat")

    def test_manual_heat_still_holds_for_manual_hold_minutes(self) -> None:
        # A manual turn-ON (here: heat while the room is already warm enough)
        # must still be respected for manual_hold_minutes.
        entities = base_entities("heat")
        entities["climate.test"] = make_entity(
            "heat",
            age_seconds=30 * 60,
            parent_id=None,
            attributes={**entities["climate.test"].attributes, "current_temperature": 24.0},
        )
        harness = self.harness(
            {
                "heat_target": 21,
                "heat_tolerance": 0.9,
                "allow_manual": True,
                "manual_hold_minutes": 60,
            },
            entities,
        )
        variables = harness.render_variables()
        self.assertTrue(variables["manual_active"])

    def master_switch_trigger(self) -> dict[str, Any]:
        return next(
            trigger
            for trigger in self.automation["triggers"]
            if trigger.get("id") == "master_switch_changed"
        )

    def test_master_switch_change_triggers_reevaluation(self) -> None:
        # Regression test: toggling master_switch must change the template
        # trigger's rendered value, otherwise the automation would not
        # re-run immediately when the switch is flipped.
        trigger = self.master_switch_trigger()
        entities = base_entities()
        entities["input_boolean.master"] = make_entity("on", age_seconds=600)
        harness_on = self.harness({"master_switch": "input_boolean.master"}, entities)
        value_on = harness_on.render(
            trigger["value_template"],
            {"master_switch_for_trigger": harness_on.inputs["master_switch"]},
        )

        entities["input_boolean.master"] = make_entity("off", age_seconds=600)
        harness_off = self.harness({"master_switch": "input_boolean.master"}, entities)
        value_off = harness_off.render(
            trigger["value_template"],
            {"master_switch_for_trigger": harness_off.inputs["master_switch"]},
        )

        self.assertNotEqual(value_on, value_off)

    def test_master_switch_unset_trigger_is_stable(self) -> None:
        # master_switch defaults to "" (not a list like the other optional
        # entity inputs), so the trigger must use a template, not a plain
        # state trigger, or an unset master_switch would fail config
        # validation ("" is not a valid entity ID).
        trigger = self.master_switch_trigger()
        harness = self.harness({"master_switch": ""})
        result = harness.render(
            trigger["value_template"],
            {"master_switch_for_trigger": harness.inputs["master_switch"]},
        )
        self.assertFalse(result)

    def master_switch_condition(self) -> dict[str, Any]:
        return self.automation["conditions"][1]

    def test_master_switch_off_blocks_automation_even_on_schedule_inactive(self) -> None:
        # Regression test: the automation must do nothing while master_switch
        # is off, including when the schedule just turned inactive. A
        # previous version special-cased trigger.id == 'schedule_inactive' to
        # bypass master_switch, which forced the climate off even though the
        # blueprint documents "if off, the automation does nothing".
        condition = self.master_switch_condition()
        entities = base_entities()
        entities["input_boolean.master"] = make_entity("off", age_seconds=600)
        harness = self.harness({"master_switch": "input_boolean.master"}, entities)
        variables = dict(harness.render_variables())
        variables["trigger"] = SimpleNamespace(id="schedule_inactive")
        result = harness.render(condition["value_template"], variables)
        self.assertFalse(result)

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

    def fan_cycle_default_master_switch_condition(self) -> dict[str, Any]:
        default_if = self.fan_branch()["sequence"][2]["default"][0]["if"]
        return next(item for item in default_if if item.get("condition") == "template")

    def test_fan_cycle_default_turn_off_respects_master_switch_off(self) -> None:
        # Regression test: if master_switch turns off while the preventive
        # FAN pause is waiting, the wait_template ends early (it already
        # checks master_switch), but the nested choose's "default" block
        # used to call climate.turn_off unconditionally regardless of
        # master_switch, violating "if off, the automation does nothing".
        condition = self.fan_cycle_default_master_switch_condition()
        entities = base_entities("fan_only")
        entities["input_boolean.master"] = make_entity("off", age_seconds=600)
        harness = self.harness({"master_switch": "input_boolean.master"}, entities)
        result = harness.render(condition["value_template"], harness.render_variables())
        self.assertFalse(result)

    def test_fan_cycle_default_turn_off_still_fires_when_master_switch_on(self) -> None:
        # The master_switch guard must not break the existing cleanup
        # behaviour: with master_switch on (or unset), the default branch
        # should still allow turning off a stuck fan_only state.
        condition = self.fan_cycle_default_master_switch_condition()
        entities = base_entities("fan_only")
        entities["input_boolean.master"] = make_entity("on", age_seconds=600)
        harness_on = self.harness({"master_switch": "input_boolean.master"}, entities)
        self.assertTrue(
            harness_on.render(condition["value_template"], harness_on.render_variables())
        )

        harness_unset = self.harness({"master_switch": ""}, base_entities("fan_only"))
        self.assertTrue(
            harness_unset.render(condition["value_template"], harness_unset.render_variables())
        )

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

    TARIFF_INPUTS = {
        "tariff_block_sensor": "sensor.block",
        "preheat_sensor": "binary_sensor.preheat",
        "peak_power_sensor": "sensor.power",
        "heat_target": 21,
        "heat_tolerance": 0.5,
    }

    def tariff_entities(
        self,
        climate_mode: str = "heat",
        *,
        block: str = "3",
        preheat: str = "off",
        power: str = "1000",
        power_unit: str = "W",
        current_temperature: float = 20.8,
        climate_setpoint: float = 21.0,
        temp_step: float | None = None,
    ) -> dict[str, EntityState]:
        entities = base_entities(climate_mode)
        attributes = entities["climate.test"].attributes
        attributes["current_temperature"] = current_temperature
        attributes["temperature"] = climate_setpoint
        if temp_step is not None:
            attributes["target_temp_step"] = temp_step
        entities["sensor.block"] = make_entity(block)
        entities["binary_sensor.preheat"] = make_entity(preheat)
        entities["sensor.power"] = make_entity(
            power, attributes={"unit_of_measurement": power_unit}
        )
        return entities

    def tariff_setpoint_branch(self) -> dict[str, Any]:
        branches = self.automation["actions"][0]["choose"]
        return next(
            branch
            for branch in branches
            if branch.get("alias")
            == "Uveljavi tarifno ciljno temperaturo gretja/hlajenja (ročna nastavitev ostane)"
        )

    def render_branch_conditions(self, branch: dict[str, Any], harness: BlueprintHarness) -> bool:
        variables = harness.render_variables()
        return all(
            harness.render(condition["value_template"], variables)
            for condition in branch["conditions"]
        )

    def test_without_tariff_inputs_heat_target_is_unchanged(self) -> None:
        variables = self.harness({"heat_target": 21}).render_variables()
        self.assertEqual(variables["tariff_block"], 0)
        self.assertEqual(variables["heat_target_final"], 21.0)
        self.assertFalse(variables["peak_shed"])

    def test_block_1_lowers_heat_target(self) -> None:
        cold = self.harness(
            self.TARIFF_INPUTS, self.tariff_entities(block="1", current_temperature=19.0)
        ).render_variables()
        self.assertEqual(cold["heat_target_final"], 20.0)
        self.assertEqual(cold["setpoint"], 20.0)
        # 20.8 °C would keep heating toward 21 °C, but the lowered target stops it.
        warm = self.harness(
            self.TARIFF_INPUTS, self.tariff_entities(block="1", current_temperature=20.8)
        ).render_variables()
        self.assertEqual(warm["desired_mode"], "off")

    def test_preheat_raises_heat_target_outside_block_1(self) -> None:
        harness = self.harness(self.TARIFF_INPUTS, self.tariff_entities(block="2", preheat="on"))
        self.assertEqual(harness.render_variables()["heat_target_final"], 21.5)

    def test_preheat_is_ignored_in_block_1(self) -> None:
        harness = self.harness(self.TARIFF_INPUTS, self.tariff_entities(block="1", preheat="on"))
        variables = harness.render_variables()
        self.assertFalse(variables["preheat_active"])
        self.assertEqual(variables["heat_target_final"], 20.0)

    def test_heat_limit_still_caps_tariff_target(self) -> None:
        entities = self.tariff_entities(block="2", preheat="on")
        entities["sensor.outdoor"] = make_entity("0")
        harness = self.harness(
            {**self.TARIFF_INPUTS, "outdoor_sensor": "sensor.outdoor", "heat_max_delta": 21},
            entities,
        )
        self.assertEqual(harness.render_variables()["heat_target_final"], 21.0)

    def test_peak_shed_converts_watts_and_kilowatts(self) -> None:
        watts = self.harness(
            self.TARIFF_INPUTS, self.tariff_entities(block="1", power="4500", power_unit="W")
        ).render_variables()
        kilowatts = self.harness(
            self.TARIFF_INPUTS, self.tariff_entities(block="1", power="4.5", power_unit="kW")
        ).render_variables()
        below = self.harness(
            self.TARIFF_INPUTS, self.tariff_entities(block="1", power="3500", power_unit="W")
        ).render_variables()
        self.assertTrue(watts["peak_shed"])
        self.assertTrue(kilowatts["peak_shed"])
        self.assertFalse(below["peak_shed"])
        self.assertEqual(watts["heat_target_final"], 19.0)

    def test_peak_shed_only_applies_in_block_1(self) -> None:
        variables = self.harness(
            self.TARIFF_INPUTS, self.tariff_entities(block="2", power="9000")
        ).render_variables()
        self.assertFalse(variables["peak_shed"])
        self.assertEqual(variables["heat_target_final"], 21.0)

    def test_peak_shed_does_not_start_heating_but_keeps_running_unit(self) -> None:
        idle = self.harness(
            self.TARIFF_INPUTS,
            self.tariff_entities("off", block="1", power="6000", current_temperature=17.0),
        ).render_variables()
        running = self.harness(
            self.TARIFF_INPUTS,
            self.tariff_entities("heat", block="1", power="6000", current_temperature=17.0),
        ).render_variables()
        self.assertEqual(idle["desired_mode"].strip(), "off")
        self.assertEqual(running["desired_mode"].strip(), "heat")

    def test_tariff_setpoint_branch_updates_automation_setpoint(self) -> None:
        harness = self.harness(
            {**self.TARIFF_INPUTS, "allow_manual": True},
            self.tariff_entities(block="1", current_temperature=19.0, climate_setpoint=21.0),
        )
        self.assertTrue(self.render_branch_conditions(self.tariff_setpoint_branch(), harness))

    def test_tariff_setpoint_branch_keeps_manual_setpoint(self) -> None:
        harness = self.harness(
            {**self.TARIFF_INPUTS, "allow_manual": True},
            self.tariff_entities(block="1", current_temperature=19.0, climate_setpoint=23.0),
        )
        self.assertFalse(self.render_branch_conditions(self.tariff_setpoint_branch(), harness))

    def test_tariff_setpoint_branch_is_idle_when_setpoint_matches(self) -> None:
        harness = self.harness(
            {**self.TARIFF_INPUTS, "allow_manual": True},
            self.tariff_entities(block="1", current_temperature=19.0, climate_setpoint=20.0),
        )
        self.assertFalse(self.render_branch_conditions(self.tariff_setpoint_branch(), harness))

    def test_setpoint_is_rounded_to_climate_step(self) -> None:
        harness = self.harness(
            self.TARIFF_INPUTS,
            self.tariff_entities(
                block="2", preheat="on", current_temperature=20.0, climate_setpoint=22.0, temp_step=1
            ),
        )
        variables = harness.render_variables()
        self.assertEqual(variables["heat_target_final"], 21.5)
        self.assertEqual(variables["setpoint"], 22.0)
        # 22 is the rounded preheat setpoint, so the branch must not loop on it.
        self.assertFalse(self.render_branch_conditions(self.tariff_setpoint_branch(), harness))

    def test_tariff_triggers_fire_on_each_transition(self) -> None:
        cases = {
            ("1", "off"): "tariff_heat_lowered",
            ("2", "on"): "tariff_heat_raised",
            ("3", "off"): "tariff_heat_normal",
        }
        trigger_ids = {"tariff_heat_lowered", "tariff_heat_raised", "tariff_heat_normal"}
        for (block, preheat), expected in cases.items():
            harness = self.harness(
                self.TARIFF_INPUTS, self.tariff_entities(block=block, preheat=preheat)
            )
            fired = {trigger_id for trigger_id in trigger_ids if harness.render_trigger(trigger_id)}
            self.assertEqual(fired, {expected}, (block, preheat))

    def test_tariff_triggers_are_inactive_without_tariff_inputs(self) -> None:
        harness = self.harness()
        for trigger_id in (
            "tariff_heat_lowered",
            "tariff_heat_raised",
            "tariff_heat_normal",
            "peak_power_exceeded",
        ):
            self.assertFalse(harness.render_trigger(trigger_id), trigger_id)

    def test_peak_power_trigger_requires_block_1(self) -> None:
        in_block_1 = self.harness(
            self.TARIFF_INPUTS, self.tariff_entities(block="1", power="5000")
        )
        in_block_2 = self.harness(
            self.TARIFF_INPUTS, self.tariff_entities(block="2", power="5000")
        )
        self.assertTrue(in_block_1.render_trigger("peak_power_exceeded"))
        self.assertFalse(in_block_2.render_trigger("peak_power_exceeded"))

    PRECOOL_INPUTS = {
        "precool_sensor": "binary_sensor.precool",
        "cool_target": 25,
        "cool_tolerance": 0.5,
        "heat_target": 21,
    }

    def precool_entities(
        self,
        climate_mode: str = "cool",
        *,
        precool: str = "on",
        current_temperature: float = 25.0,
        climate_setpoint: float = 25.0,
    ) -> dict[str, EntityState]:
        entities = base_entities(climate_mode)
        attributes = entities["climate.test"].attributes
        attributes["current_temperature"] = current_temperature
        attributes["temperature"] = climate_setpoint
        entities["binary_sensor.precool"] = make_entity(precool)
        return entities

    def test_precool_lowers_cool_target(self) -> None:
        variables = self.harness(
            self.PRECOOL_INPUTS, self.precool_entities(current_temperature=25.0)
        ).render_variables()
        self.assertTrue(variables["precool_active"])
        self.assertEqual(variables["cool_target_eff"], 24.0)
        self.assertEqual(variables["desired_mode"], "cool")
        self.assertEqual(variables["setpoint"], 24.0)

    def test_precool_off_keeps_cool_target(self) -> None:
        variables = self.harness(
            self.PRECOOL_INPUTS, self.precool_entities(precool="off")
        ).render_variables()
        self.assertEqual(variables["cool_target_eff"], 25.0)

    def test_precool_never_goes_below_heat_target(self) -> None:
        variables = self.harness(
            {**self.PRECOOL_INPUTS, "cool_target": 22, "cool_offset_precool": -3},
            self.precool_entities(current_temperature=23.0),
        ).render_variables()
        self.assertEqual(variables["cool_target_eff"], 21.0)

    def test_outdoor_cool_limit_still_caps_precool(self) -> None:
        entities = self.precool_entities(current_temperature=27.0)
        entities["sensor.outdoor"] = make_entity("33")
        variables = self.harness(
            {**self.PRECOOL_INPUTS, "outdoor_sensor": "sensor.outdoor", "cool_max_delta": 8},
            entities,
        ).render_variables()
        self.assertEqual(variables["cool_target_eff"], 25.0)

    def test_tariff_setpoint_branch_updates_precool_setpoint(self) -> None:
        harness = self.harness(
            {**self.PRECOOL_INPUTS, "allow_manual": True},
            self.precool_entities(current_temperature=25.0, climate_setpoint=25.0),
        )
        self.assertTrue(self.render_branch_conditions(self.tariff_setpoint_branch(), harness))

    def test_tariff_setpoint_branch_keeps_manual_cool_setpoint(self) -> None:
        harness = self.harness(
            {**self.PRECOOL_INPUTS, "allow_manual": True},
            self.precool_entities(current_temperature=25.0, climate_setpoint=22.0),
        )
        self.assertFalse(self.render_branch_conditions(self.tariff_setpoint_branch(), harness))

    def test_precool_triggers_fire_on_transitions(self) -> None:
        on = self.harness(self.PRECOOL_INPUTS, self.precool_entities(precool="on"))
        off = self.harness(self.PRECOOL_INPUTS, self.precool_entities(precool="off"))
        self.assertTrue(on.render_trigger("tariff_cool_precool"))
        self.assertFalse(on.render_trigger("tariff_heat_normal"))
        self.assertFalse(off.render_trigger("tariff_cool_precool"))
        self.assertTrue(off.render_trigger("tariff_heat_normal"))

    def test_cooling_persistence_trigger_uses_precool_target(self) -> None:
        entities = self.precool_entities("off", current_temperature=25.0)
        harness = self.harness(
            {**self.PRECOOL_INPUTS, "cooling_persistence_minutes": 10}, entities
        )
        self.assertTrue(harness.render_cooling_persistence_trigger())
        entities["binary_sensor.precool"] = make_entity("off")
        self.assertFalse(harness.render_cooling_persistence_trigger())


    PAUSE_INPUTS = {
        "schedule_entity": "schedule.climate",
        "keep_min_temp_pause": True,
        "min_temp_pause": 16,
        "allow_manual": False,
        "heat_target": 21,
        "heat_tolerance": 0.5,
    }

    def pause_entities(
        self,
        climate_mode: str = "off",
        *,
        temperature: float = 14.0,
        setpoint: float = 21.0,
        schedule: str = "off",
        age_seconds: int = 3600,
    ) -> dict[str, EntityState]:
        entities = base_entities(climate_mode)
        entities["climate.test"] = make_entity(
            climate_mode,
            age_seconds=age_seconds,
            attributes={
                **entities["climate.test"].attributes,
                "current_temperature": temperature,
                "temperature": setpoint,
            },
        )
        entities["schedule.climate"] = make_entity(schedule)
        return entities

    def pause_branch(self, alias: str) -> dict[str, Any]:
        branches = self.automation["actions"][0]["choose"]
        return next(branch for branch in branches if branch.get("alias") == alias)

    def test_pause_minimum_is_off_by_default(self) -> None:
        inputs = {**self.PAUSE_INPUTS, "keep_min_temp_pause": False}
        variables = self.harness(inputs, self.pause_entities()).render_variables()
        self.assertFalse(variables["pause_min_heating"])
        self.assertEqual(variables["desired_mode"].strip(), "off")

    def test_pause_below_minimum_starts_heating_at_minimum(self) -> None:
        variables = self.harness(self.PAUSE_INPUTS, self.pause_entities()).render_variables()
        self.assertEqual(variables["desired_mode"].strip(), "heat")
        self.assertEqual(variables["setpoint"], 16.0)

    def test_pause_heating_stops_at_minimum_plus_tolerance(self) -> None:
        running = self.harness(
            self.PAUSE_INPUTS, self.pause_entities("heat", temperature=16.4, setpoint=16.0)
        ).render_variables()
        self.assertEqual(running["desired_mode"].strip(), "heat")
        done = self.harness(
            self.PAUSE_INPUTS, self.pause_entities("heat", temperature=16.5, setpoint=16.0)
        ).render_variables()
        self.assertEqual(done["desired_mode"].strip(), "off")
        self.assertFalse(done["forced"])

    def test_pause_above_minimum_stays_off(self) -> None:
        variables = self.harness(
            self.PAUSE_INPUTS, self.pause_entities(temperature=16.0)
        ).render_variables()
        self.assertEqual(variables["desired_mode"].strip(), "off")

    def test_pause_minimum_is_blocked_by_window_and_radiators(self) -> None:
        entities = self.pause_entities()
        entities["binary_sensor.window"] = make_entity("on", age_seconds=600)
        window = self.harness(
            {**self.PAUSE_INPUTS, "window_sensors": ["binary_sensor.window"]}, entities
        ).render_variables()
        self.assertEqual(window["desired_mode"].strip(), "off")
        entities = self.pause_entities()
        entities["binary_sensor.radiators"] = make_entity("on")
        radiators = self.harness(
            {**self.PAUSE_INPUTS, "heating_sensors": ["binary_sensor.radiators"]}, entities
        ).render_variables()
        self.assertEqual(radiators["desired_mode"].strip(), "off")
        entities = self.pause_entities("heat", setpoint=16.0)
        entities["binary_sensor.radiators"] = make_entity("on")
        running = self.harness(
            {**self.PAUSE_INPUTS, "heating_sensors": ["binary_sensor.radiators"]}, entities
        ).render_variables()
        self.assertEqual(running["desired_mode"].strip(), "off")
        self.assertTrue(running["forced"])

    def test_pause_minimum_respects_master_switch(self) -> None:
        entities = self.pause_entities()
        entities["input_boolean.master"] = make_entity("off")
        harness = self.harness(
            {**self.PAUSE_INPUTS, "master_switch": "input_boolean.master"}, entities
        )
        condition = self.master_switch_condition()
        self.assertFalse(harness.render(condition["value_template"], harness.render_variables()))

    def test_pause_minimum_respects_compressor_times(self) -> None:
        inputs = {**self.PAUSE_INPUTS, "min_idle_minutes": 5, "min_run_minutes": 10}
        idle = self.harness(inputs, self.pause_entities(age_seconds=60)).render_variables()
        self.assertEqual(idle["desired_mode"].strip(), "heat")
        self.assertFalse(idle["change_allowed"])
        running = self.harness(
            inputs, self.pause_entities("heat", temperature=17.0, setpoint=16.0, age_seconds=120)
        ).render_variables()
        self.assertEqual(running["desired_mode"].strip(), "off")
        self.assertFalse(running["forced"])
        self.assertFalse(running["change_allowed"])

    def test_pause_minimum_ignores_absence(self) -> None:
        entities = self.pause_entities()
        entities["person.guest"] = make_entity("not_home")
        variables = self.harness(
            {**self.PAUSE_INPUTS, "person_entities": ["person.guest"]},
            entities,
            {"person.guest": 50},
        ).render_variables()
        self.assertEqual(variables["desired_mode"].strip(), "heat")

    def test_pause_minimum_keeps_manual_mode_when_manual_control_allowed(self) -> None:
        inputs = {**self.PAUSE_INPUTS, "allow_manual": True}
        manual_cool = self.harness(inputs, self.pause_entities("cool")).render_variables()
        self.assertEqual(manual_cool["desired_mode"].strip(), "cool")
        manual_heat = self.harness(
            inputs, self.pause_entities("heat", temperature=14.0, setpoint=22.0)
        ).render_variables()
        self.assertFalse(manual_heat["pause_min_heating"])
        self.assertEqual(manual_heat["desired_mode"].strip(), "heat")
        manual_heat_warm = self.harness(
            inputs, self.pause_entities("heat", temperature=20.0, setpoint=22.0)
        ).render_variables()
        self.assertEqual(manual_heat_warm["desired_mode"].strip(), "heat")
        own_heat_done = self.harness(
            inputs, self.pause_entities("heat", temperature=17.0, setpoint=16.0)
        ).render_variables()
        self.assertEqual(own_heat_done["desired_mode"].strip(), "off")
        started = self.harness(inputs, self.pause_entities("off")).render_variables()
        self.assertEqual(started["desired_mode"].strip(), "heat")

    def test_pause_minimum_does_not_override_dehumidification_when_warm(self) -> None:
        entities = self.pause_entities("dry", temperature=20.0)
        entities["sensor.humidity"] = make_entity("80", age_seconds=60)
        variables = self.harness(
            {
                **self.PAUSE_INPUTS,
                "humidity_sensors": ["sensor.humidity"],
                "dehum_during_schedule_pause": True,
            },
            entities,
        ).render_variables()
        self.assertEqual(variables["desired_mode"].strip(), "dry")

    def test_active_schedule_ignores_pause_minimum(self) -> None:
        cold = self.harness(
            self.PAUSE_INPUTS, self.pause_entities(schedule="on", temperature=19.0)
        ).render_variables()
        self.assertFalse(cold["pause_min_heating"])
        self.assertEqual(cold["desired_mode"].strip(), "heat")
        self.assertEqual(cold["setpoint"], 21.0)
        warm = self.harness(
            self.PAUSE_INPUTS, self.pause_entities(schedule="on", temperature=20.8)
        ).render_variables()
        self.assertEqual(warm["desired_mode"].strip(), "off")

    def test_pause_minimum_is_not_changed_by_tariff(self) -> None:
        entities = self.tariff_entities("off", block="1", current_temperature=14.0)
        entities["schedule.climate"] = make_entity("off")
        entities["sensor.power"] = make_entity("9000", attributes={"unit_of_measurement": "W"})
        inputs = {**self.TARIFF_INPUTS, **self.PAUSE_INPUTS, "peak_power_limit": 4}
        variables = self.harness(inputs, entities).render_variables()
        self.assertTrue(variables["peak_shed"])
        self.assertEqual(variables["desired_mode"].strip(), "heat")
        self.assertEqual(variables["setpoint"], 16.0)
        entities = self.tariff_entities("off", block="2", preheat="on", current_temperature=14.0)
        entities["schedule.climate"] = make_entity("off")
        preheat = self.harness(inputs, entities).render_variables()
        self.assertEqual(preheat["setpoint"], 16.0)

    def test_pause_minimum_setpoint_and_turn_off_branches(self) -> None:
        setpoint_branch = self.pause_branch(
            "Minimalna temperatura med premorom - ciljna temperatura"
        )
        wrong = self.harness(
            self.PAUSE_INPUTS, self.pause_entities("heat", temperature=14.0, setpoint=21.0)
        )
        self.assertTrue(self.render_branch_conditions(setpoint_branch, wrong))
        right = self.harness(
            self.PAUSE_INPUTS, self.pause_entities("heat", temperature=14.0, setpoint=16.0)
        )
        self.assertFalse(self.render_branch_conditions(setpoint_branch, right))
        off_branch = self.pause_branch("Izklopi klimo ob koncu aktivnega urnika")
        turn_off = off_branch["conditions"][1]
        cold = self.harness(
            self.PAUSE_INPUTS, self.pause_entities("heat", temperature=14.0, setpoint=21.0)
        )
        self.assertFalse(cold.render(turn_off["value_template"], cold.render_variables()))
        warm = self.harness(
            self.PAUSE_INPUTS, self.pause_entities("heat", temperature=19.0, setpoint=21.0)
        )
        self.assertTrue(warm.render(turn_off["value_template"], warm.render_variables()))


if __name__ == "__main__":
    unittest.main()