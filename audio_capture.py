"""
Audio and Caption Capture Engine for Google Meet Live Bot.
Provides browser injection scripts to capture WebRTC raw audio and live captions.
"""

FORCE_VISIBLE_JS = """
(function() {
  if (window._liveBotVisibilityPinned) return;
  window._liveBotVisibilityPinned = true;
  try {
    Object.defineProperty(document, 'visibilityState', {
      configurable: true, get: function() { return 'visible'; }
    });
    Object.defineProperty(document, 'hidden', {
      configurable: true, get: function() { return false; }
    });
    Object.defineProperty(document, 'webkitVisibilityState', {
      configurable: true, get: function() { return 'visible'; }
    });
    Object.defineProperty(document, 'webkitHidden', {
      configurable: true, get: function() { return false; }
    });
  } catch (e) {}
  try {
    Object.defineProperty(document, 'hasFocus', {
      configurable: true, value: function() { return true; }
    });
  } catch (e) {}
  var swallow = function(evt) {
    evt.stopImmediatePropagation();
    evt.stopPropagation();
  };
  ['visibilitychange', 'webkitvisibilitychange', 'blur', 'mozvisibilitychange'].forEach(function(type) {
    window.addEventListener(type, swallow, true);
    document.addEventListener(type, swallow, true);
  });
})();
"""

