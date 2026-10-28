const $ = (s) => document.querySelector(s);
const $$ = (s) => [...document.querySelectorAll(s)];
let state = null;
let liveFormLoaded = false;
let commentsLoading = false;
let commentsSignature = null;

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
    try {
      const detail = (await response.json()).detail;
      message = Array.isArray(detail) ? detail.map(item => item.msg).join('; ') : detail || message;
    } catch (_) {}
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
  return value === 'RUNNING' ? 'Đang chạy' : value === 'STARTING' ? 'Đang khởi động' : value === 'MISSING' ? 'Chưa cài dịch vụ' : 'Đã dừng';
}

function updateStatus(data) {
  state = data;
  const running = data.services.pipeline === 'RUNNING';
  $('#metric-pipeline').textContent = serviceLabel(data.services.pipeline);
  $('#metric-preview').textContent = data.preview_ready ? 'Có tín hiệu' : 'Chờ tín hiệu';
  $('#metric-openai').textContent = data.configured.openai ? data.openai_model || 'Đã cấu hình' : 'Chưa cấu hình';
  const channelCount = Number(data.configured.facebook_stream);
  $('#metric-channels').textContent = data.services.forwarder === 'RUNNING' ?
    (data.forwarder_health?.facebook === 'sending' ? 'Facebook đang gửi' : 'Facebook đang thử kết nối') :
    channelCount ? `${channelCount} kênh đã lưu` : 'Chưa cấu hình';
  $('#global-dot').classList.toggle('ok', running);
  $('#global-text').textContent = running ? 'Pipeline đang chạy' : 'Pipeline đang dừng';

  const salesDemo = data.comment_source === 'demo' && data.demo_sales_mode;
  const checks = [
    [salesDemo ? 'Chia sẻ liên tục' : 'OpenAI API', salesDemo || data.configured.openai], ['Voice', data.configured.voice], ['Avatar', data.configured.avatar]
  ];
  if (['facebook', 'both'].includes(data.comment_source)) checks.push(['Facebook comments', data.configured.facebook]);
  if (data.comment_source === 'facebook_personal') checks.push(['Facebook cá nhân · SSN', data.configured.facebook_personal]);
  if (['tiktok', 'both'].includes(data.comment_source)) checks.push(['TikTok comments', data.configured.tiktok]);
  if (data.configured.facebook_stream) checks.push(['Đích phát đã lưu', true]);
  checks.push(['MuseTalk', data.services.musetalk === 'RUNNING']);
  $('#checklist').innerHTML = checks.map(([name, ok]) => `<div class="check"><b>${name}</b><i class="${ok ? 'ok' : ''}">${ok ? 'Sẵn sàng' : 'Chưa xong'}</i></div>`).join('');
  if (!liveFormLoaded) {
    $('#fb-object').value = data.fb_object_id || '';
    $('#fb-server').value = data.facebook_server_url || 'rtmps://live-api-s.facebook.com:443/rtmp/';
    $('#comment-source').value = data.comment_source || 'demo';
    $('#demo-sales-mode').checked = Boolean(data.demo_sales_mode);
    $('#tiktok-user').value = data.tiktok_unique_id ? `@${data.tiktok_unique_id}` : '';
    $('#poll-seconds').value = data.poll_seconds || 2;
    $('#batch-seconds').value = data.batch_seconds || 2;
    liveFormLoaded = true;
    showChannelSettings();
  }
  const facebookRunning = data.services.forwarder === 'RUNNING';
  $('#ssn-status').textContent = data.has_ssn_session_id ? 'Đã lưu Session ID trên máy Vast.' : 'Chưa lưu Session ID.';
  const facebookHealth = data.forwarder_health?.facebook;
  $('#facebook-status').textContent = facebookRunning && facebookHealth === 'retrying' ?
    'Kết nối Facebook bị ngắt; hệ thống đang thử lại. Dừng gửi rồi kiểm tra URL và khóa luồng trong Live Producer.' :
    facebookRunning && facebookHealth === 'sending' ? 'Đang gửi tín hiệu đến Facebook · kiểm tra hình/tiếng trong Live Producer' :
    facebookRunning ? 'Đang kết nối Facebook · dừng gửi trước khi sửa cấu hình' :
    data.has_fb_stream_url ? (data.preview_ready ? 'Đã lưu khóa luồng · sẵn sàng gửi tín hiệu' : 'Đã lưu khóa luồng · khởi động luồng trước khi gửi') : 'Chưa lưu khóa luồng Facebook';
  $('#facebook-status').classList.toggle('ok', facebookHealth === 'sending');
  $('#start-facebook').disabled = facebookRunning || !data.has_fb_stream_url || !data.preview_ready;
  $('#stop-facebook').disabled = !facebookRunning;
  $('#live-save-hint').hidden = !facebookRunning;
  $('#openai-key-hint').textContent = data.has_openai_key ? 'Đã có key lưu trên máy. Giữ trống để tiếp tục dùng.' : 'Key chỉ được lưu trong .env trên máy Vast.';
  if (data.openai_model && ![...$('#openai-model').options].some(o => o.value === data.openai_model)) {
    $('#openai-model').add(new Option(data.openai_model, data.openai_model));
    $('#openai-model').value = data.openai_model;
  }
  if (data.configured.avatar) loadAvatar();
}

function showChannelSettings() {
  const source = $('#comment-source').value;
  $('#fb-page-comments').hidden = !['facebook', 'both'].includes(source);
  $('#fb-personal-comments').hidden = source !== 'facebook_personal';
  $('#tiktok-settings').hidden = !['tiktok', 'both'].includes(source);
}

