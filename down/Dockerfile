FROM python:3.12-slim

# The Instagram and Reddit downloader behind the search bar's "down" button.
# nginx proxies /down/ to it; it is not published on its own. Unlike the api it
# has to reach the internet — Instagram, Reddit and their CDNs — which is the
# whole job.
#
# ffmpeg merges Reddit videos, whose picture and sound are separate streams.
RUN apt-get update \
    && apt-get install -y --no-install-recommends ffmpeg \
    && rm -rf /var/lib/apt/lists/* \
    && useradd -u 10002 -M -s /usr/sbin/nologin app

COPY requirements.txt /app/
RUN pip install --no-cache-dir -r /app/requirements.txt
COPY app.py reddit.py /app/

USER app
WORKDIR /app
# Both cookie files are optional; see down/README.md for when they are needed.
ENV IG_COOKIES=/data/cookies.txt REDDIT_COOKIES=/data/reddit-cookies.txt \
    PYTHONUNBUFFERED=1
EXPOSE 5000

# Threads, because a download streams for as long as the file takes; the long
# timeout covers yt-dlp's extraction and building a zip of a whole carousel.
CMD ["gunicorn", "--bind", "0.0.0.0:5000", "--workers", "2", "--threads", "8", \
     "--timeout", "180", "app:app"]
