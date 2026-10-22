# Tree annotation tool

Streamlit app for marking tree crowns on 640 px farm tiles (6 px per metre, so a tile is about 107 m across). Each tile arrives with the detector's circles already drawn; you add the missed trees and delete the wrong circles, then press **Save and next**.

## Imagery
The sidebar **Imagery** menu switches between the Google Earth screenshot tiles and the 88 block 01 tiles cut from the UP42 images: WorldView Legion (23 Jan 2026, 0.36 m) and WorldView-2 (25 Dec 2023, 0.56 m). Both are cut at the same 6 px/m, outside the farm outline shaded dark, with the v6 detector's boxes as the draft. Tile ids start with L (Legion) or W (WorldView-2). Build them with `code/build_geotiff_annotation_tiles.py`; the ingest re-cuts them from the GeoTIFFs.

## How labelling works
- **Add a tree** mode: click the middle of a tree. **Delete a tree** mode: click on a circle to remove the nearest one.
- The radius slider sets the size of new circles (6 px = 1 m). Draft circles keep their detector size.
- **Skip** needs a remark (a text box under the buttons): say what is unclear. Remarks are optional on Save and next.
- The sidebar shows how many tiles are **pending, done and skipped**, and the **Show tiles that are** list filters the tiles to pending, done or skipped. The **Status table** page (menu at the top left) lists every tile with its status, remarks, who skipped it and when, with a CSV download.
- **Save and next** stores the tile (a tile with no circles is saved as "no trees"); **Skip** is for pictures you cannot judge.
- Progress is read from the repository, so you can close the tab and carry on later, from any computer.

## Set-up (once)
1. Create an empty **private** GitHub repo, for example `arvind-s/annotation_tree`, and push this folder to its `main` branch.
2. Create a GitHub **fine-grained personal access token**: only this repository, permission *Contents: Read and write*. Keep it secret.
3. On https://share.streamlit.io choose *New app* → this repo, branch `main`, file `app.py`. Under *Advanced settings → Secrets* paste:
   ```toml
   GITHUB_TOKEN = "github_pat_..."
   GITHUB_REPO  = "arvind-s/annotation_tree"
   ```
4. In the app's *Share* menu keep it private and invite the annotators by email.

Labels are committed to a separate branch called `labels` (created by the app on first start), as one small file per tile: `labels/<tile id>.json` (done or no trees) or `labels/<tile id>.skipped.json` (skipped, holding the remarks). Saving a tile removes the file of the other kind. Tiles skipped in an earlier version sit under the plain name and show as done until opened once. Saves therefore never touch `main`, so the app is not redeployed while you work.

Run it on your own computer first: `pip install -r requirements.txt` then `streamlit run app.py`. Without secrets it writes to `labels_local/`.

## Sending the labels back to the model
On the machine that holds the model (`/Users/mipl/Downloads/farm_landcover`):
```bash
git clone -b labels --single-branch https://github.com/arvind-s/annotation_tree.git /tmp/annotation_tree-labels    # or: git -C /tmp/annotation_tree-labels pull
./.venv_yolo/bin/python -I code/ingest_tile_labels.py /tmp/annotation_tree-labels/labels /Users/mipl/Downloads/images tree_yolo/ds_v6 tree_yolo/ds_v7
./.venv_yolo/bin/python -I code/tree_yolo_train2.py $PWD/tree_yolo/ds_v7 v7 yolov8s.pt 960 50 0.4 mps          # about 3 hours
./.venv_yolo/bin/python -I code/pp_eval.py tree_yolo/runs/v7/weights/best.pt tree_yolo/pp_eval_v7.json         # same 23 unseen test farms as v5 and v6
```
`ingest_tile_labels.py` re-cuts each labelled tile from the original screenshot with the area outside the farm grey (the app shows it darkened), writes YOLO labels, and adds the tiles to a copy of the v6 training set. Farms are split 90/10 into train/val by hash. The 23 test farms and every blind-test farm are never used. Skipped tiles are ignored; "no trees" tiles become empty negative examples.

Promote v7 only if its error on the 23 test farms beats v6 (11.6 trees per farm raw, 9.9 after merging boxes). Count the blind-test farms only once, at the end.
