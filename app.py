"""Tree crown annotation tool. One 640 px tile (6 px per metre) at a time, with the detector's draft circles pre-filled.
Click on the picture to add a tree (Add mode) or remove the nearest circle (Delete mode). Save & next stores the labels.

Labels are written as labels/<tile id>.json on the `labels` branch of the GitHub repo named in the Streamlit secrets (so the app is not redeployed on every save).
Without secrets (local run) they go to ./labels_local/ instead.
"""
import json, io, time, base64, datetime as dt
from pathlib import Path
import requests, streamlit as st
from PIL import Image, ImageDraw
from streamlit_image_coordinates import streamlit_image_coordinates

HERE = Path(__file__).parent; TILES = HERE / "data" / "tiles"; LOCAL = HERE / "labels_local"
st.set_page_config(page_title="Tree annotation", layout="wide")

# ----------------------------------------------------------------------------- storage
class Store:
    """GitHub-backed when secrets are present, local folder otherwise."""
    def __init__(self):
        sec = st.secrets if hasattr(st, "secrets") else {}
        try: self.token, self.repo = sec["GITHUB_TOKEN"], sec["GITHUB_REPO"]
        except Exception: self.token = self.repo = None
        self.branch = "labels"; self.api = f"https://api.github.com/repos/{self.repo}" if self.repo else None
        self.h = {"Authorization": f"Bearer {self.token}", "Accept": "application/vnd.github+json"} if self.token else {}
        self.shas = {}
    @property
    def remote(self): return bool(self.token)
    def ensure_branch(self):
        r = requests.get(f"{self.api}/git/ref/heads/{self.branch}", headers=self.h, timeout=30)
        if r.status_code == 200: return
        main = None
        for name in ("main", "master"):
            m = requests.get(f"{self.api}/git/ref/heads/{name}", headers=self.h, timeout=30)
            if m.status_code == 200: main = m.json()["object"]["sha"]; break
        if main is None: raise RuntimeError("repository has no main branch")
        requests.post(f"{self.api}/git/refs", headers=self.h, json={"ref": f"refs/heads/{self.branch}", "sha": main}, timeout=30).raise_for_status()
    def list_done(self):
        """tile id -> status for every saved label (one API call for the whole branch)."""
        if not self.remote:
            LOCAL.mkdir(exist_ok=True); return {p.stem: json.loads(p.read_text()).get("status", "done") for p in LOCAL.glob("*.json")}
        self.ensure_branch()
        r = requests.get(f"{self.api}/git/trees/{self.branch}?recursive=1", headers=self.h, timeout=60); r.raise_for_status()
        self.shas = {t["path"]: t["sha"] for t in r.json().get("tree", []) if t["path"].startswith("labels/") and t["path"].endswith(".json")}
        return {Path(p).stem: "done" for p in self.shas}                         # status refined when a tile is opened
    def load(self, tid):
        if not self.remote:
            p = LOCAL / f"{tid}.json"; return json.loads(p.read_text()) if p.exists() else None
        path = f"labels/{tid}.json"
        if path not in self.shas: return None
        r = requests.get(f"{self.api}/contents/{path}", params={"ref": self.branch}, headers=self.h, timeout=30)
        if r.status_code != 200: return None
        return json.loads(base64.b64decode(r.json()["content"]))
    def save(self, tid, obj):
        txt = json.dumps(obj, separators=(",", ":"))
        if not self.remote:
            LOCAL.mkdir(exist_ok=True); (LOCAL / f"{tid}.json").write_text(txt); return
        path = f"labels/{tid}.json"; body = {"message": f"label {tid}", "content": base64.b64encode(txt.encode()).decode(), "branch": self.branch}
        if path in self.shas: body["sha"] = self.shas[path]
        r = requests.put(f"{self.api}/contents/{path}", headers=self.h, json=body, timeout=60)
        if r.status_code == 409 or r.status_code == 422:                          # stale sha: fetch the current one and retry once
            g = requests.get(f"{self.api}/contents/{path}", params={"ref": self.branch}, headers=self.h, timeout=30)
            if g.status_code == 200: body["sha"] = g.json()["sha"]; r = requests.put(f"{self.api}/contents/{path}", headers=self.h, json=body, timeout=60)
        r.raise_for_status(); self.shas[path] = r.json()["content"]["sha"]

@st.cache_resource
def get_store(): return Store()
store = get_store()

@st.cache_data
def manifest(): return json.loads((HERE / "data" / "manifest.json").read_text())
M = manifest(); BY = {t["id"]: t for t in M}
SRC_LABEL = {"All imagery": None, "Google Earth screenshots": "", "WorldView Legion, Jan 2026": "legion", "WorldView-2, Dec 2023": "wv2"}   # manifest "src": absent for Google Earth tiles
def ids_for(choice):
    want = SRC_LABEL.get(choice)
    return [t["id"] for t in M] if want is None else [t["id"] for t in M if t.get("src", "") == want]

# ----------------------------------------------------------------------------- state
S = st.session_state
if "done" not in S:
    try: S.done = store.list_done()
    except Exception as e: S.done = {}; st.error(f"Could not read saved labels: {e}")
S.setdefault("src_choice", "All imagery"); IDS = ids_for(S.src_choice) or ids_for("All imagery")
S.setdefault("idx", 0); S.setdefault("crowns", {}); S.setdefault("undo", []); S.setdefault("last_click", None); S.setdefault("last_ts", None); S.setdefault("t0", time.time()); S.setdefault("opened", None)

