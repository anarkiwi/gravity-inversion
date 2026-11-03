FROM ghcr.io/anarkiwi/docker-mos-llvm-sdk:v23.4.0 AS deps
RUN apt-get update && apt-get install -y --no-install-recommends acme curl make python3 python3-venv \
    && rm -rf /var/lib/apt/lists/*
ENV VIRTUAL_ENV=/venv PATH=/venv/bin:$PATH
COPY requirements.txt requirements-dev.txt /tmp/
RUN python3 -m venv /venv && pip install --no-cache-dir -r /tmp/requirements-dev.txt

FROM deps AS test
WORKDIR /src
COPY . .
RUN make lint && make check && make test
