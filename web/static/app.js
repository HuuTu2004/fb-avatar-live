const $ = (s) => document.querySelector(s);
const $$ = (s) => [...document.querySelectorAll(s)];
let state = null;

function toast(message, error = false) {
  const box = $('#toast');
  box.textContent = message;
  box.className = error ? 'show error' : 'show';
  clearTimeout(toast.timer);
  toast.timer = setTimeout(() => box.className = '', 3200);
}

async function api(path, options = {}) {
  const response = await fetch(path, options);
  if (!response.ok) {
    let message = `Lỗi ${response.status}`;
    try { message = (await response.json()).detail || message; } catch (_) {}
    throw new Error(message);
  }
  const type = response.headers.get('content-type') || '';
  return type.includes('application/json') ? response.json() : response.blob();
}

function busy(button, value, text = 'Đang xử lý…') {
  if (value) { button.dataset.label = button.textContent; button.textContent = text; button.classList.add('busy'); }
  else { button.textContent = button.dataset.label || button.textContent; button.classList.remove('busy'); }
}

function serviceLabel(value) {
  return value === 'RUNNING' ? 'Đang chạy' : value === 'STARTING' ? 'Đang khởi động' : 'Đã dừng';
}

function updateStatus(data) {
  state = data;
  const running = data.services.pipeline === 'RUNNING';
  $('#metric-pipeline').textContent = serviceLabel(data.services.pipeline);
  $('#metric-preview').textContent = data.preview_ready ? 'Có tín hiệu' : 'Chờ tín hiệu';
  $('#metric-openai').textContent = data.configured.openai ? data.openai_model || 'Đã cấu hình' : 'Chưa cấu hình';
  const channelCount = Number(data.configured.facebook_stream) + Number(data.configured.tiktok_stream);
  $('#metric-channels').textContent = channelCount ? `${channelCount} kênh sẵn sàng` : 'Thiếu cấu hình';
  $('#global-dot').classList.toggle('ok', running);
  $('#global-text').textContent = running ? 'Pipeline đang chạy' : 'Pipeline đang dừng';

  const checks = [
    ['OpenAI API', data.configured.openai], ['Voice', data.configured.voice], ['Avatar', data.configured.avatar]
  ];
  if (['facebook', 'both'].includes(data.comment_source)) checks.push(['Facebook comments', data.configured.facebook]);
  if (['tiktok', 'both'].includes(data.comment_source)) checks.push(['TikTok comments', data.configured.tiktok]);
  if (data.configured.facebook_stream || data.configured.tiktok_stream) checks.push(['Đích phát', true]);
  checks.push(['MuseTalk', data.services.musetalk === 'RUNNING']);
  $('#checklist').innerHTML = checks.map(([name, ok]) => `<div class="check"><b>${name}</b><i class="${ok ? 'ok' : ''}">${ok ? 'Sẵn sàng' : 'Chưa xong'}</i></div>`).join('');
  $('#fb-object').value = data.fb_object_id || '';
  $('#comment-source').value = data.comment_source || 'facebook';
  $('#tiktok-user').value = data.tiktok_unique_id ? `@${data.tiktok_unique_id}` : '';
  $('#openai-key-hint').textContent = data.has_openai_key ? 'Đã có key lưu trên máy. Giữ trống để tiếp tục dùng.' : 'Key chỉ được lưu trong .env trên máy Vast.';
  if (data.openai_model && ![...$('#openai-model').options].some(o => o.value === data.openai_model)) {
    $('#openai-model').add(new Option(data.openai_model, data.openai_model));
    $('#openai-model').value = data.openai_model;
  }
  if (data.configured.avatar) loadAvatar();
  if (data.preview_ready) loadPreview();
}

async function refreshStatus(showError = false) {
  try { updateStatus(await api('/api/status')); }
  catch (error) { if (showError) toast(error.message, true); }
}

function loadPreview(force = false) {
  const frame = $('#live-frame');
  if (force || !frame.src) frame.src = `/preview/avatar?controls=true&muted=true&autoplay=true&playsInline=true&t=${Date.now()}`;
  frame.classList.add('ready');
  $('#stage-empty').style.display = 'none';
}

function loadAvatar() {
  const video = $('#avatar-video');
  if (!video.src) video.src = `/api/avatar/file?t=${Date.now()}`;
  video.style.display = 'block';
  $('#avatar-empty').style.display = 'none';
}

async function loadVoices() {
  const data = await api('/api/voices');
  const select = $('#voice-select');
  select.innerHTML = '';
  data.voices.forEach(v => select.add(new Option(`${v.name || v.id}${v.custom ? ' · custom' : ''}`, v.id)));
  select.value = data.selected;
  $('#voice-name').textContent = select.options[select.selectedIndex]?.text || data.selected;
}

$$('.nav').forEach(button => button.addEventListener('click', () => {
  $$('.nav').forEach(x => x.classList.remove('active'));
  $$('.page').forEach(x => x.classList.remove('active'));
  button.classList.add('active');
  $(`#${button.dataset.page}`).classList.add('active');
  $('#page-title').textContent = button.textContent.trim();
  if (button.dataset.page === 'voice') loadVoices().catch(e => toast(e.message, true));
}));

$('#refresh').onclick = () => refreshStatus(true);
$('#reload-preview').onclick = () => loadPreview(true);
$('#toggle-audio').onclick = async () => {
  const video = $('#live-frame').contentDocument?.querySelector('video');
  if (!video) return toast('Preview chưa sẵn sàng, hãy tải lại preview', true);
  video.muted = false;
  video.volume = 1;
  try { await video.play(); $('#toggle-audio').textContent = 'Âm thanh đã bật'; toast('Đã bật âm thanh preview'); }
  catch (_) { toast('Chrome đang chặn âm thanh; hãy bấm biểu tượng loa trên video', true); }
};
$('#show-key').onclick = () => {
  const input = $('#openai-key'); input.type = input.type === 'password' ? 'text' : 'password';
  $('#show-key').textContent = input.type === 'password' ? 'Hiện' : 'Ẩn';
};

