FROM docker.io/library/nginx:alpine

# Copy proxy config as a runtime template so compose env vars can inject
# values like NB_GATEWAY_BASE_PATH through envsubst.
COPY conf.d/default.conf /etc/nginx/templates/default.conf.template
