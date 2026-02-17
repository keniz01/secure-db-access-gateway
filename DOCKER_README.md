# Docker Setup

This project now supports running the entire application stack using Docker Compose, with PostgreSQL running locally.

## Prerequisites

- Docker and Docker Compose installed
- Docker Desktop running (on macOS)

## Quick Start

1. **Ensure PostgreSQL is running locally**
   ```bash
   # Check if PostgreSQL is running
   pg_isready -p 5432

   # If not running, start it (varies by system)
   # macOS with Homebrew: brew services start postgresql
   # Ubuntu: sudo systemctl start postgresql
   ```

2. **Set up secrets** (first time only):
   ```bash
   ./setup-secrets.sh
   # Edit the files in secrets/ with your actual credentials
   ```

3. **Run the application stack**:
   ```bash
   docker-compose up --build
   ```

This will start:
- Auth0 API on port 8001
- SQL Query API on port 8002
- Web App on port 5173

**Note**: PostgreSQL runs on your local machine, not in a container.

## Services

### Auth0 API
- **Build**: ./auth0_api
- **Port**: 8001
- **Secrets**: All Auth0 and AI credentials loaded from Docker secrets

### SQL Query API
- **Build**: ./sql_query_api
- **Port**: 8002
- **Secrets**: Database URL loaded from Docker secrets

### Web App
- **Build**: ./web-app
- **Port**: 5173
- **Environment**: API base URL configured for container networking

## Secrets Management

Sensitive configuration is now managed through Docker secrets instead of .env files:

- Secrets are stored in the `secrets/` directory as individual files
- Each secret file contains a single value (e.g., API keys, passwords)
- Docker Compose mounts these as secrets in containers
- Applications read secrets from `/run/secrets/` directory

### Secret Files

- `secret_key.txt` - Flask secret key
- `session_secret_key.txt` - Session secret
- `auth0_domain.txt` - Auth0 domain
- `auth0_client_id.txt` - Auth0 client ID
- `auth0_client_secret.txt` - Auth0 client secret
- `github_token.txt` - GitHub token for Azure OpenAI
- `database_url.txt` - PostgreSQL connection string
- And more...

## Development

For development, you can modify the secrets files and rebuild:

```bash
docker-compose down
docker-compose up --build
```

## Production

For production deployment:

1. Move secrets to a secure location outside the repository
2. Update the `docker-compose.yml` secrets file paths
3. Use Docker Swarm or Kubernetes secrets for better security
4. Configure proper CORS origins and external database

## Troubleshooting

### Check container logs
```bash
docker-compose logs [service_name]
```

### Restart services
```bash
docker-compose restart [service_name]
```

### Clean rebuild
```bash
docker-compose down -v
docker-compose up --build
```

## Architecture

The Docker setup creates a complete development environment with:

- Isolated PostgreSQL database
- Backend APIs with proper networking
- Frontend served with hot reload
- Secure secrets management
- Health checks for database readiness