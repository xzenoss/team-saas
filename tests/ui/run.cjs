const fs=require('fs'),path=require('path'),http=require('http'),assert=require('node:assert/strict');
const {chromium}=require('playwright');
const source=path.resolve(__dirname,'../../static');
const results=[];let browser;
const server=http.createServer((req,res)=>{const u=new URL(req.url,'http://localhost');const name=u.pathname==='/'?'index.html':u.pathname.slice(1);const file=path.resolve(source,name);if(!file.startsWith(source+path.sep)||!fs.existsSync(file)||!fs.statSync(file).isFile()){res.writeHead(404);res.end();return;}res.setHeader('Content-Type',({'.html':'text/html','.js':'text/javascript','.css':'text/css'})[path.extname(file)]||'application/octet-stream');res.end(fs.readFileSync(file));});
async function check(page,name){await page.addScriptTag({path:require.resolve('axe-core/axe.min.js')});const violations=await page.evaluate(async()=> (await axe.run(document,{runOnly:{type:'tag',values:['wcag2a','wcag2aa','wcag21a','wcag21aa','wcag22aa']}})).violations.map(v=>({id:v.id,targets:v.nodes.map(n=>n.target)})));const fits=await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth);results.push({name,violations,pageFitsViewport:fits});assert.deepEqual(violations,[],name+' accessibility');assert(fits,name+' overflow');}
async function text(page,selector,pattern){await page.waitForFunction(({selector,pattern})=>new RegExp(pattern).test(document.querySelector(selector)?.textContent||''),{selector,pattern});}

async function assertControlsFit(page,name){
 const clipped=await page.evaluate(()=>[...document.querySelectorAll('button,aside,aside h1,aside .brand')].filter(e=>{const r=e.getBoundingClientRect();if(!r.width||!r.height)return false;if(e.tagName==='BUTTON'||e.matches('aside h1,aside .brand'))return e.scrollWidth>e.clientWidth+2||e.scrollHeight>e.clientHeight+2;return e.scrollHeight>e.clientHeight+2&&!['auto','scroll'].includes(getComputedStyle(e).overflowY);}).map(e=>({tag:e.tagName,text:e.textContent.trim().slice(0,70),width:e.clientWidth,scrollWidth:e.scrollWidth,height:e.clientHeight,scrollHeight:e.scrollHeight})));
 assert.deepEqual(clipped,[],name+' clipped controls or unscrollable navigation');
}

async function readingModes(page, name) {
  await page.setViewportSize({width:1280,height:900});
  await page.evaluate(() => {
    const nodes=[...document.querySelectorAll('body *')].filter(e=>!['SCRIPT','STYLE','SVG','PATH'].includes(e.tagName));
    const sizes=nodes.map(e=>parseFloat(getComputedStyle(e).fontSize)*2);
    nodes.forEach((e,i)=>{e.dataset.originalFont=e.style.fontSize;e.style.setProperty('font-size',sizes[i]+'px','important');});
  });
  await check(page,name+' text resized 200%');await assertControlsFit(page,name+' text resized 200%');
  await page.evaluate(()=>document.querySelectorAll('[data-original-font]').forEach(e=>{e.style.fontSize=e.dataset.originalFont;delete e.dataset.originalFont;}));
  await page.setViewportSize({width:320,height:800});
  await check(page,name+' 320 CSS px reflow');
  const spacing=await page.addStyleTag({content:'*{line-height:1.5!important;letter-spacing:.12em!important;word-spacing:.16em!important}p{margin-block-end:2em!important}'});
  await check(page,name+' user text spacing');await assertControlsFit(page,name+' user text spacing');
  await spacing.evaluate(e=>e.remove());
}
async function skipLink(page,target){
  await page.evaluate(()=>{document.activeElement?.blur();document.documentElement.scrollTop=0;});
  await page.keyboard.press('Control+Home');
  // Start at a fresh page's first sequential focus stop.
  await page.locator('.skip-link').focus();
  await page.keyboard.press('Enter');
  assert.equal(await page.evaluate(()=>document.activeElement.id),target,'skip link focus target');
}
async function trapDialog(page){
  for(let i=0;i<14;i++){await page.keyboard.press('Tab');assert(await page.evaluate(()=>document.querySelector('dialog[open]').contains(document.activeElement)),'Tab stays inside dialog');}
  for(let i=0;i<14;i++){await page.keyboard.press('Shift+Tab');assert(await page.evaluate(()=>document.querySelector('dialog[open]').contains(document.activeElement)),'Shift+Tab stays inside dialog');}
}

