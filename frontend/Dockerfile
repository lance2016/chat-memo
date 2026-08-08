FROM node:22-alpine AS dependencies
WORKDIR /app
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci

FROM dependencies AS development
ENV NODE_ENV=development \
    NEXT_TELEMETRY_DISABLED=1
COPY frontend/ ./
EXPOSE 3000
CMD ["npm", "run", "dev", "--", "--hostname", "0.0.0.0"]

FROM dependencies AS builder
ARG NEXT_PUBLIC_API_BASE_URL=/backend
ARG INTERNAL_API_BASE_URL=http://api:8000
ARG NEXT_PUBLIC_API_KEY=
ENV NODE_ENV=production \
    NEXT_TELEMETRY_DISABLED=1 \
    NEXT_PUBLIC_API_BASE_URL=${NEXT_PUBLIC_API_BASE_URL} \
    INTERNAL_API_BASE_URL=${INTERNAL_API_BASE_URL} \
    NEXT_PUBLIC_API_KEY=${NEXT_PUBLIC_API_KEY} \
COPY frontend/ ./
RUN npm run build

FROM node:22-alpine AS production
WORKDIR /app
ENV NODE_ENV=production \
    NEXT_TELEMETRY_DISABLED=1 \
    HOSTNAME=0.0.0.0 \
    PORT=3000 \
    INTERNAL_API_BASE_URL=http://api:8000
RUN addgroup --system --gid 1001 nodejs \
    && adduser --system --uid 1001 nextjs
COPY --from=builder --chown=nextjs:nodejs /app/.next-build/standalone ./
COPY --from=builder --chown=nextjs:nodejs /app/.next-build/static ./.next-build/static
USER nextjs
EXPOSE 3000
CMD ["node", "server.js"]
