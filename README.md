[![MseeP.ai Security Assessment Badge](https://mseep.net/pr/taxuspt-garmin-mcp-badge.png)](https://mseep.ai/app/taxuspt-garmin-mcp)

# Garmin MCP Server

This Model Context Protocol (MCP) server connects to Garmin Connect and exposes your fitness and health data to Claude and other MCP-compatible clients.

Garmin's API is accessed via the awesome [python-garminconnect](https://github.com/cyberjunky/python-garminconnect) library.

## Features

- List recent activities with pagination support
- Get detailed activity information
- Access health metrics (steps, heart rate, sleep, stress, respiration)
- View body composition data
- Track training status and readiness
- Manage gear and equipment
- Access workouts and training plans
- Weekly health aggregates (steps, stress, intensity minutes)

### Tool Coverage

This MCP server implements **95+ tools** covering ~88% of the [python-garminconnect](https://github.com/cyberjunky/python-garminconnect) library (v0.2.38):

- ✅ Activity Management (14 tools)
- ✅ Health & Wellness (30 tools) - includes custom lightweight summary tools
- ✅ Training & Performance (9 tools)
- ✅ Workouts (8 tools)
- ✅ Devices (7 tools)
- ✅ Gear Management (5 tools)
- ✅ Weight Tracking (5 tools)
- ✅ Challenges & Badges (10 tools)
- ✅ Women's Health (3 tools)
- ✅ User Profile (3 tools)

### Intentionally Skipped Endpoints

Some endpoints are not implemented due to performance or complexity considerations:

**High Data Volume:**
- `get_activity_details()` - Returns large GPS tracks and chart data (50KB-500KB). Use `get_activity()` for summaries instead.

**Specialized Workout Formats:**
- `upload_running_workout()`, `upload_cycling_workout()`, `upload_swimming_workout()` - Sport-specific workout uploads. Use `upload_workout()` for general workouts.

**Maintenance & Destructive Operations:**
- `delete_activity()`, `delete_blood_pressure()` - Destructive operations require careful consideration.
- Internal/Auth methods: `login()`, `resume_login()`, `connectapi()`, `download()` - Handled automatically by the library.

If you need any of these endpoints, please [open an issue](https://github.com/Taxuspt/garmin_mcp/issues).

## Setup

### Quick Start for Claude Desktop

The easiest way to use this MCP server with Claude Desktop is to authenticate once before adding the server to your configuration.

#### Prerequisites

- Python 3.12+
- Garmin Connect account
- MFA may be required if enabled on your account

#### Step 1: Pre-authenticate (One-time)

Before adding to Claude Desktop, authenticate once in your terminal:

```bash

# Install and run authentication tool
uvx --python 3.12 --from git+https://github.com/Taxuspt/garmin_mcp garmin-mcp-auth

# You'll be prompted for:
# - Email (or set GARMIN_EMAIL env var)
# - Password (or set GARMIN_PASSWORD env var)
# - MFA code (if enabled on your account)

# OAuth tokens will be saved to ~/.garminconnect
```

**Note:** You can also set credentials via environment variables:
```bash
GARMIN_EMAIL=your@email.com GARMIN_PASSWORD=secret garmin-mcp-auth
```

If you don't have MFA enabled you can also skip `garmin-mcp-auth` and pass `GARMIN_EMAIL` and `GARMIN_PASSWORD` as env variables directly to Claude Desktop (or other MCP client, if supported), see below for an example.

#### Step 2: Configure Claude Desktop

Add to your Claude Desktop MCP settings **WITHOUT** credentials:

**macOS:** `~/Library/Application Support/Claude/claude_desktop_config.json`
**Windows:** `%APPDATA%\Claude\claude_desktop_config.json`

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
      ]
    }
  }
}
```

**Important:** No `GARMIN_EMAIL` or `GARMIN_PASSWORD` needed in config! The server uses your saved tokens.

#### Step 3: Restart Claude Desktop

Your Garmin data is now available in Claude!

---

### Development Setup

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

### With Docker

Docker provides an isolated and consistent environment for running the MCP server.

#### Quick Start with Docker Compose (Recommended)

1. Create a `.env` file with your credentials:

```bash
echo "GARMIN_EMAIL=your_email@example.com" > .env
echo "GARMIN_PASSWORD=your_password" >> .env
```

2. Start the container:

```bash
docker compose up -d
```

3. View logs to monitor the server:

```bash
docker compose logs -f garmin-mcp
```

#### Using Docker Directly

```bash
# Build the image
docker build -t garmin-mcp .

# Run the container
docker run -it \
  -e GARMIN_EMAIL="your_email@example.com" \
  -e GARMIN_PASSWORD="your_password" \
  -v garmin-tokens:/root/.garminconnect \
  garmin-mcp
