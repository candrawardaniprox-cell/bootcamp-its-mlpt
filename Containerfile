# Use the official Python image as the base image
FROM python:3.11-slim

# Set the working directory inside the container
WORKDIR /app

# Install Python dependencies
COPY requirements.txt /app/

# Install dependencies
RUN pip install --no-cache-dir -r requirements.txt

# Copy the FastAPI application code into the container
COPY . /app/

# Expose the port that the FastAPI application will run on
EXPOSE 8000

# Start the FastAPI application
CMD ["fastapi", "run"]

