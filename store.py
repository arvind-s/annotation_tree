"""Storage and manifest shared by the annotation app (app.py) and the status page (pages/1_Status_table.py).

Labels live on the `labels` branch of the GitHub repo named in the Streamlit secrets, one small file per tile:
  labels/<tile id>.json            tile saved as done or no_trees
  labels/<tile id>.skipped.json    tile skipped (the file holds the remarks)
The status of every tile can therefore be read from one directory listing. Saving a tile removes the file of the other kind.
Without secrets (local run) the same file names are used in ./labels_local/.
"""
import json, base64
from pathlib import Path
import requests, streamlit as st

HERE = Path(__file__).parent; LOCAL = HERE / "labels_local"
SKIP = ".skipped.json"

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
    @staticmethod
    def _split(name):
        """file name -> (tile id, 'skipped' | 'done')"""
        return (name[:-len(SKIP)], "skipped") if name.endswith(SKIP) else (name[:-5], "done")
    def list_done(self):
        """tile id -> status for every saved label (one API call for the whole branch). A tile with both kinds of file counts as done."""
        out = {}
        if not self.remote:
            LOCAL.mkdir(exist_ok=True)
            for p in sorted(LOCAL.glob("*.json")):
                tid, kind = self._split(p.name)
                try: st_ = json.loads(p.read_text()).get("status", kind)
                except Exception: st_ = kind
                if kind == "done" or tid not in out: out[tid] = st_
            return out
        self.ensure_branch()
        r = requests.get(f"{self.api}/git/trees/{self.branch}?recursive=1", headers=self.h, timeout=60); r.raise_for_status()
        self.shas = {t["path"]: t["sha"] for t in r.json().get("tree", []) if t["path"].startswith("labels/") and t["path"].endswith(".json")}
        for p in self.shas:
            tid, kind = self._split(Path(p).name)
            if kind == "done" or tid not in out: out[tid] = kind                 # no_trees shows as done until the tile is opened
        return out
    def skipped(self):
        """[(tile id, path, sha)] of skipped tiles, from the last list_done()"""
        if not self.remote: return [(p.name[:-len(SKIP)], str(p), "") for p in sorted(LOCAL.glob("*" + SKIP))]
        return [(Path(p).name[:-len(SKIP)], p, s) for p, s in sorted(self.shas.items()) if p.endswith(SKIP)]
    def read(self, path):
        if not self.remote: return json.loads(Path(path).read_text())
        r = requests.get(f"{self.api}/contents/{path}", params={"ref": self.branch}, headers=self.h, timeout=30)
        return json.loads(base64.b64decode(r.json()["content"])) if r.status_code == 200 else None
    def load(self, tid):
        for suffix in (".json", SKIP):
            if not self.remote:
                p = LOCAL / f"{tid}{suffix}"
                if p.exists(): return json.loads(p.read_text())
            elif f"labels/{tid}{suffix}" in self.shas: return self.read(f"labels/{tid}{suffix}")
        return None
    def _put(self, path, txt, msg):
        body = {"message": msg, "content": base64.b64encode(txt.encode()).decode(), "branch": self.branch}
        if path in self.shas: body["sha"] = self.shas[path]
        r = requests.put(f"{self.api}/contents/{path}", headers=self.h, json=body, timeout=60)
        if r.status_code in (409, 422):                                          # stale sha: fetch the current one and retry once
            g = requests.get(f"{self.api}/contents/{path}", params={"ref": self.branch}, headers=self.h, timeout=30)
            if g.status_code == 200: body["sha"] = g.json()["sha"]; r = requests.put(f"{self.api}/contents/{path}", headers=self.h, json=body, timeout=60)
        r.raise_for_status(); self.shas[path] = r.json()["content"]["sha"]
    def _delete(self, path, msg):
        sha = self.shas.get(path)
        if sha is None: return
        body = {"message": msg, "sha": sha, "branch": self.branch}
        r = requests.delete(f"{self.api}/contents/{path}", headers=self.h, json=body, timeout=60)
        if r.status_code in (409, 422):
            g = requests.get(f"{self.api}/contents/{path}", params={"ref": self.branch}, headers=self.h, timeout=30)
            if g.status_code == 200: body["sha"] = g.json()["sha"]; r = requests.delete(f"{self.api}/contents/{path}", headers=self.h, json=body, timeout=60)
        if r.status_code not in (200, 404): r.raise_for_status()
        self.shas.pop(path, None)
    def save(self, tid, obj):
        txt = json.dumps(obj, separators=(",", ":")); skipped = obj.get("status") == "skipped"
        name, other = (f"{tid}{SKIP}", f"{tid}.json") if skipped else (f"{tid}.json", f"{tid}{SKIP}")
        if not self.remote:
            LOCAL.mkdir(exist_ok=True); (LOCAL / name).write_text(txt); (LOCAL / other).unlink(missing_ok=True); return
        self._put(f"labels/{name}", txt, f"label {tid} ({obj.get('status')})"); self._delete(f"labels/{other}", f"label {tid}: replaced by {name}")

@st.cache_resource
def get_store(): return Store()

@st.cache_data
def manifest(): return json.loads((HERE / "data" / "manifest.json").read_text())

@st.cache_data(ttl=300, show_spinner=False)
def skipped_details(_store, items):
    """tile id -> label content of every skipped tile (remarks, annotator, time). items = ((tile id, path, sha), ...) so the cache follows the branch."""
    out = {}
    for tid, path, _ in items:
        try: out[tid] = _store.read(path) or {}
        except Exception: out[tid] = {}
    return out

SRC_LABEL = {"All imagery": None, "Google Earth screenshots": "", "WorldView Legion, Jan 2026": "legion", "WorldView-2, Dec 2023": "wv2"}   # manifest "src": absent for Google Earth tiles
STATUS_CHOICES = ["All tiles", "Pending", "Done", "Skipped"]
def ids_for(M, choice):
    want = SRC_LABEL.get(choice)
    return [t["id"] for t in M] if want is None else [t["id"] for t in M if t.get("src", "") == want]
def bucket(done, tid):
    """pending | done | skipped (no_trees counts as done)"""
    s = done.get(tid); return "pending" if s is None else ("skipped" if s == "skipped" else "done")
def source_of(t): return t.get("source", "Google Earth screenshot")
