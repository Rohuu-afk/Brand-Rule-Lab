"""Shared design tokens and styling injected into every Streamlit page."""

from __future__ import annotations

import streamlit as st


BRAND_CSS = """
<svg aria-hidden="true" focusable="false" style="position:fixed;width:0;height:0;overflow:hidden;pointer-events:none">
    <filter id="brl-liquid-refraction" x="-8%" y="-8%" width="116%" height="116%" color-interpolation-filters="sRGB">
        <feTurbulence type="fractalNoise" baseFrequency="0.014 0.02" numOctaves="1" seed="8" result="glass-waves" />
        <feGaussianBlur in="glass-waves" stdDeviation="2.5" result="smooth-waves" />
        <feDisplacementMap in="SourceGraphic" in2="smooth-waves" scale="26" xChannelSelector="R" yChannelSelector="G" />
    </filter>
</svg>
<style>
:root {
    --brl-bg: #E7F1F0;
    --brl-bg-secondary: #D8E9E8;
    --brl-pearl: #F6FBFA;
    --brl-frosted: rgba(245, 253, 252, 0.44);
    --brl-raised: rgba(248, 253, 252, 0.7);
    --brl-glass-blur: 30px;
    --brl-glass-surface: linear-gradient(145deg, rgba(255, 255, 255, 0.32), rgba(201, 233, 232, 0.12));
    --brl-glass-reflection: linear-gradient(122deg, rgba(255, 255, 255, 0.52) 0%, rgba(255, 255, 255, 0.22) 12%, transparent 36%), radial-gradient(ellipse at 8% 0%, rgba(255, 255, 255, 0.5), transparent 48%), linear-gradient(300deg, transparent 56%, rgba(255, 255, 255, 0.08) 78%, rgba(255, 255, 255, 0.24));
    --brl-text: #20383B;
    --brl-text-secondary: #294448;
    --brl-text-muted: #2C4649;
    --brl-border: rgba(255, 255, 255, 0.78);
    --brl-dark: #155B61;
    --brl-pass: #34765B;
    --brl-pass-bg: rgba(220, 241, 229, 0.78);
    --brl-fail: #A14F55;
    --brl-fail-bg: rgba(250, 226, 221, 0.82);
    --brl-warning: #8C611F;
    --brl-warning-bg: rgba(249, 238, 210, 0.84);
    --brl-info: #39747A;
    --brl-info-bg: rgba(220, 240, 240, 0.82);
    --brl-radius-sm: 12px;
    --brl-radius: 18px;
    --brl-radius-lg: 24px;
    --brl-shadow-glass: 0 18px 46px rgba(30, 75, 78, 0.12), 0 4px 14px rgba(30, 75, 78, 0.07), inset 1px 1px 0 rgba(255, 255, 255, 0.94), inset -1px -1px 0 rgba(143, 193, 195, 0.2);
    --brl-shadow-raised: 0 10px 24px rgba(30, 75, 78, 0.13), inset 0 1px 0 rgba(255, 255, 255, 0.72);
    --brl-shadow-inset: inset 0 1px 3px rgba(40, 85, 88, 0.12), inset 0 -1px 0 rgba(255, 255, 255, 0.82);
    color-scheme: light;
}

html, body, [class*="css"] {
    font-family: -apple-system, BlinkMacSystemFont, "SF Pro Text", "Helvetica Neue", Arial, sans-serif;
    color: var(--brl-text);
    letter-spacing: 0;
}

.stApp {
    position: relative;
    height: 100vh;
    height: 100dvh;
    overflow: hidden;
    background: linear-gradient(122deg, #70BDB2 0%, #A9DCD2 23%, #83BBD0 49%, #BBDCCB 73%, #D9A98D 100%) !important;
    color: var(--brl-text);
}
.stApp::before {
    content: "";
    position: fixed;
    inset: 0;
    z-index: 0;
    pointer-events: none;
    background:
        linear-gradient(112deg, rgba(16, 93, 98, 0.2) 0%, transparent 31%, transparent 69%, rgba(179, 91, 62, 0.16) 100%),
        linear-gradient(12deg, transparent 0%, rgba(255, 255, 255, 0.38) 42%, rgba(153, 206, 214, 0.16) 76%, transparent 100%),
        repeating-linear-gradient(0deg, rgba(33, 75, 78, 0.018) 0 1px, transparent 1px 4px);
}
[data-testid="stAppViewContainer"] {
    position: relative;
    z-index: 1;
    height: 100%;
    background: transparent !important;
    overflow: hidden;
}
[data-testid="stMain"] {
    height: 100%;
    min-height: 0;
    background: transparent !important;
    overflow-y: auto;
    overscroll-behavior-y: contain;
}

[data-testid="stHeader"] {
    background: rgba(231, 245, 243, 0.3) !important;
    backdrop-filter: blur(var(--brl-glass-blur)) saturate(180%);
    -webkit-backdrop-filter: blur(var(--brl-glass-blur)) saturate(180%);
}

.block-container {
    max-width: 1540px;
    padding: 3.5rem 2.5rem 4rem;
}

h1, h2, h3, h4, p, label, span, div {
    letter-spacing: 0;
}

h1, h2, h3, h4 {
    color: var(--brl-text);
    font-weight: 640;
}

h1 { font-size: 2rem !important; line-height: 1.18 !important; }
h2 { font-size: 1.45rem !important; line-height: 1.3 !important; }
h3 { font-size: 1.12rem !important; line-height: 1.35 !important; }
p, [data-testid="stMarkdownContainer"] { color: inherit; }
[data-testid="stCaptionContainer"], small { color: var(--brl-text-secondary); }

/* A translucent pane sits inside the app background, so the blur has real depth. */
[data-testid="stSidebar"] {
    z-index: 5;
    height: 100vh;
    height: 100dvh;
    max-height: 100dvh;
    overflow: hidden;
    background: transparent !important;
    border: 0 !important;
    box-shadow: none !important;
}
[data-testid="stSidebarContent"] {
    margin: 0.7rem 0 0.7rem 0.7rem;
    height: calc(100vh - 1.4rem);
    height: calc(100dvh - 1.4rem);
    max-height: calc(100dvh - 1.4rem);
    border: 1px solid rgba(255, 255, 255, 0.82);
    border-radius: 24px;
    background: var(--brl-glass-reflection), var(--brl-glass-surface) !important;
    backdrop-filter: blur(var(--brl-glass-blur)) saturate(180%);
    -webkit-backdrop-filter: blur(var(--brl-glass-blur)) saturate(180%);
    box-shadow: 0 18px 44px rgba(30, 75, 78, 0.14),
        inset 1px 1px 0 rgba(255, 255, 255, 0.92),
        inset -1px -1px 0 rgba(135, 192, 194, 0.2);
    overflow-x: hidden;
    overflow-y: auto;
    overscroll-behavior-y: contain;
    scrollbar-width: thin;
    scrollbar-color: rgba(37, 103, 109, 0.42) transparent;
}
[data-testid="stSidebarUserContent"] {
    padding: 0.8rem 0.65rem 1rem;
    background: transparent !important;
}
[data-testid="stSidebar"] [data-testid="stMarkdownContainer"] p,
[data-testid="stSidebar"] label,
[data-testid="stSidebar"] h3,
[data-testid="stSidebar"] h4 {
    color: var(--brl-text) !important;
}
[data-testid="stSidebar"] [data-testid="stCaptionContainer"] {
    color: var(--brl-text-secondary) !important;
}
[data-testid="stSidebar"] [data-testid="stBaseButton-secondary"] p,
[data-testid="stSidebar"] [data-testid="stBaseButton-secondary"] span {
    color: var(--brl-text) !important;
}
[data-testid="stSidebar"] [data-testid="stBaseButton-secondary"] {
    justify-content: flex-start;
    min-height: 2.45rem;
    color: var(--brl-text);
    background: rgba(255, 255, 255, 0.12) !important;
    border: 1px solid transparent !important;
    border-radius: 13px !important;
    box-shadow: none !important;
}
[data-testid="stSidebar"] [data-testid="stBaseButton-secondary"]:hover {
    background: rgba(248, 255, 254, 0.62) !important;
    border-color: rgba(255, 255, 255, 0.78) !important;
    color: var(--brl-text);
    box-shadow: 3px 3px 8px rgba(75, 68, 56, 0.06),
        -3px -3px 8px rgba(255, 255, 255, 0.75) !important;
}
[data-testid="stSidebar"] [data-testid="stBaseButton-primary"] {
    justify-content: flex-start;
    min-height: 2.45rem;
    color: var(--brl-text) !important;
    background: rgba(218, 241, 238, 0.84) !important;
    border: 1px solid rgba(255, 255, 255, 0.9) !important;
    border-radius: 13px !important;
    box-shadow: inset 2px 2px 5px rgba(75, 68, 56, 0.09),
        inset -2px -2px 5px rgba(255, 255, 255, 0.92) !important;
}
[data-testid="stSidebar"] [data-testid="stBaseButton-primary"] p,
[data-testid="stSidebar"] [data-testid="stBaseButton-primary"] span {
    color: var(--brl-text) !important;
}
[data-testid="stSidebar"] [data-testid="stBaseButton-primary"]:hover {
    background: rgba(234, 231, 225, 0.9) !important;
    border-color: rgba(255, 255, 255, 0.96) !important;
    box-shadow: inset 2px 2px 5px rgba(75, 68, 56, 0.08),
        inset -2px -2px 5px rgba(255, 255, 255, 0.96) !important;
    transform: none !important;
}

.brl-wordmark {
    display: flex;
    align-items: center;
    gap: 0.7rem;
    padding: 0.55rem 0.5rem 1rem;
    color: var(--brl-text);
}
.brl-wordmark__mark {
    display: grid;
    place-items: center;
    width: 34px;
    height: 34px;
    border-radius: 11px;
    background: var(--brl-dark);
    color: var(--brl-pearl);
    font-size: 0.73rem;
    font-weight: 720;
}
.brl-wordmark__name { font-size: 0.96rem; font-weight: 680; }
.brl-wordmark__tag { margin-top: 2px; color: var(--brl-text-secondary); font-size: 0.68rem; }
.brl-nav-group {
    margin: 1.15rem 0 0.35rem;
    padding: 0.25rem 0.5rem;
    color: var(--brl-text-muted);
    font-size: 0.68rem;
    font-weight: 660;
    text-transform: uppercase;
}
.brl-sidebar-footer {
    margin-top: 1.5rem;
    padding: 0.9rem 0.55rem 0.4rem;
    border-top: 1px solid rgba(255, 255, 255, 0.62);
    color: var(--brl-text-secondary);
    font-size: 0.66rem;
    line-height: 1.65;
}

.brl-page-heading {
    margin: 0 0 1.65rem;
    padding: 0.35rem 0 1.2rem;
    border-bottom: 1px solid rgba(255, 255, 255, 0.62);
    animation: brl-arrive 260ms ease-out both;
}
.brl-page-heading__eyebrow {
    margin-bottom: 0.55rem;
    color: var(--brl-text-secondary);
    font-size: 0.7rem;
    font-weight: 650;
    text-transform: uppercase;
}
.brl-page-heading__title {
    margin: 0;
    color: var(--brl-text);
    font-size: 2rem;
    font-weight: 660;
    line-height: 1.18;
}
.brl-section-heading {
    margin: 1.65rem 0 0.8rem;
    color: var(--brl-text);
    font-size: 1.08rem;
    font-weight: 640;
}

/* Muted white surfaces; native content stays opaque and legible. */
[data-testid="stMetric"] {
    min-height: 6rem;
    padding: 1rem 1.1rem;
    border: 1px solid rgba(255, 255, 255, 0.82);
    border-radius: var(--brl-radius);
    background: var(--brl-glass-reflection), var(--brl-glass-surface) !important;
    backdrop-filter: blur(var(--brl-glass-blur)) saturate(180%);
    -webkit-backdrop-filter: blur(var(--brl-glass-blur)) saturate(180%);
    box-shadow: var(--brl-shadow-glass), inset 1px 1px 0 rgba(255,255,255,0.92),
        inset -1px -1px 0 rgba(143, 193, 195, 0.16);
}
[data-testid="stMetricLabel"] { color: var(--brl-text-secondary); font-size: 0.82rem; }
[data-testid="stMetricValue"] { color: var(--brl-text); font-weight: 660; }
[data-testid="stVerticalBlockBorderWrapper"] {
    border-color: rgba(255, 255, 255, 0.78) !important;
    border-radius: var(--brl-radius) !important;
    background: var(--brl-glass-reflection), var(--brl-glass-surface) !important;
    backdrop-filter: blur(var(--brl-glass-blur)) saturate(180%);
    -webkit-backdrop-filter: blur(var(--brl-glass-blur)) saturate(180%);
    box-shadow: var(--brl-shadow-glass), inset 1px 1px 0 rgba(255,255,255,0.9),
        inset -1px -1px 0 rgba(143, 193, 195, 0.14);
}
[data-testid="stExpander"] {
    border: 1px solid rgba(255, 255, 255, 0.78);
    border-radius: var(--brl-radius) !important;
    background: var(--brl-glass-reflection), var(--brl-glass-surface) !important;
    backdrop-filter: blur(var(--brl-glass-blur)) saturate(180%);
    -webkit-backdrop-filter: blur(var(--brl-glass-blur)) saturate(180%);
    box-shadow: 0 14px 34px rgba(30, 75, 78, 0.09),
        inset 1px 1px 0 rgba(255, 255, 255, 0.86);
    transition: box-shadow 160ms ease, background-color 160ms ease;
}
[data-testid="stExpander"]:hover {
    background: var(--brl-glass-reflection), var(--brl-glass-surface) !important;
    box-shadow: 0 16px 38px rgba(30, 75, 78, 0.12),
        inset 1px 1px 0 rgba(255, 255, 255, 0.94);
}
[data-testid="stExpander"] details summary {
    color: var(--brl-text);
    font-weight: 570;
}

/* Inset controls use the same warm raised surface as the app background. */
input, textarea, [data-baseweb="select"] > div {
    color: var(--brl-text) !important;
    background: rgba(249, 255, 254, 0.72) !important;
    backdrop-filter: blur(16px) saturate(150%);
    -webkit-backdrop-filter: blur(16px) saturate(150%);
    border: 1px solid rgba(255, 255, 255, 0.86) !important;
    border-radius: 14px !important;
    box-shadow: var(--brl-shadow-inset) !important;
}
input:focus, textarea:focus, [data-baseweb="select"] > div:focus-within {
    border-color: rgba(31, 126, 132, 0.5) !important;
    box-shadow: var(--brl-shadow-inset), 0 0 0 2px rgba(31, 126, 132, 0.16) !important;
}
[data-testid="stForm"] {
    padding: 1.15rem;
    border: 1px solid rgba(255, 255, 255, 0.78);
    border-radius: var(--brl-radius-lg);
    background: var(--brl-glass-reflection), var(--brl-glass-surface);
    backdrop-filter: blur(var(--brl-glass-blur)) saturate(180%);
    -webkit-backdrop-filter: blur(var(--brl-glass-blur)) saturate(180%);
    box-shadow: 0 14px 34px rgba(30, 75, 78, 0.08),
        inset 1px 1px 0 rgba(255,255,255,0.9);
}
[data-testid="stFileUploader"] section {
    border: 1px dashed rgba(39, 111, 116, 0.42);
    border-radius: var(--brl-radius);
    background: var(--brl-glass-reflection), var(--brl-glass-surface);
    backdrop-filter: blur(var(--brl-glass-blur)) saturate(180%);
    -webkit-backdrop-filter: blur(var(--brl-glass-blur)) saturate(180%);
}

button, [role="button"], [data-baseweb="tab"] {
    transition: background-color 140ms ease, border-color 140ms ease,
        color 140ms ease, box-shadow 140ms ease, transform 140ms ease;
}
[data-testid="stBaseButton-primary"], button[kind="primary"] {
    border: 1px solid rgba(255, 255, 255, 0.68) !important;
    border-radius: 14px !important;
    color: var(--brl-pearl) !important;
    background: linear-gradient(135deg, #176D73, #164F58) !important;
    box-shadow: 0 10px 22px rgba(18, 83, 89, 0.24),
        inset 1px 1px 0 rgba(255,255,255,0.3),
        inset -1px -1px 0 rgba(6, 43, 48, 0.22);
    font-weight: 590;
}
[data-testid="stBaseButton-primary"]:hover, button[kind="primary"]:hover {
    background: linear-gradient(135deg, #218087, #175B63) !important;
    box-shadow: 0 14px 26px rgba(18, 83, 89, 0.3),
        inset 1px 1px 0 rgba(255,255,255,0.38);
    transform: translateY(-1px);
}
[data-testid="stBaseButton-secondary"], button[kind="secondary"] {
    border: 1px solid rgba(255, 255, 255, 0.82) !important;
    border-radius: 14px !important;
    color: var(--brl-text) !important;
    background: var(--brl-glass-reflection), var(--brl-glass-surface) !important;
    backdrop-filter: blur(var(--brl-glass-blur)) saturate(180%);
    -webkit-backdrop-filter: blur(var(--brl-glass-blur)) saturate(180%);
    box-shadow: 0 8px 20px rgba(30, 75, 78, 0.1),
        inset 1px 1px 0 rgba(255, 255, 255, 0.88) !important;
}
[data-testid="stBaseButton-secondary"]:hover, button[kind="secondary"]:hover {
    border-color: rgba(255, 255, 255, 0.92) !important;
    background: var(--brl-glass-reflection), linear-gradient(145deg, rgba(255, 255, 255, 0.48), rgba(201, 233, 232, 0.2)) !important;
    box-shadow: 0 12px 24px rgba(30, 75, 78, 0.14),
        inset 1px 1px 0 rgba(255, 255, 255, 0.96) !important;
}

[data-baseweb="tab-list"], [role="tablist"] {
    gap: 0.35rem;
    padding: 0.35rem;
    border: 1px solid rgba(255, 255, 255, 0.72);
    border-radius: 16px;
    background: var(--brl-glass-reflection), var(--brl-glass-surface) !important;
    backdrop-filter: blur(var(--brl-glass-blur)) saturate(180%);
    -webkit-backdrop-filter: blur(var(--brl-glass-blur)) saturate(180%);
}
[data-baseweb="tab"], [role="tab"] {
    color: var(--brl-text-secondary) !important;
    border-radius: 12px;
    background: transparent !important;
    border: 1px solid transparent !important;
}
[data-baseweb="tab"][aria-selected="true"], [role="tab"][aria-selected="true"] {
    color: var(--brl-text) !important;
    font-weight: 620;
    background: rgba(252, 255, 255, 0.72) !important;
    border-color: rgba(255, 255, 255, 0.96) !important;
    box-shadow: 0 6px 16px rgba(30, 75, 78, 0.12),
        inset 1px 1px 0 rgba(255, 255, 255, 0.96);
}
.react-aria-SelectionIndicator {
    background: rgba(133, 121, 99, 0.7) !important;
}
[data-testid="stDataFrame"], [data-testid="stTable"] {
    overflow: hidden;
    border: 1px solid rgba(255, 255, 255, 0.82);
    border-radius: var(--brl-radius);
    background: var(--brl-glass-reflection), var(--brl-glass-surface);
    backdrop-filter: blur(var(--brl-glass-blur)) saturate(180%);
    box-shadow: var(--brl-shadow-glass);
}
[data-testid="stProgress"] > div > div {
    background: linear-gradient(90deg, #19777D, #4D9990);
}

/* Status-bearing messages retain distinct semantic colors. */
[data-testid="stAlert"] {
    border-radius: var(--brl-radius-sm);
    border: 1px solid rgba(255, 255, 255, 0.82);
    background: transparent !important;
    box-shadow: 0 6px 20px rgba(75, 68, 56, 0.045);
}
[data-testid="stAlertContainer"] {
    border-radius: inherit;
    border: 1px solid rgba(255, 255, 255, 0.56);
    background: rgba(247, 253, 252, 0.68);
    backdrop-filter: blur(var(--brl-glass-blur)) saturate(180%);
    -webkit-backdrop-filter: blur(var(--brl-glass-blur)) saturate(180%);
}
[data-testid="stAlertContentSuccess"] { color: var(--brl-pass) !important; background: var(--brl-pass-bg) !important; }
[data-testid="stAlertContentError"] { color: var(--brl-fail) !important; background: var(--brl-fail-bg) !important; }
[data-testid="stAlertContentWarning"] { color: var(--brl-warning) !important; background: var(--brl-warning-bg) !important; }
[data-testid="stAlertContentInfo"] { color: #356C72 !important; background: transparent !important; }
[data-testid="stAlertContentInfo"] p { color: #356C72 !important; }
[data-testid="stAlert"] [data-testid="stMarkdownContainer"] p { color: inherit; }

.finding-card { border-left: 4px solid var(--brl-warning); background: var(--brl-warning-bg); }
.finding-card.severity-critical, .finding-card.severity-high { border-left-color: var(--brl-fail); background: var(--brl-fail-bg); }
.finding-card.severity-medium { border-left-color: var(--brl-warning); background: var(--brl-warning-bg); }
.finding-card.severity-low { border-left-color: #928F86; background: rgba(235, 232, 224, 0.84); }
.finding-card.result-pass { border-left-color: var(--brl-pass); background: var(--brl-pass-bg); }
.badge {
    display: inline-block;
    padding: 0.22rem 0.58rem;
    border: 1px solid transparent;
    border-radius: 999px;
    font-size: 0.7rem;
    font-weight: 620;
    letter-spacing: 0;
}
.badge-fail { color: var(--brl-fail); background: var(--brl-fail-bg); border-color: #E8CACA; }
.badge-pass { color: var(--brl-pass); background: var(--brl-pass-bg); border-color: #C6DFCF; }
.badge-warning { color: var(--brl-warning); background: var(--brl-warning-bg); border-color: #E8D8B9; }
.badge-unlinked { color: #706D66; background: rgba(234, 231, 225, 0.82); border-color: rgba(255,255,255,0.8); }
.badge-review { color: #706D66; background: rgba(235, 232, 224, 0.82); border-color: rgba(255,255,255,0.8); }
.badge-error { color: #8F5E60; background: var(--brl-fail-bg); border-color: rgba(166,110,112,0.22); }

.ai-disclosure, .demo-banner, .quote-box, .evidence-callout, .action-guidance {
    padding: 0.8rem 1rem;
    border: 1px solid rgba(255, 255, 255, 0.78);
    border-radius: var(--brl-radius-sm);
    color: var(--brl-text);
    background: var(--brl-glass-reflection), var(--brl-glass-surface);
    backdrop-filter: blur(var(--brl-glass-blur)) saturate(180%);
    -webkit-backdrop-filter: blur(var(--brl-glass-blur)) saturate(180%);
    font-size: 0.86rem;
}
.ai-disclosure { background: rgba(211, 235, 235, 0.42); border-color: rgba(255,255,255,0.86); color: #365F63; }
.demo-banner { background: rgba(255, 250, 244, 0.42); border-color: rgba(255,255,255,0.88); color: #725644; }
.quote-box { border-left: 3px solid #4A888C; font-family: ui-monospace, SFMono-Regular, Menlo, monospace; }
.evidence-callout { background: rgba(211, 235, 235, 0.42); border-color: rgba(255,255,255,0.86); color: #365F63; }
.action-guidance { background: rgba(220, 241, 229, 0.56); border-color: rgba(255,255,255,0.86); color: #37694F; }
.claim-id-pill { padding: 0.2rem 0.5rem; border-radius: 999px; color: var(--brl-pearl); background: #236B70; font-family: ui-monospace, SFMono-Regular, Menlo, monospace; }

hr { border-color: rgba(255, 255, 255, 0.76) !important; }
code { color: #315B5E; background: rgba(214, 237, 237, 0.7); border-radius: 5px; }

@keyframes brl-arrive {
    from { opacity: 0; transform: translateY(5px); }
    to { opacity: 1; transform: translateY(0); }
}
@media (max-width: 900px) {
    .block-container { padding: 3.25rem 1.15rem 3rem; }
    .brl-page-heading__title { font-size: 1.75rem; }
}
@media (max-width: 600px) {
    .block-container { padding: 3.15rem 0.85rem 2.3rem; }
    .brl-page-heading { margin-bottom: 1.2rem; }
    .brl-page-heading__title { font-size: 1.55rem; }
    [data-testid="stMetric"] { min-height: 5.2rem; padding: 0.8rem; }
}
@media (prefers-reduced-motion: reduce) {
    *, *::before, *::after { animation-duration: 0.01ms !important; transition-duration: 0.01ms !important; }
}
[role="listbox"] {
    border: 1px solid rgba(255, 255, 255, 0.88) !important;
    border-radius: 16px !important;
    background: rgba(241, 251, 250, 0.94) !important;
    backdrop-filter: blur(var(--brl-glass-blur)) saturate(180%);
    -webkit-backdrop-filter: blur(var(--brl-glass-blur)) saturate(180%);
    box-shadow: 0 18px 38px rgba(30, 75, 78, 0.18),
        inset 1px 1px 0 rgba(255, 255, 255, 0.96) !important;
}
[role="option"][aria-selected="true"], [role="option"][data-selected="true"] {
    color: var(--brl-text) !important;
    background: rgba(208, 235, 233, 0.92) !important;
}
@supports (backdrop-filter: url("#brl-liquid-refraction")) {
    [data-testid="stHeader"],
    [data-testid="stSidebarContent"],
    [data-testid="stMetric"],
    [data-testid="stVerticalBlockBorderWrapper"],
    [data-testid="stExpander"],
    [data-testid="stForm"],
    [data-testid="stFileUploader"] section,
    [data-testid="stBaseButton-secondary"],
    [data-baseweb="tab-list"],
    [role="tablist"],
    [data-testid="stDataFrame"],
    [data-testid="stTable"],
    [data-testid="stAlertContainer"],
    .ai-disclosure,
    .demo-banner,
    .quote-box,
    .evidence-callout,
    .action-guidance {
        backdrop-filter: url("#brl-liquid-refraction") blur(var(--brl-glass-blur)) saturate(180%);
        -webkit-backdrop-filter: url("#brl-liquid-refraction") blur(var(--brl-glass-blur)) saturate(180%);
    }
}
</style>
"""