INIT_AUDIO_CAPTURE_JS = """
(function() {
  if (window._liveBotAudioPatched) return 'already_patched';
  window._liveBotAudioPatched = true;
  window._liveBotChunks = [];
  window._liveBotRecorder = null;
  window._liveBotAudioCtx = null;
  window._liveBotDest = null;
  window._liveBotAnalyser = null;
  window._liveBotTrackCount = 0;
  window._liveBotPeerConnections = [];
  window._liveBotConnectedTracks = new Set();
  window._liveBotConnectedStreams = new Set();

  window._liveBotToBase64 = function(buf) {
    var bytes = new Uint8Array(buf);
    var binary = '';
    var len = bytes.byteLength;
    for (var i = 0; i < len; i++) {
      binary += String.fromCharCode(bytes[i]);
    }
    return btoa(binary);
  };

  window._liveBotOnData = function(e) {
    if (e.data && e.data.size > 0) {
      e.data.arrayBuffer().then(function(buf) {
        window._liveBotChunks.push(window._liveBotToBase64(buf));
      }).catch(function(err) {
        console.warn('[liveBot] chunk conversion error:', err);
      });
    }
  };

  window._liveBotEnsureRecorder = function() {
    try {
      var ctx = window._liveBotAudioCtx;
      if (!ctx || ctx.state === 'closed') {
        var AudioContextClass = window.AudioContext || window.webkitAudioContext;
        ctx = new AudioContextClass({ sampleRate: 48000 });
        window._liveBotAudioCtx = ctx;
        window._liveBotDest = ctx.createMediaStreamDestination();

        // Setup AnalyserNode to measure real-time volume
        var analyser = ctx.createAnalyser();
        analyser.fftSize = 256;
        window._liveBotAnalyser = analyser;

        var destSource = ctx.createMediaStreamSource(window._liveBotDest.stream);
        destSource.connect(analyser);
      }

      if (ctx.state === 'suspended') {
        ctx.resume().catch(function(){});
      }

      if (window._liveBotRecorder && window._liveBotRecorder.state === 'recording') {
        return;
      }
      if (window._liveBotRecorder) {
        try { window._liveBotRecorder.stop(); } catch(ignore) {}
      }

      var mimeType = 'audio/webm;codecs=opus';
      if (typeof MediaRecorder !== 'undefined') {
        if (!MediaRecorder.isTypeSupported(mimeType)) {
          mimeType = 'audio/webm';
        }
        var rec = new MediaRecorder(window._liveBotDest.stream, { mimeType: mimeType });
        rec.ondataavailable = window._liveBotOnData;
        rec.start(1000); // 1-second chunks for instant capture
        window._liveBotRecorder = rec;
        console.log('[liveBot] MediaRecorder active on dest.stream (' + mimeType + ')');
      }
    } catch(e) {
      console.warn('[liveBot] recorder init error:', e);
    }
  };

  window._liveBotConnectTrack = function(track, stream, label) {
    if (!track || track.kind !== 'audio') return;
    var trackId = track.id || ('track_' + Math.random());
    if (window._liveBotConnectedTracks.has(trackId)) return;

    window._liveBotEnsureRecorder();
    try {
      var inputStream = stream || new MediaStream([track]);
      var source = window._liveBotAudioCtx.createMediaStreamSource(inputStream);
      source.connect(window._liveBotDest);
      window._liveBotConnectedTracks.add(trackId);
      window._liveBotTrackCount += 1;
      console.log('[liveBot] connected audio track (' + (label || 'unknown') + '), total tracks=' + window._liveBotTrackCount);
    } catch(e) {
      console.warn('[liveBot] failed to connect audio track:', e);
    }
  };

  window._liveBotConnectStream = function(stream, label) {
    if (!stream || !stream.getAudioTracks) return;
    var streamId = stream.id || ('stream_' + Math.random());
    if (window._liveBotConnectedStreams.has(streamId)) return;

    var tracks = stream.getAudioTracks();
    var before = window._liveBotConnectedTracks.size;
    for (var i = 0; i < tracks.length; i++) {
      window._liveBotConnectTrack(tracks[i], stream, label);
    }
    if (window._liveBotConnectedTracks.size > before) {
      window._liveBotConnectedStreams.add(streamId);
    }
  };

  // Mute local playback element so bot does not echo through speakers, but keep track capturing
  window._liveBotSilenceElement = function(el) {
    try {
      el.muted = true;
      el.defaultMuted = true;
      el.volume = 0;
      el.setAttribute('muted', 'muted');
    } catch(e) {}
  };

  // Scan both audio and video elements
  window._liveBotScanElements = function(label) {
    var audioFound = 0;
    var videoFound = 0;
    document.querySelectorAll('audio').forEach(function(el) {
      if (el.srcObject) {
        window._liveBotSilenceElement(el);
        window._liveBotConnectStream(el.srcObject, (label || 'scan') + '-audio');
        audioFound++;
      }
    });
    document.querySelectorAll('video').forEach(function(el) {
      if (el.srcObject) {
        window._liveBotSilenceElement(el);
        window._liveBotConnectStream(el.srcObject, (label || 'scan') + '-video');
        videoFound++;
      }
    });
    return { audioFound: audioFound, videoFound: videoFound };
  };

  // Intercept HTMLMediaElement.prototype.srcObject
  try {
    var _srcDesc = Object.getOwnPropertyDescriptor(HTMLMediaElement.prototype, 'srcObject');
    if (_srcDesc && _srcDesc.set) {
      var _origSet = _srcDesc.set;
      var _origGet = _srcDesc.get;
      Object.defineProperty(HTMLMediaElement.prototype, 'srcObject', {
        get: _origGet,
        set: function(stream) {
          _origSet.call(this, stream);
          window._liveBotSilenceElement(this);
          var tag = (this.tagName || '').toLowerCase();
          if (stream && (tag === 'audio' || tag === 'video')) {
            window._liveBotConnectStream(stream, 'srcObject-' + tag);
          }
        },
        configurable: true,
        enumerable: true
      });
    }
  } catch(e) {
    console.warn('[liveBot] srcObject patch error:', e);
  }

  // Intercept RTCPeerConnection
  function hookPC(pc) {
    if (pc._liveBotHooked) return;
    pc._liveBotHooked = true;
    window._liveBotPeerConnections.push(pc);

    pc.addEventListener('track', function(e) {
      if (e.track && e.track.kind === 'audio') {
        var s = (e.streams && e.streams[0]) ? e.streams[0] : new MediaStream([e.track]);
        window._liveBotConnectTrack(e.track, s, 'pc-ontrack');
      }
    });

    try {
      pc.addEventListener('addstream', function(e) {
        if (e.stream) {
          window._liveBotConnectStream(e.stream, 'pc-addstream');
        }
      });
    } catch(ignore) {}
  }

  var _OrigRTC = window.RTCPeerConnection || window.webkitRTCPeerConnection;
  if (_OrigRTC) {
    function PatchedRTC(config) {
      var pc = new _OrigRTC(config);
      hookPC(pc);
      return pc;
    }
    PatchedRTC.prototype = _OrigRTC.prototype;
    Object.setPrototypeOf(PatchedRTC, _OrigRTC);
    window.RTCPeerConnection = PatchedRTC;
    if (window.webkitRTCPeerConnection) {
      window.webkitRTCPeerConnection = PatchedRTC;
    }

    var _origSRD = _OrigRTC.prototype.setRemoteDescription;
    if (_origSRD) {
      _OrigRTC.prototype.setRemoteDescription = function() {
        hookPC(this);
        return _origSRD.apply(this, arguments);
      };
    }
    var _origSLD = _OrigRTC.prototype.setLocalDescription;
    if (_origSLD) {
      _OrigRTC.prototype.setLocalDescription = function() {
        hookPC(this);
        return _origSLD.apply(this, arguments);
      };
    }
  }

  // Function to compute audio RMS / volume level
  window._liveBotGetAudioStats = function() {
    var rms = 0;
    var peak = 0;
    if (window._liveBotAnalyser) {
      var data = new Uint8Array(window._liveBotAnalyser.frequencyBinCount);
      window._liveBotAnalyser.getByteTimeDomainData(data);
      var sum = 0;
      for (var i = 0; i < data.length; i++) {
        var val = (data[i] - 128) / 128;
        sum += val * val;
        var absVal = Math.abs(val);
        if (absVal > peak) peak = absVal;
      }
      rms = Math.sqrt(sum / data.length);
    }
    return {
      tracks: window._liveBotTrackCount || 0,
      pcs: (window._liveBotPeerConnections || []).length,
      recording: window._liveBotRecorder ? window._liveBotRecorder.state : 'none',
      chunks: (window._liveBotChunks || []).length,
      rms: Math.round(rms * 1000) / 1000,
      peak: Math.round(peak * 1000) / 1000,
      speaking: rms > 0.015
    };
  };

  return 'audio_interceptor_ready';
})();
"""

