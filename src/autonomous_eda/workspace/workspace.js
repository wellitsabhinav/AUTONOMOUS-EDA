"use strict";

const $ = id => document.getElementById(id);
const labels = {floorplan:"Floorplan", place:"Placement", cts:"CTS", grt:"Global route",
    route:"Detailed route", finish:"Final OpenDB", gds:"Final GDS", "pad-ring":"I/O pad ring"};
const palette = ["#5ecbdb", "#aa86f2", "#f6ad68", "#73d79e", "#e47fad", "#6699f2", "#c0c768"];
const stageLabel = id => labels[id] || id;
const state = {bundle:null, stages:new Map(), primary:null, other:null, selection:null,
    shown:new Set(), hiddenLayers:new Set(), center:[0,0], zoom:1, loading:0, console:false,
    measuring:false, ruler:[], timingIndex:0};
const views = [$("layout"), $("comparison")];
const kinds = {cells:"Placed cells", pins:"I/O pins", pads:"Pad-ring cells", guides:"Route guides",
    congestion:"GCell usage / capacity", wires:"Routed wire shapes", polygons:"GDS geometry",
    clock:"Clock pin topology",
    timing:"Reported timing path"};
const element = (tag, value, className) => {
    const node = document.createElement(tag);
    node.textContent = value ?? "";
    if (className) node.className = className;
    return node;
};

async function api(path) {
    const response = await fetch(path);
    if (!response.ok) {
        const data = await response.json().catch(() => ({}));
        throw Error(data.error || "Request failed (" + response.status + ")");
    }
    return response;
}
const json = async path => (await api(path)).json();
const status = message => { $("feedback").textContent = message; };
const stagePath = (id, kind) => "/api/stage/" + encodeURIComponent(id) + "/" + kind;

function index(g) {
    g.pads = g.cells.filter(c => /^PAD/i.test(c[2]) || /corner/i.test(c[0]));
    const padNames = new Set(g.pads.map(c => c[0]));
    g.coreCells = g.cells.filter(c => !padNames.has(c[0]));
    g.cellMap = new Map(g.cells.map(c => [c[0], c]));
    g.pinMap = new Map(g.pins.map(p => [p[0], p]));
    g.netMap = new Map(g.nets.map(n => [n[0], n]));
    g.byNet = new Map();
    g.byCell = new Map();
    for (const term of g.terminals) {
        if (!g.byNet.has(term[0])) g.byNet.set(term[0], []);
        if (!g.byCell.has(term[1])) g.byCell.set(term[1], []);
        g.byNet.get(term[0]).push(term);
        g.byCell.get(term[1]).push(term);
    }
    g.portByNet = new Map();
    for (const port of g.ports) {
        if (!g.portByNet.has(port[0])) g.portByNet.set(port[0], []);
        g.portByNet.get(port[0]).push(port);
    }
    g.clock = g.nets.filter(n => n[1] === "CLOCK");
    g.timing ||= [];
    g.congestion ||= [];
    g.wires ||= [];
    g.layers = [...new Set([...g.pins.map(p => p[2]), ...g.guides.map(p => p[1]), ...g.wires.map(p => p[1]),
        ...g.congestion.map(p => p[0]), ...g.polygons.map(p => p[0])])].sort();
    return g;
}

async function geometry(id) {
    if (!id) return null;
    const g = state.stages.get(id) || index(await json(stagePath(id, "geometry")));
    state.stages.delete(id);
    state.stages.set(id,g);
    while (state.stages.size > 3) state.stages.delete(state.stages.keys().next().value);
    return g;
}
function fit() {
    const frames = [state.primary, state.other].filter(Boolean).map(g => g.bounds.map(n => n / g.dbu));
    if (!frames.length) return;
    const bounds = [Math.min(...frames.map(f => f[0])), Math.min(...frames.map(f => f[1])),
        Math.max(...frames.map(f => f[2])), Math.max(...frames.map(f => f[3]))];
    state.center = [(bounds[0]+bounds[2])/2, (bounds[1]+bounds[3])/2];
    state.zoom = 1;
    drawAll();
}
const center = box => [(box[0]+box[2])/2, (box[1]+box[3])/2];
const inside = (point, box) => point[0] >= box[0] && point[0] <= box[2]
    && point[1] >= box[1] && point[1] <= box[3];
const boxOf = item => item.slice(-4);

function view(canvas, g) {
    const css = canvas.getBoundingClientRect();
    const ratio = window.devicePixelRatio || 1;
    const w = Math.round(css.width * ratio), h = Math.round(css.height * ratio);
    if (canvas.width !== w || canvas.height !== h) {
        canvas.width = w; canvas.height = h;
    }
    const ctx = canvas.getContext("2d");
    const bases = [state.primary, state.other].filter(Boolean).map(item => {
        const b = item.bounds;
        return Math.max(b[2]-b[0], b[3]-b[1]) / item.dbu;
    });
    const scale = .9 * Math.min(w,h) / Math.max(...bases, .01) * state.zoom / g.dbu;
    const [cx,cy] = state.center.map(n => n * g.dbu);
    const pixel = ([x,y]) => [w/2 + (x-cx)*scale, h/2 - (y-cy)*scale];
    const world = (x,y) => [cx+(x*ratio-w/2)/scale, cy-(y*ratio-h/2)/scale];
    return {ctx, pixel, world, scale, ratio};
}

