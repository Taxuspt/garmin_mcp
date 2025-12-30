[![MseeP.ai Security Assessment Badge](https://mseep.net/pr/taxuspt-garmin-mcp-badge.png)](https://mseep.ai/app/taxuspt-garmin-mcp)

# Garmin MCP Server

This Model Context Protocol (MCP) server connects to Garmin Connect and exposes your fitness and health data to Claude and other MCP-compatible clients.

Garmin's API is accessed via the awesome [python-garminconnect](https://github.com/cyberjunky/python-garminconnect) library. 

## Features

- List recent activities
- Get detailed activity information
- Access health metrics (steps, heart rate, sleep)
- View body composition data

## Setup

1. Install the required packages on a new environment:

```bash
uv sync
```

## Running the Server

### Configuration

Your Garmin Connect credentials are read from environment variables:

- `GARMIN_EMAIL`: Your Garmin Connect email address
- `GARMIN_EMAIL_FILE`: Path to a file containing your Garmin Connect email address
- `GARMIN_PASSWORD`: Your Garmin Connect password
- `GARMIN_PASSWORD_FILE`: Path to a file containing your Garmin Connect password

File-based secrets are useful in certain environments, such as inside a Docker container. Note that you cannot set both `GARMIN_EMAIL` and `GARMIN_EMAIL_FILE`, similarly you cannot set both `GARMIN_PASSWORD` and `GARMIN_PASSWORD_FILE`.

### With Claude Desktop

1. Create a configuration in Claude Desktop:

Edit your Claude Desktop configuration file:

- macOS: `~/Library/Application Support/Claude/claude_desktop_config.json`
- Windows: `%APPDATA%\Claude\claude_desktop_config.json`

Add this server configuration:

```json
{
  "mcpServers": {
    "garmin": {
      "command": "uvx",
      "args": [
        "--python",
        "3.12",
        "--from",
        "git+https://github.com/Taxuspt/garmin_mcp",
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

Replace the path with the absolute path to your server file.

2. Restart Claude Desktop

### With MCP Inspector

For testing, you can use the MCP Inspector from the project root:

```bash
npx @modelcontextprotocol/inspector uv run garmin-mcp
```

## Usage Examples

Once connected in Claude, you can ask questions like:

- "Show me my recent activities"
- "What was my sleep like last night?"
- "How many steps did I take yesterday?"
- "Show me the details of my latest run"

## Troubleshooting

If you encounter login issues:

1. Verify your credentials are correct
2. Check if Garmin Connect requires additional verification
3. Ensure the garminconnect package is up to date

For other issues, check the Claude Desktop logs at:

- macOS: `~/Library/Logs/Claude/mcp-server-garmin.log`
- Windows: `%APPDATA%\Claude\logs\mcp-server-garmin.log`

### Garming Connect one-time code

If you have one-time codes enabled in your account, you need to login at the command line first to set the token in the interactive cli.

The app expects either the env var GARMIN_EMAIL or GARMIN_EMAIL_FILE. You can store these in files with the following command.

```bash
echo "your_email@example.com" > ~/.garmin_email
echo "your_password" > ~/.garmin_password
chmod 600 ~/.garmin_email ~/.garmin_password
```

Then you can manually run the login script.

```bash
GARMIN_EMAIL_FILE=~/.garmin_email GARMIN_PASSWORD_FILE=~/.garmin_password uvx --python 3.12 --from git+https://github.com/Taxuspt/garmin_mcp garmin-mcp
```

You will likely see

```bash
Garmin Connect MFA required. Please check your email/phone for the code.
Enter MFA code: XXXXXX
Oauth tokens stored in '~/.garminconnect' directory for future use. (first method)

Oauth tokens encoded as base64 string and saved to '~/.garminconnect_base64' file for future use. (second method)
```

After setting the token at the cli, you can use the following in Claude, without the env vars because the Oauth tokens have been set.

```bash
"garmin": {
  "command": "uvx",
  "args": [
    "--python",
    "3.12",
    "--from",
    "git+https://github.com/Taxuspt/garmin_mcp",
    "garmin-mcp"
  ]
}
```

## Testing

This project includes comprehensive tests for all 81 MCP tools. **All 96 tests are currently passing (100%)**.

### Running Tests

```bash
# Run all integration tests (default - uses mocked Garmin API)
uv run pytest tests/integration/

# Run tests with verbose output
uv run pytest tests/integration/ -v

# Run a specific test module
uv run pytest tests/integration/test_health_wellness_tools.py -v

# Run end-to-end tests (requires real Garmin credentials)
uv run pytest tests/e2e/ -m e2e -v
```

### Test Structure

- **Integration tests** (96 tests): Test all MCP tools using FastMCP integration with mocked Garmin API responses
- **End-to-end tests** (4 tests): Test with real MCP server and Garmin API (requires valid credentials)

