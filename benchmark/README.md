# Benchmark

This directory contains manifests and small redistributable fixtures only. Do not commit copyrighted books or private source scans.

The first real benchmark should contain 30–50 representative pages with manually verified ground truth spanning clear/poor scans, chapter starts, punctuation, footnotes, images, headers/footers, and Chinese/English/Japanese samples.

Required metrics: CER, WER, punctuation accuracy, missing/extra line rate, reading-order errors, wall time, pages/minute, peak RAM/VRAM when measurable.

Compare:
A. MinerU only
B. PaddleOCR only
C. MinerU + PaddleOCR disagreement pipeline
D. C + VLM review of conflict crops
