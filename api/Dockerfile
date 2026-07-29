FROM python:3.12-alpine

# Fresh named volumes inherit the ownership of the image's directory, so both
# mountpoints are created and chowned here to let the service run unprivileged.
# /data/icons matters as much as /data: without it in the image, Docker creates
# that mountpoint itself and the new volume comes out root-owned, which the
# unprivileged process cannot write to — every icon download then fails with
# EACCES while the config store, on the correctly-initialised /data volume, keeps
# working.
RUN apk add --no-cache ca-certificates \
    && adduser -D -u 10001 app \
    && mkdir -p /data/icons \
    && chown -R app:app /data

COPY server.py icons.py favicon.py /app/

USER app
ENV FIREHOME_DATA=/data FIREHOME_API_PORT=8080 PYTHONUNBUFFERED=1
EXPOSE 8080
VOLUME ["/data"]

HEALTHCHECK --interval=30s --timeout=3s --start-period=5s --retries=3 \
    CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8080/healthz', timeout=2).status==200 else 1)"

CMD ["python", "/app/server.py"]
