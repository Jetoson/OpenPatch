"""Visual theme for the OpenPatch dashboard.
"""

import base64

_ICON_ATTRS = 'width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"'

ICONS = {
    "server": f'<svg {_ICON_ATTRS}><rect x="3" y="4" width="18" height="7" rx="1.5"/><rect x="3" y="13" width="18" height="7" rx="1.5"/><circle cx="7" cy="7.5" r="0.6" fill="currentColor" stroke="none"/><circle cx="7" cy="16.5" r="0.6" fill="currentColor" stroke="none"/></svg>',
    "wifi": f'<svg {_ICON_ATTRS}><path d="M5 12.5a10 10 0 0 1 14 0"/><path d="M8.2 15.8a6 6 0 0 1 7.6 0"/><circle cx="12" cy="19" r="1" fill="currentColor" stroke="none"/></svg>',
    "wifi-off": f'<svg {_ICON_ATTRS}><path d="M5 12.5a10 10 0 0 1 14 0"/><path d="M8.2 15.8a6 6 0 0 1 7.6 0"/><circle cx="12" cy="19" r="1" fill="currentColor" stroke="none"/><line x1="3" y1="3" x2="21" y2="21"/></svg>',
    "cpu": f'<svg {_ICON_ATTRS}><rect x="6" y="6" width="12" height="12" rx="2"/><rect x="9.5" y="9.5" width="5" height="5" rx="1"/><line x1="12" y1="2" x2="12" y2="5"/><line x1="12" y1="19" x2="12" y2="22"/><line x1="2" y1="12" x2="5" y2="12"/><line x1="19" y1="12" x2="22" y2="12"/></svg>',
    "activity": f'<svg {_ICON_ATTRS}><polyline points="3 12 8 12 10 6 14 18 16 12 21 12"/></svg>',
    "clock": f'<svg {_ICON_ATTRS}><circle cx="12" cy="12" r="9"/><polyline points="12 7 12 12 15.5 14"/></svg>',
    "shield": f'<svg {_ICON_ATTRS}><path d="M12 3l7 3v6c0 4.5-3 7.5-7 9-4-1.5-7-4.5-7-9V6l7-3z"/></svg>',
    "boxes": f'<svg {_ICON_ATTRS}><path d="M21 8l-9-5-9 5 9 5 9-5z"/><path d="M3 8v8l9 5 9-5V8"/><path d="M12 13v8"/></svg>',
    "alert-triangle": f'<svg {_ICON_ATTRS}><path d="M12 3l10 18H2L12 3z"/><line x1="12" y1="9" x2="12" y2="14"/><circle cx="12" cy="17.3" r="0.6" fill="currentColor" stroke="none"/></svg>',
}

# Per-endpoint OS icons

_WINDOWS_BLUE = "#00A4EF"

_OS_ICON_SVG = {
    "windows-11": (
        '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" width="20" height="20" '
        f'fill="{_WINDOWS_BLUE}"><rect x="2.4" y="2.4" width="8.8" height="8.8" rx="0.8"/>'
        '<rect x="12.8" y="2.4" width="8.8" height="8.8" rx="0.8"/>'
        '<rect x="2.4" y="12.8" width="8.8" height="8.8" rx="0.8"/>'
        '<rect x="12.8" y="12.8" width="8.8" height="8.8" rx="0.8"/></svg>'
    ),

    "windows-10": (
        '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" width="20" height="20" '
        f'fill="{_WINDOWS_BLUE}"><path d="M2 5.4 10.4 4.2 10.4 11.4 2 11.4Z"/>'
        '<path d="M11.5 4.05 22 2.6 22 11.4 11.5 11.4Z"/>'
        '<path d="M2 12.5 10.4 12.5 10.4 19.7 2 18.6Z"/>'
        '<path d="M11.5 12.5 22 12.5 22 21.2 11.5 19.85Z"/></svg>'
    ),

    "unknown": (
        '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" width="20" height="20" '
        'fill="none" stroke="#8B93A7" stroke-width="1.7" stroke-linecap="round">'
        '<rect x="2.6" y="4.2" width="18.8" height="13" rx="2"/>'
        '<line x1="8.5" y1="20.6" x2="15.5" y2="20.6"/>'
        '<line x1="12" y1="17.2" x2="12" y2="20.6"/></svg>'
    ),
}


