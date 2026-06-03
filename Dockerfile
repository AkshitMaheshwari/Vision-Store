FROM python:3.12-slim

WORKDIR /app

# Install standard dependencies
RUN pip install --no-cache-dir fastapi uvicorn pydantic pytest httpx

# Copy the app code and tests
COPY ./app /app/app
COPY ./tests /app/tests
COPY ./main.py /app/main.py

# Set environment variables
ENV PYTHONUNBUFFERED=1

EXPOSE 8000

# Start FastAPI server
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000"]
