---
name: nginx-rate-limit
description: Add rate limiting to endpoints in nginx configuration files. Use when users want to protect API endpoints, prevent abuse, throttle traffic, or configure request limits in nginx. Handles limit_req_zone, limit_req, burst, nodelay, and per-location rate limiting.
---

# Nginx Rate Limiting Skill

This skill helps you add rate limiting to nginx endpoints using nginx's built-in `ngx_http_limit_req_module`.

---

## How Nginx Rate Limiting Works

Nginx rate limiting uses a **leaky bucket** algorithm:

- `limit_req_zone` — defines a shared memory zone that tracks request rates (set in `http` block)
- `limit_req` — applies the rate limit to a specific location or server block
- `burst` — allows a short burst of requests above the rate before rejecting
- `nodelay` — processes burst requests immediately instead of queuing them

---

## Step-by-Step Process

### 1. Read the Existing Config

Before making changes, always read the target nginx config file:

```bash
cat /etc/nginx/nginx.conf
# or
cat /etc/nginx/sites-available/default
# or the specific file the user mentions
```

Look for:
- Existing `limit_req_zone` definitions in the `http` block
- The `http {}` block boundaries
- Relevant `location` blocks where rate limiting should be applied
- Existing `limit_req` directives that might conflict

### 2. Understand the User's Requirements

Ask (or infer from context):
- **Which endpoints** need rate limiting? (e.g., `/api/`, `/login`, all traffic)
- **What rate?** (e.g., 10 requests/second, 100 requests/minute)
- **Per what key?** Usually `$binary_remote_addr` (per IP), but could be `$http_x_forwarded_for` or a custom variable
- **Burst tolerance?** How many extra requests to allow momentarily
- **Hard reject or delay?** Use `nodelay` to reject burst immediately vs. queue them

If not specified, use sensible defaults (see below).

### 3. Define Rate Limit Zones in the `http` Block

Add `limit_req_zone` directives inside the `http {}` block, **before** any `server {}` blocks:

```nginx
http {
    # Rate limiting zones
    # Zone name: api_limit
    # Key: client IP address (binary form is more memory-efficient)
    # Zone size: 10MB (stores ~160,000 IPs)
    # Rate: 10 requests per second
    limit_req_zone $binary_remote_addr zone=api_limit:10m rate=10r/s;

    # Stricter zone for login/auth endpoints
    limit_req_zone $binary_remote_addr zone=login_limit:10m rate=5r/m;

    server {
        ...
    }
}
```

**Rate format:**
- `r/s` — requests per second
- `r/m` — requests per minute

**Zone size guidance:**
- `10m` — ~160,000 IP addresses (good default)
- `1m` — ~16,000 IP addresses (lightweight)

### 4. Apply Rate Limits to Locations

Add `limit_req` inside the relevant `location` blocks:

```nginx
# General API rate limiting with burst
location /api/ {
    limit_req zone=api_limit burst=20 nodelay;
    # ... other directives
}

# Strict rate limiting for login (no burst)
location /login {
    limit_req zone=login_limit burst=5;
    # ... other directives
}

# Rate limit entire server (apply in server block)
server {
    limit_req zone=api_limit burst=50 nodelay;
}
```

**Parameters:**
| Parameter | Description |
|-----------|-------------|
| `zone=name` | Which zone to use (must match a `limit_req_zone` name) |
| `burst=N` | Max extra requests to queue/allow above the rate |
| `nodelay` | Process burst requests immediately; reject if burst exceeded |
| *(no nodelay)* | Queue burst requests, delaying them to match the rate |

### 5. Customize Error Response (Optional)

By default, rate-limited requests return HTTP 503. To return 429 (Too Many Requests):

```nginx
http {
    limit_req_status 429;
    limit_req_zone $binary_remote_addr zone=api_limit:10m rate=10r/s;
}
```

### 6. Logging (Optional but Recommended)

Rate-limited requests are logged at `warn` level by default. To suppress noise or customize:

```nginx
http {
    limit_req_zone $binary_remote_addr zone=api_limit:10m rate=10r/s;
    # Log level for rejected requests: info, notice, warn, error
    limit_req_log_level warn;
}
```

### 7. Test and Reload

After editing, always validate and reload:

```bash
nginx -t                        # Test configuration syntax
nginx -s reload                 # Reload without downtime
# OR
systemctl reload nginx
```

---

## Common Patterns

### Pattern 1: Protect a REST API

```nginx
http {
    limit_req_zone $binary_remote_addr zone=api:10m rate=30r/m;

    server {
        location /api/ {
            limit_req zone=api burst=10 nodelay;
            proxy_pass http://backend;
        }
    }
}
```

### Pattern 2: Brute-Force Protection on Login

```nginx
http {
    limit_req_zone $binary_remote_addr zone=login:10m rate=5r/m;

    server {
        location /login {
            limit_req zone=login burst=3 nodelay;
            # ...
        }
        location /auth/token {
            limit_req zone=login burst=3 nodelay;
            # ...
        }
    }
}
```

### Pattern 3: Global + Per-Endpoint Limits

```nginx
http {
    limit_req_zone $binary_remote_addr zone=global:10m rate=100r/s;
    limit_req_zone $binary_remote_addr zone=sensitive:10m rate=5r/m;

    server {
        # Apply global limit to all traffic
        limit_req zone=global burst=200 nodelay;

        location /admin/ {
            # Stricter limit on admin
            limit_req zone=sensitive burst=2 nodelay;
        }
    }
}
```

### Pattern 4: Rate Limit by API Key (Custom Variable)

```nginx
http {
    # Rate limit by API key header instead of IP
    limit_req_zone $http_x_api_key zone=api_key_limit:20m rate=100r/s;

    server {
        location /api/ {
            limit_req zone=api_key_limit burst=50 nodelay;
        }
    }
}
```

---

## Default Values (When User Doesn't Specify)

| Parameter | Default | Rationale |
|-----------|---------|-----------|
| Key | `$binary_remote_addr` | Per-IP is the standard |
| Zone size | `10m` | Handles ~160k IPs comfortably |
| Rate | `30r/m` for auth, `60r/m` for API | Conservative but usable |
| Burst | Rate × 2 | Allows short spikes without rejecting |
| nodelay | Yes for APIs | Better UX; fail fast rather than slow |
| Status code | `429` | Semantically correct for rate limiting |

---

## Output

When done, provide:
1. The full modified config file (or the relevant diff)
2. The commands to test and reload nginx
3. A brief explanation of what was added and why

---

## Important Notes

- `limit_req_zone` must be in the `http` block, NOT inside `server` or `location`
- Multiple `limit_req` directives can be applied to the same location (they all apply)
- If the config uses includes (e.g., `include sites-enabled/*`), the `limit_req_zone` goes in the main `nginx.conf` `http` block, and `limit_req` goes in the included site config
- Always test with `nginx -t` before reloading
- Be careful not to duplicate zone names — check for existing zones first