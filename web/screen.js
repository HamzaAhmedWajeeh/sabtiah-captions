/* Big-screen view: `/screen?lang=en&lines=2`.
 *
 * One language, the last N paragraphs, nothing else - designed to sit under
 * the speaker's slides. It reuses `SabtiahFeed` from app.js so there is one
 * implementation of the snapshot/diff protocol in the project, not two.
 *
 * Older paragraphs are hidden rather than removed. The server may still send
 * updates for a paragraph that has scrolled out of the window (a late
 * translation token lands in it), and a hidden node accepts that update at no
 * cost, where a removed one would have to be rebuilt and re-ordered.
 */

(function () {
  'use strict';

  var MAX_LINES = 8;

  var params = new URLSearchParams(window.location.search);
  var lang = params.get('lang') === 'ar' ? 'ar' : 'en';
  var requested = parseInt(params.get('lines'), 10);
  var lines = Math.max(1, Math.min(MAX_LINES, requested || 2));

  var container = document.getElementById('screen-stream');
  container.setAttribute('lang', lang);
  container.setAttribute('dir', lang === 'ar' ? 'rtl' : 'ltr');

  var renderer = window.SabtiahFeed.renderer(container);

  function showLastN() {
    var children = container.children;
    var cutoff = children.length - lines;
    for (var i = 0; i < children.length; i += 1) {
      children[i].hidden = i < cutoff;
    }
  }

  window.SabtiahFeed.create({
    onParagraph: function (stream, para) {
      if (stream === lang) { renderer.upsert(para); }
    },
    onRetain: function (stream, present) {
      if (stream === lang) { renderer.retain(present); }
    },
    onStatus: function () { /* no chrome on this view, nothing to show */ },
    onApplied: function () { showLastN(); }
  });
}());
