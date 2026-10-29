FROM rust:1.90.0-bookworm AS validator
WORKDIR /build
COPY tools/bids-validator-rust/ /build/bids-validator-rust/
RUN cargo install --locked --path /build/bids-validator-rust --root /native

FROM mcr.microsoft.com/devcontainers/python:3.12-bookworm
USER root
RUN apt-get update \
    && apt-get install -y --no-install-recommends dcm2niix \
    && rm -rf /var/lib/apt/lists/*
COPY --from=validator /native/bin/bids-validator-rust /usr/local/bin/bids-validator-rust
USER vscode