def _svg_data_url(svg: str) -> str:
    return "data:image/svg+xml;base64," + base64.b64encode(svg.encode("utf-8")).decode("ascii")


OS_ICONS = {name: _svg_data_url(svg) for name, svg in _OS_ICON_SVG.items()}


FIRST_WINDOWS_11_BUILD = 22000


def _windows_build(os_version: str | None) -> int | None:
    """Build number out of the agent's platform.version() string.
    """
    if not os_version:
        return None
    parts = str(os_version).strip().split(".")
    if len(parts) >= 3 and parts[0] == "10" and parts[2].isdigit():
        return int(parts[2])
    return None


def os_icon(os_version: str | None) -> str:
    """Data URL for the icon matching an endpoint's reported OS version.
    """
    build = _windows_build(os_version)
    if build is None:
        return OS_ICONS["unknown"]
    return OS_ICONS["windows-11" if build >= FIRST_WINDOWS_11_BUILD else "windows-10"]


def os_display_name(os_name: str | None, os_version: str | None) -> str:
    """Human readable OS label for the fleet table.
    """
    if os_name:
        return os_name
    build = _windows_build(os_version)
    if build is None:
        return os_version or "Unknown"
    return "Windows 11" if build >= FIRST_WINDOWS_11_BUILD else "Windows 10"

PALETTE = {
    "bg": "#0A0E17",
    "surface": "#12172A",
    "surface-2": "#171D33",
    "border": "rgba(148, 163, 184, 0.14)",
    "border-strong": "rgba(148, 163, 184, 0.28)",
    "text": "#E7EBF3",
    "text-dim": "#8B93A7",
    "accent": "#6366F1",
    "accent-hover": "#7C7FF5",
    "accent-tint": "rgba(99, 102, 241, 0.16)",
    "accent-border": "rgba(99, 102, 241, 0.40)",
    "cyan": "#22D3EE",
    "cyan-tint": "rgba(34, 211, 238, 0.16)",
    "success": "#34D399",
    "success-tint": "rgba(52, 211, 153, 0.16)",
    "success-border": "rgba(52, 211, 153, 0.40)",
    "warning": "#FBBF24",
    "warning-tint": "rgba(251, 191, 36, 0.16)",
    "warning-border": "rgba(251, 191, 36, 0.40)",
    "danger": "#F87171",
    "danger-tint": "rgba(248, 113, 113, 0.16)",
    "danger-border": "rgba(248, 113, 113, 0.40)",
    "glow-1": "rgba(99, 102, 241, 0.18)",
    "glow-2": "rgba(34, 211, 238, 0.10)",
    "grid-line": "rgba(255, 255, 255, 0.035)",
    "shadow": "0 8px 24px rgba(0, 0, 0, 0.35)",
}

