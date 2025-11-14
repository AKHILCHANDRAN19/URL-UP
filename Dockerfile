# Use an official, lightweight Python image as the base
FROM python:3.11-slim

# Set the working directory inside the container to /app
WORKDIR /app

# --- INSTALL SYSTEM DEPENDENCIES ---
# Update package lists and install aria2. This is the crucial step.
# This command runs inside the final image, so aria2 will be present.
RUN apt-get update && apt-get install -y aria2

# Copy the Python requirements file into the container
COPY requirements.txt .

# Install the Python packages
RUN pip install --no-cache-dir -r requirements.txt

# Copy all your bot's code (bot.py, etc.) into the container
COPY . .

# --- SET THE COMMAND TO RUN YOUR BOT ---
# This command is executed when the container starts
CMD ["python", "bot.py"]
