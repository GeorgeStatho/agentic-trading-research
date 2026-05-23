# Sources Folder Guide

This folder defines the public demo source configuration.

## Responsibilities

- define source templates used by the planner
- map URLs to source metadata
- decide whether a source supports `search`, `listing`, or `article`
- provide safe sample article-path patterns

## Public Files

- `urlFactories.py`: demo source templates built around harmless example domains
- `source_config.py`: hostname-to-metadata lookup helpers

The private repository contains the real allowlists, source mix, and ranking metadata.
