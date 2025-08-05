# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

This is a CAMS (Camera & Astronomy Monitoring System) slideshow application that displays meteor detection images from FITS files in a fullscreen slideshow. The application automatically fetches new images from a remote RMS (Radio Meteor Station) system and converts FITS astronomical data to displayable PNG images.

## Development Commands

### Environment Setup
```bash
# Install dependencies using uv
uv sync

# Run the slideshow application
uv run python slideshow.py

# Run in debug mode
uv run python slideshow.py --debug

# Run in fullscreen mode
uv run python slideshow.py --full-screen

# Fetch latest images from remote system (testing)
uv run python slideshow.py --fetch_latest_images

# Use specific image directory
uv run python slideshow.py -i /path/to/images
```

### FITS to Image Conversion
```bash
# Convert FITS files to images using the fitstoimg utility
cd fitstoimg
python fitstoimg.py
```

### Jupyter Development
```bash
# Run Jupyter notebook for development/testing
./runjupyter.sh
# or
uv run --group dev jupyter notebook
```

## Architecture

### Core Components

**slideshow.py** - Main application with three key responsibilities:
1. **Multi-Station Image Management**: Automatically fetches new FITS files from ALL active stations via SSH/rsync
2. **FITS Processing**: Converts astronomical FITS files to PNG images using matplotlib and astropy
3. **Slideshow Display**: Shows images from all stations in fullscreen Tkinter interface with station metadata

**State Management**: 
- Uses `State` dataclass to track last directory per station, switch times, and active stations list
- Persists state in `latest_state.json` for resuming across sessions
- Automatically switches to new image sets when available from any station

**Multi-Station Remote Integration**:
- Connects to RMS host at `pi@10.10.0.176` via SSH
- Dynamically discovers all available stations (station-1, station-2, station-3, etc.)
- Fetches fresh images from ALL active stations daily to show visitors content from all locations
- Uses rsync to synchronize FITS files from `RMS_data/{station}/ArchivedFiles/` for each station
- Only updates when new directories contain >4 FITS files per station
- Updates checked daily after 9 AM
- Flexible architecture automatically adapts when stations are added/removed
- Organizes images by station in subdirectories during fetch process

### Directory Structure
- `current/` - Active FITS files (organized by station subdirectories) and converted PNG slides
- `current_old/` - Previous image set (backup)
- `latest/` - Temporary directory for incoming files (organized by station subdirectories)
  - `latest/station-1/` - FITS files from station-1
  - `latest/station-2/` - FITS files from station-2
  - etc.
- `data/` - Sample/test FITS data archives
- `fitstoimg/` - Standalone FITS conversion utility
- `support/` - UI assets and reference images

### Dependencies
- **astropy**: FITS file handling and astronomical data processing
- **matplotlib**: Image rendering with astronomical styling
- **PIL/Pillow**: Image manipulation and format conversion
- **tkinter**: GUI framework for slideshow display
- **pytz**: Timezone handling for timestamp conversion

### Key Features
- Multi-station support with automatic station discovery and flexible scaling
- Fetches and displays fresh images from ALL active stations daily
- Station identification in image metadata and filenames
- Automatic image scaling and aspect ratio preservation
- Date/time extraction from FITS filenames with Brussels timezone conversion
- Keyboard controls (Escape/Enter/Space to exit fullscreen)
- Error handling for network issues and file conversion failures
- Configurable slide duration (default 5 seconds)
- Backward compatibility with single-station setups