```

#### Using File-Based Secrets (More Secure)

For enhanced security, especially in production environments, use file-based secrets instead of environment variables:

1. Create a secrets directory and add your credentials:

```bash
mkdir -p secrets
echo "your_email@example.com" > secrets/garmin_email.txt
echo "your_password" > secrets/garmin_password.txt
chmod 600 secrets/*.txt
```

2. Edit [docker-compose.yml](docker-compose.yml) and uncomment the secrets section:

```yaml
services:
  garmin-mcp:
    environment:
      - GARMIN_EMAIL_FILE=/run/secrets/garmin_email
      - GARMIN_PASSWORD_FILE=/run/secrets/garmin_password
    secrets:
      - garmin_email
      - garmin_password

secrets:
  garmin_email:
    file: ./secrets/garmin_email.txt
  garmin_password:
    file: ./secrets/garmin_password.txt
```

3. Start the container:

```bash
docker compose up -d
```

#### Handling MFA with Docker

If you have multi-factor authentication (MFA) enabled on your Garmin account:

1. Run the container in interactive mode:

```bash
docker compose run --rm garmin-mcp
```

2. When prompted, enter your MFA code:

```
Garmin Connect MFA required. Please check your email/phone for the code.
Enter MFA code: 123456
```

3. The OAuth tokens will be saved to the Docker volume (`garmin-tokens`), so you won't need to re-authenticate on subsequent runs.

4. After MFA setup, you can run the container normally:

```bash
docker compose up -d
```

#### Docker Volume Management

The OAuth tokens are stored in a persistent Docker volume to avoid re-authentication:

```bash
# List volumes
docker volume ls

# Inspect the tokens volume
docker volume inspect garmin_mcp_garmin-tokens

# Remove the volume (will require re-authentication)
docker volume rm garmin_mcp_garmin-tokens
```

#### Using with Claude Desktop via Docker

To use the Dockerized MCP server with Claude Desktop, you can configure it to communicate with the container. However, note that MCP servers typically communicate via stdio, which works best with direct process execution. For Docker-based deployments, consider using the standard `uvx` method shown in the [With Claude Desktop](#with-claude-desktop) section instead.

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

### Garmin Connect Multi-Factor Authentication (MFA)

#### Understanding MFA with MCP Servers

MCP servers run as background processes without direct terminal access. If your Garmin account has MFA enabled, you must authenticate once using the pre-authentication tool before the server can run.

#### Recommended: Pre-Authentication Tool

The easiest way to handle MFA is using the dedicated authentication tool:

```bash
garmin-mcp-auth
```

This saves OAuth tokens to `~/.garminconnect` for future use. The server will automatically use these tokens when running in Claude Desktop or other MCP clients.

**Additional Options:**

```bash
# Use environment variables for credentials
GARMIN_EMAIL=you@example.com GARMIN_PASSWORD=secret garmin-mcp-auth

# Verify existing tokens
garmin-mcp-auth --verify

# Force re-authentication (e.g., when tokens expire)
garmin-mcp-auth --force-reauth

# Use custom token location
garmin-mcp-auth --token-path ~/.garmin_tokens
```

#### Alternative: Manual First Run

You can also authenticate by running the server once interactively:

```bash
# Store credentials in files for security
echo "your_email@example.com" > ~/.garmin_email
echo "your_password" > ~/.garmin_password
chmod 600 ~/.garmin_email ~/.garmin_password

# Run server interactively to authenticate
GARMIN_EMAIL_FILE=~/.garmin_email GARMIN_PASSWORD_FILE=~/.garmin_password \
  uvx --python 3.12 --from git+https://github.com/Taxuspt/garmin_mcp garmin-mcp

# Enter MFA code when prompted
# Tokens will be saved automatically
# Now add to Claude Desktop config without credentials
```

After initial authentication, configure Claude Desktop **without** credentials (tokens are already saved):

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
      ]
    }
  }
}
```

#### Using Docker with MFA

If using Docker, follow the [Handling MFA with Docker](#handling-mfa-with-docker) section above for a streamlined experience with persistent token storage.

#### Troubleshooting MFA

**Error: "MFA authentication required but no interactive terminal available"**

Solution:
1. Open terminal
2. Run: `garmin-mcp-auth`
3. Enter credentials and MFA code
4. Restart Claude Desktop

**Token Expired**

OAuth tokens expire periodically (approximately every 6 months). Re-authenticate:
```bash
garmin-mcp-auth --force-reauth
```

**Verify Tokens Work**
```bash
garmin-mcp-auth --verify
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
