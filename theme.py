"""Light + dark styling for CampusPulse.

Usage in app.py:
    from theme import apply_theme
    apply_theme()   # call once, near the top of the script
"""
import streamlit as st
import streamlit.components.v1 as components

FONTS = "@import url('https://fonts.googleapis.com/css2?family=Bricolage+Grotesque:opsz,wght@12..96,600;12..96,700&family=Figtree:wght@400;500;600;700&display=swap');"

LIGHT = {
    "color-scheme": "light",
    "ink": "#1D2340", "muted": "#5B627D", "surface": "#FFFFFF", "sidebar": "#FFFFFF",
    "line": "#DCE1F0", "skim-line": "#B9C0DA", "grey-soft": "#ECEEF5", "past": "#8087A0",
    "blue": "#2F46E0", "blue-soft": "#E7EBFF", "blue-ink": "#2336B8", "drop": "#9AA6F5",
    "info-ink": "#1E2D9E", "info-line": "#C3CBFA",
    "hl": "#FFE66D", "on-hl": "#1D2340", "mark-fine": "#FF9EC0",
    "pink": "#E5366B", "pink-soft": "#FFE4EE", "pink-line": "#FFB3CC", "pink-ink": "#4A1226",
    "hot-bg": "#FFD0E1", "hot-ink": "#8E0E3B",
    "amber": "#7A4E00", "amber-soft": "#FFF0D0", "amber-line": "#F3D48A", "amber-ink": "#5E3C00",
    "mint": "#0C6B43", "mint-soft": "#DAF5E6", "mint-line": "#A9E3C4", "mint-ink": "#0A5636",
}

DARK = {
    "color-scheme": "dark",
    "ink": "#E8EBF7", "muted": "#A3AAC4", "surface": "#171B28", "sidebar": "#131723",
    "line": "#2E3555", "skim-line": "#3E4670", "grey-soft": "#222739", "past": "#555C78",
    "blue": "#4F63F0", "blue-soft": "#1B2250", "blue-ink": "#B3BEFF", "drop": "#4C5AB8",
    "info-ink": "#B3BEFF", "info-line": "#2F3B86",
    "hl": "#FFE66D", "on-hl": "#1D2340", "mark-fine": "#FF9EC0",
    "pink": "#E5366B", "pink-soft": "#35132A", "pink-line": "#7A2A48", "pink-ink": "#FFD6E4",
    "hot-bg": "#5A1830", "hot-ink": "#FFC2D6",
    "amber": "#F5C76A", "amber-soft": "#33280F", "amber-line": "#6B5217", "amber-ink": "#F7D894",
    "mint": "#7FE0B0", "mint-soft": "#0F2F1F", "mint-line": "#1F5A3B", "mint-ink": "#A8EBC9",
}


def _root(p: dict, selector: str = ":root") -> str:
    return selector + "{" + ";".join(f"--{k}:{v}" for k, v in p.items()) + "}"


def _vars_css() -> str:
    # Light is the default. Dark applies when the detector script below tags <html data-cp-theme="dark">.
    # If the script has not run yet (or is blocked), fall back to the OS preference.
    return (
        _root(LIGHT)
        + _root(DARK, ':root[data-cp-theme="dark"]')
        + "@media (prefers-color-scheme: dark){"
        + _root(DARK, ":root:not([data-cp-theme])")
        + "}"
    )


# Reads the background colour Streamlit is actually painting and tags <html> with light/dark.
# This tracks the in-app theme switch (Settings > Theme) as well as the OS setting.
DETECTOR_JS = r"""
<script>
(function () {
  try {
    var P = window.parent, D = P.document, root = D.documentElement;
    function lum(c) {
      var m = (c || "").match(/[\d.]+/g);
      if (!m || m.length < 3) return null;
      if (m.length > 3 && parseFloat(m[3]) === 0) return null;   // transparent
      return (0.299 * m[0] + 0.587 * m[1] + 0.114 * m[2]) / 255;
    }
    function apply() {
      var els = [D.querySelector(".stApp"), D.querySelector('[data-testid="stApp"]'), D.body];
      var l = null;
      for (var i = 0; i < els.length && l === null; i++) {
        if (els[i]) l = lum(P.getComputedStyle(els[i]).backgroundColor);
      }
      if (l === null) return;
      var mode = l < 0.5 ? "dark" : "light";
      if (root.getAttribute("data-cp-theme") !== mode) root.setAttribute("data-cp-theme", mode);
    }
    apply();
    setInterval(apply, 400);
  } catch (e) {}
})();
</script>
"""

HIDE_IFRAME_CSS = (
    ".stElementContainer:has(> iframe[height=\"0\"]),"
    ".element-container:has(> iframe[height=\"0\"])"
    "{position:absolute;height:0;width:0;overflow:hidden;margin:0;padding:0}"
)


