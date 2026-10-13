/* Operator page.
 *
 * Phase 3 scope: start/stop a replayed capture, watch the hub.
 * Phase 4 adds the input device, the level meter and the spend ledger.
 *
 * The admin token is read from the URL and sent back on every request; the
 * server re-checks it on each one, so this page holds no authority of its
 * own and nothing here is trusted. It is deliberately not stored in
 * localStorage: the operator page is opened once from a bookmark on the
 * venue laptop, and a token in web storage outlives the talk.
 */

(function () {
  'use strict';

  var POLL_MS = 1000;
  /* The meter polls faster than the rest. A level that updates once a second
   * cannot show a syllable, and showing the gain is the whole point of it. */
  var METER_MS = 150;
  /* Meter range, matching audio_source.FLOOR_DB. -20 dBFS and -10 dBFS are
   * the two marks drawn on the bar. */
  var FLOOR_DB = -60;

  var token = new URLSearchParams(window.location.search).get('token') || '';

  var els = {
    source: document.getElementById('source'),
    capture: document.getElementById('capture'),
    file: document.getElementById('file'),
    device: document.getElementById('device'),
    channel: document.getElementById('channel'),
    refreshDevices: document.getElementById('refresh-devices'),
    record: document.getElementById('record'),
    paced: document.getElementById('paced'),
    start: document.getElementById('start'),
    stop: document.getElementById('stop'),
    hint: document.getElementById('hint'),
    meterFill: document.getElementById('meter-fill'),
    level: document.getElementById('s-level'),
    clip: document.getElementById('s-clip'),
    running: document.getElementById('s-running'),
    sourceText: document.getElementById('s-source'),
    elapsed: document.getElementById('s-elapsed'),
    audio: document.getElementById('s-audio'),
    responses: document.getElementById('s-responses'),
    seq: document.getElementById('s-seq'),
    viewers: document.getElementById('s-viewers'),
    shed: document.getElementById('s-shed'),
    audioDropped: document.getElementById('s-audio-dropped'),
    recording: document.getElementById('s-recording'),
    error: document.getElementById('s-error'),
    spend: document.getElementById('s-spend'),
    free: document.getElementById('s-free'),
    runs: document.getElementById('s-runs')
  };

  var HINTS = {
    replay: 'Replay costs no API credit. This is the demo path: captions ' +
            'from a saved capture, at real speed.',
    mic: 'Live capture, Phase 4. Audio is measured and discarded — there ' +
         'are no captions until the engine client lands in Phase 5.',
    file: 'Plays a recording through the capture path at real time. Same ' +
          'as the microphone, minus the microphone.'
  };

  function url(path, extra) {
    var query = new URLSearchParams(extra || {});
    query.set('token', token);
    return path + '?' + query.toString();
  }

  function mode() { return els.source.value; }

  function showRows() {
    var current = mode();
    var rows = document.querySelectorAll('[data-for]');
    Array.prototype.forEach.call(rows, function (row) {
      var wanted = row.getAttribute('data-for').split(' ');
      row.hidden = wanted.indexOf(current) === -1;
    });
    els.hint.textContent = HINTS[current] || '';
  }

  /* Only rebuild a dropdown when its contents actually changed, or the
   * operator's selection would reset itself once a second. */
  function fillSelect(select, values, labels) {
    var current = Array.prototype.map.call(select.options, function (o) {
      return o.value;
    });
    if (current.join('\u0000') === values.join('\u0000')) { return; }
    var chosen = select.value;
    select.textContent = '';
    values.forEach(function (value, index) {
      var option = document.createElement('option');
      option.value = value;
      option.textContent = labels ? labels[index] : value;
      select.appendChild(option);
    });
    if (values.indexOf(chosen) !== -1) { select.value = chosen; }
  }

  function seconds(value) {
    return value === null || value === undefined ? '—' : value + ' s';
  }

  function renderMeter(level) {
    if (!level) {
      els.meterFill.style.width = '0%';
      els.level.textContent = '—';
      els.clip.hidden = true;
      return;
    }
    var db = level.db;
    var fraction = Math.max(0, Math.min(1, (db - FLOOR_DB) / -FLOOR_DB));
    els.meterFill.style.width = (fraction * 100).toFixed(1) + '%';
    els.meterFill.classList.toggle('hot', level.peak >= 0.9);
    els.level.textContent = db.toFixed(1) + ' dBFS   peak ' +
      level.peak_db.toFixed(1) + ' dBFS';
    els.clip.hidden = !level.clipping;
  }

  function renderUsage(usage) {
    if (!usage) { return; }
    var spend = usage.billable_minutes.toFixed(1) + ' min';
    if (usage.estimated_usd !== null && usage.estimated_usd !== undefined) {
      spend += '  ≈ $' + usage.estimated_usd.toFixed(2);
      if (usage.budget_usd) {
        spend += ' of $' + usage.budget_usd.toFixed(2);
      }
    } else if (usage.budget_usd) {
      /* No rate configured, so no honest dollar figure. Say so rather than
       * printing a number that looks measured. The env var is named in
       * docs/CHECKPOINT.md, not here: a test greps every served file for the
       * API key's variable prefix, and this page must stay clear of it. */
      spend += '  ($' + usage.budget_usd.toFixed(2) + ' budget; set the ' +
        'per-minute rate in .env to estimate dollars)';
    }
    els.spend.textContent = spend;
    els.free.textContent = usage.free_minutes.toFixed(1) + ' min';
    els.runs.textContent = usage.runs +
      (usage.unreadable_lines ? ' (' + usage.unreadable_lines +
        ' unreadable line(s))' : '');
  }

  function render(status) {
    fillSelect(els.capture, status.captures || []);
    fillSelect(els.file, status.audio_files || []);
    els.running.textContent = status.running
      ? (status.paced ? 'running (real time)' : 'running (fast)')
      : (status.live ? 'live, no feed' : 'stopped');
    els.sourceText.textContent = status.source || '—';
    els.elapsed.textContent = seconds(status.elapsed);
    els.audio.textContent = status.audio_total_s
      ? (status.audio_ms / 1000).toFixed(1) + ' / ' + status.audio_total_s + ' s'
      : (status.audio_ms ? (status.audio_ms / 1000).toFixed(1) + ' s' : '—');
    els.responses.textContent = status.responses;
    els.seq.textContent = status.seq;
    els.viewers.textContent = status.viewers + ' / ' + status.max_viewers;
    els.shed.textContent = status.rejected + ' / ' + status.dropped;
    els.audioDropped.textContent = status.dropped_chunks === undefined
      ? '—' : status.dropped_chunks + ' chunk(s)';
    els.recording.textContent = status.recording || 'off';
    els.error.textContent = status.error || 'none';
    els.error.className = status.error ? 'error' : '';
    els.start.disabled = !!status.running;
    els.stop.disabled = !status.running;
    renderMeter(status.level);
    renderUsage(status.usage);
  }

  function fail(message) {
    els.error.textContent = message;
    els.error.className = 'error';
  }

  function get(path) {
    return window.fetch(url(path), { cache: 'no-store' })
      .then(function (response) {
        if (!response.ok) { throw new Error('status ' + response.status); }
        return response.json();
      });
  }

  function poll() {
    get('/admin/status').then(render)
      .catch(function (error) { fail(String(error.message || error)); });
  }

  /* Its own endpoint, not /admin/status: the meter needs several updates a
   * second, and status reads the usage ledger off disk every call. */
  function pollMeter() {
    get('/admin/level')
      .then(function (data) { renderMeter(data.level); })
      .catch(function () { renderMeter(null); });
  }

  function loadDevices() {
    get('/admin/devices')
      .then(function (data) {
        var devices = data.devices || [];
        fillSelect(
          els.device,
          devices.map(function (d) { return String(d.index); }),
          devices.map(function (d) {
            return d.index + ': ' + d.name + ' (' + d.api + ', ' +
              d.channels + ' ch' + (d.default ? ', default' : '') + ')';
          })
        );
        if (!devices.length) {
          var option = document.createElement('option');
          option.value = '';
          option.textContent = 'no input devices found';
          els.device.appendChild(option);
        }
      })
      .catch(function (error) { fail(String(error.message || error)); });
  }

  function post(path, extra) {
    window.fetch(url(path, extra), { method: 'POST', cache: 'no-store' })
      .then(function (response) {
        return response.json().then(function (body) {
          if (!response.ok) {
            throw new Error(body.detail || ('status ' + response.status));
          }
          return body;
        });
      })
      .then(render)
      .catch(function (error) { fail(String(error.message || error)); });
  }

  els.source.addEventListener('change', showRows);
  els.refreshDevices.addEventListener('click', loadDevices);

  els.start.addEventListener('click', function () {
    var current = mode();
    var params = {
      source: current,
      paced: els.paced.checked ? 'true' : 'false',
      record: els.record.checked ? 'true' : 'false'
    };
    if (current === 'replay') {
      if (!els.capture.value) { return; }
      params.capture = els.capture.value;
    } else if (current === 'file') {
      if (!els.file.value) { return; }
      params.file = els.file.value;
    } else if (current === 'mic') {
      if (els.device.value !== '') { params.device = els.device.value; }
      params.channel = els.channel.value || '1';
    }
    post('/admin/start', params);
  });

  els.stop.addEventListener('click', function () { post('/admin/stop'); });

  showRows();
  loadDevices();
  poll();
  window.setInterval(poll, POLL_MS);
  window.setInterval(pollMeter, METER_MS);
}());
