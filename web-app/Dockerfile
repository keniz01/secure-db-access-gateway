FROM node:20-slim AS build

WORKDIR /app

COPY package*.json ./
RUN npm ci --ignore-scripts

COPY . .

# Vite inlines VITE_* variables at build time; ARG+ENV lets an image be built
# for a specific origin (defaults to the local dev gateway). Production builds
# pass the real origin as --build-arg (see .github/workflows/docker.yml).
ARG VITE_API_BASE_URL=https://localhost:8443
ARG VITE_SQL_GRAPHQL_BASE_URL=https://localhost:8443/api/graphql
ENV VITE_API_BASE_URL=$VITE_API_BASE_URL
ENV VITE_SQL_GRAPHQL_BASE_URL=$VITE_SQL_GRAPHQL_BASE_URL

RUN npm run build

FROM nginx:1.27-alpine

COPY nginx.conf /etc/nginx/nginx.conf
COPY --from=build /app/dist/ /usr/share/nginx/html/

USER nginx

EXPOSE 5173
