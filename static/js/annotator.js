"use strict";

// =========================================================
// DOM
// =========================================================
const $ = (id) => document.getElementById(id);
const openFolderBtn = $("openFolderBtn");
const folderInput = $("folderInput");
const saveAllBtn = $("saveAllBtn");
const undoBtn = $("undoBtn");
const redoBtn = $("redoBtn");
const previousBtn = $("previousBtn");
const nextBtn = $("nextBtn");
const exportBtn = $("exportBtn");
const zoomOutBtn = $("zoomOutBtn");
const zoomInBtn = $("zoomInBtn");
const fitBtn = $("fitBtn");
const zoomInfo = $("zoomInfo");
const imageInfo = $("imageInfo");
const emptyState = $("emptyState");
const canvasScroll = $("canvasScroll");
const canvas = $("annotationCanvas");
const ctx = canvas.getContext("2d");
const imageList = $("imageList");
const labelList = $("labelList");
const boxList = $("boxList");
const labelSelect = $("labelSelect");
const newLabelBtn = $("newLabelBtn");
const deleteLabelBtn = $("deleteLabelBtn");
const deleteBoxBtn = $("deleteBoxBtn");
const renameBoxBtn = $("renameBoxBtn");
const statusBar = $("statusBar");
const labelDialogOverlay = $("labelDialogOverlay");
const newLabelName = $("newLabelName");
const newLabelColor = $("newLabelColor");
const newLabelColorValue = $("newLabelColorValue");
const confirmLabelBtn = $("confirmLabelBtn");
const cancelLabelBtn = $("cancelLabelBtn");

// =========================================================
// State
// =========================================================
const IMAGE_EXTENSIONS = /\.(jpg|jpeg|png|bmp|webp|tif|tiff)$/i;
const PALETTE = ["#e6194b","#3cb44b","#4363d8","#f58231","#911eb4","#46f0f0","#f032e6","#bcf60c","#fabed4","#008080","#e6beff","#9a6324","#800000","#aaffc3","#808000"];
const MIN_ZOOM = 0.1;
const MAX_ZOOM = 8;
const HANDLE_RADIUS = 6;
const HIT_RADIUS = 8;
const HISTORY_LIMIT = 50;

let selectedFiles = [];
let images = [];
let currentImageIndex = -1;
let currentImage = null;
let currentImageFile = null;
let imageWidth = 0;
let imageHeight = 0;
let baseScale = 1;
let zoom = 1;
let labels = [];
let currentLabel = "";
let boxes = [];
let selectedBoxIndex = -1;
let pendingPoints = [];
let mouseImagePosition = null;
let drawMode = "two_point";
let workspaceId = null;
let workspaceAnnotations = {};
let undoStack = [];
let redoStack = [];
let dragMode = null;
let dragStart = null;
let dragOriginalBox = null;
let dragHistoryState = null;
let resizeHandle = null;
let selectedVertex = -1;
let dragChanged = false;

// =========================================================
// Helpers
// =========================================================
function clamp(v, min, max) { return Math.max(min, Math.min(max, v)); }
function deepClone(v) { return JSON.parse(JSON.stringify(v)); }
function getScale() { return baseScale * zoom; }
function getLabelColor(name) { return labels.find(l => l.name === name)?.color || "#ffffff"; }
function filePath(file) { return file.webkitRelativePath || file.name; }
function fileDir(path) { const i = path.lastIndexOf("/"); return i >= 0 ? path.slice(0, i + 1) : ""; }
function fileStem(name) { const i = name.lastIndexOf("."); return i > 0 ? name.slice(0, i) : name; }
function simpleHash(text) {
    let h = 2166136261;
    for (let i = 0; i < text.length; i++) {
        h ^= text.charCodeAt(i);
        h = Math.imul(h, 16777619);
    }
    return (h >>> 0).toString(36);
}
function buildWorkspaceId() {
    const signature = images.map(r => `${r.relativePath}:${r.file.size}:${r.file.lastModified}`).join("|");
    return `workspace_${simpleHash(signature)}`;
}
function currentRecord() { return currentImageIndex >= 0 ? images[currentImageIndex] : null; }

// =========================================================
// IndexedDB
// =========================================================
const DB_NAME = "imageAnnotatorDB";
const DB_VERSION = 1;
const STORE_NAME = "workspaces";

