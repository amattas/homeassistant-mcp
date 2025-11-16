# Getting Started

This guide walks through setting up the Home Assistant MCP server and connecting it to your Home Assistant instance.

## Prerequisites

- Docker and Docker Compose (for containerized deployment)
- A running Home Assistant instance (local or remote)
- A long-lived access token from your Home Assistant profile
- Network access from the server to Home Assistant

## Clone and Configure

```bash
cd homeassistant-mcp
cp .env.example .env.local
```

Edit `.env.local` and set at least:

```env
HA_URL=http://homeassistant.local:8123
HA_TOKEN=your-long-lived-access-token-here
HA_VERIFY_SSL=true
DEBUG=false
```

You can also configure optional Redis caching variables here.

## Run with Docker Compose

```bash
docker-compose up --build
```

The server will start in stdio mode inside the container, ready to accept MCP connections.

## Connect to Claude Desktop

Update your Claude Desktop configuration file:

- macOS: `~/Library/Application Support/Claude/claude_desktop_config.json`
- Windows: `%APPDATA%\\Claude\\claude_desktop_config.json`

Example configuration:

```json
{
  "mcpServers": {
    "homeassistant": {
      "command": "docker",
      "args": ["compose", "-f", "/path/to/homeassistant-mcp/docker-compose.yml", "run", "--rm", "homeassistant-mcp"]
    }
  }
}
```

Restart Claude Desktop to activate the server.
