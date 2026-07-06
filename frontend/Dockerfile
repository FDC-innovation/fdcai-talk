FROM node:20-alpine

WORKDIR /app

# NEXT_PUBLIC_* values are inlined into the client bundle AT BUILD TIME —
# setting them only as runtime env on the container has no effect on the
# already-compiled JS. Accept them as build args (compose passes them via
# build.args) and export as ENV so `npm run build` sees them.
ARG NEXT_PUBLIC_API_URL=http://localhost:8000
ARG NEXT_PUBLIC_WS_URL=ws://localhost:8000
ARG NEXT_PUBLIC_SITE_URL=http://localhost:3000
ARG NEXT_PUBLIC_S3_BUCKET_DOMAIN=
ARG NEXT_PUBLIC_CLOUDFRONT_DOMAIN=
ENV NEXT_PUBLIC_API_URL=$NEXT_PUBLIC_API_URL \
    NEXT_PUBLIC_WS_URL=$NEXT_PUBLIC_WS_URL \
    NEXT_PUBLIC_SITE_URL=$NEXT_PUBLIC_SITE_URL \
    NEXT_PUBLIC_S3_BUCKET_DOMAIN=$NEXT_PUBLIC_S3_BUCKET_DOMAIN \
    NEXT_PUBLIC_CLOUDFRONT_DOMAIN=$NEXT_PUBLIC_CLOUDFRONT_DOMAIN

# Copy package files
COPY package.json package-lock.json* ./

# Install dependencies
RUN npm install

# Copy application code
COPY . .

# Build application
RUN npm run build

# Expose port
EXPOSE 3000

# Start application
CMD ["npm", "start"]