INJECT_CAPTURE_JS = """
(function() {
  if (window._liveBotEnsureRecorder) window._liveBotEnsureRecorder();
  if (window._liveBotAudioCtx && window._liveBotAudioCtx.state === 'suspended') {
    window._liveBotAudioCtx.resume().catch(function(){});
  }

  var mediaInfo = { audioFound: 0, videoFound: 0 };
  if (window._liveBotScanElements) {
    mediaInfo = window._liveBotScanElements('inject-scan');
  }

  var pcRecv = 0;
  (window._liveBotPeerConnections || []).forEach(function(pc) {
    try {
      var receivers = pc.getReceivers ? pc.getReceivers() : [];
      receivers.forEach(function(r) {
        if (r.track && r.track.kind === 'audio' && r.track.readyState === 'live') {
          window._liveBotConnectTrack(r.track, new MediaStream([r.track]), 'pc-recv');
          pcRecv++;
        }
      });
    } catch(e) {}
  });

  return JSON.stringify({
    tracks: window._liveBotTrackCount || 0,
    pcs: (window._liveBotPeerConnections || []).length,
    recording: window._liveBotRecorder ? window._liveBotRecorder.state : 'none',
    audioFound: mediaInfo.audioFound,
    videoFound: mediaInfo.videoFound,
    pcRecv: pcRecv,
    chunks: (window._liveBotChunks || []).length
  });
})();
"""

