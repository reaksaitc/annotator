"""
Desktop Image Annotator (Tkinter + Pillow).

Draw bounding boxes two ways:
  - 2-point: click top-left, click bottom-right
  - 4-point: click four corners (supports rotated/oriented boxes)

Label each box with a class, then export the whole folder as
YOLO, YOLO-OBB, Pascal VOC, COCO JSON, or a flat filename<->label CSV/JSON
pairing for training in YOLO, TensorFlow, or PyTorch.
"""
import json
import shutil
import xml.etree.ElementTree as ET
from pathlib import Path
from xml.dom import minidom

import tkinter as tk
from tkinter import colorchooser, filedialog, messagebox, simpledialog, ttk

from PIL import Image, ImageOps, ImageTk

IMAGE_EXTS = {'.jpg', '.jpeg', '.png', '.bmp', '.webp', '.tiff', '.tif'}
LABELS_FILENAME = '_annotator_labels.json'
HANDLE_RADIUS = 6
HIT_RADIUS = 8
PALETTE = [
    '#e6194b', '#3cb44b', '#4363d8', '#f58231', '#911eb4',
    '#46f0f0', '#f032e6', '#bcf60c', '#fabed4', '#008080',
    '#e6beff', '#9a6324', '#800000', '#aaffc3', '#808000',
]


def clamp(v, lo, hi):
    return max(lo, min(hi, v))


class Box:
    """A single annotation: two_point (axis-aligned) or four_point (any quad)."""

    def __init__(self, label, box_type, bbox, points=None):
        self.label = label
        self.type = box_type
        self.bbox = list(bbox)            # [x1, y1, x2, y2] in image pixels
        self.points = points and [list(p) for p in points]  # 4 [x, y] for four_point

    @classmethod
    def two_point(cls, label, p1, p2):
        x1, y1 = p1
        x2, y2 = p2
        bbox = [min(x1, x2), min(y1, y2), max(x1, x2), max(y1, y2)]
        return cls(label, 'two_point', bbox)

    @classmethod
    def four_point(cls, label, points):
        xs = [p[0] for p in points]
        ys = [p[1] for p in points]
        bbox = [min(xs), min(ys), max(xs), max(ys)]
        return cls(label, 'four_point', bbox, points)

    def corners(self):
        """4 corner points in order, for OBB / segmentation export."""
        if self.type == 'four_point':
            return [list(p) for p in self.points]
        x1, y1, x2, y2 = self.bbox
        return [[x1, y1], [x2, y1], [x2, y2], [x1, y2]]

    def move(self, dx, dy):
        self.bbox = [self.bbox[0] + dx, self.bbox[1] + dy,
                     self.bbox[2] + dx, self.bbox[3] + dy]
        if self.points:
            self.points = [[p[0] + dx, p[1] + dy] for p in self.points]

    def to_dict(self):
        d = {'label': self.label, 'type': self.type, 'bbox': self.bbox}
        if self.points:
            d['points'] = self.points
        return d

    @classmethod
    def from_dict(cls, d):
        return cls(d['label'], d.get('type', 'two_point'), d['bbox'], d.get('points'))


