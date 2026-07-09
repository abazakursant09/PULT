FROM node:20-alpine

WORKDIR /app

COPY package*.json ./
RUN npm ci

COPY . .

# Backend API URL is inlined into the client bundle at BUILD time. Pass it as a
# build arg so the deployed frontend calls the real backend, never localhost:
#   docker build --build-arg NEXT_PUBLIC_API_URL=https://api.yourdomain.com .
ARG NEXT_PUBLIC_API_URL=http://localhost:8000
ENV NEXT_PUBLIC_API_URL=$NEXT_PUBLIC_API_URL

RUN npm run build

EXPOSE 3000

CMD ["npm", "run", "start"]