function openDatabase() {
    return new Promise((resolve, reject) => {
        const req = indexedDB.open(DB_NAME, DB_VERSION);
        req.onupgradeneeded = (e) => {
            const db = e.target.result;
            if (!db.objectStoreNames.contains(STORE_NAME)) {
                db.createObjectStore(STORE_NAME, { keyPath: "id" });
            }
        };
        req.onsuccess = () => resolve(req.result);
        req.onerror = () => reject(req.error);
    });
}
async function getWorkspace(id) {
    const db = await openDatabase();
    return new Promise((resolve, reject) => {
        const req = db.transaction(STORE_NAME, "readonly").objectStore(STORE_NAME).get(id);
        req.onsuccess = () => resolve(req.result || null);
        req.onerror = () => reject(req.error);
    });
}
async function saveWorkspace() {
    if (!workspaceId) return;
    const db = await openDatabase();
    const data = { id: workspaceId, labels: deepClone(labels), annotations: deepClone(workspaceAnnotations), updatedAt: new Date().toISOString() };
    return new Promise((resolve, reject) => {
        const req = db.transaction(STORE_NAME, "readwrite").objectStore(STORE_NAME).put(data);
        req.onsuccess = () => resolve(true);
        req.onerror = () => reject(req.error);
    });
}

// =========================================================
// Folder opening - broad browser support
// =========================================================
openFolderBtn.addEventListener("click", () => {
    // Reset allows choosing the same folder again too.
    folderInput.value = "";
    folderInput.click();
});

folderInput.addEventListener("change", async (event) => {
    try {
        await openSelectedFolder(Array.from(event.target.files || []));
    } catch (error) {
        console.error("Open folder failed:", error);
        alert(`Could not open this folder.\n\n${error.message || error}`);
    }
});

async function openSelectedFolder(files) {
    resetRuntimeForNewFolder();
    selectedFiles = files;
    if (!selectedFiles.length) return;

    images = selectedFiles
        .filter(f => IMAGE_EXTENSIONS.test(f.name))
        .map(f => ({ file: f, name: f.name, relativePath: filePath(f), count: 0 }))
        .sort((a,b) => a.relativePath.localeCompare(b.relativePath, undefined, { numeric:true, sensitivity:"base" }));

    if (!images.length) {
        alert("No supported images were found in this folder.");
        resetApplication();
        return;
    }

    workspaceId = buildWorkspaceId();
    await loadWorkspace();
    currentImageIndex = 0;
    renderImageList();
    refreshLabelWidgets();
    updateNavigation();
    await loadCurrentImage();
}

function resetRuntimeForNewFolder() {
    currentImage = null;
    currentImageFile = null;
    currentImageIndex = -1;
    imageWidth = imageHeight = 0;
    boxes = [];
    selectedBoxIndex = -1;
    pendingPoints = [];
    mouseImagePosition = null;
    workspaceId = null;
    workspaceAnnotations = {};
    labels = [];
    currentLabel = "";
    resetHistory();
}

async function loadWorkspace() {
    let saved = null;
    try { saved = await getWorkspace(workspaceId); }
    catch (e) { console.warn("IndexedDB unavailable; using selected folder data only.", e); }

    if (saved) {
        labels = Array.isArray(saved.labels) ? saved.labels : [];
        workspaceAnnotations = saved.annotations || {};
    } else {
        labels = [];
        workspaceAnnotations = {};
        await importExistingFolderData();
    }

    if (!labels.length) labels = [{ name:"object", color:PALETTE[0] }];
    currentLabel = labels[0].name;
    updateAllImageCounts();
    try { await saveWorkspace(); } catch (e) { console.warn("Initial autosave failed", e); }
}

async function importExistingFolderData() {
    const labelsFile = selectedFiles.find(f => f.name === "_annotator_labels.json");
    if (labelsFile) {
        try {
            const raw = JSON.parse(await labelsFile.text());
            if (Array.isArray(raw)) labels = raw;
        } catch (e) { console.warn("Could not read _annotator_labels.json", e); }
    }

    const byPath = new Map(selectedFiles.map(f => [filePath(f), f]));
    for (const img of images) {
        const expected = fileDir(img.relativePath) + fileStem(img.name) + ".json";
        const f = byPath.get(expected);
        if (!f) continue;
        try {
            const raw = JSON.parse(await f.text());
            if (raw && Array.isArray(raw.boxes)) {
                workspaceAnnotations[img.relativePath] = {
                    image: img.name,
                    width: Number(raw.width) || 0,
                    height: Number(raw.height) || 0,
                    boxes: raw.boxes
                };
            }
        } catch (e) { console.warn(`Could not read ${expected}`, e); }
    }
}

