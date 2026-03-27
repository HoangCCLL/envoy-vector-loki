#!/usr/bin/env python3
"""
Render envoy/envoy.yaml from envoy/envoy.yaml.tmpl + upstreams.yaml.
Replaces the old `envsubst` approach to support multi-upstream Jinja2 templates.
"""
import sys
import yaml
from jinja2 import Environment, FileSystemLoader

UPSTREAMS_FILE = "upstreams.yaml"
TEMPLATE_DIR   = "envoy"
TEMPLATE_FILE  = "envoy.yaml.tmpl"
OUTPUT_FILE    = "envoy/envoy.yaml"

with open(UPSTREAMS_FILE) as f:
    upstreams = yaml.safe_load(f)

env = Environment(
    loader=FileSystemLoader(TEMPLATE_DIR),
    trim_blocks=True,
    lstrip_blocks=True,
    keep_trailing_newline=True,
)
tmpl = env.get_template(TEMPLATE_FILE)
output = tmpl.render(upstreams=upstreams)

with open(OUTPUT_FILE, "w") as f:
    f.write(output)

names = [u["name"] for u in upstreams]
print(f"Rendered {len(upstreams)} upstream(s): {', '.join(names)}")
for u in upstreams:
    proto = "https" if u["tls"] else "http"
    print(f"  /{u['name']}/* → {proto}://{u['host']}:{u['port']}")
