# Stage 1: rewrite firehome.html's two remaining internet dependencies so the
# served page is fully self-contained. See localize.py.
FROM python:3.12-alpine AS localize
WORKDIR /build
COPY localize.py firehome.html ./
RUN python localize.py firehome.html index.html

# Stage 2: static server. Nothing but nginx, the page and the icon set.
FROM nginx:1.27-alpine

LABEL org.opencontainers.image.title="firehome" \
      org.opencontainers.image.description="LAN-only self-hosted homepage"

COPY --from=localize /build/index.html /usr/share/nginx/html/index.html
# The full homarr-labs icon set, so the in-app icon picker resolves locally.
COPY svg/ /usr/share/nginx/html/icons/
COPY nginx.conf /etc/nginx/conf.d/default.conf

EXPOSE 80
