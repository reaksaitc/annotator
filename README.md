# Image Annotator

A local image labelling/annotation tool for building object-detection datasets.

## Desktop app (recommended): `desktop_annotator.py`

Pure Tkinter + Pillow — no server, everything stays on disk.

### Setup

```
pip install -r requirements.txt
python desktop_annotator.py
```

### Workflow

1. **Open Folder** — pick a folder of images (jpg/png/bmp/webp/tiff).
2. **+ New Label** — add a class name and pick a color. Labels are saved per-folder
   in `_annotator_labels.json` so they persist across sessions.
3. Pick a draw **Mode**:
   - **2-Point** — click top-left, then bottom-right, to draw an axis-aligned box.
   - **4-Point** — click four corners in order, for a rotated/oriented box (great
     for text lines, tilted objects, etc.). The axis-aligned bounding box is
     computed automatically alongside the 4 corner points.
   - **Select** — click a box to select it, drag to move it, drag a corner/vertex
     handle to resize it.
4. Every add/move/resize/delete is auto-saved to a JSON sidecar next to each image
   (`photo.jpg` -> `photo.json`), pairing that filename with its labels and boxes.
5. Use **< Prev** / **Next >** (or PageUp/PageDown) to move through the folder,
   **Ctrl+Z** / **Ctrl+Y** to undo/redo, **Delete** to remove the selected box.

### Export

Use the **Export** menu to write a training-ready dataset from all annotated
images in the folder:

| Format | Output | Use with |
|---|---|---|
| YOLO (bbox) | `images/`, `labels/*.txt`, `classes.txt`, `data.yaml` | Ultralytics YOLOv5/v8/v11 |
| YOLO-OBB (4-point) | `images/`, `labels_obb/*.txt` (normalized corner coords) | Ultralytics YOLO-OBB (oriented detection) |
| Pascal VOC | `JPEGImages/`, `Annotations/*.xml` | TensorFlow Object Detection API, torchvision |
| COCO | `images/`, `annotations.json` (includes polygon `segmentation` for 4-point boxes) | pycocotools, Detectron2, TF, PyTorch |
| Filename<->Label pairs (CSV/JSON) | `annotations.csv` / `annotations_pairs.json` | Custom `torch.utils.data.Dataset` or `tf.data` pipeline |

Each sidecar JSON (`<image>.json`) is itself already a filename-to-annotation
pairing, so you can also read those directly if you'd rather write your own
loader.

## Web app (optional): `app.py`

A Flask + HTML5 Canvas version of the same idea, for browser-based use.

```
pip install -r requirements.txt
python app.py
```

Then open `http://localhost:5000`. Annotations are kept in memory for the
session; use the Export buttons (YOLO / VOC / COCO / CSV) to download a zip.
