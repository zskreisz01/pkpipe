/* Spec 026: progressive enhancement only (the forms work without it). Token estimate of an instruction
   text next to its editor; self-hosted, no inline script, no external host. */
(function () {
  function update(area) {
    var out = document.getElementById(area.getAttribute("data-token-target"));
    if (!out) return;
    var budget = parseInt(area.getAttribute("data-token-budget"), 10) || 800;
    var n = Math.ceil(area.value.length / 4);
    out.textContent = "about " + n + " tokens of " + budget;
    out.classList.toggle("over", n > budget);
  }
  document.addEventListener("input", function (e) {
    if (e.target && e.target.getAttribute && e.target.getAttribute("data-token-budget")) update(e.target);
  });
  document.addEventListener("DOMContentLoaded", function () {
    var areas = document.querySelectorAll("textarea[data-token-budget]");
    for (var i = 0; i < areas.length; i++) update(areas[i]);
  });
})();
