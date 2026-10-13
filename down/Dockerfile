FROM python:3.12-slim

# The Instagram downloader behind the search bar's "down" button. nginx proxies
# /down/ to it; it is not published on its own. Unlike the api it has to reach
# the internet — Instagram and its CDN — which is the whole job.
RUN useradd -u 10002 -M -s /usr/sbin/nologin app

COPY requirements.txt /app/
RUN pip install --no-cache-dir -r /app/requirements.txt
COPY app.py /app/

USER app
WORKDIR /app
# cookies.txt is optional; see down/README.md for why it is usually needed.
ENV IG_COOKIES=/data/cookies.txt PYTHONUNBUFFERED=1
EXPOSE 5000

# Threads, because a download streams for as long as the file takes; the long
# timeout covers yt-dlp's extraction and building a zip of a whole carousel.
CMD ["gunicorn", "--bind", "0.0.0.0:5000", "--workers", "2", "--threads", "8", \
     "--timeout", "180", "app:app"]
