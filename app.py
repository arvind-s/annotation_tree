"""Tree crown annotation tool. One 640 px tile (6 px per metre) at a time, with the detector's draft circles pre-filled.
Click on the picture to add a tree (Add mode) or remove the nearest circle (Delete mode). Save & next stores the labels; Skip needs a remark.

Labels are written as labels/<tile id>.json (skipped tiles: labels/<tile id>.skipped.json) on the `labels` branch of the GitHub repo named in the Streamlit secrets
(so the app is not redeployed on every save). Without secrets (local run) they go to ./labels_local/ instead. The "Status table" page lists every tile with its status and remarks.
"""
import time, datetime as dt
from pathlib import Path
import streamlit as st
from PIL import Image, ImageDraw
from streamlit_image_coordinates import streamlit_image_coordinates
from store import get_store, manifest, ids_for, bucket, SRC_LABEL, STATUS_CHOICES, source_of

HERE = Path(__file__).parent; TILES = HERE / "data" / "tiles"
st.set_page_config(page_title="Tree annotation", layout="wide")
store = get_store(); M = manifest(); BY = {t["id"]: t for t in M}

# ----------------------------------------------------------------------------- state
S = st.session_state
if "done" not in S:
    try: S.done = store.list_done()
    except Exception as e: S.done = {}; st.error(f"Could not read saved labels: {e}")
S.setdefault("src_choice", "All imagery"); S.setdefault("status_choice", "All tiles")
S.setdefault("idx", 0); S.setdefault("crowns", {}); S.setdefault("undo", []); S.setdefault("last_click", None); S.setdefault("last_ts", None); S.setdefault("t0", time.time()); S.setdefault("opened", None)

def visible():
    base = ids_for(M, S.src_choice)
    return base if S.status_choice == "All tiles" else [t for t in base if bucket(S.done, t) == S.status_choice.lower()]
IDS = visible()

def goto(i): S.idx = max(0, min(len(IDS) - 1, i)); S.last_click = None; S.undo = []
def next_open(start):
    for k in range(start, len(IDS)):
        if IDS[k] not in S.done: return k
    return len(IDS) - 1
def advance(tid, only_open):
    """After a save: move to the next tile of the current view. The saved tile may have left the view (Pending / Skipped lists), so look it up by id."""
    old = IDS; pos = old.index(tid) if tid in old else 0; new = visible(); S.last_click = None; S.undo = []
    if not new: S.idx = 0; return
    skip_done = only_open and S.status_choice in ("All tiles", "Pending")
    cand = [t for t in old[pos + 1:] if t in new and not (skip_done and t in S.done)] or [t for t in new if not (skip_done and t in S.done)]
    nxt = cand[0] if cand else next((t for t in old[pos + 1:] if t in new), new[0])
    S.idx = new.index(nxt)

with st.sidebar:
    st.title("Tree annotation")
    name = st.text_input("Your name", value=S.get("name", ""), placeholder="used in the saved labels"); S.name = name
    def _new_view(): S.idx = 0; S.last_click = None; S.undo = []
    st.selectbox("Imagery", list(SRC_LABEL), key="src_choice", on_change=_new_view, help="Google Earth screenshots, or the block 01 farms cut from the WorldView Legion (Jan 2026) and WorldView-2 (Dec 2023) images")
    base = ids_for(M, S.src_choice); cnt = {k: sum(1 for t in base if bucket(S.done, t) == k) for k in ("pending", "done", "skipped")}
    st.progress((cnt["done"] + cnt["skipped"]) / max(1, len(base)), text=f"{cnt['done'] + cnt['skipped']} of {len(base)} tiles reviewed")
    m = st.columns(3); m[0].metric("Pending", cnt["pending"]); m[1].metric("Done", cnt["done"]); m[2].metric("Skipped", cnt["skipped"])
    st.selectbox("Show tiles that are", STATUS_CHOICES, key="status_choice", on_change=_new_view, format_func=lambda v: v if v == "All tiles" else f"{v} ({cnt[v.lower()]})")
    mode = st.radio("Click does", ["Add a tree", "Delete a tree"], horizontal=True)
    rad = st.slider("Circle radius for new trees (px)", 3, 20, 7, help="6 px = 1 m on the ground")
    zoom = st.select_slider("Picture size", options=[640, 800, 960, 1120], value=800)
    st.markdown("---")
    only_open = st.checkbox("Jump over tiles already done", value=True)
    j = st.text_input("Go to tile id", placeholder="e.g. 2073_1_1")
    if j and j in BY and j in IDS: goto(IDS.index(j))
    elif j and j in BY: st.caption("That tile is not in the current view; change Imagery or the status list above.")
    if st.button("Go to first pending tile", use_container_width=True) and IDS: goto(next_open(0))
    st.caption(f"Labels are saved to {'the GitHub repo (branch labels)' if store.remote else 'the local folder labels_local/ (no GitHub secrets found)'}. The Status table page (menu above) lists every tile with its remarks.")

