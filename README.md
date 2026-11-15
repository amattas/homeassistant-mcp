<p align="center">
  <img src="docs/logo.svg" width="120" alt="Home Assistant MCP logo">
</p>

# Home Assistant MCP Server

A specialized Model Context Protocol (MCP) server for Home Assistant device control and monitoring. This Docker-based server enables Claude Desktop and other MCP clients to interact with your Home Assistant instance for controlling smart home devices, monitoring sensors, and managing automations.

## Features

- **Device Control**: Control lights, switches, covers, locks, and climate devices
- **WebSocket Support**: Advanced connectivity with WebSocket fallback for areas and entities
- **Sensor Monitoring**: Organize and query 345+ sensors across 9 categories
- **Area Management**: Access 24+ home areas via WebSocket API
- **Scene & Automation**: Trigger scenes and execute automations
- **Entity Queries**: Query states from 1,500+ entities
- **Service Execution**: Call any Home Assistant service
- **Energy Monitoring**: Track energy usage and consumption
- **Security Checks**: Monitor locks, doors, and security sensors
- **Caching**: Optional PostgreSQL-backed (or in-memory) response cache
- **Docker Support**: Easy deployment with Docker and Docker Compose

## Prerequisites

- **Docker** and **Docker Compose** (for containerized deployment)
- **Home Assistant**: A running Home Assistant instance (local or remote)
- **Long-Lived Access Token**: Generated from your Home Assistant profile
- **Network Access**: Server must be able to reach your Home Assistant instance
- **Claude Desktop** (optional): For MCP client integration

## Quick Start

### 1. Get Your Home Assistant Access Token