_CSS_TEMPLATE = """
<style>
@import url('https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700;800&display=swap');

:root {{
  --op-bg: {bg};
  --op-surface: {surface};
  --op-surface-2: {surface-2};
  --op-border: {border};
  --op-border-strong: {border-strong};
  --op-text: {text};
  --op-text-dim: {text-dim};

  --op-accent: {accent};
  --op-accent-hover: {accent-hover};
  --op-accent-tint: {accent-tint};
  --op-accent-border: {accent-border};

  --op-cyan: {cyan};
  --op-cyan-tint: {cyan-tint};

  --op-success: {success};
  --op-success-tint: {success-tint};
  --op-success-border: {success-border};

  --op-warning: {warning};
  --op-warning-tint: {warning-tint};
  --op-warning-border: {warning-border};

  --op-danger: {danger};
  --op-danger-tint: {danger-tint};
  --op-danger-border: {danger-border};

  --op-glow-1: {glow-1};
  --op-glow-2: {glow-2};
  --op-grid-line: {grid-line};
  --op-shadow: {shadow};

  --op-radius: 14px;
}}

html, body, [class*="css"] {{
  font-family: 'Inter', -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif !important;
}}

html, body {{ background: var(--op-bg); }}
[data-testid="stAppViewContainer"] {{
  background-color: var(--op-bg);
  background-image:
    radial-gradient(circle at 6% -8%, var(--op-glow-1) 0%, transparent 42%),
    radial-gradient(circle at 100% 0%, var(--op-glow-2) 0%, transparent 38%),
    linear-gradient(var(--op-grid-line) 1px, transparent 1px),
    linear-gradient(90deg, var(--op-grid-line) 1px, transparent 1px);
  background-size: auto, auto, 44px 44px, 44px 44px;
  background-position: 0 0, 0 0, 0 0, 0 0;
  background-repeat: no-repeat, no-repeat, repeat, repeat;
}}
[data-testid="stHeader"] {{ background: transparent; }}

[data-testid="stSidebar"] {{
  background: var(--op-surface);
  border-right: 1px solid var(--op-border);
}}
[data-testid="stSidebar"] .stButton > button {{
  width: 100%;
  justify-content: flex-start;
  border-radius: 10px;
  font-weight: 500;
  margin-bottom: 0.15rem;
}}

.stButton > button {{
  border-radius: 10px;
  font-weight: 600;
  cursor: pointer;
  transition: background-color 120ms ease, border-color 120ms ease,
              color 120ms ease, box-shadow 120ms ease, transform 60ms ease;
  will-change: transform;
}}
.stButton > button:active:not(:disabled) {{
  transform: translateY(1px) scale(0.995);
  box-shadow: none;
}}
.stButton > button:disabled {{
  opacity: 0.45;
  cursor: not-allowed;
}}
/* Keyboard focus stays visible */
.stButton > button:focus:not(:focus-visible) {{ box-shadow: none !important; }}
.stButton > button:focus-visible {{
  outline: 2px solid var(--op-accent);
  outline-offset: 2px;
}}

[data-testid="stBaseButton-primary"] {{
  background: var(--op-accent) !important;
  border-color: var(--op-accent) !important;
  color: #FFFFFF !important;
}}
[data-testid="stBaseButton-primary"]:hover:not(:disabled) {{
  background: var(--op-accent-hover) !important;
  border-color: var(--op-accent-hover) !important;
  box-shadow: 0 2px 10px var(--op-accent-tint);
}}
[data-testid="stBaseButton-secondary"] {{
  background: var(--op-surface) !important;
  border-color: var(--op-border-strong) !important;
  color: var(--op-text) !important;
}}
[data-testid="stBaseButton-secondary"]:hover:not(:disabled) {{
  border-color: var(--op-accent) !important;
  color: var(--op-accent) !important;
  background: var(--op-surface-2) !important;
}}

[data-testid="stStatusWidget"] {{
  opacity: 0;
  animation: op-status-appear 150ms ease-in 400ms forwards;
}}
@keyframes op-status-appear {{ to {{ opacity: 1; }} }}

[data-testid="stAppViewContainer"] .stApp[data-test-script-state="running"] {{
  opacity: 1 !important;
}}
a, a:visited {{ color: var(--op-accent); }}

[data-testid="stSidebar"] [data-testid="stImage"] img {{
  width: 170px !important;
  height: auto !important;
}}

/* --- Brand header --- */
.op-brand {{
  display: flex; align-items: center; gap: 0.6rem;
  font-size: 1.15rem; font-weight: 800; color: var(--op-text);
  margin-bottom: 0.1rem;
}}
.op-brand svg {{ color: var(--op-accent); flex-shrink: 0; }}

/* --- KPI cards --- */
.op-kpi-grid {{
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(150px, 1fr));
  gap: 0.85rem;
  margin-bottom: 1.5rem;
}}
.op-kpi {{
  position: relative;
  background: var(--op-surface);
  border: 1px solid var(--op-border);
  border-radius: var(--op-radius);
  padding: 1rem 1.1rem;
  overflow: hidden;
  box-shadow: var(--op-shadow);
}}
.op-kpi::before {{
  content: ""; position: absolute; top: 0; left: 0; right: 0; height: 3px;
}}
.op-kpi-icon {{
  width: 32px; height: 32px; border-radius: 9px;
  display: flex; align-items: center; justify-content: center;
  margin-bottom: 0.65rem;
}}
.op-kpi-label {{
  font-size: 0.74rem; color: var(--op-text-dim); text-transform: uppercase;
  letter-spacing: 0.06em; font-weight: 600;
}}
.op-kpi-value {{
  font-size: 1.65rem; font-weight: 700; color: var(--op-text);
  font-variant-numeric: tabular-nums; line-height: 1.3;
}}
.op-kpi-sub {{ font-size: 0.76rem; color: var(--op-text-dim); margin-top: 0.1rem; }}

.op-kpi--accent::before {{ background: var(--op-accent); }}
.op-kpi--accent .op-kpi-icon {{ color: var(--op-accent); background: var(--op-accent-tint); }}
.op-kpi--cyan::before {{ background: var(--op-cyan); }}
.op-kpi--cyan .op-kpi-icon {{ color: var(--op-cyan); background: var(--op-cyan-tint); }}
.op-kpi--success::before {{ background: var(--op-success); }}
.op-kpi--success .op-kpi-icon {{ color: var(--op-success); background: var(--op-success-tint); }}
.op-kpi--warning::before {{ background: var(--op-warning); }}
.op-kpi--warning .op-kpi-icon {{ color: var(--op-warning); background: var(--op-warning-tint); }}
.op-kpi--danger::before {{ background: var(--op-danger); }}
.op-kpi--danger .op-kpi-icon {{ color: var(--op-danger); background: var(--op-danger-tint); }}
.op-kpi--neutral::before {{ background: var(--op-border-strong); }}
.op-kpi--neutral .op-kpi-icon {{ color: var(--op-text-dim); background: var(--op-surface-2); }}

/* --- A KPI card that is also a button --- */
[class*="st-key-op_kpi_"] {{ position: relative; }}
[class*="st-key-op_kpi_"] [data-testid="stMarkdownContainer"] {{ margin-bottom: 0; }}
[class*="st-key-op_kpi_"] > [data-testid="stElementContainer"]:has(button) {{
  position: absolute;
  inset: 0;
  width: 100% !important;
  height: 100% !important;
  z-index: 2;
}}
[class*="st-key-op_kpi_"] [data-testid="stButton"] {{
  position: absolute;
  inset: 0;
}}
[class*="st-key-op_kpi_"] button {{
  position: absolute;
  inset: 0;
  width: 100%;
  height: 100%;
  opacity: 0;
  border: none;
  background: transparent;
  cursor: pointer;
}}
[class*="st-key-op_kpi_"] .op-kpi {{
  height: 100%;
  min-height: 190px;
  margin-bottom: 0;
  transition: transform 120ms ease, border-color 120ms ease, box-shadow 120ms ease;
}}
[class*="st-key-op_kpi_"]:has(button):hover .op-kpi {{
  transform: translateY(-2px);
  border-color: var(--op-border-strong);
  box-shadow: 0 12px 28px rgba(0, 0, 0, 0.45);
}}
[class*="st-key-op_kpi_"]:has(button:focus-visible) .op-kpi {{
  outline: 2px solid var(--op-accent);
  outline-offset: 2px;
}}

.op-badge {{
  display: inline-flex; align-items: center; gap: 0.4em;
  padding: 0.22em 0.75em; border-radius: 999px;
  font-size: 0.78rem; font-weight: 600; white-space: nowrap;
}}
.op-badge--online {{ background: var(--op-success-tint); color: var(--op-success); border: 1px solid var(--op-success-border); }}
.op-badge--offline {{ background: var(--op-danger-tint); color: var(--op-danger); border: 1px solid var(--op-danger-border); }}
.op-badge--warn {{ background: var(--op-warning-tint); color: var(--op-warning); border: 1px solid var(--op-warning-border); }}
.op-badge--neutral {{ background: var(--op-surface-2); color: var(--op-text-dim); border: 1px solid var(--op-border-strong); }}

/* --- Section heading --- */
.op-section-title {{
  display: flex; align-items: center; gap: 0.5rem;
  font-size: 1.05rem; font-weight: 700; color: var(--op-text);
  margin: 0.2rem 0 0.8rem 0;
}}
.op-section-title svg {{ color: var(--op-accent); }}

.op-hint {{
  color: var(--op-text-dim); font-size: 0.85rem;
  padding: 0.9rem 1rem; border: 1px dashed var(--op-border-strong);
  border-radius: var(--op-radius); background: var(--op-surface-2);
}}
</style>
"""


