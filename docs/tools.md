# MCP Tools

The Home Assistant MCP server exposes tools for device control, automation, and monitoring via the Home Assistant API.

Major categories include:

- **Device Control**
  - Lights: `turn_on_light`, `turn_off_light`, `toggle_light`, `set_light_brightness`
  - Switches: `turn_on_switch`, `turn_off_switch`, `toggle_switch`
  - Climate: `set_temperature`, `set_hvac_mode`, `turn_on_climate`, `turn_off_climate`
  - Covers: `open_cover`, `close_cover`, `stop_cover`, `set_cover_position`
  - Locks: `lock_door`, `unlock_door`

- **Scenes, Automations, and Scripts**
  - `activate_scene`
  - `trigger_automation`
  - `run_script`

- **Entity and Area Queries**
  - `get_entity_state`, `get_all_entities`
  - `get_entities_by_area`, `get_entities_by_domain`
  - `get_all_areas`, `get_area_devices`, `get_area_entities`

- **Sensors and Monitoring**
  - `get_all_sensors` and related categorization helpers

- **Server and Cache Management**
  - `get_server_status`, `get_server_config`
  - `get_cache_stats`, `clear_cache`, `get_cache_info`

See `src/server.py` and `src/services/homeassistant.py` for the full list of available tools and their parameters.
