FROM node:24-bookworm-slim AS dependencies
WORKDIR /app
COPY package.json package-lock.json ./
RUN npm ci --ignore-scripts

FROM dependencies AS build
COPY . .
RUN npm run build

FROM build AS test
CMD ["sh", "-c", "npm test && npm run typecheck"]

FROM node:24-bookworm-slim AS runtime
ENV NODE_ENV=production \
    NITRO_HOST=0.0.0.0 \
    NITRO_PORT=3000
WORKDIR /app
COPY --from=build --chown=node:node /app/.output/ ./.output/
USER node
EXPOSE 3000
CMD ["node", ".output/server/index.mjs"]
