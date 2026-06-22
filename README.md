[![MseeP.ai Security Assessment Badge](https://mseep.net/pr/taxuspt-garmin-mcp-badge.png)](https://mseep.ai/app/taxuspt-garmin-mcp)

# Garmin MCP Server

This Model Context Protocol (MCP) server connects to Garmin Connect and exposes your fitness and health data to Claude and other MCP-compatible clients.

Garmin's API is accessed via the awesome [python-garminconnect](https://github.com/cyberjunky/python-garminconnect) library.

## Features

- List recent activities with pagination support
- Get detailed activity information
- Manage activity names
- Access health metrics (steps, heart rate, sleep, stress, respiration)
- View body composition data
- Track training status and readiness
- Access cycling FTP and lactate threshold metrics
- Manage gear and equipment
- Access workouts and training plans
- Inspect detailed workout step structures, including repeat groups and swim pace targets
- Weekly health aggregates (steps, stress, intensity minutes)
- Advanced cycling analytics: power zones, FIT file analysis, DI2 electronic shift intelligence
- Training load trend (CTL/ATL/TSB), HRV trend, VO2 max trend, respiration rate trend
- Power Duration Curve, climb detection with VAM, cardiac drift (aerobic decoupling), W/kg calculations

### Tool Coverage

This MCP server implements **110+ tools** covering ~90% of the [python-garminconnect](https://github.com/cyberjunky/python-garminconnect) library (v0.3.2):

- ✅ Activity Management (15 tools)
- ✅ Health & Wellness (31 tools) - includes custom lightweight summary tools
- ✅ Training & Performance (13 tools) - includes CTL/ATL/TSB, HRV, VO2 max, and respiration trends
- ✅ Workouts (8 tools)
- ✅ Devices (7 tools)
- ✅ Gear Management (5 tools)
- ✅ Weight Tracking (5 tools)
- ✅ Challenges & Badges (10 tools)
- ✅ Nutrition (8 tools) - food logs, meals, custom foods, and food logging
- ✅ Women's Health (3 tools)
- ✅ User Profile (3 tools)
- ✅ High-Level Workout Builders (4 tools) - create and schedule workouts without writing JSON
- ✅ Courses (3 tools) - list / upload GPX as course / delete course
- ✅ Activity Analysis (2 tools) - FIT file parsing, Power Duration Curve; requires power meter and/or Di2
- ✅ Activity File Downloads (2 tools) - download activity files in FIT, GPX, TCX, or CSV format

> **Note:** Activity Analysis tools require a compatible power meter (e.g., Garmin Rally, Favero Assioma, PowerTap P1) and/or Shimano Di2 / SRAM eTap electronic shifting. The `fitparse` dependency is installed automatically.

### Activity File Downloads

Two tools let you download a raw activity file to disk:

- **`download_activity_file(activity_id, format="fit", output_dir=None)`** — downloads the activity and saves it to the configured directory. `format` accepts `fit` (default), `gpx`, `tcx`, or `csv`.
- **`set_fit_download_dir(path)`** — sets and persists the default download directory (written to the config file).

**Where files are saved (precedence):**

1. `output_dir` argument — one-off override, not persisted.
2. `GARMIN_FIT_DOWNLOAD_DIR` environment variable.
3. Persisted config set via `set_fit_download_dir`.

**First-run behavior:** if no directory is configured, `download_activity_file` returns `status: "needs_setup"`. The assistant will ask where you want to save files (suggesting the current directory as default), call `set_fit_download_dir` to persist your choice, and then retry the download automatically.

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

## Tool Filtering

This server registers 110+ tools by default, which can be a lot of context for
an LLM to carry in every session. You can expose only the tools you need with
two optional environment variables:

| Env var | Effect |
|---|---|
| `GARMIN_ENABLED_TOOLS` | Comma-separated **allowlist** — if set, *only* these tools are registered. |
| `GARMIN_DISABLED_TOOLS` | Comma-separated **denylist** — listed tools are skipped. Ignored if an allowlist is set. |

Tool names are case-insensitive. With neither variable set, all tools register
(unchanged default behaviour). Names that match no tool are ignored with a
warning on stderr, which makes typos easy to spot.

Example — expose only sleep, stress, and recent activities:

```json
"env": {
  "GARMIN_ENABLED_TOOLS": "get_sleep_data,get_stress_summary,get_activities"
}
```

## High-level workout tools

These builder tools let an LLM create and schedule workouts without writing raw Garmin JSON.

### `create_walk_run_workout`

Creates a walk/run interval workout with optional heart-rate zone target.

```json
{
  "name": "W3 Mié 2:2",
  "run_seconds": 120,
  "walk_seconds": 120,
  "repeats": 9,
  "warmup_min": 10,
  "cooldown_min": 8,
  "hr_zone": "Z3"
}
```

Returns: `{"status": "success", "workout_id": 1234567890, ...}`

### `create_z2_walk_workout`

Creates a steady Z2 walking workout.

```json
{
  "name": "Z2 Walk 45m",
  "duration_min": 45,
  "hr_min": 110,
  "hr_max": 130
}
```

Returns: `{"status": "success", "workout_id": 1234567890, ...}`

### `create_strength_workout`

Creates a strength workout from a list of exercises. Unknown names fall back to a generic step with the original name preserved.

```json
{
  "name": "Full Body A",
  "exercises": [
    {"name": "Sentadillas", "sets": 3, "reps": 12, "rest_seconds": 90},
    {"name": "Flexiones",   "sets": 3, "reps": 15, "rest_seconds": 60},
    {"name": "Peso muerto", "sets": 3, "reps": 10, "rest_seconds": 90}
  ]
}
```

Returns: `{"status": "success", "workout_id": 1234567890, ...}`

### `schedule_week`

Schedules multiple workouts in one call.

```json
{
  "week": [
    {"date": "2026-05-12", "workout_id": 1234567890},
    {"date": "2026-05-14", "workout_id": 1234567891}
  ]
}
```

Returns: `{"status": "complete", "scheduled": [...]}`

### Full flow example

```text
create_walk_run_workout(name="W3 Mié 2:2", run_seconds=120, walk_seconds=120,
                        repeats=9, warmup_min=10, cooldown_min=8)
  → workout_id = 1560092011

schedule_workout(workout_id=1560092011, date="2026-05-06")
  → OK
```

After syncing your watch, the workout appears on the Forerunner 965 calendar.

### Raw `upload_workout` end conditions

When building custom workout JSON for `upload_workout` or `upload_workouts`, the
`endCondition.conditionTypeId` and `endCondition.conditionTypeKey` must match
Garmin's canonical mapping. Garmin treats the numeric `conditionTypeId` as the
source of truth; if the key and ID conflict, Garmin stores the condition that
matches the ID.

For example, this is invalid for a heart-rate end condition because ID `4` is
`calories`, not `heart.rate`:

```json
{
  "endCondition": {
    "conditionTypeId": 4,
    "conditionTypeKey": "heart.rate"
  },
  "endConditionValue": 145
}
```

Use ID `6` for heart rate:

```json
{
  "endCondition": {
    "conditionTypeId": 6,
    "conditionTypeKey": "heart.rate"
  },
  "endConditionValue": 145
}
```

Common end-condition IDs:

| ID | Key |
|---:|---|
| 1 | `lap.button` |
| 2 | `time` |
| 3 | `distance` |
| 4 | `calories` |
| 5 | `power` |
| 6 | `heart.rate` |
| 7 | `iterations` |
| 8 | `fixed.rest` |
| 9 | `fixed.repetition` |
| 10 | `reps` |
| 11 | `training.peaks.tss` |

### Raw `upload_workout` target types

When building raw Garmin workout JSON, `targetType.workoutTargetTypeId` and
`targetType.workoutTargetTypeKey` must use Garmin's canonical mapping. Garmin
treats the numeric ID as authoritative: a mismatched payload such as
`{"workoutTargetTypeId": 6, "workoutTargetTypeKey": "heart.rate"}` is stored as
`pace.zone`, because ID `6` means `pace.zone`.

For a custom heart-rate range, use target type ID `4` with `heart.rate.zone` and
put the bpm range in `targetValueOne` / `targetValueTwo`:

```json
{
  "targetType": {
    "workoutTargetTypeId": 4,
    "workoutTargetTypeKey": "heart.rate.zone"
  },
  "targetValueOne": 143,
  "targetValueTwo": 157
}
```

For a named Garmin HR zone, use the same target type with `zoneNumber` instead:

```json
{
  "targetType": {
    "workoutTargetTypeId": 4,
    "workoutTargetTypeKey": "heart.rate.zone"
  },
  "zoneNumber": 3
}
```
## One-click Install (Claude Desktop)

The easiest way to add this server to Claude Desktop is via the `.dxt` Desktop Extension file — no JSON editing required.

### Download and install

1. Download the latest `garmin-mcp.dxt` from the [Releases page](https://github.com/Taxuspt/garmin_mcp/releases).
2. Drag the `.dxt` file into the Claude Desktop window, **or** double-click it, **or** go to **Settings → Extensions → Install Extension** and select the file.
3. Claude Desktop will prompt you for optional configuration (token path, email, password).

### First-time authentication

The extension installs and runs the server automatically, but you must authenticate with Garmin once before data can be fetched:

```bash
uvx --python 3.12 --from git+https://github.com/Taxuspt/garmin_mcp garmin-mcp-auth
```

This saves OAuth tokens to `~/.garminconnect`. After that the server works without any credentials in the config.

> **Note:** Tokens are valid for approximately 6 months. Re-run `garmin-mcp-auth` when they expire.

### Build the `.dxt` yourself

```bash
bash scripts/build_dxt.sh   # produces garmin-mcp.dxt in the repo root
```

---

## Setup

### Quick Start for MCP Clients

The easiest way to use this MCP server with Claude Desktop, [Codex](https://openai.com/codex/), or another MCP client is to authenticate once before adding the server to your configuration.

#### Prerequisites

- Python 3.12+
- Garmin Connect account
- MFA may be required if enabled on your account

#### Step 1: Pre-authenticate (One-time)

Before adding the server to your MCP client, authenticate once in your terminal:

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

If you don't have MFA enabled you can also skip `garmin-mcp-auth` and pass `GARMIN_EMAIL` and `GARMIN_PASSWORD` as env variables directly to your MCP client, if supported. For better security, prefer the pre-authentication flow above and keep credentials out of MCP client configuration.

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

#### Step 3: Restart your MCP client

Your Garmin data is now available to your MCP client.

For Codex and other clients, see the examples below.

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
- `GARMIN_IS_CN`: Set to `true` to use Garmin Connect China (garmin.cn) instead of the international version (default: `false`)
- `GARMIN_FIT_DOWNLOAD_DIR`: Default directory for downloaded activity files. When set, skips the first-run setup prompt in `download_activity_file`.
- `GARMIN_FIT_CONFIG`: Path to the persisted download-directory config file (default: `~/.garminconnect_fit_config.json`).

File-based secrets are useful in certain environments, such as inside a Docker container. Note that you cannot set both `GARMIN_EMAIL` and `GARMIN_EMAIL_FILE`, similarly you cannot set both `GARMIN_PASSWORD` and `GARMIN_PASSWORD_FILE`.

### Transport

By default the server communicates over **stdio**, which is what Claude Desktop, the MCP Inspector, and most local clients expect. To serve over **HTTP** instead (e.g. when running in a container or Kubernetes), set the transport via environment variables:

- `GARMIN_MCP_TRANSPORT`: `stdio` (default), `streamable-http`, or `sse`
- `GARMIN_MCP_HOST`: bind address for HTTP transports (default `0.0.0.0`)
- `GARMIN_MCP_PORT`: bind port for HTTP transports (default `8000`)

```bash
GARMIN_MCP_TRANSPORT=streamable-http garmin-mcp
```

When an HTTP transport is selected:

- MCP clients connect to the **`/mcp`** path (e.g. `http://localhost:8000/mcp`).
- A plain **`GET /healthz`** endpoint is exposed for liveness/readiness probes.

The server itself performs **no authentication** on the HTTP endpoint — put it behind a reverse proxy (nginx, Traefik, Authelia, etc.) if it is reachable beyond localhost.

### Garmin Connect China (garmin.cn)

If you use Garmin Connect China (garmin.cn) instead of the international version, set the `GARMIN_IS_CN` environment variable to `true`:

```bash
# Pre-authenticate with Garmin Connect China
GARMIN_IS_CN=true garmin-mcp-auth

# Or use the CLI flag
garmin-mcp-auth --is-cn
```

For Claude Desktop, add `GARMIN_IS_CN` to the `env` section:

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
        "GARMIN_IS_CN": "true"
      }
    }
  }
}
```

For Docker, add `GARMIN_IS_CN=true` to your `.env` file or uncomment it in `docker-compose.yml`.

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

### With Codex

Codex uses TOML for MCP server configuration. Add one of the following entries to `~/.codex/config.toml` after authenticating with `garmin-mcp-auth`.

You can also ask your MCP-capable client to set this up for you. For example:

```text
Install the Garmin MCP server from https://github.com/Taxuspt/garmin_mcp, authenticate with garmin-mcp-auth, and add it to my MCP configuration without storing my Garmin email or password.
```

#### Directly from GitHub without cloning the repo

```toml
[mcp_servers.garmin]
command = "uvx"
args = [
  "--python",
  "3.12",
  "--from",
  "git+https://github.com/Taxuspt/garmin_mcp",
  "garmin-mcp"
]
```

#### Directly from your local copy of the repository

```toml
[mcp_servers.garmin-local]
command = "uv"
args = [
  "--directory",
  "/full/path/to/garmin_mcp",
  "run",
  "garmin-mcp"
]
```

Restart your MCP client after saving the file.

### With opencode

[opencode](https://opencode.ai) auto-loads a project-level `opencode.json` when launched from a repository root, so contributors who clone this repo get the Garmin MCP wired up against the local source with no extra config.

#### From a clone of this repository (recommended for development)

This repo ships an [`opencode.json`](./opencode.json) that runs the MCP via `uv run garmin-mcp`, so it always tracks the working tree.

```bash
git clone https://github.com/Taxuspt/garmin_mcp.git
cd garmin_mcp
uv sync                # install dependencies
garmin-mcp-auth        # one-time Garmin login (skip if ~/.garminconnect already exists)
opencode               # launches with the garmin MCP attached
```

Verify the server is connected:

```bash
opencode mcp list
# ●  ✓ garmin   connected
#       uv run garmin-mcp
```

#### From any other directory (GitHub install)

Add the server to your global opencode config at `~/.config/opencode/opencode.json` after running `garmin-mcp-auth`:

```json
{
  "$schema": "https://opencode.ai/config.json",
  "mcp": {
    "garmin": {
      "type": "local",
      "command": [
        "uvx",
        "--python",
        "3.12",
        "--from",
        "git+https://github.com/Taxuspt/garmin_mcp",
        "garmin-mcp"
      ],
      "enabled": true,
      "timeout": 30000
    }
  }
}
```

Restart opencode after saving the file. The first `uvx` invocation downloads and caches the package, so the initial startup may take a few seconds.

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
- "Analyze my last ride's power zones and compare to my training zones"
- "Show me my CTL, ATL, and TSB trend for the last 6 weeks"
- "What was my power duration curve from yesterday's ride? Estimate my FTP."
- "Analyze the FIT data from my last cycling activity — how was my shifting quality on the climbs?"
- "Show me my HRV trend for the last 2 weeks and flag any recovery concerns"
- "What's my season best 20-minute power and when did I set it?"

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

This project includes comprehensive tests for all MCP tools. **All tests are currently passing (100%)**.

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

- **Integration tests** (200+ tests): Test all MCP tools using FastMCP integration with mocked Garmin API responses
- **End-to-end tests** (4 tests): Test with real MCP server and Garmin API (requires valid credentials)

## Reinstalling from local path

If you are working from a local checkout or fork:

```bash
uv tool install --python 3.12 --force C:\Users\aresd\Desktop\programacion\garmin_mcp
```
