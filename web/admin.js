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
    logResponses: document.getElementById('log-responses'),
    arm: document.getElementById('arm'),
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
    responseLog: document.getElementById('s-capture'),
    engine: document.getElementById('s-engine'),
    lag: document.getElementById('s-lag'),
    retries: document.getElementById('s-retries'),
    lost: document.getElementById('s-lost'),
    silence: document.getElementById('s-silence'),
    thisRun: document.getElementById('s-this-run'),
    error: document.getElementById('s-error'),
    spend: document.getElementById('s-spend'),
    free: document.getElementById('s-free'),
    runs: document.getElementById('s-runs'),
    page: document.getElementById('page-problem'),
    dTitle: document.getElementById('d-title'),
    dSpeakers: document.getElementById('d-speakers'),
    dCompanies: document.getElementById('d-companies'),
    dTerms: document.getElementById('d-terms'),
    dNotes: document.getElementById('d-notes'),
    dGlossary: document.getElementById('d-glossary'),
    dSave: document.getElementById('d-save'),
    dApply: document.getElementById('d-apply'),
    dClear: document.getElementById('d-clear'),
    dState: document.getElementById('d-state'),
    details: document.getElementById('s-details')
  };

  var HINTS = {
    replay: 'Replay costs no API credit. This is the demo path: captions ' +
            'from a saved capture, at real speed.',
    mic: 'Live capture, Phase 4. Audio is measured and discarded — there ' +
         'are no captions until the engine client lands in Phase 5.',
    file: 'Plays a recording through the capture path at real time. Same ' +
          'as the microphone, minus the microphone.',
    live: 'The real thing, and the only option here that costs money. ' +
          'Captions reach the phones. Ten minutes of silence ends the ' +
          'stream by itself and shows "paused"; the next sound resumes it.'
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
    /* Defensive about both arguments. The operator page is the thing you
     * reach for when something has already gone wrong, so it has to survive
     * a payload or a DOM that is not what it expected. */
    if (!select || !select.options) { return; }
    values = values || [];
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

  /* Write one field. Missing values become an em dash rather than "undefined"
   * or an exception - a status page is most useful exactly when the thing it
   * reports is broken, so no single unexpected field may blank the rest. */
  function text(el, value, fallback) {
    if (!el) { return; }
    el.textContent = (value === null || value === undefined || value === '')
      ? (fallback === undefined ? '—' : fallback)
      : value;
  }

  /* "a / b", or an em dash unless both halves are really there. Guards
   * against null as well as undefined: the server sends neither today, but
   * this page exists to be read when something is already wrong. */
  function pair(a, b) {
    var ok = function (v) { return v !== null && v !== undefined; };
    return ok(a) && ok(b) ? a + ' / ' + b : '—';
  }

  function num(value, digits, suffix) {
    if (typeof value !== 'number' || !isFinite(value)) { return '—'; }
    return value.toFixed(digits) + (suffix || '');
  }

  /* Run one part of the render in isolation. One bad field used to take the
   * whole table down with it: on 2026-10-13 a duplicate key in `els` made
   * `fillSelect` throw on the first line of render(), and every row on the
   * page showed an em dash while "Last error" showed a JavaScript message. */
  function section(name, fn) {
    try {
      fn();
    } catch (error) {
      pageProblem(name + ': ' + (error && error.message || error));
    }
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
    status = status || {};
    section('sources', function () {
      fillSelect(els.capture, status.captures);
      fillSelect(els.file, status.audio_files);
    });
    section('session', function () {
      text(els.running, status.running
        ? (status.paced ? 'running (real time)' : 'running (fast)')
        : (status.live ? 'live, no feed' : 'stopped'));
      text(els.sourceText, status.source);
      text(els.elapsed, seconds(status.elapsed));
      text(els.audio, status.audio_total_s
        ? num(status.audio_ms / 1000, 1) + ' / ' + status.audio_total_s + ' s'
        : (status.audio_ms ? num(status.audio_ms / 1000, 1, ' s') : '—'));
      text(els.responses, status.responses);
      text(els.seq, status.seq);
      text(els.viewers, pair(status.viewers, status.max_viewers));
      text(els.shed, pair(status.rejected, status.dropped));
      text(els.audioDropped, status.dropped_chunks === undefined
        || status.dropped_chunks === null
        ? '—' : status.dropped_chunks + ' chunk(s)');
      text(els.recording, status.recording, 'off');
      text(els.responseLog, status.capture, 'off');
      /* Counts only. The values are never in this payload. */
      var d = status.details;
      text(els.details, d ? (
        (d.title ? 'title, ' : 'no title, ') + d.speakers + ' speaker(s), '
        + d.companies + ' company(ies), ' + d.terms + ' extra term(s)'
      ) : null);
    });
    section('engine', function () {
      text(els.engine, status.engine_state);
      if (els.engine) {
        els.engine.className = status.engine_state === 'reconnecting'
          ? 'error' : '';
      }
      text(els.lag, num(status.lag_seconds, 2, ' s'));
      text(els.retries, pair(status.reconnects, status.rollovers));
      text(els.lost, num(status.dropped_seconds, 1, ' s'));
      if (els.lost) { els.lost.className = status.dropped_seconds ? 'error' : ''; }
      text(els.silence, num(status.silent_seconds, 0, ' s'));
      text(els.thisRun, num(
        typeof status.streamed_seconds === 'number'
          ? status.streamed_seconds / 60 : undefined, 2, ' min'));
    });
    section('error', function () {
      /* The engine's error, not the page's. A JavaScript exception used to
       * land here, which read as "the transcription failed" when the truth
       * was "this page has a bug". Page problems go to their own line. */
      text(els.error, status.error, 'none');
      if (els.error) { els.error.className = status.error ? 'error' : ''; }
      if (els.start) { els.start.disabled = !!status.running; }
      if (els.stop) { els.stop.disabled = !status.running; }
    });
    section('meter', function () { renderMeter(status.level); });
    section('usage', function () { renderUsage(status.usage); });
  }

  /* A problem reaching or reading the server: this page's problem, kept
   * apart from the engine's "Last error" row so the two are never confused. */
  function pageProblem(message) {
    if (!els.page) { return; }
    els.page.textContent = message || '';
    els.page.hidden = !message;
  }

  function fail(message) {
    pageProblem(message);
  }

  function get(path) {
    return window.fetch(url(path), { cache: 'no-store' })
      .then(function (response) {
        if (!response.ok) { throw new Error('status ' + response.status); }
        return response.json();
      });
  }

  function poll() {
    get('/admin/status')
      .then(function (status) { pageProblem(''); render(status); })
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

  /* -- this talk's details ---------------------------------------------
   *
   * Private. They are loaded once, by their own request, and never arrive
   * with the status poll - so a page left open on a side screen shows counts
   * and nothing else. Lists are one item per line, because a speaker name
   * can contain a comma and an operator should not have to think about it.
   */

  function lines(value) {
    return String(value || '').split('\n')
      .map(function (s) { return s.trim(); })
      .filter(function (s) { return s.length; });
  }

  function detailsState(message, kind) {
    if (!els.dState) { return; }
    els.dState.textContent = message || '';
    els.dState.className = 'note' + (kind ? ' ' + kind : '');
  }

  function loadDetails() {
    get('/admin/session')
      .then(function (data) {
        var d = data.details || {};
        els.dTitle.value = d.title || '';
        els.dSpeakers.value = (d.speakers || []).join('\n');
        els.dCompanies.value = (d.companies || []).join('\n');
        els.dTerms.value = (d.terms || []).join('\n');
        els.dNotes.value = d.notes || '';
        els.dGlossary.textContent = 'Already in the shared glossary: '
          + (data.glossary_terms || []).join(', ');
        showPending(data.pending);
      })
      .catch(function (error) { fail(String(error.message || error)); });
  }

  function showPending(pending) {
    if (els.dApply) { els.dApply.hidden = !pending; }
    if (pending) {
      detailsState('Changes saved, not applied yet.', 'pending');
    }
  }

  function sendDetails(details, note) {
    return window.fetch(url('/admin/session'), {
      method: 'POST',
      cache: 'no-store',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify(details)
    })
      .then(function (response) {
        if (!response.ok) { throw new Error('status ' + response.status); }
        return response.json();
      })
      .then(function (data) {
        detailsState(note, 'saved');
        showPending(data.pending);
        return data;
      })
      .catch(function (error) { fail(String(error.message || error)); });
  }

  els.dSave.addEventListener('click', function () {
    sendDetails({
      title: els.dTitle.value.trim(),
      speakers: lines(els.dSpeakers.value),
      companies: lines(els.dCompanies.value),
      terms: lines(els.dTerms.value),
      notes: els.dNotes.value.trim()
    }, 'Saved.');
  });

  els.dClear.addEventListener('click', function () {
    els.dTitle.value = '';
    els.dSpeakers.value = '';
    els.dCompanies.value = '';
    els.dTerms.value = '';
    els.dNotes.value = '';
    sendDetails({title: '', speakers: [], companies: [], terms: [], notes: ''},
                'Cleared.');
  });

  els.dApply.addEventListener('click', function () {
    window.fetch(url('/admin/session/apply'), {method: 'POST', cache: 'no-store'})
      .then(function (response) {
        if (!response.ok) { throw new Error('status ' + response.status); }
        return response.json();
      })
      .then(function (data) {
        showPending(data.pending);
        detailsState(data.applied
          ? 'Applied — reconnected with the new details.'
          : 'Nothing running; they are already in place for the next Start.',
          'saved');
      })
      .catch(function (error) { fail(String(error.message || error)); });
  });

  els.source.addEventListener('change', showRows);
  els.refreshDevices.addEventListener('click', loadDevices);

  els.start.addEventListener('click', function () {
    var current = mode();
    var params = {
      source: current,
      paced: els.paced.checked ? 'true' : 'false',
      record: els.record.checked ? 'true' : 'false',
      log_responses: els.logResponses.checked ? 'true' : 'false'
    };
    if (current === 'replay') {
      if (!els.capture.value) { return; }
      params.capture = els.capture.value;
    } else if (current === 'file') {
      if (!els.file.value) { return; }
      params.file = els.file.value;
    } else if (current === 'mic' || current === 'live') {
      if (current === 'live' && !els.arm.checked) {
        /* Two deliberate actions before any credit is spent. A checkbox
         * rather than a confirm() dialog: a modal blocks every later
         * instruction the page receives, including the Stop button. */
        fail('tick "I mean it" first — a live run spends credit');
        return;
      }
      if (els.device.value !== '') { params.device = els.device.value; }
      params.channel = els.channel.value || '1';
    }
    post('/admin/start', params);
  });

  els.stop.addEventListener('click', function () { post('/admin/stop'); });

  showRows();
  loadDetails();
  loadDevices();
  poll();
  window.setInterval(poll, POLL_MS);
  window.setInterval(pollMeter, METER_MS);
}());