function draw(canvas, g) {
    if (!g || !canvas.clientWidth) return;
    const {ctx, pixel, world, ratio} = view(canvas, g);
    ctx.fillStyle = "#0a121e";
    ctx.fillRect(0,0,canvas.width,canvas.height);
    const [left,top] = world(0,0);
    const [right,bottom] = world(canvas.clientWidth,canvas.clientHeight);
    const color = layer => palette[g.layers.indexOf(layer) % palette.length] || "#63b9ce";
    const shown = kind => state.shown.has(kind);
    const layerOn = layer => !state.hiddenLayers.has(layer);
    const box = (bounds, fill, outline) => {
        if (bounds[0]>right || bounds[2]<left || bounds[1]>top || bounds[3]<bottom) return;
        const [x,y] = pixel([bounds[0], bounds[3]]), [xx,yy] = pixel([bounds[2],bounds[1]]);
        if (fill) { ctx.fillStyle = fill; ctx.fillRect(x,y,xx-x,yy-y); }
        if (outline) { ctx.strokeStyle = outline; ctx.strokeRect(x,y,xx-x,yy-y); }
    };
    const line = (a,b,ink) => {
        ctx.beginPath(); ctx.moveTo(...pixel(a)); ctx.lineTo(...pixel(b));
        ctx.strokeStyle = ink; ctx.stroke();
    };
    const point = (p, fill, size) => {
        ctx.beginPath(); ctx.arc(...pixel(p),size*ratio,0,Math.PI*2); ctx.fillStyle=fill; ctx.fill();
    };
    box(g.bounds, "#101d2c", "#6d8298");
    if (shown("cells")) for (const c of g.coreCells) box(boxOf(c), "#254961", "#3d6e84");
    if (shown("polygons")) {
        const paths = new Map();
        for (const [layer,rings] of g.polygons) {
            if (!layerOn(layer)) continue;
            if (!paths.has(layer)) paths.set(layer, []);
            paths.get(layer).push(rings);
        }
        for (const [layer, polygons] of paths) {
            ctx.fillStyle = color(layer); ctx.globalAlpha = .38;
            for (const rings of polygons) {
                ctx.beginPath();
                for (const ring of rings) {
                    if (!ring.length) continue;
                    ctx.moveTo(...pixel(ring[0]));
                    for (const p of ring.slice(1)) ctx.lineTo(...pixel(p));
                    ctx.closePath();
                }
                ctx.fill("evenodd");
            }
            ctx.globalAlpha = 1;
        }
    }
    if (shown("guides")) for (const item of g.guides) {
        if (!layerOn(item[1])) continue;
        const highlight = state.selection?.kind === "net" && state.selection.name === item[0];
        box(boxOf(item), highlight ? color(item[1])+"88" : color(item[1])+"11",
            highlight ? color(item[1]) : null);
    }
    if (shown("wires")) for (const item of g.wires) {
        if (!layerOn(item[1])) continue;
        const highlighted = state.selection?.kind === "net" && state.selection.name === item[0];
        box(boxOf(item),highlighted ? "#ffcf7f" : color(item[1])+"aa",null);
    }
    if (shown("congestion")) for (const item of g.congestion) {
        if (!layerOn(item[0])) continue;
        const density = item[2] > 0 ? item[1] / item[2] : 1;
        box(boxOf(item), "rgba(255,121,69," + Math.min(.85, .04+density*.45) + ")", null);
    }
    if (shown("pins")) for (const p of g.pins) {
        if (layerOn(p[2])) box(boxOf(p),color(p[2]),null);
    }
    if (shown("pads")) for (const p of g.pads) box(boxOf(p),"#638365","#b8f0a7");
    const drawNet = (name, ink) => {
        const terms = (g.byNet.get(name) || []).filter(t => t.length === 7);
        const ports = (g.portByNet.get(name) || []).filter(p => g.pinMap.has(p[1]));
        const driver = terms.filter(t => t[3] === "OUTPUT").map(t => t.slice(5).map(Number));
        const sinks = terms.filter(t => t[3] === "INPUT").map(t => t.slice(5).map(Number));
        for (const p of ports) {
            const target = p[2] === "INPUT" ? driver : sinks;
            target.push(center(boxOf(g.pinMap.get(p[1]))));
        }
        if (driver.length === 1) for (const sink of sinks) line(driver[0],sink,ink);
        for (const pin of driver) point(pin,ink,3);
        for (const sink of sinks) point(sink,ink,2);
    };
    if (shown("clock")) {
        for (const n of g.clock) drawNet(n[0],"#73e5e0a6");
        for (const c of g.cells) {
            if (/(^|__)clkbuf|(^|__)clkinv/i.test(c[1])
                && g.byCell.get(c[0])?.some(t => g.netMap.get(t[0])?.[1] === "CLOCK")) {
                box(boxOf(c),"#68d5d099","#b9fff3");
            }
        }
    }
    const timingPath = g.timing[state.timingIndex];
    if (g === state.primary && shown("timing") && timingPath) {
        let prev = null;
        for (const a of timingPath.anchors) {
            if (prev && a.point) line(prev,a.point,"#ffad68");
            if (a.point) point(a.point,"#ffad68",3);
            prev = a.point;
        }
    }
    const selected = state.selection;
    if (selected?.kind === "net" && g.netMap.has(selected.name)) drawNet(selected.name,"#ffcf7f");
    if (selected?.kind === "cell" && g.cellMap.has(selected.name)) {
        box(boxOf(g.cellMap.get(selected.name)),"#75e9e854","#c2fffc");
    }
    if (selected?.kind === "pin" && g.pinMap.has(selected.name)) {
        box(boxOf(g.pinMap.get(selected.name)), "#ffcf7f88","#ffcf7f");
    }
    const ruler = state.ruler.map(p => p.map(coordinate => coordinate*g.dbu));
    if (ruler.length === 2) line(ruler[0],ruler[1],"#f9d388");
    for (const p of ruler) point(p,"#f9d388",3);
}

