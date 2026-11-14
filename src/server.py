#!/usr/bin/env python3
"""
Home Assistant MCP Server
A specialized MCP server for Home Assistant device control and monitoring
"""

import logging
import os
from datetime import datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from dotenv import dotenv_values
from fastmcp import FastMCP

from ._version import __version__
from .services.cache import Cache, describe_database_url

# Import our service modules
from .services.homeassistant import HomeAssistantClient

# Load environment variables with correct precedence
config: dict[str, str | None] = {}

# Load from project directory if available
for filename in (".env", ".env.local"):
    path = Path(filename)
    if path.exists():
        config.update(dotenv_values(path))

# Also check the script's directory (supports running from elsewhere)
script_dir = Path(__file__).parent
for filename in (".env", ".env.local"):
    path = script_dir / filename
    if path.exists():
        config.update(dotenv_values(path))

# Apply loaded values without overriding existing environment vars
# (dotenv_values maps a bare `KEY` line to None; skip those.)
for key, value in config.items():
    if value is not None:
        os.environ.setdefault(key, value)

# Configure logging
logging.basicConfig(
    level=(
        logging.DEBUG if os.getenv("DEBUG", "false").lower() == "true" else logging.INFO
    ),
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger(__name__)

# Initialize MCP server
mcp = FastMCP(name="HomeAssistantMCP", version=__version__)

# Service instances (will be initialized on first use)
_ha_service: HomeAssistantClient | None = None
_cache_service: Cache | None = None
_cache_initialized = False


def get_ha_service() -> HomeAssistantClient | None:
    """Get or initialize the Home Assistant service"""
    global _ha_service

    if _ha_service is None:
        ha_url = os.getenv("HA_URL")
        ha_token = os.getenv("HA_TOKEN")

        if not ha_url or not ha_token:
            logger.warning(
                "Home Assistant not configured. Set HA_URL and HA_TOKEN in environment."
            )
            return None

        try:
            verify_ssl = os.getenv("HA_VERIFY_SSL", "true").lower() == "true"
            # Get cache service if available
            cache = get_cache_service()
            _ha_service = HomeAssistantClient(
                url=ha_url,
                access_token=ha_token,
                verify_ssl=verify_ssl,
                mcp=mcp,  # Pass MCP instance to service
                cache=cache,  # Pass cache instance to service
            )
            # Test connection
            result = _ha_service.test_connection()
            if result.get("status") == "success":
                logger.info(
                    f"Initialized Home Assistant service ({result.get('connection_type')})"
                    + (" with caching" if cache else "")
                )
            else:
                logger.error(
                    f"Home Assistant connection test failed: {result.get('error')}"
                )
                _ha_service = None
                return None
        except Exception as e:
            logger.error(f"Failed to initialize Home Assistant service: {e}")
            return None

    return _ha_service


def get_cache_service() -> Cache | None:
    """Get or initialize the cache service (None when no cache is configured)"""
    global _cache_service, _cache_initialized

    if _cache_service is None and not _cache_initialized:
        _cache_initialized = True
        try:
            _cache_service = Cache.from_env()
        except Exception as e:
            logger.error(f"Failed to initialize cache: {type(e).__name__}")
            _cache_service = None
        if _cache_service is None:
            logger.info("Cache disabled (set DATABASE_URL or CACHE_BACKEND=memory)")
        else:
            logger.info("Cache enabled")

    return _cache_service


# Register additional server-level tools
@mcp.tool(
    name="get_server_status",
    description="""Get the current status of the Home Assistant service.

## Returns
• Service status for Home Assistant integration
• Connection type and status
• Overall server version

## Use Cases
• Health check
• Service monitoring
• Troubleshooting connections

## Related Tools
• Use `get_server_config` for configuration details""",
    title="Server Status",
    annotations={"title": "Server Status"},
)
def get_server_status() -> dict[str, Any]:
    """Get the status of the Home Assistant service"""
    status: dict[str, Any] = {
        "server": "HomeAssistantMCP",
        "version": __version__,
        "services": {},
    }

    # Check Home Assistant service
    ha_service = get_ha_service()
    if ha_service:
        test = ha_service.test_connection()
        if test.get("status") == "success":
            status["services"]["homeassistant"] = {
                "status": "active",
                "connection_type": test.get("connection_type"),
            }
        else:
            status["services"]["homeassistant"] = {"status": "error"}
    else:
        status["services"]["homeassistant"] = {"status": "not_configured"}

    return status


@mcp.tool(
    name="get_server_config",
    description="""Get the current server configuration (non-sensitive values only).

## Returns
• Debug mode status
• Service configuration status
• SSL verification settings
• Timezone configuration

## Use Cases
• Check configuration
• Verify settings
• Debug issues

## Related Tools
• Use `get_server_status` for service health

⚠️ **Note**: Sensitive values like API keys are not exposed""",
    title="Server Configuration",
    annotations={"title": "Server Configuration"},
)
def get_server_config() -> dict[str, Any]:
    """Get the current server configuration (non-sensitive)"""
    return {
        "debug_mode": os.getenv("DEBUG", "false").lower() == "true",
        "homeassistant_configured": bool(os.getenv("HA_URL") and os.getenv("HA_TOKEN")),
        "ha_verify_ssl": os.getenv("HA_VERIFY_SSL", "true").lower() == "true",
        "timezone": os.getenv("TIMEZONE", "UTC"),
    }


# ==================== Current DateTime Tool ====================


@mcp.tool(
    name="get_current_datetime",
    description="""Get the current date and time in the configured timezone.

## Returns
• Current date (YYYY-MM-DD format)
• Current time (HH:MM:SS format)
• Current datetime (ISO 8601 format)
• Configured timezone name
• UTC offset
• Day of week
• Unix timestamp

## Use Cases
• Reference current date/time for device control and automation
• Understand timezone context for scheduling
• Schedule automations relative to current time

## Related Tools
• Use Home Assistant automation tools with time-based triggers
• Use `get_server_config` to see configured timezone

⚠️ **Note**: The timezone is configured via the TIMEZONE environment variable (default: UTC)""",
    title="Current Date & Time",
    annotations={"title": "Current Date & Time"},
)
def get_current_datetime() -> dict[str, Any]:
    """Get the current date and time in the configured timezone"""
    # Get timezone from environment variable, default to UTC
    timezone_str = os.getenv("TIMEZONE", "UTC")

    try:
        tz = ZoneInfo(timezone_str)
    except Exception as e:
        logger.warning(f"Invalid timezone '{timezone_str}': {e}. Falling back to UTC.")
        tz = ZoneInfo("UTC")
        timezone_str = "UTC"

    # Get current datetime in the configured timezone
    now = datetime.now(tz)

    return {
        "date": now.strftime("%Y-%m-%d"),
        "time": now.strftime("%H:%M:%S"),
        "datetime": now.isoformat(),
        "timezone": timezone_str,
        "utc_offset": now.strftime("%z"),
        "timezone_abbr": now.strftime("%Z"),
        "day_of_week": now.strftime("%A"),
        "timestamp": int(now.timestamp()),
    }


# ==================== Cache Management Tools ====================


@mcp.tool(
    name="get_cache_stats",
    description="""Get cache statistics and performance metrics for this server process.

## Returns
• Hit/miss rates
• Average response times
• Error counts (cache errors never fail a request; they count as misses)
• Total requests
• Uptime

## Use Cases
• Monitor cache performance
• Debug caching issues
• Tune CACHE_TTL_* settings

## Related Tools
• Use `clear_cache` to clear cache entries
• Use `get_cache_info` for cache backend and storage details""",
    title="Cache Statistics",
    annotations={"title": "Cache Statistics"},
)
def get_cache_stats() -> dict[str, Any]:
    """Get cache statistics"""
    cache = get_cache_service()
    if not cache:
        return {"error": "Cache service not available"}

    stats = cache.get_stats()
    return stats.to_dict()


@mcp.tool(
    name="clear_cache",
    description="""Clear cache entries by key pattern, or all cached data.

## Parameters
• pattern: Glob matched against cache keys: `*` matches any run of characters,
  `?` one character (e.g., "ha:*", "ha:states:*"). If not provided, clears ALL entries.

## Returns
• connected: False while the cache backs off after a database error (nothing was
  cleared; keys_deleted is 0)

## Use Cases
• Clear stale data
• Force refresh of cached Home Assistant data
• Debug caching issues

## Related Tools
• Use `get_cache_stats` to view cache metrics
• Use `get_cache_info` for cache backend and storage details

⚠️ **Warning**: Clearing all cache may impact performance temporarily""",
    title="Clear Cache",
    annotations={"title": "Clear Cache"},
)
def clear_cache(pattern: str | None = None) -> dict[str, Any]:
    """Clear cache entries"""
    cache = get_cache_service()
    if not cache:
        return {"error": "Cache service not available"}

    if pattern:
        # Clear by pattern
        deleted = cache.delete_pattern(pattern)
        return {
            "status": "success",
            "pattern": pattern,
            "keys_deleted": deleted,
            "connected": cache.is_connected(),
        }
    else:
        # Clear all cache
        if cache.flush_all():
            return {
                "status": "success",
                "message": "All cache cleared",
                "connected": cache.is_connected(),
            }
        else:
            return {
                "status": "error",
                "message": "Failed to clear cache",
                "connected": cache.is_connected(),
            }


@mcp.tool(
    name="get_cache_info",
    description="""Get cache backend health and storage information.

## Returns
• connected: False while the cache backs off after a database error
• backend: "postgres" or "memory"
• database: PostgreSQL host and database name (no credentials)
• server_version: PostgreSQL version string
• entries: Number of stored entries (including expired ones not yet swept)
• table_bytes: Size of the cache table on disk

## Use Cases
• Monitor cache health
• Check which database the cache uses
• Check cache size

## Related Tools
• Use `get_cache_stats` for performance metrics
• Use `clear_cache` to clear cache entries""",
    title="Cache Information",
    annotations={"title": "Cache Information"},
)
def get_cache_info() -> dict[str, Any]:
    """Get cache backend information"""
    cache = get_cache_service()
    if not cache:
        return {"error": "Cache service not available"}

    info = cache.info()
    database_url = os.getenv("DATABASE_URL")
    database = (
        describe_database_url(database_url)
        if info.get("backend") == "postgres" and database_url
        else None
    )
    return {
        "connected": cache.is_connected(),
        "backend": info.get("backend"),
        "database": database,
        "server_version": info.get("server_version"),
        "entries": info.get("entries"),
        "table_bytes": info.get("table_bytes"),
    }


@mcp.tool(
    name="reset_cache_stats",
    description="""Reset cache performance statistics.

## Use Cases
• Start fresh monitoring period
• Clear old statistics
• Begin new performance measurement

## Related Tools
• Use `get_cache_stats` to view current statistics""",
    title="Reset Cache Statistics",
    annotations={"title": "Reset Cache Statistics"},
)
def reset_cache_stats() -> dict[str, Any]:
    """Reset cache statistics"""
    cache = get_cache_service()
    if not cache:
        return {"error": "Cache service not available"}

    cache.reset_stats()
    return {"status": "success", "message": "Cache statistics reset"}


# Initialize services on startup
def initialize_services() -> None:
    """Initialize all configured services"""
    logger.info("Initializing Home Assistant service...")

    # Initialize Home Assistant service
    ha = get_ha_service()
    if ha:
        logger.info("✓ Home Assistant service initialized")


if __name__ == "__main__":
    # Run the MCP server
    logger.info("Starting HomeAssistantMCP server...")

    # Initialize services
    initialize_services()

    # Check configuration
    if not os.getenv("HA_URL") or not os.getenv("HA_TOKEN"):
        logger.warning(
            "Home Assistant not configured. Set HA_URL and HA_TOKEN in .env.local or .env"
        )

    # Run the server using stdio transport
    mcp.run()
