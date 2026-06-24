FROM node:20-alpine

WORKDIR /app

COPY package*.json ./
RUN npm ci

COPY . .

# Public API origin is inlined into the client bundle at BUILD time (Next.js
# bakes NEXT_PUBLIC_* during `next build`). Default keeps local-dev behavior;
# production passes --build-arg NEXT_PUBLIC_API_URL=https://api.domain.
ARG NEXT_PUBLIC_API_URL=http://localhost:8000
ENV NEXT_PUBLIC_API_URL=$NEXT_PUBLIC_API_URL

RUN npm run build

EXPOSE 3000

CMD ["npm", "run", "start"]