let frameRequest = null;
function drawAll() {
    if (frameRequest !== null) return;
    frameRequest = requestAnimationFrame(() => {
        frameRequest = null;
        draw(views[0],state.primary);
        draw(views[1],state.other);
    });
}
const resize = new ResizeObserver(drawAll);
for (const [i, canvas] of views.entries()) {
    resize.observe(canvas);
    const model = () => i === 0 ? state.primary : state.other;
    canvas.addEventListener("wheel", e => {
        const g = model();
        if (!g) return;
        e.preventDefault();
        const p = view(canvas,g).world(e.offsetX,e.offsetY);
        state.zoom = Math.min(150,Math.max(.2,state.zoom*Math.exp(-e.deltaY*.001)));
        const next = view(canvas,g).world(e.offsetX,e.offsetY);
        state.center = state.center.map((v,j) => v+(p[j]-next[j])/g.dbu);
        drawAll();
    }, {passive:false});
    let drag = null;
    canvas.addEventListener("pointerdown", e => {
        if (!model() || e.button !== 0) return;
        canvas.setPointerCapture(e.pointerId);
        drag = {x:e.clientX, y:e.clientY, moved:false};
    });
    canvas.addEventListener("pointermove", e => {
        const g = model(); if (!g) return;
        const transform = view(canvas,g);
        const rect = canvas.getBoundingClientRect();
        if (drag) {
            const dx = e.clientX-drag.x, dy = e.clientY-drag.y;
            if (drag.moved || Math.abs(dx)+Math.abs(dy) > 2) {
                const unit = transform.ratio/(transform.scale*g.dbu);
                state.center[0] -= dx*unit; state.center[1] += dy*unit;
                drag = {x:e.clientX, y:e.clientY, moved:true};
            }
        }
        const point = transform.world(e.clientX-rect.left,e.clientY-rect.top);
        $("coordinates").textContent = point.map(p => (p/g.dbu).toFixed(3)).join(", ") + " µm";
        if (drag?.moved) drawAll();
    });
    canvas.addEventListener("pointerup", e => {
        if (drag && !drag.moved) {
            const g = model(), rect = canvas.getBoundingClientRect();
            const p = view(canvas,g).world(e.clientX-rect.left,e.clientY-rect.top);
            if (state.measuring) {
                if (state.ruler.length === 2) state.ruler = [];
                state.ruler.push(p.map(coordinate => coordinate/g.dbu));
                if (state.ruler.length === 2) {
                    const [a,b] = state.ruler;
                    status(Math.hypot(a[0]-b[0],a[1]-b[1]).toFixed(3)+" µm");
                } else status("Select the end point");
                drawAll();
                drag = null;
                return;
            }
            const pin = g.pins.find(item => !state.hiddenLayers.has(item[2]) && inside(p,boxOf(item)));
            const wire = g.wires.find(item => !state.hiddenLayers.has(item[1]) && inside(p,boxOf(item)));
            const cell = g.cells.find(item => inside(p,boxOf(item)));
            if (pin && state.shown.has("pins")) select("pin",pin[0],g);
            else if (wire && state.shown.has("wires")) select("net",wire[0],g);
            else if (cell && (g.pads.includes(cell) ? state.shown.has("pads") : state.shown.has("cells"))) {
                select("cell",cell[0],g);
            }
        }
        drag = null;
    });
    canvas.addEventListener("pointercancel", () => { drag = null; });
}

function checkbox(text, checked, listener) {
    const label = element("label");
    const input = document.createElement("input");
    input.type = "checkbox"; input.checked = checked;
    input.addEventListener("change", () => listener(input.checked));
    label.append(input,document.createTextNode(text));
    return label;
}

function controls() {
    const g = state.primary;
    const layers = [...new Set([...g.layers,...(state.other?.layers || [])])];
    $("display").replaceChildren();
    for (const [key,text] of Object.entries(kinds)) {
        const property = key === "cells" ? "coreCells" : key;
        if (g[property]?.length || state.other?.[property]?.length) {
            $("display").append(checkbox(text,state.shown.has(key), on => {
                if (on) state.shown.add(key); else state.shown.delete(key);
                evidence(); drawAll();
            }));
        }
    }
    $("layers").replaceChildren(...layers.map(name => checkbox(name,!state.hiddenLayers.has(name),on => {
        if (on) state.hiddenLayers.delete(name); else state.hiddenLayers.add(name);
        drawAll();
    })));
    $("find").disabled = !(g.cells.length || g.nets.length || g.pins.length);
}
function button(text, action) {
    const b = element("button",text);
    b.addEventListener("click",action);
    return b;
}
function select(kind, name, g=state.primary) {
    state.selection = {kind,name};
    const node = $("selection");
    node.replaceChildren(element("strong",name),element("p",kind));
    const add = text => node.append(element("p",text));
    if (kind === "cell") {
        const c = g.cellMap.get(name);
        add(c[1]+" · "+c[2]);
        const unique = [...new Set((g.byCell.get(name) || []).map(t => t[0]))];
        for (const net of unique) node.append(button(net,() => select("net",net,g)));
    } else if (kind === "pin") {
        const p = g.pinMap.get(name);
        add(p[2]); node.append(button(p[1],() => select("net",p[1],g)));
    } else {
        const net = g.netMap.get(name);
        add(net[1]+" · "+(net[2] === "1" ? "recorded wire" : "no recorded wire"));
        const terminals = g.byNet.get(name) || [];
        for (const p of (g.portByNet.get(name) || [])) add(p[1]+" · port "+p[2]);
        for (const t of terminals.slice(0,30)) add(t[1]+"/"+t[2]+" · "+t[3]+" "+t[4]);
        if (terminals.length > 30) add(terminals.length+" total instance terminals");
    }
    drawAll();
}
$("find").addEventListener("input", () => {
    const query = $("find").value.trim().toLowerCase(), g = state.primary;
    const matches = [];
    if (query && g) for (const [kind, table] of [["cell",g.cellMap],["pin",g.pinMap],["net",g.netMap]]) {
        for (const name of table.keys()) if (name.toLowerCase().includes(query)) {
            matches.push(button(kind+" · "+name,() => select(kind,name)));
            if (matches.length === 12) break;
        }
        if (matches.length === 12) break;
    }
    $("matches").replaceChildren(...matches);
});

