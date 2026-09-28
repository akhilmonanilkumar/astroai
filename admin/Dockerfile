# Admin console: a small Node server (Next.js standalone). Build context is admin/.
#   docker build -t guruji-admin admin/
#   docker run -e ADMIN_API_URL=... -e SUPABASE_URL=... -e SUPABASE_ANON_KEY=... -p 3001:3001 guruji-admin
FROM node:22-slim AS build
WORKDIR /app
COPY package.json package-lock.json ./
RUN npm ci
COPY . .
RUN npm run build

FROM node:22-slim
WORKDIR /app
ENV NODE_ENV=production PORT=3001 HOSTNAME=0.0.0.0 NEXT_TELEMETRY_DISABLED=1
COPY --from=build /app/.next/standalone ./
COPY --from=build /app/.next/static ./.next/static
USER node
EXPOSE 3001
CMD ["node", "server.js"]
