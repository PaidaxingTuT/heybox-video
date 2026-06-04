const $ = (s) => document.querySelector(s);

const input    = $('#urlInput');
const btn      = $('#parseBtn');
const spinner  = $('#spinner');
const btnLabel = $('#btnLabel');
const errorEl  = $('#errorMsg');
const result   = $('#resultCard');
const player   = $('#player');
const download = $('#downloadBtn');
const copyBtn  = $('#copyBtn');

function setLoading(on) {
  btn.disabled = on;
  input.disabled = on;
  spinner.classList.toggle('active', on);
  btnLabel.textContent = on ? '解析中…' : '解析';
}

function showError(msg) {
  errorEl.textContent = msg;
  errorEl.style.display = 'block';
}

async function doParse() {
  const url = input.value.trim();
  errorEl.style.display = 'none';
  result.style.display = 'none';

  if (!url) {
    showError('先粘贴链接再点解析');
    return;
  }

  setLoading(true);

  try {
    const res = await fetch('/api/parse?url=' + encodeURIComponent(url));
    const data = await res.json();

    if (res.ok) {
      player.src = data.video_url;
      download.href = data.video_url;
      result.style.display = 'block';
      result.scrollIntoView({ behavior: 'smooth', block: 'nearest' });
    } else {
      showError(data.detail || '解析失败，请检查链接是否正确');
    }
  } catch (e) {
    showError('网络连接失败，请确认后端服务已启动');
  } finally {
    setLoading(false);
  }
}

async function doCopy() {
  try {
    await navigator.clipboard.writeText(player.src);
    alert('已复制到剪贴板');
  } catch {
    const ta = document.createElement('textarea');
    ta.value = player.src;
    ta.style.position = 'fixed';
    ta.style.opacity = '0';
    document.body.appendChild(ta);
    ta.select();
    document.execCommand('copy');
    document.body.removeChild(ta);
    alert('已复制到剪贴板');
  }
}

btn.addEventListener('click', doParse);
input.addEventListener('keydown', (e) => {
  if (e.key === 'Enter') doParse();
});
copyBtn.addEventListener('click', doCopy);
