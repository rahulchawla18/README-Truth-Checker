# Reference template — runner.py generates the actual Dockerfile dynamically
# based on detected project strategy. This file documents the shape.
#
# Substitutions:
#   {python_version}            -> "3.11", "3.12", etc.
#   {package_manager_install}   -> one of:
#                                   RUN pip install --no-cache-dir poetry==1.8.3
#                                   RUN pip install --no-cache-dir uv
#                                   RUN pip install --no-cache-dir pipenv
#                                   (empty for plain pip)

FROM python:{python_version}-slim-bookworm

ENV DEBIAN_FRONTEND=noninteractive \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_NO_INPUT=1

RUN apt-get update && apt-get install -y --no-install-recommends \
    git curl ca-certificates build-essential \
    && rm -rf /var/lib/apt/lists/*

{package_manager_install}

WORKDIR /workspace