function evidence() {
    const p = state.bundle.stages[state.stage].provenance;
    const g = state.primary;
    const rows = [
        element("p",p.tool_version+" · "+p.source.evidence),
        element("code",p.source.path),
        element("p","Source SHA-256"), element("code",p.source.sha256),
        element("p",g.cells.length+" placed cells · "+g.nets.length+" nets"),
        ...(g.limitations || []).map(note => element("p",note)),
    ];
    if (state.other?.limitations?.length) {
        rows.push(...state.other.limitations.map(note => element("p","Comparison · "+note)));
    }
    if (state.shown.has("clock") && g.clock.length) {
        rows.push(element("p",g.clock.length+" clock nets. Lines join placed driver and sink pins."));
    }
    $("evidence").replaceChildren(...rows);
}

function timingDetails() {
    const g = state.primary;
    const path = g.timing[state.timingIndex];
    const mapped = path?.anchors.filter(anchor => anchor.point) || [];
    $("fit-path").disabled = mapped.length < 2;
    const entries = [];
    if (path) {
        if (path.startpoint) entries.push(element("p",path.startpoint+" → "+path.endpoint));
        if (path.group) entries.push(element("p","Path group "+path.group));
        if (path.launch_clock) entries.push(element("p",path.launch_clock+" → "+path.capture_clock));
        if (path.arrival_ns != null && path.required_ns != null) {
            entries.push(element("p","Arrival "+path.arrival_ns+" ns · required "+path.required_ns+" ns"));
        }
        entries.push(element("p",mapped.length < 2 ? "Matching geometry is unavailable." :
            mapped.length+" matched report pins"),element("code",path.report+" · lines "+path.lines.join("–")));
    } else entries.push(element("p","No verified timing paths for this stage."));
    $("timing-info").replaceChildren(...entries);
}

function timingOptions() {
    const paths = state.primary.timing;
    state.timingIndex = Math.max(0, paths.findIndex(path => path.check === "setup"));
    $("timing-path").replaceChildren(...paths.map((path,index) => {
        const option = element("option",path.check+" · "+path.slack_ns+" ns · "+path.lines[0]);
        option.value = index;
        option.selected = index === state.timingIndex;
        return option;
    }));
    $("timing-path").disabled = !paths.length;
    timingDetails();
}

async function switchStage(id, comparison=$("compare").value) {
    const seq = ++state.loading;
    status("Loading stage evidence…");
    try {
        const [g,other] = await Promise.all([geometry(id),geometry(comparison)]);
        if (seq !== state.loading) return;
        state.primary = g; state.other = other; state.stage = id; state.selection = null;
        state.ruler = [];
        state.shown = new Set(["cells","pins","pads","polygons"]);
        state.hiddenLayers.clear();
        $("stage-label").textContent = stageLabel(id)+" · "+g.top;
        $("compare-label").textContent = other ? stageLabel(comparison)+" · "+other.top : "";
        $("compare-panel").hidden = !other;
        for (const b of $("timeline").children) b.setAttribute("aria-pressed",String(b.dataset.id===id));
        $("selection").replaceChildren(); $("matches").replaceChildren(); $("find").value="";
        controls(); timingOptions(); evidence(); fit(); status("");
    } catch (error) { if (seq === state.loading) status(error.message); }
}
$("fit").onclick = fit;
$("plus").onclick = () => { state.zoom = Math.min(150,state.zoom*1.25); drawAll(); };
$("minus").onclick = () => { state.zoom = Math.max(.2,state.zoom/1.25); drawAll(); };
$("compare").onchange = () => switchStage(state.stage);
$("timing-path").onchange = () => {
    state.timingIndex = Number($("timing-path").value);
    state.shown.add("timing");
    controls();
    timingDetails();
    drawAll();
};
$("fit-path").onclick = () => {
    const g = state.primary;
    const pins = g.timing[state.timingIndex]?.anchors.filter(anchor => anchor.point) || [];
    if (pins.length < 2) return;
    const axes = [0,1].map(index => pins.map(pin => pin.point[index]/g.dbu));
    const spans = axes.map(axis => Math.max(...axis)-Math.min(...axis));
    const b = g.bounds.map(n => n/g.dbu);
    state.zoom = Math.min(150, .85 * Math.max(b[2]-b[0],b[3]-b[1]) / Math.max(...spans,.1));
    state.center = axes.map(axis => (Math.min(...axis)+Math.max(...axis))/2);
    state.shown.add("timing");
    controls();
    drawAll();
};
$("ruler").onclick = () => {
    state.measuring = !state.measuring;
    state.ruler = [];
    $("ruler").setAttribute("aria-pressed",String(state.measuring));
    status(state.measuring ? "Select the start point" : "");
    drawAll();
};

