FROM node:20.18-slim

WORKDIR /app

# Copy package files
COPY package*.json ./

# Install dependencies without lifecycle scripts (supply-chain hardening)
RUN npm ci --ignore-scripts

# Copy source code
COPY . .

# Vite inlines VITE_* variables at build time; ARG+ENV lets an image be built
# for a specific origin (defaults to the local dev gateway). Production builds
# pass the real origin as --build-arg (see .github/workflows/docker.yml).
ARG VITE_API_BASE_URL=https://localhost:8443
ARG VITE_SQL_GRAPHQL_BASE_URL=https://localhost:8443/api/graphql
ENV VITE_API_BASE_URL=$VITE_API_BASE_URL
ENV VITE_SQL_GRAPHQL_BASE_URL=$VITE_SQL_GRAPHQL_BASE_URL

# Build the application
RUN npm run build

# Create non-root user and drop privileges
RUN adduser --disabled-password --gecos '' appuser && chown -R appuser /app
USER appuser

# Expose port
EXPOSE 5173

# Run the application
CMD ["npm", "run", "dev", "--", "--host", "0.0.0.0"]