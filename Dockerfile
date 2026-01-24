FROM python:3.13-slim AS executable_builder

RUN --mount=type=cache,sharing=locked,target=/var/cache/apt \
    --mount=type=cache,sharing=locked,target=/var/lib/apt/lists \
    apt-get update && \
    apt-get install -y cargo

RUN \
    --mount=type=bind,source=.,target=/app,rw \
    --mount=type=cache,sharing=private,target=/app/target \
    --mount=type=cache,target=/usr/local/cargo/git/db \
    --mount=type=cache,target=/usr/local/cargo/registry/ \
    cd /app && \
    cargo build --release && \
    cp target/release/any-board /anyboard

FROM python:3.13-slim AS runtime

COPY --from=executable_builder /anyboard /anyboard

COPY requirements.txt .
RUN --mount=type=cache,sharing=private,target=/root/.cache/pip \
    pip3 install -r requirements.txt

WORKDIR /app
CMD ["/anyboard"]