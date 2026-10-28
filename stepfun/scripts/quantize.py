#!/usr/bin/env python3
"""Reproduce the INT8 embedding model from the FP32 ONNX export.

The app ships with the official quantized export (`onnx/model_quantized.onnx`)
because it is byte-for-byte reproducible from the same FP32 graph. Run this
script if you want to regenerate it yourself and compare sizes.

    pip install onnx onnxruntime
    python scripts/quantize.py \
        --input  web/public/models/Xenova/bge-small-zh-v1.5/onnx/model.onnx \
        --output web/public/models/Xenova/bge-small-zh-v1.5/onnx/model_int8_selfmade.onnx

    # then rename it to model_quantized.onnx if you want transformers.js to pick it up

Method: ONNX Runtime *dynamic* quantization
  - weights:  FP32 -> INT8 (per-channel where supported)
  - activations: quantized on the fly, scale computed per tensor at runtime
  - no calibration dataset required
"""
from __future__ import annotations

import argparse
import os

from onnxruntime.quantization import QuantType, quantize_dynamic


def main() -> None:
    parser = argparse.ArgumentParser(description="INT8 dynamic quantization for an ONNX embedding model")
    parser.add_argument("--input", required=True, help="path to the FP32 model.onnx")
    parser.add_argument("--output", required=True, help="path of the INT8 model to write")
    parser.add_argument(
        "--weight-type",
        default="qint8",
        choices=["qint8", "quint8"],
        help="QInt8 matches the published model_quantized.onnx",
    )
    args = parser.parse_args()

    if not os.path.exists(args.input):
        raise SystemExit(f"input not found: {args.input}")

    weight_type = QuantType.QInt8 if args.weight_type == "qint8" else QuantType.QUInt8

    quantize_dynamic(
        model_input=args.input,
        model_output=args.output,
        weight_type=weight_type,
        per_channel=True,          # better accuracy at the same size
        reduce_range=False,
        extra_options={"EnableSubgraph": True},
    )

    src = os.path.getsize(args.input)
    dst = os.path.getsize(args.output)
    print(f"FP32 : {src / 1024 / 1024:8.2f} MiB")
    print(f"INT8 : {dst / 1024 / 1024:8.2f} MiB")
    print(f"compression ratio: {src / dst:.2f}x")


if __name__ == "__main__":
    main()