function updateAllImageCounts() {
    for (const r of images) {
        const a = workspaceAnnotations[r.relativePath];
        r.count = Array.isArray(a?.boxes) ? a.boxes.length : 0;
    }
}

// =========================================================
// Image loading
// =========================================================
function loadImageFromFile(file) {
    return new Promise((resolve, reject) => {
        const img = new Image();
        const url = URL.createObjectURL(file);
        img.onload = () => { URL.revokeObjectURL(url); resolve(img); };
        img.onerror = () => { URL.revokeObjectURL(url); reject(new Error(`Image load failed: ${file.name}`)); };
        img.src = url;
    });
}

async function loadCurrentImage() {
    const record = currentRecord();
    if (!record) return;
    currentImageFile = record.file;
    currentImage = await loadImageFromFile(record.file);
    imageWidth = currentImage.naturalWidth;
    imageHeight = currentImage.naturalHeight;
    const annotation = workspaceAnnotations[record.relativePath];
    boxes = Array.isArray(annotation?.boxes) ? deepClone(annotation.boxes) : [];
    selectedBoxIndex = -1;
    selectedVertex = -1;
    resizeHandle = null;
    dragMode = null;
    pendingPoints = [];
    mouseImagePosition = null;
    resetHistory();
    emptyState.hidden = true;
    canvasScroll.hidden = false;
    fitToWindow();
    renderBoxList();
    updateImageListSelection();
    updateNavigation();
    updateStatus();
}

async function saveCurrentAnnotation() {
    const record = currentRecord();
    if (!record) return;
    workspaceAnnotations[record.relativePath] = {
        image: record.name,
        width: imageWidth,
        height: imageHeight,
        boxes: deepClone(boxes)
    };
    record.count = boxes.length;
    try { await saveWorkspace(); } catch (e) { console.warn("Autosave failed", e); }
    renderImageList();
    updateImageListSelection();
    updateStatus();
}

saveAllBtn.addEventListener("click", async () => {
    await saveCurrentAnnotation();
    try {
        await saveWorkspace();
        alert("Annotations saved in this browser.");
    } catch (e) {
        alert("Browser autosave is unavailable. Please export your work before closing.");
    }
});

// =========================================================
// Labels
// =========================================================
function refreshLabelWidgets() {
    labelSelect.innerHTML = "";
    labelList.innerHTML = "";
    if (!labels.length) {
        currentLabel = "";
        labelSelect.disabled = true;
        deleteLabelBtn.disabled = true;
        labelList.innerHTML = '<p class="empty-text">No labels</p>';
        return;
    }
    if (!labels.some(l => l.name === currentLabel)) currentLabel = labels[0].name;
    for (const label of labels) {
        const opt = document.createElement("option");
        opt.value = label.name; opt.textContent = label.name; labelSelect.appendChild(opt);
        const item = document.createElement("div");
        item.className = "label-list-item" + (label.name === currentLabel ? " active" : "");
        const swatch = document.createElement("span"); swatch.className = "label-color"; swatch.style.backgroundColor = label.color;
        const text = document.createElement("span"); text.textContent = label.name;
        item.append(swatch, text);
        item.addEventListener("click", () => selectLabel(label.name));
        labelList.appendChild(item);
    }
    labelSelect.value = currentLabel;
    labelSelect.disabled = images.length === 0;
    deleteLabelBtn.disabled = false;
}
function selectLabel(name) {
    if (!labels.some(l => l.name === name)) return;
    currentLabel = name;
    refreshLabelWidgets(); redrawCanvas(); updateStatus();
}
labelSelect.addEventListener("change", () => selectLabel(labelSelect.value));
newLabelBtn.addEventListener("click", () => {
    const c = PALETTE[labels.length % PALETTE.length];
    newLabelName.value = "";
    newLabelColor.value = c;
    newLabelColorValue.textContent = c;
    labelDialogOverlay.hidden = false;
    setTimeout(() => newLabelName.focus(), 0);
});
newLabelColor.addEventListener("input", () => newLabelColorValue.textContent = newLabelColor.value);
cancelLabelBtn.addEventListener("click", () => labelDialogOverlay.hidden = true);
confirmLabelBtn.addEventListener("click", addNewLabel);
newLabelName.addEventListener("keydown", e => { if (e.key === "Enter") addNewLabel(); });
async function addNewLabel() {
    const name = newLabelName.value.trim();
    if (!name) return;
    if (labels.some(l => l.name === name)) { alert(`Label "${name}" already exists.`); return; }
    labels.push({ name, color:newLabelColor.value });
    currentLabel = name;
    labelDialogOverlay.hidden = true;
    refreshLabelWidgets();
    await saveWorkspace();
    redrawCanvas();
}
deleteLabelBtn.addEventListener("click", async () => {
    if (!currentLabel) return;
    const used = Object.values(workspaceAnnotations).some(a => Array.isArray(a.boxes) && a.boxes.some(b => b.label === currentLabel));
    if (used && !confirm(`"${currentLabel}" is used by annotations. Delete the label anyway?`)) return;
    labels = labels.filter(l => l.name !== currentLabel);
    if (!labels.length) labels = [{ name:"object", color:PALETTE[0] }];
    currentLabel = labels[0].name;
    refreshLabelWidgets();
    await saveWorkspace();
    redrawCanvas();
});

