FROM python:3.12-slim

WORKDIR /app
COPY server/requirements.txt /app/server/requirements.txt
RUN pip install --no-cache-dir -r /app/server/requirements.txt
COPY server/app /app/server/app
COPY web /app/web
ENV PYTHONPATH=/app/server
ENV DATA_DIR=/app/data
EXPOSE 8787
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8787"]
