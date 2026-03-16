# Builds Caddy with the Cloudflare DNS plugin for DNS-01 ACME challenge.
# The standard caddy image does not include this plugin, so we compile it in.
FROM caddy:builder AS builder
RUN xcaddy build --with github.com/caddy-dns/cloudflare

FROM caddy:latest
COPY --from=builder /usr/bin/caddy /usr/bin/caddy