// =========================================================
// History
// =========================================================
function resetHistory() { undoStack = []; redoStack = []; updateHistoryButtons(); }
function trimHistory(stack) { while (stack.length > HISTORY_LIMIT) stack.shift(); }
function pushUndoState(state = boxes) {
    undoStack.push(deepClone(state)); trimHistory(undoStack); redoStack = []; updateHistoryButtons();
}
function updateHistoryButtons() { undoBtn.disabled = undoStack.length === 0; redoBtn.disabled = redoStack.length === 0; }
async function undo() {
    if (!undoStack.length || currentImageIndex < 0) return;
    redoStack.push(deepClone(boxes)); trimHistory(redoStack);
    boxes = deepClone(undoStack.pop());
    selectedBoxIndex = -1;
    await saveCurrentAnnotation(); renderBoxList(); redrawCanvas(); updateHistoryButtons();
}
async function redo() {
    if (!redoStack.length || currentImageIndex < 0) return;
    undoStack.push(deepClone(boxes)); trimHistory(undoStack);
    boxes = deepClone(redoStack.pop());
    selectedBoxIndex = -1;
    await saveCurrentAnnotation(); renderBoxList(); redrawCanvas(); updateHistoryButtons();
}
undoBtn.addEventListener("click", undo);
redoBtn.addEventListener("click", redo);

// =========================================================
// Drawing helpers
// =========================================================
function createTwoPointBox(p1,p2) {
    const x1=Math.min(p1.x,p2.x), y1=Math.min(p1.y,p2.y), x2=Math.max(p1.x,p2.x), y2=Math.max(p1.y,p2.y);
    if (x2-x1 < 1 || y2-y1 < 1) return null;
    return { label:currentLabel, type:"two_point", bbox:[x1,y1,x2,y2] };
}
function createFourPointBox(points) {
    const xs=points.map(p=>p.x), ys=points.map(p=>p.y);
    return { label:currentLabel, type:"four_point", bbox:[Math.min(...xs),Math.min(...ys),Math.max(...xs),Math.max(...ys)], points:points.map(p=>[p.x,p.y]) };
}
function eventToImageCoordinates(event) {
    const rect=canvas.getBoundingClientRect();
    return { x:clamp((event.clientX-rect.left)/getScale(),0,imageWidth), y:clamp((event.clientY-rect.top)/getScale(),0,imageHeight) };
}
canvas.addEventListener("click", async (event) => {
    if (!currentImage || drawMode === "select") return;
    if (!currentLabel) { alert("Please create or select a label first."); return; }
    const p=eventToImageCoordinates(event);
    pendingPoints.push(p);
    const needed = drawMode === "two_point" ? 2 : 4;
    if (pendingPoints.length === needed) {
        const newBox = drawMode === "two_point" ? createTwoPointBox(pendingPoints[0],pendingPoints[1]) : createFourPointBox(pendingPoints);
        pendingPoints=[];
        if (newBox) {
            pushUndoState();
            boxes.push(newBox);
            selectedBoxIndex=boxes.length-1;
            await saveCurrentAnnotation();
            renderBoxList();
        }
    }
    redrawCanvas();
});

