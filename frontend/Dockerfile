# Frontend image.
#
# Two stages: Node builds the static bundle, nginx serves it. The runtime image
# carries no Node and no source — just the compiled assets and a web server.
FROM node:22-alpine AS build

WORKDIR /app

# Dependencies first, so a source-only change reuses the cached install layer.
COPY package.json package-lock.json* ./
RUN npm ci --no-audit --no-fund

COPY . .
RUN npm run build


FROM nginx:1.27-alpine AS runtime

COPY --from=build /app/dist /usr/share/nginx/html
COPY nginx.conf /etc/nginx/conf.d/default.conf
# Referenced by an include in nginx.conf, so it has to exist at that exact path.
COPY proxy_params_wg /etc/nginx/proxy_params_wg

EXPOSE 80

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD wget -qO- http://localhost/ >/dev/null 2>&1 || exit 1

CMD ["nginx", "-g", "daemon off;"]
