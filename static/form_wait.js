// טופס שהשליחה שלו לוקחת זמן: נועל את הכפתור ומציג הודעת המתנה עד שהדף הבא נטען.
//
// הודעת ההמתנה יושבת באלמנט עם role="status" שקיים בדף מההתחלה (ריק), כך שקורא מסך
// מכריז עליה כשהטקסט מתמלא. אלמנט שמופיע מ-hidden לא תמיד מוכרז.
//
// חזרה עם "אחורה" יכולה לשחזר את הדף מהמטמון (bfcache) בדיוק במצב שבו עזבנו אותו:
// כפתור נעול והודעת המתנה. באירוע pageshow עם persisted מחזירים את הטופס למצב רגיל.
(function () {
  "use strict";

  function setup(formId, buttonId, waitId) {
    var form = document.getElementById(formId);
    if (!form) { return; }
    var button = document.getElementById(buttonId);
    var wait = document.getElementById(waitId);

    form.addEventListener("submit", function () {
      if (button) { button.disabled = true; }
      if (wait) { wait.textContent = wait.getAttribute("data-wait") || ""; }
    });

    window.addEventListener("pageshow", function (event) {
      if (!event.persisted) { return; }
      if (button) { button.disabled = false; }
      if (wait) { wait.textContent = ""; }
    });
  }

  setup("sample-form", "sample-button", "sample-wait");
  setup("run-form", "run-button", "run-wait");
})();