1. Open your Home Assistant instance
2. Click your profile (bottom left)
3. Scroll to "Long-Lived Access Tokens"
4. Click "Create Token"
5. Give it a name (e.g., "MCP Server")
6. Copy the generated token immediately (it won't be shown again)

### 2. Clone and Configure

```bash
cd homeassistant-mcp
cp .env.example .env.local
```

Edit `.env.local` and add your Home Assistant details:

```env
# Home Assistant URL (include http:// or https://)
HA_URL=http://homeassistant.local:8123

# Long-lived access token
HA_TOKEN=your-long-lived-access-token-here

# SSL verification (set to false for self-signed certificates)
HA_VERIFY_SSL=true

# Debug mode
DEBUG=false

# Optional cache: a PostgreSQL database, or CACHE_BACKEND=memory for local dev
# DATABASE_URL=postgresql://user:password@host:5432/homeassistant_mcp?sslmode=require
# CACHE_BACKEND=memory
```

### 3. Run with Docker Compose

```bash
docker-compose up --build
```

The server will start in stdio mode, ready to accept MCP connections.

### 4. Connect to Claude Desktop

Add this configuration to your Claude Desktop config file:

**macOS**: `~/Library/Application Support/Claude/claude_desktop_config.json`
**Windows**: `%APPDATA%\Claude\claude_desktop_config.json`

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

## Configuration

### Environment Variables

| Variable | Required | Default | Description |
|----------|----------|---------|-------------|
| `HA_URL` | Yes | - | Home Assistant URL (e.g., http://homeassistant.local:8123) |
| `HA_TOKEN` | Yes | - | Long-lived access token |
| `HA_VERIFY_SSL` | No | `true` | Verify SSL certificates (set to false for self-signed) |
| `DEBUG` | No | `false` | Enable debug logging |
| `DATABASE_URL` | No | - | PostgreSQL URL for the response cache (e.g. `postgresql://user:pw@host:5432/homeassistant_mcp?sslmode=require`). Unset: no cache |
| `CACHE_BACKEND` | No | - | Set to `memory` for a process-local cache (local development); takes precedence over `DATABASE_URL` |
| `CACHE_TTL_<NAME>` | No | see `docs/configuration.md` | Override a cache TTL in seconds (`HA_STATES`, `HA_DEVICE_LIST`, `HA_ENTITY_LIST`, `HA_AREAS`, `HA_SERVICES`) |

## Available MCP Tools

The Home Assistant MCP server provides the following tools:

### Device Control

**Lights:**
- `turn_on_light` - Turn on a light with optional brightness
- `turn_off_light` - Turn off a light
- `toggle_light` - Toggle light state
- `set_light_brightness` - Set brightness (0-100%)

**Switches:**
- `turn_on_switch` - Turn on a switch
- `turn_off_switch` - Turn off a switch
- `toggle_switch` - Toggle switch state

**Climate:**
- `set_temperature` - Set target temperature
- `set_hvac_mode` - Set HVAC mode (heat, cool, auto, off)
- `turn_on_climate` - Turn on climate device
- `turn_off_climate` - Turn off climate device

**Covers (Blinds/Shades):**
- `open_cover` - Open a cover
- `close_cover` - Close a cover
- `stop_cover` - Stop cover movement
- `set_cover_position` - Set cover position (0-100%)

**Locks:**
- `lock_door` - Lock a door
- `unlock_door` - Unlock a door

### Scene & Automation
- `activate_scene` - Activate a scene
- `trigger_automation` - Trigger an automation
- `run_script` - Execute a script

### Entity & State Queries
- `get_entity_state` - Get state of a specific entity
- `get_all_entities` - List all entities
- `get_entities_by_area` - Get entities in an area
- `get_entities_by_domain` - Get entities by type (light, switch, etc.)

### Area Management
- `get_all_areas` - List all areas
- `get_area_devices` - Get devices in an area
- `get_area_entities` - Get entities in an area

### Sensor Monitoring
- `get_all_sensors` - Get all sensor states
- `get_sensors_by_category` - Get sensors by category (weather, HVAC, energy, etc.)
- `get_sensor_value` - Get specific sensor value

**Sensor Categories:**
- Weather (temperature, humidity, pressure)
- Pool (temperature, chemical levels)
- Air Quality (PM2.5, VOC, CO2)
- HVAC (climate controls)
- Energy (power, consumption)
- Security (doors, locks, motion)
- Media (TV, audio status)
- Network (connectivity)
- System (battery, diagnostics)

### Service Execution
- `call_service` - Call any Home Assistant service
- `execute_service` - Execute a service with parameters

### Server Management
- `get_server_status` - Check server health
- `get_server_config` - View server configuration
- `test_connection` - Test Home Assistant connection
- `get_cache_stats` - View cache performance metrics
- `clear_cache` - Clear cached data
- `get_cache_info` - View cache backend, database and size

## Local Development

### Without Docker

1. Create a virtual environment:
```bash
python -m venv venv
source venv/bin/activate  # On Windows: venv\Scripts\activate
```

2. Install dependencies:
```bash
pip install -e .
```

3. Set environment variables:
```bash
export HA_URL=http://homeassistant.local:8123
export HA_TOKEN=your-token
```

4. Run the server:
```bash
python server.py
```

### Running the tests

```bash
pip install -e ".[dev]"
pytest -q
```

The PostgreSQL cache tests run only when `TEST_DATABASE_URL` points at a scratch database
(the tests delete every cache entry in it):

```bash
docker run -d --rm --name pgtest -e POSTGRES_PASSWORD=pw -p 55432:5432 postgres:18
TEST_DATABASE_URL=postgresql://postgres:pw@localhost:55432/postgres pytest -q
docker stop pgtest
```

### With Docker

Build and run:
```bash
docker build -t homeassistant-mcp .
docker run -e HA_URL=http://... -e HA_TOKEN=... homeassistant-mcp
```

## Testing

### Test Server Status

You can test the server by connecting via Claude Desktop and asking:

> "What's the status of my Home Assistant server?"

### Test Device Control

> "Turn on the living room lights"
> "Set the bedroom temperature to 72 degrees"
> "Lock the front door"

### Test Sensor Queries

> "What's the current temperature?"
> "Show me all weather sensors"
> "What's my energy consumption?"

### Test Automation

> "Activate the movie night scene"
> "Run the good morning automation"

## Troubleshooting

### "Home Assistant not configured"

**Solution**: Ensure both `HA_URL` and `HA_TOKEN` are set in your `.env.local` file.

### Connection Test Failed

**Solution**:
- Verify `HA_URL` is correct and accessible from the Docker container
- Check that Home Assistant is running
- Ensure the access token is valid
- For HTTPS with self-signed certificates, set `HA_VERIFY_SSL=false`

### SSL Certificate Errors

**Solution**:
- If using a self-signed certificate, set `HA_VERIFY_SSL=false`
- Consider using HTTP for local network connections
- Ensure certificates are valid if using HTTPS

### Entity Not Found

**Solution**:
- Use `get_all_entities` to list available entities
- Check entity ID format (e.g., `light.living_room`, not `living_room`)
- Verify the entity exists in Home Assistant

### WebSocket Connection Issues

**Solution**:
- The server automatically falls back to WebSocket for certain operations
- Check Home Assistant logs for WebSocket errors
- Ensure WebSocket port (usually 8123) is accessible

## Architecture

The Home Assistant MCP server follows this architecture:

```
Claude Desktop
     ↓ (stdio)
Docker Container
     ↓
MCP Server (FastMCP)
     ↓
Home Assistant Client
     ├─ REST API (primary)
     ├─ WebSocket API (fallback/advanced)
     └─ SSE (Server-Sent Events)
     ↓
Home Assistant Instance
     ├─ Entities (1,500+)
     ├─ Areas (24+)
     ├─ Sensors (345+)
     └─ Services
```

An optional cache (a table in the service's PostgreSQL database, or in-memory for local development) reduces calls to Home Assistant. Cache errors never fail a request: the server backs off for 30 seconds and calls Home Assistant directly.

## Advanced Features

### WebSocket Areas Support

The server uses WebSocket API to access area information when REST API is insufficient. This provides:
- Real-time area updates
- Access to 24+ home areas
- Comprehensive device listings per area

### Sensor Categorization

Sensors are automatically categorized into 9 groups:
1. **Weather**: Temperature, humidity, pressure sensors
2. **Pool**: Pool temperature, chemical sensors
3. **Air Quality**: PM2.5, VOC, CO2 sensors
4. **HVAC**: Climate and heating sensors
5. **Energy**: Power and consumption sensors
6. **Security**: Door, lock, motion sensors
7. **Media**: TV and audio status sensors
8. **Network**: Connectivity sensors
9. **System**: Battery and diagnostic sensors

### Percentage-Based Brightness

Light brightness is handled as percentages (0-100%) for better usability, automatically converting to Home Assistant's 0-255 range.

## Security Notes

- Never commit your `.env.local` file or access tokens to version control
- Use environment-specific `.env` files
- Consider network isolation for Docker containers
- Long-lived access tokens should be rotated periodically
- Use HTTPS for remote Home Assistant connections
- Tokens are never exposed through MCP tool responses

## Performance

- **Automatic Caching**: Entity states are cached to reduce API calls
- **WebSocket Fallback**: Efficient real-time updates via WebSocket
- **Shared Cache**: Optional PostgreSQL cache shared by every replica
- **Connection Pooling**: Reuses HTTP connections for better performance

## Entity ID Format

Home Assistant uses domain-prefixed entity IDs:
- Lights: `light.living_room_lamp`
- Switches: `switch.bedroom_fan`
- Sensors: `sensor.outdoor_temperature`
- Climate: `climate.thermostat`
- Covers: `cover.bedroom_blinds`
- Locks: `lock.front_door`

## License

Released under the Apache License 2.0; see [LICENSE](LICENSE).

## Support

For Home Assistant documentation, visit: https://www.home-assistant.io/docs/
