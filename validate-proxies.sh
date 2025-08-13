#!/bin/bash

# Exit on error
set -e

# --- Configuration ---
IMAGE_NAME="razor29/proxy-validator:latest" # The name of your built Docker image
INTERNAL_OUTPUT_FILE="/app/output/HTTP.txt" # Path to the final file inside the container
DEFAULT_HOST_OUTPUT_FILE="http_validated.txt" # Default output filename on the host

# --- Script Logic ---

# Check if an output filename was provided
if [ -z "$1" ]; then
  HOST_OUTPUT_FILE="$DEFAULT_HOST_OUTPUT_FILE"
  echo "No output filename provided. Using default: $HOST_OUTPUT_FILE"
else
  HOST_OUTPUT_FILE="$1"
  echo "Output will be saved to: $HOST_OUTPUT_FILE"
  # Shift arguments so $@ contains only args for the container later
  shift
fi

# Generate a unique container name to avoid collisions
CONTAINER_NAME="proxxy-run-$(date +%s)-$RANDOM"

echo "Starting container '$CONTAINER_NAME' in detached mode..."

# Run the container detached, without --rm for now
# Pass any remaining arguments ($@) to the container's CMD (e.g., custom validation flags)
docker run \
  -d \
  --name "$CONTAINER_NAME" \
  "$IMAGE_NAME" "$@"
  # Add any -e flags here if you need to override validation params, e.g.:
  # -e VALIDATION_TARGET_URL="https://example.com" \

RUN_EXIT_CODE=$?
if [ $RUN_EXIT_CODE -ne 0 ]; then
  echo "Error: Failed to start container '$CONTAINER_NAME'."
  exit $RUN_EXIT_CODE
fi

echo "Container started. Waiting for completion... (PID: $(docker inspect --format '{{.State.Pid}}' "$CONTAINER_NAME"))"

# Wait for the container to finish
docker wait "$CONTAINER_NAME" > /dev/null # Suppress output of exit code from wait
WAIT_EXIT_CODE=$? # Capture actual exit code if needed later, though wait itself might return non-zero on errors

# Check container status just in case 'wait' had issues
CONTAINER_STATUS=$(docker inspect --format '{{.State.Status}}' "$CONTAINER_NAME")
if [ "$CONTAINER_STATUS" != "exited" ]; then
    echo "Error: Container '$CONTAINER_NAME' did not exit cleanly (Status: $CONTAINER_STATUS). Check logs: docker logs $CONTAINER_NAME"
    # Optionally remove the container even on failure
    # docker rm "$CONTAINER_NAME" > /dev/null
    exit 1
fi

echo "Container '$CONTAINER_NAME' finished. Copying output..."

# Copy the output file from the stopped container
# This will overwrite the host file if it exists
docker cp "$CONTAINER_NAME:$INTERNAL_OUTPUT_FILE" "$HOST_OUTPUT_FILE"
CP_EXIT_CODE=$?

if [ $CP_EXIT_CODE -ne 0 ]; then
  echo "Error: Failed to copy output from '$CONTAINER_NAME'. File '$INTERNAL_OUTPUT_FILE' might not exist inside the container."
  echo "Check container logs: docker logs $CONTAINER_NAME"
  # Clean up container anyway
  docker rm "$CONTAINER_NAME" > /dev/null
  exit $CP_EXIT_CODE
fi

echo "Output saved to '$HOST_OUTPUT_FILE'."

# Clean up the container (simulating --rm)
echo "Removing container '$CONTAINER_NAME'..."
docker rm "$CONTAINER_NAME" > /dev/null

echo "Done."
exit 0