function pointInPolygon(point, polygon) {
    let inside=false; const x=point.x,y=point.y;
    for (let i=0,j=polygon.length-1;i<polygon.length;j=i++) {
        const xi=polygon[i][0], yi=polygon[i][1], xj=polygon[j][0], yj=polygon[j][1];
        const intersects=((yi>y)!=(yj>y)) && (x < (xj-xi)*(y-yi)/((yj-yi)||Number.EPSILON)+xi);
        if (intersects) inside=!inside;
    }
    return inside;
}
function hitTestBox(x,y) {
    for (let i=boxes.length-1;i>=0;i--) {
        const b=boxes[i];
        if (b.type === "four_point" && Array.isArray(b.points)) {
            if (pointInPolygon({x,y},b.points)) return i;
        } else {
            const [x1,y1,x2,y2]=b.bbox;
            if (x>=x1&&x<=x2&&y>=y1&&y<=y2) return i;
        }
    }
    return -1;
}
function hitTestHandle(box,x,y) {
    const radius=HIT_RADIUS/getScale();
    if (box.type === "four_point" && Array.isArray(box.points)) {
        for (let i=0;i<box.points.length;i++) {
            const p=box.points[i];
            if (Math.abs(p[0]-x)<=radius && Math.abs(p[1]-y)<=radius) return i;
        }
        return -1;
    }
    const [x1,y1,x2,y2]=box.bbox;
    const corners={nw:[x1,y1],ne:[x2,y1],sw:[x1,y2],se:[x2,y2]};
    for (const [name,p] of Object.entries(corners)) {
        if (Math.abs(p[0]-x)<=radius && Math.abs(p[1]-y)<=radius) return name;
    }
    return null;
}

// =========================================================
// Select / move / resize / vertices
// =========================================================
canvas.addEventListener("mousedown", (event) => {
    if (drawMode !== "select" || !currentImage || event.button !== 0) return;
    const {x,y}=eventToImageCoordinates(event);
    dragChanged=false; dragHistoryState=null;

    if (selectedBoxIndex>=0 && selectedBoxIndex<boxes.length) {
        const selected=boxes[selectedBoxIndex];
        const handle=hitTestHandle(selected,x,y);
        if (selected.type === "four_point" && handle !== -1 && handle !== null) {
            dragMode="vertex"; selectedVertex=handle; dragStart={x,y}; dragOriginalBox=deepClone(selected); dragHistoryState=deepClone(boxes); canvas.style.cursor="grabbing"; return;
        }
        if (selected.type !== "four_point" && handle) {
            dragMode="resize"; resizeHandle=handle; dragStart={x,y}; dragOriginalBox=deepClone(selected); dragHistoryState=deepClone(boxes); updateResizeCursor(handle); return;
        }
    }

    const index=hitTestBox(x,y);
    selectedBoxIndex=index; selectedVertex=-1; resizeHandle=null;
    if (index>=0) {
        dragMode="move"; dragStart={x,y}; dragOriginalBox=deepClone(boxes[index]); dragHistoryState=deepClone(boxes); canvas.style.cursor="move";
    } else {
        dragMode=null; dragStart=null; dragOriginalBox=null; canvas.style.cursor="default";
    }
    renderBoxList(); redrawCanvas();
});

canvas.addEventListener("mousemove", (event) => {
    if (!currentImage) return;
    const p=eventToImageCoordinates(event); mouseImagePosition=p;
    if (drawMode === "select" && dragMode && selectedBoxIndex>=0) {
        const b=boxes[selectedBoxIndex], o=dragOriginalBox;
        if (!o) return;
        if (dragMode === "move") {
            const dx=p.x-dragStart.x, dy=p.y-dragStart.y;
            b.bbox=[o.bbox[0]+dx,o.bbox[1]+dy,o.bbox[2]+dx,o.bbox[3]+dy];
            if (Array.isArray(o.points)) b.points=o.points.map(q=>[q[0]+dx,q[1]+dy]);
            dragChanged=true;
        } else if (dragMode === "resize") {
            let [x1,y1,x2,y2]=o.bbox;
            if (resizeHandle.includes("n")) y1=p.y;
            if (resizeHandle.includes("s")) y2=p.y;
            if (resizeHandle.includes("w")) x1=p.x;
            if (resizeHandle.includes("e")) x2=p.x;
            b.bbox=[Math.min(x1,x2),Math.min(y1,y2),Math.max(x1,x2),Math.max(y1,y2)];
            dragChanged=true;
        } else if (dragMode === "vertex") {
            const pts=deepClone(o.points); pts[selectedVertex]=[p.x,p.y]; b.points=pts;
            const xs=pts.map(q=>q[0]), ys=pts.map(q=>q[1]); b.bbox=[Math.min(...xs),Math.min(...ys),Math.max(...xs),Math.max(...ys)];
            dragChanged=true;
        }
        redrawCanvas(); return;
    }
    if (pendingPoints.length) redrawCanvas();
    if (drawMode === "select") updateSelectCursor(p.x,p.y);
});

