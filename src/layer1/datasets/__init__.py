"""External geo-dataset ingest and configuration.

This package owns the small infrastructure for ingesting authoritative
companion datasets that are referenced from a bylaw (e.g. height precinct
maps). The canonical attribute schema (see ``canonical.py``) defines the
vocabulary the retrieval API speaks; per-dataset YAML files (see
``config.py``) declare how each source's raw fields map into it.
"""
