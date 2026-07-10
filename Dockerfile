FROM python:3.11-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY update_4khd.py .
COPY seen_posts.json .

CMD ["python", "update_4khd.py"]