ENSURE_CAPTURE_JS = """
(function() {
  if (window._liveBotEnsureRecorder) window._liveBotEnsureRecorder();
  if (window._liveBotAudioCtx && window._liveBotAudioCtx.state === 'suspended') {
    window._liveBotAudioCtx.resume().catch(function(){});
  }

  if (window._liveBotScanElements) {
    window._liveBotScanElements('poll-scan');
  }

  (window._liveBotPeerConnections || []).forEach(function(pc) {
    try {
      if (pc.connectionState === 'closed') return;
      var receivers = pc.getReceivers ? pc.getReceivers() : [];
      receivers.forEach(function(r) {
        if (r.track && r.track.kind === 'audio' && r.track.readyState === 'live') {
          window._liveBotConnectTrack(r.track, new MediaStream([r.track]), 'poll-pc');
        }
      });
    } catch(e) {}
  });

  return JSON.stringify(window._liveBotGetAudioStats ? window._liveBotGetAudioStats() : {});
})();
"""

DRAIN_CHUNKS_JS = """
(function() {
  var chunks = window._liveBotChunks || [];
  if (chunks.length === 0) return null;
  window._liveBotChunks = [];
  return chunks.join(',');
})();
"""

STOP_RECORDER_JS = """
(function() {
  var stopped = 0;
  if (window._liveBotRecorder && window._liveBotRecorder.state !== 'inactive') {
    try {
      window._liveBotRecorder.stop();
      stopped++;
    } catch(e) {}
  }
  return stopped > 0 ? 'stopped' : 'no_recorder';
})();
"""

ENABLE_CAPTIONS_JS = """
(function() {
  // Check if captions are already visibly active
  function isAlreadyOn() {
    var btns = Array.from(document.querySelectorAll('button, div[role="button"]'));
    return btns.some(function(b) {
      var lbl = ((b.getAttribute('aria-label') || '') + ' ' + (b.getAttribute('data-tooltip') || '')).toLowerCase();
      return lbl.includes('turn off captions') || lbl.includes('hide captions') || b.getAttribute('aria-pressed') === 'true';
    });
  }

  if (isAlreadyOn()) return 'already_active';

  // 1. Dismiss any open modals/overlays first
  var escapeEvt = new KeyboardEvent('keydown', { key: 'Escape', code: 'Escape', bubbles: true, cancelable: true });
  document.dispatchEvent(escapeEvt);

  // 2. Find CC button in bottom toolbar
  var btns = Array.from(document.querySelectorAll('button, div[role="button"]'));
  var ccBtn = btns.find(function(b) {
    var lbl = ((b.getAttribute('aria-label') || '') + ' ' + (b.getAttribute('data-tooltip') || '') + ' ' + (b.innerText || '')).toLowerCase();
    return (lbl.includes('caption') || lbl.includes('subtitle') || lbl.includes('closed_caption'))
      && !lbl.includes('settings') && !lbl.includes('language') && !lbl.includes('options');
  });

  if (ccBtn) {
    ccBtn.click();
    // In case a language prompt modal appeared, auto-confirm it
    setTimeout(function() {
      var applyBtn = Array.from(document.querySelectorAll('button')).find(function(b) {
        var t = (b.innerText || '').toLowerCase().trim();
        return t === 'apply' || t === 'done' || t === 'confirm' || t === 'save';
      });
      if (applyBtn) applyBtn.click();
    }, 400);
    return 'clicked_cc_button';
  }

  // 3. Fallback: Check inside "More options" (...) overflow menu
  var moreBtn = btns.find(function(b) {
    var lbl = ((b.getAttribute('aria-label') || '') + ' ' + (b.getAttribute('data-tooltip') || '')).toLowerCase();
    return lbl.includes('more options') || lbl.includes('more_vert');
  });
  if (moreBtn) {
    moreBtn.click();
    setTimeout(function() {
      var menuItems = Array.from(document.querySelectorAll('[role="menuitem"], button'));
      var captionItem = menuItems.find(function(m) {
        return (m.innerText || '').toLowerCase().includes('caption');
      });
      if (captionItem) captionItem.click();
    }, 300);
  }

  // 4. Fallback: keyboard shortcut 'c'
  try {
    var keyEvt = new KeyboardEvent('keydown', {
      key: 'c',
      code: 'KeyC',
      keyCode: 67,
      which: 67,
      bubbles: true,
      cancelable: true
    });
    (document.activeElement || document.body).dispatchEvent(keyEvt);
    return 'dispatched_key_c';
  } catch(e) {
    return 'failed';
  }
})();
"""

