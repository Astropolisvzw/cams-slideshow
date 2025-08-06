#!/usr/bin/env bash

# Show logs for the cams-slideshow user service

echo "=== Cams Slideshow Service Status ==="
systemctl --user status cams-slideshow

echo -e "\n=== Recent Logs (last 50 lines) ==="
journalctl --user -u cams-slideshow -n 50 --no-pager

echo -e "\n=== Follow logs in real-time (Ctrl+C to exit) ==="
echo "Press Enter to start following logs, or Ctrl+C to exit..."
read -r

journalctl --user -u cams-slideshow -f