def css() -> str:
    """Full <style> block for the dashboard's (only) theme."""
    return _CSS_TEMPLATE.format(**PALETTE)


def kpi_card(icon_key: str, label: str, value, accent: str = "accent", sub: str | None = None) -> str:
    icon = ICONS.get(icon_key, "")
    sub_html = f'<div class="op-kpi-sub">{sub}</div>' if sub else ""
    return (
        f'<div class="op-kpi op-kpi--{accent}">'
        f'<div class="op-kpi-icon">{icon}</div>'
        f'<div class="op-kpi-label">{label}</div>'
        f'<div class="op-kpi-value">{value}</div>'
        f"{sub_html}"
        f"</div>"
    )


def kpi_row(cards: list[str]) -> str:
    return f'<div class="op-kpi-grid">{"".join(cards)}</div>'


def badge(text: str, kind: str = "neutral") -> str:
    return f'<span class="op-badge op-badge--{kind}">{text}</span>'


def hint(text: str) -> str:
    return f'<div class="op-hint">{text}</div>'


_CLOCK_TEMPLATE = """
<style>
  @import url('https://fonts.googleapis.com/css2?family=JetBrains+Mono:wght@700&display=swap');
</style>
<div style="width:100%;height:100%;box-sizing:border-box;display:flex;align-items:stretch;
            justify-content:center;background:{surface};border:1px solid {border};
            border-radius:16px;box-shadow:{shadow};overflow:hidden;
            font-family:'Inter',-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;">

  <div style="flex:1;display:flex;flex-direction:column;align-items:center;justify-content:center;
              padding:0.85rem 1.1rem;gap:0.2rem;min-width:0;">
    <div style="font-size:0.7rem;font-weight:700;letter-spacing:0.08em;color:{accent};
                text-transform:uppercase;">Local Time</div>
    <div id="op-clock-local-time"
         style="font-family:'JetBrains Mono','Courier New',monospace;font-size:1.7rem;font-weight:700;
                color:{text};font-variant-numeric:tabular-nums;line-height:1.15;">--:--:--</div>
    <div id="op-clock-local-date" style="font-size:0.72rem;color:{text_dim};text-align:center;">Loading...</div>
  </div>

  <div style="width:1px;flex-shrink:0;background:{border_strong};margin:0.8rem 0;"></div>

  <div style="flex:1;display:flex;flex-direction:column;align-items:center;justify-content:center;
              padding:0.85rem 1.1rem;gap:0.2rem;min-width:0;">
    <div style="display:flex;align-items:center;gap:0.4rem;">
      <span style="font-size:0.7rem;font-weight:700;letter-spacing:0.08em;color:{cyan};
                   text-transform:uppercase;">UTC</span>
      <span style="font-size:0.6rem;font-weight:700;color:{cyan};background:{cyan_tint};
                   padding:0.05rem 0.4rem;border-radius:999px;">Z</span>
    </div>
    <div id="op-clock-utc-time"
         style="font-family:'JetBrains Mono','Courier New',monospace;font-size:1.7rem;font-weight:700;
                color:{cyan};font-variant-numeric:tabular-nums;line-height:1.15;">--:--:--</div>
    <div id="op-clock-utc-date" style="font-size:0.72rem;color:{text_dim};text-align:center;">Loading...</div>
  </div>
</div>
<script>
  function opPad(n) {{ return n.toString().padStart(2, "0"); }}

  function opTick() {{
    const now = new Date();  // one instant; both clocks below just read it two different ways

    document.getElementById("op-clock-local-time").textContent =
      opPad(now.getHours()) + ":" + opPad(now.getMinutes()) + ":" + opPad(now.getSeconds());
    document.getElementById("op-clock-local-date").textContent =
      now.toLocaleDateString(undefined, {{ weekday: "long", year: "numeric", month: "long", day: "numeric" }});

    document.getElementById("op-clock-utc-time").textContent =
      opPad(now.getUTCHours()) + ":" + opPad(now.getUTCMinutes()) + ":" + opPad(now.getUTCSeconds());
    document.getElementById("op-clock-utc-date").textContent =
      now.toLocaleDateString(undefined, {{
        weekday: "long", year: "numeric", month: "long", day: "numeric", timeZone: "UTC"
      }});
  }}

  opTick();
  setInterval(opTick, 1000);  // client-side tick only - no Streamlit rerun, no flicker
</script>
"""


