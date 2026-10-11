/* Operator page. Phase 3 scope: start/stop a replayed capture, watch the hub.
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
  var token = new URLSearchParams(window.location.search).get('token') || '';

  var els = {
    capture: document.getElementById('capture'),
    paced: document.getElementById('paced'),
    start: document.getElementById('start'),
    stop: document.getElementById('stop'),
    running: document.getElementById('s-running'),
    source: document.getElementById('s-source'),
    elapsed: document.getElementById('s-elapsed'),
    audio: document.getElementById('s-audio'),
    responses: document.getElementById('s-responses'),
    seq: document.getElementById('s-seq'),
    viewers: document.getElementById('s-viewers'),
    shed: document.getElementById('s-shed'),
    error: document.getElementById('s-error')
  };

  function url(path, extra) {
    var query = new URLSearchParams(extra || {});
    query.set('token', token);
    return path + '?' + query.toString();
  }

  function fillCaptures(names) {
    // Only rebuild when the list actually changed, or the operator's
    // selection would reset itself once a second.
    var current = Array.prototype.map.call(els.capture.options, function (o) {
      return o.value;
    });
    if (current.join('\u0000') === names.join('\u0000')) { return; }
    var chosen = els.capture.value;
    els.capture.textContent = '';
    names.forEach(function (name) {
      var option = document.createElement('option');
      option.value = name;
      option.textContent = name;
      els.capture.appendChild(option);
    });
    if (names.indexOf(chosen) !== -1) { els.capture.value = chosen; }
  }

  function seconds(value) {
    return value === null || value === undefined ? '—' : value + ' s';
  }

  function render(status) {
    fillCaptures(status.captures || []);
    els.running.textContent = status.running
      ? (status.paced ? 'running (real time)' : 'running (fast)')
      : (status.live ? 'live, no feed' : 'stopped');
    els.source.textContent = status.source || '—';
    els.elapsed.textContent = seconds(status.elapsed);
    els.audio.textContent = status.audio_total_s
      ? (status.audio_ms / 1000).toFixed(1) + ' / ' + status.audio_total_s + ' s'
      : '—';
    els.responses.textContent = status.responses;
    els.seq.textContent = status.seq;
    els.viewers.textContent = status.viewers + ' / ' + status.max_viewers;
    els.shed.textContent = status.rejected + ' / ' + status.dropped;
    els.error.textContent = status.error || 'none';
    els.error.className = status.error ? 'error' : '';
    els.start.disabled = !!status.running;
    els.stop.disabled = !status.running;
  }

  function fail(message) {
    els.error.textContent = message;
    els.error.className = 'error';
  }

  function poll() {
    window.fetch(url('/admin/status'), { cache: 'no-store' })
      .then(function (response) {
        if (!response.ok) { throw new Error('status ' + response.status); }
        return response.json();
      })
      .then(render)
      .catch(function (error) { fail(String(error.message || error)); });
  }

  function post(path, extra) {
    window.fetch(url(path, extra), { method: 'POST', cache: 'no-store' })
      .then(function (response) {
        if (!response.ok) { throw new Error('status ' + response.status); }
        return response.json();
      })
      .then(render)
      .catch(function (error) { fail(String(error.message || error)); });
  }

  els.start.addEventListener('click', function () {
    if (!els.capture.value) { return; }
    post('/admin/start', {
      capture: els.capture.value,
      paced: els.paced.checked ? 'true' : 'false'
    });
  });

  els.stop.addEventListener('click', function () { post('/admin/stop'); });

  poll();
  window.setInterval(poll, POLL_MS);
}());
