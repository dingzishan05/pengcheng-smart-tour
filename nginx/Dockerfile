FROM node:20-alpine AS build
ARG AMAP_JS_KEY=""
ARG AMAP_SECURITY_CODE=""
WORKDIR /build
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci --registry=https://registry.npmmirror.com --no-audit --no-fund
COPY frontend/ ./
ENV VITE_AMAP_JS_KEY=${AMAP_JS_KEY}
ENV VITE_AMAP_SECURITY_CODE=${AMAP_SECURITY_CODE}
RUN npm run build

FROM nginx:1.27-alpine
COPY --from=build /build/dist /usr/share/nginx/html
COPY nginx/nginx.conf /etc/nginx/conf.d/default.conf
EXPOSE 80