window.addEventListener("mouseup", async () => {
    if (!dragMode) return;
    const changed=dragChanged, oldState=dragHistoryState;
    dragMode=null; resizeHandle=null; dragOriginalBox=null; dragHistoryState=null; dragStart=null; dragChanged=false;
    canvas.style.cursor=drawMode === "select" ? "default" : "crosshair";
    if (changed && oldState) { pushUndoState(oldState); await saveCurrentAnnotation(); }
    renderBoxList(); redrawCanvas();
});
function updateResizeCursor(handle) { canvas.style.cursor=(handle==="nw"||handle==="se")?"nwse-resize":"nesw-resize"; }
function updateSelectCursor(x,y) {
    if (dragMode) return;
    if (selectedBoxIndex>=0 && selectedBoxIndex<boxes.length) {
        const b=boxes[selectedBoxIndex], h=hitTestHandle(b,x,y);
        if (b.type === "four_point" && h !== -1 && h !== null) { canvas.style.cursor="grab"; return; }
        if (b.type !== "four_point" && h) { updateResizeCursor(h); return; }
    }
    canvas.style.cursor=hitTestBox(x,y)>=0?"move":"default";
}
canvas.addEventListener("mouseleave",()=>{ mouseImagePosition=null; if(pendingPoints.length) redrawCanvas(); });
canvas.addEventListener("contextmenu",e=>{ e.preventDefault(); cancelPendingDrawing(); });

// =========================================================
// Modes
// =========================================================
document.querySelectorAll('input[name="drawMode"]').forEach(input => input.addEventListener("change",()=>{
    if (!input.checked) return;
    drawMode=input.value;
    cancelPendingDrawing();
    dragMode=null; resizeHandle=null; selectedVertex=-1;
    canvas.style.cursor=drawMode === "select" ? "default" : "crosshair";
    updateStatus();
}));
function cancelPendingDrawing(){ pendingPoints=[]; mouseImagePosition=null; redrawCanvas(); }

// =========================================================
// Delete / rename box
// =========================================================
deleteBoxBtn.addEventListener("click", deleteSelectedBox);
async function deleteSelectedBox() {
    if (selectedBoxIndex<0 || selectedBoxIndex>=boxes.length) return;
    pushUndoState();
    boxes.splice(selectedBoxIndex,1); selectedBoxIndex=-1; selectedVertex=-1;
    await saveCurrentAnnotation(); renderBoxList(); redrawCanvas();
}
renameBoxBtn.addEventListener("click", renameSelectedBox);
async function renameSelectedBox() {
    if (selectedBoxIndex<0 || selectedBoxIndex>=boxes.length) return;
    const current=boxes[selectedBoxIndex].label;
    const answer=prompt(`Label for this box\n\nExisting labels: ${labels.map(l=>l.name).join(", ")}`,current);
    if (answer===null) return;
    const name=answer.trim(); if(!name||name===current) return;
    if(!labels.some(l=>l.name===name)) labels.push({name,color:PALETTE[labels.length%PALETTE.length]});
    pushUndoState(); boxes[selectedBoxIndex].label=name;
    refreshLabelWidgets(); await saveCurrentAnnotation(); await saveWorkspace(); renderBoxList(); redrawCanvas();
}

