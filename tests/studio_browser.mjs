import {createHash} from "node:crypto";
import {writeFile, mkdir, readFile} from "node:fs/promises";
import {resolve} from "node:path";

const port = process.env.AEDA_CDP_PORT || "9227";
const pages = await fetch("http://127.0.0.1:"+port+"/json/list").then(response => response.json());
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
if (process.argv[4] === "resources") {
    await wait('typeof tab === "function" && document.querySelector("#resources-tab")');
    const tabStart = performance.now();
    await cdp("Page.bringToFront");
    await evaluate('document.querySelector("#resources-tab").focus()');
    await cdp("Input.dispatchKeyEvent",{type:"keyDown",key:"Enter",code:"Enter",text:"\r",windowsVirtualKeyCode:13});
    await cdp("Input.dispatchKeyEvent",{type:"keyUp",key:"Enter",code:"Enter",windowsVirtualKeyCode:13});
    try {
        await wait('document.querySelector("#resources-stages tr[data-stage=route]")');
    } catch (error) {
        const current = await evaluate(
            '({tab:document.querySelector("#resources-tab").getAttribute("aria-pressed"),'
            +'note:document.querySelector("#resources-status").textContent})'
        );
        throw new Error(error.message+" "+JSON.stringify({current,faults}));
    }
    const renderMs = performance.now()-tabStart;
    const expected = process.argv[5];
    const routeState = await evaluate('document.querySelector("#resources-stages tr[data-stage=route]").dataset.state');
    if (expected && routeState !== expected) throw new Error("Expected route "+expected+", saw "+routeState);
    if (routeState === "failed") {
        await wait('document.querySelector("#resources-stages tr[data-stage=finish]").dataset.state==="blocked"');
    }
    const sources = await cdp("Runtime.evaluate",{
        expression:'fetch("/api/resources").then(r=>r.text())',awaitPromise:true,returnByValue:true
    });
    const source = sources.result.value;
    const data = JSON.parse(source);
    await writeFile(output+"/resource-api.json",source);
    await wait('document.querySelector("#resource-qualification").textContent.length > 0');
    let qualification = null;
    if (routeState !== "running") {
        await wait('document.querySelector("#resource-qualification").textContent.includes("Physical acceptance")');
        const loaded = await cdp("Runtime.evaluate",{
            expression:'fetch("/api/resource-qualification").then(r=>r.text())',
            awaitPromise:true,returnByValue:true
        });
        qualification = loaded.result.value;
        await writeFile(output+"/resource-qualification-api.json",qualification);
    }
    await cdp("Emulation.setDeviceMetricsOverride",{width:1440,height:1050,deviceScaleFactor:1,mobile:false});
    await screenshot("resources");
    await cdp("Emulation.setDeviceMetricsOverride",{width:390,height:844,deviceScaleFactor:1,mobile:true});
    const viewportWidth = await evaluate("document.documentElement.clientWidth");
    const scrollWidth = await evaluate("document.documentElement.scrollWidth");
    if (scrollWidth > viewportWidth + 1) throw new Error("Resources view overflows the mobile page");
    await screenshot("resources-mobile");
    if (faults.length) throw new Error(faults.join("\n"));
    const hash = value => createHash("sha256").update(value).digest("hex");
    const receipt = {
        run_id:data.run_id, routeState, renderMs, viewportWidth, scrollWidth,
        source_sha256:hash(source), source_bytes:Buffer.byteLength(source),
        qualification_source_sha256:qualification === null ? null : hash(qualification),
        image_sha256:hash(await readFile(output+"/resources.png")),
        mobile_sha256:hash(await readFile(output+"/resources-mobile.png")),
        browser:await cdp("Browser.getVersion"), generated_at:new Date().toISOString(),
        generation_command:process.argv.slice(1),
    };
    await writeFile(output+"/resource-browser-check.json",JSON.stringify(receipt,null,2)+"\n");
    console.log(receipt);
    ws.close();
    process.exit(0);
}
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
if (process.argv.includes("correction")) {
    await invoke("#correction-review > summary");
    await wait('document.querySelector("#correction-state").textContent.includes("VERIFIED")');
    await evaluate('document.querySelector("#correction-review").scrollIntoView({block:"center"})');
    await screenshot("correction");
}
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