function download(blob, name) {
    const href = URL.createObjectURL(blob);
    const link = element("a"); link.href = href; link.download = name; link.click();
    setTimeout(() => URL.revokeObjectURL(href),1000);
}
$("png").onclick = async () => {
    try {
        const id = state.stage;
        download(await (await api(stagePath(id,"png"))).blob(),id+".png");
    } catch (error) { status(error.message); }
};

function embedPng(bytes, metadata) {
    const text = JSON.stringify(metadata).replace(/[^\x00-\x7f]/g,
        char => "\\u"+char.charCodeAt(0).toString(16).padStart(4,"0"));
    const data = new TextEncoder().encode("tEXtautonomous-eda.provenance\0"+text);
    const length = new Uint8Array(4), crc = new Uint8Array(4);
    new DataView(length.buffer).setUint32(0,data.length-4);
    let value = 0xffffffff;
    for (const byte of data) {
        value ^= byte;
        for (let bit=0;bit<8;bit++) value = (value>>>1)^((value&1)?0xedb88320:0);
    }
    new DataView(crc.buffer).setUint32(0,value^0xffffffff);
    return new Blob([bytes.slice(0,33),length,data,crc,bytes.slice(33)],{type:"image/png"});
}
$("view-png").onclick = () => {
    const id = state.stage;
    if (!id) return;
    const provenance = {...state.bundle.stages[id].provenance,
        generation_command:["autonomous-eda","artifacts","workspace","Export view"],
        tool:"Browser canvas", tool_version:navigator.userAgent,
        checkpoint_generation:state.bundle.stages[id].provenance,
        view:{center_um:[...state.center],zoom:state.zoom,visible:[...state.shown],
            hidden_layers:[...state.hiddenLayers],selection:state.selection}};
    views[0].toBlob(async blob => {
        if (blob) download(embedPng(new Uint8Array(await blob.arrayBuffer()),provenance),id+"-view.png");
    });
};

function events(target, rows) {
    target.replaceChildren(...rows.map(e => {
        const node = element("div",null,"event");
        node.append(element("small",e.kind+" · "+e.status
            +(e.duration_ms == null ? "" : " · "+e.duration_ms+" ms")));
        if (e.kind === "repository") {
            node.append(...e.detail.map(item => element("p",item)));
        } else if (e.kind === "toolchain") {
            node.append(element("p","Renderers · "+e.detail.renderers.join(", ")));
            const tools = Object.entries(e.detail.host);
            const details = document.createElement("details");
            details.append(element("summary","Host tools · "+tools.filter(([,p]) => p).length
                +" / "+tools.length+" on PATH"));
            details.append(...tools.map(([name,path]) => element("p",name+" · "+(path || "unavailable"))));
            node.append(details);
        } else if (e.kind === "artifacts") {
            const details = document.createElement("details");
            details.append(element("summary",e.detail.filter(item => item.status==="verified").length
                +" / "+e.detail.length+" indexed stages verified"));
            details.append(...e.detail.map(item => element("p",stageLabel(item.stage)+" · "+item.status)));
            node.append(details);
        } else {
            const count = e.detail.attempts ?? e.detail.transitions;
            const type = e.kind === "execution" ? "attempts" : "transitions";
            node.append(element("p",e.detail.source+" · "+count+" "+type));
        }
        return node;
    }));
}

