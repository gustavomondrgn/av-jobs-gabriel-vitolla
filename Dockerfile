FROM python:3.12-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY main.py .
COPY profile.md .

# seen_ids.json será persistido via volume
VOLUME ["/app/data"]
ENV DATA_DIR=/app/data

CMD ["python", "-u", "main.py"]
