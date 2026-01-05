FROM python:3.11-slim
WORKDIR /app
RUN mkdir -p /data
ENV DB_URL=sqlite:////data/data.db
VOLUME ["/data"]
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY . .
CMD ["python", "-u", "bot.py"]
