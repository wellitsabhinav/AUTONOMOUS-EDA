import {createHash} from "node:crypto";
import {writeFile, mkdir, readFile} from "node:fs/promises";
import {resolve} from "node:path";

const pages = await fetch("http://127.0.0.1:9227/json/list").then(response => response.json());
const page = pages.find(item => item.type === "page");
const ws = new WebSocket(page.webSocketDebuggerUrl);
const pending = new Map();
let next = 0;
let faults = [];
ws.onmessage = ({data}) => {
    const message = JSON.parse(data);
    if (message.method === "Runtime.exceptionThrown") {
        faults.push(message.params.exceptionDetails.text);
    }
    const item = pending.get(message.id);
    if (!item) return;
    pending.delete(message.id);
    if (message.error) item.reject(new Error(message.error.message));
    else item.resolve(message.result);
};
await new Promise((resolve, reject) => {
    ws.onopen = resolve;
    ws.onerror = reject;
});
const cdp = (method, params={}) => new Promise((resolve,reject) => {
    const id = ++next;
    pending.set(id,{resolve,reject});
    ws.send(JSON.stringify({id,method,params}));
});
const evaluate = async expression => {
    const result = await cdp("Runtime.evaluate",{expression,returnByValue:true});
    if (result.exceptionDetails) throw new Error(result.exceptionDetails.text);
    return result.result.value;
};
const wait = async condition => {
    const end = performance.now()+40000;
    while (performance.now() < end) {
        if (await evaluate(condition)) return;
        await new Promise(resolve => setTimeout(resolve,65));
    }
    throw new Error("Studio state did not settle: "+condition);
};
const invoke = id => evaluate(`document.querySelector(${JSON.stringify(id)}).click()`);
const output = process.argv[3] || "experiments/studio-browser-smoke";
const large = process.argv[4] === "aes";
await mkdir(output,{recursive:true});
const screenshot = async name => {
    const png = await cdp("Page.captureScreenshot",{format:"png",captureBeyondViewport:false});
    await writeFile(output+"/"+name+".png",Buffer.from(png.data,"base64"));
};
await cdp("Runtime.enable");
await cdp("Page.enable");
await cdp("Emulation.setDeviceMetricsOverride",{width:1440,height:900,deviceScaleFactor:1,mobile:false});
const start = performance.now();
await cdp("Page.navigate",{url:process.argv[2] || "http://127.0.0.1:8765/"});
await wait('document.querySelector("#stage-label")?.textContent.startsWith("Placement")');
const initialMs = performance.now()-start;
await screenshot("placement");
const stage = performance.now();
await invoke(large ? '[data-id="cts"]' : '[data-id="finish"]');
await wait(large ? 'document.querySelector("#stage-label").textContent.startsWith("CTS")'
    : 'document.querySelector("#stage-label").textContent.startsWith("Final OpenDB")');
const stageMs = performance.now()-stage;
await invoke("#fit-path");
if (!large) await wait('document.querySelector("#timing-info").textContent.includes("matched report pins")');
await screenshot("timing");
let exportSha256 = null;
if (!large) {
    await cdp("Browser.setDownloadBehavior",{behavior:"allow",downloadPath:resolve(output)});
    await invoke("#view-png");
    let exported;
    for (let retry = 0; retry < 60; retry++) {
        exported = await readFile(output+"/finish-view.png").catch(() => null);
        if (exported?.includes(Buffer.from("autonomous-eda.provenance\0"))) break;
        await new Promise(resolve => setTimeout(resolve,100));
    }
    if (!exported?.includes(Buffer.from("autonomous-eda.provenance\0"))) {
        throw new Error("Canvas PNG did not export provenance");
    }
    exportSha256 = createHash("sha256").update(exported).digest("hex");
    await evaluate('document.querySelector("#find").value="_0"; document.querySelector("#find").dispatchEvent(new Event("input"))');
    await wait('document.querySelector("#matches button")');
    await invoke("#matches button");
    await screenshot("inspection");
}
await invoke('[data-id="place"]');
await wait('document.querySelector("#stage-label").textContent.startsWith("Placement")');
await evaluate('document.querySelector("#compare").value="cts"; document.querySelector("#compare").dispatchEvent(new Event("change"))');
await wait('document.querySelector("#compare-label").textContent.startsWith("CTS")');
await screenshot("comparison");
await invoke("#ruler");
for (const [x,y] of [[375,360],[417,378]]) {
    await cdp("Input.dispatchMouseEvent",{type:"mouseMoved",x,y});
    await cdp("Input.dispatchMouseEvent",{type:"mousePressed",x,y,button:"left",clickCount:1});
    await cdp("Input.dispatchMouseEvent",{type:"mouseReleased",x,y,button:"left",clickCount:1});
}
await wait('document.querySelector("#feedback").textContent.endsWith("µm")');
const ruler = await evaluate('document.querySelector("#feedback").textContent');
const heap = await cdp("Runtime.getHeapUsage");
await invoke("#verify-tab");
await wait('document.querySelector("#verification-content").textContent.includes("Acceptance:")');
await screenshot("verification");
await invoke("#console-tab");
await wait('document.querySelector("#live-status").textContent.includes("idle")');
await wait('document.querySelector("#context .event")');
await screenshot("execution");
if (large) {
    const failing = await evaluate(
        '[...document.querySelectorAll("#attempts > details")].find(row => row.textContent.includes(" · failed"))?.dataset.id'
    );
    if (failing) {
        await invoke(`#attempts > details[data-id="${failing}"] > summary`);
        await wait(`document.querySelector('#attempts > details[data-id="${failing}"]').open`);
        await wait(`[...document.querySelectorAll('#attempts > details[data-id="${failing}"] > pre')]
            .some(log => log.textContent.includes("OpenROAD"))`);
        await screenshot("diagnostic-failure");
    }
}
if (faults.length) throw new Error(faults.join("\n"));
const receipt = {initialMs,stageMs,ruler,heap,exportSha256,browser:await cdp("Browser.getVersion")};
await writeFile(output+"/browser-check.json",JSON.stringify(receipt,null,2)+"\n");
console.log(receipt);
ws.close();
