FROM node:20-alpine AS build
WORKDIR /app
COPY package*.json ./
# package-lock.json намеренно не коммитится (см. .gitignore, тот же принцип,
# что и в ../nst-tg-monitor/frontend) — `npm ci` требует лок-файл и падает
# на свежем клоне, где его нет.
RUN npm install
COPY . .
RUN npm run build

FROM nginx:alpine
COPY nginx.conf /etc/nginx/conf.d/default.conf
COPY --from=build /app/dist /usr/share/nginx/html
