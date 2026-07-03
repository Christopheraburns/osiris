FROM node:22-alpine
WORKDIR /app

COPY package.json package-lock.json ./
RUN npm ci --omit=dev

COPY config.json ./
COPY styles ./styles
COPY sprites ./sprites
COPY fonts ./fonts

EXPOSE 8080
CMD ["npx", "tileserver-gl-light", "-c", "config.json", "-p", "8080", "--bind", "0.0.0.0"]