// =========================================================
// Drawing canvas
// =========================================================
function redrawCanvas() {
    if (!currentImage) return;
    const scale=getScale();
    const dw=Math.max(1,Math.round(imageWidth*scale)), dh=Math.max(1,Math.round(imageHeight*scale));
    canvas.width=dw; canvas.height=dh; canvas.style.width=`${dw}px`; canvas.style.height=`${dh}px`;
    ctx.clearRect(0,0,dw,dh); ctx.imageSmoothingEnabled=true; ctx.imageSmoothingQuality="high";
    ctx.drawImage(currentImage,0,0,dw,dh);
    boxes.forEach((b,i)=>drawBox(b,i===selectedBoxIndex));
    drawPendingDrawing();
    zoomInfo.textContent=`${Math.round(scale*100)}%`;
    updateStatus();
}
function drawBox(box,selected) {
    const s=getScale(), color=selected?"#ffff00":getLabelColor(box.label);
    ctx.save(); ctx.strokeStyle=color; ctx.lineWidth=selected?3:2;
    if (box.type === "four_point" && Array.isArray(box.points)) {
        ctx.beginPath(); box.points.forEach((p,i)=>{ const x=p[0]*s,y=p[1]*s; i?ctx.lineTo(x,y):ctx.moveTo(x,y); }); ctx.closePath(); ctx.stroke();
        if(selected) box.points.forEach(p=>drawHandle(p[0]*s,p[1]*s));
    } else {
        const [x1,y1,x2,y2]=box.bbox; const a=x1*s,b=y1*s,c=x2*s,d=y2*s;
        ctx.strokeRect(a,b,c-a,d-b); if(selected){drawHandle(a,b);drawHandle(c,b);drawHandle(a,d);drawHandle(c,d);}
    }
    drawBoxLabel(box,color); ctx.restore();
}
function drawHandle(x,y){ ctx.save(); ctx.fillStyle="#ffff00";ctx.strokeStyle="#000";ctx.lineWidth=1;ctx.fillRect(x-HANDLE_RADIUS,y-HANDLE_RADIUS,HANDLE_RADIUS*2,HANDLE_RADIUS*2);ctx.strokeRect(x-HANDLE_RADIUS,y-HANDLE_RADIUS,HANDLE_RADIUS*2,HANDLE_RADIUS*2);ctx.restore(); }
function drawBoxLabel(box,color){
    const s=getScale(),x=box.bbox[0]*s,y=box.bbox[1]*s,text=box.label; ctx.font="13px Arial";const w=ctx.measureText(text).width+8,h=18,top=Math.max(0,y-h);
    ctx.fillStyle=color;ctx.fillRect(x,top,w,h);ctx.fillStyle="#000";ctx.textBaseline="middle";ctx.fillText(text,x+4,top+h/2);
}
function drawPendingDrawing(){
    if(!pendingPoints.length)return; const s=getScale();ctx.save();
    pendingPoints.forEach(p=>{ctx.beginPath();ctx.arc(p.x*s,p.y*s,4,0,Math.PI*2);ctx.fillStyle="#ffff00";ctx.fill();});
    if(!mouseImagePosition){ctx.restore();return;}
    ctx.strokeStyle=getLabelColor(currentLabel);ctx.lineWidth=2;ctx.setLineDash([5,3]);const mx=mouseImagePosition.x*s,my=mouseImagePosition.y*s;
    if(drawMode==="two_point"&&pendingPoints.length===1){const p=pendingPoints[0];ctx.strokeRect(p.x*s,p.y*s,mx-p.x*s,my-p.y*s);}
    if(drawMode==="four_point"){
        const preview=[...pendingPoints.map(p=>({x:p.x*s,y:p.y*s})),{x:mx,y:my}];ctx.beginPath();ctx.moveTo(preview[0].x,preview[0].y);for(let i=1;i<preview.length;i++)ctx.lineTo(preview[i].x,preview[i].y);ctx.stroke();
    }
    ctx.restore();
}

// =========================================================
// Lists/navigation
// =========================================================
function renderImageList(){
    imageList.innerHTML="";
    if(!images.length){imageList.innerHTML='<p class="empty-text">No images</p>';return;}
    images.forEach((r,i)=>{const item=document.createElement("div");item.className="image-list-item"+(i===currentImageIndex?" active":"");item.textContent=`${r.name} (${r.count})`;item.title=r.relativePath;item.addEventListener("click",async()=>{if(i===currentImageIndex)return;currentImageIndex=i;await loadCurrentImage();});imageList.appendChild(item);});
}
function updateImageListSelection(){
    imageList.querySelectorAll(".image-list-item").forEach((el,i)=>el.classList.toggle("active",i===currentImageIndex));
    imageList.querySelector(".image-list-item.active")?.scrollIntoView({block:"nearest"});
}
function renderBoxList(){
    boxList.innerHTML="";
    if(!boxes.length){boxList.innerHTML='<p class="empty-text">No boxes</p>';deleteBoxBtn.disabled=true;renameBoxBtn.disabled=true;return;}
    boxes.forEach((b,i)=>{const item=document.createElement("div");item.className="box-list-item"+(i===selectedBoxIndex?" active":"");item.textContent=`${b.label} [${b.type==="four_point"?"quad":"box"}]`;item.addEventListener("click",()=>{selectedBoxIndex=i;renderBoxList();redrawCanvas();});item.addEventListener("dblclick",async()=>{selectedBoxIndex=i;await renameSelectedBox();});boxList.appendChild(item);});
    const ok=selectedBoxIndex>=0&&selectedBoxIndex<boxes.length;deleteBoxBtn.disabled=!ok;renameBoxBtn.disabled=!ok;
}
previousBtn.addEventListener("click",async()=>{if(currentImageIndex<=0)return;currentImageIndex--;await loadCurrentImage();});
nextBtn.addEventListener("click",async()=>{if(currentImageIndex>=images.length-1)return;currentImageIndex++;await loadCurrentImage();});

