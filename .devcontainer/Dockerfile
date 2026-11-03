FROM python:3.12-bookworm AS validator

WORKDIR /build

ARG NPDB_VALIDATOR_SOURCE=github
ARG NPDB_VALIDATOR_VERSION=
ENV NPDB_VALIDATOR_SOURCE=${NPDB_VALIDATOR_SOURCE}
ENV NPDB_VALIDATOR_VERSION=${NPDB_VALIDATOR_VERSION}
ENV PATH="/root/.cargo/bin:${PATH}"
RUN if [ "$NPDB_VALIDATOR_SOURCE" = local ]; then \
        curl --fail --proto '=https' --tlsv1.2 https://sh.rustup.rs -o /tmp/rustup.sh \
        && sh /tmp/rustup.sh -y --profile minimal --default-toolchain 1.90.0; \
    fi

COPY tools/bids-validator-rust/ /build/tools/bids-validator-rust/
COPY tools/build_validator.py tools/validator_*.py tools/audit_validator.py /build/tools/
COPY src/npdb/external/native.py src/npdb/external/validator-source.json /build/src/npdb/external/
COPY src/npdb/external/validator-appveyor.json src/npdb/external/validator-appveyor-notices.txt.gz /build/src/npdb/external/

RUN if [ -z "$NPDB_VALIDATOR_VERSION" ]; then unset NPDB_VALIDATOR_VERSION; fi; \
    python3 /build/tools/build_validator.py --output /native/npdb-validator

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
