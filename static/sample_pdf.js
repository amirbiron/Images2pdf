// חיווי המתנה בזמן יצירת הקובץ, ומניעת שליחה כפולה של הטופס.
(function () {
  "use strict";
  var form = document.getElementById("sample-form");
  if (!form) { return; }
  form.addEventListener("submit", function () {
    var button = document.getElementById("sample-button");
    var wait = document.getElementById("sample-wait");
    if (button) { button.disabled = true; }
    if (wait) { wait.hidden = false; }
  });
})();
