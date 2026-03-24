FROM python:3.11-slim

RUN apt-get update && apt-get install -y --no-install-recommends \
    libgl1 libglib2.0-0 \
    && rm -rf /var/lib/apt/lists/*

# Install PyTorch with CUDA support (A10G = sm_86, CUDA 12.x)
RUN pip install --no-cache-dir \
    torch==2.2.2 torchvision==0.17.2 --index-url https://download.pytorch.org/whl/cu121

RUN pip install --no-cache-dir \
    ultralytics==8.3.40 \
    "lap>=0.5.12" \
    av==12.0.0 \
    Pillow==10.4.0 \
    snowflake-connector-python==3.12.3 \
    numpy==1.26.4

WORKDIR /app

# Pre-download YOLOv8n-seg weights at build time (segmentation model for mask polygons)
RUN python -c "from ultralytics import YOLO; YOLO('yolov8n-seg.pt')"

# SDK package (shared with visualizer and local iteration)
COPY retail_vision/ ./retail_vision/

COPY analyze_frames.py .

CMD ["python", "analyze_frames.py"]
