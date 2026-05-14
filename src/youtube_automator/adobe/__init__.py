"""Adobe automation (Premiere + Photoshop). Windows-only at runtime.

These modules generate ExtendScript (.jsx) or UXP scripts that drive Premiere
and Photoshop to fill template slots with the per-video script and assets,
then export the rendered MP4 / PNG.

Kept isolated so the rest of the pipeline (Mac-friendly Python) doesn't pull
in any Adobe deps.
"""