CHECK_CAPTIONS_ACTIVE_JS = """
(function() {
  if (document.querySelectorAll('[data-speaker-id]').length > 0) return 'active_speaker_nodes';

  var liveRegions = document.querySelectorAll('[aria-live="polite"], [aria-live="assertive"]');
  for (var i = 0; i < liveRegions.length; i++) {
    var rect = liveRegions[i].getBoundingClientRect();
    if (rect.top > window.innerHeight * 0.45 && (liveRegions[i].innerText || '').trim().length > 0) {
      return 'active_live_region';
    }
  }

  var btns = document.querySelectorAll('button, div[role="button"]');
  for (var j = 0; j < btns.length; j++) {
    var lbl = ((btns[j].getAttribute('aria-label') || '') + ' ' + (btns[j].getAttribute('data-tooltip') || '')).toLowerCase();
    if (lbl.includes('turn off captions') || btns[j].getAttribute('aria-pressed') === 'true') {
      return 'active_toggle';
    }
  }

  return 'inactive';
})();
"""

IS_MEETING_ENDED_JS = """
(function() {
  // Check if disconnected screen or rejoin screen is visibly displayed
  var returnBtn = Array.from(document.querySelectorAll('button, a')).find(function(b) {
    var t = (b.innerText || '').trim().toLowerCase();
    var r = b.getBoundingClientRect();
    return (t.includes('return to home screen') || t === 'rejoin') && r.width > 40 && r.height > 20;
  });
  if (returnBtn) return 'return_button_visible';

  // Check if call ended heading is prominently displayed
  var headings = Array.from(document.querySelectorAll('h1, h2, div[role="heading"]'));
  for (var i = 0; i < headings.length; i++) {
    var txt = (headings[i].innerText || '').trim().toLowerCase();
    var rect = headings[i].getBoundingClientRect();
    if (rect.width > 0 && rect.height > 0) {
      if (txt === "you've left the meeting" || txt === "meeting ended for everyone" || txt === "the meeting has ended" || txt.includes("your host ended the meeting")) {
        return 'call_ended_heading';
      }
    }
  }
  return null;
})();
"""

SCRAPE_PARTICIPANTS_JS = r"""
(function() {
  var names = new Set();
  function clean(n) {
    if (!n) return '';
    var s = n.replace(/\s*\((You|Host|Organizer|External|Guest)\)/gi, '')
             .replace(/\s*\d{1,2}:\d{2}\s*(am|pm)?/gi, '')
             .trim();
    if (s.length < 2 || s.length > 40) return '';
    // A person's name does not end in punctuation
    if (/[\.\?!,;:"]$/.test(s)) return '';

    var lower = s.toLowerCase();
    var actionNoise = [
      'chat', 'apps', 'participant', 'people', 'meeting details', 'settings',
      'more options', 'font size', 'circle font color', 'format_size', 'activities',
      'pin', 'unmute', 'mute', 'video', 'reframe', 'background', 'effects',
      'screen', 'host', 'ended', 'might', 'still', 'see', 'someone', 'everyone',
      'cant', "can't", 'turn on', 'turn off', 'camera', 'microphone', 'audio', 'call'
    ];
    if (actionNoise.some(function(x) { return lower === x || lower.includes(x); })) return '';

    var words = s.split(/\s+/);
    if (words.length > 4 || words.length < 1) return '';

    // Check that each word starts with a capital letter or is short initial
    var isNameCased = words.every(function(w) {
      return w.length > 0 && (w[0] === w[0].toUpperCase() || w.length <= 2);
    });
    if (!isNameCased) return '';

    return s;
  }

  // 1. Participant video tiles data attributes
  document.querySelectorAll('[data-self-name], [data-participant-id]').forEach(function(el) {
    var n = clean(el.getAttribute('data-self-name') || '');
    if (n) names.add(n);
  });

  // 2. Specific Meet tile name label (.zWGUib, div.notranslate)
  document.querySelectorAll('div[data-participant-id] .zWGUib, div[data-participant-id] [data-hovercard-id]').forEach(function(el) {
    var n = clean(el.innerText || '');
    if (n) names.add(n);
  });

  // 3. Caption avatars (Google Meet uses <img ... alt="Name">)
  document.querySelectorAll('div[jscontroller="g6iUqb"] img[alt], div[jsname="YSxPC"] img[alt], .nMxDN img[alt], img.KjWmyd[alt]').forEach(function(img) {
    var n = clean(img.getAttribute('alt') || '');
    if (n) names.add(n);
  });

  // 4. Caption speaker name elements (.zs7Du, [jsname="r4nke"])
  document.querySelectorAll('.zs7Du, [jsname="r4nke"]').forEach(function(el) {
    var n = clean(el.innerText || '');
    if (n) names.add(n);
  });

  return JSON.stringify(Array.from(names));
})();
"""

