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
docker-compose up -d
```

3. View logs to monitor the server:

```bash
docker-compose logs -f garmin-mcp
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
docker-compose up -d
```

#### Handling MFA with Docker

If you have multi-factor authentication (MFA) enabled on your Garmin account:

1. Run the container in interactive mode:

```bash
docker-compose run --rm garmin-mcp
```

2. When prompted, enter your MFA code:

```
Garmin Connect MFA required. Please check your email/phone for the code.
Enter MFA code: 123456
```

3. The OAuth tokens will be saved to the Docker volume (`garmin-tokens`), so you won't need to re-authenticate on subsequent runs.

4. After MFA setup, you can run the container normally:

```bash
docker-compose up -d
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

If you have MFA/one-time codes enabled in your Garmin account, you need to login at the command line first to set the OAuth token.

#### Option 1: Using uvx (Recommended for Claude Desktop)

The app expects either the env var GARMIN_EMAIL or GARMIN_EMAIL_FILE. You can store these in files with the following command:

```bash
echo "your_email@example.com" > ~/.garmin_email
echo "your_password" > ~/.garmin_password
chmod 600 ~/.garmin_email ~/.garmin_password
```

Then you can manually run the login script:

```bash
GARMIN_EMAIL_FILE=~/.garmin_email GARMIN_PASSWORD_FILE=~/.garmin_password uvx --python 3.12 --from git+https://github.com/Taxuspt/garmin_mcp garmin-mcp
```

You will see:

```
Garmin Connect MFA required. Please check your email/phone for the code.
Enter MFA code: 123456
Oauth tokens stored in '~/.garminconnect' directory for future use. (first method)

Oauth tokens encoded as base64 string and saved to '~/.garminconnect_base64' file for future use. (second method)
```

After setting the token at the CLI, you can use the following in Claude Desktop without the env vars, because the OAuth tokens have been set:

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

#### Option 2: Using Docker

If using Docker, follow the [Handling MFA with Docker](#handling-mfa-with-docker) section above for a streamlined experience with persistent token storage.

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
