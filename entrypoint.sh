#!/bin/bash
# This script first runs the proxy generation and validation, then, if configured,
# distributes the final output file to multiple remote servers using a flexible
# JSON configuration that supports both SSH key and password authentication.

# Exit immediately if any command fails, ensuring a failed step stops the entire process.
set -e

################################################################################
# STAGE 1: PROXY GENERATION & VALIDATION
################################################################################

echo ">>> Running proXXy Tool <<<"
# Execute proXXy using poetry, passing along any arguments provided from the CMD
poetry run python proXXy.py "$@"
EXIT_CODE=$? # Capture exit code

if [ $EXIT_CODE -ne 0 ]; then
  echo "!!! proXXy failed with exit code $EXIT_CODE. Aborting. !!!"
  exit $EXIT_CODE
fi

echo "" # Newline for clarity
echo ">>> proXXy Finished. Running Secondary Validation Script <<<"

PROXXY_OUTPUT_FILE="/app/output/HTTP.txt"

# Check if the primary output file exists before trying to validate
echo ">>> Checking for proXXy output file at: $PROXXY_OUTPUT_FILE <<<"
if [ ! -f "$PROXXY_OUTPUT_FILE" ]; then
  echo "!!! Warning: $PROXXY_OUTPUT_FILE not found after proXXy run. Skipping secondary validation and distribution. !!!"
  exit 0
else
   echo ">>> Found $PROXXY_OUTPUT_FILE. Proceeding with validation. <<<"
fi

# Execute the Python validation script using poetry to ensure access to aiohttp etc.
# The python script reads /app/output/HTTP.txt and overwrites it with the validated list.
poetry run python /app/validate_proxies.py
VALIDATION_EXIT_CODE=$?

if [ $VALIDATION_EXIT_CODE -ne 0 ]; then
  echo "!!! Secondary validation script failed with exit code $VALIDATION_EXIT_CODE. !!!"
  exit $VALIDATION_EXIT_CODE
fi

echo "" # Newline for clarity
echo ">>> Container Proxy Generation Task Completed <<<"

################################################################################
# STAGE 2: FLEXIBLE FILE DISTRIBUTION
################################################################################

# This entire block is skipped unless the DISTRIBUTE_FILE variable is explicitly set to "true".
if [[ "${DISTRIBUTE_FILE,,}" =~ ^(1|true|yes|on)$ ]]; then
    echo ""
    echo "================================================================="
    echo ">>> Starting Flexible File Distribution (Keys & Passwords) <<<"
    echo "================================================================="

    # --- Configuration & Validation ---
    # Check that required global environment variables are set.
    if [ -z "$DISTRIBUTION_CONFIG_JSON" ]; then
        echo "!!! FATAL: DISTRIBUTION_CONFIG_JSON environment variable not set. Cannot distribute. !!!"
        exit 1
    fi
    if [ -z "$REMOTE_DEST_PATH" ]; then
        echo "!!! FATAL: REMOTE_DEST_PATH environment variable not set. Cannot distribute. !!!"
        exit 1
    fi

    # Validate that the provided string is valid JSON before we proceed.
    if ! echo "$DISTRIBUTION_CONFIG_JSON" | jq -e . > /dev/null; then
        echo "!!! FATAL: DISTRIBUTION_CONFIG_JSON is not valid JSON. Please check the syntax. !!!"
        exit 1
    fi

    SOURCE_FILE_PATH="/app/output/HTTP.txt"
    # Define a single, temporary path for the key file inside the container's filesystem.
    # This file will be overwritten for each server that uses key-based auth.
    EPHEMERAL_KEY_PATH="/tmp/current_server_key"

    # Set up a trap: This command will run automatically when the script exits for ANY reason
    # (success, failure, or interrupt), ensuring the temporary key file is always deleted.
    trap 'rm -f "$EPHEMERAL_KEY_PATH"' EXIT

    # --- Main Distribution Loop ---
    # Use jq to iterate over each object in the JSON array.
    # The '-c' flag makes the output compact, ensuring each object is on a single line.
    echo "$DISTRIBUTION_CONFIG_JSON" | jq -c '.[]' | while read -r server_config; do
        
        # Extract details for the current server using jq. The '-r' flag gives raw string output.
        HOST=$(echo "$server_config" | jq -r '.host')
        USER=$(echo "$server_config" | jq -r '.user')
        
        # Sanity check the parsed data for the current server entry.
        if [ "$HOST" = "null" ] || [ "$USER" = "null" ]; then
            echo "!!! WARNING: Skipping an entry in JSON due to missing 'host' or 'user'. !!!"
            continue
        fi

        echo "-----------------------------------------------------------------"
        echo "--> Processing host: ${HOST}"

        # --- Conditional Authentication Logic ---
        # Check if a 'private_key' field exists and is not null for this server.
        if [ "$(echo "$server_config" | jq -r '.private_key')" != "null" ]; then
            # --- Method 1: Private Key Authentication ---
            echo "    Auth method: Private Key"
            KEY=$(echo "$server_config" | jq -r '.private_key')
            
            # Write the key to the ephemeral file and set strict permissions required by SSH.
            echo "${KEY}" > "${EPHEMERAL_KEY_PATH}"
            chmod 600 "${EPHEMERAL_KEY_PATH}"

            # Use rsync with ssh options to specify the key and disable host key checking.
            rsync -avz \
                -e "ssh -i ${EPHEMERAL_KEY_PATH} -o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null" \
                "${SOURCE_FILE_PATH}" \
                "${USER}@${HOST}:${REMOTE_DEST_PATH}"
                
        # If no key, check if a 'password' field exists and is not null.
        elif [ "$(echo "$server_config" | jq -r '.password')" != "null" ]; then
            # --- Method 2: Password Authentication ---
            echo "    Auth method: Password (Note: Less secure than using keys)"
            PASSWORD=$(echo "$server_config" | jq -r '.password')

            # Export the password to the SSHPASS variable for sshpass to use.
            export SSHPASS="${PASSWORD}"
            
            # Use sshpass with rsync. Crucially, pass the same SSH options to rsync's -e flag
            # to dynamically accept the host key and prevent the authenticity prompt.
            sshpass -e rsync -avz \
                -e "ssh -o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null" \
                "${SOURCE_FILE_PATH}" \
                "${USER}@${HOST}:${REMOTE_DEST_PATH}"

            # Unset the variable immediately after use for security hygiene.
            unset SSHPASS
            
        else
            echo "!!! WARNING: Skipping host ${HOST}. No 'private_key' or 'password' provided in JSON config. !!!"
            continue
        fi

        echo "    SUCCESS: File synced to ${HOST}."
    done

    echo "-----------------------------------------------------------------"
    echo ">>> Distribution Complete <<<"
else
    echo ">>> DISTRIBUTE_FILE environment variable not set to 'true'. Skipping distribution step. <<<"
fi

echo ""
echo ">>> Container Lifecycle Finished <<<"
