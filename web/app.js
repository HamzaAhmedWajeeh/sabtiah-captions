/* Audience page, and the caption feed both pages share.
 *
 * `createFeed` is the whole client side of the broadcast protocol:
 *
 *   server -> {"type":"snapshot", seq, live, streams:{en:[...], ar:[...]}}
 *          -> {"type":"diff", seq, base, live, streams:{...}}   (changed only)
 *          -> {"type":"ping"}
 *   client -> {"type":"resync"}                                (nothing else)
 *
 * Two contract details from app/captions.py that a client gets wrong by
 * default:
 *
 * 1. **Paragraph ids are globally unique, not aligned across streams.** The
 *    same id never appears in both `en` and `ar`, so the two streams are
 *    kept in separate maps. Ids ascend with time, which is what lets a
 *    paragraph be inserted in the right place without the server sending any
 *    ordering information.
 * 2. **Trimming is invisible in a diff.** When the 200-paragraph cap drops
 *    the oldest, nothing says "deleted" - they just stop appearing. So a
 *    snapshot is authoritative: any id missing from one is dropped locally.
 *
 * `base` is the sequence number a diff was computed from. If it is ahead of
 * what this client has, a message was lost and the client asks for a full
 * snapshot rather than quietly rendering a hole.
 *
 * No framework, no build step (BUILD_SPEC section 2). Served as-is.
 */

