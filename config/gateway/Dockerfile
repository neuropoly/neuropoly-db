FROM docker.io/library/nginx:alpine

RUN apk add --no-cache openssl

# Copy gateway config as a runtime template so compose env vars can inject
# values like NB_GATEWAY_BASE_PATH through envsubst.
COPY conf.d/default.conf /etc/nginx/templates/default.conf.template

# Generate a fallback self-signed certificate at container startup for local TLS.
COPY 10-generate-tls.sh /docker-entrypoint.d/10-generate-tls.sh
RUN chmod +x /docker-entrypoint.d/10-generate-tls.sh
 