(async()=>{await new Promise(r=>server.listen(0,'127.0.0.1',r));const base='http://127.0.0.1:'+server.address().port;browser=await chromium.launch({headless:true,...(process.env.UI_CHROMIUM_EXECUTABLE?{executablePath:process.env.UI_CHROMIUM_EXECUTABLE,args:['--no-sandbox','--disable-dev-shm-usage']}: {})});
const page=await browser.newPage({viewport:{width:1280,height:900}});
const state={csrf:'synthetic',user:{id:'u',name:'QA User',role:'owner'},workspace:{id:'w',name:'Synthetic workspace'},members:[{id:'u',name:'QA User',role:'owner'}],projects:[{id:'p',name:'Synthetic project',description:'Fixture',color:'violet'}],tasks:[{id:'t',project_id:'p',title:'Synthetic task',description:'Fixture',status:'todo',priority:'high',assignee_id:'u',due:''}],activity:[]};
await page.route('**/api/**',route=>route.fulfill(route.request().method()==='POST'?{status:503,json:{error:'Synthetic service unavailable'}}:{json:state}));await page.goto(base);await page.waitForSelector('#main');await page.locator('nav [data-view=projects]').click();assert.equal(await page.evaluate(()=>document.activeElement.id),'main');await check(page,'project navigation and focus');await page.getByRole('button',{name:'New project',exact:true}).click();await page.locator('#project-form input[name=name]').fill('Retained project');await page.locator('#project-form button[type=submit]').click();await text(page,'#project-form .form-error','Synthetic service unavailable');assert.equal(await page.locator('#project-form input[name=name]').inputValue(),'Retained project');assert(await page.locator('#project-form .form-error').evaluate(e=>e===document.activeElement));await check(page,'failed project preserves input and focuses error');await page.keyboard.press('Escape');await page.setViewportSize({width:320,height:800});const menu=page.getByRole('button',{name:'Toggle navigation'});await menu.click();assert.equal(await menu.getAttribute('aria-expanded'),'true');await page.keyboard.press('Escape');assert.equal(await menu.getAttribute('aria-expanded'),'false');assert(await menu.evaluate(e=>e===document.activeElement));await check(page,'mobile navigation dismissal');await page.evaluate(()=>{const e=document.createElement('div');e.contentEditable='true';e.id='fixture-editor';document.body.append(e);e.focus();});await page.keyboard.press('/');assert.equal(await page.evaluate(()=>document.activeElement.id),'fixture-editor');assert.equal(await page.locator('#fixture-editor').textContent(),'/');

await page.locator('#fixture-editor').evaluate(e=>e.remove());await page.setViewportSize({width:1280,height:900});await skipLink(page,'main');await readingModes(page,'projects workspace');await page.getByRole('button',{name:'New project',exact:true}).click();await trapDialog(page);await readingModes(page,'project dialog');await page.keyboard.press('Escape');assert(await page.getByRole('button',{name:'New project',exact:true}).evaluate(e=>e===document.activeElement));

})().catch(e=>{console.error(e);process.exitCode=1;}).finally(async()=>{await browser?.close();server.close();fs.mkdirSync(path.join(__dirname,'results'),{recursive:true});fs.writeFileSync(path.join(__dirname,'results/report.json'),JSON.stringify({scope:'Mocked APIs; Chromium regression checks, not full WCAG conformance or backend security verification',results},null,2));console.log(JSON.stringify(results));});
