"""Local read-only operations dashboard (openspec: ops-observability-dashboard).

`queries` reads the state stores and returns plain data; `pages` turns that data into HTML;
`routes` mounts the pages on the `ats serve` FastAPI app under `/dashboard`. Nothing here writes.
"""
