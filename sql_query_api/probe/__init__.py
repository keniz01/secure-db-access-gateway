"""Operator health probe shipped inside the image for the connectivity runbook."""

from probe.run import probe_all, probe_one

__all__ = ["probe_all", "probe_one"]