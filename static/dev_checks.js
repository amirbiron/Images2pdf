// בדיקה 5 של /dev/checks: האם התמונות הממוזערות נטענות בדפדפן הזה.
// רק הדפדפן יודע אם תמונה חיצונית נחסמה (עוגיות של צד שלישי, Referer, CORS),
// ולכן הבדיקה רצה כאן ומדווחת לשרת. כל הטקסט נכתב עם textContent.
(function () {
  "use strict";

  var rows = Array.prototype.slice.call(document.querySelectorAll("tr[data-thumb-id]"));
  if (rows.length === 0) { return; }

  var TIMEOUT_MS = 15000;

  function loadImage(src, referrerPolicy) {
    return new Promise(function (resolve) {
      var img = new Image();
      var done = false;
      var timer = setTimeout(function () { finish(false, null); }, TIMEOUT_MS);
      function finish(ok, el) {
        if (done) { return; }
        done = true;
        clearTimeout(timer);
        resolve({ ok: ok, img: el });
      }
      img.onload = function () { finish(img.naturalWidth > 0, img); };
      img.onerror = function () { finish(false, null); };
      if (referrerPolicy) { img.referrerPolicy = referrerPolicy; }
      img.alt = "";
      img.className = "thumb";
      img.src = src;
    });
  }

  function show(cell, result) {
    cell.textContent = "";
    if (result === null) {
      cell.textContent = "אין קישור";
      cell.className = "cell cell-none";
      return;
    }
    if (result.ok && result.img) { cell.appendChild(result.img); }
    var label = document.createElement("span");
    label.className = result.ok ? "ok" : "bad";
    label.textContent = result.ok ? "נטען" : "נחסם";
    cell.appendChild(label);
  }

  var jobs = rows.map(function (row) {
    var id = row.getAttribute("data-thumb-id");
    var direct = row.getAttribute("data-direct");
    var cells = {};
    Array.prototype.forEach.call(row.querySelectorAll("td[data-kind]"), function (td) {
      cells[td.getAttribute("data-kind")] = td;
      td.textContent = "…";
    });
    var proxySrc = "/api/drive/thumbnail/" + encodeURIComponent(id);
    var tasks = [
      direct ? loadImage(direct, "strict-origin-when-cross-origin") : Promise.resolve(null),
      direct ? loadImage(direct, "no-referrer") : Promise.resolve(null),
      loadImage(proxySrc, null)
    ];
    return Promise.all(tasks).then(function (r) {
      show(cells.direct, r[0]);
      show(cells.direct_no_referrer, r[1]);
      show(cells.proxy, r[2]);
      return {
        id: id,
        direct: r[0] === null ? null : r[0].ok,
        direct_no_referrer: r[1] === null ? null : r[1].ok,
        proxy: r[2].ok
      };
    });
  });

  Promise.all(jobs).then(function (results) {
    function allOk(key) {
      var tested = results.filter(function (r) { return r[key] !== null; });
      return tested.length > 0 && tested.every(function (r) { return r[key] === true; });
    }
    var summary = document.getElementById("thumb-summary");
    var text;
    var cls;
    if (allOk("direct") || allOk("direct_no_referrer")) {
      text = "עבר: הקישורים הישירים של גוגל נטענים בדפדפן הזה. אין צורך לעבור דרך השרת.";
      cls = "alert alert-ok";
    } else if (allOk("proxy")) {
      text = "עבר עם מעבר לשרת: הקישורים הישירים נחסמים בדפדפן הזה, והנתיב דרך השרת עובד.";
      cls = "alert alert-warn";
    } else {
      text = "נכשל: חלק מהתמונות לא נטענו גם דרך השרת.";
      cls = "alert alert-error";
    }
    if (summary) { summary.textContent = text; summary.className = cls; }

    fetch("/dev/checks/thumbnail-report", {
      method: "POST",
      credentials: "same-origin",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ results: results })
    }).then(function (resp) {
      if (!resp.ok && summary) {
        summary.textContent = text + " (הדיווח לשרת לא נשמר.)";
      }
    }).catch(function () {
      if (summary) { summary.textContent = text + " (הדיווח לשרת לא נשלח.)"; }
    });
  });
})();
