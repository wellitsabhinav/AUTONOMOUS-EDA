"use strict";

const $ = id => document.getElementById(id);
const labels = {floorplan:"Floorplan", place:"Placement", cts:"CTS", grt:"Global route",
    route:"Detailed route", finish:"Final OpenDB", gds:"Final GDS", "pad-ring":"I/O pad ring"};
const palette = ["#5ecbdb", "#aa86f2", "#f6ad68", "#73d79e", "#e47fad", "#6699f2", "#c0c768"];
const stageLabel = id => labels[id] || id;
const state = {bundle:null, stages:new Map(), primary:null, other:null, selection:null,
    shown:new Set(), hiddenLayers:new Set(), center:[0,0], zoom:1, loading:0, console:false};
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
    if (!state.stages.has(id)) {
        const g = index(await json(stagePath(id, "geometry")));
        state.stages.set(id, g);
    }
    return state.stages.get(id);
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
    ctx.fillStyle = "#0a121e"; ctx.fillRect(0,0,w,h);
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
    const {ctx, pixel, ratio} = view(canvas, g);
    const color = layer => palette[g.layers.indexOf(layer) % palette.length] || "#63b9ce";
    const shown = kind => state.shown.has(kind);
    const layerOn = layer => !state.hiddenLayers.has(layer);
    const box = (bounds, fill, outline) => {
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
    if (shown("timing") && g.timing.length) {
        const path = g.timing.find(p => p.check === "setup") || g.timing[0];
        let prev = null;
        for (const a of path.anchors) {
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
}

function drawAll() {
    draw(views[0],state.primary);
    draw(views[1],state.other);
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
    if (state.shown.has("timing") && g.timing.length) {
        const t = g.timing.find(p => p.check === "setup") || g.timing[0];
        rows.push(element("p",t.check+" "+t.slack_ns+" ns · report lines "+t.lines.join("–")),
            element("code",t.report),element("p","Timing lines join matched report pins."));
    }
    $("evidence").replaceChildren(...rows);
}

async function switchStage(id, comparison=$("compare").value) {
    const seq = ++state.loading;
    status("Loading stage evidence…");
    try {
        const [g,other] = await Promise.all([geometry(id),geometry(comparison)]);
        if (seq !== state.loading) return;
        state.primary = g; state.other = other; state.stage = id; state.selection = null;
        state.shown = new Set(["cells","pins","pads","polygons"]);
        state.hiddenLayers.clear();
        $("stage-label").textContent = stageLabel(id)+" · "+g.top;
        $("compare-label").textContent = other ? stageLabel(comparison)+" · "+other.top : "";
        $("compare-panel").hidden = !other;
        for (const b of $("timeline").children) b.setAttribute("aria-pressed",String(b.dataset.id===id));
        $("selection").replaceChildren(); $("matches").replaceChildren(); $("find").value="";
        controls(); evidence(); fit(); status("");
    } catch (error) { if (seq === state.loading) status(error.message); }
}
$("fit").onclick = fit;
$("plus").onclick = () => { state.zoom = Math.min(150,state.zoom*1.25); drawAll(); };
$("minus").onclick = () => { state.zoom = Math.max(.2,state.zoom/1.25); drawAll(); };
$("compare").onchange = () => switchStage(state.stage);

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
let polling = false;
async function refresh() {
    if (polling || !state.console) return;
    polling = true;
    try {
        const data = await json("/api/status");
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
function tab(consoleOn) {
    state.console = consoleOn;
    $("explore").hidden = consoleOn; $("console").hidden = !consoleOn;
    $("explore-tab").setAttribute("aria-pressed",String(!consoleOn));
    $("console-tab").setAttribute("aria-pressed",String(consoleOn));
    $("title").textContent = consoleOn ? "Agentic Execution Console" : "Physical Design Explorer";
    if (!consoleOn) { drawAll(); return; }
    if (!discovered) {
        discovered = true;
        $("context").replaceChildren(element("p","Reading workspace sources…","waiting"));
        json("/api/discovery").then(data => events($("context"),data.events))
            .catch(error => { discovered=false; $("context").textContent=error.message; });
    }
    refresh();
}
$("explore-tab").onclick = () => tab(false);
$("console-tab").onclick = () => tab(true);
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
