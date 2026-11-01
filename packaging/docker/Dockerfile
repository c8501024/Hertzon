# Headless Hertzon solver with the remote service (docs/REMOTE.md, "Cloud computers").
#   docker build -f packaging/docker/Dockerfile -t hertzon-solver .
#   docker run -p 47820:47820 -e HERTZON_TOKEN=... hertzon-solver
# No GUI packages: the image only solves what a Hertzon GUI sends to it.
ARG BASE=python:3.12-slim
FROM ${BASE}

# gmsh's wheel links against these X/GL libraries even without a display (ldd libgmsh.so); libgomp: OpenMP
RUN apt-get update && apt-get install -y --no-install-recommends \
        libgl1 libglu1-mesa libopengl0 libx11-6 libxext6 libxrender1 libxcursor1 libxfixes3 libxft2 \
        libfontconfig1 libxinerama1 libgomp1 \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /opt/hertzon
COPY pyproject.toml packaging/docker/solver_requirements.py ./
RUN python solver_requirements.py pyproject.toml > /tmp/req.txt \
    && pip install --no-cache-dir -r /tmp/req.txt psutil \
    && rm /tmp/req.txt solver_requirements.py

ENV PYTHONPATH=/opt/hertzon \
    PYTHONUNBUFFERED=1 \
    PYTHONUTF8=1
COPY hertzon ./hertzon
COPY examples/PatchAntenna.hzn examples/NATO-Sphere.hzn examples/Coax.hzn ./examples/
# compile the numba kernels now (disk cache in the image), so the first job starts at full speed
RUN python -m hertzon.solver.warmup && rm -rf /tmp/*

ENV HERTZON_HOME=/work
VOLUME /work
EXPOSE 47820/tcp 47820/udp
CMD ["python", "-m", "hertzon.remote", "serve"]
