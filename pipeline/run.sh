#!/bin/bash
# Automates execution of the computer vision pipeline on all store video clips

if [ -z "$1" ]; then
    echo "Usage: ./run.sh <path_to_video_directory> [store_id]"
    exit 1
fi

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
VIDEO_DIR=$1
STORE_ID=${2:-"ST1008"}
API_URL="http://localhost:8000/events/ingest"

echo "Processing store intelligence clips for store: ${STORE_ID}..."

# 1. Process Entry Camera Clip
if [ -f "${VIDEO_DIR}/entry.mp4" ]; then
    python "${SCRIPT_DIR}/detect.py" --video "${VIDEO_DIR}/entry.mp4" --camera "CAM_ENTRY_01" --store "${STORE_ID}" --api-url "${API_URL}"
elif [ -f "${VIDEO_DIR}/entry 1.mp4" ]; then
    python "${SCRIPT_DIR}/detect.py" --video "${VIDEO_DIR}/entry 1.mp4" --camera "CAM_ENTRY_01" --store "${STORE_ID}" --api-url "${API_URL}"
fi

if [ -f "${VIDEO_DIR}/entry 2.mp4" ]; then
    python "${SCRIPT_DIR}/detect.py" --video "${VIDEO_DIR}/entry 2.mp4" --camera "CAM_ENTRY_02" --store "${STORE_ID}" --api-url "${API_URL}"
fi

# 2. Process Main Floor Camera Clip
if [ -f "${VIDEO_DIR}/floor.mp4" ]; then
    python "${SCRIPT_DIR}/detect.py" --video "${VIDEO_DIR}/floor.mp4" --camera "CAM_FLOOR_01" --store "${STORE_ID}" --api-url "${API_URL}"
elif [ -f "${VIDEO_DIR}/zone.mp4" ]; then
    python "${SCRIPT_DIR}/detect.py" --video "${VIDEO_DIR}/zone.mp4" --camera "CAM_FLOOR_01" --store "${STORE_ID}" --api-url "${API_URL}"
fi

# 3. Process Billing Camera Clip
if [ -f "${VIDEO_DIR}/billing.mp4" ]; then
    python "${SCRIPT_DIR}/detect.py" --video "${VIDEO_DIR}/billing.mp4" --camera "CAM_BILLING_01" --store "${STORE_ID}" --api-url "${API_URL}"
elif [ -f "${VIDEO_DIR}/billing_area.mp4" ]; then
    python "${SCRIPT_DIR}/detect.py" --video "${VIDEO_DIR}/billing_area.mp4" --camera "CAM_BILLING_01" --store "${STORE_ID}" --api-url "${API_URL}"
fi

echo "Pipeline processing complete."
