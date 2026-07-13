FROM node:20-alpine AS deps
WORKDIR /srv
COPY package.json ./
RUN npm install

FROM node:20-alpine AS build
WORKDIR /srv
COPY --from=deps /srv/node_modules ./node_modules
COPY . .
# Rewrites werden beim standalone-Build eingebacken — Backend-URL muss
# daher zur Build-Zeit feststehen (Compose-Servicename).
ARG BACKEND_INTERNAL_URL=http://backend:8000
ENV BACKEND_INTERNAL_URL=$BACKEND_INTERNAL_URL
RUN npm run build

FROM node:20-alpine
WORKDIR /srv
ENV NODE_ENV=production
COPY --from=build /srv/.next/standalone ./
COPY --from=build /srv/.next/static ./.next/static
EXPOSE 3000
CMD ["node", "server.js"]
