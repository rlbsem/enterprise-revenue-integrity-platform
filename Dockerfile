FROM python:3.12.14-slim-bookworm@sha256:392307d22300de8b5986851a12d9176dfc0fc073e65bf6523ebd7dcbeb23564e AS python-runtime
FROM postgres:17.11-bookworm@sha256:858bb5e98eed02eb8cafbcdc007f9a65d1b613972028944a8ec2133ccaf80d27
COPY --from=python-runtime /usr/local/ /usr/local/
RUN apt-get update && apt-get install -y --no-install-recommends \
    ca-certificates libsqlite3-0 libbz2-1.0 libffi8 liblzma5 libexpat1 \
    libreadline8 libgdbm6 libgdbm-compat4 libncursesw6 libstdc++6 \
    && rm -rf /var/lib/apt/lists/* \
    && groupadd --gid 10001 demo \
    && useradd --uid 10001 --gid 10001 --home-dir /state/home --no-create-home demo \
    && mkdir -p /state/home && chown -R demo:demo /state
ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1 PYTHONUTF8=1 HOME=/state/home
WORKDIR /opt/integrity
COPY requirements.lock pyproject.toml ./
RUN python -m pip install --no-cache-dir -r requirements.lock
COPY src/ src/
COPY dbt/ dbt/
COPY web/ web/
RUN python -m pip install --no-deps --no-build-isolation -e . && python -m pip check
USER 10001:10001
EXPOSE 8080
ENTRYPOINT ["python", "-m", "integrity.portable"]
CMD ["serve", "--state", "/state", "--port", "8080", "--container"]
