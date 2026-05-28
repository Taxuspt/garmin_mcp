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
- Smart historical analytics: personal baselines, anomalies, lagged correlations,
  and weekly health review summaries
- Custom health reports that can be grouped, saved locally, and rerun
- Built-in auth tools to check saved tokens and log in with OTP only when Garmin
  asks for it

### Tool Coverage

This MCP server implements **95+ tools** covering ~88% of the [python-garminconnect](https://github.com/cyberjunky/python-garminconnect) library (v0.2.38):

- Activity Management (14 tools)
- Health & Wellness (30 tools) - includes custom lightweight summary tools
- Training & Performance (9 tools)
- Workouts (8 tools)
- Devices (7 tools)
- Gear Management (5 tools)
- Weight Tracking (5 tools)
- Challenges & Badges (10 tools)
- Women's Health (3 tools)
- User Profile (3 tools)
- Smart Analytics (8 tools)
- Authentication (2 tools)

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

### Tool Reference

Most Garmin data tools are direct wrappers around `python-garminconnect`.
The tools below are extra server tools for authentication, analytics, and saved
custom reports.

#### Authentication Tools

These tools are available even before Garmin login is complete. This lets the
MCP server start cleanly in Claude Desktop, then finish login from Claude.

| Tool | What it does | Main inputs | Typical result |
| --- | --- | --- | --- |
| `check_garmin_auth` | Checks whether saved Garmin tokens exist and still work. | `token_path` | `authenticated`, token path, token status, and next step |
| `login_to_garmin` | Logs in to Garmin Connect, handles OTP only when Garmin asks, saves tokens, and activates the running server. | `email`, `password`, `otp_code`, `token_path`, `token_base64_path`, `force_reauth` | `authenticated`, `missing_credentials`, `mfa_required`, or `failed` |

Use `check_garmin_auth` first. If it says tokens are missing or invalid, run
`login_to_garmin`. Accounts without OTP save tokens in one call. Accounts with
OTP return `mfa_required`, then you run `login_to_garmin` again with the code.

#### Smart Analytics Tools

The analytics tools fetch a bounded Garmin history window and return compact
derived results instead of full raw Garmin payloads. The default window is
90 days and the hard cap is 180 days, so calls stay manageable for MCP clients.

| Tool | What it does | Main inputs | Typical result |
| --- | --- | --- | --- |
| `get_health_baselines` | Compares current health metrics with personal rolling baselines. | `end_date`, `days`, `baseline_window` | Latest value, baseline, delta, and direction per metric |
| `get_wellness_anomalies` | Finds unusual days using z-scores against recent history. | `end_date`, `days`, `baseline_window`, `z_threshold` | Count and list of unusual metric days |
| `get_lagged_health_correlations` | Checks whether one metric tends to lead another by 1 to 14 days. | `end_date`, `days`, `max_lag_days` | Strongest delayed metric relationships |
| `get_weekly_health_review` | Compares the last 7 days with the prior 7 days. | `end_date` | Weekly comparison, notable anomalies, and delayed relationships |
| `list_health_report_metrics` | Lists supported metric keys, grouping options, and aggregation options. | None | Metrics, units, directions, groups, aggregations, and report store path |
| `run_custom_health_report` | Runs an ad hoc or saved grouped health report. | `end_date`, `days`, `metrics`, `group_by`, `aggregation`, `saved_report_name`, `include_daily_rows` | Grouped rows, chart hint, and optional daily raw rows |
| `save_custom_health_report` | Saves a reusable custom report definition locally. | `name`, `metrics`, `group_by`, `aggregation`, `days`, `end_date`, `description` | Saved report definition |
| `list_saved_health_reports` | Lists locally saved custom report definitions. | None | Report store path, count, and saved reports |

Custom reports support these report options:

- Groups: `date`, `week`, `month`
- Aggregations: `avg`, `sum`, `min`, `max`, `latest`
- Default metrics: `steps`, `sleep_score`, `stress_avg`, `overnight_hrv`,
  `training_readiness`, `recovery_pressure`
- Raw rows: set `include_daily_rows` to `true` in `run_custom_health_report`

Saved report definitions are stored in `~/.garmin_mcp_reports.json` by default.
Set `GARMIN_REPORTS_PATH` if you want a different JSON file location.

## Setup

### Desktop Extension (DXT)

This branch includes a Desktop Extension manifest for Claude Desktop.

Build it locally:

```bash
./scripts/build_dxt.sh
```

The built file is `garmin-mcp.dxt`.

The DXT package includes the server source, `pyproject.toml`, and `uv.lock`.
It runs from Claude Desktop's installed extension directory through
`${__dirname}`, so it does not depend on this repository path after install.

When installing the extension, use a persistent token directory. If Garmin sends
you an email or phone verification code during first login, paste that code into
the one-time MFA code field. After the first successful login, the server saves
OAuth tokens in the token directory, and you can leave the MFA code blank.

Claude Desktop's DXT manifest does not provide a custom pre-save login button.
This extension handles that inside the MCP server instead. The server starts
even when Garmin is not authenticated, so you can ask Claude to:

1. Run `check_garmin_auth` to see whether saved tokens exist and still work.
2. Run `login_to_garmin` with your email and password if tokens are missing.
3. If Garmin asks for a code, run `login_to_garmin` again with the same email
   and password plus `otp_code`.

Accounts without OTP save tokens in the first login call. Accounts with OTP get
an `mfa_required` response first, then save tokens after the OTP call. Use a
persistent token directory such as `~/.garminconnect` if you want to reuse the
same Garmin session across DXT rebuilds.

On macOS, `login_to_garmin` can also ask for missing credentials through a local
system dialog. This keeps the password out of chat and out of the MCP config.
Set `GARMIN_DISABLE_LOCAL_PROMPTS=1` if you want to disable those dialogs and
require credentials through tool arguments or environment variables only.

### Authentication Flow

The server can now start even when Garmin is not authenticated. This is useful
for Claude Desktop because MCP servers run in the background and cannot always
ask for terminal input.

Use this flow for a new setup:

1. Start the server or install the DXT.
2. Run `check_garmin_auth`.
3. If tokens are missing or invalid, run `login_to_garmin`.
4. If Garmin asks for a code, enter the OTP when the tool asks for it.
5. Run `check_garmin_auth` again to confirm the saved tokens work.

The token directory must be persistent. The default is `~/.garminconnect`.
Inside that directory, Garmin token files such as `oauth1_token.json` and
`oauth2_token.json` are created after a successful login.

Important behavior:

- An empty token directory is normal before the first login.
- A missing `oauth1_token.json` file means tokens have not been saved yet.
- The server should still stay online, so you can run the auth tools.
- During email and password login, the server temporarily ignores
  `GARMINTOKENS`. This avoids a Garmin library issue where an empty token
  directory is loaded before the password login can start.

Credential options:

- Pass `email` and `password` directly to `login_to_garmin`.
- Set `GARMIN_EMAIL` and `GARMIN_PASSWORD` in the MCP server environment.
- Use `GARMIN_EMAIL_FILE` and `GARMIN_PASSWORD_FILE` for file-based secrets.
- On macOS, leave the password out of config and let the local hidden dialog ask
  for it when `login_to_garmin` runs.

Security notes:

- Garmin credentials are only needed to create or refresh tokens.
- Saved tokens are reused after login, so normal data tools do not need the
  password.
- Do not commit passwords, token files, `.env` files, or local Claude Desktop
  config files.
- If you want to remove access, delete the token directory and authenticate
  again later.

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

You can verify your credentials at any time with
```bash
uv run garmin-mcp-auth --verify
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

### Testing the server locally with MCP Inspector

The Inspector runs directly through npx without requiring installation. Run from the project root:

```bash
npx @modelcontextprotocol/inspector uv run garmin-mcp
```

You'll be able to inspect and test the tools.

### With Claude Desktop

1. Create a configuration in Claude Desktop:

Edit your Claude Desktop configuration file:

- macOS: `~/Library/Application Support/Claude/claude_desktop_config.json`
- Windows: `%APPDATA%\Claude\claude_desktop_config.json`

You have two options to run the MCP locally with Claude.

#### Directly from github without cloning the repo:

1. Add this server configuration:

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

You might have to add the full path to `uvx` you can check the full path with `which uvx`

2. Restart Claude Desktop

#### Directly from your local copy of the repository:

1. Add this server configuration:

```
{
  "mcpServers": {
    "garmin-local": {
      "command": "uv",
      "args": [
        "--directory",
        "<full path to your local repository>/garmin_mcp",
        "run",
        "garmin-mcp"
      ]
    }
  }
}
```

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


## Usage Examples

Once connected in Claude, you can ask questions like:

- "Show me my recent activities"
- "What was my sleep like last night?"
- "How many steps did I take yesterday?"
- "Show me the details of my latest run"

## Troubleshooting

### "Failed to spawn process: No such file or directory"

If Claude Desktop can't find `uvx`, it's because `uvx` is not in the PATH that Claude Desktop uses. To fix this:

1. Find where `uvx` is installed:
```bash
which uvx
```

2. Use the full path in your configuration. For example, if `uvx` is at `/Users/username/.cargo/bin/uvx`:
```json
{
  "mcpServers": {
    "garmin": {
      "command": "/Users/username/.cargo/bin/uvx",
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

### Login Issues

If you encounter login issues:

1. Verify your credentials are correct
2. Check if Garmin Connect requires additional verification
3. Ensure the garminconnect package is up to date

### Logs

For other issues, check the Claude Desktop logs at:

- macOS: `~/Library/Logs/Claude/mcp-server-garmin.log`
- Windows: `%APPDATA%\Claude\logs\mcp-server-garmin.log`

### Garmin Connect Multi-Factor Authentication (MFA)

MFA can be handled in two ways. You can use the MCP auth tools from Claude, or
you can authenticate once in a terminal before starting Claude Desktop.

#### Option 1: Use the MCP Auth Tools

This is the best option when the server is already visible in Claude.

1. Run `check_garmin_auth`.
2. Run `login_to_garmin`.
3. Enter your Garmin email and password when asked.
4. If Garmin sends an OTP, enter that code when asked.
5. After success, the tokens are saved and normal Garmin tools can run.

On macOS, the tool can ask for the password and OTP in local system dialogs.
This means you do not need to paste the password into chat. If the dialog does
not appear, check behind the Claude window.

#### Option 2: Pre-Authentication Tool

You can also authenticate in a terminal with the dedicated authentication tool:

```bash
garmin-mcp-auth
```

This saves OAuth tokens to `~/.garminconnect` for future use. The server will
automatically use these tokens when running in Claude Desktop or other MCP
clients.

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

#### Option 3: Manual First Run

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

After initial authentication, configure Claude Desktop **without** credentials.
The tokens are already saved:

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

If using Docker, follow the [Handling MFA with Docker](#handling-mfa-with-docker)
section above for a simple setup with persistent token storage.

#### Troubleshooting MFA

**Error: "MFA authentication required but no interactive terminal available"**

Solution:
1. Run `login_to_garmin` from Claude if the auth tools are available.
2. Or open a terminal and run `garmin-mcp-auth`.
3. Enter credentials and MFA code.
4. Restart Claude Desktop.

**Error: "Token files not found" or "oauth1_token.json" missing**

This usually means the token directory exists, but no login has saved tokens
yet. It does not always mean the directory itself is missing.

Solution:
1. Keep the token directory, for example `~/.garminconnect`.
2. Run `login_to_garmin` or `garmin-mcp-auth`.
3. After login, verify with `check_garmin_auth` or `garmin-mcp-auth --verify`.

**Token Expired**

OAuth tokens expire periodically (approximately every 6 months). Re-authenticate:
```bash
garmin-mcp-auth --force-reauth
```

**Verify Tokens Work**
```bash
garmin-mcp-auth --verify
```

## Remote Mode (Multi-User, HTTP + OAuth2)

Remote mode runs the MCP server over HTTP with OAuth2 authentication, enabling multi-user access. Each user authenticates directly with their **Garmin Connect credentials** during the OAuth2 flow - no pre-created accounts or manual account linking required.

### How It Works

When a client (e.g., Claude) connects to the remote server:

1. The client discovers the OAuth2 endpoints and initiates authorization
2. The user is redirected to a login page asking for their **Garmin Connect email and password**
3. If 2FA is enabled on the Garmin account, a second page asks for the verification code
4. On success, the Garmin session tokens are stored server-side and an OAuth2 access token is returned to the client
5. The client uses this token to access all Garmin tools - no extra setup needed

```
Client -> 401 -> OAuth2 discovery
  -> /authorize -> redirect to /login?state=...
  -> User enters Garmin email + password
  -> POST /login/callback
      +-- No 2FA -> create user + session -> redirect with auth code
      +-- 2FA required -> redirect to /login/mfa?state=...
           -> User enters verification code
           -> POST /login/mfa/callback -> create user + session -> redirect with auth code
  -> Client exchanges code for tokens -> access granted
```

### Security

- Garmin credentials are **never stored** - only garth OAuth tokens are persisted on disk
- The 2FA client state is held in memory only, with a 5-minute TTL and single-use (`pop`)
- Users are identified by their Garmin email (upserted on login)

### Running the Remote Server

#### With Docker Compose (Recommended)

```bash
# Set the public URL of your server
export GARMIN_MCP_SERVER_URL=https://garmin-mcp.example.com

# Start
docker compose -f docker-compose.remote.yml up -d
```

#### Locally

```bash
export GARMIN_MCP_SERVER_URL=http://localhost:8000

uv run garmin-mcp-remote
```

### Configuration (Environment Variables)

| Variable | Default | Description |
|---|---|---|
| `GARMIN_MCP_SERVER_URL` | *(required)* | Public URL of the server |
| `GARMIN_MCP_HOST` | `0.0.0.0` | Listen address |
| `GARMIN_MCP_PORT` | `8000` | Listen port |
| `GARMIN_MCP_PATH` | `/mcp` | MCP endpoint path |
| `DB_PATH` | `/data/garmin_mcp.db` | SQLite database path |
| `SESSION_STORAGE_PATH` | `/data/garmin_sessions` | Garth token storage |

### Testing with MCP Inspector

```bash
npx @modelcontextprotocol/inspector http://localhost:8000/mcp
```

## Testing

This project includes comprehensive tests for the MCP tools. **All 175 tests are currently passing (100%)**.

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

- **Unit tests** (52 tests): Test auth helpers, token path handling, and CLI auth behavior
- **Integration tests** (122 tests): Test MCP tools using FastMCP integration with mocked Garmin API responses
- **Debug smoke test** (1 test): Confirms the MCP app can be reached directly
- **End-to-end tests** (4 tests): Test with real MCP server and Garmin API (requires valid credentials)