if not IDS:
    st.info(f"No tiles in this view ({S.src_choice}, {S.status_choice.lower()}). Change the lists in the sidebar.")
    st.stop()
S.idx = min(S.idx, len(IDS) - 1); tid = IDS[S.idx]; meta = BY[tid]
if S.opened != tid:                                       # entering a tile: load a saved label if there is one, else the draft
    saved = store.load(tid) if tid in S.done else None
    if saved: S.done[tid] = saved.get("status", S.done[tid])
    S.crowns[tid] = [list(c) for c in (saved["crowns"] if saved else meta["prefill"])]; S.opened = tid; S.t0 = time.time(); S.undo = []; S.last_click = None
    S[f"remarks-{tid}"] = (saved or {}).get("remarks", "")
crowns = S.crowns[tid]

@st.fragment
def editor():
    tid = IDS[S.idx]; meta = BY[tid]; crowns = S.crowns[tid]
    raw = Image.open(TILES / f"{tid}.jpg").convert("RGB")
    show_c = st.toggle("Show circles", value=True, key="show_circles", help="Turn off to see the picture clean. Clicks do nothing while the circles are hidden, so nothing is added or removed by accident.")
    c1, c2 = st.columns([5, 2])
    with c1:
        img = raw.copy(); d = ImageDraw.Draw(img)
        if show_c:
            for x, y, r in crowns: d.ellipse([x - r, y - r, x + r, y + r], outline=(255, 214, 0), width=2)
            click = streamlit_image_coordinates(img, width=zoom, key=f"img-{tid}")
        else:
            click = None; st.image(img, width=zoom, caption="Circles hidden. Turn them back on to edit; your circles are kept.")
        if click and click.get("unix_time") != S.last_ts:
            S.last_ts = click.get("unix_time"); sc = 640 / click["width"]; x, y = click["x"] * sc, click["y"] * sc
            S.undo.append([list(c) for c in crowns])
            if mode == "Add a tree": crowns.append([round(x, 1), round(y, 1), float(rad)])
            elif crowns:
                k = min(range(len(crowns)), key=lambda i: (crowns[i][0] - x) ** 2 + (crowns[i][1] - y) ** 2)
                if ((crowns[k][0] - x) ** 2 + (crowns[k][1] - y) ** 2) ** .5 <= max(14, crowns[k][2] * 2): crowns.pop(k)
            st.rerun(scope="fragment")
    with c2:
        st.subheader(f"Tile {tid}")
        st.write(f"**{len(crowns)} trees now** · detector draft {len(meta['prefill'])} · farm {meta['farm']} · tile {S.idx + 1} of {len(IDS)}")
        st.caption("Image: " + source_of(meta))
        st.caption("Yellow circles are the trees. Everything shaded dark is outside the farm outline: do not mark it.")
        b = st.columns(3)
        if b[0].button("Undo", use_container_width=True, disabled=not S.undo): crowns[:] = S.undo.pop(); st.rerun(scope="fragment")
        if b[1].button("Reset to draft", use_container_width=True): S.undo.append([list(c) for c in crowns]); crowns[:] = [list(c) for c in meta["prefill"]]; st.rerun(scope="fragment")
        if b[2].button("Clear all", use_container_width=True): S.undo.append([list(c) for c in crowns]); crowns[:] = []; st.rerun(scope="fragment")
        remarks = st.text_area("Remarks", key=f"remarks-{tid}", height=80, placeholder="Required when you skip: say what is unclear (cloud, no farm visible, outline looks wrong...). Optional otherwise.")
        def save(status):
            obj = {"tile": tid, "farm": meta["farm"], "x0": meta["x0"], "y0": meta["y0"], "crowns": [[round(a, 1), round(b_, 1), round(r, 1)] for a, b_, r in crowns], "n_draft": len(meta["prefill"]),
                   "src": meta.get("src", ""), "status": status, "remarks": remarks.strip(), "annotator": S.name or "unknown", "seconds": round(time.time() - S.t0), "saved": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")}
            try: store.save(tid, obj); S.done[tid] = status; return True
            except Exception as e: st.error(f"Save failed, nothing was lost: {e}"); return False
        s = st.columns(2)
        if s[0].button("Save and next", type="primary", use_container_width=True):
            if save("no_trees" if not crowns else "done"): advance(tid, only_open); st.rerun(scope="app")
        if s[1].button("Skip (unclear picture)", use_container_width=True):
            if not remarks.strip(): st.error("Write a remark before skipping, so the next person knows why.")
            elif save("skipped"): advance(tid, only_open); st.rerun(scope="app")
        n = st.columns(2)
        if n[0].button("Previous", use_container_width=True): goto(S.idx - 1); st.rerun(scope="app")
        if n[1].button("Next", use_container_width=True): goto(S.idx + 1); st.rerun(scope="app")
        if tid in S.done: st.success(f"Already saved as {S.done[tid]}. Saving again overwrites it.")

editor()