BASE = """
.stApp{font-family:'Figtree',system-ui,-apple-system,'Segoe UI',sans-serif;color:var(--ink)}
.stApp h1,.stApp h2,.stApp h3,.stApp h4{font-family:'Bricolage Grotesque','Figtree',system-ui,sans-serif;letter-spacing:-0.01em;color:var(--ink)}
.stApp button,.stApp textarea,.stApp input{font-family:inherit}
footer{visibility:hidden}
.block-container{max-width:1080px;margin-left:auto;margin-right:auto;padding-top:2.2rem;padding-bottom:4rem}
section[data-testid="stSidebar"]{background:var(--sidebar);border-right:1px solid var(--line)}
section[data-testid="stSidebar"] .block-container{padding-top:1.5rem}

/* inputs */
.stTextArea [data-baseweb="textarea"]{border:1px solid var(--line);border-radius:12px;background:var(--surface)}
.stTextArea textarea{color:var(--ink)}
.stSelectbox [data-baseweb="select"]>div{border:1px solid var(--line);border-radius:10px;background:var(--surface);color:var(--ink)}
[data-testid="stFileUploaderDropzone"]{background:var(--blue-soft);border:2px dashed var(--drop);border-radius:14px}
.stButton>button,.stDownloadButton>button{border-radius:10px;font-weight:600;padding:.55rem 1rem}
.stButton>button[data-testid="stBaseButton-secondary"],.stDownloadButton>button{border:1.5px solid var(--line);background:var(--surface);color:var(--ink)}
.stButton>button[data-testid="stBaseButton-secondary"]:hover,.stDownloadButton>button:hover{border-color:var(--blue-ink);color:var(--blue-ink)}
.stButton>button:focus-visible,.stDownloadButton>button:focus-visible{outline:3px solid var(--hl);outline-offset:2px}

/* tabs */
.stTabs [data-baseweb="tab-list"]{gap:.25rem;border-bottom:1px solid var(--line)}
.stTabs [data-baseweb="tab"]{font-weight:600;padding:.6rem 1rem}

/* landing */
.stApp .hero h1{font-size:2.7rem;line-height:1.08;margin:0 0 .7rem;padding:0;font-weight:700}
.hero p{font-size:1.1rem;color:var(--muted);max-width:58ch;margin:0 0 1.4rem;line-height:1.55}
.feats{display:flex;flex-wrap:wrap;gap:1.2rem 2.4rem;margin:2.2rem 0 0}
.feats div{flex:1 1 220px;max-width:310px;color:var(--muted);font-size:.95rem;line-height:1.5}
.feats b{display:block;color:var(--ink);font-size:1rem;margin-bottom:.15rem}

/* sidebar */
.brand{font-family:'Bricolage Grotesque','Figtree',sans-serif;font-weight:700;font-size:1.45rem;color:var(--ink)}
.tag{color:var(--muted);font-size:.88rem;margin:.1rem 0 1rem;line-height:1.4}
.side-h{font-weight:700;margin:1.1rem 0 .4rem;color:var(--ink)}
.up{display:flex;gap:.6rem;align-items:center;margin:.4rem 0;font-size:.88rem;line-height:1.3;color:var(--ink)}

/* notice header */
.stApp .nh h2{font-size:1.9rem;line-height:1.15;margin:0 0 .4rem;padding:0;font-weight:700}
.nh p{font-size:1.08rem;color:var(--muted);margin:0 0 .8rem;max-width:70ch;line-height:1.5}
.who{margin:.2rem 0 .6rem;font-size:.92rem;color:var(--muted)}
.who b{color:var(--ink);margin-right:.4rem}
.chip{display:inline-block;padding:.15rem .65rem;border-radius:999px;background:var(--blue-soft);color:var(--blue-ink);font-weight:600;font-size:.85rem;margin:0 .35rem .3rem 0}
.chip.plain{background:var(--grey-soft);color:var(--muted)}

/* notes */
.note{border-radius:12px;padding:.75rem 1rem;margin:.55rem 0;line-height:1.5;font-size:.95rem}
.note.warn{background:var(--amber-soft);color:var(--amber-ink);border:1px solid var(--amber-line)}
.note.ok{background:var(--mint-soft);color:var(--mint-ink);border:1px solid var(--mint-line)}
.note.info{background:var(--blue-soft);color:var(--info-ink);border:1px solid var(--info-line)}

/* fine-print alert */
.fineprint-alert{border-radius:14px;background:var(--pink-soft);border:1.5px solid var(--pink-line);border-left:8px solid var(--pink);padding:1rem 1.2rem;margin:1rem 0 1.2rem;color:var(--pink-ink);line-height:1.5}
.stApp .fineprint-alert h3{margin:0 0 .3rem;padding:0;font-size:1.25rem;color:var(--pink-ink)}
.fineprint-alert ul{margin:.5rem 0 0;padding-left:1.1rem}
.fineprint-alert li{margin:.25rem 0}
.hl{background-image:linear-gradient(var(--hl),var(--hl));background-repeat:no-repeat;background-size:0 100%;animation:swipe .8s .25s ease-out forwards;padding:0 .25em;border-radius:3px;font-weight:700;color:var(--on-hl);-webkit-box-decoration-break:clone;box-decoration-break:clone}
@keyframes swipe{to{background-size:100% 100%}}
@media (prefers-reduced-motion:reduce){.hl{animation:none;background-size:100% 100%}}

/* deadline cards */
.dl{display:flex;gap:1rem;align-items:flex-start;background:var(--surface);border:1px solid var(--line);border-radius:14px;padding:.9rem 1rem;margin:0 0 .7rem;color:var(--ink)}
.dl.fine{background:var(--pink-soft);border-color:var(--pink-line)}
.dl.past{opacity:.7}
.tile{flex:0 0 58px;text-align:center;border-radius:10px;overflow:hidden;border:1px solid var(--line);background:var(--surface);color:var(--ink)}
.tile .m{background:var(--blue);color:#fff;font-size:.78rem;font-weight:700;padding:.15rem 0}
.tile .d{font-family:'Bricolage Grotesque','Figtree',sans-serif;font-weight:700;font-size:1.6rem;line-height:1.2;padding:.1rem 0 .2rem}
.dl.hot .tile .m{background:var(--pink)}
.dl.soon .tile .m{background:#D98200}
.dl.past .tile .m,.dl.unknown .tile .m{background:var(--past)}
.dl-body{flex:1;min-width:0}
.dl-title{font-weight:700;font-size:1.05rem;line-height:1.35}
.dl-when{color:var(--muted);font-size:.9rem;margin:.1rem 0 .3rem}
.quote{border-left:3px solid var(--line);padding:.1rem 0 .1rem .7rem;color:var(--muted);font-size:.9rem;font-style:italic;margin:.3rem 0 .55rem;overflow-wrap:anywhere}
.dl.fine .quote{border-left-color:var(--pink-line)}
.pills{display:flex;flex-wrap:wrap;gap:.4rem}
.pill{display:inline-block;border-radius:999px;padding:.15rem .6rem;font-size:.8rem;font-weight:600;white-space:nowrap}
.pill.hot{background:var(--hot-bg);color:var(--hot-ink)}
.pill.soon{background:var(--amber-soft);color:var(--amber)}
.pill.later{background:var(--blue-soft);color:var(--blue-ink)}
.pill.past,.pill.unknown{background:var(--grey-soft);color:var(--muted)}
.pill.ok{background:var(--mint-soft);color:var(--mint)}
.pill.warn{background:var(--amber-soft);color:var(--amber)}
.pill.fine{background:var(--pink);color:#fff}

/* chat proof */
.proof{border-radius:10px;padding:.6rem .8rem;margin-top:.5rem;font-size:.9rem;line-height:1.45}
.proof i{display:block;margin-top:.2rem;overflow-wrap:anywhere}
.proof.ok{background:var(--mint-soft);color:var(--mint-ink)}
.proof.bad{background:var(--amber-soft);color:var(--amber-ink)}
.proof.none{background:var(--grey-soft);color:var(--muted)}

/* source view */
.legend{display:flex;gap:1.2rem;flex-wrap:wrap;font-size:.88rem;color:var(--muted);margin:.2rem 0 .7rem}
.legend mark{padding:.05em .5em;border-radius:3px;margin-right:.35rem}
.paper{background:var(--surface);color:var(--ink);border:1px solid var(--line);border-radius:12px;padding:1.2rem 1.4rem;line-height:1.75;max-height:560px;overflow:auto;white-space:pre-wrap;overflow-wrap:anywhere;font-size:.95rem}
mark.body{background:var(--hl);color:var(--on-hl)}
mark.fine{background:var(--mark-fine);color:var(--on-hl)}
.paper mark{padding:.05em .15em;border-radius:3px}
.skim{background:var(--surface);border:1px dashed var(--skim-line);border-radius:12px;padding:.8rem 1rem;color:var(--muted);font-size:.92rem;white-space:pre-wrap;overflow-wrap:anywhere;line-height:1.6}

/* calendar */
.cal{background:var(--surface);border:1px solid var(--line);border-radius:14px;padding:.8rem .9rem;color:var(--ink)}
.cal .mo{font-family:'Bricolage Grotesque','Figtree',sans-serif;font-weight:700;font-size:1.05rem;margin-bottom:.4rem}
.cal table{width:100%;border-collapse:collapse;text-align:center;font-size:.85rem}
.cal th{color:var(--muted);font-weight:600;padding:.2rem 0}
.cal td{padding:.15rem 0;height:2rem;border:none}
.cal .ev{display:inline-block;min-width:1.7rem;line-height:1.7rem;border-radius:8px;font-weight:700;color:#fff;background:var(--blue)}
.cal .ev.fine{background:var(--pink)}
.cal .ev.now{outline:2px solid var(--ink);outline-offset:1px}
.cal .today{display:inline-block;min-width:1.7rem;line-height:1.5rem;border:2px solid var(--ink);border-radius:50%;font-weight:700}
.cal-legend{font-size:.85rem;color:var(--muted);margin:.6rem 0 0}
"""


def get_css() -> str:
    return "<style>" + FONTS + _vars_css() + HIDE_IFRAME_CSS + BASE + "</style>"


def apply_theme() -> None:
    """Inject the stylesheet and the light/dark detector. Call once per run."""
    st.markdown(get_css(), unsafe_allow_html=True)
    components.html(DETECTOR_JS, height=0)