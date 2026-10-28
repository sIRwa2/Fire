FROM python:3.12-slim

# The Instagram, Reddit and YouTube downloader behind the search bar's "down"
# button. nginx proxies /down/ to it; it is not published on its own. Unlike
# the api it has to reach the internet — the three sites and their CDNs — which
# is the whole job.
#
# ffmpeg merges Reddit and YouTube videos, whose picture and sound are separate
# streams. deno runs yt-dlp's solver for YouTube's player challenges; without a
# JavaScript runtime YouTube withholds most formats and throttles the rest.
ARG DENO_VERSION=2.9.8
RUN apt-get update \
    && apt-get install -y --no-install-recommends ffmpeg curl unzip \
    && curl -fsSL -o /tmp/deno.zip \
       "https://github.com/denoland/deno/releases/download/v${DENO_VERSION}/deno-x86_64-unknown-linux-gnu.zip" \
    && unzip -q /tmp/deno.zip -d /usr/local/bin && rm /tmp/deno.zip \
    && chmod 755 /usr/local/bin/deno \
    && apt-get purge -y curl unzip && apt-get autoremove -y \
    && rm -rf /var/lib/apt/lists/* \
    && useradd -u 10002 -M -s /usr/sbin/nologin app

COPY requirements.txt /app/
RUN pip install --no-cache-dir -r /app/requirements.txt
COPY app.py reddit.py youtube.py /app/

USER app
WORKDIR /app
# The cookie files are optional; see down/README.md for when they are needed.
# DENO_DIR: the root filesystem is read-only, so deno's cache goes on the tmpfs.
ENV IG_COOKIES=/data/cookies.txt REDDIT_COOKIES=/data/reddit-cookies.txt \
    YT_COOKIES=/data/youtube-cookies.txt DENO_DIR=/tmp/deno \
    PYTHONUNBUFFERED=1
EXPOSE 5000

# Threads, because a download streams for as long as the file takes; the long
# timeout covers yt-dlp's extraction, fetching and merging a YouTube video, and
# building a zip of a whole carousel.
CMD ["gunicorn", "--bind", "0.0.0.0:5000", "--workers", "2", "--threads", "8", \
     "--timeout", "600", "app:app"]