function renderComments(comments) {
  const signature = comments.map(item => `${item.id}:${item.status}`).join('|');
  if (signature === commentsSignature) return;
  commentsSignature = signature;
  const list = $('#comments-list');
  const scrollTop = list.scrollTop;
  const labels = {tiktok:'TikTok', facebook_personal:'Facebook cá nhân', facebook_page:'Facebook Page', dashboard:'Bình luận thử', other:'Nguồn khác'};
  const fragment = document.createDocumentFragment();
  for (const item of comments) {
    const row = document.createElement('div');
    row.className = 'comment-row';
    const meta = document.createElement('div');
    meta.className = 'comment-meta';
    const source = document.createElement('span');
    source.className = `comment-source source-${item.source}`;
    source.textContent = labels[item.source] || labels.other;
    const author = document.createElement('strong');
    author.textContent = item.source === 'tiktok' ? item.author.replace(/^TikTok · /, '') : item.author;
    const time = document.createElement('time');
    time.textContent = new Date(item.received_at * 1000).toLocaleTimeString('vi-VN');
    meta.append(source, author, time);
    const message = document.createElement('p');
    message.textContent = item.message;
    const status = document.createElement('small');
    status.className = item.status === 'processed' ? 'comment-done' : 'comment-pending';
    status.textContent = item.status === 'processed' ? 'AI đã xử lý' : 'Đang chờ AI';
    row.append(meta, message, status);
    fragment.append(row);
  }
  if (!comments.length) {
    const empty = document.createElement('p');
    empty.className = 'comments-empty';
    empty.textContent = 'Chưa nhận bình luận nào. Khi TikTok/Facebook đang live và nguồn bình luận đã kết nối, bình luận mới sẽ xuất hiện ở đây.';
    fragment.append(empty);
  }
  list.replaceChildren(fragment);
  list.scrollTop = scrollTop;
  $('#comments-count').textContent = `${comments.length} bình luận gần nhất`;
}

async function refreshComments() {
  if (commentsLoading) return;
  commentsLoading = true;
  try {
    const data = await api('/api/comments/recent', {cache:'no-store'});
    renderComments(data.comments || []);
  } catch (_) {
    $('#comments-count').textContent = 'Chưa cập nhật được';
  } finally { commentsLoading = false; }
}

async function refreshStatus(showError = false) {
  try { updateStatus(await api('/api/status')); }
  catch (error) { if (showError) toast(error.message, true); }
}

async function loadReplySettings() {
  try {
    const data = await api('/api/replies');
    $('#reply-persona').value = data.system_prompt;
    $('#reply-topics').value = data.topics.join('\n');
    $('#reply-address').value = data.viewer_address;
    $('#save-replies').disabled = false;
    $('#reply-result').textContent = 'Cấu hình đã lưu. Bạn có thể đổi ngay khi đang live.';
  } catch (error) { $('#reply-result').textContent = error.message; }
}

$('#reply-form').onsubmit = async (event) => {
  event.preventDefault();
  const button = $('#save-replies');
  busy(button, true);
  try {
    await api('/api/replies', {method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify({
      system_prompt: $('#reply-persona').value.trim(),
      topics: $('#reply-topics').value.split(/\r?\n/).map(topic => topic.trim()).filter(Boolean),
      viewer_address: $('#reply-address').value.trim()
    })});
    $('#reply-result').textContent = 'Đã lưu. Áp dụng từ lượt AI tạo tiếp theo, không cần dừng live.';
    toast('Đã lưu nội dung AI');
  } catch (error) { $('#reply-result').textContent = error.message; toast(error.message, true); }
  finally { busy(button, false); }
};

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

$('#refresh').onclick = () => { refreshStatus(true); loadReplySettings(); };
$('#comment-source').onchange = showChannelSettings;
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
      ssn_session_id:$('#ssn-session').value,
      facebook_server_url:$('#fb-server').value, facebook_stream_key:$('#fb-key').value,
      tiktok_unique_id:$('#tiktok-user').value,
      poll_seconds:Number($('#poll-seconds').value), batch_seconds:Number($('#batch-seconds').value),
      demo_sales_mode:$('#demo-sales-mode').checked
    })});
    $('#fb-token').value = ''; $('#ssn-session').value = ''; $('#fb-key').value = '';
    liveFormLoaded = false; toast('Đã lưu cấu hình kênh Live'); refreshStatus();
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
  try { await api('/api/live/stop',{method:'POST'}); toast('Đã dừng luồng và các kênh'); refreshStatus(); }
  catch (error) { toast(error.message,true); }
};
async function channelAction(channel, action, button) {
  busy(button, true);
  try {
    const data = await api(`/api/live/${channel}/${action}`, {method:'POST'});
    toast(data.message || `Đã dừng gửi ${channel}`);
    await refreshStatus(true);
  } catch (error) { toast(error.message, true); }
  finally { busy(button, false); }
}
$('#start-facebook').onclick = () => channelAction('facebook', 'start', $('#start-facebook'));
$('#stop-facebook').onclick = () => channelAction('facebook', 'stop', $('#stop-facebook'));
$('#test-live').onclick = async () => {
  const message = $('#test-message').value.trim();
  if (!message) { toast('Nhập bình luận trước khi gửi', true); return; }
  try { const data=await api('/api/live/test',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({message})}); $('#test-message').value=''; toast(data.message); refreshComments(); }
  catch(error){toast(error.message,true)}
};

setInterval(() => $('#clock').textContent = new Intl.DateTimeFormat('vi-VN',{dateStyle:'medium',timeStyle:'medium'}).format(new Date()),1000);
refreshStatus(); refreshComments(); loadReplySettings(); loadVoices().catch(()=>{});
setInterval(()=>refreshStatus(),5000);
setInterval(refreshComments,2000);
