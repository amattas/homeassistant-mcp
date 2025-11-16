# Configuration

The Home Assistant MCP server is configured through environment variables, typically via `.env.local` when running with Docker Compose.

## Core Environment Variables

| Variable          | Required | Default | Description                                              |
|-------------------|----------|---------|----------------------------------------------------------|
| `HA_URL`          | Yes      | -       | Home Assistant URL (e.g. `http://homeassistant.local`)  |
| `HA_TOKEN`        | Yes      | -       | Long-lived access token                                 |
| `HA_VERIFY_SSL`   | No       | `true`  | Verify SSL certificates                                 |
| `DEBUG`           | No       | `false` | Enable debug logging                                    |
| `REDIS_HOST`      | No       | -       | Redis server hostname (for caching)                     |
| `REDIS_PORT`      | No       | `6379`  | Redis server port                                       |
| `REDIS_PASSWORD`  | No       | -       | Redis password                                          |
| `REDIS_USE_SSL`   | No       | `false` | Use SSL for Redis connection                            |

When Redis is configured, the server uses `RedisCache` to cache responses and reduce load on Home Assistant.

## Deployment Notes

- For Docker Compose, edits to `.env.local` are picked up on restart.
- For direct execution (without Docker), you can export variables in your shell before running `server.py` or `server_remote.py`.
