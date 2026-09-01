FROM node:20-alpine AS builder

WORKDIR /app
COPY package.json ./
RUN npm install
COPY . .
RUN npm run build

FROM nginx:1.25-alpine
COPY --from=builder /app/dist /usr/share/nginx/html
# nginx 配置由 docker-compose 挂载（deploy/nginx/nginx.conf）：
# Docker 的 COPY 不能引用构建上下文之外的路径，写 ../deploy/... 会直接构建失败
EXPOSE 80
CMD ["nginx", "-g", "daemon off;"]