(function () {
  'use strict';

  var LANGS = ['en', 'ar'];
  var STORE = 'sabtiah.';

  /* localStorage throws in Safari private mode rather than returning null,
   * and a thrown exception here would take the captions down with it. */
  function load(key) {
    try { return window.localStorage.getItem(STORE + key); } catch (e) { return null; }
  }

  function save(key, value) {
    try { window.localStorage.setItem(STORE + key, value); } catch (e) { /* no-op */ }
  }

  /* -- the feed ---------------------------------------------------------- */

  function createFeed(handlers) {
    var seq = 0;
    var live = false;
    var status = 'connecting';
    var socket = null;
    var attempt = 0;
    var timer = null;
    var stopped = false;

    function socketUrl() {
      var scheme = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
      return scheme + '//' + window.location.host + '/ws';
    }

    function setStatus(next) {
      if (next === status) { return; }
      status = next;
      handlers.onStatus(status, live);
    }

    function applySnapshot(message) {
      LANGS.forEach(function (lang) {
        var incoming = (message.streams && message.streams[lang]) || [];
        var present = Object.create(null);
        incoming.forEach(function (para) {
          present[para.id] = true;
          handlers.onParagraph(lang, para);
        });
        // Authoritative: whatever the server no longer lists is gone.
        handlers.onRetain(lang, present);
      });
    }

    function applyDiff(message) {
      LANGS.forEach(function (lang) {
        var changed = (message.streams && message.streams[lang]) || [];
        changed.forEach(function (para) { handlers.onParagraph(lang, para); });
      });
    }

    function handle(raw) {
      var message;
      try { message = JSON.parse(raw); } catch (e) { return; }
      if (!message || typeof message !== 'object') { return; }

      if (message.type === 'ping') { return; }

      if (message.type === 'snapshot') {
        applySnapshot(message);
      } else if (message.type === 'diff') {
        if (typeof message.base === 'number' && message.base > seq) {
          // A message went missing. Do not paint a gap - ask for everything.
          request({ type: 'resync' });
          return;
        }
        applyDiff(message);
      } else {
        return;
      }

      seq = typeof message.seq === 'number' ? message.seq : seq;
      live = !!message.live;
      setStatus(live ? 'live' : 'idle');
      handlers.onApplied(live);
    }

    function request(payload) {
      if (socket && socket.readyState === 1) {
        try { socket.send(JSON.stringify(payload)); } catch (e) { /* closing */ }
      }
    }

    function retry() {
      if (stopped || timer) { return; }
      // 1s, 2s, 4s, 8s, then 15s for as long as it takes (BUILD_SPEC).
      var delay = Math.min(15000, 1000 * Math.pow(2, attempt));
      attempt += 1;
      timer = window.setTimeout(function () { timer = null; connect(); }, delay);
    }

    function connect() {
      if (stopped) { return; }
      setStatus(seq === 0 ? 'connecting' : 'reconnecting');
      var next;
      try {
        next = new WebSocket(socketUrl());
      } catch (e) {
        retry();
        return;
      }
      socket = next;

      next.onopen = function () {
        attempt = 0;
        // The server opens with a full snapshot, so there is nothing to ask
        // for here: reconnecting is itself the resync.
      };
      next.onmessage = function (event) { handle(event.data); };
      next.onclose = function () {
        if (socket === next) { socket = null; }
        setStatus('reconnecting');
        retry();
      };
      next.onerror = function () { /* onclose always follows; handled there */ };
    }

    /* A locked phone suspends the socket and the close event may only arrive
     * when the screen comes back. Reconnect at once rather than waiting out a
     * backoff the user cannot see. */
    document.addEventListener('visibilitychange', function () {
      if (document.visibilityState !== 'visible' || stopped) { return; }
      if (socket && socket.readyState <= 1) { return; }
      if (timer) { window.clearTimeout(timer); timer = null; }
      attempt = 0;
      connect();
    });

    connect();

    return {
      resync: function () { request({ type: 'resync' }); },
      status: function () { return status; },
      isLive: function () { return live; },
      stop: function () {
        stopped = true;
        if (timer) { window.clearTimeout(timer); timer = null; }
        if (socket) { try { socket.close(); } catch (e) { /* already gone */ } }
      }
    };
  }

  window.SabtiahFeed = { create: createFeed, langs: LANGS, load: load, save: save };

  /* -- paragraph rendering ---------------------------------------------- */

  /* One <p> per paragraph, each holding a final span and a partial span.
   * Nothing is ever re-rendered: an update writes to one of those two text
   * nodes. That is what keeps the page from flickering 10 times a second,
   * and it keeps the browser's text selection alive while captions arrive. */
  function createRenderer(container) {
    var nodes = Object.create(null);

    function place(paragraph, id) {
      var last = container.lastElementChild;
      if (!last || Number(last.dataset.id) < id) {
        container.appendChild(paragraph);
        return;
      }
      var children = container.children;
      for (var i = 0; i < children.length; i += 1) {
        if (Number(children[i].dataset.id) > id) {
          container.insertBefore(paragraph, children[i]);
          return;
        }
      }
      container.appendChild(paragraph);
    }

    return {
      upsert: function (para) {
        var node = nodes[para.id];
        if (!node) {
          var element = document.createElement('p');
          element.dataset.id = para.id;
          var finalSpan = document.createElement('span');
          var partialSpan = document.createElement('span');
          partialSpan.className = 'partial';
          element.appendChild(finalSpan);
          element.appendChild(partialSpan);
          node = { element: element, final: finalSpan, partial: partialSpan };
          nodes[para.id] = node;
          place(element, para.id);
        }
        var finalText = para.final || '';
        var partialText = para.partial || '';
        if (node.final.textContent !== finalText) { node.final.textContent = finalText; }
        if (node.partial.textContent !== partialText) { node.partial.textContent = partialText; }
      },

      retain: function (present) {
        Object.keys(nodes).forEach(function (id) {
          if (present[id]) { return; }
          var node = nodes[id];
          if (node.element.parentNode) { node.element.parentNode.removeChild(node.element); }
          delete nodes[id];
        });
      },

      count: function () { return Object.keys(nodes).length; },

      /* Last N paragraphs, newest last. Used by the big screen. */
      tail: function (limit) {
        return Object.keys(nodes)
          .map(Number)
          .sort(function (a, b) { return a - b; })
          .slice(-limit);
      },

      node: function (id) { return nodes[id]; }
    };
  }

  window.SabtiahFeed.renderer = createRenderer;

  /* -- audience page ----------------------------------------------------- */

  var TEXT = {
    en: {
      connecting: 'connecting',
      reconnecting: 'reconnecting',
      live: 'live',
      idle: 'not live',
      disclaimer: 'Live auto-translation. May contain mistakes.',
      waiting: 'No session live right now. Captions start when the talk does.',
      jump: 'Jump to live'
    },
    ar: {
      connecting: 'جارٍ الاتصال',
      reconnecting: 'إعادة الاتصال',
      live: 'مباشر',
      idle: 'غير مباشر',
      disclaimer: 'ترجمة آلية مباشرة، قد تحتوي على أخطاء.',
      waiting: 'لا توجد جلسة مباشرة الآن. تبدأ الترجمة مع بداية الحديث.',
      jump: 'انتقل إلى المباشر'
    }
  };

  var MIN_SIZE = 16;
  var MAX_SIZE = 44;
  var SIZE_STEP = 2;

  /* How close to the bottom still counts as "following the talk". Generous,
   * because a thumb resting on a phone drifts a few pixels. */
  var FOLLOW_SLACK = 90;

  function mountAudience() {
    var main = document.getElementById('streams');
    var stateChip = document.getElementById('state');
    var disclaimer = document.getElementById('disclaimer');
    var jump = document.getElementById('jump');

    var streams = {};
    var renderers = {};
    var following = {};
    var notes = {};

    LANGS.forEach(function (lang) {
      streams[lang] = document.getElementById('stream-' + lang);
      renderers[lang] = createRenderer(streams[lang]);
      following[lang] = true;
      streams[lang].addEventListener('scroll', function () {
        following[lang] = isNearBottom(streams[lang]);
        updateJump();
      }, { passive: true });
    });

    var view = pickView();
    var size = pickSize();
    var live = false;

    function pickView() {
      var stored = load('view');
      if (stored === 'en' || stored === 'ar' || stored === 'both') { return stored; }
      var language = (navigator.language || 'en').toLowerCase();
      return language.indexOf('ar') === 0 ? 'ar' : 'en';
    }

    function pickSize() {
      var stored = parseInt(load('size'), 10);
      if (!stored || stored < MIN_SIZE || stored > MAX_SIZE) { return 22; }
      return stored;
    }

    function words() { return TEXT[view === 'ar' ? 'ar' : 'en']; }

    function isNearBottom(element) {
      return element.scrollHeight - element.scrollTop - element.clientHeight
        <= FOLLOW_SLACK;
    }

    function visibleLangs() {
      return view === 'both' ? LANGS : [view];
    }

    function scrollToLive(lang) {
      var element = streams[lang];
      element.scrollTop = element.scrollHeight;
      following[lang] = true;
    }

    function updateJump() {
      var behind = visibleLangs().some(function (lang) { return !following[lang]; });
      jump.hidden = !behind;
    }

    function applySize() {
      document.documentElement.style.setProperty('--size', size + 'px');
    }

    function applyView() {
      main.classList.toggle('both', view === 'both');
      var shown = visibleLangs();
      LANGS.forEach(function (lang) {
        streams[lang].hidden = shown.indexOf(lang) === -1;
      });
      document.querySelectorAll('.controls button[data-view]').forEach(function (button) {
        button.setAttribute('aria-pressed', String(button.dataset.view === view));
      });
      disclaimer.textContent = words().disclaimer;
      disclaimer.setAttribute('dir', view === 'ar' ? 'rtl' : 'ltr');
      jump.textContent = words().jump;
      jump.setAttribute('dir', view === 'ar' ? 'rtl' : 'ltr');
      renderStatus();
      renderNotes();
      // A stream that was hidden could not scroll while it was hidden.
      shown.forEach(function (lang) { if (following[lang]) { scrollToLive(lang); } });
      updateJump();
    }

    function renderStatus() {
      var status = feed ? feed.status() : 'connecting';
      stateChip.textContent = words()[status] || status;
      stateChip.className = status;
    }

    /* The spec's "No session live right now" line, shown in the stream body
     * where the captions would be - not as a banner, so it vanishes the
     * moment the first paragraph lands. */
    function renderNotes() {
      LANGS.forEach(function (lang) {
        var needed = !live && renderers[lang].count() === 0;
        var note = notes[lang];
        if (needed && !note) {
          note = document.createElement('p');
          note.className = 'note';
          notes[lang] = note;
          streams[lang].appendChild(note);
        }
        if (needed) {
          note.textContent = TEXT[lang].waiting;
        } else if (note) {
          if (note.parentNode) { note.parentNode.removeChild(note); }
          notes[lang] = null;
        }
      });
    }

    var feed = createFeed({
      onParagraph: function (lang, para) { renderers[lang].upsert(para); },
      onRetain: function (lang, present) { renderers[lang].retain(present); },
      onStatus: function () { renderStatus(); },
      onApplied: function (isLive) {
        live = isLive;
        renderNotes();
        renderStatus();
        visibleLangs().forEach(function (lang) {
          if (following[lang]) { scrollToLive(lang); }
        });
        updateJump();
      }
    });

    document.querySelectorAll('.controls button[data-view]').forEach(function (button) {
      button.addEventListener('click', function () {
        view = button.dataset.view;
        save('view', view);
        applyView();
      });
    });

    document.getElementById('bigger').addEventListener('click', function () {
      size = Math.min(MAX_SIZE, size + SIZE_STEP);
      save('size', size);
      applySize();
    });

    document.getElementById('smaller').addEventListener('click', function () {
      size = Math.max(MIN_SIZE, size - SIZE_STEP);
      save('size', size);
      applySize();
    });

    jump.addEventListener('click', function () {
      visibleLangs().forEach(scrollToLive);
      updateJump();
    });

    applySize();
    applyView();
  }

  if (document.body.dataset.page === 'audience') {
    mountAudience();
  }
}());
