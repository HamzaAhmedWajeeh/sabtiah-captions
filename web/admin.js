/* Operator page.
 *
 * Written to be read during a talk, not studied afterwards: the sticky top
 * bar carries the state, the clock, the viewer count and the two buttons,
 * and everything below it is reference.
 *
 * The admin token is read from the URL and sent back on every request; the
 * server re-checks it each time, so this page holds no authority of its own
 * and nothing here is trusted. It is deliberately not stored in
 * localStorage: the page is opened once from a bookmark on the venue laptop,
 * and a token in web storage outlives the talk.
 */

(function () {
  'use strict';

  var POLL_MS = 1000;
  /* The meter polls faster than the rest. A level that updates once a second
   * cannot show a syllable, and showing the gain is the whole point of it. */
  var METER_MS = 150;
  /* Meter range, matching the floor the server reports. The green band and
   * the clip zone in the stylesheet are placed against this same scale. */
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
    confirmBar: document.getElementById('confirm-bar'),
    confirmYes: document.getElementById('confirm-yes'),
    confirmNo: document.getElementById('confirm-no'),
    pill: document.getElementById('state-pill'),
    tElapsed: document.getElementById('t-elapsed'),
    tViewers: document.getElementById('t-viewers'),
    tEngine: document.getElementById('t-engine'),
    tSpend: document.getElementById('t-spend'),
    meterFill: document.getElementById('meter-fill'),
    meterNote: document.getElementById('meter-note'),
    level: document.getElementById('s-level'),
    clip: document.getElementById('s-clip'),
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
    reconnects: document.getElementById('s-reconnects'),
    rollovers: document.getElementById('s-rollovers'),
    lost: document.getElementById('s-lost'),
    silence: document.getElementById('s-silence'),
    thisRun: document.getElementById('s-this-run'),
    error: document.getElementById('s-error'),
    errorBanner: document.getElementById('error-banner'),
    errorText: document.getElementById('error-text'),
    errorAdvice: document.getElementById('error-advice'),
    statusHeading: document.getElementById('status-heading'),
    statusNote: document.getElementById('status-note'),
    statusCard: document.getElementById('status-card'),
    spend: document.getElementById('s-spend'),
    cost: document.getElementById('s-cost'),
    left: document.getElementById('s-left'),
    spendFill: document.getElementById('spend-fill'),
    spendBar: document.getElementById('spend-bar'),
    spendNote: document.getElementById('spend-note'),
    spendCard: document.getElementById('spend-card'),
    free: document.getElementById('s-free'),
    runs: document.getElementById('s-runs'),
    page: document.getElementById('page-problem'),
    sourceCard: document.getElementById('source-card'),
    detailsCard: document.getElementById('details-card'),
    detailsNote: document.getElementById('details-note'),
    applyBar: document.getElementById('apply-bar'),
    applyNote: document.getElementById('apply-note'),
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

  /* One short line each, in the words an operator would use. */
  var HINTS = {
    replay: 'Plays a recording you already have. Free, and the safest way to ' +
            'check the phones.',
    mic: 'Shows the microphone level. No captions, and nothing is charged.',
    file: 'Plays an audio file through the microphone path. No captions, ' +
          'nothing charged.',
    live: 'The real thing. Captions reach the phones and paid minutes are ' +
          'used. Ten quiet minutes stop it on their own.'
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
    /* The live start is red, every other start is blue. The colour is the
     * first signal that this button is not like the others. */
    var live = current === 'live';
    els.start.className = live ? 'spend' : 'primary';
    els.start.textContent = live ? 'Start live' : 'Start';
    els.sourceCard.className = live ? 'card danger' : 'card';
    hideConfirm();
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

  /* Write one field. Missing values become an em dash rather than "undefined"
   * or an exception - a status page is most useful exactly when the thing it
   * reports is broken, so no single unexpected field may blank the rest. */
  function text(el, value, fallback) {
    if (!el) { return; }
    el.textContent = (value === null || value === undefined || value === '')
      ? (fallback === undefined ? '—' : fallback)
      : value;
  }

  /* "a / b", or an em dash unless both halves are really there. */
  function pair(a, b) {
    var ok = function (v) { return v !== null && v !== undefined; };
    return ok(a) && ok(b) ? a + ' / ' + b : '—';
  }

  /* "1 speaker", "2 speakers". Worth the four lines: a status line that says
   * "1 speakers" reads as a page nobody checked. */
  function plural(count, one, many) {
    return count + ' ' + (count === 1 ? one : (many || one + 's'));
  }

  function num(value, digits, suffix) {
    if (typeof value !== 'number' || !isFinite(value)) { return '—'; }
    return value.toFixed(digits) + (suffix || '');
  }

  function clock(secs) {
    if (typeof secs !== 'number' || !isFinite(secs)) { return '—'; }
    var whole = Math.floor(secs);
    var mins = Math.floor(whole / 60);
    var rest = whole % 60;
    return mins + ':' + (rest < 10 ? '0' : '') + rest;
  }

  /* Run one part of the render in isolation. One bad field used to take the
   * whole page down with it: a duplicate key in `els` once made `fillSelect`
   * throw on the first line of render(), and every row showed an em dash. */
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
    /* Stretch the gradient back out to the width of the whole track, so its
     * colour stops stay lined up with the marks painted on the bar. Green
     * through the good band, amber past it, red inside the clip zone. */
    els.meterFill.style.backgroundSize =
      (100 / Math.max(fraction, 0.001)).toFixed(2) + '% 100%';
    els.level.textContent = db.toFixed(1) + ' dB  (peak ' +
      level.peak_db.toFixed(1) + ')';
    els.clip.hidden = !level.clipping;
  }

  /* -- the state pill ---------------------------------------------------- */

  function pillFor(status) {
    if (status.engine_state === 'paused') { return ['PAUSED', 'paused']; }
    if (!status.running) { return ['STOPPED', 'stopped']; }
    if (status.mode === 'replay') { return ['REPLAY', 'replay']; }
    /* A mic or file run captures audio but transcribes nothing, so it is
     * neither live nor a replay. Saying so beats implying captions. */
    if (!status.engine_state) { return ['AUDIO', 'audio']; }
    return ['LIVE', 'live'];
  }

  function renderSpend(usage) {
    if (!usage) { return; }
    text(els.spend, num(usage.billable_minutes, 1));
    text(els.tSpend, num(usage.billable_minutes, 1, ' min'));

    var used = usage.estimated_usd;
    var budget = usage.budget_usd;
    if (used === null || used === undefined) {
      /* No rate configured, so no honest dollar figure. Saying so is better
       * than printing a number that looks measured. */
      text(els.cost, null);
      text(els.left, null);
      els.spendFill.style.width = '0%';
      els.spendBar.hidden = true;
      text(els.spendNote,
        'Set the per-minute rate in .env to see dollars.');
    } else {
      text(els.cost, '$' + used.toFixed(2));
      els.spendBar.hidden = false;
      if (budget) {
        var left = budget - used;
        text(els.left, '$' + left.toFixed(2));
        var share = Math.max(0, Math.min(1, used / budget));
        els.spendFill.style.width = (share * 100).toFixed(1) + '%';
        els.spendFill.className = share > 0.9 ? 'out'
          : (share > 0.7 ? 'low' : '');
        text(els.spendNote, 'Of a $' + budget.toFixed(2) + ' budget.', '');
        els.spendCard.className = share > 0.9 ? 'card danger' : 'card';
      } else {
        text(els.left, null);
        text(els.spendNote, '', '');
      }
    }
    text(els.free, num(usage.free_minutes, 1, ' min'));
    text(els.runs, usage.runs === undefined ? null : usage.runs +
      (usage.unreadable_lines ? ' (' + usage.unreadable_lines +
        ' unreadable)' : ''));
  }

  /* Rows whose value is only an em dash are noise while nothing is running.
   * Hidden rather than removed so they come straight back when a run starts.
   *
   * The Connection group goes as a block when there is no engine at all: a
   * replay transcribes nothing, so "caption delay" and "reconnects" are not
   * blank, they are meaningless. */
  function tidyRows(running, hasEngine) {
    var rows = els.statusCard.querySelectorAll('tr[data-row], tr.group');
    Array.prototype.forEach.call(rows, function (row) {
      if (row.getAttribute('data-group') === 'connection' && !hasEngine) {
        row.hidden = true;
        return;
      }
      if (!row.hasAttribute('data-row')) { row.hidden = false; return; }
      var cell = row.cells[1];
      var empty = !cell || cell.textContent === '—';
      row.hidden = empty && !running;
    });
  }

  function render(status) {
    status = status || {};
    var running = !!status.running;

    section('sources', function () {
      fillSelect(els.capture, status.captures);
      fillSelect(els.file, status.audio_files);
    });

    section('topbar', function () {
      var state = pillFor(status);
      els.pill.textContent = state[0];
      els.pill.className = 'pill ' + state[1];
      text(els.tElapsed, running ? clock(status.elapsed) : null);
      text(els.tViewers, status.viewers);
      text(els.tEngine, status.engine_state, running ? 'running' : 'idle');
      els.start.disabled = running;
      els.stop.disabled = !running;
    });

    section('status', function () {
      /* Leftover numbers from a finished run are useful, but only if they
       * are labelled as the past. Showing them unmarked is how an operator
       * reads a stopped session as a live one. */
      var stale = !running && status.source;
      text(els.statusHeading, stale ? 'Last run' : 'Status');
      els.statusHeading.className = stale ? 'stale' : '';
      /* The empty fallback matters: `text` turns '' into an em dash by
       * default, which left a stray dash hanging under the table. */
      text(els.statusNote, stale
        ? 'These are from the run that just finished, not from now.'
        : '', '');

      text(els.sourceText, status.source);
      text(els.elapsed, status.elapsed === undefined || status.elapsed === null
        ? null : status.elapsed + ' s');
      text(els.audio, status.audio_total_s
        ? num(status.audio_ms / 1000, 1) + ' / ' + status.audio_total_s + ' s'
        : (status.audio_ms ? num(status.audio_ms / 1000, 1, ' s') : null));
      text(els.responses, status.responses);
      text(els.seq, status.seq);
      text(els.viewers, pair(status.viewers, status.max_viewers));
      text(els.shed, pair(status.rejected, status.dropped));
      text(els.audioDropped, status.dropped_chunks === undefined
        || status.dropped_chunks === null
        ? null : status.dropped_chunks);
      text(els.recording, status.recording, 'off');
      text(els.responseLog, status.capture, 'off');

      /* Counts only. The values are never in this payload. */
      var d = status.details;
      text(els.details, d ? [
        d.title ? 'title' : 'no title',
        plural(d.speakers, 'speaker'),
        plural(d.companies, 'company', 'companies'),
        plural(d.terms, 'word')
      ].join(', ') : null);

      text(els.engine, status.engine_state);
      els.engine.className = status.engine_state === 'reconnecting'
        ? 'error' : '';
      text(els.lag, num(status.lag_seconds, 2, ' s'));
      text(els.reconnects, status.reconnects);
      text(els.rollovers, status.rollovers);
      text(els.lost, num(status.dropped_seconds, 1, ' s'));
      els.lost.className = status.dropped_seconds ? 'error' : '';
      text(els.silence, num(status.silent_seconds, 0, ' s'));
      text(els.thisRun, num(
        typeof status.streamed_seconds === 'number'
          ? status.streamed_seconds / 60 : undefined, 2, ' min'));

      /* An engine is involved when one is connected now, or when the run
       * that just finished had one. A replay never has one. */
      tidyRows(running, !!(status.engine_state
        || status.reconnects !== undefined || status.dropped_seconds));
    });

    section('error', function () {
      /* The engine's error, not the page's. A JavaScript exception used to
       * land here, which read as "the transcription failed" when the truth
       * was "this page has a bug". Page problems go to their own banner. */
      text(els.error, status.error, 'none');
      els.error.className = status.error ? 'error' : '';
      text(els.errorText, status.error, '');
      els.errorBanner.hidden = !status.error;
      /* One plain line for the error an operator can actually act on. The
       * key is the only thing on this list they can fix from their seat. */
      var auth = /unauthenticated|permission_denied|invalid api key/i
        .test(status.error || '');
      text(els.errorAdvice, auth ? 'The API key in .env was rejected.' : '', '');
      els.errorAdvice.hidden = !auth;
      els.statusCard.className = status.error ? 'card danger' : 'card';
    });

    section('details', function () { showPending(status.details_pending); });
    section('spend', function () { renderSpend(status.usage); });
  }

  /* A problem reaching or reading the server: this page's problem, kept
   * apart from the engine's error so the two are never confused. */
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
          option.textContent = 'no microphone found';
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
   * Private. They are loaded by their own request and never arrive with the
   * status poll, so a page left open on a side screen shows counts and
   * nothing else. Lists are one item per line, because a name can contain a
   * comma and an operator should not have to think about it.
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
        els.dGlossary.textContent = 'Always included: '
          + (data.glossary_terms || []).join(', ');
        showPending(data.pending);
      })
      .catch(function (error) { fail(String(error.message || error)); });
  }

  /* Saved-but-not-applied is the one state on this page an operator must not
   * walk past, so it gets a bar of its own and the card border turns amber. */
  function showPending(pending) {
    if (!els.applyBar) { return; }
    els.applyBar.hidden = !pending;
    els.detailsCard.className = pending ? 'card attention' : 'card';
    /* The amber bar says "saved, not in use yet" in full. Saying it again
     * beside the Save button only made the page look like it was repeating
     * itself, so the small line is cleared instead. */
    if (pending) { detailsState('', null); }
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
          ? 'In use now.'
          : 'Nothing running — they will be used at the next start.', 'saved');
      })
      .catch(function (error) { fail(String(error.message || error)); });
  });

  /* -- starting ---------------------------------------------------------- */

  function hideConfirm() {
    if (els.confirmBar) { els.confirmBar.hidden = true; }
  }

  function startParams() {
    var current = mode();
    var params = {
      source: current,
      paced: els.paced.checked ? 'true' : 'false',
      record: els.record.checked ? 'true' : 'false',
      log_responses: els.logResponses.checked ? 'true' : 'false'
    };
    if (current === 'replay') {
      if (!els.capture.value) { return null; }
      params.capture = els.capture.value;
    } else if (current === 'file') {
      if (!els.file.value) { return null; }
      params.file = els.file.value;
    } else if (current === 'mic' || current === 'live') {
      if (els.device.value !== '') { params.device = els.device.value; }
      params.channel = els.channel.value || '1';
    }
    return params;
  }

  els.source.addEventListener('change', showRows);
  els.refreshDevices.addEventListener('click', loadDevices);

  els.start.addEventListener('click', function () {
    var params = startParams();
    if (!params) { return; }
    if (mode() === 'live') {
      /* Two deliberate actions before any credit is spent: the checkbox, and
       * then this. An inline bar rather than confirm(), because a modal
       * dialog blocks every later instruction the page receives - including
       * the Stop button. */
      if (!els.arm.checked) {
        fail('Tick "I mean it" first — a live run uses paid minutes.');
        return;
      }
      els.confirmBar.hidden = false;
      return;
    }
    post('/admin/start', params);
  });

  els.confirmYes.addEventListener('click', function () {
    var params = startParams();
    hideConfirm();
    if (params) { post('/admin/start', params); }
  });

  els.confirmNo.addEventListener('click', hideConfirm);

  els.stop.addEventListener('click', function () {
    hideConfirm();
    post('/admin/stop');
  });

  showRows();
  loadDetails();
  loadDevices();
  poll();
  window.setInterval(poll, POLL_MS);
  window.setInterval(pollMeter, METER_MS);
}());
