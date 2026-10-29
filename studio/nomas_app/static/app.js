const $ = s => document.querySelector(s);
let mode = 'simple', imageId = null, busy = false, lastFiles = '', currentFile = '', lastJob = '';
let imageUploadVersion = 0, imageUploading = false;
let refreshVersion = 0;
function toast(text) { $('#toast').textContent = text; $('#toast').hidden = false; clearTimeout(toast.timer); toast.timer = setTimeout(()=>$('#toast').hidden=true,7000); }
async function request(url, data) {
  const options = data === undefined ? {} : {method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(data)};
  const response = await fetch(url, options);
  if (!response.ok) throw new Error(await response.text());
  return response.json();
}
document.querySelectorAll('[data-tab]').forEach(button=>button.onclick=()=>{
  mode=button.dataset.tab;
  document.querySelectorAll('[data-tab]').forEach(b=>b.classList.toggle('active',b===button));
  $('#simple-pane').hidden=mode!=='simple'; $('#json-pane').hidden=mode!=='json';
});
$('#camera-preset').onchange=()=>$('#custom-camera').hidden=$('#camera-preset').value!=='custom';
$('#image').onchange=async()=>{
  const file=$('#image').files[0]; if(!file)return;
  clearImage(false);
  const version=imageUploadVersion;
  imageUploading=true;$('#generate').disabled=true;
  try {const data=new FormData(); data.append('image',file); const r=await fetch('/api/upload',{method:'POST',body:data}); if(!r.ok)throw Error(await r.text());const uploaded=await r.json();if(version!==imageUploadVersion)return;imageId=uploaded.id;
    const preview=$('#image-preview');if(preview.src.startsWith('blob:'))URL.revokeObjectURL(preview.src);preview.src=URL.createObjectURL(file);preview.hidden=false;
  }catch(e){if(version===imageUploadVersion){$('#image').value='';toast(e.message);}}
  finally{if(version===imageUploadVersion){imageUploading=false;await refresh();}}
};
function clearImage(clearInput=true){imageUploadVersion++;imageUploading=false;imageId=null;if(clearInput)$('#image').value='';if($('#image-preview').src.startsWith('blob:'))URL.revokeObjectURL($('#image-preview').src);$('#image-preview').hidden=true;}
function clearPreview(){currentFile='';$('#video').pause();$('#video').removeAttribute('src');$('#video').load();$('#video').hidden=true;$('#empty').hidden=false;$('#export-link').hidden=true;}
$('#clear-image').onclick=()=>{clearImage();refresh();};
$('#form').onsubmit=async event=>{
  event.preventDefault(); if(busy)return;
  if(imageUploading){toast('图片仍在上传，请稍后再生成。');return;}
  const data=Object.fromEntries(new FormData($('#form')));const [width,height]=$('#resolution').value.split('x').map(Number);Object.assign(data,{width,height});
  if(mode==='simple')data.prompt_json='';else if(!data.prompt_json.trim()){toast('请填写完整世界 JSON。');return;}
  const camera=$('#camera-preset').value;data.camera=camera==='custom'?data.camera:camera?`${camera} ${data.seconds}`:'';
  if(imageId)data.image_id=imageId;
  try{await request('/api/generate',data);busy=true;$('#generate').disabled=true;await refresh();}catch(e){toast(e.message);}
};
$('#cancel').onclick=async()=>{try{await request('/api/cancel',{});toast('正在取消，等待当前 GPU 操作结束。');}catch(e){toast(e.message);}};
$('#reset').onclick=async()=>{if(!confirm('重置当前世界并释放模型与历史缓存？已导出的视频会保留。'))return;try{await request('/api/reset',{});clearImage();await refresh();}catch(e){toast(e.message);}};
$('#export').onclick=async()=>{try{await request('/api/export',{});await refresh();}catch(e){toast(e.message);}};
async function loadPaths(){
  try{const r=await request('/api/model-paths');for(const [key,value] of Object.entries(r.settings))$('#paths-form').elements.namedItem(key).value=value;}catch(e){toast(e.message);}
}
async function savePaths(values){
  const message=$('#paths-message');
  try{const r=await request('/api/model-paths',values);for(const [key,value] of Object.entries(r.settings))$('#paths-form').elements.namedItem(key).value=value;
    const missing=r.models.filter(m=>!m.ready);message.textContent=missing.length?'路径已保存；部分模型缺失，请检查下方状态或下载模型。':'路径已保存，文件大小与格式检查通过；下次生成直接从这些位置读取。';await refresh();
  }catch(e){message.textContent=e.message;toast(e.message);}
}
$('#paths-form').onsubmit=async event=>{event.preventDefault();await savePaths(Object.fromEntries(new FormData($('#paths-form'))));};
$('#default-paths').onclick=()=>savePaths({});
function play(file){currentFile=file;$('#video').src='/outputs/'+file.split('/').map(encodeURIComponent).join('/');$('#video').hidden=false;$('#empty').hidden=true;document.querySelectorAll('.clip').forEach(b=>b.classList.toggle('selected',b.dataset.file===file));}
async function refresh(){
  const version=++refreshVersion;
  try{
    const s=await request('/api/status');if(version!==refreshVersion)return;const j=s.job,w=s.session;busy=j.state==='running';
    $('#message').textContent=j.message;$('#message').style.color=j.state==='error'?'#c54b64':'';$('#progress').value=j.progress||0;$('#percent').textContent=Math.round((j.progress||0)*100)+'%';
    $('#generate').disabled=busy||imageUploading||s.models.some(m=>!m.ready);$('#generate').innerHTML=w.segments?'✦ 继续生成下一段':'✦ 开始生成';$('#cancel').disabled=!busy;$('#reset').disabled=busy;$('#export').disabled=busy||!w.segments;
    $('#save-paths').disabled=busy||w.segments>0;$('#default-paths').disabled=busy||w.segments>0;
    for(const name of ['subject','appearance','style','perspective','scene'])$('#form').elements.namedItem(name).disabled=w.segments>0;
    $('#resolution').disabled=w.segments>0;$('#image').disabled=w.segments>0;
    $('#segment-count').innerHTML=w.segments+' <small>段</small>';$('#archive').innerHTML=w.archive_gib.toFixed(2)+' <small>GiB</small>';$('#ram').innerHTML=s.ram_available_gib.toFixed(1)+' <small>GiB</small>';
    $('#session-label').textContent=w.segments?`累计 ${w.clips.reduce((sum,c)=>sum+c.seconds,0).toFixed(1)} 秒 · ${w.segments} 段`:'等待第一段画面';
    $('#models').replaceChildren(...s.models.map(m=>{const row=document.createElement('div');row.className='model-row';row.title=m.path+(m.error?'\n'+m.error:'');const name=document.createElement('span'),state=document.createElement('span');name.textContent=m.label+' · '+m.file;state.textContent=m.ready?'✓ '+(m.source==='custom'?'自定义路径':'默认路径'):'⚠ '+m.error;state.className=m.ready?'ready':'missing';row.append(name,state);return row;}));
    const files=JSON.stringify(w.clips.map(c=>c.file));if(files!==lastFiles){lastFiles=files;$('#timeline').replaceChildren();w.clips.forEach((clip,i)=>{const b=document.createElement('button');b.className='clip';b.dataset.file=clip.file;const title=document.createElement('b'),info=document.createElement('span');title.textContent=`片段 ${String(i+1).padStart(2,'0')}`;info.textContent=`${clip.seconds.toFixed(1)}s · ${clip.width}×${clip.height}`;b.append(title,info);b.onclick=()=>play(clip.file);$('#timeline').append(b);});if(w.clips.length){play(w.clips[w.clips.length-1].file);clearImage();}else{clearPreview();$('#timeline').innerHTML='<p class="timeline-empty">已完成的片段会出现在这里，点击即可预览。</p>';}}
    if(j.id&&j.id!==lastJob&&['done','error','cancelled'].includes(j.state)){lastJob=j.id;if(j.state==='error')toast(j.message);if(j.result?.file){$('#export-link').href='/outputs/'+j.result.file;$('#export-link').hidden=false;play(j.result.file);}}
  }catch(e){if(version===refreshVersion)$('#message').textContent='无法连接本地服务，请检查启动窗口。';}
}
loadPaths();refresh();setInterval(refresh,1500);
