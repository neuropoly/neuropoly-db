FROM rust:1.90.0-bookworm AS validator
WORKDIR /build
RUN apt-get update \
    && apt-get install -y --no-install-recommends python3 \
    && rm -rf /var/lib/apt/lists/*
COPY tools/bids-validator-rust/ /build/tools/bids-validator-rust/
COPY tools/build_validator.py tools/bids-validator-source.json tools/bids-schema-NOTICE /build/tools/
RUN python3 tools/build_validator.py --output /build/validator

FROM mcr.microsoft.com/devcontainers/python:3.12-bookworm
USER root
RUN apt-get update \
    && apt-get install -y --no-install-recommends dcm2niix \
    && rm -rf /var/lib/apt/lists/*
COPY --from=validator /build/validator/bids-validator /usr/local/bin/bids-validator
COPY --from=validator /build/validator/LICENSE /build/validator/THIRD-PARTY-LICENSES /usr/local/share/doc/bids-validator/
USER vscode