class Annotator:
    def __init__(self):
        self.root = tk.Tk()
        self.root.title("Image Annotator")
        self.root.geometry("1300x850")

        self.folder = None
        self.images = []           # list of Path
        self.current_index = -1
        self.cache = {}            # str(path) -> {'width','height','boxes':[Box]}

        self.pil_image = None      # currently loaded PIL image
        self.img_w = self.img_h = 0
        self.base_scale = 1.0
        self.zoom = 1.0
        self.tk_image = None
        self.image_item = None

        self.boxes = []            # Box list for current image
        self.selected_idx = -1
        self.selected_vertex = -1  # for four_point vertex drag
        self.resize_handle = None  # 'nw','ne','sw','se' for two_point resize
        self.drag_mode = None      # 'move' | 'resize' | 'vertex'
        self.drag_start = None
        self.drag_orig_box = None

        self.pending_points = []   # points collected while drawing
        self.mouse_img_pos = None

        self.undo_stack = []
        self.redo_stack = []

        self.labels = []           # [{'name','color'}]
        self.draw_mode = tk.StringVar(value='two_point')
        self.current_label = tk.StringVar(value='')
        self.zoom_label_var = tk.StringVar(value='100%')

        self._build_ui()
        self._bind_shortcuts()
        self.root.mainloop()

    # ------------------------------------------------------------------
    # UI construction
    # ------------------------------------------------------------------
    def _build_ui(self):
        tb = tk.Frame(self.root)
        tb.pack(side=tk.TOP, fill=tk.X)

        tk.Button(tb, text="Open Folder", command=self.open_folder).pack(side=tk.LEFT, padx=4, pady=4)
        tk.Button(tb, text="Save All", command=self.save_all).pack(side=tk.LEFT, padx=4)

        tk.Label(tb, text="Mode:").pack(side=tk.LEFT, padx=(16, 4))
        for text, val in (("2-Point", "two_point"), ("4-Point", "four_point"), ("Select", "select")):
            tk.Radiobutton(tb, text=text, variable=self.draw_mode, value=val,
                            command=self._cancel_pending).pack(side=tk.LEFT)

        tk.Label(tb, text=" Label:").pack(side=tk.LEFT, padx=(16, 4))
        self.label_combo = ttk.Combobox(tb, textvariable=self.current_label, width=15, state='readonly')
        self.label_combo.pack(side=tk.LEFT)
        tk.Button(tb, text="+ New Label", command=self.add_label_dialog).pack(side=tk.LEFT, padx=4)

        tk.Label(tb, text=" Zoom:").pack(side=tk.LEFT, padx=(16, 4))
        tk.Button(tb, text="-", width=2, command=lambda: self.adjust_zoom(0.8)).pack(side=tk.LEFT)
        tk.Label(tb, textvariable=self.zoom_label_var, width=5).pack(side=tk.LEFT)
        tk.Button(tb, text="+", width=2, command=lambda: self.adjust_zoom(1.25)).pack(side=tk.LEFT)
        tk.Button(tb, text="Fit", command=self.fit_to_window).pack(side=tk.LEFT, padx=4)

        tk.Button(tb, text="< Prev", command=lambda: self.step_image(-1)).pack(side=tk.LEFT, padx=(16, 2))
        tk.Button(tb, text="Next >", command=lambda: self.step_image(1)).pack(side=tk.LEFT, padx=2)

        # Export menu button
        export_mb = tk.Menubutton(tb, text="Export", relief=tk.RAISED)
        export_menu = tk.Menu(export_mb, tearoff=0)
        export_menu.add_command(label="YOLO (bbox)", command=lambda: self.export('yolo'))
        export_menu.add_command(label="YOLO-OBB (4-point)", command=lambda: self.export('yolo_obb'))
        export_menu.add_command(label="Pascal VOC (XML)", command=lambda: self.export('voc'))
        export_menu.add_command(label="COCO (JSON)", command=lambda: self.export('coco'))
        export_menu.add_command(label="Filename<->Label pairs (CSV)", command=lambda: self.export('csv'))
        export_menu.add_command(label="Filename<->Label pairs (JSON)", command=lambda: self.export('json'))
        export_mb.config(menu=export_menu)
        export_mb.pack(side=tk.RIGHT, padx=4)

        # Main body: canvas + sidebar
        body = tk.Frame(self.root)
        body.pack(fill=tk.BOTH, expand=True)

        canvas_frame = tk.Frame(body, bg='#333')
        canvas_frame.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        hbar = tk.Scrollbar(canvas_frame, orient=tk.HORIZONTAL)
        vbar = tk.Scrollbar(canvas_frame, orient=tk.VERTICAL)
        self.canvas = tk.Canvas(canvas_frame, bg='#222', cursor='tcross',
                                 xscrollcommand=hbar.set, yscrollcommand=vbar.set)
        hbar.config(command=self.canvas.xview)
        vbar.config(command=self.canvas.yview)
        hbar.pack(side=tk.BOTTOM, fill=tk.X)
        vbar.pack(side=tk.RIGHT, fill=tk.Y)
        self.canvas.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)

        self.canvas.bind('<Button-1>', self.on_click)
        self.canvas.bind('<B1-Motion>', self.on_drag)
        self.canvas.bind('<ButtonRelease-1>', self.on_release)
        self.canvas.bind('<Motion>', self.on_motion)
        self.canvas.bind('<Button-3>', self.on_right_click)
        self.canvas.bind('<Control-MouseWheel>', self.on_ctrl_wheel)
        self.canvas.bind('<MouseWheel>', self.on_wheel)
        self.canvas.bind('<Shift-MouseWheel>', self.on_shift_wheel)

        side = tk.Frame(body, width=260, bg='#f0f0f0')
        side.pack(side=tk.RIGHT, fill=tk.Y)
        side.pack_propagate(False)

        tk.Label(side, text="Images", bg='#f0f0f0', font=('', 9, 'bold')).pack(anchor=tk.W, padx=6, pady=(6, 0))
        self.image_list = tk.Listbox(side, height=12)
        self.image_list.pack(fill=tk.X, padx=6, pady=4)
        self.image_list.bind('<<ListboxSelect>>', self.on_select_image)

        tk.Label(side, text="Labels", bg='#f0f0f0', font=('', 9, 'bold')).pack(anchor=tk.W, padx=6, pady=(6, 0))
        self.label_list = tk.Listbox(side, height=6)
        self.label_list.pack(fill=tk.X, padx=6, pady=4)
        self.label_list.bind('<<ListboxSelect>>', self.on_select_label)
        tk.Button(side, text="Delete Label", command=self.delete_label).pack(fill=tk.X, padx=6)

        tk.Label(side, text="Boxes on this image", bg='#f0f0f0', font=('', 9, 'bold')).pack(anchor=tk.W, padx=6, pady=(10, 0))
        self.box_list = tk.Listbox(side, height=10)
        self.box_list.pack(fill=tk.BOTH, expand=True, padx=6, pady=4)
        self.box_list.bind('<<ListboxSelect>>', self.on_select_box)
        self.box_list.bind('<Double-Button-1>', lambda e: self.rename_selected_box())
        btn_row = tk.Frame(side, bg='#f0f0f0')
        btn_row.pack(fill=tk.X, padx=6)
        tk.Button(btn_row, text="Delete", command=self.delete_selected).pack(side=tk.LEFT, expand=True, fill=tk.X)
        tk.Button(btn_row, text="Rename", command=self.rename_selected_box).pack(side=tk.LEFT, expand=True, fill=tk.X)

        self.status = tk.Label(self.root, text="Open a folder to begin", bd=1, relief=tk.SUNKEN, anchor=tk.W)
        self.status.pack(side=tk.BOTTOM, fill=tk.X)

    def _bind_shortcuts(self):
        self.root.bind('<Control-z>', lambda e: self.undo())
        self.root.bind('<Control-y>', lambda e: self.redo())
        self.root.bind('<Control-Shift-Z>', lambda e: self.redo())
        self.root.bind('<Delete>', lambda e: self.delete_selected())
        self.root.bind('<BackSpace>', lambda e: self.delete_selected())
        self.root.bind('<Escape>', lambda e: self._cancel_pending())
        self.root.bind('<Control-s>', lambda e: self.save_all())
        self.root.bind('<Prior>', lambda e: self.step_image(-1))   # PageUp
        self.root.bind('<Next>', lambda e: self.step_image(1))     # PageDown

    # ------------------------------------------------------------------
    # Folder / project
    # ------------------------------------------------------------------
    def open_folder(self):
        folder = filedialog.askdirectory()
        if not folder:
            return
        self.folder = Path(folder)
        self.images = sorted(
            p for p in self.folder.iterdir()
            if p.suffix.lower() in IMAGE_EXTS
        )
        self.cache.clear()
        self._load_project_labels()

        self.image_list.delete(0, tk.END)
        for p in self.images:
            count = self._peek_box_count(p)
            self.image_list.insert(tk.END, f"{p.name}  ({count})")

        if self.images:
            self.image_list.selection_set(0)
            self.select_image(0)
        else:
            messagebox.showinfo("No images", "No supported images found in that folder.")

    def _peek_box_count(self, img_path):
        json_path = self._json_path(img_path)
        if not json_path.exists():
            return 0
        try:
            with open(json_path) as f:
                return len(json.load(f).get('boxes', []))
        except (json.JSONDecodeError, OSError):
            return 0

    def _json_path(self, img_path):
        return img_path.with_suffix('.json')

    def _labels_path(self):
        return self.folder / LABELS_FILENAME

    def _load_project_labels(self):
        self.labels = []
        path = self._labels_path()
        if path.exists():
            try:
                with open(path) as f:
                    self.labels = json.load(f)
            except (json.JSONDecodeError, OSError):
                self.labels = []
        if not self.labels:
            self.labels = [{'name': 'object', 'color': PALETTE[0]}]
        self._refresh_label_widgets()

    def _save_project_labels(self):
        if not self.folder:
            return
        with open(self._labels_path(), 'w') as f:
            json.dump(self.labels, f, indent=2)

    def _refresh_label_widgets(self):
        self.label_list.delete(0, tk.END)
        for lbl in self.labels:
            self.label_list.insert(tk.END, lbl['name'])
        names = [l['name'] for l in self.labels]
        self.label_combo['values'] = names
        if names and self.current_label.get() not in names:
            self.current_label.set(names[0])

    def label_color(self, name):
        for lbl in self.labels:
            if lbl['name'] == name:
                return lbl['color']
        return '#ffffff'

    def add_label_dialog(self):
        name = simpledialog.askstring("New Label", "Class name:", parent=self.root)
        if not name:
            return
        name = name.strip()
        if not name or any(l['name'] == name for l in self.labels):
            return
        color = colorchooser.askcolor(
            color=PALETTE[len(self.labels) % len(PALETTE)],
            title="Pick a color for this label"
        )[1] or PALETTE[len(self.labels) % len(PALETTE)]
        self.labels.append({'name': name, 'color': color})
        self._save_project_labels()
        self._refresh_label_widgets()
        self.current_label.set(name)

    def delete_label(self):
        sel = self.label_list.curselection()
        if not sel:
            return
        name = self.labels[sel[0]]['name']
        if any(b.label == name for b in self.boxes):
            if not messagebox.askyesno(
                "Label in use",
                f"'{name}' is used by boxes on this image. Delete the label anyway?"
            ):
                return
        del self.labels[sel[0]]
        self._save_project_labels()
        self._refresh_label_widgets()

    def on_select_label(self, _event):
        sel = self.label_list.curselection()
        if sel:
            self.current_label.set(self.labels[sel[0]]['name'])

    # ------------------------------------------------------------------
    # Image loading / navigation
    # ------------------------------------------------------------------
    def on_select_image(self, _event):
        sel = self.image_list.curselection()
        if sel:
            self.select_image(sel[0])

    def step_image(self, delta):
        if not self.images:
            return
        new_idx = clamp(self.current_index + delta, 0, len(self.images) - 1)
        if new_idx != self.current_index:
            self.image_list.selection_clear(0, tk.END)
            self.image_list.selection_set(new_idx)
            self.image_list.see(new_idx)
            self.select_image(new_idx)

    def select_image(self, index):
        if not (0 <= index < len(self.images)):
            return
        self.current_index = index
        path = self.images[index]

        data = self.cache.get(str(path))
        if data is None:
            data = self._load_annotation(path)
            self.cache[str(path)] = data

        try:
            img = Image.open(path)
            img = ImageOps.exif_transpose(img).convert('RGB')
        except Exception as e:
            messagebox.showerror("Failed to open image", f"{path.name}: {e}")
            return

        self.pil_image = img
        self.img_w, self.img_h = img.size
        self.boxes = data['boxes']
        self.selected_idx = -1
        self.selected_vertex = -1
        self.pending_points = []
        self.undo_stack = []
        self.redo_stack = []

        self.fit_to_window()
        self._refresh_box_list()
        self._update_status()

    def _load_annotation(self, path):
        json_path = self._json_path(path)
        boxes = []
        width = height = None
        if json_path.exists():
            try:
                with open(json_path) as f:
                    raw = json.load(f)
                width, height = raw.get('width'), raw.get('height')
                boxes = [Box.from_dict(b) for b in raw.get('boxes', [])]
            except (json.JSONDecodeError, OSError):
                pass
        return {'width': width, 'height': height, 'boxes': boxes}

    def save_current(self):
        if not (0 <= self.current_index < len(self.images)):
            return
        path = self.images[self.current_index]
        data = {
            'image': path.name,
            'width': self.img_w,
            'height': self.img_h,
            'boxes': [b.to_dict() for b in self.boxes],
        }
        with open(self._json_path(path), 'w') as f:
            json.dump(data, f, indent=2)
        self.cache[str(path)] = {'width': self.img_w, 'height': self.img_h, 'boxes': self.boxes}
        self.image_list.delete(self.current_index)
        self.image_list.insert(self.current_index, f"{path.name}  ({len(self.boxes)})")
        self.image_list.selection_set(self.current_index)

    def save_all(self):
        if not self.images:
            return
        cur = self.current_index
        for i in range(len(self.images)):
            self.current_index = i
            path = self.images[i]
            data = self.cache.get(str(path))
            if data is None:
                continue
            with open(self._json_path(path), 'w') as f:
                json.dump({
                    'image': path.name,
                    'width': data['width'] or 0,
                    'height': data['height'] or 0,
                    'boxes': [b.to_dict() for b in data['boxes']],
                }, f, indent=2)
        self.current_index = cur
        self._save_project_labels()
        messagebox.showinfo("Saved", f"Saved annotations for {len(self.images)} image(s).")

    # ------------------------------------------------------------------
    # Rendering
    # ------------------------------------------------------------------
    def fit_to_window(self):
        self.canvas.update_idletasks()
        vw = max(self.canvas.winfo_width(), 200)
        vh = max(self.canvas.winfo_height(), 200)
        if self.img_w and self.img_h:
            self.base_scale = min(vw / self.img_w, vh / self.img_h, 1.0)
        self.zoom = 1.0
        self._redraw_image()

    def adjust_zoom(self, factor):
        if not self.pil_image:
            return
        self.zoom = clamp(self.zoom * factor, 0.1, 8.0)
        self._redraw_image()

    def on_ctrl_wheel(self, event):
        self.adjust_zoom(1.1 if event.delta > 0 else 0.9)

    def on_wheel(self, event):
        self.canvas.yview_scroll(-1 if event.delta > 0 else 1, 'units')

    def on_shift_wheel(self, event):
        self.canvas.xview_scroll(-1 if event.delta > 0 else 1, 'units')

    @property
    def scale(self):
        return self.base_scale * self.zoom

    def _redraw_image(self):
        if self.pil_image is None:
            return
        s = self.scale
        disp_w, disp_h = max(1, int(self.img_w * s)), max(1, int(self.img_h * s))
        resized = self.pil_image.resize((disp_w, disp_h), Image.BILINEAR)
        self.tk_image = ImageTk.PhotoImage(resized)
        self.canvas.delete('all')
        self.image_item = self.canvas.create_image(0, 0, anchor=tk.NW, image=self.tk_image)
        self.canvas.config(scrollregion=(0, 0, disp_w, disp_h))
        self.zoom_label_var.set(f"{int(s * 100)}%")
        self._redraw_overlay()

    def _redraw_overlay(self):
        self.canvas.delete('ov')
        s = self.scale
        for i, box in enumerate(self.boxes):
            self._draw_box(box, selected=(i == self.selected_idx))

        for p in self.pending_points:
            x, y = p[0] * s, p[1] * s
            self.canvas.create_oval(x - 4, y - 4, x + 4, y + 4, fill='#ffff00', outline='', tags='ov')

        if self.pending_points and self.mouse_img_pos:
            mx, my = self.mouse_img_pos[0] * s, self.mouse_img_pos[1] * s
            mode = self.draw_mode.get()
            color = self.label_color(self.current_label.get()) or '#ffff00'
            if mode == 'two_point' and len(self.pending_points) == 1:
                x0, y0 = self.pending_points[0][0] * s, self.pending_points[0][1] * s
                self.canvas.create_rectangle(x0, y0, mx, my, outline=color, dash=(5, 3), width=2, tags='ov')
            elif mode == 'four_point':
                pts = self.pending_points + [self.mouse_img_pos]
                flat = [c for p in pts for c in (p[0] * s, p[1] * s)]
                if len(pts) >= 2:
                    self.canvas.create_line(*flat, fill=color, dash=(5, 3), width=2, tags='ov')

        self._update_status()

    def _draw_box(self, box, selected):
        s = self.scale
        color = self.label_color(box.label) or '#00ff00'
        outline = '#ffff00' if selected else color
        width = 3 if selected else 2

        if box.type == 'four_point':
            flat = [c for p in box.points for c in (p[0] * s, p[1] * s)]
            self.canvas.create_polygon(*flat, outline=outline, fill='', width=width, tags='ov')
            if selected:
                for p in box.points:
                    x, y = p[0] * s, p[1] * s
                    self.canvas.create_rectangle(
                        x - HANDLE_RADIUS, y - HANDLE_RADIUS, x + HANDLE_RADIUS, y + HANDLE_RADIUS,
                        fill='#ffff00', outline='#000000', tags='ov')
            label_x, label_y = box.bbox[0] * s, box.bbox[1] * s
        else:
            x1, y1, x2, y2 = [c * s for c in box.bbox]
            self.canvas.create_rectangle(x1, y1, x2, y2, outline=outline, width=width, tags='ov')
            if selected:
                for hx, hy in ((x1, y1), (x2, y1), (x1, y2), (x2, y2)):
                    self.canvas.create_rectangle(
                        hx - HANDLE_RADIUS, hy - HANDLE_RADIUS, hx + HANDLE_RADIUS, hy + HANDLE_RADIUS,
                        fill='#ffff00', outline='#000000', tags='ov')
            label_x, label_y = x1, y1

        text = box.label
        tw = 7 * len(text) + 8
        self.canvas.create_rectangle(label_x, label_y - 18, label_x + tw, label_y, fill=outline, outline='', tags='ov')
        self.canvas.create_text(label_x + 4, label_y - 9, text=text, anchor=tk.W, fill='#000000', tags='ov')

    def _update_status(self):
        if not self.images:
            self.status.config(text="Open a folder to begin")
            return
        path = self.images[self.current_index]
        self.status.config(
            text=f"[{self.current_index + 1}/{len(self.images)}] {path.name}  |  "
                 f"{self.img_w}x{self.img_h}  |  Zoom {int(self.scale * 100)}%  |  "
                 f"Boxes: {len(self.boxes)}  |  Mode: {self.draw_mode.get()}"
        )

    # ------------------------------------------------------------------
    # Coordinate helpers
    # ------------------------------------------------------------------
    def _event_to_image_coords(self, event):
        cx = self.canvas.canvasx(event.x)
        cy = self.canvas.canvasy(event.y)
        s = self.scale
        x = clamp(cx / s, 0, self.img_w)
        y = clamp(cy / s, 0, self.img_h)
        return x, y

    def _hit_test_box(self, x, y):
        """Return index of topmost box containing (x, y) in image coords, else -1."""
        for i in range(len(self.boxes) - 1, -1, -1):
            box = self.boxes[i]
            if box.type == 'four_point':
                if _point_in_polygon((x, y), box.points):
                    return i
            else:
                x1, y1, x2, y2 = box.bbox
                if x1 <= x <= x2 and y1 <= y <= y2:
                    return i
        return -1

    def _hit_test_handle(self, box, x, y):
        s = self.scale
        r = HIT_RADIUS / s
        if box.type == 'four_point':
            for idx, p in enumerate(box.points):
                if abs(p[0] - x) <= r and abs(p[1] - y) <= r:
                    return idx
            return -1
        else:
            x1, y1, x2, y2 = box.bbox
            corners = {'nw': (x1, y1), 'ne': (x2, y1), 'sw': (x1, y2), 'se': (x2, y2)}
            for name, (cx, cy) in corners.items():
                if abs(cx - x) <= r and abs(cy - y) <= r:
                    return name
            return None

    # ------------------------------------------------------------------
    # Mouse interaction
    # ------------------------------------------------------------------
    def on_click(self, event):
        if not self.pil_image:
            return
        x, y = self._event_to_image_coords(event)
        mode = self.draw_mode.get()

        if mode == 'select':
            if self.selected_idx >= 0:
                box = self.boxes[self.selected_idx]
                if box.type == 'four_point':
                    vidx = self._hit_test_handle(box, x, y)
                    if vidx is not None and vidx != -1:
                        self.drag_mode = 'vertex'
                        self.selected_vertex = vidx
                        self.drag_start = (x, y)
                        self.drag_orig_box = Box.from_dict(box.to_dict())
                        return
                else:
                    handle = self._hit_test_handle(box, x, y)
                    if handle:
                        self.drag_mode = 'resize'
                        self.resize_handle = handle
                        self.drag_start = (x, y)
                        self.drag_orig_box = Box.from_dict(box.to_dict())
                        return

            idx = self._hit_test_box(x, y)
            self.selected_idx = idx
            self.selected_vertex = -1
            if idx >= 0:
                self.drag_mode = 'move'
                self.drag_start = (x, y)
                self.drag_orig_box = Box.from_dict(self.boxes[idx].to_dict())
            self._refresh_box_list()
            self._redraw_overlay()

        elif mode == 'two_point':
            self.pending_points.append((x, y))
            if len(self.pending_points) == 2:
                self._finalize_two_point()
            self._redraw_overlay()

        elif mode == 'four_point':
            self.pending_points.append((x, y))
            if len(self.pending_points) == 4:
                self._finalize_four_point()
            self._redraw_overlay()

    def on_drag(self, event):
        if self.draw_mode.get() != 'select' or self.drag_mode is None:
            return
        x, y = self._event_to_image_coords(event)
        dx, dy = x - self.drag_start[0], y - self.drag_start[1]
        box = self.boxes[self.selected_idx]
        orig = self.drag_orig_box

        if self.drag_mode == 'move':
            box.bbox = [orig.bbox[0] + dx, orig.bbox[1] + dy, orig.bbox[2] + dx, orig.bbox[3] + dy]
            if orig.points:
                box.points = [[p[0] + dx, p[1] + dy] for p in orig.points]

        elif self.drag_mode == 'resize':
            x1, y1, x2, y2 = orig.bbox
            h = self.resize_handle
            if 'n' in h:
                y1 = y
            if 's' in h:
                y2 = y
            if 'w' in h:
                x1 = x
            if 'e' in h:
                x2 = x
            box.bbox = [min(x1, x2), min(y1, y2), max(x1, x2), max(y1, y2)]

        elif self.drag_mode == 'vertex':
            pts = [list(p) for p in orig.points]
            pts[self.selected_vertex] = [x, y]
            box.points = pts
            xs = [p[0] for p in pts]
            ys = [p[1] for p in pts]
            box.bbox = [min(xs), min(ys), max(xs), max(ys)]

        self._redraw_overlay()

    def on_release(self, event):
        if self.drag_mode is not None:
            self._push_undo(pre=self.drag_orig_box, idx=self.selected_idx)
            self.drag_mode = None
            self.resize_handle = None
            self.drag_orig_box = None
            self.save_current()
            self._refresh_box_list()

    def on_motion(self, event):
        if not self.pil_image:
            return
        self.mouse_img_pos = self._event_to_image_coords(event)
        if self.pending_points:
            self._redraw_overlay()

    def on_right_click(self, _event):
        if self.pending_points:
            self.pending_points = []
            self._redraw_overlay()

    def _cancel_pending(self):
        self.pending_points = []
        if self.pil_image:
            self._redraw_overlay()

    # ------------------------------------------------------------------
    # Box creation / mutation
    # ------------------------------------------------------------------
    def _require_label(self):
        name = self.current_label.get()
        if not name:
            messagebox.showwarning("No label", "Add or select a label before drawing.")
            self.pending_points = []
            return None
        return name

    def _finalize_two_point(self):
        label = self._require_label()
        if label is None:
            return
        p1, p2 = self.pending_points
        self.pending_points = []
        box = Box.two_point(label, p1, p2)
        if abs(box.bbox[2] - box.bbox[0]) < 1 or abs(box.bbox[3] - box.bbox[1]) < 1:
            return
        self._push_undo()
        self.boxes.append(box)
        self.selected_idx = len(self.boxes) - 1
        self.save_current()
        self._refresh_box_list()

    def _finalize_four_point(self):
        label = self._require_label()
        if label is None:
            return
        points = self.pending_points
        self.pending_points = []
        box = Box.four_point(label, points)
        self._push_undo()
        self.boxes.append(box)
        self.selected_idx = len(self.boxes) - 1
        self.save_current()
        self._refresh_box_list()

    def delete_selected(self):
        if not (0 <= self.selected_idx < len(self.boxes)):
            self._cancel_pending()
            return
        self._push_undo()
        del self.boxes[self.selected_idx]
        self.selected_idx = -1
        self.save_current()
        self._refresh_box_list()
        self._redraw_overlay()

    def rename_selected_box(self):
        if not (0 <= self.selected_idx < len(self.boxes)):
            return
        names = [l['name'] for l in self.labels]
        if not names:
            return
        current = self.boxes[self.selected_idx].label
        new_label = simpledialog.askstring(
            "Rename label", f"Label for this box (existing: {', '.join(names)}):",
            initialvalue=current, parent=self.root
        )
        if not new_label:
            return
        new_label = new_label.strip()
        if not new_label:
            return
        if not any(l['name'] == new_label for l in self.labels):
            color = PALETTE[len(self.labels) % len(PALETTE)]
            self.labels.append({'name': new_label, 'color': color})
            self._save_project_labels()
            self._refresh_label_widgets()
        self._push_undo()
        self.boxes[self.selected_idx].label = new_label
        self.save_current()
        self._refresh_box_list()
        self._redraw_overlay()

    # ------------------------------------------------------------------
    # Box list sidebar
    # ------------------------------------------------------------------
    def _refresh_box_list(self):
        self.box_list.delete(0, tk.END)
        for b in self.boxes:
            tag = 'quad' if b.type == 'four_point' else 'box'
            self.box_list.insert(tk.END, f"{b.label}  [{tag}]")
        if 0 <= self.selected_idx < len(self.boxes):
            self.box_list.selection_set(self.selected_idx)

    def on_select_box(self, _event):
        sel = self.box_list.curselection()
        if sel:
            self.selected_idx = sel[0]
            self.selected_vertex = -1
            self._redraw_overlay()

    # ------------------------------------------------------------------
    # Undo / redo
    # ------------------------------------------------------------------
    def _push_undo(self, pre=None, idx=None):
        self.undo_stack.append([b.to_dict() for b in self.boxes])
        if len(self.undo_stack) > 50:
            self.undo_stack.pop(0)
        self.redo_stack.clear()

    def undo(self):
        if not self.undo_stack:
            return
        self.redo_stack.append([b.to_dict() for b in self.boxes])
        state = self.undo_stack.pop()
        self.boxes = [Box.from_dict(d) for d in state]
        self.selected_idx = -1
        self.save_current()
        self._refresh_box_list()
        self._redraw_overlay()

    def redo(self):
        if not self.redo_stack:
            return
        self.undo_stack.append([b.to_dict() for b in self.boxes])
        state = self.redo_stack.pop()
        self.boxes = [Box.from_dict(d) for d in state]
        self.selected_idx = -1
        self.save_current()
        self._refresh_box_list()
        self._redraw_overlay()

    # ------------------------------------------------------------------
    # Export
    # ------------------------------------------------------------------
    def _gather_dataset(self):
        """Re-read every sidecar JSON from disk so export always reflects saved state."""
        items = []
        for path in self.images:
            json_path = self._json_path(path)
            if not json_path.exists():
                continue
            try:
                with open(json_path) as f:
                    data = json.load(f)
            except (json.JSONDecodeError, OSError):
                continue
            if data.get('boxes'):
                items.append((path, data))
        return items

    def export(self, fmt):
        if not self.images:
            messagebox.showwarning("Nothing to export", "Open a folder with images first.")
            return
        self.save_current()
        self._save_project_labels()
        out_dir = filedialog.askdirectory(title="Choose export destination folder")
        if not out_dir:
            return
        out_dir = Path(out_dir)
        dataset = self._gather_dataset()
        if not dataset:
            messagebox.showwarning("Nothing to export", "No annotated images found.")
            return

        try:
            if fmt == 'yolo':
                self._export_yolo(out_dir, dataset, obb=False)
            elif fmt == 'yolo_obb':
                self._export_yolo(out_dir, dataset, obb=True)
            elif fmt == 'voc':
                self._export_voc(out_dir, dataset)
            elif fmt == 'coco':
                self._export_coco(out_dir, dataset)
            elif fmt == 'csv':
                self._export_pairs_csv(out_dir, dataset)
            elif fmt == 'json':
                self._export_pairs_json(out_dir, dataset)
        except OSError as e:
            messagebox.showerror("Export failed", str(e))
            return
        messagebox.showinfo("Export complete", f"Exported to {out_dir}")

    def _class_names(self):
        names = [l['name'] for l in self.labels]
        used = {b['label'] for _, data in self._gather_dataset() for b in data['boxes']}
        for name in sorted(used):
            if name not in names:
                names.append(name)
        return names

    def _export_yolo(self, out_dir, dataset, obb):
        classes = self._class_names()
        images_dir = out_dir / 'images'
        labels_dir = out_dir / ('labels_obb' if obb else 'labels')
        images_dir.mkdir(parents=True, exist_ok=True)
        labels_dir.mkdir(parents=True, exist_ok=True)

        with open(out_dir / 'classes.txt', 'w') as f:
            f.write('\n'.join(classes))

        yaml_lines = [
            "# YOLO dataset config (generated by desktop_annotator.py)",
            f"path: {out_dir.resolve()}",
            "train: images",
            "val: images",
            "",
            "names:",
        ]
        yaml_lines += [f"  {i}: {name}" for i, name in enumerate(classes)]
        with open(out_dir / 'data.yaml', 'w') as f:
            f.write('\n'.join(yaml_lines) + '\n')

        for path, data in dataset:
            shutil.copy(path, images_dir / path.name)
            W, H = data['width'], data['height']
            lines = []
            for b in data['boxes']:
                if b['label'] not in classes:
                    continue
                cid = classes.index(b['label'])
                if obb:
                    box = Box.from_dict(b)
                    coords = box.corners()
                    norm = [f"{clamp(c[0] / W, 0, 1):.6f} {clamp(c[1] / H, 0, 1):.6f}" for c in coords]
                    lines.append(f"{cid} " + " ".join(norm))
                else:
                    x1, y1, x2, y2 = b['bbox']
                    xc, yc = ((x1 + x2) / 2) / W, ((y1 + y2) / 2) / H
                    w, h = (x2 - x1) / W, (y2 - y1) / H
                    lines.append(f"{cid} {xc:.6f} {yc:.6f} {w:.6f} {h:.6f}")
            with open(labels_dir / (path.stem + '.txt'), 'w') as f:
                f.write('\n'.join(lines) + ('\n' if lines else ''))

    def _export_voc(self, out_dir, dataset):
        (out_dir / 'JPEGImages').mkdir(parents=True, exist_ok=True)
        (out_dir / 'Annotations').mkdir(parents=True, exist_ok=True)

        for path, data in dataset:
            shutil.copy(path, out_dir / 'JPEGImages' / path.name)

            root = ET.Element('annotation')
            ET.SubElement(root, 'folder').text = 'JPEGImages'
            ET.SubElement(root, 'filename').text = path.name
            size = ET.SubElement(root, 'size')
            ET.SubElement(size, 'width').text = str(data['width'])
            ET.SubElement(size, 'height').text = str(data['height'])
            ET.SubElement(size, 'depth').text = '3'
            ET.SubElement(root, 'segmented').text = '0'

            for b in data['boxes']:
                obj = ET.SubElement(root, 'object')
                ET.SubElement(obj, 'name').text = b['label']
                ET.SubElement(obj, 'pose').text = 'Unspecified'
                ET.SubElement(obj, 'truncated').text = '0'
                ET.SubElement(obj, 'difficult').text = '0'
                bnd = ET.SubElement(obj, 'bndbox')
                x1, y1, x2, y2 = b['bbox']
                ET.SubElement(bnd, 'xmin').text = str(int(x1))
                ET.SubElement(bnd, 'ymin').text = str(int(y1))
                ET.SubElement(bnd, 'xmax').text = str(int(x2))
                ET.SubElement(bnd, 'ymax').text = str(int(y2))

            xml_path = out_dir / 'Annotations' / (path.stem + '.xml')
            with open(xml_path, 'w') as f:
                f.write(minidom.parseString(ET.tostring(root)).toprettyxml(indent='  '))

    def _export_coco(self, out_dir, dataset):
        out_dir.mkdir(parents=True, exist_ok=True)
        images_dir = out_dir / 'images'
        images_dir.mkdir(exist_ok=True)

        classes = self._class_names()
        label_to_cat = {name: i + 1 for i, name in enumerate(classes)}

        coco = {
            'info': {'description': 'Exported from desktop_annotator.py'},
            'licenses': [],
            'images': [],
            'annotations': [],
            'categories': [
                {'id': cid, 'name': name, 'supercategory': 'object'}
                for name, cid in label_to_cat.items()
            ],
        }

        ann_id = 1
        for img_id, (path, data) in enumerate(dataset, start=1):
            shutil.copy(path, images_dir / path.name)
            coco['images'].append({
                'id': img_id, 'file_name': path.name,
                'width': data['width'], 'height': data['height'],
            })
            for b in data['boxes']:
                x1, y1, x2, y2 = b['bbox']
                entry = {
                    'id': ann_id,
                    'image_id': img_id,
                    'category_id': label_to_cat[b['label']],
                    'bbox': [x1, y1, x2 - x1, y2 - y1],
                    'area': (x2 - x1) * (y2 - y1),
                    'iscrowd': 0,
                }
                if b.get('type') == 'four_point' and b.get('points'):
                    entry['segmentation'] = [[c for p in b['points'] for c in p]]
                else:
                    entry['segmentation'] = []
                coco['annotations'].append(entry)
                ann_id += 1

        with open(out_dir / 'annotations.json', 'w') as f:
            json.dump(coco, f, indent=2)

    def _export_pairs_csv(self, out_dir, dataset):
        out_dir.mkdir(parents=True, exist_ok=True)
        with open(out_dir / 'annotations.csv', 'w') as f:
            f.write('filename,label,type,x1,y1,x2,y2,points,width,height\n')
            for path, data in dataset:
                for b in data['boxes']:
                    x1, y1, x2, y2 = b['bbox']
                    pts = json.dumps(b.get('points')) if b.get('points') else ''
                    f.write(
                        f'{path.name},{b["label"]},{b.get("type", "two_point")},'
                        f'{x1},{y1},{x2},{y2},"{pts}",{data["width"]},{data["height"]}\n'
                    )

    def _export_pairs_json(self, out_dir, dataset):
        out_dir.mkdir(parents=True, exist_ok=True)
        pairs = {}
        for path, data in dataset:
            pairs[path.name] = {
                'width': data['width'],
                'height': data['height'],
                'labels': sorted({b['label'] for b in data['boxes']}),
                'boxes': data['boxes'],
            }
        with open(out_dir / 'annotations_pairs.json', 'w') as f:
            json.dump(pairs, f, indent=2)


def _point_in_polygon(pt, poly):
    x, y = pt
    inside = False
    n = len(poly)
    for i in range(n):
        x1, y1 = poly[i]
        x2, y2 = poly[(i + 1) % n]
        if ((y1 > y) != (y2 > y)) and (x < (x2 - x1) * (y - y1) / (y2 - y1 + 1e-12) + x1):
            inside = not inside
    return inside


if __name__ == '__main__':
    Annotator()