let attempts = "";
let agentEvents = "";
let liveEvents = "";
let timelineEvents = "";
let polling = false;
async function refresh() {
    if (polling || !state.console) return;
    polling = true;
    try {
        const data = await json("/api/status");
        const timelineKey = JSON.stringify(data.timeline);
        if (timelineKey !== timelineEvents) {
            timelineEvents = timelineKey;
            const rows = data.timeline.map(event => {
                const timestamp = event.timestamp ? new Date(event.timestamp).toLocaleString() : "Time unavailable";
                return element("p",timestamp+" · "+event.source+" · "+event.message,event.severity);
            });
            $("execution-stream").replaceChildren(...(rows.length ? rows : [element("p","No recorded events")]));
        }
        const liveKey = JSON.stringify(data.events);
        if (liveKey !== liveEvents) {
            liveEvents = liveKey;
            events($("live-status"),data.events);
        }
        const agentKey = JSON.stringify(data.agents);
        if (agentKey !== agentEvents) {
            agentEvents = agentKey;
            $("agents").replaceChildren(...(data.agents.length ? data.agents.slice().reverse().map(a =>
                element("p",new Date(a.at*1000).toLocaleTimeString()+" · "+a.state+" · "+a.workflow))
                : [element("p","No recorded transitions")]));
        }
        const key = JSON.stringify(data.attempts);
        if (key === attempts) return;
        attempts = key;
        const open = new Set([...$("attempts").querySelectorAll("details[open]")].map(n => n.dataset.id));
        $("attempts").replaceChildren(...(data.attempts.length ? data.attempts.slice().reverse().map(item => {
            const node = document.createElement("details");
            node.dataset.id = item.id;
            node.append(element("summary","#"+item.id+" · "+item.stage+" · "+item.state+
                (item.runtime_seconds == null ? "" : " · "+item.runtime_seconds.toFixed(2)+" s")));
            if (item.exit_code != null) node.append(element("p","Exit code "+item.exit_code));
            if (item.detail) node.append(element("p",item.detail));
            if (item.command) {
                const command = element("details");
                command.append(element("summary","Recorded command"),element("pre",item.command));
                node.append(command);
            }
            const refs = element("pre",Object.entries(item.artifacts).map(([p,h]) => h+"  "+p).join("\n"));
            if (refs.textContent) node.append(element("p","Recorded evidence"),refs);
            node.addEventListener("toggle",async () => {
                if (!node.open || node.dataset.loaded) return;
                node.dataset.loaded = "1";
                const log = element("pre","Reading "+item.log+"…");
                node.append(log);
                try {
                    const result = await json("/api/log?attempt="+item.id);
                    log.textContent = result.text+(result.truncated ? "\n[Display limit reached]" : "");
                    for (const finding of result.diagnostics) {
                        node.append(element("p",finding.code+" · "+finding.log+":"+finding.line+
                            " · "+finding.message));
                    }
                } catch (error) { log.textContent = error.message; }
            });
            node.open = open.has(String(item.id));
            return node;
        }) : [element("p","No controller attempts selected")]));
        status("");
    } catch (error) { status(error.message); }
    finally { polling = false; }
}
let discovered = false;
async function refreshVerification() {
    const content = $("verification-content");
    content.textContent = "Reading recorded gates…";
    try {
        const report = await json("/api/verification");
        if (!report) { content.textContent = "No controller run is selected."; return; }
        const table = element("table");
        const headers = element("tr");
        for (const label of ["Gate", "Status", "Evidence"]) {
            const cell = element("th",label);
            cell.scope = "col";
            headers.append(cell);
        }
        table.createTHead().append(headers);
        const body = table.createTBody();
        for (const [name,gate] of Object.entries(report.gates)) {
            const row = body.insertRow();
            row.append(element("td",name.replaceAll("_"," ")));
            row.append(element("td",gate.status,"gate "+gate.status.toLowerCase()));
            const evidence = element("td");
            evidence.append(element("div",gate.diagnostic));
            if (gate.evidence_references.length) {
                const refs = element("details",null,"references");
                refs.append(element("summary",gate.evidence_references.length+" evidence references"));
                for (const path of gate.evidence_references) refs.append(element("code",path));
                evidence.append(refs);
            }
            row.append(evidence);
        }
        const acceptance = report.verdicts.full_acceptance.replaceAll("_"," ");
        const summary = element("p","Acceptance: "+acceptance,
            "gate "+report.verdicts.full_acceptance.toLowerCase());
        content.replaceChildren(summary);
        if (report.note) content.append(element("p",report.note));
        content.append(table);
        if (report.decision_sha256) content.append(element("code",report.decision_sha256));
    } catch (error) { content.textContent = error.message; }
}

let resourcePage = null;
function renderAESQualification(report) {
    const details = $("aes-qualification");
    details.hidden = !report;
    const content = $("aes-qualification-content");
    content.replaceChildren();
    if (!report) return;
    const labels = {
        interface_timing: "Interface timing", pre_route_sta: "Pre-route SDC parse",
        cipher_core: "Cipher-core regression", peripheral: "Peripheral regression",
        structural: "Structural correspondence", differential: "Differential regression",
        formal: "Sequential equivalence", resource_admission: "Resource admission",
        clock_reset_structure: "Clock and reset structural review", cdc_qualification: "CDC qualification",
        multi_clock: "Multi-clock functional verification", sideload: "Key-manager sideload",
        alert_errors: "Alert and error verification", bounded_formal: "Bounded partition correspondence"
    };
    content.append(element("p", "RTL revision " + report.rtl_revision));
    content.append(element("p", "Source manifest " + report.source_manifest_sha256));
    const list = element("dl");
    for (const [gate, state] of Object.entries(report.gates)) {
        list.append(element("dt", labels[gate] || gate));
        const color = {PASS:"passed", FAIL:"failed"}[state] || state.toLowerCase();
        list.append(element("dd", state, "gate " + color));
    }
    content.append(list);
    if (report.scenarios) {
        const cases = element("details");
        const labels = {
            common:"Common clock", phase_offset:"Phase offset", independent:"Independent clocks",
            sideload:"Sideload", errors:"Alerts and errors"
        };
        cases.append(element("summary", "Verilator scenarios"));
        const results = element("dl");
        for (const [name, state] of Object.entries(report.scenarios)) {
            results.append(element("dt",labels[name]),element("dd",state));
        }
        cases.append(results);
        content.append(cases);
    }
    const obligations = element("ul");
    for (const detail of Object.values(report.obligations))
        obligations.append(element("li", detail));
    content.append(obligations);
    const evidence = element("details");
    evidence.append(element("summary", "Evidence files and SHA-256"));
    for (const reference of Object.values(report.evidence_references))
        evidence.append(element("p", reference.file + " · " + reference.sha256));
    content.append(evidence);
}
let resourceRefreshing = false;
let resourceActive = false;
const resourceTitles = {...labels, synth:"Synthesis", finish:"Finish",
    timing_coverage:"Timing coverage", drc:"Platform DRC", lvs:"LVS"};
const formatResource = (value, unit, divisor=1) => value == null ? "Unknown"
    : (value/divisor).toLocaleString(undefined,{maximumFractionDigits:2})+" "+unit;

