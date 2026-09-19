// Upshot site: copy button and the remembered language choice.
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
