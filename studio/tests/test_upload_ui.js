// Exercise real UI handlers with controllable network completion order, without a server.
const fs = require('node:fs');
const vm = require('node:vm');
const assert = require('node:assert/strict');
const path = require('node:path');
const source = fs.readFileSync(process.argv[2] || path.join(__dirname, '../nomas_app/static/app.js'), 'utf8');
function fixture(deferStatuses=false) {
  const elements = new Map();
  const get = key => {
    if (!elements.has(key)) elements.set(key, {value: '', src: '', files: [], hidden: true, disabled: false,
      style: {}, elements: {namedItem: () => ({value: ''})}, pause(){}, load(){}, removeAttribute(){},
      replaceChildren(){}, append(){}, classList:{toggle(){}}});
    return elements.get(key);
  };
  get('#resolution').value = '1280x704';
  const pending = [], statuses = [], requests = [];
  let acceptActions=false;
  const context = vm.createContext({
    document: {querySelector: get, querySelectorAll: () => [], createElement: () => ({})},
    FormData: class {append(){} *[Symbol.iterator](){yield ['prompt_json', '']; yield ['seconds', '2'];}},
    fetch: (url, options) => {
      requests.push(url);
      if (url === '/api/upload') return new Promise(resolve => pending.push(resolve));
      if (url === '/api/status' && deferStatuses) return new Promise((resolve,reject) => statuses.push({resolve,reject}));
      if (url === '/api/generate' && acceptActions) return Promise.resolve({ok:true,json:async()=>({id:'accepted'})});
      return Promise.reject(new Error('No real network in UI fixture'));
    },
    URL: {createObjectURL: file => 'blob:' + file.name, revokeObjectURL(){}},
    setTimeout: () => 1, clearTimeout(){}, setInterval(){}, console, confirm: () => false,
  });
  vm.runInContext(source, context);
  const select = name => {get('#image').files = [{name}];get('#image').value='C:\\fakepath\\'+name;return get('#image').onchange();};
  const finish = (index, id) => pending[index]({ok: true, json: async () => ({id})});
  const fail = index => pending[index]({ok:false,text:async()=> 'Upload rejected'});
  const status = (index, state) => statuses[index].resolve({ok:true,json:async()=>({
    job:{state,message:state,progress:0}, session:{segments:0,archive_gib:0,clips:[]},models:[],ram_available_gib:40,
  })});
  return {get, select, finish, fail, requests, context, status, statuses,acceptActions(){acceptActions=true;}};
}
async function main() {
  const checks = [];
  async function run(name, test) {try {await test(); checks.push({name, passed:true});} catch(error) {checks.push({name, passed:false, error:String(error)});}}
  await run('Clearing an in-flight upload keeps image cleared', async () => {
    const f=fixture(), upload=f.select('A'); f.get('#clear-image').onclick(); f.finish(0,'a'.repeat(32)); await upload;
    assert.equal(f.get('#image-preview').hidden, true);
    assert.equal(vm.runInContext('imageId',f.context),null);
  });
  await run('Late older upload cannot replace newer selection', async () => {
    const f=fixture(), first=f.select('A'), second=f.select('B');
    f.finish(1,'b'.repeat(32)); await second; f.finish(0,'a'.repeat(32)); await first;
    assert.equal(f.get('#image-preview').src,'blob:B');
    assert.equal(vm.runInContext('imageId',f.context),'b'.repeat(32));
  });
  await run('Generate waits until selected image is uploaded', async () => {
    const f=fixture(), upload=f.select('A');
    await f.get('#form').onsubmit({preventDefault(){}});
    assert.equal(f.requests.includes('/api/generate'),false);
    f.finish(0,'a'.repeat(32)); await upload;
  });
  await run('Failed upload clears the selected file and previous image ID', async () => {
    const f=fixture(), upload=f.select('A');
    f.finish(0,'a'.repeat(32)); await upload;
    const failed=f.select('B');
    f.fail(1); await failed;
    assert.equal(vm.runInContext('imageId',f.context),null);
    assert.equal(f.get('#image').value,'');
    assert.equal(f.get('#image-preview').hidden,true);
  });
  await run('Older idle status cannot override a newer running status', async () => {
    const f=fixture(true), older=vm.runInContext('refresh()',f.context), newer=vm.runInContext('refresh()',f.context);
    f.status(2,'running'); await newer; f.status(1,'idle'); await older;
    assert.equal(f.get('#message').textContent,'running');
    assert.equal(f.get('#generate').disabled,true);
  });
  await run('Older failed status cannot replace a newer successful status', async () => {
    const f=fixture(true), older=vm.runInContext('refresh()',f.context), newer=vm.runInContext('refresh()',f.context);
    f.status(2,'running'); await newer; f.statuses[1].reject(new Error('older request disconnected')); await older;
    assert.equal(f.get('#message').textContent,'running');
  });
  await run('A pending newer poll does not starve completed status responses', async () => {
    const f=fixture(true), completed=vm.runInContext('refresh()',f.context), pending=vm.runInContext('refresh()',f.context);
    f.status(1,'running'); await completed;
    assert.equal(f.get('#message').textContent,'running');
    assert.equal(f.get('#generate').disabled,true);
    f.status(2,'running'); await pending;
  });
  await run('A status captured before accepted generation cannot undo local busy state', async () => {
    const f=fixture(true), older=vm.runInContext('refresh()',f.context);f.acceptActions();
    const submission=f.get('#form').onsubmit({preventDefault(){}});
    await new Promise(resolve=>setImmediate(resolve));
    f.status(1,'idle');await older;
    assert.equal(vm.runInContext('busy',f.context),true);
    assert.equal(f.get('#generate').disabled,true);
    f.status(2,'running');await submission;
  });
  console.log(JSON.stringify({passed:checks.every(x=>x.passed),checks},null,2));
  process.exitCode=checks.every(x=>x.passed)?0:1;
}
main().catch(error=>{console.error(error); process.exitCode=1;});