def goto(i): S.idx = max(0, min(len(IDS) - 1, i)); S.last_click = None; S.undo = []
def next_open(start):
    for k in range(start, len(IDS)):
        if IDS[k] not in S.done: return k
    return len(IDS) - 1

with st.sidebar:
    st.title("Tree annotation")
    name = st.text_input("Your name", value=S.get("name", ""), placeholder="used in the saved labels"); S.name = name
    def _new_source(): S.idx = 0; S.last_click = None; S.undo = []
    st.selectbox("Imagery", list(SRC_LABEL), key="src_choice", on_change=_new_source, help="Google Earth screenshots, or the block 01 farms cut from the WorldView Legion (Jan 2026) and WorldView-2 (Dec 2023) images")
    IDS = ids_for(S.src_choice) or ids_for("All imagery")
    n_done = sum(1 for t in IDS if t in S.done); st.progress(n_done / len(IDS), text=f"{n_done} of {len(IDS)} tiles done")
    mode = st.radio("Click does", ["Add a tree", "Delete a tree"], horizontal=True)
    rad = st.slider("Circle radius for new trees (px)", 3, 20, 7, help="6 px = 1 m on the ground")
    zoom = st.select_slider("Picture size", options=[640, 800, 960, 1120], value=800)
    st.markdown("---")
    show_raw = st.checkbox("Show the raw picture above the editor", value=True, help="The same tile without any circles, to compare with before you edit")
    only_open = st.checkbox("Jump over tiles already done", value=True)
    j = st.text_input("Go to tile id", placeholder="e.g. 2073_1_1")
    if j and j in BY and j in IDS: goto(IDS.index(j))
    if st.button("Go to first tile not done", use_container_width=True): goto(next_open(0))
    st.caption(f"Labels are saved to {'the GitHub repo (branch labels)' if store.remote else 'the local folder labels_local/ (no GitHub secrets found)'}.")

S.idx = min(S.idx, len(IDS) - 1); tid = IDS[S.idx]; meta = BY[tid]
if S.opened != tid:                                       # entering a tile: load a saved label if there is one, else the draft
    saved = store.load(tid) if tid in S.done else None
    S.crowns[tid] = [list(c) for c in (saved["crowns"] if saved else meta["prefill"])]; S.opened = tid; S.t0 = time.time(); S.undo = []; S.last_click = None
crowns = S.crowns[tid]

@st.fragment
def editor():
    tid = IDS[S.idx]; meta = BY[tid]; crowns = S.crowns[tid]
    raw = Image.open(TILES / f"{tid}.jpg").convert("RGB")
    if show_raw: st.image(raw, width=zoom, caption=f"Raw picture, no circles ({meta.get('source', 'Google Earth screenshot')}). Your editable copy is below.")
    c1, c2 = st.columns([5, 2])
    with c1:
        img = raw.copy(); d = ImageDraw.Draw(img)
        for x, y, r in crowns: d.ellipse([x - r, y - r, x + r, y + r], outline=(255, 214, 0), width=2)
        click = streamlit_image_coordinates(img, width=zoom, key=f"img-{tid}")
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
        st.caption("Image: " + meta.get("source", "Google Earth screenshot"))
        st.caption("Yellow circles are the trees. Everything shaded dark is outside the farm outline: do not mark it.")
        b = st.columns(3)
        if b[0].button("Undo", use_container_width=True, disabled=not S.undo): crowns[:] = S.undo.pop(); st.rerun(scope="fragment")
        if b[1].button("Reset to draft", use_container_width=True): S.undo.append([list(c) for c in crowns]); crowns[:] = [list(c) for c in meta["prefill"]]; st.rerun(scope="fragment")
        if b[2].button("Clear all", use_container_width=True): S.undo.append([list(c) for c in crowns]); crowns[:] = []; st.rerun(scope="fragment")
        def save(status):
            obj = {"tile": tid, "farm": meta["farm"], "x0": meta["x0"], "y0": meta["y0"], "crowns": [[round(a, 1), round(b_, 1), round(r, 1)] for a, b_, r in crowns], "n_draft": len(meta["prefill"]),
                   "src": meta.get("src", ""), "status": status, "annotator": S.name or "unknown", "seconds": round(time.time() - S.t0), "saved": dt.datetime.utcnow().isoformat(timespec="seconds") + "Z"}
            try: store.save(tid, obj); S.done[tid] = status; return True
            except Exception as e: st.error(f"Save failed, nothing was lost: {e}"); return False
        s = st.columns(2)
        if s[0].button("Save and next", type="primary", use_container_width=True):
            if save("no_trees" if not crowns else "done"): goto(next_open(S.idx + 1) if only_open else S.idx + 1); st.rerun(scope="app")
        if s[1].button("Skip (unclear picture)", use_container_width=True):
            if save("skipped"): goto(next_open(S.idx + 1) if only_open else S.idx + 1); st.rerun(scope="app")
        n = st.columns(2)
        if n[0].button("Previous", use_container_width=True): goto(S.idx - 1); st.rerun(scope="app")
        if n[1].button("Next", use_container_width=True): goto(S.idx + 1); st.rerun(scope="app")
        if tid in S.done: st.success(f"Already saved as {S.done[tid]}. Saving again overwrites it.")

editor()
