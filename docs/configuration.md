# Configuration

The Home Assistant MCP server is configured through environment variables, typically via `.env.local` when running with Docker Compose.

## Core Environment Variables

| Variable          | Required | Default | Description                                              |
|-------------------|----------|---------|----------------------------------------------------------|
| `HA_URL`          | Yes      | -       | Home Assistant URL (e.g. `http://homeassistant.local`)  |
| `HA_TOKEN`        | Yes      | -       | Long-lived access token                                 |
| `HA_VERIFY_SSL`   | No       | `true`  | Verify SSL certificates                                 |
| `DEBUG`           | No       | `false` | Enable debug logging                                    |
| `DATABASE_URL`    | No       | -       | PostgreSQL URL for the response cache                   |
| `CACHE_BACKEND`   | No       | -       | `memory` for a process-local cache (local development)  |

## Cache

The cache is optional. `CACHE_BACKEND=memory` selects a process-local cache; otherwise a set
`DATABASE_URL` selects PostgreSQL; with neither, every request goes to Home Assistant.

```env
DATABASE_URL=postgresql://user:password@host:5432/homeassistant_mcp?sslmode=require&application_name=homeassistant-mcp
```

The PostgreSQL backend:

- connects lazily on first use (startup never waits for the database);
- creates its table, `cache_entries` (an `UNLOGGED` table: a database crash may empty it), on first
  connect. The database user needs `CREATE` on schema `public`; if it lacks it, the server logs a
  warning naming the fix (`GRANT ALL ON SCHEMA public TO <user>`, run as `postgres`);
- never fails a request: after any database error it logs (at most once a minute per operation),
  serves misses and calls Home Assistant directly for 30 seconds, then retries.

`clear_cache` takes a glob: `*` matches any run of characters and `?` one character; character
classes (`[abc]`) are not supported. `get_cache_info` reports the database host and name, never
credentials.

### Cache TTLs

Override with `CACHE_TTL_<NAME>` (seconds):

| Name             | Default | Data                  |
|------------------|---------|-----------------------|
| `HA_STATES`      | `3`     | Entity states         |
| `HA_DEVICE_LIST` | `1800`  | Device metadata       |
| `HA_ENTITY_LIST` | `1800`  | Entity metadata       |
| `HA_AREAS`       | `3600`  | Area list             |
| `HA_SERVICES`    | `3600`  | Service list          |

## Deployment Notes

- For Docker Compose, edits to `.env.local` are picked up on restart.
- For direct execution (without Docker), you can export variables in your shell before running `server.py` or `server_remote.py`.
