# Offline Blueprint Tests

These tests parse the actual apartment climate blueprint, reject duplicate YAML mapping keys, validate `!input` references, compile its Jinja templates, and render selected decision templates with deterministic Home Assistant-like state stubs. They do not run the Home Assistant automation engine or prove full HA schema/runtime compatibility.

## Run on Windows

From the `home-assistant-blueprints` repository root, with Python 3.10 or newer installed:

```powershell
py -3 -m venv .venv
.venv\Scripts\python -m pip install -r requirements-test.txt
.venv\Scripts\python -m unittest discover -s apartment-climate-control\tests -v
```

The test dependencies are isolated in the virtual environment and are not runtime dependencies of Home Assistant.