SCRAPE_DOM_CAPTIONS_JS = r"""
(function() {
  var results = [];
  var seenKeys = new Set();

  function isUiNoise(text) {
    var t = (text || '').toLowerCase().trim();
    if (!t || t.length < 2) return true;
    var exactNoise = [
      'chat apps', 'chat', 'apps', 'participant', 'font size circle font color',
      'font size', 'font color', 'circle font color', 'format_size',
      'english', 'turn on captions', 'turn off captions', 'leave call',
      'microphone', 'camera', 'more options', 'chat with everyone',
      'raise hand', 'send a reaction', 'share screen', 'people', 'meeting details',
      'caption settings', 'settings', 'apply', 'cancel', 'tt',
      'arrow_up', 'keyboard_arrow_up', 'mic_off', 'turn on microphone', 'turn off microphone',
      'call_end', 'videocam', 'videocam_off', 'audio settings',
      'closed_caption', 'back_hand', 'more_vert', 'meeting tools', 'info',
      'language', 'mood', 'speaker', 'button'
    ];
    if (exactNoise.some(function(n) { return t === n; })) return true;
    if (t.length < 35 && exactNoise.some(function(n) { return t.includes(n); })) return true;
    return false;
  }

  function cleanSpeaker(name) {
    var s = (name || '').trim();
    if (!s) return '';
    s = s.replace(/\s*\((You|Host|Organizer|External|Guest)\)/gi, '')
         .replace(/\s*\d{1,2}:\d{2}\s*(am|pm)?/gi, '')
         .trim();
    if (isUiNoise(s)) return '';
    return s;
  }

  function cleanText(text) {
    var t = (text || '').trim();
    if (isUiNoise(t)) return '';
    if (!/[a-zA-Z0-9]/.test(t)) return '';
    return t;
  }

  // Collect candidate speaker blocks
  var blocks = [];

  // Strategy 1: Google Meet native individual speaker cards
  var nativeBlocks = Array.from(document.querySelectorAll('div[jsname="YSxPC"], .nMxDN'));
  nativeBlocks.forEach(function(b) {
    if (!b.closest('[role="toolbar"], [role="menu"], [role="dialog"], button, [role="button"], footer, nav, aside')) {
      blocks.push(b);
    }
  });

  // Strategy 2: If nativeBlocks is empty, find child elements inside caption containers
  if (blocks.length === 0) {
    var containers = Array.from(document.querySelectorAll('div[jscontroller="g6iUqb"], .a4cQT, div[aria-live="polite"][class*="caption" i]'));
    containers.forEach(function(cc) {
      if (cc.closest('[role="toolbar"], button, footer, nav, aside')) return;
      var children = Array.from(cc.children).filter(function(child) {
        return !child.closest('[role="toolbar"], button, [role="button"]');
      });
      children.forEach(function(child) {
        blocks.push(child);
      });
    });
  }

  if (!window._ntCardSeq) window._ntCardSeq = 1;

  function addResult(cardId, speaker, text) {
    var spk = cleanSpeaker(speaker) || 'Participant';
    var txt = cleanText(text);
    if (!txt) return;
    if (spk.toLowerCase() === txt.toLowerCase()) return;
    if (spk === 'Participant' && (txt.length < 5 || isUiNoise(txt))) return;

    var key = cardId + '::' + spk + '::' + txt;
    if (!seenKeys.has(key)) {
      seenKeys.add(key);
      results.push({ id: cardId, speaker: spk, text: txt });
    }
  }

  // Also collect any avatar images inside the caption area to accurately recognize speaker names
  var captionAvatars = new Set();
  document.querySelectorAll('div[jscontroller="g6iUqb"] img[alt], div[jsname="YSxPC"] img[alt], .nMxDN img[alt], img.KjWmyd[alt]').forEach(function(img) {
    var alt = (img.getAttribute('alt') || '').trim();
    if (alt && alt.length < 40 && !isUiNoise(alt)) captionAvatars.add(alt.toLowerCase());
  });

  blocks.forEach(function(block) {
    if (!block.dataset.ntId) {
      block.dataset.ntId = 'card_' + (window._ntCardSeq++);
    }
    var cardId = block.dataset.ntId;

    var avatarImg = block.querySelector('img[alt]');
    var speakerEl = block.querySelector('.zs7Du, [jsname="r4nke"], [class*="speaker" i]');
    var textEl = block.querySelector('.iTTPOb, [jsname="tgaKEf"], [class*="caption" i] span, span[jsname]');

    var spk = '';
    if (avatarImg) spk = cleanSpeaker(avatarImg.getAttribute('alt') || '');
    if (!spk && speakerEl) spk = cleanSpeaker(speakerEl.innerText || '');

    var txt = '';
    if (textEl) {
      txt = cleanText(textEl.innerText || '');
    }

    if (spk && txt) {
      addResult(cardId, spk, txt);
      return;
    }

    // Fallback: If block contains multiple speakers or un-nested text, parse lines cleanly
    var rawText = (block.innerText || '').trim();
    if (!rawText) return;
    var lines = rawText.split('\n').map(function(l) { return l.trim(); }).filter(Boolean);
    lines = lines.filter(function(l) { return !isUiNoise(l); });
    if (lines.length === 0) return;

    var currentSpeaker = spk || '';
    var currentSpeech = [];
    var turnIdx = 0;

    function isLikelySpeakerName(line) {
      if (isUiNoise(line)) return false;
      if (line.length < 2 || line.length > 35) return false;
      if (/[\.\?!,;:]$/.test(line)) return false;
      if (captionAvatars.has(line.toLowerCase())) return true;
      var words = line.split(/\s+/);
      if (words.length > 4) return false;
      var isCap = words.every(function(w) {
        return w.length > 0 && w[0] === w[0].toUpperCase() && !/[0-9]/.test(w);
      });
      return isCap;
    }

    for (var i = 0; i < lines.length; i++) {
      var line = lines[i];
      if (isLikelySpeakerName(line) && i + 1 < lines.length) {
        if (currentSpeaker && currentSpeech.length > 0) {
          addResult(cardId + '_' + (turnIdx++), currentSpeaker, currentSpeech.join(' '));
          currentSpeech = [];
        }
        currentSpeaker = cleanSpeaker(line);
      } else {
        currentSpeech.push(line);
      }
    }

    if (currentSpeaker && currentSpeech.length > 0) {
      addResult(cardId + '_' + (turnIdx++), currentSpeaker, currentSpeech.join(' '));
    } else if (currentSpeech.length > 0) {
      addResult(cardId + '_' + (turnIdx++), 'Participant', currentSpeech.join(' '));
    }
  });

  return JSON.stringify(results);
})();
"""