# Garmin MCP Server - Project Guide

## Overview

This is a Model Context Protocol (MCP) server that connects to Garmin Connect and exposes fitness and health data to Claude and other MCP-compatible clients. The server provides 157 tools organized into 16 functional modules. `scripts/generate_dxt_tools.py` registers the modules with a mocked client to produce the tool list in `dxt/manifest.json`.

## Critical: Garmin API Integration

**The integration with Garmin's API is done via the [python-garminconnect](https://github.com/cyberjunky/python-garminconnect) library.**

### When Debugging or Implementing New Tools

**Read the installed version of python-garminconnect in `.venv/lib/python*/site-packages/garminconnect/` first.** Its `__init__.py`, `client.py`, and `fit.py` are the source of truth for available methods, signatures, request payloads, and response handling. Upstream examples can describe a newer version than this project uses.

For supplementary examples, consult the python-garminconnect repository:

1. **[example.py](https://github.com/cyberjunky/python-garminconnect/blob/master/example.py)** - Contains examples for many available API endpoints
2. **[demo.py](https://github.com/cyberjunky/python-garminconnect/blob/master/demo.py)** - Comprehensive demo with 100+ API methods organized by category

Check these against the installed library when looking for:
- **Available API endpoints** - Check if a method actually exists in the library
- **Correct method signatures** - Verify parameter names and types
- **Expected output formats** - See what data structure each endpoint returns
- **Method names** - Confirm the exact name of Garmin client methods

### Library Version

Currently using: `garminconnect==0.3.2` (see [pyproject.toml](pyproject.toml))

### Important Note

Not all Garmin Connect API endpoints have corresponding methods in python-garminconnect. Before implementing a new tool:
1. Search the installed library source for the method and read its implementation.
2. Check [example.py](https://github.com/cyberjunky/python-garminconnect/blob/master/example.py) and [demo.py](https://github.com/cyberjunky/python-garminconnect/blob/master/demo.py) for supplementary examples.
3. Verify that any examples apply to the pinned version.

If a method doesn't exist in python-garminconnect, you'll need to either:
- Use an alternative method that provides similar data
- Request the feature in the python-garminconnect repository
- Implement a custom API call if absolutely necessary

## Project Structure

```
src/garmin_mcp/
├── __init__.py                 # Main MCP server entry point
├── __main__.py                 # Module entry point
├── activity_analysis.py        # Activity analysis and FIT file parsing
├── activity_management.py      # Activity CRUD operations
├── auth_cli.py                 # Pre-authentication CLI (garmin-mcp-auth)
├── calendar_events.py          # Calendar events and scheduled activities
├── challenges.py               # Challenges and badges
├── courses.py                  # Course management
├── data_management.py          # Data upload/download operations
├── devices.py                  # Device information
├── gear_management.py          # Gear tracking
├── health_wellness.py          # Health metrics (steps, HR, sleep, etc.)
├── http_auth.py                # Bearer authentication for HTTP transports
├── nutrition.py                # Nutrition and food logging
├── tool_annotations.py         # MCP tool behavior annotations
├── tool_profiles.py            # Named subsets of registered tools
├── token_utils.py              # OAuth token management utilities
├── training.py                 # Training and performance metrics
├── user_profile.py             # User account information
├── weight_management.py        # Weight and body composition
├── womens_health.py            # Women's health tracking
├── workout_builders.py         # Programmatic workout creation
├── workout_templates.py        # Workout template helpers
├── workouts.py                 # Workout management
└── write_errors.py             # Write outcome tracking and retry guidance

tests/
├── unit/                       # Helpers, server configuration, auth, packaging
├── fixtures/                   # Shared mock API responses and captured payloads
├── integration/                # Tool calls with mocked Garmin API
│   ├── test_activity_analysis_tools.py
│   ├── test_activity_management_tools.py
│   ├── test_calendar_events_tools.py
│   ├── test_challenges_tools.py
│   ├── test_courses_tools.py
│   ├── test_health_wellness_tools.py
│   ├── test_nutrition_tools.py
│   ├── test_other_modules_tools.py
│   ├── test_training_tools.py
│   ├── test_workout_builders_tools.py
│   └── test_workouts_tools.py
└── e2e/                        # Live tests; require real credentials and may write
    ├── test_brand_and_micros_live.py
    ├── test_delete_custom_food_live.py
    ├── test_delete_food_log_live.py
    ├── test_nested_workout_target_values_live.py
    ├── test_server_e2e.py
    └── test_upsert_dedup_and_update_merge_live.py
```

## Architecture

### Module Pattern

Each functional module follows this pattern:

```python
# Global client reference (set by main)
garmin_client = None

def configure(client):
    """Configure the module with the Garmin client instance"""
    global garmin_client
    garmin_client = client

def register_tools(app):
    """Register all tools with the MCP server app"""

    @app.tool()
    async def tool_name(param: str) -> str:
        """Tool description"""
        try:
            result = garmin_client.method_name(param)
            if not result:
                return f"No data found."
            return result
        except Exception as e:
            return f"Error: {str(e)}"

    return app
```

### Main Server ([__init__.py](src/garmin_mcp/__init__.py))

The main server:
1. Initializes the Garmin Connect client
2. Configures all modules with the client instance
3. Registers all tools from each module
4. Handles authentication (including MFA)

## Authentication

Garmin credentials are read from environment variables:
- `GARMIN_EMAIL` or `GARMIN_EMAIL_FILE`
- `GARMIN_PASSWORD` or `GARMIN_PASSWORD_FILE`

For accounts with MFA enabled, use the pre-authentication CLI before starting the MCP server:

```bash
garmin-mcp-auth
```

This saves OAuth tokens to `~/.garminconnect` so the MCP server can start without an interactive terminal. Token utilities are in [token_utils.py](src/garmin_mcp/token_utils.py).

## Development Guidelines

### Adding a New Tool

1. **Check python-garminconnect first**: Read the method in the installed, pinned library and verify its signature and payload handling.

2. **Choose the correct module**: Add the tool to the appropriate module based on functionality

3. **Follow the pattern**:
   ```python
   @app.tool()
   async def get_something(param: str) -> str:
       """Clear description of what the tool does

       Args:
           param: Description of parameter
       """
       try:
           result = garmin_client.garminconnect_method(param)
           if not result:
               return f"No data found."
           return result
       except Exception as e:
           return f"Error: {str(e)}"
   ```

4. **Add tests**: Create integration tests in `tests/integration/test_[module]_tools.py`

### Error Handling

All tools should:
- Wrap API calls in try/except blocks
- Return user-friendly error messages
- Handle empty/null responses gracefully

## Testing

Unit and integration tests run offline with mocked Garmin clients. Obtain the current test count with `uv run pytest tests/unit tests/integration --collect-only -q`.

```bash
# Run the offline suite
uv run pytest tests/unit tests/integration -q -p no:cacheprovider

# Run specific module tests
uv run pytest tests/integration/test_health_wellness_tools.py -v
```

**End-to-end tests** use the real MCP server and Garmin API. They are deselected by default, require credentials, and can create or delete foods, food logs, and workouts. Run them only when explicitly requested:

```bash
uv run pytest tests/e2e/ -m e2e -v
```

### Test Structure

Each tool should have at least two tests:
1. **Success case**: Verify the tool works with expected data
2. **Exception case**: Verify error handling when the API fails

Example:
```python
async def test_tool_success(app_with_module, mock_garmin_client):
    """Test tool with valid response"""
    mock_garmin_client.method.return_value = {"data": "value"}
    result = await call_tool(app_with_module, "tool_name", {"param": "value"})
    assert "value" in result

async def test_tool_exception(app_with_module, mock_garmin_client):
    """Test tool when API raises exception"""
    mock_garmin_client.method.side_effect = Exception("API Error")
    result = await call_tool(app_with_module, "tool_name", {"param": "value"})
    assert "Error" in result
```

## Running the Server

### With MCP Inspector (for testing)

```bash
npx @modelcontextprotocol/inspector uv run garmin-mcp
```

### With Claude Desktop

Add to `~/Library/Application Support/Claude/claude_desktop_config.json`:

```json
{
  "mcpServers": {
    "garmin": {
      "command": "uvx",
      "args": [
        "--python", "3.12",
        "--from", "git+https://github.com/Taxuspt/garmin_mcp",
        "garmin-mcp"
      ],
      "env": {
        "GARMIN_EMAIL": "YOUR_GARMIN_EMAIL",
        "GARMIN_PASSWORD": "YOUR_GARMIN_PASSWORD"
      }
    }
  }
}
```

## Common Patterns

### Date Parameters

Many tools accept dates in `YYYY-MM-DD` format:
```python
async def get_data(date: str) -> str:
    """Get data for a specific date

    Args:
        date: Date in YYYY-MM-DD format
    """
```

### Activity IDs

Activity IDs are typically large integers (e.g., `12345678901`):
```python
async def get_activity_detail(activity_id: int) -> str:
```

### Return Types

All tools return `str` (MCP requirement). Complex objects are returned as JSON strings.

## Troubleshooting

### Method doesn't exist in garminconnect

Search the installed library source first. Examples in the upstream repository may target a different version and do not list every method.

### Unexpected output format

Read the installed method's response handling and check captured fixtures. The Garmin API can return different structures than expected; a mocked reproduction verifies local handling without contacting Garmin.

### Authentication issues

For MFA accounts, run `garmin-mcp-auth` first to pre-authenticate and save tokens.

Check logs at:
- macOS: `~/Library/Logs/Claude/mcp-server-garmin.log`
- Windows: `%APPDATA%\Claude\logs\mcp-server-garmin.log`

## Key Dependencies

- `garminconnect==0.3.2` - Garmin Connect API wrapper
- `mcp>=1.28.1,<2` - Model Context Protocol SDK
- `fitparse>=1.2.0` - FIT file parsing for activity analysis
- `python-dotenv==1.2.2` - Environment variable management
- `requests==2.33.0` - HTTP client

## Resources

- [python-garminconnect GitHub](https://github.com/cyberjunky/python-garminconnect)
- [example.py - API examples](https://github.com/cyberjunky/python-garminconnect/blob/master/example.py)
- [demo.py - Comprehensive API demo](https://github.com/cyberjunky/python-garminconnect/blob/master/demo.py)
- [MCP Documentation](https://modelcontextprotocol.io/)