async function openaiAction(save) {
  const button = save ? $('#save-openai') : $('#test-openai'); busy(button, true);
  try {
    const data = await api(save ? '/api/openai/save' : '/api/openai/test', {
      method: 'POST', headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({api_key: $('#openai-key').value, model: $('#openai-model').value})
    });
    if (data.models) {
      const current = $('#openai-model').value || state?.openai_model;
      $('#openai-model').innerHTML = '<option value="">Chọn model</option>';
      data.models.forEach(model => $('#openai-model').add(new Option(model, model)));
      if (current && data.models.includes(current)) $('#openai-model').value = current;
      else {
        const preferred = ['gpt-4.1-mini', 'gpt-4o-mini'].find(model => data.models.includes(model));
        $('#openai-model').value = preferred || data.models.find(model => model.startsWith('gpt-')) || '';
      }
    }
    $('#openai-result').className = 'result ok';
    $('#openai-result').textContent = save ? `Đã lưu model ${data.model}.` : `Kết nối thành công · ${data.models.length} model khả dụng.`;
    toast(save ? 'Đã lưu cấu hình OpenAI' : 'OpenAI API hoạt động');
    refreshStatus();
  } catch (error) { $('#openai-result').className = 'result'; $('#openai-result').textContent = error.message; toast(error.message, true); }
  finally { busy(button, false); }
}
$('#test-openai').onclick = () => openaiAction(false);
$('#save-openai').onclick = () => openaiAction(true);

$('#voice-file').onchange = async () => {
  const file = $('#voice-file').files[0]; if (!file) return;
  const zone = $('#voice-drop'); zone.classList.add('busy');
  const form = new FormData(); form.append('file', file);
  try { const data = await api('/api/voices/upload', {method:'POST', body:form}); toast(`Đã cài giọng ${data.selected}`); await loadVoices(); }
  catch (error) { toast(error.message, true); }
  finally { zone.classList.remove('busy'); $('#voice-file').value = ''; }
};
$('#voice-select').onchange = () => $('#voice-name').textContent = $('#voice-select').options[$('#voice-select').selectedIndex]?.text || '—';
$('#select-voice').onclick = async () => {
  try { await api('/api/voices/select', {method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({voice:$('#voice-select').value})}); toast('Đã đổi giọng'); refreshStatus(); }
  catch (error) { toast(error.message, true); }
};
$('#preview-voice').onclick = async () => {
  const button = $('#preview-voice'); busy(button, true, 'Đang tổng hợp…');
  try {
    const blob = await api('/api/voices/preview', {method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({text:$('#voice-text').value})});
    const audio = $('#voice-audio'); if (audio.src) URL.revokeObjectURL(audio.src); audio.src = URL.createObjectURL(blob); audio.style.display = 'block'; await audio.play();
  } catch (error) { toast(error.message, true); }
  finally { busy(button, false); }
};

$('#upload-avatar').onclick = async () => {
  const file = $('#avatar-file').files[0]; if (!file) return toast('Hãy chọn video avatar', true);
  const button = $('#upload-avatar'); busy(button, true, 'Đang chuẩn hóa video…');
  const form = new FormData(); form.append('file', file);
  try { await api('/api/avatar/upload', {method:'POST',body:form}); $('#avatar-video').src = ''; loadAvatar(); toast('Đã cập nhật avatar'); refreshStatus(); }
  catch (error) { toast(error.message, true); }
  finally { busy(button, false); }
};

$('#save-live').onclick = async () => {
  const button = $('#save-live'); busy(button, true);
  try {
    await api('/api/live/save', {method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({
      comment_source:$('#comment-source').value,
      fb_object_id:$('#fb-object').value, fb_page_access_token:$('#fb-token').value,
      facebook_stream_url:$('#fb-stream').value, tiktok_unique_id:$('#tiktok-user').value,
      tiktok_stream_url:$('#tiktok-stream').value, poll_seconds:Number($('#poll-seconds').value), batch_seconds:Number($('#batch-seconds').value)
    })});
    $('#fb-token').value = ''; $('#fb-stream').value = ''; $('#tiktok-stream').value = ''; toast('Đã lưu cấu hình kênh Live'); refreshStatus();
  } catch (error) { toast(error.message, true); }
  finally { busy(button, false); }
};
$('#start-live').onclick = async () => {
  const button = $('#start-live'); busy(button, true, 'Đang khởi động…');
  try { const data = await api('/api/live/start',{method:'POST'}); toast(data.message); setTimeout(()=>refreshStatus(),2500); }
  catch (error) { toast(error.message,true); }
  finally { busy(button,false); }
};
$('#stop-live').onclick = async () => {
  try { await api('/api/live/stop',{method:'POST'}); toast('Đã dừng phiên phát'); refreshStatus(); }
  catch (error) { toast(error.message,true); }
};
$('#test-live').onclick = async () => {
  try { const data=await api('/api/live/test',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({message:$('#test-message').value})}); toast(data.message); }
  catch(error){toast(error.message,true)}
};

setInterval(() => $('#clock').textContent = new Intl.DateTimeFormat('vi-VN',{dateStyle:'medium',timeStyle:'medium'}).format(new Date()),1000);
refreshStatus(); loadVoices().catch(()=>{}); setInterval(()=>refreshStatus(),5000);