// =========================================================
// Zoom
// =========================================================
function fitToWindow(){
    if(!currentImage)return;const w=Math.max(canvasScroll.clientWidth-2,200),h=Math.max(canvasScroll.clientHeight-2,200);baseScale=Math.min(w/imageWidth,h/imageHeight,1);zoom=1;redrawCanvas();canvasScroll.scrollLeft=0;canvasScroll.scrollTop=0;
}
function adjustZoom(factor){if(!currentImage)return;zoom=clamp(zoom*factor,MIN_ZOOM,MAX_ZOOM);redrawCanvas();}
zoomOutBtn.addEventListener("click",()=>adjustZoom(.8));zoomInBtn.addEventListener("click",()=>adjustZoom(1.25));fitBtn.addEventListener("click",fitToWindow);
canvasScroll.addEventListener("wheel",e=>{if(e.ctrlKey){e.preventDefault();adjustZoom(e.deltaY<0?1.1:.9);}else if(e.shiftKey){e.preventDefault();canvasScroll.scrollLeft+=e.deltaY;}},{passive:false});

// =========================================================
// UI state/status
// =========================================================
function updateNavigation(){
    const has=images.length>0;
    previousBtn.disabled=!has||currentImageIndex<=0;nextBtn.disabled=!has||currentImageIndex>=images.length-1;
    zoomOutBtn.disabled=!has;zoomInBtn.disabled=!has;fitBtn.disabled=!has;newLabelBtn.disabled=!has;saveAllBtn.disabled=!has;labelSelect.disabled=!has||!labels.length;exportBtn.disabled=!has;
    imageInfo.textContent=has?`${currentImageIndex+1} / ${images.length}`:"0 / 0";updateHistoryButtons();
}
function updateStatus(){
    if(!currentImage){statusBar.textContent="Open a folder to begin";return;}
    statusBar.textContent=`[${currentImageIndex+1}/${images.length}] ${images[currentImageIndex].name} | ${imageWidth}x${imageHeight} | Zoom ${Math.round(getScale()*100)}% | Boxes: ${boxes.length} | Mode: ${drawMode} | Undo: ${undoStack.length} | Redo: ${redoStack.length} | Auto-save: On`;
}
function resetApplication(){
    resetRuntimeForNewFolder(); selectedFiles=[];images=[];canvas.width=1;canvas.height=1;canvasScroll.hidden=true;emptyState.hidden=false;renderImageList();renderBoxList();refreshLabelWidgets();updateNavigation();updateStatus();
}

// =========================================================
// Keyboard
// =========================================================
function isTyping(){const e=document.activeElement;return !!e && ["INPUT","TEXTAREA","SELECT"].includes(e.tagName);}
document.addEventListener("keydown",async e=>{
    if(e.key==="Escape"){if(!labelDialogOverlay.hidden)labelDialogOverlay.hidden=true;else cancelPendingDrawing();return;}
    if(isTyping())return;
    if(e.ctrlKey&&!e.shiftKey&&e.key.toLowerCase()==="z"){e.preventDefault();await undo();return;}
    if((e.ctrlKey&&e.key.toLowerCase()==="y")||(e.ctrlKey&&e.shiftKey&&e.key.toLowerCase()==="z")){e.preventDefault();await redo();return;}
    if(e.key==="Delete"||e.key==="Backspace"){e.preventDefault();await deleteSelectedBox();return;}
    if(e.ctrlKey&&e.key.toLowerCase()==="s"){e.preventDefault();saveAllBtn.click();return;}
    if(e.key==="PageUp"){e.preventDefault();previousBtn.click();return;}
    if(e.key==="PageDown"){e.preventDefault();nextBtn.click();}
});
let resizeTimer=null;window.addEventListener("resize",()=>{if(!currentImage)return;clearTimeout(resizeTimer);resizeTimer=setTimeout(fitToWindow,150);});

// =========================================================
// Export API - explicit interface for exporter.js
// =========================================================
window.annotatorApp = {
    async saveCurrent(){ await saveCurrentAnnotation(); },
    getImages(){ return images; },
    getLabels(){ return deepClone(labels); },
    getAnnotations(){ return deepClone(workspaceAnnotations); },
    getDataset(){
        return images.flatMap(record=>{
            const data=workspaceAnnotations[record.relativePath];
            if(!data||!Array.isArray(data.boxes)||!data.boxes.length)return [];
            return [{record,data:deepClone(data)}];
        });
    },
    deepClone
};

// Initial
refreshLabelWidgets();renderImageList();renderBoxList();updateNavigation();updateHistoryButtons();updateStatus();
