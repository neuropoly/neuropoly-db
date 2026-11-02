FROM rust:1.90.0-bookworm AS validator

WORKDIR /build

RUN apt-get update && apt-get install -y --no-install-recommends \
        python3 \
    && rm -rf /var/lib/apt/lists/*

COPY tools/bids-validator-rust/ /build/tools/bids-validator-rust/
COPY tools/build_validator.py tools/validator_artifacts.py tools/audit_validator.py /build/tools/
COPY src/npdb/external/native.py src/npdb/external/validator-source.json /build/src/npdb/external/
COPY src/npdb/external/validator-artifacts.json src/npdb/external/validator-artifact-notices.txt.gz /build/src/npdb/external/

RUN python3 /build/tools/build_validator.py --output /native/npdb-validator

FROM mcr.microsoft.com/devcontainers/python:3.12-bookworm

USER root

RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        dcm2niix \
        clang \
        lld \
        llvm \
    && rm -rf /var/lib/apt/lists/*

COPY --from=validator /native/npdb-validator /usr/local/bin/npdb-validator

RUN cp /usr/local/bin/npdb-validator/bids-validator /usr/local/bin/bids-validator

USER vscode
