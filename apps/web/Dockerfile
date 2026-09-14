# 前端生产镜像（多阶段）：构建期内联 NEXT_PUBLIC_* ——生产走同源 /backend
# 服务端代理，构建期不设任何后端地址（BACKEND_ORIGIN 运行时读取，改后端地址
# 无需重新构建，见 docs/deployment.md「前端如何连后端」）。
#
# ⚠️ NEXT_PUBLIC_* 会被**内联进客户端 bundle**（任意访客查看源码即可读到）
#    ⇒ 绝不放任何密钥。写接口的 X-API-Token 走服务端：容器**运行时**注入
#    ASHARE_API_TOKEN，由 app/backend/[...path]/route.ts 附加请求头。
#    `.env*` 已在 .dockerignore 中排除，防止凭据被 COPY 进镜像层。
FROM node:22-alpine AS deps
WORKDIR /web
COPY package.json package-lock.json ./
RUN npm ci --no-fund --no-audit

FROM node:22-alpine AS build
WORKDIR /web
COPY --from=deps /web/node_modules ./node_modules
COPY . .
ENV NEXT_TELEMETRY_DISABLED=1
# dev server 会在 .next 留下产物锁；构建在干净容器里执行，无锁冲突
RUN npm run build

FROM node:22-alpine AS run
WORKDIR /web
ENV NODE_ENV=production NEXT_TELEMETRY_DISABLED=1
COPY --from=build /web ./
EXPOSE 3000
CMD ["npm", "start"]
