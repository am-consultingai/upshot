// Upshot site: download links, copy button, the remembered language choice, and the video player.
// The page is complete without this file.
(function () {
  "use strict";

  // The language switch is remembered, so a visitor who picks English is not sent
  // back to Hebrew by the time-zone guess on their next visit, and the other way round.
  document.querySelectorAll("a[data-lang]").forEach(function (link) {
    link.addEventListener("click", function () {
      try { localStorage.setItem("upshot-lang", link.dataset.lang); } catch (e) {}
    });
  });

  // The video: YouTube's player replaces the poster only when play is pressed.
  // Without this script the link opens the video on YouTube.
  document.querySelectorAll("a[data-yt]").forEach(function (link) {
    link.addEventListener("click", function (event) {
      // A modified click (new tab, new window) still opens YouTube, as a link should.
      if (event.ctrlKey || event.metaKey || event.shiftKey || event.altKey || event.button !== 0) return;
      event.preventDefault();
      var frame = document.createElement("iframe");
      frame.src = "https://www.youtube-nocookie.com/embed/" + link.dataset.yt + "?autoplay=1&rel=0&playsinline=1";
      frame.title = link.dataset.title;
      frame.allow = "autoplay; encrypted-media; picture-in-picture; fullscreen";
      frame.allowFullscreen = true;
      frame.referrerPolicy = "strict-origin-when-cross-origin";
      link.replaceWith(frame);
      frame.focus();
    });
  });

  // The download links name a release in the page itself, so they work without this
  // file. Here they follow the update manifest instead: the stable channel's installer,
  // or the beta's until there is a stable one. Only this repository's release files.
  var downloads = document.querySelectorAll("a[data-download]");
  var script = document.currentScript;
  if (downloads.length && script && window.fetch) {
    var RELEASES = "https://github.com/am-consultingai/upshot/releases/download/";
    var manifest = function (channel) {
      return fetch(new URL("updates/" + channel + ".json", script.src)).then(function (r) {
        return r.ok ? r.json() : null;
      });
    };
    manifest("stable").then(function (m) { return m || manifest("beta"); }).then(function (m) {
      if (!m || typeof m.url !== "string" || m.url.indexOf(RELEASES) !== 0) return;
      downloads.forEach(function (link) { link.href = m.url; });
    }).catch(function () {});
  }

  document.querySelectorAll("button[data-copy]").forEach(function (btn) {
    if (!navigator.clipboard) return;
    // Labels come from the page, so the Hebrew page's button answers in Hebrew.
    var label = btn.querySelector("span");
    var idle = label.textContent;
    btn.hidden = false;
    btn.addEventListener("click", function () {
      var text = document.getElementById(btn.dataset.copy).textContent.trim();
      navigator.clipboard.writeText(text).then(function () {
        label.textContent = btn.dataset.done || "Copied";
      }, function () {
        label.textContent = btn.dataset.fail || "Select to copy";
      });
      setTimeout(function () { label.textContent = idle; }, 1600);
    });
  });
})();
