# ChatGPT OAuth on DigitalOcean App Platform

This optional integration adds OAuth 2.1 authorization to the existing Garmin MCP
HTTP endpoint. It supports **one Garmin owner account** and only OpenAI's
published ChatGPT CIMD client, not arbitrary OAuth clients.

## Existing deployments remain unchanged

Without GARMIN_MCP_OAUTH_PUBLIC_URL, no OAuth endpoints are registered and
GARMIN_MCP_HTTP_TOKEN continues to protect the server exactly as before.

## App Platform environment variables

Keep all existing GARMIN_EMAIL, GARMIN_PASSWORD, GARMIN_MCP_TRANSPORT,
GARMIN_MCP_PORT, GARMIN_MCP_HOST, GARMIN_MCP_STATELESS_HTTP and
GARMIN_MCP_HTTP_TOKEN settings.

Add exactly these **Run time / Encrypted** settings:

- GARMIN_MCP_OAUTH_PUBLIC_URL — the publicly reachable **HTTPS origin**,
  e.g. https://my-garmin.ondigitalocean.app (no trailing path or /mcp)
- GARMIN_MCP_OAUTH_PASSWORD — a new, random password of at least 20 characters,
  **different from your Garmin password and existing HTTP token**; store
  it in a password manager.

Generate one with: openssl rand -base64 36

Do not change the existing GARMIN_MCP_HTTP_TOKEN. The OAuth JWT signing key
is derived from that token, and changing it invalidates previously issued
OAuth access and refresh tokens.

The app must run with:

- GARMIN_MCP_TRANSPORT=streamable-http
- GARMIN_MCP_HOST=0.0.0.0
- GARMIN_MCP_PORT=8080 (or the same port configured in DigitalOcean)
- GARMIN_MCP_STATELESS_HTTP=true
- GARMIN_MCP_HTTP_TOKEN set to your existing 32+ character random secret

Keep your health check on GET /readyz.

## Configure ChatGPT

Use Plugins > Add custom MCP server, and configure:

- Server URL: https://my-garmin.ondigitalocean.app/mcp
- Authentication: OAuth
- Client registration: Client ID Metadata Documents (CIMD)
- Token authentication: private_key_jwt (if shown)

The server advertises:
- GET /.well-known/oauth-protected-resource
- GET /.well-known/oauth-protected-resource/mcp
- GET /.well-known/oauth-authorization-server
- GET /oauth/authorize
- POST /oauth/authorize
- POST /oauth/token

The OAuth login page asks for **GARMIN_MCP_OAUTH_PASSWORD**, not your Garmin
Connect password. Authorizing grants ChatGPT access to all exposed MCP tools,
including write actions. You can restrict the tools via GARMIN_TOOL_PROFILE or
GARMIN_DISABLED_TOOLS.

No OAuth database is required. Short-lived authorization codes live in the
single container's memory; signed access and refresh tokens validate across
restarts as long as GARMIN_MCP_HTTP_TOKEN stays unchanged.

### Limitations

- This is a lightweight one-owner integration, not a multi-tenant identity
  provider. Never share its OAuth password.
- Authorization codes expire after 5 minutes and may be lost during a redeploy.
  Restart the connection attempt if a deployment happens during login.
- Access tokens expire after 1 hour; refresh tokens expire after 30 days.
  Signing-key rotation revokes all existing tokens.
- Refresh tokens are signed but not stored or individually revocable. A
  compromised refresh token can be used until expiration unless the signing
  secret is rotated. Keep access to DigitalOcean app secrets restricted.
- ChatGPT may update its client metadata or token-exchange behavior over time.
  Validate end-to-end before relying on this integration in production.
- App Platform does not provide persistent Garmin token storage. A new container
  signs in to Garmin using the existing GARMIN_EMAIL/GARMIN_PASSWORD variables.
  This OAuth addition does not change Garmin's own token lifecycle.

## Troubleshooting

A 401 on /mcp without a token is expected and should contain a
WWW-Authenticate header with resource_metadata pointing at your HTTPS server.

A healthy server still returns 200 from /readyz without requiring any token.

If ChatGPT reports invalid_client, inspect the available methods in the
ChatGPT CIMD and verify that its signature keys are reachable. No Garmin
account credentials should ever be sent to ChatGPT during this OAuth flow.

If GARMIN_MCP_OAUTH_PUBLIC_URL is missing, malformed, or the OAuth password
or shared secret are too short, the OAuth feature will refuse to start rather
than expose the endpoint without authentication.