def clock_card_html() -> str:
    """Self-contained HTML/JS for a live-updating dual local/UTC clock, for
    st.iframe. Ticks client-side (setInterval) so it never triggers a
    Streamlit rerun. Runs in an iframe with no access to the page's own CSS,
    so colors are inlined directly from PALETTE rather than using."""
    return _CLOCK_TEMPLATE.format(
        surface=PALETTE["surface"],
        border=PALETTE["border"],
        border_strong=PALETTE["border-strong"],
        shadow=PALETTE["shadow"],
        accent=PALETTE["accent"],
        text=PALETTE["text"],
        text_dim=PALETTE["text-dim"],
        cyan=PALETTE["cyan"],
        cyan_tint=PALETTE["cyan-tint"],
    )


def section_title(icon_key: str, text: str) -> str:
    icon = ICONS.get(icon_key, "")
    return f'<div class="op-section-title">{icon}<span>{text}</span></div>'


# Login page
_LOGIN_CSS = """
<style>
[data-testid="stSidebar"],
[data-testid="stSidebarCollapsedControl"],
[data-testid="stToolbar"],
[data-testid="stDecoration"] { display: none !important; }

[data-testid="stMainBlockContainer"], .block-container {
  padding-top: 5.5vh !important;
  padding-bottom: 4rem !important;
}

[data-testid="stAppViewContainer"]::before {
  content: "";
  position: fixed;
  inset: -30% -10% auto -10%;
  height: 90vh;
  pointer-events: none;
  background:
    radial-gradient(38% 55% at 22% 40%, var(--op-glow-1) 0%, transparent 70%),
    radial-gradient(34% 50% at 78% 30%, var(--op-glow-2) 0%, transparent 70%);
  filter: blur(12px);
  animation: op-login-drift 22s ease-in-out infinite alternate;
}
@keyframes op-login-drift {
  from { transform: translate3d(-3%, -2%, 0) scale(1); }
  to   { transform: translate3d(4%, 3%, 0) scale(1.08); }
}

/* --- The card --- */
.st-key-op_login_card {
  position: relative;
  background: linear-gradient(158deg, rgba(23, 29, 51, 0.94), rgba(16, 21, 38, 0.92));
  border: 1px solid var(--op-border);
  border-radius: 20px;
  padding: 2.3rem 2.2rem 1.6rem;
  box-shadow: 0 30px 70px rgba(0, 0, 0, 0.55), inset 0 1px 0 rgba(255, 255, 255, 0.045);
  backdrop-filter: blur(14px);
  overflow: hidden;
  animation: op-login-rise 460ms cubic-bezier(0.2, 0.85, 0.25, 1) both;
}

.st-key-op_login_card::before {
  content: "";
  position: absolute; top: 0; left: 10%; right: 10%; height: 1px;
  background: linear-gradient(90deg, transparent, var(--op-accent), var(--op-cyan), transparent);
}
@keyframes op-login-rise {
  from { opacity: 0; transform: translateY(14px) scale(0.985); }
  to   { opacity: 1; transform: none; }
}

.st-key-op_login_card [data-testid="stForm"] {
  border: none !important;
  padding: 0 !important;
  background: transparent !important;
}

/* --- Brand block --- */
.op-login-head { text-align: center; margin-bottom: 1.6rem; }
.op-login-head img {
  width: 76px; height: 76px; border-radius: 18px;
  box-shadow: 0 10px 26px rgba(0, 0, 0, 0.45);
}
.op-login-title {
  margin-top: 0.85rem;
  font-size: 1.55rem; font-weight: 800; letter-spacing: -0.02em; color: var(--op-text);
}
.op-login-sub {
  margin-top: 0.3rem; font-size: 0.86rem; color: var(--op-text-dim);
}
.op-login-chip {
  display: inline-flex; align-items: center; gap: 0.4rem;
  margin-top: 0.9rem; padding: 0.25rem 0.7rem;
  border: 1px solid var(--op-border-strong); border-radius: 999px;
  background: var(--op-surface-2);
  font-size: 0.72rem; font-weight: 600; color: var(--op-text-dim);
}
.op-login-chip svg { color: var(--op-success); }

/* --- Fields --- */
.st-key-op_login_card [data-testid="stWidgetLabel"] p {
  font-size: 0.72rem !important;
  font-weight: 700 !important;
  letter-spacing: 0.08em;
  text-transform: uppercase;
  color: var(--op-text-dim) !important;
}

.st-key-op_login_card [data-testid="stTextInputRootElement"],
.st-key-op_login_card .stTextInput div[data-baseweb="input"] {
  background: rgba(10, 14, 23, 0.72) !important;
  border-style: solid !important;
  border-width: 1px !important;
  border-color: var(--op-border-strong) !important;
  border-radius: 11px !important;
  transition: border-color 140ms ease, box-shadow 140ms ease;
}
.st-key-op_login_card [data-testid="stTextInputRootElement"]:focus-within,
.st-key-op_login_card .stTextInput div[data-baseweb="input"]:focus-within {
  border-color: var(--op-accent) !important;
  box-shadow: 0 0 0 3px var(--op-accent-tint) !important;
}
.st-key-op_login_card .stTextInput input {
  height: 2.75rem;
  font-size: 0.95rem;
  color: var(--op-text) !important;
}
.st-key-op_login_card .stTextInput input::placeholder { color: rgba(139, 147, 167, 0.6); }

/* --- Submit --- */
.st-key-op_login_card [data-testid="stFormSubmitButton"] {
  width: 100% !important;
}
.st-key-op_login_card [data-testid="stFormSubmitButton"] button {
  width: 100%;
  height: 2.9rem;
  margin-top: 0.5rem;
  border: none !important;
  border-radius: 11px;
  font-size: 0.95rem; font-weight: 700;
  color: #FFFFFF !important;
  background: linear-gradient(120deg, var(--op-accent) 0%, #4F7BF7 58%, var(--op-cyan) 135%) !important;
  box-shadow: 0 10px 24px rgba(99, 102, 241, 0.28);
  transition: transform 80ms ease, box-shadow 160ms ease, filter 160ms ease;
}
.st-key-op_login_card [data-testid="stFormSubmitButton"] button:hover:not(:disabled) {
  filter: brightness(1.08);
  box-shadow: 0 14px 30px rgba(99, 102, 241, 0.36);
}
.st-key-op_login_card [data-testid="stFormSubmitButton"] button:active:not(:disabled) {
  transform: translateY(1px);
  box-shadow: 0 6px 16px rgba(99, 102, 241, 0.26);
}
.st-key-op_login_card [data-testid="stFormSubmitButton"] button:disabled {
  filter: grayscale(0.5) brightness(0.8);
  box-shadow: none;
}

/* --- Result messages --- */
.op-login-alert {
  display: flex; align-items: flex-start; gap: 0.55rem;
  margin-top: 0.9rem; padding: 0.7rem 0.85rem;
  border-radius: 11px; font-size: 0.85rem; line-height: 1.45;
}
.op-login-alert svg { flex-shrink: 0; margin-top: 0.1rem; }
.op-login-alert--error {
  background: var(--op-danger-tint); border: 1px solid var(--op-danger-border);
  color: var(--op-danger);
  animation: op-login-shake 320ms cubic-bezier(0.36, 0.07, 0.19, 0.97) both;
}
.op-login-alert--wait {
  background: var(--op-warning-tint); border: 1px solid var(--op-warning-border);
  color: var(--op-warning);
}
@keyframes op-login-shake {
  10%, 90% { transform: translateX(-2px); }
  20%, 80% { transform: translateX(3px); }
  30%, 50%, 70% { transform: translateX(-5px); }
  40%, 60% { transform: translateX(5px); }
}

/* --- Below the card --- */
.op-login-note {
  margin: 1.15rem auto 0;
  text-align: center;
  font-size: 0.78rem; line-height: 1.6; color: var(--op-text-dim);
}
.op-login-note code {
  background: var(--op-surface-2); border: 1px solid var(--op-border);
  border-radius: 6px; padding: 0.05rem 0.35rem;
  font-size: 0.74rem; color: var(--op-text);
}

@media (prefers-reduced-motion: reduce) {
  [data-testid="stAppViewContainer"]::before,
  .st-key-op_login_card,
  .op-login-alert--error { animation: none !important; }
}
</style>
"""


