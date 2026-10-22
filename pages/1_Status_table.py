"""Status of every tile: pending, done or skipped, with the remarks written when a tile was skipped. Reads the same labels as the annotation page."""
import pandas as pd, streamlit as st
from store import get_store, manifest, skipped_details, SRC_LABEL, ids_for, bucket, source_of

st.set_page_config(page_title="Tile status", layout="wide")
store = get_store(); M = manifest()
st.title("Tile status")
if st.button("Refresh from the repository"): st.cache_data.clear(); st.session_state.pop("done", None); st.rerun()
try: done = store.list_done()
except Exception as e: st.error(f"Could not read saved labels: {e}"); st.stop()
det = skipped_details(store, tuple(store.skipped()))
rows = []
for t in M:
    k = bucket(done, t["id"]); d = det.get(t["id"], {}) if k == "skipped" else {}
    rows.append({"Tile": t["id"], "Farm": t["farm"], "Imagery": source_of(t), "Status": k.capitalize(), "Remarks": d.get("remarks", ""), "By": d.get("annotator", ""), "Saved (UTC)": d.get("saved", "").replace("T", " ").replace("Z", ""), "Draft trees": len(t["prefill"])})
df = pd.DataFrame(rows)

f = st.columns([2, 2, 3])
src = f[0].selectbox("Imagery", list(SRC_LABEL)); stat = f[1].multiselect("Status", ["Pending", "Done", "Skipped"], default=["Pending", "Done", "Skipped"]); q = f[2].text_input("Search tile, farm or remarks")
v = df[df.Tile.isin(ids_for(M, src)) & df.Status.isin(stat)]
if q: v = v[v.apply(lambda r: q.lower() in " ".join(str(x) for x in r.values).lower(), axis=1)]
base = df[df.Tile.isin(ids_for(M, src))]
c = st.columns(4); c[0].metric("Tiles", len(base)); c[1].metric("Pending", int((base.Status == "Pending").sum())); c[2].metric("Done", int((base.Status == "Done").sum())); c[3].metric("Skipped", int((base.Status == "Skipped").sum()))
by = df.pivot_table(index="Imagery", columns="Status", values="Tile", aggfunc="count", fill_value=0).reindex(columns=["Pending", "Done", "Skipped"], fill_value=0); by["Total"] = by.sum(axis=1)
st.caption("By imagery"); st.dataframe(by, use_container_width=True)
st.caption(f"{len(v)} tiles shown. Done includes tiles saved with no trees. Remarks, name and time are listed for skipped tiles.")
st.dataframe(v, use_container_width=True, hide_index=True, height=520, column_config={"Remarks": st.column_config.TextColumn(width="large")})
st.download_button("Download the table as CSV", v.to_csv(index=False).encode(), file_name="tile_status.csv", mime="text/csv")