function resourceAttempts(page) {
    const checks = (page.verification_attempts || []).map(item => {
        const dispatch = item.dispatches.primary;
        return {...item, duration_seconds:dispatch.elapsed_seconds,
            state:dispatch.classification === "COMPLETED" ? "completed" : "stopped"};
    });
    return [...page.attempts,...checks];
}

function resourceGraph(attempt) {
    const canvas = $("resource-plot");
    const series = attempt?.dispatches.primary.samples || [];
    const usable = series.filter(point => Number.isFinite(point.memory_current_bytes));
    canvas.hidden = !usable.length;
    $("resource-sample-note").textContent = usable.length
        ? usable.length+" plotted cgroup samples · "+attempt.dispatches.primary.status.toLowerCase()
          +" · Cyan: memory use · Amber: high-water"
        : "Container memory samples are unavailable for this attempt.";
    if (!usable.length) return;
    const ctx = canvas.getContext("2d");
    const {width,height} = canvas;
    const bottom = height-32, left = 72, right = width-24, top = 20;
    const duration = Math.max(1, usable.at(-1).elapsed_seconds);
    const limit = attempt.dispatches.primary.configured_memory_bytes ?? resourcePage.memory_limit_bytes;
    const max = Math.max(limit || 0, ...usable.map(point => point.memory_kernel_peak_bytes || 0));
    ctx.clearRect(0,0,width,height);
    ctx.font = "14px system-ui";
    ctx.textBaseline = "middle";
    for (let tick=0; tick<=4; tick++) {
        const y = bottom-tick*(bottom-top)/4;
        ctx.strokeStyle = "#344659";
        ctx.beginPath(); ctx.moveTo(left,y); ctx.lineTo(right,y); ctx.stroke();
        ctx.fillStyle="#c7d3e0";
        ctx.fillText(Math.round(max*tick/4/1048576).toString(),8,y);
    }
    ctx.fillText("MiB",8,8);
    for (let tick=0; tick<=4; tick++) {
        const x = left+tick*(right-left)/4;
        ctx.fillText(Math.round(duration*tick/4)+" s",Math.min(x-8,right-32),bottom+16);
    }
    for (const [key,color] of [["memory_current_bytes","#59cddb"],["memory_kernel_peak_bytes","#efbd77"]]) {
        ctx.beginPath();
        ctx.strokeStyle=color; ctx.lineWidth=2;
        let first = true;
        for (const point of usable) {
            if (!Number.isFinite(point[key])) continue;
            const x = left+point.elapsed_seconds/duration*(right-left);
            const y = bottom-point[key]/Math.max(max,1)*(bottom-top);
            if (first) ctx.moveTo(x,y); else ctx.lineTo(x,y);
            first = false;
        }
        ctx.stroke();
    }
}

function selectResource() {
    const attempt = resourcePage
        ? resourceAttempts(resourcePage).find(item => String(item.id) === $("resource-attempt").value)
        : null;
    resourceGraph(attempt);
    const record = attempt?.dispatches.primary;
    const facts = $("resource-facts");
    facts.replaceChildren();
    if (!record) return;
    const values = record.measurements;
    const bytes = values.memory_kernel_peak_bytes?.value;
    const limit = record.configured_memory_bytes ?? resourcePage.memory_limit_bytes;
    const cpu = record.configured_cpu_nanocores == null ? resourcePage.allocated_cpus
        : record.configured_cpu_nanocores/1000000000;
    const used = bytes != null && limit ? (100*bytes/limit).toFixed(1)+"%" : "Unknown";
    const last = record.samples?.at(-1)?.elapsed_seconds;
    const duration = attempt.duration_seconds ?? last;
    const budget = record.stage_timeout_seconds ??
        (attempt.stage==="route" && record.status==="PROVISIONAL" ? resourcePage.route_budget_seconds : null);
    const timeUsed = duration != null && budget ? (100*duration/budget).toFixed(1)+"%" : "Unknown";
    for (const [label,value] of [
        ["Peak memory",formatResource(bytes,"MiB",1048576)],["Memory budget used",used],
        ["Container cap",formatResource(limit,"MiB",1048576)],
        ["CPU time",formatResource(values.cpu_usage_usec?.value,"s",1000000)],
        ["CPU allocation",formatResource(cpu,"cores")],
        ["Elapsed",formatResource(duration,"s")],["Wall budget",formatResource(budget,"s")],
        ["Wall budget used",timeUsed],
        ["Termination",record.classification ?? attempt.state],
        ["Native exit",record.native_exit_code ?? attempt.exit_code ?? "Unknown"],
        ["Sampled OOM kills",values.oom_kills?.value ?? "Unknown"],
        ["Docker OOM flag",record.docker_oom_killed == null ? "Unknown"
            : String(record.docker_oom_killed)],
        ["Retry count",record.retry_count ?? "Unknown"],
        ["Reused checkpoints",record.reused_checkpoints?.map(stage=>resourceTitles[stage] || stage).join(", ") || "Unknown"],
    ]) facts.append(element("dt",label),element("dd",String(value)));
}