def login_css() -> str:
    """Styling for the signed-out page. Layered on top of css()."""
    return _LOGIN_CSS


def login_header(logo_data_url: str, server_url: str) -> str:
    """Logo, wordmark, and the address this dashboard talks to.
    """
    lock = (
        '<svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" '
        'stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round">'
        '<rect x="4" y="10.5" width="16" height="10.5" rx="2"/>'
        '<path d="M8 10.5V7a4 4 0 0 1 8 0v3.5"/></svg>'
    )
    return (
        '<div class="op-login-head">'
        f'<img src="{logo_data_url}" alt="OpenPatch"/>'
        '<div class="op-login-title">OpenPatch</div>'
        '<div class="op-login-sub">Sign in to the patch &amp; vulnerability console</div>'
        f'<div class="op-login-chip">{lock}<span>{server_url}</span></div>'
        "</div>"
    )


def login_alert(message: str, kind: str = "error") -> str:
    """A message drawn inside the card.
    """
    icons = {
        "error": '<path d="M12 3l10 18H2L12 3z"/><line x1="12" y1="9.5" x2="12" y2="14"/>'
                 '<circle cx="12" cy="17.3" r="0.7" fill="currentColor" stroke="none"/>',
        "wait": '<circle cx="12" cy="12" r="9"/><polyline points="12 7 12 12 15.5 14"/>',
    }
    icon = (
        '<svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" '
        'stroke-width="1.9" stroke-linecap="round" stroke-linejoin="round">'
        f'{icons.get(kind, icons["error"])}</svg>'
    )
    return f'<div class="op-login-alert op-login-alert--{kind}">{icon}<span>{message}</span></div>'
