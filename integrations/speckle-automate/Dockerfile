FROM python:3.11-slim

# Speckle Automate functions run as a container; the runner invokes
# `python main.py` and passes the automation context via env vars.
WORKDIR /app

COPY pyproject.toml ./
RUN pip install --no-cache-dir -e ".[dev]"

COPY . .

CMD ["python", "main.py"]