async function refreshResources() {
    if (resourceRefreshing) return;
    resourceRefreshing = true;
    const note = $("resources-status");
    try {
        const page = await json("/api/resources");
        if (!page) { note.textContent = "No controller run is selected."; return; }
        resourcePage = page;
        const attempts = resourceAttempts(page);
        const body = $("resources-stages").tBodies[0];
        body.replaceChildren();
        const checkRows = attempts.filter(item=>typeof item.id === "string");
        for (const stage of [...page.pipeline,...checkRows]) {
            const record = attempts.find(item=>item.stage===stage.stage);
            const primary = record?.dispatches.primary;
            const row = body.insertRow();
            row.dataset.stage = stage.stage;
            row.dataset.state = stage.state;
            const label = element("th",resourceTitles[stage.stage] || stage.stage);
            label.scope = "row";
            row.append(label, element("td",stage.state,"gate "+stage.state),
                element("td",formatResource(record?.duration_seconds,"s")),
                element("td",formatResource(primary?.measurements.memory_kernel_peak_bytes?.value,"MiB",1048576)),
                element("td",primary?.classification || stage.reason || ""));
        }
        const select = $("resource-attempt");
        const preferred = select.value || String(page.attempts.find(item=>item.stage==="route")?.id || "");
        select.replaceChildren(...attempts.map(item=>{
            const label = (resourceTitles[item.stage] || item.stage)+" · Attempt "+item.id+" · "+item.state;
            const option = element("option",label);
            option.value = item.id;
            return option;
        }));
        if ([...select.options].some(option=>option.value===preferred)) select.value=preferred;
        const active = page.attempts.some(row=>row.state==="running");
        note.textContent = active ? "Live observations are provisional." : "Retained attempt evidence";
        selectResource();
        const qualification = $("resource-qualification");
        $("resource-gates").textContent = page.pipeline.find(item=>item.stage==="finish")?.state==="blocked"
            ? "Final extraction, STA, DRC, LVS and GDS reopening are blocked." : "";
        qualification.textContent = page.readiness_level == null
            ? "Readiness unknown" : "Readiness Level "+page.readiness_level;
        if (!active) {
            json("/api/resource-qualification").then(result=>{
                if (!result || resourcePage?.run_id!==result.run_id) return;
                renderAESQualification(result.aes_qualification);
                qualification.textContent = "Readiness Level "+(result.readiness_level ?? "unknown")
                    +" · Implementation "+result.implementation_status
                    +" · Physical acceptance "+result.physical_acceptance_status
                    +" · Design qualification "+result.design_qualification_status;
                const gates = result.verification_gates;
                $("resource-gates").textContent = "Setup "+gates.setup
                    +" · Hold "+gates.hold+" · Coverage "+gates.timing_coverage
                    +" · Router "+gates.router+" · Platform DRC "+gates.platform_drc
                    +" · LVS "+gates.lvs+" · GDS reopening "+gates.gds_reopen;
            }).catch(()=>{});
        }
    } catch (error) { note.textContent = error.message; }
    finally { resourceRefreshing = false; }
}

function tab(viewName) {
    state.console = viewName === "console";
    resourceActive = viewName === "resources";
    const tabs = {explore:"Physical layout", verification:"Verification", resources:"Resource diagnostics", console:"Execution console"};
    for (const name of Object.keys(tabs)) $(name).hidden = viewName !== name;
    $("explore-tab").setAttribute("aria-pressed",String(viewName === "explore"));
    $("verify-tab").setAttribute("aria-pressed",String(viewName === "verification"));
    $("console-tab").setAttribute("aria-pressed",String(state.console));
    $("resources-tab").setAttribute("aria-pressed",String(resourceActive));
    $("title").textContent = tabs[viewName];
    status("");
    if (viewName === "explore") { drawAll(); return; }
    if (viewName === "verification") { refreshVerification(); return; }
    if (resourceActive) { refreshResources(); return; }
    if (!discovered) {
        discovered = true;
        $("context").replaceChildren(element("p","Reading workspace sources…","waiting"));
        json("/api/discovery").then(data => events($("context"),data.events))
            .catch(error => { discovered=false; $("context").textContent=error.message; });
    }
    refresh();
}
$("explore-tab").onclick = () => tab("explore");
$("verify-tab").onclick = () => tab("verification");
$("console-tab").onclick = () => tab("console");
$("resources-tab").onclick = () => tab("resources");
$("refresh-resources").onclick = refreshResources;
$("resource-attempt").onchange = selectResource;
setInterval(()=>{
    if (resourceActive && resourcePage?.attempts.some(item=>item.state==="running")) refreshResources();
},5000);
$("refresh-verification").onclick = refreshVerification;
$("correction-review").addEventListener("toggle", async () => {
    if (!$("correction-review").open) return;
    const output = $("correction-state");
    output.textContent = "Reading correction receipts…";
    try {
        const review = await json("/api/correction");
        if (!review) {
            output.textContent = "No corrective-action review is selected.";
            return;
        }
        output.replaceChildren(
            element("p",review.state+" · "+stageLabel(review.stage)+" · "+review.action),
            element("code",review.plan_sha256),
            ...review.events.map(event =>
                element("p",new Date(event.timestamp).toLocaleString()+" · "+event.state))
        );
    } catch (error) { output.textContent = error.message; }
});
setInterval(refresh,2000);

(async () => {
    state.bundle = await json("/api/bundle");
    $("run").textContent = "Run "+state.bundle.run_id;
    const available = [];
    for (const id of state.bundle.order) {
        const record = state.bundle.stages[id];
        const ready = record.status === "rendered";
        const b = button(stageLabel(id)+(ready ? "" : " · "+record.status),() => switchStage(id));
        b.dataset.id = id; b.disabled = !ready;
        $("timeline").append(b);
        if (ready) {
            available.push(id);
            const option = element("option",stageLabel(id)); option.value=id; $("compare").append(option);
        }
    }
    if (available.length) await switchStage(available.includes("place") ? "place" : available[0]);
    else status("The selected bundle contains no rendered stages.");
})().catch(error => status(error.message));
