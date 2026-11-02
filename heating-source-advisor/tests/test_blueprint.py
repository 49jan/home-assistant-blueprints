from __future__ import annotations

import ast
import math
import unittest
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import yaml
from jinja2.nativetypes import NativeEnvironment


REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
BLUEPRINT_PATH = (
    REPOSITORY_ROOT / "heating-source-advisor" / "heating-source-advisor.yaml"
)
FIXED_NOW = datetime(2026, 12, 1, 17, 0, tzinfo=timezone.utc)
HELPER = "input_boolean.ogrevanje_s_tc"
WEATHER = "weather.home"
TREND = "sensor.zunanja_temperatura_3_dni"


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
        if key in result:
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
    last_changed: datetime = FIXED_NOW - timedelta(days=60)
    attributes: dict[str, Any] = field(default_factory=dict)


class StateRegistry:
    def __init__(self, entities: dict[str, EntityState]):
        self.entities = entities

    def __call__(self, entity_id: str) -> str:
        entity = self.entities.get(entity_id)
        return entity.state if entity else "unknown"

    def __getitem__(self, entity_id: str) -> EntityState:
        return self.entities[entity_id]


def flatten_inputs(sections: dict[str, Any]) -> dict[str, dict[str, Any]]:
    result: dict[str, dict[str, Any]] = {}
    for section in sections.values():
        result.update(section["input"])
    return result


def collect_values(value: Any):
    yield value
    if isinstance(value, dict):
        for item in value.values():
            yield from collect_values(item)
    elif isinstance(value, list):
        for item in value:
            yield from collect_values(item)


def daily_forecast(*days: tuple[float, float | None]) -> dict[str, Any]:
    return {
        WEATHER: {
            "forecast": [
                {"temperature": high, "templow": low} for high, low in days
            ]
        }
    }


class BlueprintHarness:
    def __init__(
        self,
        document: dict[str, Any],
        entities: dict[str, EntityState],
        input_overrides: dict[str, Any] | None = None,
    ):
        self.document = document
        self.inputs = {
            name: definition.get("default")
            for name, definition in flatten_inputs(
                document["blueprint"]["input"]
            ).items()
        }
        self.inputs.update(
            {
                "source_helper": HELPER,
                "weather_entity": WEATHER,
                "notify_service": "notify.mobile_app_telefon",
            }
        )
        self.inputs.update(input_overrides or {})
        self.states = StateRegistry(entities)
        self.environment = NativeEnvironment()
        self.environment.tests["is_number"] = self.is_number
        self.environment.globals.update(
            {
                "is_number": self.is_number,
                "is_state": lambda entity_id, value: self.states(entity_id) == value,
                "now": lambda: FIXED_NOW,
                "states": self.states,
            }
        )

    @staticmethod
    def is_number(value: Any) -> bool:
        if value is None or isinstance(value, bool):
            return False
        try:
            return math.isfinite(float(value))
        except (TypeError, ValueError):
            return False

    def render(self, source: str, variables: dict[str, Any]) -> Any:
        rendered = self.environment.from_string(source).render(variables)
        if not isinstance(rendered, str):
            return rendered
        # Home Assistant strips whitespace and parses native types from rendered results.
        scalar = rendered.strip()
        try:
            return ast.literal_eval(scalar)
        except (ValueError, SyntaxError):
            return scalar

    def render_block(
        self, block: dict[str, Any], variables: dict[str, Any]
    ) -> dict[str, Any]:
        for name, value in block.items():
            if isinstance(value, InputReference):
                variables[name] = self.inputs[value.name]
            elif isinstance(value, str) and ("{{" in value or "{%" in value):
                variables[name] = self.render(value, variables)
            else:
                variables[name] = value
        return variables

    def action_variables(self, alias: str | None = None) -> list[dict[str, Any]]:
        return [
            step["variables"]
            for step in self.document["actions"]
            if "variables" in step and (alias is None or step.get("alias") == alias)
        ]

    def evaluate(self, forecast: dict[str, Any]) -> dict[str, Any]:
        variables = self.render_block(self.document["variables"], {})
        variables["forecast"] = forecast
        for block in self.action_variables():
            self.render_block(block, variables)
        return variables


class HeatingSourceAdvisorTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        with BLUEPRINT_PATH.open(encoding="utf-8") as stream:
            cls.document = yaml.load(stream, Loader=BlueprintLoader)

    def evaluate(
        self,
        forecast: dict[str, Any],
        helper_state: str = "off",
        days_since_switch: float = 60,
        trend: str | None = None,
        inputs: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        entities = {
            HELPER: EntityState(
                helper_state, FIXED_NOW - timedelta(days=days_since_switch)
            )
        }
        overrides = dict(inputs or {})
        if trend is not None:
            entities[TREND] = EntityState(trend)
            overrides["trend_sensor"] = TREND
        harness = BlueprintHarness(self.document, entities, overrides)
        return harness.evaluate(forecast)

    def test_input_references_match_definitions(self) -> None:
        inputs = flatten_inputs(self.document["blueprint"]["input"])
        references = {
            value.name
            for value in collect_values(self.document)
            if isinstance(value, InputReference)
        }
        self.assertEqual(references, set(inputs))

    def test_every_template_compiles(self) -> None:
        environment = NativeEnvironment()
        for value in collect_values(self.document):
            if isinstance(value, str) and ("{{" in value or "{%" in value):
                environment.parse(value)

    def test_cold_forecast_while_on_climates_recommends_heat_pump(self) -> None:
        result = self.evaluate(daily_forecast((-2, -10), (-3, -11), (-1, -9), (0, -8), (-2, -9)))
        self.assertEqual(result["forecast_mean"], -5.5)
        self.assertEqual(result["recommendation"], "heat_pump")
        self.assertTrue(result["to_heat_pump"])
        self.assertIn("-5.5 °C", result["message"])
        self.assertIn("vključi gretje radiatorjev", result["message"])

    def test_mild_forecast_while_on_climates_gives_no_recommendation(self) -> None:
        result = self.evaluate(daily_forecast((4, -3), (5, -2), (3, -4), (2, -5), (4, -3)))
        self.assertEqual(result["recommendation"], "keep")

    def test_only_first_forecast_days_are_used(self) -> None:
        days = [(-2, -10)] * 5 + [(15, 10)] * 2
        result = self.evaluate(daily_forecast(*days))
        self.assertEqual(result["daily_means"], [-6.0] * 5)
        self.assertEqual(result["recommendation"], "heat_pump")

    def test_warm_forecast_and_trend_on_heat_pump_recommend_climates(self) -> None:
        result = self.evaluate(
            daily_forecast((8, 0), (9, 1), (7, 2), (10, 3), (9, 1)),
            helper_state="on",
            trend="4.6",
        )
        self.assertEqual(result["recommendation"], "climates")
        self.assertIn("zadnji dnevi 4.6 °C", result["message"])
        self.assertIn("izklopi gretje radiatorjev", result["message"])

    def test_cold_trend_blocks_return_to_climates(self) -> None:
        result = self.evaluate(
            daily_forecast((8, 0), (9, 1), (7, 2), (10, 3), (9, 1)),
            helper_state="on",
            trend="-1.0",
        )
        self.assertEqual(result["recommendation"], "keep")

    def test_return_to_climates_without_trend_sensor_uses_forecast(self) -> None:
        result = self.evaluate(
            daily_forecast((8, 0), (9, 1), (7, 2), (10, 3), (9, 1)), helper_state="on"
        )
        self.assertIsNone(result["trend"])
        self.assertEqual(result["recommendation"], "climates")

    def test_recent_switch_blocks_recommendation(self) -> None:
        result = self.evaluate(
            daily_forecast((8, 0), (9, 1), (7, 2), (10, 3), (9, 1)),
            helper_state="on",
            days_since_switch=5,
        )
        self.assertEqual(result["recommendation"], "keep")

    def test_empty_or_missing_forecast_gives_no_recommendation(self) -> None:
        for forecast in ({WEATHER: {"forecast": []}}, {}):
            result = self.evaluate(forecast)
            self.assertIsNone(result["forecast_mean"])
            self.assertEqual(result["recommendation"], "keep")

    def test_missing_templow_uses_daily_temperature(self) -> None:
        result = self.evaluate(daily_forecast((-6, None), (-7, None)), inputs={"forecast_days": 2})
        self.assertEqual(result["daily_means"], [-6.0, -7.0])
        self.assertEqual(result["recommendation"], "heat_pump")

    def test_climates_threshold_stays_above_heat_pump_threshold(self) -> None:
        result = self.evaluate(
            daily_forecast((0, -2)), inputs={"heat_pump_below": -3, "climates_above": -5}
        )
        self.assertEqual(result["climates_above"], -2.0)

    def test_notification_button_ids_include_helper(self) -> None:
        result = self.evaluate(daily_forecast((0, -2)))
        self.assertEqual(result["action_heat_pump"], f"HSA_HEAT_PUMP_{HELPER}")
        self.assertEqual(result["action_climates"], f"HSA_CLIMATES_{HELPER}")


if __name__ == "__main__":
    unittest.main()
