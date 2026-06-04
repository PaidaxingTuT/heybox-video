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
const videoMeta = $('#videoMeta');
const videoTitle = $('#videoTitle');
const videoDesc = $('#videoDesc');

function setLoading(on) {
  spinner.classList.toggle('active', on);
  input.disabled = on;
  if (on) {
    btn.disabled = true;
    btnLabel.textContent = '解析中…';
  } else if (!cooldownTimer) {
    // 冷却倒计时进行中时不要解禁按钮
    btn.disabled = false;
    btnLabel.textContent = '解析';
  }
}

let cooldownTimer = null;

function startCooldown(seconds) {
  clearInterval(cooldownTimer);
  let left = seconds;
  btn.disabled = true;
  btnLabel.textContent = `冷却 ${left}s`;

  cooldownTimer = setInterval(() => {
    left -= 1;
    if (left <= 0) {
      clearInterval(cooldownTimer);
      cooldownTimer = null;
      btn.disabled = false;
      btnLabel.textContent = '解析';
    } else {
      btnLabel.textContent = `冷却 ${left}s`;
    }
  }, 1000);
}

function showError(msg, url, type = 'error') {
  errorEl.textContent = '';
  errorEl.classList.toggle('warning', type === 'warning');
  errorEl.appendChild(document.createTextNode(msg));

  if (url) {
    const link = document.createElement('a');
    link.href = url;
    link.target = '_blank';
    link.rel = 'noopener';
    link.textContent = '打开原链接';
    errorEl.appendChild(document.createTextNode(' '));
    errorEl.appendChild(link);
  }

  errorEl.style.display = 'block';
}

function setText(el, text) {
  const value = typeof text === 'string' ? text.trim() : '';
  el.textContent = value;
  el.style.display = value ? '' : 'none';
  return Boolean(value);
}

function renderMeta(meta = {}) {
  const hasTitle = setText(videoTitle, meta.title);
  const hasDesc = setText(videoDesc, meta.description);

  videoMeta.style.display = hasTitle || hasDesc ? 'block' : 'none';
}

async function doParse() {
  const url = input.value.trim();
  errorEl.style.display = 'none';
  errorEl.classList.remove('warning');
  result.style.display = 'none';
  renderMeta();

  if (!url) {
    showError('先粘贴链接再点解析');
    return;
  }

  setLoading(true);

  try {
    const res = await fetch('/api/parse?url=' + encodeURIComponent(url));
    const data = await res.json();

    if (res.ok) {
      if (data.captcha_required) {
        player.removeAttribute('src');
        showError(data.message || '返回验证码或操作过于频繁，无法自动解析该帖子，请稍后重试或手动下载', data.url || url, 'warning');
        if (data.cooldown) startCooldown(data.cooldown);
        return;
      }

      player.src = data.video_url;
      download.href = data.video_url;
      renderMeta(data.meta);
      result.style.display = 'block';
      result.scrollIntoView({ behavior: 'smooth', block: 'nearest' });
    } else {
      const detail = typeof data.detail === 'string' ? data.detail : '解析失败，请检查链接是否正确';
      showError(detail);
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
