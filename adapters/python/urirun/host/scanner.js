    const video = document.getElementById('video');
    const canvas = document.getElementById('canvas');
    const state = document.getElementById('state');
    const startBtn = document.getElementById('start');
    const torchBtn = document.getElementById('torch');
    const captureBtn = document.getElementById('capture');
    const bestBtn = document.getElementById('best');
    const bestCount = document.getElementById('bestCount');
    const quality = document.getElementById('quality');
    const scanInterval = document.getElementById('scanInterval');
    const autoIntervalLabel = document.getElementById('autoIntervalLabel');
    const startBest = document.getElementById('startBest');
    const auto = document.getElementById('auto');
    let stream = null;
    let timer = null;
    let bestRunning = false;
    let torchOn = false;
    let startCameraPromise = null;
    let startCameraClickPromise = null;
    let torchClickPromise = null;
    const scannerParams = new URLSearchParams(location.search);
    const DEFAULT_SCANNER_PARAMS = {
      autostart: '1',
      auto: '1',
      best: '1',
      count: '3',
      minScore: '45',
    };

    function applyDefaultScannerParams() {
      let changed = false;
      Object.entries(DEFAULT_SCANNER_PARAMS).forEach(([name, value]) => {
        if (!scannerParams.has(name)) {
          scannerParams.set(name, value);
          changed = true;
        }
      });
      if (!scannerParams.has('interval') && !scannerParams.has('scanInterval') && !scannerParams.has('intervalMs')) {
        scannerParams.set('interval', '1');
        changed = true;
      }
      if (!changed) return;
      const query = scannerParams.toString();
      history.replaceState(null, '', `${location.pathname}${query ? `?${query}` : ''}${location.hash || ''}`);
    }

    applyDefaultScannerParams();

    function truthyParam(name, fallback=false) {
      if (!scannerParams.has(name)) return fallback;
      const value = String(scannerParams.get(name) || '').toLowerCase();
      return !['0', 'false', 'no', 'off'].includes(value);
    }

    function numericParam(name, fallback) {
      const raw = Number(scannerParams.get(name));
      return Number.isFinite(raw) && raw > 0 ? raw : fallback;
    }

    function positivePresentNumber(options, key) {
      if (!Object.prototype.hasOwnProperty.call(options || {}, key)) return null;
      const value = Number(options[key]);
      return Number.isFinite(value) && value > 0 ? value : null;
    }

    function scanIntervalMs(options={}) {
      const seconds = positivePresentNumber(options, 'interval') ?? positivePresentNumber(options, 'intervalSeconds');
      if (seconds !== null) return seconds * 1000;
      const ms = positivePresentNumber(options, 'intervalMs');
      if (ms !== null) return ms;
      if (scannerParams.has('interval')) return numericParam('interval', 3) * 1000;
      if (scannerParams.has('scanInterval')) return numericParam('scanInterval', 3) * 1000;
      return numericParam('intervalMs', 3000);
    }

    function writeScannerUrlState() {
      const query = scannerParams.toString();
      history.replaceState(null, '', `${location.pathname}${query ? `?${query}` : ''}${location.hash || ''}`);
    }

    function formatSeconds(value) {
      const rounded = Math.round(Number(value) * 10) / 10;
      return Number.isFinite(rounded) ? String(rounded).replace(/\.0$/, '') : '3';
    }

    function syncIntervalControl(options={}) {
      const seconds = formatSeconds(scanIntervalMs(options) / 1000);
      scanInterval.value = seconds;
      autoIntervalLabel.textContent = `auto every ${seconds}s`;
      return Number(seconds);
    }

    function updateIntervalFromControl() {
      const seconds = Number(scanInterval.value);
      if (!Number.isFinite(seconds) || seconds <= 0) {
        syncIntervalControl();
        return;
      }
      const normalized = formatSeconds(seconds);
      scannerParams.set('interval', normalized);
      scannerParams.delete('scanInterval');
      scannerParams.delete('intervalMs');
      writeScannerUrlState();
      syncIntervalControl();
      if (auto.checked) startAutoLoop();
      announce('interval-changed', {interval: Number(normalized)}).catch(() => {});
    }

    function setState(text, error=false) {
      state.textContent = text;
      state.className = error ? 'status error' : 'status';
    }

    // Audible/tactile confirmation that scan + OCR + identification finished.
    // 'ok' = new document saved, 'duplicate' = recognised as already archived,
    // 'superseded' = replaced a worse earlier scan, 'error' = processing failed.
    let feedbackAudioCtx = null;
    function feedbackEnabled() {
      return truthyParam('beep', true);
    }

    function unlockFeedbackAudio() {
      if (!feedbackEnabled()) return Promise.resolve(null);
      try {
        const Ctx = window.AudioContext || window.webkitAudioContext;
        if (!Ctx) return Promise.resolve(null);
        feedbackAudioCtx = feedbackAudioCtx || new Ctx();
        const resume = feedbackAudioCtx.state === 'suspended'
          ? feedbackAudioCtx.resume().catch(() => null)
          : Promise.resolve(feedbackAudioCtx);
        return resume.then(() => feedbackAudioCtx);
      } catch (_e) {
        return Promise.resolve(null);
      }
    }

    function feedbackTone(kind) {
      if (!feedbackEnabled()) return;
      try {
        if (navigator.vibrate) {
          navigator.vibrate(kind === 'error' ? [120, 60, 120] : kind === 'duplicate' ? [40, 40, 40] : 30);
        }
      } catch (_e) {}
      unlockFeedbackAudio().then((ctx) => {
        if (!ctx) return;
        // Each tone: [frequencyHz, startOffsetSec, durationSec].
        const tones = kind === 'error'
          ? [[220, 0, 0.32]]
          : kind === 'duplicate'
            ? [[620, 0, 0.09], [620, 0.13, 0.09]]
            : kind === 'superseded'
              ? [[660, 0, 0.09], [990, 0.11, 0.16]]
              : [[880, 0, 0.12], [1320, 0.12, 0.16]];
        const now = ctx.currentTime;
        for (const [freq, at, dur] of tones) {
          const osc = ctx.createOscillator();
          const gain = ctx.createGain();
          osc.type = 'sine';
          osc.frequency.value = freq;
          gain.gain.setValueAtTime(0.0001, now + at);
          gain.gain.exponentialRampToValueAtTime(0.25, now + at + 0.02);
          gain.gain.exponentialRampToValueAtTime(0.0001, now + at + dur);
          osc.connect(gain).connect(ctx.destination);
          osc.start(now + at);
          osc.stop(now + at + dur + 0.02);
        }
      }).catch(() => {});
    }

    function captureFeedbackKind(data) {
      const doc = (data && data.document) || {};
      if (doc.superseded || (data && data.superseded)) return 'superseded';
      if (doc.duplicate || (data && data.duplicate)) return 'duplicate';
      return 'ok';
    }

    function invokeURI(uri, payload={}) {
      if (window.urirun && typeof window.urirun.invoke === 'function') {
        return window.urirun.invoke(uri, payload);
      }
      return fetch('/api/uri/invoke', {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({uri, payload})
      }).then((response) => response.json());
    }

    async function announce(event, extra={}) {
      try {
        await invokeURI('scanner://host/session/command/log', {
          event,
          href: location.href,
          width: window.innerWidth,
          height: window.innerHeight,
          userAgent: navigator.userAgent,
          at: new Date().toISOString(),
          ...extra
        });
      } catch (_) {}
    }

    async function startCamera(options={}) {
      if (stream && stream.getVideoTracks && stream.getVideoTracks().some((track) => track.readyState === 'live')) {
        await waitForVideoReady();
        refreshTorchButton();
        return cameraStatus();
      }
      stream = await navigator.mediaDevices.getUserMedia({
        audio: false,
        video: {
          facingMode: { ideal: 'environment' },
          width: { ideal: 2560 },
          height: { ideal: 1440 }
        }
      });
      video.srcObject = stream;
      if (video.play) await video.play().catch(() => {});
      await waitForVideoReady();
      captureBtn.disabled = false;
      bestBtn.disabled = false;
      refreshTorchButton();
      setState('camera ready');
      await announce('camera-started', {tracks: stream.getVideoTracks().map((track) => track.label)});
      const shouldStartBest = Object.prototype.hasOwnProperty.call(options || {}, 'startBest') ? !!options.startBest : startBest.checked;
      if (auto.checked) startAutoLoop();
      if (shouldStartBest) {
        setTimeout(() => bestPdf(options || {}).catch((err) => setState(err.message, true)), 350);
      }
      return cameraStatus();
    }

    function runStartCamera(options={}) {
      if (!startCameraPromise) {
        startCameraPromise = startCamera(options).finally(() => {
          startCameraPromise = null;
        });
      }
      return startCameraPromise;
    }

    function beginStartCamera(options={}) {
      const promise = runStartCamera(options);
      startCameraClickPromise = promise;
      promise.finally(() => {
        if (startCameraClickPromise === promise) startCameraClickPromise = null;
      });
      return promise;
    }

    function dispatchRemoteButtonClick(button) {
      button.classList.add('remote-click');
      const makeEvent = (name) => new Event(name, {bubbles: true, cancelable: true});
      try {
        button.dispatchEvent(makeEvent('pointerdown'));
        button.dispatchEvent(makeEvent('mousedown'));
        button.dispatchEvent(makeEvent('pointerup'));
        button.dispatchEvent(makeEvent('mouseup'));
        button.click();
      } finally {
        setTimeout(() => button.classList.remove('remote-click'), 450);
      }
    }

    async function clickStartCameraButton(payload={}) {
      if (Object.prototype.hasOwnProperty.call(payload || {}, 'startBest')) {
        startBest.checked = !!payload.startBest;
      }
      setState('URI click Start camera');
      dispatchRemoteButtonClick(startBtn);
      const status = await (startCameraClickPromise || beginStartCamera(payload || {}));
      return {ok: true, clicked: true, button: 'Start camera', uri: 'scanner://page/ui/button/start-camera/command/click', status};
    }

    function cameraTrack() {
      return stream && stream.getVideoTracks ? stream.getVideoTracks()[0] : null;
    }

    function torchInfo() {
      const track = cameraTrack();
      let supported = false;
      let settings = {};
      if (track) {
        try {
          const capabilities = track.getCapabilities ? track.getCapabilities() : {};
          supported = !!(capabilities && Object.prototype.hasOwnProperty.call(capabilities, 'torch'));
        } catch (_) {}
        try {
          settings = track.getSettings ? track.getSettings() : {};
        } catch (_) {}
      }
      return {
        supported,
        enabled: torchOn,
        ready: !!track,
        label: track ? track.label : '',
        settings: {torch: Object.prototype.hasOwnProperty.call(settings, 'torch') ? settings.torch : null}
      };
    }

    function refreshTorchButton() {
      const info = torchInfo();
      torchBtn.disabled = !info.supported;
      torchBtn.textContent = torchOn ? 'Light on' : 'Light off';
      torchBtn.className = torchOn ? 'primary' : '';
      return info;
    }

    async function setTorch(enabled=true) {
      if (!stream) {
        await runStartCamera({startBest: false});
      }
      const track = cameraTrack();
      if (!track) throw new Error('camera stream not ready');
      const capabilities = track.getCapabilities ? track.getCapabilities() : {};
      if (track.getCapabilities && !Object.prototype.hasOwnProperty.call(capabilities || {}, 'torch')) {
        refreshTorchButton();
        throw new Error('torch not supported by this browser/camera');
      }
      await track.applyConstraints({advanced: [{torch: !!enabled}]});
      torchOn = !!enabled;
      const info = refreshTorchButton();
      setState(torchOn ? 'light on' : 'light off');
      await announce('torch-changed', {enabled: torchOn, supported: info.supported});
      return {ok: true, uri: 'scanner://page/camera/command/torch', enabled: torchOn, torch: info, status: cameraStatus()};
    }

    async function clickTorchButton(payload={}) {
      if (!stream) {
        await runStartCamera({startBest: false});
      }
      const info = refreshTorchButton();
      if (!info.supported) throw new Error('torch not supported by this browser/camera');
      if (Object.prototype.hasOwnProperty.call(payload || {}, 'enabled')) {
        torchBtn.dataset.nextTorch = payload.enabled ? '1' : '0';
      }
      setState('URI click Light');
      dispatchRemoteButtonClick(torchBtn);
      const result = await (torchClickPromise || setTorch(Object.prototype.hasOwnProperty.call(payload || {}, 'enabled') ? !!payload.enabled : !torchOn));
      return {ok: true, clicked: true, button: 'Light', uri: 'scanner://page/ui/button/torch/command/click', result, status: cameraStatus()};
    }

    function sleep(ms) {
      return new Promise((resolve) => setTimeout(resolve, ms));
    }

    function waitForVideoReady(timeout=3000) {
      if (video.videoWidth && video.videoHeight) return Promise.resolve();
      return new Promise((resolve) => {
        let done = false;
        const finish = () => {
          if (done) return;
          done = true;
          video.removeEventListener('loadedmetadata', finish);
          video.removeEventListener('canplay', finish);
          resolve();
        };
        video.addEventListener('loadedmetadata', finish);
        video.addEventListener('canplay', finish);
        setTimeout(finish, timeout);
      });
    }

    async function sendFrame(options={}) {
      if (!stream) return;
      await waitForVideoReady();
      const w = video.videoWidth || 1920;
      const h = video.videoHeight || 1080;
      canvas.width = w;
      canvas.height = h;
      canvas.getContext('2d').drawImage(video, 0, 0, w, h);
      const quality = Number(document.getElementById('quality').value || '0.92');
      const image = canvas.toDataURL('image/jpeg', quality);
      return invokeURI('scanner://host/capture/command/run', {
        source: 'phone',
        image,
        width: w,
        height: h,
        userAgent: navigator.userAgent,
        capturedAt: new Date().toISOString(),
        ...options
      });
    }

    function captureLabel(kind, duplicateLabel, supersededLabel, defaultLabel) {
      if (kind === 'duplicate') return duplicateLabel;
      if (kind === 'superseded') return supersededLabel;
      return defaultLabel;
    }

    function resolveMinScore(options={}) {
      return Number(Object.prototype.hasOwnProperty.call(options || {}, 'minScore') ? options.minScore : numericParam('minScore', 45));
    }

    function reportRejectedScan(data) {
      const sc = data.quality && data.quality.score != null ? Number(data.quality.score).toFixed(0) : '?';
      const reasons = data.quality && Array.isArray(data.quality.reasons) ? data.quality.reasons.join(', ') : '';
      const why = data.reason || reasons || 'low quality scan';
      setState(`discarded — ${why} (score ${sc}, min ${data.minScore})`, true);
      feedbackTone('error');
      return data;
    }

    async function capture(options={}) {
      const w = video.videoWidth || 1920;
      const h = video.videoHeight || 1080;
      setState(`uploading ${w}x${h}...`);
      try {
        const data = await sendFrame({archive: true, ...options});
        if (!data || data.ok === false) throw new Error((data && data.error) || 'scan failed');
        if (data.rejected) return reportRejectedScan(data);
        const kind = captureFeedbackKind(data);
        const label = captureLabel(kind, 'already saved', 'updated', 'saved');
        const savedArtifact = data.primaryArtifact || data.documentArtifact || data.artifact || {};
        setState(`${label} ${savedArtifact.path || data.uri}`);
        feedbackTone(kind);
        return data;
      } catch (err) {
        feedbackTone('error');
        throw err;
      }
    }

    async function captureBestFrame(seriesId, frame, total) {
      setState(`frame ${frame}/${total}...`);
      const data = await sendFrame({
        archive: false,
        mode: 'best-candidate',
        seriesId,
        frameIndex: frame,
        frameCount: total
      });
      if (!data || data.ok === false) throw new Error((data && data.error) || 'candidate scan failed');
      const best = data.series && data.series.best ? data.series.best : data.candidate;
      const score = best && best.quality ? Number(best.quality.score || 0).toFixed(1) : '0.0';
      setState(`frame ${frame}/${total}, best score ${score}`);
      return best;
    }

    async function finishBestSeries(seriesId, minScore) {
      const finalData = await invokeURI('scanner://host/best/command/finish', {seriesId, minScore});
      if (!finalData || finalData.ok === false) throw new Error((finalData && finalData.error) || 'best scan failed');
      const kind = captureFeedbackKind(finalData);
      const label = captureLabel(kind, 'already saved', 'updated best', 'saved best');
      setState(`${label} ${finalData.document && finalData.document.path ? finalData.document.path : finalData.uri}`);
      feedbackTone(kind);
      return finalData;
    }

    async function bestPdf(options={}) {
      if (!stream || bestRunning) return;
      bestRunning = true;
      bestBtn.disabled = true;
      captureBtn.disabled = true;
      const selectedCount = Number(document.getElementById('bestCount')?.value || 0);
      const total = Number(selectedCount > 0 ? selectedCount : (options.count || numericParam('count', 3)));
      const intervalMs = scanIntervalMs(options);
      const seriesId = `best-${Date.now()}-${Math.random().toString(16).slice(2)}`;
      try {
        for (let frame = 1; frame <= total; frame += 1) {
          await captureBestFrame(seriesId, frame, total);
          if (frame < total) await sleep(intervalMs);
        }
        return await finishBestSeries(seriesId, resolveMinScore(options));
      } catch (err) {
        feedbackTone('error');
        throw err;
      } finally {
        bestRunning = false;
        bestBtn.disabled = !stream;
        captureBtn.disabled = !stream;
      }
    }

    function bestOptions(options={}) {
      const selectedCount = Number(document.getElementById('bestCount')?.value || 0);
      return {
        count: Number(selectedCount > 0 ? selectedCount : (options.count || numericParam('count', 3))),
        minScore: resolveMinScore(options),
        intervalMs: scanIntervalMs(options),
      };
    }

    function startAutoLoop(options={}) {
      clearInterval(timer);
      if (!auto.checked) return null;
      const run = () => {
        if (!stream || bestRunning) return;
        bestPdf(bestOptions(options)).catch((err) => setState(err.message, true));
      };
      timer = setInterval(run, scanIntervalMs(options));
      return timer;
    }

    async function beginAutonomousScanning(options={}) {
      auto.checked = Object.prototype.hasOwnProperty.call(options || {}, 'auto') ? !!options.auto : true;
      startBest.checked = Object.prototype.hasOwnProperty.call(options || {}, 'startBest') ? !!options.startBest : true;
      await announce('autonomous-start-requested', {auto: auto.checked, startBest: startBest.checked});
      const status = await runStartCamera({startBest: startBest.checked, ...bestOptions(options)});
      startAutoLoop(options);
      return {ok: true, uri: 'scanner://page/camera/command/autonomous', status, auto: auto.checked};
    }

    function cameraStatus() {
      const track = cameraTrack();
      return {
        ok: true,
        uri: 'scanner://page/camera/query/status',
        ready: !!stream,
        runningBest: bestRunning,
        width: video.videoWidth || 0,
        height: video.videoHeight || 0,
        torch: torchInfo(),
        track: track ? {label: track.label, readyState: track.readyState, enabled: track.enabled} : null,
        localActions: window.urirun && window.urirun.listActions ? window.urirun.listActions() : []
      };
    }

    function registerCameraActions() {
      if (!window.urirun || typeof window.urirun.registerAction !== 'function') return;
      window.urirun.registerAction('scanner://page/ui/button/start-camera/command/click', (payload) => clickStartCameraButton(payload || {}), {
        label: 'Click Start camera button', layer: 'page', kind: 'command', sideEffects: ['dom-click', 'camera-permission', 'media-stream']
      });
      window.urirun.registerAction('scanner://page/camera/command/start', (payload) => runStartCamera(payload || {}), {
        label: 'Start camera', layer: 'page', kind: 'command', sideEffects: ['camera-permission', 'media-stream']
      });
      window.urirun.registerAction('scanner://page/ui/button/torch/command/click', (payload) => clickTorchButton(payload || {}), {
        label: 'Click Light button', layer: 'page', kind: 'command', sideEffects: ['dom-click', 'camera-torch']
      });
      window.urirun.registerAction('scanner://page/camera/command/torch', (payload) => setTorch(!payload || !Object.prototype.hasOwnProperty.call(payload, 'enabled') ? true : !!payload.enabled), {
        label: 'Set camera light/torch', layer: 'page', kind: 'command', sideEffects: ['camera-torch']
      });
      window.urirun.registerAction('scanner://page/camera/command/scan', (payload) => capture(payload || {}), {
        label: 'Scan current frame', layer: 'page', kind: 'command', sideEffects: ['network', 'document-write']
      });
      window.urirun.registerAction('scanner://page/camera/command/best-pdf', (payload) => bestPdf(payload || {}), {
        label: 'Capture best PDF', layer: 'page', kind: 'command', sideEffects: ['camera-read', 'network', 'document-write']
      });
      window.urirun.registerAction('scanner://page/camera/command/autonomous', (payload) => beginAutonomousScanning(payload || {}), {
        label: 'Autonomous receipt/invoice scanning', layer: 'page', kind: 'command', sideEffects: ['camera-permission', 'camera-read', 'network', 'document-write']
      });
      window.urirun.registerAction('scanner://page/camera/query/status', () => cameraStatus(), {
        label: 'Camera page status', layer: 'page', kind: 'query', sideEffects: []
      });
      window.urirun.registerAction('scanner://page/actions/query/list', () => ({ok: true, actions: window.urirun.listActions()}), {
        label: 'List page actions', layer: 'page', kind: 'query', sideEffects: []
      });
      window.urirun.track('scanner_actions_ready', { count: window.urirun.listActions().length });
    }

    async function sendActionResult(action, result, error) {
      try {
        await fetch('/api/page/actions/result', {
          method: 'POST',
          headers: {'Content-Type': 'application/json'},
          body: JSON.stringify({
            id: action.id,
            target: action.target || 'scanner',
            uri: action.uri,
            ok: !error && (!result || result.ok !== false),
            error: error ? String(error.message || error) : '',
            result: result || null,
            at: new Date().toISOString()
          })
        });
      } catch (_) {}
    }

    function actionTimeoutMs(action) {
      const payload = action && action.payload ? action.payload : {};
      const raw = Number(payload.timeoutMs || payload.timeout || action.timeoutMs || 0);
      if (Number.isFinite(raw) && raw >= 1000) return Math.min(raw, 120000);
      const uri = action && action.uri ? String(action.uri) : '';
      if (uri.includes('/camera/command/best-pdf') || uri.includes('/camera/command/autonomous')) return 60000;
      if (uri.includes('/camera/command/start') || uri.includes('/ui/button/start-camera/command/click')) return 20000;
      return 15000;
    }

    function withActionTimeout(promise, action) {
      const timeoutMs = actionTimeoutMs(action);
      const uri = action && action.uri ? action.uri : 'page action';
      let timeoutId = null;
      const timeout = new Promise((_, reject) => {
        timeoutId = setTimeout(() => {
          reject(new Error(`page action timed out after ${timeoutMs}ms: ${uri}; keep the scanner tab visible and accept camera permission if prompted`));
        }, timeoutMs);
      });
      return Promise.race([promise, timeout]).finally(() => {
        if (timeoutId) clearTimeout(timeoutId);
      });
    }

    let _pollPageActionsInflight = false;
    async function pollPageActions() {
      if (_pollPageActionsInflight) return;
      if (!window.urirun || typeof window.urirun.invoke !== 'function') return;
      _pollPageActionsInflight = true;
      let data = null;
      try {
        const response = await fetch('/api/page/actions/poll?target=scanner&limit=4', {cache: 'no-store'});
        data = await response.json();
      } catch (_) {
        _pollPageActionsInflight = false;
        return;
      }
      const actions = data && Array.isArray(data.actions) ? data.actions : [];
      for (const action of actions) {
        try {
          setState(`URI ${action.uri}`);
          const result = await withActionTimeout(
            window.urirun.invoke(action.uri, action.payload || {}, {mode: action.mode || 'execute', localOnly: true}),
            action
          );
          await sendActionResult(action, result, null);
        } catch (err) {
          setState(err.message || String(err), true);
          await sendActionResult(action, null, err);
        }
      }
      _pollPageActionsInflight = false;
    }

    function applyInitialScannerOptions() {
      startBest.checked = truthyParam('best', startBest.checked);
      auto.checked = truthyParam('auto', auto.checked);
      const count = String(numericParam('count', Number(bestCount.value || '3')));
      if ([...bestCount.options].some((option) => option.value === count)) bestCount.value = count;
      const qualityValue = scannerParams.get('quality');
      if (qualityValue && [...quality.options].some((option) => option.value === qualityValue)) quality.value = qualityValue;
      syncIntervalControl();
    }

    applyInitialScannerOptions();
    announce('open', {autostart: truthyParam('autostart', false), auto: auto.checked, startBest: startBest.checked});
    registerCameraActions();
    setInterval(() => pollPageActions().catch(() => {}), 1000);
    window.addEventListener('pointerdown', unlockFeedbackAudio, {once: true, passive: true});
    window.addEventListener('touchstart', unlockFeedbackAudio, {once: true, passive: true});
    window.addEventListener('keydown', unlockFeedbackAudio, {once: true});
    startBtn.addEventListener('click', () => {
      unlockFeedbackAudio();
      beginStartCamera().catch((err) => {
        feedbackTone('error');
        setState(err.message, true);
      });
    });
    torchBtn.addEventListener('click', () => {
      const requested = Object.prototype.hasOwnProperty.call(torchBtn.dataset, 'nextTorch') ? torchBtn.dataset.nextTorch === '1' : !torchOn;
      delete torchBtn.dataset.nextTorch;
      const promise = setTorch(requested).catch((err) => setState(err.message, true));
      torchClickPromise = promise;
      promise.finally(() => {
        if (torchClickPromise === promise) torchClickPromise = null;
      });
    });
    captureBtn.addEventListener('click', () => {
      unlockFeedbackAudio();
      capture().catch((err) => setState(err.message, true));
    });
    bestBtn.addEventListener('click', () => {
      unlockFeedbackAudio();
      bestPdf().catch((err) => {
        bestRunning = false;
        bestBtn.disabled = !stream;
        captureBtn.disabled = !stream;
        setState(err.message, true);
      });
    });
    scanInterval.addEventListener('change', updateIntervalFromControl);
    scanInterval.addEventListener('blur', updateIntervalFromControl);
    bestCount.addEventListener('change', () => {
      const val = String(bestCount.value);
      scannerParams.set('count', val);
      writeScannerUrlState();
      announce('count-changed', {count: Number(val)}).catch(() => {});
    });
    quality.addEventListener('change', () => {
      const val = String(quality.value);
      scannerParams.set('quality', val);
      writeScannerUrlState();
      announce('quality-changed', {quality: val}).catch(() => {});
    });
    auto.addEventListener('change', () => {
      if (auto.checked && !stream) {
        beginAutonomousScanning({auto: true, startBest: startBest.checked}).catch((err) => setState(err.message, true));
      } else {
        startAutoLoop();
      }
    });
    if (truthyParam('autostart', false)) {
      setTimeout(() => {
        beginAutonomousScanning({auto: auto.checked, startBest: startBest.checked}).catch((err) => {
          setState(`camera permission needed: ${err.message || err}`, true);
        });
      }, 350);
    }
