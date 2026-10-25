"use strict";
// Genuine browser interaction with a fresh, private local demo. No vendor calls.
const fs=require("node:fs"),path=require("node:path"),assert=require("node:assert/strict");
const {execFileSync}=require("node:child_process");
const {chromium}=require("playwright");
const AxeBuilder=require("@axe-core/playwright").default;
const workspace=path.resolve(process.argv[2]||".local/console-acceptance");
const output=path.resolve(process.argv[3]||".local/browser-evidence");
const origin=process.env.INTEGRITY_BROWSER_ORIGIN||"http://127.0.0.1:8582";
if(fs.existsSync(output))throw Error("fresh_browser_evidence_path_required");
fs.mkdirSync(output,{recursive:true});
const credentials=JSON.parse(fs.readFileSync(path.join(workspace,"operator-credentials.json")));
const cases=[],accessibility=[],errors=[];
const crypto=require("node:crypto");
function fingerprints(){const result={};for(const name of ["web/index.html","web/app.css","web/app.js","scripts/browser_acceptance.cjs","scripts/verify_browser.py","package.json","pnpm-lock.yaml"]){result[name]=crypto.createHash("sha256").update(fs.readFileSync(name,"utf8").replaceAll("\r\n","\n")).digest("hex");}return result;}
const startedFingerprints=fingerprints();
const started=execFileSync("git",["rev-parse","HEAD"],{encoding:"utf8"}).trim();
const check=(name,value)=>{assert.ok(value,name);cases.push(name);};
async function audit(page,name){
 check(name+" readable Unicode",!/[âÃÂ]/.test(await page.locator("body").innerText()));
 const result=await new AxeBuilder({page}).withTags(["wcag2a","wcag2aa","wcag21aa"]).analyze();
 accessibility.push({view:name,violations:result.violations.map(v=>({id:v.id,impact:v.impact,nodes:v.nodes.length})),passes:result.passes.length});
 assert.deepEqual(result.violations.map(v=>v.id),[],name+" accessibility");
 check(name+" no document overflow",await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth));
}
async function actor(browser,name,viewport={width:1512,height:1060}){
 const context=await browser.newContext({viewport,acceptDownloads:true});
 const page=await context.newPage();page.on("pageerror",e=>errors.push(e.message));
 await page.goto(origin);await page.locator("#username").fill(name);
 await page.locator("#password").fill(credentials[name].password);
 await page.getByRole("button",{name:"Sign in",exact:true}).click();
 await page.locator("#app-screen").waitFor({state:"visible"});
 await page.locator(".metric-grid").waitFor();return {page,context};
}
async function overview(page){const r=await page.request.get(origin+"/api/overview");assert.equal(r.status(),200);return r.json();}
const total=(data,key)=>data.runs.flatMap(x=>x.report.period_measures).reduce((sum,x)=>sum+BigInt(x[key]),0n);
async function until(fn,timeout=150000){const deadline=Date.now()+timeout;while(Date.now()<deadline){if(await fn())return;await new Promise(r=>setTimeout(r,500));}throw Error("timed_out_waiting_for_verified_state");}
(async()=>{
 const browser=await chromium.launch({headless:true,...(process.env.INTEGRITY_CHROME_PATH?{executablePath:process.env.INTEGRITY_CHROME_PATH}:{})});
 try{
  const anonymousContext=await browser.newContext({viewport:{width:1512,height:1060}});const anonymous=await anonymousContext.newPage();await anonymous.goto(origin);await audit(anonymous,"login");
  await anonymous.keyboard.press("Tab");check("keyboard skip link",await anonymous.getByText("Skip to workspace").evaluate(e=>e===document.activeElement));await anonymous.close();
  const auditor=await actor(browser,"auditor");const initial=await overview(auditor.page);
  check("initial Q1 billing 39000",total(initial,"billing_net_cents")===3900000n);
  check("initial Q1 ERP 37500",total(initial,"erp_net_cents")===3750000n);
  check("initial ARR 120000",total(initial,"period_end_arr_cents")===12000000n);
  await audit(auditor.page,"desktop reconciliation");await auditor.page.screenshot({path:path.join(output,"console-overview.png"),fullPage:true});
  for(const [view,label] of [["operations","Exceptions & delivery"],["lineage","Source lineage"],["audit","Decision ledger"]]){
   await auditor.page.getByRole("button",{name:label,exact:true}).click();await auditor.page.locator("#view .panel").first().waitFor();
   await audit(auditor.page,"desktop "+view);
  }
  await auditor.page.getByRole("button",{name:"Source lineage",exact:true}).click();
  await auditor.page.getByText("Fresh authenticated GET",{exact:false}).waitFor();
  await auditor.page.getByText("Canonical independent receipt",{exact:true}).click();
  check("fresh canonical receipt visible",await auditor.page.locator("pre").filter({hasText:'"receipt_id"'}).isVisible());
  await auditor.page.screenshot({path:path.join(output,"console-lineage.png"),fullPage:true});
  const op=await actor(browser,"operator");await op.page.getByRole("button",{name:"Exceptions & delivery",exact:true}).click();
  await op.page.getByLabel("Why is another bounded attempt safe?").fill("Synthetic receiver outage cleared; review the unchanged contract and payload.");
  await op.page.getByRole("button",{name:"Propose recovery",exact:true}).click();await op.page.getByText("Exact recovery proposed.",{exact:false}).waitFor();
  const approver=await actor(browser,"approver");await approver.page.getByRole("button",{name:"Exceptions & delivery",exact:true}).click();
  await approver.page.getByLabel("Review note",{exact:true}).fill("Reject this first proposal pending an explicit source review.");
  await approver.page.getByRole("button",{name:"Reject proposal",exact:true}).click();await approver.page.getByText("Decision recorded: rejected",{exact:false}).waitFor();
  check("rejection did not dispatch",(await overview(op.page)).effects.some(e=>e.status==="exhausted"&&e.attempts===3));
  await op.page.getByRole("button",{name:"Refresh evidence",exact:true}).click();
  await op.page.getByLabel("Why is another bounded attempt safe?").fill("Source and mappings independently reviewed; retry the exact synthetic intent.");
  await op.page.getByRole("button",{name:"Propose recovery",exact:true}).click();await op.page.getByText("Exact recovery proposed.",{exact:false}).waitFor();
  await approver.page.getByRole("button",{name:"Refresh evidence",exact:true}).click();
  await approver.page.getByLabel("Review note",{exact:true}).fill("Exact hash, owner and scoped identity confirmed. Permit the bounded retry only.");
  await audit(approver.page,"approval queue");await approver.page.evaluate(()=>{document.activeElement.blur();window.scrollTo(0,0);});await approver.page.screenshot({path:path.join(output,"console-approval.png"),fullPage:true});
  await approver.page.getByRole("button",{name:"Approve exact recovery",exact:true}).click();
  let final;await until(async()=>{final=await overview(auditor.page);return final.jobs.some(j=>j.state==="verified")&&total(final,"billing_net_cents")===4560000n;});
  check("actual approved effect and dependent finance verified",final.effects.every(e=>e.status==="verified"));
  check("recovery budget persisted",final.effects.some(e=>e.event_id==="sf-recovery-v1"&&e.destination==="stripe"&&e.attempts===4&&e.max_attempts===6));
  check("rechecked ERP 44100",total(final,"erp_net_cents")===4410000n);
  check("cash remains 16500",total(final,"allocated_cash_cents")===1650000n);
  check("rechecked ARR 146400",total(final,"period_end_arr_cents")===14640000n);
  check("original 1500 variance still open",final.cases.some(c=>c.state==="open"&&BigInt(c.amount_cents)===-150000n));
  await auditor.page.getByRole("button",{name:"Reconciliation",exact:true}).click();await auditor.page.getByRole("button",{name:"Refresh evidence",exact:true}).click();
  await auditor.page.getByText("$45,600.00",{exact:true}).first().waitFor();await auditor.page.screenshot({path:path.join(output,"console-recovered.png"),fullPage:true});
  const downloadPromise=auditor.page.waitForEvent("download");await auditor.page.getByRole("button",{name:"Download review",exact:true}).click();const download=await downloadPromise;const exported=fs.readFileSync(await download.path(),"utf8");check("download is actual scoped JSON",JSON.parse(exported).runs.length===2);
  for(const c of Object.values(credentials))check("download excludes "+c.username+" credential",!exported.includes(c.password));
  const eu=await actor(browser,"reader_eu",{width:390,height:844});
  check("EU principal cannot read US",(await eu.page.request.get(origin+"/api/overview?entity=US")).status()===403);
  check("caller cannot override tenant",(await eu.page.request.get(origin+"/api/overview?tenant=northstar")).status()===403);
  check("EU-only records",(await overview(eu.page)).effects.every(e=>e.entity==="EU"));
  for(const [view,label] of [["overview","Reconciliation"],["operations","Exceptions & delivery"],["lineage","Source lineage"],["audit","Decision ledger"]]){
   await eu.page.getByRole("button",{name:label,exact:true}).click();await eu.page.locator("#view .panel").first().waitFor();await audit(eu.page,"mobile "+view);
   if(view==="overview")await eu.page.screenshot({path:path.join(output,"console-mobile.png"),fullPage:true});
  }
  await eu.page.getByRole("button",{name:"Sign out",exact:true}).click();await eu.page.locator("#login-screen").waitFor({state:"visible"});check("logout revokes session",(await eu.page.request.get(origin+"/api/me")).status()===401);
  check("no browser script errors",errors.length===0);
  assert.equal(execFileSync("git",["rev-parse","HEAD"],{encoding:"utf8"}).trim(),started,"source commit unchanged");
  assert.deepEqual(fingerprints(),startedFingerprints,"browser source unchanged");const hashes={};for(const name of fs.readdirSync(output))hashes[name]=crypto.createHash("sha256").update(fs.readFileSync(path.join(output,name))).digest("hex");
  fs.writeFileSync(path.join(output,"browser.json"),JSON.stringify({executed_at:new Date().toISOString(),source_commit:started,source_hash_encoding:"UTF-8 with LF line endings",source_hashes:startedFingerprints,browser:browser.version(),lane:"actual_local_postgresql_http_console",cases,accessibility,errors,initial:{billing_cents:"3900000",erp_cents:"3750000"},final:{billing_cents:String(total(final,"billing_net_cents")),erp_cents:String(total(final,"erp_net_cents"))},screenshots:hashes,native_vendor_execution:false},null,2)+"\n");
  console.log(JSON.stringify({checks:cases.length,accessibility_views:accessibility.length,script_errors:errors.length}));
 }finally{await browser.close();}
})().catch(e=>{console.error(e.message);process.exitCode=1;});