def inject_styles() -> None:
    """Inject the shared visual system into the current Streamlit page."""
    st.markdown(BRAND_CSS, unsafe_allow_html=True)


def ai_disclosure() -> None:
    st.markdown(
        '<div class="ai-disclosure"><strong>Before running AI checks:</strong> NVIDIA NIM is '
        'primary and Google Gemini is the configured fallback. Claim extraction receives the '
        'full extracted draft text and source candidates; each fact-ranking request receives one '
        'validated claim plus eligible approved fact statements and evidence references; tone '
        'analysis receives at most the first 8,000 draft characters plus applicable guidelines '
        'and campaign context. Longer drafts are marked partial. '
        'Uploaded binary files are parsed locally and are not sent to either provider. AI '
        'suggestions are advisory; deterministic rules and human decisions remain authoritative.</div>',
        unsafe_allow_html=True,
    )


def demo_banner() -> None:
    st.markdown(
        '<div class="demo-banner"><strong>Demo workspace</strong> · Simulated brands, products, '
        'facts and role switching for evaluation.</div>',
        unsafe_allow_html=True,
    )


SEVERITY_BADGE = {
    "CRITICAL": '<span class="badge badge-fail">High Priority</span>',
    "HIGH": '<span class="badge badge-fail">Attention Needed</span>',
    "MEDIUM": '<span class="badge badge-warning">Review Recommended</span>',
    "LOW": '<span class="badge badge-review">Low Priority</span>',
}

RESULT_BADGE = {
    "FAIL": '<span class="badge badge-fail">Needs a change</span>',
    "PASS": '<span class="badge badge-pass">Looks good</span>',
    "WARNING": '<span class="badge badge-warning">Check recommended</span>',
    "UNLINKED": '<span class="badge badge-unlinked">No supporting fact linked</span>',
    "REVIEW_REQUIRED": '<span class="badge badge-review">Needs human review</span>',
    "NOT_OBSERVED": '<span class="badge badge-unlinked">Not checked</span>',
    "ERROR": '<span class="badge badge-error">Check could not be completed</span>',
}

EVIDENCE_BADGE = {
    "SUPPORTS": '<span class="badge badge-pass">Supported by evidence</span>',
    "CONTRADICTS": '<span class="badge badge-fail">Conflicts with evidence</span>',
    "RELATED_NOT_EVIDENCE": '<span class="badge badge-unlinked">Related, not proof</span>',
    "NO_MATCH": '<span class="badge badge-unlinked">No evidence linked</span>',
}