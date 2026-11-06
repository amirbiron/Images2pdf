// static/picker.js — הבורר (שלב 2): חיפוש לוקיישן, דפדוף בתתי-התיקיות שלו, ובחירה של עד 12 תמונות.
//
// המסך נקבע רק לפי הכתובת, כדי שכפתור "אחורה" של הטלפון יעבוד בין המסכים:
//   #/                  חיפוש
//   #/s/<טקסט>          חיפוש עם טקסט (כדי שחזרה מלוקיישן תציג שוב את התוצאות)
//   #/l/<מיקום>/<תיקייה>/...   לוקיישן: המזהה הראשון הוא תיקיית הלוקיישן, והשאר הנתיב בתוכו
//
// הבחירה נשמרת בין תתי-תיקיות של אותו לוקיישן, ומתאפסת רק במעבר ללוקיישן אחר (ROADMAP).
// היא נשמרת גם ב-sessionStorage, כדי שרענון הדף לא ימחק אותה.
//
// כל טקסט שמגיע מדרייב (שמות תיקיות וקבצים) נכנס לדף עם textContent בלבד.
//
// שלב 3: כפתור הגדלה על כל תמונה (בלי לבחור אותה), ויצירת PDF. ליצירה יש מזהה שהדפדפן
// יוצר ושומר ב-sessionStorage לפני השליחה. אם החיבור נופל באמצע (מסך נעול, מעבר
// לאפליקציה אחרת), השרת ממשיך, והדפדפן שואל על המצב לפי אותו מזהה כשהוא חוזר — ולא
// שולח יצירה חדשה. ראו pdf_jobs.py.
(function () {
  "use strict";

  var root = document.getElementById("picker");
  if (!root) { return; }

  var MAX = parseInt(root.getAttribute("data-max"), 10) || 12;
  var STORAGE_KEY = "images2pdf.picker.v1";
  var SEARCH_DEBOUNCE_MS = 400;
  var MIN_QUERY_CHARS = 2;
  var ID_RE = /^[A-Za-z0-9_-]{1,200}$/;

  function $(id) { return document.getElementById(id); }
  var el = {
    screenSearch: $("screen-search"),
    screenLocation: $("screen-location"),
    searchForm: $("search-form"),
    searchInput: $("search-input"),
    searchStatus: $("search-status"),
    searchResults: $("search-results"),
    searchMore: $("search-more"),
    back: $("back-button"),
    heading: $("location-heading"),
    breadcrumb: $("breadcrumb"),
    locationStatus: $("location-status"),
    grid: $("grid"),
    selection: $("selection"),
    selectionCount: $("selection-count"),
    clearSelection: $("clear-selection"),
    strip: $("selection-strip"),
    title: $("title-input"),
    create: $("create-pdf"),
    toast: $("toast"),
    viewer: $("viewer"),
    viewerCount: $("viewer-count"),
    viewerName: $("viewer-name"),
    viewerClose: $("viewer-close"),
    viewerLow: $("viewer-low"),
    viewerHigh: $("viewer-high"),
    viewerPrev: $("viewer-prev"),
    viewerNext: $("viewer-next"),
    viewerStatus: $("viewer-status"),
    viewerSelect: $("viewer-select"),
    job: $("job"),
    jobHeading: $("job-heading"),
    jobRunning: $("job-running"),
    jobProgress: $("job-progress"),
    jobResult: $("job-result"),
    jobFilename: $("job-filename"),
    jobShare: $("job-share"),
    jobDownload: $("job-download"),
    jobDrive: $("job-drive"),
    jobMessage: $("job-message"),
    jobFailed: $("job-failed"),
    jobFailedList: $("job-failed-list"),
    jobRetry: $("job-retry"),
    jobNew: $("job-new"),
    jobClose: $("job-close")
  };

  // ─── מצב ────────────────────────────────────────────────────

  var JOB_ID_RE = /^[0-9a-f]{32}$/;

  function emptyState() {
    return { location: null, selection: [], title: "", titleEdited: false, names: {}, job: null };
  }

  // היצירה האחרונה: המזהה, והבקשה עצמה — כדי לשלוח אותה שוב עם אותו מזהה אם היא לא הגיעה לשרת.
  function validJob(j) {
    return j === null || j === undefined ||
      (typeof j === "object" && JOB_ID_RE.test(j.id) && j.payload && typeof j.payload === "object");
  }

  function isItem(s) {
    return s && typeof s === "object" && ID_RE.test(s.id) && typeof s.name === "string" &&
      (s.thumb === null || typeof s.thumb === "string");
  }

  function validState(s) {
    if (!s || typeof s !== "object") { return false; }
    if (s.location !== null && !(s.location && ID_RE.test(s.location.id) && typeof s.location.name === "string")) { return false; }
    if (!Array.isArray(s.selection) || s.selection.length > MAX || !s.selection.every(isItem)) { return false; }
    if (typeof s.title !== "string" || typeof s.titleEdited !== "boolean") { return false; }
    if (!validJob(s.job)) { return false; }
    return s.names && typeof s.names === "object";
  }

  function loadState() {
    try {
      var raw = window.sessionStorage.getItem(STORAGE_KEY);
      var parsed = raw ? JSON.parse(raw) : null;
      return validState(parsed) ? parsed : null;
    } catch (e) {
      return null; // אחסון חסום או תוכן פגום: מתחילים נקי
    }
  }

  function saveState() {
    try {
      window.sessionStorage.setItem(STORAGE_KEY, JSON.stringify(state));
    } catch (e) {
      // גלישה פרטית או אחסון מלא: הבחירה נשמרת רק בזיכרון, עד רענון הדף.
    }
  }

  var state = loadState() || emptyState();

  function rememberName(id, name) {
    if (!ID_RE.test(id) || typeof name !== "string" || !name) { return; }
    state.names[id] = name;
    var keys = Object.keys(state.names);
    if (keys.length > 300) { delete state.names[keys[0]]; } // תקרה כדי שהאחסון לא יגדל בלי סוף
  }

  // ─── קריאות לשרת ────────────────────────────────────────────

  function api(url, signal) {
    return fetch(url, { credentials: "same-origin", signal: signal, headers: { "Accept": "application/json" } })
      .then(function (resp) {
        return resp.json().catch(function () { return {}; }).then(function (body) {
          if (!resp.ok) {
            var err = new Error("request failed");
            err.code = body && typeof body.error === "string" ? body.error : "http_" + resp.status;
            throw err;
          }
          return body;
        });
      });
  }

  function setStatus(node, text, link) {
    node.textContent = "";
    if (!text) { return; }
    node.appendChild(document.createTextNode(text));
    if (link) {
      var a = document.createElement("a");
      a.href = link.href;
      a.textContent = link.text;
      node.appendChild(document.createTextNode(" "));
      node.appendChild(a);
    }
  }

  function showError(node, err) {
    if (err.code === "unauthorized") {
      window.location.href = "/login?next=" + encodeURIComponent("/" + window.location.hash);
      return;
    }
    if (err.code === "google_reconnect_required") {
      setStatus(node, "צריך לחבר מחדש את חשבון הגוגל.", { href: "/settings", text: "למסך החיבור" });
    } else if (err.code === "not_found" || err.code === "bad_folder_id") {
      setStatus(node, "התיקייה לא נמצאה, או שהחשבון לא רואה אותה.");
    } else if (err.code === "query_too_long") {
      setStatus(node, "הטקסט לחיפוש ארוך מדי.");
    } else {
      setStatus(node, "משהו השתבש מול גוגל. נסו שוב בעוד רגע.");
    }
  }

  var toastTimer = null;
  function toast(text) {
    el.toast.textContent = text;
    el.toast.hidden = false;
    clearTimeout(toastTimer);
    toastTimer = setTimeout(function () { el.toast.hidden = true; }, 4000);
  }

  // ─── ניתוב ──────────────────────────────────────────────────

  function safeDecode(text) {
    try { return decodeURIComponent(text); } catch (e) { return ""; }
  }

  function parseHash() {
    var parts = window.location.hash.replace(/^#\/?/, "").split("/");
    if (parts[0] === "l") {
      var ids = parts.slice(1).filter(function (p) { return p !== ""; });
      if (ids.length > 0 && ids.every(function (id) { return ID_RE.test(id); })) {
        return { screen: "location", path: ids };
      }
    }
    if (parts[0] === "s") {
      return { screen: "search", q: safeDecode(parts.slice(1).join("/")) };
    }
    return { screen: "search", q: "" };
  }

  function locationHash(path) { return "#/l/" + path.join("/"); }
  function searchHash(q) { return q ? "#/s/" + encodeURIComponent(q) : "#/"; }

  function go(hash) {
    if (window.location.hash === hash) { route(); } else { window.location.hash = hash; }
  }

  function replaceHash(hash) {
    if (window.location.hash !== hash) { window.history.replaceState(null, "", hash); }
  }

  // הכתובת של המסך שמוצג עכשיו — לשם חוזרים כשהמשתמש מבטל מעבר ללוקיישן אחר.
  var shownHash = null;

  // הבחירה שייכת ללוקיישן אחד, ומתאפסת רק במעבר ללוקיישן אחר (ROADMAP). המעבר יכול
  // להגיע מתוצאת חיפוש, מכפתור "אחורה" או "קדימה" של הדפדפן, או מכתובת שהודבקה —
  // ולכן האישור נמצא כאן, בנקודה שכל המסלולים עוברים בה, ולא רק בלחיצה על תוצאה.
  function confirmSwitch(locId) {
    var n = state.selection.length;
    if (!state.location || state.location.id === locId || n === 0) { return true; }
    var from = state.location.name ? "מ\"" + state.location.name + "\" " : "";
    return window.confirm(n === 1
      ? "לעבור ללוקיישן אחר? התמונה שנבחרה " + from + "תימחק מהבחירה."
      : "לעבור ללוקיישן אחר? " + n + " התמונות שנבחרו " + from + "יימחקו מהבחירה.");
  }

  function switchLocation(locId) {
    if (state.location && state.location.id === locId) { return; }
    state.location = { id: locId, name: nameOf(locId) };
    state.selection = [];
    state.title = nameOf(locId);
    state.titleEdited = false;
    saveState();
  }

  function route() {
    closeViewer();
    var r = parseHash();
    if (r.screen === "location") {
      if (!confirmSwitch(r.path[0])) {
        // ביטול: הכתובת חוזרת למסך שמוצג, בלי לגעת בבחירה. replaceState לא מפעיל hashchange.
        replaceHash(shownHash || locationHash([state.location.id]));
        if (shownHash === null) { route(); } // טעינה ראשונה: עדיין לא הוצג מסך, מציגים את הלוקיישן השמור
        return;
      }
      switchLocation(r.path[0]);
      showLocation(r.path);
    } else {
      showSearch(r.q);
    }
    shownHash = window.location.hash || "#/";
  }

  // ─── מסך החיפוש ────────────────────────────────────────────

  var search = { seq: 0, abort: null, query: "", token: null, timer: null };

  function normalize(text) { return text.split(/\s+/).filter(Boolean).join(" "); }

  function showSearch(q) {
    cancelListing();
    el.screenLocation.hidden = true;
    el.selection.hidden = true;
    el.screenSearch.hidden = false;
    root.classList.remove("on-location");
    document.title = "חיפוש לוקיישן — כלי PDF ללוקיישנים";
    if (q && normalize(el.searchInput.value) !== q) { el.searchInput.value = q; }
    if (q && q !== search.query) { runSearch(q, null); }
  }

  function runSearch(q, token) {
    var seq = ++search.seq;
    if (search.abort) { search.abort.abort(); }
    search.abort = new AbortController();
    if (!token) {
      search.query = q;
      el.searchResults.textContent = "";
    }
    el.searchMore.hidden = true;
    setStatus(el.searchStatus, "מחפש…");
    var url = "/api/drive/search?q=" + encodeURIComponent(q) + (token ? "&page_token=" + encodeURIComponent(token) : "");
    api(url, search.abort.signal).then(function (data) {
      if (seq !== search.seq) { return; } // תשובה של חיפוש קודם שהגיעה באיחור
      (Array.isArray(data.results) ? data.results : []).forEach(renderResult);
      var total = el.searchResults.children.length;
      search.token = typeof data.nextPageToken === "string" && data.nextPageToken ? data.nextPageToken : null;
      var text;
      if (total === 0) {
        text = "לא נמצאו תיקיות שמתחילות ב-\"" + q + "\". החיפוש מוצא תחילת מילה, אז כדאי לנסות את תחילת השם.";
      } else {
        var found = total === 1 ? "נמצאה תיקייה אחת" : "נמצאו " + total + " תיקיות";
        text = search.token ? found + ", ויש עוד." : found + ".";
      }
      // incompleteSearch של דרייב: לא כל המקומות נסרקו, ולכן ייתכן שחסרות תוצאות.
      if (data.incomplete === true) { text += " גוגל לא הספיק לחפש בכל המקומות, אז ייתכן שחסרות תוצאות. אפשר לחפש שוב."; }
      setStatus(el.searchStatus, text);
      el.searchMore.hidden = !search.token;
    }).catch(function (err) {
      if (err.name === "AbortError" || seq !== search.seq) { return; }
      showError(el.searchStatus, err);
    });
  }

  function renderResult(r) {
    if (!r || !ID_RE.test(r.id) || typeof r.name !== "string") { return; }
    var li = document.createElement("li");
    var button = document.createElement("button");
    button.type = "button";
    button.className = "result";
    var name = document.createElement("span");
    name.className = "result-name";
    name.dir = "auto";
    name.textContent = r.name;
    button.appendChild(name);
    if (r.parent && typeof r.parent.name === "string") {
      var parent = document.createElement("span");
      parent.className = "result-parent";
      parent.textContent = "בתוך: " + r.parent.name;
      button.appendChild(parent);
    }
    button.addEventListener("click", function () { openLocation(r.id, r.name); });
    li.appendChild(button);
    el.searchResults.appendChild(li);
  }

  function openLocation(id, name) {
    rememberName(id, name);
    if (!confirmSwitch(id)) { return; }
    switchLocation(id);
    go(locationHash([id]));
  }

  el.searchInput.addEventListener("input", function () {
    clearTimeout(search.timer);
    var q = normalize(el.searchInput.value);
    if (q.length < MIN_QUERY_CHARS) {
      search.seq++;
      if (search.abort) { search.abort.abort(); }
      search.query = "";
      el.searchResults.textContent = "";
      el.searchMore.hidden = true;
      setStatus(el.searchStatus, "");
      replaceHash("#/");
      return;
    }
    search.timer = setTimeout(function () {
      replaceHash(searchHash(q));
      runSearch(q, null);
    }, SEARCH_DEBOUNCE_MS);
  });

  el.searchForm.addEventListener("submit", function (event) {
    event.preventDefault();
    clearTimeout(search.timer);
    var q = normalize(el.searchInput.value);
    if (!q) { return; }
    replaceHash(searchHash(q));
    runSearch(q, null);
  });

  el.searchMore.addEventListener("click", function () {
    if (search.token) { runSearch(search.query, search.token); }
  });

  // ─── מסך הלוקיישן ──────────────────────────────────────────

  var listing = { seq: 0, abort: null, path: [], images: [] };

  function cancelListing() {
    listing.seq++;
    if (listing.abort) { listing.abort.abort(); }
  }

  function nameOf(id) { return state.names[id] || ""; }

  function fetchName(id) {
    api("/api/drive/folder/" + id).then(function (data) {
      if (typeof data.name === "string") {
        rememberName(id, data.name);
        if (state.location && state.location.id === id) {
          state.location.name = data.name;
          if (!state.titleEdited) { state.title = data.name; }
        }
        saveState();
        renderHeader();
        renderSelection();
      }
    }).catch(function (err) {
      showError(el.locationStatus, err);
    });
  }

  function showLocation(path) {
    listing.path = path;
    saveState();
    path.forEach(function (id) { if (!nameOf(id)) { fetchName(id); } });

    el.screenSearch.hidden = true;
    el.screenLocation.hidden = false;
    el.selection.hidden = false;
    root.classList.add("on-location");
    renderHeader();
    renderSelection();
    loadFolder(path[path.length - 1]);
  }

  function renderHeader() {
    var path = listing.path;
    if (!path.length) { return; }
    el.heading.textContent = nameOf(path[0]) || "…";
    document.title = (nameOf(path[0]) || "לוקיישן") + " — בחירת תמונות";
    el.breadcrumb.textContent = "";
    if (path.length < 2) { return; } // בשורש הלוקיישן אין מה להציג מעבר לכותרת
    path.forEach(function (id, i) {
      if (i > 0) {
        var sep = document.createElement("span");
        sep.className = "crumb-sep";
        sep.setAttribute("aria-hidden", "true");
        sep.textContent = "/";
        el.breadcrumb.appendChild(sep);
      }
      var label = nameOf(id) || "…";
      if (i === path.length - 1) {
        var current = document.createElement("span");
        current.className = "crumb-current";
        current.setAttribute("aria-current", "page");
        current.textContent = label;
        el.breadcrumb.appendChild(current);
      } else {
        var link = document.createElement("button");
        link.type = "button";
        link.className = "crumb";
        link.textContent = label;
        link.addEventListener("click", function () { go(locationHash(path.slice(0, i + 1))); });
        el.breadcrumb.appendChild(link);
      }
    });
  }

  el.back.addEventListener("click", function () {
    var path = listing.path;
    if (path.length > 1) { go(locationHash(path.slice(0, -1))); } else { go(searchHash(search.query)); }
  });

  function loadFolder(folderId) {
    cancelListing();
    var seq = listing.seq;
    listing.abort = new AbortController();
    var signal = listing.abort.signal;
    el.grid.textContent = "";
    listing.images = [];
    setStatus(el.locationStatus, "טוען…");
    var counts = { folders: 0, images: 0 };

    function page(token) {
      var url = "/api/drive/files?folder_id=" + folderId + (token ? "&page_token=" + encodeURIComponent(token) : "");
      return api(url, signal).then(function (data) {
        if (seq !== listing.seq) { return; } // המשתמש כבר עבר לתיקייה אחרת
        (Array.isArray(data.folders) ? data.folders : []).forEach(function (f) {
          if (f && ID_RE.test(f.id) && typeof f.name === "string") {
            el.grid.appendChild(folderTile(f));
            counts.folders++;
          }
        });
        (Array.isArray(data.images) ? data.images : []).forEach(function (img) {
          if (img && ID_RE.test(img.id) && typeof img.name === "string") {
            el.grid.appendChild(imageTile(img, listing.images.length));
            listing.images.push(img);
            counts.images++;
          }
        });
        if (typeof data.nextPageToken === "string" && data.nextPageToken) {
          setStatus(el.locationStatus, "טוען… " + counts.images + " תמונות עד עכשיו");
          return page(data.nextPageToken);
        }
        setStatus(el.locationStatus, summary(counts));
      });
    }

    page(null).catch(function (err) {
      if (err.name === "AbortError" || seq !== listing.seq) { return; }
      showError(el.locationStatus, err);
    });
  }

  function summary(counts) {
    if (!counts.folders && !counts.images) { return "אין כאן תמונות או תיקיות."; }
    var parts = [];
    if (counts.folders) { parts.push(counts.folders === 1 ? "תת-תיקייה אחת" : counts.folders + " תתי-תיקיות"); }
    if (counts.images) { parts.push(counts.images === 1 ? "תמונה אחת" : counts.images + " תמונות"); }
    return parts.join(" · ");
  }

  function folderTile(f) {
    var button = document.createElement("button");
    button.type = "button";
    button.className = "tile tile-folder";
    var icon = document.createElement("span");
    icon.className = "folder-icon";
    icon.setAttribute("aria-hidden", "true");
    var name = document.createElement("span");
    name.className = "tile-name";
    name.dir = "auto";
    name.textContent = f.name;
    button.appendChild(icon);
    button.appendChild(name);
    button.setAttribute("aria-label", "תיקייה: " + f.name);
    button.addEventListener("click", function () {
      rememberName(f.id, f.name);
      go(locationHash(listing.path.concat([f.id])));
    });
    return button;
  }

  // הקישור של גוגל מסתיים ב-"=s220" (220 פיקסלים בצד הארוך). הפאנל המקורי החליף את
  // הסיומת בגודל אחר לתצוגה המוגדלת (docs/drive-picker-extract.md, סעיף 6). זה לא מתועד
  // אצל גוגל, ולכן קישור בלי הסיומת נשאר כמו שהוא, ותמונה שלא נטענת עוברת לנתיב שדרך השרת.
  var GRID_THUMB_PX = 480;   // משבצת ברוחב כ-180 פיקסלים, במסך טלפון חד (פי 2 עד 3)
  var LARGE_THUMB_PX = 1600;

  function sizedLink(link, px) {
    if (typeof link !== "string" || link.indexOf("https://") !== 0) { return null; }
    return /=s\d+$/.test(link) ? link.replace(/=s\d+$/, "=s" + px) : link;
  }

  function thumbImage(id, link, px) {
    // קודם הקישור הישיר של גוגל; אם התמונה לא נטענת — הנתיב שדרך השרת, רק לתמונה הזו.
    var img = document.createElement("img");
    var proxy = "/api/drive/thumbnail/" + id;
    img.alt = "";
    img.loading = "lazy";
    img.decoding = "async";
    img.src = sizedLink(link, px || GRID_THUMB_PX) || proxy;
    img.addEventListener("error", function onError() {
      if (img.getAttribute("src") !== proxy) {
        img.src = proxy;
        return;
      }
      img.removeEventListener("error", onError);
      img.classList.add("thumb-broken");
    });
    return img;
  }

  // שם התמונה בלי הסיומת. עם הסיומת, שם עברי כמו "גינה 11.jpg" מוצג בכיוון עברי
  // כ-"jpg.11 גינה", ובכיוון לועזי כ-"11 גינה.jpg" — לפי האלגוריתם הדו-כיווני. בגלריה
  // אין צורך בסיומת, ובלעדיה השם מוצג נכון.
  function displayName(name) {
    return name.replace(/\.(jpe?g|png)$/i, "") || name;
  }

  // משבצת תמונה: כפתור בחירה (התמונה עצמה) וכפתור הגדלה נפרד בפינה. שני כפתורים אחים
  // ולא אחד בתוך השני, כי כפתור בתוך כפתור אינו HTML תקין.
  function imageTile(item, index) {
    var tile = document.createElement("div");
    tile.className = "tile tile-image";
    tile.setAttribute("data-id", item.id);
    tile.setAttribute("data-name", item.name);
    var button = document.createElement("button");
    button.type = "button";
    button.className = "tile-select";
    button.appendChild(thumbImage(item.id, item.thumbnailLink));
    var name = document.createElement("span");
    name.className = "tile-name";
    name.dir = "auto";
    name.textContent = displayName(item.name);
    button.appendChild(name);
    var badge = document.createElement("span");
    badge.className = "order-badge";
    badge.setAttribute("aria-hidden", "true");
    button.appendChild(badge);
    button.addEventListener("click", function () { toggle(item); });
    var zoom = document.createElement("button");
    zoom.type = "button";
    zoom.className = "tile-zoom";
    zoom.setAttribute("aria-label", "הגדלה: " + displayName(item.name));
    zoom.appendChild(zoomIcon());
    zoom.addEventListener("click", function () { openViewer(index); });
    tile.appendChild(button);
    tile.appendChild(zoom);
    syncTile(tile);
    return tile;
  }

  function zoomIcon() {
    // זכוכית מגדלת עם פלוס. SVG שנבנה ב-DOM, בלי innerHTML ובלי סגנון inline (CSP).
    var ns = "http://www.w3.org/2000/svg";
    var svg = document.createElementNS(ns, "svg");
    svg.setAttribute("viewBox", "0 0 24 24");
    svg.setAttribute("aria-hidden", "true");
    svg.setAttribute("focusable", "false");
    [["circle", { cx: "10.5", cy: "10.5", r: "6.5" }], ["line", { x1: "15.5", y1: "15.5", x2: "21", y2: "21" }],
     ["line", { x1: "10.5", y1: "7.5", x2: "10.5", y2: "13.5" }], ["line", { x1: "7.5", y1: "10.5", x2: "13.5", y2: "10.5" }]]
      .forEach(function (spec) {
        var node = document.createElementNS(ns, spec[0]);
        Object.keys(spec[1]).forEach(function (k) { node.setAttribute(k, spec[1][k]); });
        svg.appendChild(node);
      });
    return svg;
  }

  function indexInSelection(id) {
    for (var i = 0; i < state.selection.length; i++) {
      if (state.selection[i].id === id) { return i; }
    }
    return -1;
  }

  function syncTile(tile) {
    var i = indexInSelection(tile.getAttribute("data-id"));
    var button = tile.querySelector(".tile-select");
    tile.classList.toggle("selected", i >= 0);
    button.setAttribute("aria-pressed", i >= 0 ? "true" : "false");
    var label = displayName(tile.getAttribute("data-name"));
    button.setAttribute("aria-label", i >= 0 ? label + ", נבחרה מספר " + (i + 1) : label);
    button.querySelector(".order-badge").textContent = i >= 0 ? String(i + 1) : "";
  }

  function syncTiles() {
    Array.prototype.forEach.call(el.grid.querySelectorAll(".tile-image"), syncTile);
  }

  var LIMIT_TEXT = "אפשר לבחור עד " + MAX + " תמונות. כדי להוסיף, מסירים קודם תמונה אחרת.";

  // מחזיר false כשהבחירה מלאה (ההודעה מוצגת אצל הקורא: בגריד או בתצוגה המוגדלת).
  function toggle(item, quiet) {
    var i = indexInSelection(item.id);
    if (i >= 0) {
      state.selection.splice(i, 1);
    } else {
      if (state.selection.length >= MAX) {
        if (!quiet) { toast(LIMIT_TEXT); }
        return false;
      }
      state.selection.push({
        id: item.id,
        name: item.name,
        thumb: typeof item.thumbnailLink === "string" ? item.thumbnailLink : null,
        folderId: listing.path[listing.path.length - 1]
      });
    }
    saveState();
    syncTiles();
    renderSelection();
    return true;
  }

  // ─── הבחירה והכותרת ────────────────────────────────────────

  function renderSelection() {
    var n = state.selection.length;
    el.selectionCount.textContent = n === 0 ? "לא נבחרו תמונות" : "נבחרו " + n + " מתוך " + MAX;
    el.clearSelection.hidden = n === 0;
    el.strip.textContent = "";
    state.selection.forEach(function (item, i) {
      var li = document.createElement("li");
      var button = document.createElement("button");
      button.type = "button";
      button.className = "strip-item";
      button.setAttribute("aria-label", "הסרת תמונה " + (i + 1) + ": " + displayName(item.name));
      button.title = displayName(item.name);
      button.appendChild(thumbImage(item.id, item.thumb));
      var badge = document.createElement("span");
      badge.className = "order-badge";
      badge.setAttribute("aria-hidden", "true");
      badge.textContent = String(i + 1);
      button.appendChild(badge);
      button.addEventListener("click", function () {
        state.selection.splice(i, 1);
        saveState();
        syncTiles();
        renderSelection();
      });
      li.appendChild(button);
      el.strip.appendChild(li);
    });
    if (document.activeElement !== el.title) { el.title.value = state.title; }
    updateCreateButton();
  }

  // הכותרת שתישלח: מה שבשדה, ואם הוא ריק — שם הלוקיישן (ROADMAP: הכותרת היא שם התיקייה).
  function effectiveTitle() {
    var typed = el.title.value.trim();
    return typed || (state.location && state.location.name) || "";
  }

  function updateCreateButton() {
    el.create.disabled = state.selection.length === 0 || !effectiveTitle() || jobBusy();
  }

  el.clearSelection.addEventListener("click", function () {
    var n = state.selection.length;
    if (n > 1 && !window.confirm("לנקות את כל " + n + " התמונות שנבחרו?")) { return; }
    state.selection = [];
    saveState();
    syncTiles();
    renderSelection();
  });

  el.title.addEventListener("input", function () {
    state.title = el.title.value;
    state.titleEdited = true;
    saveState();
    updateCreateButton();
  });

  // ─── תצוגה מוגדלת ──────────────────────────────────────────
  // נפתחת מכפתור ההגדלה בלי לבחור את התמונה, כי בתיקיות יש צילומים דומים מאוד.
  // אפשר לדפדף בין התמונות של התיקייה ולבחור מתוכה.

  var viewer = { index: -1 };

  function openViewer(index) {
    if (index < 0 || index >= listing.images.length) { return; }
    viewer.index = index;
    renderViewer();
    if (!el.viewer.open) { el.viewer.showModal(); }
  }

  function closeViewer() {
    if (el.viewer.open) { el.viewer.close(); }
  }

  function renderViewer() {
    var item = listing.images[viewer.index];
    if (!item) { return; }
    el.viewerCount.textContent = (viewer.index + 1) + " מתוך " + listing.images.length;
    el.viewerName.textContent = displayName(item.name);
    el.viewerStatus.textContent = "";
    // קודם התמונה הקטנה שכבר נטענה בגריד, ומעליה הגדולה כשהיא מגיעה. כל אחת מוצגת רק
    // אחרי שנטענה (בלי אייקון של תמונה שבורה), ובכשל עוברת לנתיב שדרך השרת.
    var proxy = "/api/drive/thumbnail/" + item.id;
    var token = {};
    viewer.token = token;
    loadViewerImage(el.viewerLow, sizedLink(item.thumbnailLink, GRID_THUMB_PX), proxy, token);
    loadViewerImage(el.viewerHigh, sizedLink(item.thumbnailLink, LARGE_THUMB_PX), proxy + "?size=large", token);
    el.viewerPrev.disabled = viewer.index === 0;
    el.viewerNext.disabled = viewer.index === listing.images.length - 1;
    syncViewerSelect();
  }

  function loadViewerImage(img, direct, fallback, token) {
    img.classList.remove("loaded");
    img.onload = function () { if (viewer.token === token) { img.classList.add("loaded"); } };
    img.onerror = function () {
      if (viewer.token === token && img.getAttribute("src") !== fallback) { img.src = fallback; }
    };
    img.src = direct || fallback;
  }

  function syncViewerSelect() {
    var item = listing.images[viewer.index];
    if (!item) { return; }
    var i = indexInSelection(item.id);
    el.viewerSelect.textContent = i >= 0 ? "נבחרה (" + (i + 1) + ") · הסרה" : "בחירה";
    el.viewerSelect.classList.toggle("button-secondary", i >= 0);
    el.viewerSelect.setAttribute("aria-pressed", i >= 0 ? "true" : "false");
  }

  function stepViewer(delta) {
    var next = viewer.index + delta;
    if (next >= 0 && next < listing.images.length) {
      viewer.index = next;
      renderViewer();
    }
  }

  el.viewerClose.addEventListener("click", closeViewer);
  el.viewerPrev.addEventListener("click", function () { stepViewer(-1); });
  el.viewerNext.addEventListener("click", function () { stepViewer(1); });
  el.viewerSelect.addEventListener("click", function () {
    var item = listing.images[viewer.index];
    if (item && toggle(item, true) === false) {
      el.viewerStatus.textContent = LIMIT_TEXT; // ה-toast מתחת לחלון, ולכן ההודעה כאן
      return;
    }
    syncViewerSelect();
  });
  el.viewer.addEventListener("keydown", function (event) {
    // בעברית "הבאה" משמאל: חץ שמאלה מתקדם, חץ ימינה חוזר.
    if (event.key === "ArrowLeft") { stepViewer(1); event.preventDefault(); }
    if (event.key === "ArrowRight") { stepViewer(-1); event.preventDefault(); }
  });
  el.viewer.addEventListener("click", function (event) {
    if (event.target === el.viewer) { closeViewer(); } // לחיצה על הרקע
  });

  // ─── יצירת PDF ─────────────────────────────────────────────

  var JOB_POLL_MS = 2000;
  var JOB_POLL_MAX_MS = 8000;
  // שני ערוצים בלתי תלויים: בקשת היצירה (POST), ושאלות על המצב (GET) כל שתי שניות.
  // השאלות מציגות התקדמות, וממשיכות גם כשה-POST "תקוע" אחרי נעילת מסך — כך התוצאה
  // מגיעה גם אם התשובה ל-POST לא תגיע לעולם.
  var job = { timer: null, posting: false, polling: false, failures: 0, lastError: null, file: null };

  function jobBusy() { return !!(state.job && state.job.status !== "done" && state.job.status !== "failed"); }

  function newJobId() {
    var bytes = new Uint8Array(16);
    window.crypto.getRandomValues(bytes);
    return Array.prototype.map.call(bytes, function (b) { return (b < 16 ? "0" : "") + b.toString(16); }).join("");
  }

  function startJob() {
    if (jobBusy() || state.selection.length === 0 || !effectiveTitle()) { return; }
    var payload = {
      id: newJobId(),
      location_id: state.location.id,
      title: effectiveTitle(),
      images: state.selection.map(function (item) { return { id: item.id, name: item.name }; })
    };
    // נשמר לפני השליחה: אם הדף נטען מחדש באמצע, ממשיכים עם אותו מזהה ולא יוצרים קובץ שני.
    state.job = { id: payload.id, payload: payload, status: "sending" };
    saveState();
    job.file = null;
    job.lastError = null;
    job.failures = 0;
    showJobRunning(null);
    sendJob();
    schedulePoll(false);
  }

  function sendJob() {
    if (!jobBusy() || job.posting) { return; }
    var id = state.job.id;
    job.posting = true;
    fetch("/api/pdf/jobs", {
      method: "POST",
      credentials: "same-origin",
      headers: { "Content-Type": "application/json", "Accept": "application/json" },
      body: JSON.stringify(state.job.payload)
    }).then(function (resp) {
      return resp.json().catch(function () { return {}; }).then(function (body) {
        job.posting = false;
        if (!state.job || state.job.id !== id) { return; }
        if (resp.status === 200 || resp.status === 202) { renderJob(body); return; }
        var code = body && typeof body.error === "string" ? body.error : "http_" + resp.status;
        if (code === "unauthorized") { goToLogin(); return; }
        if (resp.status === 401 || resp.status >= 500) {
          // התשובה היא רק רמז; מקור האמת הוא מצב העבודה בשרת (למשל: הקובץ נוצר, ורק
          // השמירה בדרייב נכשלה כי ההרשאה בוטלה — ההורדה עדיין זמינה).
          job.lastError = code;
          pollJob();
          return;
        }
        endJob({ id: id, status: "failed", error: code });
      });
    }).catch(function () {
      job.posting = false;
      // החיבור נפל (מסך נעול, רשת). השרת ממשיך; השאלות על המצב לפי המזהה ממשיכות.
      schedulePoll(true);
    });
  }

  function schedulePoll(afterFailure) {
    clearTimeout(job.timer);
    if (!jobBusy()) { return; }
    if (afterFailure) { job.failures++; }
    var wait = job.failures ? Math.min(JOB_POLL_MS * Math.pow(2, job.failures - 1), JOB_POLL_MAX_MS) : JOB_POLL_MS;
    job.timer = setTimeout(pollJob, wait);
  }

  function pollJob() {
    clearTimeout(job.timer);
    if (!state.job || job.polling) { return; }
    var id = state.job.id;
    job.polling = true;
    fetch("/api/pdf/jobs/" + id, { credentials: "same-origin", headers: { "Accept": "application/json" } })
      .then(function (resp) {
        return resp.json().catch(function () { return {}; }).then(function (body) {
          job.polling = false;
          if (!state.job || state.job.id !== id) { return; }
          job.failures = 0;
          if (resp.status === 200 || resp.status === 202) { renderJob(body); return; }
          var code = body && typeof body.error === "string" ? body.error : "http_" + resp.status;
          if (code === "unauthorized") { goToLogin(); return; }
          if (resp.status === 404) {
            if (!jobBusy()) { finishJob(); return; }       // קובץ ישן שכבר נמחק בשרת (אחרי 24 שעות)
            if (job.lastError) { endJob({ id: id, status: "failed", error: job.lastError }); return; }
            if (!job.posting) { sendJob(); }               // הבקשה לא הגיעה לשרת: שולחים שוב, באותו מזהה
            schedulePoll(false);
            return;
          }
          endJob({ id: id, status: "failed", error: code });
        });
      })
      .catch(function () {
        job.polling = false;
        if (jobBusy()) { setJobProgress("החיבור נותק. מנסה שוב…"); }
        schedulePoll(true);
      });
  }

  function renderJob(data) {
    if (!data || typeof data !== "object" || !JOB_ID_RE.test(data.id) || !state.job || data.id !== state.job.id) { return; }
    if (data.status === "running") {
      if (!jobBusy()) { return; } // תשובה מאוחרת אחרי שהעבודה כבר הסתיימה
      state.job.status = "running";
      saveState();
      showJobRunning(data);
      schedulePoll(false);
    } else if (data.status === "done" || data.status === "failed") {
      endJob(data);
    }
  }

  function setJobProgress(text) { el.jobProgress.textContent = text; }

  function showJobRunning(data) {
    el.jobHeading.textContent = "מכין את הקובץ";
    el.jobRunning.hidden = false;
    el.jobResult.hidden = true;
    el.jobFailed.hidden = true;
    el.jobMessage.hidden = true;
    el.jobRetry.hidden = el.jobNew.hidden = el.jobClose.hidden = true;
    // המשפט "מכין את הקובץ, זה יכול לקחת עד דקה" קבוע בחלון (ROADMAP, זרימת המשתמש 6);
    // כאן רק שורת ההתקדמות שמתחתיו.
    var text = "";
    if (data && data.stage === "saving") {
      text = "שומר את הקובץ בדרייב…";
    } else if (data && data.progress && data.progress.total > 0) {
      text = "מעבד תמונה " + Math.min(data.progress.done + 1, data.progress.total) + " מתוך " + data.progress.total + "…";
    }
    setJobProgress(text);
    if (!el.job.open) { el.job.showModal(); }
    updateCreateButton();
  }

  var JOB_ERRORS = {
    no_images: "אף תמונה לא עובדה, ולכן לא נוצר קובץ.",
    location_not_found: "תיקיית הלוקיישן לא נמצאה בדרייב. ייתכן שהיא נמחקה או הועברה.",
    google_reconnect_required: "צריך לחבר מחדש את חשבון הגוגל.",
    interrupted: "היצירה נקטעה באמצע, כנראה בגלל עדכון של השרת. אפשר לנסות שוב.",
    busy: "קובץ אחר נוצר עכשיו. מחכים שיסתיים ומנסים שוב.",
    title_not_drawable: "בכותרת אין אף תו שאפשר להציג בקובץ (למשל רק אמוג'י). משנים את הכותרת ומנסים שוב.",
    bad_title: "הכותרת לא תקינה. משנים אותה ומנסים שוב."
  };

  function endJob(data) {
    clearTimeout(job.timer);
    if (state.job) { state.job.status = data.status; saveState(); }
    el.jobRunning.hidden = true;
    el.jobMessage.hidden = true;
    var failed = Array.isArray(data.failed) ? data.failed : [];
    el.jobFailedList.textContent = "";
    failed.forEach(function (f) {
      if (!f || typeof f.name !== "string") { return; }
      var li = document.createElement("li");
      li.textContent = displayName(f.name) + (typeof f.reason === "string" ? " — " + f.reason : "");
      el.jobFailedList.appendChild(li);
    });
    el.jobFailed.hidden = el.jobFailedList.children.length === 0;

    if (data.status === "done" && typeof data.file_url === "string" && data.file_url.indexOf("/api/pdf/jobs/") === 0) {
      el.jobHeading.textContent = "הקובץ מוכן";
      el.jobResult.hidden = false;
      renderFilename(typeof data.filename === "string" ? data.filename : "");
      el.jobDownload.href = data.file_url;
      el.jobDownload.setAttribute("download", typeof data.filename === "string" ? data.filename : "");
      var drive = typeof data.drive_url === "string" && data.drive_url.indexOf("https://drive.google.com/") === 0;
      el.jobDrive.hidden = !drive;
      if (drive) { el.jobDrive.href = data.drive_url; }
      if (data.drive_error === "google_reconnect_required") {
        showJobMessage("הקובץ לא נשמר בדרייב כי צריך לחבר מחדש את חשבון הגוגל. אפשר להוריד אותו.",
          { href: "/settings", text: "לחיבור מחדש" });
      } else if (data.drive_error) {
        showJobMessage("הקובץ לא נשמר בדרייב, אבל אפשר להוריד אותו.");
      } else if (!data.saved_to_drive) {
        showJobMessage("הקובץ לא נשמר בדרייב, אבל אפשר להוריד אותו.");
      }
      prepareShare(data);
      el.jobRetry.hidden = true;
    } else {
      el.jobHeading.textContent = "הקובץ לא נוצר";
      el.jobResult.hidden = true;
      var code = typeof data.error === "string" ? data.error : "";
      if (code === "google_reconnect_required") {
        showJobMessage(JOB_ERRORS[code], { href: "/settings", text: "לחיבור מחדש" });
      } else {
        showJobMessage(JOB_ERRORS[code] || "משהו השתבש ביצירת הקובץ. אפשר לנסות שוב.");
      }
      el.jobRetry.hidden = code === "google_reconnect_required" || code === "no_images";
    }
    el.jobNew.hidden = data.status !== "done";
    el.jobClose.hidden = false;
    if (!el.job.open) { el.job.showModal(); }
    updateCreateButton();
  }

  // שם הקובץ הוא `<כותרת> <DD-MM-YYYY>.pdf`. בשורה בעברית, התאריך והסיומת מתהפכים
  // ("pdf.06-11-2026") לפי האלגוריתם הדו-כיווני, ולכן הם מבודדים ב-bdi בכיוון שמאל-לימין.
  function renderFilename(name) {
    el.jobFilename.textContent = "";
    var m = /^(.*) (\d\d-\d\d-\d{4}\.pdf)$/.exec(name);
    if (!m) {
      el.jobFilename.dir = "auto";
      el.jobFilename.textContent = name;
      return;
    }
    el.jobFilename.removeAttribute("dir");
    var title = document.createElement("bdi");
    title.textContent = m[1];
    var tail = document.createElement("bdi");
    tail.dir = "ltr";
    tail.textContent = m[2];
    el.jobFilename.appendChild(title);
    el.jobFilename.appendChild(document.createTextNode(" "));
    el.jobFilename.appendChild(tail);
  }

  function showJobMessage(text, link) {
    setStatus(el.jobMessage, text, link);
    el.jobMessage.hidden = false;
  }

  // שיתוף: navigator.share עם קבצים דורש לחיצה של המשתמש ("transient activation"), ולכן
  // הקובץ נטען מראש, והלחיצה קוראת ל-share מיד. אם הדפדפן לא יכול לשתף את הקובץ
  // (canShare מחזיר false או לא קיים) — רק הורדה. מקור:
  // https://developer.mozilla.org/en-US/docs/Web/API/Navigator/share ו-.../Navigator/canShare
  function prepareShare(data) {
    el.jobShare.hidden = true;
    job.file = null;
    if (typeof navigator.canShare !== "function" || typeof navigator.share !== "function" || typeof File !== "function") { return; }
    var id = data.id;
    fetch(data.file_url, { credentials: "same-origin" })
      .then(function (resp) { if (!resp.ok) { throw new Error("download failed"); } return resp.blob(); })
      .then(function (blob) {
        if (!state.job || state.job.id !== id) { return; }
        var file = new File([blob], data.filename || "קובץ.pdf", { type: "application/pdf" });
        if (navigator.canShare({ files: [file], title: data.title || "" })) {
          job.file = { file: file, title: data.title || "" };
          el.jobShare.hidden = false;
        }
      })
      .catch(function () { /* בלי שיתוף: ההורדה עדיין מוצגת */ });
  }

  el.jobShare.addEventListener("click", function () {
    if (!job.file) { return; }
    navigator.share({ files: [job.file.file], title: job.file.title }).catch(function (err) {
      if (err && err.name === "AbortError") { return; } // המשתמש סגר את חלון השיתוף
      showJobMessage("השיתוף לא הצליח. אפשר להוריד את הקובץ ולשתף אותו מהטלפון.");
    });
  });

  function finishJob() {
    clearTimeout(job.timer);
    state.job = null;
    job.file = null;
    saveState();
    if (el.job.open) { el.job.close(); }
    updateCreateButton();
  }

  el.create.addEventListener("click", startJob);
  el.jobClose.addEventListener("click", finishJob);
  el.jobRetry.addEventListener("click", function () {
    state.job = null;
    startJob();
  });
  el.jobNew.addEventListener("click", function () {
    finishJob();
    state.selection = [];
    saveState();
    syncTiles();
    renderSelection();
    go(searchHash(""));
  });
  el.job.addEventListener("cancel", function (event) {
    // Escape בזמן היצירה לא סוגר את החלון: העבודה ממשיכה, והחלון הוא המקום שבו תופיע התוצאה.
    if (jobBusy()) { event.preventDefault(); }
  });
  el.job.addEventListener("close", function () {
    if (!jobBusy() && state.job) { finishJob(); }
  });

  // חזרה לדף אחרי נעילת מסך או מעבר לאפליקציה אחרת: שואלים מיד מה המצב.
  function resumeJob() {
    if (jobBusy()) { pollJob(); }
  }
  document.addEventListener("visibilitychange", function () {
    if (document.visibilityState === "visible") { resumeJob(); }
  });
  window.addEventListener("pageshow", resumeJob);
  window.addEventListener("online", resumeJob);

  function goToLogin() {
    window.location.href = "/login?next=" + encodeURIComponent("/" + window.location.hash);
  }

  // ─── התחלה ─────────────────────────────────────────────────

  window.addEventListener("hashchange", route);
  route();
  // יצירה שהתחילה לפני שהדף נטען מחדש (למשל אחרי שהטלפון סגר את הלשונית ברקע)
  if (state.job) {
    if (jobBusy()) { showJobRunning(null); }
    pollJob();
  }
})();
