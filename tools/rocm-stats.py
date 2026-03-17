#!/usr/bin/env python3
"""
GPU stats HTTP endpoint for Glance dashboard (RX 7900 GRE / ROCm).

Runs on the host, listens on 0.0.0.0:40404.
Returns styled HTML that the Glance 'extension' widget embeds directly.

GET /        → HTML widget (GPU use, VRAM, power, temps, clock)
GET /health  → plain "ok" for monitoring
"""
import re
import subprocess
from http.server import BaseHTTPRequestHandler, HTTPServer

PORT = 40404
ROCM_CMD = [
    "rocm-smi",
    "--showuse",
    "--showmemuse",
    "--showtemp",
    "--showpower",
    "--showmaxpower",
    "--showclocks",
]


def run_rocm_smi():
    try:
        result = subprocess.run(
            ROCM_CMD, capture_output=True, text=True, timeout=5
        )
        return result.stdout, None
    except FileNotFoundError:
        return None, "rocm-smi not found"
    except subprocess.TimeoutExpired:
        return None, "rocm-smi timed out"


def parse_gpu0(output):
    """Extract GPU[0] (7900 GRE) key→value pairs from rocm-smi text output."""
    stats = {}
    for line in output.splitlines():
        m = re.match(r"GPU\[0\]\s+:\s+(.+?):\s*(.+)", line)
        if m:
            stats[m.group(1).strip()] = m.group(2).strip()
    return stats


def find(stats, *patterns):
    """Return first value whose key contains all patterns (case-insensitive)."""
    for k, v in stats.items():
        kl = k.lower()
        if all(p.lower() in kl for p in patterns):
            return v
    return "N/A"


def clock_mhz(raw):
    """Parse 'level: N: (XXXMhz)' → 'XXX MHz'."""
    m = re.search(r"\((\d+(?:\.\d+)?)\s*[Mm]hz\)", raw)
    return (m.group(1) + " MHz") if m else raw


def pct_bar(pct, color):
    pct = max(0, min(100, pct))
    return (
        f'<div style="background:rgba(255,255,255,0.1);border-radius:3px;'
        f'height:5px;margin:2px 0 8px">'
        f'<div style="background:{color};width:{pct}%;height:100%;border-radius:3px"></div>'
        f"</div>"
    )


def temp_color(val):
    try:
        t = float(val)
        if t < 65:
            return "#4caf50"
        if t < 80:
            return "#ff9800"
        return "#f44336"
    except (ValueError, TypeError):
        return "#aaa"


def render(stats, error):
    if error:
        return (
            f'<div style="color:#f55;padding:8px;font-size:0.85em">'
            f"rocm-smi error: {error}</div>"
        )

    gpu_use   = find(stats, "GPU use")
    vram      = find(stats, "Memory Allocated", "VRAM")
    temp_edge = find(stats, "Temperature", "edge")
    temp_junc = find(stats, "Temperature", "junction")
    power     = find(stats, "Average Graphics Package Power")
    power_max = find(stats, "Max Graphics Package Power")
    sclk      = clock_mhz(find(stats, "sclk clock"))

    try:
        gpu_pct = int(float(gpu_use))
    except (ValueError, TypeError):
        gpu_pct = 0
    try:
        vram_pct = int(float(vram))
    except (ValueError, TypeError):
        vram_pct = 0
    try:
        pwr_pct = int(float(power) / float(power_max) * 100)
    except (ValueError, TypeError, ZeroDivisionError):
        pwr_pct = 0

    tc = temp_color(temp_junc)

    return f"""<div style="font-size:0.85em;line-height:1.3;padding:2px 0">
  <div>
    <div style="display:flex;justify-content:space-between">
      <span style="opacity:0.7">GPU</span>
      <span style="font-weight:600">{gpu_use}%</span>
    </div>
    {pct_bar(gpu_pct, "#00bcd4")}
  </div>
  <div>
    <div style="display:flex;justify-content:space-between">
      <span style="opacity:0.7">VRAM</span>
      <span style="font-weight:600">{vram}%</span>
    </div>
    {pct_bar(vram_pct, "#7c4dff")}
  </div>
  <div>
    <div style="display:flex;justify-content:space-between">
      <span style="opacity:0.7">Power</span>
      <span style="font-weight:600">{power} W
        <span style="opacity:0.5;font-size:0.9em">/ {power_max} W</span>
      </span>
    </div>
    {pct_bar(pwr_pct, "#ff9800")}
  </div>
  <div style="display:flex;gap:12px;margin-top:2px;opacity:0.85">
    <span>Edge <b>{temp_edge}°C</b></span>
    <span style="color:{tc}">Junc <b>{temp_junc}°C</b></span>
    <span style="opacity:0.7">Clk <b>{sclk}</b></span>
  </div>
</div>"""


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass  # suppress per-request stdout noise

    def do_GET(self):
        if self.path == "/health":
            self._send(200, "text/plain", b"ok")
            return
        output, err = run_rocm_smi()
        if err:
            html = render(None, err)
        else:
            html = render(parse_gpu0(output), None)
        self._send(200, "text/html; charset=utf-8", html.encode(),
                   {"Widget-Content-Type": "html"})

    def _send(self, code, content_type, body, extra_headers=None):
        self.send_response(code)
        self.send_header("Content-Type", content_type)
        if extra_headers:
            for k, v in extra_headers.items():
                self.send_header(k, v)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


if __name__ == "__main__":
    server = HTTPServer(("0.0.0.0", PORT), Handler)
    print(f"rocm-stats listening on 0.0.0.0:{PORT}")
    server.serve_forever()
