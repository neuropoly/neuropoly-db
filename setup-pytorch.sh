#!/bin/bash
# Setup script for neuropoly-db environment with 1080Ti (SM 6.1) support

set -e

# Activate venv
source .venv/bin/activate

echo "=== Installing PyTorch 2.1.2 (CUDA 12.1, SM 6.1 compatible) ==="
pip install -r pytorch-requirements.txt --index-url https://download.pytorch.org/whl/cu121

echo "=== Installing remaining dependencies from PyPI ==="
pip install -r requirements.txt

echo "✓ Installation complete!"
echo ""
echo "PyTorch version:"
python -c "import torch; print(f'  {torch.__version__}')"
echo ""
echo "GPU info:"
nvidia-smi --query-gpu=name,compute_cap --format=csv,noheader
