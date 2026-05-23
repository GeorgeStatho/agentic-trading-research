# Market Data Folder Guide

This folder contains the market-data side of the public demo project.

## Responsibilities

- fetch structured market and ranking data through provider wrappers
- normalize provider responses into a stable internal shape
- support optional live price streaming hooks
- provide a small supplemental news-discovery helper for the company pipeline

The public branch keeps the architecture visible without documenting private provider tuning or production data policies.
