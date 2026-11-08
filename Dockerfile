FROM ghcr.io/anarkiwi/docker-mos-llvm-sdk:v23.4.0 AS deps
RUN apt-get update && apt-get install -y --no-install-recommends ca-certificates curl \
    && curl -fsSL -o /etc/apt/keyrings/llvm.asc https://apt.llvm.org/llvm-snapshot.gpg.key \
    && . /etc/os-release \
    && echo "deb [signed-by=/etc/apt/keyrings/llvm.asc] https://apt.llvm.org/$VERSION_CODENAME/ llvm-toolchain-$VERSION_CODENAME-22 main" \
       > /etc/apt/sources.list.d/llvm.list \
    && apt-get update && apt-get install -y --no-install-recommends acme clang-22 gcc graphviz libc6-dev make mlir-22-tools python3 python3-venv \
    && ln -s /usr/lib/llvm-22/bin/mlir-opt /usr/lib/llvm-22/bin/mlir-translate /usr/local/bin/ \
    && rm -rf /var/lib/apt/lists/*
ENV VIRTUAL_ENV=/venv PATH=/venv/bin:$PATH CLANG=clang-22
COPY requirements.txt requirements-dev.txt /tmp/
RUN python3 -m venv /venv && pip install --no-cache-dir -r /tmp/requirements-dev.txt

FROM deps AS test
WORKDIR /src
COPY . .
RUN make lint && make check && make test
