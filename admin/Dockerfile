# Contexto de build = raiz do repositório (ver docker-compose.yml).
#
# Três estágios para a imagem final não carregar nem o código-fonte nem as
# dependências de build: só o servidor `standalone` que o Next produz.

# --- 1. dependências -------------------------------------------------------
FROM node:22-alpine AS deps
WORKDIR /app
COPY admin/package.json admin/package-lock.json ./
# `npm ci` respeita o lockfile — build reproduzível, sem versão nova entrando
# sozinha entre um deploy e outro.
RUN npm ci

# --- 2. build --------------------------------------------------------------
FROM node:22-alpine AS builder
WORKDIR /app
COPY --from=deps /app/node_modules ./node_modules
COPY admin/ ./
ENV NEXT_TELEMETRY_DISABLED=1
RUN npm run build

# --- 3. execução -----------------------------------------------------------
FROM node:22-alpine AS runner
WORKDIR /app
ENV NODE_ENV=production NEXT_TELEMETRY_DISABLED=1 PORT=3000 HOSTNAME=0.0.0.0

# Usuário sem privilégios: se alguém escapar do processo, não escapa como root.
RUN addgroup -g 1001 -S nodejs && adduser -S nextjs -u 1001

# `standalone` já traz um node_modules podado com só o que o servidor usa —
# inclusive `pg` e `bcryptjs`, que os scripts de manutenção também precisam.
COPY --from=builder --chown=nextjs:nodejs /app/.next/standalone ./
COPY --from=builder --chown=nextjs:nodejs /app/.next/static ./.next/static

# Scripts de manutenção (criar usuário, aplicar schema), para rodar por
# `docker exec` sem precisar de outra imagem.
COPY --from=builder --chown=nextjs:nodejs /app/scripts ./scripts
COPY --chown=nextjs:nodejs db/ ./db/

USER nextjs
EXPOSE 3000

# O Coolify só encaminha tráfego quando o healthcheck passa.
HEALTHCHECK --interval=30s --timeout=5s --start-period=25s --retries=3 \
  CMD node -e "fetch('http://127.0.0.1:3000/login').then(r=>process.exit(r.ok?0:1)).catch(()=>process.exit(1))"

CMD ["node", "server.js"]
