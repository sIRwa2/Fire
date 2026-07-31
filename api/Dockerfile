FROM python:3.12-alpine

# Fresh named volumes inherit the ownership of the image's directory, so both
# mountpoints are created and chowned here to let the service run unprivileged.
# Every path a volume is mounted at needs to exist here, not just /data: where
# the image has no directory, Docker creates the mountpoint itself and the new
# volume comes out root-owned, which the unprivileged process cannot write to.
# That failed twice — first for /data/icons, then again for /data/favicons — so
# tests/run.sh now checks this list against the compose file rather than trusting
# it to be kept in step by hand.
RUN apk add --no-cache ca-certificates \
    && adduser -D -u 10001 app \
    && mkdir -p /data/icons /data/favicons \
    && chown -R app:app /data

COPY server.py icons.py favicon.py suggest.py /app/

USER app
ENV FIREHOME_DATA=/data FIREHOME_API_PORT=8080 PYTHONUNBUFFERED=1
EXPOSE 8080
VOLUME ["/data"]

HEALTHCHECK --interval=30s --timeout=3s --start-period=5s --retries=3 \
    CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8080/healthz', timeout=2).status==200 else 1)"

CMD ["python", "/app/server.py"]
