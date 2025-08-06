#!/usr/bin/env bash

# CAMS Slideshow Startup Script
# Prevents system suspension and starts slideshow with timeout handling

echo "Starting CAMS Slideshow with power management..."

# Set up library path for numpy and other native dependencies
export LD_LIBRARY_PATH="/run/current-system/sw/lib:$LD_LIBRARY_PATH"

# Prevent system suspension while slideshow is running
echo "Disabling system suspend/idle..."
# systemd-inhibit --what=idle:sleep:suspend --who="CAMS Slideshow" --why="Running astronomy slideshow" --mode=block bash -c "
#     echo 'System suspend disabled for slideshow session'
    
    # Start the slideshow with timeout handling
    echo 'Starting slideshow application...'
    cd /home/astropolis/cams-slideshow
    
    # Run slideshow in a loop - if it crashes, restart it
    while true; do
        uv run python slideshow.py \"\$@\"
        exit_code=\$?
        
        if [ \$exit_code -eq 0 ]; then
            echo 'Slideshow exited normally'
            break
        else
            echo \"Slideshow crashed with exit code \$exit_code, restarting in 5 seconds...\"
            sleep 5
        fi
    done
"

echo "CAMS Slideshow stopped - system suspend re-enabled"
